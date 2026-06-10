"""W131 in-proc dispatch serialization — regression guard.

2026-06-09: `roam --json compiler-corpus` over a 717-prompt corpus emitted
ONLY a stray in-proc `grep` envelope on stdout (exit 0) — the aggregate
envelope was lost. Root cause: CliRunner.invoke swaps the process-global
sys.stdout, and the W132/W145 "bypass the lock when no chdir is needed"
optimization let the W125 parallel probe pool run concurrent unlocked
invokes. The stdout swap raced: one probe's envelope leaked to the REAL
stdout while the parent command's output was swallowed into a probe's
capture buffer.

The fixed invariant: `_roam_invoke_inproc` NEVER runs `_invoke_cli`
concurrently from two threads, chdir-bypass path included. This test
monkeypatches `_invoke_cli` with a concurrency recorder and hammers the
bypass path (cwd == os.getcwd()) from 8 threads — pre-fix it observes
overlap > 1, post-fix it must stay at exactly 1.
"""

from __future__ import annotations

import os
import threading
import time

import roam.plan.compiler as compiler


def test_inproc_invoke_is_serialized_on_chdir_bypass_path(monkeypatch):
    overlap = {"current": 0, "max": 0}
    gate = threading.Lock()

    def fake_invoke(runner, cli, args):
        with gate:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
        time.sleep(0.01)  # widen the race window
        with gate:
            overlap["current"] -= 1
        return (0, "{}")

    monkeypatch.setattr(compiler, "_invoke_cli", fake_invoke)
    # Force the runner cache to look available without importing the CLI.
    monkeypatch.setattr(compiler, "_get_cached_cli_runner", lambda: (object(), object()))
    monkeypatch.setattr(compiler, "_ROAM_INPROC_ENABLED", True)

    cwd = os.getcwd()  # bypass path: no chdir needed
    threads = [
        threading.Thread(target=lambda: compiler._roam_invoke_inproc(["--json", "health"], cwd)) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlap["max"] == 1, (
        f"_invoke_cli ran {overlap['max']}x concurrently — the stdout-swap "
        "race that lost the compiler-corpus aggregate envelope is back"
    )


def test_inproc_lock_is_reentrant():
    """A same-thread re-entrant acquire must not deadlock (RLock contract):
    an in-proc command that itself reaches _run_roam would self-deadlock on
    a plain Lock now that the bypass path locks too."""
    acquired = compiler._ROAM_INPROC_LOCK.acquire(timeout=1)
    assert acquired
    try:
        nested = compiler._ROAM_INPROC_LOCK.acquire(timeout=1)
        assert nested, "_ROAM_INPROC_LOCK is not re-entrant"
        compiler._ROAM_INPROC_LOCK.release()
    finally:
        compiler._ROAM_INPROC_LOCK.release()
