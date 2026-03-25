# SessionAnchor

[![PyPI version](https://img.shields.io/pypi/v/sessionanchor.svg)](https://pypi.org/project/SessionAnchor/)
[![Python](https://img.shields.io/pypi/pyversions/sessionanchor.svg)](https://pypi.org/project/SessionAnchor/)
[![License](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](https://github.com/calesthio/SessionAnchor/blob/master/LICENSE)

One-command context memory for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

## The Problem

Claude Code sessions are stateless. Every new conversation starts cold. Claude re-reads your entire `CLAUDE.md`, architecture docs, and context files from scratch, burning through tokens and losing the decisions, progress, and institutional knowledge from your last session.

If your project has grown any real context (and most do), you're paying for it on every single session start:

- A typical project accumulates 10-30KB of context files
- That's ~15,000-25,000 tokens loaded every session, most of it stale
- Decisions made three weeks ago get equal weight to yesterday's blockers
- There's no continuity: Claude doesn't know what it did last time

## The Solution

`SessionAnchor` replaces flat context files with a local SQLite memory store that uses tiered retrieval. One command gives your repo continuity across sessions.

```
Before: ~18,000 tokens loaded from markdown files every session
After:   ~2,000 tokens loaded from structured memory (9x reduction)
```

The boot briefing is compact, current, and structured. Claude gets exactly what it needs to resume work, not a wall of historical text.

## Who This Is For

This tool is built for a specific scenario:

- **You use Claude Code** (Anthropic's CLI agent) on real projects
- **Your projects have accumulated context** that Claude needs across sessions
- **You want local-only, zero-setup continuity** without hosted services or API keys
- **You care about token efficiency** and want to measure it

It is **not** a general-purpose memory layer, a hosted service, or a multi-agent coordination framework. It solves one problem well: giving Claude Code repos continuity across sessions.

## Why Not the Alternatives?

The broader "AI memory" space has several tools, but they solve different (bigger) problems:

| Tool | What it is | Why it's different |
|------|-----------|-------------------|
| **[Mem0](https://github.com/mem0ai/mem0)** | Hosted/self-hosted memory layer for AI apps | Platform-scale ambition. Has a cloud service, API keys, embedding models. You're adopting an infrastructure dependency, not a dev tool. |
| **[Letta](https://github.com/letta-ai/letta)** (MemGPT) | Stateful agent framework with git-backed memory filesystem | Deep on agent internals: memory editing, inner/outer monologue, tool use. Way more than context persistence for a CLI. |
| **[Zep](https://github.com/getzep/zep)** | Fast, scalable context engine with knowledge graphs | Enterprise graph-backed memory with temporal awareness. Requires a server, designed for production apps with users. |
| **[OpenViking](https://github.com/ArcadeAI/openviking)** | Context database with hierarchical retrieval | Full context DB: session management, retrieval strategies, embedding pipelines. Another infrastructure layer. |

**SessionAnchor** is none of these. It's a single `pip install` with zero dependencies that creates a SQLite file in your repo's `.claude/` directory. No servers, no API keys, no embeddings, no hosted anything. It's closer to a `.bash_history` for your Claude sessions than a memory platform.

The sharp wedge:
> One command gives your Claude Code repo continuity across sessions. Local-only SQLite. Measurable token savings. Nothing else.

## Install

```bash
pip install sessionanchor
# or
pipx install sessionanchor
# or
uvx sessionanchor init
```

**Requirements:** Python 3.9+. No dependencies (uses Python's built-in `sqlite3` with FTS5).

## Quick Start

```bash
# 1. Initialize (run once per project)
cd your-project
sessionanchor init

# 2. At the start of every Claude Code session
sessionanchor boot

# 3. That's it. CLAUDE.md is configured to tell Claude
#    to save context automatically during sessions.
```

## Architecture

### Tiered Memory Model

Context is stored in three tiers, inspired by CPU cache hierarchies. Each tier has a different loading strategy:

```
+-------------------------------------------+
|  L0: Core Identity       (~500 tokens)    |  Always loaded
|  Project name, current phase, top 3       |
|  priorities, critical deadlines           |
+-------------------------------------------+
|  L1: Session-Relevant   (~1500 tokens)    |  Loaded at boot
|  Last session summary, active action      |
|  items, recent decisions, blockers        |
+-------------------------------------------+
|  L2: Deep Archive       (unlimited)       |  Loaded on demand
|  Full history, completed items, old       |
|  decisions, technical notes, patterns     |
+-------------------------------------------+
```

| Tier | When Loaded | Token Budget | Contains |
|------|-------------|-------------|----------|
| **L0** | Every boot | ~500 | Project identity, current phase, top priorities, critical deadlines |
| **L1** | Every boot | ~1500 | Last session summary, active action items, recent decisions, current blockers |
| **L2** | On query | Unlimited | Full history, completed items, old decisions, deep technical notes, patterns, lessons |

Boot loads L0 + L1 (~2000 tokens). L2 is only retrieved when Claude calls `sessionanchor query` during a session. This keeps the boot payload small while making the full history searchable.

### Storage

Everything lives in a single SQLite database at `.claude/memory/context.db`:

- **WAL mode** for safe concurrent access (Claude's subagents can write while the main session reads)
- **FTS5 full-text search** with BM25 ranking for `query` lookups
- **Deterministic IDs** via `sha256(project:category:title)` enable natural dedup through upsert
- **Access tracking** records how often each entry is retrieved (useful for future relevance scoring)

The schema is two tables: `entries` (the memory items) and `sessions` (session metadata with token counts).

### Auto-Compaction

Without compaction, L1 bloats over time as decisions and action items accumulate. `SessionAnchor` runs automatic compaction at every boot:

| Strategy | Rule | Effect |
|----------|------|--------|
| **Time-based** | L1 entries older than 14 days | Demoted to L2 |
| **Count cap** | More than 20 active L1 entries | Oldest demoted to L2 |
| **Completed grace** | Completed items older than 3 days | Demoted to L2 |
| **Superseded** | Entries with a `supersedes` field | Old entry archived |

This means L1 stays fresh and bounded without manual cleanup.
When a newer memory replaces an older one, save it with `--supersedes <entry-id>` so the next `boot` or `compact` run archives the superseded entry automatically.

### Secret Filtering

The `.contextignore` file (created by `init`) uses gitignore-style patterns to exclude files from the repo indexer:

```
.env
.env.*
*.key
*.pem
node_modules/
.claude/memory/
```

Additionally, content is scanned for secret patterns before being stored. AWS keys (`AKIA...`), API tokens (`sk-...`, `ghp_...`), JWTs (`eyJ...`), and Bearer tokens are automatically replaced with `[REDACTED]`.

### CLAUDE.md Integration

`init` creates (or patches) your `CLAUDE.md` with instructions that tell Claude to:

1. **Boot from memory** instead of reading large context files
2. **Save continuously** via background subagents after decisions, completions, and blockers
3. **Never wait for session end** to save (the user may close the window at any time)
4. **Log token counts** from the boot footer for measurability

The key insight: Claude doesn't need to be told to "use the memory system." It needs to be told to **save to it continuously** and **not read the old markdown files**. The CLAUDE.md prompt handles both.

### Token Tracking

The boot command appends a token estimate footer:

```
# Boot tokens: ~1,247 (L0: ~480, L1: ~767)
```

This uses a simple heuristic (words x 1.3) that's ~90% accurate for English text. It's designed for measuring **relative savings** (how much smaller is boot vs. reading all your markdown files?) rather than exact billing. The `session-end` command can log these counts for tracking over time.

### How `init` Works

`sessionanchor init` performs exactly these steps:

1. Detects the repo root (walks up to find `.git`, or uses cwd)
2. Auto-detects the project name from git remote origin or directory name
3. Creates `.claude/memory/` directory
4. Creates the SQLite database with schema
5. Creates `.contextignore` with sensible defaults
6. Creates or patches `CLAUDE.md` (appends the context section if CLAUDE.md already exists)
7. Adds `.claude/memory/` to `.gitignore`
8. Runs an initial repo structure index
9. Prints a quick-start guide

It's idempotent. Running `init` twice won't duplicate the CLAUDE.md section or overwrite your `.contextignore`.

## Commands

```bash
sessionanchor init              # Bootstrap memory for current repo
sessionanchor boot              # Print session briefing (~2000 tokens)
sessionanchor boot --full       # Include stats and last session info

# Save context entries
sessionanchor save add \
  --tier L1 \
  --category decision \
  --title "Chose Postgres" \
  --content "Better JSON support than MySQL"

sessionanchor save add \
  --tier L1 \
  --category decision \
  --title "Use OAuth PKCE" \
  --content "Replaces the legacy auth callback flow" \
  --supersedes 1a2b3c4d5e6f7890

sessionanchor save complete \
  --title "Build auth flow" \
  --category action_item

sessionanchor save session-end \
  --summary "Shipped auth, started rate limiting"

# Query memory
sessionanchor query "auth"                    # Full-text search
sessionanchor query --category decision       # By category
sessionanchor query --category action_item --status active
sessionanchor query --since 7                 # Last 7 days

# Codebase index
sessionanchor index             # Re-index repo structure
sessionanchor index find "auth" # Search code files
sessionanchor index map         # Show structural overview

# Maintenance
sessionanchor compact           # Manual L1 compaction
sessionanchor stats             # Memory store statistics
sessionanchor version           # Show version
```

## Categories

Entries are organized into 13 categories. Use the one that fits:

| Category | When to use |
|----------|------------|
| `identity` | Project identity, org structure, what this repo is |
| `preference` | Working conventions, code style, deploy preferences |
| `priority` | Current goals, ranked by urgency |
| `decision` | Decisions made, with date and rationale |
| `deadline` | Time-sensitive items with expiry dates |
| `action_item` | Tasks with owner and status |
| `blocker` | Things preventing progress |
| `session_log` | Summary of what happened in a session |
| `architecture` | System design, data flow, constraints |
| `technical_note` | Implementation details, fixes, gotchas |
| `infrastructure` | Deploy config, env vars, DNS |
| `pattern` | Reusable problem-solution pairs |
| `lesson` | Things learned from mistakes or successes |

## Limitations and Non-Goals

Transparency about what this tool does **not** do:

- **No semantic search.** Queries use SQLite FTS5 (keyword matching with BM25 ranking), not embeddings. This is deliberate: zero dependencies, instant results, good enough for project-scoped memory.
- **No multi-user coordination.** This is personal memory for one developer's Claude sessions on one repo. WAL mode handles concurrent reads/writes from subagents, but there's no semantic conflict resolution.
- **No cloud sync.** Memory lives in `.claude/memory/context.db` on your machine. If you want it on another machine, copy the file.
- **No automatic summarization.** Compaction demotes old entries to L2, but doesn't summarize or merge them. That's a future direction.
- **Token estimates are approximate.** The word x 1.3 heuristic is ~90% accurate for English. Anthropic doesn't publish Claude's tokenizer, so exact counts aren't possible without API calls.

## Contributing

Contributions welcome. The codebase is intentionally small (~1,200 lines of Python, zero dependencies). If your change adds a dependency, it needs a very strong justification.

```bash
git clone https://github.com/calesthio/SessionAnchor
cd SessionAnchor
pip install -e .
pytest tests/ -v
```

## License

GNU AGPL v3
