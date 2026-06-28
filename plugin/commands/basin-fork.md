---
description: Explicitly start a new branch (workroom) that forks from the current Canon head.
allowed-tools: ["Bash"]
---

Run `basin fork --name "$ARGUMENTS"` in the current project root to create a new branch
that bases off the current Canon head checkpoint.

Report the new branch id and explain that exploration on this branch stays isolated until
`/basin-save` settles it back into the Canon.
