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
            m = re.search(r"\{.*\}", obj, re.S)   # pull JSON out of a verbose agent wrapper (codex/claude)
            try:
                obj = json.loads(m.group(0)) if m else None
            except Exception:
                obj = None
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
    # Legacy backend. NOTE: `claude -p` is now API-only, so this needs ANTHROPIC_API_KEY and
    # will not work on a subscription CLI — prefer the codex backend (BASIN_LLM=codex).
    if os.environ.get("BASIN_LLM") != "1" or not shutil.which("claude"):
        return None
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=120)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def codex_completion(prompt: str) -> str | None:
    """Refine via Codex (gpt-5.5 / xhigh). `--output-last-message` captures just the final
    answer (not the event stream); read-only sandbox since extraction runs no commands."""
    if os.environ.get("BASIN_LLM") != "codex" or not shutil.which("codex"):
        return None
    import tempfile
    out = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    out.close()
    try:
        r = subprocess.run(
            ["codex", "exec", "-m", "gpt-5.5", "-c", "model_reasoning_effort=xhigh",
             "--skip-git-repo-check", "-s", "read-only", "--color", "never",
             "--output-last-message", out.name, prompt],
            capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return None
        with open(out.name, encoding="utf-8") as f:
            return f.read() or None
    except Exception:
        return None
    finally:
        try:
            os.unlink(out.name)
        except OSError:
            pass


_BACKENDS = {"1": default_completion, "claude": default_completion, "codex": codex_completion}


def select_backend():
    """Pick the completion backend from BASIN_LLM (codex | 1/claude); None disables the worker."""
    return _BACKENDS.get(os.environ.get("BASIN_LLM", ""))


def run(store: Store, project_id: str, branch_id: str, session_id: str, complete=None) -> dict:
    complete = complete or select_backend()
    if complete is None:
        return {"skipped": "no LLM backend (set BASIN_LLM=codex, or =1 for legacy claude -p)"}
    events = [e for e in store.read_jsonl(store.events_path(session_id)) if e.get("t") == "raw_event"]
    if not events:
        return {"skipped": "no events"}
    convo = "\n".join(f"[{e['event_type']}] {e.get('content_text','')}" for e in events)[:12000]
    raw = complete(_PROMPT + convo)
    if not raw:
        return {"skipped": "backend returned nothing (BASIN_LLM=codex needs `codex` on PATH)"}
    clean = forgiving_validate(raw)
    staged, subj_to_atom = [], {}
    for a in clean["atoms"]:
        res = ops.stage_atom(store, project_id, branch_id, None, a["atom_type"], a["statement"],
                             a["subject_key"], a["authority_tier"], source_quote=a["statement"],
                             confidence_score=a["confidence_score"], created_by="extractor_llm")
        if res:
            staged.append(res[0])
            subj_to_atom[a["subject_key"]] = res[0]
    edges = 0
    for e in clean["edges"]:                 # persist the model's relationship edges (subject_key -> atom_id)
        src, dst = subj_to_atom.get(e["src"]), subj_to_atom.get(e["dst"])
        if src and dst and src != dst:
            ops.record_edge(store, project_id, branch_id, src, dst, e["relation"],
                            confidence="INFERRED", created_by="extractor_llm")
            edges += 1
    return {"staged": len(staged), "edges": edges}
