"""Context graph clustering — ported from graphify (stdlib, no networkx).

- label propagation over the atom/edge graph -> communities (context neighborhoods)
- community 0 = largest (graphify cluster.py:93 convention)
- stable IDs across reruns by overlap remap (graphify cluster.py:224
  `remap_communities_to_previous`) so a cluster keeps its id when re-clustered.

Clusters become the unit of do_not_load / retrieval. Non-blocking: written as a
regenerable projection `.basin/clusters.json`; Context Packs work without it.
"""
from __future__ import annotations

import json
from collections import Counter

from .core import Store, now_iso


def build_graph(store: Store) -> tuple[dict, dict]:
    """Return (adjacency, node_meta) over current atoms and their edges."""
    nodes = {}
    for aid in store.all_atom_ids():
        rev = store.latest_revision(aid)
        if rev:
            nodes[aid] = {"subject_key": rev.get("subject_key", ""), "atom_type": rev.get("atom_type", ""),
                          "statement": rev.get("statement", "")}
    adj = {n: set() for n in nodes}
    for e in store.read_jsonl(store.edges_path):
        if e.get("t") != "edge":
            continue
        a, b = e.get("src"), e.get("dst")
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)
    return adj, nodes


def label_propagation(adj: dict, max_iter: int = 50) -> dict:
    """Deterministic LPA: nodes processed in sorted order, ties -> smallest label."""
    labels = {n: n for n in adj}
    for _ in range(max_iter):
        changed = False
        for n in sorted(adj):
            nbrs = adj[n]
            if not nbrs:
                continue
            counts = Counter(labels[m] for m in nbrs)
            best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if labels[n] != best:
                labels[n] = best
                changed = True
        if not changed:
            break
    return labels


def _assign_ids_by_size(labels: dict) -> dict:
    groups: dict = {}
    for node, lab in labels.items():
        groups.setdefault(lab, []).append(node)
    ordered = sorted(groups.values(), key=lambda g: (-len(g), sorted(g)[0]))
    return {node: i for i, g in enumerate(ordered) for node in g}


def remap_communities_to_previous(new_assign: dict, prev_assign: dict) -> dict:
    """Remap new community ids to maximize overlap with a previous assignment
    (graphify cluster.py:224). Keeps ids stable so the UI/budget don't churn."""
    if not prev_assign:
        return new_assign
    new_groups: dict = {}
    for node, cid in new_assign.items():
        new_groups.setdefault(cid, set()).add(node)
    overlaps = []  # (overlap, new_cid, prev_cid)
    for ncid, nodes in new_groups.items():
        prev_counts = Counter(prev_assign[n] for n in nodes if n in prev_assign)
        for pcid, ov in prev_counts.items():
            overlaps.append((ov, ncid, pcid))
    overlaps.sort(reverse=True)
    mapping, used_new, used_prev = {}, set(), set()
    for ov, ncid, pcid in overlaps:
        if ncid in used_new or pcid in used_prev:
            continue
        mapping[ncid] = pcid
        used_new.add(ncid)
        used_prev.add(pcid)
    next_id = (max(prev_assign.values()) + 1) if prev_assign else 0
    for ncid in new_groups:
        if ncid not in mapping:
            mapping[ncid] = next_id
            next_id += 1
    return {node: mapping[cid] for node, cid in new_assign.items()}


def _name_cluster(nodes: list[str], meta: dict) -> str:
    words = Counter()
    for n in nodes:
        for w in (meta.get(n, {}).get("subject_key", "") or "").split("-"):
            if len(w) > 2:
                words[w] += 1
    top = [w for w, _ in words.most_common(3)]
    return " / ".join(top) if top else "misc"


def run(store: Store) -> dict:
    adj, meta = build_graph(store)
    if not adj:
        return {"clusters": 0, "atoms": 0}
    labels = label_propagation(adj)
    assign = _assign_ids_by_size(labels)
    prev = {}
    p = store.dir / "clusters.json"
    if p.exists():
        try:
            prev = json.loads(p.read_text(encoding="utf-8")).get("atom_to_cluster", {})
        except Exception:
            prev = {}
    assign = remap_communities_to_previous(assign, prev)

    by_cluster: dict = {}
    for node, cid in assign.items():
        by_cluster.setdefault(cid, []).append(node)
    clusters = [{"id": cid, "name": _name_cluster(sorted(nodes), meta), "size": len(nodes),
                 "atoms": sorted(nodes)} for cid, nodes in sorted(by_cluster.items())]
    out = {"generated_at": now_iso(), "clusters": clusters, "atom_to_cluster": assign}
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"clusters": len(clusters), "atoms": len(assign), "path": str(p)}
