"""`basin` CLI. Engine-only commands (no app, no LLM worker yet)."""
from __future__ import annotations

import argparse
import sys

from .core import Store, VERSION, new_id
from . import ops, ingest, extract_det, reindex, project, fork, hook
from .compile_pack import compile_context_pack


def _store(args) -> Store:
    return Store(args.root)


def cmd_setup(args):
    store = _store(args)
    cfg = {}
    if args.project_id:
        cfg["project_id"] = args.project_id
    else:
        cfg["project_id"] = new_id("p", str(store.root))
    if args.name:
        cfg["project_name"] = args.name
    store.scaffold(cfg)
    ops.ensure_branch(store, store.config().get("canon_branch", "main"), name="main", intent="canon line")
    counts = reindex.reindex(store)
    print(f"setup: {store.dir} (project_id={store.config()['project_id']}) "
          f"tables={counts['_tables']} views={counts['_views']}")


def cmd_ingest(args):
    store = _store(args)
    pid = store.config()["project_id"]
    branch = args.branch or store.config().get("canon_branch", "main")
    ops.ensure_branch(store, branch, name=branch)
    res = ingest.ingest_transcript(store, pid, args.session, branch, args.transcript)
    print(f"ingest: parsed={res['parsed']} new={res['new']} session={args.session} branch={branch}")


def cmd_extract(args):
    store = _store(args)
    pid = store.config()["project_id"]
    branch = args.branch or store.config().get("canon_branch", "main")
    events = [e for e in store.read_jsonl(store.events_path(args.session)) if e.get("t") == "raw_event"]
    res = extract_det.extract_events(store, pid, branch, None, events)
    print(f"extract: staged={res['staged']} from {len(events)} events on {branch}")
    for a in res["atoms"][:20]:
        print(f"  + [{a['type']}] {a['statement'][:80]}")


def cmd_save(args):
    store = _store(args)
    pid = store.config()["project_id"]
    branch = args.branch or store.config().get("canon_branch", "main")
    n = ops.promote_branch(store, branch)
    ck = ops.create_checkpoint(store, pid, branch, kind="semantic_commit",
                               title=args.message or "save", created_by="user")
    project.append_ledger(store, branch, args.message or "", n)
    project.project_branch(store, branch)
    project.project_canon(store)
    reindex.reindex(store)
    print(f"save: promoted {n} atoms on {branch} (checkpoint {ck})")


def cmd_pack(args):
    store = _store(args)
    pid = store.config()["project_id"]
    branch = args.branch or store.config().get("canon_branch", "main")
    pack_id, path, pack = compile_context_pack(store, pid, branch, lod=args.lod)
    print(f"pack: {pack_id} -> {path} (always_load_used={pack['token_budget']['always_load_used']} "
          f"/ {pack['token_budget']['always_load_max']} tokens)")
    if args.show:
        print("---")
        print(open(path, encoding="utf-8").read())


def cmd_inherit(args):
    store = _store(args)
    pid = store.config()["project_id"]
    branch = args.branch or store.config().get("canon_branch", "main")
    pack_id, path, _ = compile_context_pack(store, pid, branch, lod=args.lod)
    new_session = args.session or new_id("s", "inherited", branch)
    sl = ops.register_session(store, pid, new_session, branch,
                              base_context_pack_id=pack_id, status="active")
    reindex.reindex(store)
    print(f"inherit: session={new_session} link={sl} base_context_pack_id={pack_id}")
    print(f"handoff pack: {path}")
    print("--- paste the following as the new thread's first message ---")
    print(open(path, encoding="utf-8").read())
    print("You inherited this Context Pack. Before starting, answer the continuity_test above.")


def cmd_fork(args):
    store = _store(args)
    pid = store.config()["project_id"]
    canon = store.config().get("canon_branch", "main")
    base = args.base or store.get_branch_head(canon)
    bid = fork.create_branch(store, pid, args.name, intent=args.intent or "",
                             base_checkpoint_id=base, from_branch=canon)
    if args.from_session and args.transcript:
        ops.ensure_branch(store, bid, name=args.name)
        ingest.ingest_transcript(store, pid, args.from_session, bid, args.transcript)
    reindex.reindex(store)
    print(f"fork: branch={bid} name={args.name} base={base}")


def cmd_detect_fork(args):
    store = _store(args)
    pid = store.config()["project_id"]
    res = fork.detect_fork(store, pid, args.session, min_prefix=int(store.config().get("min_fork_prefix", 3)))
    print(f"detect-fork: {res}")


def cmd_status(args):
    store = _store(args)
    branch = args.branch or store.config().get("canon_branch", "main")
    cur = ops.current_atoms(store, branch)
    stg = ops.staged_candidates(store, branch)
    print(f"status branch={branch}: active={len(cur)} staged={len(stg)}")
    for a in cur[:30]:
        print(f"  ● [{a['atom_type']}] {a['statement'][:76]}  ({a['authority_tier']})")
    for a in stg[:30]:
        print(f"  ○ [{a['atom_type']}] {a['statement'][:76]}  ({a.get('change_kind','')})")


def cmd_reindex(args):
    store = _store(args)
    counts = reindex.reindex(store)
    print("reindex:", {k: v for k, v in counts.items() if not k.startswith("_")},
          f"tables={counts['_tables']} views={counts['_views']}")


def cmd_project(args):
    store = _store(args)
    paths = project.project_all(store)
    print(f"project: canon={paths['canon']} branches={len(paths['branches'])}")


def cmd_toggle(args):
    store = _store(args)
    store.set_config("enabled", args.on)
    print(f"basin {'on' if args.on else 'off'}: engine {'enabled' if args.on else 'disabled'} for {store.root}")


def cmd_graph(args):
    from . import graph
    store = _store(args)
    res = graph.run(store)
    print("graph:", res)


def cmd_worker(args):
    from . import extract_llm
    store = _store(args)
    pid = store.config()["project_id"]
    branch = args.branch or ops.current_branch_for_session(store, args.session, store.config().get("canon_branch", "main"))
    res = extract_llm.run(store, pid, branch, args.session)
    reindex.reindex(store)
    print("worker:", res)


def cmd_reconcile(args):
    store = _store(args)
    canon = store.config().get("canon_branch", "main")
    byname = {b["name"]: b for b in ops.list_branches(store)}
    canon_id = byname.get(canon, {}).get("branch_id", canon)
    branch = args.branch
    if branch in byname:
        branch = byname[branch]["branch_id"]
    d = ops.diff_branch_vs_canon(store, branch, canon_id)
    print(f"reconcile {branch} -> canon: +{len(d['new'])} new  ~{len(d['changed'])} changed  -{len(d['removed'])} canon-only")
    for a in d["new"]:
        print(f"  + {a['statement'][:80]}")
    for c in d["changed"]:
        print(f"  ~ {c['branch']['statement'][:80]}")


def cmd_tui(args):
    from . import tui
    return tui.main(args.root, argv=(["--selftest"] if args.selftest else []))


def cmd_hook(args):
    return hook.run(args.mode)


def build_parser():
    p = argparse.ArgumentParser(prog="basin", description="Basin engine CLI")
    p.add_argument("--root", default=".", help="project root containing .basin/")
    p.add_argument("--version", action="version", version=f"basin {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup"); s.add_argument("--project-id", dest="project_id"); s.add_argument("--name"); s.set_defaults(func=cmd_setup)
    s = sub.add_parser("ingest"); s.add_argument("--session", required=True); s.add_argument("--transcript", required=True); s.add_argument("--branch"); s.set_defaults(func=cmd_ingest)
    s = sub.add_parser("extract"); s.add_argument("--session", required=True); s.add_argument("--branch"); s.set_defaults(func=cmd_extract)
    s = sub.add_parser("save"); s.add_argument("--branch"); s.add_argument("-m", "--message", default=""); s.set_defaults(func=cmd_save)
    s = sub.add_parser("pack"); s.add_argument("--branch"); s.add_argument("--lod", default="standard", choices=["brief", "standard", "full"]); s.add_argument("--show", action="store_true"); s.set_defaults(func=cmd_pack)
    s = sub.add_parser("inherit"); s.add_argument("--branch"); s.add_argument("--session"); s.add_argument("--lod", default="standard", choices=["brief", "standard", "full"]); s.set_defaults(func=cmd_inherit)
    s = sub.add_parser("fork"); s.add_argument("--name", required=True); s.add_argument("--intent"); s.add_argument("--base"); s.add_argument("--from-session", dest="from_session"); s.add_argument("--transcript"); s.set_defaults(func=cmd_fork)
    s = sub.add_parser("detect-fork"); s.add_argument("--session", required=True); s.set_defaults(func=cmd_detect_fork)
    s = sub.add_parser("status"); s.add_argument("--branch"); s.set_defaults(func=cmd_status)
    s = sub.add_parser("reindex"); s.set_defaults(func=cmd_reindex)
    s = sub.add_parser("project"); s.set_defaults(func=cmd_project)
    s = sub.add_parser("graph"); s.set_defaults(func=cmd_graph)
    s = sub.add_parser("worker"); s.add_argument("--session", required=True); s.add_argument("--branch"); s.set_defaults(func=cmd_worker)
    s = sub.add_parser("reconcile"); s.add_argument("--branch", required=True); s.set_defaults(func=cmd_reconcile)
    s = sub.add_parser("tui"); s.add_argument("--selftest", action="store_true"); s.set_defaults(func=cmd_tui)
    s = sub.add_parser("on"); s.set_defaults(func=cmd_toggle, on=True)
    s = sub.add_parser("off"); s.set_defaults(func=cmd_toggle, on=False)
    s = sub.add_parser("hook"); s.add_argument("--mode", default="session_end"); s.set_defaults(func=cmd_hook)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
