"""Transcript -> raw_event JSONL (append-only truth).

Tolerant of Claude Code and Codex transcript shapes. Each event carries a
raw_hash over (event_type, normalized text) — the key to deterministic fork
detection: a forked thread shares an identical raw_hash prefix with its parent,
then diverges.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import Store, new_id, now_iso, sha256_hex, norm_ws


_CODEX_RECORD_TYPES = {"response_item", "session_meta", "event_msg", "turn_context"}
_CODEX_ROLE_MAP = {"user": "user", "assistant": "assistant", "developer": "system"}


def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                elif block.get("type") == "tool_result":
                    parts.append(_text_of(block.get("content")))
                elif "text" in block:
                    parts.append(str(block["text"]))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return _text_of(content.get("content"))
    return str(content)


def _event_type(line: dict) -> str:
    t = line.get("type")
    msg = line.get("message") if isinstance(line.get("message"), dict) else {}
    role = line.get("role") or msg.get("role")
    if role in ("user", "assistant", "system"):
        return role
    if t in ("user", "assistant", "system"):
        return t
    return t or role or "unknown"


def _is_codex_record(line: dict) -> bool:
    return isinstance(line.get("payload"), dict) and line.get("type") in _CODEX_RECORD_TYPES


def _codex_event(line: dict) -> dict | None:
    payload = line.get("payload") if isinstance(line.get("payload"), dict) else {}
    if line.get("type") != "response_item" or payload.get("type") != "message":
        return None
    role = _CODEX_ROLE_MAP.get(payload.get("role"), payload.get("role") or "unknown")
    return {
        "event_type": role,
        "text": _text_of(payload.get("content")),
        "occurred_at": (
            line.get("timestamp")
            or payload.get("timestamp")
            or payload.get("occurred_at")
            or payload.get("started_at")
            or now_iso()
        ),
    }


def _claude_code_event(line: dict) -> dict:
    msg = line.get("message") if isinstance(line.get("message"), dict) else line
    return {
        "event_type": _event_type(line),
        "text": _text_of(line.get("content", msg.get("content"))),
        "occurred_at": line.get("timestamp") or line.get("occurred_at") or now_iso(),
    }


def raw_hash(event_type: str, text: str) -> str:
    return "sha256:" + sha256_hex(event_type + "\n" + norm_ws(text))[:32]


def parse_transcript(transcript_path: str | Path) -> list[dict]:
    p = Path(transcript_path)
    out = []
    if not p.exists():
        return out
    raw_lines = p.read_text(encoding="utf-8").splitlines()
    dialect = "claude_code"
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict) and _is_codex_record(line):
            dialect = "codex"
        break

    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(line, dict):
            continue
        if dialect == "codex":
            ev = _codex_event(line)
            if ev is not None:
                out.append(ev)
        else:
            out.append(_claude_code_event(line))
    return out


def ingest_transcript(store: Store, project_id: str, session_id: str, branch_id: str,
                      transcript_path: str | Path) -> dict:
    """Append any new raw_events for this session. Idempotent by event_seq."""
    parsed = parse_transcript(transcript_path)
    new = []
    # serialize concurrent hooks writing the same session's event log
    with store.lock(f"events-{session_id}"):
        existing = store.read_jsonl(store.events_path(session_id))
        have = {e.get("event_seq") for e in existing if e.get("t") == "raw_event"}
        for seq, ev in enumerate(parsed):
            if seq in have:
                continue
            rh = raw_hash(ev["event_type"], ev["text"])
            rec = {
                "t": "raw_event", "id": new_id("re", session_id, seq), "project_id": project_id,
                "session_id": session_id, "branch_id": branch_id, "event_seq": seq,
                "event_type": ev["event_type"], "occurred_at": ev["occurred_at"],
                "content_text": ev["text"], "token_estimate": max(1, len(ev["text"]) // 4),
                "raw_hash": rh, "created_at": now_iso(),
            }
            store.append_jsonl(store.events_path(session_id), rec)
            new.append(rec)
    return {"parsed": len(parsed), "new": len(new), "events": new}


def session_hashes(store: Store, session_id: str) -> list[str]:
    evs = [e for e in store.read_jsonl(store.events_path(session_id)) if e.get("t") == "raw_event"]
    evs.sort(key=lambda e: e.get("event_seq", 0))
    return [e.get("raw_hash", "") for e in evs]
