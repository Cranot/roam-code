---
name: roam
description: >
  Codebase comprehension via roam-code CLI. Use when exploring codebases,
  planning modifications, debugging failures, assessing PR risk, or checking
  architecture health. Triggers on: understanding project structure,
  pre-change safety checks, finding symbols/files, blast radius analysis,
  affected tests, health scoring, refactoring guidance, code review.
  Requires roam-code installed (`pip install roam-code`) and an indexed
  project (`roam init`).
---

# Roam — Codebase Comprehension Skill

**Repository:** <https://github.com/Cranot/roam-code>

Roam pre-indexes codebases into a semantic graph (symbols, dependencies,
call graphs, architecture layers, git history) stored in a local SQLite DB.
Query it via CLI instead of repeatedly grepping files and guessing structure.

## Setup

Ensure roam-code is installed and the project is indexed:

```bash
pip install roam-code   # or: pipx install roam-code
cd <project-root>
roam init               # indexes codebase, creates .roam/index.db
```

After `git pull` or major changes, run `roam index` to refresh (incremental,
near-instant if few files changed). After large refactors: `roam index --force`.

## Command Decision Table

Use this table to pick the right command for the situation:

| Situation | Command |
|-----------|---------|
| First time in a repo | `roam understand` then `roam tour` |
| Need a compact codebase overview | `roam map` or `roam minimap` |
| Find a symbol by name | `roam search <pattern>` |
| Need files to read for a symbol | `roam context <symbol>` |
| Inspect a file's structure | `roam file <path>` |
| Inspect a directory | `roam module <path>` |
| Before modifying a symbol | `roam preflight <symbol>` |
| What breaks if I change X? | `roam impact <symbol>` |
| Blast radius of uncommitted changes | `roam diff` |
| Debugging a failure | `roam diagnose <symbol>` |
| Which tests cover a symbol? | `roam affected-tests <symbol>` |
| Check codebase health | `roam health` |
| Find hotspots (churn x complexity) | `roam weather` |
| Detect dead/unused code | `roam dead` |
| PR risk assessment | `roam pr-risk` |
| Find dependency paths | `roam trace <source> <target>` |
| Who calls/imports this? | `roam uses <symbol>` |
| Algorithm anti-patterns | `roam algo` |
| Side effects of a function | `roam effects <symbol>` |
| Safe to delete? | `roam safe-delete <symbol>` |
| Simulate a refactor | `roam simulate move|extract|merge|delete` |

## Core Workflow

### 1. Orientation (first time in a repo)

```bash
roam understand        # tech stack, architecture, health, conventions
roam tour              # onboarding: key symbols, reading order, entry points
roam map               # project skeleton with top symbols by PageRank
```

### 2. Before Making Changes

Always run `roam preflight <symbol>` before modifying code. It combines
blast radius + affected tests + complexity + coupling + fitness into one check:

```bash
roam preflight MyClass
# Output: blast radius, affected tests, complexity, coupling, fitness verdict
```

If you only need files to read:

```bash
roam context MyClass
# Output: definition file + callers + callees with exact line ranges
```

### 3. After Making Changes

```bash
roam diff              # blast radius of uncommitted changes
roam diff --staged     # blast radius of staged changes
roam pr-risk           # risk score (0-100) + suggested reviewers
```

### 4. Debugging

```bash
roam diagnose <symbol>  # root cause ranking by z-score risk
roam trace <A> <B>      # dependency path between two symbols
roam effects <symbol>   # DB writes, network I/O, filesystem, global mutation
```

## Output Modes

- **Default:** Human-readable text (also optimized for LLM consumption)
- **`roam --json <cmd>`:** Structured JSON with consistent envelope
- **`roam --budget N <cmd>`:** Token-capped output (N = max tokens)
- **`roam --sarif <cmd>`:** SARIF 2.1.0 for CI integration

Prefer `--json` when you need to parse output programmatically.
Prefer `--budget 2000` when context window is tight.

## Key Commands Reference

### `roam search <pattern>`
Find symbols by name (regex). Results ranked by PageRank.
```bash
roam search "Auth.*Service"
roam search "handle_request" --kind fn
```

### `roam context <symbol>`
AI-optimized file list with line ranges for reading.
Supports `--task modify|debug|review` for context tuning.
```bash
roam context Flask
roam context myfile:MyFunction    # disambiguate with file prefix
```

### `roam preflight <symbol|file>`
Compound pre-change check. Run this before every modification.
```bash
roam preflight UserController
roam preflight src/auth/login.py
```

### `roam health`
Composite score (0-100). Use `--gate score>=70` for CI.
```bash
roam health
roam health --gate score>=70    # exit 1 on failure
```

### `roam diff`
Blast radius of uncommitted or committed changes.
```bash
roam diff                       # uncommitted
roam diff --staged              # staged only
roam diff HEAD~3..HEAD          # commit range
```

### `roam algo`
Detect algorithm anti-patterns (23 patterns: O(n^2) loops, N+1 queries,
quadratic string building, etc.) with confidence levels and fix suggestions.
```bash
roam algo
roam algo --confidence high     # high-confidence only
roam algo --task nested-lookup  # specific pattern
```

### `roam impact <symbol>`
Full blast radius using Personalized PageRank.
```bash
roam impact Flask
```

### `roam symbol <name>`
Symbol definition + callers + callees + metrics.
```bash
roam symbol open_db
roam symbol --full open_db      # include source code
```

### `roam affected-tests <symbol|file>`
Trace reverse call graph to find covering tests.
```bash
roam affected-tests UserService
```

### `roam agent-export --write`
Auto-generate agent instructions for the project. Detects CLAUDE.md,
AGENTS.md, .cursor/rules, etc.
```bash
roam agent-export --write
roam agent-export --agent-prompt    # compact ~500-token prompt
```

### `roam minimap --update`
Inject/refresh annotated codebase snapshot in CLAUDE.md.
```bash
roam minimap --update           # update sentinel block in CLAUDE.md
```

## Discovering More Commands

This skill covers the most common commands, but roam has 137 commands.
To explore what's available:

```bash
roam --help                 # list all available commands
roam <command> --help       # detailed usage for a specific command
```

For full documentation, examples, and the latest features, see the
[roam-code repository](https://github.com/Cranot/roam-code).

## Tips

- One `roam` command replaces 5-10 grep/read cycles. Always try roam first.
- Use `roam search` instead of grep/glob for finding symbols — it understands
  definitions vs. usage and ranks by importance.
- `roam context` gives exact line ranges — more precise than reading whole files.
- After `git pull`, run `roam index` to keep the graph fresh.
- For disambiguation, use `file:symbol` syntax: `roam symbol myfile:MyClass`.
