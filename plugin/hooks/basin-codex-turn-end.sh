#!/usr/bin/env bash
# Basin Codex Stop hook — capture at turn end without finalizing the session.
python3 -m basin codex-hook --mode turn_end >/dev/null 2>&1 || true
exit 0
