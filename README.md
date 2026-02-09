<div align="center">

# roam

**give your AI agent a senior developer's intuition about your codebase**

one command. instant answers. zero round-trips.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/Cranot/roam-code/actions/workflows/ci.yml/badge.svg)](https://github.com/Cranot/roam-code/actions/workflows/ci.yml)

</div>

---

Your agent burns 5 tool calls and 30 seconds to answer "what calls this function?"

Roam answers it in 0.3 seconds:

```
$ roam symbol Flask
cls  Flask
  class Flask(App)
  src/flask/app.py:76
  PR=0.0312  in=47  out=3
Callers (47):
  fn  create_app   (call)    src/flask/testing.py:18
  fn  test_config  (import)  tests/test_config.py:4
  ...
```

Roam pre-indexes your entire codebase -- symbols, call graphs, dependencies, architecture, git history -- so any question is a single shell command away.

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
| `roam module <path>` | Directory contents: exports, signatures, dependencies |
| `roam file <path> [--full]` | File skeleton: all definitions with signatures, no bodies |
| `roam symbol <name> [--full]` | Symbol definition + callers + callees + metrics |
| `roam trace <source> <target>` | Shortest dependency path between two symbols |
| `roam deps <path> [--full]` | What a file imports and what imports it |
| `roam search <pattern> [--full]` | Find symbols matching a name pattern |
| `roam grep <pattern> [-g glob] [-n N]` | Text search annotated with enclosing symbol context |
| `roam impact <symbol>` | Blast radius: what breaks if a symbol changes |
| `roam diff [--staged] [--full]` | Blast radius of uncommitted changes |

### Architecture

| Command | Description |
|---------|-------------|
| `roam health` | Cycles, god components, bottlenecks, layer violations |
| `roam clusters [--min-size N]` | Community detection vs directory structure -- hidden coupling |
| `roam layers` | Topological dependency layers + upward violations |
| `roam dead` | Unreferenced exported symbols (dead code candidates) |
| `roam fan [symbol\|file] [-n N]` | Fan-in/fan-out: most connected symbols or files |

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
=== Cycles ===
  (none)

=== God Components (degree > 20) ===
Name   Kind  Degree  File
-----  ----  ------  ------------------
Flask  cls   47      src/flask/app.py
```

Five commands, and you have a complete picture of the project: structure, key symbols, dependencies, hotspots, and architecture problems. An AI agent doing this manually would need 20+ tool calls.

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
- `roam deps <path>` -- file import/imported-by graph
- `roam trace <source> <target>` -- shortest path between symbols
- `roam search <pattern>` -- find symbols by name
- `roam grep <pattern>` -- text search with symbol context
- `roam health` -- architecture problems
- `roam weather` -- hotspots (churn x complexity)
- `roam impact <symbol>` -- blast radius (what breaks if changed)
- `roam diff` -- blast radius of uncommitted changes
- `roam owner <path>` -- code ownership (who should review)
- `roam coupling` -- temporal coupling (hidden dependencies)
- `roam fan [symbol|file]` -- fan-in/fan-out (god objects)
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
| "Show me this file's structure" | `roam file <path>` | Read the file directly |
| "Find a specific string in code" | `roam grep <pattern>` | `grep` / `rg` (same speed, less context) |
| "Understand project architecture" | `roam map` + `roam health` | Manual exploration (many tool calls) |
| "What breaks if I change X?" | `roam impact <symbol>` | No direct equivalent |

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
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | classes, functions, arrow functions, exports | imports, require, calls | extends |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | interfaces, type aliases, enums + all JS | imports, calls, type refs | extends, implements |
| Java | `.java` | classes, interfaces, enums, constructors, fields | imports, calls | extends, implements |
| Go | `.go` | structs, interfaces, functions, methods, fields | imports, calls | embedded structs |
| Rust | `.rs` | structs, traits, impls, enums, functions | use, calls | impl Trait for Struct |
| C | `.c` `.h` | structs, functions, typedefs, enums | includes, calls | -- |
| C++ | `.cpp` `.hpp` `.cc` `.hh` | classes, namespaces, templates + all C | includes, calls | extends |
| Vue | `.vue` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |

### Tier 2 -- Generic extraction

| Language | Extensions |
|----------|-----------|
| Ruby | `.rb` |
| PHP | `.php` |
| C# | `.cs` |
| Kotlin | `.kt` `.kts` |
| Swift | `.swift` |
| Scala | `.scala` `.sc` |

Tier 2 languages get symbol extraction (classes, functions, methods) and basic inheritance detection via a generic tree-sitter walker. Adding a new Tier 1 language requires one file inheriting from `LanguageExtractor`.

## Performance

Benchmarked on [Flask](https://github.com/pallets/flask) (226 files, 1,132 symbols):

| Operation | Time | Notes |
|-----------|------|-------|
| Full index (`roam index --force`) | ~5s | Parses all files, computes graph metrics, analyzes git history |
| Incremental index (no changes) | <1s | Hash check only |
| Any query command | <0.5s | ~0.25s startup + instant query from pre-computed SQLite |

For comparison, an AI agent answering "what calls this function?" typically needs 5-10 tool calls at ~3-5 seconds each. Roam answers the same question in one call under 0.5s.

**Token efficiency:** A 1,600-line source file summarized in ~5,000 characters (~70:1 compression). Full project map in ~4,000 characters. Output is plain ASCII with compact abbreviations (`fn`, `cls`, `meth`) -- no tokens wasted on colors, box-drawing, or decorative formatting.

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
- **Shortest path** -- traces dependency chains between any two symbols (`trace`)

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
- **Tier 2 languages** get basic symbol extraction only (no import resolution or call tracking)
- **Large monorepos** (100k+ files) may have slow initial indexing -- incremental updates remain fast

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `roam: command not found` | Ensure install location is on PATH. For `uv`: run `uv tool update-shell`. For `pip`: check `pip show roam-code` for install path. |
| `Another indexing process is running` | A previous `roam index` crashed and left a stale lock. Delete `.roam/index.lock` and retry. |
| `database is locked` or corrupt index | Run `roam index --force` to rebuild from scratch. |
| Unicode errors on Windows | Roam handles UTF-8 and Latin-1 files. If you see `charmap` errors, ensure your terminal uses UTF-8 (`chcp 65001`). |
| `.vue` / `.svelte` files not indexed | Vue SFC is supported (Tier 1). Other frameworks need script extraction support -- file an issue. |
| Too many false positives in `roam dead` | Check the "N files had no symbols extracted" note. Files without parsers don't produce symbols, so their exports appear unreferenced. |
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

# Run tests (~195 tests across 14 languages, 3 OS, Python 3.10-3.13)
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
│   ├── cli.py                      # Click CLI entry point (20 commands)
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
│   │   ├── typescript_lang.py      # TypeScript extractor
│   │   ├── java_lang.py            # Java extractor
│   │   ├── go_lang.py              # Go extractor
│   │   ├── rust_lang.py            # Rust extractor
│   │   ├── c_lang.py               # C/C++ extractor
│   │   └── generic_lang.py         # Tier 2 fallback extractor
│   ├── graph/
│   │   ├── builder.py              # DB -> NetworkX graph
│   │   ├── pagerank.py             # PageRank + centrality metrics
│   │   ├── cycles.py               # Tarjan SCC detection
│   │   ├── clusters.py             # Louvain community detection
│   │   ├── layers.py               # Topological layer detection
│   │   └── pathfinding.py          # Shortest path for trace
│   ├── commands/                    # One module per CLI command
│   └── output/
│       └── formatter.py            # Token-efficient text formatting
└── tests/
    ├── test_basic.py               # Core functionality tests
    ├── test_fixes.py               # Regression tests
    ├── test_comprehensive.py       # Language and command tests
    └── test_performance.py         # Performance, stress, and resilience tests
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

- **Add a Tier 1 language** -- create one file in `src/roam/languages/` inheriting from `LanguageExtractor`. See `go_lang.py` as a clean template.
- **Improve reference resolution** -- better import path matching for existing languages.
- **Add output formats** -- JSON output mode for programmatic consumption.

Please open an issue first to discuss larger changes.

## License

[MIT](LICENSE)
