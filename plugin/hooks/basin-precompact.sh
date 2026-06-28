#!/usr/bin/env bash
# Basin PreCompact hook — capture context just before the window compresses.
# fail-soft even if the package is not importable: never block the session.
python3 -m basin hook --mode precompact >/dev/null 2>&1 || true
exit 0
