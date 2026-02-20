# CLAUDE.md — roam-code development guide

## What this project is

roam-code is a CLI tool that gives AI coding agents instant codebase comprehension.
It pre-indexes symbols, call graphs, dependencies, architecture, and git history into
a local SQLite DB. 94 commands, 26 languages, 100% local, zero API keys.

**Package:** `roam-code` on PyPI. Entry point: `roam.cli:cli`.

## Quick reference

```bash
# Run tests (parallel by default, 4 workers)
pytest tests/

# Run tests sequentially (for debugging)
pytest tests/ -n 0

# Skip timing-sensitive perf tests
pytest tests/ -m "not slow"

# Run a single test file
pytest tests/test_comprehensive.py -x -v -n 0

# Install in dev mode
pip install -e .

# Index roam itself
roam init
roam health
```

## Architecture

### Directory layout

```
src/roam/
  cli.py              # Click CLI entry point — LazyGroup, _COMMANDS dict, _CATEGORIES
  mcp_server.py       # FastMCP server (48 tools, 2 resources)
  __init__.py          # Version string (reads from pyproject.toml via importlib.metadata)
  db/
    schema.py          # SQLite schema (CREATE TABLE statements)
    connection.py      # open_db(), ensure_schema(), batched_in(), migrations
    queries.py         # Named SQL constants
  index/
    indexer.py         # Full pipeline: discovery → parse → extract → resolve → metrics → health → cognitive load
    discovery.py       # git ls-files, .gitignore
    parser.py          # Tree-sitter parsing
    symbols.py         # Symbol + reference extraction
    relations.py       # Reference resolution → edges
    complexity.py      # Cognitive complexity (SonarSource-compatible)
    git_stats.py       # Churn, co-change, blame, entropy
    incremental.py     # mtime + hash change detection
    file_roles.py      # Smart file role classifier (source, test, config, docs, etc.)
    test_conventions.py # Pluggable test naming adapters (Python, Go, JS, Java, Ruby, Apex)
  bridges/
    base.py            # Abstract LanguageBridge — cross-language symbol resolution
    registry.py        # Bridge auto-discovery + detection
    bridge_salesforce.py # Apex → Aura/LWC/Visualforce bridge
    bridge_protobuf.py # .proto → Go/Java/Python stubs bridge
    bridge_rest_api.py # Frontend HTTP calls → backend route definitions
    bridge_template.py # Jinja2/Django/ERB/Handlebars variable + include resolution
    bridge_config.py   # Env var reads → .env/.yml definitions
  catalog/
    tasks.py           # Universal algorithm catalog — 23 tasks with ranked solution approaches
    detectors.py       # Algorithm anti-pattern detectors — query DB signals to find suboptimal patterns
  languages/
    base.py            # Abstract LanguageExtractor — all languages inherit this
    registry.py        # Language detection + grammar aliasing
    *_lang.py          # One file per language (python, javascript, typescript, java, go, rust, c, csharp, php, ruby, foxpro, apex, aura, visualforce, sfxml, hcl, yaml, generic)
  graph/
    builder.py         # DB → NetworkX graph
    pagerank.py        # PageRank + centrality metrics
    cycles.py          # Tarjan SCC + tangle ratio
    clusters.py        # Louvain community detection
    layers.py          # Topological layer detection — returns {node_id: layer_number}
    pathfinding.py     # k-shortest paths for trace
    split.py           # Intra-file decomposition
    why.py             # Symbol role classification
    anomaly.py         # Statistical anomaly detection (Modified Z-Score, Theil-Sen, Mann-Kendall, CUSUM)
    simulate.py        # Counterfactual architecture simulation (graph cloning + transforms)
    partition.py       # Multi-agent work partitioning (Louvain-based)
    fingerprint.py     # Topology fingerprinting + comparison
  search/
    tfidf.py           # Zero-dependency TF-IDF semantic search
    index_embeddings.py # Symbol corpus + cosine similarity
  security/
    vuln_store.py      # Vulnerability ingestion (npm/pip/trivy/osv audit)
    vuln_reach.py      # Reachability analysis from vuln → entry points
  runtime/
    trace_ingest.py    # OpenTelemetry/Jaeger/Zipkin trace ingestion
    hotspots.py        # Runtime hotspot classification (UPGRADE/CONFIRMED/DOWNGRADE)
  refactor/
    codegen.py         # Import generation (Python/JS/Go)
    transforms.py      # move/rename/add-call/extract symbol transforms
  commands/
    resolve.py         # Shared symbol resolution + ensure_index()
    changed_files.py   # Shared git changeset detection
    gate_presets.py    # Framework-specific gate rules + .roam-gates.yml loader
    graph_helpers.py   # Shared graph utilities (adjacency builders, BFS helpers)
    context_helpers.py # Data-gathering helpers extracted from cmd_context.py
    cmd_*.py           # One module per CLI command (93 modules, 94 commands)
  output/
    formatter.py       # Token-efficient text formatting, abbrev_kind(), loc(), format_table(), to_json(), json_envelope()
    sarif.py           # SARIF 2.1.0 output (--sarif flag on health/debt/complexity)
    schema_registry.py # JSON envelope schema versioning + validation
tests/                 # 70 test files
  # Core & legacy
  test_basic.py, test_comprehensive.py, test_fixes.py, test_performance.py,
  test_resolve.py, test_salesforce.py, test_v6_features.py,
  test_v7_features.py, test_v71_features.py, test_v82_features.py,
  test_workspace.py, test_visualize.py, test_foxpro.py,
  # Organized command tests
  test_commands_exploration.py, test_commands_health.py, test_commands_architecture.py,
  test_commands_workflow.py, test_commands_refactoring.py,
  # Feature-specific
  test_smoke.py, test_json_contracts.py, test_formatters.py, test_languages.py,
  test_anomaly.py, test_file_roles.py, test_pr_risk_author.py, test_dead_aging.py,
  test_bridges.py, test_bridges_extended.py, test_test_conventions.py, test_gate_presets.py,
  test_python_extractor_v2.py, test_math.py, test_properties.py, test_index.py,
  # v9.1 new commands
  test_simulate.py, test_orchestrate.py, test_fingerprint.py, test_mutate.py,
  test_adversarial.py, test_plan.py, test_cut.py, test_invariants.py,
  test_bisect.py, test_intent.py, test_closure.py, test_rules.py,
  test_vuln.py, test_runtime.py, test_relate.py, test_semantic_search.py,
  test_schema_versioning.py, test_sarif_flag.py, test_ruby.py, test_yaml_hcl.py,
  test_dark_matter.py, test_effects.py, test_effects_propagation.py,
  test_capsule.py, test_forecast.py, test_path_coverage.py,
  test_minimap.py, test_attest.py, test_annotations.py, test_budget.py,
  test_pr_diff.py, test_framework_detection.py, test_backend_fixes_round2.py,
  test_backend_fixes_round3.py, test_exclude_patterns.py, test_math_tips.py
```

### Key patterns

- **Lazy-loading commands:** `cli.py` uses a `LazyGroup` that imports command modules only when invoked. This avoids importing networkx (~500ms) on every CLI call. Register new commands in `_COMMANDS` dict and `_CATEGORIES` dict.

- **Command template:** Every command follows this pattern:
  ```python
  from __future__ import annotations  # required for Python 3.9 compat
  import click
  from roam.db.connection import open_db
  from roam.output.formatter import to_json, json_envelope
  from roam.commands.resolve import ensure_index

  @click.command()
  @click.pass_context
  def my_cmd(ctx):
      json_mode = ctx.obj.get('json') if ctx.obj else False
      ensure_index()
      with open_db(readonly=True) as conn:
          # ... query the DB ...
          if json_mode:
              click.echo(to_json(json_envelope("my-cmd",
                  summary={"verdict": "...", ...},
                  ...
              )))
              return
          # Text output
          click.echo("VERDICT: ...")
  ```

- **`from __future__ import annotations`** — Required at top of every file for Python 3.9 compatibility.

- **Batched IN-clauses:** Never write raw `WHERE id IN (...)` with a list > 400 items. Use `batched_in()` from `connection.py` instead.

- **`detect_layers()` returns `{node_id: layer_number}`** — a dict, not a list of sets. Convert if you need per-layer groupings.

- **Verdict-first output:** Key commands emit a one-line `VERDICT:` as the first text output line and include `verdict` in the JSON summary.

- **JSON envelope:** All JSON output uses `json_envelope(command_name, summary={...}, **data)`. The summary dict should include a `verdict` field. Envelopes automatically include `schema` and `schema_version` fields.

- **SARIF output:** Health/debt/complexity commands support `--sarif` flag for CI integration (GitHub Code Scanning, etc.).

## Conventions

- **Functions:** `snake_case` (100%)
- **Classes:** `PascalCase` (100%)
- **Methods:** `snake_case` (100%)
- **Imports:** Absolute imports for cross-directory; `from __future__ import annotations` at top of every source file
- **Test files:** `test_*.py` in `tests/`
- **Output abbreviations:** `fn` (function), `cls` (class), `meth` (method) — via `abbrev_kind()`
- **No emojis, no colors, no box-drawing** in output — plain ASCII only for token efficiency

## Adding a new CLI command

1. Create `src/roam/commands/cmd_yourcommand.py` following the command template above
2. Register in `cli.py` → `_COMMANDS` dict: `"your-command": ("roam.commands.cmd_yourcommand", "your_command")`
3. Add to appropriate category in `_CATEGORIES` dict
4. Add MCP tool wrapper in `mcp_server.py` if useful for agents
5. Add tests

## Adding a new language (Tier 1)

1. Create `src/roam/languages/yourlang_lang.py` inheriting from `LanguageExtractor`
2. See `go_lang.py` or `php_lang.py` as clean templates
3. Register in `registry.py`
4. Add tests in `tests/`

## Schema changes

1. Add column in `schema.py` (CREATE TABLE)
2. Add migration in `connection.py` → `ensure_schema()` using `_safe_alter()`
3. Populate in `indexer.py` pipeline

## Testing

- All tests must pass before committing (run `pytest tests/` to verify)
- **Parallel by default:** pytest-xdist runs auto workers (`-n auto --dist loadgroup`)
- Use `-n 0` to run sequentially when debugging
- Use `-m "not slow"` to skip timing-sensitive performance tests
- Tests create temporary project directories with fixture files
- Use `CliRunner` from Click for command tests
- Run full suite: `pytest tests/`
- Run specific: `pytest tests/test_comprehensive.py::TestHealth -x -v -n 0`
- Mark tests needing sequential execution with `@pytest.mark.xdist_group("groupname")`

## Dependencies

- click >= 8.0 (CLI framework)
- tree-sitter >= 0.23 (AST parsing)
- tree-sitter-language-pack >= 0.6 (165+ grammars)
- networkx >= 3.0 (graph algorithms)
- Optional: fastmcp (MCP server)
- Dev: pytest >= 7.0, pytest-xdist >= 3.0, ruff >= 0.4

## Version bumping

Update **one place only**: `pyproject.toml` → `version`

`__init__.py` reads it dynamically via `importlib.metadata`. README badge pulls from PyPI.

## Codebase navigation with roam

This project uses `roam` for codebase comprehension. Always prefer roam over Glob/Grep/Read exploration.

Before modifying any code:
1. First time in the repo: `roam understand` then `roam tour`
2. Find a symbol: `roam search <pattern>`
3. Before changing a symbol: `roam preflight <name>` (blast radius + tests + fitness)
4. Need files to read: `roam context <name>` (files + line ranges, prioritized)
5. Debugging a failure: `roam diagnose <name>` (root cause ranking)
6. After making changes: `roam diff` (blast radius of uncommitted changes)

Additional commands: `roam health` (0-100 score), `roam impact <name>` (what breaks),
`roam pr-risk` (PR risk score), `roam file <path>` (file skeleton),
`roam simulate move <sym> <file>` (what-if architecture), `roam orchestrate` (multi-agent partitioning),
`roam adversarial` (attack surface review), `roam mutate move <sym> <file>` (code transforms).

Run `roam --help` for all 94 commands. Use `roam --json <cmd>` for structured output.
Use `roam --sarif health` for CI integration (SARIF 2.1.0).
