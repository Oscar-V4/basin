"""Pure, testable data + render layer for the Basin TUI.

The gh-orbit interaction model, our way. Renders are plain data — each tab returns
list[list[(text, style)]] (rows of styled segments) so curses stays a thin blitter and
the whole UI is unit-testable without a terminal.
"""
from __future__ import annotations

import time
import calendar
import unicodedata

from .core import Store, AUTHORITY_RANK
from . import ops

TABS = ["Threads", "Branches", "Changes", "Canon", "Proposals", "Map"]
LANE_STYLES = ["laneA", "laneB", "laneC", "laneD", "laneE"]
CHIP_STYLES = ["chip_a", "chip_b", "chip_c", "chip_d", "chip_e"]
RIGHT_ALIGN = "__right__"

# atom type -> (Section title) for Canon / grouping
_GROUPS = [
    ("principle", "Principles"), ("decision", "Decisions"), ("constraint", "Constraints"),
    ("open_question", "Open questions"), ("rejected_path", "Rejected paths"),
    ("preference", "Preferences"), ("risk", "Risks"), ("task", "Tasks"), ("fact", "Facts"),
]
_NODE = {"fork_point": "◆", "release_build": "◆", "merge": "◇", "placeholder": "○"}
_BADGE_STYLE = {
    "decision": "badge_decision",
    "constraint": "badge_constraint",
    "rejected_path": "badge_rejected_path",
    "open_question": "badge_open_question",
    "risk": "badge_risk",
    "principle": "badge_principle",
    "preference": "badge_preference",
    "task": "badge_task",
    "fact": "badge_fact",
    "assumption": "badge_fact",
    "artifact": "badge_fact",
}


def _char_display_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    category = unicodedata.category(char)
    if category in ("Mn", "Me", "Cf", "Cc"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1


def display_width(text: str) -> int:
    """Return the terminal column width for stdlib-only TUI placement."""
    return sum(_char_display_width(char) for char in str(text))


def fit_display_width(text: str, max_width: int) -> str:
    """Clip text so it fits within `max_width` terminal columns."""
    if max_width <= 0:
        return ""
    out = []
    width = 0
    for char in str(text):
        char_width = _char_display_width(char)
        if char_width == 0:
            if out:
                out.append(char)
            continue
        if width + char_width > max_width:
            break
        out.append(char)
        width += char_width
    return "".join(out)


def row(left, right=None):
    """Return a pure render row with an optional right-aligned tail."""
    out = list(left)
    if right:
        out.append(("", RIGHT_ALIGN))
        out.extend(right)
    return out


def split_row(segs):
    for i, (_text, style) in enumerate(segs):
        if style == RIGHT_ALIGN:
            return segs[:i], segs[i + 1:]
    return segs, []


def row_text(segs, gap="  "):
    left, right = split_row(segs)
    text = "".join(s[0] for s in left)
    if right:
        text += gap + "".join(s[0] for s in right)
    return text


def rel_time(iso: str) -> str:
    if not iso:
        return ""
    try:
        t = time.strptime(iso.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        secs = max(0, int(time.time() - calendar.timegm(t)))
    except Exception:
        return ""
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def infer_merge_source_branch(checkpoint, branch_by_name, branch_by_id):
    """Infer a merge source branch for legacy checkpoints.

    Merge checkpoints do not yet persist `source_branch_id`, so the TUI can only
    recover the source from the CLI-era title convention: `settle <branch>`.
    Keep that compatibility parsing here until the checkpoint schema grows an
    explicit field.
    """
    if checkpoint.get("kind") != "merge":
        return None
    source_branch_id = checkpoint.get("source_branch_id")
    if source_branch_id in branch_by_id:
        return source_branch_id
    title = checkpoint.get("title") or ""
    name = title.removeprefix("settle ").strip() if title.startswith("settle ") else title.strip()
    if not name:
        return None
    if name in branch_by_name:
        return branch_by_name[name]["branch_id"]
    if name in branch_by_id:
        return name
    return None


class Model:
    def __init__(self, root: str):
        self.store = Store(root)
        self.cfg = self.store.config()
        self.canon_branch = self.cfg.get("canon_branch", "main")
        self.current_branch = self.canon_branch
        self.flash = None            # transient one-shot message shown in the status line
        self.reload()

    def reload(self):
        self.cfg = self.store.config()
        self.branches = ops.list_branches(self.store)
        if not any(b["branch_id"] == self.current_branch for b in self.branches):
            # branch ids are content-addressed; fall back to canon by name
            byname = {b["name"]: b for b in self.branches}
            if self.canon_branch in byname:
                self.current_branch = byname[self.canon_branch]["branch_id"]
            elif self.branches:
                self.current_branch = self.branches[0]["branch_id"]
        self.checkpoints = ops.list_checkpoints(self.store)
        self.sessions = ops.list_sessions(self.store)
        # lane assignment: by branch creation order, canon first
        order = sorted(self.branches, key=lambda b: (b.get("name") != self.canon_branch, b.get("created_at", "")))
        self.lane_of = {b["branch_id"]: i for i, b in enumerate(order)}
        self.branch_by_name = {b.get("name", b["branch_id"]): b for b in self.branches}
        self.branch_by_id = {b["branch_id"]: b for b in self.branches}
        self.branch_name = {b["branch_id"]: b.get("name", b["branch_id"]) for b in self.branches}
        self._lane_branch = {self.lane_of[b["branch_id"]]: b["branch_id"] for b in order}
        self._checkpoint_by_id = {c.get("id"): c for c in self.checkpoints}
        self._load_graph_indexes()

    # ---- helpers ----
    def _atoms(self, branch_id, lifecycles=("active", "released")):
        return ops.current_atoms(self.store, branch_id, lifecycles=lifecycles)

    def _load_graph_indexes(self):
        self._revs_by_checkpoint = {}
        for atom_id in self.store.all_atom_ids():
            for rev in self.store.read_jsonl(self.store.atom_path(atom_id)):
                if rev.get("t") != "atom_revision":
                    continue
                ck = rev.get("checkpoint_id")
                if ck:
                    self._revs_by_checkpoint.setdefault(ck, []).append(rev)
        self._edges = [e for e in self.store.read_jsonl(self.store.edges_path) if e.get("t") == "edge"]

    def _lane_style(self, branch_id):
        return LANE_STYLES[self.lane_of.get(branch_id, 0) % len(LANE_STYLES)]

    def _chip_style(self, branch_id):
        return CHIP_STYLES[self.lane_of.get(branch_id, 0) % len(CHIP_STYLES)]

    def _branch_chip(self, branch_id):
        return (f" {self.branch_name.get(branch_id, branch_id)} ", self._chip_style(branch_id))

    def _atom_badge(self, atom):
        t = atom.get("atom_type", "atom")
        label = {
            "open_question": "question",
            "rejected_path": "rejected",
        }.get(t, t)
        return (f" {label} ", _BADGE_STYLE.get(t, "badge_fact"))

    def _atom_meta(self, atom):
        conf = round(float(atom.get("confidence_score", 0) or 0), 2)
        bits = [atom.get("authority_tier", "") or "unknown", f"{conf:.2f}"]
        rt = rel_time(atom.get("created_at", ""))
        if rt:
            bits.append(rt)
        return " · ".join(bits)

    def _checkpoint_diffstat(self, checkpoint):
        added = changed = removed = 0
        for rev in self._revs_by_checkpoint.get(checkpoint.get("id"), []):
            ck = rev.get("change_kind")
            if rev.get("lifecycle_status") in ("rejected", "pruned"):
                removed += 1
            elif ck == "NEW":
                added += 1
            elif ck in ("STRUCTURAL", "COSMETIC"):
                changed += 1
            else:
                added += 1
        return added, changed, removed

    def _diffstat_segments(self, checkpoint):
        added, changed, removed = self._checkpoint_diffstat(checkpoint)
        return [("+", "green"), (str(added), "green"), (" ~", "yellow"), (str(changed), "yellow"),
                (" −", "red" if removed else "dim"), (str(removed), "red" if removed else "dim")]

    def _merge_source_branch(self, checkpoint):
        return infer_merge_source_branch(checkpoint, self.branch_by_name, self.branch_by_id)

    def _fork_parent_branch(self, checkpoint):
        if checkpoint.get("kind") != "fork_point":
            return None
        parent = self._checkpoint_by_id.get(checkpoint.get("parent_checkpoint_id"))
        return parent.get("branch_id") if parent else None

    def _rail_cells(self, checkpoint, idx, spans, nlanes):
        branch_id = checkpoint.get("branch_id")
        branch_lane = self.lane_of.get(branch_id, 0)
        cells = []
        for lane in range(nlanes):
            lane_branch = self._lane_branch.get(lane)
            style = LANE_STYLES[lane % len(LANE_STYLES)]
            if lane == branch_lane:
                cells.append((_NODE.get(checkpoint.get("kind"), "●") + " ", style))
            elif lane_branch in spans and spans[lane_branch][0] <= idx <= spans[lane_branch][1]:
                cells.append(("│ ", style))
            else:
                cells.append(("  ", "dim"))

        parent_branch = self._fork_parent_branch(checkpoint)
        if parent_branch is not None and parent_branch in self.lane_of and parent_branch != branch_id:
            parent_lane = self.lane_of[parent_branch]
            lo, hi = sorted((parent_lane, branch_lane))
            for lane in range(lo + 1, hi):
                cells[lane] = ("──", LANE_STYLES[lane % len(LANE_STYLES)])
            if parent_lane < branch_lane:
                cells[parent_lane] = ("├─", self._lane_style(parent_branch))
                cells[branch_lane] = ("╮ ", self._lane_style(branch_id))
            else:
                cells[branch_lane] = ("╰─", self._lane_style(branch_id))
                cells[parent_lane] = ("┤ ", self._lane_style(parent_branch))
            return cells

        source_branch = self._merge_source_branch(checkpoint)
        if source_branch is not None and source_branch in self.lane_of and source_branch != branch_id:
            source_lane = self.lane_of[source_branch]
            lo, hi = sorted((source_lane, branch_lane))
            for lane in range(lo + 1, hi):
                cells[lane] = ("──", LANE_STYLES[lane % len(LANE_STYLES)])
            if branch_lane < source_lane:
                cells[branch_lane] = ("┤─", self._lane_style(branch_id))
                cells[source_lane] = ("╯ ", self._lane_style(source_branch))
            else:
                cells[source_lane] = ("╰─", self._lane_style(source_branch))
                cells[branch_lane] = ("├ ", self._lane_style(branch_id))
        return cells

    def status_line(self):
        if self.flash:
            text, style = self.flash
            return [(style and "⚠ " or "", style or "muted"), (text, style or "muted")]
        n_atoms = len(self._atoms(self.current_branch))
        on = self.cfg.get("enabled", True)
        dot = ("●", "green") if on else ("○", "muted")
        return [dot, (f" Basin · {self.cfg.get('project_name','project')} · "
                      f"{self.branch_name.get(self.current_branch, self.current_branch)} · "
                      f"{n_atoms} atoms · {len(self.branches)} branches · {len(self.checkpoints)} checkpoints", "muted")]

    def merge_row(self, idx, force=False):
        """Merge/force-merge the atom at proposal row `idx`. Sets a flash message; returns it."""
        atoms = getattr(self, "_proposal_row_atoms", [])
        kinds = getattr(self, "_proposal_row_kind", [])
        if not (0 <= idx < len(atoms)) or not atoms[idx]:
            self.flash = ("Select a proposal row to merge.", "muted")
            return self.flash
        kind = kinds[idx] if idx < len(kinds) else None
        if kind == "conflict" and not force:
            self.flash = ("Conflict — press F to force-merge (overrides divergence/rejection).", "yellow")
            return self.flash
        res = ops.merge_atom(self.store, self.current_branch, self._canon_id(), atoms[idx],
                             project_id=self.cfg.get("project_id"), force=force)
        st = res.get("status")
        self.reload()                # refresh data first; reload() leaves flash untouched
        if st == "conflict":
            self.flash = (f"Conflict ({res.get('reason','')}) — not merged. Press F to force.", "red")
        elif st in ("merged", "noop"):
            self.flash = (f"Merged into Canon ({st}).", "green")
        else:
            self.flash = (f"Merge {st}.", "muted")
        return self.flash

    # ---- THREADS (lane graph) ----
    def threads_rows(self):
        cks = list(reversed(self.checkpoints))  # newest first
        # span per branch in this ordering
        span = {}
        for i, c in enumerate(cks):
            b = c.get("branch_id")
            lo, hi = span.get(b, (i, i))
            span[b] = (min(lo, i), max(hi, i))
        nlanes = (max(self.lane_of.values()) + 1) if self.lane_of else 1
        rows = []
        self._threads_row_checkpoints = []
        for i, c in enumerate(cks):
            b = c.get("branch_id")
            blane = self.lane_of.get(b, 0)
            seg = self._rail_cells(c, i, span, nlanes)
            title = c.get("title") or c.get("kind", "")
            seg.append((title + "  ", "normal"))
            seg.extend(self._diffstat_segments(c))
            right = [self._branch_chip(b)]
            rt = rel_time(c.get("created_at", ""))
            if rt:
                right.append((f" {rt}", "muted"))
            rows.append(row(seg, right))
            self._threads_row_checkpoints.append(c)
        if not rows:
            if self.branches:
                for b in sorted(self.branches, key=lambda x: (x.get("name") != self.canon_branch, x.get("created_at", ""))):
                    lane = self.lane_of.get(b["branch_id"], 0)
                    seg = []
                    for i in range(nlanes):
                        seg.append(("○ ", LANE_STYLES[i % len(LANE_STYLES)] if i == lane else "dim"))
                    seg.append(("waiting for first checkpoint", "muted"))
                    rows.append(row(seg, [self._branch_chip(b["branch_id"]), (f" {b.get('status','active')}", "muted")]))
                    self._threads_row_checkpoints.append(None)
            else:
                rows = [[("No checkpoints yet. Capture a session or run `basin save`.", "muted")]]
                self._threads_row_checkpoints.append(None)
        return rows

    # ---- BRANCHES ----
    def branches_rows(self):
        rows = []
        for b in sorted(self.branches, key=lambda x: (x.get("name") != self.canon_branch, x.get("created_at", ""))):
            bid = b["branch_id"]
            n = len(self._atoms(bid))
            cur = " ▸" if bid == self.current_branch else "  "
            rows.append(row([(cur + " ", "accent"), self._branch_chip(bid)],
                            [(f"{n} atoms · {b.get('status','active')}", "muted"),
                             (f" {rel_time(b.get('created_at',''))}", "muted")]))
            head = (self.store.get_branch_head(bid) or "")[:14]
            rows.append([("     ", "dim"), (b.get("intent") or "(no intent)", "dim"),
                         (f"  · {head}", "dim")])
        if not rows:
            rows = [[("No branches. Run `basin setup`.", "muted")]]
        return rows

    # ---- CHANGES (staged candidates of current branch) ----
    def changes_rows(self):
        stg = ops.staged_candidates(self.store, self.current_branch)
        rows = []
        self._changes_atoms = []
        for a in stg:
            t = a.get("atom_type")
            ck = a.get("change_kind", "")
            if t == "rejected_path":
                glyph, gs = "− ", "red"
            elif t == "open_question":
                glyph, gs = "? ", "accent"
            elif ck == "NEW":
                glyph, gs = "+ ", "green"
            else:
                glyph, gs = "~ ", "yellow"
            rows.append(row([(glyph, gs), self._atom_badge(a), (" ", "normal"),
                             (a.get("statement", "")[:200], "normal")],
                            [(f"{ck or 'change'} · {self._atom_meta(a)}", "muted")]))
            self._changes_atoms.append(a)
        if not rows:
            rows = [[("No staged changes on this branch.", "muted")]]
        return rows

    # ---- CANON ----
    def canon_rows(self):
        atoms = self._atoms(self.canon_branch, lifecycles=("active", "released"))
        rows = []
        self._canon_row_atoms = []
        for _type, title in _GROUPS:
            items = [a for a in atoms if a.get("atom_type") == _type]
            if not items:
                continue
            rows.append([(title, "header")])
            self._canon_row_atoms.append(None)
            for a in items:
                rows.append(row([("  • ", "muted"), self._atom_badge(a), (" ", "normal"),
                                 (a.get("statement", ""), "normal")],
                                [(self._atom_meta(a), "dim")]))
                self._canon_row_atoms.append(a)
            rows.append([("", "normal")])
            self._canon_row_atoms.append(None)
        if not rows:
            rows = [[("Canon is empty. Settle a branch with `basin save`.", "muted")]]
            self._canon_row_atoms = [None]
        return rows

    # ---- PROPOSALS (branch -> canon diff) ----
    def proposals_rows(self):
        # row-aligned merge targets + row kind (reset first so a canon view can't reuse stale ones)
        rows, self._proposal_row_atoms, self._proposal_row_kind = [], [], []

        def add(segs, atom_id=None, kind=None, right=None):
            rows.append(row(segs, right))
            self._proposal_row_atoms.append(atom_id)
            self._proposal_row_kind.append(kind)

        if self.current_branch == self._canon_id():
            add([("Switch to a branch (Branches tab, enter) to see merge proposals.", "muted")])
            return rows
        d = ops.diff_branch_vs_canon(self.store, self.current_branch, self._canon_id())
        if d["new"]:
            add([("+ New — will be added to Canon", "header")])
            for a in d["new"]:
                add([("  + ", "green"), self._atom_badge(a), (" ", "normal"),
                     (a.get("statement", "")[:200], "normal")],
                    a["atom_id"], "new", [(self._atom_meta(a), "dim")])
        if d["changed"]:
            add([("~ Changed — will supersede Canon", "header")])
            for c in d["changed"]:
                a = c["branch"]
                add([("  ~ ", "yellow"), self._atom_badge(a), (" ", "normal"),
                     (a.get("statement", "")[:200], "normal")],
                    a["atom_id"], "changed", [(self._atom_meta(a), "dim")])
        if d.get("conflicts"):
            add([("⚠ Conflicts — settle will NOT auto-merge (F to force)", "header")])
            for c in d["conflicts"]:
                reason = c.get("reason", "conflict")
                a = c["branch"]
                add([("  ", "normal"), (" ⚠ conflict ", "badge_conflict"), (" ", "normal"),
                     self._atom_badge(a), (" ", "normal"), (f"{reason}: ", "yellow"),
                     (a.get("statement", "")[:180], "normal")],
                    a["atom_id"], "conflict", [(self._atom_meta(a), "dim")])
        if d["removed"]:
            add([("− Only in Canon (unchanged here)", "header")])
            for a in d["removed"]:
                add([("  · ", "muted"), self._atom_badge(a), (" ", "normal"),
                     (a.get("statement", "")[:200], "dim")])
        if not (d["new"] or d["changed"] or d.get("conflicts") or d["removed"]):
            add([("No differences from Canon.", "muted")])
        return rows

    # ---- MAP (context graph clusters from graph.py) ----
    def map_rows(self):
        import json
        p = self.store.dir / "clusters.json"
        if not p.exists():
            return [[("Run `basin graph` to build the context map.", "muted")]]
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return [[("clusters.json unreadable — re-run `basin graph`.", "muted")]]
        rows = []
        for c in data.get("clusters", []):
            rows.append([(f"◆ {c.get('name', 'misc')}", "header"), (f"  ({c.get('size', 0)})", "muted")])
            for aid in c.get("atoms", [])[:8]:
                rev = self.store.latest_revision(aid)
                if rev:
                    rows.append([("   • ", "muted"), self._atom_badge(rev), (" ", "normal"),
                                 (rev.get("statement", "")[:90], "normal")])
            rows.append([("", "normal")])
        return rows or [[("No clusters yet.", "muted")]]

    def _canon_id(self):
        byname = {b["name"]: b for b in self.branches}
        return byname.get(self.canon_branch, {}).get("branch_id", self.canon_branch)

    # ---- details ----
    def detail_rows(self, tab, idx):
        target = None
        if tab == "Threads":
            checkpoints = getattr(self, "_threads_row_checkpoints", None)
            if checkpoints is None:
                self.threads_rows()
                checkpoints = getattr(self, "_threads_row_checkpoints", [])
            if 0 <= idx < len(checkpoints):
                target = checkpoints[idx]
            return self._checkpoint_detail(target) if target else [[("No checkpoint details for this row.", "muted")]]
        if tab == "Canon":
            atoms = getattr(self, "_canon_row_atoms", None)
            if atoms is None:
                self.canon_rows()
                atoms = getattr(self, "_canon_row_atoms", [])
            if 0 <= idx < len(atoms):
                target = atoms[idx]
        elif tab == "Changes":
            atoms = getattr(self, "_changes_atoms", None)
            if atoms is None:
                self.changes_rows()
                atoms = getattr(self, "_changes_atoms", [])
            if 0 <= idx < len(atoms):
                target = atoms[idx]
        elif tab == "Proposals":
            atom_ids = getattr(self, "_proposal_row_atoms", None)
            if atom_ids is None:
                self.proposals_rows()
                atom_ids = getattr(self, "_proposal_row_atoms", [])
            if 0 <= idx < len(atom_ids) and atom_ids[idx]:
                target = self._atom_for_branch(atom_ids[idx], self.current_branch) or self.store.latest_revision(atom_ids[idx])
        return self._atom_detail(target) if target else [[("Select an atom or checkpoint row for details.", "muted")]]

    def _atom_for_branch(self, atom_id, branch_id):
        ref = self.store.read_atom_refs(branch_id).get(atom_id)
        if not ref:
            return None
        return self.store.get_revision(atom_id, ref.get("current_revision_id"))

    def _checkpoint_detail(self, checkpoint):
        branch_id = checkpoint.get("branch_id")
        added, changed, removed = self._checkpoint_diffstat(checkpoint)
        rows = [
            row([("Checkpoint", "header")], [self._branch_chip(branch_id), (f" {rel_time(checkpoint.get('created_at',''))}", "muted")]),
            [(checkpoint.get("title") or checkpoint.get("kind", ""), "normal")],
            [("kind ", "dim"), (checkpoint.get("kind", ""), "normal"),
             ("  id ", "dim"), (checkpoint.get("id", ""), "dim")],
            [("parent ", "dim"), ((checkpoint.get("parent_checkpoint_id") or "none"), "normal")],
            [("raw events ", "dim"),
             (f"{checkpoint.get('raw_event_start_seq') or '-'}..{checkpoint.get('raw_event_end_seq') or '-'}", "normal"),
             ("  created_by ", "dim"), (checkpoint.get("created_by", ""), "normal")],
            [("diffstat ", "dim"), ("+", "green"), (str(added), "green"),
             (" ~", "yellow"), (str(changed), "yellow"), (" −", "red" if removed else "dim"),
             (str(removed), "red" if removed else "dim")],
        ]
        if checkpoint.get("summary_text"):
            rows.append([("summary ", "dim"), (checkpoint.get("summary_text", ""), "normal")])

        parent_branch = self._fork_parent_branch(checkpoint)
        if parent_branch:
            rows.append([("forked from ", "dim"), self._branch_chip(parent_branch)])
        source_branch = self._merge_source_branch(checkpoint)
        if source_branch:
            rows.append([("merged from ", "dim"), self._branch_chip(source_branch)])

        revs = self._revs_by_checkpoint.get(checkpoint.get("id"), [])
        rows.append([("", "normal")])
        rows.append([("Atoms in checkpoint", "header")])
        if revs:
            for rev in revs[:8]:
                rows.append([("  ", "normal"), self._atom_badge(rev), (" ", "normal"),
                             (rev.get("statement", "")[:110], "normal")])
            if len(revs) > 8:
                rows.append([(f"  … {len(revs) - 8} more", "muted")])
        else:
            rows.append([("  no atom revisions recorded on this checkpoint", "muted")])
        return rows

    def _atom_detail(self, atom):
        rows = [
            row([self._atom_badge(atom), (" Atom", "header")],
                [self._branch_chip(atom.get("branch_id")), (f" {rel_time(atom.get('created_at',''))}", "muted")]),
            [(atom.get("statement", ""), "normal")],
            [("atom ", "dim"), (atom.get("atom_id", ""), "normal"),
             ("  revision ", "dim"), (atom.get("id", ""), "dim")],
            [("subject ", "dim"), (str(atom.get("subject_key", "")), "normal"),
             ("  change ", "dim"), (atom.get("change_kind", ""), "normal")],
            [("Authority / confidence ", "header"),
             (atom.get("authority_tier", "") or "unknown", "normal"),
             (" · ", "dim"), (f"{round(float(atom.get('confidence_score', 0) or 0), 2):.2f}", "normal")],
        ]
        if atom.get("checkpoint_id") or atom.get("source_raw_event_id") or atom.get("created_by"):
            rows.append([("Provenance ", "header"),
                         ("checkpoint ", "dim"), (atom.get("checkpoint_id") or "none", "normal"),
                         ("  raw ", "dim"), (atom.get("source_raw_event_id") or "none", "normal"),
                         ("  by ", "dim"), (atom.get("created_by") or "unknown", "normal")])
        if atom.get("source_quote"):
            rows.append([("quote ", "dim"), (atom.get("source_quote", "")[:140], "normal")])

        rows.append([("", "normal")])
        rows.append([("Supersedes chain", "header")])
        chain = self._supersedes_chain(atom)
        if chain:
            for rev in chain:
                rows.append([("  ← ", "yellow"), (f"rev {rev.get('revision_no','?')} ", "dim"),
                             (rev.get("statement", "")[:110], "normal")])
        else:
            rows.append([("  none", "muted")])

        rows.append([("", "normal")])
        rows.append([("Related edges", "header")])
        edges = self._related_edges(atom.get("atom_id"))
        if edges:
            for edge in edges[:10]:
                other_id = edge.get("dst") if edge.get("src") == atom.get("atom_id") else edge.get("src")
                other = self.store.latest_revision(other_id) if other_id else None
                direction = "→" if edge.get("src") == atom.get("atom_id") else "←"
                if other_id == atom.get("atom_id"):
                    label = "same atom lineage"
                else:
                    label = other.get("statement", other_id)[:80] if other else other_id
                rows.append([("  ", "normal"), (edge.get("relation", ""), "accent"),
                             (f" {direction} ", "dim"), (label or "", "normal"),
                             (f"  {edge.get('confidence','')}", "dim")])
            if len(edges) > 10:
                rows.append([(f"  … {len(edges) - 10} more", "muted")])
        else:
            rows.append([("  none", "muted")])
        return rows

    def _supersedes_chain(self, atom, limit=8):
        out, seen = [], set()
        atom_id = atom.get("atom_id")
        cur = atom.get("supersedes_revision_id")
        prov = atom.get("provenance") or {}
        if not cur and isinstance(prov, dict):
            vals = prov.get("supersedes") or []
            cur = vals[0] if vals else None
        while atom_id and cur and cur not in seen and len(out) < limit:
            seen.add(cur)
            rev = self.store.get_revision(atom_id, cur)
            if not rev:
                break
            out.append(rev)
            cur = rev.get("supersedes_revision_id")
        return out

    def _related_edges(self, atom_id):
        if not atom_id:
            return []
        out, seen = [], set()
        for edge in self._edges:
            if edge.get("src") != atom_id and edge.get("dst") != atom_id:
                continue
            key = (edge.get("src"), edge.get("dst"), edge.get("relation"), edge.get("confidence"))
            if key in seen:
                continue
            seen.add(key)
            out.append(edge)
        return out

    # ---- actions ----
    def adopt(self, idx):
        atoms = getattr(self, "_changes_atoms", [])
        if 0 <= idx < len(atoms):
            ops.set_atom_lifecycle(self.store, self.current_branch, atoms[idx]["atom_id"], "tracked", "active")
            self.reload(); return True
        return False

    def discard(self, idx):
        atoms = getattr(self, "_changes_atoms", [])
        if 0 <= idx < len(atoms):
            ops.set_atom_lifecycle(self.store, self.current_branch, atoms[idx]["atom_id"], "archived", "rejected")
            self.reload(); return True
        return False

    def set_branch_by_row(self, idx):
        order = sorted(self.branches, key=lambda x: (x.get("name") != self.canon_branch, x.get("created_at", "")))
        bidx = idx // 2  # 2 rows per branch
        if 0 <= bidx < len(order):
            self.current_branch = order[bidx]["branch_id"]
            return True
        return False

    def render(self, tab):
        return {
            "Threads": self.threads_rows, "Branches": self.branches_rows,
            "Changes": self.changes_rows, "Canon": self.canon_rows,
            "Proposals": self.proposals_rows, "Map": self.map_rows,
        }[tab]()
