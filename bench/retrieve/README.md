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
> NumPy/SciPy dependencies also change ranking: the current minimal and
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

* **Recall@5 ≥ 0.5** is the rough bar for "the agent can solve this from
  the retrieve output alone."
* **Recall@20 ≥ 0.7** is the bar at which agents stop needing
  `roam search` follow-ups.
* **Recall@K = 1.0** when every expected file is in the top K — the
  ideal.

## Current baseline — `roam_self.jsonl` (30 tasks)

Measured **2026-09-05** at commit `5c56ff472ebbd3351ce5ae416a6c9caf01d58d74`,
roam-code 14.0.0, against a **fresh full index** (5,074 files / 47,542
symbols / 99,217 edges), full Git history, `--rerank fast`, and the locked
**minimal installation**: no NumPy/SciPy, semantic or learned-ranker extras.
The Linux/Python 3.11 reproduction agrees with the
[exact-commit CI measurement](https://github.com/Cranot/roam-code/actions/runs/33934250545).

<!-- canonical-recall:begin -- parsed by check_published_recall.py; keep the format -->
```
recall@5  = 0.706
recall@10 = 0.783
recall@20 = 0.856
```
<!-- canonical-recall:end -->

| K  | mean recall | comment |
|----|-------------|---------|
|  5 | **0.706** | minimal-install profile |
| 10 | **0.783** | minimal-install profile |
| 20 | **0.856** | minimal-install profile; always quote K |

An isolated Windows/Python 3.12 installation reproduced the same values on
the same fresh index. Adding **only NumPy 2.4.4 and SciPy 1.17.1** to that
environment changed recall@5/@10/@20 to **0.633 / 0.764 / 0.897**. The numerical
backend uses power-iteration PageRank; the minimal backend uses degree ranking
with a seed boost. This is a controlled dependency-profile difference, not
evidence of an overall retrieval improvement or a Windows/Linux discrepancy.
The CI gate below targets the minimal profile. See [SUBMISSION.md](SUBMISSION.md)
for both profiles and the retained historical measurements.

Exact command, index provenance, per-signal ablations, and the reasons to
distrust a stale index are in [SUBMISSION.md](SUBMISSION.md). These
numbers are enforced in CI by `check_published_recall.py` (see below) —
if you change the retriever and this table is not updated, the build fails.

### Historical baselines

Kept so the history is auditable; these rows were not rerun in September.
Different dependency profiles and corpora prevent a controlled trend claim.

| when | commit | recall@5 | recall@10 | recall@20 | note |
|---|---|---|---|---|---|
| 2026-05-01 | `78de9ee` | 0.286 | 0.358 | 0.503 | 30-task bench, pre-v12.1 retriever |
| — | — | — | — | 0.433 | prior 10-task bench |
| 2026-08-06 | `0f3d3ac1` | 0.642 | 0.772 | 0.914 | historical full-index measurement; numerical dependency profile was not recorded |

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
