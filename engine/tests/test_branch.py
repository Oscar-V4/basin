"""Two-branch regression tests — the fork use case (review r1 P0-1/P0-2/P0-3).
Run: python tests/test_branch.py
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from basin import core, ops, fork, hook  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "transcript_main.jsonl")
_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="basin_branch_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_b", "project_name": "b"})
    pid = "p_b"
    ops.ensure_branch(store, "main", name="main")

    # canon: settle one decision
    ops.stage_atom(store, pid, "main", None, "decision", "Use SQLite as the truth.", "storage", "user_explicit")
    ops.promote_branch(store, "main")
    main_atoms = ops.current_atoms(store, "main")
    check("main has 1 active atom", len(main_atoms) == 1)

    # fork inherits the canon (P0-2)
    feat = fork.create_branch(store, pid, "feat", base_checkpoint_id=store.get_branch_head("main"), from_branch="main")
    check("P0-2 fork inherits canon", len(ops.current_atoms(store, feat)) == 1,
          f"feat={len(ops.current_atoms(store, feat))}")

    # structural change on feat supersedes — branch-local
    ops.stage_atom(store, pid, feat, None, "decision", "Use files as the truth.", "storage", "user_explicit")
    ops.promote_branch(store, feat)

    main_now = ops.current_atoms(store, "main")
    feat_now = ops.current_atoms(store, feat)
    check("P0-1 main isolated (still SQLite)", main_now and "SQLite" in main_now[0]["statement"],
          f"main={main_now and main_now[0]['statement']}")
    check("P0-1 feat changed (files)", feat_now and "files" in feat_now[0]["statement"],
          f"feat={feat_now and feat_now[0]['statement']}")
    check("supersede is branch-local (revision_no=2 on feat)", feat_now and feat_now[0].get("revision_no") == 2)

    # P0-3 hook idempotency: same transcript twice -> no checkpoint churn
    hi = {"session_id": "s_x", "transcript_path": FIX, "cwd": str(store.root)}
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(hi)); hook.run("precompact"); sys.stdin = old
    cks1 = len([c for c in store.read_jsonl(store.checkpoints_path) if c.get("t") == "checkpoint"])
    head1 = store.get_branch_head("main")
    sys.stdin = io.StringIO(json.dumps(hi)); hook.run("precompact"); sys.stdin = old
    cks2 = len([c for c in store.read_jsonl(store.checkpoints_path) if c.get("t") == "checkpoint"])
    head2 = store.get_branch_head("main")
    check("P0-3 no checkpoint churn on re-fire", cks1 == cks2, f"{cks1} -> {cks2}")
    check("P0-3 head stable on re-fire", head1 == head2)

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
