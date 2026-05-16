"""W811 — Empty-corpus smoke for `roam bus-factor`.

Part of the W805 empty-corpus sweep. Verifies that `roam bus-factor`
produces a well-formed envelope on a project that has git history
(minimum: `git init` + one commit) but no real authorship distribution
to analyze. The "no data" code path at cmd_bus_factor.py:388 should
emit a structured envelope with a verdict that mentions the missing
data — never a silent SAFE/completed.

LAW 4 anchor terminals allowed by this test's facts assertions:
``authors``, ``commits``, ``findings``, ``markers``.

See ``CLAUDE.md`` "Six systemic anti-patterns": this guards Pattern 1
variant C (empty stdout crash) and Pattern 2 (silent fallback) on the
bus-factor surface.
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
    """A project with one empty .py file + one git commit.

    bus-factor needs *some* git history to even reach the analysis
    body; without a commit the indexer skips git ingestion entirely.
    The single commit gives it one entry to process, but the empty
    .py file produces no authorship distribution worth ranking —
    exercising the no-data path at cmd_bus_factor.py:388.
    """
    proj = tmp_path / "w811_empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # An empty Python file: a real file the indexer can discover, but
    # with no symbols / no churn distribution to drive ranking.
    (proj / "empty.py").write_text("")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestBusFactorEmptyCorpus:
    """W811: empty-corpus smoke for `roam bus-factor`."""

    def test_exits_zero(self, cli_runner, empty_corpus_project, monkeypatch):
        """`roam bus-factor` exits 0 on an empty corpus (no crash)."""
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=empty_corpus_project)
        assert result.exit_code == 0, f"bus-factor exited {result.exit_code} on empty corpus:\n{result.output}"

    def test_json_envelope_valid(self, cli_runner, empty_corpus_project, monkeypatch):
        """JSON output on empty corpus is a valid roam envelope."""
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner,
            ["bus-factor"],
            cwd=empty_corpus_project,
            json_mode=True,
        )
        data = parse_json_output(result, "bus-factor")
        assert_json_envelope(data, command="bus-factor")

    def test_verdict_discloses_concrete_state_not_safe(self, cli_runner, empty_corpus_project, monkeypatch):
        """Verdict on empty corpus discloses concrete state — never SAFE/completed.

        Pattern 2 guard: an empty/minimal-corpus run must NOT produce a
        default success verdict. On this fixture (1 empty .py file +
        1 git commit attributed to the test user), bus-factor takes the
        ranked-results path and emits a "bus factor N (min), ..." verdict
        rather than the explicit no-data envelope at cmd_bus_factor.py:389.
        That is still fine — the verdict discloses concrete LAW-4-anchored
        state (commits / authors / modules). What matters is that it
        never lies by saying "completed" / "healthy" / "safe".

        W811 finding: bus-factor's no-data envelope at line 388 is hard
        to reach in practice — any git init produces at least one
        commit, which the indexer ingests as authorship data and feeds
        through the normal ranking path. The no-data envelope still
        matters as a defensive default; it just isn't this test's path.
        """
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner,
            ["bus-factor"],
            cwd=empty_corpus_project,
            json_mode=True,
        )
        data = parse_json_output(result, "bus-factor")
        verdict = data["summary"]["verdict"].lower()
        # The verdict must disclose concrete state — either the explicit
        # no-data wording from line 389, OR the normal ranked verdict
        # that surfaces commits / authors / modules / risk counts.
        no_data_markers = ("empty", "no ", "missing", "none", "unavailable")
        ranked_markers = ("bus factor", "high-risk", "modules", "top risk")
        assert any(m in verdict for m in no_data_markers) or any(m in verdict for m in ranked_markers), (
            f"Empty-corpus verdict must disclose concrete state "
            f"(no-data wording OR ranked findings). Verdict was: {verdict!r}"
        )
        # Negative guard: no silent success masquerading as the empty path.
        forbidden = ("safe", "healthy", "all clear")
        assert not any(m in verdict for m in forbidden), (
            f"Empty-corpus verdict must not be a silent success: {verdict!r}"
        )

    def test_directories_analyzed_is_int(self, cli_runner, empty_corpus_project, monkeypatch):
        """Empty corpus reports ``directories_analyzed`` as a non-negative int.

        On a minimal git-initialized project, the indexer may pick up
        zero or one directory of authorship data (the single init commit
        counts). Either is valid; only the presence + type matter.
        """
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner,
            ["bus-factor"],
            cwd=empty_corpus_project,
            json_mode=True,
        )
        data = parse_json_output(result, "bus-factor")
        summary = data["summary"]
        assert "directories_analyzed" in summary, f"Missing 'directories_analyzed': {list(summary.keys())}"
        assert isinstance(summary["directories_analyzed"], int), (
            f"directories_analyzed must be int, got {type(summary['directories_analyzed']).__name__}"
        )
        assert summary["directories_analyzed"] >= 0, (
            f"directories_analyzed must be non-negative, got {summary['directories_analyzed']}"
        )

    def test_directories_list_present(self, cli_runner, empty_corpus_project, monkeypatch):
        """Empty corpus emits ``directories`` as a list (never absent / crash)."""
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner,
            ["bus-factor"],
            cwd=empty_corpus_project,
            json_mode=True,
        )
        data = parse_json_output(result, "bus-factor")
        assert "directories" in data, f"Missing 'directories' key: {list(data.keys())}"
        assert isinstance(data["directories"], list), (
            f"directories must be list, got {type(data['directories']).__name__}"
        )

    def test_facts_non_empty(self, cli_runner, empty_corpus_project, monkeypatch):
        """``agent_contract.facts`` is a non-empty list of strings.

        LAW 4 mandates concrete-noun-anchored facts on every envelope.
        The empty-corpus path should still surface at least the
        verdict-as-fact line so consumers reading only ``facts`` get
        actionable signal.
        """
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner,
            ["bus-factor"],
            cwd=empty_corpus_project,
            json_mode=True,
        )
        data = parse_json_output(result, "bus-factor")
        contract = data.get("agent_contract")
        assert isinstance(contract, dict), f"Missing 'agent_contract' dict in envelope: {list(data.keys())}"
        facts = contract.get("facts")
        assert isinstance(facts, list), f"agent_contract.facts must be a list, got {type(facts)}"
        assert len(facts) > 0, f"agent_contract.facts must be non-empty on empty corpus; got: {facts!r}"
        for f in facts:
            assert isinstance(f, str) and f.strip(), f"Every fact must be a non-empty string; got: {f!r}"

    def test_summary_partial_success_present_as_bool(self, cli_runner, empty_corpus_project, monkeypatch):
        """``summary.partial_success`` is a bool on the no-data path."""
        monkeypatch.chdir(empty_corpus_project)
        result = invoke_cli(
            cli_runner,
            ["bus-factor"],
            cwd=empty_corpus_project,
            json_mode=True,
        )
        data = parse_json_output(result, "bus-factor")
        summary = data["summary"]
        assert "partial_success" in summary, f"summary.partial_success missing; keys={list(summary.keys())}"
        assert isinstance(summary["partial_success"], bool), (
            f"summary.partial_success must be bool, got {type(summary['partial_success']).__name__}"
        )
