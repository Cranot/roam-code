"""W802 — Empty-corpus smoke test for `roam dead`.

Sibling to W680/W801: assert that the `dead` command emits a clean
Pattern-2 envelope on a corpus where there is nothing to find. The
empty-state envelope must:

- exit 0
- carry a `summary.verdict` that names the empty state explicitly
  (no silent "completed"-style fallback verdicts)
- expose `summary.partial_success` so machine consumers can read the
  empty state without parsing the verdict string
- emit a non-empty `agent_contract.facts` list disclosing the empty
  state (LAW 4 anchored facts, derived by ``json_envelope``)

LAW 4 anchor terminals used in facts inspection: ``symbols``,
``findings``, ``verdicts``, ``markers``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


def _make_empty_corpus(tmp: Path) -> None:
    """Initialise a git repo + run `roam init` over a single empty .py file."""
    (tmp / "empty.py").write_text("", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp,
        check=True,
    )

    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)


def _run_dead_on_empty_corpus(tmp_path: Path) -> tuple[int, dict]:
    """Build an empty corpus and return (exit_code, parsed_envelope)."""
    _make_empty_corpus(tmp_path)

    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "dead"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    payload = json.loads(result.output) if result.output.strip() else {}
    return result.exit_code, payload


def test_dead_empty_corpus_exit_code_and_command(tmp_path):
    """`roam --json dead` exits 0 with command=dead on an empty corpus."""
    exit_code, payload = _run_dead_on_empty_corpus(tmp_path)

    assert exit_code == 0, f"exit={exit_code}, payload={payload}"
    assert payload.get("command") == "dead", payload


def test_dead_empty_corpus_verdict_discloses_empty_state(tmp_path):
    """The verdict must name the empty state explicitly — no silent fallback."""
    exit_code, payload = _run_dead_on_empty_corpus(tmp_path)
    assert exit_code == 0

    summary = payload.get("summary") or {}
    assert "verdict" in summary, f"no summary.verdict in {payload}"

    verdict = (summary["verdict"] or "").lower()
    empty_state_markers = ("empty", "no dead", "0 dead", "no findings")
    assert any(marker in verdict for marker in empty_state_markers), (
        f"verdict {verdict!r} doesn't disclose the empty state — "
        f"must mention one of {empty_state_markers}; full envelope: {payload}"
    )


def test_dead_empty_corpus_agent_contract_facts_disclose_state(tmp_path):
    """`agent_contract.facts` must be non-empty and disclose the empty-state findings."""
    exit_code, payload = _run_dead_on_empty_corpus(tmp_path)
    assert exit_code == 0

    contract = payload.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert facts, (
        f"agent_contract.facts empty — should disclose empty-state findings; "
        f"payload={payload}"
    )


def test_dead_empty_corpus_summary_partial_success_present(tmp_path):
    """summary.partial_success must be exposed as a machine-readable boolean."""
    exit_code, payload = _run_dead_on_empty_corpus(tmp_path)
    assert exit_code == 0

    summary = payload.get("summary") or {}
    assert "partial_success" in summary, (
        f"summary.partial_success missing — Pattern 2 requires explicit "
        f"empty-state markers; summary keys: {sorted(summary)}"
    )
    # An empty corpus is a fully-resolved "nothing to flag" state, not a
    # partial failure. partial_success must be the literal boolean False.
    assert summary["partial_success"] is False, (
        f"partial_success should be False on a clean empty corpus, got "
        f"{summary['partial_success']!r}"
    )
