"""Fold the .basin/ JSONL truth into a fresh SQLite read-model index.

The index is disposable: `basin reindex` rebuilds it from files. Lossless if deleted.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .core import Store

_SCHEMA = Path(__file__).with_name("schema.sql")


def _cols(table_sql_fields: list[str]):
    return table_sql_fields


def reindex(store: Store) -> dict:
    db_path = store.index_db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Build into a private temp DB, then atomically swap — readers never see a
    # tableless/half-built index, and concurrent reindexers don't clobber each other.
    tmp_path = db_path.with_name(f"basin.build.{os.getpid()}.db")
    if tmp_path.exists():
        tmp_path.unlink()
    con = sqlite3.connect(tmp_path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    counts = {}

    def insert(table: str, cols: list[str], rows: list[dict]):
        if not rows:
            counts[table] = 0
            return
        placeholders = ",".join("?" for _ in cols)
        sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        con.executemany(sql, [[r.get(c) for c in cols] for r in rows])
        counts[table] = len(rows)

    # branches (+ head from refs)
    branches = [b for b in store.read_jsonl(store.branches_meta_path) if b.get("t") == "branch"]
    for b in branches:
        b["head_checkpoint_id"] = store.get_branch_head(b["branch_id"])
    insert("branch", ["branch_id", "name", "intent", "status", "base_checkpoint_id",
                      "parent_branch_id", "parent_session_link_id",
                      "merge_policy", "head_checkpoint_id", "created_at"], branches)

    # raw_events (all sessions)
    raw = []
    for sid in store.list_sessions():
        raw += [e for e in store.read_jsonl(store.events_path(sid)) if e.get("t") == "raw_event"]
    insert("raw_event", ["id", "project_id", "session_id", "branch_id", "event_seq",
                         "event_type", "occurred_at", "content_text", "token_estimate", "raw_hash"], raw)

    insert("checkpoint", ["id", "project_id", "branch_id", "kind", "parent_checkpoint_id",
                          "raw_event_start_seq", "raw_event_end_seq", "title", "summary_text",
                          "created_by", "created_at"],
           [c for c in store.read_jsonl(store.checkpoints_path) if c.get("t") == "checkpoint"])

    # atom revisions (all atom files); preserve provenance as JSON text
    revs = []
    for aid in store.all_atom_ids():
        for r in store.read_jsonl(store.atom_path(aid)):
            if r.get("t") != "atom_revision":
                continue
            if isinstance(r.get("provenance"), (dict, list)):
                r["provenance"] = json.dumps(r["provenance"], ensure_ascii=False)
            revs.append(r)
    insert("atom_revision", ["id", "atom_id", "project_id", "branch_id", "checkpoint_id",
                             "semantic_entity_pk", "revision_no", "visibility", "lifecycle_status",
                             "atom_type", "statement", "subject_key", "confidence_score", "authority_tier",
                             "source_raw_event_id", "source_quote", "provenance", "change_kind",
                             "structural_fp", "semantic_fp",
                             "cosmetic_fp", "revision_hash", "supersedes_revision_id", "created_by", "created_at"], revs)

    # atom_refs (per branch json)
    ref_rows = []
    for b in branches:
        bid = b["branch_id"]
        for atom_id, r in store.read_atom_refs(bid).items():
            ref_rows.append({"branch_id": bid, "atom_id": atom_id,
                             "current_revision_id": r.get("current_revision_id"),
                             "visibility": r.get("visibility"), "lifecycle_status": r.get("lifecycle_status"),
                             "updated_at": r.get("updated_at")})
    insert("atom_ref", ["branch_id", "atom_id", "current_revision_id", "visibility",
                        "lifecycle_status", "updated_at"], ref_rows)

    insert("edge", ["id", "project_id", "branch_id", "src", "dst", "relation",
                    "confidence", "source_raw_event_id", "created_at"],
           [e for e in store.read_jsonl(store.edges_path) if e.get("t") == "edge"])

    insert("session_link", ["id", "project_id", "provider", "external_session_id", "branch_id",
                            "base_checkpoint_id", "base_context_pack_id", "parent_session_link_id",
                            "parent_external_session_id", "fork_lcp", "fork_relation", "fork_confidence",
                            "transcript_path", "cwd", "status", "started_at", "last_seen_at"],
           [s for s in store.read_jsonl(store.sessions_path) if s.get("t") == "session_link"])

    insert("do_not_load", ["id", "project_id", "branch_id", "atom_id", "action",
                           "policy", "reason", "created_by", "created_at"],
           [d for d in store.read_jsonl(store.do_not_load_path) if d.get("t") == "do_not_load"])

    con.commit()
    # table/view sanity
    n_tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    n_views = con.execute("SELECT count(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
    con.close()
    os.replace(tmp_path, db_path)  # atomic swap
    counts["_tables"] = n_tables
    counts["_views"] = n_views
    return counts
