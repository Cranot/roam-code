# Changelog

## v7.0.0

Major release: Composite health scoring, SARIF output, MCP server, guided onboarding, and deep enhancements across 8 existing commands. Shifts roam from single-metric health to multi-factor CodeScene-inspired analysis.

### New Commands (3)

- **`roam init`** — Guided project onboarding. Creates `.roam/fitness.yaml` with starter rules, generates `.github/workflows/roam.yml` CI workflow, runs initial index, and shows health summary. One command to go from zero to full analysis.
- **`roam digest`** — Compare current metrics against the most recent snapshot. Shows deltas with directional arrows (improvement/regression), `--brief` for one-line summary, `--since <tag>` for specific comparisons. Generates actionable recommendations.
- **`roam describe --agent-prompt`** — Compact agent-oriented project summary under 500 tokens. Returns project name, stack, conventions, key abstractions, hotspots, health score, and test command in a format optimized for LLM system prompts.

### New Modules

- **SARIF 2.1.0 output** (`src/roam/output/sarif.py`) — Static Analysis Results Interchange Format for GitHub code scanning. Converters for: dead code, complexity, fitness violations, health issues, breaking changes, and naming conventions. Upload to GitHub Advanced Security for inline PR annotations.
- **MCP server** (`src/roam/mcp_server.py`) — Model Context Protocol server exposing 12 roam tools (`understand`, `health`, `preflight`, `search_symbol`, `context`, `trace`, `impact`, `file_info`, `pr_risk`, `breaking_changes`, `affected_tests`, `dead_code`, `complexity_report`, `repo_map`) plus 2 resources (`roam://health`, `roam://summary`). Run with `fastmcp run roam.mcp_server:mcp`.
- **GitHub Action** (`action.yml`) — Reusable composite action for CI. Installs roam, indexes, runs analysis, and posts/updates PR comments with results. Supports `command`, `comment`, `fail-on-violation`, and `roam-version` inputs.

### Composite Health Scoring

- **Multi-factor project health score (0-100)** — Replaces the old cycles-only formula with a composite that weights: tangle ratio (up to -30), god components (up to -20), bottlenecks (up to -15), layer violations (up to -15), and average per-file health (up to -20). Much more accurate for real-world codebases.
- **Tangle ratio** — Percentage of symbols involved in dependency cycles (Structure101 concept). Displayed in health output: `Tangle: 5.2% (42/800 symbols in cycles)`.
- **Per-file health score (1-10)** — CodeScene-inspired 7-factor composite per file: max cognitive complexity, file-level indentation complexity, cycle membership, god component membership, dead export ratio, co-change entropy, and churn amplification. Stored in `file_stats.health_score`.
- **Co-change entropy** — Shannon entropy of co-change distribution per file. High entropy = shotgun surgery. Stored in `file_stats.cochange_entropy`.

### Correctness Fixes

- **elif complexity scoring** — Fixed critical bug where `elif`/`else`/`case` chains inflated cognitive complexity ~3x vs SonarSource spec. Continuation nodes now get +1 flat (no nesting penalty), matching SonarQube behavior exactly.

### Enhanced Commands (8)

- **`roam describe --agent-prompt`** — Compact agent-oriented output mode (under 500 tokens) with project overview, stack, conventions, key abstractions, hotspots, and health.
- **`roam fitness --explain`** — Show `reason` and `link` fields for each rule. Rules can now include `reason:` and `link:` in `.roam/fitness.yaml`.
- **`roam file <path1> <path2> ...`** — Multi-file mode with `--changed` (show all uncommitted changed files) and `--deps-of PATH` (show file + all its imports).
- **`roam context --for-file PATH`** — File-level context: callers grouped by source file, callees grouped by target file, tests, coupling partners, and complexity summary.
- **`roam bus-factor --brain-methods`** — Brain method detection (cc >= 25 AND line_count >= 50) and Shannon entropy contribution analysis with knowledge risk labels.
- **`roam health`** — Now shows composite score with tangle ratio in both text and JSON output.
- **`roam snapshot` / `roam trend`** — Store and track tangle_ratio, avg_complexity, and brain_methods in snapshot history.

### CLI Infrastructure

- **`--compact` flag** — Token-efficient output mode across all commands. TSV tables (40-50% fewer tokens) and minimal JSON envelope (strips version/timestamp/project). For AI agent pipelines where every token counts.
- **`--gate EXPR`** — CI quality gate expressions (e.g., `roam health --gate score>=70`). Supports `>=`, `<=`, `>`, `<`, `=` operators.
- **Categorized `--help`** — Progressive disclosure: 48 commands organized into 7 categories (Getting Started, Daily Workflow, Codebase Health, Architecture, Exploration, Reports & CI, Refactoring) instead of flat alphabetical list.

### Schema & Migrations

- `file_stats.health_score REAL` — Per-file health score (1-10)
- `file_stats.cochange_entropy REAL` — Shannon entropy of co-change partners
- `snapshots.tangle_ratio REAL` — % of symbols in cycles at snapshot time
- `snapshots.avg_complexity REAL` — Average cognitive complexity at snapshot time
- `snapshots.brain_methods INTEGER` — Count of brain methods at snapshot time
- Safe ALTER TABLE migrations via `_safe_alter()` for existing databases

### Testing

- Comprehensive v7 tests covering: SARIF module, init, digest, describe --agent-prompt, fitness reason/link, multi-file mode, context --for-file, bus-factor entropy/brain-methods, compact mode, gate expressions, categorized help, elif fix, per-file health, composite health, tangle ratio
- 56 new v7 feature tests
- **489 total tests passing**

## v6.0.0

Major release: 15 new intelligence commands, cognitive complexity analysis, architectural fitness functions, and compound agent-friendly operations. Shifts roam from descriptive ("what the code looks like") to prescriptive ("how to work with it").

### New Commands (15)

- **`roam complexity`** — Per-function cognitive complexity metrics with multi-factor scoring (nesting, boolean ops, callback depth, params). Includes `--bumpy-road` mode for files with many moderate-complexity functions.
- **`roam conventions`** — Auto-detect implicit codebase patterns: naming styles (snake_case/camelCase/PascalCase), file organization, import preferences, export patterns. Flags outliers.
- **`roam debt`** — Hotspot-weighted technical debt prioritization. Combines complexity, churn, cycle membership, and god components. Code in hotspots costs 15x more — focus refactoring here.
- **`roam fitness`** — Architectural fitness function runner. Define rules in `.roam/fitness.yaml` for dependency constraints, metric thresholds, and naming conventions. Returns exit code 1 for CI integration.
- **`roam preflight`** — Compound pre-change safety check. Combines blast radius + affected tests + complexity + coupling + conventions + fitness into one call. Reduces agent round-trips by 60-70%.
- **`roam affected-tests`** — Trace from changed symbol/file through reverse call graph to test files. Classifies as DIRECT/TRANSITIVE/COLOCATED. Outputs runnable `pytest` command with `--command`.
- **`roam entry-points`** — Entry point catalog with protocol classification (HTTP, CLI, Event, Scheduled, Message, Main, Export). Shows fan-out and reachability coverage per entry point.
- **`roam safe-zones`** — Graph-based containment boundary analysis. Shows ISOLATED/CONTAINED/EXPOSED zones, internal symbols safe to change, and boundary symbols requiring contract maintenance.
- **`roam patterns`** — Architectural pattern recognition: Strategy, Factory, Observer, Repository, Middleware, Decorator. Detects patterns from graph structure and naming.
- **`roam bus-factor`** — Knowledge loss risk per module. Tracks author concentration, recency, and bus factor. Flags single-point-of-failure directories.
- **`roam breaking`** — Breaking change detection between git refs. Finds removed exports, signature changes, and renamed symbols.
- **`roam alerts`** — Health degradation trend detection from snapshot history. Fires CRITICAL/WARNING/INFO alerts for threshold violations and worsening trends.
- **`roam fn-coupling`** — Function-level temporal coupling. Finds symbols that co-change across files without direct edges — hidden dependencies.
- **`roam doc-staleness`** — Detect stale docstrings where code body changed long after documentation was last updated.
- **`roam complexity --bumpy-road`** — Files where many functions are individually moderate but collectively hard to maintain.

### Enhanced Commands

- **`roam understand`** — Now includes conventions, complexity overview (avg/critical/high), pattern summary, and debt hotspots alongside existing sections.
- **`roam describe`** — Generates CLAUDE.md with Coding Conventions and Complexity Hotspots sections for agents to follow.
- **`roam context --task`** — Task-aware context mode: `refactor|debug|extend|review|understand` tailors output to agent intent. Refactor shows coupling + blast radius; debug shows execution flow; extend shows conventions to follow.
- **`roam map --budget N`** — Token-budget-aware repo map. Constrains output to N tokens using PageRank ranking.
- **`roam diff --tests --coupling --fitness`** — Enhanced diff with affected tests, coupling warnings for missing co-change partners, and fitness rule checks. `--full` enables all three.

### Infrastructure

- **`symbol_metrics` table** — Per-function complexity data stored during indexing: cognitive_complexity, nesting_depth, param_count, line_count, return_count, bool_op_count, callback_depth.
- **Cognitive complexity module** (`src/roam/index/complexity.py`) — Tree-sitter AST-based complexity analysis. Walks control flow nodes with nesting penalties (SonarSource-inspired).
- **Fitness YAML config** — `.roam/fitness.yaml` for user-defined architectural rules. Supports dependency, metric, and naming rule types.

### Testing

- 62 new v6 feature tests (conventions, complexity, debt, preflight, fitness, patterns, safe-zones, entry-points, alerts, bus-factor, fn-coupling, doc-staleness, task-context, enhanced understand/describe)
- 17 new performance benchmarks for v6 commands
- **393 total tests passing**

## v5.0.0

Major release: 6 new commands, Salesforce language support, hypergraph co-change analysis, and defensive hardening across all commands.

### New Commands

- **`roam understand`** — Single-call codebase comprehension for AI agents. Returns tech stack, architecture overview, key abstractions, health summary, and entry points in one shot. Designed for LLM context priming.
- **`roam coverage-gaps`** — Find unprotected entry points with no path to a required gate (auth, permission, etc.). BFS reachability from entry points to gate symbols.
- **`roam snapshot`** — Persist a timestamped health metrics snapshot to the index DB. Use `--tag` to label milestones.
- **`roam trend`** — Display health score history with sparkline visualization. Supports `--assert "metric<=N"` for CI quality gates.
- **`roam report`** — Run compound presets (first-contact, security, pre-pr, refactor) that execute multiple commands in one shot. Supports `--list` and custom preset names.
- **`roam coupling --set`** — Set-mode coupling analysis against changesets with hypergraph surprise scoring.

### Enhanced Commands

- **`roam dead`** — Added `--summary`, `--by-kind`, `--clusters` flags for grouped dead-code analysis and cluster detection.
- **`roam context`** — Batch mode: pass multiple symbols to get shared callers and merged context.
- **`roam risk`** — Added `--explain` flag with full BFS chain reasoning showing why symbols are risky.
- **`roam grep`** — Added `--source-only`, `--exclude`, `--test-only` filters.
- **`roam coupling`** — Added `--staged` changeset mode and hypergraph surprise score.
- **`roam pr-risk`** — Added hypergraph novelty factor for change-pattern analysis.

### Salesforce Support

- **Grammar aliasing infrastructure** — Languages without dedicated tree-sitter grammars can piggyback on existing ones (apex -> java, aura/visualforce/sfxml -> html).
- **Apex extractor** (`.cls`, `.trigger`) — Extends Java extractor with SOQL query refs, System.Label refs, sharing modifiers, trigger declarations, and Salesforce annotations.
- **Aura extractor** (`.cmp`, `.app`, `.evt`, `.intf`, `.design`) — Component structure, attribute/method/event symbols, controller refs, custom component refs.
- **Visualforce extractor** (`.page`) — Page symbols, controller/extensions refs, merge field scanning.
- **SF Metadata XML extractor** (`*-meta.xml`) — Object/field/class metadata, formula scanning, sidecar deduplication.
- **Cross-language edge resolution** — `@salesforce/apex/`, `@salesforce/schema/`, `@salesforce/label/` import paths resolve across language boundaries.

### Infrastructure

- **JSON envelope contract** — All `--json` output now wrapped in `json_envelope(command, summary, **payload)` with consistent `command`, `timestamp`, `summary` fields across all 34 commands.
- **Hypergraph co-change tables** — N-ary commit patterns stored in `git_hyperedges`/`git_hyperedge_files` for surprise scoring.
- **Shared `changed_files` utility** — Extracted common `--staged`/`--diff`/`--branch` file detection into reusable module.
- **Metrics history** — `metrics_snapshots` table for trend tracking with `append_snapshot()` API.
- **Defensive hardening** — `.get()` safety patterns, `or 0` null-safe arithmetic, and empty-result guards across all commands and graph metrics.

### Testing

- 27 Salesforce-specific tests covering all 4 extractors, grammar aliasing, and project-level indexing.
- 20 integration tests for new commands (understand, dead enhancements, context batch, snapshot, trend, coverage-gaps, report, JSON envelope).
- 72 performance tests including indexing benchmarks, query latency, stress tests, and self-benchmarks on roam-code itself.

### Performance (self-benchmark on roam-code, ~140 files)

| Command | Latency |
|---------|---------|
| understand | ~800ms |
| health | ~770ms |
| layers | ~780ms |
| map | ~200ms |
| dead | ~220ms |
| coupling | ~220ms |
| weather | ~190ms |

All commands under 1s on a real-world Python project.
