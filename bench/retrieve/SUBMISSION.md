# roam-code retrieval submission — public bench formats

This directory ships **roam_self.coderag.jsonl** and a generator command
so anyone can reproduce the numbers + submit the run to public retrieval
leaderboards (CodeRAG-Bench, BEIR/trec_eval).

## Quick reproduce

```bash
# Indexed roam-code v12.2 + the canonical 30-task self-bench.
roam --json eval-retrieve \
    --tasks bench/retrieve/roam_self.jsonl \
    --emit-format coderag \
    --emit-out bench/retrieve/roam_self.coderag.jsonl \
    --emit-k 20
```

Output is one JSON object per task with the CodeRAG-Bench-compatible
``ctxs`` array shape:

```json
{
  "task_id": "trace-personalized-pagerank",
  "query": "where is personalized PageRank computed",
  "ctxs": [
    {"id": "src/roam/graph/pagerank.py:50-148",
     "title": "src/roam/graph/pagerank.py",
     "text": "personalized_pagerank (function)",
     "score": 0.8421}
  ]
}
```

## Run name

When asked for a ``run_name`` (BEIR-style), use ``roam-code-v12``. The
official CodeRAG-Bench leaderboard accepts this format directly via
their ``evaluation/utils.py``.

## Methodology

* Bench: 30 hand-curated ``(task, expected_files)`` pairs spanning
  12 subsystems (`bench/retrieve/roam_self.jsonl`).
* Retriever: roam's ``run_retrieve`` with the v12.0 default weights
  (α=0.40, β=0.25, δ=0.15, ε=0.05, lexical_baseline=0.5) plus the
  v12.3 ``path_token_boost`` (max 0.15 per candidate, prefix-tolerant).
  v12.2 added a ζ=0.20 semantic signal which contributes 0 unless the
  ``[semantic]`` extras are installed and the embeddings table is
  populated.
* Top-K: 20 (the headline recall@K).
* Index: ``roam init`` on the v12.3 commit indexed in this run (see
  the commit SHA in this submission's git history).

## Headline numbers

```
recall@5  = 0.600
recall@10 = 0.794
recall@20 = 0.903
```

(30 tasks, full self-bench, default weights, no learned ranker.)

## v12.0 → v12.3 retrieval iteration log

The v12.0 baseline reported 0.486 recall@20 on this exact same bench.
v12.3 lifts it to 0.903. The work is auditable — each iteration is a
single commit with a measured before/after:

| Iter | Change | recall@5 | recall@10 | recall@20 |
|------|--------|----------|-----------|-----------|
| 0 (v12.0) | baseline | 0.289 | 0.358 | 0.486 |
| 1 | domain-noun supplement + file-level dedup | 0.542 | 0.731 | 0.861 |
| 2 | + file-edge neighbour expansion | 0.553 | 0.775 | 0.861 |
| 3 | + path-token boost (set-equality) | 0.581 | 0.775 | 0.897 |
| 4 (v12.3) | + path-token boost (prefix-match) | 0.600 | 0.794 | 0.903 |

The v12.0 numbers are reproducible by reverting commit ``47ce02f`` and
re-running the harness.

## Cross-repo sanity check

To check whether the iter 1–4 lift overfits roam-code's specific
layout, ``tests/test_retrieve_cross_repo.py`` builds a small
synthetic Python microservice (auth + payments + notifications, 5
source files + 2 test files), indexes it via the real ``roam init``,
and runs 5 generic retrieve tasks against it. As of v12.3 (commit
2471521): **recall@5 = recall@10 = recall@20 = 1.000**, all 5 tasks.
The pipeline isn't coupled to roam-code's shape.

This is still a synthetic and small repo — formal external
validation requires CodeRAG-Bench / SWE-bench Pro. But it does rule
out the failure mode where the gains evaporate on any codebase the
maintainer didn't write.

## Caveats and what to read into these numbers

* **This is a self-bench.** A 30-task suite curated by the maintainer
  on the maintainer's own codebase will be friendlier than any
  external eval. Expect lower numbers on CodeRAG-Bench when this is
  formally submitted. The point of publishing both the bench and the
  generator is so external reviewers can re-run the same code on
  *their* repo with *their* tasks and see what the system actually
  delivers in the wild.
* **No learned ranker.** This submission uses ``--rerank fast`` (the
  default). The optional ``--rerank learned`` (``[learned]`` extra,
  LightGBM LambdaMART distillation) is not exercised here.
* **Six tasks still miss at least one expected file.** Most are
  missing a ``commands/cmd_FOO.py`` companion file whose path token
  is structurally distinct from the engine module's tokens. The fix
  would be a ``cmd_FOO.py ↔ FOO/`` pairing heuristic, but the
  marginal lift is small enough that it would couple the ranker to
  roam's specific layout.

## License gate

Per the v12.0 brainstorm review and the ``bench/retrieve/README.md``
licensing rules:

> never train or auto-tune from GPL datasets. SWE-bench Pro is GPL —
> fine for *reporting against the leaderboard* but never as an A.0.4
> input. Defects4J / BugsInPy / first-party PRs are MIT-or-equivalent
> and safe to use here.

The roam_self bench is first-party and Apache-2.0-licensed (this repository).
