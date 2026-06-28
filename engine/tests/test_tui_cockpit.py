"""TUI cockpit model tests. Run: python tests/test_tui_cockpit.py"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, fork  # noqa: E402
from basin.tui import _layout_segments  # noqa: E402
from basin.tui_model import (  # noqa: E402
    Model, TABS, RIGHT_ALIGN, display_width, fit_display_width,
    infer_merge_source_branch, row, row_text, split_row,
)

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def build_fixture():
    tmp = tempfile.mkdtemp(prefix="basin_tui_cockpit_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_tui", "project_name": "tui"})
    pid = "p_tui"
    main = ops.ensure_branch(store, "main", name="main", intent="canon line")["branch_id"]

    ck_main = ops.create_checkpoint(store, pid, main, "session_end",
                                    raw_event_start_seq=1, raw_event_end_seq=3,
                                    title="session: cockpit baseline", created_by="hook")
    decision = ops.stage_atom(store, pid, main, ck_main, "decision",
                              "Basin's TUI should show context history visually.",
                              "tui-history", "user_explicit",
                              source_raw_event_id="re_main_1", source_quote="Show history visually.",
                              confidence_score=0.92)[0]
    ops.stage_atom(store, pid, main, ck_main, "constraint",
                   "Keep the TUI stdlib-only and curses-backed.",
                   "tui-stdlib", "artifact_declared",
                   source_raw_event_id="re_main_2", confidence_score=0.86)
    ops.promote_branch(store, main)
    ops.create_checkpoint(store, pid, main, "semantic_commit",
                          parent_checkpoint_id=store.get_branch_head(main),
                          title="save: baseline context", created_by="user")

    cockpit = fork.create_branch(store, pid, "cockpit", intent="polish the TUI cockpit",
                                 base_checkpoint_id=store.get_branch_head(main), from_branch=main)
    ck_cockpit = ops.create_checkpoint(store, pid, cockpit, "session_end",
                                       parent_checkpoint_id=store.get_branch_head(cockpit),
                                       raw_event_start_seq=4, raw_event_end_seq=6,
                                       title="session: orbit-grade cockpit", created_by="hook")
    changed = ops.stage_atom(store, pid, cockpit, ck_cockpit, "decision",
                             "Basin's TUI should show context atoms, branches, and checkpoints as a visual cockpit.",
                             "tui-history", "user_explicit",
                             source_raw_event_id="re_cockpit_4",
                             source_quote="Make it a visual cockpit for context history.",
                             confidence_score=0.96)[0]
    risk = ops.stage_atom(store, pid, cockpit, ck_cockpit, "risk",
                          "A sparse checkpoint graph can look empty unless each node carries diffstat.",
                          "sparse-rails", "assistant_proposed",
                          source_raw_event_id="re_cockpit_5", confidence_score=0.73)[0]
    ops.record_edge(store, pid, cockpit, risk, changed, "refines", confidence="ASSERTED",
                    source_raw_event_id="re_cockpit_5", created_by="test")
    ops.promote_branch(store, cockpit)

    docs = fork.create_branch(store, pid, "문서", intent="settle docs note",
                              base_checkpoint_id=store.get_branch_head(main), from_branch=main)
    ck_docs = ops.create_checkpoint(store, pid, docs, "session_end",
                                    parent_checkpoint_id=store.get_branch_head(docs),
                                    raw_event_start_seq=7, raw_event_end_seq=8,
                                    title="session: docs note", created_by="hook")
    ops.stage_atom(store, pid, docs, ck_docs, "task",
                   "Document the TUI cockpit status in README.",
                   "readme-tui-status", "assistant_proposed",
                   source_raw_event_id="re_docs_7", confidence_score=0.68)
    ops.promote_branch(store, docs)
    ops.settle_branch(store, docs, main, project_id=pid)
    ops.create_checkpoint(store, pid, main, "merge",
                          parent_checkpoint_id=store.get_branch_head(main),
                          title="settle 문서", created_by="user")

    ops.stage_atom(store, pid, main, None, "decision",
                   "Basin's TUI should prioritize proposal review before graph polish.",
                   "tui-history", "user_explicit", confidence_score=0.91)
    ops.promote_branch(store, main)
    return store, tmp, {"main": main, "cockpit": cockpit, "docs": docs,
                        "decision": decision, "changed": changed}


def styles(rows):
    return [style for r in rows for _text, style in r]


def main():
    store, tmp, ids = build_fixture()
    m = Model(tmp)

    check("Display width: Korean glyphs use terminal cells",
          display_width("브랜치") == 6 and display_width("문서 · meta") == 11)
    check("Display width: combining marks do not advance columns",
          display_width("e\u0301") == 1 and fit_display_width("e\u0301x", 1) == "e\u0301")
    draws = _layout_segments(1, 20, row([("왼쪽 meta", "normal")], [("문서", "muted")]))
    right_draw = next((d for d in draws if d[2] == "muted"), None)
    check("Renderer: RIGHT_ALIGN places Korean meta by display width",
          right_draw == (16, "문서", "muted"), str(draws))
    clipped = _layout_segments(1, 6, [("문서abc", "normal")])
    check("Renderer: draw clipping preserves wide glyph boundaries",
          clipped == [(1, "문서a", "normal")], str(clipped))

    threads = m.threads_rows()
    flat_threads = "\n".join(row_text(r) for r in threads)
    check("Threads: fork and merge rail connectors render",
          "╮" in flat_threads and "╯" in flat_threads and ("├" in flat_threads or "┤" in flat_threads),
          flat_threads)
    check("Threads: checkpoint diffstat renders",
          "+1 ~1 −0" in flat_threads or "+2 ~0 −0" in flat_threads, flat_threads)

    branch_rows = m.branches_rows()
    canon_rows = m.canon_rows()
    m.current_branch = ids["cockpit"]
    proposal_rows = m.proposals_rows()
    check("Badges: branch chip styles appear", any(s.startswith("chip_") for s in styles(branch_rows)))
    check("Badges: atom type badge styles appear", "badge_decision" in styles(canon_rows))
    check("Badges: conflict badge appears in Proposals", "badge_conflict" in styles(proposal_rows))

    for label, rows in (("Threads", threads), ("Branches", branch_rows), ("Canon", canon_rows)):
        left, right = split_row(next(r for r in rows if any(s == RIGHT_ALIGN for _t, s in r)))
        check(f"Right meta: {label} separates left and right segments", bool(left) and bool(right),
              row_text(left + [("", RIGHT_ALIGN)] + right))

    kinds = m._proposal_row_kind
    conflict_idx = kinds.index("conflict")
    detail = m.detail_rows("Proposals", conflict_idx)
    detail_text = "\n".join(row_text(r) for r in detail)
    check("Details: atom panel includes provenance and authority",
          "Provenance" in detail_text and "Authority / confidence" in detail_text, detail_text)
    check("Details: supersedes chain and related edges render",
          "Supersedes chain" in detail_text and "Related edges" in detail_text
          and "supersedes" in detail_text and "refines" in detail_text, detail_text)

    merge_checkpoint = next(c for c in m.checkpoints if c.get("kind") == "merge")
    check("Merge source: legacy settle-title inference resolves Korean branch name",
          infer_merge_source_branch(merge_checkpoint, m.branch_by_name, m.branch_by_id) == ids["docs"],
          str(merge_checkpoint))

    for tab in TABS:
        try:
            rows = m.render(tab)
            ok = isinstance(rows, list) and rows
        except Exception as e:  # noqa
            ok = False
            print("   render error:", tab, e)
        check(f"Every tab renders: {tab}", ok)

    proc = subprocess.run([sys.executable, "-m", "basin", "--root", tmp, "tui", "--selftest"],
                          cwd=ENGINE, text=True, capture_output=True, check=False)
    out = proc.stdout + proc.stderr
    check("Selftest: populated fixture prints cockpit rows",
          proc.returncode == 0 and "--- Threads" in out and "cockpit" in out and "+1 ~1" in out,
          out[:800])

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    print(f"workdir: {store.root}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
