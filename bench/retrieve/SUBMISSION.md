# roam-code retrieval submission — public bench formats

This directory ships **roam_self.coderag.jsonl** and a generator command
so anyone can reproduce the numbers and re-run the harness on their own
repo. See [Where these numbers can and cannot be
submitted](#where-these-numbers-can-and-cannot-be-submitted) before
treating this as a leaderboard entry — it is not one.

## Provenance

The current baseline below has a named commit, index and dependency profile.
Historical experiments retain their own dates and environments; they were not
rerun with this baseline. Report a reproduction mismatch with those details.

| field | value |
|---|---|
| Commit | `b3447d3d9d0bdd9e2974b4a7be484fb98842c445` (measured runtime/source, before this documentation update) |
| Date measured | 2026-09-08, Europe/Athens (2026-09-07 UTC) |
| roam version | 14.1.0 |
| Index | **fresh full index**, full Git history — 5,125 files, 48,007 symbols, 82,006 edges; schema 20 |
| Git scope | Full checkout ancestry: 2,649 commits; index contains 1,986 git-commit rows, not every ancestry commit |
| Task set | `bench/retrieve/roam_self.jsonl` — 30 tasks |
| Task SHA-256 | `d3e3d15fe0582913e2d662de0d9e72faf165f912c8135ee09c2d3b1b0321d2c1`; unchanged from September 5 |
| Reranker | `--rerank fast` (the default). No learned ranker. |
| Dependencies | `uv sync --locked --no-default-groups`; no NumPy/SciPy; degree-with-seed-boost ranking fallback |
| Semantic | **inert** — no semantic/learned extras, 0/48,007 dense vectors, so ζ=0.2 contributes nothing |
| Platform | Linux, CPython 3.11.15; agrees with GitHub Actions Ubuntu 24.04 / CPython 3.11.16 |
| Independent reproduction | Windows / CPython 3.12.13, same minimal profile and separate fresh index: identical per-task recalls and aggregate values |

## Quick reproduce

```bash
# Use a separate checkout of the measured commit; this selects minimal extras.
git checkout --detach b3447d3d9d0bdd9e2974b4a7be484fb98842c445
uv sync --locked --no-default-groups --python 3.11

# Build a FULL index, preserving other repo-local state.
uv run --no-sync roam index --force

# 2. Run the harness.
uv run --no-sync roam --json eval-retrieve \
    --tasks bench/retrieve/roam_self.jsonl \
    --emit-format coderag \
    --emit-out bench/retrieve/roam_self.coderag.jsonl \
    --emit-k 20
```

> **Historical index experiment — 2026-08-06, commit `0f3d3ac1`.**
> These retained measurements used the same binary, commit and task file,
> varying the index build mode. They are not a September re-test of the
> incremental path:
>
> | index state | `symbol_fts` rows | recall@5 | recall@10 | recall@20 |
> |---|---|---|---|---|
> | fresh full `roam init` | 45,584 / 45,584 | 0.642 | 0.772 | **0.914** |
> | incrementally maintained | 44,412 / 45,574 | 0.592 | 0.678 | **0.781** |
> | same stale index, FTS force-rebuilt and nothing else | 45,574 / 45,574 | 0.636 | 0.778 | **0.906** |
>
> Always benchmark against a fresh index. See [Known defects](#known-defects)
> for the historical finding and its verification scope.

## Headline numbers

<!-- canonical-recall:begin -- parsed by bench/retrieve/check_published_recall.py; keep the format -->
```
recall@5  = 0.644
recall@10 = 0.783
recall@20 = 0.872
```
<!-- canonical-recall:end -->

(30 tasks, full self-bench, default weights, `--rerank fast`, no learned
ranker, minimal dependencies, fresh full index at `b3447d3d`.)

The [CI run](https://github.com/Cranot/roam-code/actions/runs/34169970244)
reported the four-decimal values **0.6444 / 0.7833 / 0.8722**. Its documentation
gate failed because top-5 recall differed from the previous rounded claim of
0.706 by -0.0616, beyond the unchanged 0.06 tolerance. Two Linux runs reproduce
identical complete per-task results. An independent Windows full build agrees
on all per-task recalls; no labels, ranking weights, tolerance or recall floor
were changed to make the gate pass. Matching recall does not imply identical
ordering of every lower-ranked file across all environments.

The checked-in `roam_self.coderag.jsonl` candidate export was regenerated from
this minimal-profile checkout at K=20; the recall aggregates come from the
evaluation envelope, not from treating exported symbol spans as file-level hits.

### September 8 controlled comparison

The September 5 baseline at `5c56ff47` was 0.7056 / 0.7833 / 0.8556, on
5,074 files / 47,542 symbols / 99,217 edges. Both the source corpus and graph
definition have changed since then; this is not a fixed-corpus performance
comparison. To investigate rather than simply replace the published claim,
two additional controls hold the current corpus and dependency profile fixed:

| Current-corpus configuration | edges | recall@5 | recall@10 | recall@20 |
|---|---|---|---|---|
| Current index and runtime | 82,006 | 0.6444 | 0.7833 | 0.8722 |
| Same current index; only `retrieve.pipeline` and `retrieve.rerank` loaded from `5c56ff47` | 82,006 | 0.6444 | 0.7833 | 0.8722 |
| Separate full index; only the Python extractor loaded from `5c56ff47`, current ranking | 100,736 | 0.6722 | 0.7833 | 0.8722 |

The old ranking-module control produces identical ordered per-task results.
The old-extractor control restores 18,730 edges and raises top-5 recall on two
tasks (`mcp-presets-short-desc` and `anomaly-detection-statistics`). It does not
restore the whole September 5 score. The remaining cross-snapshot difference
has not been causally isolated.

The current Python extractor deliberately excludes assigned local values from
global callback resolution while preserving real callback references. Paired
controls in `tests/test_python_local_reference_precision.py` cover this
correctness boundary. Restoring the old extractor also restores that known
false-reference behavior; a better self-bench score would not justify it.
The edge-count difference is not a claim that every removed edge was labeled
and verified, nor is the higher top-20 score evidence of an overall quality gain.
Keep the precision fix, report the lower top-5 recall, and assess future ranking
changes on independent tasks before tuning to these 30 labels.

### Historical numerical-dependency control — September 5

At `5c56ff472ebbd3351ce5ae416a6c9caf01d58d74`, one fresh Windows index and
isolated wheel environment were evaluated before and after adding only NumPy
2.4.4 and SciPy 1.17.1. These rows were not rerun at `b3447d3d`:

| profile | recall@5 | recall@10 | recall@20 |
|---|---|---|---|
| minimal, no numerical extras | 0.7056 | 0.7833 | 0.8556 |
| same environment plus NumPy/SciPy | 0.6333 | 0.7639 | 0.8972 |

`roam.graph.pagerank` uses degree-with-seed-boost ranking without these optional
libraries and power-iteration PageRank with them. The dependency profile changes
which K performs better; this is not a uniform quality improvement or an OS
effect. The development environment reproduced the numerical-profile row.
The canonical CI numbers refer to the minimal profile, not every installation.

Only the canonical minimal-profile values are checked in CI on every push and
PR — `bench/retrieve/check_published_recall.py` re-measures against a fresh
index and fails the build if the published
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

**Historical signal ablations, 2026-08-06 at `0f3d3ac1`**, not rerun for the
current baseline (each row = that table emptied, everything else intact):

| ablation | recall@5 | recall@10 | recall@20 |
|---|---|---|---|
| none (baseline) | 0.642 | 0.772 | 0.914 |
| `graph_metrics` emptied | 0.642 | 0.772 | 0.914 |
| `symbol_tfidf` emptied | 0.642 | 0.772 | 0.914 |
| `file_edges` emptied | 0.633 | 0.764 | 0.914 |
| `git_cochange` emptied | 0.644 | 0.761 | 0.903 |
| `symbol_fts` stale | 0.592 | 0.678 | 0.781 |

Those historical rows showed limited graph/co-change influence in that
configuration. They do not establish the contribution of each signal in the
current minimal profile. The September dependency control above independently
demonstrates that the numerical backend affects ranking on the same index;
neither experiment is evidence of a universal quality gain.

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

The 2026-08-06 v13.10 re-measurement gave 0.914 recall@20, close to the
historical v12.3 value of 0.903; neither establishes the current profile's
performance. The v12.0 numbers were
reproducible by reverting commit `47ce02f` and re-running the harness.

## Cross-repo sanity check

To check whether the iter 1–4 lift overfits roam-code's specific
layout, `tests/test_retrieve_cross_repo.py` builds a small synthetic
Python microservice (auth + payments + notifications, 5 source files +
2 test files), indexes it via the real `roam init`, and runs 5 generic
retrieve tasks against it. As of v12.3 (commit 2471521):
**recall@5 = recall@10 = recall@20 = 1.000**, all 5 tasks. Not
re-measured for the current baseline.

This small maintainer-authored fixture checks another layout; it does not
establish gains on independently selected repositories or real agent tasks.
External validation needs independently sourced tasks, reviewed relevance
labels, a fixed comparison baseline and an explicit evaluation protocol.

## Caveats and what to read into these numbers

* **This is a self-bench.** A 30-task suite curated by the maintainer
  on the maintainer's own codebase can favor its design and naming conventions.
  Performance on external tasks is unknown. The point of publishing both the bench and the
  generator is so external reviewers can re-run the same code on
  *their* repo with *their* tasks and see what the system actually
  delivers in the wild.
* **No learned ranker.** This submission uses `--rerank fast` (the
  default). The optional `--rerank learned` (`[learned]` extra,
  LightGBM LambdaMART distillation) is not exercised here.
* **Recall@20 is not recall@5.** The current minimal profile finds about
  64% of labeled expected files at K=5 and 87% at K=20, averaged per task.
  Quote K, date and dependency profile. File recall does not measure agent task
  success, content sufficiency, or the need for follow-up searches. The historical
  numerical-profile result is not a current-commit measurement.
* **Some tasks still miss at least one expected file.** Most are missing
  a `commands/cmd_FOO.py` companion whose path token is structurally
  distinct from the engine module's tokens. The fix would be a
  `cmd_FOO.py ↔ FOO/` pairing heuristic, but the marginal lift is small
  enough that it would couple the ranker to roam's specific layout.

## Known defects

The following findings were recorded on 2026-08-06. This September baseline
uses a full index; it does not retest the old incremental-index experiment or
claim these historical findings are all still live. The Coderag export path was
invoked again; its stdout preamble remains observable. The BEIR run-name literal
is still present in the current source.

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
