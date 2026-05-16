"""W1272 — Pattern-2c unresolved exit convention (Convention (c)).

The W1268 audit found a 5-way divergence across Pattern-2c command
unresolved-path exit behavior: some commands raised ``SystemExit(1)``,
others returned exit 0, and the shape of the structured envelope on
miss varied widely. W1272 collapses the divergence onto Convention (c):

* ``return`` (exit 0) on unresolved input
* structured envelope with ``resolution=unresolved`` +
  ``partial_success=True`` at BOTH the summary level and the top-level
  envelope keys (mirrors ``cmd_dead --extinction`` and ``cmd_annotate``)
* text-mode keeps the FTS suggestion list (most useful next step for a
  human caller staring at a typo)

The rationale: a typo is recoverable — the agent retries with a hint
or a different name. A non-zero exit conflates it with a tool/IO
failure (genuinely non-recoverable), which derails CI gating and
makes the failure mode untestable.

This file pins the 7 commands migrated by W1272:

* cmd_impact (was Convention (a) — auto_log + SystemExit(1))
* cmd_diagnose, cmd_safe_delete, cmd_closure, cmd_symbol, cmd_hover,
  cmd_context (were Convention (b) — symbol_not_found helper + SystemExit(1))

Excluded by W1268 audit:

* cmd_annotate (canonical W324 — state-mutation, exit 0 + text-only)
* cmd_dead --extinction (already Convention (c))
* cmd_trace (dual-disclosing via src/tgt extension fields)
* cmd_preflight (already Convention (c) on unresolved path)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def w1272_project(tmp_path):
    """Tiny indexed project with one trivial symbol.

    The unresolved-path tests never touch the indexed symbol; we just
    need a valid index so ``ensure_index()`` doesn't trigger a fresh
    indexer run on every CLI call.
    """
    proj = tmp_path / "w1272"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text(
        "def real_function():\n    return 42\n\ndef real_caller():\n    return real_function()\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


_MISSING_NAME = "definitely_no_such_symbol_w1272_xyz"


def _assert_unresolved_envelope(result, command_name: str) -> None:
    """Pattern-2c Convention (c) shape assertions.

    Every migrated command MUST:
    1. exit 0 (success-with-disclosure, not failure)
    2. emit a JSON envelope on stdout with ``resolution=unresolved``
       at both ``summary`` and top-level
    3. set ``partial_success=True``
    4. carry a ``not found`` verdict (LAW-6 single-line readability)
    """
    assert result.exit_code == 0, f"{command_name}: exit_code={result.exit_code}\n{result.output}"
    data = json.loads(result.output)
    assert data.get("command") == command_name, data
    # Top-level disclosure block (mirrors cmd_dead --extinction).
    assert data["resolution"] == "unresolved", data
    assert data["partial_success"] is True, data
    # Summary-level disclosure (LAW-6 readability — verdict works
    # without any other field).
    summary = data["summary"]
    assert summary["resolution"] == "unresolved", summary
    assert summary["partial_success"] is True, summary
    assert "not found" in summary["verdict"].lower(), summary


def test_impact_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(cli_runner, ["impact", _MISSING_NAME], cwd=w1272_project, json_mode=True)
    _assert_unresolved_envelope(result, "impact")


def test_diagnose_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(cli_runner, ["diagnose", _MISSING_NAME], cwd=w1272_project, json_mode=True)
    _assert_unresolved_envelope(result, "diagnose")


def test_safe_delete_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(
        cli_runner,
        ["safe-delete", _MISSING_NAME],
        cwd=w1272_project,
        json_mode=True,
    )
    _assert_unresolved_envelope(result, "safe-delete")


def test_closure_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(cli_runner, ["closure", _MISSING_NAME], cwd=w1272_project, json_mode=True)
    _assert_unresolved_envelope(result, "closure")


def test_symbol_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(cli_runner, ["symbol", _MISSING_NAME], cwd=w1272_project, json_mode=True)
    _assert_unresolved_envelope(result, "symbol")


def test_hover_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(cli_runner, ["hover", _MISSING_NAME], cwd=w1272_project, json_mode=True)
    _assert_unresolved_envelope(result, "hover")


def test_context_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(cli_runner, ["context", _MISSING_NAME], cwd=w1272_project, json_mode=True)
    _assert_unresolved_envelope(result, "context")


def test_preflight_unresolved_exits_zero(cli_runner, w1272_project, monkeypatch):
    """cmd_preflight was already Convention (c) per W1268 audit.

    This test pins the behaviour so a future refactor can't drift back
    to ``SystemExit(1)`` on the unresolved branch.
    """
    monkeypatch.chdir(w1272_project)
    result = invoke_cli(
        cli_runner,
        ["preflight", _MISSING_NAME],
        cwd=w1272_project,
        json_mode=True,
    )
    # Preflight's not-found envelope has its own shape — verify the
    # exit code + the unresolved disclosure rather than re-using the
    # shared helper above.
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data.get("command") == "preflight"
    assert data["resolution"] == "unresolved"
    assert data["partial_success"] is True
    assert data["summary"]["resolution"] == "unresolved"
    assert "not found" in data["summary"]["verdict"].lower()
