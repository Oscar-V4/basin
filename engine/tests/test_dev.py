"""Tests for the continued-dev batch: YAML nesting, doctor, Map tab, ref locking.
Run: python tests/test_dev.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, graph, doctor, reindex  # noqa: E402
from basin.compile_pack import to_yaml  # noqa: E402
from basin.tui_model import Model  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def fresh():
    tmp = tempfile.mkdtemp(prefix="basin_dev_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_dev", "project_name": "dev"})
    ops.ensure_branch(store, "main", name="main")
    return store, "p_dev", tmp


def main():
    # ---- YAML emitter handles nested dict/list inside list items ----
    obj = {"items": [{"k": "v", "nested": {"x": 1, "y": [1, 2]}, "tags": []}], "empty": []}
    y = to_yaml(obj)
    check("yaml: no python-repr leak", "{'" not in y and "[{" not in y, y)
    check("yaml: nested key present", "nested:" in y and "x: 1" in y)
    check("yaml: list under nested key", "- 1" in y and "- 2" in y)
    check("yaml: empty list as []", "tags: []" in y and "empty: []" in y)
    nested_indent = next(l for l in y.splitlines() if "x: 1" in l)
    nested_key = next(l for l in y.splitlines() if l.strip() == "nested:")
    check("yaml: nested value indented deeper than its key",
          (len(nested_indent) - len(nested_indent.lstrip())) > (len(nested_key) - len(nested_key.lstrip())))

    # ---- doctor: clean store ----
    store, pid, tmp = fresh()
    ops.stage_atom(store, pid, "main", None, "decision", "Ship it.", "ship", "user_explicit")
    ops.promote_branch(store, "main")
    reindex.reindex(store)
    rep = doctor.run(store)
    check("doctor: clean store ok", rep["ok"] and not rep["errors"], f"errors={rep['errors']}")

    # ---- doctor: detects corruption + orphan ref ----
    store.append_jsonl(store.edges_path, {"t": "edge"})  # valid json but harmless
    with open(store.edges_path, "a", encoding="utf-8") as f:
        f.write("{ this is not json\n")  # corrupt line
    refs = store.read_atom_refs("main")
    refs["at_ghost"] = {"current_revision_id": "rev_missing", "visibility": "tracked", "lifecycle_status": "active"}
    store.write_atom_refs("main", refs)
    rep2 = doctor.run(store)
    check("doctor: flags corrupt JSONL", rep2["corrupt_lines"] >= 1, f"corrupt={rep2['corrupt_lines']}")
    check("doctor: flags orphan ref (error)", any("orphan ref" in m for m in rep2["errors"]))
    check("doctor: not ok when errors present", not rep2["ok"])

    # ---- Map tab renders clusters ----
    store3, pid3, tmp3 = fresh()
    a = ops.stage_atom(store3, pid3, "main", None, "fact", "auth uses oauth", "auth-oauth", "user_explicit")[0]
    b = ops.stage_atom(store3, pid3, "main", None, "fact", "auth tokens expire", "auth-token", "user_explicit")[0]
    ops.record_edge(store3, pid3, "main", a, b, "refines")
    ops.promote_branch(store3, "main")
    g = graph.run(store3)
    check("graph: ran", g["clusters"] >= 1)
    rows = Model(tmp3).map_rows()
    flat = "\n".join("".join(s[0] for s in r) for r in rows)
    check("Map tab: renders a cluster header", "◆" in flat, flat[:120])
    check("Map tab: shows an atom statement", "auth" in flat)

    # ---- ref locking: update_atom_refs persists; promote/set_lifecycle still work ----
    store4, pid4, tmp4 = fresh()
    aid = ops.stage_atom(store4, pid4, "main", None, "decision", "Lockable.", "lock", "user_explicit")[0]
    n = ops.promote_branch(store4, "main")
    check("lock: promote via update_atom_refs", n == 1 and len(ops.current_atoms(store4, "main")) == 1)
    ops.set_atom_lifecycle(store4, "main", aid, "archived", "rejected")
    check("lock: set_atom_lifecycle persisted", len(ops.current_atoms(store4, "main")) == 0)

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
