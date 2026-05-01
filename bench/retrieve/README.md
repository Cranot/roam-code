# Retrieval eval harness (A.0.4)

A small infrastructure for measuring `roam retrieve` quality against a
labeled set of (task, expected_files) pairs.

## Quick start

```bash
# Eval the built-in self-test set against the indexed roam-code repo
roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl

# Sweep weight vectors (α / β / γ / δ / ε)
roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl --sweep

# Pipe to a CI gate
roam --json eval-retrieve --tasks ... --min-recall-at-20 0.6
```

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

Measured 2026-05-01 against the indexed roam-code repo (HEAD = `78de9ee`):

| K  | mean recall | comment |
|----|-------------|---------|
|  5 | **0.286** | minimum useful — half of tasks miss the headline file |
| 10 | **0.358** | the regime where agents start to stop double-checking |
| 20 | **0.503** | the bar for "agent has enough to act" |

Compare to the prior 10-task baseline (recall@20 = 0.433). The 30-task
bench — which spans 12 subsystems and is built from real merged commits —
is more representative and gives β=0.15 a clearer win in the sweep grid:

| α | β | recall@20 | rank |
|---|---|-----------|------|
| 0.3-0.5 | **0.15** | **0.539** | tied 1st (6 combos) |
| 0.3-0.4 | 0.25 (default) | 0.503 | 7th-10th |
| any     | 0.35 | < 0.503 | tail |

**Open follow-up:** the sweep favours β=0.15 by ~3.6 points across all
α values. Defaults stay at β=0.25 until either (a) the bench grows past
50 tasks or (b) the lift survives a controlled sweep on a non-roam repo.
See `src/roam/config.py:DEFAULT_RETRIEVE_WEIGHTS`.

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
