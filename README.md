<div align="center">

# roam-code

**The local codebase intelligence layer that lets AI coding agents earn the right to change code — and prove they did.**

[![PyPI version](https://img.shields.io/pypi/v/roam-code?style=flat-square&color=blue)](https://pypi.org/project/roam-code/)
[![GitHub stars](https://img.shields.io/github/stars/Cranot/roam-code?style=flat-square)](https://github.com/Cranot/roam-code/stargazers)
[![CI](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml/badge.svg)](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

<sub>Credential-free · zero network egress · tamper-evident `ChangeEvidence` packets · Apache 2.0 · runs entirely on your machine</sub>

<sub>241 commands · 224 MCP tools (57 in the default `core` preset) · 28 languages</sub>

![roam terminal demo](docs/assets/roam-terminal-demo.gif)

</div>

---

## Why Roam is different

Cursor, Cody, Aider, and Windsurf are **human-first IDE surfaces** that log a session. Roam is an **agent-first CLI surface** that gates the change and emits proof. The moat is three properties no competitor combines today:

- **Credential-free.** No account, no API key, no cloud login. `pip install` and run.
- **Zero network egress.** Your source code never leaves the machine. Air-gapped repos work the same as cloud repos.
- **Tamper-evident `ChangeEvidence` packets.** Every AI-assisted change compiles into one portable evidence packet (HMAC-chained run ledger + signed Code Graph Attestation + signed PR bundle) that answers the eight evidence questions: *who acted, what authority existed, what context was read, what changed, what could break, what policy applied, what verified it, who accepted risk*. Cursor logs the run; Roam proves the change.

Underneath sits a SQLite-backed graph of symbols, calls, imports, architecture layers, git history, runtime traces, smells, clones, security flows, and algorithmic patterns across 28 languages. Agents and developers query the same local facts before a change, during review, and after the patch lands.

---

## Install + first three commands

Ten minutes from `pip install` to a verdict on whether your next edit is safe.

```bash
pip install "roam-code[mcp]"          # 1. install with MCP server for Claude Code / Cursor / Continue
cd /path/to/your/repo
roam init                             # 2. index the repo into .roam/index.db (one-time, ~30s on most repos)
roam health                           # 3. composite 0-100 score: complexity, cycles, dark-matter coupling, dead code
roam preflight <symbol>               # 4. blast radius + tests + complexity + architecture rules before you edit
```

Requires Python 3.10+. `pipx install roam-code` and `uv tool install roam-code` work too. Drop `[mcp]` if you only want the CLI.

**Want to see it before installing?** [`docs/fresh-install-smoke.md`](docs/fresh-install-smoke.md) is a verbatim transcript of these four commands run against a clean venv and a three-file synthetic project — copy-paste reproducible, no synthetic output.

---

## What's next

Pick the path that matches your role:

- **5-minute demo (CTO/CISO/dev-tools-lead):** [The Canonical Demo](https://roam-code.com/docs/canonical-demo) — install → health → preflight → critique → signed `ChangeEvidence` packet, in five commands, without leaving the laptop. This is the screen-recording arc that establishes the moat in one sitting.
- **Developer tutorial (15 min):** [Getting Started](https://roam-code.com/docs/getting-started) — install, index, query, ship.
- **Agent integration:** `roam mcp-setup claude-code` (or `cursor`, `continue`) — then see [Using Roam via MCP](https://roam-code.com/docs/mcp-usage) for the cold-start envelope and canonical agent loop.
- **Full surface reference:** [Command Reference](https://roam-code.com/docs/command-reference) — every command, every flag, every JSON envelope.
- **Architecture deep-dive:** [Architecture](https://roam-code.com/docs/architecture) — how the graph, findings registry, run ledger, and evidence compiler fit together.

---

## What's New in v13

### v13.2 (released 2026-05-16) -- Evidence freshness + resolution disclosure + public-surface cleanup

- **Canonical unresolved-path envelopes across high-traffic commands.** `impact`, `preflight`, `trace`, `test-map`, `context`, `safe-delete`, `split`, and `why` now use one explicit "not found" shape in JSON mode instead of mixing exit codes and partial-success vocabulary.
- **Evidence freshness is now stamped at the producer.** Runs record hashes for `.roam-rules.yml`, `.roam/constitution.yml`, and `.roam/control-map.yml`, so later evidence packets can prove which policy/config inputs were active.
- **PR Replay evidence coverage improved.** The replay path now answers 7 of the 8 evidence questions completely and marks the remaining approvals question as `producer_not_available` instead of silently omitting it.
- **Public surface refreshed to the current shape.** README, MCP metadata, and install guidance now describe the 238-command / 224-tool v13.2 surface with the 57-tool core preset.

### v13.1 (released 2026-05-15) -- Pattern-2 propagation + shared YAML helper + 3 flagship silent-fallback seals

- **3 flagship Pattern-2 silent-fallback bugs sealed (W826/W834/W836).** `cmd_taint`, `cmd_health`, and `cmd_doctor` now emit explicit `state="empty_corpus"` + `partial_success=True` on unanalyzed repos instead of false `Healthy 100/100` / `No taint findings` / `all checks passed` verdicts. Security-critical for `cmd_taint`, CI-gate-critical for `cmd_health --gate`.
- **Shared YAML config-loader helper.** New `src/roam/commands/_yaml_loader.py::load_yaml_with_warnings()` absorbs the boilerplate Pattern-2 plumbing (PyYAML + tiny-parser + structured warnings + root-type check). 5 of 7 surveyed YAML loaders migrated. Net ~125 LOC removed across the package.
- **5 new live smell detectors.** `type-switch`, `speculative-generality`, `empty-catch`, `cross-layer-clone`, and `parallel-hierarchy` bring the `roam smells` roster to 24 deterministic detectors. The rename-invariant clone work exists as clone-analysis groundwork, not a public `roam smells --kind` id yet.
- **`@detector` registry consolidation (W941, Gate-1 closure).** `ALL_DETECTORS` and `_SMELL_KIND_TO_CONFIDENCE` are derived views from the `@detector`-decorated registry. Parallel-maintenance debt class eliminated for smell detectors.
- **Cargo-cult `or ""` cleanup (W1029/W1013/W1014/W1034).** 14 defensive wrappers removed across `cmd_complexity`, `cmd_fan`, `cmd_risk`, `cmd_fn_coupling`, `laws/miner`, `world_model/causal_graph`, `search/tfidf`, `search/index_embeddings`. 3 helpers None-guarded at source.
- **SQL `LIKE` `ESCAPE` discipline (W990–W993).** 26 wildcard-unsafe `LIKE` patterns hardened across 4 files; drift-guard test enforces forever.
- **Catalog/_shared.py hoisting.** 6 helpers consolidated to a canonical home with `__all__`; 6 catalog/* modules adopted the `__all__` discipline; cross-layer `_is_test_path` / `_camel_split` / `_enclosing_symbol` / `_parse_iso` / `make_finding_id` / `make_smell_finding` all single-sourced.
- **30+ behavioral Pattern-2 fixes** across `cmd_alerts` / `cmd_smells` / `cmd_pr_risk` / `cmd_taint` / `cmd_health` / `cmd_doctor` / `finding_suppress` / `smells_suppress` / `suppression` / `sarif`.
- **Empty-corpus smoke sweep (W805 + W639 + W661).** 25+ detectors smoke-tested; 12 already clean, 7 auto-fixed by W817 helper-level auto-inject, 3 dedicated flagship fixes. Forbidden-fragment blacklist (`"safe"` / `"healthy"` / `"100/100"` / `"no concerns"`) prevents regressions.
- **No persisted-data breaks.** Hash-stability mandate held: 31/31 `test_evidence_schema_migration.py` byte-identical; `make_finding_id` hashes confirmed byte-identical before/after consolidation.

### v13.0 (released 2026-05-13) -- Agent-OS substrate + Laravel idioms + Vue SFC

- **Agent-OS control plane.** New repo-local substrates under `.roam/`: constitution
  (single source for laws / rules / memory / gates), HMAC-chained run ledger
  (`roam runs start|verify|end`), multi-agent leases (`roam lease claim|release|list`),
  portable agent memory (`.roam/memory.jsonl`), and 4 cumulative modes
  (`read_only` → `safe_edit` → `migration` → `autonomous_pr`). Mode enforcement
  is opt-in behind `ROAM_MODE_ENFORCEMENT=1` for v13.0 (PR-C ready; staged
  rollout). See CLAUDE.md "Agent OS substrate" for the canonical loop.
- **World-model classifiers (R28).** 4 new detectors with first-class CLI surface:
  `roam side-effects` (io_read / io_write / mutation / process / none),
  `roam idempotency` (idempotent / non_idempotent / unknown),
  `roam causal-graph` (param → sink dependency edges), and
  `roam tx-boundaries` (begin / commit / rollback regions + `unsafe_mutation`
  outside-tx findings).
- **Laravel dynamic-dispatch idioms.** New `src/roam/index/laravel_post.py`
  post-resolver catches 7 of 8 implicit-edge idioms that `auth-gaps`, `n1`,
  and `algo` were silently missing: Route closures, Eloquent scopes, Policy
  resolution, Observer registration, Job dispatch, Queue worker dispatch,
  and Artisan commands.
- **Vue SFC import graph.** Single-File Component support (`.vue`) — template,
  script, and style blocks parsed into the symbol graph; imports + component
  registrations resolved across the SFC boundary so `roam impact`, `roam context`,
  and `roam preflight` work on Vue projects.
- **Plugin substrate (R25) validated end-to-end.** The `roam-plugin-*` entry-point
  surface (see CLAUDE.md "Writing a roam plugin") shipped clean cut on Rails
  Path A — framework-specific knowledge can ship as a plugin instead of
  landing in core.
- **~20 new CLI commands.** `roam brief`, `roam next`, `roam mode`,
  `roam constitution`, `roam laws`, `roam memory`, `roam lease`, `roam runs`,
  `roam replay`, `roam agent-score`, `roam agents-md`, `roam architecture-drift`,
  `roam graph-diff`, `roam side-effects`, `roam idempotency`, `roam causal-graph`,
  `roam tx-boundaries`, `roam batch-search`, `roam complete`, `roam mcp`.
- **Real-world feedback fixes.** `stale-refs` heading-slugger now matches
  GitHub's algorithm exactly; `stale-refs --fix` URL-half + bare-backtick
  corruption guards; `algo` nested-lookup dataflow predicate +
  PHP `===`/`!==` detection; `auth-gaps` helper indirection (2-level same-class +
  ancestor descent); `over-fetch` 3-state classification
  (BARE / GUARDED_RELATION / UNGUARDED_RELATION);
  `pr-bundle --strict-resolved` + `--ci` global mode integration.
- **Schema bump (USER_VERSION 12 → 13).** Migration #51 adds the
  `loop_eq_with_dependent_write` column that backs the new algo
  nested-lookup dataflow predicate.

Full release notes in [CHANGELOG.md](CHANGELOG.md#132--2026-05-16).

## What's New in v12

### v12.1+ -- Boolean oracles, IDOR classifier, index portability + Django bridge
- **`roam oracle <name>`**: 5 boolean oracles for agents — 1-token yes/no answers (`symbol-exists`, `route-exists`, `is-test-only`, `is-reachable-from-entry`, `is-clone-of`). Direct counter to CKB v9.2's `symbolExists` pattern. MCP tools: `roam_oracle_*`.
- **`roam_taint_classify` (MCP only)**: LLM-augmented taint classification — runs `roam taint` then asks the agent's own model (via MCP sampling) to label each reachable finding as IDOR/AUTHZ/SQLI/XSS/etc. with confidence + reasoning. Counter to Semgrep Multimodal — same LLM-reasoning narrative without a hosted API key. Sequential for v12.1; concurrency-bounded gather lands in v12.2.
- **`roam index-export` / `roam index-import`**: portable, integrity-checked tarball format with manifest sha256 round-trip + optional cosign signing. Counter to Cursor's "92% similar codebase = reuse teammate's index" without a vendor cloud. Tamper-evident (manifest verifies index.db sha256 on import).
- **`roam eval-retrieve --emit-format coderag|beir`**: bench-portable JSONL emit for public leaderboard submission. CodeRAG-Bench-compatible `ctxs` array + BEIR-style trec_eval run files.
- **Django bridge**: full implicit-relationship resolution (admin→model, serializer→model, FK transitive, signal handlers, URL configs, Celery tasks, DRF routers). Ported from `upstream fork/roam-code` — credit upstream fork author. New schema columns: `framework_type`, `field_type`, `field_metadata`. Post-resolver runs after graph metrics.
- **`worktree_git_env()`** (`git_utils.py`): `GIT_INDEX_FILE` override fixes `.git/index.lock` contention when parallel agents run roam in sibling worktrees. Wired into `discovery.py`, `git_stats.py`, `changed_files.py`. Ported from `upstream fork/roam-code-sf` — credit upstream fork author.

### v12.0 (released 2026-05-01) -- Retrieval substrate + patch verifier
- **`roam retrieve "<task>"`**: graph-aware context server. Hybrid first stage (FTS5) + structural reranker (personalised PageRank + clone-canonical signal + lexical baseline) + token-budget cap. Returns ranked spans with justification tags (`pagerank=...`, `clone_cluster=...`, `fts=...`) so callers can see *why* each span ranked. MCP tool: `roam_retrieve(task, budget, k, rerank, seed_files)`.
- **`roam critique`**: graph-grounded patch verifier. Pipe `git diff | roam critique` to get findings ranked by severity. The killer signal is **clones-not-edited**: for every changed symbol with persisted clone siblings outside the diff, we flag the sibling as a likely missed change. Plus a blast-radius caller-count finding. Exits 5 on high severity (CI-gateable). MCP tool: `roam_critique(diff_text)`.
- **`roam clones --persist`**: populate the `clone_pairs` and `clone_clusters` tables so downstream consumers (critique, retrieve) can query clones in O(1) instead of re-running detection.
- **`personalized_pagerank()`** in `graph/pagerank.py`: NetworkX `personalization=` wrapper with empty-seed fallback to global PR; biases ranking toward query-relevant nodes for the retrieve reranker.
- **`.roam/config.toml`** (new): zero-dep TOML loader (stdlib `tomllib` → `tomli` → in-tree subset parser). Tunable retrieve weights (`alpha`/`beta`/`gamma`/`delta`/`epsilon`), `tokens_per_line`, `lexical_baseline`, `first_stage_token_cap`, `default_budget`, `default_k`, `default_rerank`.
- **DX corrections from dogfood pass**: `roam --detail <cmd>` is the canonical group-level flag; misleading "use --detail" hints in 7 commands rewritten to point users at `roam --detail <cmd>`. `--top N` aliased on `complexity`/`algo`/`rules` (`--top 0` means unlimited on `rules`). `roam fingerprint` no longer refuses graphs ≥5,000 symbols (new soft-warn threshold 20k, hard cap 100k).
- **211 CLI commands, 145 MCP tools** (`fleet`, `ask`, `workflow`, `cga`, `eval-retrieve` remain CLI-only; v12 exposes `roam_retrieve`, `roam_critique`, `roam_fleet_plan`, plus 5 v12.1 boolean oracles (`roam_oracle_*`), `roam_taint_classify`, `roam_pytest_fixtures`, and `roam_hover` as MCP tools). 57-tool `core` preset is the default for token-budget-conscious clients.

## What's New in v11

### v11.2 -- AST Clone Detection + Debug Artifact Rules
- **`roam clones`**: New AST structural clone detection via subtree hashing. Finds Type-2 clones (identical control flow, different identifiers/literals) with Jaccard similarity scoring, Union-Find clustering, and automated refactoring suggestions. More precise than the metric-based `duplicates` command.
- **9 debug artifact rules** (COR-560 through COR-568): Detect leftover `print()`, `breakpoint()`, `pdb.set_trace()`, `console.log()`, `debugger`, and `System.out.println()` in Python, JavaScript, TypeScript, and Java code. All use `ast_match` type with test file exemptions.
- **140 commands, 102 MCP tools** (at v11.2.0 release).

### v11.1.2 -- SQL + Scala Tier 1, 27 Languages
- **SQL DDL promoted to Tier 1** with dedicated `SqlExtractor` -- tables, columns, views, functions, triggers, schemas, types (enums), sequences, ALTER TABLE ADD COLUMN. Foreign keys produce graph edges; views and triggers reference source tables. Database-schema projects now work with `roam health`, `roam layers`, `roam impact`, `roam coupling` and all graph commands.
- **Scala promoted to Tier 1** with dedicated `ScalaExtractor` -- classes, traits, objects, case classes, sealed hierarchies, val/var properties, type aliases, imports, and inheritance. Full `extends` + `with` trait mixin resolution.
- **28 languages** with 17 dedicated Tier 1 extractors.
- `server.json` for official MCP Registry submission.

### v11.1.1 -- Command Quality Audit
- **Full command audit**: all 152 commands reviewed for usefulness, duplicates, and test coverage. ~20 bugs fixed, 21 new test files (700+ tests), every command docstring updated with cross-references to related commands.
- **Kotlin promoted to Tier 1** via new YAML-based declarative extractor architecture. Classes, interfaces, enums, objects, functions, methods, properties, and inheritance fully extracted.
- **7 new commands**: `roam congestion`, `roam adrs`, `roam flag-dead`, `roam test-scaffold`, `roam sbom`, `roam triage`, `roam ci-setup`.
- **CI templates**: `roam ci-setup` generates pipelines for GitHub Actions, GitLab CI, Azure Pipelines, Jenkins, and Bitbucket.
- **Bug fixes**: `--undocumented` mode in `intent` (wrong DB table), `--changed` flag in `verify` (was permanently dead), lazy-load violation in `visualize` (~500ms penalty), exit code inconsistency in `rules`, VERDICT-first convention enforced across all commands.
- **Code quality**: 15 unused variables removed, dead code swept (4 orphaned cmd files, 2 dead helper functions), algo detector false-positive rate reduced (regex-in-loop: 7 to 1, list-prepend deque suppression), 6 regex patterns pre-compiled for loop performance.

### v11.0 -- MCP v2 for Agent-First Workflows
- In-process MCP execution removes per-call subprocess overhead.
- 4 compound operations (`roam_explore`, `roam_prepare_change`, `roam_review_change`, `roam_diagnose_issue`) reduce multi-step agent workflows to single calls.
- Preset-based tool surfacing (`core`, `review`, `refactor`, `debug`, `architecture`, `full`) keeps default tool choice tight for agents while retaining full depth on demand.
- MCP tools now expose structured schemas and richer annotations for safer planner behavior.
- MCP token overhead for default core context dropped from ~36K to <3K tokens (about 92% reduction).

### Performance and Retrieval
- Symbol search moved to SQLite FTS5/BM25: typical search moved from seconds to tens of milliseconds on the indexed cohort (mileage varies by repo size and query selectivity — see `bench/retrieve/` for the methodology).
- Incremental indexing shifted from O(N) full-edge rebuild behavior to O(changed) updates.
- DB/runtime optimizations (`mmap_size`, safer large-graph guards, batched writes) reduce first-run and reindex friction on larger repos.

### CI, Governance, and Delivery
- GitHub Action supports quality gates, SARIF upload, sticky PR comments, and cache-aware execution.
- CI hardening includes changed-only analysis mode, trend-aware gates, and SARIF pre-upload guardrails (size/result caps + truncation signaling).
- Agent governance expanded with verification and AI-quality tooling (`roam verify`, `roam vibe-check`, `roam ai-readiness`, `roam ai-ratio`) for teams managing agent-written code.

## Best for

- **Agent-assisted coding** -- structured answers that reduce token usage vs raw file exploration
- **Large codebases (100+ files)** -- graph queries beat linear search at scale
- **Architecture governance** -- health scores, CI quality gates, budget enforcement, fitness functions
- **Safe refactoring** -- blast radius, affected tests, pre-change safety checks, graph-level editing
- **Multi-agent orchestration** -- partition codebases for parallel agent work with conflict-aware planning
- **Security analysis** -- vulnerability reachability mapping, auth gaps, CVE path tracing
- **Algorithm optimization** -- detect O(n^2) loops, N+1 queries, and 21 other anti-patterns with suggested fixes
- **Backend quality** -- auth gaps, missing indexes, over-fetching models, non-idempotent migrations, orphan routes, API drift
- **Runtime analysis** -- overlay production trace data onto the static graph for hotspot detection
- **Multi-repo projects** -- cross-repo API edge detection between frontend and backend

### When NOT to use Roam

- **Real-time type checking** -- use an LSP (pyright, gopls, tsserver). Roam is static and offline.
- **Small scripts (<10 files)** -- just read the files directly.
- **Pure text search** -- ripgrep is faster for raw string matching.

## Why use Roam

**Speed.** One command replaces 5-10 tool calls (in typical workflows). Under 0.5s for any query.

**Dependency-aware.** Computes structure, not string matches. Knows `Flask` has 47 dependents and 31 affected tests. `grep` knows it appears 847 times.

**LLM-optimized output.** Plain ASCII, compact abbreviations (`fn`, `cls`, `meth`), `--json` envelopes. Designed for agent consumption, not human decoration.

**Evidence that never leaves your machine.** Local SQLite, no telemetry, no network calls. Evidence packets hash-verify offline — works in air-gapped environments.

**Algorithm-aware.** Built-in catalog of 23 anti-patterns. Detects suboptimal algorithms (quadratic loops, N+1 queries, unbounded recursion) and suggests fixes with Big-O improvements and confidence scores. Receiver-aware loop-invariant analysis minimizes false positives.

**CI-ready.** `--json` output, `--gate` quality gates, GitHub Action, SARIF 2.1.0.

|  | Without Roam | With Roam |
|--|-------------|-----------|
| Tool calls | 8 | **1** |
| Wall time | ~11s | **<0.5s** |
| Tokens consumed | ~15,000 | **~3,000** |

*Measured on a typical agent workflow in a 200-file Python project (Flask). See [benchmarks](#performance) for more.*

<details>
<summary><strong>Table of Contents</strong></summary>

**Getting Started:** [What is Roam?](#what-is-roam) · [What's New in v13](#whats-new-in-v13) · [Best for](#best-for) · [Why use Roam](#why-use-roam) · [Install](#install) · [Quick Start](#quick-start)

**Using Roam:** [Commands](#commands) · [Walkthrough](#walkthrough-investigating-a-codebase) · [AI Coding Tools](#integration-with-ai-coding-tools) · [MCP Server](#mcp-server)

**Operations:** [CI/CD Integration](#cicd-integration) · [SARIF Output](#sarif-output) · [For Teams](#for-teams)

**Reference:** [Language Support](#language-support) · [Performance](#performance) · [How It Works](#how-it-works) · [How Roam Compares](#how-roam-compares) · [FAQ](#faq)

**More:** [Limitations](#limitations) · [Troubleshooting](#troubleshooting) · [Update / Uninstall](#update--uninstall) · [Development](#development) · [Contributing](#contributing)

</details>

## Install

```bash
pip install roam-code

# Recommended: isolated environment
pipx install roam-code
# or
uv tool install roam-code

# From source
pip install git+https://github.com/Cranot/roam-code.git
```

Requires Python 3.10+. Works on Linux, macOS, and Windows.

> **Windows:** If `roam` is not found after installing with `uv`, run `uv tool update-shell` and restart your terminal.

### Docker (alpine-based)

```bash
docker build -t roam-code .
docker run --rm -v "$PWD:/workspace" roam-code index
docker run --rm -v "$PWD:/workspace" roam-code health
```

## Quick Start

```bash
cd your-project
roam init                  # indexes codebase, creates config + CI workflow
roam understand            # full codebase briefing
```

First index takes ~5s for 200 files, ~15s for 1,000 files. Subsequent runs are incremental and near-instant.

**Next steps:**

- **Set up your AI agent:** `roam describe --write` (auto-detects CLAUDE.md, AGENTS.md, .cursor/rules, etc. — see [integration instructions](#integration-with-ai-coding-tools))
- **Explore:** `roam health` → `roam weather` → `roam map`
- **Run the v2 stack on every PR:** `git diff | roam pr-analyze --explain` (gates AI-generated risk; pair with `roam pr-comment-render` for sticky GitHub comments — see [Roam Review](#roam-review-pr-bot-for-ai-generated-changes))
- **First-touch demo:** `roam dogfood` (audit + pr-analyze + audit-trail + governance checks in one envelope)
- **Add to CI:** `roam init` already generated a GitHub Action
- **Customer-facing artifacts:** see starter rule packs at [`templates/rules/`](templates/rules/), the agent change packet at [`templates/examples/agent-change-packet.md`](templates/examples/agent-change-packet.md), the audit-report template + redacted sample at [`templates/audit-report/`](templates/audit-report/), and the security/procurement packet at [`templates/legal/security-procurement-packet.md`](templates/legal/security-procurement-packet.md).

<details>
<summary><strong>Try it on Roam itself</strong></summary>

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .
roam init
roam understand
roam health
```

</details>

## Works With

<p align="center">
  <a href="#integration-with-ai-coding-tools">Claude Code</a> &bull;
  <a href="#integration-with-ai-coding-tools">Cursor</a> &bull;
  <a href="#integration-with-ai-coding-tools">Windsurf</a> &bull;
  <a href="#integration-with-ai-coding-tools">GitHub Copilot</a> &bull;
  <a href="#integration-with-ai-coding-tools">Aider</a> &bull;
  <a href="#integration-with-ai-coding-tools">Cline</a> &bull;
  <a href="#integration-with-ai-coding-tools">Gemini CLI</a> &bull;
  <a href="#integration-with-ai-coding-tools">OpenAI Codex CLI</a> &bull;
  <a href="#mcp-server">MCP</a> &bull;
  <a href="#cicd-integration">GitHub Actions</a> &bull;
  <a href="#cicd-integration">GitLab CI</a> &bull;
  <a href="#cicd-integration">Azure DevOps</a>
</p>

## Commands

<!-- BEGIN auto-count:readme-canonical-mention -->
**Lead with the 5 verbs.** The [5 core commands](#core-commands) cover ~80% of agent workflows: `understand`, `context`, `retrieve`, `preflight`, `critique`. The remaining ~236 commands are detail surface for specialised workflows (taint, fleet, cga, oracle, eval, …) — they're called by agents on demand, not memorised. This is intentional design; under the hood the canonical surface is **241 commands (234 canonical + 7 aliases) organised into 7 categories** (aliases for muscle memory: `algo` → `math`, `weather` → `churn`, `digest` / `snapshot` / `trend` → `trends`, `onboard` → `understand`, `refs` → `uses`), but you don't need to know that to start.
<!-- END auto-count:readme-canonical-mention -->

<details>
<!-- BEGIN auto-count:readme-cli-command-list-summary -->
<summary><strong>Full command reference — canonical command list (all 234)</strong></summary>
<!-- END auto-count:readme-cli-command-list-summary -->

### Getting Started

| Command | Description |
|---------|-------------|
| `roam index [--force] [--verbose]` | Build or rebuild the codebase index |
| `roam index-export <bundle.tar.gz> [--sign] [--key K] [--keyless]` | Export the indexed `.roam/index.db` as a signed, integrity-checked tarball. Counter to Cursor's "reuse teammate's index" without a vendor cloud. |
| `roam index-import <bundle.tar.gz> [--force] [--cosign-bundle B] [--cosign-key K]` | Import a portable index bundle. Verifies manifest sha256 + optional cosign signature; refuses to overwrite without `--force`. |
| `roam watch [--interval N] [--debounce N] [--webhook-port P] [--guardian]` | Long-running index daemon: poll/webhook-triggered refreshes plus optional continuous architecture-guardian snapshots and JSONL compliance artifacts |
| `roam init` | Guided onboarding: creates `.roam/fitness.yaml`, CI workflow, runs index, shows health |
| `roam hooks [--install] [--uninstall]` | Manage git hooks for automated roam index updates and health gates |
| `roam doctor` | Diagnose installation and environment: verify tree-sitter grammars, SQLite, git, and config health |
| `roam reset [--hard]` | Reset the roam index and cached data. `--hard` removes all `.roam/` artifacts |
| `roam clean [--all]` | Remove stale or orphaned index entries without a full rebuild |
| `roam understand` | Full codebase briefing: tech stack, architecture, key abstractions, health, conventions, complexity overview, entry points |
| `roam onboard` | Alias for `understand` |
| `roam tour [--write PATH]` | Auto-generated onboarding guide: top symbols, reading order, entry points, language breakdown. `--write` saves to Markdown |
| `roam describe [--write] [--force] [-o PATH] [--agent-prompt]` | Auto-generate project description for AI agents. `--write` auto-detects your agent's config file. `--agent-prompt` returns a compact (<500 token) system prompt |
| `roam agent-export [--format F] [--write]` | Generate agent-context bundle from project analysis (`AGENTS.md` + provider-specific overlays) |
| `roam minimap [--update] [-o FILE] [--init-notes]` | Compact annotated codebase snapshot for agent config injection: stack, annotated directory tree, key symbols by PageRank, high fan-in symbols to avoid touching, hotspots, conventions. Sentinel-based in-place updates |
| `roam config [--set-db-dir PATH] [--use-local-cache] [--semantic-status] [--semantic-backend MODE]` | Manage `.roam/config.json` (DB path, local cache storage, excludes, optional ONNX semantic settings, and activation diagnostics) |
| `roam map [-n N] [--full] [--budget N]` | Project skeleton: files, languages, entry points, top symbols by PageRank. `--budget` caps output to N tokens |
| `roam schema [--diff] [--version V]` | JSON envelope schema versioning: view, diff, and validate output schemas |
| `roam mcp [--list-tools] [--transport T]` | Start MCP server (stdio/SSE/streamable-http), inspect available tools, and expose roam to coding agents |
| `roam mcp-setup <platform>` | Generate MCP config snippets for AI platforms: claude-code, cursor, windsurf, vscode, gemini-cli, codex-cli |
| `roam ci-setup [--platform P] [--write] [--with-slsa-l3] [--with-oscal]` | Generate CI/CD pipeline config (GitHub Actions, GitLab CI, Azure Pipelines, Jenkins, Bitbucket) with SARIF + quality gates. `--with-slsa-l3` adds the SRC-L3 auto-trigger workflow (W471). `--with-oscal` materialises persistent OSCAL v1.2 artifacts under `.roam/oscal/` (control-mapping.json + stub-assessment-plan.json) so future `roam evidence-oscal --kind assessment-results` calls can pass `--import-ap-ref` instead of inlining the stub (W535) |
| `roam adrs [--status S] [--limit N]` | Discover Architecture Decision Records, link to affected code modules, show status and coverage |
| `roam plugins` | List discovered plugins (commands, detectors, language extractors) registered via `ROAM_PLUGIN_MODULES` or entry points |
| `roam index-stats` | Report .roam index size, row counts, and SQLite fragmentation; hints when VACUUM or `roam reset` is overdue |
| `roam test-pyramid` | Count tests by kind (unit/integration/e2e/smoke) using path + filename heuristics; flags inverted pyramids |
| `roam telemetry` | Surface the opt-in local telemetry ring buffer (slowest + recent calls); enable via `ROAM_TELEMETRY_LOCAL=1` |
| `roam orphan-imports` | List Python imports that don't resolve to any indexed module or installed package |
| `roam changelog [--suggest]` | List commits since the last tag, optionally as a Conventional-Commits-bucketed markdown CHANGELOG draft |
| `roam graph-export [--format graphml\|dot\|jsonl]` | Export the symbol or file dependency graph for external tooling (Gephi, Cytoscape, custom analyses) |
| `roam help-search <query>` | Fuzzy match across every command's name + help text (replaces grepping `--help-all` output) |
| `roam stats` | Aggregate metrics over the index: count by language, file role, kind, plus recent commit activity |
| `roam timeline <symbol>` | Chronological commits that touched the file owning the symbol — author, date, lines added/removed |
| `roam pr-prep [<range>]` | One-shot pre-PR fitness check that bundles diff + critique + pr-risk into one envelope |
| `roam pr-analyze [<range>] [--input F] [--rules F] [--gate]` | Agent-aware PR risk verdict: aggregates `pr-prep` with AI-likelihood scoring, `.roam/rules.yml` enforcement, and INTENTIONAL/SAFE/REVIEW/BLOCK mapping; CI gate via `--gate` (exit 5 on BLOCK); governance audit trail via `--audit-trail` |
| `roam pr-comment-render --input F` | Render a markdown PR comment from a `pr-analyze` JSON envelope; styles: `github`, `gitlab`, `plain` |
| `roam pr-replay [<sha>] [--audit-trail]` | Replay a PR's analysis at a specific commit (or HEAD); useful for reproducing audit decisions and validating cache stability |
| `roam metrics-push [--token T] [--anonymize] [--dry-run]` | Push metrics-only summary (no source-code bodies) from `roam audit` to a Roam Cloud endpoint; `--dry-run` prints the payload locally |
| `roam audit-trail-verify [--input F] [--gate]` | Walk the EU AI Act audit-trail JSONL and verify SHA-256 chain integrity; exit 5 on broken chain |
| `roam audit-trail-export [--format md\|json\|csv] [--since T] [--verdict V] [--aggregate]` | Export the audit trail for procurement / compliance review; `--aggregate` rolls up per actor / repo / verdict / month |
| `roam audit-trail-conformance-check [--retention-days N] [--gate]` | Score the audit trail against governance-evidence checks (chain integrity, timestamps, actors, reproducibility, retention) |
| `roam article-12-check [--output F] [--pdf F]` | Article 12 scope/readiness assessment for actual Annex III high-risk AI-system buyers; produces a 1-page Markdown / PDF report. |
| `roam capabilities [--emit yaml\|json\|text] [--category X] [--ai-safe-only]` | Emit the decorator-driven capability registry — every command's machine-readable shape (inputs, outputs, ai_safe flag, since-version). For Roam Review GitHub App + MCP filtering. |
| `roam skill-generate [--target claude\|cursor\|continue\|aider] [--output F]` | Generate an agent-runtime skill manifest from the capability registry. SKILL.md / .mdc / config snippets — derived from decorators, never hand-edited. |
| `roam compare <baseline.db> <target.db> [--top N] [--threshold N]` | Structural delta between two indices: symbols added/removed/moved + per-file complexity deltas + IMPROVED/SIDEWAYS/REGRESSED verdict. The "did this refactor actually work?" tool. |
| `roam migration-plan [--target spec.yml] [--move SYMBOL=path] [--max-risk low\|medium\|high]` | Generate an ordered migration plan from current state to a target architecture. Each step annotated with blast-radius (caller count) + risk score (low/medium/high) + cross-layer detection. |
| `roam permit [--staged] [--input F] [--symbol N]` | Structural-permission verdict facade for AI agents: ALLOW/REVIEW/BLOCK over critique + preflight + blast-radius. Exit codes 0/5/6 for Cursor rules / Claude Code hooks / pre-commit / CI gates. |
| `roam postmortem <commit-range> [--limit N] [--show N]` | Replay current detectors against past commits; reports findings that would have surfaced pre-merge. The "would Roam have caught my Q1 incident?" demo. |
| `roam rules-validate [PATH] [--against DIFF] [--strict] [--gate] [--explain]` | Lint a `.roam/rules.yml` for typos, schema mistakes, unknown patterns, duplicate IDs; optional dry-run against a sample diff |
| `roam dogfood [--no-audit] [--no-pr-analyze] [--no-audit-trail]` | One-shot v2 stack runner: audit + pr-analyze + audit-trail + governance checks — first-touch demo for any repo |
| `roam suppress <finding-id> --reason "…"` | Suppress a math / over-fetch / missing-index / auth-gaps false positive with audit-trail-friendly record (`.roam/suppressions.json`); `--list` / `--remove` complete the workflow |
| `roam why-fail <test>` | Find recently-changed symbols transitively reachable from a failing test |
| `roam why-slow <symbol>` | Surface runtime hotspots and slow callers for a specific symbol (uses runtime traces if present) |
| `roam recommend <symbol>` | Surface related symbols using call-graph + co-change + clone signals |
| `roam graph-stats` | Graph-level invariants: density, weak components, non-trivial cycles, top inbound symbols |
| `roam api [--scope <dir>]` | List the public API surface (exported public symbols + signatures) |
| `roam exit-codes` | List every roam exit code with its meaning |
| `roam version [--check]` | Show installed version; with `--check` also queries PyPI for newer releases |
| `roam audit [--brief]` | One-shot AI-governance audit envelope — chains health + debt + dead + risk + test-pyramid + api into a single envelope |
| `roam disambiguate <name>` | List every symbol matching the name with file/line/kind/signature/docstring snippet to pick the right one |
| `roam pre-commit [--install\|--print]` | Install or preview a roam-critique git pre-commit hook |
| `roam mcp-status` | MCP server health: preset, registered tools, backpressure limits, cache entries, watcher state |
| `roam test-impact [<range>]` | Tests transitively reachable from changed symbols (sharper scope than `affected-tests`) |
| `roam recipes` | List every `roam ask` recipe with intent + example queries (sugar over `ask --list`) |
| `roam surface [--filter F] [--category C]` | Print the canonical capability surface (commands, aliases, MCP tools, maturity) for inventory and JSON consumption |
| `roam explain-command <name>` | Show what a command does, what it depends on, and how stale-index sensitive it is |
| `roam db-check` | Integrity sweep over the local index. Reports orphans, broken edges, missing FTS, and other structural issues |

### Daily Workflow

| Command | Description |
|---------|-------------|
| `roam file <path> [--full] [--changed] [--deps-of PATH]` | File skeleton: all definitions with signatures, cognitive load index, health score |
| `roam symbol <name> [--full]` | Symbol definition + callers + callees + metrics. Supports `file:symbol` disambiguation |
| `roam context <symbol> [--task MODE] [--for-file PATH]` | AI-optimized context: definition + callers + callees + files-to-read with line ranges |
| `roam hover <symbol>` | One-line architectural summary: kind, location, blast-radius bucket, top caller, top callee. Bounded at ~200 tokens for IDE hover panels |
| `roam retrieve <task> [--budget N] [--k N] [--seed-files PATH]` | Graph-aware context for free-form tasks: FTS5 + structural rerank (PageRank + clones) + token budget |
| `roam critique [--input DIFF] [--intent TEXT] [--high-callers N]` | Verify a patch against the graph: clones-not-edited + blast radius + intent-vs-semantic-diff. Pipe `git diff` in. Exit 5 on high severity. |
| `roam fleet plan <goal> [--n-agents N] [--adapter raw\|composio\|copilot]` | Graph-aware planner: Louvain partition + co-change + PageRank anchors → `.roam-fleet.json` for Composio/Copilot CLI/raw. |
| `roam ask <query> [--list] [--explain] [--recipe NAME]` | One-phrase intent classifier over a 25-recipe registry with phase, review-lens, gate, and follow-up metadata — composes preflight/retrieve/critique/fleet/understand/diagnose/trace/trends/hotspots/debt/taint/dead/coupling/stale-refs to cover the most common workflows. |
| `roam workflow [RECIPE] [--list] [--query TEXT]` | Inspect a recipe DAG, review lenses, gates, rendered command arguments, and follow-up commands without running the workflow. |
| `roam taint [--rules-dir PATH] [--rule NAME] [--rules-pack PACK] [--ci]` | Graph-reach taint analysis with OpenVEX-correct VEX justifications. YAML rule packs (10 starter packs: sqli, xss, ssrf, path-traversal, command-injection, deserialization, open-redirect, urllib, socketio, fileupload). |
| `roam cga emit [--include-taint] [--sign --key]` | Code Graph Attestation — in-toto v1 statement with `roam-code.dev/CodeGraph/v1` predicate, Merkle root + edge bundle digest. `--include-taint` embeds OpenVEX-shaped reachability claims from `roam taint`. `--sign` signs with cosign (graceful skip if absent); `roam cga verify` round-trips both predicate digest and cosign signature. |
| `roam eval-retrieve [--tasks FILE] [--sweep] [--min-recall-at-20 N] [--emit-format coderag\|beir]` | Recall@K eval harness for `roam retrieve` — measures against a JSONL ground-truth file. CI-gateable. `--emit-format coderag` writes CodeRAG-Bench-compatible run files for public leaderboard submission. |
| `roam oracle <name> <subject>` | Boolean oracles for agents — 1-token yes/no answers. Subcommands: `symbol-exists`, `route-exists`, `is-test-only`, `is-reachable-from-entry`, `is-clone-of`. |
| `roam search <pattern> [--kind KIND]` | Find symbols by name pattern, PageRank-ranked |
| `roam grep <pattern> [-g glob] [-n N]` | Text search annotated with enclosing symbol context |
| `roam refs-text <string>...` | String audit with verdict (SAFE-TO-REMOVE / REVIEW / LOAD-BEARING). Groups refs by surface (code/test/docs/config/dead) and annotates reachability. |
| `roam delete-check [--source working\|staged\|pr\|head] [--ci]` | Gate a diff on surviving references — exits 5 on `BREAK-RISK` with `--ci`. The companion to `safe-delete` for unstructured deletion review. |
| `roam history-grep <pattern> [--polarity]` | Git pickaxe (`-S` / `-G`) with author / date and introduced-vs-removed annotation — for "when did this string appear?" investigations. |
| `roam deps <path> [--full]` | What a file imports and what imports it |
| `roam trace <source> <target> [-k N]` | Dependency paths with coupling strength and hub detection |
| `roam impact <symbol>` | Blast radius: what breaks if a symbol changes (Personalized PageRank weighted) |
| `roam diff [--staged] [--full] [REV_RANGE]` | Blast radius of uncommitted changes or a commit range |
| `roam pr-risk [REV_RANGE]` | PR risk score (0-100, multiplicative model) + structural spread + suggested reviewers |
| `roam pr-diff [--staged] [--range R] [--format markdown]` | Structural PR diff: metric deltas, edge analysis, symbol changes, footprint. Not text diff — graph delta |
| `roam evidence-diff <old> <new>` | Diff two `ChangeEvidence` packets: hash drift, schema drift, added/removed refs, missing/new findings, 8-question completeness regressions vs improvements |
| `roam evidence-doctor [PACKET]` | Read-only health diagnostic for a `ChangeEvidence` packet: schema validity, content-hash integrity, W259 completeness banner (STRONG / PARTIAL / INSUFFICIENT), suggested producer to lift the lowest-scoring question |
| `roam evidence-oscal` | Emit OSCAL v1.2 Control Mapping (or Assessment Results with --kind assessment-results). |
| `roam boundary [--changed-range R] [--json]` | Detect cross-layer boundary violations: wrong-direction imports, public-by-accident exports, and layer-boundary import cycles |
| `roam compatibility [--baseline B] [--json]` | Compare current API surface against a baseline JSON snapshot: classify each symbol as added / removed / breaking-change / non-breaking-change |
| `roam test-hermeticity [--json]` | Detect non-hermetic tests: tests that depend on wallclock, env vars, network, or filesystem state outside the test fixture root |
| `roam api-changes [REV_RANGE]` | API change classifier: breaking/non-breaking changes, severity, and affected contracts |
| `roam semantic-diff [REV_RANGE]` | Structural change summary: symbols added/removed/modified and changed call edges |
| `roam test-gaps [REV_RANGE]` | Changed-symbol test gap detection: what changed and what still lacks test coverage |
| `roam affected [REV_RANGE]` | Monorepo/package impact analysis: what components are affected by a change |
| `roam attest [REV_RANGE] [--format markdown] [--sign]` | Proof-carrying PR attestation: bundles blast radius, risk, breaking changes, fitness, budget, tests, effects into one verifiable artifact |
| `roam pr-bundle init\|set\|add\|emit\|validate` | Build a proof-carrying PR bundle (intent + context + affected symbols + risks + tests + non-goals). `--auto-collect` folds in envelopes from prior roam runs. CI-gateable via `validate` |
| `roam annotate <symbol> <note>` | Attach persistent notes to symbols (agentic memory across sessions) |
| `roam annotations [--file F] [--symbol S]` | View stored annotations |
| `roam diagnose <symbol> [--depth N]` | Root cause analysis: ranks suspects by z-score normalized risk |
| `roam preflight <symbol\|file>` | Compound pre-change check: blast radius + tests + complexity + coupling + fitness |
| `roam guard <symbol>` | Compact sub-agent preflight bundle: definition, 1-hop callers/callees, test files, breaking-risk score, and layer signals |
| `roam agent-plan --agents N` | Decompose partitions into dependency-ordered agent tasks with merge sequencing and handoffs |
| `roam agent-context --agent-id N [--agents M]` | Generate per-agent execution context: write scope, read-only dependencies, and interface contracts |
| `roam agent-score [--agent A] [--since N]` | Composite per-agent score (0-100) over the `.roam/runs/` ledger: completion rate + clean-signal rate + breadth, with low-confidence flag for <2 runs |
| `roam syntax-check [--changed] [PATHS...]` | Tree-sitter syntax integrity check for changed files and multi-agent judge workflows |
| `roam verify [--threshold N]` | Pre-commit AI-code consistency check across naming, imports, error handling, and duplication signals |
| `roam verify-imports [--file F]` | Import hallucination firewall: validate all imports against indexed symbol table, suggest corrections via FTS5 fuzzy matching |
| `roam triage list\|add\|stats\|check` | Security finding suppression workflow: manage `.roam-suppressions.yml` (SAFE/ACKNOWLEDGED/WONT-FIX status lifecycle) |
| `roam safe-delete <symbol>` | Safe deletion check: SAFE/REVIEW/UNSAFE verdict |
| `roam test-map <name>` | Map a symbol or file to its test coverage |
| `roam adversarial [--staged] [--range R]` | Adversarial architecture review: generates targeted challenges based on changes |
| `roam plan [--staged] [--range R] [--agents N]` | Agent work planner: decompose changes into sequenced, dependency-aware steps |
| `roam closure <symbol> [--rename] [--delete]` | Minimal-change synthesis: all files to touch for a safe rename/delete |
| `roam mutate move\|rename\|add-call\|extract` | Graph-level code editing: move symbols, rename across codebase, add calls, extract functions. Dry-run by default |
| `roam dogfood-aggregate [--all] [--status S] [--severity H\|M\|L] [--type T]` | Aggregate the dogfood eval corpus into a backlog/triage view — surface open findings, filter by status/severity/type |
| `roam memory add\|list\|relevant` | Repo-local agent memory at `.roam/memory.jsonl` — portable across agent vendors, travels with checkouts. `add` records, `list` filters by recency, `relevant` ranks against a query |
| `roam runs start\|log\|end\|list\|show\|verify` | Per-agent-run event ledger at `.roam/runs/<run_id>/` — `start` opens a run, `log` appends events, `end` closes it, `list`/`show` inspect, `verify` checks HMAC chain integrity. Substrate for replay / agent-score / audit-trail |
| `roam replay <run_id> [--execute --dry-run\|--no-dry-run]` | Re-narrate a past agent run from the ledger: numbered timeline + per-step verdicts. `--execute` re-runs the logged commands (refuses bare `--execute` to prevent accidental state mutation) |
| `roam constitution init\|check\|show\|apply\|where` | Manage the repo-local agent constitution at `.roam/constitution.yml` — the single declarative file an agent reads first. Points at laws/rules/memory/runs and enforces per-gate policy thresholds |
| `roam laws mine\|check\|list\|explain` | Self-installing constitution: mine repo invariants from index + tests + git history into `roam-laws.yml`, then `check` enforces them against a diff (exit 5 on violation) |
| `roam agents-md` | Generate AGENTS.md from indexed conventions, danger zones, constitution, and capability registry |
| `roam brief` | One-page agent briefing covering mode / next / highlights / pr-bundle / runs |
| `roam intent-check <command>` | Check if an intended command is allowed by the active mode |
| `roam lease claim\|release\|list\|show\|gc` | Multi-agent lease system: coordinate parallel agents on the same repo by reserving file/symbol scopes. `claim` opens, `release` drops, `gc` expires stale leases |
| `roam mode [MODE] [--check CMD] [--list]` | Show or switch active mode (read_only / safe_edit / migration / autonomous_pr) |
| `roam next` | Suggest the next roam command based on current repo state (index presence, staleness, working-tree dirtiness, recent envelope/memory). Bounded under 200ms |

### Codebase Health

| Command | Description |
|---------|-------------|
| `roam health [--no-framework] [--gate]` | Composite health score (0-100): weighted geometric mean of tangle ratio, god components, bottlenecks, layer violations. `--gate` runs quality gate checks from `.roam-gates.yml` (exit 5 on failure) |
| `roam smells [--file F] [--min-severity S]` | Code smell detection: 24 deterministic detectors (brain methods, god classes, feature envy, shotgun surgery, data clumps, type switches, cross-layer clones, parallel hierarchies, etc.) with per-file health scores |
| `roam dashboard` | Unified single-screen project status: health, hotspots, risks, ownership, and AI-rot indicators |
| `roam vibe-check [--threshold N]` | AI-rot auditor: 8-pattern taxonomy with composite risk score and prioritized findings |
| `roam llm-smells [--min-severity S] [--persist]` | LLM-API integration anti-patterns: 10 patterns (no-model-version-pinning, missing-max-tokens, prompt-injection surface, missing timeout/retries, no system message, LLM call in loop, etc.). Scans files that import openai/anthropic/langchain/litellm/google.generativeai/cohere/mistralai/together/groq/fireworks/llama_index/replicate. Distinct audience from `vibe-check` |
| `roam ai-readiness` | 0-100 score for how well this codebase supports AI coding agents |
| `roam ai-ratio [--since N]` | Statistical estimate of AI-generated code ratio using commit-behavior signals |
| `roam trends [--record] [--days N] [--metric M]` | Historical metrics snapshots with sparklines and trend deltas |
| `roam complexity [--bumpy-road] [--include-tooling]` | Per-function cognitive complexity (SonarSource-compatible, triangular nesting penalty) + Halstead metrics (volume, difficulty, effort, bugs) + cyclomatic density |
| `roam py-types [--detail] [--include-tests] [--ci --min-coverage N]` | Python type-annotation health: % of public functions with full annotations, ``Any`` usage, legacy ``typing.Optional/Dict/List`` (PEP 585/604 modernisation candidates), per-file worst offenders. CI-gateable via ``--ci --min-coverage N`` (exit 5 below threshold). Default-excludes test files |
| `roam py-modern [--detail]` | Modern-Python adoption signal: counts walrus operator (PEP 572), match statements (PEP 634), PEP 604 ``X \| None``, PEP 585 ``dict[…]``, PEP 695 type aliases, f-strings vs ``.format()``. Reports type-modernisation % and f-string adoption % to gauge migration progress |
| `roam pytest-fixtures [SYMBOL] [--max-depth N]` | Inventory pytest fixture chains. With no SYMBOL, prints the project-wide fixture count and the top fixtures by dependent count. With a fixture or test name, walks the implicit fixture-parameter dependency graph to show what each test transitively requires. Resolves through ``conftest.py`` chains |
| `roam algo [--task T] [--confidence C] [--profile P]` | Algorithm anti-pattern detection: 23-pattern catalog detects suboptimal algorithms (O(n^2) loops, N+1 queries, quadratic string building, branching recursion, loop-invariant calls) and suggests better approaches with Big-O improvements. Confidence calibration via caller-count + runtime traces, evidence paths, impact scoring, framework-aware N+1 packs, and language-aware fix templates. Alias: `roam math` |
| `roam n1 [--confidence C] [--verbose]` | Implicit N+1 I/O detection: finds ORM model computed properties (`$appends`/accessors) that trigger lazy-loaded DB queries in collection contexts. Cross-references with eager loading config. Supports Laravel, Django, Rails, SQLAlchemy, JPA |
| `roam over-fetch [--threshold N] [--confidence C]` | Detect models serializing too many fields: large `$fillable` without `$hidden`/`$visible`, direct controller returns bypassing API Resources, poor exposed-to-hidden ratio |
| `roam missing-index [--table T] [--confidence C]` | Find queries on non-indexed columns: cross-references `WHERE`/`ORDER BY` clauses, foreign keys, and paginated queries against migration-defined indexes |
| `roam weather [-n N]` | Hotspots ranked by geometric mean of churn x complexity (percentile-normalized) |
| `roam debt [--roi]` | Hotspot-weighted tech debt prioritization with SQALE remediation costs and optional refactoring ROI estimates |
| `roam fitness [--explain] [--baseline PATH] [--write-baseline]` | Architectural fitness functions from `.roam/fitness.yaml`, with baseline/delta mode for existing debt |
| `roam alerts` | Health degradation trend detection (Mann-Kendall + Sen's slope) |
| `roam forecast [--symbol S] [--horizon N] [--alert-only]` | Predict when metrics will exceed thresholds: Theil-Sen regression on snapshot history + churn-weighted per-symbol risk |
| `roam budget [--init] [--staged] [--range R]` | Architectural budget enforcement: per-PR delta limits on health, cycles, complexity. CI gate (exit 5 on violation) |
| `roam bisect [--metric M] [--range R]` | Architectural git bisect: find the commit that degraded a specific metric |
| `roam ingest-trace <file> [--otel\|--jaeger\|--zipkin\|--generic]` | Ingest runtime trace data (OpenTelemetry, Jaeger, Zipkin) for hotspot overlay |
| `roam hotspots [--runtime] [--discrepancy]` | Runtime hotspot analysis: find symbols missed by static analysis but critical at runtime |

<details>
<summary><strong>roam algo — algorithm anti-pattern catalog (23 patterns)</strong></summary>

`roam algo` scans every indexed function against a 23-pattern catalog, ranks findings by runtime-aware impact score, and shows the exact Big-O improvement available. Findings include semantic evidence paths, precision metadata, and language-aware tips/fixes (Python, JS, Go, Rust, Java, etc.):

```
$ roam algo
VERDICT: 8 algorithmic improvements found (3 high, 4 medium, 1 low)
Ordering: highest impact first
Profile: balanced (filtered 0 low-signal findings)

Nested loop lookup (2):
  fn   resolve_permissions          src/auth/rbac.py:112     [high, impact=86.4]
        Current: Nested iteration -- O(n*m)
        Better:  Hash-map join -- O(n+m)
        Tip: Build a dict/set from one collection, iterate the other

  fn   find_matching_rule           src/rules/engine.py:67   [high, impact=78.1]
        Current: Nested iteration -- O(n*m)
        Better:  Hash-map join -- O(n+m)
        Tip: Build a dict/set from one collection, iterate the other

String building (1):
  meth build_query                  src/db/query.py:88       [high, impact=74.0]
        Current: Loop concatenation -- O(n^2)
        Better:  Join / StringBuilder -- O(n)
        Tip: Collect parts in a list, join once at the end

Branching recursion without memoization (1):
  fn   compute_cost                 src/pricing/calc.py:34   [medium, impact=49.5]
        Current: Naive branching recursion -- O(2^n)
        Better:  Memoized / iterative DP -- O(n)
        Tip: Add @cache / @lru_cache, or convert to iterative with a table
```

**Full catalog — 23 patterns:**

| Pattern | Anti-pattern detected | Better approach | Improvement |
|---------|----------------------|-----------------|-------------|
| Nested loop lookup | `for x in a: for y in b: if x==y` | Hash-map join | O(n·m) → O(n+m) |
| Membership test | `if x in list` in a loop | Set lookup | O(n) → O(1) per check |
| Sorting | Bubble / selection sort | Built-in sort | O(n²) → O(n log n) |
| Search in sorted data | Linear scan on sorted sequence | Binary search | O(n) → O(log n) |
| String building | `s += chunk` in loop | `join()` / StringBuilder | O(n²) → O(n) |
| Deduplication | Nested loop dedup | `set()` / `dict.fromkeys` | O(n²) → O(n) |
| Max / min | Manual tracking loop | `max()` / `min()` | idiom |
| Accumulation | Manual accumulator | `sum()` / `reduce()` | idiom |
| Group by key | Manual key-existence check | `defaultdict` / `groupingBy` | idiom |
| Fibonacci | Naive recursion | Iterative / `@lru_cache` | O(2ⁿ) → O(n) |
| Exponentiation | Loop multiplication | `pow(b, e, mod)` | O(n) → O(log n) |
| GCD | Manual loop | `math.gcd()` | O(n) → O(log n) |
| Matrix multiply | Naive triple loop | NumPy / BLAS | same asymptotic, ~1000× faster via SIMD |
| Busy wait | `while True: sleep()` poll | Event / condition variable | O(k) → O(1) wake-up |
| Regex in loop | `re.match()` compiled per iteration | Pre-compiled pattern | O(n·(p+m)) → O(p + n·m) |
| N+1 query | Per-item DB / API call in loop | Batch `WHERE IN (...)` | n round-trips → 1 |
| List front operations | `list.insert(0, x)` in loop | `collections.deque` | O(n) → O(1) per op |
| Sort to select | `sorted(x)[0]` or `sorted(x)[:k]` | `min()` / `heapq.nsmallest` | O(n log n) → O(n) or O(n log k) |
| Repeated lookup | `.index()` / `.contains()` inside loop | Pre-built set / dict | O(m) → O(1) per lookup |
| Branching recursion | Naive `f(n-1) + f(n-2)` without cache | `@cache` / iterative DP | O(2ⁿ) → O(n) |
| Quadratic string building | `result += chunk` across multiple scopes | `parts.append` + `join` at end | O(n²) → O(n) |
| Loop-invariant call | `get_config()` / `compile_schema()` inside loop body | Hoist before loop | per-iter cost → O(1) |
| String reversal | Manual char-by-char loop | `s[::-1]` / `.reverse()` | idiom |

**Filtering:**

```bash
roam algo --task nested-lookup       # one pattern type only
roam algo --confidence high          # high-confidence findings only
roam algo --profile strict           # precision-first filtering
roam algo --task io-in-loop -n 5    # top 5 N+1 query sites
roam --json algo                     # machine-readable output
roam --sarif algo > roam-algo.sarif  # SARIF with fingerprints + fixes
```

**Confidence calibration:** `high` = strong structural signal (unbounded loop + high caller/runtime impact + pattern confirmed); `medium` = pattern matched but uncertainty remains; `low` = heuristic signal only.

**Profiles:** `balanced` (default), `strict` (precision-first), `aggressive` (surface more candidates).

</details>

<details>
<summary><strong>roam minimap — annotated codebase snapshot for agent configs</strong></summary>

`roam minimap` generates a compact block (stack, annotated directory tree, key symbols, hotspots, conventions) wrapped in sentinel comments for in-place agent config updates:

```
$ roam minimap
<!-- roam:minimap generated=2026-02-25 -->
**Stack:** Python · JavaScript · YAML

```
.github/  (CI + Action)
benchmarks/  (agent-eval + oss-eval)
src/
  roam/
    bridges/
      base.py                 # LanguageBridge
      registry.py             # register_bridge, detect_bridges
    commands/  (137 cmd files) # is_test_file, get_changed_files
    db/
      connection.py           # find_project_root, batched_in
      schema.py
    graph/
      builder.py              # build_symbol_graph, build_file_graph
      pagerank.py             # compute_pagerank, compute_centrality
    languages/  (21 files) # ApexExtractor
    output/
      formatter.py            # to_json, json_envelope
    cli.py                    # cli, LazyGroup
    mcp_server.py
tests/  (267 files)
` ` `

**Key symbols** (PageRank): `open_db` · `ensure_index` · `json_envelope` · `to_json` · `LanguageExtractor`

**Touch carefully** (fan-in >= 15): `to_json` (116 callers) · `json_envelope` (116 callers) · `open_db` (105 callers) · `ensure_index` (100 callers)

**Hotspots** (churn x complexity): `cmd_context.py` · `csharp_lang.py` · `cmd_dead.py`

**Conventions:** snake_case fns, PascalCase classes
<!-- /roam:minimap -->
```

**Workflow:**

```bash
roam minimap                    # print to stdout
roam minimap --update           # replace sentinel block in CLAUDE.md in-place
roam minimap -o docs/AGENTS.md  # target a different file
roam minimap --init-notes       # scaffold .roam/minimap-notes.md for project gotchas
```

The sentinel pair `<!-- roam:minimap -->` / `<!-- /roam:minimap -->` is replaced on each run — surrounding content is left intact. Add project-specific gotchas to `.roam/minimap-notes.md` and they appear in every subsequent output.

**Tree annotations** come from the top exported symbols by fan-in per file. Non-source root directories (`.github/`, `benchmarks/`, `docs/`) are collapsed immediately. Large subdirectories (e.g. `commands/`, `languages/`) are collapsed at depth 2+ with a file count.

</details>

### Architecture

| Command | Description |
|---------|-------------|
| `roam clusters [--min-size N]` | Community detection vs directory structure. Modularity Q-score (Newman 2004) + per-cluster conductance |
| `roam spectral [--depth N] [--compare] [--gap-only] [--k K]` | Spectral bisection: Fiedler vector partition tree with algebraic connectivity gap verdict |
| `roam layers` | Topological dependency layers + upward violations + Gini balance |
| `roam dead [--all] [--summary] [--clusters]` | Unreferenced exported symbols with safety verdicts + confidence scoring (60-95%) |
| `roam flag-dead [--config FILE] [--include-tests]` | Feature flag dead code detection: stale LaunchDarkly/Unleash/Split/custom flags with staleness analysis |
| `roam fan [symbol\|file] [-n N] [--no-framework]` | Fan-in/fan-out: most connected symbols or files |
| `roam risk [-n N] [--domain KW] [--explain]` | Domain-weighted risk ranking |
| `roam why <name> [name2 ...]` | Role classification (Hub/Bridge/Core/Leaf), reach, criticality |
| `roam split <file>` | Internal symbol groups with isolation % and extraction suggestions |
| `roam entry-points` | Entry point catalog with protocol classification |
| `roam patterns` | Architectural pattern recognition: Strategy, Factory, Observer, etc. |
| `roam visualize [--format mermaid\|dot] [--focus NAME] [--limit N]` | Generate Mermaid or DOT architecture diagrams. Smart filtering via PageRank, cluster grouping, cycle highlighting |
| `roam effects [TARGET] [--file F] [--type T]` | Side-effect classification: DB writes, network I/O, filesystem, global mutation. Direct + transitive effects through call graph |
| `roam side-effects [SYMBOL] [--kind K] [--top N]` | Classify symbol side-effects (io_read / io_write / mutation / process / none) — coarse, agent-friendly verdict that composes with `roam idempotency` |
| `roam idempotency [SYMBOL] [--kind K] [--top N]` | Classify symbol idempotency (idempotent / non_idempotent / unknown) — is this symbol safe to call twice? Builds on `roam side-effects` |
| `roam tx-boundaries [SYMBOL] [--classification C] [--top N]` | Classify functions by transactional safety (transactional / partial_transactional / unsafe_mutation / unmatched_begin / unmatched_commit / non_transactional / unknown). Composes with `roam idempotency` for retry-safety reasoning |
| `roam causal-graph [SYMBOL] [--kind K] [--top N]` | Build per-symbol causal graphs: trace input-to-sink data dependencies (param/global/env flowing into side-effect / return / raise / mutation). Heuristic — false negatives expected |
| `roam dark-matter [--min-cochanges N]` | Detect hidden co-change couplings not explained by import/call edges |
| `roam simulate move\|extract\|merge\|delete` | Counterfactual architecture simulator: test refactoring ideas in-memory, see metric deltas before writing code |
| `roam orchestrate --agents N [--files P]` | Multi-agent swarm partitioning: split codebase for parallel agents with conflict-aware planning |
| `roam partition [--agents N]` | Multi-agent partition manifest: conflict risk, complexity, and suggested ownership splits |
| `roam fingerprint [--compact] [--compare F]` | Topology fingerprint: extract/compare architectural signatures across repos |
| `roam graph-diff [--base L] [--head L] [--save-snapshot N]` | Structural diff between two graph snapshots: added/removed symbols, edge churn, new cycles, layer migrations, likely-move rename heuristics. Persists snapshots under `.roam/snapshots/` |
| `roam architecture-drift [--window 30d]` | Time-series structural-drift detection over `.roam/snapshots/`: classifies trend as improving / degrading / stable based on cycle counts, edge churn, and cohesion proxy |
| `roam cut <target> [--depth N]` | Minimum graph cuts: find critical edges whose removal disconnects components |
| `roam safe-zones` | Graph-based containment boundaries |
| `roam coverage-gaps` | Unprotected entry points with no path to gate symbols |
| `roam duplicates [--threshold T] [--min-lines N]` | Semantic duplicate detector: functionally equivalent code clusters with divergent edge-case handling |
| `roam clones [--threshold T] [--min-lines N] [--scope P]` | AST structural clone detection: Type-2 clones via subtree hashing (more precise than `duplicates`) |

### Exploration

| Command | Description |
|---------|-------------|
| `roam module <path>` | Directory contents: exports, signatures, dependencies, cohesion |
| `roam sketch <dir> [--full]` | Compact structural skeleton of a directory |
| `roam uses <name>` | All consumers: callers, importers, inheritors. Use this *instead of* `grep "->X\|\.X\\b\|'X'\|\"X\""` to find references — graph-precise, no string-literal / comment false positives, structured by edge type. Available as `roam refs <name>` for grep-familiar muscle memory. |
| `roam owner <path>` | Code ownership: who owns a file or directory |
| `roam coupling [-n N] [--set]` | Temporal coupling: file pairs that change together (NPMI + lift) |
| `roam fn-coupling` | Function-level temporal coupling across files |
| `roam bus-factor [--brain-methods]` | Knowledge loss risk per module |
| `roam doc-staleness` | Detect stale docstrings |
| `roam docs-coverage` | Public-symbol doc coverage + stale docs + PageRank-ranked missing-doc hotlist |
| `roam stale-refs [--gate] [--diff REF] [--fix preview\|apply]` | Find dangling file references AND markdown anchor mismatches — confidence-tagged rename hints from git history / basename / symbol graph; HIGH-confidence auto-fix; branch-diff filter for CI; SARIF export. Index-free. |
| `roam lsp` | Minimal LSP server (JSON-RPC over stdio). Wire into VS Code / Neovim / JetBrains as a custom server to get squiggly underlines on dangling links and missing anchors as you type. |
| `roam suggest-refactoring [--limit N] [--min-score N]` | Proactive refactoring recommendations ranked by complexity, coupling, churn, smells, coverage gaps, and debt |
| `roam plan-refactor <symbol> [--operation auto\|extract\|move]` | Ordered refactor plan with blast radius, test gaps, layer risk, and simulation-based strategy preview |
| `roam test-scaffold <name\|file> [--write] [--framework F]` | Generate test file/function/import skeletons from symbol data (pytest, jest, Go, JUnit, RSpec) |
| `roam conventions` | Auto-detect naming styles, import preferences. Flags outliers |
| `roam breaking [REV_RANGE]` | Breaking change detection: removed exports, signature changes |
| `roam affected-tests <symbol\|file>` | Trace reverse call graph to test files |
| `roam relate <sym1> <sym2>` | Show relationship between two symbols: shared callers, shortest path, common ancestors |
| `roam endpoints [--routes] [--api]` | Enumerate all HTTP/API endpoint definitions and surface them for review or cross-repo matching |
| `roam metrics <file\|symbol>` | Unified vital signs: complexity, fan-in/out, PageRank, churn, test coverage, dead code risk -- all in one call |
| `roam findings list\|show\|count [--detector D]` | Query the central findings registry (the cross-detector denormalised view). 16+ detectors emit here (clones, dead, complexity, smells, n1, missing-index, over-fetch, bus-factor, auth-gaps, vulns, invariants, hotspots, taint, vibe-check, orphan-imports, conventions, pr-risk, duplicates, audit-trail-conformance, audit-trail-verify). Substrate for suppression and SARIF projection |
| `roam search-semantic <query>` | Hybrid semantic search: BM25 + TF-IDF + optional local ONNX vectors (select via `--backend`) with framework/library packs |
| `roam intent [--staged] [--range R]` | Doc-to-code linking: match documentation to symbols, detect drift |
| `roam x-lang [--bridges] [--edges]` | Cross-language edge browser: inspect bridge-resolved connections |
| `roam batch-search <pattern1> <pattern2> ... [--limit-per-query N] [--include-paths]` | Run up to 10 symbol-name pattern searches in one DB connection. Replaces 10 sequential `roam search` calls; results grouped by query |
| `roam complete <prefix> [--kind symbol\|path\|command\|all] [--limit N]` | Left-anchored prefix completions (FTS5-backed). Use `roam search` for substring matches and `roam search-semantic` for natural-language queries |

### Reports & CI

| Command | Description |
|---------|-------------|
| `roam report [--list] [--config FILE] [PRESET]` | Compound presets: `first-contact`, `security`, `pre-pr`, `refactor`, `guardian` |
| `roam describe --write` | Generate agent config (auto-detects: CLAUDE.md, AGENTS.md, .cursor/rules, etc.) |
| `roam auth-gaps [--routes-only] [--controllers-only] [--min-confidence C]` | Find endpoints missing authentication or authorization: routes outside auth middleware groups, CRUD methods without `$this->authorize()` / `Gate::allows()` checks. String-aware PHP brace parsing |
| `roam orphan-routes [-n N] [--confidence C]` | Detect backend routes with no frontend consumer: parses route definitions, searches frontend for API call references, reports controller methods with no route mapping |
| `roam migration-safety [-n N] [--include-archive]` | Detect non-idempotent migrations: missing `hasTable`/`hasColumn` guards, raw SQL without `IF NOT EXISTS`, index operations without existence checks |
| `roam api-drift [--model M] [--confidence C]` | Detect mismatches between PHP model `$fillable`/`$appends` fields and TypeScript interface properties. Auto-converts snake_case/camelCase for comparison. Single-repo; cross-repo planned for `roam ws api-drift` |
| `roam codeowners [--unowned] [--owner NAME]` | CODEOWNERS coverage analysis: owned/unowned files, top owners, and ownership risk |
| `roam drift [--threshold N]` | Ownership drift detection: declared ownership vs observed maintenance activity |
| `roam suggest-reviewers [REV_RANGE]` | Reviewer recommendation via ownership, recency, breadth, and impact signals |
| `roam simulate-departure <developer>` | Knowledge-loss simulation: what breaks if a key contributor leaves |
| `roam dev-profile [--developer NAME] [--since N]` | Developer productivity profile: commit patterns, specialization, impact, and knowledge concentration per contributor |
| `roam secrets [--fail-on-found] [--include-tests]` | Secret scanning with masking, entropy detection, env-var suppression, remediation suggestions, and optional CI gate failure |
| `roam vulns [--import-file F] [--reachable-only]` | Vulnerability scanning: ingest npm/pip/trivy/osv reports, auto-detect format, reachability filtering, SARIF output |
| `roam path-coverage [--from P] [--to P] [--max-depth N]` | Find critical call paths (entry -> sink) with zero test protection. Suggests optimal test insertion points |
| `roam capsule [--redact-paths] [--no-signatures] [--output F]` | Export sanitized structural graph (no code bodies) for external architectural review |
| `roam rules [--init] [--ci] [--rules-dir D]` | Plugin DSL for governance: user-defined path/symbol/AST rules via `.roam/rules/` YAML (`$METAVAR` captures supported) |
| `roam check-rules [--severity S] [--fix]` | Evaluate built-in and user-defined governance rules (10 built-in: no-circular-imports, max-fan-out, etc.) |
| `roam vuln-map --generic\|--npm-audit\|--trivy F` | Ingest vulnerability reports and match to codebase symbols |
| `roam vuln-reach [--cve C] [--from E]` | Vulnerability reachability: exact paths from entry points to vulnerable calls |
| `roam supply-chain [--top N]` | Dependency risk dashboard: pin coverage, risk scoring, supply-chain health |
| `roam sbom [--format cyclonedx\|spdx] [--no-reachability] [-o FILE]` | SBOM generation (CycloneDX 1.5 / SPDX 2.3) enriched with call-graph reachability per dependency |
| `roam congestion [--window N] [--min-authors N]` | Developer congestion detection: concurrent authors per file, coordination risk scoring |
| `roam invariants [--staged] [--range R]` | Discover architectural contracts (invariants) from the codebase structure |

### Multi-Repo Workspace

| Command | Description |
|---------|-------------|
| `roam ws init <repo1> <repo2> [--name NAME]` | Initialize a workspace from sibling repos. Auto-detects frontend/backend roles |
| `roam ws status` | Show workspace repos, index ages, cross-repo edge count |
| `roam ws resolve` | Scan for REST API endpoints and match frontend calls to backend routes |
| `roam ws understand` | Unified workspace overview: per-repo stats + cross-repo connections |
| `roam ws health` | Workspace-wide health report with cross-repo coupling assessment |
| `roam ws context <symbol>` | Cross-repo augmented context: find a symbol across repos + show API callers |
| `roam ws trace <source> <target>` | Trace cross-repo paths via API edges |

### Global Options

| Option | Description |
|--------|-------------|
| `roam --json <command>` | Structured JSON output with consistent envelope |
| `roam --compact <command>` | Token-efficient output: TSV tables, minimal JSON envelope |
| `roam --sarif <command>` | SARIF 2.1.0 output for dead, health, complexity, rules, secrets, algo, py-types, py-modern (GitHub/CI integration) |
| `roam health --gate` | CI quality gate. Reads `.roam-gates.yml` thresholds. Exit code 5 on failure |

</details>

## Walkthrough: Investigating a Codebase

<details>
<summary><strong>10-step walkthrough using Flask as an example</strong> (click to expand)</summary>

Here's how you'd use Roam to understand a project you've never seen before. Using Flask as an example:

**Step 1: Onboard and get the full picture**

```
$ roam init
Created .roam/fitness.yaml (6 starter rules)
Created .github/workflows/roam.yml
Done. 226 files, 1132 symbols, 233 edges.
Health: 78/100

$ roam understand
Tech stack: Python (flask, jinja2, werkzeug)
Architecture: Monolithic — 3 layers, 5 clusters
Key abstractions: Flask, Blueprint, Request, Response
Health: 78/100 — 1 god component (Flask)
Entry points: src/flask/__init__.py, src/flask/cli.py
Conventions: snake_case functions, PascalCase classes, relative imports
Complexity: avg 4.2, 3 high (>15), 0 critical (>25)
```

**Step 2: Drill into a key file**

```
$ roam file src/flask/app.py
src/flask/app.py  (python, 963 lines)

  cls  Flask(App)                                   :76-963
    meth  __init__(self, import_name, ...)           :152
    meth  route(self, rule, **options)               :411
    meth  register_blueprint(self, blueprint, ...)   :580
    meth  make_response(self, rv)                    :742
    ...12 more methods
```

**Step 3: Who depends on this?**

```
$ roam deps src/flask/app.py
Imported by:
file                        symbols
--------------------------  -------
src/flask/__init__.py       3
src/flask/testing.py        2
tests/test_basic.py         1
...18 files total
```

**Step 4: Find the hotspots**

```
$ roam weather
=== Hotspots (churn x complexity) ===
Score  Churn  Complexity  Path                    Lang
-----  -----  ----------  ----------------------  ------
18420  460    40.0        src/flask/app.py        python
12180  348    35.0        src/flask/blueprints.py python
```

**Step 5: Check architecture health**

```
$ roam health
Health: 78/100
  Tangle: 0.0% (0/1132 symbols in cycles)
  1 god component (Flask, degree 47, actionable)
  0 bottlenecks, 0 layer violations

=== God Components (degree > 20) ===
Sev      Name   Kind  Degree  Cat  File
-------  -----  ----  ------  ---  ------------------
WARNING  Flask  cls   47      act  src/flask/app.py
```

**Step 6: Get AI-ready context for a symbol**

```
$ roam context Flask
Files to read:
  src/flask/app.py:76-963              # definition
  src/flask/__init__.py:1-15           # re-export
  src/flask/testing.py:22-45           # caller: FlaskClient.__init__
  tests/test_basic.py:12-30            # caller: test_app_factory
  ...12 more files

Callers: 47  Callees: 3
```

**Step 7: Pre-change safety check**

```
$ roam preflight Flask
=== Preflight: Flask ===
Blast radius: 47 callers, 89 transitive
Affected tests: 31 (DIRECT: 12, TRANSITIVE: 19)
Complexity: cc=40 (critical), nesting=6
Coupling: 3 hidden co-change partners
Fitness: 1 violation (max-complexity exceeded)
Verdict: HIGH RISK — consider splitting before modifying
```

**Step 8: Decompose a large file**

```
$ roam split src/flask/app.py
=== Split analysis: src/flask/app.py ===
  87 symbols, 42 internal edges, 95 external edges
  Cross-group coupling: 18%

  Group 1 (routing) — 12 symbols, isolation: 83% [extractable]
    meth  route              L411  PR=0.0088
    meth  add_url_rule       L450  PR=0.0045
    ...

=== Extraction Suggestions ===
  Extract 'routing' group: route, add_url_rule, endpoint (+9 more)
    83% isolated, only 3 edges to other groups
```

**Step 9: Understand why a symbol matters**

```
$ roam why Flask url_for Blueprint
Symbol     Role          Fan         Reach     Risk      Verdict
---------  ------------  ----------  --------  --------  --------------------------------------------------
Flask      Hub           fan-in:47   reach:89  CRITICAL  God symbol (47 in, 12 out). Consider splitting.
url_for    Core utility  fan-in:31   reach:45  HIGH      Widely used utility (31 callers). Stable interface.
Blueprint  Bridge        fan-in:18   reach:34  moderate  Coupling point between clusters.
```

**Step 10: Generate docs and set up CI**

```
$ roam describe --write
Wrote CLAUDE.md (98 lines)  # auto-detects: CLAUDE.md, AGENTS.md, .cursor/rules, etc.

$ roam health --gate
Health: 78/100 — PASS
```

Ten commands. Complete picture: structure, dependencies, hotspots, health, context, safety checks, decomposition, and CI gates.

</details>

## Integration with AI Coding Tools

Roam is designed to be called by coding agents via shell commands. Instead of repeatedly grepping and reading files, the agent runs one `roam` command and gets structured output.

**Decision order for agents:**

| Situation | Command |
|-----------|---------|
| First time in a repo | `roam understand` then `roam tour` |
| Need to modify a symbol | `roam preflight <name>` (blast radius + tests + fitness) |
| Debugging a failure | `roam diagnose <name>` (root cause ranking) |
| Need files to read | `roam context <name>` (files + line ranges) |
| Need to find a symbol | `roam search <pattern>` |
| Need file structure | `roam file <path>` |
| Pre-PR check | `roam pr-risk HEAD~3..HEAD` |
| What breaks if I change X? | `roam impact <symbol>` |
| Check for N+1 queries | `roam n1` (implicit lazy-load detection) |
| Check auth coverage | `roam auth-gaps` (routes + controllers) |
| Check migration safety | `roam migration-safety` (idempotency guards) |

**Fastest setup:**

```bash
roam describe --write               # auto-detects your agent's config file
roam describe --write -o AGENTS.md  # or specify an explicit path
roam describe --agent-prompt        # compact ~500-token prompt (append to any config)
roam minimap --update               # inject/refresh annotated codebase minimap in CLAUDE.md
```

**Agent not using Roam correctly?** If your agent is ignoring Roam and falling back to grep/read exploration, it likely doesn't have the instructions. Run:

```bash
roam describe --write          # writes instructions to your agent's config (CLAUDE.md, AGENTS.md, etc.)
```

If you already have a config file and don't want to overwrite it:

```bash
roam describe --agent-prompt   # prints a compact prompt — copy-paste into your existing config
roam minimap --update          # injects an annotated codebase snapshot into CLAUDE.md (won't touch other content)
```

This teaches the agent which Roam command to use for each situation (e.g., `roam preflight` before changes, `roam context` for files to read, `roam diagnose` for debugging).

<details>
<summary><strong>Copy-paste agent instructions</strong></summary>

```markdown
## Codebase navigation

This project uses `roam` for codebase comprehension. Always prefer roam over Glob/Grep/Read exploration.

Before modifying any code:
1. First time in the repo: `roam understand` then `roam tour`
2. Find a symbol: `roam search <pattern>`
3. Before changing a symbol: `roam preflight <name>` (blast radius + tests + fitness)
4. Need files to read: `roam context <name>` (files + line ranges, prioritized)
5. Debugging a failure: `roam diagnose <name>` (root cause ranking)
6. After making changes: `roam diff` (blast radius of uncommitted changes)

Additional: `roam health` (0-100 score), `roam impact <name>` (what breaks),
`roam pr-risk` (PR risk), `roam file <path>` (file skeleton).

Run `roam --help` for all commands. Use `roam --json <cmd>` for structured output.
```

</details>

<details>
<summary><strong>Where to put this for each tool</strong></summary>

| Tool | Config file |
|------|-------------|
| **Claude Code** | `CLAUDE.md` in your project root |
| **OpenAI Codex CLI** | `AGENTS.md` in your project root |
| **Gemini CLI** | `GEMINI.md` in your project root |
| **Cursor** | `.cursor/rules/roam.mdc` (add `alwaysApply: true` frontmatter) |
| **Windsurf** | `.windsurf/rules/roam.md` (add `trigger: always_on` frontmatter) |
| **GitHub Copilot** | `.github/copilot-instructions.md` |
| **Aider** | `CONVENTIONS.md` |
| **Continue.dev** | `config.yaml` rules |
| **Cline** | `.clinerules/` directory |

</details>

<details>
<summary><strong>Roam vs native tools</strong></summary>

| Task | Use Roam | Use native tools |
|------|----------|-----------------|
| "What calls this function?" | `roam symbol <name>` | LSP / Grep |
| "What files do I need to read?" | `roam context <name>` | Manual tracing (5+ calls) |
| "Is it safe to change X?" | `roam preflight <name>` | Multiple manual checks |
| "Show me this file's structure" | `roam file <path>` | Read the file directly |
| "Understand project architecture" | `roam understand` | Manual exploration |
| "What breaks if I change X?" | `roam impact <symbol>` | No direct equivalent |
| "What tests to run?" | `roam affected-tests <name>` | Grep for imports (misses indirect) |
| "What's causing this bug?" | `roam diagnose <name>` | Manual call-chain tracing |
| "Codebase health score for CI" | `roam health --gate` | No equivalent |

</details>

## MCP Server

Roam includes a [Model Context Protocol](https://modelcontextprotocol.io/) server for direct integration with tools that support MCP.

```bash
pip install "roam-code[mcp]"
roam mcp
```

224 tools, 10 resources, and 6 prompts are available in the full preset. Most tools are read-only index queries; side-effect tools are explicitly annotated.

See [Using Roam via MCP](https://roam-code.com/docs/mcp-usage) for the first-run flow, the cold-start envelope your agent will see on a fresh repo, and the canonical 7-step agent sequence.

**MCP v2 highlights (v11):**
- In-process MCP execution (no subprocess shell-out per call)
- Preset-based tool surfacing (`core`, `review`, `refactor`, `debug`, `architecture`, `full`)
- Compound tools that collapse multi-step exploration/review flows into one call
- Structured output schemas + tool annotations for safer planner behavior

**MCP-native enhancements (v12):**
- **Sampling-driven compression** -- pass `summarize=True` to `roam_explore`, `roam_understand`, `roam_health`, or `roam_repo_map`. The server asks the client's own LLM (no API keys) to compress the full envelope into a short briefing, dropping output from ~50 KB JSON to ~1-2 KB prose. Falls back gracefully when the client doesn't support sampling.
- **Server-side session memory** -- `roam_context`, `roam_explore`, and `roam_retrieve` now remember symbols you've touched in the current session and auto-bias ranking without you threading `recent_symbols` through every call. Explicit args still win.
- **Phase-aware progress** -- `roam_init`, `roam_reindex`, and `roam_orchestrate` stream real `discover -> parse -> extract -> resolve -> graph -> metrics` progress to the client, replacing the old 5/100 placeholders.
- **Symbol & path completions** -- new `roam_complete(prefix, kind, limit)` tool returns just names from the FTS5 index (cheaper than `roam_search_symbol`). A protocol-level handler is also installed for clients that support `completion/complete`.
- **Reactive resource invalidation** (opt-in) -- set `ROAM_MCP_WATCH=1` and the server watches the working tree, runs incremental reindex on file changes, and emits `notifications/resources/updated` for `roam://health`, `roam://summary`, etc., so subscribed clients see fresh data without polling.

<!-- BEGIN auto-count:readme-default-preset -->
**Default preset:** `core` (58 tools: 57 core + `roam_expand_toolset` meta-tool).
<!-- END auto-count:readme-default-preset -->

```bash
# Default
roam mcp

# Full toolset
ROAM_MCP_PRESET=full roam mcp

# Legacy compatibility (same as full preset)
ROAM_MCP_LITE=0 roam mcp
```

<!-- BEGIN auto-count:readme-mcp-core-preset-tools -->
Core preset tools: `roam_affected_tests`, `roam_alerts`, `roam_ask`, `roam_audit_trail_conformance_check`, `roam_audit_trail_export`, `roam_audit_trail_verify`, `roam_batch_get`, `roam_batch_search`, `roam_catalog`, `roam_complete`, `roam_complexity_report`, `roam_context`, `roam_critique`, `roam_dead_code`, `roam_deps`, `roam_diagnose`, `roam_diagnose_issue`, `roam_diff`, `roam_disambiguate`, `roam_dogfood`, `roam_explore`, `roam_fetch_handle`, `roam_file_info`, `roam_fleet_plan`, `roam_for_bug_fix`, `roam_for_new_feature`, `roam_for_refactor`, `roam_for_security_review`, `roam_health`, `roam_impact`, `roam_metrics_push`, `roam_oracle_is_clone_of`, `roam_oracle_is_reachable_from_entry`, `roam_oracle_is_test_only`, `roam_oracle_route_exists`, `roam_oracle_symbol_exists`, `roam_pr_analyze`, `roam_pr_comment_render`, `roam_pr_risk`, `roam_preflight`, `roam_prepare_change`, `roam_py_modern`, `roam_py_types`, `roam_retrieve`, `roam_review_change`, `roam_rules_validate`, `roam_search_symbol`, `roam_session_metrics`, `roam_syntax_check`, `roam_taint_classify`, `roam_test_impact`, `roam_timeline`, `roam_trace`, `roam_understand`, `roam_uses`, `roam_validate_plan`, `roam_why_fail`.
<!-- END auto-count:readme-mcp-core-preset-tools -->

<details>
<!-- BEGIN auto-count:readme-mcp-tool-list-summary -->
<summary><strong>MCP tool list (all 224)</strong></summary>
<!-- END auto-count:readme-mcp-tool-list-summary -->

*New in v12.26: `roam_pr_analyze`, `roam_pr_comment_render`, `roam_metrics_push`, `roam_audit_trail_verify`, `roam_audit_trail_export`, `roam_audit_trail_conformance_check`, `roam_rules_validate`, `roam_dogfood` — Roam Review + Cloud engines + governance audit-trail toolkit + production-grade rules linting + one-shot v2 stack runner.*

<!-- BEGIN auto-count:readme-mcp-tool-list-table -->
| Tool | Description |
|------|-------------|
| `roam_adrs` | Discover Architecture Decision Records (ADRs) and link them to code modules. Scans well-known ADR directories (``docs/adr/`` / ``architecture/decisions/`` / ...) for markdown files matching ADR naming patterns, parses each ADR's title / status / date / file refs, then cross-references mentioned files against the symbol index. Different from ``roam_doc_staleness`` (inline docstring drift) -- this is the prose-decision-document discoverer. |
| `roam_adversarial` | Frame architectural issues in changed files as challenges the developer must defend: CRITICAL (new cyclic dependencies), HIGH (layer violations, high-confidence anti-patterns), WARNING (cross-cluster coupling, high fan-out), INFO (orphaned symbols). Composes cycles + clusters + layers + catalog + dead + complexity. Different from ``roam_diff`` (blast-radius facts) -- this is the architecture-review framing for code-review agents. |
| `roam_adversarial_review` | Adversarial architecture review: challenges about cycles, anti-patterns, coupling. |
| `roam_affected` | Monorepo impact analysis: find all affected packages/modules from changes. |
| `roam_affected_tests` | Test files that exercise changed code, with hop distance. |
| `roam_agent_context` | Extract a single agent's partition from the full agent plan: write scope, read-only dependencies, interface contracts, coordination instructions, and key symbols. Different from ``roam_agent_plan`` (full multi-agent view) and ``roam_orchestrate`` (operational dispatch with merge order) -- this is the focused per-worker packet for one agent. |
| `roam_agent_export` | Generate AI agent context file (CLAUDE.md/AGENTS.md/.cursorrules) from index. |
| `roam_agent_plan` | Decompose partitions into dependency-ordered multi-agent tasks: per-task write scope, read-only dependencies, interface contracts, phase schedule, and merge sequencing. Supports ``plain`` / ``json`` / ``claude-teams`` output formats. Different from ``roam_partition`` (raw analytical manifest) and ``roam_orchestrate`` (operational dispatch) -- this is the dependency-ordered phase schedule. |
| `roam_agent_score` | Aggregate runs from the local ledger and score each agent on a 0..100 composite (run completion, gate adherence, preflight compliance, blast accuracy, replay survival). Empty state (no runs / no matching runs) returns a clean envelope with ``state: "no_data"`` -- never empty stdout, never a crash. Different from ``roam_runs_verify`` (HMAC tamper-detection) -- this is the per-agent quality score across runs. |
| `roam_ai_ratio` | Estimate AI-generated code percentage from git commit heuristics. |
| `roam_ai_readiness` | AI readiness score (0-100): how effectively AI agents can work on this codebase. |
| `roam_alerts` | Active health alerts: thresholds breached on tangle, complexity, churn, or coverage. |
| `roam_algo` | Detect suboptimal algorithms with better alternatives and complexity analysis. |
| `roam_annotate_symbol` | Add persistent annotation to a symbol/file for future agent sessions. |
| `roam_api` | List the public API surface — exported public symbols with signatures and docs. |
| `roam_api_changes` | Detect breaking and non-breaking API changes vs a git ref. |
| `roam_api_drift` | Mismatches between backend models and frontend interfaces. |
| `roam_architecture_drift` | Compute per-week growth rates for symbols / edges / cycles across a sliding window of persisted ``.roam/snapshots/`` and classify overall direction as ``improving`` / ``degrading`` / ``stable``. Different from ``roam_graph_diff`` (point-in-time delta between two commits) and ``roam_trends`` (metric-level time series) -- this is the snapshot-based architectural-trajectory report. |
| `roam_article_12_check` | Run a 6-item EU AI Act Article 12 readiness checklist over the indexed repo: audit-trail directory, audit-trail records, retention policy doc, technical docs, attestation surface, high-risk classification heuristic. Emits a structured envelope mapping each item to its Article (12, 18, 19) or Annex (III). Different from ``roam_audit_trail_conformance_check`` (per-record chain integrity) -- this is the repo-level governance-readiness assessment. Per the agentic-assurance guardrails: 'maps to' / 'supports evidence for', never 'certifies' / 'makes compliant'. |
| `roam_ask` | Free-form intent dispatcher: maps a natural-language question ("is it safe to delete X", "where does login validate", "what just broke") to one of 24 pre-built recipes that compose preflight / retrieve / critique / fleet / diagnose / trace / trends / hotspots / debt / taint commands. Call this BEFORE falling back to Grep+Read — the recipe registry covers most common workflows in one tool call. |
| `roam_attest` | Proof-carrying PR attestation: evidence bundle + merge verdict. |
| `roam_audit` | Run a one-shot codebase architecture audit: bundles health, debt, dead-code, risk, test-pyramid, coverage, and API-surface signals into a single envelope. Designed as the structured artifact a written audit report attaches. Different from ``roam_health`` (single 0-100 score) and ``roam_report`` (preset-driven Markdown report) -- this is the verdict-first audit packet for governance and onboarding. |
| `roam_audit_trail_conformance_check` | Score the audit trail against an EU AI Act Article 12 checklist. |
| `roam_audit_trail_export` | Export the audit trail as markdown / json / csv for procurement review. |
| `roam_audit_trail_verify` | Verify SHA-256 chain integrity of a roam audit trail. |
| `roam_auth_gaps` | Endpoints missing authentication or authorization checks. |
| `roam_batch_get` | Get details for up to 50 symbols in one call. Replaces 50 sequential roam_symbol calls. |
| `roam_batch_search` | Search up to 10 patterns in one call. Replaces 10 sequential roam_search_symbol calls. |
| `roam_bisect_blame` | Find snapshots that caused architectural degradation, ranked by impact. |
| `roam_breaking_changes` | Detect breaking API changes between git refs: removed exports, changed signatures. |
| `roam_brief` | Compose a one-page agent briefing covering five sections: ``next`` (what ``roam next`` would recommend), ``highlights`` (stack / top danger zones / top mined laws from ``roam agents-md``), ``pr_bundle`` (current PR-bundle status on the active branch), ``mode`` (active agent mode and its allow-list size), and ``runs`` (the N most-recent runs from the ledger). Designed as the FIRST command an agent runs when joining a roam-indexed repo. Different from ``roam_next`` (single-command router) -- this is the verdict-first session kickoff packet. |
| `roam_budget_check` | Check changes against architectural budgets (cycles, health floor, complexity). |
| `roam_bus_factor` | Score knowledge-concentration risk per directory: Shannon entropy over unique authors, primary-author share, last activity, and a staleness factor. Flags CRITICAL / HIGH / MEDIUM / LOW per module. Different from ``roam_owner`` (per-file blame) and ``roam_congestion`` (too-many-authors merge-conflict risk) -- this measures knowledge-loss risk. |
| `roam_capsule_export` | Sanitized structural graph export without code bodies (privacy-safe). |
| `roam_catalog` | Return the full machine-readable list of every roam MCP tool currently registered, including title, description, and capability flags (core / read_only / destructive). Use this once at session start to discover what's available without enumerating tools. |
| `roam_causal_graph` | Build per-symbol causal graphs: edges from inputs (parameters / globals / env reads) to sinks (side-effecting calls / return / raise / mutation). Six causal kinds: ``param_to_effect``, ``param_to_return``, ``global_to_effect``, ``global_to_mutation``, ``env_to_effect``, ``param_to_raise``. Heuristic line-level text scan -- false negatives expected. Different from ``roam_taint`` (cross-symbol taint propagation) -- this is intra-symbol dataflow only. |
| `roam_cga_emit` | Emit a Code Graph Attestation — in-toto v1 statement with predicate type `roam-code.dev/CodeGraph/v1` (or `CodeGraph-AIBOM/v1` with --aibom). Merkle root over symbol fingerprints + edge-bundle digest. Optional cosign keyless or offline signing. |
| `roam_cga_verify` | Verify a Code Graph Attestation — re-derives the Merkle root + edge-bundle digest from the live DB and compares to the bundled predicate, AND verifies the cosign signature on the sibling `.bundle`. Fails closed (exit 5) when no bundle is present unless no_cosign=True is passed to acknowledge predicate-only verification. |
| `roam_changelog` | List commits since last tag, optionally formatted as a markdown CHANGELOG draft. |
| `roam_check_rules` | Run 10 built-in structural rules: cycles, fan-out, complexity, tests, god classes, layer violations. |
| `roam_clean` | Remove orphaned index entries (files deleted from disk) without full rebuild. |
| `roam_clones` | Detect near-duplicate code via AST structural hashing (Type-2 clones). |
| `roam_closure` | Minimal set of changes needed for rename/delete/modify (exact files + lines). |
| `roam_clusters` | Show Louvain code clusters and directory mismatches. Returns per-cluster size, cohesion, conductance, modularity Q, mega-cluster sub-group breakdowns, and inter-cluster coupling. Different from ``roam_layers`` (dependency-layer violations) -- this groups by community detection, not by topological depth. |
| `roam_codeowners` | CODEOWNERS coverage, ownership distribution, unowned files, drift detection. |
| `roam_compare` | Diff two roam indices structurally: reports symbols added/removed/moved, per-file complexity deltas above a threshold, language counts, and a one-line health verdict (improved / regressed / sideways). Different from ``roam_graph_diff`` (commit-range graph delta from one index) -- this is the cross-index structural delta for release-vs-release comparisons. |
| `roam_complete` | Prefix completion for symbols / file paths / commands. Faster than search; returns just names. |
| `roam_complexity_report` | Functions ranked by cognitive complexity above threshold. |
| `roam_congestion` | Detect developer congestion: files with too many concurrent authors within a sliding time window. Combines author count, churn intensity, and complexity into a congestion score that predicts merge conflicts and coordination failures. Different from ``roam_bus_factor`` (knowledge-loss risk) and ``roam_owner`` (per-file blame breakdown) -- this measures too-many-cooks contention. |
| `roam_context` | Minimal files + line ranges needed to work with a symbol. |
| `roam_conventions` | Auto-detect codebase naming, file, import, and export conventions with outliers. |
| `roam_coupling` | Show temporal coupling: file pairs that change together. Reads git history to find files with high co-change frequency. Different from ``roam_fan`` (structural connectivity) and ``roam_dark_matter`` (hidden co-change) -- this measures file-level temporal coupling. |
| `roam_coverage_gaps` | Find unprotected entry points: top-level exported functions / methods that have no call-graph path to a required gate symbol (auth / permission / validation). Supports exact gate names, regex patterns, framework presets (python / javascript / go / java-maven / rust), and a ``.roam-gates.yml`` sidecar config. Different from ``roam_auth_gaps`` (PHP/Laravel source analysis) and ``roam_test_gaps`` (untested symbols in changed files) -- this walks the call graph to verify every entry reaches a required gate. |
| `roam_critique` | Verify a patch against the indexed graph (clones-not-edited + blast radius). Pipe a diff in `diff_text`. |
| `roam_cut` | Find fragile domain boundaries via minimum-cut analysis. Computes the thinnest edge cuts between architectural clusters and the highest-impact 'leak edges' whose removal would best improve domain isolation. Different from ``roam_split`` (decomposes a single file) -- this finds boundaries between clusters. |
| `roam_cut_analysis` | Minimum cut analysis: fragile domain boundaries, highest-impact leak edges. |
| `roam_dark_matter` | File pairs that co-change without structural links (hidden coupling). |
| `roam_dashboard` | Unified single-screen codebase status: health, hotspots, bus factor, dead code, AI rot. |
| `roam_dead_code` | Unreferenced exported symbols (dead code candidates). |
| `roam_debt` | Prioritized tech debt with SQALE remediation cost estimates. |
| `roam_delete_check` | Gate the diff (working / staged / PR / HEAD) on surviving references to deleted symbols and files. Per-deletion verdict: SAFE (no surviving references), LIKELY-SAFE (survivors only in tests / docs / unreachable code), or BREAK-RISK (survivors in reachable code). Different from ``roam_critique`` (PR-wide diff review) -- this targets the deletion surface specifically with CI-gate semantics (overall BREAK-RISK trips the gate). |
| `roam_deps` | File-level imports and importers (what depends on this file). |
| `roam_describe` | Auto-generate a project description for AI coding agents: multi-section Markdown report covering overview, directories, entry points, key abstractions, architecture, and testing. Different from ``roam_understand`` (compact codebase overview) -- this is the comprehensive prose description for CLAUDE.md / AGENTS.md / .cursor/rules. The wrapper emits to stdout; on-disk writes are deferred to the CLI (``roam describe --write``) so the MCP surface stays read-only. |
| `roam_dev_profile` | Developer behavioral profiling: commit time patterns, change scatter (Gini), burst detection. |
| `roam_diagnose` | Root cause analysis: upstream/downstream suspects ranked by composite risk. |
| `roam_diagnose_issue` | Debug bundle: root cause suspects + side effects in one call. |
| `roam_diff` | Blast radius of uncommitted/committed changes: affected symbols, files, tests. |
| `roam_disambiguate` | List every symbol matching a name with file/line/kind/signature/PageRank — pick the right overload. |
| `roam_doc_intent` | Link documentation to code: find drift, dead refs, undocumented symbols. |
| `roam_doc_staleness` | Detect stale docstrings: docs whose body has drifted since the comment was written. Uses ``git blame`` to compare docstring timestamps against code body timestamps. Different from ``roam_docs_coverage`` (missing docs ranked by PageRank) and ``roam_stale_refs`` (dangling doc links) -- this audits what existing docs SAY. |
| `roam_docs_coverage` | Doc coverage + stale-doc drift with PageRank-ranked missing docs. |
| `roam_doctor` | Setup diagnostics: Python version, tree-sitter, git, index existence, freshness, SQLite. |
| `roam_dogfood` | One-shot full-stack run: audit + pr-analyze + audit-trail + conformance. |
| `roam_dogfood_aggregate` | Triage view over the dogfood eval corpus: totals, per-command findings count, by-status / by-severity / by-type breakdowns. Reads ``internal/dogfood/evals/`` (or an override path). Useful for agents auditing roam-code itself; mostly a no-op on consumer repos that have no dogfood corpus. |
| `roam_drift` | Ownership drift detection: declared CODEOWNERS vs actual time-decayed contributors. |
| `roam_duplicates` | Detect semantically duplicate functions via structural similarity. |
| `roam_effects` | Side effects of functions: DB writes, network, filesystem (direct + transitive). |
| `roam_endpoints` | List all REST/GraphQL/gRPC endpoints with handlers, methods, and locations. |
| `roam_entry_points` | Catalog every entry point into the codebase: HTTP routes, CLI commands, scheduled jobs, event handlers, message consumers, main functions, and exports. Reports per-entry reachability coverage -- what fraction of symbols each entry transitively reaches through the call graph. |
| `roam_eval_retrieve` | Run the retrieval eval harness over a labeled task set. Reports recall@K, mean reciprocal rank, and per-task diagnostics. Supports a weight sweep and CodeRAG-Bench / BEIR emit formats for public leaderboard submission. |
| `roam_evidence_diff` | Diff two ``ChangeEvidence`` packets: shows hash drift, schema drift, added/removed refs, missing evidence, and changed verdicts. Useful for reviewing PR re-runs, comparing replay windows, or auditing whether a fresh evidence packet has improved or regressed against a stored baseline. Different from ``roam_compare`` (two-index structural delta) -- this is the two-packet evidence delta. |
| `roam_evidence_doctor` | Diagnose a ChangeEvidence packet's health: schema validity, closed-enum conformance, content_hash integrity, completeness banner tier (STRONG / PARTIAL / INSUFFICIENT), declared redactions, and actionable next steps for partial / missing evidence questions. Read-only. |
| `roam_evidence_oscal` | Emit an OSCAL v1.2 document. Default kind='control-mapping' compiles the roam control map (maps roam evidence to EU AI Act, ISO/IEC 42001, NIST AI RMF, NIST AI 600-1, NIST SP 800-218A, SOC 2, internal AI-change policy). kind='assessment-results' compiles a per-run AR document from a ChangeEvidence packet (requires evidence_path); AR mandates an Assessment Plan reference — pass import_ap_ref for an external AP or omit it to inline a synthesized stub AP. Supports evidence for the listed frameworks — does not certify compliance. Two roam-specific concepts (authority_refs, redactions) surface as OSCAL ``prop`` extensions under the ``urn:roam:oscal:v1`` namespace. |
| `roam_expand_toolset` | List available tool presets or show contents of a preset. Presets: core (57), review (70), refactor (70), debug (69), architecture (71), compliance (13), full (224). |
| `roam_explore` | Codebase exploration bundle: understand overview + optional symbol deep-dive in one call. |
| `roam_fan` | Show fan-in / fan-out: the most-connected symbols or files. Flags hub / spreader / HIGH-RISK structural hotspots based on cross-file import / call edges. Different from coupling (co-change frequency) -- this measures structural connectivity. |
| `roam_fetch_handle` | Fetch all or part of a large payload by handle — supports byte slice, section pick, jq projection. |
| `roam_file_info` | File skeleton: all symbols with signatures, kinds, line ranges. |
| `roam_findings_count` | Show per-detector finding counts. Useful for spotting which detectors have migrated to the central registry vs which are still only emitting to their detector-specific tables. |
| `roam_findings_list` | List rows from the central findings registry, optionally filtered by detector or subject. Cross-detector view -- every migrated detector (clones, dead, complexity, smells, n1, missing-index, ...) emits here behind one schema. |
| `roam_findings_show` | Show full detail for a single finding by its stable ``finding_id_str``. Returns the detector version, subject, confidence tier, claim, evidence JSON, and any suppressions. |
| `roam_fingerprint` | Topology fingerprint for cross-repo comparison or structural drift tracking. |
| `roam_fitness` | Run architectural fitness functions from ``.roam/fitness.yaml``: dependency constraints, layer enforcement, metric thresholds, naming conventions, and trend regression guards. Different from ``roam_preflight`` (compound 6-signal pre-edit gate) -- this is the dedicated fitness surface with per-rule output, baseline / delta mode, and trend regression guards. |
| `roam_flag_dead` | Detect potentially stale feature-flag code: flags referenced only once, flags always checked with the same boolean default, and flags clustered in a single file. Recognises LaunchDarkly, Unleash, Split, generic ``feature_flag(...)`` calls, and ``FEATURE_*`` env-var patterns. Different from ``roam_dead_code`` (graph-unreachable symbols) -- this targets code that is alive in the graph but gated behind flags that may never fire. |
| `roam_fleet_plan` | Plan a multi-agent fleet for a goal — graph-aware partition (Louvain + co-change) emits .roam-fleet.json for Composio / Copilot CLI / raw. |
| `roam_fn_coupling` | Show function-level temporal coupling: symbol pairs that change together across commits. Different from ``roam_coupling`` (file-level pairs) -- this drills into co-changing symbols inside and across files, with optional structural-edge filtering. |
| `roam_for_bug_fix` | Compound: diagnose + affected_tests + diff + context for a symbol you're about to debug. |
| `roam_for_new_feature` | Compound: understand + search + context + complexity for an area you're about to add code to. |
| `roam_for_refactor` | Compound: preflight + impact + complexity_report + clones for a symbol you're about to refactor. |
| `roam_for_security_review` | Compound: taint + vuln + critique + adversarial for a security review pass. |
| `roam_forecast` | Predict when metrics will exceed thresholds (Theil-Sen regression). |
| `roam_generate_plan` | Structured execution plan for code modification: read order, invariants, tests. |
| `roam_get_annotations` | Read annotations for symbols, files, or project. Filter by tag/date. |
| `roam_get_invariants` | Implicit contracts for symbols: signature stability, usage spread, breaking risk. |
| `roam_graph_diff` | Show the structural graph delta between two snapshots. Surfaces new / removed symbols, edge churn, degree shifts, new cycles, layer migrations, and likely renames. Reads persisted snapshots from ``.roam/snapshots/`` -- capture one with ``--save-snapshot``. |
| `roam_graph_stats` | Report graph-level invariants: density, connected components, average in/out degree, top in-degree symbols, and approximate diameter. One overview number for 'how dense, connected, and cyclic is this codebase'. |
| `roam_grep` | Run index-aware grep across the codebase. Returns matches with their enclosing symbol, reachability badge, PageRank, clone-class, and bridge annotations. Supports multi-pattern, source-only / test-only filters, reachable-from / unreachable filters, co-occurrence across patterns, and rank-by importance. |
| `roam_guard` | Check breaking-change risk for a symbol before editing: 0..100 risk score with component breakdown (blast radius, complexity, centrality, test gap, layer analysis) plus caller / callee lists and covering tests -- all within a ~2K-token budget. Different from ``roam_preflight`` (file / staged / coupling / convention / fitness composite) -- this is the per-symbol quantified risk score for sub-agent dispatch. |
| `roam_health` | Codebase health score (0-100) with issue breakdown, cycles, bottlenecks. |
| `roam_history_grep` | Run git pickaxe (``-S`` / ``-G``) through commit history. Returns commits that introduced or removed the literal string, with author, date, short SHA, and summary per commit. |
| `roam_hotspots` | Show runtime hotspots: symbols ranked by static analysis vs real production traces (requires ``roam ingest-trace`` to have populated ``runtime_stats``). Each row is tagged UPGRADE (runtime-critical but statically safe), CONFIRMED (both agree), or DOWNGRADE (statically risky but low traffic). Different from ``roam_why_slow`` (top-N by latency alone) -- this classifies static vs runtime mismatch. |
| `roam_hover` | One-line architectural summary for a symbol — kind, location, blast-radius bucket, top caller, top callee. |
| `roam_idempotency` | Classify symbols by retry safety: ``idempotent`` (pure, read-only I/O, write-with-check patterns like ``mkdir(exist_ok=True)`` / ``INSERT OR IGNORE`` / ``UPSERT`` / ``if not exists: create``), ``non_idempotent`` (naive writes, mutations, appends), or ``unknown`` (process spawn / unreadable body). Composes on top of ``roam_side_effects``. Different from ``roam_tx_boundaries`` (transaction correctness) -- this answers ``is it safe to retry?``. |
| `roam_impact` | Blast radius for 'is it safe to change?' — symbols + files affected, in 5 lines. Compact decision-support output. Round 4 / S: the right default tool for safety-checks; preflight is heavier. |
| `roam_ingest_trace` | Ingest runtime traces (OTel/Jaeger/Zipkin), match spans to symbols. |
| `roam_init` | Initialize roam and build the first index. Task-mode for non-blocking setup. |
| `roam_intent` | Link documentation to code: find which docs mention which symbols, and detect doc-to-code drift (references to non-existent symbols). Different from ``roam_docs_coverage`` (PageRank-ranked missing-docstring hotlist) and ``roam_doc_staleness`` (stale docstring content) -- this is the prose-doc-to-symbol linker plus drift detector. |
| `roam_invariants` | Discover implicit contracts for a symbol or the public API surface: signature shape, parameter count and ordering, usage spread across files, dependency set. Different from ``roam_check_rules`` (explicit governance rules) -- this is the AUTO-discovered implicit-contract surface so agents know what must stay stable when modifying a symbol. |
| `roam_layers` | Show topological dependency layers and violations. Returns each layer's symbol count, directory breakdown, and any back-edges that violate the topological order. Different from ``roam_clusters`` (community detection) -- this measures dependency depth. |
| `roam_llm_smells` | Run LLM-API integration linter over indexed files: detects unpinned model versions, missing max_tokens, prompt injection via user-input concatenation, unvalidated json.loads on LLM output, and missing temperature. Different from ``roam_vibe_check`` (AI-generated code shape) and ``roam_smells`` (structural anti-patterns) -- this is the production gate for human-authored LLM-using code. |
| `roam_map` | Show project skeleton: directory tree, entry points, top symbols by PageRank, language counts. Different from ``roam_describe`` (prose description) and ``roam_minimap`` (sentinel-block one-pager for CLAUDE.md) -- this is the structured skeleton with directories, entry points, and ranked symbols for agent onboarding. |
| `roam_metrics` | Show unified per-file or per-symbol metrics: cognitive complexity, fan-in / fan-out, SNA centrality vector (PageRank / betweenness / closeness / eigenvector / clustering coefficient), composite debt score, churn, test coverage, and comprehension difficulty in a single view. |
| `roam_metrics_push` | Push metrics-only summary to Roam Cloud Lite. **Default is dry-run.** |
| `roam_migration_plan` | Generate an ordered migration plan with risk + blast-radius per step from a target-architecture YAML spec or inline ``--move SYMBOL=path/to/new/file`` directives. Each step is annotated with caller count and a derived risk score so agents can decide where to stop or insert tests. Stops at the first step exceeding ``max_risk``. Different from ``roam_simulate`` (counterfactual single-move analysis) -- this is the ordered multi-step plan with a risk gate. |
| `roam_migration_safety` | Non-idempotent database migrations (unsafe for re-run). |
| `roam_minimap` | Generate a compact ~20-line codebase minimap for CLAUDE.md injection: tech stack, annotated directory tree, key symbols by PageRank, high-fan-in symbols to avoid, hotspots, detected conventions. Different from ``roam_describe`` (long-form prose) and ``roam_map`` (structured skeleton) -- this is the sentinel-block one-pager. The wrapper emits to stdout; on-disk updates are deferred to the CLI (``roam minimap --update`` / ``--init-notes``) so the MCP surface stays read-only. |
| `roam_missing_index` | Queries on non-indexed columns (slow query risk). |
| `roam_module` | Show directory contents: exported symbols, signatures, external imports / importers, internal cohesion percentage, and API surface ratio. Different from ``roam_describe`` (project-wide) -- this analyses a single directory. |
| `roam_mutate` | Agentic editing: move/rename/add-call/extract symbols with auto-import rewrite. |
| `roam_n1` | Detect N+1 I/O patterns in ORM code (Laravel/Django/Rails/SQLAlchemy/JPA). |
| `roam_next` | Suggest the next ``roam`` command based on cheap repo-state signals: index presence, staleness, working-tree dirtiness, recent envelope, and recent memory. Emits one imperative recommendation in <200ms. Different from ``roam_brief`` (multi-section session kickoff) and ``roam_workflow`` (curated multi-step recipes) -- this is the single-command router. |
| `roam_onboard` | Generate a new-developer onboarding guide for the codebase. |
| `roam_oracle_batch` | Run multiple oracle queries in one call. Items: [{name, oracle, max_hops?}, ...] where oracle is one of symbol-exists, route-exists, is-test-only, is-reachable-from-entry, is-clone-of. |
| `roam_oracle_is_clone_of` | Answer the boolean oracle question: does this symbol have persisted clone siblings in the ``clone_pairs`` table? Returns a yes/no verdict envelope with the matched clone class size. Different from ``roam_clones`` (full clone-pair enumeration) -- this is the cheap boolean lookup for one symbol's clone status. |
| `roam_oracle_is_reachable_from_entry` | Answer the boolean oracle question: is the symbol reachable from any entry point via the call graph (BFS up to ``max_hops`` depth)? Useful for sniffing orphans and production-vs-tooling code. Different from ``roam_dead_code`` (broad dead-symbol detection) and ``roam_entry_points`` (entry-point enumeration) -- this is the cheap boolean lookup for one symbol's reachability. |
| `roam_oracle_is_test_only` | Answer the boolean oracle question: are ALL callers of this symbol in test files? Useful for sniffing test fixtures and dead-but-test-only helpers. Different from ``roam_dead_code`` (broad dead-symbol detection) -- this is the cheap boolean lookup for one symbol's test-only status. |
| `roam_oracle_route_exists` | Answer the boolean oracle question: does a route handler match this URL path? Returns a yes/no verdict envelope with the matched handler's file + kind when found. Different from ``roam_endpoints`` (full endpoint enumeration) -- this is the cheap boolean lookup for one route precondition check. |
| `roam_oracle_symbol_exists` | Answer the boolean oracle question: does a symbol with this name exist in the index? Returns a yes/no verdict envelope with the matched symbol's file + kind when found. Different from ``roam_search_symbol`` (top-N ranked hits) -- this is the cheap boolean lookup for agent precondition checks. |
| `roam_oracle_test_only` | Alias of roam_oracle_is_test_only — preserves the shorter name agents sometimes guess. |
| `roam_orchestrate` | Partition codebase for parallel multi-agent work with exclusive write zones. |
| `roam_orphan_imports` | List imports that don't resolve to any indexed module or installed package -- catches typo'd local imports, missing packages, and dangling relative imports. Covers Python (default), JavaScript / TypeScript, and Go. Different from ``roam_dead_code`` (unused symbols) -- this targets import-statement orphans. |
| `roam_orphan_routes` | Backend routes with no frontend consumer (dead endpoints). |
| `roam_over_fetch` | Models serializing too many fields (data over-exposure risk). |
| `roam_owner` | Show code ownership computed from git blame: per-author line counts, percentages, last-active dates, and a fragmentation index. Works on a file or a directory prefix. Different from ``roam_codeowners`` (which reads the CODEOWNERS file) -- this measures actual ownership. |
| `roam_partition` | Multi-agent work partitioning: split codebase into independent work zones. |
| `roam_path_coverage` | Critical call paths with zero test protection, ranked by risk. |
| `roam_patterns` | Detect positive architectural patterns: Singleton, Factory, Observer, Repository, Middleware, Strategy, and Decorator. Different from ``roam_smells`` (negative anti-patterns) -- this discovers intentional design patterns. |
| `roam_plan` | Generate a structured execution plan for modifying code: read-order (call-graph BFS), invariants (mined contracts), blast-radius preview, and per-task heuristics. Five task types: ``refactor`` / ``debug`` / ``extend`` / ``review`` / ``understand``. Different from ``roam_plan_refactor`` (refactoring-specific simulation) and ``roam_preflight`` (blast-radius gate) -- this is the general-purpose work plan for any task type. |
| `roam_plan_refactor` | Build an ordered refactor plan for one symbol using risk/test/simulation context. |
| `roam_postmortem` | Replay current detectors against past commits: walks a git commit range, runs ``roam critique`` against each commit's diff, and reports which findings would have surfaced pre-merge. Useful for retrospective replay -- 'would today's detector set have caught the incidents already in history?' Different from ``roam_pr_replay`` (one PR replay) -- this is the range-replay over historical commits. |
| `roam_pr_analyze` | Agent-aware PR risk verdict — INTENTIONAL / SAFE / REVIEW / BLOCK. |
| `roam_pr_comment_render` | Render a markdown PR comment from a pr-analyze JSON envelope. |
| `roam_pr_diff` | Structural graph delta of code changes: metric deltas, layer violations. |
| `roam_pr_prep` | One-shot pre-PR fitness check: bundles ``diff`` blast radius + ``critique`` + ``pr-risk`` into a single envelope with a ``ready_to_open`` verdict. Different from ``roam_pr_risk`` (composite risk score alone) and ``roam_critique`` (clones-not-edited + blast-radius alone) -- this is the three-section pre-PR rollup with the go/no-go verdict. |
| `roam_pr_risk` | Risk score (0-100) for pending changes with per-file breakdown. |
| `roam_preflight` | Pre-change safety check: blast radius, tests, complexity, fitness. Call BEFORE modifying code. |
| `roam_prepare_change` | Pre-change bundle: preflight + context + effects in one call. Call BEFORE modifying code. |
| `roam_py_modern` | Python modernisation signal: walrus, match, PEP 604/585, f-strings vs legacy. |
| `roam_py_types` | Python type-annotation health: % public fns fully typed, Any usage, legacy typing. |
| `roam_pytest_fixtures` | pytest fixture chain: top fixtures by dependent count, or per-symbol dependency walk. |
| `roam_recommend` | Surface symbols related to a given symbol via three signal sources combined: call-graph neighbours (1-hop in + out), git co-change (other symbols whose files changed in the same commits), and persisted clone siblings (when ``roam clones --persist`` was run). Each candidate gets a score that's the normalised sum of the three contributions. Different from ``roam_impact`` (transitive blast radius) and ``roam_neighbours`` (graph-only 1-hop neighbours) -- this fuses co-change + clones into the ranking. |
| `roam_refs_text` | Audit literal strings across the project and emit a per-string verdict: SAFE-TO-REMOVE / REVIEW / LOAD-BEARING. Groups every reference by surface (code, test, docs, config, generated, vendored) and annotates reachability for code hits. |
| `roam_reindex` | Incremental or force reindex. Task-mode + elicited confirmation for force runs. |
| `roam_relate` | How symbols connect: shared deps, call chains, conflicts, cohesion score. |
| `roam_repo_map` | Compact project skeleton with key symbols per file, by PageRank. |
| `roam_report` | Run a compound report preset (built-ins: ``first-contact``, ``security``, ``pre-pr``, ``refactor``, ``guardian``) that orchestrates multiple analysis commands into one rendered report. Different from ``roam_audit`` (single fixed bundle) -- this is the preset-driven multi-command roll-up with optional Markdown output and strict exit-code gating. |
| `roam_reset` | Delete index DB and rebuild from scratch. Requires force=True. Recovery for corrupted indexes. |
| `roam_retrieve` | Graph-aware context for free-form tasks: FTS5 + structural rerank (PageRank + clones) + token budget. |
| `roam_review_change` | Change review bundle: pr-risk + breaking changes + structural diff in one call. |
| `roam_risk` | Rank symbols by domain-weighted risk: combines static risk (fan-in + fan-out + betweenness) with domain criticality weights so financial / auth / data-integrity symbols rank higher than UI symbols. Different from ``roam_fan`` (raw fan-in/out degree) and ``roam_hotspots`` (runtime hotspot classification) -- this is the semantic-domain-weighted risk heatmap. |
| `roam_rules_check` | Evaluate custom governance rules from .roam/rules/ YAML files. |
| `roam_rules_validate` | Lint a `.roam/rules.yml` for shippability before customers see it. |
| `roam_runtime_hotspots` | Runtime hotspots where static and runtime rankings disagree (UPGRADE/DOWNGRADE). |
| `roam_safe_delete` | Fuse dead-code, blast-radius, and test-coverage signals into a single deletion verdict: SAFE / REVIEW / UNSAFE. Reports direct callers (non-test), transitive dependents, affected files, and a public-API bump that flips SAFE -> REVIEW for exported symbols whose name matches a common public-API prefix. Different from ``roam_dead_code`` (all unreferenced symbols) and ``roam_impact`` (transitive blast radius) -- this is the single go/no-go gate. |
| `roam_safe_zones` | Classify the refactor containment zone around a symbol or file: ISOLATED (no external connections), CONTAINED (<=5 boundary symbols), or EXPOSED (>5). Reports strictly-internal vs boundary symbols and external caller / callee counts per boundary. Different from ``roam_impact`` (unbounded reverse blast radius) and ``roam_closure`` (exact locations needing modification) -- this maps the bounded zone where it is safe to refactor freely. |
| `roam_sbom` | Emit a Software Bill of Materials (CycloneDX 1.7 by default, or SPDX 2.3) enriched with call-graph reachability — distinguishes phantom dependencies from those actually exercised. Pair with --aibom for the AIBOM extension required by EU AI Act Art. 50. |
| `roam_search_semantic` | Find symbols by natural language query (hybrid BM25 + vector + framework packs). |
| `roam_search_symbol` | Find symbols by name substring. Returns kind, file, line, PageRank importance. |
| `roam_secrets` | Scan for hardcoded secrets, API keys, tokens, passwords (24 patterns). |
| `roam_semantic_diff` | Structural change summary: what symbols were added/removed/modified. |
| `roam_session_metrics` | Local-only telemetry: per-tool invocation counts grouped by outcome (success / rate_limited / error). Helps answer "which tools are agents actually using?" and "are 90 of the 224 tools dead weight?". Never phones home — counters live in the MCP server process and reset on restart. |
| `roam_side_effects` | Classify symbols by side-effect bucket: ``none`` (pure), ``io_read`` (disk / network / DB read), ``io_write`` (disk / network / DB write), ``mutation`` (global / module state mutation), ``process`` (subprocess / thread / async), or ``unknown``. Coarse five-bucket taxonomy designed for agent decisions. Different from ``roam_effects`` (finer 11-kind taxonomy + transitive propagation) -- this is the agent's go/no-go classifier for ``can I retry this safely?``. |
| `roam_simulate` | Predict metric deltas from move/extract/merge/delete operations. |
| `roam_simulate_departure` | Simulate knowledge loss if a developer leaves the team. |
| `roam_sketch` | Render a compact structural skeleton of a directory: every file's exported symbols with kind, signature, line range, and first-line docstring. Different from ``roam_understand`` (broader project overview) and ``roam_file_info`` (one-file skeleton) -- this is the directory-level API surface in a single view, with optional ``full=True`` to include private symbols. |
| `roam_smells` | Run 24 deterministic code-smell detectors over the indexed codebase: brain methods, god classes, deep nesting, shotgun surgery, feature envy, long parameter lists, large classes, dead params, low cohesion, message chains, data clumps, type switches, cross-layer clones, parallel hierarchies, and more. Different from ``roam_vibe_check`` (AI-rot pattern regex) and ``roam_patterns`` (positive design patterns) -- this surfaces negative structural anti-patterns from DB queries. |
| `roam_spectral` | Spectral bisection: Fiedler vector partition tree and modularity gap. |
| `roam_split` | Analyse a file's internal call / reference graph and propose natural decomposition groups via Louvain community detection. Reports per-group isolation %, internal vs cross-group edges, and ranked extraction candidates (groups with >=3 symbols and >=50% isolation). Different from ``roam_clusters`` (repo-wide module partitioning) -- this analyses ONE file's internal seams. |
| `roam_stale_refs` | Find dangling file references — markdown links / HTML href-src / backtick paths whose target is missing. v12.48 adds anchor validation, confidence-tagged hints, --diff branch filter, --fix preview/apply, and --sort-by ranking. Set enrich_with_llm=True for LLM-sampled hints on findings the deterministic providers couldn't resolve. |
| `roam_stats` | Aggregate high-level statistics: language / role / kind counts plus a recent-commit activity counter over a configurable window. Different from ``roam_metrics`` (per-symbol static-metric report) and ``roam_graph_stats`` (graph-wide topology stats) -- this is the language-and-role inventory snapshot. |
| `roam_suggest_refactoring` | Rank proactive refactoring candidates using complexity/coupling/churn/smells. |
| `roam_suggest_reviewers` | Suggest optimal code reviewers for changed files. |
| `roam_supply_chain` | Dependency risk dashboard: pin coverage, risk scoring, supply-chain health. |
| `roam_symbol` | Symbol definition, callers, callees, PageRank, fan-in/out metrics. |
| `roam_syntax_check` | Tree-sitter syntax validation. Finds ERROR/MISSING AST nodes. No index needed. |
| `roam_taint` | Graph-reach taint analysis. Returns OpenVEX-shaped findings (spec-legal status + justification — never `code_not_reachable`). 10 starter rule packs: sqli, xss, ssrf, path-traversal, command-injection, deserialization, open-redirect, urllib, socketio, fileupload. Pair with --ci to gate on findings (exit 5). |
| `roam_taint_classify` | Run `roam taint` then ask the agent's own LLM (via MCP sampling) to classify each reachable finding as IDOR/AUTHZ/SQLI/XSS/CMD_INJECTION/etc. with confidence + reasoning. Counter to Semgrep Multimodal — same LLM-reasoning narrative without a hosted API key. |
| `roam_test_gaps` | Find changed symbols missing test coverage, ranked by severity. |
| `roam_test_impact` | Tests transitively reachable from changed symbols — sharper scope than affected_tests. |
| `roam_test_map` | Map a symbol or file to its current test coverage: direct test edges (test file calls the symbol), file-level importers (test file imports the symbol's module), and convention-based matches (Salesforce ``<Name>Test`` / ``<Name>_Test`` classes). Different from ``roam_test_gaps`` (untested symbols in changed files) and ``roam_affected_tests`` (forward trace from changes to affected tests) -- this is the lookup for what currently exercises a given symbol. |
| `roam_test_pyramid` | Count indexed test files by kind (unit / integration / e2e / smoke / unknown) using path and name conventions, and flag inverted pyramids (when ``e2e + integration > unit``). Different from ``roam_test_gaps`` (missing coverage) -- this measures the shape of the existing test suite for slow-CI risk. |
| `roam_test_scaffold` | Generate a test-file skeleton for a source file or symbol (functions, classes, methods) with the right imports and per-symbol stub blocks. Supports pytest / unittest (Python), jest / mocha / vitest (JS/TS), Go testing, JUnit4 / JUnit5 (Java), and RSpec / Minitest (Ruby). Dry-run by default; pair with ``roam_test_map`` first to confirm no existing coverage. Skips symbols that already have tests in the target file. |
| `roam_timeline` | Chronological commits that touched the file owning a symbol — author, date, lines added/removed. |
| `roam_tour` | Codebase onboarding guide: reading order, entry points, architecture roles. |
| `roam_trace` | Shortest dependency path between two symbols with hop details. |
| `roam_trends` | Historical metric tracking: record and query health metric trends over time. |
| `roam_tx_boundaries` | Classify functions by transactional safety: ``transactional`` (begin matched by commit/rollback, all mutations inside scope), ``partial_transactional`` (mutations both inside AND outside scope), ``unsafe_mutation`` (mutations OUTSIDE any transaction wrapper -- latent bug), ``unmatched_begin`` (begin without commit/rollback -- leak), ``unmatched_commit``, ``non_transactional``, or ``unknown``. Composes on top of ``roam_side_effects``. Different from ``roam_idempotency`` (retry safety) -- this gates transaction correctness. |
| `roam_understand` | Full codebase briefing: stack, architecture, health, hotspots. Call FIRST in a new repo. |
| `roam_uses` | All consumers of a symbol: callers, importers, inheritors by edge type. Use this *instead of* a multi-shape grep ("->X\|\.X\b\|'X'\|\"X\"") to find references — graph-precise, no string-literal / comment false positives, and the result is already structured by edge type. For 3+ symbols call `roam_batch_get` (one round-trip) instead. |
| `roam_validate_plan` | Pre-apply validator for a multi-step change plan. Returns blockers, warnings, advice per operation. |
| `roam_verify` | Check changed files for naming, import, error-handling, and duplicate issues. |
| `roam_verify_imports` | Hallucination firewall: validate import statements resolve to indexed symbols. |
| `roam_vibe_check` | AI rot score (0-100): 8-pattern taxonomy of AI code anti-patterns. |
| `roam_visualize` | Generate Mermaid/DOT architecture diagram with smart filtering. |
| `roam_vuln_map` | Ingest vulnerability scanner reports (npm/pip/trivy/osv), match to symbols. |
| `roam_vuln_reach` | Vulnerability reachability through call graph: paths, hops, blast radius. |
| `roam_weather` | Churn x complexity hotspot ranking: highest-leverage refactoring targets. |
| `roam_why` | Explain why a symbol matters: role classification (Hub/Bridge/Leaf), transitive reach, critical-path membership, cluster cohesion, and a one-line verdict. Accepts multiple symbol names for batch triage. Different from ``roam_fan`` (raw connectivity ranking) and ``roam_preflight`` (blast-radius gate before edit) -- this is the per-symbol role explainer for triage and onboarding. |
| `roam_why_fail` | Triage a failing test/symbol: recently-changed symbols transitively reachable from it. |
| `roam_why_slow` | Rank runtime hotspots by cost = log10(call_count + 1) * p99_latency_ms. Reads ``runtime_stats`` populated by ``roam ingest-trace``. Optionally restricts to symbols in changed files vs a base ref. Different from ``roam_hotspots`` (static-vs-runtime classification) -- this is the pure latency-weighted ranking. |
| `roam_workflow` | Inspect a workflow recipe DAG, list available recipes, or suggest what to run next given a prior command. Useful as an agent navigation aid: 'I just ran roam impact -- what should I run next?' Different from the heavyweight analytical recipes -- this is the metadata-only recipe browser. |
| `roam_ws_context` | Cross-repo augmented context for a symbol spanning multiple repos. |
| `roam_ws_understand` | Multi-repo workspace overview: per-repo stats, cross-repo connections. |
| `roam_x_lang` | Show cross-language symbol bridges: Protobuf .proto -> generated Go/Java/Python stubs, Salesforce Apex -> Aura/LWC/Visualforce, REST API frontend -> backend route, template variable -> source, and env-var read -> .env definition. Use ``roam_bridges`` to list registered bridge types. |
<!-- END auto-count:readme-mcp-tool-list-table -->

**Resources:** `roam://health` (current health score), `roam://summary` (project overview)

</details>

<details>
<summary><strong>Claude Code</strong></summary>

```bash
claude mcp add roam-code -- roam mcp
```

Or add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "roam-code": {
      "command": "roam",
      "args": ["mcp"]
    }
  }
}
```

</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "roam-code": {
      "command": "roam",
      "args": ["mcp"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "roam-code": {
      "command": "roam",
      "args": ["mcp"]
    }
  }
}
```

</details>

<details>
<summary><strong>VS Code + Copilot</strong></summary>

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "roam-code": {
      "type": "stdio",
      "command": "roam",
      "args": ["mcp"]
    }
  }
}
```

</details>

## Roam Review (PR bot for AI-generated changes)

> **Status (2026-05-09):** Early access. Pricing published, sign-up via
> [hello@roam-code.com](mailto:hello@roam-code.com). Flat tiers from
> $99/mo: Starter $99 · Team $299 · Business $799 · Scale $1,499. Free
> for open-source forever. Full pricing at <https://roam-code.com/pricing>.

Most "AI safety net" tools — CodeRabbit, Greptile, Qodo — review PR **semantics** (does the diff *look* right?). They don't read your **graph**: who calls the changed symbol, which layer it belongs to, whether the AI just touched a god-component with 47 callers. Roam Review fills that gap.

`roam pr-analyze` is the CLI engine: pipe a unified diff in, get back a verdict (`INTENTIONAL` / `SAFE` / `REVIEW` / `BLOCK`) plus AI-likelihood score, blast radius, rule violations, suggested reviewers, and a governance audit-trail record.

```bash
git diff main..HEAD | roam pr-analyze --explain --with-reviewers --audit-trail
roam pr-analyze main..HEAD --gate                      # exit 5 on BLOCK (CI gate)
roam --json pr-analyze --input pr.diff | roam pr-comment-render   # ready-to-post markdown
roam pr-analyze --batch ./diffs/ --cache --parallel 4  # 24-55x speedup on incremental re-runs
roam audit-trail-verify                                # check SHA-256 chain integrity
roam audit-trail-conformance-check --gate              # governance-evidence score (CI)
```

**Nine weighted heuristic signals** score the diff for AI-likelihood: add/remove ratio, comment density, test coverage, function-size variance, generic naming, orphan imports, **placeholder density** (TODO/FIXME/NotImplementedError stubs), **LLM-phrase density** ("we use this approach because…"), **suspicious imports** (numbered modules, mass typing imports). Each carries **language-aware weights** across Python / TypeScript / JavaScript / Go / Rust / Java / Kotlin. Starter rule packs ship for Python, TypeScript, Go, and Java at [`templates/rules/`](templates/rules/) — drop one at `.roam/rules.yml` to enable. Custom rules look like:

```yaml
rules:
  - id: no-frontend-db-import
    description: Frontend modules must not import from db/ directly
    pattern: import_from              # supported: import_from, function_call, class_inherit, decorator_use
    source_glob: "frontend/**/*.{ts,tsx}"
    forbidden_target_glob: "lib/db/**"
    severity: BLOCK
  - id: no-eval
    pattern: function_call
    source_glob: "src/**/*.py"
    forbidden_target_glob: "eval"
    severity: BLOCK
```

A **drift baseline** (`--save-baseline` / `--baseline FILE`) compares the current PR's signals against the previous analysis and auto-escalates the verdict on regression — the GitHub App reads this to render `(+5 vs prev)` / `(-22 vs prev)` arrows on every push.

The **`pr-analyze --audit-trail`** flag appends a SHA-256-chained record to `.roam/audit-trail.jsonl` for each analysis (actor, repo, git SHA, diff hash, verdict, blast radius, AI-likelihood, intent marker). `roam audit-trail-verify` walks the chain and surfaces tampered records; `roam audit-trail-export --format md|csv|json` produces procurement-friendly reports. This is best framed as SOC 2 CC8.1, ISO 42001, and internal AI-governance evidence. Article 12 is only relevant for actual Annex III high-risk AI-system buyers, and Roam's records are supporting evidence rather than complete runtime inference logs.

A hosted GitHub App is in development on top of this CLI engine. Until it ships, the CLI is usable today as a free CI gate (`roam pr-analyze --gate` exits 5 on BLOCK).

## Roam Cloud (metrics history, no source upload)

> **Status (2026-05-09):** Early access. From $19/repo/mo (Starter),
> $99/mo Team (10 repos), $299/mo Growth. 30-day money-back. Source
> code is never uploaded — only metrics.

`roam metrics-push` sends a *summary-only* payload from `roam audit --json` to a Roam Cloud endpoint — numerical metrics, file paths (or SHA-256 hashes when `--anonymize`), and identifier names only. **No source-code bodies are transmitted**, ever. Inspect the exact payload locally with `--dry-run` before any token is set.

```bash
roam metrics-push --dry-run                            # local-only inspection
roam metrics-push --token $ROAM_CLOUD_TOKEN --anonymize
roam metrics-push --no-hotspots --json                 # minimal payload
```

The hosted dashboard at `roam.cloud` (in development) renders trend charts of health-score, debt, dead-code count, danger-zone count, and bus-factor concentration over time. The schema (`roam-metrics-v1`) is allow-listed: any payload key outside the allow-list is rejected by the receiving API.

Both products are paid layers on top of the free CLI; the CLI itself stays Apache 2.0, zero-API-key, fully local, forever.

## PR Replay (one-shot paid audit)

> **Status (2026-05-09):** Available today via email. Self-serve checkout
> launches alongside Roam Review.

PR Replay runs Roam against your last 30 or 90 merged PRs and ships a
written structural-review report plus a founder walk-through. Same
engine as the free CLI; the engagement is the report + the call.

| Tier   | Price | What you get |
| ------ | ----- | ------------ |
| Sample | Free, DIY (`roam pr-replay --tier sample`) | 5 PRs, watermarked, no founder review. Same engine as the paid tiers. |
| Team   | $2,500 | 30-PR report, 30-min walk-through. **$1,250 credit** toward a Roam Review subscription within 60 days. |
| Deep   | $6,000 | 90-PR report with per-detector deep-dive + a 90-day remediation plan + 90-min walk-through. **$3,000 credit** toward a Roam Review subscription within 60 days. |

Order paid tiers by emailing
[hello@roam-code.com](mailto:hello@roam-code.com) — the
self-serve Stripe checkout launches with Roam Review. Full deliverable
shape at <https://roam-code.com/audit>.

## CI/CD Integration

All you need is Python 3.10+ and `pip install roam-code`.

### GitHub Actions

```yaml
# .github/workflows/roam.yml
name: Roam Analysis
on: [pull_request]

jobs:
  roam:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: Cranot/roam-code@main
        with:
          commands: health
          gate: "score>=70"
          sarif: true
          comment: true
```

Use `roam init` to auto-generate this workflow.

| Input | Default | Description |
|-------|---------|-------------|
| `commands` | `health` | Space-separated roam commands to run |
| `gate` | (empty) | Quality gate expression (e.g., `score>=70`). Exit 5 on failure |
| `sarif` | `false` | Upload SARIF results to GitHub Code Scanning |
| `comment` | `true` | Post sticky PR comment with results |
| `python-version` | `3.11` | Python version |
| `version` | `latest` | Pin to a specific roam-code version |
| `cache` | `true` | Cache the SQLite index between runs |
| `changed-only` | `false` | Incremental mode: adapt commands to changed files |

<details>
<summary><strong>GitLab CI</strong></summary>

```yaml
roam-analysis:
  stage: test
  image: python:3.12-slim
  before_script:
    - pip install roam-code
  script:
    - roam index
    - roam health --gate
    - roam --json pr-risk origin/main..HEAD > roam-report.json
  artifacts:
    paths:
      - roam-report.json
  rules:
    - if: $CI_MERGE_REQUEST_IID
```

</details>

<details>
<summary><strong>Azure DevOps / any CI</strong></summary>

Universal pattern:

```bash
pip install roam-code
roam index
roam health --gate               # exit 5 on failure (reads .roam-gates.yml)
roam --json health > report.json
```

</details>

## SARIF Output

Roam exports analysis results in [SARIF 2.1.0](https://sarifweb.azurewebsites.net/) format for GitHub Code Scanning.

Fourteen commands honour the global `--sarif` flag (the authoritative list is
`_SARIF_CONSUMERS` in `src/roam/cli.py`, drift-guarded by
`tests/test_sarif_consumer_list.py`). Minimal end-to-end upload:

```yaml
- run: roam --sarif health > roam-health.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: roam-health.sarif
```

For the full CI integration playbook -- plain-pip quickstart, composite Action,
GitLab/Jenkins/Azure/BitBucket templates, severity gates, and upload guardrails
-- see [docs/ci-integration.md](docs/ci-integration.md).

Programmatic access from Python:

```python
from roam.output.sarif import health_to_sarif, write_sarif

sarif = health_to_sarif(health_data)
write_sarif(sarif, "roam-health.sarif")
```

## For Teams

Zero infrastructure, zero vendor lock-in, zero data leaving your network.

| Tool | Annual cost (20-dev team) | Infrastructure | Setup time |
|------|--------------------------|----------------|------------|
| SonarQube Server | $15,000-$45,000 | Self-hosted server | Days |
| CodeScene | $20,000-$60,000 | SaaS or on-prem | Hours |
| Code Climate | $12,000-$36,000 | SaaS | Hours |
| **Roam** | **$0 (Apache 2.0)** | **None (local)** | **5 minutes** |

<details>
<summary><strong>Team rollout guide</strong></summary>

**Week 1-2 (pilot):** 1-2 developers run `roam init` on one repo. Use `roam preflight` before changes, `roam pr-risk` before PRs.

**Week 3-4 (expand):** Add `roam health --gate` to CI as a non-blocking check (configure thresholds in `.roam-gates.yml`).

**Month 2+ (standardize):** Tighten gate thresholds. Expand to additional repos. Track trajectory with `roam trends`.

</details>

<details>
<summary><strong>Complements your existing stack</strong></summary>

| If you use... | Roam adds... |
|---------------|-------------|
| **SonarQube** | Architecture-level analysis: dependency cycles, god components, blast radius, health scoring |
| **CodeScene** | Free, local alternative for health scoring and hotspot analysis |
| **ESLint / Pylint** | Cross-language architecture checks. Linters enforce style per file; Roam enforces architecture across the codebase |
| **LSP** | AI-agent-optimized queries. `roam context` answers "what calls this?" with PageRank-ranked results in one call |

</details>

## Language Support

### Tier 1 -- Full extraction (dedicated parsers)

| Language | Extensions | Symbols | References | Inheritance |
|----------|-----------|---------|------------|-------------|
| Python | `.py` `.pyi` | classes, functions, methods, decorators, variables | imports, calls, inheritance | extends, `__all__` exports |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | classes, functions, arrow functions, CJS exports | imports, require(), calls | extends |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | interfaces, type aliases, enums + all JS | imports, calls, type refs | extends, implements |
| Java | `.java` | classes, interfaces, enums, constructors, fields | imports, calls | extends, implements |
| Go | `.go` | structs, interfaces, functions, methods, fields | imports, calls | embedded structs |
| Rust | `.rs` | structs, traits, impls, enums, functions | use, calls | impl Trait for Struct |
| C / C++ | `.c` `.h` `.cpp` `.hpp` `.cc` | structs, classes, functions, namespaces, templates | includes, calls | extends |
| C# | `.cs` | classes, interfaces, structs, enums, records, methods, constructors, properties, delegates, events, fields | using directives, calls, `new`, attributes | extends, implements |
| PHP | `.php` | classes, interfaces, traits, enums, methods, properties | namespace use, calls, static calls, `new` | extends, implements, use (traits) |
| Visual FoxPro | `.prg` | functions, procedures, classes, methods, properties, constants | DO, SET PROCEDURE/CLASSLIB, CREATEOBJECT, `=func()`, `obj.method()` | DEFINE CLASS ... AS |
| YAML (CI/CD) | `.yml` `.yaml` | GitLab CI: jobs, template anchors, stages. GitHub Actions: workflow name, jobs, reusable workflows. Generic: top-level keys | `extends:`, `needs:`, `!reference`, `uses:` | — |
| HCL / Terraform | `.tf` `.tfvars` `.hcl` | `resource`, `data`, `variable`, `output`, `module`, `provider`, `locals` entries | `var.*`, `module.*`, `data.*`, `local.*`, resource cross-refs | — |
| Vue | `.vue` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |
| Svelte | `.svelte` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |

<details>
<summary><strong>Salesforce ecosystem (Tier 1)</strong></summary>

| Language | Extensions | Symbols | References |
|----------|-----------|---------|------------|
| Apex | `.cls` `.trigger` | classes, triggers, SOQL, annotations | imports, calls, System.Label, generic type refs |
| Aura | `.cmp` `.app` `.evt` `.intf` `.design` | components, attributes, methods, events | controller refs, component refs |
| LWC (JavaScript) | `.js` (in LWC dirs) | anonymous class from filename | `@salesforce/apex/`, `@salesforce/schema/`, `@salesforce/label/` |
| Visualforce | `.page` `.component` | pages, components | controller/extensions, merge fields, includes |
| SF Metadata XML | `*-meta.xml` | objects, fields, rules, layouts | Apex class refs, formula field refs, Flow actionCalls |

Cross-language edges mean `roam impact AccountService` shows blast radius across Apex, LWC, Aura, Visualforce, and Flows.

</details>

| Ruby | `.rb` | classes, modules, methods, singleton methods, constants | require, require_relative, include/extend, calls, ClassName.new | class inheritance |
| Kotlin | `.kt` `.kts` | classes, interfaces, enums, objects, functions, methods, properties | imports, calls, type refs | extends, implements |
| Scala | `.scala` `.sc` | classes, traits, objects, case classes, functions, methods, val/var, type aliases | imports, calls, `new` | extends, with (trait mixins) |
| SQL (DDL) | `.sql` | tables, columns, views, functions, triggers, schemas, types (enums), sequences | foreign keys, view table deps, trigger table/function refs | -- |
| Swift | `.swift` | classes, structs, enums, protocols, functions, methods, properties | imports, calls, type refs | extends, conforms |
| Dart | `.dart` | classes, mixins, extensions, enums, type aliases, functions, methods, constructors | imports, calls, type refs | extends, implements, with |
| JSONC | `.jsonc` | via JSON grammar | -- | -- |
| MDX | `.mdx` | via Markdown grammar | -- | -- |

## Performance

| Metric | Value |
|--------|-------|
| Index 200 files | ~3-5s |
| Index 3,000 files | ~2 min |
| Incremental (no changes) | <1s |
| Any query command | <0.5s |

<details>
<summary><strong>Detailed benchmarks</strong></summary>

### Indexing Speed

| Project | Language | Files | Symbols | Edges | Index Time | Rate |
|---------|----------|-------|---------|-------|-----------|------|
| Express | JS | 211 | 624 | 804 | 3s | 70 files/s |
| Axios | JS | 237 | 1,065 | 868 | 6s | 41 files/s |
| Vue | TS | 697 | 5,335 | 8,984 | 25s | 28 files/s |
| Laravel | PHP | 3,058 | 39,097 | 38,045 | 1m46s | 29 files/s |
| Svelte | TS | 8,445 | 16,445 | 19,618 | 2m40s | 52 files/s |

### Quality Benchmark

| Repo | Language | Score | Coverage | Edge Density |
|------|----------|-------|----------|--------------|
| Laravel | PHP | **9.55** | 91.2% | 0.97 |
| Vue | TS | **9.27** | 85.8% | 1.68 |
| Svelte | TS | **9.04** | 94.7% | 1.19 |
| Axios | JS | **8.98** | 85.9% | 0.82 |
| Express | JS | **8.46** | 96.0% | 1.29 |

### Token Efficiency

| Metric | Value |
|--------|-------|
| 1,600-line file → `roam file` | ~5,000 chars (~70:1 compression) |
| Full project map | ~4,000 chars |
| `--compact` mode | 40-50% additional token reduction |
| `roam preflight` replaces | 5-7 separate agent tool calls |

</details>

Agent-efficiency benchmarks: see the [`benchmarks/`](benchmarks/) directory for harness, repos, and results.

## How It Works

```
Codebase
    |
[1] Discovery ──── git ls-files (respects .gitignore + .roamignore)
    |
[2] Parse ──────── tree-sitter AST per file (28 languages)
    |
[3] Extract ────── symbols + references (calls, imports, inheritance)
    |
[4] Resolve ────── match references to definitions → edges
    |
[5] Metrics ────── adaptive PageRank, betweenness, cognitive complexity, Halstead
    |
[6] Algorithms ── 23-pattern anti-pattern catalog (O(n^2) loops, N+1, recursion)
    |
[7] Git ────────── churn, co-change matrix, authorship, Renyi entropy
    |
[8] Clusters ───── Louvain community detection
    |
[9] Health ─────── per-file scores (7-factor) + composite score (0-100)
    |
[10] Store ─────── .roam/index.db (SQLite, WAL mode)
```

After the first full index, `roam index` only re-processes changed files (mtime + SHA-256 hash). Incremental updates are near-instant.

### .roamignore

Create a `.roamignore` file in your project root to exclude files from indexing. It uses **full gitignore syntax**:

| Pattern | Meaning |
|---------|---------|
| `*.log` | Exclude all `.log` files (basename match) |
| `vendor/` | Exclude the `vendor` directory and everything under it |
| `/build/` | Exclude `build/` at repo root only (anchored) |
| `src/**/*.pb.go` | Exclude `.pb.go` files at any depth under `src/` |
| `**/test_*.py` | Exclude `test_*.py` files anywhere |
| `?` | Match any single character (not `/`) |
| `[abc]` / `[!abc]` | Character class / negated character class |
| `!important.log` | Un-exclude (re-include) `important.log` |
| `# comment` | Lines starting with `#` are comments |

Key rules: `*` matches within a single path segment (not across `/`). `**` matches across `/` boundaries. Last matching pattern wins (for negation). Patterns containing `/` are anchored to the repo root.

```
# .roamignore example
*_pb2.py
*_pb2_grpc.py
vendor/
node_modules/
*.generated.*
/build/
!build/keep/
```

You can also exclude patterns via `roam config --exclude "*.proto"` (stored in `.roam/config.json`) or inspect active patterns with `roam config --show`.

<details>
<summary><strong>Graph algorithms</strong></summary>

- **Adaptive PageRank** -- damping factor auto-tunes based on cycle density (0.82-0.92); identifies the most important symbols (used by `map`, `search`, `context`)
- **Personalized PageRank** -- distance-weighted blast radius for `impact` (Gleich, 2015)
- **Adaptive betweenness centrality** -- exact for small graphs, sqrt-scaled sampling for large (Brandes & Pich, 2007); finds bottleneck symbols
- **Edge betweenness centrality** -- identifies critical cycle-breaking edges in SCCs (Brandes, 2001)
- **Tarjan's SCC** -- detects dependency cycles with tangle ratio
- **Propagation Cost** -- fraction of system affected by any change, via transitive closure (MacCormack, Rusnak & Baldwin, 2006)
- **Algebraic connectivity (Fiedler value)** -- second-smallest Laplacian eigenvalue; measures architectural robustness (Fiedler, 1973)
- **Louvain community detection** -- groups related symbols into clusters
- **Modularity Q-score** -- measures if cluster boundaries match natural community structure (Newman, 2004)
- **Conductance** -- per-cluster boundary tightness: cut(S, S_bar) / min(vol(S), vol(S_bar)) (Yang & Leskovec)
- **Topological sort** -- computes dependency layers, Gini coefficient for layer balance (Gini, 1912), weighted violation severity
- **k-shortest simple paths** -- traces dependency paths with coupling strength
- **Renyi entropy (order 2)** -- measures co-change distribution; more robust to outliers than Shannon (Renyi, 1961)
- **Mann-Kendall trend test** -- non-parametric degradation detection, robust to noise (Mann, 1945; Kendall, 1975)
- **Sen's slope estimator** -- robust trend magnitude, resistant to outliers (Sen, 1968)
- **NPMI** -- Normalized Pointwise Mutual Information for coupling strength (Bouma, 2009)
- **Lift** -- association rule mining metric for co-change statistical significance (Agrawal & Srikant, 1994)
- **Halstead metrics** -- volume, difficulty, effort, and predicted bugs from operator/operand counts (Halstead, 1977)
- **SQALE remediation cost** -- time-to-fix estimates per issue type for tech debt prioritization (Letouzey, 2012)
- **Algorithm anti-pattern catalog** -- 23 patterns detecting suboptimal algorithms (quadratic loops, N+1 queries, quadratic string building, branching recursion, manual top-k, loop-invariant calls) with confidence calibration via caller-count and bounded-loop analysis

</details>

<details>
<summary><strong>Health scoring</strong></summary>

Composite health score (0-100) using a **weighted geometric mean** of sigmoid health factors. Non-compensatory: a zero in any dimension cannot be masked by high scores in others.

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Tangle ratio | 30% | % of symbols in dependency cycles |
| God components | 20% | Symbols with extreme fan-in/fan-out |
| Bottlenecks | 15% | High-betweenness chokepoints |
| Layer violations | 15% | Upward dependency violations (severity-weighted by layer distance) |
| Per-file health | 20% | Average of 7-factor file health scores |

Each factor uses sigmoid health: `h = e^(-signal/scale)` (1 = pristine, approaches 0 = worst). Score = `100 * product(h_i ^ w_i)`. Also reports **propagation cost** (MacCormack 2006) and **algebraic connectivity** (Fiedler 1973). Per-file health (1-10) combines: cognitive complexity (triangular nesting penalty per Sweller's Cognitive Load Theory), indentation complexity, cycle membership, god component membership, dead export ratio, co-change entropy, and churn amplification.

</details>

## How Roam Compares

roam-code is the only tool that combines graph algorithms (PageRank, Tarjan SCC, Louvain clustering), git archaeology, architecture simulation, and multi-agent partitioning in a single local CLI with zero API keys.

Documentation lives at <https://roam-code.com/docs/>:
- Tutorial — <https://roam-code.com/docs/getting-started>
- Command reference — <https://roam-code.com/docs/command-reference>
- Architecture guide — <https://roam-code.com/docs/architecture>
- Integration tutorials — <https://roam-code.com/docs/integration-tutorials>

| Capability | roam-code | AI IDEs (Cursor, Windsurf) | AI Agents (Claude Code, Codex) | SAST (SonarQube, CodeQL) |
|---|---|---|---|---|
| Persistent local index | SQLite | Cloud embeddings | None | Per-scan |
| Call graph analysis | Yes | No | No | Yes (CodeQL) |
| PageRank / centrality | Yes | No | No | No |
| Cycle detection (Tarjan) | Yes | No | No | Deprecated (SonarQube) |
| Community detection (Louvain) | Yes | No | No | No |
| Git churn / co-change | Yes | No | No | No |
| Architecture simulation | Yes | No | No | No |
| Multi-agent partitioning | Yes | No | No | No |
| MCP tools for agents | 224 (57 in default core preset) | Client only | Client only | 34 (SonarQube) |
| Languages | 28 | 70+ | 50+ | 12-42 |
| 100% local, zero API keys | Yes | No | No | Partial |
| Open source | Apache 2.0 | No | Partial | Partial |

### Key Differentiators

- **vs AI IDEs** (Cursor, Windsurf, Augment): roam-code provides deterministic structural analysis. AI IDEs use probabilistic embeddings that can't guarantee reproducible results.
- **vs AI Agents** (Claude Code, Codex CLI, Gemini CLI): These agents read files one at a time. roam-code pre-computes relationships so agents get instant answers about architecture, blast radius, and dependencies.
- **vs SAST Tools** (SonarQube, CodeQL, Semgrep): SAST tools find bugs and vulnerabilities. roam-code understands architecture -- how code is structured, where it's coupled, and what breaks when you change it. Complementary, not competitive.
- **vs Code Search** (Sourcegraph/Amp, Greptile): Text search finds where code is. roam-code understands why code matters -- which functions are central, which modules are tangled, which files are high-risk.

## FAQ

**Does Roam send any data externally?**
No. Zero network calls. No telemetry, no analytics, no update checks.

**Can Roam run in air-gapped environments?**
Yes. Once installed, no internet access is required.

**Does Roam modify my source code?**
Read-only by default. Creates `.roam/` with an index database. The `roam mutate` command can apply code changes (move/rename/extract) but defaults to `--dry-run` mode — you must explicitly pass `--apply` to write changes.

**How does Roam handle monorepos?**
Indexes from the root. Batched SQL handles 100k+ symbols. Incremental updates stay fast.

**How does Roam handle multi-repo projects (e.g., frontend + backend)?**
Use `roam ws init <repo1> <repo2>` to create a workspace. Each repo keeps its own index; a workspace overlay DB stores cross-repo API edges. `roam ws resolve` scans for REST endpoints and matches frontend calls to backend routes. Then `roam ws context`, `roam ws trace`, etc. work across repos.

**Is Roam compatible with SonarQube / CodeScene?**
Yes. Roam complements existing tools. Both can run in the same CI pipeline. SARIF output integrates with GitHub Code Scanning.

## Limitations

Static analysis trade-offs:

- **Static analysis primarily** -- can't trace dynamic dispatch, reflection, or eval'd code. Runtime trace ingestion (`roam ingest-trace`) adds production data but requires external trace export
- **Import resolution is heuristic** -- complex re-exports or conditional imports may not resolve
- **Limited cross-language edges** -- Salesforce, Protobuf, REST API, and multi-repo edges are supported, but not arbitrary FFI
- **Tier 2 languages** get basic symbol extraction only via generic tree-sitter walker
- **Large monorepos** (100k+ files) may have slow initial indexing

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `roam: command not found` | Ensure install location is on PATH. For `uv`: `uv tool update-shell` |
| `Another indexing process is running` | Delete `.roam/index.lock` and retry |
| `database is locked` | `roam index --force` to rebuild |
| Unicode errors on Windows | `chcp 65001` for UTF-8 |
| Symbol resolves to wrong file | Use `file:symbol` syntax: `roam symbol myfile:MyFunction` |
| Health score seems wrong | `roam --json health` for factor breakdown |
| Index stale after `git pull` | `roam index` (incremental). After major refactors: `roam index --force` |

## Update / Uninstall

```bash
# Update
pipx upgrade roam-code
uv tool upgrade roam-code
pip install --upgrade roam-code

# Uninstall
pipx uninstall roam-code
uv tool uninstall roam-code
pip uninstall roam-code
```

Delete `.roam/` from your project root to clean up local data.

## Development

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e ".[dev]"   # includes pytest, ruff
pytest tests/              # ~7,500 tests, Python 3.10-3.13

# Or use Make targets:
make dev      # install with dev extras
make test     # run tests
make lint     # ruff check
```

<details>
<summary><strong>Project structure</strong></summary>

```
roam-code/
├── pyproject.toml
├── action.yml                         # Reusable GitHub Action
├── src/roam/
│   ├── __init__.py                    # Version (from pyproject.toml)
│   ├── cli.py                         # Click CLI (231 canonical + 7 aliases)
│   ├── mcp_server.py                  # MCP server (224 tools, 10 resources, 6 prompts)
│   ├── db/
│   │   ├── connection.py              # SQLite (WAL, pragmas, batched IN)
│   │   ├── schema.py                  # Tables, indexes, migrations
│   │   └── queries.py                 # Named SQL constants
│   ├── index/
│   │   ├── indexer.py                 # Orchestrates full pipeline
│   │   ├── discovery.py               # git ls-files, .gitignore
│   │   ├── parser.py                  # Tree-sitter parsing
│   │   ├── symbols.py                 # Symbol + reference extraction
│   │   ├── relations.py               # Reference resolution -> edges
│   │   ├── complexity.py              # Cognitive complexity (SonarSource) + Halstead metrics
│   │   ├── git_stats.py               # Churn, co-change, blame, Renyi entropy
│   │   ├── incremental.py             # mtime + hash change detection
│   │   ├── file_roles.py              # Smart file role classifier
│   │   └── test_conventions.py        # Pluggable test naming adapters
│   ├── languages/
│   │   ├── base.py                    # Abstract LanguageExtractor
│   │   ├── registry.py                # Language detection + aliasing
│   │   ├── *_lang.py                  # One file per language (21 dedicated + generic)
│   │   └── generic_lang.py            # Tier 2 fallback
│   ├── bridges/
│   │   ├── base.py, registry.py       # Cross-language bridge framework
│   │   ├── bridge_salesforce.py       # Apex <-> Aura/LWC/Visualforce
│   │   └── bridge_protobuf.py         # .proto -> Go/Java/Python stubs
│   ├── catalog/
│   │   ├── tasks.py                  # Universal algorithm catalog (23 patterns)
│   │   └── detectors.py              # Anti-pattern detectors with confidence calibration
│   ├── workspace/
│   │   ├── config.py                  # .roam-workspace.json
│   │   ├── db.py                      # Workspace overlay DB
│   │   ├── api_scanner.py             # REST API endpoint detection
│   │   └── aggregator.py              # Cross-repo aggregation
│   ├── graph/
│   │   ├── builder.py, pagerank.py    # DB -> NetworkX, PageRank
│   │   ├── cycles.py, clusters.py     # Tarjan SCC, propagation cost, Louvain, modularity Q
│   │   ├── layers.py, pathfinding.py  # Topo layers, k-shortest paths
│   │   ├── simulate.py, spectral.py   # Architecture simulation, Fiedler bisection
│   │   ├── partition.py, fingerprint.py # Multi-agent partitioning, topology fingerprints
│   │   └── anomaly.py                 # Statistical anomaly detection
│   ├── commands/
│   │   ├── resolve.py                 # Shared symbol resolution
│   │   ├── graph_helpers.py           # Shared graph utilities (adj builders, BFS)
│   │   ├── context_helpers.py         # Data-gathering helpers for context command
│   │   ├── gate_presets.py            # Framework-specific gate rules
│   │   └── cmd_*.py                   # One module per command
│   ├── analysis/
│   │   ├── effects.py                 # Side-effect classification engine
│   │   └── taint.py                   # Taint analysis
│   ├── refactor/
│   │   ├── codegen.py                 # Import generation (Python/JS/Go)
│   │   └── transforms.py             # move/rename/add-call/extract transforms
│   ├── rules/
│   │   ├── engine.py                  # YAML rule parser + graph query evaluator
│   │   ├── builtin.py                 # 10 built-in governance rules
│   │   ├── ast_match.py               # AST pattern matching with $METAVAR captures
│   │   └── dataflow.py                # Intra-procedural dataflow analysis
│   ├── runtime/
│   │   ├── trace_ingest.py            # OpenTelemetry/Jaeger/Zipkin ingestion
│   │   └── hotspots.py                # Runtime hotspot analysis
│   ├── search/
│   │   ├── tfidf.py                   # TF-IDF semantic search engine
│   │   ├── index_embeddings.py        # Embedding index builder
│   │   └── onnx_embeddings.py         # Optional local ONNX semantic backend
│   ├── security/
│   │   ├── vuln_store.py              # CVE/vulnerability storage
│   │   └── vuln_reach.py              # Vulnerability reachability paths
│   └── output/
│       ├── formatter.py               # Token-efficient formatting
│       ├── sarif.py                   # SARIF 2.1.0 output
│       └── schema_registry.py         # JSON envelope schema versioning
└── tests/                             # ~7,500 tests across 267 test files
```

</details>

### Dependencies

| Package | Purpose |
|---------|---------|
| [click](https://click.palletsprojects.com/) >= 8.0 | CLI framework |
| [tree-sitter](https://github.com/tree-sitter/py-tree-sitter) >= 0.23 | AST parsing |
| [tree-sitter-language-pack](https://github.com/nicolo-ribaudo/tree-sitter-language-pack) >= 0.6 | 165+ grammars |
| [networkx](https://networkx.org/) >= 3.0 | Graph algorithms |

Optional: [fastmcp](https://github.com/jlowin/fastmcp) >= 2.0 (MCP server — install with `pip install "roam-code[mcp]"`)

Optional: Local semantic ONNX stack (`numpy`, `onnxruntime`, `tokenizers`) via `pip install "roam-code[semantic]"`; verify activation with `roam config --semantic-status`.

## Contributing

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e .
pytest tests/   # all ~7,500 tests must pass
```

Good first contributions: add a [Tier 1 language](src/roam/languages/) (see `go_lang.py` or `php_lang.py` as templates), improve reference resolution, add benchmark repos, extend SARIF converters, add MCP tools.

Please open an issue first to discuss larger changes.

## License

[Apache 2.0](LICENSE)
