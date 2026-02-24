# Contributing to roam-code

Thank you for your interest in contributing to roam-code! This document covers
everything you need to get started.

## Quick Start

1. Fork the repository
2. Clone your fork: `git clone https://github.com/<you>/roam-code.git`
3. Install in development mode: `pip install -e ".[mcp,dev]"`
4. Run tests: `pytest tests/`
5. Create a branch, make changes, submit a PR

## Development Setup

### Prerequisites

- Python 3.9+
- Git

### Installation

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e ".[dev]"      # core + pytest, pytest-xdist, ruff
pip install -e ".[mcp,dev]"  # also includes fastmcp for MCP server work
```

### Running Tests

```bash
# Full test suite
pytest tests/

# Parallel execution (faster, requires pytest-xdist)
pytest tests/ -n auto

# Skip timing-sensitive performance tests
pytest tests/ -m "not slow"

# Single test file
pytest tests/test_comprehensive.py -x -v

# Single test class or method
pytest tests/test_comprehensive.py::TestHealth -x -v -n 0

# Sequential execution (useful for debugging)
pytest tests/ -n 0
```

All tests must pass before submitting a PR.

### Linting

```bash
ruff check src/ tests/
```

The project uses ruff with `target-version = "py39"` and `line-length = 100`.
Selected rule sets: E, F, W, I (pyflakes, pycodestyle, isort).

### Code Style

- **Functions and methods:** `snake_case`
- **Classes:** `PascalCase`
- **Imports:** Absolute imports for cross-directory references
- **Future annotations:** Every source file must start with `from __future__ import annotations` (required for Python 3.9 compatibility)
- **Output format:** Plain ASCII only -- no emojis, no colors, no box-drawing characters. This keeps output token-efficient for LLM consumption.
- **Output abbreviations:** `fn` (function), `cls` (class), `meth` (method) -- via `abbrev_kind()`

See [CLAUDE.md](CLAUDE.md) for the complete conventions reference.

## Pre-commit Hooks

roam-code ships a `.pre-commit-hooks.yaml` so you can run roam checks as
[pre-commit](https://pre-commit.com/) hooks in any project that has roam-code
installed.

Add the following to your project's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/Cranot/roam-code
    rev: v10.0.1          # pin to a release tag
    hooks:
      - id: roam-secrets        # secret scanning -- no index required
      - id: roam-syntax-check   # tree-sitter syntax validation -- no index required
      - id: roam-verify         # convention consistency check
      - id: roam-health         # composite health score (informational)
```

Available hook IDs and what they do:

| Hook ID | Command | Fails on | Index required? |
|---|---|---|---|
| `roam-secrets` | `roam secrets --fail-on-found` | Any secret found | No |
| `roam-syntax-check` | `roam syntax-check --changed` | Syntax errors | No |
| `roam-verify` | `roam verify --changed` | Score < 70 | Yes (auto-init) |
| `roam-health` | `roam health` | Never (informational) | Yes (auto-init) |
| `roam-vibe-check` | `roam vibe-check` | Never by default | Yes (auto-init) |

Notes:
- `roam-secrets` and `roam-syntax-check` operate directly on files and work
  without a pre-existing roam index.
- `roam-verify`, `roam-health`, and `roam-vibe-check` call `ensure_index()`
  internally and will auto-index the project on first run (equivalent to
  `roam init`).
- All hooks use `pass_filenames: false` and `always_run: true` because roam
  operates on the whole repository rather than individual files.
- To enforce a health threshold in CI, use the `gate` input of the
  [GitHub Action](docs/ci-integration.md) rather than `roam-health` alone.
- To enable the `--threshold` gate on `roam-vibe-check`, override the hook
  args in your config:
  ```yaml
  - id: roam-vibe-check
    args: ['--threshold', '50']
  ```

## How to Contribute

### Reporting Bugs

Use the [Bug Report](https://github.com/Cranot/roam-code/issues/new?template=bug_report.yml) issue template. Please include:

- roam-code version (`roam --version`)
- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Actual vs expected output

### Suggesting Features

Use the [Feature Request](https://github.com/Cranot/roam-code/issues/new?template=feature_request.yml) issue template. Explain the use case and why it matters.

### Submitting Code

#### Adding a New CLI Command

1. Create `src/roam/commands/cmd_yourcommand.py` following the command template:

   ```python
   from __future__ import annotations
   import click
   from roam.db.connection import open_db
   from roam.output.formatter import to_json, json_envelope
   from roam.commands.resolve import ensure_index

   @click.command()
   @click.pass_context
   def your_command(ctx):
       json_mode = ctx.obj.get('json') if ctx.obj else False
       ensure_index()
       with open_db(readonly=True) as conn:
           # ... query the DB ...
           if json_mode:
               click.echo(to_json(json_envelope("your-command",
                   summary={"verdict": "...", ...},
                   ...
               )))
               return
           # Text output
           click.echo("VERDICT: ...")
   ```

2. Register in `cli.py`:
   - Add to `_COMMANDS` dict: `"your-command": ("roam.commands.cmd_yourcommand", "your_command")`
   - Add to the appropriate category in `_CATEGORIES` dict

3. Add an MCP tool wrapper in `mcp_server.py` if the command would be useful for AI agents

4. Add tests in `tests/`

#### Adding a New Language (Tier 1)

1. Create `src/roam/languages/yourlang_lang.py` inheriting from `LanguageExtractor`
2. Use `go_lang.py` or `php_lang.py` as clean templates
3. Register in `src/roam/languages/registry.py`
4. Add tests in `tests/`

#### Schema Changes

1. Add column in `src/roam/db/schema.py` (CREATE TABLE statements)
2. Add migration in `src/roam/db/connection.py` via `ensure_schema()` using `_safe_alter()`
3. Populate the new column in `src/roam/index/indexer.py`

### Key Patterns to Follow

- **Verdict-first output:** Key commands should emit a one-line `VERDICT:` as the first text output and include `verdict` in the JSON summary.
- **JSON envelope:** All JSON output uses `json_envelope(command_name, summary={...}, **data)`.
- **Batched IN-clauses:** Never write raw `WHERE id IN (...)` with a list > 400 items. Use `batched_in()` from `connection.py`.
- **Lazy-loading:** Commands are lazy-loaded via `LazyGroup` in `cli.py` to avoid importing networkx on every CLI call.

See [CLAUDE.md](CLAUDE.md) for the full list of patterns and conventions.

## PR Guidelines

- One feature or fix per PR
- Include tests for new functionality
- All tests must pass (`pytest tests/`)
- Follow existing code conventions
- Please open an issue first to discuss larger changes

## Testing Tips

- Tests create temporary project directories with fixture files
- Use `CliRunner` from Click for command tests
- Mark tests that need sequential execution with `@pytest.mark.xdist_group("groupname")`
- Use `-m "not slow"` to skip timing-sensitive performance tests during development

## Architecture Overview

roam-code is organized into these key areas:

| Directory | Purpose |
|-----------|---------|
| `src/roam/cli.py` | Click CLI entry point with lazy-loaded commands |
| `src/roam/commands/` | One `cmd_*.py` module per CLI command |
| `src/roam/db/` | SQLite schema, connection management, queries |
| `src/roam/index/` | Indexing pipeline: discovery, parsing, extraction, resolution |
| `src/roam/languages/` | One `*_lang.py` per language, inheriting `LanguageExtractor` |
| `src/roam/graph/` | NetworkX graph algorithms (PageRank, SCC, clustering, layers) |
| `src/roam/bridges/` | Cross-language symbol resolution |
| `src/roam/output/` | Formatting, JSON envelopes, SARIF output |
| `src/roam/mcp_server.py` | MCP server with 61 tools |
| `tests/` | Test suite (71 test files) |

For full architectural details, see [CLAUDE.md](CLAUDE.md).

## Good First Contributions

- Add a Tier 1 language extractor (see `go_lang.py` or `php_lang.py` as templates)
- Improve reference resolution for an existing language
- Add benchmark repos to the test suite
- Extend SARIF converters
- Add MCP tool wrappers for existing commands
- Improve documentation

## Need Help?

- Open an [issue](https://github.com/Cranot/roam-code/issues) for questions
- Check [existing issues](https://github.com/Cranot/roam-code/issues) before creating new ones
- See [CLAUDE.md](CLAUDE.md) for detailed technical conventions
