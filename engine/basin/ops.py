"""Domain operations over the store: branches, checkpoints, sessions, atoms, edges.

atom identity is content-addressed on (project_id, semantic_entity_pk) so the same
(type, subject) accrues revisions — the substrate for supersede/conflict detection.
atom_revision is append-only truth; the atom_ref (refs/atoms/<branch>.json) is the
moving pointer carrying *effective* visibility/lifecycle for a branch.
"""
from __future__ import annotations

from .core import Store, new_id, now_iso, sha256_hex, norm_ws, AUTHORITY_RANK
from . import fingerprint as fp


# ---- branches ------------------------------------------------------------
def ensure_branch(store: Store, branch_id: str, name: str, intent: str = "",
                  base_checkpoint_id: str | None = None, owner: str = "") -> dict:
    existing = {b["branch_id"]: b for b in store.read_jsonl(store.branches_meta_path) if b.get("t") == "branch"}
    if branch_id in existing:
        return existing[branch_id]
    rec = {
        "t": "branch", "branch_id": branch_id, "name": name, "intent": intent,
        "base_checkpoint_id": base_checkpoint_id, "owner": owner,
        "status": "active", "merge_policy": "human_required",
        "created_at": now_iso(),
    }
    store.append_jsonl(store.branches_meta_path, rec)
    return rec


def list_branches(store: Store) -> list[dict]:
    return [b for b in store.read_jsonl(store.branches_meta_path) if b.get("t") == "branch"]


# ---- checkpoints ---------------------------------------------------------
def create_checkpoint(store: Store, project_id: str, branch_id: str, kind: str,
                      parent_checkpoint_id: str | None = None,
                      raw_event_start_seq: int | None = None,
                      raw_event_end_seq: int | None = None,
                      title: str = "", summary_text: str = "",
                      created_by: str = "hook") -> str:
    # Deterministic id (no clock) so a re-fired hook with the same range dedups (P0-3).
    cid = new_id("ck", project_id, branch_id, kind, raw_event_end_seq, title, parent_checkpoint_id)
    existing = {c.get("id") for c in store.read_jsonl(store.checkpoints_path) if c.get("t") == "checkpoint"}
    if cid not in existing:
        rec = {
            "t": "checkpoint", "id": cid, "project_id": project_id, "branch_id": branch_id,
            "kind": kind, "parent_checkpoint_id": parent_checkpoint_id,
            "raw_event_start_seq": raw_event_start_seq, "raw_event_end_seq": raw_event_end_seq,
            "title": title, "summary_text": summary_text,
            "created_by": created_by, "created_at": now_iso(),
        }
        store.append_jsonl(store.checkpoints_path, rec)
    store.set_branch_head(branch_id, cid)
    return cid


# ---- sessions ------------------------------------------------------------
def register_session(store: Store, project_id: str, external_session_id: str, branch_id: str,
                     transcript_path: str = "", cwd: str = "",
                     parent_session_link_id: str | None = None,
                     base_checkpoint_id: str | None = None,
                     base_context_pack_id: str | None = None,
                     status: str = "active") -> str:
    sl = new_id("sl", project_id, external_session_id, branch_id)
    rec = {
        "t": "session_link", "id": sl, "project_id": project_id, "provider": "claude_code",
        "external_session_id": external_session_id, "branch_id": branch_id,
        "base_checkpoint_id": base_checkpoint_id, "base_context_pack_id": base_context_pack_id,
        "parent_session_link_id": parent_session_link_id,
        "transcript_path": transcript_path, "cwd": cwd, "status": status,
        "started_at": now_iso(), "last_seen_at": now_iso(),
    }
    store.append_jsonl(store.sessions_path, rec)
    return sl


# ---- atoms ---------------------------------------------------------------
def stage_atom(store: Store, project_id: str, branch_id: str, checkpoint_id: str | None,
               atom_type: str, statement: str, subject_key: str,
               authority_tier: str, source_raw_event_id: str | None = None,
               source_quote: str = "", confidence_score: float = 0.5,
               created_by: str = "extractor") -> tuple[str, str] | None:
    """Append a staged candidate revision + update the atom_ref. Idempotent on cosmetic identity.

    Returns (atom_id, revision_id) or None if skipped as a duplicate.
    """
    statement = norm_ws(statement)
    if not statement:
        return None
    semantic_entity_pk = f"{atom_type}::{subject_key}"
    atom_id = new_id("at", project_id, semantic_entity_pk)
    f = fp.fingerprints(atom_type, subject_key, statement)

    # branch-local comparison: prev is this branch's current revision, not global latest (P0-2)
    refs = store.read_atom_refs(branch_id)
    prev_ref = refs.get(atom_id)
    prev = store.get_revision(atom_id, prev_ref["current_revision_id"]) if prev_ref else None
    change = fp.classify_change(prev, f)
    if change == "NONE":
        return None  # identical content already current on this branch — idempotent

    revision_no = (prev.get("revision_no", 0) + 1) if prev else 1
    revision_hash = sha256_hex(f"{atom_id}|{statement}|{atom_type}|{authority_tier}|{confidence_score}")
    revision_id = new_id("rev", revision_hash, revision_no)
    supersedes_revision_id = prev.get("id") if (prev and change == "STRUCTURAL") else None
    provenance = {"supersedes": [supersedes_revision_id] if supersedes_revision_id else [], "conflicts_with": []}

    rec = {
        "t": "atom_revision", "id": revision_id, "atom_id": atom_id, "project_id": project_id,
        "branch_id": branch_id, "checkpoint_id": checkpoint_id,
        "semantic_entity_pk": semantic_entity_pk, "revision_no": revision_no,
        "visibility": "staged", "lifecycle_status": "candidate",
        "atom_type": atom_type, "statement": statement, "subject_key": subject_key,
        "confidence_score": confidence_score, "authority_tier": authority_tier,
        "source_raw_event_id": source_raw_event_id, "source_quote": norm_ws(source_quote)[:280],
        "provenance": provenance, "change_kind": change,
        "structural_fp": f["structural_fp"], "semantic_fp": f["semantic_fp"],
        "cosmetic_fp": f["cosmetic_fp"], "revision_hash": revision_hash,
        "supersedes_revision_id": supersedes_revision_id,
        "created_by": created_by, "created_at": now_iso(),
    }
    if not store.has_revision(atom_id, revision_id):  # avoid duplicate append across branches
        store.append_jsonl(store.atom_path(atom_id), rec)

    # COSMETIC change to an already-settled atom keeps it settled — never a new "Change" (P1).
    prev_life = prev_ref.get("lifecycle_status") if prev_ref else None
    if change == "COSMETIC" and prev_life in ("active", "released"):
        visibility, lifecycle = prev_ref.get("visibility", "tracked"), prev_life
    else:
        visibility, lifecycle = "staged", "candidate"
    refs[atom_id] = {"current_revision_id": revision_id, "visibility": visibility,
                     "lifecycle_status": lifecycle, "updated_at": now_iso()}
    store.write_atom_refs(branch_id, refs)

    if supersedes_revision_id:
        record_edge(store, project_id, branch_id, atom_id, atom_id, "supersedes",
                    confidence="EXTRACTED", source_raw_event_id=source_raw_event_id)
    return atom_id, revision_id


def promote_branch(store: Store, branch_id: str,
                   to_visibility: str = "tracked", to_lifecycle: str = "active") -> int:
    """`basin save`: flip candidate atom_refs to active/tracked (moving-ref update)."""
    refs = store.read_atom_refs(branch_id)
    n = 0
    for atom_id, r in refs.items():
        if r.get("lifecycle_status") in ("candidate", "staged"):
            r["visibility"] = to_visibility
            r["lifecycle_status"] = to_lifecycle
            r["updated_at"] = now_iso()
            n += 1
    store.write_atom_refs(branch_id, refs)
    return n


def current_atoms(store: Store, branch_id: str, lifecycles: tuple[str, ...] = ("active", "released")) -> list[dict]:
    """Effective atoms on a branch: atom_ref joined to ITS current revision (P0-1)."""
    refs = store.read_atom_refs(branch_id)
    out = []
    for atom_id, r in refs.items():
        if r.get("lifecycle_status") not in lifecycles:
            continue
        rev = store.get_revision(atom_id, r.get("current_revision_id"))
        if not rev:
            continue
        rev = dict(rev)
        rev["_eff_visibility"] = r.get("visibility")
        rev["_eff_lifecycle"] = r.get("lifecycle_status")
        out.append(rev)
    out.sort(key=lambda a: (AUTHORITY_RANK.get(a.get("authority_tier"), 9),
                            -float(a.get("confidence_score", 0)),
                            a.get("atom_type", ""), a.get("subject_key", "")))
    return out


def staged_candidates(store: Store, branch_id: str) -> list[dict]:
    # COSMETIC revisions never surface as Changes (P1).
    return [a for a in current_atoms(store, branch_id, lifecycles=("candidate", "staged"))
            if a.get("change_kind") != "COSMETIC"]


def set_atom_lifecycle(store: Store, branch_id: str, atom_id: str,
                       visibility: str, lifecycle: str) -> bool:
    """Single-atom moving-ref update (used by Changes/Proposals actions)."""
    refs = store.read_atom_refs(branch_id)
    if atom_id not in refs:
        return False
    refs[atom_id]["visibility"] = visibility
    refs[atom_id]["lifecycle_status"] = lifecycle
    refs[atom_id]["updated_at"] = now_iso()
    store.write_atom_refs(branch_id, refs)
    return True


def merge_atom(store: Store, from_branch: str, to_branch: str, atom_id: str,
               visibility: str = "tracked", lifecycle: str = "active") -> bool:
    """Settle one atom from a branch into another (the Proposals merge action).

    atom_id is content-addressed on the semantic entity, so it is shared across
    branches — merging is pointing the target branch's atom_ref at this revision.
    """
    fr = store.read_atom_refs(from_branch)
    if atom_id not in fr:
        return False
    to = store.read_atom_refs(to_branch)
    to[atom_id] = {"current_revision_id": fr[atom_id]["current_revision_id"],
                   "visibility": visibility, "lifecycle_status": lifecycle, "updated_at": now_iso()}
    store.write_atom_refs(to_branch, to)
    return True


def list_checkpoints(store: Store) -> list[dict]:
    cks = [c for c in store.read_jsonl(store.checkpoints_path) if c.get("t") == "checkpoint"]
    cks.sort(key=lambda c: (c.get("created_at", ""), c.get("raw_event_end_seq") or 0))
    return cks


def list_sessions(store: Store) -> list[dict]:
    """Latest state per session_link id (append-with-latest-wins)."""
    by_id = {}
    for s in store.read_jsonl(store.sessions_path):
        if s.get("t") == "session_link":
            by_id[s["id"]] = s
    return list(by_id.values())


def end_session(store: Store, project_id: str, external_session_id: str, branch_id: str,
                transcript_path: str = "", cwd: str = "") -> bool:
    """Mark a session ended. Updates the latest record, or registers one if the
    session was never seen (engine enabled mid-session)."""
    latest = None
    for s in store.read_jsonl(store.sessions_path):
        if s.get("t") == "session_link" and s.get("external_session_id") == external_session_id:
            latest = s
    if latest:
        rec = dict(latest)
        rec["status"] = "ended"
        rec["ended_at"] = now_iso()
        rec["last_seen_at"] = now_iso()
        store.append_jsonl(store.sessions_path, rec)
    else:
        register_session(store, project_id, external_session_id, branch_id,
                         transcript_path=transcript_path, cwd=cwd, status="ended")
    return True


def current_branch_for_session(store: Store, external_session_id: str, default: str) -> str:
    """Latest branch this external session is linked to (else default/canon)."""
    branch = default
    for s in store.read_jsonl(store.sessions_path):
        if s.get("t") == "session_link" and s.get("external_session_id") == external_session_id:
            branch = s.get("branch_id", branch)
    return branch


def diff_branch_vs_canon(store: Store, branch_id: str, canon_branch: str) -> dict:
    """Pure analysis (lix merge-preview spirit): what would settling this branch change?"""
    if branch_id == canon_branch:
        return {"new": [], "changed": [], "removed": []}
    branch = {a["atom_id"]: a for a in current_atoms(store, branch_id)}
    canon = {a["atom_id"]: a for a in current_atoms(store, canon_branch)}
    new, changed, removed = [], [], []
    for aid, a in branch.items():
        if aid not in canon:
            new.append(a)
        elif a.get("semantic_fp") != canon[aid].get("semantic_fp"):
            changed.append({"branch": a, "canon": canon[aid]})
    for aid, a in canon.items():
        if aid not in branch:
            removed.append(a)
    return {"new": new, "changed": changed, "removed": removed}


# ---- edges ---------------------------------------------------------------
def record_edge(store: Store, project_id: str, branch_id: str, src_atom_id: str, dst_atom_id: str,
                relation: str, confidence: str = "INFERRED",
                source_raw_event_id: str | None = None, created_by: str = "extractor") -> str:
    eid = new_id("e", branch_id, src_atom_id, dst_atom_id, relation)
    rec = {
        "t": "edge", "id": eid, "project_id": project_id, "branch_id": branch_id,
        "src": src_atom_id, "dst": dst_atom_id, "relation": relation,
        "confidence": confidence, "source_raw_event_id": source_raw_event_id,
        "created_by": created_by, "created_at": now_iso(),
    }
    store.append_jsonl(store.edges_path, rec)
    return eid
