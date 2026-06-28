#!/usr/bin/env bash
# Basin SessionStart hook — register the session and ingest its transcript prefix.
python3 -m basin hook --mode session_start >/dev/null 2>&1 || true
exit 0
