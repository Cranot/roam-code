# Retrieval eval harness (A.0.4)

A small infrastructure for measuring `roam retrieve` quality against a
labeled set of (task, expected_files) pairs.

## Quick start

```bash
# Build a FULL index first -- see the warning below. This is not optional.
roam init

# Eval the built-in self-test set against the indexed roam-code repo
roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl

# Sweep weight vectors (α / β / γ / δ / ε)
roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl --sweep

# Pipe to a CI gate (0.85 is the current pinned floor -- see below)
roam --json eval-retrieve --tasks ... --min-recall-at-20 0.85
```

> **Warning — benchmark only against a fresh index.** `roam init` on an
> existing index takes the incremental path, which does not resync the
> FTS5 table. Measured on this bench with the same binary at the same
> commit, an incrementally-maintained index scores **0.781** recall@20
> against **0.914** for a fresh full build — a 13-point silent loss.
> Rebuilding only the FTS table on the stale index recovers 0.906,
> which is how the cause was isolated. Full evidence table and the
> underlying defect are in [SUBMISSION.md](SUBMISSION.md).

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

Measured **2026-08-06** at commit `0f3d3ac1`, roam-code 13.10.0, against
a **fresh full `roam init`** (4,944 files / 45,584 symbols / 94,498
edges), `--rerank fast`, no `[semantic]` extras:

<!-- canonical-recall:begin -- parsed by check_published_recall.py; keep the format -->
```
recall@5  = 0.642
recall@10 = 0.772
recall@20 = 0.914
```
<!-- canonical-recall:end -->

| K  | mean recall | comment |
|----|-------------|---------|
|  5 | **0.642** | above the "solvable from retrieve alone" bar |
| 10 | **0.772** | agents mostly stop double-checking here |
| 20 | **0.914** | the headline number |

Exact command, index provenance, per-signal ablations, and the reasons to
distrust a stale index are in [SUBMISSION.md](SUBMISSION.md). These
numbers are enforced in CI by `check_published_recall.py` (see below) —
if you change the retriever and this table is not updated, the build fails.

### Historical baselines

Kept so the trend is auditable. Neither row has been re-measured; both
predate the v12.1–v12.3 retrieval work.

| when | commit | recall@5 | recall@10 | recall@20 | note |
|---|---|---|---|---|---|
| 2026-05-01 | `78de9ee` | 0.286 | 0.358 | 0.503 | 30-task bench, pre-v12.1 retriever |
| — | — | — | — | 0.433 | prior 10-task bench |

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
roam --json eval-retrieve --tasks bench/retrieve/roam_self.jsonl > eval.json
python bench/retrieve/check_published_recall.py --eval-json eval.json
```

* **Tolerance: ±0.06 absolute**, per K. Chosen from measured variance,
  not guessed: CI clones at `fetch-depth: 50`, and emptying the
  `git_cochange` table entirely — a strictly larger perturbation than a
  shallow clone — moves recall@20 by 0.011. The remaining headroom
  absorbs platform and tokenizer differences. It is far tighter than the
  0.133 drift that motivated the gate.
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
