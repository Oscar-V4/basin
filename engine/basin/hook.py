"""Claude Code/Codex hook entry. fail-soft: always exit 0, never write stdout.

Modes: session_start | precompact | turn_end | session_end. The deterministic
path (capture + staging) runs immediately; the LLM worker is intentionally not
invoked here (later, detached). Reads the hook JSON on stdin (session_id /
transcript_path / cwd).
"""
from __future__ import annotations

import json
import sys

from .core import Store, safe_id, sha256_hex
from . import ingest, ops, extract_det, reindex, project, fork


_CODEX_MODE_BY_EVENT = {
    "SessionStart": "session_start",
    "PreCompact": "precompact",
    "Stop": "turn_end",
    # Keep this future-tolerant without requiring a separate Codex release.
    "SessionEnd": "session_end",
}


def _read_input() -> dict:
    try:
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except Exception:
        return {}


def _checkpoint_kind(mode: str) -> str:
    if mode == "precompact":
        return "pre_compact"
    if mode == "turn_end":
        return "turn_end"
    return "session_end"


def _run_with_hook(mode: str, hook: dict, provider: str) -> int:
    root = hook.get("cwd") or "."
    store = Store(root)
    if not store.dir.exists():
        return 0  # Basin not set up for this project — no-op
    cfg = store.config()
    if not cfg.get("enabled", True):
        return 0  # engine toggled off for this project (`basin off`)
    project_id = cfg.get("project_id", "p_default")
    branch_id = cfg.get("canon_branch", "main")
    ops.ensure_branch(store, branch_id, name=branch_id, intent="canon line")
    session_id = hook.get("session_id") or "unknown-session"
    transcript = hook.get("transcript_path", "")

    if mode == "session_start":
        base = store.get_branch_head(branch_id)
        parent_sl = None
        parent_external_session_id = None
        det = None
        if cfg.get("auto_fork", True) and transcript:
            hashes = fork.transcript_hashes(transcript)
            det = fork.detect_fork_from_hashes(
                store, session_id, hashes, min_prefix=int(cfg.get("min_fork_prefix", 3))
            )
            if det:
                parent_external_session_id = det["parent_session"]
                parent_branch = ops.current_branch_for_session(store, parent_external_session_id, branch_id)
                parent_sl = ops.latest_session_link_id(store, parent_external_session_id)
                base = store.get_branch_head(parent_branch)
                auto_branch_name = f"fork-{safe_id(session_id)[:10]}-{sha256_hex(str(session_id))[:6]}"
                branch_id = fork.create_branch(
                    store, project_id, auto_branch_name,
                    intent=f"auto-detected {det.get('relation', 'fork')} from {parent_external_session_id}",
                    base_checkpoint_id=base, from_branch=parent_branch,
                    parent_session_link_id=parent_sl,
                )
        if transcript:
            ingest.ingest_transcript(store, project_id, session_id, branch_id, transcript)
        ops.register_session(store, project_id, session_id, branch_id,
                             transcript_path=transcript, cwd=root, status="active",
                             base_checkpoint_id=base, parent_session_link_id=parent_sl,
                             parent_external_session_id=parent_external_session_id,
                             provider=provider,
                             fork_lcp=det.get("lcp") if det else None,
                             fork_relation=det.get("relation", "") if det else "",
                             fork_confidence=det.get("confidence") if det else None)
        return 0

    branch_id = ops.current_branch_for_session(store, session_id, branch_id)
    res = ingest.ingest_transcript(store, project_id, session_id, branch_id, transcript) if transcript else {"events": []}
    new_events = res.get("events", [])

    # No new content → no checkpoint, no head churn (P0-3). Still finalize on session end.
    if not new_events:
        if mode == "session_end":
            ops.end_session(store, project_id, session_id, branch_id, transcript, root, provider=provider)
            reindex.reindex(store)
        return 0

    seqs = [e.get("event_seq") for e in new_events]
    kind = _checkpoint_kind(mode)
    ck = ops.create_checkpoint(store, project_id, branch_id, kind=kind,
                               raw_event_start_seq=seqs[0], raw_event_end_seq=seqs[-1],
                               title=f"{kind} {session_id[:8]}", created_by="hook")
    extract_det.extract_events(store, project_id, branch_id, ck, new_events)

    if mode == "session_end":
        ops.end_session(store, project_id, session_id, branch_id, transcript, root, provider=provider)
        reindex.reindex(store)
        project.project_all(store)
    return 0


def run(mode: str) -> int:
    try:
        return _run_with_hook(mode, _read_input(), provider="claude_code")
    except Exception:
        return 0  # fail-soft: never break the session


def run_codex(mode: str = "auto") -> int:
    try:
        hook = _read_input()
        if mode == "auto":
            mode = _CODEX_MODE_BY_EVENT.get(hook.get("hook_event_name"), "turn_end")
        return _run_with_hook(mode, hook, provider="codex")
    except Exception:
        return 0  # fail-soft: never break the session


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    mode = "session_end"
    if "--mode" in argv:
        i = argv.index("--mode")
        if i + 1 < len(argv):
            mode = argv[i + 1]
    return run(mode)


if __name__ == "__main__":
    raise SystemExit(main())
