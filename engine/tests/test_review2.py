"""Regression tests for code-review r2 fixes (P0/P1/P2 from the parallel audit).
Run: python tests/test_review2.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, fork, project, reindex  # noqa: E402
from basin.compile_pack import compile_context_pack  # noqa: E402
from basin.tui_model import Model  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def fresh():
    tmp = tempfile.mkdtemp(prefix="basin_r2_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_r2", "project_name": "r2"})
    ops.ensure_branch(store, "main", name="main")
    return store, "p_r2", tmp


def main():
    # ---- security: path traversal contained ----
    store, pid, tmp = fresh()
    ev = store.events_path("../../../../HOOK_PWN")
    ra = store.ref_atoms("../../../X")
    inside = os.path.realpath(ev).startswith(os.path.realpath(str(store.dir)))
    check("security: events_path traversal contained", inside and "/" not in ev.name and ".." not in ev.name)
    check("security: ref_atoms traversal contained", "/" not in ra.name and ".." not in ra.name)

    # ---- P0: checkpoint id no longer collides on duplicate save messages ----
    ops.stage_atom(store, pid, "main", None, "decision", "A.", "a", "user_explicit")
    ops.promote_branch(store, "main")
    ops.create_checkpoint(store, pid, "main", "semantic_commit",
                          parent_checkpoint_id=store.get_branch_head("main"), title="save")
    h1 = store.get_branch_head("main")
    ops.stage_atom(store, pid, "main", None, "decision", "B.", "b", "user_explicit")
    ops.promote_branch(store, "main")
    ops.create_checkpoint(store, pid, "main", "semantic_commit",
                          parent_checkpoint_id=store.get_branch_head("main"), title="save")
    h2 = store.get_branch_head("main")
    sc = [c for c in ops.list_checkpoints(store) if c["kind"] == "semantic_commit"]
    check("P0: two saves same msg -> two checkpoints", len(sc) == 2, f"n={len(sc)}")
    check("P0: branch head advanced", h1 != h2)

    # ---- P1: revision branch_id attribution across branches ----
    store2, pid2, tmp2 = fresh()
    ops.stage_atom(store2, pid2, "main", None, "decision", "alpha", "x", "user_explicit")
    ops.promote_branch(store2, "main")
    feat = fork.create_branch(store2, pid2, "feat", base_checkpoint_id=store2.get_branch_head("main"), from_branch="main")
    ops.stage_atom(store2, pid2, "main", None, "decision", "beta", "x", "user_explicit")
    ops.stage_atom(store2, pid2, feat, None, "decision", "beta", "x", "user_explicit")
    ops.promote_branch(store2, "main")
    ops.promote_branch(store2, feat)
    mrev = ops.current_atoms(store2, "main")[0]
    frev = ops.current_atoms(store2, feat)[0]
    check("P1: main revision stamped branch=main", mrev["branch_id"] == "main", mrev["branch_id"])
    check("P1: feat revision stamped branch=feat", frev["branch_id"] == feat, frev["branch_id"])
    check("P1: distinct revision rows per branch", mrev["id"] != frev["id"])

    # ---- P1: COSMETIC re-wording of a still-staged candidate stays visible in Changes ----
    store3, pid3, tmp3 = fresh()
    ops.stage_atom(store3, pid3, "main", None, "decision", "Use SQLite.", "s", "user_explicit")
    ops.stage_atom(store3, pid3, "main", None, "decision", "use sqlite", "s", "user_explicit")  # cosmetic, still candidate
    staged = ops.staged_candidates(store3, "main")
    check("P1: cosmetic candidate visible in Changes", len(staged) == 1, f"staged={len(staged)}")

    # ---- P1: continuity test derived from this pack's atoms ----
    store4, pid4, tmp4 = fresh()
    ops.stage_atom(store4, pid4, "main", None, "decision", "Adopt the basin layout.", "basin-layout", "user_explicit")
    ops.stage_atom(store4, pid4, "main", None, "rejected_path", "No vector DB.", "no-vec", "user_explicit")
    ops.promote_branch(store4, "main")
    _, _, pack = compile_context_pack(store4, pid4, "main", lod="standard")
    ct = " ".join(pack["continuity_test"])
    check("P1: continuity references real decision subject", "basin-layout" in ct, ct)
    check("P1: continuity drops stale 'UX priority for v1'", "UX priority for v1" not in ct)

    # ---- P1: pack + CANON.md cover all 11 atom types ----
    store5, pid5, tmp5 = fresh()
    for t, s in [("fact", "f"), ("assumption", "as"), ("task", "tk"), ("artifact", "ar"), ("decision", "d")]:
        ops.stage_atom(store5, pid5, "main", None, t, f"{t} statement", s, "user_explicit")
    ops.promote_branch(store5, "main")
    _, _, pack5 = compile_context_pack(store5, pid5, "main", lod="standard")
    check("P1: pack has facts/tasks/assumptions/artifacts",
          pack5["facts"] and pack5["tasks"] and pack5["assumptions"] and pack5["artifacts"])
    canon = project.project_canon(store5)
    txt = open(canon, encoding="utf-8").read()
    check("P1: CANON.md renders Facts/Tasks/Assumptions/Artifacts",
          all(h in txt for h in ("## Facts", "## Tasks", "## Assumptions", "## Artifacts")))

    # ---- P2: do_not_load writer + settle ----
    store6, pid6, tmp6 = fresh()
    keep = ops.stage_atom(store6, pid6, "main", None, "decision", "Keep.", "keep", "user_explicit")[0]
    drop = ops.stage_atom(store6, pid6, "main", None, "decision", "Drop.", "drop", "user_explicit")[0]
    ops.promote_branch(store6, "main")
    ops.set_do_not_load(store6, pid6, "main", drop, action="exclude")
    _, _, pack6 = compile_context_pack(store6, pid6, "main", lod="standard")
    decided = [d["atom"] for d in pack6["current_decisions"]]
    check("P2: do_not_load writer excludes atom", drop not in decided and keep in decided)

    spike = fork.create_branch(store6, pid6, "spike", base_checkpoint_id=store6.get_branch_head("main"), from_branch="main")
    ops.stage_atom(store6, pid6, spike, None, "decision", "Spike new.", "spike-new", "user_explicit")
    ops.promote_branch(store6, spike)
    res = ops.settle_branch(store6, spike, "main")
    check("P2: settle merges branch atoms into canon", res["merged"] >= 1, f"merged={res['merged']}")
    check("P2: settled atom now in canon", any(a["statement"] == "Spike new." for a in ops.current_atoms(store6, "main")))

    # ---- P1: Proposals rows align with merge targets ----
    m = Model(tmp6)
    m.current_branch = spike
    rows = m.proposals_rows()
    check("P1: proposal row map aligns with rows", len(m._proposal_row_atoms) == len(rows))

    # ---- P1: reindex atomic swap leaves no build leftovers ----
    store7, pid7, tmp7 = fresh()
    ops.stage_atom(store7, pid7, "main", None, "decision", "x", "x", "user_explicit")
    ops.promote_branch(store7, "main")
    reindex.reindex(store7)
    leftovers = list((store7.dir / ".index").glob("basin.build.*.db"))
    check("P1: reindex leaves no temp build db", store7.index_db.exists() and not leftovers)

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
