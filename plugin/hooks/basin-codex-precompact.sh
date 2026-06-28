#!/usr/bin/env bash
# Basin Codex PreCompact hook — capture context just before compaction.
python3 -m basin codex-hook --mode precompact >/dev/null 2>&1 || true
exit 0
