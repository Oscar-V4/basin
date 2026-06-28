"""Pure, testable data + render layer for the Basin TUI.

The gh-orbit interaction model, our way. Renders are plain data — each tab returns
list[list[(text, style)]] (rows of styled segments) so curses stays a thin blitter and
the whole UI is unit-testable without a terminal.
"""
from __future__ import annotations

import time

from .core import Store, AUTHORITY_RANK
from . import ops

TABS = ["Threads", "Branches", "Changes", "Canon", "Proposals"]
LANE_STYLES = ["laneA", "laneB", "laneC", "laneD", "laneE"]

# atom type -> (Section title) for Canon / grouping
_GROUPS = [
    ("principle", "Principles"), ("decision", "Decisions"), ("constraint", "Constraints"),
    ("open_question", "Open questions"), ("rejected_path", "Rejected paths"),
    ("preference", "Preferences"), ("risk", "Risks"), ("task", "Tasks"), ("fact", "Facts"),
]
_NODE = {"fork_point": "◆", "release_build": "◆", "merge": "◇", "placeholder": "○"}


def rel_time(iso: str) -> str:
    if not iso:
        return ""
    try:
        t = time.strptime(iso.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        secs = max(0, int(time.time() - (time.timegm(t))))
    except Exception:
        return ""
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


class Model:
    def __init__(self, root: str):
        self.store = Store(root)
        self.cfg = self.store.config()
        self.canon_branch = self.cfg.get("canon_branch", "main")
        self.current_branch = self.canon_branch
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
        self.branch_name = {b["branch_id"]: b.get("name", b["branch_id"]) for b in self.branches}

    # ---- helpers ----
    def _atoms(self, branch_id, lifecycles=("active", "tracked", "released")):
        return ops.current_atoms(self.store, branch_id, lifecycles=lifecycles)

    def status_line(self):
        n_atoms = len(self._atoms(self.current_branch))
        on = self.cfg.get("enabled", True)
        dot = ("●", "green") if on else ("○", "muted")
        return [dot, (f" Basin · {self.cfg.get('project_name','project')} · "
                      f"{self.branch_name.get(self.current_branch, self.current_branch)} · "
                      f"{n_atoms} atoms · {len(self.branches)} branches · {len(self.checkpoints)} checkpoints", "muted")]

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
        for i, c in enumerate(cks):
            b = c.get("branch_id")
            blane = self.lane_of.get(b, 0)
            seg = []
            for lane in range(nlanes):
                lane_branch = next((bb for bb, l in self.lane_of.items() if l == lane), None)
                style = LANE_STYLES[lane % len(LANE_STYLES)]
                if lane == blane:
                    seg.append((_NODE.get(c.get("kind"), "●") + " ", style))
                elif lane_branch in span and span[lane_branch][0] <= i <= span[lane_branch][1]:
                    seg.append(("│ ", style))
                else:
                    seg.append(("  ", "dim"))
            title = c.get("title") or c.get("kind", "")
            seg.append((title + "  ", "normal"))
            seg.append((f"{self.branch_name.get(b, b)}", LANE_STYLES[blane % len(LANE_STYLES)]))
            seg.append((f"  {rel_time(c.get('created_at',''))}", "muted"))
            rows.append(seg)
        if not rows:
            rows = [[("No checkpoints yet. Capture a session or run `basin save`.", "muted")]]
        return rows

    # ---- BRANCHES ----
    def branches_rows(self):
        rows = []
        for b in sorted(self.branches, key=lambda x: (x.get("name") != self.canon_branch, x.get("created_at", ""))):
            bid = b["branch_id"]
            n = len(self._atoms(bid))
            cur = " ▸" if bid == self.current_branch else "  "
            lane = LANE_STYLES[self.lane_of.get(bid, 0) % len(LANE_STYLES)]
            rows.append([(cur + " ", "accent"), (b.get("name", bid), lane),
                         (f"   {n} atoms · {b.get('status','active')}", "muted")])
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
            rows.append([(glyph, gs), (a.get("statement", "")[:200], "normal"),
                         (f"   [{t} · {ck}]", "muted")])
        if not rows:
            rows = [[("No staged changes on this branch.", "muted")]]
        self._changes_atoms = stg
        return rows

    # ---- CANON ----
    def canon_rows(self):
        atoms = self._atoms(self.canon_branch, lifecycles=("active", "released"))
        rows = []
        for _type, title in _GROUPS:
            items = [a for a in atoms if a.get("atom_type") == _type]
            if not items:
                continue
            rows.append([(title, "header")])
            for a in items:
                rows.append([("  • ", "muted"), (a.get("statement", ""), "normal"),
                             (f"  ({a.get('authority_tier','')}, {round(float(a.get('confidence_score',0)),2)})", "dim")])
            rows.append([("", "normal")])
        if not rows:
            rows = [[("Canon is empty. Settle a branch with `basin save`.", "muted")]]
        return rows

    # ---- PROPOSALS (branch -> canon diff) ----
    def proposals_rows(self):
        if self.current_branch == self._canon_id():
            return [[("Switch to a branch (Branches tab, enter) to see merge proposals.", "muted")]]
        d = ops.diff_branch_vs_canon(self.store, self.current_branch, self._canon_id())
        rows = []
        self._proposal_atoms = []
        if d["new"]:
            rows.append([("+ New — will be added to Canon", "header")])
            for a in d["new"]:
                rows.append([("  + ", "green"), (a.get("statement", "")[:200], "normal")])
                self._proposal_atoms.append(a["atom_id"])
        if d["changed"]:
            rows.append([("~ Changed — will supersede Canon", "header")])
            for c in d["changed"]:
                rows.append([("  ~ ", "yellow"), (c["branch"].get("statement", "")[:200], "normal")])
                self._proposal_atoms.append(c["branch"]["atom_id"])
        if d["removed"]:
            rows.append([("− Only in Canon (unchanged here)", "header")])
            for a in d["removed"]:
                rows.append([("  · ", "muted"), (a.get("statement", "")[:200], "dim")])
        if not rows:
            rows = [[("No differences from Canon.", "muted")]]
        return rows

    def _canon_id(self):
        byname = {b["name"]: b for b in self.branches}
        return byname.get(self.canon_branch, {}).get("branch_id", self.canon_branch)

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
            "Proposals": self.proposals_rows,
        }[tab]()
