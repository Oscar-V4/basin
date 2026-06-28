<p align="center">
  <img src="assets/logo.png" alt="Basin" height="76">
</p>

<h3 align="center">Version control for the context of AI collaboration</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-black" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-black?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/dependencies-stdlib%20only-black" alt="stdlib only">
  <img src="https://img.shields.io/badge/tests-172%20passing-brightgreen" alt="172 tests passing">
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="alpha">
  <img src="https://img.shields.io/badge/built%20for-Claude%20Code%20%2B%20Codex-black" alt="built for Claude Code and Codex">
</p>

Basin tracks, branches, and settles the **context** of a long AI collaboration — the decisions, constraints, rejected paths, and open questions an assistant should hold on to across sessions, compactions, and forked threads.

Turn it on and Basin quietly does the context engineering for you: it captures each session, extracts durable **context atoms**, settles the agreed ones into a versioned **Canon**, and compiles a **Context Pack** so a new thread inherits everything that matters. Everything is stored as plain, append-only files committed to your repo — your context travels with your code and diffs in a pull request.

- 📌 **Branch, fork, and settle context — not just code.** Drafts and forked threads explore freely; what's agreed settles into the Canon.
- 🧬 **Track semantic changes.** Every decision/constraint/assumption is a first-class atom with status, provenance, confidence, and an authority tier.
- 🔀 **Review and merge.** See exactly what a branch would add to or supersede in the Canon, then settle it.
- 🧠 **Compile Context Packs.** A budgeted, inheritable artifact a new thread loads to continue where the last left off — with a built-in continuity test.
- 🪂 **Survive compaction.** A pre-compact hook captures context the moment before the window compresses — the point where it's usually lost.
- 📁 **Files are the source of truth.** Append-only JSONL + human-readable Markdown in `.basin/`; SQLite is a rebuildable index you can delete anytime.

> **Status: alpha, under active development.** The engine, the Claude Code/Codex capture integrations, and the terminal UI work today and are covered by 172 passing tests. The detached LLM refiner and automatic fork detection are opt-in. See [Status](#status).

---

## Why Basin?

### Context engineering should not be manual.

In a long project with an AI assistant, the useful state is not the chat log — it's *what the assistant should believe, avoid, and use as a basis for the next action*. That state keeps getting lost: context windows compact, threads fork to try a new approach, and the same decisions get re-litigated three sessions later.

Today people manage this by hand — re-pasting summaries, re-explaining constraints, re-deciding things that were already settled. Basin treats that state as a first-class, versioned object, the way Git treats source code.

It is **not** "Git for chat logs." The transcript is raw input; the tracked thing is the distilled, governed context state. Git is the surface metaphor (branch, checkpoint, merge, release); underneath it is semantic event sourcing.

### The fork is the hero use case.

You're deep in a thread, you fork it to try a different approach, and now two lines of reasoning are diverging. Basin captures both branches, shows you the difference as a set of context changes, and lets you settle the winning ideas back into the Canon — without losing the road not taken.

---

## How it works

```
 Claude Code / Codex session ──hooks──▶  .basin/ (committed to your repo)
                                  ├─ events/*.jsonl     ← truth: raw transcript events
                                  ├─ atoms/*.jsonl      ← truth: context atom revisions
                                  ├─ checkpoints.jsonl  ← commits (semantic state transitions)
                                  ├─ canon/CANON.md     ← the human-readable source of truth
                                  ├─ branches/*.md      ← branch cards
                                  ├─ packs/*.yaml       ← Context Packs (handoff units)
                                  └─ .index/basin.db    ← rebuildable index (gitignored)
                                            │
                                  basin tui ▼  (gh-orbit-style terminal cockpit)
                          Threads · Branches · Changes · Canon · Proposals
```

A **commit is not a chat turn** — it's a semantic state transition. Most messages change nothing; the ones that settle a decision or add a constraint do.

## Quickstart

Requires Python 3.10+ and either [Claude Code](https://claude.com/claude-code) or Codex. No other dependencies.

```bash
git clone https://github.com/Oscar-V4/basin.git
cd basin
./plugin/install.sh          # Claude Code: links hooks + slash commands, merges settings.json
./plugin/install-codex.sh    # Codex: links hooks, merges ~/.codex/hooks.json

cd /your/project
basin setup                  # scaffold .basin/ in your project
basin tui                    # open the cockpit
```

Then work in Claude Code or Codex as usual — Basin captures context automatically (and especially right before a compaction when the host exposes that hook). Codex `Stop` is treated as turn-end capture, not final session closure, so frequent turn-end runs are safe and idempotent when no new transcript events exist. Or drive it directly:

```bash
basin status                 # current Canon + staged changes + open questions
basin save -m "lock storage decision"   # settle staged atoms into the Canon
basin fork --name spike      # explore a new approach on a branch
basin reconcile --branch spike           # preview what settling that branch would change
basin pack --lod standard    # compile a Context Pack to hand off to a new thread
basin inherit --branch main  # load the pack into a new thread + take the continuity test
basin on | off               # toggle the always-on engine for this project
```

In Claude Code, the same actions are slash commands: `/basin-save`, `/basin-pack`, `/basin-fork`, `/basin-status`, `/basin-inherit`.

## Concepts

| Concept | What it is |
|---|---|
| **Context atom** | One durable unit of context — a `decision`, `constraint`, `assumption`, `open_question`, `rejected_path`, etc. — with status, provenance, confidence, and authority tier. |
| **Canon** | The settled source of truth for a project. Branches explore; the Canon is what has settled. |
| **Branch / fork** | A workroom for exploring an approach. A fork inherits the Canon, then diverges. |
| **Checkpoint** | A commit: a semantic state transition (capture, pre-compact, session-end, save, fork point). |
| **Context Pack** | The deployment unit of AI collaboration — a budgeted, inheritable artifact a new thread loads, with a continuity test to verify the inheritance took. |

More in [docs/concepts.md](docs/concepts.md) and [docs/comparison-to-git.md](docs/comparison-to-git.md).

## Status

| Area | State |
|---|---|
| Engine — capture, extraction, fingerprint change-classification, save/promote, reindex, Context Pack | ✅ working |
| Storage — append-only JSONL truth + Markdown/YAML projections + rebuildable SQLite index | ✅ working |
| Claude Code plugin — hooks (session-start / pre-compact / session-end), slash commands, `install.sh` | ✅ working |
| Codex capture — rollout transcript adapter + hooks (session-start / pre-compact / turn-end), `install-codex.sh` | ✅ working |
| `basin tui` — Threads lane graph, Branches, Changes, Canon, Proposals | ✅ working |
| Context graph clustering (stable community ids) | ✅ working |
| Detached LLM refiner (`basin worker`) | 🧪 opt-in — `BASIN_LLM=codex` (gpt-5.5) or `=1` (legacy `claude -p`, now API-only) |
| Automatic fork detection | 🧪 opt-in (`auto_fork`); explicit `basin fork` is the reliable path |
| **Tests** | ✅ **172 passing** (`engine/tests/`) |

This is research-grade software under active development. Interfaces will change.

## Repository layout

```
engine/   Python 3 (stdlib only). The capture/extract/compile engine, the curses TUI, and the `basin` CLI.
plugin/   Claude Code and Codex distribution: hooks, slash commands, install scripts, plugin manifests.
docs/     Concepts and comparison notes.
assets/   Logo.
```

## Run the tests

```bash
cd engine
for t in tests/test_*.py; do python3 "$t"; done
```

## License

[MIT](LICENSE).

---

<p align="center"><sub>Basin is named for what it does: scattered context flows in, settles, and accumulates into something you can stand on.</sub></p>
