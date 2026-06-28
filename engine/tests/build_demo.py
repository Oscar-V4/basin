"""Build a rich demo project (canon + a fork branch) for TUI verification.

Usage: python tests/build_demo.py /path/to/demo
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from basin import core, ops, ingest, extract_det, reindex, project, fork  # noqa: E402

MAIN = os.path.join(HERE, "fixtures", "transcript_main.jsonl")
FORK = os.path.join(HERE, "fixtures", "transcript_fork.jsonl")


def build(root):
    store = core.Store(root)
    store.scaffold({"project_id": "p_demo", "project_name": "basin"})
    pid = "p_demo"
    main = ops.ensure_branch(store, "main", name="main", intent="canon line")["branch_id"]

    # canon session
    ingest.ingest_transcript(store, pid, "s_main", main, MAIN)
    evs = [e for e in store.read_jsonl(store.events_path("s_main")) if e.get("t") == "raw_event"]
    ck = ops.create_checkpoint(store, pid, main, "session_end", title="session: design v2", created_by="hook")
    extract_det.extract_events(store, pid, main, ck, evs)
    ops.promote_branch(store, main)
    ops.create_checkpoint(store, pid, main, "semantic_commit", title="save: lock v2 storage + plugin", created_by="user")

    # fork a spike branch off the canon head
    head = store.get_branch_head(main)
    bid = fork.create_branch(store, pid, "v2-tui-spike", intent="drop GUI, build a TUI", base_checkpoint_id=head)
    ingest.ingest_transcript(store, pid, "s_fork", bid, FORK)
    fevs = [e for e in store.read_jsonl(store.events_path("s_fork")) if e.get("t") == "raw_event"]
    fck = ops.create_checkpoint(store, pid, bid, "session_end", title="session: TUI pivot", created_by="hook")
    extract_det.extract_events(store, pid, bid, fck, fevs)
    ops.promote_branch(store, bid)
    ops.create_checkpoint(store, pid, bid, "semantic_commit", title="save: TUI decisions", created_by="user")

    reindex.reindex(store)
    project.project_all(store)
    print(f"demo built at {root}: branches={len(ops.list_branches(store))} "
          f"checkpoints={len(ops.list_checkpoints(store))} "
          f"canon_atoms={len(ops.current_atoms(store, main))} spike_atoms={len(ops.current_atoms(store, bid))}")
    return store, bid


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "./demo")
