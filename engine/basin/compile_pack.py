"""compile_context_pack — the deployment unit of AI collaboration.

Selects active atoms by authority/confidence under a token budget (always_load),
spills the rest to a retrieval_manifest, applies do_not_load, and emits handoff YAML.
"""
from __future__ import annotations

from .core import Store, new_id, now_iso
from . import ops

_LOD_KEY = {"brief": "lod_brief_tokens", "standard": "lod_standard_tokens", "full": "lod_full_tokens"}

# always_load is a scarce budget — fill it with the highest-signal atoms first.
# Lower number = higher priority. (Dogfood lesson: without this the budget filled in
# arbitrary DB order and a flood of low-value constraints starved every decision.)
_AUTH_RANK = {"user_explicit": 0, "assistant_proposed": 1, "tool_observed": 2,
              "artifact_declared": 3, "model_inferred": 4}
_TYPE_PRIORITY = {"principle": 0, "decision": 1, "constraint": 2, "open_question": 3,
                  "rejected_path": 4, "preference": 5, "risk": 6, "fact": 7,
                  "assumption": 8, "task": 9, "artifact": 10}


def _rank_key(a: dict) -> tuple:
    return (
        _AUTH_RANK.get(a.get("authority_tier"), 9),
        -round(float(a.get("confidence_score", 0) or 0), 4),
        _TYPE_PRIORITY.get(a.get("atom_type"), 99),
        -int(a.get("revision_no", 0) or 0),       # newer revision wins ties
    )

_GENERIC_QUESTIONS = [
    "What is a commit in this system, and why is it not the same as a chat turn?",
    "Why is a summary alone insufficient to inherit this project's context?",
]


def _continuity_test(always: list[dict]) -> list[str]:
    """Derive questions from THIS pack's atoms so the test verifies real inheritance."""
    qs = list(_GENERIC_QUESTIONS)
    have = {a.get("atom_type") for a in always}
    if "decision" in have:
        subj = next((a.get("subject_key") for a in always if a.get("atom_type") == "decision"), "the key decision")
        qs.append(f"What was decided about '{subj}', and why?")
    if "rejected_path" in have:
        qs.append("Name one rejected path you must not revive.")
    if "open_question" in have:
        qs.append("State one open question that is still unresolved.")
    if "constraint" in have and len(qs) < 5:
        qs.append("Name one binding constraint that limits how you proceed.")
    return qs[:5]


# ---- tiny YAML emitter (no dependency) -----------------------------------
def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def to_yaml(obj, indent: int = 0) -> str:
    sp = "  " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        if not obj:
            return f"{sp}{{}}"
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{sp}{k}:")
                lines.append(to_yaml(v, indent + 1))
            elif isinstance(v, (dict, list)):
                lines.append(f"{sp}{k}: {'[]' if isinstance(v, list) else '{}'}")
            else:
                lines.append(f"{sp}{k}: {_scalar(v)}")
    elif isinstance(obj, list):
        if not obj:
            return f"{sp}[]"
        for item in obj:
            if isinstance(item, dict) and item:
                first = True
                for k, v in item.items():
                    prefix = f"{sp}- " if first else f"{sp}  "
                    if isinstance(v, (dict, list)) and v:
                        lines.append(f"{prefix}{k}:")
                        lines.append(to_yaml(v, indent + 2))  # nested under a list item
                    elif isinstance(v, (dict, list)):
                        lines.append(f"{prefix}{k}: {'[]' if isinstance(v, list) else '{}'}")
                    else:
                        lines.append(f"{prefix}{k}: {_scalar(v)}")
                    first = False
            elif isinstance(item, (dict, list)):
                lines.append(f"{sp}- {'[]' if isinstance(item, list) else '{}'}")
            else:
                lines.append(f"{sp}- {_scalar(item)}")
    return "\n".join(lines)


def _effective_do_not_load(store: Store, branch_id: str) -> tuple[set[str], set[str]]:
    """Fold the latest action per (branch, atom). Returns (excluded, retrieve_only)."""
    latest: dict[str, str] = {}
    for r in store.read_jsonl(store.do_not_load_path):
        if r.get("t") == "do_not_load" and r.get("branch_id") == branch_id and r.get("atom_id"):
            latest[r["atom_id"]] = r.get("action")  # last write wins
    excluded = {a for a, act in latest.items() if act == "exclude"}
    retrieve_only = {a for a, act in latest.items() if act == "retrieve_only"}
    return excluded, retrieve_only


def compile_context_pack(store: Store, project_id: str, branch_id: str,
                         lod: str = "standard") -> tuple[str, str, dict]:
    cfg = store.config()
    budget = int(cfg.get(_LOD_KEY.get(lod, "lod_standard_tokens"), 4000))
    head = store.get_branch_head(branch_id)
    pack_id = new_id("cp", project_id, branch_id, lod, head or "nohead")

    atoms = ops.current_atoms(store, branch_id, lifecycles=("active", "released"))
    excluded, retrieve_only = _effective_do_not_load(store, branch_id)
    atoms = [a for a in atoms if a.get("atom_id") not in excluded]
    atoms.sort(key=_rank_key)                  # highest-signal first, so the budget can't starve decisions

    always, retrieve, used = [], [], 0
    for a in atoms:
        tok = int(a.get("token_estimate", max(1, len(a.get("statement", "")) // 4)))
        if a.get("atom_id") in retrieve_only:
            retrieve.append(a)  # forced to retrieval regardless of budget
        elif used + tok <= budget:
            always.append(a)
            used += tok
        else:
            retrieve.append(a)

    def by_type(items, t):
        return [{"atom": x["atom_id"], "statement": x["statement"],
                 "authority": x["authority_tier"], "confidence": round(float(x.get("confidence_score", 0)), 2)}
                for x in items if x.get("atom_type") == t]

    pack = {
        "context_pack": {
            "id": pack_id, "project": project_id, "branch": branch_id,
            "checkpoint": head, "lod": lod,
        },
        "token_budget": {"always_load_max": budget, "always_load_used": used,
                         "retrieve_on_demand_max": int(cfg.get("retrieve_on_demand_max_tokens", 20000))},
        "identity": {"product_name": "Basin", "canon_name": "Canon"},
        "mission": by_type(always, "principle"),
        "current_decisions": by_type(always, "decision"),
        "constraints": by_type(always, "constraint"),
        "open_questions": by_type(always, "open_question"),
        "rejected_paths": by_type(always, "rejected_path"),
        "preferences": by_type(always, "preference"),
        "risks": by_type(always, "risk"),
        "facts": by_type(always, "fact"),
        "assumptions": by_type(always, "assumption"),
        "tasks": by_type(always, "task"),
        "artifacts": by_type(always, "artifact"),
        "do_not_load": sorted(excluded),
        "retrieval_manifest": [{"atom": x["atom_id"], "type": x["atom_type"],
                                "subject": x.get("subject_key", ""),
                                "hint": x["statement"][:80]} for x in retrieve],
        "operating_instructions": [
            "You inherited this Context Pack. Treat current_decisions and constraints as binding.",
            "Do not resurrect anything under rejected_paths or do_not_load.",
            "Before starting work, answer the continuity_test below to confirm inheritance.",
        ],
        "continuity_test": _continuity_test(always),
    }
    yaml = to_yaml(pack) + "\n"
    out = store.dir / "packs" / f"{branch_id}.{lod}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml, encoding="utf-8")
    return pack_id, str(out), pack
