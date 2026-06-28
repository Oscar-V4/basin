# Contributing to Basin

Basin is alpha, research-grade software under active development. Issues, ideas, and PRs are welcome — but expect interfaces to change.

## Development setup

Requires Python 3.10+. The engine has **no runtime dependencies** (standard library only).

```bash
git clone https://github.com/Oscar-V4/basin.git
cd basin/engine
export PYTHONPATH="$PWD"
python3 -m basin --help
```

## Running the tests

```bash
cd engine
for t in tests/test_*.py; do python3 "$t"; done
```

All test files print `ALL PASS` on success. There are 72 checks across:

- `test_e2e.py` — end-to-end: setup → ingest → extract → fingerprint → save → pack → projections.
- `test_branch.py` — two-branch isolation, fork inheritance, hook idempotency.
- `test_extra.py` — LLM validation, graph clustering with stable ids, fork detection, TUI rendering.
- `test_review.py` — regression coverage for reviewed correctness fixes.

## Principles to preserve

- **Files are the source of truth.** Only `.basin/.index/basin.db` may be deleted and rebuilt; everything in `.basin/` is append-only and must survive a `reindex`.
- **The hook is fail-soft.** It must never block a session: always exit 0, no stdout, no network, no subprocess on the hot path.
- **The deterministic path needs no model.** LLM refinement is detached and opt-in; the core must work without it.
- **stdlib only** in the engine hot path.

## Style

Match the surrounding code: small modules, English identifiers and user-facing strings, type hints, no new dependencies without discussion.
