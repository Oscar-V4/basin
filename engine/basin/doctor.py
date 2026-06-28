"""`basin doctor` — integrity diagnostics over the .basin/ store.

Read-only. Surfaces the failure modes that append-only + rebuildable-index can drift into:
orphan refs, headless branches, corrupt JSONL, index staleness, dangling do_not_load.
"""
from __future__ import annotations

import json
import sqlite3

from .core import Store


def _ref_branches(store: Store) -> list[str]:
    d = store.dir / "refs" / "atoms"
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def _jsonl_files(store: Store):
    for sub in ("events", "atoms"):
        d = store.dir / sub
        if d.exists():
            yield from d.glob("*.jsonl")
    for name in ("edges.jsonl", "checkpoints.jsonl", "sessions.jsonl",
                 "do_not_load.jsonl", "branches.jsonl"):
        p = store.dir / name
        if p.exists():
            yield p


def run(store: Store) -> dict:
    issues: list[tuple[str, str]] = []

    # corrupt JSONL lines (read_jsonl silently skips these — surface them here)
    corrupt = 0
    for p in _jsonl_files(store):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                issues.append(("error", f"corrupt JSONL line in {p.relative_to(store.dir)}"))

    # orphan atom_refs (ref -> missing revision) + branch heads
    for bid in _ref_branches(store):
        for atom_id, r in store.read_atom_refs(bid).items():
            if not store.get_revision(atom_id, r.get("current_revision_id")):
                issues.append(("error", f"orphan ref {bid}/{atom_id} -> missing revision {r.get('current_revision_id')}"))

    branches = [b for b in store.read_jsonl(store.branches_meta_path) if b.get("t") == "branch"]
    for b in branches:
        if not store.get_branch_head(b["branch_id"]):
            issues.append(("warn", f"branch '{b.get('name', b['branch_id'])}' has no head checkpoint"))

    # dangling do_not_load (references an atom with no revisions)
    for d in store.read_jsonl(store.do_not_load_path):
        aid = d.get("atom_id")
        if d.get("t") == "do_not_load" and aid and not store.latest_revision(aid):
            issues.append(("warn", f"do_not_load references unknown atom {aid}"))

    # index staleness: atoms on disk vs rows in basin.db
    stale = None
    if store.index_db.exists():
        try:
            con = sqlite3.connect(store.index_db)
            n_idx = con.execute("SELECT count(DISTINCT atom_id) FROM atom_revision").fetchone()[0]
            con.close()
            n_disk = len(store.all_atom_ids())
            if n_idx != n_disk:
                stale = (n_disk, n_idx)
                issues.append(("warn", f"index stale: {n_disk} atoms on disk vs {n_idx} indexed — run `basin reindex`"))
        except sqlite3.Error:
            issues.append(("warn", "index unreadable — run `basin reindex`"))
    else:
        issues.append(("warn", "no index — run `basin reindex`"))

    errors = [m for s, m in issues if s == "error"]
    warns = [m for s, m in issues if s == "warn"]
    return {"ok": not errors, "errors": errors, "warnings": warns,
            "corrupt_lines": corrupt, "stale": stale,
            "branches": len(branches), "atoms": len(store.all_atom_ids())}
