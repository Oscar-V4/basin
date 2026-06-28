"""Basin core — paths, hashing, ids, time, config, and the JSONL/refs store.

Files are the source of truth (append-only JSONL); SQLite is a rebuildable index.
This module owns the on-disk `.basin/` contract and low-level read/append.
stdlib only (runs in the hook hot path).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:  # non-posix
    _HAVE_FCNTL = False

VERSION = "0.1.0"

# ---- enums / vocab (mirrors BLUEPRINT 4.1) -------------------------------
ATOM_TYPES = (
    "fact", "decision", "assumption", "constraint", "task", "open_question",
    "artifact", "preference", "risk", "principle", "rejected_path",
)
VISIBILITY = ("untracked", "staged", "tracked", "released", "archived")
LIFECYCLE = ("candidate", "staged", "active", "rejected", "superseded", "pruned", "released")
AUTHORITY_TIERS = (
    "user_explicit", "user_approved", "artifact_declared", "tool_observed",
    "assistant_proposed", "model_inferred", "external_untrusted",
)
AUTHORITY_RANK = {t: i for i, t in enumerate(AUTHORITY_TIERS)}  # lower = more authoritative
CHECKPOINT_KINDS = (
    "manual", "pre_compact", "session_end", "semantic_commit", "merge",
    "release_build", "fork_point", "placeholder",
)
EDGE_RELATIONS = (
    "contradicts", "supersedes", "refines", "depends_on", "blocks",
    "references_session", "inherits_context",
)


# ---- primitives ----------------------------------------------------------
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def norm_ws(s: str | None) -> str:
    return " ".join((s or "").split())


def new_id(prefix: str, *parts) -> str:
    """Content-addressed id: stable for the same logical content (graphify spirit)."""
    digest = sha256_hex("::".join(str(p) for p in parts))[:16]
    return f"{prefix}_{digest}"


_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def safe_id(s) -> str:
    """Sanitize an externally-influenced id before it becomes a filename.

    Strips path separators and rejects dotfile/traversal names so attacker-controlled
    hook stdin (session_id, branch names) can never escape `.basin/`.
    """
    raw = str(s)
    cleaned = _UNSAFE_ID.sub("_", raw)
    if not cleaned or cleaned in (".", "..") or cleaned.startswith("."):
        return "_" + sha256_hex(raw)[:16]
    return cleaned[:128]


# ---- minimal flat YAML (config only; we never add a yaml dep) -------------
def _coerce(v: str):
    s = v.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s.strip().strip('"').strip("'")


def load_flat_yaml(path: Path) -> dict:
    out: dict = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = _coerce(v)
    return out


DEFAULT_CONFIG = {
    "project_id": "p_default",
    "project_name": "project",
    "canon_branch": "main",
    "default_lod": "standard",
    "lod_brief_tokens": 2000,
    "lod_standard_tokens": 4000,
    "lod_full_tokens": 8000,
    "retrieve_on_demand_max_tokens": 20000,
    "min_fork_prefix": 3,
    "enabled": True,
    "auto_fork": False,
}

_CONFIG_KEYS = (
    "project_id", "project_name", "canon_branch", "default_lod",
    "lod_brief_tokens", "lod_standard_tokens", "lod_full_tokens",
    "retrieve_on_demand_max_tokens", "min_fork_prefix", "enabled", "auto_fork",
)


# ---- the store -----------------------------------------------------------
class Store:
    """Read/append the `.basin/` files and moving refs for one project root."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.dir = self.root / ".basin"

    # config
    @property
    def config_path(self) -> Path:
        return self.dir / "config.yaml"

    def config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(load_flat_yaml(self.config_path))
        return cfg

    def write_config(self, cfg: dict) -> None:
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        lines = [f"{k}: {merged[k]}" for k in _CONFIG_KEYS]
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text("# Basin project config\n" + "\n".join(lines) + "\n", encoding="utf-8")

    def set_config(self, key: str, value) -> None:
        cfg = self.config()
        cfg[key] = value
        self.write_config(cfg)

    # truth (append-only JSONL) — external ids sanitized to stay under .basin/
    def events_path(self, session_id: str) -> Path:
        return self.dir / "events" / f"{safe_id(session_id)}.jsonl"

    def atom_path(self, atom_id: str) -> Path:
        return self.dir / "atoms" / f"{safe_id(atom_id)}.jsonl"

    @property
    def edges_path(self) -> Path:
        return self.dir / "edges.jsonl"

    @property
    def checkpoints_path(self) -> Path:
        return self.dir / "checkpoints.jsonl"

    @property
    def sessions_path(self) -> Path:
        return self.dir / "sessions.jsonl"

    @property
    def do_not_load_path(self) -> Path:
        return self.dir / "do_not_load.jsonl"

    @property
    def branches_meta_path(self) -> Path:
        return self.dir / "branches.jsonl"  # append-only branch creation log

    # moving refs (overwritable pointers / projections) — branch ids sanitized
    def ref_branch_head(self, branch_id: str) -> Path:
        return self.dir / "refs" / "branches" / safe_id(branch_id)

    def ref_atoms(self, branch_id: str) -> Path:
        return self.dir / "refs" / "atoms" / f"{safe_id(branch_id)}.json"

    @property
    def index_db(self) -> Path:
        return self.dir / ".index" / "basin.db"

    @contextlib.contextmanager
    def lock(self, name: str):
        """Advisory file lock (posix flock) serializing concurrent writers (hooks)."""
        if not _HAVE_FCNTL:
            yield
            return
        lock_path = self.dir / ".index" / "locks" / f"{safe_id(name)}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()

    def update_atom_refs(self, branch_id: str, mutate) -> None:
        """Locked read-modify-write of a branch's atom_ref pointer (no lost updates)."""
        with self.lock(f"refs-{branch_id}"):
            refs = self.read_atom_refs(branch_id)
            mutate(refs)
            self.write_atom_refs(branch_id, refs)

    # io helpers
    @staticmethod
    def append_jsonl(path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # refs/atoms/<branch>.json — the moving atom_ref pointer (effective state)
    def read_atom_refs(self, branch_id: str) -> dict:
        p = self.ref_atoms(branch_id)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def write_atom_refs(self, branch_id: str, refs: dict) -> None:
        p = self.ref_atoms(branch_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(refs, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)  # atomic ref update (P0-4)

    def set_branch_head(self, branch_id: str, checkpoint_id: str) -> None:
        p = self.ref_branch_head(branch_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(checkpoint_id + "\n", encoding="utf-8")

    def get_branch_head(self, branch_id: str) -> str | None:
        p = self.ref_branch_head(branch_id)
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    # revisions per atom file
    def latest_revision(self, atom_id: str) -> dict | None:
        revs = self.read_jsonl(self.atom_path(atom_id))
        revs = [r for r in revs if r.get("t") == "atom_revision"]
        return revs[-1] if revs else None

    def get_revision(self, atom_id: str, revision_id: str) -> dict | None:
        """Fetch a specific revision — the branch-correct read (P0-1)."""
        for r in self.read_jsonl(self.atom_path(atom_id)):
            if r.get("t") == "atom_revision" and r.get("id") == revision_id:
                return r
        return None

    def has_revision(self, atom_id: str, revision_id: str) -> bool:
        return self.get_revision(atom_id, revision_id) is not None

    def all_atom_ids(self) -> list[str]:
        d = self.dir / "atoms"
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

    def list_sessions(self) -> list[str]:
        d = self.dir / "events"
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

    def scaffold(self, cfg: dict | None = None) -> None:
        """Create the `.basin/` skeleton (idempotent)."""
        for sub in ("events", "atoms", "refs/branches", "refs/atoms", "canon/releases", "packs", ".index"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        gi = self.dir / ".gitignore"
        if not gi.exists():
            gi.write_text(".index/\n", encoding="utf-8")
        if not self.config_path.exists():
            self.write_config(cfg or {})
