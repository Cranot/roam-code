# Fork Analysis Summary — roam-code

Generated: 2026-02-14

## Overview

6 forks analyzed. 4 are unmodified clones. 2 contain meaningful work.

| Fork | Owner | Status | Unique Commits | Value |
|------|-------|--------|----------------|-------|
| xtremebeing | xtremebeing | Unmodified clone | 0 | None |
| edsonmartins | edsonmartins | Unmodified clone | 0 | None |
| oondeo | oondeo | Unmodified clone | 0 | None |
| pskpatil | pskpatil | Unmodified clone | 0 | None |
| chuckjewell | chuckjewell | Active — 24 commits | 24 | 12 features (code superseded), 11 test files survive |
| river-mounts | river-mounts | Active — 15 commits | 15 | Salesforce DX specialization, 4 languages, cross-language edges |

**Key finding:** ALL 14 features from both forks already exist in our upstream codebase.
The main gaps are: 11 missing test files and opportunities to go deeper on each concept.

---

## Fork 1: chuckjewell — 12 Feature Ideas

All 12 features were independently developed and later superseded by upstream v7.2.
What survives: 11 integration test files (+1,084 lines). Tests are subprocess-based,
each creating a temp git repo with fixtures.

### Ideas & Current Status

| # | Idea | We Have It? | Gap / Improvement Opportunity |
|---|------|-------------|-------------------------------|
| 1 | Hypergraph N-ary coupling (3+ file change patterns) | YES | Add temporal decay, velocity-weighted patterns, ML clustering |
| 2 | Coupling --against/--staged/--pr (missing co-changer detection) | YES | Add confidence intervals, team-specific pattern detection |
| 3 | Coverage-gaps gate reachability (BFS for unprotected endpoints) | YES | Add policy-as-code files, auto-detect common gate patterns across frameworks |
| 4 | Risk --explain with callee chains | YES | Add visual chain rendering, risk propagation heat scoring |
| 5 | Grep --source-only and --exclude filters | YES | Add smart file categorization (auto-detect code/test/config/docs) |
| 6 | Dead code grouping (--by-directory, --by-kind, --summary) | YES | Add dead code aging (days since last reference), effort estimation |
| 7 | Context multi-symbol batch mode (shared callers) | YES | Add relationship graph between queried symbols, conflict detection |
| 8 | Snapshot + trend health history with CI assertions | YES | Add anomaly detection, regression prediction, trend forecasting |
| 9 | Compound report presets + custom config runner | YES | Add template engine, scheduled CI reports, diff-aware reports |
| 10 | Stable JSON envelope (version, index_age, project) | YES | Add schema versioning, backward-compat guarantees |
| 11 | Diff mode parity (source field in JSON) | YES | Fully implemented |
| 12 | PR-Risk hypergraph novelty signal | YES | Add team-specific patterns, time-weighted novelty decay |

### Test Coverage Gap (HIGH PRIORITY)

11 test files from chuckjewell that we DON'T have:
- test_context_batch.py (79 lines) — multi-symbol context batch mode
- test_coupling_against.py (143 lines) — coupling --against/--staged modes
- test_coverage_gaps.py (138 lines) — gate reachability analysis
- test_dead_grouping.py (87 lines) — --by-directory/--by-kind/--summary
- test_diff_range_parity.py (97 lines) — diff label/source field parity
- test_grep_source_only.py (85 lines) — --source-only/--exclude filters
- test_json_contracts.py (98 lines) — JSON envelope contract on 6 commands
- test_report_custom_config.py (78 lines) — custom report config loading
- test_report_presets.py (70 lines) — built-in report presets
- test_risk_explain.py (77 lines) — risk --explain chain output
- test_snapshot_trend.py (94 lines) — snapshot/trend with CI assertions

These represent ~1,046 lines of test coverage we should write ourselves (better).

---

## Fork 2: river-mounts — Salesforce DX Specialization

15 commits across 8 PRs. Full Salesforce ecosystem support with cross-language edges.
All features already merged/implemented in upstream.

### Ideas & Current Status

| # | Idea | We Have It? | Gap / Improvement Opportunity |
|---|------|-------------|-------------------------------|
| 1 | Apex class/trigger/enum/method/field extraction | YES | Mature |
| 2 | Aura component/app/event extraction | YES | Mature |
| 3 | Visualforce page/component extraction | YES | Mature |
| 4 | SF Metadata XML extraction (50+ types) | YES | Mature |
| 5 | Cross-language edge resolution (@salesforce/* imports) | YES | GENERALIZE: monorepo cross-language support beyond Salesforce |
| 6 | Context-aware XML reference extraction | YES | GENERALIZE: apply pattern to other XML/config ecosystems |
| 7 | Canonical symbol disambiguation (priority-based) | YES | GENERALIZE: framework-agnostic disambiguation system |
| 8 | Anonymous class default exports (JS/TS) | YES | Mature |
| 9 | Salesforce test naming conventions | YES | GENERALIZE: pluggable test naming conventions per ecosystem |
| 10 | SQL batching for large repos | YES | Already uses batched_in() |
| 11 | Defensive null safety (.get() patterns) | YES | Hardened |
| 12 | stdout flush for large repos | YES | Present |

### Generalization Opportunities (STRATEGIC)

The Salesforce-specific cross-language patterns could be generalized:
- **Cross-language edges for ANY monorepo** (e.g., Python+JS, Go+Proto, Java+Kotlin)
- **Pluggable test naming conventions** per ecosystem (not just Salesforce)
- **Context-aware config parsing** for any XML/YAML/JSON config ecosystem
- **Canonical disambiguation** as a framework-agnostic plugin system

---

## Top Ideas to Implement (Deeper, Cleaner, Smarter)

### Tier 1 — High Impact, Clear Path

| ID | Idea | Source | Why |
|----|------|--------|-----|
| A | Write 11 superior test files for existing features | chuckjewell | 0 of 11 test files exist. Critical coverage gap. |
| B | Generalized cross-language edge resolution | river-mounts | Their SF pattern is a template for ANY multi-lang monorepo |
| C | Health trend anomaly detection + forecasting | chuckjewell | Beyond simple sparklines — detect degradation patterns |
| D | Smart file categorization for grep/search | chuckjewell | Auto-detect file roles instead of static exclude lists |

### Tier 2 — Medium Impact, Strategic

| ID | Idea | Source | Why |
|----|------|--------|-----|
| E | Dead code aging + effort estimation | chuckjewell | Time-based staleness adds actionable priority |
| F | Pluggable test naming conventions | river-mounts | Not just SF — support Go, Rust, Java, Python conventions |
| G | Coverage-gaps policy-as-code | chuckjewell | Define gates in a config file, auto-detect framework patterns |
| H | PR-Risk team-aware novelty | chuckjewell | Weight patterns by team/author for more accurate risk |

### Tier 3 — Nice to Have

| ID | Idea | Source | Why |
|----|------|--------|-----|
| I | Report template engine | chuckjewell | Go beyond presets — customizable report templates |
| J | Context symbol relationship graph | chuckjewell | Show how batch-queried symbols relate to each other |
| K | Risk chain heat visualization | chuckjewell | Score propagation through call chains |
| L | JSON envelope schema versioning | chuckjewell | Formal contract for consumers |

---

## Implementation Roadmap (Researched & Validated)

Detailed implementation docs in `docs/ideas/`:

### Phase 1 — Foundation (v7.6.0)
**A: Test Suite** -- Write 12 test files (~545 tests) using CliRunner, shared fixtures,
JSON contract validation (jsonschema), snapshot testing (syrupy). Replaces fork's
subprocess approach. Zero production code changes. `docs/ideas/A_test_coverage.md`

**C: Trend Anomaly Detection** -- Add Modified Z-Score, Theil-Sen slope, Mann-Kendall
significance, and Western Electric rules to `roam trend`. Zero new dependencies (Python
stdlib only). ~80 lines of algorithm code. `docs/ideas/C_health_anomaly_detection.md`

### Phase 2 — Intelligence (v7.7.0)
**D: Smart File Roles** -- Three-tier heuristic classifier (path -> filename -> content).
New `file_role` column in files table. Replaces static exclude lists in grep/testmap.
Based on GitHub Linguist + go-enry patterns. `docs/ideas/D_smart_file_categorization.md`

**E: Dead Code Aging** -- Decay score (0-100) + effort estimation using existing DB data
(blame, complexity, clusters, coupling). No new indexing pass needed.
`docs/ideas/E_dead_code_aging.md`

**H: PR-Risk Author Awareness** -- Two new factors: exponential-decay familiarity +
minor-contributor risk. Uses existing schema. Privacy-first design (opt-in, local-only).
`docs/ideas/H_pr_risk_team_aware.md`

### Phase 3 — Ecosystem (v8.0.0)
**B: Cross-Language Bridges** -- Plugin architecture (bridges/ dir, CrossLanguageBridge ABC).
Phase 1: refactor Salesforce into bridge_salesforce.py. Phase 2: add bridge_protobuf.py.
Phase 3: heuristic bridges (REST API, templates, config). Inspired by SCIP/Kythe.
`docs/ideas/B_cross_language_edges.md`

**F: Test Conventions** -- Pluggable adapter system for 15 languages, 25+ frameworks.
Consolidates duplicated _is_test_file() logic. Adds source-to-test reverse mapping.
`docs/ideas/F_pluggable_test_conventions.md`

**G: Coverage-Gaps Policy** -- Auto-detect 12 frameworks from dependency manifests.
Ship built-in gate presets. Support .roam-gates.yml policy files with exemptions.
`docs/ideas/G_coverage_gaps_policy.md`

---

## Research Sources

8 deep-research sessions completed covering:
- Cross-language edges (SCIP, Kythe, Glean, Stack Graphs, CodePrism, JetBrains PSI)
- Anomaly detection (Modified Z-Score, Theil-Sen, Mann-Kendall, CUSUM, EWMA, BOCD)
- File categorization (GitHub Linguist, go-enry, scc heuristics)
- Dead code aging (SQALE, SCARF at Meta, Knight Capital case study)
- Test conventions (13 languages, 25+ frameworks, IDE discovery patterns)
- Coverage-gaps policy (OPA/Rego, Cedar, Casbin, Semgrep, CodeQL, route-detect)
- PR-risk team awareness (Bird et al. at Microsoft, Kamei framework, gitpert)
- Test architecture (CliRunner, pytest fixtures, jsonschema, syrupy, Hypothesis)
