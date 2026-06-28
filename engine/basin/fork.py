"""Fork model. Explicit `basin fork` is the reliable path (wired in).

Auto-detection (longest-common-prefix of raw_hash sequences -> merge-base) is
implemented here for validation (T8) but is NOT on the hook hot path yet, since it
depends on Claude Code duplicating the transcript prefix verbatim on a fork.
"""
from __future__ import annotations

from .core import Store, new_id
from . import ops, ingest


def lcp_len(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x == y and x:
            n += 1
        else:
            break
    return n


def create_branch(store: Store, project_id: str, name: str, intent: str = "",
                  base_checkpoint_id: str | None = None, from_branch: str | None = None,
                  parent_session_link_id: str | None = None) -> str:
    branch_id = new_id("b", project_id, name)
    ops.ensure_branch(store, branch_id, name=name, intent=intent, base_checkpoint_id=base_checkpoint_id)
    # Inheritance: a fork starts from the parent's settled context (P0-2).
    if from_branch:
        store.write_atom_refs(branch_id, dict(store.read_atom_refs(from_branch)))
    ops.create_checkpoint(store, project_id, branch_id, kind="fork_point",
                          parent_checkpoint_id=base_checkpoint_id, title=f"fork: {name}",
                          created_by="user")
    return branch_id


def detect_fork(store: Store, project_id: str, session_id: str, min_prefix: int = 3) -> dict | None:
    """Find the parent session this one forked from, via shared raw_hash prefix."""
    S = ingest.session_hashes(store, session_id)
    if len(S) <= min_prefix:
        return None
    best = None
    for other in store.list_sessions():
        if other == session_id:
            continue
        H = ingest.session_hashes(store, other)
        L = lcp_len(H, S)
        if L >= min_prefix and L < len(S):  # shared prefix then divergence
            if best is None or L > best["lcp"]:
                best = {"parent_session": other, "lcp": L, "diverges_at": L}
    return best
