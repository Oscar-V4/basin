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

### Fixed — r4 review (Codex audit of the r3 fixes) + cold-continuity
- **Merge: same-revision promotion.** A fork that inherited a canon *candidate* atom at the same
  revision and then promoted it was silently dropped (`merge_atom` returned a noop without
  reconciling lifecycle). It now promotes the target ref.
- **Merge: cosmetic revisions no longer break the supersedes chain.** A COSMETIC re-wording used
  to cut the lineage pointer, making a later real edit look *diverged* and blocking a legitimate
  fast-forward. The lineage pointer is now always set (the supersede *edge* stays STRUCTURAL-only).
- **Merge: `--force` now also overrides resurrection conflicts**, and a genuine same-fork
  contradiction (do X *and* X-rejected with high word overlap) is flagged via content overlap
  rather than a branch heuristic that missed it.
- **Pack: the per-type floor is limited to must-show types** (decisions/constraints/principles/
  rejected_paths/open_questions/risks) so low-value artifacts can't lock budget away from
  decisions; `_best_subject`/`_continuity_test` tolerate non-string subject keys.
- **Extraction: stop over-rejecting and stop self-ingesting.** Bare-number prose, blockquoted
  decisions, and dotted config identifiers are handled correctly (the cat -n guard now keys on the
  real TAB/wide-aligned format, judged on the raw sentence before whitespace normalisation). New
  `_is_self_output` guard stops Basin's own status rows, pack/CANON YAML, engine source, atom-id
  refs, and the generic continuity questions from being re-ingested as atoms (a self-referential
  loop surfaced by the cold-continuity test).

### Fixed — fork/settle merge semantics (the hero use case)
- **No more lost updates.** `merge_atom` is no longer a blind last-writer-wins overwrite. If
  the canon advanced independently since a branch forked (divergence) or the canon owner put
  an atom in a terminal lifecycle (rejected/pruned), settling surfaces a **conflict** instead
  of silently winning — so the settled canon is deterministic regardless of settle order and
  never reverses an explicit rejection. `basin settle --force` overrides intentionally.
- **Provenance recorded.** A clean overturn records a `supersedes` edge; a conflict records a
  `contradicts` edge.
- **Cross-type contradictions flagged.** When an incoming `rejected_path` contradicts a
  *surviving* canon decision/principle/constraint on the same subject (do X *and* X-rejected),
  settle flags it (non-blocking) so the pack compiler / a human can reconcile. A fork that
  rejected X *and* chose Y is recognised as complementary and not flagged.
- `basin reconcile` / `basin settle` now report conflicts and contradictions.

### Added
- **TUI surfaces merge conflicts.** The Proposals tab now shows a **⚠ Conflicts** section
  (divergence / would-resurrect-rejected), `m` warns instead of silently no-merging a conflict,
  and `F` force-merges. The merge-preview (`diff_branch_vs_canon`) now distinguishes a clean
  fast-forward from a divergence, so the preview matches what `settle` will do.
- `basin doctor` — integrity check (orphan refs, headless branches, corrupt JSONL,
  index staleness, dangling do-not-load).
- `basin settle --branch X` — reconcile a branch and settle it into canon from the CLI.
- `basin ignore --atom A` — attention-budget control (`exclude` / `retrieve_only` / `allow`).
- TUI **Map** tab (context clusters).
- Test suites grew to **154 checks** (`engine/tests/`), including `test_dogfood.py` and
  `test_merge.py` (fork/settle conflict semantics).

## [0.1.0] — initial alpha
- Engine (Python 3, stdlib-only): files-as-truth append-only JSONL in `.basin/`, a
  rebuildable SQLite index, and committed Markdown/YAML projections.
- Context atoms (11 types), branches, forks, checkpoints, and Context Packs with a token
  budget and continuity test.
- Claude Code plugin (hooks + slash commands) and a curses TUI (`basin tui`).
