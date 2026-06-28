"""Fork model.

Auto-detection uses the longest common prefix of raw_hash sequences as the
transcript-level merge base. Hosts that duplicate the inherited transcript when a
side thread/fork is created can be captured as a Basin branch without an
operator running `basin fork` first.
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
    existed = any(
        b.get("t") == "branch" and b.get("branch_id") == branch_id
        for b in store.read_jsonl(store.branches_meta_path)
    )
    ops.ensure_branch(store, branch_id, name=name, intent=intent, base_checkpoint_id=base_checkpoint_id,
                      parent_branch_id=from_branch, parent_session_link_id=parent_session_link_id)
    if existed:
        return branch_id
    # Inheritance: a fork starts from the parent's settled context (P0-2).
    if from_branch:
        store.write_atom_refs(branch_id, dict(store.read_atom_refs(from_branch)))
    ops.create_checkpoint(store, project_id, branch_id, kind="fork_point",
                          parent_checkpoint_id=base_checkpoint_id, title=f"fork: {name}",
                          created_by="user")
    return branch_id


def transcript_hashes(transcript_path: str) -> list[str]:
    """Return the raw_hash sequence a transcript would ingest as raw_events."""
    return [ingest.raw_hash(e["event_type"], e["text"]) for e in ingest.parse_transcript(transcript_path)]


def detect_fork_from_hashes(store: Store, session_id: str, hashes: list[str],
                            min_prefix: int = 3) -> dict | None:
    """Find the parent session for a not-yet-ingested transcript hash sequence.

    A candidate can be either already diverged (shared prefix, then different
    event) or prefix-only (a newly opened side thread before its first distinct
    turn). Prefix-only detection is lower confidence but is the practical way to
    recognize one-layer side chats as soon as they appear.
    """
    S = hashes
    if len(S) < min_prefix:
        return None
    best = None
    for other in store.list_sessions():
        if other == session_id:
            continue
        H = ingest.session_hashes(store, other)
        L = lcp_len(H, S)
        if L < min_prefix:
            continue
        diverged = L < len(S)
        prefix_only = L == len(S)
        if diverged or prefix_only:
            rel = "diverged" if diverged else "prefix"
            confidence = 0.9 if diverged else 0.7
            cand = {
                "parent_session": other,
                "lcp": L,
                "diverges_at": L if diverged else None,
                "candidate_events": len(S),
                "parent_events": len(H),
                "relation": rel,
                "confidence": confidence,
            }
            if best is None or (cand["lcp"], cand["confidence"]) > (best["lcp"], best["confidence"]):
                best = cand
    return best


def detect_fork(store: Store, project_id: str, session_id: str, min_prefix: int = 3) -> dict | None:
    """Find the parent session this one forked from, via shared raw_hash prefix."""
    return detect_fork_from_hashes(store, session_id, ingest.session_hashes(store, session_id), min_prefix)
