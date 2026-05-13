"""Tests for Fix B (SYNTHESIS Pattern 5) — compound recipe registry.

Two confirmed compound recipes shipped with internal-command typos:
``for_security_review`` called ``roam vuln`` (CLI key is ``vulns``)
and ``for_refactor`` called ``roam complexity-report`` (CLI key is
``complexity``). The registry + import-time sanity check makes both
typos impossible to ship again.
"""

from __future__ import annotations

import pytest


def test_every_registry_value_is_a_live_cli_command():
    """The import-time check already enforces this, but exercise it
    here too so a future hand-edit doesn't slip past."""
    from roam.cli import _COMMANDS
    from roam.mcp_server import _COMPOUND_REGISTRY

    missing = [v for v in _COMPOUND_REGISTRY.values() if v not in _COMMANDS]
    assert missing == [], f"compound registry references missing CLI commands: {missing}"


def test_for_refactor_resolves_to_complexity_not_complexity_report():
    """SYNTHESIS Pattern 5 — the dogfood-corpus bug. Confirm the registry
    resolves ``complexity`` to the live CLI key (not ``complexity-report``)
    and that the literal typo cannot re-enter."""
    from roam.cli import _COMMANDS
    from roam.mcp_server import _COMPOUND_REGISTRY, _cr

    assert _cr("complexity") == "complexity"
    assert "complexity" in _COMMANDS
    # Regression guard: the legacy typo must not appear in _COMMANDS,
    # which would let a future drift re-introduce the bug.
    assert "complexity-report" not in _COMMANDS
    # And the registry must point at the real key.
    assert _COMPOUND_REGISTRY["complexity"] == "complexity"


def test_for_security_review_resolves_to_vulns_not_vuln():
    """SYNTHESIS Pattern 5 — companion bug. Same as above but for the
    ``vuln`` → ``vulns`` typo."""
    from roam.cli import _COMMANDS
    from roam.mcp_server import _COMPOUND_REGISTRY, _cr

    assert _cr("vulns") == "vulns"
    assert "vulns" in _COMMANDS
    assert "vuln" not in _COMMANDS
    assert _COMPOUND_REGISTRY["vulns"] == "vulns"


def test_unknown_key_raises_keyerror_with_helpful_message():
    """A compound author that references a key not in the registry gets
    a clear KeyError naming the missing key — not an opaque CLI ``no
    such command`` error wrapped in a partial-success envelope."""
    from roam.mcp_server import _cr

    with pytest.raises(KeyError) as exc:
        _cr("definitely-not-a-real-key")
    assert "definitely-not-a-real-key" in str(exc.value)


def test_for_refactor_invokes_complexity_not_complexity_report(monkeypatch):
    """End-to-end: invoke for_refactor and verify the subcommand args
    list passed to _run_roam includes ``complexity`` and never
    ``complexity-report``. This is the test the dogfood corpus wanted
    so the typo never ships silently again."""
    from roam.mcp_server import for_refactor

    called_args: list[list[str]] = []

    def fake_run(args, root="."):
        called_args.append(list(args))
        return {"summary": {"verdict": "ok"}}

    monkeypatch.setattr("roam.mcp_server._run_roam", fake_run)
    out = for_refactor("some_symbol", root=".")
    assert isinstance(out, dict)
    # Find the complexity invocation.
    complexity_calls = [a for a in called_args if a and a[0] == "complexity"]
    bad_calls = [a for a in called_args if a and a[0] == "complexity-report"]
    assert complexity_calls, f"expected a 'complexity' call, got {called_args!r}"
    assert not bad_calls, f"'complexity-report' typo re-introduced: {bad_calls!r}"


def test_for_security_review_invokes_vulns_not_vuln(monkeypatch):
    """End-to-end: invoke for_security_review and verify the subcommand
    args list includes ``vulns`` and never ``vuln``."""
    from roam.mcp_server import for_security_review

    called_args: list[list[str]] = []

    def fake_run(args, root="."):
        called_args.append(list(args))
        return {"summary": {"verdict": "ok"}}

    monkeypatch.setattr("roam.mcp_server._run_roam", fake_run)
    out = for_security_review("", root=".")
    assert isinstance(out, dict)
    vulns_calls = [a for a in called_args if a and a[0] == "vulns"]
    bad_calls = [a for a in called_args if a and a[0] == "vuln"]
    assert vulns_calls, f"expected a 'vulns' call, got {called_args!r}"
    assert not bad_calls, f"'vuln' typo re-introduced: {bad_calls!r}"


def test_compound_all_failed_reports_partial_success_true():
    """SYNTHESIS Pattern 2 (silent fallback) — when ALL subcommands in a
    compound fail, ``partial_success`` must flip True. Previously it
    was False because the guard required at least one survivor."""
    from roam.mcp_server import _compound_envelope

    sections = [
        ("a", {"error": "fail-a"}),
        ("b", {"error": "fail-b"}),
    ]
    env = _compound_envelope("for-test", sections, situation="test", target="x")
    summary = env.get("summary") or {}
    assert summary.get("partial_success") is True
    # And the verdict must NOT say "compound operation completed" —
    # that was the lie. It should name the failure count instead.
    assert "completed" not in summary.get("verdict", "")
    assert "failed" in summary.get("verdict", "").lower()
