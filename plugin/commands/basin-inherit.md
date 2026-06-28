---
description: Inherit a branch's Context Pack into this thread and take the continuity test.
allowed-tools: ["Bash"]
---

Run `basin inherit --branch "${ARGUMENTS:-main}"` in the current project root.

Then, acting as the rookie thread that just inherited this Context Pack:
1. Read the pack that was printed.
2. Answer every question under `continuity_test` using only the pack — do not guess.
3. If you cannot answer items 2, 3, or 5, say so explicitly: the inheritance is incomplete
   and the user should expand the pack before continuing.
