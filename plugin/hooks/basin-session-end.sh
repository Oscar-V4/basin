#!/usr/bin/env bash
# Basin SessionEnd hook — checkpoint, stage atoms, reindex, and reproject dot-files.
python3 -m basin hook --mode session_end >/dev/null 2>&1 || true
exit 0
