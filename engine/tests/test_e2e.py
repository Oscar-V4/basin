"""End-to-end engine test. Run: python tests/test_e2e.py

Covers BLUEPRINT tests: T0 init, T1 hook non-blocking, T2 extraction, T3 cosmetic skip,
supersede edge, pack token budget, T7 session linking, idempotent ingest.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

from basin import core, ops, ingest, extract_det, reindex, project, fork, hook  # noqa: E402
from basin.compile_pack import compile_context_pack  # noqa: E402
from basin.fingerprint import classify_session  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "transcript_main.jsonl")
_fails = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="basin_test_")
    store = core.Store(tmp)
    store.scaffold({"project_id": "p_test", "project_name": "test"})
    pid = store.config()["project_id"]
    counts = reindex.reindex(store)

    # T0 — init
    check("T0 init: 8 tables", counts["_tables"] == 8, f"tables={counts['_tables']}")
    check("T0 init: 3 views", counts["_views"] == 3, f"views={counts['_views']}")
    check("T0 init: .basin exists", store.dir.exists())

    # T1 — hook precompact is non-blocking and fail-soft (rc 0), and captures
    hook_input = {"session_id": "s_main", "transcript_path": FIX, "cwd": str(store.root)}
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(hook_input))
    rc = hook.run("precompact")
    sys.stdin = old_stdin
    check("T1 hook: exit 0", rc == 0)
    raw = store.read_jsonl(store.events_path("s_main"))
    check("T1 hook: raw_events captured", len(raw) >= 7, f"events={len(raw)}")
    cks = [c for c in store.read_jsonl(store.checkpoints_path) if c.get("kind") == "pre_compact"]
    check("T1 hook: pre_compact checkpoint", len(cks) == 1)
    staged = ops.staged_candidates(store, "main")
    check("T1 hook: staged candidates", len(staged) >= 6, f"staged={len(staged)}")

    # T2 — deterministic extraction caught the right types
    types = {a["atom_type"] for a in staged}
    for t in ("decision", "constraint", "rejected_path", "open_question", "preference"):
        check(f"T2 extract: has {t}", t in types, f"types={sorted(types)}")

    # fail-soft on bad input (no transcript) — must not raise
    sys.stdin = io.StringIO("{}")
    rc2 = hook.run("session_end")
    sys.stdin = old_stdin
    check("T1 hook: fail-soft on empty input", rc2 == 0)

    # idempotent: re-running precompact ingests nothing new
    sys.stdin = io.StringIO(json.dumps(hook_input))
    hook.run("precompact")
    sys.stdin = old_stdin
    raw2 = store.read_jsonl(store.events_path("s_main"))
    check("idempotent ingest: no new raw_events", len(raw2) == len(raw), f"{len(raw2)} vs {len(raw)}")

    # supersede — same (type, subject), structural change → supersedes + edge
    ops.stage_atom(store, pid, "main", None, "decision", "We will use SQLite as the truth.", "storage-truth", "user_explicit")
    r2 = ops.stage_atom(store, pid, "main", None, "decision", "We will use files as the truth.", "storage-truth", "user_explicit")
    rev = store.latest_revision(r2[0])
    check("supersede: supersedes_revision_id set", bool(rev.get("supersedes_revision_id")))
    check("supersede: change_kind STRUCTURAL", rev.get("change_kind") == "STRUCTURAL", rev.get("change_kind"))
    edges = [e for e in store.read_jsonl(store.edges_path) if e.get("relation") == "supersedes"]
    check("supersede: edge recorded", len(edges) >= 1)

    # T3 — cosmetic re-statement is COSMETIC, session aggregate = SKIP; exact dup = NONE (skipped)
    ops.stage_atom(store, pid, "main", None, "decision", "Files are the truth.", "cosmetic-x", "user_explicit")
    cz = ops.stage_atom(store, pid, "main", None, "decision", "files are the truth", "cosmetic-x", "user_explicit")
    crev = store.latest_revision(cz[0])
    check("T3 cosmetic: change_kind COSMETIC", crev.get("change_kind") == "COSMETIC", crev.get("change_kind"))
    check("T3 cosmetic: session classify SKIP", classify_session(["COSMETIC"]) == "SKIP")
    dup = ops.stage_atom(store, pid, "main", None, "decision", "files are the truth", "cosmetic-x", "user_explicit")
    check("T3 cosmetic: exact dup skipped (idempotent)", dup is None)

    # save (promote) then compile pack
    n = ops.promote_branch(store, "main")
    check("save: promoted atoms", n >= 6, f"promoted={n}")
    pack_id, path, pack = compile_context_pack(store, pid, "main", lod="standard")
    used = pack["token_budget"]["always_load_used"]
    check("pack: within token budget", used <= 4000, f"used={used}")
    check("pack: has current_decisions", len(pack["current_decisions"]) >= 1, f"n={len(pack['current_decisions'])}")
    check("pack: file written", os.path.exists(path))

    # T7 — session linking carries base_context_pack_id
    sl = ops.register_session(store, pid, "s_rookie", "main", base_context_pack_id=pack_id)
    sess = [s for s in store.read_jsonl(store.sessions_path) if s.get("id") == sl]
    check("T7 link: base_context_pack_id set", sess and sess[0].get("base_context_pack_id") == pack_id)

    # projections + reindex round-trip
    project.project_all(store)
    counts2 = reindex.reindex(store)
    con = sqlite3.connect(store.index_db)
    nca = con.execute("SELECT count(*) FROM v_current_atoms WHERE branch_id='main'").fetchone()[0]
    con.close()
    check("reindex: v_current_atoms populated", nca >= 6, f"current={nca}")
    check("project: CANON.md exists", (store.dir / "canon" / "CANON.md").exists())

    print("\n" + ("ALL PASS" if not _fails else f"FAILED: {_fails}"))
    print(f"workdir: {tmp}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
