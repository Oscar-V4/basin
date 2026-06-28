"""Basin TUI — gh-orbit-style cockpit, our way. stdlib `curses` only.

A read-client over the engine: tabs Threads / Branches / Changes / Canon / Proposals,
lane graph, keyboard navigation, inline adopt/discard/merge actions. The rendering data
comes from tui_model (pure, testable); this file is the thin curses blitter + input loop.
"""
from __future__ import annotations

import sys

from .tui_model import Model, TABS, display_width, fit_display_width, row_text, split_row

# style -> (fg, bg, attrs). Resolved to curses pairs at runtime.
_STYLE_COLOR = {
    "normal": ("default", "default", 0), "muted": ("default", "default", "dim"),
    "dim": ("default", "default", "dim"),
    "accent": ("cyan", "default", 0), "green": ("green", "default", 0),
    "yellow": ("yellow", "default", 0), "red": ("red", "default", 0),
    "header": ("cyan", "default", "bold"),
    "laneA": ("cyan", "default", 0), "laneB": ("magenta", "default", 0),
    "laneC": ("blue", "default", 0), "laneD": ("green", "default", 0),
    "laneE": ("yellow", "default", 0),
    "chip_a": ("black", "cyan", "bold"), "chip_b": ("white", "magenta", "bold"),
    "chip_c": ("white", "blue", "bold"), "chip_d": ("black", "green", "bold"),
    "chip_e": ("black", "yellow", "bold"),
    "badge_decision": ("black", "green", "bold"),
    "badge_constraint": ("black", "cyan", "bold"),
    "badge_rejected_path": ("white", "red", "bold"),
    "badge_open_question": ("black", "yellow", "bold"),
    "badge_risk": ("white", "magenta", "bold"),
    "badge_principle": ("white", "blue", "bold"),
    "badge_preference": ("black", "cyan", "bold"),
    "badge_task": ("black", "yellow", "bold"),
    "badge_fact": ("default", "default", "bold"),
    "badge_conflict": ("white", "red", "bold"),
}
_SELECTABLE = {"Threads", "Branches", "Changes", "Canon", "Proposals"}
_HINTS = {
    "Threads": "tab: switch · ↑↓/jk: move · enter: details · r: refresh · q: quit",
    "Branches": "tab: switch · ↑↓: move · enter: focus branch · r: refresh · q: quit",
    "Changes": "tab: switch · ↑↓: move · enter: details · a: adopt · d: discard · r: refresh · q: quit",
    "Canon": "tab: switch · ↑↓: move · enter: details · r: refresh · q: quit",
    "Proposals": "tab: switch · ↑↓: move · enter: details · m: merge · F: force-merge conflict · r: refresh · q: quit",
    "Map": "tab: switch · r: refresh · q: quit",
}


def _layout_segments(x0, max_x, segs):
    """Return (x, text, style) draws for a styled row within terminal columns."""
    left, right = split_row(segs)
    right_len = sum(display_width(text) for text, _style in right)
    right_x = max(x0, max_x - right_len) if right else max_x
    draws = []

    x = x0
    left_limit = min(max_x, right_x - 1) if right else max_x
    for text, style in left:
        if x >= left_limit:
            break
        clipped = fit_display_width(text, left_limit - x)
        if clipped:
            draws.append((x, clipped, style))
        if display_width(clipped) < display_width(text):
            break
        x += display_width(clipped)

    if right:
        x = right_x
        for text, style in right:
            if x >= max_x:
                break
            clipped = fit_display_width(text, max_x - x)
            if clipped:
                draws.append((x, clipped, style))
            if display_width(clipped) < display_width(text):
                break
            x += display_width(clipped)
    return draws


def _selftest(root: str) -> int:
    m = Model(root)
    print(f"== selftest {root} ==")
    for tab in TABS:
        rows = m.render(tab)
        print(f"\n--- {tab} ({len(rows)} rows) ---")
        for r in rows[:12]:
            print(row_text(r))
    print("\nstatus:", row_text(m.status_line()))
    return 0


def run(stdscr, root: str):
    import curses

    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)
    try:
        curses.use_default_colors()
    except Exception:
        pass
    names = {"default": -1, "cyan": curses.COLOR_CYAN, "green": curses.COLOR_GREEN,
             "yellow": curses.COLOR_YELLOW, "red": curses.COLOR_RED, "magenta": curses.COLOR_MAGENTA,
             "blue": curses.COLOR_BLUE, "white": curses.COLOR_WHITE, "black": curses.COLOR_BLACK}
    pairs = {}
    idx = 1
    have_color = curses.has_colors()
    if have_color:
        needed = {(fg, bg) for fg, bg, _extra in _STYLE_COLOR.values()}
        needed.add(("cyan", "default"))
        for fg, bg in sorted(needed):
            try:
                curses.init_pair(idx, names.get(fg, -1), names.get(bg, -1))
                pairs[(fg, bg)] = idx
                idx += 1
            except curses.error:
                pairs[(fg, bg)] = 0

    def attr_of(style):
        fg, bg, extra = _STYLE_COLOR.get(style, ("default", "default", 0))
        a = curses.color_pair(pairs.get((fg, bg), 0)) if have_color else 0
        if extra == "dim":
            a |= curses.A_DIM
        elif extra == "bold":
            a |= curses.A_BOLD
        return a

    model = Model(root)
    tab_idx = 0
    sel = {t: 0 for t in TABS}
    scroll = {t: 0 for t in TABS}
    detail = None

    def draw_segments(y, x0, max_x, segs, is_sel=False):
        for x, text, style in _layout_segments(x0, max_x, segs):
            a = attr_of(style)
            if is_sel:
                a |= curses.A_REVERSE
            try:
                stdscr.addstr(y, x, text, a)
            except Exception:
                pass

    def draw():
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        # header tabs
        x = 1
        for i, t in enumerate(TABS):
            a = curses.A_BOLD | attr_of("accent") if i == tab_idx else curses.A_DIM
            label = f" {t} "
            try:
                stdscr.addstr(0, x, label, a)
            except Exception:
                pass
            x += display_width(label) + 1
        hint = "tab / shift+tab"
        try:
            stdscr.addstr(0, max(1, w - display_width(hint) - 1), hint, curses.A_DIM)
            stdscr.hline(1, 0, curses.ACS_HLINE, w)
        except Exception:
            pass

        tab = TABS[tab_idx]
        rows = model.render(tab)
        body_h = max(1, h - 4)
        # clamp selection + scroll
        max_scroll = max(0, len(rows) - body_h)
        if tab in _SELECTABLE:
            sel[tab] = max(0, min(sel[tab], len(rows) - 1))
            if sel[tab] < scroll[tab]:
                scroll[tab] = sel[tab]
            elif sel[tab] >= scroll[tab] + body_h:
                scroll[tab] = sel[tab] - body_h + 1
        else:
            scroll[tab] = max(0, min(scroll[tab], max_scroll))  # scroll-driven tabs
        view = rows[scroll[tab]: scroll[tab] + body_h]
        for r, segs in enumerate(view):
            y = 2 + r
            is_sel = tab in _SELECTABLE and (scroll[tab] + r) == sel[tab]
            if is_sel:
                try:
                    stdscr.addstr(y, 0, " " * (w - 1), curses.A_REVERSE)
                except Exception:
                    pass
            draw_segments(y, 1, w - 1, segs, is_sel=is_sel)
        # footer
        try:
            stdscr.hline(h - 2, 0, curses.ACS_HLINE, w)
            sx = 1
            for text, style in model.status_line():
                stdscr.addstr(h - 1, sx, text, attr_of(style))
                sx += display_width(text)
            hint = fit_display_width(_HINTS[tab], w - 2)
            stdscr.addstr(h - 1, max(1, w - display_width(hint) - 1), hint, curses.A_DIM)
        except Exception:
            pass
        if detail:
            dtab, didx = detail
            drows = model.detail_rows(dtab, didx)
            ph = max(4, min(max(4, h - 4), max(8, len(drows) + 2)))
            pw = max(20, min(max(20, w - 4), max(52, min(104, w - 4))))
            py = max(2, (h - ph) // 2)
            px = max(1, (w - pw) // 2)
            try:
                for yy in range(py, py + ph):
                    stdscr.addstr(yy, px, " " * pw, attr_of("normal"))
                stdscr.addch(py, px, curses.ACS_ULCORNER)
                stdscr.hline(py, px + 1, curses.ACS_HLINE, pw - 2)
                stdscr.addch(py, px + pw - 1, curses.ACS_URCORNER)
                for yy in range(py + 1, py + ph - 1):
                    stdscr.addch(yy, px, curses.ACS_VLINE)
                    stdscr.addch(yy, px + pw - 1, curses.ACS_VLINE)
                stdscr.addch(py + ph - 1, px, curses.ACS_LLCORNER)
                stdscr.hline(py + ph - 1, px + 1, curses.ACS_HLINE, pw - 2)
                stdscr.addch(py + ph - 1, px + pw - 1, curses.ACS_LRCORNER)
                stdscr.addstr(py, px + 2, " details ", attr_of("header"))
            except Exception:
                pass
            for i, segs in enumerate(drows[: ph - 2]):
                draw_segments(py + 1 + i, px + 2, px + pw - 2, segs)
        stdscr.refresh()

    while True:
        draw()
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            break
        tab = TABS[tab_idx]
        if detail:
            if ch in (ord("q"), 27):
                detail = None
            continue
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
                scroll[tab] += 1
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
        elif ch in (curses.KEY_ENTER, 10, 13) and tab in ("Threads", "Changes", "Canon", "Proposals"):
            detail = (tab, sel[tab])
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
