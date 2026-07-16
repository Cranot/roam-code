<div align="center">

# roam-code

**The local codebase intelligence layer that lets AI coding agents earn the right to change code — with evidence for what was checked.**

[![PyPI version](https://img.shields.io/pypi/v/roam-code?style=flat-square&color=blue)](https://pypi.org/project/roam-code/)
[![GitHub stars](https://img.shields.io/github/stars/Cranot/roam-code?style=flat-square)](https://github.com/Cranot/roam-code/stargazers)
[![CI](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml/badge.svg)](https://github.com/Cranot/roam-code/actions/workflows/roam-ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

<sub>Credential-free · 100% local by default (opt-in `metrics-push` is the only outbound surface) · tamper-evident `ChangeEvidence` packets · Apache 2.0 · runs entirely on your machine</sub>

<!-- BEGIN auto-count:readme-headline-counts -->
<sub>279 commands · 244 MCP tools (16 in the default `core` preset) · 28 languages</sub>
<!-- END auto-count:readme-headline-counts -->

![roam terminal demo](docs/assets/roam-terminal-demo.gif)

</div>

---

**Jump to** —
[Why Roam](#why-roam-is-different) ·
[Install](#install--first-four-commands) ·
[The Compiler](#the-compiler--your-agents-first-token-already-knows-the-answer) ·
[Core commands](#core-commands) ·
[MCP server](#mcp-server) ·
[AI-tool integration](#integration-with-ai-coding-tools) ·
[Roam Guard (PR gate)](#roam-guard-for-prs) ·
[Performance](#performance) ·
[Compare](#how-roam-compares) ·
[Pricing](#paid-layers-free-cli-stays-apache-20) ·
[FAQ](#faq)

---

## Why Roam is different

[METR](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/) and [FrontierCode](https://cognition.ai/blog/frontier-code) both point at the same gap: passing tests is not the same as mergeable code. Roam is an **agent-first CLI surface** that gives the agent local graph facts before it edits, gates risky changes, and emits scoped evidence after the run. In the agent/review tools surveyed as of 2026-06-12, the differentiator is this combination:

- **Credential-free.** No account, no API key, no cloud login. `pip install` and run.
- **100% local by default.** Source code never leaves the machine; air-gapped repos work like cloud repos. The single outbound surface (`roam metrics-push`) is opt-in, summary-only, and prints its exact payload under `--dry-run`.
- **Tamper-evident `ChangeEvidence` packets.** A Roam-guided change can compile into one portable packet — HMAC-chained run ledger + signed Code Graph Attestation + signed PR bundle — answering eight questions: *who acted, what authority existed, what context was read, what changed, what could break, what policy applied, what verified it, who accepted risk*. PR Replay maps those eight questions today: structural change/risk/policy axes are in scope, context and verification are partial, and missing identity/authority/approval evidence is disclosed instead of invented. Cursor logs the run; Roam records and verifies the evidence its producers captured.
- **MCP runtime security at the wrapper boundary.** Every MCP response is scrubbed for secrets on egress, gated against the active mode (`read_only` / `safe_edit` / `migration` / `autonomous_pr`) with a closed-enum `policy_decision`, and each decision receipt is HMAC-linked into the signed run ledger. Inside-server controls; the gateway layer (Interlock / Lasso / Portkey) composes on top — see [`dev/MCP-SECURITY-POSTURE.md`](dev/MCP-SECURITY-POSTURE.md).

Underneath sits a SQLite-backed graph of symbols, calls, imports, layers, git history, runtime traces, smells, clones, security flows, and algorithmic patterns across 28 languages — the same local facts queried before, during, and after a change.

**Dependency-aware, not string-based.** Roam knows `Flask` has 47 dependents and 31 affected tests; `grep` knows it appears 847 times. One command replaces 5-10 tool calls — <0.5s per query, plain-ASCII output, `--json` and `--sarif` envelopes for agents and CI.

|  | Without Roam | With Roam |
|--|-------------|-----------|
| Tool calls | 8 | **1** |
| Wall time | ~11s | **<0.5s** |
| Tokens consumed | ~15,000 | **~3,000** |

*Illustrative — a typical agent workflow on a 200-file Python project (Flask). Reproducible smoke transcript in [`docs/fresh-install-smoke.md`](docs/fresh-install-smoke.md); full indexing-rate harness in [`benchmarks/`](benchmarks/). Exact numbers vary with repo size, agent prompt, and model.*

---

## Install + first four commands

About two minutes from `pip install` to a verdict on whether your next edit is safe.

```bash
pip install "roam-code[mcp]"          # 1. install with MCP server for Claude Code / Cursor / Continue
cd /path/to/your/repo
roam init                             # 2. index the repo into .roam/index.db (one-time, ~30s on most repos)
roam health                           # 3. composite 0-100 score: complexity, cycles, dark-matter coupling, dead code
roam preflight <symbol>               # 4. blast radius + tests + complexity + architecture rules before you edit
```

Python 3.10+. `pipx install roam-code` and `uv tool install roam-code` work too. Drop `[mcp]` for CLI-only. See [`docs/fresh-install-smoke.md`](docs/fresh-install-smoke.md) for a verbatim transcript of these four commands against a clean venv.

Step 4 is the payoff — `roam preflight` on a hot symbol returns a verdict before you touch it:

```text
$ roam preflight open_db
VERDICT: Significant risk — CRITICAL, 1847 symbols in blast radius

Pre-flight check for `open_db (src/roam/db/connection.py:799)`:

  Blast radius:     1847 symbols in 382 files                [CRITICAL]
  Affected tests:   617 direct, 962 transitive               [OK]
  Complexity:       cc=30, nest=4                            [CRITICAL]
  Coupling:         2 files often change together            [MEDIUM]
  Conventions:      no violations                            [OK]

  Overall risk: CRITICAL
  Risk driver:  complexity (cc=30, CRITICAL)
```

*An agent sees the blast radius before it edits — not after the tests fail.*

<details>
<summary><strong>Alternate install methods + Docker</strong></summary>

```bash
pipx install roam-code                                   # isolated environment (recommended)
uv tool install roam-code                                # uv-managed tool
pip install git+https://github.com/Cranot/roam-code.git  # from source

# Docker (alpine-based)
docker build -t roam-code .
docker run --rm -v "$PWD:/workspace" roam-code index
docker run --rm -v "$PWD:/workspace" roam-code health
```

Works on Linux, macOS, and Windows. **Windows:** if `roam` is not found after installing with `uv`, run `uv tool update-shell` and restart your terminal.

</details>

---

## The Compiler — your agent's first token already knows the answer

You ask your agent *"who calls `handleSave`?"* and watch it grep, open
three files, grep again, read a fourth — six turns and $1.30 later you get
the answer the repo's call graph held all along.

Roam ships a **task compiler** that ends that loop. Before your prompt
reaches the model, roam recognizes what kind of question it is, runs the
right code-graph lookups locally (~90 ms, zero model calls), and puts the
*answers* into the prompt: the caller list with line numbers, the git
history already filtered, the source around the bug line you cited. The
agent's first words can be the answer.

For Claude Code it's **one command, zero configuration**:

```bash
pip install "roam-code[mcp]"
cd your-repo && roam init
roam hooks claude --write     # compile-before + verify-after, wired into Claude Code
```

Then use `claude` exactly as you always do. Undo anytime with
`roam hooks claude --uninstall --write`. A broken install can never block
your agent — every hook is fail-open.

**What that buys you, measured head-to-head on Claude** (same prompts, same
repo, with and without the compiler — June 2026, 41 cells):

| Median per task | vanilla | compiled | delta |
|---|---|---|---|
| Agent turns (navigation/comprehension) | 6 | 1 | **−83%** |
| Input tokens | 271K | 53K | **−80%** |
| Cost | $1.30 | $0.48 | **−63%** |
| Wall time | — | — | **−50%** |

A second run on Opus shows the same direction at smaller magnitude (−33%
turns overall; the best single cell hit −88%). And the compiler knows
where it *doesn't* help: prompts that ask the agent to **write** code get
no envelope at all — injection there was measured as pure overhead, so it
spends your tokens only where it wins.

<details>
<summary><b>The full data</b> — every bench cell (including the losses), the ground-truth bug bench, and routing stats</summary>

| Task | turns | input tokens | cost |
|---|---|---|---|
| "where is `open_db` defined?" | 3 → **1** | 156K → 51K | $0.67 → $0.28 |
| "which files depend on `cli.py`?" | 6 → **1** | 252K → 51K | $1.15 → $0.30 |
| "where is the env var configured?" | 9 → **1** | 497K → 53K | $1.40 → $0.31 |
| "what are the layers of this codebase?" | 5 → **1** | 271K → 50K | $1.42 → $0.41 |
| "what changed in `cli.py` recently?" | 4 → **2** | 186K → 104K | $0.62 → $0.40 |
| "explain the compiler module's architecture" | 13 → **6** | 618K → 240K | $1.85 → $1.01 |
| "trace how a command becomes an MCP tool" | 12 → **8** | 464K → 303K | $1.25 → $1.01 |
| security-hook comprehension (hard, multi-file) | 6 → **2** | 267K → 117K | $1.15 → $0.56 |
| "what are the biggest cycles in this codebase?" (re-measured 06-11) | 6 → **1** | — | $0.65 → **$0.07** |
| "where is the CLI entry point?" (trivial, re-measured 06-11) | 1 → 1 | 48K → 50K | $0.21 → $0.22 |
| "write a pytest for X" (generation, re-measured 06-11) | 5 → 7 | 275K → 396K | $0.61 → **$0.45** |

The last two rows were the published LOSSES (trivial prompts once paid
the envelope for nothing at +$0.20; generation once cost +17%). After the
generation-skip lever (write-code prompts get a ~0.6 KB lean envelope or
none — measured 3.5% of a 723-prompt real corpus) and the entry-point
routing fix, both cells were re-measured at n=3 medians on the same
model: generation flipped to a −26% cost / −18% wall win — input tokens
rise (cache-read-heavy, cheap) while expensive output tokens drop −29%
across more-but-cheaper turns — and the trivial cell is a tie within
noise. Losses are findable because we publish them — and fixable because
the compiler routes them.

**Bug-fixing, ground-truth graded** (a failing test must transition to
passing — no LLM judging): 20 cells of planted bugs with real tracebacks —
10/10 fixed in both arms at −13% dollar cost. Read that honestly: **n=10
cannot establish quality parity** (the 95% interval on 10/10 spans
[72%, 100%]), and the dollar saving comes with **more tokens, not fewer**
on this task class — the envelope shifts spend into cheaper cache reads. No
quality difference was *detected*; the sample has little power to detect one.

**Routing, replayed on 723 real prompts** from live agent sessions: **57%
of envelopes ship pre-executed answers** (L1 probes) — the envelope already
contains the literal answer — and a further ~33% ship structured facts
(context, not the literal answer), at **p50 0.45 s cold / p50 92 ms live**
(warm cache) compile latency, fully local. Zero model calls.

**Eval history by version** — re-measured on every kernel change; losses are
published, attacked, then re-measured. The table below is the summary ledger;
raw per-cell data for the historical runs is retained privately, not in this
repository:

| measured | kernel | what | result |
|---|---|---|---|
| Jun 09 | v13.4 | 41-cell nav/comprehension A/B | turns −83%, tokens −80%, cost −63% |
| Jun 09 | v13.4 | 20-cell ground-truth bugbench | 10/10 both arms (n=10 — no parity claim), $ −13% but tokens up |
| Jun 09 | v13.4 | trivial-prompt cell | **+80% cost — published loss** |
| Jun 09 | v13.4 | generation cell | **+17% cost — published loss** |
| Jun 11 | v13.6 | trivial-prompt cell, re-measured n=3 | tie ($0.21 → $0.22) |
| Jun 11 | v13.6 | generation cell, re-measured n=3 | **−26% cost win** |
| Jun 11 | v13.6 | "biggest cycles" cell, re-measured n=3 | **−89% cost win** ($0.65 → $0.07, 6→1 turns) |
| Jun 11 | v13.6 | 723-prompt routing replay | 57% L1 (answer-shipping) + ~33% facts, p50 0.45 s cold |
| Jul 11 | v13.7 | live dogfood rolling window (separate population, not the replay harness) | cold-compile median 410 ms |

Caveats that always ship with these numbers: trivial prompts the agent
one-shots anyway gain nothing (now a within-noise tie after the lean/skip
levers); cells are n=2–3 with medians and ranges.

<details>
<summary><strong>Benchmark archaeology — runs #1–#4 (May 2026), including the honest negative result that drove the fixes</strong></summary>

Two independent A/B runs at different scales — the larger sample inverts the smaller. Reporting both honestly.

**Run #1 (n=3 per cell, 27 cells, $16.88):** compile appeared to dominate (−29% wall vs static). That static prompt included a `"Hard cap: 4 tool calls"` line that turned out to act as a quota.

**Run #2 (n=3–7 per cell, 78 cells, $54.88, "Hard cap" line removed from static):**

| Condition | Mean turns | Mean wall | Mean cost |
|---|---|---|---|
| vanilla | 7.0 | 33.2s | $0.68 |
| **static / roam_agent** | **5.8** | **25.1s** | **$0.66** |
| compile | 8.2 | 47.9s | $0.78 |

At scale, **static (with the "Hard cap" line removed) is the winner**: −17% turns and −24% wall vs vanilla, with cost within 3%. The compile-mode envelope was **+91% wall vs static on hard structural tasks** — variance probe revealed compile occasionally pushes the agent into over-tool-use (one t1 run hit 41 turns and $2.43). The compile-the-COMMAND itself is robust (250/250 latency cells, 14/15 fuzz, brief mode <300 chars across all 10 procedure families) — the issue is over-direction of the consuming agent, not the compiler.

Private raw cells are retained for audit; the public summary above is the quotable result.

**Run #3 (2026-05-31, n=1, 24 cells, $12.78, on 8-task user-shape corpus after W34→W37 fixes):**

| Condition | Mean turns | Mean wall | Mean cost |
|---|---|---|---|
| vanilla | 6.00 | 28.6s | $0.58 (1 cell timed out at 240s) |
| static / roam_agent | 5.38 | 39.9s | $0.63 |
| **compile** | **2.75** | 35.6s | **$0.46** |

This run inverts Run #2 on a different corpus. Compile **wins 7/8 shapes** including stack-trace, "what does X do", "what changed recently", `compare files`, `who calls X`, file coupling, and trace-flow. The compiler fix wave between Run #2 and Run #3 added six new probes (stack-trace source slice, body-embed for explain, git-log for history, sibling-test embed, path-comparison diff, symbol-pickaxe) and four real bug fixes (callers-backtick fallback, dead-code wrong CLI, consumer-dict flattening, stack-trace classifier missing PascalCase Errors). Headline win: a "what files are coupled to X" task that took vanilla 20 turns / $1.20 / 64s collapsed to compile's 1 turn / $0.32 / 11s — embedded coupling pairs eliminate 19 turns of exploration. The +24% wall vs vanilla is the envelope cache-creation tax at n=1; expected to amortize at n≥3.

Static remains a non-improvement (0/8 wins vs vanilla, 1/8 marginal vs compile). Caveat: Run #3 is n=1 per cell; n=3 replication ($30-40) is pending.

Private per-task tables and raw cells are retained for audit; the public summary above is the quotable result.

**Run #4 (2026-05-31, n=1, 24 cells, $13.00, same corpus after W43→W45 polish/improvements/corrections):**

| Condition | Mean turns | Mean wall | Mean cost |
|---|---|---|---|
| vanilla | 5.25 | 39.6s | $0.63 |
| static / roam_agent | 4.75 | 32.8s | $0.61 |
| **compile** | **1.88** | **25.2s** | **$0.40** |

Compile now **wins 8/8 shapes** and the +24% wall penalty from Run #3 is **gone**: compile is −36% wall vs vanilla. Aggregate **−64% turns / −36% cost / −36% wall** vs vanilla on Opus 4.7. The flip came from three wave-43-to-45 changes: (a) a 60-second bounded cache on `_run_roam` subprocess calls, (b) anti-Read directives in the `stack_trace_fix` and `synthesis_query` answer contracts, and (c) richer enrichment in the `write_pytest` probe (sibling test + source under test + nearest `conftest.py` together). The biggest single delta: `write_pytest` went from 10 vanilla turns to 6 compile turns (−40%, saving $0.29 / cell). Static remains 0/8 wins and should be retired from the default bench-compile conditions in a future release.

Private per-task tables and raw cells are retained for audit; the public summary above is the quotable result.

</details>
</details>

Headless for scripts and CI: `roam compile "<task>" --artifact auto`.
Prefer a dedicated product CLI? The same loop ships as
[**compile-code**](https://github.com/Cranot/compile-code) —
`pip install git+https://github.com/Cranot/compile-code && compile claude`.

### The verify half of the loop — what runs after every edit

The compile half front-loads facts; the verify half reviews what the agent
just changed. `roam verify --auto` scopes to the touched files, auto-selects
the checks that make sense for what changed (Python edits unlock the Python
checks, source edits unlock naming/duplicates), and runs:

- **naming** — against the codebase's own per-language convention (sampled
  from production code only: test/vendored/generated files neither vote nor
  get flagged, framework lifecycle names like `setUp` are never touched)
- **imports** — the hallucination firewall: every import must resolve — to
  the index, the stdlib, or a declared dependency. A module path that
  resolves to nothing fails as a likely hallucination; near-miss names get
  fuzzy did-you-mean candidates
- **error handling / syntax / complexity / cycles / duplicates** — scoped
  structural review with honest disclosure when any sub-check could not run
- **secrets** — a leak gate over every touched file: credential shapes
  (cloud keys, tokens, PEM blocks) fail the check, and an optional
  repo-local `.roam-leak-patterns.py` catalogue catches the strings *your*
  project must never publish
- **patterns** *(advisory, `--deep`)* — the algorithm/idiom catalog scoped
  to the diff: N+1 query shapes, loop-invariant calls, string-concat loops,
  each with the better approach and a fix sketch

**The fix loop.** Wired via `roam hooks claude --write`, findings come back
to the agent as an actionable list — *fix or suppress, then re-verify* — and
the loop re-runs automatically until quiet (bounded rounds). Findings the
agent disagrees with go to `.roam-suppressions.yml`, keyed by **symbol** so
a suppression survives refactors that shift line numbers; the file is
append-only (a suppression is never silently dropped). Everything is
fail-open and quiet-on-pass: the loop surfaces only real findings, and a
broken install can never block a turn.

**Scoping and debt control** — the flags that make verify usable on a
codebase with history:

```bash
roam verify --auto                      # changed files, auto-selected checks
roam verify --diff-only                 # only lines you changed vs HEAD
roam verify --changed-lines cli.py:40-90   # exact ranges (agent harnesses)
roam verify --baseline-write            # snapshot current findings as accepted debt
roam verify --new-only                  # then: only NEW findings fail
roam verify --report --severity fail    # whole-repo ranked punch-list (non-gating)
roam verify --off / --on               # pause / resume the loop repo-wide
```

**The commands that run beside it** in the same post-edit stance:

| Command | Role in the loop |
|---|---|
| `roam verify-imports --path src/roam/cli.py` | The hallucination firewall, standalone — validates every import resolves |
| `roam delete-check --ci` | Gates a deletion diff on surviving references (exit 5 on BREAK-RISK) |
| `git diff \| roam critique` | Clones-not-edited check + blast radius on the patch (exit 5 on high severity) |
| `roam verify --report --persist` | Writes findings to the registry so the **compiler** embeds them as `known_findings` in future envelopes — debt gets fixed opportunistically |

**Measured, not asserted.** The detector quality is pinned by three eval
suites in CI: a planted-issues recall corpus (every category must catch its
canonical positives), a clean-corpus false-positive lock (dogfooded on this
repo: the naming rule alone dropped ~2000 FPs when test files stopped
voting), and an adversarial suppression fuzz suite (suppressions survive
refactors, never lose entries).

---

## What's New

**v13.6 (2026-06-11) — the verify loop grows teeth + compiler injection economics.** A default secrets-leak gate and scoped algorithm sweep run after every edit; suppressions are symbol-keyed so they survive refactors; and the compiler now skips generation-shaped prompts (measured overhead) while ranking retrieval by graph importance. Full notes below · [CHANGELOG.md](CHANGELOG.md).

<details>
<summary><strong>Release notes — v13.6 → v13.0</strong></summary>

**v13.6 (2026-06-11) — The verify loop grows teeth + compiler injection economics.** The post-edit loop now runs a **secrets leak gate** by default (credential shapes + an optional repo-local `.roam-leak-patterns.py` catalogue) and an advisory **algorithm/idiom sweep** scoped to the diff; suppressions are **symbol-keyed** (refactor-proof) and the suppression file is append-only after a confirmed data-loss fix; the naming rule samples production code only (~2000 false positives removed on a test-heavy codebase) and `verify --auto` is **16× faster** on sweeping diffs. The compiler learns injection economics — generation-shaped prompts get **no envelope** (measured pure overhead) — plus graph-ranked retrieval (PageRank + file-role + path-token blend), new answer probes (taint scan, world-model idempotency/side-effects, design patterns, scoped algo findings, and verify findings riding into envelopes as `known_findings`), and routing waves for trace/entry-point phrasings. New offline lock suites (procedure-registry lint, suppression fuzz corpus, self-dogfood FP lock, envelope byte budgets, L1-rate floor) and a `prepush_check.py --release` gate that proves the full CI surface green before any release push. Full diff in [CHANGELOG.md](CHANGELOG.md).

**v13.5 (2026-06-10) — Compiler coverage waves + the Claude Code adapter.** Eight new compile intent procedures land from production-telemetry mining (`file_history` "what changed in X last week", `repo_structure` layers/clusters/health, `entry_point_where` with the authoritative `[project.scripts]` answer, `config_where` env-var lookup, module-name `describe_file` recall, `session_meta`, a zero-probe fast-path for self-contained batch prompts, and a `bug_site_slice` that embeds the source around "fix the bug in cli.py:45"); **`roam hooks claude --write`** wires the full compile-before/verify-after loop into Claude Code in one command (fail-open, idempotent, `--no-verify` / `--uninstall`); two reliability fixes seal a CliRunner stdout-swap race in the in-process probe pool and add a compiler fingerprint to all three compile cache keys; `envelope-diff` regression rules stop false-flagging budget bookkeeping keys. Compiler A/B on Claude (Fable 5): −83% turns / −80% input tokens / −63% cost on nav-comprehension (41 cells). Full diff in [CHANGELOG.md](CHANGELOG.md).

**v13.4 (released 2026-05-21) — Perf wave + Pattern-1 stabilisation + assurance hardening.** Major detector speed-ups (`clones` 43.8s → 13.1s, `intent` 66s → 12s, `doc-staleness` 93s → 19s, `sbom` 30s → 9s — all byte-identical output), 17 commands now emit `isError`/`status` on error envelopes + 11 commands route their argless `--json` path through a proper envelope (Pattern-1C drift-guards added), a persisted per-snapshot spectral gap powering a real `roam forecast` failure budget, MCP prompt-injection marker scan on tool-call egress, release supply-chain hardening (PEP 740 attestations, tag-bound artifacts), and large false-positive cuts in `feature-envy` / `shotgun-surgery` / `god-components`. Full diff in [CHANGELOG.md](CHANGELOG.md).

### v13.3 (released 2026-05-19) — MCP runtime security + UX polish

- **Egress secret-redaction at the MCP wrapper boundary**, 4-mode `policy_decision` enforcement with shadow-mode (`ROAM_MODE_DRY_RUN`), HMAC-linked `McpDecisionReceipt` + `receipt_integrity` verdict on `roam runs verify`.
- **3 new persisting detectors** (`boundary`, `test-hermeticity`, `compatibility`), `roam doctor` advisory-vs-blocking split, and `--json` warnings-channel discipline.

### v13.2 (released 2026-05-16) — Evidence freshness + resolution disclosure

- **Canonical unresolved-path envelopes** across `impact` / `preflight` / `trace` / `test-map` / `context` / `safe-delete` / `split` / `why` — one explicit "not found" shape in JSON mode.
- **Evidence freshness stamped at the producer.** Runs record hashes for `.roam-rules.yml`, `.roam/constitution.yml`, `.roam/control-map.yml`.
- **PR Replay evidence coverage improved.** Replay path maps the 8 evidence questions, fully answers structural change/risk/policy axes, and marks identity/authority/approval evidence as partial, out of scope, or `producer_not_available` instead of silently omitted.

### v13.1 (released 2026-05-15) — Pattern-2 propagation + shared YAML helper + 3 flagship silent-fallback seals

- **3 flagship silent-fallback seals.** `cmd_taint`, `cmd_health`, `cmd_doctor` now emit `state="empty_corpus"` + `partial_success=True` on unanalyzed repos instead of false `Healthy 100/100` / `No taint findings` / `all checks passed` verdicts.
- **Shared YAML config-loader helper** (`load_yaml_with_warnings`). 5 of 7 surveyed loaders migrated; ~125 LOC removed.
- **5 new live smell detectors.** `type-switch`, `speculative-generality`, `empty-catch`, `cross-layer-clone`, `parallel-hierarchy` — `roam smells` now ships 24 deterministic detectors.
- **30+ behavioral Pattern-2 fixes** + empty-corpus smoke sweep across 25+ detectors.

### v13.0 (released 2026-05-13) — Agent-OS substrate + Laravel idioms + Vue SFC

- **Agent-OS control plane.** Repo-local substrates under `.roam/`: constitution, HMAC-chained run ledger, multi-agent leases, portable agent memory, 4 cumulative modes (`read_only` → `safe_edit` → `migration` → `autonomous_pr`).
- **World-model classifiers (R28).** `roam side-effects`, `roam idempotency`, `roam causal-graph`, `roam tx-boundaries`.
- **Laravel dynamic-dispatch idioms.** 7 of 8 implicit-edge idioms (Route closures, Eloquent scopes, Policy resolution, Observer registration, Job/Queue/Artisan dispatch).
- **Vue SFC import graph.** `.vue` template/script/style blocks parsed; component registrations resolved across the SFC boundary.
- **~20 new CLI commands** (`brief`, `next`, `mode`, `constitution`, `laws`, `memory`, `lease`, `runs`, `replay`, `agent-score`, `agents-md`, …) and schema bump (USER_VERSION 12 → 13).

</details>

Full release notes in [CHANGELOG.md](CHANGELOG.md).

## Best for

- **Agent-assisted coding** — structured answers that cut tokens vs raw file exploration
- **Large codebases (100+ files)** — graph queries beat linear search at scale
- **Architecture governance** — health scores, CI quality gates, budget enforcement, fitness functions
- **Safe refactoring** — blast radius, affected tests, pre-change safety checks, graph-level editing
- **Multi-agent orchestration** — partition codebases for parallel agents with conflict-aware planning
- **Security analysis** — vulnerability reachability, auth gaps, CVE path tracing, taint analysis
- **Algorithm optimization** — detect O(n²) loops, N+1 queries, and 32 other anti-patterns with suggested fixes

### When NOT to use Roam

- **Real-time type checking** — use an LSP (pyright, gopls, tsserver). Roam is static and offline.
- **Small scripts (<10 files)** — read the files directly.
- **Pure text search** — ripgrep is faster for raw string matching.

### What's measured vs advisory

Roam's surfaces differ in how rigorously they've been validated — know which is which before you gate on them:

- **Repair-intent retrieval** (`roam retrieve --repair-intent <patch>`) — **the one surface with a preregistered, held-out, stranger-repo result.** Give it the diff of a fix you just made and it reranks toward the *other* files that need the same repair, rather than the files that merely look similar. Measured on 576 real multi-site fixes from 12 third-party repos (rich, aiohttp, httpx, fastapi, click, flask, jinja, werkzeug, pydantic, pytest, attrs, urllib3), frozen before scoring and shipped in-repo:

  | vs plain lexical search | delta | 95% CI (bootstrap, n=2000) |
  |---|---|---|
  | nDCG@10 | **+0.064** (0.605 vs 0.541) | [+0.032, +0.097] |
  | P@3 | **+0.041** | [+0.024, +0.058] |
  | MRR | **+0.059** | [+0.026, +0.092] |
  | recall@10 | +0.034 | [−0.002, +0.070] — **not significant** |

  That clears the preregistered bar (nDCG@10 ≥ +0.05 with a CI excluding zero) and it survived an adversarial falsifier. **Read it for what it is: a real but modest improvement over lexical search on this task — not a step change.** The one striking result underneath: our graph-sibling candidate pool *on its own* scores **0.258**, far *worse* than lexical's 0.541. It only beats lexical once repair-intent reranking is applied. The reranking is not polish on a good pool — it is the reason the pool is usable at all.

  Scope honestly: it needs a real patch as input, and it finds *repair siblings*. It is not a general-purpose search improvement, and recall is not measurably better. This is the only roam surface we would put in front of your codebase without hedging.
- **Reachability triage** (`roam vuln-reach`, `roam sbom`) — the most conservatively designed surface: reachability is derived only from import evidence (import sites and import edges, with file:line), never from symbol-name coincidence, so a CVE with no import evidence reports as unknown rather than reachable. Strong precision by construction; real-CVE recall on unfamiliar repos is still being measured — use it as a high-precision triage signal, and treat "unknown" as unverified rather than safe.
- **Taint packs** (`roam taint`) — validated on synthetic fixtures; real-code recall on arbitrary repositories is low/unmeasured. Treat findings as leads to investigate, not a completeness guarantee; the `--ci` gate is opt-in.
- **Idiom & long-tail detectors** (`roam auth-gaps`, `roam missing-index`, `roam over-fetch`, `roam n1`, framework idioms) — advisory. Blind precision on unfamiliar repos is not yet measured for all of them, and framework idiom detectors that measured low on stranger repos are opt-in (not on the default surface). Review each finding; don't gate CI on these alone.

## Core commands

<!-- BEGIN auto-count:readme-canonical-mention -->
**Lead with the 5 verbs.** The [5 core commands](#core-commands) cover ~80% of agent workflows: `understand`, `context`, `retrieve`, `preflight`, `critique`. The remaining ~274 commands are detail surface for specialised workflows (taint, fleet, cga, oracle, eval, …) — they're called by agents on demand, not memorised. This is intentional design; under the hood the canonical surface is **279 commands (272 canonical + 7 aliases) organised into 7 categories** (aliases for muscle memory: `math` → `algo`, `churn` → `weather`, `digest` / `snapshot` / `trend` → `trends`, `onboard` → `understand`, `refs` → `uses`), but you don't need to know that to start.
<!-- END auto-count:readme-canonical-mention -->

| Verb | What it does |
|------|--------------|
| `roam understand` | Full codebase briefing: stack, architecture, key abstractions, health, conventions, entry points |
| `roam context <symbol>` | AI-optimized context: definition + callers + callees + files-to-read with line ranges |
| `roam retrieve <task>` | Graph-aware context for free-form tasks ("trace login flow", "where is the n+1?") — FTS5 + structural rerank within a token budget |
| `roam preflight <symbol>` | Pre-change safety gate: blast radius + tests + complexity + coupling + fitness |
| `roam critique` | Verify a patch against the graph: clones-not-edited + blast radius + intent vs semantic-diff. Pipe `git diff` in; exit 5 on high severity |

The full surface spans **7 categories** — Getting Started, Daily Workflow, Codebase Health, Architecture, Exploration, Reports & CI, and Refactoring. Run `roam --help` for the 5-verb core, `roam --help-all` for every command name, and `roam surface --json` for the machine-readable inventory. Every command accepts `roam --json <cmd>` for structured output and `roam --sarif <cmd>` for CI integration (SARIF 2.1.0, honoured by 36 commands).

<details>
<!-- BEGIN auto-count:readme-cli-command-list-summary -->
<summary><strong>Full command reference — canonical command list (all 272)</strong></summary>
<!-- END auto-count:readme-cli-command-list-summary -->

The complete, always-current list with flags and examples lives in the [Command Reference](https://roam-code.com/docs/command-reference).

</details>

A few representative commands beyond the core five:

- **Health & architecture:** `roam health` (0-100 score), `roam weather` (churn × complexity hotspots), `roam smells` (24 deterministic detectors), `roam algo` (34-task anti-pattern catalog), `roam clusters` / `roam layers` / `roam cycles`.
- **Change safety:** `roam impact <symbol>` (blast radius), `roam diff` (uncommitted-change blast radius), `roam pr-risk` (0-100 PR risk), `roam diagnose <symbol>` (root-cause ranking).
- **Backend quality:** `roam n1` (N+1 queries), `roam auth-gaps`, `roam missing-index`, `roam over-fetch`, `roam taint` (graph-reach taint, 10 rule packs).
- **Index-aware search:** `roam search <pattern>`, `roam grep <pattern>` (grep + reachability + PageRank), `roam uses <name>` (graph-precise references, no string-literal false positives).
- **Multi-agent:** `roam orchestrate --agents 3` (conflict-aware partitioning), `roam fleet plan`, `roam lease` (parallel-agent coordination).

## Walkthrough

<details>
<summary><strong>10-command walkthrough investigating Flask</strong> (click to expand)</summary>

How you'd use Roam to understand a project you've never seen before, using Flask as an example.

```
$ roam understand
Tech stack: Python (flask, jinja2, werkzeug)
Architecture: Monolithic — 3 layers, 5 clusters
Key abstractions: Flask, Blueprint, Request, Response
Health: 78/100 — 1 god component (Flask)
Entry points: src/flask/__init__.py, src/flask/cli.py

$ roam file src/flask/app.py            # file skeleton: definitions + signatures + health
$ roam deps src/flask/app.py            # what imports this file
$ roam weather                          # hotspots ranked by churn × complexity
$ roam health                           # composite 0-100 + god components / cycles / layer violations
$ roam context Flask                    # AI-ready context: files to read with line ranges
$ roam preflight Flask                  # pre-change gate: blast radius + tests + complexity + fitness
$ roam split src/flask/app.py           # internal symbol groups + extraction suggestions
$ roam why Flask url_for Blueprint      # role classification (Hub/Bridge/Core) + reach + risk
$ roam health --gate                    # CI quality gate (exit 5 on failure)
```

Ten commands. Complete picture: structure, dependencies, hotspots, health, context, safety checks, decomposition, and CI gates.

</details>

## Integration with AI coding tools

Roam is designed to be called by coding agents. Instead of repeatedly grepping and reading files, the agent runs one `roam` command and gets a verdict-first envelope. `roam preflight` (above) replaces grep+read+test-impact+complexity+fitness in one ~3KB call; `roam health` rolls the whole codebase into one score:

```text
$ roam health
VERDICT: Fair codebase (75/100) — 47 critical, 9 warnings, focus: god_components

Health Score: 75/100  |  Tangle: 0.0% (7/33395 symbols in cycles)
Propagation Cost: 0.1%  |  Algebraic Connectivity: 0.0074

Health: 67 issues — 47 CRITICAL, 9 WARNING, 19 INFO
  Breakdown: cycles [1 CRITICAL, 1 WARNING], god [31 CRITICAL, 8 WARNING, 11 INFO], bottlenecks [15 CRITICAL]

Top CRITICAL issues (run `roam --detail health` for the full breakdown):
  cycle (5 symbols): _COMMANDS, complete, _reconstruct_command
  god component: path (prop, degree=2408)
```

*The verdict line works alone — an agent that reads nothing else still knows where to look.* Pipe `--json` for the structured envelope your agent consumes.

**Fastest setup (Claude Code):** wire the compile/verify loop in one command — no config files, no MCP setup, no rules to write:

```bash
roam hooks claude --write           # compile-before + verify-after hooks; --uninstall to undo
```

For other agents (or alongside the hooks), point them at Roam via instructions in their config file:

```bash
roam describe --write               # auto-detects CLAUDE.md, AGENTS.md, .cursor/rules, etc.
roam describe --agent-prompt        # compact ~500-token prompt — copy-paste into an existing config
roam minimap --update               # inject/refresh an annotated codebase minimap (won't touch other content)
```

This teaches the agent which command fits each situation: `roam preflight` before changes, `roam context` for files to read, `roam diagnose` for debugging.

<details>
<summary><strong>Where to put agent instructions for each tool</strong></summary>

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

## MCP Server

Roam includes a [Model Context Protocol](https://modelcontextprotocol.io/) server for direct integration with MCP-aware tools.

```bash
pip install "roam-code[mcp]"
roam mcp
```

<!-- BEGIN auto-count:readme-default-preset -->
**Default preset:** `core` (17 tools: 16 core + `roam_expand_toolset` meta-tool).
<!-- END auto-count:readme-default-preset -->

244 MCP tools span seven selectable presets (`core`, `review`, `refactor`, `debug`, `architecture`, `compliance`, `full`); `core` stays narrow to keep the prompt tight. Most tools are read-only index queries; side-effect tools are explicitly annotated. Set `ROAM_MCP_PRESET=full roam mcp` for the complete toolset.

**Cold-start envelope.** Any wrapper that can't complete normally — missing index, stale index, partial failure — returns one canonical structured envelope (`status`, `error_code`, `summary.verdict`, `hint`, `next_command`) instead of hanging or emitting empty output. Agents always get an actionable signal, never a silent failure.

**MCP runtime security.** Three controls run at the wrapper boundary inside the server, protecting every client even with no gateway present: egress secret-redaction, mode-gated `policy_decision` enforcement (opt-in shadow-mode via `ROAM_MODE_DRY_RUN`), and HMAC-linked decision receipts bound into the signed run ledger. Gateway integrators: see [`dev/MCP-SECURITY-POSTURE.md`](dev/MCP-SECURITY-POSTURE.md).

See [Using Roam via MCP](https://roam-code.com/docs/mcp-usage) for the first-run flow and canonical agent sequence.

<details>
<!-- BEGIN auto-count:readme-mcp-tool-list-summary -->
<summary><strong>MCP tool list (all 244)</strong></summary>
<!-- END auto-count:readme-mcp-tool-list-summary -->

<!-- BEGIN auto-count:readme-mcp-tool-list-table -->
| Tool | Description |
|------|-------------|
| `roam_adrs` | Discover Architecture Decision Records (ADRs) and link them to code modules. Scans well-known ADR directories (``docs/adr/`` / ``architecture/decisions/`` / ...) for markdown files matching ADR naming patterns, parses each ADR's title / status / date / file refs, then cross-references mentioned files against the symbol index. Different from ``roam_doc_staleness`` (inline docstring drift) -- this is the prose-decision-document discoverer. |
| `roam_adversarial` | Frame architectural issues in changed files as challenges the developer must defend: CRITICAL (new cyclic dependencies), HIGH (layer violations, high-confidence anti-patterns), WARNING (cross-cluster coupling, high fan-out), INFO (orphaned symbols). Composes cycles + clusters + layers + catalog + dead + complexity. Different from ``roam_diff`` (blast-radius facts) -- this is the architecture-review framing for code-review agents. |
| `roam_adversarial_review` | Adversarial architecture review: challenges about cycles, anti-patterns, coupling. |
| `roam_affected` | Monorepo impact analysis: find all affected packages/modules from changes. |
| `roam_affected_tests` | List the tests you actually need to run after editing a symbol or file. Use when user asks 'which tests do I run?', 'what tests cover X?', or after Edit/Write. Walks reverse-dependencies with hop distance — closer hops run first. For a full pre-commit check (blast radius + fitness + tests), use roam_prepare_change. |
| `roam_agent_context` | Extract a single agent's partition from the full agent plan: write scope, read-only dependencies, interface contracts, coordination instructions, and key symbols. Different from ``roam_agent_plan`` (full multi-agent view) and ``roam_orchestrate`` (operational dispatch with merge order) -- this is the focused per-worker packet for one agent. |
| `roam_agent_export` | Generate AI agent context file (CLAUDE.md/AGENTS.md/.cursorrules) from index. |
| `roam_agent_opt` | Detect weak agent-contract shape in roam's tool descriptions and envelopes and recommend the stronger shape. |
| `roam_agent_plan` | Decompose partitions into dependency-ordered multi-agent tasks: per-task write scope, read-only dependencies, interface contracts, phase schedule, and merge sequencing. Supports ``plain`` / ``json`` / ``claude-teams`` output formats. Different from ``roam_partition`` (raw analytical manifest) and ``roam_orchestrate`` (operational dispatch) -- this is the dependency-ordered phase schedule. |
| `roam_agent_score` | Aggregate runs from the local ledger and score each agent on a 0..100 composite (run completion, gate adherence, preflight compliance, blast accuracy, replay survival). Empty state (no runs / no matching runs) returns a clean envelope with ``state: "no_data"`` -- never empty stdout, never a crash. Different from ``roam_runs_verify`` (HMAC tamper-detection) -- this is the per-agent quality score across runs. |
| `roam_ai_ratio` | Estimate AI-generated code percentage from git commit heuristics. |
| `roam_ai_readiness` | AI readiness score (0-100): how effectively AI agents can work on this codebase. |
| `roam_alerts` | Active health alerts: thresholds breached on tangle, complexity, churn, or coverage. |
| `roam_algo` | Detect suboptimal algorithms with better alternatives and complexity analysis. |
| `roam_annotate_symbol` | Add persistent annotation to a symbol/file for future agent sessions. |
| `roam_api` | List the public API surface — exported public symbols with signatures and docs. |
| `roam_api_changes` | Detect breaking and non-breaking API changes vs a git ref. |
| `roam_api_drift` | Detect field drift between Laravel/PHP models and TypeScript interfaces. Triggers: 'where do API contracts diverge?', 'find drift between PHP $fillable fields and TypeScript types', 'audit frontend API types'. Pair with roam_endpoints for full route inventory. |
| `roam_architecture_drift` | Compute per-week growth rates for symbols / edges / cycles across a sliding window of persisted ``.roam/snapshots/`` and classify overall direction as ``improving`` / ``degrading`` / ``stable``. Different from ``roam_graph_diff`` (point-in-time delta between two commits) and ``roam_trends`` (metric-level time series) -- this is the snapshot-based architectural-trajectory report. |
| `roam_article_12_check` | Run a 6-item EU AI Act Article 12 readiness checklist over the indexed repo: audit-trail directory, audit-trail records, retention policy doc, technical docs, attestation surface, high-risk classification heuristic. Emits a structured envelope mapping each item to its Article (12, 18, 19) or Annex (III). Different from ``roam_audit_trail_conformance_check`` (per-record chain integrity) -- this is the repo-level governance-readiness assessment. Per the agentic-assurance guardrails: 'maps to' / 'supports evidence for', never 'certifies' / 'makes compliant'. |
| `roam_ask` | Natural-language codebase question dispatcher. Examples: 'is it safe to delete X?', 'where does login validate?', 'what just broke?', 'who owns module Y?'. Routes intent to one recipe in the graph-aware 31-recipe registry. One call replaces Grep+Read for most questions. Run this FIRST when the user asks a code-comprehension question. |
| `roam_at` | Show the code AT a file:line with its enclosing symbol + callers. Targeted alternative to Read-ing the whole file. location is 'file:line'. |
| `roam_attest` | Proof-carrying PR attestation: evidence bundle + merge verdict. |
| `roam_audit` | Run a one-shot codebase architecture audit: bundles health, debt, dead-code, risk, test-pyramid, coverage, and API-surface signals into a single envelope. Designed as the structured artifact a written audit report attaches. Different from ``roam_health`` (single 0-100 score) and ``roam_report`` (preset-driven Markdown report) -- this is the verdict-first audit packet for governance and onboarding. |
| `roam_audit_trail_conformance_check` | Score the audit trail against an EU AI Act Article 12 checklist. |
| `roam_audit_trail_export` | Export the audit trail as markdown / json / csv for procurement review. |
| `roam_audit_trail_verify` | Verify SHA-256 chain integrity of a roam audit trail. |
| `roam_auth_gaps` | Find endpoints lacking auth / authorization checks ranked by confidence. Triggers: 'which routes are unprotected?', 'show me auth gaps', 'audit handler protection'. Pair with roam_taint for taint-source reachability over the unprotected surfaces. |
| `roam_batch_get` | Get details for up to 50 symbols in one call. Replaces 50 sequential roam_symbol calls. |
| `roam_batch_search` | Search up to 10 patterns in one call. Replaces 10 sequential roam_search_symbol calls. |
| `roam_bisect_blame` | Find snapshots that caused architectural degradation, ranked by impact. |
| `roam_boundary` | Surface public-by-accident exports + changed-range layer violations. Two closed-enum kinds: public_by_accident (warning, _-prefixed name in __all__) and wrong_direction_import (high, lower-layer module imports from higher-layer caller). |
| `roam_breaking_changes` | Detect breaking API changes between git refs: removed exports, changed signatures. |
| `roam_brief` | Compose a one-page agent briefing covering five sections: ``next`` (what ``roam next`` would recommend), ``highlights`` (stack / top danger zones / top mined laws from ``roam agents-md``), ``pr_bundle`` (current PR-bundle status on the active branch), ``mode`` (active agent mode and its allow-list size), and ``runs`` (the N most-recent runs from the ledger). Designed as the FIRST command an agent runs when joining a roam-indexed repo. Different from ``roam_next`` (single-command router) -- this is the verdict-first session kickoff packet. |
| `roam_budget_check` | Check changes against architectural budgets (cycles, health floor, complexity). |
| `roam_bus_factor` | Score knowledge-concentration risk per directory: Shannon entropy over unique authors, primary-author share, last activity, and a staleness factor. Flags CRITICAL / HIGH / MEDIUM / LOW per module. Different from ``roam_owner`` (per-file blame) and ``roam_congestion`` (too-many-authors merge-conflict risk) -- this measures knowledge-loss risk. |
| `roam_capsule_export` | Sanitized structural graph export without code bodies (privacy-safe). |
| `roam_catalog` | Return the full machine-readable list of every roam MCP tool currently registered, including title, description, and capability flags (core / read_only / destructive). Use this once at session start to discover what's available without enumerating tools. |
| `roam_causal_graph` | Build per-symbol causal graphs: edges from inputs (parameters / globals / env reads) to sinks (side-effecting calls / return / raise / mutation). Six causal kinds: ``param_to_effect``, ``param_to_return``, ``global_to_effect``, ``global_to_mutation``, ``env_to_effect``, ``param_to_raise``. Heuristic line-level text scan -- false negatives expected. Different from ``roam_taint`` (cross-symbol taint propagation) -- this is intra-symbol dataflow only. |
| `roam_cga_emit` | Emit a Code Graph Attestation — in-toto v1 statement with predicate type `https://roam-code.com/spec/CodeGraph/v1` (or `https://roam-code.com/spec/CodeGraph-AIBOM/v1` with --aibom). Merkle root over symbol fingerprints + edge-bundle digest. Optional cosign keyless or offline signing. |
| `roam_cga_verify` | Verify a Code Graph Attestation — re-derives the Merkle root + edge-bundle digest from the live DB and compares to the bundled predicate, AND verifies the cosign signature on the sibling `.bundle`. Fails closed (exit 5) when no bundle is present unless no_cosign=True is passed to acknowledge predicate-only verification. |
| `roam_changelog` | List commits since last tag, optionally formatted as a markdown CHANGELOG draft. |
| `roam_check_rules` | Run 10 built-in structural rules: cycles, fan-out, complexity, tests, god classes, layer violations. |
| `roam_clean` | Remove orphaned index entries (files deleted from disk) without full rebuild. |
| `roam_clones` | Detect near-duplicate code via AST structural hashing (Type-2 clones). |
| `roam_closure` | Minimal set of changes needed for rename/delete/modify (exact files + lines). |
| `roam_clusters` | Show Louvain code clusters and directory mismatches. Returns per-cluster size, cohesion, conductance, modularity Q, mega-cluster sub-group breakdowns, and inter-cluster coupling. Different from ``roam_layers`` (dependency-layer violations) -- this groups by community detection, not by topological depth. |
| `roam_codeowners` | CODEOWNERS coverage, ownership distribution, unowned files, drift detection. |
| `roam_commands` | List the repo's own runnable build/test/lint commands, classified by kind/scope/cost with evidence. |
| `roam_compare` | Diff two roam indices structurally: reports symbols added/removed/moved, per-file complexity deltas above a threshold, language counts, and a one-line health verdict (improved / regressed / sideways). Different from ``roam_graph_diff`` (commit-range graph delta from one index) -- this is the cross-index structural delta for release-vs-release comparisons. |
| `roam_compatibility` | Detect outbound surface regressions vs a baseline snapshot. Closed-enum verdicts: no regressions / surface additions / surface drift / breaking changes. Compares commands, flags, envelope summary fields, MCP tools, and preset counts. Capture the baseline via CLI: roam compatibility --write-baseline PATH. |
| `roam_compile` | Compile a freeform coding task into a structured envelope an AI agent can consume. Returns the ArtifactSelector verdict (facts / lean / full envelope) plus the deterministic plan. Empirically validated on Opus 4.8 (2026-05-28): FactsEnvelope delivers 99% of vanilla quality at 54% of vanilla cost. Different from roam_plan (symbol-centric execution plan) -- this is the freeform-task compiler. |
| `roam_complete` | Prefix completion for symbols / file paths / commands. Faster than search; returns just names. |
| `roam_complexity_report` | Functions ranked by cognitive complexity above threshold. |
| `roam_congestion` | Detect developer congestion: files with too many concurrent authors within a sliding time window. Combines author count, churn intensity, and complexity into a congestion score that predicts merge conflicts and coordination failures. Different from ``roam_bus_factor`` (knowledge-loss risk) and ``roam_owner`` (per-file blame breakdown) -- this measures too-many-cooks contention. |
| `roam_context` | Get the minimum files + line ranges needed to understand or modify a symbol. Use when user says 'show me X', 'I need to change Y', 'how does Z work?'. Returns targeted reads ranked by PageRank — cheaper than Read'ing whole files. For pre-change safety (blast radius + tests + effects), use roam_prepare_change instead. |
| `roam_conventions` | Auto-detect codebase naming, file, import, and export conventions with outliers. |
| `roam_coupling` | Use for: 'what files change together?' / 'find hidden coupling not visible in imports' / 'which sibling file should I also update?'. Pick over reading git log manually — surfaces co-change partners the call graph misses. Use roam_fan for structural connectivity, roam_dark_matter for the latent variant. |
| `roam_coverage_gaps` | Find unprotected entry points: top-level exported functions / methods that have no call-graph path to a required gate symbol (auth / permission / validation). Supports exact gate names, regex patterns, framework presets (python / javascript / go / java-maven / rust), and a ``.roam-gates.yml`` sidecar config. Different from ``roam_auth_gaps`` (PHP/Laravel source analysis) and ``roam_test_gaps`` (untested symbols in changed files) -- this walks the call graph to verify every entry reaches a required gate. |
| `roam_critique` | Post-edit patch verifier. Pass `git diff` output as diff_text. Catches clones-not-edited (sibling duplicates the agent missed) and high-blast-radius edits. Grounded in the indexed graph, not heuristics. Triggers: 'review my patch', 'is this PR safe?', after generating any non-trivial diff. |
| `roam_cut` | Find fragile domain boundaries via minimum-cut analysis. Computes the thinnest edge cuts between architectural clusters and the highest-impact 'leak edges' whose removal would best improve domain isolation. Different from ``roam_split`` (decomposes a single file) -- this finds boundaries between clusters. |
| `roam_cut_analysis` | Minimum cut analysis: fragile domain boundaries, highest-impact leak edges. |
| `roam_cycles` | Show import/call cycles (Tarjan strongly-connected components) of the symbol graph. Returns per-cycle size, member files/symbols, and an `actionable` flag (spans >=2 distinct non-test files). The focused counterpart to the cycles section of ``roam_health``; sibling of ``roam_clusters`` / ``roam_layers``. |
| `roam_dark_matter` | File pairs that co-change without structural links (hidden coupling). |
| `roam_dashboard` | Unified single-screen codebase status: health, hotspots, bus factor, dead code, AI rot. |
| `roam_dead_code` | Use for: 'what can I safely delete?' / 'find dead code' / 'list unused exports'. Pick over manual grep sweeps — filters out entry points and framework lifecycle hooks, ranks candidates by deletion safety. Pair with roam_safe_delete for per-symbol deletion verdicts. |
| `roam_debt` | Rank files by tech-debt score with SQALE remediation-cost estimates. Triggers: 'where's the worst debt?', 'what should we refactor next?', 'estimate cleanup cost'. Pair with roam_complexity_report for per-function brain-method targeting. |
| `roam_delete_check` | Gate the diff (working / staged / PR / HEAD) on surviving references to deleted symbols and files. Per-deletion verdict: SAFE (no surviving references), LIKELY-SAFE (survivors only in tests / docs / unreachable code), or BREAK-RISK (survivors in reachable code). Different from ``roam_critique`` (PR-wide diff review) -- this targets the deletion surface specifically with CI-gate semantics (overall BREAK-RISK trips the gate). |
| `roam_deps` | Use for: 'what does file X import?' / 'which files depend on module Y?' / 'show me the importers of Z'. Pick this for file/module-level coupling before refactors; symbol-level lookups belong in roam_uses. Set multi=True to get imports + importers + git co-change coupling in ONE envelope (do this instead of shelling out to `roam deps --multi` or hand-querying the index). Run in parallel with roam_coupling for the biggest token win. |
| `roam_describe` | Auto-generate a project description for AI coding agents: multi-section Markdown report covering overview, directories, entry points, key abstractions, architecture, and testing. Different from ``roam_understand`` (compact codebase overview) -- this is the comprehensive prose description for CLAUDE.md / AGENTS.md / .cursor/rules. The wrapper emits to stdout; on-disk writes are deferred to the CLI (``roam describe --write``) so the MCP surface stays read-only. |
| `roam_dev_profile` | Developer behavioral profiling: commit time patterns, change scatter (Gini), burst detection. |
| `roam_diagnose` | Root cause analysis: upstream/downstream suspects ranked by composite risk. |
| `roam_diagnose_issue` | Root-cause triage for a failing symbol. Pass the suspect symbol. Ranks upstream / downstream callers by risk + lists side effects + transactional boundaries. Replaces manual call-graph Grep+Read. Triggers: 'X is broken', 'test Y fails', 'why does Z return null?'. |
| `roam_diff` | Show the blast radius of your edits BEFORE you commit. Run after Edit/Write tools to see affected symbols, files, tests, plus coupling and fitness warnings. Use when user asks 'what did my change break?', 'safe to commit?'. Replaces ad-hoc `git diff --stat` inspection with graph-aware impact data. For PR-level risk verdict, use roam_pr_risk. |
| `roam_disambiguate` | List every symbol matching a name with file/line/kind/signature/PageRank — pick the right overload. |
| `roam_doc_intent` | Link documentation to code: find drift, dead refs, undocumented symbols. |
| `roam_doc_staleness` | Run a semantic docstring-drift audit: flag documented parameters, returns, or raises that no longer match code. Pass ``include_prose_drift`` to include optional blame-only summary drift. Different from ``roam_docs_coverage`` (missing docs ranked by PageRank) and ``roam_stale_refs`` (dangling doc links) -- this audits concrete claims in existing docs. |
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
| `roam_expand_toolset` | List available tool presets or show contents of a preset. Presets: core (minimal), review, refactor, debug, architecture, compliance, compile-curated, and full (every tool). Pass a preset name to list its tools. |
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
| `roam_full_coupling` | Composite coupling report for ONE file in a single envelope: top-N temporal coupling pairs touching the file + structural imports/importers + top-N file symbols. Use instead of chaining roam_coupling + roam_deps + roam_file_info. |
| `roam_generate_plan` | Structured execution plan for code modification: read order, invariants, tests. |
| `roam_get_annotations` | Read annotations for symbols, files, or project. Filter by tag/date. |
| `roam_get_invariants` | Implicit contracts for symbols: signature stability, usage spread, breaking risk. |
| `roam_graph_diff` | Show the structural graph delta between two snapshots. Surfaces new / removed symbols, edge churn, degree shifts, new cycles, layer migrations, and likely renames. Reads persisted snapshots from ``.roam/snapshots/`` -- capture one with ``--save-snapshot``. |
| `roam_graph_stats` | Report graph-level invariants: density, connected components, average in/out degree, top in-degree symbols, and approximate diameter. One overview number for 'how dense, connected, and cyclic is this codebase'. |
| `roam_grep` | Run index-aware grep across the codebase. Returns matches with their enclosing symbol, reachability badge, PageRank, clone-class, and bridge annotations. Supports multi-pattern, source-only / test-only filters, reachable-from / unreachable filters, co-occurrence across patterns, and rank-by importance. |
| `roam_guard` | Check breaking-change risk for a symbol before editing: 0..100 risk score with component breakdown (blast radius, complexity, centrality, test gap, layer analysis) plus caller / callee lists and covering tests -- all within a ~2K-token budget. Different from ``roam_preflight`` (file / staged / coupling / convention / fitness composite) -- this is the per-symbol quantified risk score for sub-agent dispatch. |
| `roam_guard_clean` | Prune the verdict log at `.roam/verdict-log.jsonl` to its last N entries (default 500). Atomic rewrite — concurrent appenders never see a partial file. Pair `dry_run=True` for a probe. |
| `roam_guard_diff` | Verdict diff between two bundle snapshots (or the two most-recent verdict-log entries via `from_log=True`). Returns the verdict delta + reasons added/resolved + file/check counts. Answers 'did my last commit help?' |
| `roam_guard_doctor` | Roam Guard preflight: 8 health checks (.roam dir, bundles, rule pack, command graph, git, GitHub token, verdict log, yaml lib). Run once before adopting Roam Guard in CI. |
| `roam_guard_history` | List past Roam Guard verdicts on this repo (reads `.roam/verdict-log.jsonl` fast-path when present, falls back to scanning `.roam/pr-bundles/`). Supports `--verdict` and `--limit` filters. |
| `roam_guard_pr` | Aggregate Roam Guard PR check: auto-collect bundle, compose AgentChangeProofBundle v1, render verdict (pass/pass_with_warnings/needs_review/blocked), optionally POST a GitHub Check Run. The headline tool — drop this into a CI step to gate any PR. |
| `roam_guard_rules` | Inspect or validate a Roam Guard rule pack. Subcommands: `show` (default) renders the pack, `validate` checks schema, `test` matches a path against the pack. |
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
| `roam_migration_safety` | Detect non-idempotent database migrations unsafe to re-run. Triggers: 'audit migration safety', 'find non-idempotent migrations', 'which DDL would break on replay?'. Pair with roam_tx_boundaries for transaction-correctness analysis. |
| `roam_minimap` | Generate a compact ~20-line codebase minimap for CLAUDE.md injection: tech stack, annotated directory tree, key symbols by PageRank, high-fan-in symbols to avoid, hotspots, detected conventions. Different from ``roam_describe`` (long-form prose) and ``roam_map`` (structured skeleton) -- this is the sentinel-block one-pager. The wrapper emits to stdout; on-disk updates are deferred to the CLI (``roam minimap --update`` / ``--init-notes``) so the MCP surface stays read-only. |
| `roam_missing_index` | Detect queries hitting non-indexed columns flagged as slow-query risks. Triggers: 'find slow queries', 'audit database indexes', 'where are the N+1 candidates?'. Pair with roam_n1 for per-property iteration patterns. |
| `roam_module` | Show directory contents: exported symbols, signatures, external imports / importers, internal cohesion percentage, and API surface ratio. Different from ``roam_describe`` (project-wide) -- this analyses a single directory. |
| `roam_mutate` | Agentic editing: move/rename/add-call/extract symbols with auto-import rewrite. |
| `roam_n1` | Detect N+1 I/O patterns in ORM code (Laravel/Django/Rails/SQLAlchemy/JPA). |
| `roam_next` | Suggest the next ``roam`` command based on cheap repo-state signals: index presence, staleness, working-tree dirtiness, recent envelope, and recent memory. Emits one imperative recommendation in <200ms. Different from ``roam_brief`` (multi-section session kickoff) and ``roam_workflow`` (curated multi-step recipes) -- this is the single-command router. |
| `roam_observability_opt` | Detect code that leaves systems hard to debug (raw debug prints, ...) and recommend the structured-logging shape. |
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
| `roam_orphan_routes` | Find backend routes lacking a frontend consumer — the dead-endpoint surface. Triggers: 'which routes can we delete?', 'find unused endpoints', 'audit API surface coverage'. Pair with roam_dead_code for symbol-level dead-export detection. |
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
| `roam_prepare_change` | Pre-change safety gate. Run before any non-trivial edit — returns blast radius, affected tests, and fitness gates. |
| `roam_proof_bundle` | Compose AgentChangeProofBundle v1 from the active pr-bundle. Returns the structured verdict envelope an agent can attach to a PR. Supports markdown / json / sarif output formats. |
| `roam_py_modern` | Python modernisation signal: walrus, match, PEP 604/585, f-strings vs legacy. |
| `roam_py_types` | Python type-annotation health: % public fns fully typed, Any usage, legacy typing. |
| `roam_pytest_fixtures` | pytest fixture chain: top fixtures by dependent count, or per-symbol dependency walk. |
| `roam_reachability_triage` | Classify vulnerability-flow findings as reachable or not reachable from entrypoints through local call-graph evidence. This MCP tool is read-only: it does not write or move the reachability baseline; use the CLI for baseline management. |
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
| `roam_search_symbol` | Use for: 'where is X defined?' / 'find function Y' / 'locate class Z'. Pick over Bash grep for function/class/method lookups — PageRank-ranked file:line + qualified names, no string/comment false positives. For 3+ symbols use roam_batch_search; for callers use roam_uses. |
| `roam_secrets` | Scan for hardcoded secrets, API keys, tokens, passwords (25 patterns). |
| `roam_semantic_diff` | Structural change summary: what symbols were added/removed/modified. |
| `roam_session_metrics` | Local-only telemetry: per-tool invocation counts grouped by outcome (success / rate_limited / error). Helps answer "which tools are agents actually using?" and "which tools are dead weight?". Never phones home — counters live in the MCP server process and reset on restart. |
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
| `roam_test_hermeticity` | Detect non-hermetic test patterns that cause CI flakiness. Six closed-enum kinds: network, time, random, filesystem, env, subprocess. AST-driven (not regex) with module-level suppression for monkeypatch / freezegun / responses / random.seed. |
| `roam_test_impact` | Tests transitively reachable from changed symbols — sharper scope than affected_tests. |
| `roam_test_map` | Map a symbol or file to its current test coverage: direct test edges (test file calls the symbol), file-level importers (test file imports the symbol's module), and convention-based matches (Salesforce ``<Name>Test`` / ``<Name>_Test`` classes). Different from ``roam_test_gaps`` (untested symbols in changed files) and ``roam_affected_tests`` (forward trace from changes to affected tests) -- this is the lookup for what currently exercises a given symbol. |
| `roam_test_pyramid` | Count indexed test files by kind (unit / integration / e2e / smoke / unknown) using path and name conventions, and flag inverted pyramids (when ``e2e + integration > unit``). Different from ``roam_test_gaps`` (missing coverage) -- this measures the shape of the existing test suite for slow-CI risk. |
| `roam_test_scaffold` | Generate a test-file skeleton for a source file or symbol (functions, classes, methods) with the right imports and per-symbol stub blocks. Supports pytest / unittest (Python), jest / mocha / vitest (JS/TS), Go testing, JUnit4 / JUnit5 (Java), and RSpec / Minitest (Ruby). Dry-run by default; pair with ``roam_test_map`` first to confirm no existing coverage. Skips symbols that already have tests in the target file. |
| `roam_timeline` | Chronological commits that touched the file owning a symbol — author, date, lines added/removed. |
| `roam_tour` | Codebase onboarding guide: reading order, entry points, architecture roles. |
| `roam_trace` | Shortest dependency path between two symbols with hop details. |
| `roam_trends` | Historical metric tracking: record and query health metric trends over time. |
| `roam_tx_boundaries` | Classify functions by transactional safety: ``transactional`` (begin matched by commit/rollback, all mutations inside scope), ``partial_transactional`` (mutations both inside AND outside scope), ``unsafe_mutation`` (mutations OUTSIDE any transaction wrapper -- latent bug), ``unmatched_begin`` (begin without commit/rollback -- leak), ``unmatched_commit``, ``non_transactional``, or ``unknown``. Composes on top of ``roam_side_effects``. Different from ``roam_idempotency`` (retry safety) -- this gates transaction correctness. |
| `roam_understand` | Codebase briefing in one call. Returns stack + architecture layers + entry points + hotspots + conventions in ~2-4K tokens. Triggers: 'what is this repo?', 'where do I start?', 'give me the lay of the land'. Run this FIRST in an unfamiliar repo — Glob/Grep around comes later. |
| `roam_uses` | Use for: 'who calls X?' / 'where is Y referenced?' / 'what breaks if I rename Z?'. Pick over multi-pattern grep — graph-resolved callers, importers, and subclasses grouped by edge type, zero comment/string-literal false positives. For 3+ symbols use roam_batch_get; for counts only, roam_impact. |
| `roam_validate_plan` | Pre-apply validator for a multi-step change plan. Returns blockers, warnings, advice per operation. |
| `roam_verdict` | Compute a closed-enum verdict (pass / pass_with_warnings / needs_review / blocked) from the active pr-bundle. Pure judgment layer — no rendering, no log, no GH POST. |
| `roam_verification_contract` | Compute the minimal `{required, skipped}` verification set for the current changed_files × risk × mode × policy. Surfaces what an agent MUST run before its PR can pass. |
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
| `roam_x_lang` | Show cross-language symbol bridges: Protobuf .proto -> generated Go/Java/Python stubs, Salesforce Apex -> Aura/LWC/Visualforce, REST API frontend -> backend route, template variable -> source, and env-var read -> .env definition. Call this tool to list every registered bridge type. |
<!-- END auto-count:readme-mcp-tool-list-table -->

</details>

<!-- BEGIN auto-count:readme-mcp-core-preset-tools -->
Core preset tools: `roam_alerts`, `roam_ask`, `roam_batch_search`, `roam_coupling`, `roam_dead_code`, `roam_deps`, `roam_diagnose_issue`, `roam_fetch_handle`, `roam_file_info`, `roam_grep`, `roam_metrics`, `roam_prepare_change`, `roam_search_symbol`, `roam_taint`, `roam_understand`, `roam_uses`.
<!-- END auto-count:readme-mcp-core-preset-tools -->

<details>
<summary><strong>MCP client setup (Claude Code / Claude Desktop / Cursor / VS Code)</strong></summary>

**Claude Code:** `claude mcp add roam-code -- roam mcp`, or add to `.mcp.json`:

```json
{ "mcpServers": { "roam-code": { "command": "roam", "args": ["mcp"] } } }
```

**Claude Desktop** — add to `claude_desktop_config.json` (include `"cwd": "/path/to/your/project"`).

**Cursor** — add the same `mcpServers` block to `.cursor/mcp.json`.

**VS Code + Copilot** — add to `.vscode/mcp.json` under a `servers` key with `"type": "stdio"`.

</details>

## Go deeper

Pick the path that matches your role:

- **5-min demo (CTO/CISO/dev-tools-lead):** [The Canonical Demo](https://roam-code.com/docs/canonical-demo) — install → health → preflight → critique → signed `ChangeEvidence` packet, five commands, no laptop egress.
- **Developer tutorial (15 min):** [Getting Started](https://roam-code.com/docs/getting-started) — install, index, query, ship.
- **Agent integration:** `roam mcp-setup claude-code` (or `cursor`, `continue`) — then [Using Roam via MCP](https://roam-code.com/docs/mcp-usage) for the cold-start envelope and canonical agent loop.
- **Full surface:** [Command Reference](https://roam-code.com/docs/command-reference) — every command, flag, and JSON envelope.
- **Architecture:** [How it fits together](https://roam-code.com/docs/architecture) — graph, findings registry, run ledger, evidence compiler.

## CI/CD integration

All you need is Python 3.10+ and `pip install roam-code`.

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

`roam init` auto-generates this workflow. The Action accepts `commands`, `gate` (quality-gate expression, exit 5 on failure), `sarif` (upload to GitHub Code Scanning), `comment` (sticky PR comment), `cache`, and `changed-only` (incremental mode).

**SARIF output.** 36 commands honour the global `--sarif` flag (health, complexity, dead, smells, clones, vulns, taint, secrets, n1, …). Minimal upload:

```yaml
- run: roam --sarif health > roam-health.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: roam-health.sarif
```

For GitLab / Jenkins / Azure / Bitbucket templates, severity gates, and upload guardrails, see [docs/ci-integration.md](docs/ci-integration.md).

## Roam Guard for PRs

`roam guard-pr` is the one-call CI gate that emits an **Agent Change Proof Bundle v1** + closed-enum verdict (`pass` / `pass_with_warnings` / `needs_review` / `blocked`) for the current PR. Every fact carries evidence — what changed, which checks were required, which ran, why the verdict landed where it did.

```bash
# Local — show the markdown verdict for your current branch's pr-bundle.
roam guard-pr --format markdown

# CI — one line; --ci is shorthand for --strict + --init-if-missing + markdown.
roam guard-pr --ci --output guard.md

# CI — post to GitHub Check Runs (works with the default GITHUB_TOKEN).
roam guard-pr --post-check --gh-repo $REPO --gh-sha $SHA
```

**Example reviewer markdown:**

```markdown
## 🛑 Roam Guard verdict: `blocked`

> **0** of **4** required checks ran. **4** missing. Risk: `low`.

### Verdict reasons
- `required_checks_not_run` (×4) — `because=config_file_changed`
  - `lint.make.lint` (detail=['.mcp.json'])
  - `test.make.test` (detail=['.mcp.json'])

### Verification checks
| Status | Command | Why |
|---|---|---|
| 🛑 missing | `lint.make.lint` | config_file_changed |
| 🛑 missing | `test.make.test` | config_file_changed |
```

**Verdict → CI exit + GitHub conclusion map:**

| Roam verdict | Exit code | GitHub conclusion | Build status |
|---|---|---|---|
| `pass` | 0 | `success` | ✅ green |
| `pass_with_warnings` | 0 (4 with `--strict`) | `neutral` | 🟡 yellow |
| `needs_review` | 4 | `action_required` | 🟠 attention |
| `blocked` | 5 | `failure` | 🛑 red |

**Output formats:** `text` (default), `markdown` (PR comment / GH Check), `json` (the full AgentChangeProofBundle v1), `sarif` (GitHub Code Scanning / GitLab SAST / Defender).

**Pluggable rule packs.** The verification contract (what counts as a required check for a given change) lives in YAML, not code. Default pack ships with the binary; override with `roam guard-pr --rules templates/examples/roam-guard-rules.default.yml`:

```yaml
name: my-repo
extends: default
file_patterns:
  - id: api_schema_changed
    regex: '^src/api/.*\.proto$'
    applies_to_kinds: [test, build]
```

**JSON Schema** for the v1 bundle ships at `src/roam/schemas/agent_change_proof_bundle.v1.json`. Validate any bundle with `roam proof-bundle --validate`.

**See also:**
- [templates/examples/roam-guard-pr.github-actions.yml](templates/examples/roam-guard-pr.github-actions.yml) — drop-in GHA workflow
- [templates/examples/roam-guard-pr.README.md](templates/examples/roam-guard-pr.README.md) — full adoption guide
- [templates/examples/roam-guard-rules.default.yml](templates/examples/roam-guard-rules.default.yml) — default rule pack

## Paid layers (free CLI stays Apache 2.0)

The CLI is Apache 2.0, fully local, zero-API-key, and never expires. Three optional paid layers build on the same engine:

- **Roam Review** — hosted PR bot for AI-generated changes, built on `roam pr-analyze`. CodeRabbit/Greptile review PR *semantics*; Roam Review reads the *graph* (who calls the changed symbol, which layer it sits in) and emits a portable `ChangeEvidence` packet. The CLI engine is a working CI gate today: `git diff main..HEAD | roam pr-analyze --gate` (exit 5 on `BLOCK`).
- **Roam Cloud** — opt-in metrics history with no source upload. `roam metrics-push` sends a summary-only payload (numerical metrics, paths or SHA-256 hashes, identifier names) — never source-code bodies. Inspect the exact payload with `--dry-run`.
- **PR Replay** — one-shot paid audit of your last 30/90 merged PRs: a written structural-review report plus a founder walk-through. Free DIY sample via `roam pr-replay --tier sample`.

Early access — email [hello@roam-code.com](mailto:hello@roam-code.com). Full pricing at <https://roam-code.com/pricing>.

## Language Support

### Tier 1 — Full extraction (dedicated parsers)

| Language | Extensions | Symbols | References | Inheritance |
|----------|-----------|---------|------------|-------------|
| Python | `.py` `.pyi` | classes, functions, methods, decorators, variables | imports, calls, inheritance | extends, `__all__` exports |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | classes, functions, arrow functions, CJS exports | imports, require(), calls | extends |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | interfaces, type aliases, enums + all JS | imports, calls, type refs | extends, implements |
| Java | `.java` | classes, interfaces, enums, constructors, fields | imports, calls | extends, implements |
| Go | `.go` | structs, interfaces, functions, methods, fields | imports, calls | embedded structs |
| Rust | `.rs` | structs, traits, impls, enums, functions | use, calls | impl Trait for Struct |
| C / C++ | `.c` `.h` `.cpp` `.hpp` `.cc` | structs, classes, functions, namespaces, templates | includes, calls | extends |
| C# | `.cs` | classes, interfaces, structs, enums, records, methods, properties, delegates, events | using directives, calls, `new`, attributes | extends, implements |
| PHP | `.php` | classes, interfaces, traits, enums, methods, properties | namespace use, calls, static calls, `new` | extends, implements, use (traits) |
| Ruby | `.rb` | classes, modules, methods, singleton methods, constants | require, require_relative, include/extend, calls | class inheritance |
| Kotlin | `.kt` `.kts` | classes, interfaces, enums, objects, functions, methods, properties | imports, calls, type refs | extends, implements |
| Scala | `.scala` `.sc` | classes, traits, objects, case classes, functions, val/var, type aliases | imports, calls, `new` | extends, with (trait mixins) |
| Swift | `.swift` | classes, structs, enums, protocols, functions, methods, properties | imports, calls, type refs | extends, conforms |
| Dart | `.dart` | classes, mixins, extensions, enums, type aliases, functions, methods, constructors | imports, calls, type refs | extends, implements, with |
| Visual FoxPro | `.prg` | functions, procedures, classes, methods, properties, constants | DO, SET PROCEDURE/CLASSLIB, CREATEOBJECT, `obj.method()` | DEFINE CLASS ... AS |
| SQL (DDL) | `.sql` | tables, columns, views, functions, triggers, schemas, types, sequences | foreign keys, view table deps, trigger refs | — |
| YAML (CI/CD) | `.yml` `.yaml` | GitLab CI jobs/anchors, GitHub Actions workflows/jobs, generic top-level keys | `extends:`, `needs:`, `!reference`, `uses:` | — |
| HCL / Terraform | `.tf` `.tfvars` `.hcl` | `resource`, `data`, `variable`, `output`, `module`, `provider`, `locals` | `var.*`, `module.*`, `data.*`, `local.*` | — |
| Vue / Svelte | `.vue` `.svelte` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |

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

Tier 2 languages (and `.jsonc` / `.mdx`) get basic symbol extraction via a generic tree-sitter walker.

## Performance

| Metric | Value |
|--------|-------|
| Index 200 files | ~3-5s |
| Index 3,000 files | ~2 min |
| Incremental (no changes) | <1s |
| Any query command | <0.5s |

After the first full index, `roam index` only re-processes changed files (mtime + SHA-256 hash). Detailed indexing benchmarks across Express / Axios / Vue / Laravel / Svelte live in [`benchmarks/`](benchmarks/).

Compiler A/B results, the per-task gallery, routing stats, and the
version-keyed eval history live in [The Compiler](#the-compiler--your-agents-first-token-already-knows-the-answer)
section — one home, no duplicate numbers.

## How It Works

```
Codebase
    |
[1] Discovery ──── git ls-files (respects .gitignore + .roamignore)
[2] Parse ──────── tree-sitter AST per file (28 languages)
[3] Extract ────── symbols + references (calls, imports, inheritance)
[4] Resolve ────── match references to definitions → edges
[5] Metrics ────── adaptive PageRank, betweenness, cognitive complexity, Halstead
[6] Algorithms ── 34-task anti-pattern catalog (O(n^2) loops, N+1, recursion, async)
[7] Git ────────── churn, co-change matrix, authorship, Renyi entropy
[8] Clusters ───── Louvain community detection
[9] Health ─────── per-file scores (7-factor) + composite score (0-100)
[10] Store ─────── .roam/index.db (SQLite, WAL mode)
```

Exclude paths with a `.roamignore` file (full gitignore syntax) or `roam config --exclude "*.proto"`. For the graph algorithms (Personalized PageRank for blast radius, Tarjan SCC, Louvain, Fiedler bisection, Mann-Kendall trend detection, …) and the weighted-geometric-mean health score, see the [Architecture guide](https://roam-code.com/docs/architecture).

## How Roam Compares

roam-code combines graph algorithms (PageRank, Tarjan SCC, Louvain clustering), git archaeology, architecture simulation, and multi-agent partitioning in a single local CLI with zero API keys.

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
| MCP tools for agents | 244 (16 in default core preset) | Client only | Client only | 34 (SonarQube) |
| Languages | 28 | 70+ | 50+ | 12-42 |
| 100% local, zero API keys | Yes | No | No | Partial |
| Open source | Apache 2.0 | No | Partial | Partial |
| Interprocedural taint depth | shallow (OpenVEX-shaped) | n/a | n/a | **deep (CodeQL)** |
| Built-in rule packs | 10 taint packs, 10 governance rules | n/a | n/a | **2,000+ (Semgrep community)** |
| Cross-repo at GitHub scale | workspace overlay (sibling repos) | n/a | n/a | **native (Sourcegraph)** |

### Key Differentiators

- **vs AI IDEs** (Cursor, Windsurf, Augment): roam-code provides deterministic structural analysis. AI IDEs use probabilistic embeddings that can't guarantee reproducible results.
- **vs AI Agents** (Claude Code, Codex CLI, Gemini CLI): these agents read files one at a time. roam-code pre-computes relationships so agents get instant answers about architecture, blast radius, and dependencies.
- **vs SAST Tools** (SonarQube, CodeQL, Semgrep): SAST tools find bugs and vulnerabilities. roam-code understands architecture — how code is structured, where it's coupled, and what breaks when you change it. Complementary, not competitive.
- **vs Code Search** (Sourcegraph/Amp, Greptile): text search finds where code is. roam-code understands why code matters — which functions are central, which modules are tangled, which files are high-risk.

<details>
<summary><strong>For teams — cost comparison</strong></summary>

| Tool | Annual cost (20-dev team) | Infrastructure | Setup time |
|------|--------------------------|----------------|------------|
| SonarQube Server (paid tier) | $15,000-$45,000 | Self-hosted server | Days |
| CodeScene | $20,000-$60,000 | SaaS or on-prem | Hours |
| Code Climate | $12,000-$36,000 | SaaS | Hours |
| **Roam (free CLI)** | **$0 (Apache 2.0)** | **None (local)** | **5 minutes** |

The comparison is against the paid tiers a 20-dev team usually buys, not free Community editions. Roam complements either tier — pipe its SARIF output into the same Code Scanning surface. Rollout: pilot on one repo, add `roam health --gate` to CI as non-blocking, then tighten thresholds and track trajectory with `roam trends`.

</details>

## FAQ

**Does Roam send any data externally?**
No by default — zero telemetry, zero analytics, zero update checks. The single outbound surface is `roam metrics-push`: opt-in, summary metrics only, prints its exact payload locally under `--dry-run`. Source-code bodies never leave the machine.

**Can Roam run in air-gapped environments?**
Yes. Once installed, no internet access is required.

**Does Roam modify my source code?**
Read-only by default. Creates `.roam/` with an index database. `roam mutate` (move/rename/extract) defaults to `--dry-run`; pass `--apply` explicitly to write changes.

**How does Roam handle monorepos and multi-repo projects?**
Monorepos: indexes from the root; batched SQL handles 100k+ symbols. Multi-repo: `roam ws init <repo1> <repo2>` builds a workspace overlay DB for cross-repo API edges, then `roam ws resolve` / `ws context` / `ws trace` work across repos.

**Is Roam compatible with SonarQube / CodeScene?**
Yes — they coexist in the same CI pipeline. SARIF output uploads to GitHub Code Scanning.

**Does Roam satisfy SOC 2 / ISO 42001 / EU AI Act on its own?**
No. Roam **maps to** controls and produces supporting evidence — the signed `ChangeEvidence` packet, HMAC-chained run ledger, and audit-trail records answer the eight evidence questions a reviewer asks after an AI-assisted change. Roam does not certify; your auditor still owns that step.

**What's the difference between the free CLI and Roam Review / Cloud / PR Replay?**
The CLI is Apache 2.0, fully local, and never expires. Roam Review is a hosted PR bot, Roam Cloud is opt-in metrics history with no source upload, PR Replay is a one-shot paid audit. All three are layers on top of the same engine.

## Limitations

- **Static analysis primarily** — can't trace dynamic dispatch, reflection, or eval'd code. Runtime trace ingestion (`roam ingest-trace`) adds production data but requires external trace export.
- **Import resolution is heuristic** — complex re-exports or conditional imports may not resolve.
- **Limited cross-language edges** — Salesforce, Protobuf, REST API, and multi-repo edges are supported, but not arbitrary FFI.
- **Tier 2 languages** get basic symbol extraction only via the generic tree-sitter walker.
- **Large monorepos** (100k+ files) may have slow initial indexing.

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
pipx upgrade roam-code        # or: uv tool upgrade roam-code / pip install --upgrade roam-code

# Uninstall
pipx uninstall roam-code      # or: uv tool uninstall roam-code / pip uninstall roam-code
```

Delete `.roam/` from your project root to clean up local data.

## Contributing

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
pip install -e ".[dev]"   # includes pytest, ruff
pytest tests/              # all test cases must pass
```

Good first contributions: add a [Tier 1 language](src/roam/languages/) (see `go_lang.py` or `php_lang.py` as templates), improve reference resolution, add benchmark repos, extend SARIF converters, add MCP tools. Please open an issue first to discuss larger changes.

## License

[Apache 2.0](LICENSE)
