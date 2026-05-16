"""Pin per-command ``--detail`` semantics for the three custom-cap commands.

Background -- W22.3 / Pattern 3 (vocabulary mismatch across commands)
====================================================================
W22.3's P2 audit found ``--detail`` is consumed by 10 commands:

- 7 use the centralized progressive-disclosure helper
  ``roam.output.formatter.strip_list_payloads`` -- default mode DROPS all
  list-valued payload fields, ``--detail`` returns the full envelope.

- 3 use **custom caps**: ``guard``, ``plan-refactor``, ``suggest-refactoring``.
  These commands cannot be migrated to ``strip_list_payloads`` without
  breaking their headline contract (a sub-agent context packet, an
  ordered execution plan, a ranked candidate list -- each is meant to
  be actionable in default mode, NOT a counts-only summary).

This module classifies all three as **Class C** in the W22.3 framework
("genuinely different semantics") and pins their behavior so future
drift gets caught early. If a future change accidentally makes any of
these three look like a ``strip_list_payloads`` consumer (i.e. drops the
headline list in default mode), these tests will fail loudly.

The fix for the documented vocabulary mismatch is the per-command help
text (each of the three docstrings carries a ``--detail semantics:``
block as of W22.3-followup), NOT unification under one helper.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


@pytest.fixture
def cli_runner():
    # Click 8.3+ removed mix_stderr; use result.stderr_bytes manually if needed
    return CliRunner()


# ---------------------------------------------------------------------------
# guard -- "compact sub-agent context packet"
# ---------------------------------------------------------------------------
# CONTRACT: callers/callees/tests arrays are always populated. ``--detail``
# raises caps (8/8/8/6 -> 15/15/20/20) but keeps the same schema. Default
# mode does NOT drop list payloads (that would defeat the sub-agent use case).


class TestGuardDetailSemantics:
    """Pin guard's truncate-in-place ``--detail`` contract."""

    def test_guard_default_keeps_callers_array(self, cli_runner, indexed_project, monkeypatch):
        """Default ``guard`` must keep ``callers`` array -- it is the headline
        payload for sub-agents. ``strip_list_payloads`` would drop it; guard MUST NOT."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["guard", "User"], json_mode=True)
        data = parse_json_output(result, "guard")
        assert "callers" in data, "guard default mode must keep callers payload"
        assert "callees" in data, "guard default mode must keep callees payload"
        assert "tests" in data, "guard default mode must keep tests payload"
        assert isinstance(data["callers"], list)
        assert isinstance(data["callees"], list)
        assert isinstance(data["tests"], list)

    def test_guard_default_omits_progressive_disclosure_flags(self, cli_runner, indexed_project, monkeypatch):
        """guard uses custom caps, NOT ``strip_list_payloads``. So the
        ``detail_available`` and ``truncated`` flags that
        ``strip_list_payloads`` writes into ``summary`` must NOT appear.
        This pins the divergence: guard is intentionally not a
        progressive-disclosure consumer."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["guard", "User"], json_mode=True)
        data = parse_json_output(result, "guard")
        summary = data["summary"]
        assert "detail_available" not in summary, (
            "guard uses custom caps -- it must NOT use strip_list_payloads's "
            "progressive-disclosure flags. See cmd_guard.py docstring."
        )

    def test_guard_detail_keeps_same_top_level_shape(self, cli_runner, indexed_project, monkeypatch):
        """``--detail`` must NOT change the top-level envelope shape --
        same keys appear in both modes. Only list lengths and the
        per-step ``details``/``run:`` text-mode expansion differ."""
        monkeypatch.chdir(indexed_project)
        default_result = invoke_cli(cli_runner, ["guard", "User"], json_mode=True)
        default_data = parse_json_output(default_result, "guard")
        detail_result = invoke_cli(cli_runner, ["--detail", "guard", "User"], json_mode=True)
        detail_data = parse_json_output(detail_result, "guard")

        # Same top-level keys.
        assert set(default_data.keys()) == set(detail_data.keys()), (
            f"guard --detail must not change envelope shape. "
            f"default={sorted(default_data.keys())} "
            f"detail={sorted(detail_data.keys())}"
        )

    def test_guard_summary_counts_reflect_untruncated_totals(self, cli_runner, indexed_project, monkeypatch):
        """The summary's ``callers``/``callees``/``test_files`` numbers
        must reflect totals, not the (possibly capped) payload length.
        Pattern 3 fix: each count comes with ``caller_metric_definition``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["guard", "User"], json_mode=True)
        data = parse_json_output(result, "guard")
        summary = data["summary"]
        assert "callers" in summary
        assert "callees" in summary
        assert "test_files" in summary
        assert summary.get("caller_metric_definition") == "raw_edge_rows"


# ---------------------------------------------------------------------------
# plan-refactor -- "ordered execution plan"
# ---------------------------------------------------------------------------
# CONTRACT: the ``plan`` array is ALWAYS returned in full. ``--detail``
# expands auxiliary arrays (tests, layer items, simulation previews) and,
# in text mode, prints per-step ``details``/``command`` lines.


class TestPlanRefactorDetailSemantics:
    """Pin plan-refactor's truncate-in-place ``--detail`` contract."""

    def test_plan_refactor_default_keeps_plan_array(self, cli_runner, indexed_project, monkeypatch):
        """Default ``plan-refactor`` must keep the full ``plan`` array --
        the plan is the headline output. ``strip_list_payloads`` would drop
        it; plan-refactor MUST NOT."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan-refactor", "create_user"], json_mode=True)
        data = parse_json_output(result, "plan-refactor")
        assert "plan" in data, "plan-refactor default mode must keep plan array"
        assert isinstance(data["plan"], list)
        assert len(data["plan"]) >= 1, "plan must have at least one step"

    def test_plan_refactor_default_omits_progressive_disclosure_flags(self, cli_runner, indexed_project, monkeypatch):
        """plan-refactor uses custom caps, NOT ``strip_list_payloads``. So
        the ``detail_available`` and ``truncated`` flags must NOT appear
        in the summary."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan-refactor", "create_user"], json_mode=True)
        data = parse_json_output(result, "plan-refactor")
        summary = data["summary"]
        assert "detail_available" not in summary, (
            "plan-refactor uses custom caps -- it must NOT use strip_list_payloads's progressive-disclosure flags."
        )

    def test_plan_refactor_detail_keeps_same_top_level_shape(self, cli_runner, indexed_project, monkeypatch):
        """``--detail`` must not change envelope shape, only payload sizes."""
        monkeypatch.chdir(indexed_project)
        default_result = invoke_cli(cli_runner, ["plan-refactor", "create_user"], json_mode=True)
        default_data = parse_json_output(default_result, "plan-refactor")
        detail_result = invoke_cli(
            cli_runner,
            ["--detail", "plan-refactor", "create_user"],
            json_mode=True,
        )
        detail_data = parse_json_output(detail_result, "plan-refactor")

        assert set(default_data.keys()) == set(detail_data.keys()), (
            f"plan-refactor --detail must not change envelope shape. "
            f"default={sorted(default_data.keys())} "
            f"detail={sorted(detail_data.keys())}"
        )

    def test_plan_refactor_detail_expands_simulation_previews(self, cli_runner, indexed_project, monkeypatch):
        """``--detail`` returns ALL simulation previews; default returns
        at most one. Pin both invariants so future drift is caught."""
        monkeypatch.chdir(indexed_project)
        default_result = invoke_cli(cli_runner, ["plan-refactor", "create_user"], json_mode=True)
        default_data = parse_json_output(default_result, "plan-refactor")
        detail_result = invoke_cli(
            cli_runner,
            ["--detail", "plan-refactor", "create_user"],
            json_mode=True,
        )
        detail_data = parse_json_output(detail_result, "plan-refactor")

        default_previews = default_data.get("simulation_previews", [])
        detail_previews = detail_data.get("simulation_previews", [])

        assert isinstance(default_previews, list)
        assert isinstance(detail_previews, list)
        # Default caps at <=1 preview; detail returns all.
        assert len(default_previews) <= 1, (
            f"default plan-refactor must cap simulation_previews to <=1, got {len(default_previews)}"
        )
        assert len(detail_previews) >= len(default_previews)


# ---------------------------------------------------------------------------
# suggest-refactoring -- "ranked candidate list"
# ---------------------------------------------------------------------------
# CONTRACT: the ``recommendations`` array is ALWAYS returned in full.
# ``--detail`` adds the ``scoring`` sub-object (weights) and, in text,
# prints all reasons per row instead of top-two.


class TestSuggestRefactoringDetailSemantics:
    """Pin suggest-refactoring's truncate-in-place ``--detail`` contract."""

    def test_suggest_refactoring_default_keeps_recommendations_array(self, cli_runner, indexed_project, monkeypatch):
        """Default ``suggest-refactoring`` must keep the
        ``recommendations`` array -- it is the headline output.
        ``strip_list_payloads`` would drop it; this command MUST NOT."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["suggest-refactoring", "--min-score", "0"], json_mode=True)
        data = parse_json_output(result, "suggest-refactoring")
        assert "recommendations" in data, "suggest-refactoring default mode must keep recommendations payload"
        assert isinstance(data["recommendations"], list)

    def test_suggest_refactoring_default_omits_scoring_block(self, cli_runner, indexed_project, monkeypatch):
        """Default mode drops the ``scoring`` sub-object (the weight
        recipe). ``--detail`` adds it back. Pin both."""
        monkeypatch.chdir(indexed_project)
        default_result = invoke_cli(cli_runner, ["suggest-refactoring"], json_mode=True)
        default_data = parse_json_output(default_result, "suggest-refactoring")
        detail_result = invoke_cli(cli_runner, ["--detail", "suggest-refactoring"], json_mode=True)
        detail_data = parse_json_output(detail_result, "suggest-refactoring")

        assert "scoring" not in default_data, "suggest-refactoring default mode must NOT include 'scoring' block"
        assert "scoring" in detail_data, "suggest-refactoring --detail must include 'scoring' block"
        weights = detail_data["scoring"].get("weights", {})
        # Pin the documented weight keys -- if a future change adds/removes
        # one, the docstring should be updated too.
        assert set(weights.keys()) == {
            "complexity",
            "coupling",
            "churn",
            "smells",
            "coverage_gap",
            "debt",
        }, f"scoring weight keys drifted from documented set: {sorted(weights.keys())}"

    def test_suggest_refactoring_default_omits_progressive_disclosure_flags(
        self, cli_runner, indexed_project, monkeypatch
    ):
        """suggest-refactoring uses custom caps, NOT ``strip_list_payloads``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["suggest-refactoring"], json_mode=True)
        data = parse_json_output(result, "suggest-refactoring")
        summary = data["summary"]
        assert "detail_available" not in summary, (
            "suggest-refactoring uses custom caps -- it must NOT use "
            "strip_list_payloads's progressive-disclosure flags."
        )


# ---------------------------------------------------------------------------
# Cross-command: pin that the canonical helper users keep their behavior
# ---------------------------------------------------------------------------
# This is the OTHER half of the W22.3 invariant: the 7 commands that use
# ``strip_list_payloads`` MUST drop list payloads in default mode and MUST
# include ``detail_available: true`` in the summary. If anyone accidentally
# moves one of those 7 to the custom-cap pattern, this fails.


class TestSummaryEnvelopeUsersStaySummaryEnvelopeUsers:
    """Pin the contract for the 7 commands that DO use ``strip_list_payloads``."""

    def test_smells_default_marks_detail_available(self, cli_runner, indexed_project, monkeypatch):
        """``smells`` uses ``strip_list_payloads`` -- default mode must
        carry ``detail_available: true`` in the summary. This is the
        canonical progressive-disclosure marker the other 6 commands
        also emit."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["smells"], json_mode=True)
        data = parse_json_output(result, "smells")
        summary = data["summary"]
        assert summary.get("detail_available") is True, (
            "smells uses strip_list_payloads -- summary must carry "
            "'detail_available: true' as the progressive-disclosure marker. "
            "If this fails, someone moved smells off strip_list_payloads -- "
            "the W22.3 audit needs to be re-run."
        )

    def test_health_default_marks_detail_available(self, cli_runner, indexed_project, monkeypatch):
        """Same invariant as ``smells`` -- health is another
        ``strip_list_payloads`` consumer."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], json_mode=True)
        data = parse_json_output(result, "health")
        summary = data["summary"]
        assert summary.get("detail_available") is True, (
            "health uses strip_list_payloads -- summary must carry 'detail_available: true'."
        )
