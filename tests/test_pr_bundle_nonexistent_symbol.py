"""Tests for the W20.5 silent-SAFE fix on ``roam pr-bundle add affected``.

Bug (pre-fix): ``roam pr-bundle add affected <nonexistent>`` silently
recorded the symbol with empty kind / file / blast_radius=0 and
``causal_snapshot.state="no_index"``, but the envelope's ``verdict`` and
``agent_contract.facts`` gave NO signal that the symbol was a ghost.
An agent automating bundle population would accumulate unresolved names
without noticing.

Fix (additive): the record is still written (an agent may legitimately
track "I want to address this symbol but it's not yet in the index"),
but the envelope now flips ``partial_success=True``, names the
unresolved state in the verdict, and ships an ``agent_contract`` with
imperative next-commands.

These tests pin the three load-bearing behaviors:

1. add a ghost symbol -> verdict mentions "not in index",
   ``state`` carries an unresolved marker, ``partial_success=True``,
   and ``agent_contract.facts`` / ``next_commands`` exist.
2. add a real indexed symbol -> ``resolution_state="ok"`` on the record,
   ``unresolved_affected_symbols_count == 0``, no warning verdict.
3. emit folds ``unresolved_affected_symbols_count`` into the summary
   so consumers reading ONLY the emit envelope still see the count.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import parse_json_output  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _pin_branch(proj: Path) -> None:
    """Pin branch so the bundle filename is deterministic across hosts."""
    subprocess.run(
        ["git", "checkout", "-B", "w20-5-branch"],
        cwd=proj,
        capture_output=True,
    )


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


def _read_bundle_file(proj: Path, branch: str = "w20-5-branch") -> dict:
    safe = branch.replace("/", "__")
    path = proj / ".roam" / "pr-bundles" / f"{safe}.json"
    if not path.exists():
        path = proj / ".roam" / "pr-bundle.json"
    assert path.exists(), f"bundle file missing -- looked at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Nonexistent symbol -> partial_success envelope with explicit warning
# ---------------------------------------------------------------------------


def test_add_nonexistent_symbol_returns_partial_success(
    project_factory, cli_runner, monkeypatch
):
    """Ghost symbol: record is written but the envelope MUST warn.

    The bundle file still contains the entry (additive fix), but the
    envelope's verdict, partial_success flag, and agent_contract all
    surface the unresolved state.
    """
    proj = project_factory(
        {
            "src/real.py": (
                "def real_symbol():\n"
                "    return 1\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "test ghost"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "add", "affected", "nonexistent_xyz123"],
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-add-affected")

    # Verdict mentions the ghost state.
    verdict = data["summary"]["verdict"]
    assert "nonexistent_xyz123" in verdict, verdict
    assert "not in index" in verdict, verdict
    assert "WARNING" in verdict or "warning" in verdict.lower(), verdict

    # State + partial_success flipped.
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["unresolved_affected_symbol"] == "nonexistent_xyz123"
    assert data["summary"]["unresolved_affected_state"] in {
        "not_found",
        "no_db",
        "lookup_failed",
    }
    assert data["summary"]["unresolved_affected_symbols_count"] >= 1

    # agent_contract exists and is shaped per the LAW 10 flat-facts rule.
    contract = data.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) >= 1, contract
    assert any("not in the indexed symbol table" in f for f in facts), facts
    next_cmds = contract.get("next_commands") or []
    assert isinstance(next_cmds, list) and len(next_cmds) >= 1, contract
    # next_commands are literally executable per CONSTRAINT 12.
    assert any("roam search-symbol nonexistent_xyz123" in c for c in next_cmds), next_cmds

    # The record IS in the bundle file -- additive fix.
    bundle = _read_bundle_file(proj)
    rec = next(
        (s for s in bundle["affected_symbols"] if s.get("name") == "nonexistent_xyz123"),
        None,
    )
    assert rec is not None, bundle["affected_symbols"]
    assert rec.get("resolution_state") in {"not_found", "no_db", "lookup_failed"}


# ---------------------------------------------------------------------------
# 2. Resolved symbol -> clean envelope, resolution_state="ok"
# ---------------------------------------------------------------------------


def test_resolved_symbol_returns_clean_state(
    project_factory, cli_runner, monkeypatch
):
    """Happy path: a real indexed symbol gets resolution_state="ok",
    partial_success stays False (subject to other proofs missing), and
    the verdict does NOT contain the "not in index" warning.
    """
    proj = project_factory(
        {
            "src/real.py": (
                "def real_symbol():\n"
                "    return 1\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "test happy path"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "add", "affected", "real_symbol"],
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-add-affected")

    # No warning verdict.
    verdict = data["summary"]["verdict"]
    assert "not in index" not in verdict, verdict
    assert "WARNING" not in verdict, verdict

    # The unresolved counter is zero (the only affected symbol resolved).
    assert data["summary"]["unresolved_affected_symbols_count"] == 0
    # No unresolved-symbol marker fields when everything resolved.
    assert "unresolved_affected_symbol" not in data["summary"]
    assert "unresolved_affected_state" not in data["summary"]

    # Record carries resolution_state="ok" + the index filled in kind/file.
    bundle = _read_bundle_file(proj)
    rec = next(
        (s for s in bundle["affected_symbols"] if s.get("name") == "real_symbol"),
        None,
    )
    assert rec is not None, bundle["affected_symbols"]
    assert rec.get("resolution_state") == "ok"
    # Index auto-populated kind+file since the caller didn't pass --kind/--file.
    assert rec.get("kind") == "function", rec
    assert rec.get("file", "").endswith("src/real.py"), rec


# ---------------------------------------------------------------------------
# 3. emit surfaces unresolved_affected_symbols_count
# ---------------------------------------------------------------------------


def test_emit_surfaces_unresolved_symbol_count(
    project_factory, cli_runner, monkeypatch
):
    """Mixed bundle: 1 resolved + 2 ghost symbols.

    The emit envelope's ``summary.unresolved_affected_symbols_count``
    must be 2 and the verdict must mention the ghost count so an agent
    reading ONLY the verdict still sees the warning.
    """
    proj = project_factory(
        {
            "src/real.py": (
                "def real_symbol():\n"
                "    return 1\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "test mixed"])
    # One real, two ghosts.
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "real_symbol"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "ghost_one_xyz"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "ghost_two_xyz"])

    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    # Count surfaced in summary.
    assert data["summary"]["unresolved_affected_symbols_count"] == 2, data["summary"]
    # Verdict mentions the unresolved count -- LAW 6 standalone-readable.
    assert "NOT in index" in data["summary"]["verdict"], data["summary"]["verdict"]
    assert "2" in data["summary"]["verdict"], data["summary"]["verdict"]

    # Per-record resolution_state is preserved through emit.
    states = sorted(
        rec.get("resolution_state", "?")
        for rec in data["affected_symbols"]
    )
    # 1 ok + 2 not_found (or whatever unresolved variant the DB picked).
    assert states.count("ok") == 1, states
    assert sum(1 for s in states if s in {"not_found", "no_db", "lookup_failed"}) == 2, states
