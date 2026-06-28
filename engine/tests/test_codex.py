"""Codex rollout adapter tests. Run: python tests/test_codex.py"""
import io
import json
import os
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


def main():
    tmp = tempfile.mkdtemp(prefix="basin_codex_")
    test_parse_codex(tmp)
    test_parse_claude_regression(tmp)
    test_codex_hook_contract(tmp)
    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    print(f"workdir: {tmp}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
