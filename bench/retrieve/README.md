# Retrieval eval harness (A.0.4)

A small infrastructure for measuring `roam retrieve` quality against a
labeled set of (task, expected_files) pairs.

## Quick start

```bash
# In a separate checkout, reproduce the minimal dependency profile used by CI.
uv sync --locked --no-default-groups --python 3.11

# Build a FULL index first, preserving other repo-local state.
uv run --no-sync roam index --force

# Eval the built-in self-test set against the indexed roam-code repo
uv run --no-sync roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl

# Sweep weight vectors (α / β / γ / δ / ε)
uv run --no-sync roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl --sweep

# Pipe to a CI gate (0.85 is the current pinned floor -- see below)
uv run --no-sync roam --json eval-retrieve --tasks ... --min-recall-at-20 0.85
```

> **Benchmark against a fresh index and a named dependency profile.** A
> historical 2026-08-06 experiment measured **0.781** recall@20 on an
> incrementally maintained index versus **0.914** on a full build at the
> same commit. That experiment is retained in [SUBMISSION.md](SUBMISSION.md),
> not presented as a new measurement of today's incremental path. Optional
> NumPy/SciPy dependencies also change ranking: the minimal and historical
> numerical profiles below are different measurements, not interchangeable
> environments. Use a separate checkout for the minimal-profile sync above;
> it intentionally excludes development and numerical extras.

## Task file format

JSONL — one task per line:

```json
{"task_id": "trace-usersession", "task": "trace UserSession refresh flow", "expected_files": ["src/roam/retrieve/seeds.py", "tests/test_retrieve_seeds.py"]}
{"task_id": "where-is-fingerprint", "task": "where is the topology fingerprint computed", "expected_files": ["src/roam/graph/fingerprint.py", "src/roam/commands/cmd_fingerprint.py"]}
```

Required fields:

- `task` — free-form natural-language query, fed to `roam retrieve`.
- `expected_files` — list of paths that should appear in the top-K
  retrieved candidates. Recall@K = `|expected ∩ retrieved_top_K| / |expected|`.

Optional:

- `task_id` — slug used in summary tables. Auto-generated from the
  task text if absent.
- `notes` — free-form explanation, surfaced in the per-task report.

## Recall@K interpretation

Recall@K measures how many labeled expected files appear in the first K
distinct retrieved files, averaged equally across tasks. **Recall@K = 1.0**
means every expected file was found within that budget. It does not establish
that an agent can solve the task, that the retrieved content is sufficient,
or that no follow-up search is needed. Those outcomes require separate agent
trials. The labels can also omit useful files; this is a small file-retrieval
benchmark, not a complete relevance or task-success score.

## Current baseline — `roam_self.jsonl` (30 tasks)

Measured **2026-09-08 (Europe/Athens)** at commit
`b3447d3d9d0bdd9e2974b4a7be484fb98842c445`, roam-code 14.1.0, against a
**fresh full index** (5,125 files / 48,007 symbols / 82,006 edges), full Git
history, `--rerank fast`, and the locked
**minimal installation**: no NumPy/SciPy, semantic or learned-ranker extras.
Independent Linux/Python 3.11 and Windows/Python 3.12 full-index reproductions
agree on all 30 tasks' recalls with the
[exact-commit CI measurement](https://github.com/Cranot/roam-code/actions/runs/34169970244).
That CI run failed the old published-number gate; it is evidence of the
measurement and discrepancy, not a green release receipt.

<!-- canonical-recall:begin -- parsed by check_published_recall.py; keep the format -->
```
recall@5  = 0.644
recall@10 = 0.783
recall@20 = 0.872
```
<!-- canonical-recall:end -->

| K  | mean recall | comment |
|----|-------------|---------|
|  5 | **0.644** | minimal-install profile |
| 10 | **0.783** | minimal-install profile |
| 20 | **0.872** | minimal-install profile; always quote K |

Compared with the September 5 snapshot, top-5 recall is lower, top-10 is
unchanged and top-20 is higher. A controlled old-extractor build explains
part of the top-5 loss: the Python local-reference correction changes the
graph and candidate ordering. Restoring the old ranking modules alone on the
current index leaves every task result unchanged. These controls do not
isolate the remaining corpus/history effects or establish a uniform quality
gain. See [the controlled comparison](SUBMISSION.md#september-8-controlled-comparison).

The September 5 numerical-dependency control is retained below and in
[SUBMISSION.md](SUBMISSION.md); it was **not** rerun at the current commit.
The CI gate targets the minimal profile, not every installation.

Exact command, index provenance, per-signal ablations, and the reasons to
distrust a stale index are in [SUBMISSION.md](SUBMISSION.md). These
numbers are enforced in CI by `check_published_recall.py` (see below) —
if the measured values drift beyond tolerance, the build fails. Investigate
the cause before publishing a new baseline; do not tune labels, weights or
tolerance merely to clear the gate.

### Historical baselines

Kept so the history is auditable; these rows were not rerun for the September 8 baseline.
Different dependency profiles and corpora prevent a controlled trend claim.

| when | commit | recall@5 | recall@10 | recall@20 | note |
|---|---|---|---|---|---|
| 2026-05-01 | `78de9ee` | 0.286 | 0.358 | 0.503 | 30-task bench, pre-v12.1 retriever |
| — | — | — | — | 0.433 | prior 10-task bench |
| 2026-08-06 | `0f3d3ac1` | 0.642 | 0.772 | 0.914 | historical full-index measurement; numerical dependency profile was not recorded |
| 2026-09-05 | `5c56ff47` | 0.706 | 0.783 | 0.856 | fresh full index; minimal profile; Linux and Windows agree |
| 2026-09-05 | `5c56ff47` | 0.633 | 0.764 | 0.897 | same Windows environment/index plus NumPy 2.4.4 and SciPy 1.17.1 |

The 2026-05-01 row sat in this file as "current" until 2026-08-06 while
the retriever improved underneath it, so this README understated real
recall@20 by 41 points for three minor versions. That is the drift the
CI gate below exists to prevent.

The sweep grid measured at that time favoured β=0.15 by ~3.6 points
across all α values (0.539 vs 0.503 at the default β=0.25). **Not
re-measured against the current retriever** — treat it as a stale
observation, not a live recommendation. Defaults stay at β=0.25 until
either (a) the bench grows past 50 tasks or (b) the lift survives a
controlled sweep on a non-roam repo, re-measured at current HEAD.
See `src/roam/config.py:DEFAULT_RETRIEVE_WEIGHTS`.

## CI gate — `check_published_recall.py`

`bench/retrieve/check_published_recall.py` is the structural guard against
exactly the failure this directory shipped for three months: a published
benchmark number that reproduction refutes.

It re-runs the harness against a fresh index and compares the measured
result to the numbers published in **both** `README.md` and
`SUBMISSION.md`, parsed from the `canonical-recall` blocks above. It
fails if any of them drift by more than the tolerance, **in either
direction** — a number that quietly improves is as much a docs defect as
one that quietly regresses, and improvement is in fact how this file
went stale.

```bash
uv run --no-sync roam --json eval-retrieve --tasks bench/retrieve/roam_self.jsonl > eval.json
uv run --no-sync python bench/retrieve/check_published_recall.py --eval-json eval.json
```

* **Tolerance: ±0.06 absolute**, per K, unchanged. CI uses **full history**
  (`fetch-depth: 0`) and the minimal dependency profile. The earlier rationale
  claiming shallow history was a smaller perturbation than an empty co-change
  table was disproved by the August 12 experiment recorded in the checker:
  recall@20 was 0.9139 with full history, 0.8778 with no co-change rows, and
  0.8500 with a 50-commit clone. Do not spend this tolerance on a different
  history scope or numerical backend.
* Wired into `.github/workflows/dogfood.yml`, which already builds a
  fresh index on every push and PR.
* The previously documented floor, `--min-recall-at-20 0.6`, was loose
  enough to pass at 0.781 — i.e. it would not have caught the regression
  it existed to catch. Prefer this gate; the flag remains for ad-hoc use.

## Sweep mode

`--sweep` runs the harness across a small grid of weight vectors and
emits the best-scoring vector. Useful when adding a new signal.
Defaults sweep α ∈ {0.3, 0.4, 0.5}, β ∈ {0.15, 0.25, 0.35} keeping
γ + δ + ε pegged. Use `--full-sweep` for the complete cartesian
product (slower, more thorough).

## Building a new task set

Extract tasks from real PRs:

```bash
# Take the last 50 PRs, extract title + edited files via gh
gh pr list --state merged --limit 50 --json title,files \
  | jq -c '.[] | {task_id: (.title | tostring), task: .title, expected_files: [.files[].path]}' \
  > bench/retrieve/recent_prs.jsonl
```

Hand-craft thematic tasks targeted at the specific corner you want to
measure (the bench/retrieve/roam_self.jsonl set is hand-crafted to
exercise different parts of the retrieve pipeline: file-mode queries,
identifier-shaped queries, natural-language queries, etc.).

## Licensing

Per the C.2 review: **never train or auto-tune from GPL datasets**.
SWE-bench Pro is GPL — fine for *reporting against the leaderboard*
but never as an A.0.4 input. Defects4J / BugsInPy / first-party PRs
are MIT-or-equivalent and safe to use here.
