---
description: Promote staged atoms on a chosen Basin branch, defaulting to Canon/main.
allowed-tools: ["Bash"]
---

Run from the current project root (the directory containing `.basin/`):

- If the user passed CLI flags such as `--branch spike -m "lock decision"`, run
  `basin save $ARGUMENTS`.
- Otherwise run `basin save -m "$ARGUMENTS"`; this saves the default Canon/main branch,
  not an auto-detected fork branch.

For an auto-detected fork, ask for or use the branch name and run
`basin save --branch <branch> -m "<message>"`.

Then run `basin status` and report, in English:
- how many atoms were promoted,
- the current decisions and constraints on the saved branch,
- whether a non-Canon branch still needs `basin settle --branch <branch>` to enter Canon,
- any open questions still unresolved.

If `.basin/` does not exist, tell the user to run `basin setup` first.
