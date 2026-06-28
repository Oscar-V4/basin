"""Transcript -> raw_event JSONL (append-only truth).

Tolerant of Claude Code transcript shapes. Each event carries a raw_hash over
(event_type, normalized text) — the key to deterministic fork detection: a forked
thread shares an identical raw_hash prefix with its parent, then diverges.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import Store, new_id, now_iso, sha256_hex, norm_ws


def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                elif block.get("type") == "tool_result":
                    parts.append(_text_of(block.get("content")))
                elif "text" in block:
                    parts.append(str(block["text"]))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return _text_of(content.get("content"))
    return str(content)


def _event_type(line: dict) -> str:
    t = line.get("type")
    msg = line.get("message") if isinstance(line.get("message"), dict) else {}
    role = line.get("role") or msg.get("role")
    if role in ("user", "assistant", "system"):
        return role
    if t in ("user", "assistant", "system"):
        return t
    return t or role or "unknown"


def raw_hash(event_type: str, text: str) -> str:
    return "sha256:" + sha256_hex(event_type + "\n" + norm_ws(text))[:32]


def parse_transcript(transcript_path: str | Path) -> list[dict]:
    p = Path(transcript_path)
    out = []
    if not p.exists():
        return out
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(line, dict):
            continue
        msg = line.get("message") if isinstance(line.get("message"), dict) else line
        et = _event_type(line)
        text = _text_of(line.get("content", msg.get("content")))
        out.append({
            "event_type": et,
            "text": text,
            "occurred_at": line.get("timestamp") or line.get("occurred_at") or now_iso(),
        })
    return out


def ingest_transcript(store: Store, project_id: str, session_id: str, branch_id: str,
                      transcript_path: str | Path) -> dict:
    """Append any new raw_events for this session. Idempotent by event_seq."""
    parsed = parse_transcript(transcript_path)
    existing = store.read_jsonl(store.events_path(session_id))
    have = {e.get("event_seq") for e in existing if e.get("t") == "raw_event"}
    new = []
    for seq, ev in enumerate(parsed):
        if seq in have:
            continue
        rh = raw_hash(ev["event_type"], ev["text"])
        rec = {
            "t": "raw_event", "id": new_id("re", session_id, seq), "project_id": project_id,
            "session_id": session_id, "branch_id": branch_id, "event_seq": seq,
            "event_type": ev["event_type"], "occurred_at": ev["occurred_at"],
            "content_text": ev["text"], "token_estimate": max(1, len(ev["text"]) // 4),
            "raw_hash": rh, "created_at": now_iso(),
        }
        store.append_jsonl(store.events_path(session_id), rec)
        new.append(rec)
    return {"parsed": len(parsed), "new": len(new), "events": new}


def session_hashes(store: Store, session_id: str) -> list[str]:
    evs = [e for e in store.read_jsonl(store.events_path(session_id)) if e.get("t") == "raw_event"]
    evs.sort(key=lambda e: e.get("event_seq", 0))
    return [e.get("raw_hash", "") for e in evs]
