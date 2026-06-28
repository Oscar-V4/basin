# How does Basin compare to Git?

Basin borrows Git's *workflow vocabulary* — branch, commit, merge, tag — but the thing under version control is different. Git versions **lines of text**. Basin versions **context atoms**: the decisions, constraints, and rejected paths an AI assistant should carry forward.

| Git | Basin | Note |
|---|---|---|
| File / line | Context atom | The tracked entity is a semantic unit, not a text line. |
| Commit | Checkpoint | A captured context transition, not every keystroke or raw chat turn. |
| Branch | Branch / workroom | A line of exploration; a fork inherits the Canon then diverges. |
| `main` | Canon | The settled source of truth. |
| Merge | Settle / reconcile | Reconcile beliefs, not text — what a branch adds to or supersedes in the Canon. |
| Tag / release | Release | An immutable snapshot of the Canon. |
| Diff | Changes | What materially changed — cosmetic rewordings are filtered out. |
| Blame | Provenance | Every atom records its source, authority tier, and what it supersedes. |
| `.git/` | `.basin/` | Append-only files, committed to the same repo. |
| Checkout | Context Pack | The inheritable, budgeted artifact a new thread loads to continue. |

## The key difference

A Git diff over a transcript is almost useless: the meaningful change is rarely a contiguous block of text, and most of the transcript is noise. Basin treats Claude Code and Codex transcripts as **raw input**, distills them into atoms with status and provenance, and versions *those*. So you can ask "what has been decided, what's still open, and what did we explicitly reject?" — and get an answer without rereading the conversation.

It is deliberately **not** "Git for chat logs." Git is the surface metaphor that makes the workflow legible; underneath, Basin is semantic event sourcing.

## What Basin is not (yet)

- Not a replacement for Git — it lives *alongside* your code repo and versions context, not source.
- Not multi-user/real-time sync (single-project, local-first today).
- Not a fully automatic curator — settling context into the Canon is a deliberate, human-in-the-loop act.
