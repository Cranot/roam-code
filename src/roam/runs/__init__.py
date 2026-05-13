"""Per-agent-run event ledger substrate (R20).

Stores each agent run as a directory under ``.roam/runs/<run_id>/`` with
two files:

  - ``events.jsonl`` -- append-only event stream, one JSON object per line
  - ``meta.json``    -- single JSON blob holding run identity + status

This is the SUBSTRATE for R20. CGA signing, replay, agent-score and
audit-trail features build on top.

Pairs with R19 (``roam.memory``): memory captures durable knowledge,
runs capture transient per-session activity.
"""

from __future__ import annotations

from roam.runs.ledger import (
    RUN_ID_RE,
    RunMeta,
    end_run,
    list_runs,
    log_event,
    read_run_events,
    read_run_meta,
    run_dir,
    runs_root,
    start_run,
)
from roam.runs.signing import (
    SEED_SIGNATURE,
    compute_event_signature,
    ensure_ledger_key,
    ledger_key_path,
    verify_chain,
)

__all__ = [
    "RUN_ID_RE",
    "RunMeta",
    "SEED_SIGNATURE",
    "compute_event_signature",
    "end_run",
    "ensure_ledger_key",
    "ledger_key_path",
    "list_runs",
    "log_event",
    "read_run_events",
    "read_run_meta",
    "run_dir",
    "runs_root",
    "start_run",
    "verify_chain",
]
