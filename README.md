<div align="center">
<pre>
██████   ██████   █████  ███    ███
██   ██ ██    ██ ██   ██ ████  ████
██████  ██    ██ ███████ ██ ████ ██
██   ██ ██    ██ ██   ██ ██  ██  ██
██   ██  ██████  ██   ██ ██      ██
</pre>

**codebase intelligence for AI**

v4.3.1 · 29 commands · one pre-built index · instant answers

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/Cranot/roam-code/actions/workflows/ci.yml/badge.svg)](https://github.com/Cranot/roam-code/actions/workflows/ci.yml)

</div>

---

Your AI agent shouldn't need 10 tool calls to understand a codebase. Roam pre-indexes everything -- symbols, call graphs, dependencies, architecture, git history -- so any question is one shell command away.

```bash
$ roam index                     # build once (~5s), then incremental
$ roam symbol Flask              # definition + 47 callers + 3 callees + PageRank
$ roam context Flask             # AI-ready: files-to-read with exact line ranges
$ roam deps src/flask/app.py     # imports + imported-by with symbol breakdown
$ roam impact create_app         # 34 symbols break if this changes
$ roam split src/flask/app.py    # internal groups with isolation % + extraction suggestions
$ roam why Flask                 # role, reach, criticality, one-line verdict
$ roam risk                      # domain-weighted risk ranking of all symbols
$ roam health                    # cycles, god components, bottlenecks + summary
$ roam pr-risk HEAD~3..HEAD      # 0-100 risk score + dead exports + reviewers
$ roam diff                      # blast radius of your uncommitted changes
```

## Table of Contents

- [Install](#install)
- [Quick Start](#quick-start)
- [Commands](#commands)
- [Walkthrough: Investigating a Codebase](#walkthrough-investigating-a-codebase)
- [Integration with AI Coding Tools](#integration-with-ai-coding-tools)
- [Language Support](#language-support)
- [Performance](#performance)
- [How It Works](#how-it-works)
- [How Roam Compares](#how-roam-compares)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Update / Uninstall](#update--uninstall)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Install

```bash
# Recommended for CLI tools (isolated environment)
pipx install git+https://github.com/Cranot/roam-code.git

# Or with uv (fastest)
uv tool install git+https://github.com/Cranot/roam-code.git

# Or with pip
pip install git+https://github.com/Cranot/roam-code.git
```

> **Note:** Roam is not yet published to PyPI. Install from source as shown above, or clone and `pip install -e .` for development.

Verify the install:

```bash
roam --version
```

> **Windows:** If `roam` is not found after installing with `uv`, run `uv tool update-shell` and restart your terminal so the tool directory is on PATH.

Requires Python 3.9+. Works on Linux, macOS, and Windows. Best with `git` installed (for file discovery and history analysis; falls back to directory walking without it). No external services, no API keys, no configuration.

## Quick Start

```bash
cd your-project

# Add the index directory to .gitignore
echo ".roam/" >> .gitignore

# Build the index (runs once, then incremental)
roam index

# Get a project overview
roam map

# Explore a file's structure
roam file src/main.py

# Find a symbol and its connections
roam symbol MyClass

# See what depends on a file
roam deps src/utils.py

# Find architecture problems
roam health
```

> **First index:** Expect ~5s for a 200-file project, ~15s for 1,000 files. Subsequent runs are incremental and near-instant. Use `roam index --verbose` to see detailed progress.

The index is stored at `.roam/index.db` in your project root. Run `roam --help` to see all available commands.

<details>
<summary><strong>Try it on Roam itself</strong></summary>

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .
roam index --force
roam map
roam symbol Indexer
roam health
```

</details>

## Commands

### Navigation

| Command | Description |
|---------|-------------|
| `roam index [--force] [--verbose]` | Build or rebuild the codebase index |
| `roam map [-n N] [--full]` | Project skeleton: files, languages, entry points, top symbols by PageRank |
| `roam module <path>` | Directory contents: exports, signatures, dependencies, cohesion rating |
| `roam file <path> [--full]` | File skeleton: all definitions with signatures, no bodies |
| `roam symbol <name> [--full]` | Symbol definition + callers + callees + metrics. Supports `file:symbol` syntax for disambiguation (e.g., `roam symbol app:Flask`) |
| `roam context <symbol>` | AI-optimized context: definition + callers + callees + files-to-read with line ranges (PageRank-capped) |
| `roam trace <source> <target> [-k N]` | Dependency paths between two symbols with coupling strength and quality scoring. Shows up to k paths (default 3) with edge-kind labels, coupling classification (strong/moderate/weak), hub detection (high-degree intermediates flagged), and path quality ranking. Paths sorted by quality, not just length |
| `roam deps <path> [--full]` | What a file imports and what imports it |
| `roam search <pattern> [--kind KIND] [--full]` | Find symbols by name pattern — PageRank-ranked with signatures |
| `roam grep <pattern> [-g glob] [-n N]` | Text search annotated with enclosing symbol context |
| `roam impact <symbol>` | Blast radius: what breaks if a symbol changes |
| `roam split <file>` | Analyze a file's internal structure: symbol groups, isolation %, cross-group coupling, extraction suggestions |
| `roam risk [-n N] [--domain KW]` | Domain-weighted risk ranking using three-source matching: symbol name keywords, callee-chain analysis (up to 3 hops with decay), and file path-zone matching. UI files auto-dampened (except zone-matched symbols — zone overrides UI dampening). Tuned keyword weights to reduce false positives from ambiguous terms. Configurable via `.roam/domain-weights.json` and `.roam/path-zones.json` |
| `roam why <name> [name2 ...]` | Explain why a symbol matters: role classification (Core utility/Hub/Bridge/Leaf/Internal), transitive reach, critical path, cluster, one-line verdict. Batch mode for triage |
| `roam safe-delete <symbol>` | Check if a symbol can be safely deleted — SAFE/REVIEW/UNSAFE verdict with reasoning |
| `roam diff [--staged] [--full] [REV_RANGE]` | Blast radius of uncommitted changes or a commit range (e.g., `HEAD~3..HEAD`) |
| `roam pr-risk [REV_RANGE]` | PR risk score (0-100) + new dead exports + suggested reviewers |
| `roam describe [--write] [--force]` | Auto-generate project description (CLAUDE.md) from the index — domain-aware keyword extraction |
| `roam test-map <name>` | Map a symbol or file to its test coverage |
| `roam sketch <dir> [--full]` | Compact structural skeleton of a directory (API surface) |

### Architecture

| Command | Description |
|---------|-------------|
| `roam health [--no-framework]` | Cycles, god components, bottlenecks, layer violations — location-aware severity. Utility paths (composables/, utils/, services/) get relaxed thresholds (3x) for both god components and bottlenecks. Both categorized as "actionable" vs "utility". Cycle severity is directory-aware (single-dir cycles capped at INFO). `--no-framework` filters framework primitives |
| `roam clusters [--min-size N]` | Community detection vs directory structure — cohesion %, coupling matrices, split suggestions for mega-clusters |
| `roam layers` | Topological dependency layers + directory breakdown per layer + upward violations |
| `roam dead [--all]` | Unreferenced exported symbols with SAFE/REVIEW/INTENTIONAL verdicts and reason column. Lifecycle hooks (onMounted, componentDidMount, etc.) auto-classified as INTENTIONAL |
| `roam fan [symbol\|file] [-n N] [--no-framework]` | Fan-in/fan-out: most connected symbols or files (`--no-framework` filters Vue/React primitives) |

### Git Signals

| Command | Description |
|---------|-------------|
| `roam weather [-n N]` | Hotspots ranked by churn x complexity |
| `roam owner <path>` | Code ownership: who owns a file or directory |
| `roam coupling [-n N]` | Temporal coupling: file pairs that change together |

### Inheritance

| Command | Description |
|---------|-------------|
| `roam uses <name>` | Find all classes that extend, implement, or use a symbol |

### Global Options

| Option | Description |
|--------|-------------|
| `roam --json <command>` | Output structured JSON instead of human-readable tables. Works on all 29 commands. |
| `roam --version` | Show version |

```bash
# Examples
roam --json health          # {"cycles": [...], "god_components": [...], "actionable_count": 5, ...}
roam --json symbol Flask    # {"name": "Flask", "callers": [...], "callees": [...]}
roam --json why Flask         # {"symbols": [{"name": "Flask", "role": "Hub", "reach": 89, ...}]}
roam --json diff HEAD~3..HEAD  # {"changed_files": 11, "affected_symbols": 405, ...}
```

## Walkthrough: Investigating a Codebase

Here's how you'd use Roam to understand a project you've never seen before. Using Flask as an example:

**Step 1: Index and get the lay of the land**

```
$ roam index
Done. 226 files, 1132 symbols, 233 edges.

$ roam map
Files: 226  Symbols: 1132  Edges: 233
Languages: python=83, javascript=2, css=1

Directories:
dir           files
------------  -----
src/flask     42
tests         31
docs          18

Entry points:
  src/flask/__init__.py

Top symbols (PageRank):
kind  name               signature                           location                      PR
----  -----------------  ----------------------------------  ----------------------------  ------
cls   Flask              class Flask(App)                    src/flask/app.py:76           0.0312
fn    url_for            def url_for(endpoint, ...)          src/flask/helpers.py:108      0.0201
cls   Blueprint          class Blueprint(...)                src/flask/blueprints.py:24    0.0188
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

**Step 5: Check for architecture problems**

```
$ roam health
Health: 1 issue — 1 WARNING
  (1 god component (1 actionable, 0 expected utilities))

=== Cycles ===
  (none)

=== God Components (degree > 20) ===
Sev      Name   Kind  Degree  Cat  File
-------  -----  ----  ------  ---  ------------------
WARNING  Flask  cls   47      act  src/flask/app.py
```

God components and bottlenecks in utility paths (`composables/`, `utils/`, `services/`, etc.) get relaxed thresholds (3x) and are categorized separately, so infrastructure symbols don't drown out genuinely problematic code.

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

`roam context` gives an AI agent exactly the files and line ranges it needs to safely modify a symbol — no more, no less. Output is capped by PageRank to avoid flooding context with low-value files.

**Step 7: Decompose a large file**

```
$ roam split src/flask/app.py
=== Split analysis: src/flask/app.py ===
  87 symbols, 42 internal edges, 95 external edges
  Cross-group coupling: 18%

  Group 1 (routing) — 12 symbols, 15 internal / 3 cross / 8 external edges (isolation: 83%) [extractable]
    meth  route              L411  PR=0.0088
    meth  add_url_rule       L450  PR=0.0045
    meth  endpoint           L510  PR=0.0022
    ...

  Group 2 (request) — 8 symbols, 10 internal / 2 cross / 12 external edges (isolation: 80%) [extractable]
    meth  make_response      L742  PR=0.0067
    meth  process_response   L780  PR=0.0034
    ...

=== Extraction Suggestions ===
  Extract 'routing' group: route, add_url_rule, endpoint (+9 more)
    83% isolated, only 3 edges to other groups
```

`roam split` analyzes the internal symbol graph of a file and identifies groups with high isolation — natural seams for extraction into separate modules or composables.

**Step 8: Understand why a symbol matters**

```
$ roam why Flask url_for Blueprint
Symbol     Role          Fan         Reach     Risk      Verdict
---------  ------------  ----------  --------  --------  --------------------------------------------------
Flask      Hub           fan-in:47   reach:89  CRITICAL  God symbol (47 in, 12 out). Consider splitting.
url_for    Core utility  fan-in:31   reach:45  HIGH      Widely used utility (31 callers). Stable interface.
Blueprint  Bridge        fan-in:18   reach:34  moderate  Coupling point between clusters.
```

`roam why` gives a quick assessment of a symbol's architectural role — is it a core utility, a hub, a bridge, or a leaf? Batch mode lets you triage multiple symbols in one call, ideal for deciding modification order or reviewing dead code lists.

**Step 9: Generate a CLAUDE.md for your team**

```
$ roam describe --write
Wrote CLAUDE.md (98 lines)
```

`roam describe` reads the index and writes a project overview that any AI tool can consume — entry points, directory structure, key conventions, test commands. Think of it as a machine-readable onboarding doc generated in one command.

Nine commands, and you have a complete picture of the project: structure, key symbols, dependencies, hotspots, architecture problems, AI-ready context, file decomposition guidance, and a shareable project description. An AI agent doing this manually would need 20+ tool calls.

## Integration with AI Coding Tools

Roam is designed to be called by AI coding agents via shell commands. Instead of multiple Glob/Grep/Read cycles, the agent runs one `roam` command and gets structured, token-efficient output.

Add the following instructions to your AI tool's configuration file:

```markdown
## Codebase navigation

Use `roam` CLI for codebase comprehension (pre-installed).
Run `roam index` once, then use these commands instead of Glob/Grep/Read exploration:

- `roam map` -- project overview, entry points, key symbols
- `roam file <path>` -- file skeleton with all definitions
- `roam symbol <name>` -- definition + callers + callees
- `roam context <name>` -- AI context: definition + callers + callees + files-to-read with line ranges
- `roam deps <path>` -- file import/imported-by graph
- `roam trace <source> <target>` -- dependency paths with coupling strength, hub detection, quality ranking
- `roam search <pattern>` -- find symbols by name (PageRank-ranked with signatures)
- `roam grep <pattern>` -- text search with symbol context
- `roam health` -- architecture problems with location-aware severity (utility paths get relaxed thresholds for god components and bottlenecks, `--no-framework` filters primitives)
- `roam weather` -- hotspots (churn x complexity)
- `roam impact <symbol>` -- blast radius (what breaks if changed)
- `roam split <file>` -- internal symbol groups with isolation % and extraction suggestions
- `roam risk` -- domain-weighted risk ranking (3-source matching: name + callee-chain + path-zone, UI auto-dampened except zone matches)
- `roam why <name>` -- role, reach, criticality, verdict (batch: `roam why A B C`)
- `roam safe-delete <symbol>` -- check if safe to delete (SAFE/REVIEW/UNSAFE verdict)
- `roam diff` -- blast radius of uncommitted changes
- `roam diff HEAD~3..HEAD` -- blast radius of a commit range
- `roam pr-risk HEAD~3..HEAD` -- PR risk score (0-100) + dead exports + reviewers
- `roam owner <path>` -- code ownership (who should review)
- `roam coupling` -- temporal coupling (hidden dependencies)
- `roam fan [symbol|file]` -- fan-in/fan-out (`--no-framework` to filter primitives)
- `roam dead` -- unreferenced exports with SAFE/REVIEW/INTENTIONAL verdicts
- `roam uses <name>` -- all consumers: callers, importers, inheritors
- `roam clusters` -- code communities with cohesion %, coupling matrices, split suggestions
- `roam layers` -- dependency layers with directory breakdown
- `roam describe` -- auto-generate CLAUDE.md from the index
- `roam test-map <name>` -- map symbols/files to test coverage
- `roam sketch <dir>` -- structural skeleton of a directory

Use `roam --json <command>` for structured JSON output (all 29 commands).
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
| "What calls this function?" | `roam symbol <name>` | LSP / Grep (if Roam not indexed) |
| "What files do I need to read?" | `roam context <name>` | Manual symbol tracing (5+ tool calls) |
| "Show me this file's structure" | `roam file <path>` | Read the file directly |
| "Find a specific string in code" | `roam grep <pattern>` | `grep` / `rg` (same speed, less context) |
| "Understand project architecture" | `roam map` + `roam health` | Manual exploration (many tool calls) |
| "What breaks if I change X?" | `roam impact <symbol>` | No direct equivalent |
| "How should I split this large file?" | `roam split <file>` | Manual reading + guessing (error-prone, slow) |
| "What's the riskiest code?" | `roam risk` | Manual review (no domain weighting, no callee-chain analysis) |
| "Why does this symbol exist?" | `roam why <name>` | `roam context` + `roam impact` + `roam fan` (3 commands) |
| "Can I safely delete this?" | `roam safe-delete <symbol>` | `roam dead` + manual grep (slow, error-prone) |
| "Review blast radius of a PR" | `roam pr-risk HEAD~3..HEAD` | Manual `git diff` + tracing (slow, incomplete) |
| "Who should review this file?" | `roam owner <path>` | `git log --follow` (raw data, no aggregation) |
| "Generate project docs for AI" | `roam describe --write` | Write manually |
| "Which tests cover this symbol?" | `roam test-map <name>` | Grep for imports (misses indirect coverage) |

### When to rebuild the index

- **After `git pull` / branch switch:** `roam index` (incremental, fast)
- **After major refactor or first clone:** `roam index --force` (full rebuild)
- **Index seems stale or corrupt:** `roam index --force`
- **No rebuild needed:** Roam auto-detects changed files on every `roam index` run

## Language Support

### Tier 1 -- Full extraction (dedicated parsers)

| Language | Extensions | Symbols | References | Inheritance |
|----------|-----------|---------|------------|-------------|
| Python | `.py` `.pyi` | classes, functions, methods, decorators, variables | imports, calls, inheritance | extends, `__all__` exports |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | classes, functions, arrow functions, CJS exports (`exports.X`, `module.exports={}`, prototype methods) | imports, require(), calls | extends |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | interfaces, type aliases, enums + all JS | imports, calls, type refs | extends, implements |
| Java | `.java` | classes, interfaces, enums, constructors, fields | imports, calls | extends, implements |
| Go | `.go` | structs, interfaces, functions, methods, fields | imports, calls | embedded structs |
| Rust | `.rs` | structs, traits, impls, enums, functions | use, calls | impl Trait for Struct |
| C | `.c` `.h` | structs, functions, typedefs, enums | includes, calls | -- |
| C++ | `.cpp` `.hpp` `.cc` `.hh` | classes, namespaces, templates + all C | includes, calls | extends |
| PHP | `.php` | classes, interfaces, traits, enums, methods, properties, constants, constructor promotion | namespace use, calls, static calls, nullsafe calls (`?->`), `new` | extends, implements, use (traits) |
| Vue | `.vue` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |
| Svelte | `.svelte` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |

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

### Quality Benchmark

Roam ships with an automated benchmark suite (`roam-bench.py`) that indexes real-world open-source repos, measures extraction quality, and runs all 29 commands against each repo:

| Repo | Language | Score | Coverage | Ambiguity | Edge Density | Qualified Names | Commands |
|------|----------|-------|----------|-----------|--------------|-----------------|----------|
| Laravel | PHP | **9.55** | 91.2% | 0.6% | 0.97 | 91.0% | 29/29 |
| Vue | TS | **9.27** | 85.8% | 17.2% | 1.68 | 49.5% | 29/29 |
| Svelte | TS | **9.04** | 94.7% | 57.6% | 1.19 | 25.2% | 29/29 |
| Axios | JS | **8.98** | 85.9% | 38.4% | 0.82 | 40.6% | 29/29 |
| Express | JS | **8.46** | 96.0% | 37.1% | 1.29 | 15.1% | 29/29 |

**Per-language average:**

| Language | Avg Score | Repos |
|----------|-----------|-------|
| PHP | 9.55 | Laravel |
| TypeScript | 9.15 | Vue, Svelte |
| JavaScript | 8.72 | Express, Axios |

**What the metrics mean:**

- **Coverage** -- % of code files that produced at least one symbol (higher = fewer blind spots)
- **Ambiguity** -- % of symbols whose name collides with another symbol in a different scope. Low ambiguity means the extractor captures enough context (parent class, module, namespace) to distinguish identically-named symbols
- **Edge Density** -- edges per symbol. Values near 1.0 mean most symbols have at least one cross-reference. Below 0.5 means the graph is sparse and many relationships are missing
- **Qualified Names** -- % of symbols with a parent-qualified name (e.g., `Router.get` instead of `get`). High values reduce ambiguity and enable more precise resolution
- **Composite score** -- weighted combination of coverage (×2), misresolution rate (×2.5), ambiguity, density (×1.5), graph richness, and command pass rate (×1.5)

The benchmark suite supports additional repos (FastAPI, Gin, Ripgrep, Tokio, urfave/cli) across Python, Go, and Rust. Run `python roam-bench.py --help` for options.

### Token Efficiency

A 1,600-line source file summarized in ~5,000 characters (~70:1 compression). Full project map in ~4,000 characters. Output is plain ASCII with compact abbreviations (`fn`, `cls`, `meth`) -- no tokens wasted on colors, box-drawing, or decorative formatting.

## How It Works

### Indexing Pipeline

```
Source files
    |
    v
[1] Discovery ---- git ls-files (respects .gitignore)
    |
    v
[2] Parse -------- tree-sitter AST per file
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
[6] Metrics ------ PageRank, degree centrality, betweenness
    |
    v
[7] Git analysis - churn, co-change matrix, authorship
    |
    v
[8] Clusters ----- Louvain community detection
    |
    v
[9] Store -------- .roam/index.db (SQLite, WAL mode)
```

### Incremental Indexing

After the first full index, `roam index` only re-processes changed files (by mtime + SHA-256 hash). Modified files trigger edge re-resolution across the project to maintain cross-file reference integrity.

### Graph Algorithms

- **PageRank** -- identifies the most important symbols in the codebase (used by `map`, `symbol`)
- **Betweenness centrality** -- finds bottleneck symbols that sit on many shortest paths (`health`)
- **Tarjan's SCC** -- detects dependency cycles (`health`)
- **Louvain community detection** -- groups related symbols into clusters, compared against directory structure to find hidden coupling (`clusters`)
- **Topological sort** -- computes dependency layers and finds upward violations (`layers`)
- **k-shortest simple paths** -- traces up to k dependency paths between any two symbols with coupling strength classification, hub detection, and quality-based ranking (`trace`)

### Storage

Everything lives in `.roam/index.db`, a single SQLite file using WAL mode for fast reads. The schema includes 10 tables with 20+ indexes. No external database required.

### Output Design

Roam's output is optimized for AI token consumption:

- **Plain ASCII** -- no colors, no box-drawing, no emoji
- **Compact abbreviations** -- `fn`, `cls`, `meth`, `var`, `iface`, `const`, `struct`
- **File:line format** -- `src/main.py:42` for every location
- **Budget-aware** -- sections truncate with `(+N more)` when exceeding line budgets
- **Table formatting** -- aligned columns for scannable output

## How Roam Compares

Roam is **not** an LSP, linter, or editor plugin. It's a static index optimized for AI agents that explore codebases via shell commands.

| Tool | What it does | Difference from Roam |
|------|-------------|---------------------|
| **ctags / cscope** | Symbol index for editors | No graph metrics, no git signals, no architecture analysis, editor-focused output |
| **LSP (pyright, gopls, etc.)** | Real-time type checking + navigation | Requires running server, language-specific. Now integrated into AI agents (Claude Code, etc.) but API requires file:line:col -- not designed for exploratory queries |
| **Sourcegraph / Cody** | Code search + AI assistant | Proprietary since 2024. Cody adds structural indexing but requires hosted or self-hosted enterprise deployment |
| **Aider repo map** | Tree-sitter + PageRank map for AI | Context selection for chat, not a standalone query tool. No git signals, no architecture commands |
| **tree-sitter CLI** | AST parsing | Raw AST only -- no symbol resolution, no cross-file edges, no metrics |
| **grep / ripgrep** | Text search | No semantic understanding -- can't distinguish definitions from usage, no graph |

Roam fills a specific gap: giving AI agents the same structural understanding a senior developer has, in a format they can consume in one turn.

## Limitations

Roam is a static analysis tool. These are fundamental trade-offs, not bugs:

- **No runtime analysis** -- can't trace dynamic dispatch, reflection, or eval'd code
- **Import resolution is heuristic** -- complex re-exports or conditional imports may not resolve correctly
- **No cross-language edges** -- a Python file calling a C extension won't show as a dependency
- **Tier 2 languages** (Ruby, C#, Kotlin, Swift, Scala) get basic symbol extraction only (no import resolution or call tracking)
- **Large monorepos** (100k+ files) may have slow initial indexing -- incremental updates remain fast

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `roam: command not found` | Ensure install location is on PATH. For `uv`: run `uv tool update-shell`. For `pip`: check `pip show roam-code` for install path. |
| `Another indexing process is running` | A previous `roam index` crashed and left a stale lock. Delete `.roam/index.lock` and retry. |
| `database is locked` or corrupt index | Run `roam index --force` to rebuild from scratch. |
| Unicode errors on Windows | Roam handles UTF-8 and Latin-1 files. If you see `charmap` errors, ensure your terminal uses UTF-8 (`chcp 65001`). |
| `.vue` / `.svelte` files not indexed | Both are Tier 1 (script block extraction). Ensure files have a `<script>` tag. Other SFC frameworks -- file an issue. |
| Too many false positives in `roam dead` | Check the "N files had no symbols extracted" note. Files without parsers don't produce symbols, so their exports appear unreferenced. |
| Symbol resolves to wrong file | Use `file:symbol` syntax: `roam symbol myfile:MyFunction` to disambiguate. |
| Vue template functions show fan-in:0 | Rebuild the index with `roam index --force` (v4.3.1 fixed multi-line attribute detection + callback references + shorthand properties). |
| Slow first index | Expected for large projects. Use `roam index --verbose` to monitor progress. Subsequent runs are incremental. |

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

# Run tests (~276 tests across 16 languages, 3 OS, Python 3.10-3.13)
pytest tests/

# Index roam itself
roam index --force
roam map
```

### Dependencies

| Package | Purpose |
|---------|---------|
| [click](https://click.palletsprojects.com/) >= 8.0 | CLI framework |
| [tree-sitter](https://github.com/tree-sitter/py-tree-sitter) >= 0.23 | AST parsing |
| [tree-sitter-language-pack](https://github.com/nicolo-ribaudo/tree-sitter-language-pack) >= 0.6 | 165+ grammars, no compilation |
| [networkx](https://networkx.org/) >= 3.0 | Graph algorithms |

### Project Structure

<details>
<summary>Click to expand</summary>

```
roam-code/
├── pyproject.toml
├── src/roam/
│   ├── cli.py                      # Click CLI entry point (29 commands)
│   ├── db/
│   │   ├── connection.py           # SQLite connection (WAL, pragmas)
│   │   ├── schema.py               # 10 tables, 20+ indexes
│   │   └── queries.py              # Named SQL constants
│   ├── index/
│   │   ├── indexer.py              # Orchestrates full pipeline
│   │   ├── discovery.py            # git ls-files, .gitignore
│   │   ├── parser.py               # Tree-sitter parsing
│   │   ├── symbols.py              # Symbol + reference extraction
│   │   ├── relations.py            # Reference resolution -> edges
│   │   ├── git_stats.py            # Churn, co-change, blame
│   │   └── incremental.py          # mtime + hash change detection
│   ├── languages/
│   │   ├── base.py                 # Abstract LanguageExtractor
│   │   ├── registry.py             # Language detection + factory
│   │   ├── python_lang.py          # Python extractor
│   │   ├── javascript_lang.py      # JavaScript extractor
│   │   ├── typescript_lang.py      # TypeScript/Vue/Svelte extractor
│   │   ├── java_lang.py            # Java extractor
│   │   ├── go_lang.py              # Go extractor
│   │   ├── rust_lang.py            # Rust extractor
│   │   ├── c_lang.py               # C/C++ extractor
│   │   ├── php_lang.py             # PHP extractor
│   │   └── generic_lang.py         # Tier 2 fallback extractor
│   ├── graph/
│   │   ├── builder.py              # DB -> NetworkX graph
│   │   ├── pagerank.py             # PageRank + centrality metrics
│   │   ├── cycles.py               # Tarjan SCC detection
│   │   ├── clusters.py             # Louvain community detection
│   │   ├── layers.py               # Topological layer detection
│   │   ├── pathfinding.py          # Shortest path for trace
│   │   └── split.py                # Intra-file decomposition analysis
│   │   └── why.py                  # Symbol role classification + verdict
│   ├── commands/
│   │   ├── resolve.py              # Shared symbol resolution + ensure_index
│   │   └── cmd_*.py                # One module per CLI command (29 total)
│   └── output/
│       └── formatter.py            # Token-efficient text formatting
└── tests/
    ├── test_basic.py               # Core functionality tests
    ├── test_fixes.py               # Regression tests
    ├── test_comprehensive.py       # Language and command tests
    ├── test_performance.py         # Performance, stress, and resilience tests
    └── test_resolve.py             # Symbol resolution + line_start attribution tests
```

</details>

## Contributing

Contributions are welcome! Here's how to get started:

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .
pytest tests/   # All tests must pass before submitting
```

**Good first contributions:**

- **Add a Tier 1 language** -- create one file in `src/roam/languages/` inheriting from `LanguageExtractor`. See `go_lang.py` or `php_lang.py` as clean templates. Ruby, C#, Kotlin, Swift, and Scala are all waiting.
- **Improve reference resolution** -- better import path matching for existing languages.
- **Add a benchmark repo** -- add an entry to `roam-bench.py` and run `python roam-bench.py --repos yourrepo` to evaluate quality.

Please open an issue first to discuss larger changes.

## License

[MIT](LICENSE)
