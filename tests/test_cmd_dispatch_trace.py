"""Tests for `roam dispatch-trace` — classifier + per-probe dispatch trace.

Invokes the click command directly (bypassing `roam.cli`) so the test
doesn't depend on the command being registered in `cli._COMMANDS`.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.commands.cmd_dispatch_trace import dispatch_trace


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, *args, json_mode=True):
    """Invoke the dispatch_trace click command directly."""
    obj = {"json": json_mode}
    return runner.invoke(
        dispatch_trace,
        list(args),
        obj=obj,
        catch_exceptions=False,
    )


def test_dispatch_trace_json_happy_path(runner, tmp_path):
    """JSON mode emits a roam-envelope-v1 with classifier + probe_decisions."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = _invoke(
            runner,
            "Find files coupled to src/roam/cli.py",
            "--root",
            str(tmp_path),
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # Envelope shape
        assert env["schema"] == "roam-envelope-v1"
        assert env["command"] == "dispatch-trace"
        # Summary contract — verdict is single line and contains the procedure.
        verdict = env["summary"]["verdict"]
        assert "Classified as" in verdict
        assert "probes fired" in verdict
        # Classifier block
        cls = env["classifier"]
        assert "procedure" in cls
        assert "confidence" in cls
        assert isinstance(cls["alternatives"], list)
        assert isinstance(cls["regex_matches"], dict)
        # Probe decisions — one per known family
        probes = env["probe_decisions"]
        assert isinstance(probes, list)
        assert len(probes) >= 1
        for p in probes:
            assert set(p.keys()) >= {"family", "fired", "reason", "latency_ms"}
            assert isinstance(p["fired"], bool)
        # Sized envelope + normalized task text
        assert isinstance(env["final_envelope_size_bytes"], int)
        assert isinstance(env["task_text_normalized"], str)
        # Coupling query should classify as structural_coupling.
        assert cls["procedure"] == "structural_coupling"
    finally:
        os.chdir(old_cwd)


def test_dispatch_trace_facts_anchor_on_concrete_plural_terminals(runner, tmp_path):
    """LAW 4: every fact's terminal token must be a concrete-plural anchor.

    The humanizer's anchor set lives in
    `roam.output.formatter:concrete_plural_terminals`. A LAW-4-compliant
    `agent_contract.facts` ends each entry on one of those tokens. We
    exercise the lint locally here so a future fact-string edit fails fast.
    """
    # Mirror of the formatter's anchor set — kept small enough that the
    # assertion is precise. Adding new fact-string terminals requires
    # extending BOTH this list AND the formatter's anchor set per
    # AGENTS.md (LAW 4).
    anchors = {
        "alternatives",
        "matches",
        "families",
        "bytes",
        # Already in the formatter set:
        "files",
        "symbols",
        "edges",
        "nodes",
        "cycles",
        "clusters",
        "layers",
        "smells",
        "findings",
        "warnings",
        "errors",
        "lines",
        "tokens",
        "items",
        "entries",
        "records",
        "fields",
        "callers",
        "callees",
        "imports",
        "matches",
        "patterns",
        "alerts",
        "issues",
        "violations",
        "risks",
    }
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = _invoke(
            runner,
            "investigate why login is slow",
            "--root",
            str(tmp_path),
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        facts = env["agent_contract"]["facts"]
        assert facts, "agent_contract.facts must be non-empty"
        for fact in facts:
            terminal = fact.rstrip(".?!,;:").split()[-1].lower()
            assert terminal in anchors, f"fact terminal {terminal!r} not in LAW-4 anchor set (fact={fact!r})"
    finally:
        os.chdir(old_cwd)
