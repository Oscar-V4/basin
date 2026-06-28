#!/usr/bin/env bash
# Basin Codex Stop hook — capture at turn end without finalizing the session.
BASIN_ENV="${BASIN_CODEX_ENV:-$HOME/.codex/hooks/basin-env.sh}"
[ -f "$BASIN_ENV" ] && . "$BASIN_ENV"
python3 -m basin codex-hook --mode turn_end >/dev/null 2>&1 || true
exit 0
