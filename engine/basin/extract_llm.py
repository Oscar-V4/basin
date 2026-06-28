"""Detached LLM extraction worker.

The model refines the deterministic candidates: cleaner statements, conflict edges,
confidence. Two parts:
  - forgiving_validate(): port of UA `schema.ts` — sanitize/normalize/clamp/drop bad
    LLM JSON, enforce referential integrity. Fully testable, no model needed.
  - run(): gather events, call a completion fn, validate, stage refined atoms.
Model calls are OFF unless BASIN_LLM=1 and `claude` is on PATH — the hot path never
depends on them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from .core import Store, ATOM_TYPES, AUTHORITY_TIERS
from . import ops

_TYPE_ALIASES = {
    "rule": "constraint", "requirement": "constraint", "todo": "task", "action_item": "task",
    "question": "open_question", "reject": "rejected_path", "rejected": "rejected_path",
    "pref": "preference", "assumption_": "assumption", "goal": "principle", "mission": "principle",
}
_VALID = set(ATOM_TYPES)
_VALID_TIERS = set(AUTHORITY_TIERS)


def _coerce_confidence(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


def _slug(stmt: str) -> str:
    words = re.findall(r"[\w가-힣]+", (stmt or "").lower())
    return "-".join(words[:4]) if words else "general"


def forgiving_validate(obj) -> dict:
    """Repair messy LLM JSON into clean atoms+edges (never raises)."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return {"atoms": [], "edges": []}
    if not isinstance(obj, dict):
        return {"atoms": [], "edges": []}

    atoms, by_subject = [], {}
    for a in (obj.get("atoms") or []):
        if not isinstance(a, dict):
            continue
        stmt = (a.get("statement") or a.get("text") or "").strip()
        if not stmt:
            continue  # drop invalid
        t = str(a.get("type") or a.get("atom_type") or "fact").strip().lower()
        t = _TYPE_ALIASES.get(t, t)
        if t not in _VALID:
            t = "fact"
        tier = str(a.get("authority_tier") or "model_inferred").strip().lower()
        if tier not in _VALID_TIERS:
            tier = "model_inferred"
        subj = (a.get("subject_key") or _slug(stmt)).strip()
        atom = {"atom_type": t, "statement": stmt, "subject_key": subj,
                "authority_tier": tier, "confidence_score": _coerce_confidence(a.get("confidence", a.get("confidence_score", 0.5)))}
        atoms.append(atom)
        by_subject[subj] = atom

    edges = []
    for e in (obj.get("edges") or []):
        if not isinstance(e, dict):
            continue
        src, dst = e.get("src") or e.get("from"), e.get("dst") or e.get("to")
        rel = str(e.get("relation") or "refines").strip().lower()
        if src in by_subject and dst in by_subject and src != dst:  # referential integrity
            edges.append({"src": src, "dst": dst, "relation": rel})
    return {"atoms": atoms, "edges": edges}


_PROMPT = """You extract durable context atoms from an AI collaboration transcript.
Return ONLY JSON: {"atoms":[{"type","statement","subject_key","authority_tier","confidence"}],"edges":[{"src","dst","relation"}]}.
type in: fact, decision, assumption, constraint, task, open_question, artifact, preference, risk, principle, rejected_path.
authority_tier in: user_explicit, user_approved, artifact_declared, tool_observed, assistant_proposed, model_inferred.
subject_key: short kebab slug. edges src/dst reference subject_key. Be precise; skip small talk.

TRANSCRIPT:
"""


def default_completion(prompt: str) -> str | None:
    if os.environ.get("BASIN_LLM") != "1" or not shutil.which("claude"):
        return None
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=120)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def run(store: Store, project_id: str, branch_id: str, session_id: str, complete=None) -> dict:
    complete = complete or default_completion
    events = [e for e in store.read_jsonl(store.events_path(session_id)) if e.get("t") == "raw_event"]
    if not events:
        return {"skipped": "no events"}
    convo = "\n".join(f"[{e['event_type']}] {e.get('content_text','')}" for e in events)[:12000]
    raw = complete(_PROMPT + convo)
    if not raw:
        return {"skipped": "no completion (set BASIN_LLM=1 and install claude)"}
    clean = forgiving_validate(raw)
    staged = []
    for a in clean["atoms"]:
        res = ops.stage_atom(store, project_id, branch_id, None, a["atom_type"], a["statement"],
                             a["subject_key"], a["authority_tier"], source_quote=a["statement"],
                             confidence_score=a["confidence_score"], created_by="extractor_llm")
        if res:
            staged.append(res[0])
    return {"staged": len(staged), "edges": len(clean["edges"])}
