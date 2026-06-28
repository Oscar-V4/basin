"""Codex rollout adapter tests. Run: python tests/test_codex.py"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, hook, ingest, ops  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def codex_rows():
    return [
        {"timestamp": "2026-06-29T01:00:00Z", "type": "session_meta",
         "payload": {"session_id": "codex-s", "cwd": "/tmp/project"}},
        {"timestamp": "2026-06-29T01:00:01Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "skip UI event"}},
        {"timestamp": "2026-06-29T01:00:02Z", "type": "turn_context",
         "payload": {"turn_id": "t1", "model": "gpt-5.5"}},
        {"timestamp": "2026-06-29T01:00:03Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "Decision: capture Codex rollouts."}]}},
        {"timestamp": "2026-06-29T01:00:04Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Constraint: keep stdlib-only."}]}},
        {"timestamp": "2026-06-29T01:00:05Z", "type": "response_item",
         "payload": {"type": "message", "role": "developer",
                     "content": [{"type": "text", "text": "System note: developer maps to system."}]}},
        {"timestamp": "2026-06-29T01:00:06Z", "type": "response_item",
         "payload": {"type": "reasoning", "summary": []}},
        {"timestamp": "2026-06-29T01:00:07Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command"}},
    ]


def test_parse_codex(tmp):
    path = os.path.join(tmp, "codex.jsonl")
    write_jsonl(path, codex_rows())
    evs = ingest.parse_transcript(path)
    check("codex parse: skips non-message rollout rows", len(evs) == 3, f"events={len(evs)}")
    check("codex parse: maps roles", [e["event_type"] for e in evs] == ["user", "assistant", "system"],
          str([e["event_type"] for e in evs]))
    check("codex parse: extracts non-empty text", all(e["text"] for e in evs), str(evs))
    check("codex parse: developer maps to system",
          evs[2]["event_type"] == "system" and "developer maps" in evs[2]["text"], str(evs[2]))


def test_parse_claude_regression(tmp):
    path = os.path.join(tmp, "claude.jsonl")
    rows = [
        {"type": "assistant", "timestamp": "2026-06-29T02:00:00Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Claude text"}]}},
        {"type": "user", "timestamp": "2026-06-29T02:00:01Z",
         "message": {"role": "user", "content": "User text"}},
    ]
    write_jsonl(path, rows)
    check("claude parse: existing shape unchanged", ingest.parse_transcript(path) == [
        {"event_type": "assistant", "text": "Claude text", "occurred_at": "2026-06-29T02:00:00Z"},
        {"event_type": "user", "text": "User text", "occurred_at": "2026-06-29T02:00:01Z"},
    ])


def test_codex_hook_contract(tmp):
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_codex", "project_name": "codex"})
    ops.ensure_branch(store, "main", name="main")
    transcript = os.path.join(tmp, "codex.jsonl")
    write_jsonl(transcript, codex_rows())

    old = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": "codex-s",
            "transcript_path": transcript,
            "cwd": tmp,
        }))
        rc_start = hook.run_codex("auto")
        turn_rows = codex_rows() + [
            {"timestamp": "2026-06-29T01:00:08Z", "type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "Open question: should Codex capture turns?"}]}}
        ]
        write_jsonl(transcript, turn_rows)
        sys.stdin = io.StringIO(json.dumps({
            "hook_event_name": "Stop",
            "session_id": "codex-s",
            "transcript_path": transcript,
            "cwd": tmp,
        }))
        rc_stop = hook.run_codex("auto")
    finally:
        sys.stdin = old

    raw = store.read_jsonl(store.events_path("codex-s"))
    sessions = store.read_jsonl(store.sessions_path)
    checkpoints = store.read_jsonl(store.checkpoints_path)
    check("codex hook: fail-soft exit 0", rc_start == 0 and rc_stop == 0)
    check("codex hook: registered provider", sessions and sessions[-1].get("provider") == "codex", str(sessions))
    check("codex hook: captured rollout events", len(raw) == 4, f"raw={len(raw)}")
    check("codex hook: Stop maps to turn_end without finalizing",
          any(c.get("kind") == "turn_end" for c in checkpoints)
          and not any(c.get("kind") == "session_end" for c in checkpoints)
          and sessions[-1].get("status") == "active",
          f"checkpoints={checkpoints} sessions={sessions}")


def test_codex_session_end_without_start_preserves_provider(tmp):
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_codex_end", "project_name": "codex-end"})
    old = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps({
            "hook_event_name": "SessionEnd",
            "session_id": "codex-end-only",
            "cwd": tmp,
        }))
        rc = hook.run_codex("auto")
    finally:
        sys.stdin = old

    sessions = store.read_jsonl(store.sessions_path)
    check("codex hook: SessionEnd without SessionStart preserves provider",
          rc == 0
          and sessions
          and sessions[-1].get("provider") == "codex"
          and sessions[-1].get("status") == "ended",
          str(sessions))


def test_codex_installer_tolerates_empty_hook_entries(tmp):
    repo = os.path.dirname(ENGINE)
    codex_dir = os.path.join(tmp, "codex-home")
    os.makedirs(codex_dir, exist_ok=True)
    hooks_path = os.path.join(codex_dir, "hooks.json")
    with open(hooks_path, "w", encoding="utf-8") as f:
        json.dump({"hooks": {"Stop": [
            {"hooks": []},
            {"hooks": [
                {"type": "command", "command": "existing", "timeout": 1},
                {"type": "command", "command": "~/.codex/hooks/basin-codex-turn-end.sh", "timeout": 10},
            ]},
        ]}}, f)

    env = dict(os.environ)
    env["CODEX_DIR"] = codex_dir
    res = subprocess.run(["bash", os.path.join(repo, "plugin", "install-codex.sh"), "--no-pip", "--copy"],
                         env=env, capture_output=True, text=True, timeout=20)
    merged = json.load(open(hooks_path, encoding="utf-8"))
    stop_entries = merged.get("hooks", {}).get("Stop", [])
    turn_end_count = sum(
        hook.get("command") == "~/.codex/hooks/basin-codex-turn-end.sh"
        for entry in stop_entries
        for hook in entry.get("hooks", [])
    )
    check("codex installer: empty existing hook entry does not crash",
          res.returncode == 0 and turn_end_count == 1,
          res.stderr or str(merged))


def test_claude_installer_tolerates_empty_hook_entries(tmp):
    repo = os.path.dirname(ENGINE)
    claude_dir = os.path.join(tmp, "claude-home")
    os.makedirs(claude_dir, exist_ok=True)
    settings_path = os.path.join(claude_dir, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"hooks": {"SessionStart": [
            {"hooks": []},
            {"hooks": [
                {"type": "command", "command": "existing", "timeout": 1},
                {"type": "command", "command": "~/.claude/hooks/basin-session-start.sh", "timeout": 10},
            ]},
        ]}}, f)

    env = dict(os.environ)
    env["CLAUDE_DIR"] = claude_dir
    res = subprocess.run(["bash", os.path.join(repo, "plugin", "install.sh"), "--no-pip", "--copy"],
                         env=env, capture_output=True, text=True, timeout=20)
    merged = json.load(open(settings_path, encoding="utf-8"))
    session_entries = merged.get("hooks", {}).get("SessionStart", [])
    session_start_count = sum(
        hook.get("command") == "~/.claude/hooks/basin-session-start.sh"
        for entry in session_entries
        for hook in entry.get("hooks", [])
    )
    check("claude installer: empty existing hook entry does not crash",
          res.returncode == 0 and session_start_count == 1,
          res.stderr or str(merged))


def main():
    tmp = tempfile.mkdtemp(prefix="basin_codex_")
    test_parse_codex(tmp)
    test_parse_claude_regression(tmp)
    test_codex_hook_contract(tmp)
    test_codex_session_end_without_start_preserves_provider(tmp)
    test_codex_installer_tolerates_empty_hook_entries(tmp)
    test_claude_installer_tolerates_empty_hook_entries(tmp)
    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    print(f"workdir: {tmp}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
