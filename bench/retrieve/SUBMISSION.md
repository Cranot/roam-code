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
  (α=0.40, β=0.25, δ=0.15, ε=0.05, lexical_baseline=0.5) — v12.2 adds
  a ζ=0.20 semantic signal which contributes 0 unless the
  ``[semantic]`` extras are installed and the embeddings table is
  populated, so this submission is comparable to v12.0.
* Top-K: 20 (the headline recall@K).
* Self-test recall@20: ~0.503 baseline, sweep best 0.539 at β=0.15.

## License gate

Per the v12.0 brainstorm review and the ``bench/retrieve/README.md``
licensing rules:

> never train or auto-tune from GPL datasets. SWE-bench Pro is GPL —
> fine for *reporting against the leaderboard* but never as an A.0.4
> input. Defects4J / BugsInPy / first-party PRs are MIT-or-equivalent
> and safe to use here.

The roam_self bench is first-party and MIT-licensed (this repository).
