"""Merge-semantics regression tests (review r3, dimension: merge-semantics).

The fork->settle hero workflow must not be a blind last-writer-wins overwrite:
  - a divergent canon edit is a conflict, not silently lost (findings 11, 12);
  - a decision the canon owner rejected is not silently resurrected (finding 14);
  - settle records provenance edges (finding 13);
  - a cross-type contradiction (do X + X-rejected) is surfaced (finding 15);
  - a clean fast-forward overturn still merges (regression guard).

Run: python tests/test_merge.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, fork  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def fresh():
    tmp = tempfile.mkdtemp(prefix="basin_merge_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_m", "project_name": "m"})
    ops.ensure_branch(store, "main", name="main")
    return store, "p_m"


def stmts_on(store, branch):
    return {a["statement"] for a in ops.current_atoms(store, branch)}


def edges(store):
    return [e for e in store.read_jsonl(store.edges_path) if e.get("t") == "edge"]


def main():
    # ---- finding 11: divergent canon edit is a conflict, not a lost update ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "DB is Postgres.", "db", "user_explicit")
    ops.promote_branch(store, "main")
    base = store.get_branch_head("main")
    exp = fork.create_branch(store, pid, "exp", base_checkpoint_id=base, from_branch="main")
    ops.stage_atom(store, pid, exp, None, "decision", "DB is SQLite.", "db", "user_explicit")
    ops.promote_branch(store, exp)
    ops.stage_atom(store, pid, "main", None, "decision", "DB is MySQL after review.", "db", "user_explicit")
    ops.promote_branch(store, "main")               # canon advanced independently
    res = ops.settle_branch(store, exp, "main", project_id=pid)
    check("11: divergent settle surfaces a conflict", len(res["conflicts"]) >= 1, str(res))
    check("11: divergent settle does NOT overwrite canon (no lost update)",
          "DB is MySQL after review." in stmts_on(store, "main")
          and "DB is SQLite." not in stmts_on(store, "main"))

    # ---- finding 12: settling two divergent forks is order-independent (2nd conflicts, not wins) ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "v0.", "k", "user_explicit")
    ops.promote_branch(store, "main")
    b0 = store.get_branch_head("main")
    bx = fork.create_branch(store, pid, "bx", base_checkpoint_id=b0, from_branch="main")
    by = fork.create_branch(store, pid, "by", base_checkpoint_id=b0, from_branch="main")
    ops.stage_atom(store, pid, bx, None, "decision", "X says alpha.", "k", "user_explicit"); ops.promote_branch(store, bx)
    ops.stage_atom(store, pid, by, None, "decision", "Y says beta.", "k", "user_explicit"); ops.promote_branch(store, by)
    r1 = ops.settle_branch(store, bx, "main", project_id=pid)     # clean: canon at base
    r2 = ops.settle_branch(store, by, "main", project_id=pid)     # canon moved -> conflict
    check("12: first fork settles cleanly", r1["merged"] == 1 and not r1["conflicts"])
    check("12: second divergent fork conflicts instead of silently winning",
          len(r2["conflicts"]) >= 1 and "X says alpha." in stmts_on(store, "main"), str(r2))

    # ---- finding 14: settle does not resurrect a rejected decision ----
    store, pid = fresh()
    aid = ops.stage_atom(store, pid, "main", None, "decision", "Risky choice.", "r", "user_explicit")[0]
    ops.promote_branch(store, "main")
    rb = store.get_branch_head("main")
    spike = fork.create_branch(store, pid, "spike", base_checkpoint_id=rb, from_branch="main")
    ops.set_atom_lifecycle(store, "main", aid, "tracked", "rejected")   # canon owner kills it
    ops.stage_atom(store, pid, spike, None, "decision", "Risky choice, reworded.", "r", "user_explicit")
    ops.promote_branch(store, spike)
    res = ops.settle_branch(store, spike, "main", project_id=pid)
    check("14: rejected decision is NOT resurrected by settle",
          all("Risky" not in s for s in stmts_on(store, "main")), str(stmts_on(store, "main")))
    check("14: resurrection attempt surfaced as conflict", len(res["conflicts"]) >= 1, str(res))

    # ---- finding 13: a clean overturn records a supersedes edge (provenance) ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Approach A.", "ap", "user_explicit")
    ops.promote_branch(store, "main")
    h = store.get_branch_head("main")
    fb = fork.create_branch(store, pid, "fb", base_checkpoint_id=h, from_branch="main")
    ops.stage_atom(store, pid, fb, None, "decision", "Approach B (better).", "ap", "user_explicit")
    ops.promote_branch(store, fb)
    res = ops.settle_branch(store, fb, "main", project_id=pid)
    check("13: clean overturn merges", res["merged"] == 1 and not res["conflicts"])
    check("13: clean overturn records a supersedes edge",
          any(e["relation"] == "supersedes" and e["created_by"] == "merge" for e in edges(store)))
    check("13: canon now holds the overturning decision", "Approach B (better)." in stmts_on(store, "main"))

    # ---- finding 15: cross-type contradiction (do X + X-rejected) is surfaced ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Adopt plugin architecture.", "arch", "user_explicit")
    ops.promote_branch(store, "main")
    h = store.get_branch_head("main")
    mono = fork.create_branch(store, pid, "mono", base_checkpoint_id=h, from_branch="main")
    ops.stage_atom(store, pid, mono, None, "rejected_path", "Plugin architecture rejected; use monolith.", "arch", "user_explicit")
    ops.promote_branch(store, mono)
    res = ops.settle_branch(store, mono, "main", project_id=pid)
    check("15: cross-type contradiction surfaced (incoming reject vs surviving canon decision)",
          len(res["contradictions"]) >= 1, str(res))
    check("15: contradicts edge recorded",
          any(e["relation"] == "contradicts" and e["created_by"] == "merge" for e in edges(store)))

    # ---- complementary case: a fork that rejects X AND chooses Y is NOT flagged as contradiction ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Build a separate GUI app.", "ui", "user_explicit")
    ops.promote_branch(store, "main")
    h = store.get_branch_head("main")
    tb = fork.create_branch(store, pid, "tui", base_checkpoint_id=h, from_branch="main")
    ops.stage_atom(store, pid, tb, None, "rejected_path", "Separate GUI app rejected.", "ui", "user_explicit")
    ops.stage_atom(store, pid, tb, None, "decision", "Use a curses TUI instead.", "ui", "user_explicit")
    ops.promote_branch(store, tb)
    res = ops.settle_branch(store, tb, "main", project_id=pid)
    check("15b: rejected-X + chose-Y from one fork is complementary, not flagged",
          not res["contradictions"] and "Use a curses TUI instead." in stmts_on(store, "main"), str(res))

    # ---- idempotence + force ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Base.", "z", "user_explicit"); ops.promote_branch(store, "main")
    h = store.get_branch_head("main")
    fz = fork.create_branch(store, pid, "fz", base_checkpoint_id=h, from_branch="main")
    ops.stage_atom(store, pid, fz, None, "decision", "Forked Z.", "z", "user_explicit"); ops.promote_branch(store, fz)
    ops.settle_branch(store, fz, "main", project_id=pid)
    r_again = ops.settle_branch(store, fz, "main", project_id=pid)
    check("idempotent: re-settle merges nothing new", r_again["merged"] == 0 and not r_again["conflicts"], str(r_again))
    # force overrides a divergence
    ops.stage_atom(store, pid, "main", None, "decision", "Canon moved again.", "z", "user_explicit"); ops.promote_branch(store, "main")
    forced = ops.settle_branch(store, fz, "main", project_id=pid, force=True)
    check("force: --force overrides the conflict", "Forked Z." in stmts_on(store, "main"), str(stmts_on(store, "main")))

    # ---- r4-1: a same-revision candidate inherited by a fork is PROMOTED on settle (no silent drop) ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Use staged thing.", "topic", "user_explicit")  # candidate, not promoted
    b = fork.create_branch(store, pid, "feat", base_checkpoint_id=store.get_branch_head("main"), from_branch="main")
    ops.promote_branch(store, b)                  # promote on the fork -> active there, same revision id
    res = ops.settle_branch(store, b, "main", project_id=pid)
    check("r4-1: same-rev candidate is promoted into canon, not a silent noop",
          "Use staged thing." in stmts_on(store, "main"), str(res))

    # ---- r4-2: a COSMETIC intermediate revision must not break the supersedes chain (false divergence) ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Use SQLite as truth.", "storage", "user_explicit")
    ops.promote_branch(store, "main")
    b = fork.create_branch(store, pid, "feat", base_checkpoint_id=store.get_branch_head("main"), from_branch="main")
    ops.stage_atom(store, pid, b, None, "decision", "use sqlite as truth", "storage", "user_explicit")        # COSMETIC
    ops.stage_atom(store, pid, b, None, "decision", "Use Postgres as truth.", "storage", "user_explicit")     # STRUCTURAL
    ops.promote_branch(store, b)
    res = ops.settle_branch(store, b, "main", project_id=pid)
    check("r4-2: cosmetic intermediate does not cause a false divergence conflict",
          not res["conflicts"] and "Use Postgres as truth." in stmts_on(store, "main"), str(res))

    # ---- r4-3: --force overrides a resurrection conflict ----
    store, pid = fresh()
    aid = ops.stage_atom(store, pid, "main", None, "decision", "Risky choice.", "r", "user_explicit")[0]
    ops.promote_branch(store, "main")
    sp = fork.create_branch(store, pid, "sp", base_checkpoint_id=store.get_branch_head("main"), from_branch="main")
    ops.set_atom_lifecycle(store, "main", aid, "tracked", "rejected")
    ops.stage_atom(store, pid, sp, None, "decision", "Risky choice, reworded.", "r", "user_explicit")
    ops.promote_branch(store, sp)
    blocked = ops.settle_branch(store, sp, "main", project_id=pid)                 # default: blocked
    forced = ops.settle_branch(store, sp, "main", project_id=pid, force=True)      # force: resurrects
    check("r4-3: resurrection blocked by default", all("Risky" not in s for s in stmts_on(store, "main")) or blocked["conflicts"])
    check("r4-3: --force resurrects intentionally", any("Risky" in s for s in stmts_on(store, "main")), str(forced))

    # ---- r4-4: a REAL same-fork contradiction (high word overlap) IS flagged (branch_id no longer suppresses) ----
    store, pid = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Base.", "arch", "user_explicit")
    ops.promote_branch(store, "main")
    b = fork.create_branch(store, pid, "b", base_checkpoint_id=store.get_branch_head("main"), from_branch="main")
    ops.stage_atom(store, pid, b, None, "decision", "Adopt plugin architecture fully.", "arch", "user_explicit")
    ops.stage_atom(store, pid, b, None, "rejected_path", "Plugin architecture rejected; use monolith.", "arch", "user_explicit")
    ops.promote_branch(store, b)
    res = ops.settle_branch(store, b, "main", project_id=pid)
    check("r4-4: same-fork real contradiction (do X + X-rejected) is flagged", len(res["contradictions"]) >= 1, str(res))

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
