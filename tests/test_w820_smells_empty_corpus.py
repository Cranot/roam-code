"""W820 — Empty-corpus smoke for `roam smells` (W805 sweep).

Asserts that `roam smells --json` produces a clean structured envelope
when the indexed corpus contains zero indexable smells. Verifies:

- Exit code 0 (no crash on empty)
- Canonical JSON envelope shape (`command`, `version`, `summary`)
- Verdict mentions empty / clean state
- `summary.partial_success` flag is present (auto-injected, see CLAUDE.md)
- `agent_contract.facts` is non-empty (LAW 4 anchor terminals: smells /
  kinds / findings / markers)
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Helper: invoke smells directly via its Click command object
# ---------------------------------------------------------------------------


def invoke_smells(runner, args=None, cwd=None, json_mode=False):
    """Invoke the smells command, bypassing the CLI group."""
    from roam.commands.cmd_smells import smells

    full_args = list(args or [])
    obj = {"json": json_mode}

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(smells, full_args, obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Minimal indexed project: one empty .py file, no symbols, no smells."""
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # An empty Python file gives the indexer something to discover without
    # producing any structural smell findings.
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSmellsEmptyCorpus:
    def test_empty_corpus_exits_zero(self, cli_runner, empty_corpus):
        """`roam smells --json` on an empty corpus exits 0."""
        result = invoke_smells(cli_runner, cwd=empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

    def test_empty_corpus_envelope_contract(self, cli_runner, empty_corpus):
        """Envelope conforms to the canonical roam shape."""
        result = invoke_smells(cli_runner, cwd=empty_corpus, json_mode=True)
        data = parse_json_output(result, command="smells")
        assert_json_envelope(data, command="smells")

    def test_empty_corpus_verdict_signals_clean(self, cli_runner, empty_corpus):
        """Verdict mentions the empty / clean state instead of an opaque
        success."""
        result = invoke_smells(cli_runner, cwd=empty_corpus, json_mode=True)
        data = parse_json_output(result, command="smells")
        summary = data["summary"]
        verdict = summary.get("verdict", "")
        assert isinstance(verdict, str) and verdict, "verdict must be a non-empty string"
        verdict_lower = verdict.lower()
        # The smells command emits "Clean: no code smells detected" on an
        # empty / smell-free corpus. Accept any of the empty-signalling
        # vocabulary so the test stays robust to verdict-text tweaks.
        assert any(marker in verdict_lower for marker in ("clean", "no code smells", "no smells", "0 smell")), (
            f"verdict should signal empty/clean state, got: {verdict!r}"
        )
        # Headline count must be zero.
        assert summary.get("total_smells", -1) == 0, (
            f"total_smells should be 0 on an empty corpus, got {summary.get('total_smells')}"
        )

    def test_empty_corpus_partial_success_flag_present(self, cli_runner, empty_corpus):
        """`summary.partial_success` is auto-injected (see CLAUDE.md
        Pattern-1 variant D) and must be present on every smells
        envelope so consumers can branch on degraded vs full success.
        """
        result = invoke_smells(cli_runner, cwd=empty_corpus, json_mode=True)
        data = parse_json_output(result, command="smells")
        summary = data["summary"]
        assert "partial_success" in summary, (
            f"summary.partial_success must be present (auto-injected); got summary keys = {sorted(summary.keys())}"
        )
        # Empty corpus = nothing degraded; the flag should be False.
        assert summary["partial_success"] is False, (
            f"partial_success should be False on a clean empty corpus, got {summary['partial_success']!r}"
        )

    def test_empty_corpus_agent_contract_facts_non_empty(self, cli_runner, empty_corpus):
        """`agent_contract.facts` carries at least the verdict so
        tight-context agents see something concrete (LAW 4 anchor
        terminals: smells / kinds / findings / markers)."""
        result = invoke_smells(cli_runner, cwd=empty_corpus, json_mode=True)
        data = parse_json_output(result, command="smells")
        contract = data.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, f"agent_contract.facts must be a non-empty list; got {facts!r}"
