#!/usr/bin/env bash
# Basin Codex SessionStart hook — register the Codex session and ingest its transcript prefix.
python3 -m basin codex-hook --mode session_start >/dev/null 2>&1 || true
exit 0
