# Concepts

Basin models the *context state* of an AI collaboration — not the chat log, but the distilled, governed answer to: **what should the assistant believe, avoid, and use as a basis for the next action?**

## Context atom

The unit of context. Every atom has a type:

`fact` · `decision` · `assumption` · `constraint` · `task` · `open_question` · `artifact` · `preference` · `risk` · `principle` · `rejected_path`

Each atom carries more than a statement:

- **Status** — its lifecycle (`candidate` → `active` → `superseded`/`pruned`/`released`) and visibility (`staged`, `tracked`, `released`, `archived`).
- **Provenance** — the source event it came from, what it supersedes, what it conflicts with.
- **Authority tier** — who asserted it, from `user_explicit` down to `model_inferred` and `external_untrusted`. Higher authority wins when budgeting a Context Pack.
- **Confidence** — a 0–1 score.
- **Fingerprints** — structural / semantic / cosmetic hashes that drive change classification, so a reworded sentence (`COSMETIC`) never shows up as a real change, while a changed meaning (`STRUCTURAL`) does.

Atom identity is content-addressed on `(project, type, subject)`, so the same idea accrues **revisions** over time — the substrate for supersede and conflict detection. The revision log is append-only: nothing is ever edited in place.

## Canon

The settled source of truth for a project, projected to a human-readable `canon/CANON.md`. Branches explore; the Canon is what has settled. Promoting a branch's atoms into the Canon is a deliberate act (`basin save`, then merge proposals), never automatic.

## Branch and fork

A branch is a workroom for exploring an approach. A **fork** inherits the Canon's atoms at the fork point, then diverges — the hero use case: you fork a thread to try something different, and Basin keeps both lines of reasoning distinct.

Fork detection (opt-in) finds the parent by the **longest common prefix** of the two sessions' event hashes — the same idea as Git's merge-base, reconstructed from the transcript. The reliable path today is the explicit `basin fork`.

## Checkpoint

A commit — a captured context transition, not the raw transcript itself. Kinds: `manual`, `pre_compact`, `turn_end`, `session_end`, `semantic_commit`, `merge`, `release_build`, `fork_point`. The `pre_compact` checkpoint is special: it fires the instant before a context window compresses, which is exactly when context is usually lost. `turn_end` is the Codex-friendly capture point for hosts that fire after each turn; repeated hooks with no new transcript events do not create checkpoints.

## Context Pack

The deployment unit of AI collaboration — like a package or container, but for context. `basin pack` compiles the active atoms under a token budget (brief 2k / standard 4k / full 8k for always-loaded context, the rest deferred to a retrieval manifest), applies `do_not_load`, and emits a YAML artifact a new thread loads as its first message.

Every pack ends with a **continuity test**: a handful of questions the inheriting thread must answer *from the pack alone*. If it can't, the inheritance was incomplete — better to find out before work starts than three sessions later.

## do_not_load

Managing context is as much about what *not* to load as what to keep. `do_not_load` is an attention-budget action log (not a delete): it excludes pruned or noisy atoms from the always-loaded set, or routes them to retrieve-on-demand.

## Files are the source of truth

Everything lives in `.basin/` as plain files committed to your repo:

- **Truth** (append-only JSONL): `events/`, `atoms/`, `edges.jsonl`, `checkpoints.jsonl`, `sessions.jsonl`, `do_not_load.jsonl`.
- **Projections** (regenerated, human-readable): `canon/CANON.md`, `branches/*.md`, `packs/*.yaml`, `ledger.md`.
- **Index** (rebuildable, gitignored): `.index/basin.db` — a SQLite read-model you can delete and regenerate with `basin reindex`.

Because the truth is files, a pull request shows context changes as a readable diff, and your context travels with your code.
