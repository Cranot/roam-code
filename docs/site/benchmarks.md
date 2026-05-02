# Roam — Accuracy & Benchmarks

External reviewers (and we agree) flag that **roam-code's benchmarks
are self-bench-heavy**. This page is the honest version of what's
been measured, what hasn't, and how to reproduce.

## TL;DR

| Bench | What it measures | Result | Reproducer |
|---|---|---|---|
| Self-bench (in-tree) | recall@K on 30 hand-curated tasks against roam's own codebase | recall@5 0.656, recall@10 0.769, recall@20 **0.900** | `roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl` |
| Cross-repo (synthetic) | recall@K on a generic Python microservice (auth + payments + notifications) | 1.000 / 1.000 / 1.000 | `pytest tests/test_retrieve_cross_repo.py` |
| Detector E2E | every Python idiom detector finds the right line on a fixture project | 12/12 detectors hit the expected line ±2 | `pytest tests/test_python_idioms_e2e.py` |
| Detector roundup on roam-code | 19 detectors against roam's own ~14k symbols | 0 findings on the 16 high-confidence detectors after self-fix; 24+168+386 on the 3 low-confidence ones (lambda-in-loop / except-pass / broad-except — many legit) | `python -c "import sqlite3; from roam.catalog.python_idioms import *; ..."` (snippet at end) |
| Detector at scale | 19 detectors against supernode (17k files, 255k symbols) | 167 open-leak + 4 sync-in-async + 146 bare-except real findings | indexed offline; numbers from session log |

## The retrieve arc — what improved over today's iterations

| Version | recall@5 | recall@10 | recall@20 | Notes |
|---|---|---|---|---|
| v12.0 baseline | 0.289 | 0.358 | 0.486 | initial release |
| v12.3 (path-token boost + dedup + neighbour expansion) | 0.581 | 0.775 | 0.897 | +30 pp R@5, +37 pp R@10, +41 pp R@20 |
| v12.4 (Python-pivot foundations) | 0.600 | 0.794 | 0.903 | +1.9 pp R@5 |
| v12.5+ | 0.656 | 0.769 | 0.900 | small R@10 trade for async-aware boost |

Reproducible: revert any version and re-run
``roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl``.

## What's still self-bench

The 30-task suite (`bench/retrieve/roam_self.jsonl`) is curated by
the maintainer on the maintainer's own codebase. **Expect lower
numbers on external benches.** This is acknowledged in
``bench/retrieve/SUBMISSION.md`` — the file is also bench-portable
(``--emit-format coderag|beir``) so external reviewers can drop the
JSONL into CodeRAG-Bench / BEIR / trec_eval directly.

## What's NOT yet measured (open work)

- **CodeRAG-Bench public submission** — JSONL is generated; not yet
  uploaded.
- **Baseline-vs-roam table** — grep, ripgrep, aider repo map, CKB,
  Codebase-Memory, Cursor (where measurable). Tracked as
  ``REV3-followup`` in `internal/review_actions_external_2026-05-02.md`.
- **20-30 external repos cross-section** — 5-10 today on synthetic +
  agi-in-md / supernode / roam-agent-eval / deep-research. Need
  larger repo sweep with hand-validated answers.
- **Per-detector precision/recall** — we have raw counts but no
  hand-labelled ground truth.

## How to run roam against your own repo

```bash
pip install roam-code
cd your-repo
roam init                           # 1 build the index (~5s for typical Python)
roam understand                     # 2 sanity-check what got indexed
roam eval-retrieve \
    --tasks bench/retrieve/roam_self.jsonl \
    --emit-format coderag \
    --emit-out my_roam_run.jsonl    # 3 emit a CodeRAG-Bench file for your repo
```

Send us your numbers — open an issue at
https://github.com/Cranot/roam-code/issues with the JSONL attached
and we'll add the result to this table.

## Detector findings on roam-code itself (post-fix)

Roam was used to find 3 real `open()` resource leaks in its own
production code (`cmd_agent_export.py:626` + 2 test sites). All 3
were fixed in v12.5; detectors now report 0 findings on the 16
high-confidence patterns:

| Detector | Findings on roam-code |
|---|---|
| py-mutable-default-arg | 0 |
| py-bare-except | 0 |
| py-none-eq | 0 |
| py-logger-fstring | 6 (post-string-strip-removal — tracked) |
| py-sync-in-async | 0 |
| py-open-without-with | 0 (was 3, fixed) |
| py-star-import | 0 |
| py-dict-keys-iter | 0 |
| py-async-not-awaited | 0 |
| py-async-with-missing | 0 |
| py-type-eq | 0 |
| py-lock-without-with | 0 |
| py-sync-calls-async | 0 |
| py-django-n1 | 0 |
| py-sqlalchemy-lazy | 0 |
| py-fastapi-depends | 0 |
| py-lambda-in-loop | 24 (mostly Click callbacks — low confidence) |
| py-except-pass | 168 (defensive CLI swallows — low confidence) |
| py-broad-except | 386 (defensive `except Exception:` — low confidence) |

Real findings on supernode (17k-file external repo):

| Detector | Real findings |
|---|---|
| py-open-without-with | **167** |
| py-sync-in-async | **4** |
| py-bare-except | **146** |

This is signal enough to validate that the detectors fire on real
code. Per-finding precision audit is open work.

## What this page is and isn't

**This is**: an honest snapshot of what we've measured, what
remains, and what reproducers exist.

**This isn't**: a "we beat X" leaderboard. The Cursor /
Sourcegraph / Codebase-Memory headline numbers are not directly
comparable to ours without identical task sets.

The next leap is exactly what the external reviewer asked for: a
20-30 external-repo bench against named baselines, scripted and
public. Expected in v12.8 or v12.9.
