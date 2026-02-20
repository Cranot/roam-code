# Fork Intelligence Report

**Generated:** 2026-02-20
**Upstream:** https://github.com/Cranot/roam-code
**Stars:** 272 | **Forks:** 23 | **Open issues:** 2 | **Created:** 2026-02-09

---

## Executive Summary

Of 23 forks, **4 have commits ahead of upstream** with original work. The rest are
unmodified clones (many already behind by 8-72 commits). Three clear patterns emerge:

1. **Salesforce/enterprise platform support** (river-mounts) -- deepest investment, 21 commits
2. **Power-user workflows and CI integration** (chuckjewell) -- 24 commits across 9 feature branches
3. **New language extractors** (didier1969 for Elixir, DubDev720 aspires to Godot/GDScript)
4. **Code quality cleanup** (flint-dominic) -- ruff linting pass

Two forks (chuckjewell, river-mounts) represent significant independent feature development
that could inform upstream roadmap priorities.

---

## Active Forks (Commits Ahead of Upstream)

### 1. chuckjewell/roam-code -- "Power User Platform"

| Field | Value |
|-------|-------|
| URL | https://github.com/chuckjewell/roam-code |
| Owner | Chuck Jewell (cargo.ai) |
| Ahead/Behind | +24 / -48 |
| Last push | 2026-02-12 |
| Branches | 9 feature branches + main |
| Upstream PR | #1 (fix: numpy/scipy deps) -- closed, not merged |

**What they built (9 distinct features):**

1. **Hypergraph co-change analysis** (`feat/hypergraph-cochange`)
   - Rewrote `cmd_coupling.py` (+202/-90 lines) to use hypergraph-based change-set analysis
   - Added hypergraph table to `schema.py`
   - Added co-change data collection in `git_stats.py`
   - Includes benchmark harness for measuring improvement
   - *Implication:* They found the existing coupling command insufficient for real-world
     change-set correlation and built a mathematically richer model

2. **JSON contract / stable envelope** (`json-contract-v1`)
   - Added `json_envelope()` helper to `formatter.py`
   - Standardized JSON output for health, coupling, dead, diff, pr-risk, risk commands
   - Created `test_json_contracts.py` (98 lines) to enforce schema stability
   - *Implication:* They are consuming roam output programmatically and need stable contracts.
     This was later adopted upstream.

3. **Coupling against changesets** (`coupling-against-changeset`)
   - Extended coupling to run targeted checks against git changesets
   - *Implication:* PR-time coupling checks -- "does this PR increase coupling?"

4. **Diff range parity** (`diff-range-parity`)
   - Clarified staged vs. range mode behavior in `cmd_diff.py`
   - *Implication:* Using diff in CI pipelines where range specification matters

5. **Security: coverage-gaps gate** (`security-coverage-gaps`)
   - New command `cmd_coverage_gaps.py` (172 lines) -- gate reachability analysis
   - Identifies code reachable from entry points that lacks test coverage
   - *Implication:* Security-focused usage, likely for compliance gates

6. **Security: risk explain + grep source filters** (`security-risk-explain-grep`)
   - Extended `cmd_risk.py` with explain chains (+46/-6)
   - Extended `cmd_grep.py` with source-only filters (+47/-6)
   - *Implication:* Wants to trace *why* something is risky, not just *that* it is

7. **Dead code grouping + context batch mode** (`dead-context-ergonomics`)
   - Extended `cmd_dead.py` with grouped output (+69/-10)
   - Extended `cmd_context.py` with batch mode (+77/-2)
   - *Implication:* Running roam on large codebases where dead code lists are long
     and context needs to be gathered for multiple symbols at once

8. **Health snapshot + trend history** (`health-history-trend`)
   - New `cmd_snapshot.py` -- save health scores to history table
   - New `cmd_trend.py` -- show health over time
   - New `metrics_history.py` -- storage layer for historical metrics
   - *Implication:* Tracking codebase health longitudinally, likely for dashboards

9. **Report presets + config runner** (`report-workflows`)
   - New `cmd_report.py` (110 lines) -- compound report command
   - New `report_presets.py` -- pre-built report templates
   - New `report_runner.py` -- config-driven report execution
   - *Implication:* Automating multi-command roam workflows for CI/CD pipelines

**Their direction:** Building roam into a CI/CD-integrated quality gate platform.
Focus on programmatic consumption (JSON contracts), historical tracking (trends),
security gates (coverage-gaps, risk explain), and automation (report presets).
This fork is the most architecturally ambitious.

**What we can learn:**
- JSON output stability matters to programmatic consumers (we adopted json_envelope)
- Health trending over time is a desired feature (we built cmd_trend.py)
- Coverage-gaps reachability is a compelling security feature
- Hypergraph co-change could improve coupling detection accuracy
- Report presets show demand for "run N commands, get one report"

---

### 2. river-mounts/roam-code-sf -- "Salesforce Deep Dive"

| Field | Value |
|-------|-------|
| URL | https://github.com/river-mounts/roam-code-sf |
| Owner | River Mounts (account created 2025-07) |
| Contributors | Mark Ramsell (maintainer), Livio Ravetto, Claude (AI) |
| Ahead/Behind | +21 / -55 |
| Last push | 2026-02-14 |
| PRs merged | 9 internal PRs |

**What they built (iterative Salesforce platform support):**

1. **PR #1:** Apex + metadata XML parsers
   - `apex_lang.py` (552 lines) -- full Apex extractor: triggers, sharing modifiers,
     DML ops, annotations, properties, inner classes, ApexDoc
   - `sfxml_lang.py` (267 lines) -- `-meta.xml` sidecar file extractor
   - 31 tests in `test_salesforce.py` (693 lines)

2. **PR #2-3:** LWC-to-Apex edges, Aura controller case-sensitivity, Visualforce support
   - Cross-language symbol resolution between Lightning Web Components and Apex

3. **PR #4:** Cross-language edges: LWC<->Apex, Flow->Apex, labels, trigger handlers

4. **PR #5:** Fix 12 Salesforce edge resolution, symbol dedup, output issues

5. **PR #6:** Strip namespace prefix from Flow actionName refs

6. **PR #7:** Batch SQL queries for large repos + numpy dependency

7. **PR #8:** Add scipy>=1.17 dependency

8. **PR #9:** Fix overloaded method references silently dropped in `resolve_references`
   - Added fallback logic in `relations.py` for qualified-name ambiguity

**Their direction:** Making roam work for Salesforce DX projects at production scale.
They operate on a large Salesforce codebase (enough to hit SQL batch limits) with
Apex, LWC, Aura, Visualforce, and Flows. They need cross-language edges to work
correctly across the Salesforce platform stack.

**What we can learn:**
- Apex/Salesforce is a real market (this fork has the most PRs of any fork)
- Cross-language edge resolution needs the bridge pattern we later built
- The overload fix in `resolve_references` is a genuine bug worth upstreaming
- Large-repo SQL batching was needed (we adopted `batched_in()`)
- Real users hit namespace prefix issues in Salesforce metadata

---

### 3. didier1969/roam-code -- "Elixir Language Support"

| Field | Value |
|-------|-------|
| URL | https://github.com/didier1969/roam-code |
| Owner | didier1969 (Didier Stadelmann) |
| Ahead/Behind | +1 / 0 |
| Last push | 2026-02-19 |

**What they built:**

- `elixir_lang.py` (421 lines) -- full Elixir extractor
  - Symbols: defmodule, defprotocol, defimpl, defexception, def/defp,
    defmacro/defmacrop, defguard, defstruct, @type/@opaque, @callback
  - References: alias, import, require, use, Module.function() calls
  - Validated on a real project: 297 symbols, 27 edges, 82% parse coverage
- Registry update to register Elixir

**Their direction:** Bringing Elixir into roam's language support. Single focused
contribution, well-tested on a real codebase.

**What we can learn:**
- Elixir community is interested in roam
- The extractor covers the full Elixir module system including protocols and macros
- 82% parse coverage on a real project suggests the grammar works well
- Good candidate for upstreaming as a Tier 1 language

---

### 4. flint-dominic/roam-code -- "Code Quality Cleanup"

| Field | Value |
|-------|-------|
| URL | https://github.com/flint-dominic/roam-code |
| Owner | Flint Dominic (Seattle) |
| Ahead/Behind | +1 / -8 |
| Last push | 2026-02-19 |

**What they did:**

- Single commit: removed 50 unused imports, 16 extraneous f-strings, fixed 1 missing
  import, fixed 15 unused variables across 45 files
- Reduced ruff F-class errors from 83 to 0
- Remaining 211 errors are style-only (E501, E741, E701)

**Their direction:** Code cleanliness. Ran ruff on the codebase and fixed all
functional lint issues.

**What we can learn:**
- The codebase had 83 functional lint issues (dead imports, undefined names)
- A ruff pass would be worthwhile to upstream
- The `cmd_describe.py` had a missing `Path` import (F821 -- real bug)

---

## Forks with Modified Descriptions (No Code Changes Yet)

### HornetGit/xcs-roam-code -- "Adding bash handler"

| Field | Value |
|-------|-------|
| URL | https://github.com/HornetGit/xcs-roam-code |
| Description | "Adding bash handler" (modified from upstream) |
| Ahead/Behind | 0 / 0 (identical to upstream) |
| Last updated | 2026-02-20 (today) |

No code changes yet, but the description signals intent to add Bash/shell script
language support. Still actively watching the repo (updated today).

### DubDev720/roam-code-godot -- "Godot Edition"

| Field | Value |
|-------|-------|
| URL | https://github.com/DubDev720/roam-code-godot |
| Description | "Instant codebase comprehension for AI coding agents - Godot Edition" |
| Ahead/Behind | 0 / -14 (behind, no new commits on main) |
| Last push | 2026-02-17 |
| Owner bio | "Dev... long-term connection w/ systems, tooling, & clean architecture" |

No GDScript extractor committed yet. The C# work visible in commit history was
already in the upstream branch they forked from. Aspirational fork for Godot
game engine support (GDScript).

### syntax-syndicate/roam-codebase-knowledge

| Field | Value |
|-------|-------|
| URL | https://github.com/syntax-syndicate/roam-codebase-knowledge |
| Ahead/Behind | 0 / -8 |

Renamed fork, no changes. Likely a collector/aggregator account.

---

## Inactive Forks (No Changes, Behind Upstream)

| Fork | Behind | Last Push | Notes |
|------|--------|-----------|-------|
| domocarroll/roam-code | -8 | Feb 18 | Clone only |
| AlexMikhalev/roam-code | -8 | Feb 18 | Clone only |
| robin-rrt/roam-code | -9 | Feb 18 | Clone only |
| otrofimo/roam-code | -10 | Feb 18 | Clone only |
| NetRxn/roam-code | -10 | Feb 18 | Clone only |
| rkunnamp/roam-code | -12 | Feb 18 | Clone only |
| holive/roam-code | -23 | Feb 17 | Submitted C# extractor PR (#3, closed) |
| ematus1681/roam-code | -23 | Feb 14 | Clone only |
| santoshdahale/roam-code | -23 | Feb 14 | Clone only |
| mydcc/roam-code | -23 | Feb 14 | Clone only |
| jpsangare/roam-code | -23 | Feb 14 | Clone only |
| didierhk/roam-code | -25 | Feb 14 | Clone only |
| edsonmartins/roam-code | -34 | Feb 13 | Clone only |
| oondeo/roam-code | -37 | Feb 13 | Clone only |
| pskpatil/roam-code | -42 | Feb 12 | Clone only |
| xtremebeing/roam-code | -72 | Feb 9 | Earliest fork, clone only |

---

## Upstream Issues & PRs from Fork Authors

| # | Title | Author | Status | Fork? |
|---|-------|--------|--------|-------|
| #1 | fix(deps): add numpy/scipy for graph metrics | chuckjewell | Closed | Yes |
| #2 | Add C# Tier 1 extractor | holive | Closed | Yes |
| #3 | feat: add C# Tier 1 extractor | holive | Closed | Yes |
| #7 | roam math suggestions in wrong language | camball | Open | No |
| #8 | Ignore unwanted code by extension/pattern | camball | Open | No |

Notable: chuckjewell submitted a dependency fix PR that was closed (not merged).
holive submitted the C# extractor twice (both closed). Neither contributor has had
their fork work merged upstream, which may explain why chuckjewell maintains such
an extensive parallel development.

---

## Patterns Across Forks

### 1. Enterprise Platform Support Demand
- river-mounts: Salesforce (Apex, LWC, Aura, Visualforce, Flows)
- DubDev720: Godot (GDScript aspiration)
- HornetGit: Bash/shell scripts (aspiration)
- didier1969: Elixir

Users want roam to work on their specific tech stacks. The Salesforce fork is the
deepest, suggesting enterprise Salesforce teams see high value in roam.

### 2. CI/CD and Programmatic Consumption
- chuckjewell: JSON contracts, report presets, health trending, security gates
- Multiple forks: dependency fixes (numpy, scipy) needed for production use

Power users want to integrate roam into automated pipelines, not just use it
interactively. They need stable JSON output, compound reports, and historical tracking.

### 3. Code Quality is a Concern
- flint-dominic: 83 ruff errors fixed across the codebase
- Multiple forks hit runtime issues (missing numpy/scipy, SQL batch limits)

The codebase ships with functional lint issues and missing runtime dependencies
that users discover quickly.

### 4. Cross-Language Edge Resolution is Hard
- river-mounts needed 9 PRs to get Salesforce cross-language edges working
- The bridges architecture was developed in response to this pattern

Real multi-language projects need edges that cross technology boundaries
(LWC->Apex, Flow->Apex, etc.).

### 5. AI-Assisted Development is Common
- river-mounts: Most commits authored by "Claude" with claude.ai session links
- All forks: Likely using AI agents to extend roam (tool for agents, built by agents)

---

## Online Presence & Mentions

### PyPI
- Package: `roam-code` v9.1.0 (published 2026-02-18)
- Author: CosmoHac / DimitrisMitsos

### Web Coverage
- No Hacker News Show HN post found
- No Reddit threads found
- No dedicated blog posts found
- Not listed in Tembo's "2026 Guide to Coding CLI Tools: 15 AI Agents Compared"
- GitHub is the primary (and essentially only) presence

### Upstream Issues Signal Real Usage
- Issue #8 (camball): Auto-generated protobuf/gRPC files clutter output -- wants
  `.roamignore` or extension-based filtering. Notes roam is "a really cool concept"
  but "entirely unusable" without ignore patterns.
- Issue #7 (camball): `roam math` suggestions are Python-specific even when analyzing
  JS code. Suggests per-language tip text.

---

## Strategic Takeaways

1. **Upstream chuckjewell's best ideas.** Health trending, report presets, and
   coverage-gaps are high-value features. The JSON contract work was already
   adopted. The hypergraph co-change model deserves evaluation.

2. **Consider upstreaming the Elixir extractor.** didier1969's work is clean,
   well-validated, and a single PR away from contributing a new Tier 1 language.

3. **The overload resolution bug from river-mounts is real.** The fix to
   `resolve_references` for overloaded methods should be reviewed and upstreamed.

4. **Run a ruff pass.** 83 functional lint issues (including a real missing import)
   suggest a quick cleanup round is warranted.

5. **File ignore patterns are blocking adoption.** Issue #8 is the most detailed
   user feedback received, from someone who wants to use roam but cannot. The
   `.roamignore` feature (already in didier1969's upstream sync) addresses this.

6. **Per-language math tips would improve polish.** Issue #7 is a simple fix that
   improves cross-language UX.

7. **No HN/Reddit presence yet.** The 272 stars came from GitHub discovery alone.
   A Show HN post or blog post could significantly increase visibility.
