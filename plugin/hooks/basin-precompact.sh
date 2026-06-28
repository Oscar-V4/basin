#!/usr/bin/env bash
# Basin PreCompact hook — capture context just before the window compresses.
# fail-soft even if the package is not importable: never block the session.
BASIN_ENV="${BASIN_CLAUDE_ENV:-$HOME/.claude/hooks/basin-env.sh}"
[ -f "$BASIN_ENV" ] && . "$BASIN_ENV"
python3 -m basin hook --mode precompact >/dev/null 2>&1 || true
exit 0
