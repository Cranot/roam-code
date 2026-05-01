"""roam retrieve eval harness (A.0.4).

Public API:

* :func:`harness.load_tasks` — read the JSONL ground-truth file.
* :func:`harness.evaluate_task` — run retrieve + compute per-task recall.
* :func:`harness.run_eval` — full sweep across tasks, optional weight sweep.

The CLI wrapper lives in :mod:`commands.cmd_eval_retrieve`.

Design choices (per the C.2 review):

* JSONL ground-truth format — line-per-task, plays nicely with
  ``gh pr list`` extraction and with manual editing.
* Recall@K only — no precision metric in v12.0. Retrieve is bounded
  by token budget, not candidate count, so precision-at-K is the
  wrong frame. Recall is what matters: did the agent see every file
  it actually needed?
* No GPL inputs. SWE-bench Pro is excluded by design; only MIT or
  first-party PR-mined tasks are valid bench inputs.
"""

from __future__ import annotations
