---
description: Show the current Canon, staged changes, and open questions for this project.
allowed-tools: ["Bash"]
---

Run `basin status` in the current project root and summarize, in English:
- active atoms grouped by type (decisions, constraints, open questions, rejected paths),
- staged candidates not yet settled (with their change kind),
- anything that looks like drift or a conflict worth the user's attention.
