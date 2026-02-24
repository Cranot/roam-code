# Changelog

All notable changes to [roam-code](https://github.com/Cranot/roam-code) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [11.0.0] - 2026-02-25

### Added
- **MCP v2 Overhaul:**
  - In-process MCP execution via CliRunner -- eliminates subprocess overhead (#1)
  - 4 compound MCP operations: `roam_explore`, `roam_prepare_change`, `roam_review_change`, `roam_diagnose_issue` -- each replaces 2-4 tool calls (#2)
  - 6 MCP tool presets: core (20 tools), review, refactor, debug, architecture, full (65 tools) via `ROAM_MCP_PRESET` env var (#3)
  - Structured return schemas (`output_schema`) on all 65 MCP tools (#4)
  - `roam_expand_toolset` meta-tool for dynamic mid-session preset switching (#6)
- **Performance Foundations:**
  - SQLite FTS5/BM25 search replacing TF-IDF -- symbol search is now ~1000x faster (#14)
  - O(changed) incremental edge rebuild via `source_file_id` provenance tracking (#13)
  - 7 new database indexes, UPSERT pattern, batch size optimization (#15)
  - `PRAGMA mmap_size=268435456` (256MB memory-mapped I/O) (#11)
  - Size guard on `propagation_cost()` for graphs >500 nodes (#12)
- **MCP Protocol Compliance (Epic 14):**
  - Structured error responses with `isError`, `retryable`, and `suggested_action` fields (#116)
  - `structuredContent` alongside text on MCP tool failures (#117)
  - 5 MCP Prompts: `/roam-onboard`, `/roam-review`, `/roam-debug`, `/roam-refactor`, `/roam-health-check` (#118)
  - Response metadata in `_meta`: `response_tokens`, `latency_ms`, `cacheable`, `cache_ttl_s` (#119)
- **Code Smell Detection:**
  - `roam smells` — 15 deterministic detectors (brain methods, god classes, feature envy, shotgun surgery, data clumps, etc.) with per-file health scores (#120)
- **Quality Gates and Setup:**
  - `roam health --gate` — quality gate checks from `.roam-gates.yml` with exit code 5 on failure (#122)
  - `roam mcp-setup <platform>` — config snippets for claude-code, cursor, windsurf, vscode, gemini-cli, codex-cli (#130)
- **Security and Verification:**
  - `roam verify-imports [--file F]` — import hallucination firewall: validate imports against indexed symbol table with FTS5 fuzzy suggestions (#125)
  - `roam vulns [--import-file F] [--reachable-only]` — vulnerability scanning CLI: auto-detect npm/pip/trivy/osv formats, reachability filtering, SARIF output (#131)
  - `roam secrets` upgraded: test/doc suppression, env-var detection, Shannon entropy detector, per-finding remediation suggestions (#133)
- **Analytics and Scoring:**
  - `roam metrics <file|symbol>` — unified vital signs: complexity, fan-in/out, PageRank, churn, test coverage, dead code risk (#137)
  - `roam debt --roi` — refactoring ROI estimate (developer-hours saved per quarter/year) with confidence band based on complexity, churn, and coupling signals (#144)
  - Composite difficulty scoring for partitions: weighted complexity + coupling + churn + size with Easy/Medium/Hard/Critical labels (#128)
  - Quality rule profiles with inheritance: default, strict-security, ai-code-review, legacy-maintenance, minimal — `--profile` flag on `roam check-rules` (#138)
- **Documentation Intelligence:**
  - `roam docs-coverage` — exported-symbol docs coverage report with stale-doc drift detection and PageRank-ranked missing-doc hotlist, plus threshold gate support (`--threshold`) (#143)
- MCP resources expanded from 2 to 10: architecture, hotspots, tech-stack, dead-code, recent-changes, dependencies, test-coverage, complexity (#129)
- **CI/Runtime Ergonomics:**
  - Standardized exit codes for CI integration (0=success, 3=index missing, 5=gate failure) (#19)
  - GitHub Action: composite action with SARIF upload, sticky PR comments, quality gates, SQLite caching (#20)
  - Progress indicators during `roam init` / `roam index` with `--quiet` flag (#30)
  - `defer_loading` annotations on non-core MCP tools for Claude Code Tool Search compatibility (#66)
- **Ownership and Reviewer Intelligence:**
  - `roam codeowners` (#38)
  - `roam drift` (#39)
  - `roam simulate-departure` (#40)
  - `roam suggest-reviewers` (#41)
- **Change-risk and Structural Review:**
  - `roam api-changes` (#42)
  - `roam test-gaps` (#43)
  - `roam secrets` (#44)
  - `roam semantic-diff` (#77)
- **Agent Quality and Governance Suite:**
  - `roam vibe-check` (#57)
  - `roam ai-readiness` (#84)
  - `roam verify` (#85)
  - `roam ai-ratio` (#86)
  - `roam duplicates` (#87)
- **Dashboard and Trend Visibility:**
  - `roam dashboard` (#80)
  - `roam trends` (#81)
  - `--mermaid` architecture output support (#82)
  - `roam onboard` (#83)
- **Multi-agent Workflows:**
  - `roam partition` (#88)
  - `roam affected` (#89)
  - `roam syntax-check` (#92)
- **Output Determinism and Context Ranking:**
  - Deterministic output ordering for cache-friendly prompts (#90)
  - PageRank-weighted budget truncation metadata (#91)
  - Conversation-aware ranking personalization (#94)
- **Agent Context Export and MCP Compatibility:**
  - Agent context export bundles (`AGENTS.md` + provider overlays) (#65, #68, #97)
  - Streamable HTTP transport baseline (`roam mcp --transport streamable-http`) (#98)
  - Expanded MCP annotations + task-support metadata (#99)
  - MCP client conformance/profile suite (#100)
- **Algorithm Detection Upgrades:**
  - Precision profiles (`balanced`/`strict`/`aggressive`) (#101)
  - Runtime-aware impact scoring + evidence paths + framework-aware N+1 packs (#102)
  - `roam algo --sarif` with stable fingerprints, codeFlows, and fixes payloads (#103)
- **CI Quality-gate Hardening:**
  - Idempotent sticky PR comment updater with duplicate cleanup (#23)
  - Trend-aware fitness gates (#74)
  - `--changed-only` incremental CI mode (#75)
  - SARIF guardrails + configurable category + truncation warnings (#105)
- **Documentation/Release Hygiene:**
  - CONTRIBUTING.md with issue/PR templates (#28)
  - README competitive positioning table (#76)
  - Command/matrix count reconciliation helpers and tests (#108)
  - README command/MCP inventory overhaul to match source reality (#106)
  - Product landing page at `docs/site/index.html` with competitive comparison, feature showcase, and install instructions
  - Competitive research page at `docs/site/landscape.html` with fairness-recalibrated scores
- PyPI discoverability: keywords, Documentation URL, and expanded classifiers in `pyproject.toml` (#111)
- Pre-commit integration: `.pre-commit-hooks.yaml` with 5 hooks (`roam-secrets`, `roam-syntax-check`, `roam-verify`, `roam-health`, `roam-vibe-check`) (#21)
- Fuzzy symbol-not-found suggestions via FTS5/BM25 search in `roam symbol`, `roam impact`, `roam context`, `roam diagnose` (#51)
- Actionable remediation hints in all major error messages — "index missing", "symbol not found", "database error" now include next steps (#50)
- **Agent Error Recovery and Diagnostics:**
  - `roam doctor` — setup diagnostics: Python version, tree-sitter, git, index freshness, SQLite, networkx checks (#48)
  - `roam reset` — destructive index rebuild with `--force` safety flag (#52)
  - `roam clean` — lightweight orphaned-file cleanup without full rebuild (#52)
  - Next-step suggestions in `roam health`, `roam context`, `roam hotspots`, `roam diagnose`, `roam dead` output (#45)
  - `roam endpoints` — multi-framework API endpoint scanner (Flask, FastAPI, Django, Express, Go, Spring, Laravel, GraphQL, gRPC) (#113)
- **Progressive Disclosure and Batch Operations:**
  - Universal progressive disclosure: `--detail` flag for full output, compact summary by default. Applied to `health`, `hotspots`, `dead`, `deps`, `layers`, `clusters` (#10)
  - Batch MCP operations: `roam_batch_search` (10 queries) and `roam_batch_get` (50 symbols) in single MCP call with shared DB connection (#7)
- **Developer Workflow Tools:**
  - Git hook auto-indexing: `roam hooks install/uninstall/status` with append-mode markers for post-merge/post-checkout/post-rewrite (#61)
  - Install verification: `roam --check` eager flag for quick first-run validation (#115)
  - `roam dev-profile` — developer behavioral profiling: commit time patterns, Gini scatter, burst detection, session analysis, risk scoring (#78)
  - `roam watch` — poll-based file watcher with debouncing for always-on agent sessions, plus authenticated webhook daemon mode (`POST /roam/reindex`, `GET /health`) for warm refresh workflows (#60, #95)
- **Search and Analysis:**
  - `roam search-semantic` now uses hybrid retrieval: BM25 lexical ranking + TF-IDF vector ranking fused with Reciprocal Rank Fusion for stronger semantic recall (#54)
  - Pre-indexed framework/library packs now enrich semantic retrieval for common stacks (Django, Flask, FastAPI, React, Express, SQLAlchemy, pytest, stdlib) to improve cold-start recall (#96)
  - `roam search --explain` — BM25 score breakdown with field match highlights for search result transparency (#55)
  - `roam supply-chain` — dependency risk dashboard: 7 package formats, pin coverage scoring, maintenance signals (#79)
  - `roam spectral` — Fiedler vector bisection for module decomposition, spectral gap metric, `--compare` vs Louvain (#73)
- **Structural Governance:**
  - `roam check-rules` — structural rule engine with 10 built-in rules and `.roam-rules.yml` config (#93)
  - Bottom-up context propagation through call graph for `roam context` ranking (#72)

### Changed
- All MCP tool descriptions shortened to <60 tokens each for agent efficiency (#5)
- MCP token overhead reduced from ~36K to <3K tokens (core preset) -- 92% reduction
- `--budget N` Phase 2: extended to all list-producing commands (13 more commands), completing universal budget support across the full CLI (#9)
- MCP core preset expanded from 21 to 23 tools (added `roam_batch_search`, `roam_batch_get`)
- CI workflows consolidated: removed redundant `ci.yml`, enhanced `roam-ci.yml` with lint job, converted `roam.yml` to `workflow_dispatch`-only template (#110)
- Competitive landscape scoring rebalanced: equal weights (0.5/0.5), self-assessed labels, roam arch score 90→78, SonarQube 62→72, CodeQL 60→74
- roam-code category in competitive data changed from standalone `"roam"` to `"mcp_server"`
- Confidence system removed from competitive landscape page
- Consolidated duplicated EXTENSION_MAP and schema definitions to single sources of truth (#17)

### Fixed
- Command-count drift removed from docs and launch copy by adopting canonical-vs-alias counting (`algo` + legacy `math`) (#108)
- README command tables and MCP inventory now match code (121 canonical CLI commands + 1 alias, 93 MCP tools) (#106)
- Bare `except:` audit confirmed — codebase already clean, no broad exception swallowing (#18)
- Cycle detection in health scoring now uses Tarjan SCC (O(V+E)) instead of 2-cycle self-join (#16)

## [10.0.1] - 2026-02-21

### Added
- MCP lite mode (16 core tools) as the default; full mode via `ROAM_MCP_LITE=0`
- MCP tool namespacing with `roam_` prefix across all 61 tools
- `roam mcp` command with `--transport` and `--list-tools` flags
- 13 additional MCP tools with structured error handling

### Fixed
- Community issues #7 and #9 addressed
- YAML fallback parser indentation handling corrected
- `--json` flag position in CI workflow examples fixed
- CI dev dependencies (pytest-xdist) properly installed

## [10.0.0] - 2026-02-20

### Added
- **30+ new commands** bringing total to 94 (from 56 in v9.1):
  - Architecture: `simulate`, `fingerprint`, `orchestrate`, `cut`, `adversarial`, `plan`
  - Debugging: `invariants`, `bisect`, `intent`, `closure`
  - Governance: `rules`, `attest`, `pr-diff`, `budget`, `capsule`, `forecast`, `path-coverage`
  - Analysis: `dark-matter`, `effects`, `annotate`, `annotations`, `relate`
  - Backend quality: `n1`, `auth-gaps`, `over-fetch`, `migration-safety` (and 3 more)
- Cross-language bridges: Salesforce (Apex/Aura/LWC), Protobuf, REST API, Jinja2/Django templates, env var config
- Semantic search via TF-IDF with cosine similarity (`roam search --semantic`)
- JSON envelope schema versioning and validation on all command output
- `--sarif` global CLI flag for SARIF 2.1.0 output (health, debt, complexity)
- `--include-excluded` flag for inspecting normally-excluded files
- Algorithm catalog tips integrated into analysis output
- Ruby Tier 1 language support (26 languages total)
- `roam fingerprint` for topology fingerprinting and comparison
- `roam orchestrate` for multi-agent work partitioning (Louvain-based)
- `roam mutate` for code transforms (move, rename, add-call, extract)
- Vulnerability mapping (`roam vuln-map`) and trace ingestion (`roam ingest-trace`)
- Property-based and indexing integration tests

### Fixed
- ON DELETE CASCADE/SET NULL added to foreign key constraints
- `fitness` command outputs proper JSON when no rules are configured
- Schema-prefixed `$` table names stripped in `missing-index` detection
- Pluralization edge cases and `$hidden` symbol messaging
- Algorithm findings accuracy: auth-gaps brace tracking, over-fetch, migration-safety
- Loop-invariant false positive rate reduced

### Changed
- Lint cleanup and algorithm optimizations across codebase
- pytest-xdist enabled for parallel test execution (~2x speedup)

## [9.1.0] - 2026-02-18

### Added
- `roam minimap` -- compact annotated codebase snapshot for CLAUDE.md generation
- YAML language support (Tier 1)
- HCL/Terraform language support (Tier 1)
- `roam describe --write` for agent-generated project instructions
- `.roamignore` support for excluding files from indexing

### Fixed
- Network drive path detection with automatic SQLite journal mode adaptation
- Indexer stall on binary formats (SCX files) with cloud-sync hardening

## [9.0.0] - 2026-02-18

### Added
- Universal algorithm catalog -- 23 tasks with ranked solution approaches (`roam math`)
- Algorithm anti-pattern detectors that query DB signals to find suboptimal code
- Command decomposition: large CLI modules split into focused `cmd_*.py` files
- `roam n1` -- implicit N+1 I/O pattern detection
- 6 backend quality analysis commands

## [8.2.0] - 2026-02-14

### Added
- Python extractor: `with`, `except`, `raise` statement extraction

### Fixed
- Dead export count discrepancy between `roam understand` and `roam dead --summary`
- Alerts health score mismatch with `roam health` (replaced simple penalty formula with weighted geometric mean)
- `roam patterns` self-detection of its own detector functions
- Middleware false positives from `%Handler` and `%Filter` patterns

### Changed
- Smarter health scoring: `dev/`, `tests/`, `scripts/`, `benchmark/` classified as expected utilities
- File role classifier: `dev/` directory assigned `ROLE_SCRIPTS`

### Removed
- 5 unused functions: `condense_cycles`, `layer_balance`, `find_path`, `build_reverse_adj`, `get_symbol_blame` (~200 lines)

## [8.1.1] - 2026-02-14

### Added
- Python extractor: instance attribute extraction from `__init__` methods (Pyan-inspired)
- Python extractor: assignment type annotation references for class fields and module variables
- Python extractor: forward reference support for string annotations (`Optional["Config"]`)

## [8.1.0] - 2026-02-14

### Added
- Python extractor: decorator references (`@decorator` and `@module.decorator(args)`)
- Python extractor: type annotation references for parameters, returns, and generics

### Fixed
- `roam complexity` crash on databases missing v7.4 columns (defensive `_safe_metric()` accessor)
- 95% fewer dead code false positives (test files excluded, ABC overrides and CLI functions marked intentional)
- Smarter health scoring with expanded utility path detection (`output/`, `db/`, `common/`, `internal/`)

## [8.0.1] - 2026-02-14

### Changed
- Extracted `graph_helpers.py` with shared BFS/adjacency code from 4 command files
- Split `cmd_context.py` into focused modules (1,622 to 1,022 lines)
- Added Python 3.9 to CI matrix, `Makefile`, and dev tooling

## [8.0.0] - 2026-02-14

### Added
- Statistical anomaly detection: Modified Z-Score, Theil-Sen regression, Mann-Kendall trend, CUSUM change-point detection
- Smart file role classifier (source, test, config, docs, build, generated, etc.)
- Dead code aging with git blame temporal decay scoring
- Cross-language bridge framework (abstract `LanguageBridge` with auto-discovery)
- C# Tier 1 language support (attributes, nullable types, using directives, constructors)
- `roam visualize` command for Mermaid/DOT architecture diagrams
- SCX/SCT binary form support for Visual FoxPro
- Agent-agnostic `roam describe` with auto-detection
- Gate presets for `coverage-gaps` (Python, JavaScript, Go, Java, Rust) with `.roam-gates.yml`
- Pluggable test convention adapters (Python, Go, JavaScript, Java, Ruby, Apex)
- `roam trend --analyze` with anomaly detection, forecasting, and `--fail-on-anomaly` flag
- 1,656 tests (up from 669 in v7.5.0)

### Changed
- Version sourced from single location (`pyproject.toml` via `importlib.metadata`)
- License format updated to SPDX string

### Fixed
- Cloud-synced path auto-detection with SQLite journal mode adaptation
- Indexer stall on binary SCX formats

## [7.5.0] - 2026-02-13

### Changed
- 12 research-backed math improvements across core analysis algorithms
- Percentile-based betweenness severity scoring (scales across codebase sizes)
- Three-factor trace quality scoring (directness + coupling + scaled hub penalty)

## [7.4.0] - 2026-02-12

### Added
- Multi-repo workspace support (`roam ws init`, `roam ws add`, `roam ws query`)
- Visual FoxPro (VFP) Tier 1 language support with regex-only extractor
- Cross-repo API edge detection (REST routes and HTTP client calls)
- Smart encoding detection for multi-codepage files (11 Windows codepages)
- Case-insensitive reference resolution fallback for VFP

## [7.2.0] - 2026-02-12

### Added
- Cognitive load index (0-100) per file combining complexity, dependencies, entropy, and size
- `roam tour` -- auto-generated onboarding guide with PageRank-ranked symbols
- `roam diagnose` -- root cause ranking with composite risk scoring
- Verdict-first output pattern across key commands (VERDICT line + JSON `verdict` field)
- Trend-based fitness rules (`type: trend` in `.roam/fitness.yaml`)
- MCP tools for `tour` and `diagnose`
- PR risk structural profile (cluster spread + layer spread)
- PyPI Trusted Publishing workflow

## [7.1.0] - 2026-02-12

### Added
- `batched_in()` / `batched_count()` helpers preventing >999 parameter SQL crashes
- Salesforce cross-language edges (LWC anonymous classes, `@salesforce/*` imports, Apex generics)
- Flow XML `actionCalls` to Apex class edge resolution
- Custom report presets via `roam report --config <path>`

### Fixed
- 41 unbatched IN-clause sites across 15 command modules and 2 graph modules
- Flow XML cross-block regex spanning across `</actionCalls>` boundaries

## [7.0.0] - 2026-02-12

### Added
- Composite health scoring (0-100) replacing old cycles-only formula
- SARIF 2.1.0 output for GitHub Code Scanning
- MCP server with 12 tools and 2 resources via FastMCP
- `roam init` -- guided project onboarding with CI workflow generation
- `roam digest` -- metric comparison against snapshots with delta arrows
- `roam describe --agent-prompt` -- compact agent-oriented summary under 500 tokens
- Per-file health score (1-10) with 7-factor CodeScene-inspired composite
- Co-change entropy (Shannon) for shotgun surgery detection
- Tangle ratio metric (Structure101 concept)
- `--compact` flag for token-efficient output across all commands
- `--gate EXPR` for CI quality gates (`roam health --gate score>=70`)
- Categorized `--help` with 7 command categories
- GitHub Action (`action.yml`) for CI integration

### Fixed
- `elif`/`else`/`case` chains inflating cognitive complexity ~3x vs SonarSource spec

## [6.0.0] - 2026-02-12

### Added
- 15 new commands: `complexity`, `conventions`, `debt`, `fitness`, `preflight`, `affected-tests`, `entry-points`, `safe-zones`, `patterns`, `bus-factor`, `breaking`, `alerts`, `fn-coupling`, `doc-staleness`, `complexity --bumpy-road`
- Cognitive complexity analysis (SonarSource-compatible, tree-sitter based)
- Architectural fitness functions via `.roam/fitness.yaml`
- `roam context --task` with mode-specific output (refactor/debug/extend/review/understand)
- `roam map --budget N` token-budget-aware repo map
- `roam diff --tests --coupling --fitness` enhanced diff analysis
- `symbol_metrics` table for per-function complexity data

## [5.0.0] - 2026-02-10

### Added
- `roam understand` -- single-call codebase comprehension for AI agents
- `roam coverage-gaps` -- unprotected entry point detection via BFS reachability
- `roam snapshot` / `roam trend` -- health metric history with sparklines and CI assertions
- `roam report` -- compound preset runner (first-contact, security, pre-pr, refactor)
- Salesforce support: Apex, Aura, Visualforce, SF Metadata XML extractors
- Hypergraph co-change analysis with surprise scoring
- JSON envelope contract across all `--json` output
- `roam dead --summary --by-kind --clusters` grouped analysis modes
- `roam context` batch mode for multiple symbols

## [4.0.0] - 2026-02-10

### Added
- Location-aware health scoring, callee-chain risk, multi-path trace
- `roam why` -- symbol role classification with reach and verdict
- PHP Tier 1 language support
- `--json` global flag on all commands

### Changed
- Precision refinements: utility-aware bottlenecks, zone-override risk, hub-aware trace

## [3.7.0] - 2026-02-10

### Added
- `roam describe`, `roam test-map`, `roam sketch` commands
- Method call extraction
- `.svelte` file support

## [3.0.0] - 2026-02-09

### Added
- Vue SFC parsing with template consumption analysis
- `roam diff` for blast radius of uncommitted changes
- Cross-file resolution with exported preference and Go same-directory matching
- Dead code transitive consumption check

### Fixed
- Vue import resolution and preprocessing off-by-one errors
- Windows NUL device crash
- Symbol disambiguation accuracy

## [1.0.0] - 2026-02-09

### Added
- Initial release: instant codebase comprehension for AI coding agents
- Tree-sitter AST parsing for Python, JavaScript, TypeScript, Go, Java, Rust, C
- Symbol extraction with qualified names and visibility
- Reference resolution and call graph construction
- Dependency graph with NetworkX (PageRank, cycles, layers, clusters)
- Core commands: `search`, `get`, `callers`, `callees`, `uses`, `map`, `layers`, `clusters`, `health`, `dead`, `hotspot`, `risk`, `owner`, `coupling`, `trace`, `grep`, `deps`
- SQLite local index (`.roam/index.db`)
- Incremental indexing via mtime + hash change detection
- Git integration: churn, blame, co-change analysis

[Unreleased]: https://github.com/Cranot/roam-code/compare/v11.0.0...HEAD
[11.0.0]: https://github.com/Cranot/roam-code/compare/v10.0.1...v11.0.0
[10.0.1]: https://github.com/Cranot/roam-code/compare/v10.0.0...v10.0.1
[10.0.0]: https://github.com/Cranot/roam-code/compare/v9.1.0...v10.0.0
[9.1.0]: https://github.com/Cranot/roam-code/compare/v9.0.0...v9.1.0
[9.0.0]: https://github.com/Cranot/roam-code/compare/v8.2.0...v9.0.0
[8.2.0]: https://github.com/Cranot/roam-code/compare/v8.1.1...v8.2.0
[8.1.1]: https://github.com/Cranot/roam-code/compare/v8.1.0...v8.1.1
[8.1.0]: https://github.com/Cranot/roam-code/compare/v8.0.1...v8.1.0
[8.0.1]: https://github.com/Cranot/roam-code/compare/v8.0.0...v8.0.1
[8.0.0]: https://github.com/Cranot/roam-code/compare/v7.5.0...v8.0.0
[7.5.0]: https://github.com/Cranot/roam-code/compare/v7.4.0...v7.5.0
[7.4.0]: https://github.com/Cranot/roam-code/compare/v7.2.0...v7.4.0
[7.2.0]: https://github.com/Cranot/roam-code/compare/v7.1.0...v7.2.0
[7.1.0]: https://github.com/Cranot/roam-code/compare/v7.0.0...v7.1.0
[7.0.0]: https://github.com/Cranot/roam-code/compare/v6.0.0...v7.0.0
[6.0.0]: https://github.com/Cranot/roam-code/compare/v5.0.0...v6.0.0
[5.0.0]: https://github.com/Cranot/roam-code/compare/v4.0.0...v5.0.0
[4.0.0]: https://github.com/Cranot/roam-code/compare/v3.7.0...v4.0.0
[3.7.0]: https://github.com/Cranot/roam-code/compare/v3.0.0...v3.7.0
[3.0.0]: https://github.com/Cranot/roam-code/compare/v1.0.0...v3.0.0
[1.0.0]: https://github.com/Cranot/roam-code/releases/tag/v1.0.0
