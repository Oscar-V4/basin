"""Three-fingerprint change classification.

Ported from understand-anything `packages/core/src/{fingerprint,change-classifier}.ts`:
- per-item:  NONE | COSMETIC | STRUCTURAL | NEW
- per-batch: SKIP | PARTIAL_UPDATE | ARCHITECTURE_UPDATE | FULL_UPDATE
The classifier drives "what materially changed" so cosmetic edits never become
Changes cards, and it sizes how much of a branch to re-summarize.
"""
from __future__ import annotations

import re

from .core import sha256_hex, norm_ws

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_meaning(statement: str) -> str:
    s = (statement or "").lower()
    s = _PUNCT.sub(" ", s)
    return norm_ws(s)


def structural_fp(atom_type: str, subject_key: str) -> str:
    return sha256_hex(f"{atom_type}|{subject_key}")[:16]


def semantic_fp(statement: str) -> str:
    return sha256_hex(_normalize_meaning(statement))[:16]


def cosmetic_fp(statement: str) -> str:
    return sha256_hex(norm_ws(statement))[:16]


def fingerprints(atom_type: str, subject_key: str, statement: str) -> dict:
    return {
        "structural_fp": structural_fp(atom_type, subject_key),
        "semantic_fp": semantic_fp(statement),
        "cosmetic_fp": cosmetic_fp(statement),
    }


def classify_change(prev: dict | None, fp: dict) -> str:
    """Compare a new revision's fingerprints to the previous revision."""
    if prev is None:
        return "NEW"
    if prev.get("cosmetic_fp") == fp["cosmetic_fp"]:
        return "NONE"
    if prev.get("semantic_fp") == fp["semantic_fp"]:
        return "COSMETIC"  # same meaning, different wording
    return "STRUCTURAL"


def classify_session(changes: list[str], total_items: int | None = None) -> str:
    """Aggregate per-item changes into a batch action (UA thresholds, change-classifier.ts:16-19)."""
    structural = sum(1 for c in changes if c in ("STRUCTURAL", "NEW"))
    total = total_items if total_items is not None else max(len(changes), 1)
    if structural == 0:
        return "SKIP"
    if structural > 30 or structural > 0.5 * total:
        return "FULL_UPDATE"
    if structural > 10:
        return "ARCHITECTURE_UPDATE"
    return "PARTIAL_UPDATE"
