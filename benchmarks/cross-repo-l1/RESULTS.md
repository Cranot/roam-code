# Cross-repo L1 transfer — RESULTS

Run date: 2026-07-27. Engine: roam-code `b6a8e87f` (`roam, version 13.10.0`),
fresh venv from `git archive HEAD`, on the benchmark host. Corpus:
`CORPUS_L1_TRANSFER_60.txt`, frozen at `b6a8e87f`, `--limit 60`,
`ROAM_AGENT_MODE=bench`. Target SHAs in `TARGET_SHAS.md`, recorded pre-run.

## Verdict against the preregistered bar

**MISSED.** Bar was `l1_route_rate_pct >= 45` on all three targets.

| repo                | l1_route_rate_pct | l1_probe / 60 | bar 45% |
|---------------------|-------------------|---------------|---------|
| fastapi (Python)    | **12**            | 7             | MISS    |
| gin (Go)            | **10**            | 6             | MISS    |
| svelte (JS/TS)      | **13**            | 8             | MISS    |
| roam-code (control) | **17**            | 10            | n/a     |

0 compile errors and 0 empty-probe findings on all four runs;
`partial_success: false` everywhere. Wall time 21–32 s per 60-prompt sweep.

## The control changes the reading

The 45% bar was calibrated against a reported 63.0% roam-on-roam figure that
came from a **different, unavailable corpus** (`internal/benchmarks/…/CORPUS.txt`
is not in the tree). Running *this* corpus on roam-code's own repo and own
index yields **17%**, not 63%. So the bar was not reachable by any repo with
this corpus, including the home repo. The absolute miss is real and is reported
as the verdict; it is not by itself evidence of a transfer failure.

The like-for-like comparison — same corpus, same engine, home vs foreign — is:

| repo    | L1 fires | fraction of home-repo L1 |
|---------|----------|--------------------------|
| roam    | 10       | 1.00 (reference)         |
| svelte  | 8        | 0.80                     |
| fastapi | 7        | 0.70                     |
| gin     | 6        | 0.60                     |

## Route classification transfers exactly

`procedure_distribution` is **byte-identical across all four repos**:

```
entry_point_where 1, freeform_explore 22, structural_blast 10,
structural_callers 12, structural_complexity 1, structural_coupling 7,
structural_cycle 2, structural_dead 1, synthesis_query 1, top_n_ranking 3
```

This confirms the recorded prediction's first half: the classifier
(`src/roam/plan/compiler.py:155-188`) is pure English regex and carries zero
repo-specific state. Every one of the 12 authored callers-prompts routed to
`structural_callers`, every one of the 10 blast-prompts to `structural_blast`.
What varies across repos is only which routed prompts then **fire L1** —
i.e. `_l1_has_target()` / `bool(plan.likely_files)` at `compiler.py:11934`,
symbol resolution against the local index. Prediction's second half confirmed;
its per-repo ordering ("fastapi clears 45%") refuted — fastapi did not clear it,
and neither did the home repo.

## Where the 43 non-L1 prompts went

`freeform_explore` took 22 of 60 — the whole 10-prompt recent-change/history
family plus the 6 locate and 6 open-ended prompts. "what changed in X recently"
has no history/churn route in the classifier at all. That is a classifier gap
independent of repo, and it caps `l1_route_rate_pct` at 63% before any index is
consulted. A further 21 prompts landed on `facts` and 29–32 on `full`.

## Secondary metrics (reported, non-gating)

| repo    | env p50 B | env p95 B | compile p50 ms | compile p95 ms |
|---------|-----------|-----------|----------------|----------------|
| fastapi | 1162      | 1416      | 495            | 835            |
| gin     | 1003      | 1408      | 396            | 806            |
| svelte  | 1310      | 1629      | 397            | 522            |
| roam    | 1100      | 2510      | 494            | 951            |

Foreign-repo envelopes are *not* larger or slower than home-repo ones; svelte
(8,973 files, 17,938 symbols) compiles at the same p50 as roam-code. Cost does
not degrade with foreignness.

## Caveats

- `numpy` absent in the measurement venv → `_compute_algebraic_connectivity`
  returned its 0.0 sentinel during indexing. Affects spectral bisection only,
  not L1 routing or symbol resolution.
- One corpus, one composition. `l1_route_rate_pct` is strongly corpus-dependent
  (17% here vs a reported 63% elsewhere); it is a property of the prompt mix at
  least as much as of the engine.
- Repo-agnostic prompts are the hard case by construction: no prompt names a
  real symbol, so `likely_files` must be inferred from generic English nouns.
  This measures the floor of L1 transfer, not the typical case.
- `top_misses` echoes corpus prompt text and was kept on the box.
