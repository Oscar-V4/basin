#!/usr/bin/env bash
# Basin Codex PreCompact hook — capture context just before compaction.
BASIN_ENV="${BASIN_CODEX_ENV:-$HOME/.codex/hooks/basin-env.sh}"
[ -f "$BASIN_ENV" ] && . "$BASIN_ENV"
python3 -m basin codex-hook --mode precompact >/dev/null 2>&1 || true
exit 0
