# Changelog

All notable changes to Basin are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this is alpha software and the
data formats may still change.

## [Unreleased]

### Fixed — post-release hardening (7-dimension adversarial audit)
- **Security:** `safe_id()` sanitizes every external id (`session_id`/`branch_id`)
  at the path boundary — a hook-stdin value like `../../../X` can no longer write
  outside `.basin/`.
- **Data integrity:** checkpoint ids now include the event range, summary, and parent,
  and `save` chains off the current head, so two saves of the same message can no longer
  collide and silently drop the second commit.
- **Concurrency:** `atom_ref` read-modify-write is serialized with an `fcntl` lock
  (`Store.lock` + `update_atom_refs`); `ingest` is locked per session; `reindex` builds a
  temp DB and swaps it in atomically.
- Cross-branch revisions no longer misattribute their `branch_id`.
- `COSMETIC` re-wording no longer hides a still-staged candidate from Changes.
- The Proposals `m` (merge) action is row-aligned with the displayed atom list.
- Context packs and `CANON.md` now render all 11 atom types (+ an "Other" catch-all);
  the continuity test is derived from the pack's own atoms.
- `install.sh` backs up and refuses to overwrite an unparseable `settings.json`.

### Fixed — live dogfood (ran Basin on its own 14.8 MB transcript)
- **Extraction precision:** the deterministic hook-path extractor no longer mints atoms
  from pasted attachments, prompt echoes, UI metadata, or `cat -n` file dumps folded into
  user turns. It now stages only conversational prose and rejects file-dump / code / markup
  lines. Raw events are still fully logged, so recall is preserved.
- **Pack ranking:** `compile_context_pack` now ranks atoms by
  `(authority, confidence, type-priority, recency)` before filling the always-load budget,
  instead of arbitrary DB order. Previously a flood of low-value constraints could starve
  every decision out of the pack (`current_decisions` shipped empty).
- On the dogfood transcript: staged atoms 768 → 406, pack `current_decisions` 0 → 40,
  ground-truth decision recall in the pack 0/8 → 7/8.

### Added
- `basin doctor` — integrity check (orphan refs, headless branches, corrupt JSONL,
  index staleness, dangling do-not-load).
- `basin settle --branch X` — reconcile a branch and settle it into canon from the CLI.
- `basin ignore --atom A` — attention-budget control (`exclude` / `retrieve_only` / `allow`).
- TUI **Map** tab (context clusters).
- Test suites grew to **116 checks** (`engine/tests/`).

## [0.1.0] — initial alpha
- Engine (Python 3, stdlib-only): files-as-truth append-only JSONL in `.basin/`, a
  rebuildable SQLite index, and committed Markdown/YAML projections.
- Context atoms (11 types), branches, forks, checkpoints, and Context Packs with a token
  budget and continuity test.
- Claude Code plugin (hooks + slash commands) and a curses TUI (`basin tui`).
