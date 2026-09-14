# Measurements and historical comparisons

This page preserves the measurements previously carried in the project README,
relocated on 2026-09-12. It is an evidence record, not a claim that every current
release or agent reproduces these outcomes. The historical passages below retain
their original numbers, losses, qualifications and model/version labels. Phrases
such as "now", "pending", "the only surface" and recommendations within those
passages belong to the original record, not a current operational assessment.

The available results do not establish general call-graph accuracy or universal
savings. Use [detector evidence](concepts/detector-evidence.md) for current finding
interpretation, [verification evidence](concepts/verification-evidence.md) for
acceptance boundaries, and the [README](../README.md) for first use. Source-line
citations into older README snapshots remain historical references. The timing
notes correct one old link to a local result file that was never versioned;
the recorded numbers are unchanged.

For a current reading guide, see [website measurements](https://roam-code.com/measurements).
Public artifact pointers checked on 2026-09-14: the repair-sibling record has
[576 frozen cases](../tests/data/1c_frozen.json),
[saved four-arm results](../tests/data/1c_fourarm_results.json), and a
[scorer/saved-metric regression](../tests/test_repair_intent_frozen.py).
That regression does not recreate candidate collection or the original four-arm
experiment. The frozen protocol describes reranking the same lexical pool,
whereas the saved results define treatment over the graph pool. The historical
win language below does not resolve that discrepancy or establish current
end-to-end retrieval accuracy. Preserve the original numbers and this boundary.

<a id="context-for-your-agent"></a>

## Agent workflow comparisons — May–July 2026

<!-- BEGIN preserved-readme:agent-comparisons -->
<details>
<summary><strong>Benchmark results and history (May–July 2026)</strong></summary>

**What was measured head-to-head on Claude** (same prompts, same
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

**Eval history by version** — losses are published, attacked, then
re-measured. The ledger is **not** re-run on every release: the newest
measured kernel is **v13.7 (Jul 11)**, and later kernel releases have shipped
since without a fresh A/B. Read every row
below as measured-at-the-stated-kernel, not as a current-release claim. The
table is the summary ledger; raw per-cell data for the historical runs is
retained privately, not in this repository:

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

</details>
<!-- END preserved-readme:agent-comparisons -->

## Retrieval results and detector evidence — historical README record

The following passage includes older comparative judgments (for example,
"high-precision" and "without hedging"). Those judgments are not a measured
precision guarantee. The retrieval result is scoped to repair siblings under
the stated benchmark; other surfaces still need their own measurements.

<!-- BEGIN preserved-readme:measured-advisory -->
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
<!-- END preserved-readme:measured-advisory -->

## Timing notes — historical README record

These rough timings have no shared dated run manifest. Treat them as historical
estimates, not reproduced current-release measurements or an installation SLA.
Measure the commands you need on your own repository and machine.

<!-- BEGIN preserved-readme:performance -->
## Performance

| Metric | Value |
|--------|-------|
| Index 200 files | ~3-5s |
| Index 3,000 files | ~2 min |
| Incremental (no changes) | <1s |
| Lightweight index queries | Often <0.5s; broader analyses can take much longer |

Every figure includes CLI process startup, which is host- and platform-dependent:
on a slow Windows host `roam --version` alone can cost ~1.5s, putting a floor
under every row above. Measure on your own machine before gating on these.

After the first full index, `roam index` reuses unchanged source data (mtime + SHA-256 hash), reprocesses changed files and affected neighbors, and checks Git history even when file contents are unchanged. The OSS benchmark harness in [`benchmarks/oss-eval/`](../benchmarks/oss-eval/) tracks 14 repositories (Express, Axios, Vue, Laravel, Svelte, React, Django, cpython, Linux, …). Its result files are generated locally; no result snapshot is versioned with this document. Run the harness to establish which targets completed, failed, or were absent for that run.

Historical agent comparisons, per-task results, and their limitations are in
[Context for your agent](#context-for-your-agent).
<!-- END preserved-readme:performance -->
