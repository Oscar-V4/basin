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
    confidence_text, infer_merge_source_branch, row, row_text, split_row,
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
    merge_result = ops.settle_branch(store, docs, main, project_id=pid)
    ops.create_checkpoint(store, pid, main, "merge",
                          parent_checkpoint_id=store.get_branch_head(main),
                          title="settle 문서", summary_text="+1 new, ~0 changed, 0 conflict(s)",
                          created_by="user", source_branch_id=docs, merge_result=merge_result)

    forced = fork.create_branch(store, pid, "forced", intent="force merge display",
                                base_checkpoint_id=store.get_branch_head(main), from_branch=main)
    ck_forced = ops.create_checkpoint(store, pid, forced, "session_end",
                                      parent_checkpoint_id=store.get_branch_head(forced),
                                      raw_event_start_seq=9, raw_event_end_seq=10,
                                      title="session: forced branch", created_by="hook")
    ops.stage_atom(store, pid, forced, ck_forced, "decision",
                   "Use SQLite for the cockpit cache.",
                   "cockpit-cache", "user_explicit",
                   source_raw_event_id="re_forced_9", confidence_score=0.87)
    ops.promote_branch(store, forced)
    ops.stage_atom(store, pid, main, None, "decision",
                   "Use JSONL-only cache for the cockpit.",
                   "cockpit-cache", "user_explicit", confidence_score=0.89)
    ops.promote_branch(store, main)
    force_result = ops.settle_branch(store, forced, main, project_id=pid, force=True)
    ops.create_checkpoint(store, pid, main, "merge",
                          parent_checkpoint_id=store.get_branch_head(main),
                          title="settle forced", summary_text="+0 new, ~1 forced, 0 conflict(s)",
                          created_by="user", source_branch_id=forced, merge_result=force_result)

    ops.stage_atom(store, pid, main, None, "decision",
                   "Basin's TUI should prioritize proposal review before graph polish.",
                   "tui-history", "user_explicit", confidence_score=0.91)
    ops.stage_atom(store, pid, main, None, "task",
                   "Document the TUI cockpit status in README and docs.",
                   "readme-tui-status", "assistant_proposed",
                   confidence_score=0.72)
    bad_conf = ops.stage_atom(store, pid, main, None, "task",
                              "TUI should tolerate malformed confidence scores.",
                              "bad-confidence", "tool_observed",
                              confidence_score="high")[0]
    ops.promote_branch(store, main)
    return store, tmp, {"main": main, "cockpit": cockpit, "docs": docs, "forced": forced,
                        "decision": decision, "changed": changed, "bad_conf": bad_conf}


def styles(rows):
    return [style for r in rows for _text, style in r]


def main():
    compat_tmp = tempfile.mkdtemp(prefix="basin_ck_compat_")
    compat_store = core.Store(compat_tmp)
    compat_store.scaffold({"project_id": "p_compat", "project_name": "compat"})
    compat_ck = ops.create_checkpoint(compat_store, "p_compat", "main", "manual", title="compat")
    legacy_ck = core.new_id("ck", "p_compat", "main", "manual", None, None, "compat", "", None)
    check("Checkpoint ids: optional metadata absent preserves legacy id", compat_ck == legacy_ck,
          "" if compat_ck == legacy_ck else f"{compat_ck} != {legacy_ck}")

    store, tmp, ids = build_fixture()
    with open(store.edges_path, "a", encoding="utf-8") as f:
        f.write('"bad edge row"\n')
    with open(store.atom_path(ids["decision"]), "a", encoding="utf-8") as f:
        f.write('"bad atom row"\n')
    try:
        m = Model(tmp)
        startup_crashed = False
    except Exception as e:  # noqa
        startup_crashed = True
        print("   startup error:", e)
        m = None
    check("Startup: non-object JSONL rows are ignored", not startup_crashed)
    if startup_crashed:
        return 1

    check("Display width: Korean glyphs use terminal cells",
          display_width("브랜치") == 6 and display_width("문서 · meta") == 11)
    check("Display width: combining marks do not advance columns",
          display_width("e\u0301") == 1 and fit_display_width("e\u0301x", 1) == "e\u0301")
    check("Confidence: malformed scores render as 0.00",
          confidence_text("high") == "0.00" and "0.00" in m._atom_meta({"confidence_score": "high"}))
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
    merge_thread_row = next((row_text(r) for r in threads if "settle 문서" in row_text(r)), "")
    check("Threads: merge checkpoint diffstat uses merge metadata",
          "+1 ~0 −0" in merge_thread_row, merge_thread_row)
    forced_thread_row = next((row_text(r) for r in threads if "settle forced" in row_text(r)), "")
    check("Threads: forced merge diffstat counts override",
          "+0 ~1 −0" in forced_thread_row, forced_thread_row)

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
    legacy_merge = {"kind": "merge", "title": "settle 문서"}
    check("Merge source: legacy settle-title inference resolves Korean branch name",
          infer_merge_source_branch(legacy_merge, m.branch_by_name, m.branch_by_id) == ids["docs"],
          str(legacy_merge))
    check("Merge source: persisted source_branch_id wins when present",
          infer_merge_source_branch(merge_checkpoint, m.branch_by_name, m.branch_by_id) == ids["docs"],
          str(merge_checkpoint))
    merge_idx = next(i for i, c in enumerate(m._threads_row_checkpoints)
                     if c and c.get("title") == "settle 문서")
    merge_detail = "\n".join(row_text(r) for r in m.detail_rows("Threads", merge_idx))
    check("Details: merge checkpoint lists merged atoms",
          "Merged atoms" in merge_detail and "Document the TUI cockpit status" in merge_detail
          and "README and docs" not in merge_detail
          and "no atom revisions recorded" not in merge_detail,
          merge_detail)

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
