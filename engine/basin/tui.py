"""Basin TUI — gh-orbit-style cockpit, our way. stdlib `curses` only.

A read-client over the engine: tabs Threads / Branches / Changes / Canon / Proposals,
lane graph, keyboard navigation, inline adopt/discard/merge actions. The rendering data
comes from tui_model (pure, testable); this file is the thin curses blitter + input loop.
"""
from __future__ import annotations

import sys

from .tui_model import Model, TABS

# style -> (color name, attrs). Resolved to curses pairs at runtime.
_STYLE_COLOR = {
    "normal": ("default", 0), "muted": ("default", "dim"), "dim": ("default", "dim"),
    "accent": ("cyan", 0), "green": ("green", 0), "yellow": ("yellow", 0), "red": ("red", 0),
    "header": ("cyan", "bold"),
    "laneA": ("cyan", 0), "laneB": ("magenta", 0), "laneC": ("blue", 0),
    "laneD": ("green", 0), "laneE": ("yellow", 0),
}
_SELECTABLE = {"Threads", "Branches", "Changes", "Proposals"}
_HINTS = {
    "Threads": "tab: switch · ↑↓/jk: move · r: refresh · q: quit",
    "Branches": "tab: switch · ↑↓: move · enter: focus branch · r: refresh · q: quit",
    "Changes": "tab: switch · ↑↓: move · a: adopt · d: discard · r: refresh · q: quit",
    "Canon": "tab: switch · r: refresh · q: quit",
    "Proposals": "tab: switch · ↑↓: move · m: merge · F: force-merge conflict · r: refresh · q: quit",
    "Map": "tab: switch · r: refresh · q: quit",
}


def _selftest(root: str) -> int:
    m = Model(root)
    print(f"== selftest {root} ==")
    for tab in TABS:
        rows = m.render(tab)
        print(f"\n--- {tab} ({len(rows)} rows) ---")
        for r in rows[:12]:
            print("".join(seg[0] for seg in r))
    print("\nstatus:", "".join(s[0] for s in m.status_line()))
    return 0


def run(stdscr, root: str):
    import curses

    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)
    curses.use_default_colors()
    names = {"default": -1, "cyan": curses.COLOR_CYAN, "green": curses.COLOR_GREEN,
             "yellow": curses.COLOR_YELLOW, "red": curses.COLOR_RED, "magenta": curses.COLOR_MAGENTA,
             "blue": curses.COLOR_BLUE, "white": curses.COLOR_WHITE}
    pairs = {}
    idx = 1
    for color, cid in names.items():
        try:
            curses.init_pair(idx, cid, -1)
        except curses.error:
            pass
        pairs[color] = idx
        idx += 1

    def attr_of(style):
        color, extra = _STYLE_COLOR.get(style, ("default", 0))
        a = curses.color_pair(pairs.get(color, 0))
        if extra == "dim":
            a |= curses.A_DIM
        elif extra == "bold":
            a |= curses.A_BOLD
        return a

    model = Model(root)
    tab_idx = 0
    sel = {t: 0 for t in TABS}
    scroll = {t: 0 for t in TABS}

    def draw():
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        # header tabs
        x = 1
        for i, t in enumerate(TABS):
            a = curses.A_BOLD | curses.color_pair(pairs.get("cyan", 0)) if i == tab_idx else curses.A_DIM
            label = f" {t} "
            try:
                stdscr.addstr(0, x, label, a)
            except Exception:
                pass
            x += len(label) + 1
        hint = "tab / shift+tab"
        try:
            stdscr.addstr(0, max(1, w - len(hint) - 1), hint, curses.A_DIM)
            stdscr.hline(1, 0, curses.ACS_HLINE, w)
        except Exception:
            pass

        tab = TABS[tab_idx]
        rows = model.render(tab)
        body_h = h - 4
        # clamp selection + scroll
        max_scroll = max(0, len(rows) - body_h)
        if tab in _SELECTABLE:
            sel[tab] = max(0, min(sel[tab], len(rows) - 1))
            if sel[tab] < scroll[tab]:
                scroll[tab] = sel[tab]
            elif sel[tab] >= scroll[tab] + body_h:
                scroll[tab] = sel[tab] - body_h + 1
        else:
            scroll[tab] = max(0, min(scroll[tab], max_scroll))  # scroll-driven (Canon, Map)
        view = rows[scroll[tab]: scroll[tab] + body_h]
        for r, segs in enumerate(view):
            y = 2 + r
            is_sel = tab in _SELECTABLE and (scroll[tab] + r) == sel[tab]
            x = 1
            if is_sel:
                try:
                    stdscr.addstr(y, 0, " " * (w - 1), curses.A_REVERSE)
                except Exception:
                    pass
            for text, style in segs:
                if x >= w - 1:
                    break
                a = attr_of(style)
                if is_sel:
                    a |= curses.A_REVERSE
                try:
                    stdscr.addstr(y, x, text[: w - 1 - x], a)
                except Exception:
                    pass
                x += len(text)
        # footer
        try:
            stdscr.hline(h - 2, 0, curses.ACS_HLINE, w)
            sx = 1
            for text, style in model.status_line():
                stdscr.addstr(h - 1, sx, text, attr_of(style))
                sx += len(text)
            stdscr.addstr(h - 1, max(1, w - len(_HINTS[tab]) - 1), _HINTS[tab][: w - 2], curses.A_DIM)
        except Exception:
            pass
        stdscr.refresh()

    while True:
        draw()
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            break
        tab = TABS[tab_idx]
        if ch in (ord("q"), 27):
            break
        elif ch == ord("\t"):
            tab_idx = (tab_idx + 1) % len(TABS)
        elif ch == curses.KEY_BTAB:
            tab_idx = (tab_idx - 1) % len(TABS)
        elif ch in (curses.KEY_DOWN, ord("j")):
            if tab in _SELECTABLE:
                sel[tab] += 1
            else:
                scroll[tab] += 1  # scroll-driven tabs (Canon, Map)
        elif ch in (curses.KEY_UP, ord("k")):
            if tab in _SELECTABLE:
                sel[tab] = max(0, sel[tab] - 1)
            else:
                scroll[tab] = max(0, scroll[tab] - 1)
        elif ch == ord("r"):
            model.flash = None
            model.reload()
        elif ch in (curses.KEY_ENTER, 10, 13) and tab == "Branches":
            model.set_branch_by_row(sel[tab])
        elif ch == ord("a") and tab == "Changes":
            model.adopt(sel[tab])
        elif ch == ord("d") and tab == "Changes":
            model.discard(sel[tab])
        elif ch == ord("m") and tab == "Proposals":
            model.merge_row(sel[tab])
        elif ch == ord("F") and tab == "Proposals":
            model.merge_row(sel[tab], force=True)


def main(root: str = ".", argv=None) -> int:
    argv = argv if argv is not None else []
    if "--selftest" in argv:
        return _selftest(root)
    try:
        import curses
    except ImportError:
        print("curses unavailable; use --selftest", file=sys.stderr)
        return 1
    curses.wrapper(run, root)
    return 0
