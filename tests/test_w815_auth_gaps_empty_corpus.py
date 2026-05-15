"""W815 — Empty-corpus smoke for `roam auth-gaps` (W805 sweep).

Asserts that running `auth-gaps --json` on a project containing only an
empty Python file (no PHP, no routes, no controllers) returns a clean
structured envelope at exit 0 with a non-success-defaulting verdict.

Pattern variants exercised:
  - Pattern 1C: structured envelope on no-results (no empty stdout / crash).
  - Pattern 2 :  verdict must NOT silently default to "SAFE" / "completed".
                 The current verdict "0 auth gap(s) found" is acceptable —
                 it names a concrete zero count rather than asserting a
                 misleading pass.
  - W802 xfail-strict on `summary.partial_success` — auth-gaps does not
    yet emit this flag; the xfail pins the gap until it does.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def empty_corpus_project(tmp_path):
    """Project root containing one empty `.py` file and nothing else.

    No routes/, no controllers, no PHP — auth-gaps should find zero
    route gaps and zero controller findings, yet still emit a
    well-formed envelope.
    """
    proj = tmp_path / "empty_corpus_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestAuthGapsEmptyCorpus:
    def test_exits_zero(self, cli_runner, empty_corpus_project, monkeypatch):
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner, ["auth-gaps"], cwd=empty_corpus_project, json_mode=True
        )
        assert result.exit_code == 0, (
            f"auth-gaps must exit 0 on empty corpus, got {result.exit_code}:\n"
            f"{result.output}"
        )

    def test_structured_envelope(self, cli_runner, empty_corpus_project, monkeypatch):
        """Envelope must parse + carry command/version/summary contract."""
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner, ["auth-gaps"], cwd=empty_corpus_project, json_mode=True
        )
        data = parse_json_output(result, "auth-gaps")
        assert_json_envelope(data, "auth-gaps")
        # Structural fields the empty-corpus envelope must still expose.
        assert "route_gaps" in data
        assert "controller_gaps" in data
        assert isinstance(data["route_gaps"], list)
        assert isinstance(data["controller_gaps"], list)
        # On an empty corpus both lists must be empty — guards against
        # leaking findings from a stale / cross-contaminated index.
        assert data["route_gaps"] == []
        assert data["controller_gaps"] == []

    def test_verdict_mentions_empty_not_default_success(
        self, cli_runner, empty_corpus_project, monkeypatch
    ):
        """Pattern 2 guard: verdict must reflect the zero-finding state
        explicitly (a concrete count), not a silent SAFE / completed
        default. ``"0 auth gap(s) found"`` qualifies; ``"PASSED"`` /
        ``"SAFE"`` / ``"completed"`` would NOT.
        """
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner, ["auth-gaps"], cwd=empty_corpus_project, json_mode=True
        )
        data = parse_json_output(result, "auth-gaps")
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict, "verdict must be a non-empty string"
        verdict_lower = verdict.lower()
        # The verdict must name the zero-count outcome (concrete signal).
        assert "0" in verdict or "no " in verdict_lower or "empty" in verdict_lower, (
            f"verdict {verdict!r} must reflect empty-corpus state; "
            "must not silently default to a success word"
        )
        # Explicit Pattern-2 blocklist: no default-success vocabulary.
        for forbidden in ("safe", "passed", "completed", "all clear", "ok"):
            assert forbidden not in verdict_lower, (
                f"verdict {verdict!r} contains default-success word {forbidden!r} — "
                "Pattern 2 silent-fallback violation on empty corpus"
            )
        # The structured counts must mirror the verdict.
        summary = data["summary"]
        assert summary.get("total") == 0
        assert summary.get("high") == 0
        assert summary.get("medium") == 0
        assert summary.get("low") == 0

    def test_facts_non_empty(self, cli_runner, empty_corpus_project, monkeypatch):
        """`agent_contract.facts` must always carry at least the verdict
        line — never an empty list, never absent. LAW 4 anchor terminals
        (`gaps`, `findings`, `routes`, `markers`) are acceptable here;
        the auth-gaps verdict anchors on ``gap(s) found``.
        """
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner, ["auth-gaps"], cwd=empty_corpus_project, json_mode=True
        )
        data = parse_json_output(result, "auth-gaps")
        agent_contract = data.get("agent_contract")
        assert agent_contract is not None, "envelope must include agent_contract"
        facts = agent_contract.get("facts")
        assert isinstance(facts, list), f"facts must be a list, got {type(facts)}"
        assert len(facts) >= 1, "facts must be non-empty (verdict at minimum)"
        assert all(isinstance(f, str) and f for f in facts), (
            "every fact must be a non-empty string"
        )

    def test_summary_partial_success_present_as_bool(
        self, cli_runner, empty_corpus_project, monkeypatch
    ):
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner, ["auth-gaps"], cwd=empty_corpus_project, json_mode=True
        )
        data = parse_json_output(result, "auth-gaps")
        summary = data["summary"]
        assert "partial_success" in summary, (
            "summary.partial_success must be present so agents can "
            "distinguish empty-corpus from confirmed-clean states"
        )
        assert isinstance(summary["partial_success"], bool), (
            f"summary.partial_success must be bool, got "
            f"{type(summary['partial_success']).__name__}"
        )
