# roam-code Backlog — All Ideas, Plans & Unfinished Work

Consolidated: 2026-02-19

This document collects every TODO, idea, planned feature, and unfinished work
item from across the codebase into one place.

---

## Table of Contents

**Existing Work (Fork Analysis + Field Testing)**

1. [README Roadmap (Not Yet Done)](#1-readme-roadmap-not-yet-done)
2. [Idea A: Superior Test Suite](#2-idea-a-superior-test-suite)
3. [Idea B: Cross-Language Edge Resolution](#3-idea-b-cross-language-edge-resolution)
4. [Idea C: Health Trend Anomaly Detection](#4-idea-c-health-trend-anomaly-detection)
5. [Idea D: Smart File Role Categorization](#5-idea-d-smart-file-role-categorization)
6. [Idea E: Dead Code Aging](#6-idea-e-dead-code-aging)
7. [Idea F: Pluggable Test Naming Conventions](#7-idea-f-pluggable-test-naming-conventions)
8. [Idea G: Coverage-Gaps Policy-as-Code](#8-idea-g-coverage-gaps-policy-as-code)
9. [Idea H: PR-Risk Team-Aware Novelty](#9-idea-h-pr-risk-team-aware-novelty)
10. [Idea I: Backend Command Improvements](#10-idea-i-backend-command-improvements)
11. [Tier 3 Ideas (Concept Only)](#11-tier-3-ideas-concept-only)

**Next-Generation Ideas (External Feedback — Round 1)**

12. [N1: Architectural Simulations — `roam simulate`](#12-n1-architectural-simulations--roam-simulate)
13. [N2: Agentic Memory — `roam annotate`](#13-n2-agentic-memory--roam-annotate)
14. [N3: Reachability-Based Security — CVE Mapping](#14-n3-reachability-based-security--cve-mapping)
15. [N4: Semantic Blast-Radius Prompting](#15-n4-semantic-blast-radius-prompting)
16. [N5: OpenTelemetry Overlay — Runtime Weights](#16-n5-opentelemetry-overlay--runtime-weights)
17. [N6: Local Vector Embeddings — `sqlite-vec`](#17-n6-local-vector-embeddings--sqlite-vec)
18. [N7: Predictive Tech Debt — `roam forecast`](#18-n7-predictive-tech-debt--roam-forecast)

**Paradigm-Shift Ideas (External Feedback — Round 2)**

19. [P1: Swarm Orchestration — `roam orchestrate`](#19-p1-swarm-orchestration--roam-orchestrate)
20. [P2: Dark Matter Detection — `roam dark-matter`](#20-p2-dark-matter-detection--roam-dark-matter)
21. [P3: Syntax-less Agentic Editing — `roam mutate`](#21-p3-syntax-less-agentic-editing--roam-mutate)
22. [P4: Graph-Isomorphism Transfer — `roam fingerprint`](#22-p4-graph-isomorphism-transfer--roam-fingerprint)
23. [P5: Adversarial Architecture Review — `roam adversarial`](#23-p5-adversarial-architecture-review--roam-adversarial)

**Workflow Integration Ideas (External Feedback — Round 3)**

24. [W1: Graph Diff as PR Artifact — `roam pr-diff`](#24-w1-graph-diff-as-pr-artifact--roam-pr-diff)
25. [W2: Architecture Budget Gates — `roam budget`](#25-w2-architecture-budget-gates--roam-budget)
26. [W3: Invariant Discovery — `roam invariants`](#26-w3-invariant-discovery--roam-invariants)
27. [W4: Graph-Path Test Coverage — `roam path-coverage`](#27-w4-graph-path-test-coverage--roam-path-coverage)
28. [W5: Agent Work Planner — `roam plan`](#28-w5-agent-work-planner--roam-plan)
29. [W6: Intent Graph from Docs — `roam intent`](#29-w6-intent-graph-from-docs--roam-intent)
30. [W7: Minimum Cut Safety Zones — `roam cut`](#30-w7-minimum-cut-safety-zones--roam-cut)
31. [W8: Plugin DSL for Detectors — `.roam/rules/`](#31-w8-plugin-dsl-for-detectors--roamrules)
32. [Cross-References: Round 3 Overlaps](#32-cross-references-round-3-overlaps)

**Deep Foundation Ideas (External Feedback — Round 4)**

33. [D1: Time-Travel Graph — `roam bisect`](#33-d1-time-travel-graph--roam-bisect)
34. [D2: Proof-Carrying PRs — `roam attest`](#34-d2-proof-carrying-prs--roam-attest)
35. [D3: Effect & Side-Effect Graph — `roam effects`](#35-d3-effect--side-effect-graph--roam-effects)
36. [D4: Minimal-Change Synthesis — `roam closure`](#36-d4-minimal-change-synthesis--roam-closure)
37. [D5: Sanitized Roam Capsule — `roam capsule`](#37-d5-sanitized-roam-capsule--roam-capsule)
38. [Cross-References: Round 4 Overlaps](#38-cross-references-round-4-overlaps)

**Execution**

39. [Execution Playbook — Agent-Ready Prompts](#39-execution-playbook--agent-ready-prompts)

**Meta**

40. [In-Code Markers](#40-in-code-markers)
41. [Status Summary](#41-status-summary)

---

## 1. README Roadmap (Not Yet Done)

These items appear as unchecked in `README.md` lines 1250-1254.

| Item                                       | Effort Est. | Notes |
|--------------------------------------------|-------------|-------|
| Terminal demo GIF                          | Small       | Marketing / onboarding asset |
| Ruby Tier 1 support                        | Medium      | Currently falls back to GenericExtractor; needs `ruby_lang.py` |
| `--sarif` CLI flag for direct SARIF export | Small       | `output/sarif.py` exists but is not wired to a CLI flag |
| Docker image for CI                        | Small       | No Dockerfile exists |
| VS Code extension                          | Large       | No extension project exists |

---

## 2. Idea A: Superior Test Suite

**Source:** `docs/ideas/A_test_coverage.md` | **Priority:** HIGH | **Effort:** Medium (2-3 sessions)

**Goal:** Write 12 test files (~545 tests) using CliRunner, shared fixtures, JSON
contract validation, snapshot testing, and property-based testing.

**Status: DONE**

Implemented files:
- `test_smoke.py`, `test_json_contracts.py`, `test_formatters.py`, `test_languages.py`
- `test_commands_exploration.py`, `test_commands_health.py`, `test_commands_architecture.py`
- `test_commands_workflow.py`, `test_commands_refactoring.py`
- `test_properties.py` — Property-based tests for search, formatters, `batched_in`
- `test_index.py` — Full/incremental indexing integration tests, language detection

---

## 3. Idea B: Cross-Language Edge Resolution

**Source:** `docs/ideas/B_cross_language_edges.md` | **Priority:** STRATEGIC (Tier 1) | **Effort:** High (phased)

**Goal:** Generalize the Salesforce cross-language pattern into a plugin architecture
supporting ANY multi-language monorepo (Python+JS, Go+Proto, Java+Kotlin, Ruby+ERB).

Three-layer architecture:
1. Canonical symbol namespace (extended `qualified_name` matching)
2. Bridge plugins (auto-discovered `bridge_*.py` files)
3. Heuristic string-literal matching (REST URLs, CSS classes, SQL tables, config refs)

**Status: DONE**

Implemented:
- `bridges/base.py` — `LanguageBridge` ABC
- `bridges/registry.py` — Bridge auto-discovery (5 bridges)
- `bridges/bridge_salesforce.py` — Apex/Aura/LWC/Visualforce
- `bridges/bridge_protobuf.py` — .proto to Go/Java/Python stubs
- `bridges/bridge_rest_api.py` — URL string matching frontend<->backend
- `bridges/bridge_template.py` — Template engine to host language (Jinja2/Django/ERB/Handlebars)
- `bridges/bridge_config.py` — Config/env var reads to .env/.yml definitions
- Schema columns: `bridge TEXT` and `confidence REAL` on edges table
- `roam x-lang` command showing cross-language boundaries
- Tests in `test_bridges.py` and `test_bridges_extended.py`

---

## 4. Idea C: Health Trend Anomaly Detection

**Source:** `docs/ideas/C_health_anomaly_detection.md` | **Priority:** HIGH | **Effort:** Medium

**Goal:** Add statistical anomaly detection, trend forecasting, and pattern alerts to
`roam trend`. Algorithms: Modified Z-Score (MAD), Theil-Sen robust slope, Mann-Kendall
significance, Western Electric Rules, CUSUM, delta-based analysis.

**Status: DONE**

`graph/anomaly.py` exists. Tests in `test_anomaly.py`.

---

## 5. Idea D: Smart File Role Categorization

**Source:** `docs/ideas/D_smart_file_categorization.md` | **Priority:** HIGH | **Effort:** Medium

**Goal:** Three-tier heuristic file classifier (path-based, filename+extension,
content-based). New `file_role` column. Replaces static exclude lists.

Roles: source, test, config, build, docs, generated, vendored, data, examples,
dotfile, media, scripts, ci.

**Status: DONE**

`index/file_roles.py` exists. Tests in `test_file_roles.py`.

---

## 6. Idea E: Dead Code Aging

**Source:** `docs/ideas/E_dead_code_aging.md` | **Priority:** MEDIUM | **Effort:** Medium

**Goal:** Add time-based analysis to `roam dead`: decay score (0-100), effort
estimation in minutes, staleness categories (Fresh/Stale/Decayed/Fossilized).
All data already exists in DB (blame, complexity, clusters, coupling).

**Status: DONE**

Tests in `test_dead_aging.py`.

---

## 7. Idea F: Pluggable Test Naming Conventions

**Source:** `docs/ideas/F_pluggable_test_conventions.md` | **Priority:** MEDIUM | **Effort:** Medium

**Goal:** Pluggable adapter system for 13 languages, 25+ frameworks. Consolidates
duplicated `_is_test_file()` logic. Adds source-to-test reverse mapping.

**Status: DONE**

`index/test_conventions.py` exists. Tests in `test_test_conventions.py`.

---

## 8. Idea G: Coverage-Gaps Policy-as-Code

**Source:** `docs/ideas/G_coverage_gaps_policy.md` | **Priority:** MEDIUM | **Effort:** Medium

**Goal:** Auto-detect 12 frameworks from dependency manifests. Ship built-in gate
presets. Support `.roam-gates.yml` policy files with exemptions.

Frameworks mapped: Express, Django, Flask, FastAPI, Spring, Rails, ASP.NET,
Gin/Chi, Laravel, Symfony, Next.js, JAX-RS.

**Status: DONE**

`commands/gate_presets.py` exists. Tests in `test_gate_presets.py`.

---

## 9. Idea H: PR-Risk Team-Aware Novelty

**Source:** `docs/ideas/H_pr_risk_team_aware.md` | **Priority:** MEDIUM | **Effort:** Low-Medium

**Goal:** Two new risk factors: author familiarity (exponential-decay weighted) and
minor contributor risk (Bird et al. finding: strongest defect predictor). Uses
existing schema. Privacy-first (opt-in, local-only, no leaderboards).

**Status: DONE**

Tests in `test_pr_risk_author.py`.

---

## 10. Idea I: Backend Command Improvements

**Source:** `docs/ideas/I_backend_command_improvements.md` | **Priority:** Mixed | **Effort:** Mixed

Field-tested findings from running `missing-index`, `migration-safety`, `auth-gaps`,
and `over-fetch` against a real-world Laravel multi-tenant codebase.

**Status: PARTIALLY DONE** — Some items fixed per git log, outstanding items below.

### 10.1 missing-index: Schema-prefixed table names — HIGH, Small

`_RE_SCHEMA_TABLE` captures `"{$schema}.users"` instead of bare `users`. Indexes
stored under interpolated key, queries resolved to bare name — no match.

Fix: Strip schema prefixes from captured table names (`rsplit(".", 1)[-1]`).

### 10.2 missing-index: Raw SQL CREATE INDEX not parsed — MEDIUM, Small

`_parse_migration_indexes()` only detects Laravel Blueprint patterns. Raw SQL
`CREATE INDEX` statements are ignored.

Fix: Add regex for `CREATE [UNIQUE] INDEX name ON [schema.]table(columns)`.

### 10.3 missing-index: Cross-model column attribution — LOW, Medium

When a service queries multiple models sequentially, `_infer_table_from_context()`
bleeds across query boundaries (looks back 2000 chars for nearest model reference).

Fix: Require model reference to be on same statement chain (nearest `;` boundary),
or cross-reference columns against migration-defined columns.

### 10.4 missing-index: Table name pluralization edge cases — LOW, Small

Missing pluralization rules: `Branch -> branchs` (should be `branches`),
irregular non-English names. Missing `-ch -> -ches`, `-sh -> -shes` rules.

Fix: Add more rules or look up `$table` property first.

### 10.5 migration-safety: Raw SQL guards not recognized — MEDIUM, Small

`_RE_HAS_COLUMN` only matches Laravel `hasColumn()`. Raw SQL `information_schema`
queries used as idempotency guards are not recognized.

Fix: Add `information_schema.columns` / `information_schema.tables` as alternative
guard patterns.

### 10.6 auth-gaps: Service provider route registration — LOW, Medium

Routes registered in `ServiceProvider::boot()` with `Route::middleware(['auth:sanctum'])`
are not detected. Controllers appear unprotected even when wrapped in auth middleware.

Fix: Scan `app/Providers/*.php` for `Route::` calls, or accept config listing
protected controller classes.

### 10.7 over-fetch: $hidden messaging improvement — LOW, Tiny

When `over-fetch` suggests `$hidden`, it should note that `$hidden`/`$visible` will
also hide fields from edit endpoints. Suggest API Resources as the proper fix for
CRUD apps where all fields are user-editable.

---

## 11. Tier 3 Ideas (Concept Only)

From `docs/fork_analysis_summary.md`. No detailed design docs exist.

| ID | Idea | Source | Description |
|----|------|--------|-------------|
| I (Tier 3) | Report template engine | chuckjewell | Go beyond presets — customizable report templates, scheduled CI reports, diff-aware reports |
| J | Context symbol relationship graph | chuckjewell | Show how batch-queried symbols relate to each other, add conflict detection |
| K | Risk chain heat visualization | chuckjewell | Score propagation through call chains, visual chain rendering |
| L | JSON envelope schema versioning | chuckjewell | Formal contract for consumers, backward-compat guarantees |

---

## 12. N1: Architectural Simulations — `roam simulate`

**Priority:** HIGH-STRATEGIC | **Effort:** High | **Status: NOT STARTED**

**The leap:** Roam currently analyzes the *current* state. This lets agents test
refactoring ideas *in memory* before writing a single line of code.

**Command:**
```bash
roam simulate move src/auth/utils.py::verify_token src/security/jwt.py
roam simulate extract src/api/views.py::checkout src/checkout/service.py
roam simulate merge src/utils/a.py src/utils/b.py
roam simulate delete src/legacy/old_handler.py
```

**What it does:** Clone the in-memory NetworkX graph, apply the proposed structural
change (move/extract/merge/delete nodes and re-wire edges), then recalculate:
- Health score delta
- Fiedler value (algebraic connectivity) delta
- Modularity Q-score delta
- Layer violations introduced/removed
- Cyclic dependency changes (Tarjan SCC)
- Affected symbol count (blast radius)

**Output:**
```
VERDICT: health +4, modularity +0.03, 0 new cycles, 12 symbols affected
  Fiedler:    0.0023 -> 0.0031 (+34.8%)  -- better connected
  Modularity: 0.412  -> 0.445  (+8.0%)   -- cleaner boundaries
  Cycles:     3 SCC  -> 3 SCC  (no change)
  Layers:     2 violations removed
  WARNING: creates cross-cluster edge between auth and payments
```

**Why it matters:** LLMs are bad at long-term architectural planning. With a rapid
feedback loop, agents can perform gradient descent on architecture — loop through
structural permutations until `roam simulate` yields the highest health score,
then actually write the code.

**Builds on:** `graph/builder.py`, `graph/pagerank.py`, `graph/cycles.py`,
`graph/clusters.py`, `graph/layers.py`, `cmd_health.py`.

**Key design constraint:** Must operate on a *copy* of the graph. Never mutate the
real index. The graph is already in-memory (NetworkX), so cloning is cheap.

**Implementation sketch:**
1. New `src/roam/commands/cmd_simulate.py`
2. New `src/roam/graph/simulate.py` — graph cloning + structural transforms
3. Reuse existing metric functions (they already accept a graph as input)
4. Register in CLI as `simulate` with subcommands (move/extract/merge/delete)

---

## 13. N2: Agentic Memory — `roam annotate`

**Priority:** HIGH-STRATEGIC | **Effort:** Medium | **Status: DONE**

**The leap:** Roam becomes a read/write persistent memory for AI agents.
Agents discover nuanced context (edge cases, gotchas, performance notes) and
write it directly to the graph. The next agent session inherits it all.

**Command:**
```bash
roam annotate src/auth/utils.py::verify_token \
  "Fails silently if JWT secret is empty string. Do not use for batch processing."

roam annotate src/db/connection.py::open_db \
  --tag=performance "Connection pool exhaustion above 50 concurrent calls."

roam annotate src/api/views.py::checkout \
  --tag=security "Validated: no SQL injection. Last reviewed 2026-02-15."
```

**Reading annotations:**
```bash
roam annotations src/auth/utils.py::verify_token   # all notes on a symbol
roam annotations --tag=security                      # all security notes
roam annotations --since=30d                         # recent annotations
roam context verify_token                            # auto-injects annotations
```

**Schema change:**
```sql
CREATE TABLE annotations (
    id INTEGER PRIMARY KEY,
    symbol_id INTEGER REFERENCES symbols(id),
    file_path TEXT,              -- if annotating a file, not a symbol
    tag TEXT,                    -- security, performance, gotcha, review, etc.
    content TEXT NOT NULL,
    author TEXT,                 -- agent name or user
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT              -- optional TTL for temporary notes
);
```

**Why it matters:** AI agents are stateless. If an agent discovers that
`verify_token` breaks with empty strings, that insight is lost when the session
ends. With annotations, roam becomes a **shared, persistent semantic memory for
AI swarms**. `roam context <symbol>` auto-injects historical annotations so every
agent session starts with accumulated institutional knowledge.

**Builds on:** `db/schema.py`, `db/connection.py`, `commands/resolve.py`
(symbol resolution), `cmd_context.py` (annotation injection point).

**Key design decisions:**
- Annotations survive `roam index` (separate table, not recomputed)
- Symbol renames: match by `qualified_name`, not by `id` (IDs change on reindex)
- TTL support: temporary notes auto-expire (e.g., "WIP: refactoring in progress")
- Tags are free-form but suggested: security, performance, gotcha, review, wip
- MCP tool: `annotate_symbol` and `get_annotations` for programmatic access

---

## 14. N3: Reachability-Based Security — CVE Mapping

**Priority:** MEDIUM-HIGH | **Effort:** Medium | **Status: NOT STARTED**

**The leap:** Map known vulnerabilities from standard audit tools onto the
call graph, showing the exact topological path from public API routes down to
vulnerable dependency calls.

**Command:**
```bash
# Ingest vulnerability reports
roam vuln-map --npm-audit audit.json
roam vuln-map --pip-audit pip-audit.json
roam vuln-map --trivy trivy-results.json
roam vuln-map --osv osv-output.json

# Query reachability
roam vuln-reach                          # all reachable vulns
roam vuln-reach --from "POST /login"     # specific entry point
roam vuln-reach --cve CVE-2024-1234      # specific vulnerability
```

**Output:**
```
VERDICT: 3 reachable vulnerabilities, 1 critical path

CVE-2024-1234 (lodash.merge prototype pollution) — CRITICAL
  Path: POST /api/login
    -> src/api/auth.py::login_handler (line 42)
    -> src/services/user.py::merge_profile (line 118)
    -> node_modules/lodash/merge.js::merge
  Distance: 3 hops | Entry points: 2 | Blast radius: 14 symbols

CVE-2024-5678 (pyyaml unsafe load) — HIGH
  Path: POST /api/config
    -> src/api/settings.py::update_config (line 67)
    -> src/config/parser.py::load_yaml (line 23)
    -> yaml.unsafe_load
  Distance: 3 hops | Entry points: 1 | Blast radius: 8 symbols

lodash.template (no CVE, flagged by npm audit) — NOT REACHABLE
  No path from any public endpoint. Safe to deprioritize.
```

**Why it matters:** Most security tools say "you have a vulnerable lodash." Roam
tells agents the *exact structural path* from public entry points to the vulnerable
call, enabling targeted patching or firewall insertion at the right graph node.
Unreachable vulnerabilities are explicitly flagged as low-priority.

**Builds on:** `graph/pathfinding.py` (k-shortest paths), `cmd_auth_gaps.py`
(BFS reachability from routes), `cmd_impact.py` (transitive dependency tracing).

**Implementation sketch:**
1. New `src/roam/commands/cmd_vuln_map.py` — parses audit JSON formats
2. New `src/roam/security/vuln_graph.py` — maps package+function to graph nodes
3. Reuse `graph/pathfinding.py` for route-to-vuln path computation
4. Mapping layer: vuln report names (e.g., `lodash.merge`) to `qualified_name`
   in the symbol index (fuzzy matching + import tracing)

**Key challenge:** Mapping third-party function names from audit reports to actual
call sites in user code. Two approaches:
- A) Import tracing: find `import lodash` / `require('lodash')` and follow refs
- B) String matching: match function names from vuln report against reference table

---

## 15. N4: Semantic Blast-Radius Prompting

**Priority:** MEDIUM-HIGH | **Effort:** Medium | **Status: NOT STARTED**

**The leap:** Instead of just showing what might break, generate an
LLM-optimized prompt that forces agents to write tests targeting the most
critical, highly-coupled integration points of a changed symbol.

**Command:**
```bash
roam test-prompt calculate_tax
roam test-prompt --depth 3 checkout_cart
roam test-prompt --format pytest src/billing/invoice.py::generate
```

**Output (copy-pasteable prompt for an LLM):**
```
Write tests for `calculate_tax` (src/billing/tax.py:45).

CRITICAL dependencies to mock or test through:
  1. db.query (distance 2, fan-in 34) — database call, MUST mock
  2. get_tax_rates (distance 1, fan-in 3) — pure function, test directly
  3. apply_discount (distance 1, fan-in 12) — shared by checkout flow

CALLERS that will break if behavior changes:
  1. checkout_cart (distance 1, PageRank 0.034) — highest-traffic caller
  2. generate_invoice (distance 1, PageRank 0.021)
  3. monthly_report (distance 2, PageRank 0.008)

EDGE CASES from graph structure:
  - calculate_tax is in a cycle with apply_discount (mutual recursion)
  - 3 callers pass None for `region` parameter (from reference analysis)
  - Last modified 142 days ago by an author no longer active

Test these specific integration boundaries:
  - checkout_cart -> calculate_tax -> db.query (the hot path)
  - generate_invoice -> calculate_tax -> get_tax_rates (the edge case path)
```

**Why it matters:** Agents frequently write shallow tests that only cover the
happy path. By using graph centrality, edge distances, and coupling data, roam
generates prompts that target the *structurally most important* integration
points — the exact places where bugs propagate.

**Builds on:** `cmd_context.py` (dependency gathering), `cmd_impact.py`
(caller analysis), `cmd_preflight.py` (blast radius), `graph/pagerank.py`
(centrality ranking), `graph/pathfinding.py` (path computation).

**Implementation sketch:**
1. New `src/roam/commands/cmd_test_prompt.py`
2. Gather: callees (mock targets), callers (regression targets), cycles, churn
3. Rank by PageRank + fan-in to prioritize what to mention
4. Format as a structured prompt with sections (dependencies, callers, edge cases)
5. `--format` flag: pytest, jest, go-test, generic

---

## 16. N5: OpenTelemetry Overlay — Runtime Weights

**Priority:** MEDIUM | **Effort:** High | **Status: NOT STARTED**

**The leap:** Overlay production runtime data onto the static graph. Functions
ranked not just by git churn and complexity, but by actual production traffic.

**Command:**
```bash
# Ingest traces
roam ingest-trace prod-trace.json          # OpenTelemetry JSON
roam ingest-trace --jaeger jaeger.json     # Jaeger format
roam ingest-trace --zipkin zipkin.json      # Zipkin format

# Query with runtime weights
roam weather --runtime                     # hot spots by traffic, not just churn
roam map --runtime                         # architecture colored by traffic
roam context verify_token --runtime        # includes runtime stats
```

**Schema change:**
```sql
CREATE TABLE runtime_stats (
    symbol_id INTEGER REFERENCES symbols(id),
    trace_source TEXT,           -- "otel", "jaeger", "zipkin"
    call_count INTEGER,          -- total invocations in trace window
    p50_latency_ms REAL,
    p99_latency_ms REAL,
    error_rate REAL,             -- 0.0-1.0
    last_seen TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

**Output example (roam weather --runtime):**
```
VERDICT: 4 runtime hot spots (static analysis missed 2)

  src/api/auth.py::verify_token
    Static:  churn=12, CC=8, PageRank=0.034  -- ranked #7
    Runtime: 142K calls/day, p99=340ms, err=0.2%  -- ranked #1
    >> UPGRADE: statically safe but runtime-critical

  src/db/pool.py::get_connection
    Static:  churn=2, CC=3, PageRank=0.008  -- ranked #31
    Runtime: 89K calls/day, p99=12ms, err=0.01%  -- ranked #3
    >> UPGRADE: low-churn but high-traffic utility
```

**Why it matters:** Static analysis has a blind spot: a function can be simple
and rarely changed but handle 80% of production traffic. Roam can say "this is
statically safe to change, but be careful — it handles most of your runtime
traffic." Solves the stated limitation ("No runtime analysis") without building
a heavy runtime tool.

**Builds on:** `db/schema.py` (new table), `cmd_weather.py` (ranking),
`cmd_map.py` (visualization), `cmd_context.py` (enrichment).

**Key design constraints:**
- Trace ingestion is a one-shot import, not a live connection
- Map trace spans to symbols via function name + file path matching
- Unmatched spans are reported but don't block ingestion
- Runtime data is advisory (separate column/flag), never overwrites static metrics
- Zero new runtime dependencies (JSON parsing only)

---

## 17. N6: Local Vector Embeddings — `sqlite-vec`

**Priority:** MEDIUM | **Effort:** High | **Status: DONE (TF-IDF lite approach)**

**The leap:** Semantic search over the codebase by meaning, not just string
matching. "Where do we handle credit card retries?" returns the exact symbol
without needing to know the function name.

**Command:**
```bash
roam search-semantic "where do we handle credit card retries"
roam search-semantic "database connection pooling logic"
roam search-semantic "error handling for external API calls"
```

**Output:**
```
  0.89  src/billing/retry.py::retry_charge      (fn, 34 lines)
  0.84  src/billing/payment.py::process_payment  (fn, 67 lines)
  0.71  src/billing/stripe.py::create_charge     (fn, 45 lines)
```

**How it works:**
1. During `roam index`, generate embeddings for each symbol using:
   - Option A: TF-IDF vectors from symbol name + docstring + parameter names
     (zero dependencies, fast, ~70% accuracy)
   - Option B: Local ONNX model (e.g., all-MiniLM-L6-v2) for dense embeddings
     (~95% accuracy, adds ~20MB optional dependency)
2. Store vectors in SQLite via `sqlite-vec` extension (zero-dependency C extension)
3. Query: embed the search string, do cosine similarity against stored vectors

**Schema change:**
```sql
-- sqlite-vec virtual table
CREATE VIRTUAL TABLE symbol_embeddings USING vec0(
    symbol_id INTEGER PRIMARY KEY,
    embedding FLOAT[384]         -- dimension depends on model
);
```

**Why it matters:** Maintains the "100% local, zero API keys" promise while adding
semantic RAG capabilities. Agents working with unfamiliar codebases can describe
*what they're looking for* instead of guessing function names.

**Builds on:** `db/schema.py`, `db/connection.py`, `index/indexer.py` (embedding
generation during index pipeline), `cmd_search.py` (new search mode).

**Key design decisions:**
- TF-IDF as default (zero deps), ONNX model as optional `pip install roam[semantic]`
- `sqlite-vec` ships as a loadable extension (single .so/.dll file)
- Embedding generation adds ~2-5s to `roam index` for a 10K-symbol codebase
- Hybrid search: combine vector similarity with existing string matching

**New dependency:** `sqlite-vec` (zero-dependency SQLite extension, ~200KB)

---

## 18. N7: Predictive Tech Debt — `roam forecast`

**Priority:** MEDIUM | **Effort:** Medium | **Status: NOT STARTED**

**The leap:** Measure the *derivative* of complexity over time. Instead of
"this is a god object," roam says "this function is accreting complexity at
a super-linear rate and will become unmaintainable in ~40 commits."

**Command:**
```bash
roam forecast                         # all metrics
roam forecast --symbol process_order  # specific function
roam forecast --horizon 50            # look ahead 50 commits
roam forecast --alert-only            # only show accelerating debt
```

**How it works:**
1. Walk git history at intervals (HEAD~10, HEAD~20, ..., HEAD~N)
2. For each checkpoint, parse the file and compute: CC, fan-in, fan-out, LOC
3. Fit Theil-Sen slope to each metric series (already implemented in `graph/anomaly.py`)
4. Detect super-linear growth via second derivative (acceleration)
5. Extrapolate to predict when thresholds will be crossed

**Output:**
```
VERDICT: 3 functions accelerating toward debt thresholds

  src/api/views.py::process_order
    CC: 14 -> 18 -> 23 over last 30 commits (acceleration: +0.3/commit^2)
    Forecast: CC will exceed 40 in ~38 commits
    Fan-in: stable at 7 (no concern)
    ACTION: decompose now — complexity is growing super-linearly

  src/db/queries.py::build_report_query
    LOC: 89 -> 134 -> 201 over last 30 commits (acceleration: +1.2/commit^2)
    Forecast: LOC will exceed 300 in ~25 commits
    Fan-in: growing linearly at +0.4/commit
    ACTION: extract sub-queries — function is accumulating responsibilities

  src/auth/permissions.py::check_access
    CC: stable at 11 (linear growth, no acceleration)
    Fan-in: 4 -> 8 -> 15 (acceleration: +0.2/commit^2)
    Forecast: fan-in will exceed 25 in ~50 commits — emerging god function
    ACTION: consider interface extraction to decouple callers
```

**Why it matters:** Current health metrics are point-in-time snapshots. Forecasting
adds a time dimension — agents can proactively decompose functions *before* they
become unmaintainable. The acceleration metric (second derivative) specifically
catches functions where debt is compounding, not just growing.

**Builds on:** `graph/anomaly.py` (Theil-Sen slope, Mann-Kendall already
implemented), `index/git_stats.py` (git history walking), `cmd_trend.py`
(existing trend infrastructure), Idea C (anomaly detection — DONE).

**Key difference from Idea C:** Idea C detects anomalies in *aggregate* metrics
(total cycles, total dead exports) across snapshots. N7 operates at the
*per-symbol* level, tracking individual function trajectories through git history.

**Implementation sketch:**
1. New `src/roam/commands/cmd_forecast.py`
2. New `src/roam/index/symbol_history.py` — walk git history, parse at checkpoints
3. Reuse `graph/anomaly.py` for Theil-Sen + acceleration detection
4. Cache historical metrics in a new `symbol_history` table to avoid re-parsing

**Performance concern:** Parsing git history at N checkpoints for M files is
O(N*M). Mitigate by: only analyzing files with recent churn, caching parsed
results, and limiting default horizon to 30 commits.

---

## 19. P1: Swarm Orchestration — `roam orchestrate`

**Priority:** PARADIGM-SHIFT | **Effort:** Very High | **Status: NOT STARTED**

**The thesis:** The biggest bottleneck in AI engineering is that you cannot put
10 autonomous agents on a single codebase. They overwrite files, create merge
conflicts, and produce incoherent architecture. Roam already computes the math
needed to solve this.

**What we already have:**
- `cluster_quality()` in `graph/clusters.py` computes per-cluster **conductance**
  (cut(S,S_bar) / min(vol(S), vol(S_bar))) — this IS the metric for how
  independent two clusters are
- `detect_clusters()` runs **Louvain community detection** — natural work boundaries
- `_against_mode()` in `cmd_coupling.py` computes **Lift** and **NPMI** for
  co-change coupling — reveals which files move together
- `build_symbol_graph()` and `build_file_graph()` in `graph/builder.py` give us
  the complete dependency topology

**Command:**
```bash
roam orchestrate "Migrate legacy billing to Stripe" --agents 5
roam orchestrate --staged --agents 3     # split current changeset
roam orchestrate --files src/billing/ src/payments/ --agents 4
```

**What it does:**
1. Identify the target sub-graph (from prompt, files, or staged changes)
2. Run Louvain clustering on the sub-graph
3. Compute inter-cluster conductance for all cluster pairs
4. Find the partition that minimizes cross-agent write-dependencies
5. For each partition, compute: shared read-only symbols (safe), shared write
   symbols (conflict risk), estimated blast radius
6. Emit N sub-prompts, one per agent, with:
   - Assigned files (exclusive write access)
   - Read-only context files (shared, do not modify)
   - Dependency interface contracts (what each agent should NOT change)
   - Suggested merge order (topological, leaves first)

**Output:**
```
VERDICT: 5 agents, 0 write conflicts, 2 read-only shared interfaces

Agent 1: billing/invoice.py, billing/tax.py (cluster: billing-core)
  Writes: 4 files, 12 symbols
  Reads:  db/connection.py (shared), models/order.py (shared)
  Contract: do NOT modify Order.total or db.query signature

Agent 2: payments/stripe.py, payments/webhook.py (cluster: payments)
  Writes: 3 files, 8 symbols
  Reads:  models/order.py (shared), config/stripe.py (shared)
  Contract: do NOT modify Order.status enum values

...

Merge order: Agent 3 -> Agent 1 -> Agent 5 -> Agent 2 -> Agent 4
Conflict probability: 0.02 (2 symbols in conductance boundary)
```

**Why it matters:** Roam becomes the **load balancer for multi-agent swarms**.
Parallelization is solved mathematically via graph isolation, not heuristically.
Agents can work in parallel on the same codebase with provable zero-conflict
guarantees (within the conductance threshold).

**Builds on:** `graph/clusters.py` (Louvain + conductance), `graph/builder.py`
(graph construction), `cmd_coupling.py` (NPMI/Lift temporal coupling),
`graph/layers.py` (topological ordering for merge sequence).

**Implementation sketch:**
1. New `src/roam/commands/cmd_orchestrate.py`
2. New `src/roam/graph/partition.py` — sub-graph extraction, multi-agent
   partitioning with conductance minimization, conflict probability estimation
3. Prompt template generator — structured sub-prompts per agent
4. MCP tools: `orchestrate_task`, `get_agent_assignment`

**Key challenges:**
- Prompt-to-subgraph mapping: how to identify target symbols from a natural
  language description (could leverage N6/sqlite-vec if available, or fall back
  to keyword matching against symbol names)
- Granularity: file-level vs symbol-level partitions (start with file-level)
- Agents need to respect contracts — roam can verify post-hoc via `roam diff`

**Prerequisite ideas:** N1 (simulate) for validating partition quality,
N2 (annotate) for agents to claim/lock their assigned files.

---

## 20. P2: Dark Matter Detection — `roam dark-matter`

**Priority:** HIGH-STRATEGIC | **Effort:** Medium | **Status: DONE**

**The thesis:** If two files change together frequently (high NPMI/Lift) but have
**zero static dependency edges** between them, you have found an invisible
architectural coupling — "dark matter." These are the bugs that span disconnected
systems and that no agent will ever find by reading code.

**What we already have:**
- `cmd_coupling.py` already computes **NPMI** and **Lift** for temporal co-change
  pairs, and flags pairs as "HIDDEN" when they have no structural edge
- `build_file_graph()` gives us the complete static dependency graph
- The temporal data lives in `git_file_changes` (co-change counts per commit)
- The static data lives in `edges` + `file_edges` tables

**Command:**
```bash
roam dark-matter                      # all hidden couplings
roam dark-matter --min-npmi 0.5       # strong hidden couplings only
roam dark-matter --explain            # hypothesize WHY they co-change
roam dark-matter --category           # group by likely cause
```

**What it does:**
1. Compute temporal coupling graph (NPMI/Lift for all file pairs with >= N co-changes)
2. Compute static dependency graph (file-level edges from imports/calls)
3. Subtract: dark_matter = temporal_edges - static_edges
4. For each dark-matter pair, hypothesize the hidden channel:
   - Shared DB table (both files contain SQL referencing same table name)
   - Shared config key (both files read same env var or config path)
   - Event bus (one file emits events, the other subscribes — matched by string)
   - Copy-pasted logic (high textual similarity, no shared abstraction)
   - Shared external API (both files call same HTTP endpoint)
   - Unknown (no hypothesis — flag for human investigation)

**Output:**
```
VERDICT: 7 dark-matter couplings found (3 shared-DB, 2 event-bus, 2 unknown)

  src/billing/invoice.py <-> src/reporting/monthly.py
    NPMI: 0.72 | Lift: 4.3 | Co-changes: 18 | Static edges: 0
    Hypothesis: SHARED-DB (both reference table 'invoices')
    Risk: changes to invoice schema silently break monthly reports

  src/auth/login.py <-> src/analytics/events.py
    NPMI: 0.61 | Lift: 3.1 | Co-changes: 12 | Static edges: 0
    Hypothesis: EVENT-BUS (login.py emits 'user.login', events.py subscribes)
    Risk: renaming event breaks analytics without any import error

  src/api/orders.py <-> src/workers/fulfillment.py
    NPMI: 0.58 | Lift: 2.8 | Co-changes: 9 | Static edges: 0
    Hypothesis: UNKNOWN
    Risk: hidden coupling — investigate manually
```

**Why it matters:** AI agents read code top-down through imports. They are blind
to couplings that exist through shared databases, event buses, config files, or
copy-pasted business logic. Dark matter detection gives agents a "sixth sense"
about how the system *behaves* versus how it is *written*. When an agent modifies
`invoice.py`, roam can warn: "This file has dark-matter coupling to
`monthly.py` via shared DB table — check that too."

**Builds on:** `cmd_coupling.py` (NPMI/Lift computation, "HIDDEN" flag already
exists), `graph/builder.py` (static graph), `db/queries.py` (co-change SQL).

**Implementation sketch:**
1. New `src/roam/commands/cmd_dark_matter.py`
2. New `src/roam/graph/dark_matter.py` — graph subtraction, hypothesis engine
3. Hypothesis engine: regex scan for shared DB tables, config keys, event strings
4. Integrate into `cmd_context.py` and `cmd_preflight.py` — auto-warn about
   dark-matter partners when analyzing a symbol

**Key insight:** The coupling command already flags "HIDDEN" pairs. Dark matter
is the formalization and expansion of that signal into a first-class concept
with hypothesis generation and integration into the context pipeline.

---

## 21. P3: Syntax-less Agentic Editing — `roam mutate`

**Priority:** PARADIGM-SHIFT | **Effort:** Very High | **Status: NOT STARTED**

**The thesis:** Currently, an agent uses roam to understand the graph, then drops
back to writing raw text strings into files — risking syntax errors, import
misses, and indentation bugs. What if agents could edit at the *graph level*
and roam generates the actual code changes?

**What we already have:**
- Full AST parsing via tree-sitter (`index/parser.py`)
- Symbol table with exact line ranges (`symbols` table: `line_start`, `line_end`)
- Qualified names and edge types (`edges` table with `kind`)
- Import tracking per file
- `build_symbol_graph()` gives the complete topology

**Command:**
```bash
# Add a dependency edge (roam generates the import + call site)
roam mutate add-call \
  --from src/api/views.py::checkout \
  --to src/metrics/tracking.py::log_event \
  --args "event='checkout', user_id=user.id"

# Move a symbol (roam rewrites all callers)
roam mutate move \
  src/auth/utils.py::verify_token \
  src/security/jwt.py

# Extract a method (roam splits a function)
roam mutate extract \
  src/api/views.py::process_order \
  --lines 45-67 \
  --name validate_inventory

# Rename (roam updates all references)
roam mutate rename \
  src/models/user.py::User.email_address \
  --to email
```

**What it does for `add-call`:**
1. Resolve source and target symbols in the index
2. Check if an import already exists in the source file; if not, generate one
3. Determine the correct import style for the language (Python: `from x import y`,
   JS: `import { y } from 'x'`, Go: `import "x"`)
4. Find the insertion point in the source function (end of function body by default,
   or `--at-line` for specific placement)
5. Generate the call statement with provided args
6. Write the file changes (or `--dry-run` to preview)
7. Re-index affected files

**What it does for `move`:**
1. Resolve the symbol and all its callers/importers
2. Copy the symbol definition to the target file
3. Add necessary imports to the target file
4. Update all caller files: rewrite imports to point to new location
5. Remove the symbol from the source file
6. Optionally add a re-export shim in the source file (`--shim` flag)
7. Re-index all affected files

**Output (dry-run):**
```
VERDICT: move verify_token — 4 files modified, 0 conflicts

  src/security/jwt.py (CREATE)
    +1  from src.auth.models import TokenPayload
    +2
    +3  def verify_token(token: str) -> TokenPayload:
    +4      ...  (23 lines moved from src/auth/utils.py)

  src/auth/utils.py (MODIFY)
    -45..67  def verify_token(...): ...  (removed)

  src/api/auth.py (MODIFY)
    -1  from src.auth.utils import verify_token
    +1  from src.security.jwt import verify_token

  src/middleware/jwt.py (MODIFY)
    -3  from src.auth.utils import verify_token
    +3  from src.security.jwt import verify_token

Run `roam mutate move ... --apply` to execute.
```

**Why it matters:** The agent stops acting like a typist and starts acting like a
software architect. It commands the graph to change shape, and roam handles the
syntax-level mechanics. This eliminates entire categories of agent errors:
- Wrong import paths
- Forgotten reference updates
- Indentation/syntax errors
- Orphaned imports after moves

**Builds on:** `index/parser.py` (tree-sitter AST), `index/symbols.py` (symbol
extraction with line ranges), `graph/builder.py` (dependency graph),
`commands/resolve.py` (symbol resolution).

**Implementation sketch:**
1. New `src/roam/commands/cmd_mutate.py` — CLI with subcommands
2. New `src/roam/refactor/` directory:
   - `move.py` — symbol relocation + reference rewriting
   - `rename.py` — qualified name change + reference update
   - `extract.py` — function extraction from line range
   - `add_call.py` — import insertion + call site generation
   - `codegen.py` — language-specific code generation (import styles, etc.)
3. MCP tools: `mutate_move`, `mutate_rename`, `mutate_add_call`, `mutate_extract`

**Key challenges:**
- Language-specific code generation (import syntax, indentation rules)
- Preserving formatting and comments around modified code
- Handling re-exports and backward-compatible shims
- Circular import prevention (check before writing)
- Start with Python-only, then extend to JS/TS, Go

**Prerequisite ideas:** N1 (simulate) for previewing the health impact of
mutations before applying them.

---

## 22. P4: Graph-Isomorphism Transfer — `roam fingerprint`

**Priority:** STRATEGIC | **Effort:** High | **Status: NOT STARTED**

**The thesis:** Every SaaS backend has roughly the same topological shape
(Routers -> Controllers -> Services -> ORM). Because roam abstracts syntax into
pure math (PageRank, Fiedler, conductance, layer structure), it can create a
universal language of software architecture independent of programming language.

**What we already have:**
- `compute_pagerank()` — node importance distribution
- `cluster_quality()` — modularity Q-score and per-cluster conductance
- `detect_clusters()` — Louvain community structure
- `detect_layers()` in `graph/layers.py` — topological layer assignment
- Fiedler value (algebraic connectivity) computed in `cmd_health.py`
- `graph/cycles.py` — Tarjan SCC, tangle ratio
- `cmd_patterns.py` — architectural pattern detection

**Command:**
```bash
# Generate fingerprint of current repo
roam fingerprint                          # full topology signature
roam fingerprint --compact                # compressed hash for comparison
roam fingerprint --export fingerprint.json

# Compare two repos
roam fingerprint --compare other-repo-fingerprint.json

# Use as scaffold template
roam fingerprint --scaffold fingerprint.json --language go --output ./new-project/
```

**What the fingerprint contains:**
```json
{
  "topology": {
    "layers": 5,
    "layer_distribution": [0.12, 0.25, 0.35, 0.20, 0.08],
    "fiedler": 0.0031,
    "modularity": 0.445,
    "tangle_ratio": 0.03
  },
  "clusters": [
    {
      "label": "api-layer",
      "layer": 0,
      "size_pct": 0.12,
      "conductance": 0.15,
      "roles": {"router": 3, "controller": 8, "middleware": 4},
      "pattern": "facade"
    },
    {
      "label": "business-logic",
      "layer": 2,
      "size_pct": 0.35,
      "conductance": 0.08,
      "roles": {"service": 12, "validator": 6, "transformer": 4},
      "pattern": "service-layer"
    }
  ],
  "hub_bridge_ratio": 0.15,
  "pagerank_gini": 0.34,
  "dependency_direction": "top-down",
  "antipatterns": {"god_objects": 0, "cyclic_clusters": 1}
}
```

**Why it matters:** Agents starting a new Go project can ingest the fingerprint
of a mature, well-architected Django backend. They use the graph template to
scaffold with the same structural robustness — same layer count, same modularity
target, same conductance bounds — without copying any syntax. Architecture
becomes transferable across languages.

**Builds on:** `graph/pagerank.py`, `graph/clusters.py`, `graph/layers.py`,
`graph/cycles.py`, `cmd_health.py` (Fiedler), `cmd_patterns.py`.

**Implementation sketch:**
1. New `src/roam/commands/cmd_fingerprint.py`
2. New `src/roam/graph/fingerprint.py` — metric extraction + normalization
3. Comparison: vector distance between fingerprints (Euclidean on normalized metrics)
4. Scaffold mode: generate directory structure + stub files matching the topology
5. MCP tool: `get_fingerprint`, `compare_fingerprint`

**Key challenges:**
- Normalization: metrics must be repo-size-independent (use ratios, not absolutes)
- Scaffold generation is language-specific (directory conventions, file templates)
- Fingerprint versioning as metrics evolve
- Start with comparison only, add scaffold in a follow-up

---

## 23. P5: Adversarial Architecture Review — `roam adversarial`

**Priority:** HIGH-STRATEGIC | **Effort:** High | **Status: NOT STARTED**

**The thesis:** AI agents are good at generating code but poor at adversarial
thinking against their own creations. Roam can act as a "Dungeon Master" — when
an agent submits changes, roam generates targeted architectural challenges based
on the topology, forcing the agent to defend its choices before merge.

**What we already have:**
- 19 anti-pattern detectors in `catalog/detectors.py` (IO in loops, regex in
  loops, quadratic string ops, branching recursion, etc.)
- `graph/cycles.py` — new cycle detection via Tarjan SCC
- `cmd_diff.py` — blast radius of uncommitted changes
- `cmd_preflight.py` — combined blast radius + fitness + test mapping
- `cluster_quality()` — conductance changes between clusters
- `detect_layers()` — layer violation detection

**Command:**
```bash
roam adversarial                      # review current uncommitted changes
roam adversarial --staged             # review staged changes only
roam adversarial --range main..HEAD   # review branch changes
roam adversarial --severity high      # only critical challenges
```

**What it does:**
1. Compute the diff sub-graph (new/modified/deleted symbols and their edges)
2. Run all anti-pattern detectors on new/modified code
3. Check for new cycles introduced (diff old SCC vs new SCC)
4. Check for layer violations (symbols calling into wrong layers)
5. Check for conductance degradation (cluster boundaries weakened)
6. Check for unprotected paths (new public entry -> unguarded internal)
7. Check for orphaned symbols (new code with zero callers)
8. Generate adversarial questions — structured challenges the agent must address

**Output:**
```
VERDICT: 4 challenges, 2 critical

CHALLENGE 1 [CRITICAL] — New cyclic dependency
  You connected User -> Order -> User via add_order_to_user().
  SCC size grew from 0 to 3 symbols.
  Question: "With circular instantiation, explain why a deeply nested
  order tree won't cause a stack overflow."

CHALLENGE 2 [CRITICAL] — Unprotected path created
  New route POST /api/admin/reset connects to db.drop_table()
  via admin_controller -> reset_service -> db.drop_table.
  No auth gate detected in the path (checked: auth-gaps BFS).
  Question: "This path reaches a destructive DB operation without
  authentication. Prove this is intentional or add a gate."

CHALLENGE 3 [WARNING] — Conductance degradation
  Your change adds 3 cross-cluster edges between 'billing' and 'auth'.
  Cluster conductance: 0.08 -> 0.19 (+137%).
  Question: "These clusters were well-isolated. Justify the new
  coupling or extract a shared interface."

CHALLENGE 4 [WARNING] — Anti-pattern: IO in loop
  New function batch_update() at src/api/orders.py:89
  contains db.query() inside a for loop (N+1 pattern).
  Confidence: 0.92.
  Question: "This will issue N separate queries. Use a bulk
  operation or justify why single queries are necessary here."
```

**Integration with CI:**
```bash
# In CI pipeline — exit 1 if any critical challenge
roam adversarial --staged --fail-on-critical

# Generate challenges as PR comment
roam adversarial --range main..HEAD --format markdown > challenges.md
```

**Why it matters:** Turns roam into a self-play environment for code architecture.
The agent must defend its architectural choices against mathematical assertions
before code is merged. This catches structural regressions that unit tests miss:
new cycles, broken modularity, unprotected paths, N+1 patterns.

**Builds on:** `catalog/detectors.py` (19 anti-pattern detectors + `run_detectors()`),
`graph/cycles.py` (Tarjan SCC), `graph/clusters.py` (conductance),
`graph/layers.py` (layer violations), `cmd_diff.py` (change detection),
`cmd_auth_gaps.py` (unprotected path BFS), `cmd_preflight.py` (blast radius).

**Implementation sketch:**
1. New `src/roam/commands/cmd_adversarial.py`
2. New `src/roam/review/` directory:
   - `challenges.py` — challenge generation from graph deltas
   - `templates.py` — adversarial question templates per challenge type
3. Reuse `cmd_diff.py` infrastructure for change detection
4. Run anti-pattern detectors scoped to changed symbols only
5. Compute before/after metrics (SCC count, conductance, layer violations)
6. MCP tool: `adversarial_review` for agent integration

**Key design principle:** Challenges are *questions*, not blockers. The agent
(or human) can dismiss a challenge with a justification. The goal is forcing
deliberate architectural decisions, not gatekeeping.

**Relationship to other ideas:**
- N1 (simulate) can pre-check whether proposed changes would trigger challenges
- N2 (annotate) can store challenge dismissals as persistent context
- P1 (orchestrate) uses adversarial as a post-merge validation step

---

## 24. W1: Graph Diff as PR Artifact — `roam pr-diff`

**Priority:** HIGHEST-LEVERAGE | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** Treat every PR as a **graph delta**, not a text diff. PR review
becomes structural review ("you introduced a coupling path between clusters A
and C") instead of line-by-line opinions.

**What we already have:**
- `cmd_diff.py` computes blast radius (affected symbols/files from changes)
- `cmd_diff.py --coupling` shows co-change partner warnings
- `cmd_diff.py --fitness` scopes fitness rule violations to the diff
- `graph/cycles.py` computes SCC and tangle ratio
- `graph/clusters.py` computes modularity and per-cluster conductance
- `graph/layers.py` computes topological layer assignments
- `graph/pagerank.py` computes centrality metrics

**What's missing (the leap):** Diff currently shows affected *files and symbols*.
It does NOT show structural deltas: new/removed edges, metric changes, layer
violations introduced, bottlenecks created.

**Command:**
```bash
roam pr-diff                          # uncommitted changes
roam pr-diff --staged                 # staged only
roam pr-diff --range main..HEAD       # full branch
roam pr-diff --format markdown        # PR comment format
```

**What it computes:**
1. Edge delta: new edges added, edges removed, edge kind changes
2. Metric deltas (before/after for the changed sub-graph):
   - Propagation cost delta
   - Tangle ratio delta
   - Algebraic connectivity (Fiedler) delta
   - Modularity Q-score delta
3. New bottlenecks: symbols whose betweenness centrality increased significantly
4. Layer violations: new cross-layer imports introduced by this PR
5. Cluster boundary changes: conductance shifts between affected clusters
6. Architectural footprint: how much of the graph structure changed (not LOC)

**Output:**
```
VERDICT: medium structural impact (footprint: 3.2% of graph)

EDGE DELTA:
  +4 new dependency edges, -1 removed
  +1 new cross-cluster edge (billing -> auth)   << WARNING
  0 new cycles introduced

METRIC DELTAS:
  Propagation cost:  12.4 -> 12.9  (+4.0%)
  Tangle ratio:      0.03 -> 0.03  (no change)
  Fiedler value:     0.0031 -> 0.0028  (-9.7%)  << DEGRADED
  Modularity:        0.445 -> 0.431  (-3.1%)    << DEGRADED

NEW BOTTLENECK:
  src/billing/tax.py::calculate_tax
    Betweenness: 0.012 -> 0.034 (+183%)
    Now sits on 5 shortest paths between clusters

LAYER VIOLATIONS: 1 new
  src/api/views.py (layer 0) imports src/db/queries.py (layer 3)
  Skipped layers: 1, 2
```

**Integration with CI:**
```bash
# In CI: fail if structural degradation exceeds threshold
roam pr-diff --range main..HEAD --fail-on-degradation

# Generate as PR comment (markdown)
roam pr-diff --range main..HEAD --format markdown > pr-review.md
```

**Why it matters:** This makes roam "the thing you look at" in code review.
Reviewers stop arguing about style and start seeing *architectural consequences*.
Combined with W2 (budget gates), it enforces structural trajectory.

**Builds on:** `cmd_diff.py` (change detection, blast radius), `graph/cycles.py`
(tangle ratio), `graph/clusters.py` (modularity, conductance), `graph/layers.py`
(layer violations), `graph/pagerank.py` (centrality/betweenness).

**Implementation sketch:**
1. New `src/roam/commands/cmd_pr_diff.py`
2. Compute "before" metrics by checking out base branch (or caching last index)
3. Compute "after" metrics from current index
4. Diff the two metric snapshots
5. Edge delta: compare edge tables between before/after indexes
6. Markdown formatter for PR comments

**Key challenge:** Computing "before" metrics requires either:
- A) Re-indexing the base branch (slow but accurate)
- B) Caching metrics at each `roam snapshot` (fast, requires prior snapshots)
- C) Using the stored index and only recomputing affected sub-graph (compromise)
- Start with (C), upgrade to (B) when snapshot infrastructure matures.

**Relationship to other ideas:**
- P5 (adversarial) generates *questions* from the same deltas
- W2 (budget) defines *thresholds* for acceptable deltas
- N1 (simulate) can preview deltas *before* writing code

---

## 25. W2: Architecture Budget Gates — `roam budget`

**Priority:** HIGHEST-LEVERAGE | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** Health gates are pass/fail. Budgets enforce *trajectory*:
"this repo may not increase propagation cost by >0.5% per PR." Architecture
becomes a constrained resource, not an afterthought.

**What we already have:**
- `cmd_fitness.py` supports 4 rule types: `dependency`, `metric`, `naming`, `trend`
- `.roam/fitness.yaml` already defines architectural rules
- `cmd_diff.py --fitness` scopes rule checks to changed code
- `graph/anomaly.py` computes Theil-Sen slopes and trend detection
- `cmd_trend.py` tracks metrics over snapshot history

**What's missing (the leap):** Fitness rules check absolute thresholds ("CC must
be < 25"). Budgets check *per-PR deltas* ("this PR may not increase CC by more
than 2 in any single file"). This is trajectory enforcement, not threshold enforcement.

**Command:**
```bash
roam budget                           # check current PR against budgets
roam budget --init                    # generate default .roam/budget.yaml
roam budget --staged                  # check staged changes only
roam budget --range main..HEAD        # check branch
roam budget --explain                 # show reasoning per rule
```

**Budget file: `.roam/budget.yaml`**
```yaml
version: "1"
budgets:
  # Per-PR propagation cost increase cap
  - name: "Propagation cost growth"
    metric: propagation_cost
    max_increase_pct: 0.5
    reason: "Prevent coupling creep"

  # No new cross-layer imports
  - name: "Layer discipline"
    type: layer_violation
    max_new: 0
    reason: "Enforce layered architecture"

  # God component cap
  - name: "Max fan-in growth"
    metric: max_fan_in
    max_increase: 3
    reason: "Prevent emerging god objects"

  # Cluster coupling cap
  - name: "Cluster isolation"
    clusters: ["billing", "auth"]
    max_cross_edges: 5
    reason: "These domains must stay decoupled"

  # Complexity budget per PR
  - name: "Complexity budget"
    metric: total_cognitive_complexity
    max_increase: 10
    reason: "Each PR gets 10 CC points to spend"

  # No new cycles
  - name: "Cycle prevention"
    metric: scc_count
    max_increase: 0
    reason: "Zero tolerance for new cyclic dependencies"
```

**Output:**
```
VERDICT: 1 of 6 budgets exceeded

  [PASS] Propagation cost growth: +0.3% (budget: 0.5%)
  [PASS] Layer discipline: 0 new violations (budget: 0)
  [FAIL] Max fan-in growth: +5 (budget: 3)  << EXCEEDED
         src/billing/tax.py::calculate_tax gained 5 new callers
  [PASS] Cluster isolation: 4 cross-edges (budget: 5)
  [PASS] Complexity budget: +7 CC (budget: 10)
  [PASS] Cycle prevention: 0 new SCCs (budget: 0)
```

**Why it matters:** Teams stop arguing about code style and start enforcing
*architectural trajectory*. A PR that adds 10 lines but creates a new god object
is caught. A PR that refactors 500 lines but improves modularity is rewarded.
This is architecture as a constrained resource.

**Builds on:** `cmd_fitness.py` (rule evaluation), `cmd_diff.py` (change scoping),
`graph/anomaly.py` (trend computation), all metric computation in `graph/`.

**Implementation sketch:**
1. New `src/roam/commands/cmd_budget.py`
2. New `.roam/budget.yaml` schema (extends fitness.yaml concept)
3. Compute before/after metrics (shares infrastructure with W1 pr-diff)
4. Evaluate delta against budget rules
5. CI integration: `roam budget --fail-on-exceed` exits 1

**Synergy with W1:** W1 (pr-diff) shows the structural changes. W2 (budget)
defines what changes are acceptable. Together they make roam the center of
architectural governance in CI.

---

## 26. W3: Invariant Discovery — `roam invariants`

**Priority:** HIGH | **Effort:** High | **Status: NOT STARTED**

**The thesis:** Agents break systems because they don't know the invisible rules.
Roam can infer likely invariants (implicit contracts) from tests, API usage
patterns, serialization shapes, and query patterns — then surface them before
any agent modifies the code.

**What we already have:**
- `cmd_testmap.py` maps symbols to their tests (direct, file-level, convention)
- `cmd_impact.py` traces transitive dependents
- `cmd_context.py` gathers callers, callees, and usage patterns
- `cmd_breaking.py` detects breaking changes (signature changes, removed exports)
- Symbol table has full signatures (parameters, return types where available)

**Command:**
```bash
roam invariants calculate_tax         # invariants for a symbol
roam invariants src/billing/tax.py    # invariants for a file
roam invariants --public-api          # all exported/public symbols
roam invariants --breaking-risk       # rank by how many callers assume the contract
```

**What it infers:**
1. **Test-asserted invariants:** Parse test assertions targeting the symbol;
   extract expected return types, boundary values, exception types
2. **Usage-pattern invariants:** How callers actually use the symbol:
   - Always called with N arguments (signature contract)
   - Return value always used as type X (return contract)
   - Always called after Y (ordering contract)
   - Called from N distinct callers across M files (stability contract)
3. **Serialization invariants:** If symbol produces/consumes DTOs, API responses,
   or DB rows — the shape is an implicit contract with external consumers
4. **Migration/schema invariants:** If symbol references DB columns, the column
   set is a contract with the schema

**Output:**
```
VERDICT: 7 invariants discovered for calculate_tax (31 callers, 12 tests)

CONTRACT: calculate_tax(amount: float, region: str, exempt: bool = False) -> float
  Stability: HIGH (31 callers depend on this signature)

INVARIANTS:
  1. [TEST] Returns float >= 0 (asserted in 4 tests)
  2. [TEST] Raises ValueError for negative amount (asserted in 2 tests)
  3. [TEST] Returns 0.0 when exempt=True (asserted in 1 test)
  4. [USAGE] Return value always used in arithmetic (12/31 callers multiply result)
  5. [USAGE] Called after validate_order() in 8/10 caller chains (ordering contract)
  6. [USAGE] Never called with region=None (0/31 callers pass None)
  7. [SCHEMA] References tax_rates table columns: region, rate, effective_date

BREAKING RISK: HIGH
  Changing return type would affect 31 callers in 14 files
  Changing parameter order would affect 31 call sites
  Removing 'exempt' parameter would break 8 callers
```

**Why it matters:** This turns roam into a "contract discovery" engine. When an
agent runs `roam context calculate_tax`, it gets not just "what calls this" but
"what MUST remain true about this." Agents can modify implementation freely as
long as they preserve discovered invariants.

**Builds on:** `cmd_testmap.py` (test mapping), `cmd_impact.py` (callers),
`cmd_context.py` (usage analysis), `cmd_breaking.py` (signature contracts),
`index/symbols.py` (parameter extraction), `index/parser.py` (AST for assertion
parsing).

**Implementation sketch:**
1. New `src/roam/commands/cmd_invariants.py`
2. New `src/roam/analysis/invariants.py`:
   - `extract_test_invariants()` — parse test ASTs for assertions on target symbol
   - `extract_usage_invariants()` — analyze caller patterns (arg types, return usage)
   - `extract_schema_invariants()` — cross-reference with DB column references
3. Integrate into `cmd_context.py` — auto-inject invariant summary
4. MCP tool: `get_invariants` for agent consumption

**Key challenge:** Test assertion parsing is language-specific. Start with Python
(`assert`, `assertEqual`, `pytest.raises`), then JS (`expect`, `assert`), then Go
(`if got != want`).

---

## 27. W4: Graph-Path Test Coverage — `roam path-coverage`

**Priority:** HIGH | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** Coverage is usually measured by lines or files. The deeper view
is: which **critical paths** through the call graph have no test protection?
A 78% line coverage can mask a completely untested path from public API to
database deletion.

**What we already have:**
- `cmd_testmap.py` maps symbols to tests
- `cmd_auth_gaps.py` does BFS from routes to find unprotected paths
- `graph/pathfinding.py` computes k-shortest paths
- `cmd_impact.py` traces transitive dependencies
- `graph/pagerank.py` ranks symbol importance

**Command:**
```bash
roam path-coverage                        # all critical untested paths
roam path-coverage --from "POST /api/*"   # paths from specific entry points
roam path-coverage --to "db.*"            # paths to sensitive sinks
roam path-coverage --min-risk medium      # filter by risk level
roam path-coverage --suggest-tests        # recommend test harness points
```

**What it computes:**
1. Identify entry points (route handlers, public API, CLI commands)
2. Identify sensitive sinks (DB writes, file I/O, network calls, auth checks)
3. Enumerate all paths from entries to sinks (k-shortest, bounded depth)
4. For each path, check if ANY node has test coverage (from testmap)
5. Score risk: path with zero tested nodes + high PageRank sink = critical
6. Suggest optimal test insertion points (nodes that cover the most untested paths)

**Output:**
```
VERDICT: 4 critical paths with zero test coverage

PATH 1 [CRITICAL]: User deletion (no test protection)
  POST /api/users/delete
    -> src/api/users.py::delete_user (UNTESTED)
    -> src/services/user.py::remove_user (UNTESTED)
    -> src/db/queries.py::hard_delete (UNTESTED)
  Risk: destructive DB operation, 0/3 nodes tested
  Suggest: test delete_user (covers entire path)

PATH 2 [HIGH]: Payment processing
  POST /api/checkout
    -> src/api/checkout.py::process (TESTED)
    -> src/billing/charge.py::create_charge (UNTESTED)
    -> src/billing/stripe.py::api_call (UNTESTED)
  Risk: financial operation, 1/3 nodes tested (entry only)
  Suggest: test create_charge (covers untested tail)

OPTIMAL TEST POINTS (maximize path coverage with fewest tests):
  1. test delete_user     — covers 3 untested paths
  2. test create_charge   — covers 2 untested paths
  3. test validate_input  — covers 2 untested paths
  Adding these 3 tests would protect 7/9 critical paths (78% -> 100%)
```

**Why it matters:** "Line coverage is 78%" is meaningless. "The user-delete path
to hard_delete has zero test protection" is actionable. Path coverage reveals
*structural* testing gaps that line coverage completely misses.

**Builds on:** `cmd_testmap.py` (test mapping), `cmd_auth_gaps.py` (BFS from
routes), `graph/pathfinding.py` (k-shortest paths), `graph/pagerank.py`
(importance ranking).

**Implementation sketch:**
1. New `src/roam/commands/cmd_path_coverage.py`
2. Entry point detection: reuse route scanner from `cmd_auth_gaps.py`
3. Sink detection: identify DB/IO/network calls from edge kinds + symbol names
4. Path enumeration: bounded BFS/DFS, max depth ~8
5. Test overlay: for each node in path, check testmap for coverage
6. Optimal test point: greedy set-cover on untested paths

---

## 28. W5: Agent Work Planner — `roam plan`

**Priority:** HIGH | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** Roam currently gives agents *data* (files to read, symbols
affected). The next step is giving agents *strategy*: a complete execution plan
with reading order, invariants to preserve, safe insertion points, and test
shortlists ranked by expected failure detection.

**Relationship to N4 (test-prompt):** N4 generates test-writing prompts for a
specific symbol. W5 is the broader concept: generate a complete *work plan*
for any modification task, where test prompts are one component.

**Command:**
```bash
roam plan "add logging to checkout flow"
roam plan --symbol calculate_tax --task refactor
roam plan --file src/billing/tax.py --task extend
roam plan --staged --task review
```

**What it outputs (structured agent execution plan):**

```
PLAN: refactor calculate_tax

1. READ ORDER (topological, 6 files):
   1. src/billing/tax.py:45-89        (target — read first)
   2. src/billing/rates.py:12-34      (callee — understand dependency)
   3. src/db/queries.py:67-82         (callee — DB interface)
   4. src/api/checkout.py:112-130     (highest-PageRank caller)
   5. tests/test_billing.py:45-89     (existing tests)
   6. .roam/fitness.yaml              (architectural rules)

2. INVARIANTS TO PRESERVE:
   - Signature: calculate_tax(amount, region, exempt=False) -> float
   - Returns float >= 0 (asserted in 4 tests)
   - Never called with region=None (31 callers)
   - Called after validate_order() in 8/10 chains

3. SAFE MODIFICATION POINTS (lowest blast radius):
   - Line 67-72: internal helper _apply_rate() — 0 external callers
   - Line 78-82: rate lookup logic — encapsulated, 1 callee
   AVOID: Line 45-50 (function signature) — 31 callers depend on it

4. TOUCH CAREFULLY (with reasons):
   - rates.py::get_rate — shared by 3 other commands
   - queries.py::fetch_rates — DB contract, schema-coupled

5. TEST SHORTLIST (ranked by failure detection probability):
   1. test_billing.py::test_tax_basic        — covers main path
   2. test_billing.py::test_tax_exempt       — covers exempt branch
   3. test_checkout.py::test_checkout_total   — integration, catches regressions
   Run: pytest tests/test_billing.py tests/test_checkout.py -x

6. POST-CHANGE VERIFICATION:
   - Run: roam preflight calculate_tax
   - Check: roam diff --fitness (no budget violations)
   - Verify: roam invariants calculate_tax (contracts preserved)
```

**Why it matters:** You stop giving agents data and start giving them *strategy*.
An agent following this plan will read files in the right order, avoid breaking
contracts, modify at the lowest-risk insertion points, and run the right tests.

**Builds on:** `cmd_context.py` (dependency gathering, task-specific extras),
`cmd_preflight.py` (blast radius, complexity, coupling, fitness),
`cmd_testmap.py` (test mapping), `graph/pagerank.py` (importance ranking),
`graph/layers.py` (topological ordering for read sequence), W3 (invariants).

**Implementation sketch:**
1. New `src/roam/commands/cmd_plan.py`
2. Compose from existing commands: context + preflight + testmap + invariants
3. Topological sort for reading order (callee-first or caller-first by task type)
4. Safe-point identification: symbols with zero external callers in the target file
5. MCP tool: `generate_plan` for agent consumption

---

## 29. W6: Intent Graph from Docs — `roam intent`

**Priority:** MEDIUM | **Effort:** Medium-High | **Status: NOT STARTED**

**The thesis:** Bridge "why" (docs) to "what" (code) in a reproducible, local,
offline way. Parse markdown docs, ADRs, TODOs, changelogs and link them to
symbols via references and naming. Zero API keys.

**Command:**
```bash
roam intent                               # all doc-to-code links
roam intent --symbol calculate_tax        # docs mentioning this symbol
roam intent --doc docs/adr-003.md         # code implementing this ADR
roam intent --drift                       # code that diverged from docs
roam intent --orphan-docs                 # docs with no matching code
```

**What it does:**
1. Scan documentation files: `docs/`, `*.md`, `ADR-*.md`, `CHANGELOG.md`,
   `TODO.md`, inline `# ADR:` comments, `README.md` sections
2. Extract references to code: function names, file paths, module names,
   class names mentioned in docs (regex + symbol index cross-reference)
3. Build a doc-to-symbol edge table in SQLite
4. Detect drift: doc says "auth uses JWT" but code uses session tokens;
   doc references `old_function` that was renamed/deleted
5. Detect orphan docs: ADRs that reference no existing symbols
6. Detect undocumented architecture: high-centrality clusters with no ADR

**Output:**
```
VERDICT: 12 doc-code links, 2 drifts, 1 orphan ADR

DOC-CODE LINKS:
  docs/adr-003-billing.md -> src/billing/ (8 symbols)
  docs/adr-007-auth.md -> src/auth/ (4 symbols)

DRIFT DETECTED:
  docs/adr-003-billing.md references calculate_tax_v2()
    -> Symbol does not exist (renamed to calculate_tax in commit abc123?)
  docs/architecture.md says "3-layer architecture"
    -> detect_layers() found 5 layers (architecture evolved past docs)

UNDOCUMENTED:
  Cluster 'payments' (12 symbols, PageRank sum 0.15) has no ADR
```

**Why it matters:** Agents can ask "which ADR does this module implement?" and
"is the code still aligned with the documented architecture?" This bridges
the gap between human intent and machine-readable structure.

**Builds on:** `index/parser.py` (markdown is a tree-sitter grammar),
`db/schema.py` (new `doc_refs` table), `commands/resolve.py` (symbol name matching),
`graph/clusters.py` (cluster labeling for undocumented detection).

**Implementation sketch:**
1. New `src/roam/commands/cmd_intent.py`
2. New `src/roam/index/doc_linker.py` — markdown scanning + symbol matching
3. Schema: `doc_refs(doc_path, symbol_id, match_type, snippet)`
4. Drift detection: compare doc references against live symbol index
5. Run during `roam index` as optional pass (docs change less frequently)

---

## 30. W7: Minimum Cut Safety Zones — `roam cut`

**Priority:** MEDIUM | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** Mathematically quantify containment boundaries. Find the minimal
set of edges whose removal would isolate domains. Identify "leak edges" (thin
bridges that cause massive blast radius) and propose exactly where to insert
guard rails (interfaces, adapters) for maximum propagation cost reduction.

**What we already have:**
- `graph/clusters.py` computes per-cluster conductance (already a cut metric)
- `graph/builder.py` builds both symbol and file graphs
- `graph/pagerank.py` computes betweenness centrality
- `cmd_coupling.py` identifies strongly coupled pairs
- NetworkX provides `minimum_edge_cut()` and `minimum_node_cut()`

**Command:**
```bash
roam cut                              # all domain boundaries + cut sets
roam cut --between billing auth       # specific domain pair
roam cut --leak-edges                 # edges with highest blast-radius amplification
roam cut --suggest-interfaces         # where to insert abstractions
```

**What it computes:**
1. For each cluster pair: minimum edge cut (NetworkX `minimum_edge_cut`)
2. Cut thinness: |min_cut| / total_cross_edges — how fragile is the boundary
3. Leak edges: single edges whose removal would reduce propagation cost most
   (edges with highest edge betweenness that cross cluster boundaries)
4. Interface suggestions: for each leak edge, propose an abstraction point
   (interface/protocol/ABC) that would decouple the domains

**Output:**
```
VERDICT: 3 domain boundaries analyzed, 2 fragile

BOUNDARY: billing <-> auth
  Cross-edges: 7 | Min cut: 2 edges | Thinness: 0.29  << FRAGILE
  Cut edges:
    1. auth/tokens.py::validate -> billing/charge.py::get_user_id
    2. billing/invoice.py::send -> auth/permissions.py::check_billing_access
  Removing these 2 edges would fully isolate billing from auth.
  Suggest: extract BillingAuthInterface with get_user_id() + check_access()

BOUNDARY: api <-> db
  Cross-edges: 12 | Min cut: 5 edges | Thinness: 0.42  (adequate)

LEAK EDGES (highest blast-radius amplification):
  1. src/billing/tax.py -> src/db/queries.py::fetch_rates
     Edge betweenness: 0.034 (top 1%)
     Removing this edge reduces propagation cost by 8.2%
     Suggest: inject rate provider interface
```

**Why it matters:** Architecture cleanup becomes surgical. Instead of "refactor
the billing module" (weeks of work), roam says "insert this one interface at
this exact point to reduce propagation cost by 8.2%."

**Builds on:** `graph/clusters.py` (conductance, community detection),
`graph/builder.py` (graph construction), `graph/pagerank.py` (betweenness),
NetworkX graph algorithms (min cut, edge betweenness).

**Implementation sketch:**
1. New `src/roam/commands/cmd_cut.py`
2. New `src/roam/graph/cuts.py` — min cut computation, leak edge ranking,
   interface suggestion generation
3. Per-cluster-pair analysis using `nx.minimum_edge_cut()`
4. Propagation cost delta estimation: simulate edge removal (reuses N1 infrastructure)

---

## 31. W8: Plugin DSL for Detectors — `.roam/rules/`

**Priority:** MEDIUM-HIGH | **Effort:** High | **Status: NOT STARTED**

**The thesis:** Roam already has fitness.yaml for architecture rules and 19
built-in anti-pattern detectors. The profound step: let users define new
"health laws" as graph queries without writing Python. Roam becomes a
governance platform.

**What we already have:**
- `cmd_fitness.py` + `.roam/fitness.yaml` (dependency, metric, naming, trend rules)
- `catalog/detectors.py` — 19 anti-pattern detectors with confidence calibration
- `output/sarif.py` — SARIF output format
- All graph data in SQLite (queryable)

**Rule file: `.roam/rules/no-controller-db.yaml`**
```yaml
name: "No controller calls DB directly"
description: "Controllers must go through service layer"
severity: error

# Pattern query: find paths matching this shape
match:
  from:
    kind: [function, method]
    file_glob: "**/controllers/**"
  to:
    kind: [function, method]
    file_glob: "**/db/**"
  max_distance: 1          # direct call only (distance 1 = no intermediary)

# Exemptions
exempt:
  symbols: [health_check, db_status]
  files: ["**/controllers/admin/**"]
```

**Rule file: `.roam/rules/public-api-tested.yaml`**
```yaml
name: "All public API functions must have tests"
description: "Every exported function with fan-in > 3 needs test coverage"
severity: warning

match:
  kind: [function, method]
  exported: true
  min_fan_in: 3

require:
  has_test: true           # testmap must find at least one test
```

**Rule file: `.roam/rules/no-internal-imports.yaml`**
```yaml
name: "No imports from internal packages"
description: "Modules marked 'internal' should only be imported by their parent"
severity: error

match:
  edge_kind: imports
  to:
    file_glob: "**/internal/**"
  from:
    not_file_glob: "${to_parent}/**"   # parent directory of target
```

**Command:**
```bash
roam rules                        # run all custom rules
roam rules --file no-controller-db.yaml   # run specific rule
roam rules --init                 # generate example rules
roam rules --sarif                # output in SARIF format
roam rules --ci                   # exit 1 on any error-severity violation
```

**Output:**
```
VERDICT: 2 rules passed, 1 rule failed

  [PASS] No controller calls DB directly (0 violations)
  [PASS] No imports from internal packages (0 violations)
  [FAIL] All public API functions must have tests (3 violations)
    src/api/admin.py::reset_cache (fan-in: 5, 0 tests)
    src/api/users.py::bulk_import (fan-in: 4, 0 tests)
    src/api/reports.py::generate_pdf (fan-in: 7, 0 tests)
```

**Why it matters:** Teams encode their architectural norms as declarative rules.
Roam enforces them in CI. New team members can't accidentally violate conventions
they don't know about. The rules live in the repo, are version-controlled, and
are self-documenting.

**Builds on:** `cmd_fitness.py` (rule evaluation framework), `catalog/detectors.py`
(detection patterns), `output/sarif.py` (SARIF output), `db/connection.py`
(SQL queries against symbol/edge tables).

**Implementation sketch:**
1. New `src/roam/commands/cmd_rules.py`
2. New `src/roam/rules/engine.py` — YAML rule parser + graph query evaluator
3. Rule types: `path_match` (from/to/distance), `symbol_match` (kind/fan-in/exported),
   `edge_match` (edge_kind/file patterns), `require` (has_test, has_doc, etc.)
4. SARIF output for IDE integration
5. Scan `.roam/rules/*.yaml` automatically

**Design principle:** The DSL is intentionally simple — not Cypher, not Datalog.
It covers 80% of team conventions with readable YAML. For complex queries, users
still write Python detectors.

---

## 32. Cross-References: Round 3 Overlaps

Three ideas from Round 3 feedback overlap with existing entries. Documented here
rather than creating duplicate sections.

### 32.1 Counterfactual Refactor Simulator → N1 (roam simulate)

Round 3 idea #3 ("simulate refactors without changing code") is exactly N1.
The Round 3 framing adds useful nuance:
- Emphasizes comparing *multiple refactor options* quantitatively
- Suggests a "candidate boundary" input (set of symbols or cluster split)
- Explicitly mentions `split` command as the starting point

**Action:** N1's implementation should support `roam simulate split` that takes
`cmd_split.py` suggestions and simulates each extraction option, ranking them
by predicted health improvement. Added to N1's scope.

### 32.2 Structural Regression Prediction → H (PR-Risk) + N7 (Forecast)

Round 3 idea #8 ("predict breakage likelihood from structural signals") extends
two existing ideas:
- H (PR-Risk Team-Aware) already adds author familiarity + minor contributor risk
- N7 (Forecast) already tracks per-symbol complexity trajectories

**What's genuinely new in this feedback:**
- Combining *multiple* structural signals into a lightweight prediction model:
  high fan-in touched + low test coverage + historical co-change + churn spikes
  + "changed near bottleneck" + "first time in area"
- The output framing: "where to spend review energy" (not just a risk score)

**Action:** When implementing N7 and evolving H, build a composite signal that
combines: fan-in of changed symbols, test coverage of changed paths (W4),
co-change surprise (already in coupling), churn acceleration (N7), and author
familiarity (H). This composite becomes `pr-risk v2`.

### 32.3 Agent Query Planner → N4 (Test Prompt) expanded to W5 (Plan)

Round 3 idea #6 ("generate an agent execution plan, not just context") is the
broader version of N4 (which focused on test-writing prompts). W5 (roam plan)
subsumes N4:
- N4 generates test prompts for a specific symbol
- W5 generates complete work plans (reading order, invariants, safe points,
  tests, verification steps)
- N4 becomes one component of W5's output (the "test shortlist" section)

**Action:** Implement W5 as the primary command. N4's test-prompt functionality
becomes `roam plan --task test` or a section within every plan output.

---

## 33. D1: Time-Travel Graph — `roam bisect`

**Priority:** HIGH-STRATEGIC | **Effort:** High | **Status: NOT STARTED**

**The thesis:** Architecture is currently a report. It should be a **debuggable
phenomenon with causality.** When a repo "suddenly feels worse," teams should be
able to find *which commit* caused the structural degradation, just like
`git bisect` finds which commit introduced a bug.

**What we already have:**
- `cmd_snapshot.py` stores health metrics with git commit + timestamp
- `cmd_trend.py` compares snapshots over time with anomaly detection
- `graph/anomaly.py` has Theil-Sen, Mann-Kendall, CUSUM, Western Electric rules
- `index/git_stats.py` has `parse_git_log()`, commit history, `get_blame_for_file()`
- `cmd_breaking.py` compares symbols between git refs via `_git_show()`
- Full reindex pipeline in `index/indexer.py`

**Commands:**
```bash
# Find which commit introduced a dependency cycle
roam bisect --metric scc_count --first-bad
roam bisect --metric tangle_ratio --threshold 0.05

# Blame architectural degradation
roam blame health              # which commits degraded health most
roam blame cycles              # which commits introduced cycles
roam blame propagation_cost    # which commits increased coupling

# Find regressions since a tag/release
roam regressions --since v1.2
roam regressions --since HEAD~50 --metric health_score,cycles,dead_exports
```

**How `roam bisect` works:**
1. Binary search through git history (like `git bisect`)
2. At each checkpoint commit: checkout, re-index (fast — incremental), compute metric
3. Find the first commit where metric crossed threshold
4. Report the commit, the diff, and the specific edges/symbols responsible

**How `roam blame health` works:**
1. Walk recent commits (configurable range, default ~50)
2. At sampled checkpoints, compute target metric
3. Attribute metric delta to the commit that caused the largest jump
4. Rank commits by architectural impact (not LOC, not churn — structural delta)

**Output (`roam bisect --metric scc_count --first-bad`):**
```
VERDICT: dependency cycle introduced in commit abc1234

BISECT LOG:
  HEAD      scc_count=3  (bad)
  HEAD~25   scc_count=3  (bad)
  HEAD~50   scc_count=2  (good)
  HEAD~37   scc_count=2  (good)
  HEAD~31   scc_count=3  (bad)  << first bad
  HEAD~34   scc_count=2  (good)
  HEAD~32   scc_count=2  (good)
  HEAD~31   scc_count=3  (bad)  << CONFIRMED

FIRST BAD COMMIT: abc1234 "Add order processing pipeline"
  Author: jane@company.com | Date: 2026-01-15
  New edges creating cycle:
    order_processor -> payment_gateway -> order_validator -> order_processor
  Files: src/orders/processor.py, src/payments/gateway.py, src/orders/validator.py
```

**Output (`roam blame health`):**
```
VERDICT: 5 commits caused 80% of health degradation (score 89 -> 71)

  COMMIT abc1234 (-8 health points)
    Added 3-node dependency cycle in orders cluster
    +2 SCC, +0.04 tangle ratio

  COMMIT def5678 (-5 health points)
    Introduced god component: src/api/views.py::process (fan-in 12->28)
    +1 bottleneck, +3.2 propagation cost

  COMMIT ghi9012 (-3 health points)
    Layer violation: controller importing DB directly
    +1 layer violation, +0.8% propagation cost

  Remaining 37 commits: -2 health points combined (noise)
```

**Why it matters:** "The health score dropped" is useless. "Commit abc1234
introduced a 3-node cycle in orders, and commit def5678 created a god component
in views.py" is actionable. Architecture becomes debuggable with causal
attribution.

**Builds on:** `cmd_snapshot.py` (metric storage), `cmd_trend.py` (comparison),
`cmd_breaking.py` (git ref checkout + comparison), `graph/anomaly.py` (detection),
`index/indexer.py` (re-index at checkpoints).

**Implementation sketch:**
1. New `src/roam/commands/cmd_bisect.py` — binary search driver
2. New `src/roam/commands/cmd_blame_arch.py` — architectural blame
3. New `src/roam/commands/cmd_regressions.py` — since-ref comparison
4. New `src/roam/graph/time_travel.py` — checkpoint indexing, metric extraction,
   delta attribution (which edges caused the metric change)
5. Optimization: cache index snapshots per commit hash to avoid re-parsing

**Key challenge:** Re-indexing at each checkpoint is expensive. Mitigations:
- Only re-index changed files (incremental via `index/incremental.py`)
- Cache parsed results per commit hash
- Sample checkpoints (every 5th commit by default, binary search narrows)
- `--quick` mode: estimate from snapshot table without re-indexing

---

## 34. D2: Proof-Carrying PRs — `roam attest`

**Priority:** HIGH-STRATEGIC | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** A PR should include machine-checkable *evidence* of safety and
impact, not just human opinion. Roam computes blast radius, tests, risk, fitness,
invariants — bundle all of that into a signed attestation that reviewers and CI
can verify programmatically.

**What we already have:**
- `cmd_diff.py` — blast radius, affected files/symbols
- `cmd_preflight.py` — compound safety check (blast radius + tests + complexity +
  coupling + conventions + fitness)
- `cmd_pr_risk.py` — risk score with author familiarity
- `cmd_affected_tests.py` — test selection with hop distance
- `cmd_fitness.py` — architectural rule evaluation
- `cmd_breaking.py` — breaking change detection
- `output/sarif.py` — SARIF 2.1.0 output format
- `output/formatter.py` — `json_envelope()` for structured output

**Command:**
```bash
roam attest HEAD~1..HEAD                   # attest current branch
roam attest --staged                       # attest staged changes
roam attest --format sarif                 # SARIF attestation
roam attest --format json                  # JSON evidence bundle
roam attest --sign                         # include content hash for tamper detection
roam attest --output attestation.json      # write to file
```

**What the attestation contains:**
```json
{
  "attestation": {
    "version": "1.0",
    "tool": "roam-code",
    "tool_version": "9.1.0",
    "timestamp": "2026-02-19T14:30:00Z",
    "git_range": "abc1234..def5678",
    "content_hash": "sha256:..."
  },
  "evidence": {
    "blast_radius": {
      "changed_files": 4,
      "affected_symbols": 23,
      "affected_files": 7,
      "propagation_cost_delta": "+0.3%"
    },
    "risk": {
      "verdict": "MEDIUM",
      "score": 42,
      "factors": {
        "structural": 0.35,
        "churn": 0.20,
        "coupling": 0.15,
        "author_familiarity": 0.85
      }
    },
    "breaking_changes": [],
    "invariants_touched": [
      {"symbol": "calculate_tax", "invariant": "returns float >= 0", "preserved": true}
    ],
    "fitness_violations": [],
    "budget_consumed": {
      "propagation_cost": "+0.3% of 0.5% budget",
      "complexity": "+4 of 10 CC budget"
    },
    "tests": {
      "selected": 12,
      "direct": 4,
      "transitive": 6,
      "colocated": 2,
      "command": "pytest tests/test_billing.py tests/test_checkout.py -x"
    },
    "effects_introduced": [
      {"type": "DB_WRITE", "path": "checkout -> charge -> db.insert", "auth_gated": true}
    ]
  },
  "verdict": {
    "safe_to_merge": true,
    "conditions": ["run selected tests", "no budget exceeded"],
    "warnings": ["propagation cost at 60% of budget"]
  }
}
```

**Integration with CI:**
```yaml
# GitHub Actions
- name: Roam Attestation
  run: |
    roam attest --range ${{ github.event.pull_request.base.sha }}..HEAD \
      --format json --output attestation.json
    # Upload as PR artifact
    gh pr comment --body "$(roam attest --range ... --format markdown)"
```

**Why it matters:** Review becomes "verify evidence," not "scan diff and hope."
The attestation is a trust protocol between humans, agents, and CI. A reviewer
can look at the attestation and know: blast radius is contained, no breaking
changes, invariants preserved, budget not exceeded, tests selected with reasons.
This is what makes Roam a *new category*, not "better ctags."

**Builds on:** `cmd_preflight.py` (compound check), `cmd_diff.py` (blast radius),
`cmd_pr_risk.py` (risk score), `cmd_affected_tests.py` (test selection),
`cmd_fitness.py` (rule evaluation), `cmd_breaking.py` (breaking changes),
`output/sarif.py` (SARIF format), `output/formatter.py` (JSON envelope).

**Implementation sketch:**
1. New `src/roam/commands/cmd_attest.py` — orchestrates all evidence gathering
2. Compose: preflight + breaking + fitness + risk + affected-tests into one bundle
3. Add W3 (invariants) and D3 (effects) when available
4. Content hash: SHA-256 of the attestation body for tamper detection
5. Markdown formatter for PR comments
6. SARIF formatter for IDE integration

**Key insight:** This is primarily a *composition* command. Almost all evidence
is already computed by existing commands. The value is bundling it into a single,
verifiable, machine-readable artifact with a trust-oriented framing.

---

## 35. D3: Effect & Side-Effect Graph — `roam effects`

**Priority:** HIGHEST-IMPACT | **Effort:** High | **Status: NOT STARTED**

**The thesis:** Roam's current graph is structural (calls/imports/inheritance).
Agents actually break systems via **effects**: DB writes, network calls, filesystem
access, global state mutation. Adding an effect axis upgrades roam from "where
things connect" to "what things *do*" — the biggest gap in static codebase
comprehension for agents.

**What we already have (partial effect detection):**
- `cmd_n1.py` already classifies IO patterns: DB relationships, HTTP calls, file IO
  (via `_trace_accessor_io()` with pattern matching on source)
- `cmd_auth_gaps.py` traces paths from routes to sensitive operations
  (route handlers → auth gates → business logic)
- `catalog/detectors.py` has `detect_io_in_loop()` (DB query, HTTP request,
  file IO in loops) and `detect_regex_in_loop()`
- Tree-sitter AST parsing gives us full function call information
- Call graph propagation is core infrastructure

**What's new (the leap):** Systematically classify every function's effect
signature and propagate it through the call graph. Not just "this function calls
DB" but "this entire path from API endpoint to DB write is a WRITE_DB effect
chain, and there's no auth gate in the path."

**Effect taxonomy:**
```
PURE          — no side effects (deterministic, referentially transparent)
READS_DB      — database read (SELECT, find, get, where)
WRITES_DB     — database write (INSERT, UPDATE, DELETE, save, create, destroy)
NETWORK       — HTTP/gRPC/WebSocket calls
FILESYSTEM    — file read/write/delete
TIME          — depends on current time (non-deterministic)
RANDOM        — depends on randomness (non-deterministic)
MUTATES_GLOBAL — modifies global/singleton state
CACHE         — reads/writes cache layer
QUEUE         — enqueues/dequeues async jobs
LOGGING       — writes to logs (usually safe but can leak data)
```

**Classification strategy (per language/framework):**

```python
# Python/Django
EFFECT_PATTERNS = {
    "WRITES_DB": [
        r"\.save\(\)", r"\.create\(\)", r"\.update\(\)", r"\.delete\(\)",
        r"\.bulk_create\(\)", r"cursor\.execute\(",
    ],
    "READS_DB": [
        r"\.objects\.", r"\.filter\(\)", r"\.get\(\)", r"\.all\(\)",
        r"cursor\.fetchone\(", r"cursor\.fetchall\(",
    ],
    "NETWORK": [
        r"requests\.\w+\(", r"httpx\.\w+\(", r"urllib\.",
        r"aiohttp\.", r"fetch\(",
    ],
    "FILESYSTEM": [
        r"open\(", r"Path\(.*\)\.\w+\(", r"os\.\w+\(",
        r"shutil\.", r"pathlib\.",
    ],
    # ... per framework
}
```

**Propagation through call graph:**
```
classify_function(symbol) -> set of direct effects
propagate_effects(graph) -> for each node, compute transitive effect closure

Example:
  api_handler()        -> {READS_DB, WRITES_DB, NETWORK}
    |- validate_input() -> {PURE}
    |- process_order()  -> {READS_DB, WRITES_DB}
    |    |- db.query()  -> {READS_DB}
    |    |- db.save()   -> {WRITES_DB}
    |- notify_user()    -> {NETWORK}
         |- smtp.send() -> {NETWORK}
```

**Command:**
```bash
roam effects process_order             # effects of a specific symbol
roam effects --file src/api/views.py   # effects per function in file
roam effect-map                        # full effect classification map
roam effect-gates                      # paths with effects but no guards
roam effects --propagate               # show transitive effect chains
```

**Output (`roam effects process_order`):**
```
VERDICT: process_order has 3 effect types (READS_DB, WRITES_DB, NETWORK)

DIRECT EFFECTS:
  READS_DB:   db.query() at line 45
  WRITES_DB:  db.save() at line 52

TRANSITIVE EFFECTS (via callees):
  NETWORK:    notify_user() -> smtp.send() (distance 2)

EFFECT CHAIN:
  POST /api/orders -> process_order [READS_DB, WRITES_DB]
                   -> notify_user [NETWORK]
  Auth gate: YES (validate_auth at distance 1)
  Transaction: YES (db.transaction wraps lines 44-53)
```

**Output (`roam effect-gates`):**
```
VERDICT: 2 unguarded effect paths

  POST /api/admin/reset -> clear_cache [WRITES_DB]
    DB write without auth gate: clear_cache -> db.truncate()
    Suggestion: add auth middleware or gate check

  POST /api/webhooks -> process_webhook [WRITES_DB, NETWORK]
    DB write + network call without rate limiting
    Suggestion: add rate limiter before process_webhook
```

**Schema change:**
```sql
CREATE TABLE symbol_effects (
    symbol_id INTEGER REFERENCES symbols(id),
    effect_type TEXT,          -- PURE, READS_DB, WRITES_DB, NETWORK, etc.
    source TEXT,               -- 'direct' or 'transitive'
    evidence TEXT,             -- pattern that matched or callee chain
    confidence REAL            -- 0.0-1.0
);
```

**Why it matters:** "This function calls that function" is structural. "This API
endpoint can write to the database without authentication" is *behavioral*. Effect
propagation is how you catch the bugs that actually cost money: unguarded writes,
network calls in loops, missing transactions, side effects leaking through
boundaries.

**Builds on:** `cmd_n1.py` (already classifies DB/HTTP/file IO patterns),
`cmd_auth_gaps.py` (route-to-sink path tracing), `catalog/detectors.py`
(`detect_io_in_loop` patterns), `graph/builder.py` (call graph), `index/parser.py`
(AST access for pattern matching).

**Implementation sketch:**
1. New `src/roam/analysis/effects.py` — effect classification + propagation
2. New `src/roam/commands/cmd_effects.py` — CLI for effect queries
3. New `src/roam/commands/cmd_effect_gates.py` — unguarded effect path detection
4. Schema: `symbol_effects` table populated during indexing
5. Per-language effect patterns: start with Python + JS/TS + PHP, extend
6. Propagation: BFS through call graph, union effect sets at each node
7. Integration: inject effect summary into `cmd_context.py` output

**Key design principle:** Classification is heuristic (pattern matching on AST),
not sound. Confidence levels reflect this. False positives are preferable to
false negatives for safety-critical effects (WRITES_DB, NETWORK).

**Synergy with D2 (attest):** The attestation includes `effects_introduced` —
new effect paths created by the PR. Combined, they answer: "this PR introduces
a new DB-write path that bypasses the auth boundary" with verifiable evidence.

---

## 36. D4: Minimal-Change Synthesis — `roam closure`

**Priority:** HIGH | **Effort:** High | **Status: NOT STARTED**

**The thesis:** When an agent needs to make a change (rename a field, extract a
service, modify an API), roam should compute the *minimal closure* — the exact
set of symbols, files, and artifacts that MUST change to satisfy the goal while
preserving all contracts. Agents stop spraying edits and start doing precise
surgical updates.

**What we already have:**
- `cmd_impact.py` — transitive dependents (what breaks if X changes)
- `cmd_safe_delete.py` — deletion safety (callers, exports, tests)
- `cmd_breaking.py` — breaking change detection (signature changes, removals)
- `cmd_affected_tests.py` — tests that need to pass after the change
- `cmd_testmap.py` — test-to-symbol mapping
- `graph/pathfinding.py` — path computation through dependency graph

**What's new (the leap):** Impact tells you what MIGHT break. Closure tells you
what MUST change. It's the difference between "blast radius" and "surgical plan."

**Command:**
```bash
# Rename a public field — what must change?
roam closure rename User.email_address email

# Extract a function into a new module — what must change?
roam closure extract src/api/views.py::checkout src/checkout/service.py

# Change a function signature — what must change?
roam closure signature calculate_tax --add-param currency:str

# Delete a symbol — what must change?
roam closure delete src/legacy/old_handler.py::process_v1
```

**What it computes (for `rename User.email_address email`):**

1. **Symbol definition:** `src/models/user.py` line 15 (the field itself)
2. **Direct references:** All files that access `user.email_address`
   (from edge table, kind=references)
3. **Serialization shapes:** DTOs, API responses, form fields referencing the name
   (string matching in templates, serializers, validators)
4. **Test assertions:** Tests that assert on `email_address` (string match in test files)
5. **Database layer:** Migrations referencing the column, ORM field definitions
6. **Documentation:** Docs, README sections mentioning the field
7. **Configuration:** Config files with the field name

**Output:**
```
VERDICT: rename User.email_address -> email requires 11 changes in 7 files

MUST CHANGE (closure):
  1. src/models/user.py:15           — field definition
  2. src/api/serializers.py:23       — UserSerializer field list
  3. src/api/serializers.py:45       — UserDetailSerializer field list
  4. src/forms/registration.py:12    — form field reference
  5. src/services/email.py:34        — user.email_address access
  6. src/services/email.py:67        — user.email_address access
  7. src/services/export.py:89       — CSV column header
  8. tests/test_user.py:23           — assertion on field name
  9. tests/test_user.py:56           — fixture with email_address key
  10. tests/test_api.py:112          — API response assertion
  11. docs/api.md                    — API field documentation

MIGRATION NEEDED:
  ALTER TABLE users RENAME COLUMN email_address TO email;

VERIFY AFTER CHANGE:
  pytest tests/test_user.py tests/test_api.py tests/test_export.py -x
```

**Why it matters:** An agent doing `grep -r "email_address"` will find too many
results and miss serializer shapes. An agent using `roam impact` sees blast radius
but not the precise set. `roam closure` computes the *exact minimal edit set*
using graph edges + string matching + contract analysis. It's a compiler for
changes.

**Builds on:** `cmd_impact.py` (transitive dependents), `cmd_safe_delete.py`
(caller analysis), `cmd_breaking.py` (signature contracts), `cmd_testmap.py`
(test mapping), `graph/builder.py` (dependency graph).

**Relationship to P3 (mutate):** P3 *executes* graph-level edits. D4 *computes*
what must change. D4 is the planner; P3 is the executor. Together: `roam closure`
computes the plan, `roam mutate` applies it.

**Implementation sketch:**
1. New `src/roam/commands/cmd_closure.py`
2. New `src/roam/analysis/closure.py`:
   - `compute_rename_closure()` — edge references + string matches + serializers
   - `compute_extract_closure()` — symbols to move + callers to update
   - `compute_signature_closure()` — callers with specific arg patterns
   - `compute_delete_closure()` — callers + tests + re-exports
3. String matching layer: scan non-indexed artifacts (templates, configs, docs)
   for the symbol name as a string literal
4. Output: ordered list of (file, line, change_type, reason)

---

## 37. D5: Sanitized Roam Capsule — `roam capsule`

**Priority:** MEDIUM | **Effort:** Medium | **Status: NOT STARTED**

**The thesis:** Teams often need architectural advice from external consultants,
auditors, or AI services — but can't share source code. A "capsule" exports
roam's structural graph (signatures, edges, metrics, hotspots) without function
bodies, letting outsiders reason about architecture while preserving code privacy.

**Command:**
```bash
roam capsule                              # full capsule
roam capsule --redact-paths               # anonymize file paths
roam capsule --no-signatures              # omit parameter names
roam capsule --output capsule.json        # write to file
roam capsule --import other-capsule.json  # load external capsule for analysis
```

**What the capsule contains:**
```json
{
  "capsule": {
    "version": "1.0",
    "generated": "2026-02-19",
    "tool_version": "9.1.0",
    "redacted": false
  },
  "topology": {
    "files": 142,
    "symbols": 1847,
    "edges": 5234,
    "languages": ["python", "typescript"]
  },
  "symbols": [
    {
      "id": 1,
      "name": "calculate_tax",
      "kind": "function",
      "file": "src/billing/tax.py",
      "signature": "(amount: float, region: str, exempt: bool) -> float",
      "metrics": {
        "cognitive_complexity": 12,
        "fan_in": 31,
        "fan_out": 4,
        "pagerank": 0.034,
        "churn": 15
      }
    }
  ],
  "edges": [
    {"source": 1, "target": 2, "kind": "calls"}
  ],
  "clusters": [...],
  "health": {
    "score": 78,
    "cycles": 3,
    "god_components": 1,
    "layer_violations": 2,
    "modularity": 0.44,
    "fiedler": 0.003
  },
  "hotspots": [...],
  "test_coverage_map": [...]
}
```

**What it does NOT contain:**
- Function bodies / implementation code
- File contents
- Comments / docstrings (optional, off by default)
- Git commit messages (optional)
- Author names (optional)

**Redacted mode (`--redact-paths`):**
- File paths hashed: `src/billing/tax.py` → `module_a7f3/file_2b1c.py`
- Symbol names preserved (they're signatures, not implementations)
- Cluster labels preserved (they're derived from directory structure)

**Why it matters:** Enables external architectural review, compliance audits,
consulting engagements, and AI-assisted architecture advice without exposing
proprietary source code. A consultant can run roam commands against the capsule:
`roam health --capsule capsule.json`, `roam map --capsule capsule.json`.

**Builds on:** `db/schema.py` (all data is already in SQLite), `output/formatter.py`
(JSON serialization), all metric computation in `graph/`.

**Implementation sketch:**
1. New `src/roam/commands/cmd_capsule.py`
2. Export: query all tables, serialize to JSON, strip function bodies
3. Import: load capsule into a temporary SQLite DB, run roam commands against it
4. Redaction: SHA-256 truncated hashes for path anonymization
5. `--capsule` flag on key commands (health, map, clusters, weather, coupling)

---

## 38. Cross-References: Round 4 Overlaps

Two ideas from Round 4 feedback overlap with existing entries. Documented here
for completeness.

### 38.1 Graph Query Language → W8 (Plugin DSL) evolved

Round 4 idea #6 ("a real graph query language") extends W8's YAML-based rule
DSL toward a full query language with MATCH/FILTER/ASSERT/OUTPUT semantics
(Cypher-lite or Datalog-lite).

**What Round 4 adds beyond W8:**
- W8 defines rules declaratively (match + require). Round 4 proposes open-ended
  *queries* — users can express new analyses, not just pass/fail rules
- Pattern matching over call chains (not just from/to pairs)
- Templated output (SARIF, custom formats)
- This is the "platform moment" — Roam becomes programmable

**Action:** W8 is the starting point (YAML rules, 80% of use cases). After W8
ships and proves the concept, evolve the DSL toward a constrained query language
in a follow-up. The query language should NOT be Cypher or Datalog — it should be
a small, purpose-built DSL that covers the most common architectural queries:

```yaml
# W8-style rule (ships first)
match:
  from: {file_glob: "**/controllers/**"}
  to: {file_glob: "**/db/**"}
  max_distance: 1

# Future DSL extension (ships later)
MATCH chain FROM controllers/** THROUGH services/** TO db/**
FILTER chain.length > 2
ASSERT chain HAS node WHERE node.effect = "AUTH_CHECK"
OUTPUT sarif
```

### 38.2 Architecture-Aware Test Selection → W4 (Path Coverage) evolved

Round 4 idea #7 ("test selection that learns from history") extends W4's
graph-path coverage with three additional dimensions:

**What Round 4 adds beyond W4:**
- **Historical co-failure patterns:** "these tests fail together when touching
  this cluster" — mine from CI history (test result logs)
- **Effect-aware selection:** if change touches WRITES_DB path, include integration
  tests (not just unit tests) — requires D3 (effects)
- **Two output modes:** "minimal confidence set" (fast CI) vs "risk-maximizing set"
  (catch scary failures) — replaces single list with ranked tiers

**Action:** W4 ships first (path-based coverage gaps). When D3 (effects) is
available, extend W4 with effect-aware test selection. Historical co-failure
requires CI log ingestion (separate data source, lower priority).

Evolved test selection output:
```
MINIMAL SET (8 tests, ~45s, 90% confidence):
  [reason: direct caller] test_billing.py::test_tax_basic
  [reason: transitive]    test_checkout.py::test_total
  ...

RISK-MAXIMIZING SET (24 tests, ~180s, 99% confidence):
  [reason: DB_WRITE path] test_integration/test_order_flow.py
  [reason: co-failure]    test_api/test_edge_cases.py
  ...
```

---

## 39. Execution Playbook — Agent-Ready Prompts

This section translates the backlog into context-bounded, deterministic prompts
for AI coding agents (Claude Code, Cursor, Aider, etc.). Agents fail on open-ended
architectural tasks but excel when given specific data inputs, file locations,
and structural goals.

### Phase 0: Agent Meta-Prompt

Give this to your agent at the start of every session to set the rules of
engagement. This goes in CLAUDE.md or equivalent.

```
System Context: You are a senior Python architect working on roam-code, a local,
AST-based static analysis engine. The codebase uses tree-sitter, networkx,
sqlite, and click.

Rules:
1. Never use external APIs or introduce heavy runtime dependencies (keep it 100% local).
2. Use roam commands locally to explore the codebase before making changes.
3. Preserve the exact JSON/CLI contract formatting in output/formatter.py.
4. Focus on pure, testable functions in src/roam/graph/ and src/roam/analysis/
   directories before wiring up Click CLI commands in src/roam/commands/.
5. Write the pytest test file BEFORE wiring up the CLI command.
6. Every command follows the pattern in CLAUDE.md (from __future__ import annotations,
   ensure_index(), open_db(), json_envelope(), verdict-first output).
7. Register new commands in cli.py _COMMANDS dict and _CATEGORIES dict.
```

### Sprint 1: Quick Wins & Data Foundations

These tasks are perfectly scoped for agents — regex, schema changes, and extending
existing logic.

#### Ticket 1: Agentic Memory (N2 — `roam annotate`)

**Implements:** Section 13 | **Effort:** Medium | **Unlocks:** P1, P5, D2

```
Prompt for Agent:

We need to add a feature called "roam annotate".
1. Modify src/roam/db/schema.py to add an "annotations" table (columns: id,
   symbol_id, file_path, tag, content, author, created_at, expires_at).
   Add the migration in connection.py ensure_schema() using _safe_alter().
2. Add write/read functions in src/roam/db/queries.py.
3. Create a new CLI command in src/roam/commands/cmd_annotate.py to allow users
   to add annotations to a symbol or file. Follow the command template in CLAUDE.md.
4. Modify src/roam/commands/cmd_context.py so that if a symbol has annotations,
   they are injected at the top of the context output. Ensure it gracefully handles
   symbols that have been renamed by matching qualified_name, not id.
5. Add an MCP tool wrapper in mcp_server.py: annotate_symbol and get_annotations.
6. Write tests in tests/test_annotations.py BEFORE wiring the CLI.
7. Register "annotate" in cli.py _COMMANDS and _CATEGORIES dicts.
```

#### Ticket 2: Backend Edge Case Fixes (I — items 1, 2, 5)

**Implements:** Section 10 (items 10.1, 10.2, 10.5) | **Effort:** Small

```
Prompt for Agent:

Review the backend analysis edge cases. Fix these three specific bugs:

1. In src/roam/commands/cmd_missing_index.py, update _RE_SCHEMA_TABLE to strip
   schema prefixes. When the captured table name contains a dot (e.g.,
   "{$schema}.users"), strip everything before the last dot to get "users".
   Apply this in _parse_migration_indexes() after group(1) capture.

2. Also in cmd_missing_index.py, add a regex fallback to catch raw SQL
   CREATE [UNIQUE] INDEX [IF NOT EXISTS] name ON [schema.]table(columns)
   statements that bypass Laravel blueprints. Feed these into table_indexes
   alongside the Blueprint-detected indexes.

3. In src/roam/commands/cmd_migration_safety.py, update the idempotency checks
   to recognize raw SQL "information_schema.columns" and "information_schema.tables"
   queries as valid guards, equivalent to hasColumn(). Add _RE_INFO_SCHEMA_GUARD
   regex and check it alongside _RE_HAS_COLUMN in _detect_columns_without_guard()
   and _detect_drops_without_guard().

Write tests for each fix FIRST. Run the full test suite (pytest tests/) to
verify no regressions.
```

### Sprint 2: The Dark Matter Track

Highest value paradigm-shift shippable in a single session.

#### Ticket 3: Dark Matter Detection (P2 — `roam dark-matter`)

**Implements:** Section 20 | **Effort:** Medium | **Builds on:** `cmd_coupling.py`

```
Prompt for Agent:

Build the "roam dark-matter" command (Idea P2).

1. Read src/roam/commands/cmd_coupling.py first. It already computes NPMI and
   Lift for temporal co-change pairs, and flags pairs as "HIDDEN" when they have
   no static edges. Understand how _npmi() and _against_mode() work.

2. Create src/roam/graph/dark_matter.py with:
   - dark_matter_edges(conn) -> list[dict]: take the temporal coupling graph
     (high NPMI pairs from git_cochange table), subtract the static file dependency
     graph (from build_file_graph()), return the "dark matter" edges.
   - HypothesisEngine class: for each dark matter file pair, scan their source
     contents to hypothesize the coupling reason:
     a) SHARED_DB: both files contain SQL/ORM referencing the same table name
     b) EVENT_BUS: one file emits an event string, the other subscribes to it
     c) SHARED_CONFIG: both files read the same env var or config key
     d) TEXT_SIMILARITY: high textual similarity suggesting copy-paste
     e) UNKNOWN: no hypothesis matches

3. Create src/roam/commands/cmd_dark_matter.py. Output: verdict line, then a
   table of dark-matter pairs with NPMI, Lift, co-changes, hypothesis, and risk.
   Support --json output via json_envelope().

4. Write tests in tests/test_dark_matter.py first. Create fixture files that
   have temporal coupling (via git commits) but no import edges.

5. Register "dark-matter" in cli.py _COMMANDS and _CATEGORIES.
```

### Sprint 3: Architectural Governance Core

Implements W1+W2 — turns roam into a CI/CD governance tool.

#### Ticket 4: Graph Diff Engine (W1 — `roam pr-diff`)

**Implements:** Section 24 | **Effort:** Medium-High | **Shared infra with W2**

```
Prompt for Agent:

Build "roam pr-diff" — the structural graph diff for PRs.

1. Create src/roam/graph/diff.py with:
   - graph_delta(before: nx.DiGraph, after: nx.DiGraph) -> dict: compute new edges,
     removed edges, and deltas for Fiedler value, Modularity Q-score, Tangle Ratio,
     and betweenness centrality changes.
   - approximate_before_graph(conn, base_ref: str) -> nx.DiGraph: for each file
     changed between base_ref and HEAD, retrieve the old version via
     git show base_ref:path, parse it with tree-sitter, extract symbols/edges,
     and build a graph with those nodes replacing the current ones. For unchanged
     files, reuse the current index.

2. Create src/roam/commands/cmd_pr_diff.py with flags: --staged, --range,
   --format (text/markdown). Output:
   - Edge delta (new/removed, highlighting cross-cluster edges)
   - Metric deltas (propagation cost, tangle ratio, Fiedler, modularity)
   - New bottlenecks (betweenness centrality spikes)
   - Layer violations introduced
   - Architectural footprint (% of graph structure changed)

3. Support --format markdown for direct use in PR comments.

4. Write tests in tests/test_pr_diff.py. Create a fixture with a git repo,
   make structural changes, verify the diff output captures edge/metric deltas.

5. Register "pr-diff" in cli.py _COMMANDS and _CATEGORIES.
```

#### Ticket 5: Architecture Budget Gates (W2 — `roam budget`)

**Implements:** Section 25 | **Effort:** Medium | **Depends on:** Ticket 4

```
Prompt for Agent:

Build "roam budget" — architectural trajectory enforcement.

1. Define the .roam/budget.yaml schema. Support these rule types:
   - metric delta: max_increase / max_increase_pct on any computed metric
     (propagation_cost, scc_count, max_fan_in, total_cognitive_complexity)
   - layer_violation: max_new layer violations allowed
   - cluster_coupling: max cross-edges between named clusters
   Each rule has: name, type, threshold, and reason.

2. Create src/roam/commands/cmd_budget.py:
   - Load .roam/budget.yaml
   - Use graph_delta() from src/roam/graph/diff.py (Ticket 4) to compute
     the PR's structural deltas
   - Compare each delta against the budget rule threshold
   - Output: per-rule PASS/FAIL with actual vs budget values
   - --init flag: generate a default best-practices budget.yaml
   - Exit code 1 if any budget exceeded (CI integration)

3. Support --json output via json_envelope().

4. Write tests in tests/test_budget.py first. Create fixture repos with
   budget.yaml files and verify pass/fail behavior.

5. Register "budget" in cli.py _COMMANDS and _CATEGORIES.
```

### Sprint 4: The Deep Behavior Leap

Most complex task. Split into two separate prompts to prevent context overload.

#### Ticket 6A: Effect Pattern Classification (D3 — Step 1)

**Implements:** Section 35 (classification layer) | **Effort:** Medium

```
Prompt for Agent:

We are adding "effect" detection to roam — classifying what functions DO,
not just what they call.

1. Create src/roam/analysis/effects.py.

2. Define the effect taxonomy as string constants:
   PURE, READS_DB, WRITES_DB, NETWORK, FILESYSTEM, TIME, RANDOM,
   MUTATES_GLOBAL, CACHE, QUEUE, LOGGING

3. Build framework-aware pattern dictionaries. For each supported language
   (start with Python, JavaScript/TypeScript, PHP), create dicts mapping
   regex patterns to effect types. Examples:
   - Python: .save() / .create() / .delete() -> WRITES_DB
   - Python: .filter() / .objects. / .get() -> READS_DB
   - Python: requests.get / httpx. / urllib. -> NETWORK
   - Python: open( / Path(). / os. / shutil. -> FILESYSTEM
   - JS: fetch( / axios. -> NETWORK
   - PHP: ->save() / ::create() / DB::insert -> WRITES_DB

4. Write classify_symbol_effects(source_bytes, language) -> set[str]:
   scan the source bytes of a function body for pattern matches and return
   the set of direct effects. Use the symbol's line_start/line_end from the
   DB to extract only the function body.

5. Write comprehensive tests in tests/test_effects.py using dummy code
   snippets in each language. Test edge cases: patterns inside strings/comments
   should NOT match (use tree-sitter AST to check node types if possible).
```

#### Ticket 6B: Effect Graph Propagation (D3 — Step 2)

**Implements:** Section 35 (propagation layer) | **Effort:** Medium | **Depends on:** 6A

```
Prompt for Agent:

Now propagate the classified effects through the call graph.

1. In src/roam/analysis/effects.py, add:
   - propagate_effects(G: nx.DiGraph, direct_effects: dict[int, set[str]])
     -> dict[int, set[str]]:
     Perform a bottom-up traversal (reverse topological sort). For each node,
     union its direct effects with the effects of all its callees. If A calls B
     and B has WRITES_DB, then A transitively inherits WRITES_DB. Handle cycles
     by iterating until convergence.
   - store_effects(conn, effects: dict[int, set[str]]): persist to DB.

2. Update src/roam/db/schema.py: add symbol_effects table (symbol_id, effect_type,
   source TEXT 'direct'|'transitive', evidence TEXT, confidence REAL). Add
   migration in connection.py.

3. Integrate into src/roam/index/indexer.py: after symbol extraction and edge
   resolution, run classify + propagate + store as a new pipeline step. Make it
   optional (skip if no patterns match the detected languages).

4. Create src/roam/commands/cmd_effects.py:
   - "roam effects <symbol>": show direct + transitive effects with call chain
   - "roam effects --file <path>": effects per function in file
   - Support --json via json_envelope()

5. Write tests in tests/test_effects_propagation.py. Create a fixture with a
   call chain A->B->C where C has WRITES_DB and verify A inherits it.

6. Register "effects" in cli.py _COMMANDS and _CATEGORIES.
```

### Agent Execution Guidelines

**Before every sprint:**
1. Have the agent run `roam health` and `roam map` to understand current state
2. Have the agent run `roam context <target_symbol>` on files it will modify
3. Have the agent run `roam preflight <target>` after making changes

**TDD enforcement:** End every prompt with "Write the pytest test file for this
logic BEFORE you wire up the CLI command." Agents are bad at testing retroactively
but excellent at TDD.

**Context management:** Each ticket above is sized to fit within a single agent
session (~100K context). Do NOT combine tickets — let each one complete, verify
tests pass, then start the next.

**Dependency order:** Tickets within a sprint can run in parallel EXCEPT where
noted. Cross-sprint dependencies:
- Sprint 3 (W1/W2) shares before/after metric infrastructure
- Sprint 4 Ticket 6B depends on 6A
- Sprint 2 (P2) and Sprint 1 (N2) are fully independent

**Do NOT attempt yet:**
- P1 (orchestrate) and P3 (mutate) — these require a stable foundation
- D1 (bisect) — requires significant git history walking infrastructure
- P4 (fingerprint) — lower priority, can ship anytime

**Verification after each ticket:**
```bash
pytest tests/ -x -v           # all tests must pass
roam health                    # verify no health regression
roam diff --fitness            # verify no fitness violations
```

---

## 40. In-Code Markers

The source tree is remarkably clean — zero TODO/FIXME/HACK/XXX annotations.
Only three notable markers exist:

### 40.1 Planned feature note in source

`src/roam/commands/cmd_api_drift.py:448`:
```
NOTE: Cross-repo drift detection (separate PHP + TS repos) is planned for
a future 'roam ws api-drift' workspace command.
```

Not implemented. `cmd_ws.py` has no `api-drift` subcommand.

### 40.2 Deferred grammar aliases

`src/roam/index/parser.py:95-97`:
```python
# Future candidates:
# "jsonc": "json",
# "mdx": "markdown",
```

JSONC and MDX language support commented out pending validation.

### 40.3 Legacy fallback comment

`src/roam/commands/changed_files.py:28`:
```python
# Legacy fallback for patterns file_roles might miss
```

Intentional defense-in-depth, not dead code.

---

## 41. Status Summary

### Done (45 items)
- A: Superior test suite (`test_properties.py`, `test_index.py`, and 9 other test files)
- B: Cross-language bridges (5 bridges: Salesforce, Protobuf, REST API, Template, Config)
- C: Health trend anomaly detection
- D: Smart file role categorization
- E: Dead code aging
- F: Pluggable test naming conventions
- G: Coverage-gaps policy-as-code
- H: PR-Risk team-aware novelty
- N1: Architectural simulations (`roam simulate`)
- N2: Agentic memory (`roam annotate` / `roam annotations`)
- N3: Reachability-based security (`roam vuln-map` + `roam vuln-reach`)
- N5: OpenTelemetry overlay (`roam ingest-trace` + `roam hotspots`)
- N6: TF-IDF semantic search (`roam search-semantic`)
- N7: Predictive tech debt (`roam forecast`)
- P1: Swarm orchestration (`roam orchestrate`)
- P2: Dark matter detection (`roam dark-matter`)
- P3: Syntax-less agentic editing (`roam mutate`)
- P4: Graph-isomorphism transfer (`roam fingerprint`)
- P5: Adversarial architecture review (`roam adversarial`)
- W1: Graph diff as PR artifact (`roam pr-diff`)
- W2: Architecture budget gates (`roam budget`)
- W3: Invariant discovery (`roam invariants`)
- W4: Graph-path test coverage (`roam path-coverage`)
- W5: Agent work planner (`roam plan`) — subsumes N4
- W6: Intent graph from docs (`roam intent`)
- W7: Minimum cut safety zones (`roam cut`)
- W8: Plugin DSL for detectors (`roam rules`)
- D1: Time-travel graph (`roam bisect`)
- D2: Proof-carrying PRs (`roam attest`)
- D3: Effect & side-effect graph (`roam effects`)
- D4: Minimal-change synthesis (`roam closure`)
- D5: Sanitized roam capsule (`roam capsule`)
- Tier 3 J: Symbol relationship graph (`roam relate`)
- Tier 3 L: JSON schema versioning (`roam schema`)
- Ruby Tier 1 language support (`ruby_lang.py`)
- `--sarif` CLI flag (wired to dead, health, complexity, rules)
- JSONC / MDX grammar aliases
- I.10.1: Schema-prefixed table names fix
- I.10.3: Cross-model column attribution fix
- I.10.4: Pluralization edge cases fix
- I.10.6: ServiceProvider auth-gaps detection
- I.10.7: `$hidden` messaging improvement
- pytest-xdist parallel test execution (~2x speedup)
- JSON envelope schema versioning (schema + schema_version fields)
- FK constraint fixes (ON DELETE CASCADE/SET NULL)

### Not Started — Existing (3 items)
- Docker image for CI
- VS Code extension
- Terminal demo GIF

### Idea Dependency Graph

Some ideas unlock or enhance others. This shows the critical path:

```
                    Existing Infrastructure
                    (clusters, coupling, pagerank,
                     cycles, layers, anomaly, detectors,
                     diff, fitness, testmap, context,
                     n1, auth-gaps, breaking)
                            |
     +----------+-----------+-----------+----------+
     |          |           |           |          |
  D3 effects  W1 pr-diff  N2 annotate  P2 dark   D1 bisect
     |         W2 budget   N7 forecast  matter       |
     |            |           |           |          |
     v            v           v           |          v
  D2 attest    W8 rules    N1 simulate   |     (causal debug)
  (evidence)   DSL         W3 invariants |
     |            |           |          |
     |        +---+---+   +--+--+       |
     |        |       |   |     |       |
     v        v       v   v     v       v
  (trust    W4 path  P5 adversarial  (all benefit)
   protocol) coverage W5 plan
                      D4 closure
                         |
                    +----+----+
                    |         |
                    v         v
                 P3 mutate  P1 orchestrate
                    |
                    v
              (full agentic
               architecture OS)

Independent tracks (no blockers):
  N3 CVE mapping   -- can ship anytime (reuses pathfinding)
  N5 OTel overlay  -- can ship anytime (new data source)
  N6 sqlite-vec    -- can ship anytime (new search mode)
  P4 fingerprint   -- can ship anytime (read-only metric extraction)
  W6 intent graph  -- can ship anytime (doc parsing + symbol matching)
  W7 min-cut zones -- can ship anytime (NetworkX min_edge_cut)
  D5 capsule       -- can ship anytime (read-only export)
```

### By Priority

**"THE MOST PROFOUND THING WE FORGOT":**
- D3 + D2: `roam effects` + `roam attest` — effect propagation (what code DOES,
  not just where it connects) combined with proof-carrying PRs (machine-verifiable
  evidence bundles). Together they answer: "this PR introduces a new DB-write path
  that bypasses auth" with a signed attestation. This is the new-category move.

**HIGHEST LEVERAGE (recommended first build):**
- W1 + W2: `roam pr-diff` + `roam budget` — makes roam "the thing you look at"
  in every code review. Shared infrastructure (before/after metric computation).
  Combined, they turn roam from a dev tool into an architectural governance system.

**PARADIGM-SHIFT — The Big Bets:**
- P2: `roam dark-matter` — lowest effort of the P-series, immediate value,
  extends existing coupling command (HIDDEN flag already computed)
- P5: `roam adversarial` — composes 90% from existing detectors + diff + cycles;
  high value for CI integration
- P1: `roam orchestrate` — requires N2 (annotate) first; solves multi-agent
  parallelization mathematically
- P3: `roam mutate` — highest effort, highest impact; agents edit at graph level
- P4: `roam fingerprint` — read-only extraction, useful for scaffolding + comparison

**HIGH / STRATEGIC — Do Next:**
- D3: `roam effects` — effect propagation, upgrades graph from structural to behavioral
- D1: `roam bisect` — architectural git bisect, causality for degradation
- D4: `roam closure` — minimal-change synthesis, precise surgical plans
- N2: `roam annotate` — simple schema change, unlocks P1/P5, persistent agent memory
- N1: `roam simulate` — graph cloning + metrics, gradient descent on architecture
- W3: `roam invariants` — contract discovery, critical for safe agent edits
- W5: `roam plan` — full agent work planner (subsumes N4)
- Finish test suite (A) — `test_properties.py`, `test_index.py`

**MEDIUM-HIGH — High Impact:**
- D2: `roam attest` — proof-carrying PRs, trust protocol (mostly composition)
- P5: `roam adversarial` — CI-ready architectural review, composes existing tools
- W4: `roam path-coverage` — graph-path test gaps (not just line coverage)
- W8: `.roam/rules/` DSL — governance platform, extends fitness.yaml
- N3: CVE reachability mapping — exact vulnerability paths

**MEDIUM — Solid Improvements:**
- D5: `roam capsule` — privacy-preserving structural export
- W7: `roam cut` — minimum cut safety zones, surgical refactoring guidance
- W6: `roam intent` — doc-to-code linking, drift detection
- N7: `roam forecast` — per-symbol predictive tech debt
- N5: OpenTelemetry overlay — runtime traffic weights
- N6: `sqlite-vec` semantic search (also enables P1 prompt-to-subgraph mapping)
- Schema-prefixed table names in `missing-index` (I.1)
- Raw SQL CREATE INDEX parsing (I.2)
- `information_schema` guard recognition (I.5)
- `--sarif` CLI flag
- Ruby Tier 1 support

**LOW / LONG-TERM:**
- P3: `roam mutate` — highest effort, build after simulate + closure are stable
- P4: `roam fingerprint` — novel but niche; can ship independently
- Remaining cross-language bridges (B)
- Backend command edge cases (I.3, I.4, I.6, I.7)
- Docker image, VS Code extension, demo GIF
- Tier 3 ideas (I, J, K, L)

### Suggested Implementation Order

```
Phase 1 — Governance Foundation (makes roam indispensable in CI)
  W1: roam pr-diff       -- graph delta as PR artifact
  W2: roam budget        -- trajectory enforcement, shares infra with W1
  D3: roam effects       -- effect classification + propagation (THE missing axis)
  P2: roam dark-matter   -- extends existing coupling, medium effort

Phase 2 — Trust & Intelligence (the competitive moat)
  D2: roam attest        -- proof-carrying PRs (composes Phase 1 outputs)
  N2: roam annotate      -- schema change, enables agent memory
  N1: roam simulate      -- graph cloning + metric recomputation
  W3: roam invariants    -- contract discovery for safe agent edits

Phase 3 — Agent Strategy
  W5: roam plan          -- complete agent work planner (subsumes N4)
  D4: roam closure       -- minimal-change synthesis (precise surgical plans)
  P5: roam adversarial   -- CI-ready architectural challenge generation
  D1: roam bisect        -- time-travel graph, architectural causality

Phase 4 — Deep Analysis + Governance
  W4: roam path-coverage -- graph-path test gaps (enhanced by D3 effects)
  W8: roam rules DSL     -- user-defined governance rules
  N7: roam forecast      -- per-symbol predictive tech debt
  W7: roam cut           -- minimum cut safety zones

Phase 5 — External Data + Search
  N3: CVE mapping        -- vulnerability path tracing
  N5: OTel overlay       -- runtime weights on static graph
  N6: sqlite-vec         -- semantic search, optional ONNX
  W6: roam intent        -- doc-to-code linking
  D5: roam capsule       -- privacy-preserving export

Phase 6 — The Operating System
  P1: roam orchestrate   -- multi-agent swarm partitioning (needs N2, N1)
  P3: roam mutate        -- syntax-less graph editing (needs D4 for planning)
  P4: roam fingerprint   -- topology transfer across languages
```

Each phase is independently shippable. Later phases build on earlier ones
but don't hard-block them.

### The Strategic Narrative

```
Current state:   "A tool that helps AI agents read code better"
                  (map of the territory — structural graph)

After Phase 1:   "The architectural governance layer for every PR"
                  (graph diffs, budgets, EFFECTS, dark matter)

After Phase 2:   "A trust protocol for software changes"
                  (proof-carrying PRs, agent memory, simulations, invariants)

After Phase 3:   "A mathematical reasoning engine for software architecture"
                  (agent plans, minimal-change synthesis, adversarial review,
                   time-travel debugging)

After Phase 4-5: "The deep analysis platform"
                  (path coverage, forecasting, CVE mapping, semantic search,
                   governance DSL, privacy-preserving export)

After Phase 6:   "The operating system for multi-agent software engineering"
                  (orchestrate swarms, mutate graphs, transfer architectures)
```

### Total Idea Count

| Category | Count | Status |
|----------|-------|--------|
| Existing (A-I + Tier 3 + README) | 15 | 10 done (A,B,C,D,E,F,G,H,I,Tier3-J,L), 5 not started |
| Next-Gen N-series | 7 | 6 done (N1,N2,N3,N5,N6,N7), 1 subsumed (N4→W5) |
| Paradigm-Shift P-series | 5 | 5 done (P1,P2,P3,P4,P5) |
| Workflow W-series | 8 | 8 done |
| Deep Foundation D-series | 5 | 5 done |
| **Total ideas** | **40** | **34 done, 1 subsumed, 5 not started** |

**Bonus items shipped** (beyond the 40-idea framework): Ruby Tier 1 language support, `--sarif` CLI flag, JSONC/MDX aliases, pytest-xdist parallel tests, JSON envelope schema versioning, FK constraint fixes, I.10.1/I.10.3/I.10.4/I.10.6/I.10.7 backend fixes.
