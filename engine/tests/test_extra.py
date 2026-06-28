"""Tests for the second wave: LLM validation, graph clustering, TUI model,
fork detection, reconcile, auto-fork hook. Run: python tests/test_extra.py
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, ingest, graph, fork, hook  # noqa: E402
from basin.extract_llm import forgiving_validate, run as llm_run  # noqa: E402
from basin.tui_model import Model, TABS  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def write_transcript(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for role, text in lines:
            f.write(json.dumps({"type": role, "timestamp": "2026-06-28T10:00:00Z",
                                "message": {"role": role, "content": text}}) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="basin_extra_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_x", "project_name": "x"})
    pid = "p_x"
    ops.ensure_branch(store, "main", name="main")

    # ---- forgiving_validate (UA schema.ts port) ----
    messy = {
        "atoms": [
            {"type": "rule", "statement": "  Must pin versions.  ", "confidence": 5},  # alias + clamp + trim
            {"type": "decision", "text": "Use files as truth", "confidence": "0.9"},   # text alias + str conf
            {"type": "bogus", "statement": "Unknown type maps to fact"},                # invalid type -> fact
            {"statement": ""},                                                          # empty -> dropped
            "not a dict",                                                               # junk -> dropped
        ],
        "edges": [
            {"src": "must-pin-versions", "dst": "use-files-as-truth", "relation": "depends_on"},  # valid
            {"src": "ghost", "dst": "use-files-as-truth", "relation": "x"},                       # dangling -> dropped
        ],
    }
    clean = forgiving_validate(messy)
    types = [a["atom_type"] for a in clean["atoms"]]
    check("validate: drops empty/junk (3 atoms kept)", len(clean["atoms"]) == 3, f"n={len(clean['atoms'])}")
    check("validate: alias rule->constraint", "constraint" in types)
    check("validate: invalid type->fact", "fact" in types)
    check("validate: confidence clamped <=1", all(a["confidence_score"] <= 1.0 for a in clean["atoms"]))
    check("validate: edge referential integrity (1 kept)", len(clean["edges"]) == 1, f"edges={len(clean['edges'])}")
    check("validate: bad string is non-raising", forgiving_validate("{bad json") == {"atoms": [], "edges": []})

    # llm_run is a safe no-op without BASIN_LLM
    r = llm_run(store, pid, "main", "nope")
    check("llm worker: safe no-op without model", "skipped" in r)

    # ---- graph clustering + stable IDs (graphify port) ----
    A = ops.stage_atom(store, pid, "main", None, "fact", "auth uses oauth", "auth-oauth", "user_explicit")[0]
    B = ops.stage_atom(store, pid, "main", None, "fact", "auth tokens expire", "auth-tokens", "user_explicit")[0]
    C = ops.stage_atom(store, pid, "main", None, "fact", "auth session store", "auth-session", "user_explicit")[0]
    D = ops.stage_atom(store, pid, "main", None, "fact", "ui uses linear theme", "ui-linear", "user_explicit")[0]
    E = ops.stage_atom(store, pid, "main", None, "fact", "ui dark mode", "ui-dark", "user_explicit")[0]
    for s, d in [(A, B), (B, C), (A, C), (D, E)]:
        ops.record_edge(store, pid, "main", s, d, "refines")
    g1 = graph.run(store)
    check("graph: clusters formed", g1["clusters"] >= 2, f"clusters={g1['clusters']}")
    assign1 = json.loads((store.dir / "clusters.json").read_text())["atom_to_cluster"]
    check("graph: A,B,C same cluster", assign1[A] == assign1[B] == assign1[C])
    check("graph: A and D different clusters", assign1[A] != assign1[D])
    # rerun must keep ids stable (remap_communities_to_previous)
    graph.run(store)
    assign2 = json.loads((store.dir / "clusters.json").read_text())["atom_to_cluster"]
    check("graph: stable IDs across rerun", assign1 == assign2)

    # ---- fork detection (LCP merge-base) ----
    t_a = os.path.join(tmp, "a.jsonl")
    t_b = os.path.join(tmp, "b.jsonl")
    shared = [("user", "shared one"), ("assistant", "shared two"), ("user", "shared three")]
    write_transcript(t_a, shared + [("user", "A path four"), ("user", "A path five")])
    write_transcript(t_b, shared + [("user", "B path four different"), ("user", "B five")])
    ingest.ingest_transcript(store, pid, "s_a", "main", t_a)
    ingest.ingest_transcript(store, pid, "s_b", "main", t_b)
    det = fork.detect_fork(store, pid, "s_b", min_prefix=3)
    check("fork: detected parent", det and det["parent_session"] == "s_a", f"det={det}")
    check("fork: lcp == 3 (shared prefix)", det and det["lcp"] == 3, f"lcp={det and det.get('lcp')}")

    # ---- auto-fork via SessionStart hook ----
    store.set_config("auto_fork", True)
    hook_input = {"session_id": "s_b", "transcript_path": t_b, "cwd": str(store.root)}
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(hook_input))
    rc = hook.run("session_start")
    sys.stdin = old
    branches = ops.list_branches(store)
    check("auto-fork: hook exit 0", rc == 0)
    check("auto-fork: created a fork branch", any(b["name"].startswith("fork-") for b in branches),
          f"branches={[b['name'] for b in branches]}")

    # ---- reconcile / diff ----
    bid = fork.create_branch(store, pid, "spike", base_checkpoint_id=store.get_branch_head("main"))
    ops.stage_atom(store, pid, bid, None, "decision", "Spike only decision", "spike-only", "user_explicit")
    ops.promote_branch(store, bid)
    d = ops.diff_branch_vs_canon(store, bid, "main")
    check("reconcile: spike has a new atom vs canon", len(d["new"]) >= 1, f"new={len(d['new'])}")

    # ---- TUI model renders every tab without raising ----
    m = Model(tmp)
    for tab in TABS:
        try:
            rows = m.render(tab)
            ok = isinstance(rows, list)
        except Exception as e:  # noqa
            ok = False
            print("   render error:", tab, e)
        check(f"tui: renders {tab}", ok)
    check("tui: status line", len(m.status_line()) >= 2)

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
