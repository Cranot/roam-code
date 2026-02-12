<div align="center">
<pre>
██████   ██████   █████  ███    ███
██   ██ ██    ██ ██   ██ ████  ████
██████  ██    ██ ███████ ██ ████ ██
██   ██ ██    ██ ██   ██ ██  ██  ██
██   ██  ██████  ██   ██ ██      ██
</pre>

**codebase intelligence for AI agents**

one shell command replaces 5-10 tool calls · saves 60-70% of context-gathering tokens

v7.1.0 · 48 commands · 16 languages · Salesforce Tier 1 · SARIF · MCP · GitHub Action

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml/badge.svg)](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml)
[![556 tests](https://img.shields.io/badge/tests-556_passing-brightgreen.svg)](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml)

</div>

---

> Your AI agent shouldn't need 10 tool calls to understand a codebase.

Roam pre-indexes everything -- symbols, call graphs, dependencies, architecture, git history -- into a local SQLite database. Any codebase question is one shell command away. Output is plain ASCII optimized for LLM token budgets. No API keys, no servers, no configuration.

```bash
$ roam index                     # build once (~5s), then incremental
$ roam understand                # full codebase briefing in one call
$ roam symbol Flask              # definition + 47 callers + 3 callees + PageRank
$ roam context Flask             # AI-ready: files-to-read with exact line ranges
$ roam impact create_app         # 34 symbols break if this changes
$ roam health                    # composite score (0-100) + cycles + bottlenecks
$ roam preflight Flask           # blast radius + tests + complexity + fitness in one call
$ roam pr-risk HEAD~3..HEAD      # 0-100 risk score + dead exports + reviewers
$ roam diff                      # blast radius of your uncommitted changes
$ roam --json health             # structured JSON for CI pipelines
$ roam health --gate score>=70   # CI quality gate — exit 1 on failure
```

<details>
<summary><strong>Table of Contents</strong></summary>

**Getting Started:** [Install](#install) · [Quick Start](#quick-start) · [Commands](#commands) · [Walkthrough](#walkthrough-investigating-a-codebase)

**Integration:** [AI Coding Tools](#integration-with-ai-coding-tools) · [MCP Server](#mcp-server) · [GitHub Action](#github-action) · [SARIF Output](#sarif-output)

**Reference:** [Language Support](#language-support) · [Performance](#performance) · [How It Works](#how-it-works) · [How Roam Compares](#how-roam-compares)

**More:** [Limitations](#limitations) · [Troubleshooting](#troubleshooting) · [Development](#development) · [Contributing](#contributing)

</details>

## Install

```bash
# Recommended for CLI tools (isolated environment)
pipx install git+https://github.com/Cranot/roam-code.git

# Or with uv (fastest)
uv tool install git+https://github.com/Cranot/roam-code.git

# Or with pip
pip install git+https://github.com/Cranot/roam-code.git
```

Verify the install:

```bash
roam --version
```

> **Windows:** If `roam` is not found after installing with `uv`, run `uv tool update-shell` and restart your terminal so the tool directory is on PATH.

Requires Python 3.9+. Works on Linux, macOS, and Windows. Best with `git` installed (for file discovery and history analysis; falls back to directory walking without it).

> **Privacy:** Roam is 100% local. No external services, no API keys, no telemetry, no network calls. Your code never leaves your machine.

## Quick Start

```bash
cd your-project
roam init                  # creates .roam/fitness.yaml, CI workflow, indexes codebase
roam understand            # full codebase briefing in one call
```

That's it. `roam init` creates a `.roam/fitness.yaml` config (6 architecture rules), a `.github/workflows/roam.yml` CI workflow, and the `.roam/index.db` index. First index takes ~5s for 200 files, ~15s for 1,000 files. Subsequent runs are incremental and near-instant.

### What's Next

- **Set up your AI agent (recommended):** Copy the [integration instructions](#integration-with-ai-coding-tools) into your agent's config, or run `roam describe --agent-prompt >> CLAUDE.md`
- **Explore your codebase:** `roam health` → `roam weather` → `roam map`
- **Add to CI:** `roam init` already generated a GitHub Action, or see [GitHub Action](#github-action)

<details>
<summary><strong>Try it on Roam itself</strong></summary>

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .
roam init
roam understand
roam health
```

</details>

<details>
<summary><strong>Manual setup (without roam init)</strong></summary>

```bash
echo ".roam/" >> .gitignore
roam index
```

</details>

## Commands

Roam organizes its 48 commands into 7 categories. The five commands below cover ~80% of agent workflows:

```
roam understand              # orient: tech stack, architecture, conventions
roam context <name>          # gather: files-to-read with exact line ranges
roam preflight <name>        # check: blast radius + tests + fitness before changing
roam diff                    # review: blast radius of uncommitted changes
roam health                  # score: composite architecture health (0-100)
```

<details>
<summary><strong>Full command reference (48 commands, 7 categories)</strong></summary>

### Getting Started

| Command | Description |
|---------|-------------|
| `roam index [--force] [--verbose]` | Build or rebuild the codebase index |
| `roam init` | Guided onboarding: creates `.roam/fitness.yaml`, CI workflow, runs index, shows health |
| `roam understand` | Full codebase briefing: tech stack, architecture, key abstractions, health, conventions, complexity overview, entry points |
| `roam describe [--write] [--force] [--agent-prompt]` | Auto-generate project description (CLAUDE.md). `--agent-prompt` returns a compact (<500 token) LLM system prompt |
| `roam map [-n N] [--full] [--budget N]` | Project skeleton: files, languages, entry points, top symbols by PageRank. `--budget` caps output to N tokens |

### Daily Workflow

| Command | Description |
|---------|-------------|
| `roam file <path> [--full] [--changed] [--deps-of PATH]` | File skeleton: all definitions with signatures. Multi-file mode with `--changed` (uncommitted files) or `--deps-of` (file + imports) |
| `roam symbol <name> [--full]` | Symbol definition + callers + callees + metrics. Supports `file:symbol` disambiguation |
| `roam context <symbol> [--task MODE] [--for-file PATH]` | AI-optimized context: definition + callers + callees + files-to-read with line ranges. `--task` tailors output for refactor/debug/extend/review. `--for-file` gives file-level context |
| `roam search <pattern> [--kind KIND]` | Find symbols by name pattern, PageRank-ranked |
| `roam grep <pattern> [-g glob] [-n N]` | Text search annotated with enclosing symbol context |
| `roam deps <path> [--full]` | What a file imports and what imports it |
| `roam trace <source> <target> [-k N]` | Dependency paths with coupling strength, hub detection, quality scoring |
| `roam impact <symbol>` | Blast radius: what breaks if a symbol changes |
| `roam diff [--staged] [--full] [REV_RANGE]` | Blast radius of uncommitted changes or a commit range. `--full` adds tests + coupling + fitness |
| `roam pr-risk [REV_RANGE]` | PR risk score (0-100) + new dead exports + suggested reviewers |
| `roam preflight <symbol\|file>` | Compound pre-change check: blast radius + tests + complexity + coupling + conventions + fitness. Reduces agent round-trips by 60-70% |
| `roam safe-delete <symbol>` | Safe deletion check: SAFE/REVIEW/UNSAFE verdict with reasoning |
| `roam test-map <name>` | Map a symbol or file to its test coverage |

### Codebase Health

| Command | Description |
|---------|-------------|
| `roam health [--no-framework]` | Composite health score (0-100): tangle ratio, god components, bottlenecks, layer violations, per-file health. Utility paths get relaxed thresholds |
| `roam complexity [--bumpy-road]` | Per-function cognitive complexity (SonarSource-compatible). `--bumpy-road` flags files with many moderate-complexity functions |
| `roam weather [-n N]` | Hotspots ranked by churn x complexity |
| `roam debt` | Hotspot-weighted tech debt prioritization. Code in hotspots costs 15x more |
| `roam fitness [--explain]` | Architectural fitness functions from `.roam/fitness.yaml`. `--explain` shows reason + link per rule |
| `roam alerts` | Health degradation trend detection from snapshot history |
| `roam snapshot [--tag TAG]` | Persist health metrics snapshot for trend tracking |
| `roam trend` | Health score history with sparkline visualization |
| `roam digest [--brief] [--since TAG]` | Compare current metrics against last snapshot — deltas with directional arrows |

### Architecture

| Command | Description |
|---------|-------------|
| `roam clusters [--min-size N]` | Community detection vs directory structure — cohesion %, coupling matrices, split suggestions |
| `roam layers` | Topological dependency layers + directory breakdown + upward violations |
| `roam dead [--all] [--summary] [--clusters]` | Unreferenced exported symbols with SAFE/REVIEW/INTENTIONAL verdicts |
| `roam fan [symbol\|file] [-n N] [--no-framework]` | Fan-in/fan-out: most connected symbols or files |
| `roam risk [-n N] [--domain KW] [--explain]` | Domain-weighted risk ranking (3-source matching: name + callee-chain + path-zone) |
| `roam why <name> [name2 ...]` | Role classification (Hub/Bridge/Core utility/Leaf/Internal), reach, criticality, verdict. Batch mode |
| `roam split <file>` | Internal symbol groups with isolation % and extraction suggestions |
| `roam entry-points` | Entry point catalog with protocol classification (HTTP, CLI, Event, Scheduled, Main) |
| `roam patterns` | Architectural pattern recognition: Strategy, Factory, Observer, Repository, Middleware, Decorator |
| `roam safe-zones` | Graph-based containment boundaries: ISOLATED/CONTAINED/EXPOSED zones |
| `roam coverage-gaps` | Unprotected entry points with no path to gate symbols (auth, permission checks) |

### Exploration

| Command | Description |
|---------|-------------|
| `roam module <path>` | Directory contents: exports, signatures, dependencies, cohesion |
| `roam sketch <dir> [--full]` | Compact structural skeleton of a directory (API surface) |
| `roam uses <name>` | All consumers: callers, importers, inheritors |
| `roam owner <path>` | Code ownership: who owns a file or directory |
| `roam coupling [-n N] [--set]` | Temporal coupling: file pairs that change together. `--set` for hypergraph analysis |
| `roam fn-coupling` | Function-level temporal coupling across files — hidden dependencies |
| `roam bus-factor [--brain-methods]` | Knowledge loss risk per module. `--brain-methods` flags functions with cc >= 25 AND 50+ lines |
| `roam doc-staleness` | Detect stale docstrings where code changed after docs were last updated |
| `roam conventions` | Auto-detect naming styles, import preferences, export patterns. Flags outliers |
| `roam breaking [REV_RANGE]` | Breaking change detection: removed exports, signature changes, renamed symbols |
| `roam affected-tests <symbol\|file>` | Trace through reverse call graph to test files. Outputs runnable `pytest` command |

### Reports & CI

| Command | Description |
|---------|-------------|
| `roam report [--list] [--config FILE] [PRESET]` | Compound presets: `first-contact`, `security`, `pre-pr`, `refactor`. `--config` loads custom presets from JSON |
| `roam describe --write` | Generate CLAUDE.md with conventions, complexity hotspots, and architecture overview |

### Global Options

| Option | Description |
|--------|-------------|
| `roam --json <command>` | Structured JSON output with consistent envelope. Works on all 48 commands |
| `roam --compact <command>` | Token-efficient output: TSV tables (40-50% fewer tokens), minimal JSON envelope |
| `roam <command> --gate EXPR` | CI quality gate (e.g., `--gate score>=70`). Exit code 1 on failure |
| `roam --version` | Show version |
| `roam --help` | Categorized command list (7 groups) |

```bash
# Examples
roam --json health               # {"command":"health","score":78,"tangle_pct":5.2,...}
roam --compact --json health     # minimal envelope, no version/timestamp
roam health --gate score>=70     # CI gate — fails if score < 70
roam --json diff HEAD~3..HEAD    # structured blast radius
```

</details>

## Walkthrough: Investigating a Codebase

Here's how you'd use Roam to understand a project you've never seen before. Using Flask as an example:

**Step 1: Onboard and get the full picture**

```
$ roam init
Created .roam/fitness.yaml (6 starter rules)
Created .github/workflows/roam.yml
Done. 226 files, 1132 symbols, 233 edges.
Health: 78/100

$ roam understand
Tech stack: Python (flask, jinja2, werkzeug)
Architecture: Monolithic — 3 layers, 5 clusters
Key abstractions: Flask, Blueprint, Request, Response
Health: 78/100 — 1 god component (Flask)
Entry points: src/flask/__init__.py, src/flask/cli.py
Conventions: snake_case functions, PascalCase classes, relative imports
Complexity: avg 4.2, 3 high (>15), 0 critical (>25)
```

**Step 2: Drill into a key file**

```
$ roam file src/flask/app.py
src/flask/app.py  (python, 963 lines)

  cls  Flask(App)                                   :76-963
    meth  __init__(self, import_name, ...)           :152
    meth  route(self, rule, **options)               :411
    meth  register_blueprint(self, blueprint, ...)   :580
    meth  make_response(self, rv)                    :742
    ...12 more methods
```

**Step 3: Who depends on this?**

```
$ roam deps src/flask/app.py
Imported by:
file                        symbols
--------------------------  -------
src/flask/__init__.py       3
src/flask/testing.py        2
tests/test_basic.py         1
...18 files total
```

**Step 4: Find the hotspots**

```
$ roam weather
=== Hotspots (churn x complexity) ===
Score  Churn  Complexity  Path                    Lang
-----  -----  ----------  ----------------------  ------
18420  460    40.0        src/flask/app.py        python
12180  348    35.0        src/flask/blueprints.py python
```

**Step 5: Check architecture health**

```
$ roam health
Health: 78/100
  Tangle: 0.0% (0/1132 symbols in cycles)
  1 god component (Flask, degree 47, actionable)
  0 bottlenecks, 0 layer violations

=== God Components (degree > 20) ===
Sev      Name   Kind  Degree  Cat  File
-------  -----  ----  ------  ---  ------------------
WARNING  Flask  cls   47      act  src/flask/app.py
```

**Step 6: Get AI-ready context for a symbol**

```
$ roam context Flask
Files to read:
  src/flask/app.py:76-963              # definition
  src/flask/__init__.py:1-15           # re-export
  src/flask/testing.py:22-45           # caller: FlaskClient.__init__
  tests/test_basic.py:12-30            # caller: test_app_factory
  ...12 more files

Callers: 47  Callees: 3
```

`roam context` gives an AI agent exactly the files and line ranges it needs to safely modify a symbol -- no more, no less. Output is capped by PageRank to avoid flooding context with low-value files.

**Step 7: Pre-change safety check**

```
$ roam preflight Flask
=== Preflight: Flask ===
Blast radius: 47 callers, 89 transitive
Affected tests: 31 (DIRECT: 12, TRANSITIVE: 19)
Complexity: cc=40 (critical), nesting=6
Coupling: 3 hidden co-change partners
Fitness: 1 violation (max-complexity exceeded)
Verdict: HIGH RISK — consider splitting before modifying
```

**Step 8: Decompose a large file**

```
$ roam split src/flask/app.py
=== Split analysis: src/flask/app.py ===
  87 symbols, 42 internal edges, 95 external edges
  Cross-group coupling: 18%

  Group 1 (routing) — 12 symbols, isolation: 83% [extractable]
    meth  route              L411  PR=0.0088
    meth  add_url_rule       L450  PR=0.0045
    ...

=== Extraction Suggestions ===
  Extract 'routing' group: route, add_url_rule, endpoint (+9 more)
    83% isolated, only 3 edges to other groups
```

**Step 9: Understand why a symbol matters**

```
$ roam why Flask url_for Blueprint
Symbol     Role          Fan         Reach     Risk      Verdict
---------  ------------  ----------  --------  --------  --------------------------------------------------
Flask      Hub           fan-in:47   reach:89  CRITICAL  God symbol (47 in, 12 out). Consider splitting.
url_for    Core utility  fan-in:31   reach:45  HIGH      Widely used utility (31 callers). Stable interface.
Blueprint  Bridge        fan-in:18   reach:34  moderate  Coupling point between clusters.
```

**Step 10: Generate docs and set up CI**

```
$ roam describe --write
Wrote CLAUDE.md (98 lines)

$ roam health --gate score>=70
Health: 78/100 — PASS
```

Ten commands, and you have a complete picture of the project: structure, key symbols, dependencies, hotspots, architecture health, AI-ready context, pre-change safety checks, file decomposition, and CI quality gates.

## Integration with AI Coding Tools

Roam is designed to be called by AI coding agents via shell commands. Instead of multiple Glob/Grep/Read cycles, the agent runs one `roam` command and gets structured, token-efficient output.

**Decision order for agents** -- when in doubt, follow this priority:

| Situation | Command |
|-----------|---------|
| First time in a repo | `roam understand` then `roam map` |
| Need to modify a symbol | `roam preflight <name>` (blast radius + tests + fitness) |
| Need files to read around a symbol | `roam context <name>` (files + line ranges) |
| Need to find a symbol | `roam search <pattern>` |
| Need file structure | `roam file <path>` |
| Pre-PR check | `roam pr-risk HEAD~3..HEAD` |
| What breaks if I change X? | `roam impact <symbol>` (read-only blast radius) |

Add the following instructions to your AI tool's configuration file:

```markdown
## Codebase navigation

Use `roam` CLI for codebase comprehension (pre-installed).
Run `roam init` once, then use these commands instead of Glob/Grep/Read exploration:

- `roam understand` -- full codebase briefing (tech stack, architecture, health, conventions)
- `roam map` -- project overview, entry points, key symbols
- `roam file <path>` -- file skeleton with all definitions (multi-file: `--changed`, `--deps-of`)
- `roam symbol <name>` -- definition + callers + callees
- `roam context <name>` -- AI context: definition + callers + callees + files-to-read
- `roam context --task refactor|debug|extend|review <name>` -- task-aware context
- `roam context --for-file <path>` -- file-level context with tests + coupling
- `roam preflight <name>` -- compound pre-change check (blast radius + tests + fitness)
- `roam deps <path>` -- file import/imported-by graph
- `roam trace <source> <target>` -- dependency paths with coupling + hub detection
- `roam search <pattern>` -- find symbols by name (PageRank-ranked)
- `roam grep <pattern>` -- text search with symbol context
- `roam health` -- composite score (0-100) + architecture issues
- `roam health --gate score>=70` -- CI quality gate
- `roam weather` -- hotspots (churn x complexity)
- `roam impact <symbol>` -- blast radius (what breaks if changed)
- `roam split <file>` -- internal symbol groups with extraction suggestions
- `roam risk` -- domain-weighted risk ranking
- `roam why <name>` -- role, reach, criticality, verdict (batch: `roam why A B C`)
- `roam safe-delete <symbol>` -- check if safe to delete
- `roam diff` -- blast radius of uncommitted changes
- `roam pr-risk HEAD~3..HEAD` -- PR risk score + dead exports + reviewers
- `roam affected-tests <name>` -- trace to test files, outputs runnable command
- `roam owner <path>` -- code ownership
- `roam dead` -- unreferenced exports with verdicts
- `roam describe --agent-prompt` -- compact project summary (<500 tokens)

Use `roam --json <cmd>` for structured JSON. Use `roam --compact --json <cmd>` for token-efficient output.
```

<details>
<summary><strong>Where to put this for each tool</strong></summary>

| Tool | Config file |
|------|-------------|
| **Claude Code** | `CLAUDE.md` in your project root |
| **OpenAI Codex CLI** | `AGENTS.md` in your project root |
| **Gemini CLI** | `GEMINI.md` in your project root |
| **Cursor** | `.cursor/rules/roam.mdc` (add `alwaysApply: true` frontmatter) |
| **Windsurf** | `.windsurf/rules/roam.md` (add `trigger: always_on` frontmatter) |
| **GitHub Copilot** | `.github/copilot-instructions.md` |
| **Aider** | `CONVENTIONS.md` |
| **Continue.dev** | `config.yaml` rules |
| **Cline** | `.clinerules/` directory |

The pattern is the same for any tool that can execute shell commands: tell the agent that `roam` exists and when to use it instead of raw file exploration.

</details>

### When to use Roam vs native tools

| Task | Use Roam | Use native tools |
|------|----------|-----------------|
| "What calls this function?" | `roam symbol <name>` | LSP / Grep (if not indexed) |
| "What files do I need to read?" | `roam context <name>` | Manual symbol tracing (5+ calls) |
| "Is it safe to change X?" | `roam preflight <name>` | Multiple manual checks (slow) |
| "Show me this file's structure" | `roam file <path>` | Read the file directly |
| "Find a specific string in code" | `roam grep <pattern>` | `grep` / `rg` (less context) |
| "Understand project architecture" | `roam understand` | Manual exploration (many calls) |
| "What breaks if I change X?" | `roam impact <symbol>` | No direct equivalent |
| "How should I split this large file?" | `roam split <file>` | Manual reading + guessing |
| "What's the riskiest code?" | `roam risk` | Manual review |
| "Why does this symbol exist?" | `roam why <name>` | 3+ separate commands |
| "Can I safely delete this?" | `roam safe-delete <symbol>` | `roam dead` + manual grep |
| "Review blast radius of a PR" | `roam pr-risk HEAD~3..HEAD` | Manual git diff + tracing |
| "What tests to run?" | `roam affected-tests <name>` | Grep for imports (misses indirect) |
| "Who should review this file?" | `roam owner <path>` | `git log --follow` (raw data) |
| "Generate project docs for AI" | `roam describe --write` | Write manually |
| "Codebase health score for CI" | `roam health --gate score>=70` | No equivalent |

## MCP Server

Roam includes a [Model Context Protocol](https://modelcontextprotocol.io/) server for direct integration with AI tools that support MCP (Claude Desktop, Claude Code, etc.).

```bash
# Install the optional dependency
pip install fastmcp

# Run the server
fastmcp run roam.mcp_server:mcp
```

The MCP server exposes 14 tools and 2 resources:

**Tools:** `understand`, `health`, `preflight`, `search_symbol`, `context`, `trace`, `impact`, `file_info`, `pr_risk`, `breaking_changes`, `affected_tests`, `dead_code`, `complexity_report`, `repo_map`

**Resources:** `roam://health` (current health score), `roam://summary` (project overview)

<details>
<summary><strong>Claude Desktop configuration</strong></summary>

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "roam": {
      "command": "fastmcp",
      "args": ["run", "roam.mcp_server:mcp"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

</details>

## GitHub Action

Roam ships a reusable GitHub Action for CI integration. Add architecture checks to any PR:

```yaml
# .github/workflows/roam.yml
name: Roam Analysis
on: [pull_request]

jobs:
  roam:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: Cranot/roam-code@main
        with:
          command: health --gate score>=70
          comment: true          # post results as PR comment
          fail-on-violation: true
```

**Inputs:**

| Input | Default | Description |
|-------|---------|-------------|
| `command` | `health` | Roam command to run |
| `python-version` | `3.12` | Python version for the runner |
| `comment` | `false` | Post results as PR comment |
| `fail-on-violation` | `false` | Fail the job on violations |
| `roam-version` | (latest) | Pin to a specific roam version |

Use `roam init` to auto-generate this workflow for your project.

## SARIF Output

Roam can export analysis results in [SARIF 2.1.0](https://sarifweb.azurewebsites.net/) format for GitHub Code Scanning integration. Upload results to get inline PR annotations.

```python
from roam.output.sarif import (
    dead_to_sarif,
    complexity_to_sarif,
    health_to_sarif,
    fitness_to_sarif,
    breaking_to_sarif,
    conventions_to_sarif,
    write_sarif,
)

# Generate SARIF from health analysis
sarif = health_to_sarif(health_data)
write_sarif(sarif, "roam-health.sarif")
```

Then upload in CI:

```yaml
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: roam-health.sarif
```

## Language Support

### Tier 1 -- Full extraction (dedicated parsers)

| Language | Extensions | Symbols | References | Inheritance |
|----------|-----------|---------|------------|-------------|
| Python | `.py` `.pyi` | classes, functions, methods, decorators, variables | imports, calls, inheritance | extends, `__all__` exports |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | classes, functions, arrow functions, CJS exports | imports, require(), calls | extends |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | interfaces, type aliases, enums + all JS | imports, calls, type refs | extends, implements |
| Java | `.java` | classes, interfaces, enums, constructors, fields | imports, calls | extends, implements |
| Go | `.go` | structs, interfaces, functions, methods, fields | imports, calls | embedded structs |
| Rust | `.rs` | structs, traits, impls, enums, functions | use, calls | impl Trait for Struct |
| C | `.c` `.h` | structs, functions, typedefs, enums | includes, calls | -- |
| C++ | `.cpp` `.hpp` `.cc` `.hh` | classes, namespaces, templates + all C | includes, calls | extends |
| PHP | `.php` | classes, interfaces, traits, enums, methods, properties, constants, constructor promotion | namespace use, calls, static calls, nullsafe calls (`?->`), `new` | extends, implements, use (traits) |
| Vue | `.vue` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |
| Svelte | `.svelte` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |

### Tier 1 -- Salesforce ecosystem

Roam is one of the few static analysis tools with first-class Salesforce support. Cross-language edges mean `roam impact AccountService` shows blast radius across Apex, LWC, Aura, and Visualforce -- not just within one language.

| Language | Extensions | Symbols | References |
|----------|-----------|---------|------------|
| Apex | `.cls` `.trigger` | classes, triggers, SOQL, sharing modifiers, annotations | imports, calls, System.Label, generic type refs (`List<Account>`, `Map<Id, Contact>`) |
| Aura | `.cmp` `.app` `.evt` `.intf` `.design` | components, attributes, methods, events | controller refs, component refs |
| LWC (JavaScript) | `.js` (in LWC dirs) | anonymous class → derived from filename | `@salesforce/apex/`, `@salesforce/schema/`, `@salesforce/label/` cross-language edges |
| Visualforce | `.page` `.component` | pages, components | controller/extensions, merge fields, includes |
| SF Metadata XML | `*-meta.xml` | objects, fields, rules, layouts | Apex class refs, formula field refs, Flow actionCalls → Apex |

### Tier 2 -- Generic extraction

| Language | Extensions |
|----------|-----------|
| Ruby | `.rb` |
| C# | `.cs` |
| Kotlin | `.kt` `.kts` |
| Swift | `.swift` |
| Scala | `.scala` `.sc` |

Tier 2 languages get symbol extraction (classes, functions, methods) and basic inheritance detection via a generic tree-sitter walker. Adding a new Tier 1 language requires one file inheriting from `LanguageExtractor`.

## Performance

### Indexing Speed

Measured on the [benchmark suite](#quality-benchmark) repos (single-threaded, includes parse + resolve + metrics):

| Project | Language | Files | Symbols | Edges | Index Time | Rate |
|---------|----------|-------|---------|-------|-----------|------|
| Express | JS | 211 | 624 | 804 | 3s | 70 files/s |
| Axios | JS | 237 | 1,065 | 868 | 6s | 41 files/s |
| Vue | TS | 697 | 5,335 | 8,984 | 25s | 28 files/s |
| Laravel | PHP | 3,058 | 39,097 | 38,045 | 1m46s | 29 files/s |
| Svelte | TS | 8,445 | 16,445 | 19,618 | 2m40s | 52 files/s |

Incremental index (no changes): **<1s**. Only re-parses files with changed mtime + SHA-256 hash.

### Query Speed

All query commands complete in **<0.5s** (~0.25s Python startup + instant SQLite lookup). For comparison, an AI agent answering "what calls this function?" typically needs 5-10 tool calls at ~3-5s each. Roam answers the same question in one call.

### Large Repo Safety

All queries are batched to handle codebases with 100k+ symbols without hitting SQLite parameter limits. No configuration needed -- batching is automatic and transparent.

### Quality Benchmark

Roam ships with an automated benchmark suite (`roam-bench.py`) that indexes real-world open-source repos, measures extraction quality, and runs all commands against each repo:

| Repo | Language | Score | Coverage | Ambiguity | Edge Density | Qualified Names | Commands |
|------|----------|-------|----------|-----------|--------------|-----------------|----------|
| Laravel | PHP | **9.55** | 91.2% | 0.6% | 0.97 | 91.0% | 29/29 |
| Vue | TS | **9.27** | 85.8% | 17.2% | 1.68 | 49.5% | 29/29 |
| Svelte | TS | **9.04** | 94.7% | 57.6% | 1.19 | 25.2% | 29/29 |
| Axios | JS | **8.98** | 85.9% | 38.4% | 0.82 | 40.6% | 29/29 |
| Express | JS | **8.46** | 96.0% | 37.1% | 1.29 | 15.1% | 29/29 |

**What the metrics mean:**

- **Coverage** -- % of code files that produced at least one symbol (higher = fewer blind spots)
- **Ambiguity** -- % of symbols whose name collides with another in a different scope. Low = better qualified names
- **Edge Density** -- edges per symbol. Near 1.0 = most symbols have cross-references. Below 0.5 = sparse graph
- **Qualified Names** -- % with parent-qualified name (e.g., `Router.get` not `get`). High = more precise resolution
- **Composite score** -- weighted combination of coverage, misresolution, ambiguity, density, richness, and command pass rate

The benchmark suite supports additional repos (FastAPI, Gin, Ripgrep, Tokio, urfave/cli) across Python, Go, and Rust. Run `python roam-bench.py --help` for options.

### Token Efficiency

| Metric | Value |
|--------|-------|
| 1,600-line file → `roam file` | ~5,000 chars (~70:1 compression) |
| Full project map | ~4,000 chars |
| `--compact` mode | additional 40-50% token reduction |
| `roam preflight` replaces | 5-7 separate agent tool calls |
| `roam context` replaces | 3-5 Glob/Grep/Read cycles |

Output is plain ASCII with compact abbreviations (`fn`, `cls`, `meth`). No colors, no box-drawing, no emoji -- zero tokens wasted on decoration.

**Without Roam** (typical agent workflow to understand a symbol):

```
1. Grep for symbol name          → 1 tool call, ~2s
2. Read definition file           → 1 tool call, ~1s
3. Grep for imports of that file  → 1 tool call, ~2s
4. Read 3 caller files            → 3 tool calls, ~3s
5. Grep for test files            → 1 tool call, ~2s
6. Read test file                 → 1 tool call, ~1s
Total: 8 calls, ~11s, ~15,000 tokens consumed
```

**With Roam:**

```
$ roam context MySymbol
Total: 1 call, <0.5s, ~3,000 tokens consumed
```

## How It Works

### Indexing Pipeline

```
Source files
    |
    v
[1] Discovery ---- git ls-files (respects .gitignore)
    |
    v
[2] Parse -------- tree-sitter AST per file (16 languages)
    |
    v
[3] Extract ------ symbols (functions, classes, methods, etc.)
    |               + references (calls, imports, inheritance)
    v
[4] Resolve ------ match references to symbol definitions -> edges
    |
    v
[5] File edges --- aggregate symbol edges to file-level dependencies
    |
    v
[6] Metrics ------ PageRank, degree centrality, betweenness, cognitive complexity
    |
    v
[7] Git analysis - churn, co-change matrix, authorship, co-change entropy
    |
    v
[8] Clusters ----- Louvain community detection
    |
    v
[9] Health ------- per-file health scores (7-factor composite)
    |
    v
[10] Store ------- .roam/index.db (SQLite, WAL mode)
```

### Incremental Indexing

After the first full index, `roam index` only re-processes changed files (by mtime + SHA-256 hash). Modified files trigger edge re-resolution across the project to maintain cross-file reference integrity.

### Graph Algorithms

- **PageRank** -- identifies the most important symbols in the codebase (used by `map`, `symbol`, `search`)
- **Betweenness centrality** -- finds bottleneck symbols on many shortest paths (`health`)
- **Tarjan's SCC** -- detects dependency cycles with tangle ratio (`health`)
- **Louvain community detection** -- groups related symbols into clusters vs directory structure (`clusters`)
- **Topological sort** -- computes dependency layers and finds upward violations (`layers`)
- **k-shortest simple paths** -- traces dependency paths with coupling strength, hub detection, and quality ranking (`trace`)
- **Shannon entropy** -- measures co-change distribution (shotgun surgery) and knowledge concentration (bus factor)

### Health Scoring

Roam computes a composite health score (0-100) using five factors:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Tangle ratio | up to -30 | % of symbols in dependency cycles |
| God components | up to -20 | Symbols with extreme fan-in/fan-out |
| Bottlenecks | up to -15 | High-betweenness chokepoints |
| Layer violations | up to -15 | Upward dependency violations |
| Per-file health | up to -20 | Average of 7-factor file health scores |

Per-file health (1-10) combines: max cognitive complexity, indentation complexity, cycle membership, god component membership, dead export ratio, co-change entropy, and churn amplification.

### Storage

Everything lives in `.roam/index.db`, a single SQLite file using WAL mode for fast reads. The schema includes tables for files, symbols, edges, file edges, symbol metrics, file stats, clusters, git stats, co-change data, hypergraph edges, and metric snapshots. No external database required.

## How Roam Compares

Roam is **not** an LSP, linter, or editor plugin. It's a static index optimized for AI agents that explore codebases via shell commands.

| Tool | What it does | Difference from Roam |
|------|-------------|---------------------|
| **ctags / cscope** | Symbol index for editors | No graph metrics, no git signals, no architecture analysis, editor-focused output |
| **LSP (pyright, gopls)** | Real-time type checking + navigation | Requires running server, language-specific. API requires file:line:col -- not designed for exploratory queries |
| **Sourcegraph / Cody** | Code search + AI assistant | Proprietary since 2024. Requires hosted or self-hosted enterprise deployment |
| **Aider repo map** | Tree-sitter + PageRank map | Context selection for chat, not a standalone query tool. No git signals, no architecture commands |
| **CodeScene** | Behavioral code analysis | Commercial SaaS. Roam's health scoring is inspired by CodeScene's per-file metrics |
| **Structure101** | Dependency structure analysis | Commercial desktop app. Roam's tangle ratio concept comes from Structure101 |
| **SonarQube** | Code quality + security | Heavy server-based tool. Roam's cognitive complexity follows SonarSource spec |
| **tree-sitter CLI** | AST parsing | Raw AST only -- no symbol resolution, no cross-file edges, no metrics |
| **grep / ripgrep** | Text search | No semantic understanding -- can't distinguish definitions from usage |

Roam fills a specific gap: giving AI agents the same structural understanding a senior developer has, in a format they can consume in one turn.

## Limitations

Roam is a static analysis tool. These are fundamental trade-offs, not bugs:

- **No runtime analysis** -- can't trace dynamic dispatch, reflection, or eval'd code
- **Import resolution is heuristic** -- complex re-exports or conditional imports may not resolve correctly
- **Limited cross-language edges** -- Salesforce `@salesforce/apex/` and Flow→Apex edges are supported, but a Python file calling a C extension won't show as a dependency
- **Tier 2 languages** (Ruby, C#, Kotlin, Swift, Scala) get basic symbol extraction only (no import resolution or call tracking)
- **Large monorepos** (100k+ files) may have slow initial indexing -- incremental updates remain fast
- **Cognitive complexity** follows SonarSource spec -- other complexity models (Halstead, cyclomatic) are not included

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `roam: command not found` | Ensure install location is on PATH. For `uv`: run `uv tool update-shell`. For `pip`: check `pip show roam-code` for install path |
| `Another indexing process is running` | Previous `roam index` crashed. Delete `.roam/index.lock` and retry |
| `database is locked` or corrupt index | Run `roam index --force` to rebuild from scratch |
| Unicode errors on Windows | Ensure your terminal uses UTF-8 (`chcp 65001`) |
| `.vue` / `.svelte` files not indexed | Both are Tier 1. Ensure files have a `<script>` tag |
| Too many false positives in `roam dead` | Check the "N files had no symbols extracted" note. Files without parsers don't produce symbols |
| Symbol resolves to wrong file | Use `file:symbol` syntax: `roam symbol myfile:MyFunction` |
| Slow first index | Expected for large projects. Use `roam index --verbose`. Subsequent runs are incremental |
| Health score seems wrong | Check `roam health --json` for factor breakdown. Utility paths get relaxed thresholds |
| `--gate` not failing | Ensure the metric name matches (e.g., `score`, `tangle_pct`). Check `roam health --json` for available fields |
| Index seems stale after `git pull` | Run `roam index` (incremental, fast). After major refactors: `roam index --force` |

## Update / Uninstall

```bash
# Update to latest
pipx upgrade roam-code            # if installed with pipx
uv tool upgrade roam-code         # if installed with uv
pip install --upgrade git+https://github.com/Cranot/roam-code.git  # if installed with pip

# Uninstall
pipx uninstall roam-code          # if installed with pipx
uv tool uninstall roam-code       # if installed with uv
pip uninstall roam-code            # if installed with pip
```

To clean up project-local data, delete the `.roam/` directory from your project root.

## Development

```bash
# Clone and install in dev mode
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .

# Run tests (556 tests across 16 languages, Python 3.9-3.13)
pytest tests/

# Index roam itself
roam init
roam health
```

### Dependencies

| Package | Purpose |
|---------|---------|
| [click](https://click.palletsprojects.com/) >= 8.0 | CLI framework |
| [tree-sitter](https://github.com/tree-sitter/py-tree-sitter) >= 0.23 | AST parsing |
| [tree-sitter-language-pack](https://github.com/nicolo-ribaudo/tree-sitter-language-pack) >= 0.6 | 165+ grammars, no compilation |
| [networkx](https://networkx.org/) >= 3.0 | Graph algorithms |

Optional: [fastmcp](https://github.com/jlowin/fastmcp) (for MCP server)

### Project Structure

<details>
<summary>Click to expand</summary>

```
roam-code/
├── pyproject.toml
├── action.yml                         # Reusable GitHub Action
├── CHANGELOG.md
├── src/roam/
│   ├── __init__.py                    # Version (7.1.0)
│   ├── cli.py                         # Click CLI entry point (48 commands, 7 categories)
│   ├── mcp_server.py                  # MCP server (12 tools, 2 resources)
│   ├── db/
│   │   ├── connection.py              # SQLite connection (WAL, pragmas, batched IN helpers)
│   │   ├── schema.py                  # Tables, indexes, safe ALTER migrations
│   │   └── queries.py                 # Named SQL constants
│   ├── index/
│   │   ├── indexer.py                 # Orchestrates full pipeline
│   │   ├── discovery.py               # git ls-files, .gitignore
│   │   ├── parser.py                  # Tree-sitter parsing
│   │   ├── symbols.py                 # Symbol + reference extraction
│   │   ├── relations.py               # Reference resolution -> edges
│   │   ├── complexity.py              # Cognitive complexity (SonarSource-compatible)
│   │   ├── git_stats.py               # Churn, co-change, blame, entropy
│   │   └── incremental.py             # mtime + hash change detection
│   ├── languages/
│   │   ├── base.py                    # Abstract LanguageExtractor
│   │   ├── registry.py                # Language detection + grammar aliasing
│   │   ├── python_lang.py             # Python extractor
│   │   ├── javascript_lang.py         # JavaScript extractor
│   │   ├── typescript_lang.py         # TypeScript extractor
│   │   ├── java_lang.py               # Java extractor
│   │   ├── go_lang.py                 # Go extractor
│   │   ├── rust_lang.py               # Rust extractor
│   │   ├── c_lang.py                  # C/C++ extractor
│   │   ├── php_lang.py                # PHP extractor
│   │   ├── apex_lang.py               # Salesforce Apex extractor
│   │   ├── aura_lang.py               # Salesforce Aura extractor
│   │   ├── visualforce_lang.py        # Salesforce Visualforce extractor
│   │   ├── sfxml_lang.py              # Salesforce Metadata XML extractor
│   │   └── generic_lang.py            # Tier 2 fallback extractor
│   ├── graph/
│   │   ├── builder.py                 # DB -> NetworkX graph
│   │   ├── pagerank.py                # PageRank + centrality metrics
│   │   ├── cycles.py                  # Tarjan SCC + tangle ratio
│   │   ├── clusters.py                # Louvain community detection
│   │   ├── layers.py                  # Topological layer detection
│   │   ├── pathfinding.py             # k-shortest paths for trace
│   │   ├── split.py                   # Intra-file decomposition
│   │   └── why.py                     # Symbol role classification
│   ├── commands/
│   │   ├── resolve.py                 # Shared symbol resolution + ensure_index
│   │   ├── changed_files.py           # Shared git changeset detection
│   │   ├── metrics_history.py         # Snapshot persistence
│   │   └── cmd_*.py                   # One module per CLI command (48 total)
│   └── output/
│       ├── formatter.py               # Token-efficient text formatting + compact mode
│       └── sarif.py                   # SARIF 2.1.0 output for GitHub Code Scanning
└── tests/
    ├── test_basic.py                  # Core functionality tests
    ├── test_fixes.py                  # Regression tests
    ├── test_comprehensive.py          # Language and command tests
    ├── test_performance.py            # Performance, stress, and resilience tests
    ├── test_resolve.py                # Symbol resolution tests
    ├── test_salesforce.py             # Salesforce extractor tests
    ├── test_new_features.py           # v5-v6 feature tests
    ├── test_v7_features.py            # v7 feature tests (56 tests)
    └── test_v71_features.py           # v7.1 feature tests (67 tests)
```

</details>

## Contributing

Contributions are welcome! Here's how to get started:

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .
pytest tests/   # All 556 tests must pass before submitting
```

**Good first contributions:**

- **Add a Tier 1 language** -- create one file in `src/roam/languages/` inheriting from `LanguageExtractor`. See `go_lang.py` or `php_lang.py` as clean templates. Ruby, C#, Kotlin, Swift, and Scala are all waiting.
- **Improve reference resolution** -- better import path matching for existing languages.
- **Add a benchmark repo** -- add an entry to `roam-bench.py` and run `python roam-bench.py --repos yourrepo` to evaluate quality.
- **SARIF integration** -- extend the SARIF converters with additional analysis types.
- **MCP tools** -- add new tools to the MCP server for specialized queries.

Please open an issue first to discuss larger changes.

## License

[MIT](LICENSE)
