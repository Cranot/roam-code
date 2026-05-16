"""W1289 — regression test for ``roam mcp-status`` Pattern-1A discipline.

P0.2 fresh-install smoke (``docs/fresh-install-smoke.md`` §"Smoke findings",
item 2) caught ``roam mcp-status`` surfacing a bare ``KeyError: 'symbol'``
verdict line on a fresh install. That's a Pattern 1 variant A violation:
the cold-start failure path must produce the canonical structured envelope,
not a one-line dump of the underlying Python exception (CLAUDE.md
§"Pattern-1 family — (A) Hang on missing prerequisite").

These tests pin the canonical envelope shape for the failure path:

* ``status: "index_not_built"``
* ``isError: true``
* ``summary.state: "not_initialized"``
* ``error_code: "INDEX_NOT_BUILT"``
* ``next_command: "roam init"`` (literal, copy-paste-executable)
* ``agent_contract.facts`` (LAW 4 anchored)
* No raw Python traceback in stdout/stderr.

The happy path (MCP server module loads) is covered elsewhere by the
``mcp-status`` smoke surface — these tests scope to the failure path only,
which is the one the smoke surfaced.
"""

from __future__ import annotations

import json
import sys

from click.testing import CliRunner


def _force_mcp_import_failure(monkeypatch):
    """Force ``from roam.mcp_server import ...`` to raise ``KeyError('symbol')``.

    Mirrors the failure mode surfaced by the P0.2 fresh-install smoke
    (docs/fresh-install-smoke.md item 2) without relying on a particular
    Python build / environment combination. The cmd_mcp_status handler's
    ``except Exception`` clause must produce the canonical envelope
    regardless of which exception type was raised.
    """
    # Evict any cached module so the next import statement re-runs the
    # finder; install a finder that fails the way the smoke captured.
    sys.modules.pop("roam.mcp_server", None)

    class _Boom:
        def find_spec(self, name, path=None, target=None):
            if name == "roam.mcp_server":
                # The exact KeyError shape the smoke caught. The handler
                # must structure it correctly regardless of the raw type.
                raise KeyError("symbol")
            return None

    boom = _Boom()
    monkeypatch.setattr(sys, "meta_path", [boom, *sys.meta_path])


def test_mcp_status_cold_start_text_mode(tmp_path, monkeypatch):
    """Text-mode failure path emits the canonical verdict, no traceback."""
    monkeypatch.chdir(tmp_path)
    _force_mcp_import_failure(monkeypatch)

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-status"], catch_exceptions=False)

    assert result.exit_code == 0, f"non-zero exit: {result.output!r}"
    assert "VERDICT:" in result.output
    assert "roam init" in result.output
    # No raw Python traceback should leak through the structured handler.
    assert "Traceback" not in result.output


def test_mcp_status_cold_start_json_mode(tmp_path, monkeypatch):
    """JSON-mode failure path produces the canonical Pattern-1A envelope."""
    monkeypatch.chdir(tmp_path)
    _force_mcp_import_failure(monkeypatch)

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "mcp-status"], catch_exceptions=False)

    assert result.exit_code == 0, f"non-zero exit: {result.output!r}"
    raw = getattr(result, "stdout", None) or result.output
    parsed = json.loads(raw)

    # Canonical Pattern-1A failure-envelope shape (CLAUDE.md §"Pattern-1 family").
    assert parsed["command"] == "mcp-status"
    assert parsed["status"] == "index_not_built"
    assert parsed["isError"] is True
    assert parsed["error_code"] == "INDEX_NOT_BUILT"
    assert parsed["next_command"] == "roam init"
    assert "roam init" in parsed["hint"]

    summary = parsed["summary"]
    assert summary["state"] == "not_initialized"
    assert summary["partial_success"] is False
    assert summary["level"] == "warning"
    assert "roam init" in summary["verdict"]

    agent_contract = parsed["agent_contract"]
    assert isinstance(agent_contract["facts"], list)
    assert len(agent_contract["facts"]) >= 1
    assert "roam init" in agent_contract["next_commands"]


def test_mcp_status_no_keyerror_traceback_in_output(tmp_path, monkeypatch):
    """The raw ``KeyError: 'symbol'`` must NOT appear as a bare verdict line.

    The pre-W1289 behaviour was to print
    ``VERDICT: MCP server module unavailable: KeyError: 'symbol'`` and
    return — a Pattern-1A discipline violation because consumers reading
    only the verdict line had no next-action signal.
    """
    monkeypatch.chdir(tmp_path)
    _force_mcp_import_failure(monkeypatch)

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-status"], catch_exceptions=False)

    # The VERDICT line itself must be the canonical "run roam init" message,
    # NOT the raw "MCP server module unavailable: KeyError: 'symbol'" form.
    verdict_lines = [ln for ln in result.output.splitlines() if ln.startswith("VERDICT:")]
    assert len(verdict_lines) == 1, f"expected exactly one VERDICT line, got: {verdict_lines!r}"
    verdict_line = verdict_lines[0]
    assert "roam init" in verdict_line, f"VERDICT lacks next-action: {verdict_line!r}"
    # Underlying exception text may appear below the verdict (as diagnostic
    # detail), but never as the bare verdict line itself.
    assert "KeyError" not in verdict_line
