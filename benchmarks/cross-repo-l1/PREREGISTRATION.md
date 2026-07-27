# Cross-repo L1 transfer — preregistration

Committed **before** any measurement was run. If results appear in git history
before this file, the preregistration is void.

## Question

Does roam's L1 compile path (the cheap, prefetched-facts artifact) transfer to
repositories it was not developed against?

## Metric

`l1_route_rate_pct` as emitted by `roam compiler-corpus`, defined in-envelope as:

    100 * count(artifact_label == 'l1_probe') / count(all successful compiles)

Corpus: `benchmarks/cross-repo-l1/CORPUS_L1_TRANSFER_60.txt` (60 authored,
repo-agnostic prompts, frozen). Same corpus for every repo. `--limit 60`.
`ROAM_AGENT_MODE=bench` so the sweep does not pollute live KPIs.

## Win bar (FIXED, not adjustable after seeing data)

Transfer **holds** iff `l1_route_rate_pct >= 45` on **all three** target repos:

| target  | language | repo               |
|---------|----------|--------------------|
| fastapi | Python   | fastapi/fastapi    |
| gin     | Go       | gin-gonic/gin      |
| svelte  | JS/TS    | sveltejs/svelte    |

Anything less on any one of the three is a **MISS**, reported as such.

Reference point: 63.0% roam-on-roam (home-repo baseline, from prior internal
corpus — not this corpus, so not directly comparable). roam-code is therefore
also run against *this* corpus as a non-gating control, so the transfer ratio
is interpretable.

Secondary, **reported but not gating**: `envelope_bytes` p50/p95,
`compile_latency_ms` p50/p95, `artifact_distribution`, `procedure_distribution`.

## Recorded prediction

A **NULL is expected**. Route classification should transfer — the classifier
is pure English regex (`src/roam/plan/compiler.py:155-188`), carrying no
repo-specific tokens. But L1 *firing* gates on `_l1_has_target()`
(`src/roam/plan/compiler.py:11934`), i.e. `bool(plan.likely_files)` — symbol
resolution against a foreign index. Prior: fastapi clears 45%; gin or svelte
does not.

## Honesty constraints

- `top_misses` echoes corpus prompt text; it stays local and is never reported
  beyond aggregate shape.
- A results file without its terminal marker means STILL RUNNING, not success.
- Piped commands report the tail's exit status; `${PIPESTATUS[0]}` is used.
- If a number disagrees with the expectation, the measurement wins.
