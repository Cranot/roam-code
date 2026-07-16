"""tail-1: `impact` must route through the killable subprocess, not in-process.

The structural_blast probe calls `roam impact <sym>` with an 8s budget. On the
in-process CliRunner path that budget is UNENFORCEABLE — the global runner lock
cannot be cancelled mid-scan (documented at compiler.py's _ROAM_INPROC_DENYLIST
for `dead`/`boundary`/`path-coverage`). A hot-symbol impact ran 14.7s live. This
guards that impact is denylisted so its cap becomes real (fast fallback instead
of a multi-second stall); the W147 result cache still serves warm hits.
"""

from __future__ import annotations

from roam.plan import compiler
from roam.plan.compiler import _ROAM_INPROC_DENYLIST, _roam_invoke_inproc


def test_impact_is_denylisted():
    assert "impact" in _ROAM_INPROC_DENYLIST


def test_impact_invoke_inproc_returns_none(monkeypatch):
    # even with the in-process path enabled, impact must decline it (-> None ->
    # caller falls back to the killable subprocess where timeout= is enforced)
    monkeypatch.setattr(compiler, "_ROAM_INPROC_ENABLED", True, raising=False)
    assert _roam_invoke_inproc(["impact", "some_symbol"], None) is None
    # also when a leading global flag precedes the subcommand
    assert _roam_invoke_inproc(["--json", "impact", "some_symbol"], None) is None


def test_other_scan_denylist_unchanged():
    # the existing O(repo) scans stay denylisted (no regression)
    for cmd in ("dead", "boundary", "path-coverage", "compile"):
        assert cmd in _ROAM_INPROC_DENYLIST
