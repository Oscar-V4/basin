"""Domain operations over the store: branches, checkpoints, sessions, atoms, edges.

atom identity is content-addressed on (project_id, semantic_entity_pk) so the same
(type, subject) accrues revisions — the substrate for supersede/conflict detection.
atom_revision is append-only truth; the atom_ref (refs/atoms/<branch>.json) is the
moving pointer carrying *effective* visibility/lifecycle for a branch.
"""
from __future__ import annotations

import re

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
    # Deterministic id (no clock) so a re-fired hook with the same range dedups (P0-3),
    # but distinct enough that two real commits never collide (e.g. two saves with the
    # same message chain off different parents -> different id).
    cid = new_id("ck", project_id, branch_id, kind, raw_event_start_seq, raw_event_end_seq,
                 title, summary_text, parent_checkpoint_id)
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

    # branch-local read-decide-write, serialized per branch (P0-2 + lost-update safety)
    with store.lock(f"refs-{branch_id}"):
        refs = store.read_atom_refs(branch_id)
        prev_ref = refs.get(atom_id)
        prev = store.get_revision(atom_id, prev_ref["current_revision_id"]) if prev_ref else None
        change = fp.classify_change(prev, f)
        if change == "NONE":
            return None  # identical content already current on this branch — idempotent

        revision_no = (prev.get("revision_no", 0) + 1) if prev else 1
        revision_hash = sha256_hex(f"{atom_id}|{statement}|{atom_type}|{authority_tier}|{confidence_score}")
        # branch in the id so each branch owns its row (correct branch_id attribution)
        revision_id = new_id("rev", revision_hash, revision_no, branch_id)
        # Lineage pointer: ALWAYS link to the branch-local predecessor (incl. COSMETIC), so the
        # supersedes chain stays continuous for divergence detection (a cosmetic re-wording must
        # not cut the chain and make a later edit look diverged). The supersede EDGE below stays
        # STRUCTURAL-only to preserve its "meaningfully replaces" semantics.
        supersedes_revision_id = prev.get("id") if prev else None
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

    if supersedes_revision_id and change == "STRUCTURAL":
        record_edge(store, project_id, branch_id, atom_id, atom_id, "supersedes",
                    confidence="EXTRACTED", source_raw_event_id=source_raw_event_id)
    return atom_id, revision_id


def promote_branch(store: Store, branch_id: str,
                   to_visibility: str = "tracked", to_lifecycle: str = "active") -> int:
    """`basin save`: flip candidate atom_refs to active/tracked (moving-ref update)."""
    n = [0]

    def mutate(refs):
        for _atom_id, r in refs.items():
            if r.get("lifecycle_status") in ("candidate", "staged"):
                r["visibility"] = to_visibility
                r["lifecycle_status"] = to_lifecycle
                r["updated_at"] = now_iso()
                n[0] += 1

    store.update_atom_refs(branch_id, mutate)
    return n[0]


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
    # Only candidate/staged atoms surface as Changes. A COSMETIC edit to an already
    # settled atom keeps it active (stage_atom), so it is excluded by lifecycle here —
    # but a cosmetic re-wording of a still-staged candidate IS a real pending change.
    return current_atoms(store, branch_id, lifecycles=("candidate", "staged"))


def set_atom_lifecycle(store: Store, branch_id: str, atom_id: str,
                       visibility: str, lifecycle: str) -> bool:
    """Single-atom moving-ref update (used by Changes/Proposals actions)."""
    found = [False]

    def mutate(refs):
        if atom_id in refs:
            refs[atom_id]["visibility"] = visibility
            refs[atom_id]["lifecycle_status"] = lifecycle
            refs[atom_id]["updated_at"] = now_iso()
            found[0] = True

    store.update_atom_refs(branch_id, mutate)
    return found[0]


_TERMINAL_LIFECYCLES = ("rejected", "pruned", "archived")


def _is_ancestor_rev(store: Store, atom_id: str, ancestor_rev: str, descendant_rev: str,
                     _max: int = 500) -> bool:
    """True if ancestor_rev equals descendant_rev or sits in its supersedes chain."""
    cur, seen, steps = descendant_rev, set(), 0
    while cur and cur not in seen and steps < _max:
        if cur == ancestor_rev:
            return True
        seen.add(cur)
        steps += 1
        rev = store.get_revision(atom_id, cur)
        cur = rev.get("supersedes_revision_id") if rev else None
    return False


def merge_atom(store: Store, from_branch: str, to_branch: str, atom_id: str,
               visibility: str = "tracked", lifecycle: str = "active",
               project_id: str | None = None, force: bool = False) -> dict:
    """Settle one atom from a branch into another (the Proposals merge action).

    atom_id is content-addressed on the semantic entity, so it is shared across branches —
    merging points the target branch's atom_ref at the source revision. But it must NOT be a
    blind last-writer-wins overwrite: if the target advanced independently since the fork
    (divergence) or the canon owner put the atom in a terminal lifecycle (rejected/pruned),
    that is a conflict requiring explicit intent (`force`), surfaced — not silently won.

    Returns {ok, status: merged|noop|conflict|missing, ...}.
    """
    fr = store.read_atom_refs(from_branch)
    if atom_id not in fr:
        return {"ok": False, "status": "missing", "atom_id": atom_id}
    rev_id = fr[atom_id]["current_revision_id"]

    target = store.read_atom_refs(to_branch).get(atom_id)
    superseded = None
    if target:
        t_rev = target.get("current_revision_id")
        if t_rev == rev_id:
            # Same revision, but the target may still need its lifecycle/visibility reconciled — a
            # forked branch can inherit a canon *candidate* ref at the same rev and then promote it.
            # Treating that as a pure noop silently drops the promotion (the atom never reaches canon).
            if target.get("lifecycle_status") == lifecycle and target.get("visibility") == visibility:
                return {"ok": True, "status": "noop", "atom_id": atom_id}
            store.update_atom_refs(to_branch, lambda refs: refs.__setitem__(atom_id, {
                "current_revision_id": rev_id, "visibility": visibility,
                "lifecycle_status": lifecycle, "updated_at": now_iso()}))
            return {"ok": True, "status": "merged", "atom_id": atom_id, "superseded_rev": None}
        diverged = not _is_ancestor_rev(store, atom_id, t_rev, rev_id)
        rejected = target.get("lifecycle_status") in _TERMINAL_LIFECYCLES
        if (diverged or rejected) and not force:
            if project_id:
                record_edge(store, project_id, to_branch, atom_id, atom_id, "contradicts",
                            confidence="ASSERTED", created_by="merge")
            return {"ok": False, "status": "conflict", "atom_id": atom_id,
                    "reason": "rejected_in_canon" if rejected else "diverged",
                    "target_rev": t_rev, "incoming_rev": rev_id}
        superseded = t_rev   # clean fast-forward over an older canon revision

    def mutate(refs):
        refs[atom_id] = {"current_revision_id": rev_id, "visibility": visibility,
                         "lifecycle_status": lifecycle, "updated_at": now_iso()}

    store.update_atom_refs(to_branch, mutate)
    if project_id and superseded and superseded != rev_id:
        record_edge(store, project_id, to_branch, atom_id, atom_id, "supersedes",
                    confidence="ASSERTED", created_by="merge")
    return {"ok": True, "status": "merged", "atom_id": atom_id, "superseded_rev": superseded}


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
    """Pure analysis (lix merge-preview spirit): what would settling this branch change?

    `conflicts` surfaces atoms the canon owner explicitly retired (terminal lifecycle) that the
    branch would resurrect — so settle can refuse them instead of silently reversing a rejection.
    """
    if branch_id == canon_branch:
        return {"new": [], "changed": [], "removed": [], "conflicts": []}
    branch = {a["atom_id"]: a for a in current_atoms(store, branch_id)}
    canon = {a["atom_id"]: a for a in current_atoms(store, canon_branch)}
    canon_refs = store.read_atom_refs(canon_branch)          # ALL lifecycles, incl. rejected/pruned
    new, changed, removed, conflicts = [], [], [], []
    for aid, a in branch.items():
        if aid not in canon:
            cref = canon_refs.get(aid)
            if cref and cref.get("lifecycle_status") in _TERMINAL_LIFECYCLES:
                conflicts.append({"branch": a, "canon_lifecycle": cref.get("lifecycle_status"),
                                  "reason": "would_resurrect_rejected"})
            else:
                new.append(a)
        elif a.get("semantic_fp") != canon[aid].get("semantic_fp"):
            changed.append({"branch": a, "canon": canon[aid]})
    for aid, a in canon.items():
        if aid not in branch:
            removed.append(a)
    return {"new": new, "changed": changed, "removed": removed, "conflicts": conflicts}


def set_do_not_load(store: Store, project_id: str, branch_id: str, atom_id: str,
                    action: str = "exclude", reason: str = "", created_by: str = "user") -> str:
    """Append a do_not_load action (exclude | retrieve_only | allow) — the attention budget."""
    rec = {
        "t": "do_not_load", "id": new_id("dnl", branch_id, atom_id, action),
        "project_id": project_id, "branch_id": branch_id, "atom_id": atom_id,
        "action": action, "policy": "default_exclude", "reason": reason,
        "created_by": created_by, "created_at": now_iso(),
    }
    store.append_jsonl(store.do_not_load_path, rec)
    return rec["id"]


_C_STOP = {"the", "a", "an", "use", "using", "for", "and", "or", "to", "of", "is", "are", "be",
           "we", "will", "with", "as", "instead", "that", "this", "it", "do", "does", "should"}
_REJECT_W = {"rejected", "reject", "rejects", "avoid", "avoided", "drop", "dropped", "discard",
             "deprecate", "deprecated", "never", "말자", "폐기", "버리", "대신", "하지", "금지", "않"}


def _content_words(statement: str, subject_key: str = "") -> set[str]:
    """Significant words of a statement, minus stopwords, rejection words, and the shared subject
    tokens (so the overlap measures whether the rejected thing IS the affirmed thing)."""
    subj = set(re.findall(r"[a-z0-9]+|[가-힣]+", (subject_key or "").lower()))
    ws = re.findall(r"[a-z]{3,}|[가-힣]{2,}", statement.lower())
    return {w for w in ws if w not in _C_STOP and w not in _REJECT_W and w not in subj}


def _cross_type_contradictions(store: Store, canon_branch: str, project_id: str | None) -> list[dict]:
    """An active rejected_path that rejects the SAME thing a surviving decision/principle/constraint
    affirms (do X *and* X-rejected) is a self-contradictory canon — flag it (non-blocking) so the
    pack compiler / a human can reconcile. "Rejected X, chose Y" (e.g. GUI rejected + use TUI) is
    complementary, not a contradiction. The two are distinguished by content-word overlap rather
    than by branch (a single fork can legitimately produce a real same-subject contradiction)."""
    by_subj: dict[str, list[dict]] = {}
    for a in current_atoms(store, canon_branch):
        by_subj.setdefault(a.get("subject_key"), []).append(a)
    out = []
    for subj, group in by_subj.items():
        rps = [a for a in group if a["atom_type"] == "rejected_path"]
        affirms = [a for a in group if a["atom_type"] in ("decision", "principle", "constraint")]
        for rp in rps:
            rp_w = _content_words(rp.get("statement", ""), subj)
            for a in affirms:
                if len(rp_w & _content_words(a.get("statement", ""), subj)) < 2:
                    continue   # low overlap -> rejects a DIFFERENT thing than is affirmed (complementary)
                if project_id:
                    record_edge(store, project_id, canon_branch, rp["atom_id"], a["atom_id"],
                                "contradicts", confidence="ASSERTED", created_by="merge")
                out.append({"status": "contradiction", "subject": subj,
                            "rejected_path": rp["atom_id"], "conflicts_with": a["atom_id"]})
    return out


def settle_branch(store: Store, branch_id: str, canon_branch: str,
                  project_id: str | None = None, force: bool = False) -> dict:
    """Merge every new/changed atom of a branch into the Canon (the reconcile -> settle step).

    Divergences, resurrections of rejected atoms, and cross-type contradictions are surfaced in
    `conflicts` and NOT silently won (unless force=True), so the settled canon is deterministic
    and never reverses an explicit rejection.
    """
    d = diff_branch_vs_canon(store, branch_id, canon_branch)
    merged, conflicts = 0, []
    candidates = [a for a in d["new"]] + [c["branch"] for c in d["changed"]]
    seen = {a["atom_id"] for a in candidates}
    if force:                                  # --force also overrides resurrection conflicts
        for c in d.get("conflicts", []):
            if c["branch"]["atom_id"] not in seen:
                candidates.append(c["branch"]); seen.add(c["branch"]["atom_id"])
    for a in candidates:
        res = merge_atom(store, branch_id, canon_branch, a["atom_id"],
                         project_id=project_id, force=force)
        if res.get("status") == "merged":
            merged += 1
        elif res.get("status") == "conflict":
            conflicts.append(res)
    if not force:
        for c in d.get("conflicts", []):       # resurrection attempts flagged by the preview
            conflicts.append({"status": "conflict", "reason": c.get("reason"),
                              "atom_id": c["branch"]["atom_id"]})
    contradictions = _cross_type_contradictions(store, canon_branch, project_id)  # merged but flagged
    return {"merged": merged, "new": len(d["new"]), "changed": len(d["changed"]),
            "conflicts": conflicts, "contradictions": contradictions}


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
