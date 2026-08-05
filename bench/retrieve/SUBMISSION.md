# roam-code retrieval submission — public bench formats

This directory ships **roam_self.coderag.jsonl** and a generator command
so anyone can reproduce the numbers and re-run the harness on their own
repo. See [Where these numbers can and cannot be
submitted](#where-these-numbers-can-and-cannot-be-submitted) before
treating this as a leaderboard entry — it is not one.

## Provenance

Every number in this file was produced by one command, at one commit, on
one index. If you cannot reproduce it, that is a bug — please open an issue.

| field | value |
|---|---|
| Commit | `0f3d3ac176b9f258a2238c9a1abf9098136e377a` |
| Date measured | 2026-08-06 |
| roam version | 13.10.0 |
| Index | **fresh full `roam init`** at that commit — 4,944 files, 45,584 symbols, 94,498 edges |
| Task set | `bench/retrieve/roam_self.jsonl` — 30 tasks |
| Reranker | `--rerank fast` (the default). No learned ranker. |
| Semantic | **inert** — `[semantic]` extras not installed, 0/45,584 dense vectors, so ζ=0.2 contributes nothing |
| Platform | Windows 11, CPython 3.12 |

## Quick reproduce

```bash
# 1. Build a FULL index. This step is load-bearing -- see the warning below.
roam init

# 2. Run the harness.
roam --json eval-retrieve \
    --tasks bench/retrieve/roam_self.jsonl \
    --emit-format coderag \
    --emit-out bench/retrieve/roam_self.coderag.jsonl \
    --emit-k 20
```

> **Warning — a stale index silently costs ~13 points of recall@20.**
> `roam init` on an existing index is incremental, and the incremental
> path does not resync the FTS5 table. Retrieval quality then decays with
> no error and no warning. Measured on this exact bench, same binary, same
> commit, same task file — the *only* variable is index build mode:
>
> | index state | `symbol_fts` rows | recall@5 | recall@10 | recall@20 |
> |---|---|---|---|---|
> | fresh full `roam init` | 45,584 / 45,584 | 0.642 | 0.772 | **0.914** |
> | incrementally maintained | 44,412 / 45,574 | 0.592 | 0.678 | **0.781** |
> | same stale index, FTS force-rebuilt and nothing else | 45,574 / 45,574 | 0.636 | 0.778 | **0.906** |
>
> Always benchmark against a fresh index. Tracked as a known defect —
> see [Known defects](#known-defects).

## Headline numbers

<!-- canonical-recall:begin -- parsed by bench/retrieve/check_published_recall.py; keep the format -->
```
recall@5  = 0.642
recall@10 = 0.772
recall@20 = 0.914
```
<!-- canonical-recall:end -->

(30 tasks, full self-bench, default weights, `--rerank fast`, no learned
ranker, fresh full index at `0f3d3ac1`.)

These are checked in CI on every push and PR — `bench/retrieve/check_published_recall.py`
re-measures against a fresh index and fails the build if the published
numbers and the measured numbers drift more than ±0.06 apart, **in either
direction**. Numbers that quietly improve are as much a docs bug as numbers
that quietly regress; this file went stale for three minor versions the
first way.

## Output format

Output is one JSON object per task:

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

This is the **DPR / Atlas / Self-RAG `ctxs` convention**, which is what
most retrieval harnesses and trec_eval wrappers expect. It is *not*
CodeRAG-Bench's own on-disk shape — theirs is a `docs` column of
`[{title, text}]` with the score discarded (see below). Use
`--emit-format beir` for a trec_eval-style run file.

## Where these numbers can and cannot be submitted

**There is no CodeRAG-Bench leaderboard.** Verified 2026-08-06:

* The project site's "Reprduction and Leaderboard" section says
  *"Instructions to submit to the CodeRAG-Bench leaderboard will be
  available soon on Github."* That sentence was committed on **2024-06-21**
  and is byte-identical at the site repo's current HEAD — unchanged for
  over two years. No submission mechanism was ever published.
* The benchmark repo has **no `evaluation/` directory** and no
  `evaluation/utils.py`. The nearest files are `generation/eval/utils.py`
  (a code-generation tokenizer helper) and `retrieval/utils.py` (four
  lines listing BEIR dataset names). Neither ingests a run file.
* Across the repo's source there are **zero** occurrences of `ctxs`,
  `run_name`, `leaderboard`, or `submission`.
* There are no HuggingFace Spaces under the `code-rag-bench` org.

A prior revision of this file claimed *"The official CodeRAG-Bench
leaderboard accepts this format directly via their `evaluation/utils.py`"*
and told submitters to use the run name `roam-code-v12`. **That was false
in all three of its parts** — the leaderboard, the file, and the format —
and it is removed. It was never verified; it was inferred.

The real way to evaluate against CodeRAG-Bench is to run their harness
locally. Scoring happens in-process via `beir.retrieval.evaluation.EvaluateRetrieval`
and is written to a local JSON file; there is no submission step:

```bash
cd retrieval/
python3 eval_beir_sbert_canonical.py \
    --model YOUR_MODEL_NAME_OR_PATH \
    --dataset TASK_NAME \
    --output_file PATH_TO_YOUR_SCORE_FILE \
    --results_file PATH_TO_YOUR_RETRIEVAL_RESULTS_FILE
```

Their generation side reads a `docs` column and uses only the `text`
field (`generation/eval/tasks/humaneval.py`), so adapting our output means
projecting `ctxs[].text`/`ctxs[].title` into their `docs` shape and
dropping `id`/`score`.

## Methodology

* Bench: 30 hand-curated `(task, expected_files)` pairs spanning
  12 subsystems (`bench/retrieve/roam_self.jsonl`).
* Retriever: roam's `run_retrieve` with the default weight vector from
  `src/roam/config.py:DEFAULT_RETRIEVE_WEIGHTS`, plus the `path_token_boost`
  (max 0.15 per candidate, prefix-tolerant).
* Recall@K = `|expected ∩ retrieved_top_K| / |expected|`, averaged
  unweighted across the 30 tasks.
* Top-K: 20 (the headline recall@K).

Signal ablations on the fresh index, for anyone reasoning about which
parts of the pipeline carry the result (each row = that table emptied,
everything else intact):

| ablation | recall@5 | recall@10 | recall@20 |
|---|---|---|---|
| none (baseline) | 0.642 | 0.772 | 0.914 |
| `graph_metrics` emptied | 0.642 | 0.772 | 0.914 |
| `symbol_tfidf` emptied | 0.642 | 0.772 | 0.914 |
| `file_edges` emptied | 0.633 | 0.764 | 0.914 |
| `git_cochange` emptied | 0.644 | 0.761 | 0.903 |
| `symbol_fts` stale | 0.592 | 0.678 | 0.781 |

Lexical FTS is doing nearly all of the work at K=20. The graph and
co-change signals move recall@10 by ~1 point and recall@20 by ~1 point
at most. Treat the headline number as a lexical-retrieval result with a
small structural rerank on top, not as evidence that the graph signals
are load-bearing for recall.

## Historical: v12.0 → v12.3 retrieval iteration log

Measured 2026-05-05 at roam-code v12.3. **Not re-verified since** — kept
because the deltas are the auditable part, not the absolute values. The
v12.0 baseline reported 0.486 recall@20 on this same bench:

| Iter | Change | recall@5 | recall@10 | recall@20 |
|------|--------|----------|-----------|-----------|
| 0 (v12.0) | baseline | 0.289 | 0.358 | 0.486 |
| 1 | domain-noun supplement + file-level dedup | 0.542 | 0.731 | 0.861 |
| 2 | + file-edge neighbour expansion | 0.553 | 0.775 | 0.861 |
| 3 | + path-token boost (set-equality) | 0.581 | 0.775 | 0.897 |
| 4 (v12.3) | + path-token boost (prefix-match) | 0.600 | 0.794 | 0.903 |

The v12.3 line (0.903 recall@20) still holds: the 2026-08-06 re-measurement
at v13.10 gives 0.914, within noise of it. The v12.0 numbers are
reproducible by reverting commit `47ce02f` and re-running the harness.

## Cross-repo sanity check

To check whether the iter 1–4 lift overfits roam-code's specific
layout, `tests/test_retrieve_cross_repo.py` builds a small synthetic
Python microservice (auth + payments + notifications, 5 source files +
2 test files), indexes it via the real `roam init`, and runs 5 generic
retrieve tasks against it. As of v12.3 (commit 2471521):
**recall@5 = recall@10 = recall@20 = 1.000**, all 5 tasks. Not
re-measured at v13.10.

This is still a synthetic and small repo — formal external validation
requires CodeRAG-Bench / SWE-bench Pro. But it does rule out the failure
mode where the gains evaporate on any codebase the maintainer didn't write.

## Caveats and what to read into these numbers

* **This is a self-bench.** A 30-task suite curated by the maintainer
  on the maintainer's own codebase will be friendlier than any
  external eval. Expect lower numbers on CodeRAG-Bench when this is
  formally run. The point of publishing both the bench and the
  generator is so external reviewers can re-run the same code on
  *their* repo with *their* tasks and see what the system actually
  delivers in the wild.
* **No learned ranker.** This submission uses `--rerank fast` (the
  default). The optional `--rerank learned` (`[learned]` extra,
  LightGBM LambdaMART distillation) is not exercised here.
* **Recall@20 is not recall@5.** At K=5 the system finds 64% of expected
  files; the headline 0.914 is a K=20 number and should be quoted with
  its K.
* **Some tasks still miss at least one expected file.** Most are missing
  a `commands/cmd_FOO.py` companion whose path token is structurally
  distinct from the engine module's tokens. The fix would be a
  `cmd_FOO.py ↔ FOO/` pairing heuristic, but the marginal lift is small
  enough that it would couple the ranker to roam's specific layout.

## Known defects

Open at the time of writing, recorded rather than silently carried:

1. **Incremental indexing does not resync FTS5.** `build_fts_index`
   (`src/roam/search/index_embeddings.py`) syncs by rowid-set difference
   only: rowids added to `symbols` are inserted, rowids removed are
   deleted, and **a symbol modified in place — same id, new name or
   signature — is never re-indexed.** Separately, incremental `roam init`
   runs on this repo recorded only the `discover` / `parse_extract` /
   `resolve` phases, skipping the `search_indexes` phase entirely and
   leaving 1,162 symbols absent from FTS. Net effect measured above:
   −13.3 points of recall@20, with no error, warning, or degraded-mode
   disclosure to the user.
2. **`--json` output is contaminated when emitting.** With
   `--emit-format coderag|beir`, `cmd_eval_retrieve.py` prints
   `Wrote N records to ... (coderag format).` to stdout *before* the JSON
   envelope, even under `--json`. The quick-reproduce command above
   therefore cannot be piped to `jq` without stripping the first line.
3. **`run_name` is hardcoded to `roam-code-v12`** in `_emit_bench_run`,
   three minor versions stale, and is emitted into every `beir`-format
   record.

## License gate

Per the v12.0 brainstorm review and the `bench/retrieve/README.md`
licensing rules:

> never train or auto-tune from GPL datasets. SWE-bench Pro is GPL —
> fine for *reporting against the leaderboard* but never as an A.0.4
> input. Defects4J / BugsInPy / first-party PRs are MIT-or-equivalent
> and safe to use here.

The roam_self bench is first-party and Apache-2.0-licensed (this repository).
