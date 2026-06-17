# Legacy audit report template — `{PROJECT_NAME}`

> Superseded for launch. The current paid offer is PR Replay. Use this only as
> source material for a rewritten PR Replay deliverable.

> Prepared by **{AUDITOR_NAME}**, {AUDIT_DATE}.
> Methodology: roam-code v{ROAM_VERSION}. Local SQLite + tree-sitter graph + git history.
> Evidence stays on your machine and hash-verifies offline. No code or content sent to any LLM unless ROAM_AI_ENABLED=1.

---

## 0 · Executive Summary

| Metric | Value | Action |
|---|---|---|
| **Health score** (0-100) | `{health_score}` / 100 | {action_health} |
| **Imported coverage** | `{imported_coverage_pct}%` | {action_coverage} |
| **Public API surface** | `{api_surface}` symbols | — |
| **Danger-zone files** | `{danger_zone_count}` | {action_danger} |
| **Total dead exports** | `{dead_count}` (`{safe_dead}` safe, `{review_dead}` review) | {action_dead} |
| **Test files** | `{test_count}` (pyramid: `{unit}/{integration}/{e2e}`) | {action_tests} |

**One-line verdict:** `{verdict}`

**Top 3 blockers** (highest leverage to address before agent-driven work):

1. `{blocker_1}` — _why this matters: {reasoning_1}_
2. `{blocker_2}` — _why this matters: {reasoning_2}_
3. `{blocker_3}` — _why this matters: {reasoning_3}_

---

## 1 · Inventory

| Dimension | Count |
|---|---|
| Files | `{file_total}` |
| Symbols (functions / classes / methods / vars) | `{symbol_total}` |
| Lines of code | `{line_total}` |
| Tracked git commits | `{commits_total}` |
| Active authors (last 30 days) | `{active_authors}` |
| Languages indexed | `{languages_count}` |

### 1.1 Language breakdown
> _from `roam stats --json`_

| Language | Files | % |
|---|---|---|
| {lang_1} | {lang_1_files} | {lang_1_pct}% |
| {lang_2} | {lang_2_files} | {lang_2_pct}% |
| ... | ... | ... |

---

## 2 · Architecture

### 2.1 Health by category
> _from `roam health --json --explain`_

| Factor | Health | Weight | Loss (pp) |
|---|---|---|---|
| Tangle ratio | `{tangle_ratio}` | 0.30 | `{tangle_loss}` |
| God components | `{god_health}` | 0.20 | `{god_loss}` |
| Bottlenecks | `{bottleneck_health}` | 0.15 | `{bottleneck_loss}` |
| Layer violations | `{layer_health}` | 0.15 | `{layer_loss}` |
| File health | `{file_health}` | 0.20 | `{file_loss}` |
| Imported coverage | `{coverage_health}` | 0.10 | `{coverage_loss}` |

### 2.2 Cycles & layering
> _from `roam --detail health` and `roam graph-stats`_

- **Non-trivial cycles**: `{nontrivial_cycles}` (largest SCC = `{largest_scc}` nodes)
- **Layer violations**: `{layer_violations}`
- **Density**: `{density}` — {density_interpretation}

### 2.3 God components (top 5 actionable)
| Symbol | Kind | Fan-in | File:Line |
|---|---|---|---|
| `{god_1}` | `{god_1_kind}` | `{god_1_fan_in}` | `{god_1_loc}` |
| ... | ... | ... | ... |

### 2.4 Bottlenecks (top 5)
| Symbol | Edge betweenness | File:Line |
|---|---|---|
| `{bn_1}` | `{bn_1_score}` | `{bn_1_loc}` |
| ... | ... | ... |

---

## 3 · Code Quality

### 3.1 Top complexity offenders
> _from `roam complexity --top 10`_

| # | Symbol | Cognitive complexity | File:Line | Recommendation |
|---|---|---|---|---|
| 1 | `{cx_1}` | `{cx_1_cc}` | `{cx_1_loc}` | {cx_1_rec} |
| 2 | `{cx_2}` | `{cx_2_cc}` | `{cx_2_loc}` | {cx_2_rec} |
| ... | ... | ... | ... | ... |

### 3.2 Hotspot intersection (high-churn × high-complexity × high-fan-in)
> _from `roam hotspots --danger`_

These are the files where every change has the largest blast radius. Refactoring or
test-hardening here yields disproportionate ROI.

| File | Score | Churn | Complexity | Max fan-in |
|---|---|---|---|---|
| `{hot_1_file}` | `{hot_1_score}` | `{hot_1_churn}` | `{hot_1_cx}` | `{hot_1_fanin}` |
| ... | ... | ... | ... | ... |

### 3.3 Duplication
> _from `roam clones --persist` and `roam clones --by-file`_

- `{clone_clusters}` clone clusters across `{clone_files}` file pairs
- Top-coupled file pair: `{top_clone_pair_a}` ↔ `{top_clone_pair_b}` (`{top_clone_count}` shared functions)

### 3.4 Dead code
> _from `roam dead --aging --effort`_

- `{dead_total}` dead exports total
- `{dead_safe}` safe to delete (no callers, not test-only, not scaffolding)
- `{dead_review}` need review
- Total dead LOC: `{dead_loc}` · estimated removal effort: `{dead_hours}h`

---

## 4 · Test Health

### 4.1 Test pyramid
> _from `roam test-pyramid`_

| Kind | Count |
|---|---|
| Unit | `{tests_unit}` |
| Integration | `{tests_integration}` |
| E2E | `{tests_e2e}` |
| Smoke | `{tests_smoke}` |
| Unknown (filename-only convention) | `{tests_unknown}` |

**Verdict:** {pyramid_verdict}

### 4.2 Coverage
> _from `roam health` (imported coverage)_

- Imported lines: `{covered_lines}` / `{coverable_lines}` (`{coverage_pct}%`)
- Source: {coverage_source}

### 4.3 Test → code reach
> _from `roam test-impact <range>` for the last `{commit_window}` commits_

- `{tests_reachable}` test files reachable from changed symbols
- Top-impact tests:
  1. `{test_1}` — reaches `{test_1_reach}` changed symbols
  2. `{test_2}` — reaches `{test_2_reach}` changed symbols

---

## 5 · Security & Supply Chain

### 5.1 Taint findings
> _from `roam taint`_

- Risk score: `{taint_risk_score}` / 100
- Findings: `{taint_count}` (`{taint_errors}` error / `{taint_warnings}` warning / `{taint_sanitized}` sanitized)

### 5.2 Vulnerability surface (if SBOM available)
> _from `roam vulns` and `roam supply-chain`_

- Direct dependencies: `{deps_direct}`
- Vulnerable dependencies: `{deps_vuln}` (severity breakdown: `{deps_critical}/{deps_high}/{deps_medium}`)
- Reachable from entry points: `{deps_reachable_count}`

---

## 6 · Agent workflow readiness

This section addresses the question paying customers actually have:
**"Is this codebase ready for AI agents to safely modify it?"**

### 6.1 Indicators that say YES
- ✅ {agent_pos_1}
- ✅ {agent_pos_2}
- ✅ {agent_pos_3}

### 6.2 Indicators that say NO (must address first)
- ❌ {agent_neg_1}
- ❌ {agent_neg_2}
- ❌ {agent_neg_3}

### 6.3 Recommended pre-agent baseline
1. {pre_agent_1}
2. {pre_agent_2}
3. {pre_agent_3}

---

## 7 · Recommended Roadmap

### 7.1 Quick wins (1-2 days each)
1. `{qw_1}`
2. `{qw_2}`
3. `{qw_3}`

### 7.2 Medium leverage (1-2 weeks each)
1. `{med_1}`
2. `{med_2}`

### 7.3 Strategic (1-3 months)
1. `{strat_1}`

---

## 8 · Appendices

### A · Raw command output
> The structured JSON output from `roam audit --json` is attached as `audit-report-data.json`.
> Every metric in this report is derivable from that envelope.

### B · Methodology
- Index built with `roam index` at commit `{indexed_commit}`.
- Dataflow analysis: tree-sitter ASTs across `{languages_count}` languages.
- Graph algorithms: PageRank (personalised), Tarjan SCC, Louvain communities, Fiedler vector.
- Git temporal: 30-day churn / co-change / weather / blame.
- Health composite: weighted geometric mean of 6 factors (tangle, god, bottlenecks, layers, file health, coverage).

### C · Reproducibility
```bash
git checkout {indexed_commit}
roam init
roam audit --json > audit-report-data.json
```

### D · Limitations
- Static analysis only. Runtime behavior not measured beyond optional trace ingestion.
- Cross-procedural taint is intra-graph; framework-specific dataflow (Django ORM, Express middleware) only partially modelled.
- Multi-repo federation is an opt-in workspace feature; this audit covers a single repository unless `roam ws` was configured.

---

> **Attribution.** Generated from roam-code v{ROAM_VERSION}.
> Re-run any time with `roam audit --brief` (summary only) or `roam audit` (full envelope).
