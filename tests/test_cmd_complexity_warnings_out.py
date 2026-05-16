"""W1086 — Pattern 1B + Pattern 2 ``warnings_out`` tests for ``cmd_complexity``.

Drives the accumulator plumbed in W1086 through the two silent-fallback sites
in ``cmd_complexity.py``:

- **Count-probe failure** (Pattern 1B advisory): if the
  ``SELECT COUNT(*) FROM symbol_metrics`` query throws, the prior code
  silently set ``count = -1`` and rendered the ``no_complexity_data`` envelope
  with no signal that the probe itself failed. W1086 appends a structured
  warning so consumers know the empty state was probe-driven, not data-driven.

- **--persist on pre-W89 schema** (Pattern 2 silent fallback): the
  ``except sqlite3.OperationalError`` around ``_persist_complexity_findings``
  silently no-oped on a DB without the ``findings`` table. W1086 appends a
  structured warning so ``--persist`` users see "I asked for findings,
  none were written, here's why."

Per-row ``_safe_metric`` ``KeyError`` / ``IndexError`` swallows stay silent
by design — they fire many times per row and would spam the accumulator.

Cross-links:
- W918 — canonical ``warnings_out`` plumb-through pattern (cmd_alerts).
- W1019c — ``cmd_budget`` ``warnings_out`` reference test
  (``tests/test_cmd_budget_warnings_out.py``).
- CLAUDE.md "Six systemic anti-patterns" / Pattern 1B + Pattern 2.

Hash stability (W1086 invariant): when ``warnings`` is empty, the JSON
envelope is byte-identical to pre-W1086. The two assertions below pin the
omit-when-empty contract: (a) no ``warnings_out`` key in the envelope, and
(b) no ``partial_success`` in summary.
"""

from __future__ import annotations

import json as _json
import sqlite3

from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_complexity_json(project_path, *extra):
    """Run ``roam --json complexity`` in-process under the given cwd."""
    import os

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, ["--json", "complexity", *extra], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _invoke_complexity_text(project_path, *extra):
    """Run ``roam complexity`` (text mode) in-process under the given cwd.

    Click 8.3+ always separates stdout/stderr; ``result.stderr`` works without
    ``mix_stderr=False`` (which was removed in 8.3).
    """
    import os

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, ["complexity", *extra], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Hash-stability invariant — no warnings -> envelope byte-identical
# ---------------------------------------------------------------------------


def test_happy_path_omits_warnings_out_key(indexed_project):
    """Healthy index produces NO ``warnings_out`` key (hash stability).

    Note: ``summary.partial_success`` is centrally injected by
    ``json_envelope`` (W817) and defaults to ``False`` on every envelope; the
    Pattern-2 contract is that ``warnings_out`` fires ``True`` when warnings
    are non-empty, and stays ``False`` otherwise. We assert the centralised
    ``False`` default here so any future regression to ``True`` on the happy
    path fails loudly.
    """
    result = _invoke_complexity_json(indexed_project)
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.stdout if hasattr(result, "stdout") else result.output)
    # Hash-stable contract: the top-level key is OMITTED when empty.
    assert "warnings_out" not in payload, (
        f"warnings_out should be absent on happy path; got: {payload.get('warnings_out')!r}"
    )
    # W817 centrally injects partial_success=False; assert it stays False here.
    assert payload["summary"].get("partial_success") is False, (
        f"summary.partial_success must be False on happy path; got: {payload['summary'].get('partial_success')!r}"
    )


def test_happy_path_text_emits_no_warning_lines(indexed_project):
    """Text-mode happy path: no ``WARNING:`` lines on stderr."""
    result = _invoke_complexity_text(indexed_project)
    assert result.exit_code == 0, result.output
    # CliRunner(mix_stderr=False) exposes stderr separately.
    assert "WARNING:" not in (result.stderr or ""), f"unexpected WARNING on happy-path stderr: {result.stderr!r}"


# ---------------------------------------------------------------------------
# Pattern 2 — --persist with pre-W89 findings table missing
# ---------------------------------------------------------------------------


def test_persist_pre_w89_findings_table_missing_warns(indexed_project, monkeypatch):
    """Simulate pre-W89 schema: ``_persist_complexity_findings`` raises
    ``sqlite3.OperationalError``. The CLI must surface a structured warning.

    We monkeypatch the helper rather than dropping the ``findings`` table on
    disk because ``open_db`` runs ``ensure_schema`` on every read, which
    re-creates the table via ``CREATE TABLE IF NOT EXISTS``. The
    OperationalError path we want to exercise fires when the persist call
    discovers a genuinely-missing table mid-execution.
    """
    from roam.commands import cmd_complexity

    def _raise_pre_w89(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings")

    monkeypatch.setattr(cmd_complexity, "_persist_complexity_findings", _raise_pre_w89)

    result = _invoke_complexity_json(indexed_project, "--persist")
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.stdout if hasattr(result, "stdout") else result.output)
    # warnings_out should be present + non-empty.
    assert "warnings_out" in payload, f"--persist on pre-W89 DB must surface warnings_out; got keys: {list(payload)}"
    warnings = payload["warnings_out"]
    assert isinstance(warnings, list)
    assert len(warnings) >= 1
    # The structured message must name the pre-W89 condition.
    joined = "\n".join(warnings)
    assert "findings table missing" in joined, f"expected 'findings table missing' in warnings, got: {joined!r}"
    assert "pre-W89" in joined, f"expected 'pre-W89' anchor in warnings, got: {joined!r}"
    # partial_success must flip in summary when warnings fired.
    assert payload["summary"].get("partial_success") is True, (
        "summary.partial_success must be True when warnings_out is non-empty"
    )


def test_persist_pre_w89_text_emits_warning_on_stderr(indexed_project, monkeypatch):
    """Text-mode --persist on pre-W89 DB: ``WARNING:`` line on stderr."""
    from roam.commands import cmd_complexity

    def _raise_pre_w89(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings")

    monkeypatch.setattr(cmd_complexity, "_persist_complexity_findings", _raise_pre_w89)

    result = _invoke_complexity_text(indexed_project, "--persist")
    assert result.exit_code == 0, result.output
    stderr = result.stderr or ""
    assert "WARNING:" in stderr, f"expected a 'WARNING:' line on text-mode stderr, got: {stderr!r}"
    assert "findings table missing" in stderr


# ---------------------------------------------------------------------------
# Pattern 1B — symbol_metrics count probe failure
# ---------------------------------------------------------------------------


def test_count_probe_failure_warns_on_empty_envelope(indexed_project, monkeypatch):
    """Force the count probe to raise -> warning fires + envelope routes to
    the ``no_complexity_data`` path.

    sqlite3.Connection is a C-extension type whose ``execute`` slot is
    immutable. Wrap ``open_db`` so the yielded connection is a thin proxy
    that raises on the specific count statement and passes everything else
    through.
    """
    from contextlib import contextmanager

    from roam.commands import cmd_complexity
    from roam.db import connection as _db_conn_mod

    real_open_db = _db_conn_mod.open_db

    class _ProxyConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "SELECT COUNT(*) FROM symbol_metrics" in sql:
                raise sqlite3.OperationalError("simulated probe failure")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    @contextmanager
    def _wrapped_open_db(*args, **kwargs):
        with real_open_db(*args, **kwargs) as inner:
            yield _ProxyConn(inner)

    # Patch the binding the command module pulled in at import time.
    monkeypatch.setattr(cmd_complexity, "open_db", _wrapped_open_db)

    result = _invoke_complexity_json(indexed_project)
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.stdout if hasattr(result, "stdout") else result.output)
    assert payload["summary"]["state"] == "no_complexity_data"
    assert "warnings_out" in payload, f"count-probe failure must surface warnings_out; got keys: {list(payload)}"
    warnings = payload["warnings_out"]
    assert len(warnings) >= 1
    joined = "\n".join(warnings)
    assert "symbol_metrics" in joined and "count probe" in joined, (
        f"expected 'symbol_metrics count probe failed' anchor, got: {joined!r}"
    )
    assert payload["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# Sensitive-substring guardrail (W1086 redaction check)
# ---------------------------------------------------------------------------


def test_warning_strings_carry_no_absolute_paths(indexed_project, monkeypatch):
    """Warnings must NOT leak absolute paths (redactions[] mental model)."""
    from roam.commands import cmd_complexity

    def _raise_pre_w89(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings")

    monkeypatch.setattr(cmd_complexity, "_persist_complexity_findings", _raise_pre_w89)

    result = _invoke_complexity_json(indexed_project, "--persist")
    payload = _json.loads(result.stdout if hasattr(result, "stdout") else result.output)
    warnings = payload.get("warnings_out") or []
    for w in warnings:
        # No drive letters, no leading slashes, no .roam paths.
        assert ":\\" not in w, f"warning leaks Windows absolute path: {w!r}"
        assert not w.startswith("/"), f"warning leaks POSIX absolute path: {w!r}"
        assert ".roam" not in w, f"warning leaks .roam/ internal path: {w!r}"
