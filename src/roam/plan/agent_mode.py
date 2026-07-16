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
# is a MIXED bucket (all pre-stamp rows), so dropping it would hide real traffic;
# the stats output discloses that caveat instead.
NON_PRODUCTION_MODES = frozenset({MODE_BENCH, MODE_CORPUS, MODE_TRACE, MODE_ENVELOPE_DIFF, MODE_CACHE_BUILD, MODE_TEST})


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
