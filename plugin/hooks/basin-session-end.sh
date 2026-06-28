#!/usr/bin/env bash
# Basin SessionEnd hook — checkpoint, stage atoms, reindex, and reproject dot-files.
BASIN_ENV="${BASIN_CLAUDE_ENV:-$HOME/.claude/hooks/basin-env.sh}"
[ -f "$BASIN_ENV" ] && . "$BASIN_ENV"
python3 -m basin hook --mode session_end >/dev/null 2>&1 || true
exit 0
