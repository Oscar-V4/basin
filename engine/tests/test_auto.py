"""Automatic capture/fork tests. Run: python tests/test_auto.py"""
import io
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, fork, hook, ingest, ops, reindex  # noqa: E402
from basin.__main__ import main as cli_main  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def write_transcript(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for role, text in lines:
            f.write(json.dumps({"type": role, "timestamp": "2026-06-29T10:00:00Z",
                                "message": {"role": role, "content": text}}) + "\n")


def run_session_start(session_id, transcript, root, provider="claude"):
    old = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps({
            "session_id": session_id,
            "transcript_path": transcript,
            "cwd": root,
        }))
        return hook.run("session_start") if provider == "claude" else hook.run_codex("SessionStart")
    finally:
        sys.stdin = old


def main():
    tmp = tempfile.mkdtemp(prefix="basin_auto_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_auto", "project_name": "auto"})
    pid = "p_auto"
    ops.ensure_branch(store, "main", name="main")

    check("default config: auto_fork on", store.config().get("auto_fork") is True)
    manual_tmp = tempfile.mkdtemp(prefix="basin_auto_manual_")
    rc_manual = cli_main(["--root", manual_tmp, "setup", "--manual-fork"])
    check("cli: setup --manual-fork opts out", rc_manual == 0 and core.Store(manual_tmp).config().get("auto_fork") is False)
    store.set_config("auto_fork", False)
    rc_auto = cli_main(["--root", tmp, "auto"])
    check("cli: basin auto enables existing project", rc_auto == 0 and store.config().get("auto_fork") is True)

    parent_t = os.path.join(tmp, "parent.jsonl")
    child_t = os.path.join(tmp, "child.jsonl")
    prefix_t = os.path.join(tmp, "prefix_child.jsonl")
    shared = [("user", "Decision: use Basin for context capture."),
              ("assistant", "Constraint: keep hooks fail-soft."),
              ("user", "Open question: how should side chats branch?")]
    write_transcript(parent_t, shared + [("assistant", "Decision: parent continues on main.")])
    write_transcript(child_t, shared + [("assistant", "Decision: child explores automatic branching.")])
    write_transcript(prefix_t, shared)

    parent_sl = ops.register_session(store, pid, "parent-s", "main", transcript_path=parent_t)
    ingest.ingest_transcript(store, pid, "parent-s", "main", parent_t)

    hashes = fork.transcript_hashes(child_t)
    det = fork.detect_fork_from_hashes(store, "child-s", hashes, min_prefix=3)
    check("detect: diverged child finds parent", det and det["parent_session"] == "parent-s", str(det))
    check("detect: diverged relation recorded", det and det["relation"] == "diverged", str(det))

    prefix_det = fork.detect_fork_from_hashes(store, "prefix-s", fork.transcript_hashes(prefix_t), min_prefix=3)
    check("detect: prefix-only side chat finds parent", prefix_det and prefix_det["relation"] == "prefix", str(prefix_det))

    rc = run_session_start("child-s", child_t, tmp)
    sessions = [s for s in store.read_jsonl(store.sessions_path) if s.get("external_session_id") == "child-s"]
    child = sessions[-1] if sessions else {}
    branches = ops.list_branches(store)
    child_branch = child.get("branch_id")
    raw = store.read_jsonl(store.events_path("child-s"))

    check("hook: session_start exits 0", rc == 0)
    check("hook: auto fork created a non-main branch", child_branch and child_branch != "main", child_branch)
    check("hook: parent session link stored", child.get("parent_session_link_id") == parent_sl, str(child))
    check("hook: parent external session stored", child.get("parent_external_session_id") == "parent-s", str(child))
    check("hook: fork metadata stored", child.get("fork_lcp") == 3 and child.get("fork_relation") == "diverged", str(child))
    check("hook: raw events attributed to fork branch",
          raw and {r.get("branch_id") for r in raw if r.get("t") == "raw_event"} == {child_branch}, str(raw))

    branch = next((b for b in branches if b.get("branch_id") == child_branch), {})
    check("branch: parent metadata stored",
          branch.get("parent_branch_id") == "main" and branch.get("parent_session_link_id") == parent_sl, str(branch))

    local_atom = ops.stage_atom(store, pid, child_branch, None, "decision",
                                "Child branch keeps its local decision.",
                                "auto-idempotency", "user_explicit")
    ops.promote_branch(store, child_branch)
    before_refs = store.read_atom_refs(child_branch)
    raw_count = len(store.read_jsonl(store.events_path("child-s")))
    rc_again = run_session_start("child-s", child_t, tmp)
    after_refs = store.read_atom_refs(child_branch)
    check("hook: repeated session_start exits 0", rc_again == 0)
    check("branch: repeated auto-fork does not reset local refs",
          local_atom[0] in after_refs and after_refs == before_refs, str(after_refs))
    check("ingest: repeated session_start is idempotent",
          len(store.read_jsonl(store.events_path("child-s"))) == raw_count)

    counts = reindex.reindex(store)
    con = sqlite3.connect(store.index_db)
    row = con.execute(
        "SELECT parent_external_session_id, fork_lcp, fork_relation FROM session_link "
        "WHERE external_session_id='child-s'"
    ).fetchone()
    brow = con.execute(
        "SELECT parent_branch_id, parent_session_link_id FROM branch WHERE branch_id=?",
        (child_branch,),
    ).fetchone()
    con.close()
    check("reindex: schema still has 8 tables", counts["_tables"] == 8, f"tables={counts['_tables']}")
    check("reindex: session fork metadata queryable", row == ("parent-s", 3, "diverged"), str(row))
    check("reindex: branch parent metadata queryable", brow == ("main", parent_sl), str(brow))

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    print(f"workdir: {tmp}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
