"""Regression tests for the live-dogfood findings (2026-06-29).

Dogfooding Basin on its own 14.8MB development transcript surfaced two real defects:
  1. extract_det atomized pasted attachments, prompt echoes, and cat -n file dumps,
     flooding the store with file-content "constraints" tagged user_explicit.
  2. compile_pack claimed to select "by authority/confidence" but did not sort at all,
     so the budget filled in arbitrary DB order and a flood of constraints starved every
     decision (pack shipped with current_decisions: []).

Run: python tests/test_dogfood.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, extract_det  # noqa: E402
from basin.compile_pack import compile_context_pack  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def fresh():
    tmp = tempfile.mkdtemp(prefix="basin_dog_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_dog", "project_name": "dog"})
    ops.ensure_branch(store, "main", name="main")
    return store, "p_dog", tmp


def main():
    # ---- precision: only conversational prose is atomized ----
    store, pid, _ = fresh()
    events = [
        {"id": "e1", "event_type": "user",
         "content_text": "Let's go with SQLite as the rebuildable index."},          # real decision -> stage
        {"id": "e2", "event_type": "attachment",
         "content_text": "We decided to use a vector DB for everything."},            # pasted file -> skip
        {"id": "e3", "event_type": "last-prompt",
         "content_text": "Let's go with SQLite as the rebuildable index."},           # prompt echo -> skip (dup)
        {"id": "e4", "event_type": "mode", "content_text": "always plan mode required"},  # UI meta -> skip
        {"id": "e5", "event_type": "user",
         "content_text": "10 - Registers JSON Schema-based entity tables; we must validate FKs."},  # cat -n dump -> skip
        {"id": "e6", "event_type": "user",
         "content_text": "We must keep CLAUDE.md additive-only going forward."},  # prose + citation -> KEEP (r3)
    ]
    res = extract_det.extract_events(store, pid, "main", None, events)
    stmts = [a["statement"] for a in res["atoms"]]
    check("precision: real prose decision staged", any("SQLite" in s for s in stmts), str(stmts))
    check("precision: attachment not atomized", not any("vector DB" in s for s in stmts))
    check("precision: prompt-echo dup not double-staged",
          sum("SQLite" in s for s in stmts) == 1, f"n={sum('SQLite' in s for s in stmts)}")
    check("precision: cat -n file-dump line rejected", not any("Registers JSON Schema" in s for s in stmts))
    check("precision: prose that cites a filename is kept (r3 recall fix)",
          any("additive-only" in s for s in stmts), str(stmts))

    # ---- _is_prose unit checks (r3: markers/citations must not be fatal) ----
    check("is_prose: accepts a sentence", extract_det._is_prose("We will ship as a plugin and skip the GUI."))
    check("is_prose: accepts a bulleted decision (r3)", extract_det._is_prose('- "We decided to ship as a plugin."'))
    check("is_prose: accepts prose citing a filename (r3)",
          extract_det._is_prose("CLAUDE.md never names a client domain."))
    check("is_prose: accepts a short Korean decision (r3)", extract_det._is_prose("별도 GUI 앱은 폐기하자."))
    check("is_prose: rejects line-number dump", not extract_det._is_prose("105 you only need a small subset"))
    check("is_prose: rejects markdown table row", not extract_det._is_prose("| col | value | note |"))
    check("is_prose: rejects a bare path line", not extract_det._is_prose("src/deeplake-api.ts"))

    # ---- ranking: a flood of constraints must not starve decisions in the pack ----
    store2, pid2, _ = fresh()
    store2.set_config("lod_standard_tokens", 300)   # tight budget so not everything fits
    for i in range(60):
        ops.stage_atom(store2, pid2, "main", None, "constraint",
                       f"Constraint number {i} that the system must always uphold here.",
                       f"c{i}", "user_explicit")
    for i in range(3):
        ops.stage_atom(store2, pid2, "main", None, "decision",
                       f"We decided approach {i} for the core engine design.", f"d{i}", "user_explicit")
    ops.promote_branch(store2, "main")
    _, _, pack = compile_context_pack(store2, pid2, "main", lod="standard")
    check("ranking: decisions survive a constraint flood", len(pack["current_decisions"]) >= 1,
          f"decisions={len(pack['current_decisions'])}")
    check("ranking: budget actually constrained", pack["token_budget"]["always_load_used"] <= 300)

    # ---- ranking: higher authority wins the budget over lower authority ----
    store3, pid3, _ = fresh()
    store3.set_config("lod_standard_tokens", 15)   # only one decision fits
    ops.stage_atom(store3, pid3, "main", None, "decision",
                   "Low authority guess about the schema layout here.", "low", "model_inferred",
                   confidence_score=0.4)
    ops.stage_atom(store3, pid3, "main", None, "decision",
                   "User confirmed the canonical branch is named main.", "high", "user_explicit",
                   confidence_score=0.9)
    ops.promote_branch(store3, "main")
    _, _, pack3 = compile_context_pack(store3, pid3, "main", lod="standard")
    kept = [d["statement"] for d in pack3["current_decisions"]]
    check("ranking: user_explicit beats model_inferred under tight budget",
          any("User confirmed" in s for s in kept) and not any("Low authority" in s for s in kept), str(kept))

    # ---- ranking: per-type FLOOR keeps rejected_paths/open_questions from full eviction (r3) ----
    # The first ranking fix used a global type-priority sort, which merely converted the
    # constraint-flood into a rejected_path/open_question-flood — a cold reader saw zero of them.
    store4, pid4, _ = fresh()
    store4.set_config("lod_standard_tokens", 120)   # decisions alone would consume the whole budget
    for i in range(40):
        ops.stage_atom(store4, pid4, "main", None, "decision",
                       f"We decided detail {i} about the engine design here.", f"d{i}", "user_explicit")
    ops.stage_atom(store4, pid4, "main", None, "rejected_path", "No vector DB; files are truth.", "no-vec", "user_explicit")
    ops.stage_atom(store4, pid4, "main", None, "open_question", "Fork auto-detect reliability is unresolved.", "fork-detect", "user_explicit")
    ops.promote_branch(store4, "main")
    _, _, pack4 = compile_context_pack(store4, pid4, "main", lod="standard")
    check("ranking: floor keeps a rejected_path in always-load", len(pack4["rejected_paths"]) >= 1,
          f"rp={len(pack4['rejected_paths'])}")
    check("ranking: floor keeps an open_question in always-load", len(pack4["open_questions"]) >= 1,
          f"oq={len(pack4['open_questions'])}")
    check("ranking: decisions still fill the rest", len(pack4["current_decisions"]) >= 3,
          f"dec={len(pack4['current_decisions'])}")

    # ---- ranking: canonical authority order (artifact_declared outranks assistant_proposed) ----
    from basin.compile_pack import _rank_key  # noqa: E402
    art = _rank_key({"authority_tier": "artifact_declared", "confidence_score": 0.5})
    asst = _rank_key({"authority_tier": "assistant_proposed", "confidence_score": 0.5})
    check("ranking: artifact_declared outranks assistant_proposed (canonical)", art < asst, f"{art} vs {asst}")

    # ---- ranking: malformed confidence_score does not crash the pack ----
    store5, pid5, _ = fresh()
    a5 = ops.stage_atom(store5, pid5, "main", None, "decision", "Robust to bad data.", "robust", "user_explicit")[0]
    ops.promote_branch(store5, "main")
    try:
        _rank_key({"authority_tier": "user_explicit", "confidence_score": "high", "revision_no": None})
        crashed = False
    except Exception:
        crashed = True
    check("ranking: non-numeric confidence_score does not raise", not crashed)

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
