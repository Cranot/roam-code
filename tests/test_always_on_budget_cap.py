"""Regression: the always_on probe budget must cap a BLOCKING probe.

W42-followup (2026-06-05): `as_completed` without a timeout WAITED for the
slowest future, and the wall budget (checked only as each future COMPLETED)
therefore never fired against a single probe that blocked. Live compile-runs
telemetry showed always_on tailing to 20005ms (the section timeout) on a real
fraction of compiles, making those compiles take 30-56s before the agent even
started. The fix gives `as_completed` the budget as its timeout and shuts the
pool down with `wait=False`. This pins the behaviour: a long-blocking probe must
not extend the phase past ~budget, the fast probe's result must still be
captured, and the blocking probe's result must be discarded.
"""

from __future__ import annotations

import threading
import time

import roam.plan.compiler as compiler


def test_always_on_budget_caps_blocking_probe(monkeypatch):
    # An Event lets us release the "blocking" probe at teardown so it leaves no
    # lingering background thread (the fix runs it orphaned with wait=False).
    release = threading.Event()

    def slow(task, named, cwd, proc):
        release.wait(20)  # blocks well past the budget (released at teardown)
        return {"slow": "should_be_discarded"}

    def fast(task, named, cwd, proc):
        return {"fast_ok": 1}

    monkeypatch.setattr(compiler, "_L1_ALWAYS_ON_PROBES", [("slow", slow), ("fast", fast)])
    monkeypatch.setattr(compiler, "_W42_ALWAYS_ON_BUDGET_MS", 800)

    try:
        t0 = time.monotonic()
        out = compiler._apply_always_on_extenders(
            "freeform_explore", "unique always-on budget test task xyz", [], None, {}
        )
        elapsed = time.monotonic() - t0

        # Pre-fix this was ~20s (the blocker gated the phase). Now ~budget.
        assert elapsed < 4.0, f"budget not enforced: phase took {elapsed:.1f}s"
        # The fast probe completed within budget — its result is kept.
        assert out.get("fast_ok") == 1, "fast probe result was lost"
        # The blocking probe never completed in time — its result is discarded.
        assert "slow" not in out, "blocking probe's late result must not leak in"
    finally:
        release.set()  # let the orphaned probe thread exit immediately


def test_inner_probe_bounded_caps_runaway(monkeypatch):
    """The synchronous procedure probe (inner_probe) must be wall-capped too.

    After the always_on budget was made effective, inner_probe became the last
    uncapped synchronous call on the compile critical path. `_probe_for_procedure_bounded`
    runs it with a hard timeout, degrading to {} on a runaway (the compile keeps
    the rest of the prefetch). Pins: a blocking probe is capped at the budget and
    a fast one passes through unchanged.
    """
    release = threading.Event()

    def runaway(procedure, named, cwd, task=None):
        release.wait(20)
        return {"runaway": "discard"}

    monkeypatch.setattr(compiler, "_probe_for_procedure", runaway)
    try:
        t0 = time.monotonic()
        out = compiler._probe_for_procedure_bounded("freeform_explore", [], None, "t", 0.8)
        elapsed = time.monotonic() - t0
        assert elapsed < 4.0, f"inner_probe not capped: {elapsed:.1f}s"
        assert out == {}, "runaway probe result must be discarded"
    finally:
        release.set()

    monkeypatch.setattr(compiler, "_probe_for_procedure", lambda procedure, named, cwd, task=None: {"ok": 1})
    assert compiler._probe_for_procedure_bounded("p", [], None, "t", 8.0) == {"ok": 1}
