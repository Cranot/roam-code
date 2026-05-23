# AGENTS.md — roam-code development guide

## What this project is

<!-- BEGIN auto-count:Codex-headline -->
roam-code is a local codebase intelligence CLI for developers and AI coding agents.
It pre-indexes symbols, call graphs, dependencies, architecture, and git history into
a local SQLite DB. **241 commands · 227 MCP tools (57 in the default `core` preset) · 28 languages · 100% local · zero API keys.**
<!-- END auto-count:Codex-headline -->

<!-- BEGIN auto-count:Codex-authoritative -->
Authoritative counts (AST-derived, env-independent): `command_count: 241 · canonical_count: 234 · category_count: 7 · mcp tools registered: 227 · mcp tools in core preset: 57`. The `roam surface --json` envelope additionally exposes `mcp_tool_count_by_preset` for per-preset counts.
<!-- END auto-count:Codex-authoritative -->

**Package:** `roam-code` on PyPI. Entry point: `roam.cli:cli`.

## Documentation Hub

- **Dogfood corpus** (read for quality lessons): `internal/dogfood/` — 212-eval corpus + 6 systemic-pattern synthesis. Single most important reference for understanding what good roam-command behavior looks like. Start with `internal/dogfood/README.md`. (Private — gitignored; not shipped to PyPI/GitHub.)
- Getting started tutorial: `templates/distribution/landing-page/docs/getting-started.html`
- Command reference with examples: `templates/distribution/landing-page/docs/command-reference.html`
- Architecture guide and diagram: `templates/distribution/landing-page/docs/architecture.html`
- Live URL: https://roam-code.com/docs/

## Where files go (private vs public)

This repo is public. The placement rule is physical, not pattern-based:

- **`internal/`** — the ONE private folder. Wholesale gitignored. Everything that shouldn't be on GitHub goes here: session memos, sprint plans, release-readiness drafts, monetization research, dogfood batches, smoke results, generated test fixtures, scratch notes, anything you'd be uncomfortable seeing in a public diff. Sub-conventions: `internal/dogfood/` (eval corpus), `internal/planning/` (session / sprint / release memos).
- **Everything else** — public by default. `src/`, `tests/`, `templates/`, `dev/`, root files. No magic in the gitignore, no whitelists, no per-extension rules.

When in doubt about a file: if it's a planning artifact or session-cadence output, write it under `internal/`. If you intend it to be public, write it in the appropriate public folder.

Anti-pattern history: the repo used to use an enumerative gitignore in `dev/` (whitelisting specific filename templates to exclude), which fail-opened on any new memo family. Fixed by the rule above: one private folder, no pattern magic.

## Quality discipline (from `internal/dogfood/` + agi-in-md)

This section codifies what makes a roam command good. Distilled from 212 dogfood evals + 1000+ prompt-design experiments. Treat as constraints when adding or modifying commands.

### Six systemic anti-patterns to NEVER ship

From `internal/dogfood/SYNTHESIS-2026-05-12.md` — validated unchanged across 30 → 59 → 212 evals as failure classes. Several of the original incidents are now SEALED behind regression tests; they are kept here as regression-invariant examples, not as claims that the bug is currently live.

1. **Pattern-1 family — "structured signal lost or never reached".** One root failure family. (A) Hang on missing prerequisite — SEALED (live guard `src/roam/mcp_extras/preflight.py`). (B) Structured signal collapsed to generic `COMMAND_FAILED` by an intermediate layer — SEALED (try-parse stdout as JSON at the wrapper-bridge). (C) Empty-stdout crash on `json.loads()` — SEALED (CLI always emits a structured envelope, even on no-results). (D) Silent success on degraded resolution — LIVE: disclose the resolution state via a `resolution` field + `partial_success: true` + a degraded verdict. Every wrapper that cannot complete normally emits the canonical failure envelope (closed `status` / `error_code` enums; `isError: true` inside a successful JSON-RPC result).

2. **Silent fallback.** Never emit `verdict: "SAFE"` / `"completed"` / `"non-conformant"` when the underlying check failed or didn't run. Make absent state explicit: `state: "not_initialized"`, not `state: "broken"`. Historical example: `for_refactor` once reported `verdict: "compound operation completed"` despite 4/4 subcommand failures — the guard now lives in `_compound_envelope()` and its tests (subcommand failure must set `partial_success: true` and name the failed subcommands).

3. **Vocabulary mismatch family.** (a) Cross-command metric divergence — different commands report different "callers" / "complexity" / "AI rot" / "public symbols" for the same input; fix by stamping a `<metric>_definition` sidecar field (`caller_metric_definition: "raw_edge_rows"` pattern). (b) Cross-MCP parameter-name divergence — 9+ MCP parameter names refer to similar concepts (`symbol` vs `name`; `path` vs `paths` vs `file`; `query` vs `queries` vs `patterns` vs `prefix`); fix via the `_PARAM_ALIASES` table in `src/roam/mcp_server.py` with boundary normalization at wrapper-dispatch time. The lint `tests/test_mcp_param_names.py` blocks new wrappers re-introducing legacy names.

4. **Conventions detector inconsistency — RESOLVED (Fix G).** All 5 sites (`describe`, `understand`, `minimap`, `preflight`, `conventions` standalone) now delegate to the canonical `conventions_helper.compute_conventions()`. Any new convention-aware command MUST call the helper; `--persist` to the findings registry lives ONLY on the standalone `conventions` command.

5. **Compound-recipe internal command-name drift — SEALED.** Use **registry-key lookup** at compound-init time, never string-concat. Live in `_cr()` / `_COMPOUND_REGISTRY` (`src/roam/mcp_server.py`); guard test `tests/test_compound_recipe_registry.py` AST-scans recipes against `cli._COMMANDS | cli._DEPRECATED_COMMANDS`.

6. **Response volume family.** (a) Auto-handle pattern — caller polls for chunks via `_wrap_with_handle_off`; FULLY ADOPTED, every `@_tool` command inherits it. (b) File-write pattern — writes to disk + returns a tiny envelope (`graph-export`, `fingerprint`, `index-bundle`, `cga`, `agent-export`). (c) `fetch_handle`'s own crash on large handles — SEALED at v2.0.0 (fully paginated byte-slice + section-pick + jq-projection modes). Mandate: any response >20K tokens MUST use 6a OR 6b.

### Twelve agi-in-md laws applied to roam-code

These are language-model behavioral laws (validated on Haiku/Sonnet/Opus 4.5/4.6, 1000+ experiments). Each translates directly to a roam-design constraint.

1. **[LAW] The prompt is the dominant variable.** (D13) — In roam: the **JSON envelope shape is the dominant variable** for agent integration. A 5-signal envelope (`preflight`'s blast/complexity/conventions/coupling/fitness) outperforms a 12-signal envelope on agent-decision speed. Quality of envelope > volume of fields.

2. **[LAW] Imperatives beat descriptions.** (D3) — Tool descriptions and `next_commands` must use imperative voice. "Run `roam impact handleSave` to see callers." NOT "This command shows callers." Validated across 50+ MCP tool descriptions in the dogfood.

3. **[LAW] The prompt is a program; operation order becomes section order.** (D4) — In roam: the order of fields in `agent_contract.facts` determines what agents act on first. Put the actionable verdict in `facts[0]`. Validated by the difference between `preflight` (verdict-first, agents preflight before edit) and `complexity` (numeric-first, agents skip the gate).

4. **[LAW] "Code" nouns activate analytical mode on any input.** (D15) — In roam: `agent_contract.facts` strings should anchor on concrete nouns ("`useThemeClasses` has 528 callers") not abstract ones ("this symbol has many callers"). Concrete nouns activate analytical processing; abstract nouns activate summary mode.

   **Concrete-noun anchor vocabulary**: the LAW 4 lint at `tests/test_law4_lint.py` accepts a fact string as concrete-noun-anchored if its terminal token (last word, punctuation stripped) is in a known anchor set. Authoritative sources: `src/roam/output/formatter.py:concrete_plural_terminals` (99 entries, drives the humanizer's "skip findings suffix" rule) and `tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS` (116 entries = 99 shared with the formatter + 17 SBOM/registry additions; mirrors the formatter set per the `# Keep these two lists in sync.` comment, and the count drift is pinned by `tests/test_law4_anchor_counts.py`). Representative entries — consult the source files for the full list:

   - **Code structure**: `files`, `symbols`, `edges`, `nodes`, `cycles`, `clusters`, `layers`, `modules`, `commands`, `tools`, `capabilities`, `imports`, `endpoints`, `dependencies`, `packages`, `routes`
   - **Findings**: `findings`, `hotspots`, `smells`, `violations`, `warnings`, `errors`, `alerts`, `issues`, `gaps`, `leaks`, `secrets`, `vulnerabilities`
   - **Quality metrics**: `keys`, `values`, `chars`, `lines`, `tokens`, `bytes`, `items`, `entries`, `records`, `fields`
   - **Past participles / state qualifiers**: `passed`, `failed`, `scanned`, `checked`, `affected`, `scored`, `confirmed`, `analyzed`, `skipped`, `reached`
   - **Time units**: `days`, `weeks`, `months`, `years`, `hours`, `minutes`, `seconds`, `milliseconds`

   When writing a new fact, ensure the terminal token is in the anchor set. If not, either rephrase to anchor on a different terminal OR add the new noun to BOTH `src/roam/output/formatter.py:concrete_plural_terminals` AND `tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS`. The test set is a deliberate superset of the formatter set (formatter has 99 entries; the test mirrors all of them and adds 17 SBOM/registry-domain terminals — `capabilities`, `commands`, `tools`, `packages`, `phantom`, `reachable`, etc.). The mirror is hand-maintained rather than imported so the lint stays decoupled from `roam.output.formatter` — see the `# Keep these two lists in sync.` comment in the test file.

   Example:
   - WRONG: `"7 of 10 capabilities are AI-safe"` (ends on `AI-safe`, not anchored)
   - RIGHT: `"7 of 10 AI-safe capabilities"` (ends on `capabilities`, anchored)

5. **[LAW] ≤3 concrete steps = universal; 9+ abstract steps = catastrophic on Haiku.** (D16) — In roam: compound recipes that chain 5+ subcommands are unreliable when an agent on Haiku consumes them. Either chain ≤3 OR use a registry recipe that the runtime expands at dispatch time. Validated by `for_refactor`'s 4-subcommand chain being broken vs `pr_prep`'s 3-subcommand chain working cleanly.

6. **[LAW] Compression forces domain neutrality.** (D17) — In roam: the `summary.verdict` line MUST work without any other field. Agents that consume only the verdict do not load the full envelope. `verdict: "Healthy 32/100 with 12 cycles"` works; `verdict: "see details"` fails.

7. **[CONSTRAINT] Use positive vocabulary, not negative constraints.** — In roam: error envelopes should name what works, not what's forbidden. `"Use --gate-pattern to filter"` beats `"Do not call without a filter"`. Same applies to `risks[]` arrays — name the surviving risk, not the absent guard.

8. **[CONSTRAINT] Use semantically meaningful operation names.** — In roam: this is exactly the `vuln`/`vulns` typo bug. Internal command names must be a CLOSED ENUMERATION, not free string composition. Fix: registry-key lookup; prevention: a CI lint that fails when a compound recipe references a command name that isn't in `cli._COMMANDS`.

9. **[CONSTRAINT] Coupling lives in what steps SAY, not output format specs.** — In roam: compound recipes should compose by **shared input/output types**, not by string-templated arg passing. The `for_bug_fix` / `diagnose_issue` divergence on `handleSave` resolution (different file picked!) is exactly this bug — they share a name string, not a resolved symbol id.

10. **[CONSTRAINT] Drop negative framing + 2-phase output specs from agent contracts.** (V13 ablation: each removal = +1 compliance) — In roam: `agent_contract.facts` should be a flat list of positive assertions. No "first verify X, then check Y" structure inside facts. One claim per fact, all positive.

11. **[CONSTRAINT] Identity/persona > step enumeration for single-call depth.** — In roam: tool descriptions should describe what the tool IS, not what it DOES step-by-step. "Pre-change safety gate" beats "First runs blast, then complexity, then conventions, then fitness." Identity activates the right consumption pattern.

12. **[CONSTRAINT] First-token EXECUTABILITY is a separate axis from shape compliance.** (CP44) — In roam: a verdict like `verdict: "Run roam preflight handleSave"` must produce a literally executable command. The 7919-partition `partition` output technically conforms to schema but is *not actionable* — the partition count is unusable. Shape and executability are separate quality axes; both must pass.

### "Never N/A without running it" — the operational rule

The single hardest-earned lesson from the 212-eval corpus. Three commands were marked N/A by judgment at the 59-eval midpoint (`py_modern`, `py_types`, `metrics_push`). All 3 returned real signal when actually invoked:

- `py_modern` discovered Python files in `scripts/` (judged "no Python repo" — wrong)
- `py_types` reported 90% type coverage on those files
- `metrics_push --dry-run` exposed a **unique `danger_score` metric** not surfaced by any other command

**When adding tests / dogfooding / triaging: run every command at least once.** Empty output is itself signal; non-empty output on a "no X" project is the strongest signal of all. See `internal/dogfood/EVALS-HOW-TO.md` for the full lesson.

### Adding-a-command checklist (informed by patterns 1-6 above)

Before merging a new `cmd_X.py`:

- [ ] JSON mode handles empty input cleanly — emit a non-empty envelope, never empty stdout (Pattern 1)
- [ ] `summary.verdict` is a single line that works without any other field (LAW 6)
- [ ] `summary.partial_success: true` whenever ANY subcommand or check failed; no silent SAFE (Pattern 2)
- [ ] If output may exceed 20K tokens, use the handle pattern (`roam_explore` template) OR write to file (`graph-export` template) (Pattern 6)
- [ ] If it reports a "callers" / "complexity" / "rot" / "compliance" count, include a `<metric>_definition` field naming the precise computation (Pattern 3)
- [ ] If it's a compound recipe, reference subcommands by **registry key** not by string-templated CLI invocation (Pattern 5)
- [ ] `agent_contract.facts` is flat, imperative, concrete-noun-anchored (LAWs 2, 4, 10)
- [ ] If it depends on missing state (no migrations / no audit trail / no diff), the verdict says so explicitly — `"chain not initialized"` not `"chain broken"` (Pattern 2)
- [ ] Add at least one MCP-level test that runs the compound's actual internal subcommand chain (catches `vuln`/`vulns`-class typos)
- [ ] Tool description uses imperative voice ("Run X") not declarative ("This command") (LAW 2)
- [ ] If it returns a verdict that names a follow-up command, that command must be a literal `roam <subcommand>` string — copy-paste-executable (CONSTRAINT 12)

## Quick reference

```bash
# Run tests
pytest tests/

# Run tests in parallel (requires pytest-xdist)
pytest tests/ -n auto

# Skip timing-sensitive perf tests
pytest tests/ -m "not slow"

# Run a single test file
pytest tests/test_comprehensive.py -x -v

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
  cli.py              # Click CLI entry point — LazyGroup, _COMMANDS dict, _CATEGORIES. 241 command names (234 canonical + 7 aliases).
  mcp_server.py       # FastMCP server (57 tools in core preset; 227 in `full`) + `roam mcp` CLI command
  mcp_extras/         # MCP-native enhancements: sampling, watcher, session, progress, completions
    sampling.py       # Sampling-driven result compression (summarize=True) via Context.sample
    watcher.py        # watchdog observer + notifications/resources/updated (opt-in via ROAM_MCP_WATCH)
    session.py        # Per-session symbol memory; auto-injected into retrieve/context ranking
    progress.py       # Phase-aware progress: parses indexer stderr for discover/parse/extract/resolve/graph
    completions.py    # FTS5-backed prefix completion for symbols/paths/commands + protocol-level handler
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
    bridge_django.py   # Django admin/serializer/form/URL → Model + view resolution
    bridge_config.py   # Env var reads → .env/.yml definitions
  catalog/
    tasks.py           # Universal algorithm catalog — 23 tasks with ranked solution approaches
    detectors.py       # Algorithm anti-pattern detectors — query DB signals to find suboptimal patterns
  languages/
    base.py            # Abstract LanguageExtractor — all languages inherit this
    registry.py        # Language detection + grammar aliasing
    *_lang.py          # One file per language (python, javascript, typescript, java, go, rust, c, csharp, php, ruby, kotlin, swift, scala, sql, foxpro, apex, aura, visualforce, sfxml, hcl, yaml, generic)
  graph/
    builder.py         # DB → NetworkX graph
    pagerank.py        # PageRank + centrality metrics
    cycles.py          # Tarjan SCC + tangle ratio
    clusters.py        # Louvain community detection
    layers.py          # Topological layer detection — returns {node_id: layer_number}
    pathfinding.py     # k-shortest paths for trace
    dark_matter.py     # Hidden co-change coupling detection
    diff.py            # Graph-level diff analysis
    propagation.py     # Propagation cost computation
    spectral.py        # Fiedler vector bisection + spectral gap
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
  # Agent-OS substrates (2026-05-12 sprint) — repo-local state under .roam/:
  #   constitution/  (constitution.yml gates), modes/ (read_only/safe_edit/migration/autonomous_pr),
  #   runs/ (HMAC-chained per-run event ledger), leases/ (multi-agent claims),
  #   memory/ (portable agent memory.jsonl), pr-bundles/ (proof-carrying PRs),
  #   laws/ (mined invariants), agents_md/ (AGENTS.md generator).
  #   Surfaced via: roam constitution, mode, runs, lease, memory, pr-bundle, laws,
  #     agents-md, brief, next, agent-score, intent-check, replay.
  commands/
    resolve.py         # Shared symbol resolution + ensure_index()
    changed_files.py   # Shared git changeset detection
    gate_presets.py    # Framework-specific gate rules + .roam-gates.yml loader
    graph_helpers.py   # Shared graph utilities (adjacency builders, BFS helpers)
    context_helpers.py # Data-gathering helpers extracted from cmd_context.py
    cmd_*.py           # One module per CLI command family (232 modules backing 241 command names)
  output/
    formatter.py       # Token-efficient text formatting, abbrev_kind(), loc(), format_table(), to_json(), json_envelope()
    sarif.py           # SARIF 2.1.0 output (--sarif flag on health/debt/complexity)
    schema_registry.py # JSON envelope schema versioning + validation
tests/                 # 1100+ test_*.py files
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
  test_backend_fixes_round3.py, test_exclude_patterns.py, test_math_tips.py,
  test_mcp_server.py
```

### Key patterns

- **Lazy-loading commands:** `cli.py` uses a `LazyGroup` that imports command modules only when invoked. This avoids importing networkx (~500ms) on every CLI call. Register new commands in `_COMMANDS` dict and `_CATEGORIES` dict.

- **Command template:** Every command follows this pattern:
  ```python
  from __future__ import annotations  # project convention (lazy annotations on 3.10+)
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

- **`from __future__ import annotations`** — Required at top of every source file. The project requires Python 3.10+ (`pyproject.toml`); the import keeps annotations lazy (cheaper import, safer forward references, avoids PEP 604 runtime evaluation) rather than acting as a 3.9 back-compat shim.

- **Batched IN-clauses:** Never write raw `WHERE id IN (...)` with a list > 400 items. Use `batched_in()` from `connection.py` instead.

- **`detect_layers()` returns `{node_id: layer_number}`** — a dict, not a list of sets. Convert if you need per-layer groupings.

- **Verdict-first output:** Key commands emit a one-line `VERDICT:` as the first text output line and include `verdict` in the JSON summary.

- **JSON envelope:** All JSON output uses `json_envelope(command_name, summary={...}, **data)`. The summary dict should include a `verdict` field. Envelopes automatically include `schema` and `schema_version` fields.

- **SARIF output:** Health/debt/complexity commands support `--sarif` flag for CI integration (GitHub Code Scanning, etc.).

## Agent OS substrate (the 2026-05-12 sprint shipped this)

### The control-plane thesis

Roam's base layer is local codebase intelligence: a SQLite-backed model of
symbols, calls, imports, dependencies, architecture, git history, risks,
smells, security flows, and algorithmic patterns. The Agent OS substrate is the
control-plane layer built on top of that model — it lets agents (a) earn the
right to change code via gates, (b) record their work in a tamper-evident
ledger, and (c) compose proof bundles a human reviewer can trust. Everything
below is repo-local (stored under `.roam/`), zero-network, and additive to the
analysis core.

### The 12 substrate packages

```
src/roam/atomic_io.py     - atomic_write_text/bytes/json (os.replace; POSIX+Windows safe)
src/roam/agents_md/       - AGENTS.md generator (compositional; consumes the rest)
src/roam/constitution/    - capstone .roam/constitution.yml unifying laws+rules+memory+gates
src/roam/db/findings.py   - cross-detector finding registry (roam findings list/show/count); USER_VERSION 18 schema
src/roam/laws/            - invariant mining (roam laws mine/check) - self-installing
src/roam/leases/          - multi-agent coordination (roam lease claim/release/list)
src/roam/memory/          - repo-local agent memory (.roam/memory.jsonl)
src/roam/modes/           - 4 cumulative modes: read_only/safe_edit/migration/autonomous_pr
src/roam/policy/          - graph-aware rule clauses (reachable_from/imports_from/...)
src/roam/quality/         - canonical metric definitions (ai_rot, cycles, god_components, public_symbols)
src/roam/runs/            - per-run event ledger + HMAC tamper-detection (roam runs verify)
src/roam/world_model/     - 4 detectors: side_effects, idempotency, causal_graph, tx_boundaries
```

### The agent loop (the canonical workflow this enables)

```
1.  roam runs start             - open run, get ROAM_RUN_ID (HMAC-signed events)
2.  roam mode safe_edit         - declare action surface
3.  roam pr-bundle init         - start proof bundle
4.  roam preflight <sym>        - gate before edit (auto-logs to active run)
5.  roam impact <sym>           - blast radius (auto-logs)
6.  <edit>
7.  roam diff | roam critique   - review (auto-logs)
8.  roam pr-bundle emit         - close bundle with proofs
9.  roam runs end --with-pr-bundle-emit
10. roam replay <id>            - narrate the run
11. roam agent-score            - score the agent on 0..100 composite
```

### The 4 R28 World Model classifiers

```
side-effects   - classifies each symbol's effect kinds (io_read/io_write/mutation/process/none)
idempotency    - classifies safe-to-retry (idempotent/non_idempotent/unknown)
causal-graph   - traces param -> sink dependency edges per symbol
tx-boundaries  - detects begin/commit/rollback regions; flags unsafe_mutation outside transactions
```

### The agent-OS thesis check

"Roam helps agents earn the right to change code." The substrate exists when an
agent can: read the constitution -> check active mode -> claim leases -> emit a
pr-bundle with proof of preflight+impact+critique -> commit only if the ledger
chain verifies (`roam runs verify`) AND the bundle validates with `--strict`.
Every other piece (laws, memory, world-model, agents-md, brief, next, intent-check,
replay, agent-score) feeds one of those four verbs.

### MCP boundary security (runtime wave, sealed 2026-05-18)

Roam ships structured evidence emission as the security stance at the MCP
boundary. As of 2026-05-18 the P0/P1/P2 wave is sealed. Full integrator
spec: `dev/MCP-SECURITY-POSTURE.md` (companion doc for Interlock / Lasso /
Portkey / MintMCP gateway authors). Canonical dataclass:
`src/roam/evidence/mcp_receipt.py`. JSON Schema emitter:
`scripts/export_mcp_receipt_schema.py`. Public reply:
https://github.com/Cranot/roam-code/discussions/37#discussioncomment-16967163.
Agent-developer landing page:
`templates/distribution/landing-page/docs/agent-contract.html`.

- **Egress redaction (MCP-P0.1, shipped).** Sensitive MCP results are
  scrubbed of producer-boundary secret patterns (GitHub PAT classic +
  fine-grained, `sk-` keys, AWS AKIA, Bearer tokens, PEM blocks, JWT)
  BEFORE returning to the client AND before `output_hash` is computed.
  Redactor: `src/roam/security/redact.py`
  (`redact_secrets_in_string` / `redact_secrets_in_value`); wire-up at
  `_wrap_with_receipt` in `src/roam/mcp_server.py`. Per-pattern hit map
  surfaces in `extra["redaction_details"]`.
- **Mode-gate enforcement at the MCP boundary (MCP-P0.2, shipped).**
  `_evaluate_mcp_mode_policy` + `_build_mode_blocked_envelope` wire the
  4-mode substrate (`read_only` / `safe_edit` / `migration` /
  `autonomous_pr`) into the MCP dispatcher. Receipts now carry a real
  decision from the closed `policy_decision` enum, not hard-coded
  `"allow"`.
- **HMAC-link receipts to the signed event stream (MCP-P0.3, shipped).**
  Each receipt's sha256 anchors into a signed ledger event;
  `verify_chain_with_receipts()` in `src/roam/runs/signing.py` extends
  the offline envelope with a `receipt_integrity` closed enum. Pre-P0.3
  chains hash byte-identical (no migration).
- **Shadow-mode dry-run (MCP-P1.1, shipped).** `ROAM_MODE_DRY_RUN=1`
  flips the P0.2 mode gate into observe-only — denials are emitted as
  receipts but the call proceeds. Gateways can stage policy changes
  without raising.
- **Per-tool side-effect declarations (MCP-P2.1, shipped).** Every
  `@_tool` wrapper carries declared `read_only` / `destructive` /
  `idempotent` flags in `_TOOL_METADATA`; receipts surface them as
  `declared_side_effects`. A gateway can reject calls whose declared
  effects exceed caller authority before the call lands at the server.
- **JSON Schema export for receipts (MCP-P2.2, shipped).**
  `scripts/export_mcp_receipt_schema.py` emits a JSON Schema
  Draft 2020-12 document for `McpDecisionReceipt` so gateway integrators
  can validate receipts without importing the Python dataclass.

**Closed-enum vocabulary** (membership validated at receipt construction;
unknown literals raise `ValueError`):

- `policy_decision` (6 values): `allow`, `deny`, `escalate`, `redact`,
  `not_evaluated`, `would_deny_dry_run`.
- `redactions` reasons (9 values, canonical W226 `REDACTION_REASONS`):
  `secret`, `pii`, `sensitive_content`, `size_limit`, `policy`,
  `user_opt_in_required`, `machine_local_path`, `schema_strict`,
  `producer_not_available`.
- `receipt_integrity` (4 values, emitted by `verify_chain_with_receipts`):
  `ok`, `missing`, `tampered`, `not_linked`.

**Three UX bugs sealed in the same wave**: `doctor` advisory exit-0
correction, `surface --json` top-level keys completion, and
`_meta.roam_version` stamped on every MCP receipt envelope.

### Where to look next (cross-links)

- `README.md` (this repo)            - public surface + headline counts
- `https://roam-code.com/docs/`      - hosted command reference, architecture, getting-started
- `templates/distribution/landing-page/docs/agent-contract.html` - agent-developer landing page (envelope shape + closed enums)
- `dev/MCP-SECURITY-POSTURE.md`      - MCP runtime-security integrator spec (gateway PEP authors)
- `src/roam/evidence/mcp_receipt.py` - canonical `McpDecisionReceipt` dataclass
- `scripts/export_mcp_receipt_schema.py` - JSON Schema Draft 2020-12 emitter (P2.2)
- https://github.com/Cranot/roam-code/discussions/37#discussioncomment-16967163 - public reply on the runtime-security posture
- Strategic planning, engineering ledger, dogfood corpus, and other internal-cadence memos live under `internal/` (folder-wide gitignored). Read the newest files there at session start.

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
4. **Decide MCP exposure.** Add a wrapper in `mcp_server.py` via `@_tool(name="roam_<canonical>")`
   UNLESS the command falls into one of these four "skip" categories:
   - **Setup / bootstrap** (e.g., `init`, `ci-setup`, `mcp-setup`, `hooks`, `pre-commit`,
     `index-export`, `graph-export`, `config`, `version`, `schema`, `surface`) — one-time
     human-driven; writes to disk and offers no value through a stateless MCP call.
   - **Local-state only** (e.g., `mode`, `memory`, `runs`, `lease`, `annotate`, `replay`,
     `suppress`, `permit`) — state lives on disk in `.roam/`; agents read the file directly.
   - **Daemon / long-running** (e.g., `watch`) — incompatible with stateless MCP invocations.
   - **REPL / interactive helpers** — N/A in MCP context.
   If the command doesn't fit any of these, add the wrapper.
   The advisory audit `tests/test_mcp_wrapper_coverage.py` surfaces commands that lack a
   wrapper and aren't in a skip-taxonomy allowlist; extend the allowlist (with rationale)
   when you intentionally skip MCP exposure.
5. **Add `@roam_capability(name="...", category="...", ...)` decorator** — the auto-derived
   capability-registry test (`tests/test_capability_decoration.py`) will fail without it
6. **If your command is an alias of an existing one** (sharing the same `(module, function)`
   tuple in `_COMMANDS`), add it to `_DEPRECATED_COMMANDS` in `cli.py` — the auto-test reads
   that dict to know which entries are exempt from decoration
7. **Anchor your `agent_contract.facts` strings on concrete-noun terminals** — see the
   "Concrete-noun anchor vocabulary" sub-section under LAW 4 above for the accepted terminal
   tokens and the `WRONG`/`RIGHT` worked example. The LAW 4 lint (`tests/test_law4_lint.py`)
   blocks merges on un-anchored facts.
8. Add tests

## Adding a new language (Tier 1)

1. Create `src/roam/languages/yourlang_lang.py` inheriting from `LanguageExtractor`
2. See `go_lang.py` or `php_lang.py` as clean templates
3. Register in `registry.py`
4. Add tests in `tests/`

## Writing a roam plugin

roam supports third-party `roam-plugin-*` packages — the substrate is in
`src/roam/plugins/` and the reference example is at `dev/example-plugin/`.
Framework-specific knowledge (nextjs, laravel, prisma, django, …) should ship
as a plugin rather than landing in core.

**Entry-point pattern.** Plugins register via Python entry points; roam
walks the `roam.plugins` group at startup:

```toml
# In your plugin's pyproject.toml
[project.entry-points."roam.plugins"]
nextjs = "roam_plugin_nextjs:register"
```

**`register(ctx)` signature.** Every plugin exposes a top-level
`register(ctx: RoamPluginContext) -> None` callable. The `ctx` argument
exposes typed methods for each extension point:

| Method                                                                          | Purpose                                                |
| ------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `ctx.declare(name, version, description)`                                       | Plugin identity (optional but recommended).           |
| `ctx.register_command(name, module_path, attr_name)`                            | Add a `roam <name>` CLI subcommand.                   |
| `ctx.register_detector(task_id, way_id, detect_fn)`                             | Add an algorithm-catalog detector.                    |
| `ctx.register_language_extractor(language, factory, *, extensions, grammar_alias)` | Add a per-language symbol/reference extractor.     |
| `ctx.register_framework_detector(detect_fn)`                                    | Detect which framework a project uses.                |
| `ctx.register_bridge(bridge)`                                                   | Add a cross-language reference bridge.                |

**Minimal example.** See `dev/example-plugin/`:

```python
def register(ctx):
    ctx.declare(name="example", version="0.1.0",
                description="Reference roam plugin")
    ctx.register_framework_detector(detect_framework)
    ctx.register_detector("example-task", "naive", detect_demo_finding)
```

**Discovery & safety.** Discovery is wrapped in `try/except` end-to-end —
a broken plugin records an error string visible via
`roam plugins doctor` but never crashes roam. For local development,
load a plugin without installing it via the env channel:

```bash
PYTHONPATH=dev/example-plugin ROAM_PLUGIN_MODULES=roam_plugin_example roam plugins list
```

Full typed surface lives in `src/roam/plugins/registry.py`. Tests live in
`tests/test_plugin_substrate.py` and `tests/test_plugin_discovery.py`.

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
- Optional: fastmcp >= 2.0 (MCP server — `pip install "roam-code[mcp]"`)
- Dev: pytest >= 7.0, pytest-xdist >= 3.0, ruff >= 0.4

## Version bumping

Update **one place only**: `pyproject.toml` → `version`

`__init__.py` reads it dynamically via `importlib.metadata`. README badge pulls from PyPI.

## Codebase navigation with roam

This project uses `roam` for codebase comprehension. Always prefer roam over Glob/Grep/Read exploration.

Before modifying any code:
1. First time in the repo: `roam understand` then `roam tour`
2. Find a symbol: `roam search <pattern>`
3. Free-form task ("trace login flow", "where is the n+1?"): `roam retrieve "<task>"` — graph-aware FTS5 + structural rerank, returns ranked spans within a token budget
4. Before changing a symbol: `roam preflight <name>` (blast radius + tests + fitness)
5. Need files to read: `roam context <name>` (files + line ranges, prioritized)
6. Debugging a failure: `roam diagnose <name>` (root cause ranking)
7. After making changes: `roam diff` (blast radius of uncommitted changes)
8. Verifying a patch: `git diff | roam critique` — clones-not-edited check + blast-radius (exit 5 on high severity)

Additional commands: `roam health` (0-100 score), `roam impact <name>` (what breaks),
`roam pr-risk` (PR risk score), `roam file <path>` (file skeleton),
`roam simulate move <sym> <file>` (what-if architecture), `roam orchestrate` (multi-agent partitioning),
`roam adversarial` (architectural challenges on changed files — composes cycles + clusters + layers + catalog + dead + complexity), `roam mutate move <sym> <file>` (code transforms),
`roam clones --persist` (populate `clone_pairs` so `critique` and `retrieve` can flag clone classes).

Index-aware text search (added on top of grep / refs):
- `roam grep <pattern> [--reachable-from <entry>] [--unreachable] [--co-occur] [--missing-pattern P] [--rank-by importance] [--group-by symbol] [--blame] [--heat]` — grep + reachability + PageRank + clones + bridges. Supports `-e` repeatable, `--patterns-from FILE`, `-g` repeatable, `-F`. Engine: ripgrep > git grep > fallback (pin via `ROAM_GREP_ENGINE`).
- `roam refs-text <string>...` — string audit with verdict (SAFE-TO-REMOVE / REVIEW / LOAD-BEARING). Groups refs by surface (code/test/docs/config/dead) and annotates reachability.
- `roam delete-check [--source working|staged|pr|head] [--ci]` — gates the diff on surviving references; exits 5 on BREAK-RISK with `--ci`.
- `roam history-grep <pattern> [--polarity]` — git pickaxe (-S/-G) with author/date and introduced/removed annotation.

Run `roam --help` for the 5-verb core; `roam --help-all` for all 241 command names; `roam surface --json` for the machine-readable inventory. Use `roam --json <cmd>` for structured output.
Use `roam --sarif health` for CI integration (SARIF 2.1.0).
