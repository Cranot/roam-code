"""Agent-mode telemetry stamping (measurement integrity).

Every compile writes a row to ``.roam/compile-runs.jsonl`` stamped with
``agent_mode`` (``os.environ["ROAM_AGENT_MODE"]``). That field feeds the L1-rate
and latency KPIs — so rows produced by benchmarks, corpus sweeps, diff tools,
and the test battery must be *distinguishable* from real production traffic, or
every reported number silently mixes them in.

``ROAM_AGENT_MODE`` is dual-purpose: it is also the mode-**policy** signal, but
policy only honors values in ``VALID_MODES`` (read_only/safe_edit/...), so a
telemetry stamp like ``bench`` or ``test`` is inert to policy (exactly how the
pre-existing ``compile_cache_build`` stamp coexists). This module centralizes
the set/restore dance the cache-warmer hand-rolled, and names the non-production
stamps in one place so the stats reader can exclude them by construction.
"""

from __future__ import annotations

import contextlib
import os

ENV_VAR = "ROAM_AGENT_MODE"

# Telemetry-only stamps (NOT mode-policy modes) marking a row as non-production.
# The stats reader default-excludes these from KPI aggregates; --include-bench
# (and --by-mode) still surface them.
MODE_BENCH = "bench"  # roam bench-compile
MODE_CORPUS = "corpus"  # roam compiler-corpus
MODE_TRACE = "trace"  # roam dispatch-trace
MODE_ENVELOPE_DIFF = "envelope_diff"  # roam envelope-diff
MODE_CACHE_BUILD = "compile_cache_build"  # roam compile-cache build (pre-existing)
MODE_TEST = "test"  # the pytest battery (conftest default)
MODE_HOOK = "hook"  # UPS-hook production channel (real traffic, explicitly stamped)

# Rows to drop from KPI aggregates (L1-rate, latency, top-misses) by default.
# MODE_HOOK is production and is NOT here. 'unknown' stays in — historically it
# is a MIXED bucket, so dropping it would hide real traffic; the stats output
# discloses that caveat instead. See ``unknown_cohort`` below for exactly what
# is mixed and why (it is NOT "the field did not exist yet").
NON_PRODUCTION_MODES = frozenset({MODE_BENCH, MODE_CORPUS, MODE_TRACE, MODE_ENVELOPE_DIFF, MODE_CACHE_BUILD, MODE_TEST})

# ---------------------------------------------------------------------------
# 'unknown' cohort provenance (measurement-integrity follow-up to ruler-1).
#
# VERIFIED against compile-runs.jsonl and git history (2026-07-27): the
# `agent_mode` field itself — `os.environ.get(ENV_VAR, "unknown")` written on
# every compile-runs.jsonl row — has existed since commit ffa51bb1
# (2026-06-10). This module and its call-site stamping landed a month later,
# in commit 19e74bd5 (2026-07-16). So `agent_mode == "unknown"` NEVER means
# "the field did not exist for this row" — every row in the dataset postdates
# ffa51bb1. It always means exactly one thing: ROAM_AGENT_MODE was unset in
# the process environment at compile time.
#
# What DOES change at 19e74bd5 is composition, not existence: before it, the
# bench/corpus/trace/envelope-diff/hook call sites had no wrapper setting the
# env var, so any of THAT traffic which ran in that window also fell into
# "unknown" — a wider, more mixed bucket. After it, "unknown" narrows to
# whatever call sites still have no wrapper (plain CLI/MCP usage). A single
# blended "unknown" L1-rate or latency number spanning this boundary is
# silently averaging over two differently-composed cohorts.
#
# Hour-bucketed to match compile-runs.jsonl's `ts` field
# (see roam.compile_telemetry.bucket_telemetry_ts).
AGENT_MODE_COVERAGE_EPOCH = "2026-07-16T13:00:00Z"


def unknown_cohort(row: dict) -> str | None:
    """Classify one telemetry row's ``agent_mode == "unknown"`` sub-cohort.

    Returns ``None`` when the row is not in the unknown bucket at all (it has
    an explicit, non-default ``agent_mode``). Otherwise returns:

    - ``"unknown_pre_coverage"`` — ``ts`` predates (or is missing/unparseable
      relative to) :data:`AGENT_MODE_COVERAGE_EPOCH`. The wider, historically
      mixed bucket: may include un-wrapped bench/corpus/trace/envelope-diff/
      hook traffic alongside organic usage.
    - ``"unknown_post_coverage"`` — ``ts`` is at/after the epoch. The
      narrower bucket: every wrapped call site now stamps away from the
      default, so this is (to the best of the writer's knowledge) organic
      CLI/MCP usage with no wrapper.

    A consumer that reports on "unknown" as a single number cannot tell these
    apart; group by this function instead of trusting one blended average.
    """
    mode = row.get("agent_mode")
    mode = mode if isinstance(mode, str) and mode else "unknown"
    if mode != "unknown":
        return None
    ts = row.get("ts")
    if isinstance(ts, str) and ts >= AGENT_MODE_COVERAGE_EPOCH:
        return "unknown_post_coverage"
    return "unknown_pre_coverage"


@contextlib.contextmanager
def agent_mode(value: str):
    """Temporarily stamp ``ROAM_AGENT_MODE`` for compiles in this block.

    Restores the prior value (or unsets it) on exit — safe for the long-lived
    MCP server and for nested/parallel command dispatch within one process.
    """
    prior = os.environ.get(ENV_VAR)
    os.environ[ENV_VAR] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = prior


def is_non_production(row: dict) -> bool:
    """True when a telemetry row was produced by a non-production channel."""
    return (row.get("agent_mode") or "unknown") in NON_PRODUCTION_MODES
