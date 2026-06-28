---
description: Settle the current session's agreed context into the Canon (promote staged atoms, checkpoint, reproject dot-files).
allowed-tools: ["Bash"]
---

Run `basin save -m "$ARGUMENTS"` in the current project root (the directory containing `.basin/`).

Then run `basin status` and report, in English:
- how many atoms were promoted,
- the current decisions and constraints now in the Canon,
- any open questions still unresolved.

If `.basin/` does not exist, tell the user to run `basin setup` first.
