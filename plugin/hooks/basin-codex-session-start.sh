#!/usr/bin/env bash
# Basin Codex SessionStart hook — register the Codex session and ingest its transcript prefix.
BASIN_ENV="${BASIN_CODEX_ENV:-$HOME/.codex/hooks/basin-env.sh}"
[ -f "$BASIN_ENV" ] && . "$BASIN_ENV"
python3 -m basin codex-hook --mode session_start >/dev/null 2>&1 || true
exit 0
