---
description: Explicitly start a new branch (workroom) that forks from the current Canon head.
allowed-tools: ["Bash"]
---

Run `basin fork --name "$ARGUMENTS"` in the current project root to create a new branch
that bases off the current Canon head checkpoint.

Report the new branch id and explain that exploration on this branch stays isolated until
the user reviews it with `basin reconcile --branch <branch>` and settles clean changes with
`basin settle --branch <branch>`. Use `/basin-save --branch <branch> -m "<message>"`
only to promote staged atoms on that branch before settling.
