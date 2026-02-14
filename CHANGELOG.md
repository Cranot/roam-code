# Changelog

## v8.1.1

Deep Python extractor improvements based on Pyan, PyCG, and Scalpel research.

### Python Extractor

- **Instance attribute extraction** -- `self.x = value` assignments in `__init__` now produce property symbols. Detects self-name from first parameter (Pyan-inspired, not hardcoded to `self`). Recurses into `if`/`try`/`with` blocks. Deduplicates with class-level properties.
- **Assignment type annotation references** -- Class fields (`path: Path`), module variables (`cache: Dict[str, Config]`), and instance attributes (`self.x: List[Item] = []`) with type annotations now create `type_ref` edges.
- **Forward reference support** -- String annotations like `Optional["Config"]` and `"module.ClassName"` now produce `type_ref` edges. Validates that string content is a valid identifier before creating references.

### Testing

- 1691 tests across 29 test files (up from 1664 across 28)
- New test file: `test_python_extractor_v2.py` (27 tests covering instance attrs, self-name detection, deduplication, assignment type refs, forward refs)

## v8.1.0

Self-analysis driven improvements: ran roam on itself and fixed every issue it surfaced.

### Bug Fixes

- **Fixed `complexity` command crash** -- `roam complexity` crashed with `IndexError` on databases missing v7.4 columns (`cyclomatic_density`, `halstead_*`). Now uses defensive `_safe_metric()` accessor with graceful fallback.
- **Fixed CHANGELOG v8.0.0** -- Incorrectly listed `roam anomalies` as a standalone command. The anomaly detection shipped as `roam trend --analyze` (with `--anomalies`, `--forecast`, `--fail-on-anomaly`, `--sensitivity`).

### Improved Analysis

- **Smarter health scoring** -- Expanded utility path detection to recognize `output/`, `db/`, `common/`, `internal/`, `infra/` directories and utility files (`resolve.py`, `helpers.py`, `base.py`). Shared infrastructure symbols are now properly categorized as expected utilities instead of false god-component alerts.
- **95% fewer dead code false positives** -- Test files (`test_*.py`) now excluded from dead export analysis. ABC method overrides, CLI command functions, and dynamically-loaded symbols correctly marked as intentional. Total false reports dropped from 668 to ~131.
- **Python extractor: decorator references** -- `@decorator` and `@module.decorator(args)` now create reference edges, enabling accurate decorator dependency tracking.
- **Python extractor: type annotation references** -- Function parameter types (`x: SomeClass`), return types (`-> Result`), and generic type arguments (`List[Item]`) now create `type_ref` edges. Builtin types (`int`, `str`, etc.) are excluded.

## v8.0.1

Project organization and code quality improvements.

### Refactoring

- **Extracted `graph_helpers.py`** -- Deduplicated BFS/adjacency code from 4 command files (`cmd_coverage_gaps`, `cmd_entry_points`, `cmd_safe_zones`, `cmd_context`) into shared `build_forward_adj`, `build_reverse_adj`, `bfs_reachable`, `bfs_nx` helpers.
- **Split `cmd_context.py`** -- Extracted 13 data-gathering functions into `context_helpers.py`, reducing cmd_context.py from 1,622 to 1,022 lines.
- **Renamed `test_new_features.py`** to `test_v6_features.py` for naming consistency.

### Infrastructure

- Added Python 3.9 to CI matrix (matches `requires-python = ">=3.9"`).
- Added `[tool.pytest.ini_options]`, `[tool.ruff]`, and `[project.optional-dependencies]` dev extras to pyproject.toml.
- Added `Makefile` with install, dev, test, lint, format, build, publish, clean targets.
- Moved `roam-bench.py` to `dev/` directory.

## v8.0.0

Major release: anomaly detection, file role classification, dead code aging, cross-language bridges, gate presets, test convention adapters, and massive test suite expansion.

### New Capabilities

- **Anomaly detection** (`src/roam/graph/anomaly.py`) -- Statistical anomaly detection using Modified Z-Score, Theil-Sen regression, Mann-Kendall trend test, and CUSUM change-point detection. Surfaces outlier symbols and files across multiple metrics.
- **File role classifier** (`src/roam/index/file_roles.py`) -- Smart classifier that assigns roles (source, test, config, docs, build, generated, etc.) to files based on path patterns, naming conventions, and content heuristics.
- **Dead code aging** -- Dead exports now include git blame age data, showing how long dead code has been accumulating. Helps prioritize cleanup by staleness.
- **Cross-language bridges** (`src/roam/bridges/`) -- Abstract `LanguageBridge` infrastructure for resolving symbols across language boundaries. Includes Salesforce bridge (Apex to Aura/LWC/Visualforce) and Protobuf bridge (.proto to Go/Java/Python stubs).
- **Gate presets** (`src/roam/commands/gate_presets.py`) -- Framework-specific gate rules for `coverage-gaps`. Built-in presets for Python, JavaScript, Go, Java, and Rust. Custom rules via `.roam-gates.yml`.
- **Test convention adapters** (`src/roam/index/test_conventions.py`) -- Pluggable test naming adapters for Python, Go, JavaScript, Java, Ruby, and Apex. Improves test discovery in `test-map` and `impact` commands.

### Enhanced Commands

- **`roam trend --analyze`** -- Full anomaly analysis: Modified Z-Score outlier detection, Theil-Sen trend estimation, Mann-Kendall significance testing, CUSUM change-point detection, and linear forecasting. Also available as `--anomalies`, `--forecast`, `--fail-on-anomaly`, `--sensitivity=[low|medium|high]`.

### Testing

- **1656 total tests passing** (up from 669 in v7.5.0)
- New test files: `test_anomaly.py`, `test_file_roles.py`, `test_pr_risk_author.py`, `test_dead_aging.py`, `test_bridges.py`, `test_test_conventions.py`, `test_gate_presets.py`

### Infrastructure

- 12 research-backed math improvements across core analysis modules (v7.5.0)
- Enhanced PR risk scoring with author experience factor

## v7.4.0

Multi-repo workspace support: group sibling repos, detect cross-repo REST API connections, and run unified analysis commands.

### Multi-Repo Workspace

- **`roam ws init`** -- Initialize a workspace from multiple repo directories. Auto-detects frontend/backend roles from package.json, composer.json, etc. Creates `.roam-workspace.json` config and `.roam-workspace/workspace.db` overlay DB.
- **`roam ws status`** -- Show workspace repos with file/symbol counts, index ages, and cross-repo edge count.
- **`roam ws resolve`** -- Scan frontend repos for API calls (axios, fetch, useFetch) and backend repos for route definitions (Laravel `Route::get`, Express `router.get`, FastAPI `@app.get`). Normalize URL patterns, match by path + HTTP method, store as cross-repo edges.
- **`roam ws understand`** -- Unified workspace overview: per-repo stats (files, symbols, languages, key symbols by PageRank) + cross-repo connection summary.
- **`roam ws health`** -- Workspace-wide health report: per-repo health scores, cross-repo coupling assessment (low/moderate/high).
- **`roam ws context <symbol>`** -- Cross-repo augmented context: find a symbol across all repos, show callers/callees within each repo, plus cross-repo API edges.
- **`roam ws trace <source> <target>`** -- Trace cross-repo paths: find symbols in their respective repos, show API bridge edges connecting them.

### Architecture

- **Federated DB** -- Each repo keeps its own `.roam/index.db`. Workspace overlay DB stores only cross-repo edges and metadata. Single-repo commands work unchanged.
- **Post-hoc scanning** -- No changes to per-repo indexing pipeline. API edge detection runs separately via regex scanning of source files.
- **Zero new dependencies** -- JSON config (stdlib `json`), no YAML or external packages.

### MCP Server

- **`ws_understand`** tool -- Unified multi-repo workspace overview via MCP.
- **`ws_context`** tool -- Cross-repo augmented symbol context via MCP.

### Testing

- 46 new workspace tests: config parsing, DB operations, init/status commands, API call scanning (JS/TS), route scanning (Laravel/Express/FastAPI), URL normalization, endpoint matching, cross-repo edge storage, resolve integration, aggregation, understand/health/context/trace commands, formatter helpers.
- **636 total tests passing**

## v7.3.0

Visual FoxPro language support and regex-only language infrastructure.

### Visual FoxPro Support (Tier 1)

- **Full VFP extractor** (`src/roam/languages/foxpro_lang.py`) — Pure regex-based extractor for `.prg` files. No tree-sitter grammar exists for VFP, so this is the first **regex-only** Tier 1 language.
- **Symbols**: functions, procedures, classes, methods, properties, `#DEFINE` constants, implicit file-functions (`.prg` with no routines → file stem as function name).
- **References**: `DO filename`, `DO proc IN lib`, `SET PROCEDURE TO`, `SET CLASSLIB TO`, `#INCLUDE`, `CREATEOBJECT()`, `NEWOBJECT()`, `DECLARE ... IN dll`, `=funcname()` expression calls, `THIS.method()` / `obj.method()` dot calls, `DEFINE CLASS X AS Y` inheritance.
- **VFP preprocessing**: line continuation (`;`), comment stripping (`*`, `&&`, `*!*` blocks, `NOTE`), case-insensitive keyword matching.
- **Built-in filtering**: 100+ VFP built-in functions excluded from call references to reduce noise.

### Regex-Only Language Infrastructure

- **`REGEX_ONLY_LANGUAGES`** in `parser.py` — Generic mechanism for languages without tree-sitter grammars. Returns `(None, source_bytes, language)` so extractors receive source without a tree.
- **Pipeline bypass** — `indexer.py`, `symbols.py`, `complexity.py` all relaxed from `tree is None → skip` to `tree is None and source is None → skip`.
- **GenericExtractor guard** — Supplement pass skipped for regex-only languages (requires tree-sitter AST nodes).
- **Complexity fallback** — VFP files use indentation-based complexity estimation (no AST available).

### Case-Insensitive Reference Resolution

- **Fallback index** in `relations.py` — When exact-case symbol lookup fails, tries case-insensitive matching. Resolves `DO BACKUP` → `FUNCTION backup` across files. Non-breaking: only fires when exact match yields nothing, so case-sensitive languages are unaffected.

### Smart Encoding Detection

- **Multi-codepage heuristic** in `foxpro_lang.py` — Detects BOM (UTF-8, UTF-16), tries strict UTF-8, then scores 11 Windows codepages (CP1252 Western, CP1251 Cyrillic, CP1253 Greek, CP1250 Central European, CJK codepages, etc.) by printable character ratio. Zero external dependencies.

### Testing

- 34 new tests: 10 symbol extraction, 16 reference extraction, 5 encoding detection (UTF-8, Latin-1, CP1253 Greek, BOM, empty), 1 case-insensitive resolution, 2 integration.
- **590 total tests passing**

### Quality (tested on 468-file, 97K-line Greek accounting VFP codebase)

- 2,718 symbols extracted, 500 edges (+19% from case-insensitive resolution), 232 file-level edges
- All 50 roam commands produce meaningful output
- 8 Louvain clusters, 4 clean layers, zero layer violations
- Pattern detection: Factory (20), Strategy (3), Observer (1), Middleware (3)

## v7.2.0

AI agent experience, new analysis commands, and deeper per-file metrics.

### New Commands

- **`roam tour [--write PATH]`** — Auto-generated onboarding guide: top symbols by PageRank with role labels (Hub/Core utility/Orchestrator/Leaf), suggested file reading order based on topological layers, entry points, language breakdown, and codebase statistics. `--write` saves to Markdown.
- **`roam diagnose <symbol> [--depth N]`** — Root cause analysis for debugging. Given a failing symbol, walks upstream callers and downstream callees up to N hops, ranks suspects by a composite risk score combining git churn (30%), cognitive complexity (30%), file health (25%), and co-change entropy (15%). Shows co-change partners and recent git history.

### Cognitive Load Index

- **Per-file cognitive load (0-100)** — New metric stored in `file_stats.cognitive_load`. Combines max cognitive complexity (30%), avg nesting depth (15%), dependency surface area (20%), co-change entropy (15%), dead export ratio (10%), and file size (10%). Surfaced in `roam file` output (both text and JSON).

### Trend-Based Fitness Rules

- **`type: trend` fitness rules** — New rule type in `.roam/fitness.yaml` that compares snapshot metrics over a configurable window. Guards against regressions: `max_decrease: 5` fails if health_score dropped more than 5 from the window average, `max_increase: 3` fails if cycles grew by more than 3. Supports all snapshot metrics (health_score, tangle_ratio, avg_complexity, cycles, etc.).

### Agent Experience

- **Verdict-first output** — Key commands (`health`, `preflight`, `pr-risk`, `impact`, `diagnose`) now emit a one-line VERDICT as the first line of text output and include a `verdict` field in the JSON summary. Agents can stop reading after the first line for quick decisions.
- **Engineered MCP tool descriptions** — All 16 MCP tool descriptions rewritten with "WHEN TO USE" guidance, "Do NOT call X if Y covers your need" hints, and expected output descriptions. Based on Anthropic's research that tool descriptions are prompts.
- **MCP tools for tour + diagnose** — 2 new MCP tools (`tour`, `diagnose`). Total: 16 tools, 2 resources.

### PR Risk Enhancement

- **Structural profile** — `roam pr-risk` now includes cluster spread (how many Louvain communities the change touches) and layer spread (how many architectural layers crossed), surfaced in both text and JSON output.

### Distribution

- **PyPI Trusted Publishing workflow** — `.github/workflows/publish.yml` uses GitHub OIDC tokens, no API secrets needed.
- **`glama.json`** — MCP server discovery file for Glama.ai registry.
- **`llms-install.md`** — Machine-readable installation guide for AI agent auto-install.

## v7.1.0

Large-repo safety, deeper Salesforce cross-language edges, and custom report presets. Fixes a latent crash on repos with >999 symbols in queries.

### Batched SQL Infrastructure

- **`batched_in()` / `batched_count()` helpers** — Centralized IN-clause batching in `connection.py`. SQLite's default `SQLITE_MAX_VARIABLE_NUMBER` is 999; Python's `sqlite3` module doesn't expose `sqlite3_limit()`, so a conservative batch size of 400 prevents crashes on large codebases. Handles single and double `{ph}` placeholders with automatic chunk sizing.
- **41 unbatched IN-clause sites fixed** across 15 command modules and 2 graph modules: `clusters.py`, `cycles.py`, `cmd_dead.py`, `cmd_context.py`, `cmd_layers.py`, `cmd_module.py`, `cmd_debt.py`, `cmd_affected_tests.py`, `cmd_fn_coupling.py`, `cmd_health.py`, `cmd_coverage_gaps.py`, `cmd_risk.py`, `cmd_safe_zones.py`, `cmd_why.py`, `cmd_impact.py`.
- **AND-based double-IN correctness** — Queries like `WHERE source_id IN (...) AND target_id IN (...)` cannot be batched by repeating the same subset to both placeholders (cross-batch edges would be missed). Fixed by fetching single-IN results and filtering in Python.
- **OR-based double-IN** — Split into two separate single-IN queries with dict-based deduplication.
- **GROUP BY / ORDER BY / LIMIT** — Queries with aggregation that can't work across batches use Python-side `Counter` and `sort` instead.

### Salesforce Enhancements

- **LWC anonymous class extraction** — Lightning Web Components use `export default class extends LightningElement {}` (anonymous). The JavaScript extractor now derives the class name from the filename (e.g., `myComponent.js` → `MyComponent`).
- **`@salesforce/*` import resolution** — Cross-language edges for `@salesforce/apex/ClassName.method` (→ call), `@salesforce/schema/Object.Field` (→ schema_ref), `@salesforce/label/c.LabelName` (→ label), and `@salesforce/messageChannel/Channel` (→ import).
- **Apex generic type references** — `List<Account>`, `Set<Contact__c>`, `Map<Id, Opportunity>` now produce `type_ref` edges to the parameterized types. Built-in types (`String`, `Integer`, `Id`, etc.) are filtered out.
- **Flow actionCalls → Apex edges** — Salesforce Flow XML files with `<actionCalls>` blocks containing `<actionType>apex</actionType>` now produce `call` edges to the referenced Apex class. Block-scoped parsing prevents cross-block false positives.
- **SF test naming conventions** — `roam test-map` and `roam impact` now discover Salesforce-style test classes (`{Name}Test.cls`, `{Name}_Test.cls`) via convention-based queries, in addition to path/filename pattern matching.

### Custom Report Presets

- **`roam report --config <path>`** — Load custom report presets from a JSON file. Custom presets are merged with built-in ones (`first-contact`, `security`, `pre-pr`, `refactor`). Each preset defines sections with title + command arrays.

### Correctness Fixes

- **Flow XML cross-block regex** — Fixed a bug where the `_extract_flow_refs` regex could span across `</actionCalls>` boundaries, pairing an `actionName` from one block with an `actionType` from another. Replaced with block-scoped parsing.
- **AND-based double-IN batching** — Fixed `cmd_module.py` cohesion calculation and `cmd_dead.py` cluster detection that undercounted internal edges for large datasets (>200 symbols).
- **Report `--config` error handling** — Malformed JSON now raises a clean `click.BadParameter` instead of an unhandled traceback.

### Testing

- 67 new tests covering: batched helpers, SF import resolution, Apex generic types, LWC anonymous class, Flow actionCalls, report --config, SF test detection, cross-block safety, cross-batch edge correctness.
- **556 total tests passing**

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
