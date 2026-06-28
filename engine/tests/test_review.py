"""Regression tests for code review r1 P0/P1 fixes. Run: python tests/test_review.py

These cover the gaps the review identified — multi-branch isolation & fork inheritance
were the bugs hidden by single-branch-only coverage.
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, fork, hook, project  # noqa: E402
from basin.compile_pack import compile_context_pack  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def fresh():
    tmp = tempfile.mkdtemp(prefix="basin_rev_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_rev", "project_name": "rev"})
    ops.ensure_branch(store, "main", name="main")
    return store, "p_rev", tmp


def write_transcript(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for role, text in lines:
            f.write(json.dumps({"type": role, "message": {"role": role, "content": text}}) + "\n")


def stmt(s):
    return s


def main():
    # ---- P0-2 fork inheritance + P0-1 cross-branch isolation ----
    store, pid, tmp = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Use SQLite as the truth.", "storage-truth", "user_explicit")
    ops.promote_branch(store, "main")
    base = store.get_branch_head("main") or None
    feat = fork.create_branch(store, pid, "feat", base_checkpoint_id=base, from_branch="main")

    inh = ops.current_atoms(store, feat)
    check("P0-2 fork inherits parent atoms", len(inh) == 1 and inh[0]["statement"] == "Use SQLite as the truth.",
          f"feat atoms={[a['statement'] for a in inh]}")

    # structural change on feat only
    ops.stage_atom(store, pid, feat, None, "decision", "Use files as the truth.", "storage-truth", "user_explicit")
    ops.promote_branch(store, feat)
    main_now = ops.current_atoms(store, "main")
    feat_now = ops.current_atoms(store, feat)
    check("P0-1 main is isolated (still SQLite)", main_now[0]["statement"] == "Use SQLite as the truth.",
          main_now[0]["statement"])
    check("P0-1 feat diverged (files)", feat_now[0]["statement"] == "Use files as the truth.",
          feat_now[0]["statement"])
    check("P0-1 shared atom_id, different revisions", main_now[0]["atom_id"] == feat_now[0]["atom_id"]
          and main_now[0]["id"] != feat_now[0]["id"])

    # ---- COSMETIC keeps active atom settled, never a staged Change ----
    ops.stage_atom(store, pid, "main", None, "constraint", "Hook must never block.", "hook-block", "user_explicit")
    ops.promote_branch(store, "main")
    res = ops.stage_atom(store, pid, "main", None, "constraint", "hook must never block", "hook-block", "user_explicit")
    crev = store.latest_revision(res[0]) if res else None
    staged = ops.staged_candidates(store, "main")
    check("COSMETIC recorded as COSMETIC", crev and crev.get("change_kind") == "COSMETIC")
    check("COSMETIC not in staged Changes", all(a["atom_id"] != res[0] for a in staged))
    check("COSMETIC atom still active in Canon", any(a["atom_id"] == res[0] for a in ops.current_atoms(store, "main")))

    # ---- token-budget overflow spills into retrieval_manifest ----
    store2, pid2, tmp2 = fresh()
    for i in range(20):
        ops.stage_atom(store2, pid2, "main", None, "fact", ("word " * 120) + f"n{i}", f"big-{i}", "user_explicit")
    ops.promote_branch(store2, "main")
    _, _, pack = compile_context_pack(store2, pid2, "main", lod="brief")
    check("budget: always_load within brief 2000", pack["token_budget"]["always_load_used"] <= 2000,
          f"used={pack['token_budget']['always_load_used']}")
    check("budget: overflow -> retrieval_manifest", len(pack["retrieval_manifest"]) > 0,
          f"retrieve={len(pack['retrieval_manifest'])}")

    # ---- T5 do_not_load exclusion ----
    store3, pid3, tmp3 = fresh()
    a = ops.stage_atom(store3, pid3, "main", None, "decision", "Keep this one.", "keep", "user_explicit")[0]
    b = ops.stage_atom(store3, pid3, "main", None, "decision", "Exclude this one.", "drop", "user_explicit")[0]
    ops.promote_branch(store3, "main")
    store3.append_jsonl(store3.do_not_load_path, {"t": "do_not_load", "id": "dnl_1", "branch_id": "main",
                                                  "atom_id": b, "action": "exclude", "created_at": "x"})
    _, _, pack3 = compile_context_pack(store3, pid3, "main", lod="standard")
    decided = [d["atom"] for d in pack3["current_decisions"]]
    check("T5 do_not_load excludes atom", b not in decided and a in decided, f"decisions={decided}")
    check("T5 excluded listed in do_not_load", b in pack3["do_not_load"])

    # ---- P0-3 hook idempotency: no checkpoint / head churn on re-fire ----
    store4, pid4, tmp4 = fresh()
    tpath = os.path.join(tmp4, "t.jsonl")
    write_transcript(tpath, [("user", "We decided to use curses for the TUI."), ("user", "The hook must exit zero.")])
    hi = {"session_id": "s1", "transcript_path": tpath, "cwd": str(store4.root)}
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(hi)); hook.run("precompact"); sys.stdin = old
    n1 = len(ops.list_checkpoints(store4)); head1 = store4.get_branch_head("main")
    sys.stdin = io.StringIO(json.dumps(hi)); hook.run("precompact"); sys.stdin = old
    n2 = len(ops.list_checkpoints(store4)); head2 = store4.get_branch_head("main")
    check("P0-3 no extra checkpoint on re-fire", n1 == n2, f"{n1} -> {n2}")
    check("P0-3 branch head stable on re-fire", head1 == head2)

    # ---- SessionEnd writes ended status ----
    sys.stdin = io.StringIO(json.dumps({"session_id": "s1", "transcript_path": tpath, "cwd": str(store4.root)}))
    hook.run("session_end"); sys.stdin = old
    sess = [s for s in ops.list_sessions(store4) if s.get("external_session_id") == "s1"]
    check("SessionEnd marks ended", sess and sess[0].get("status") == "ended", f"status={sess and sess[0].get('status')}")

    # ---- projection determinism (no clock churn) ----
    p1 = project.project_canon(store4); c1 = open(p1, encoding="utf-8").read()
    c2_path = project.project_canon(store4); c2 = open(c2_path, encoding="utf-8").read()
    check("projection deterministic (CANON.md stable)", c1 == c2)

    # ---- pack YAML has no python-repr leakage (emitter sanity) ----
    _, ppath, _ = compile_context_pack(store3, pid3, "main", lod="standard")
    txt = open(ppath, encoding="utf-8").read()
    check("pack YAML: no dict-repr leak", "{'" not in txt and "[{'" not in txt)

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
