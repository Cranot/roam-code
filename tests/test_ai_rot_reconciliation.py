"""Tests for AI rot vocabulary reconciliation (Pattern 3, Wave 16.3).

Before W16.3, ``roam dashboard`` and ``roam vibe-check`` reported
different "AI rot" scores on the same codebase (eg. 7/100 vs 4/100 on
roam-code itself). This was the canonical Pattern 3 "vocabulary mismatch
across commands" defect from the 212-eval corpus.

The fix:

1. Extract the 8-pattern algorithm into ``roam.quality.ai_rot``.
2. Have both ``vibe-check`` and ``dashboard`` consume it.
3. Attach an ``ai_rot_definition`` label to every envelope that emits
   an AI rot number.
4. Add cross-references so agents discover the canonical command.

These tests pin all four guarantees so regressions show up early.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli, parse_json_output

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def mixed_project(tmp_path):
    """A small project with at least one AI-rot pattern present.

    We need some non-zero detector signal so both dashboard and
    vibe-check produce non-trivial numbers. Empty error handlers
    (pattern 3) is the cheapest reliable signal to inject.
    """
    repo = tmp_path / "mixed"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")

    (repo / "handlers.py").write_text(
        "def safe():\n    try:\n        risky()\n    except Exception:\n        pass\n\n\ndef risky():\n    return 1\n"
    )
    (repo / "utils.py").write_text("from handlers import safe\n\n\ndef run():\n    return safe()\n")
    git_init(repo)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        out, rc = index_in_process(repo)
        assert rc == 0, f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return repo


# ===========================================================================
# Tests
# ===========================================================================


class TestAiRotReconciliation:
    """Verify dashboard and vibe-check agree on AI rot, with shared label."""

    def test_dashboard_and_vibe_check_agree_on_ai_rot_score(self, cli_runner, mixed_project, monkeypatch):
        """Same codebase => same AI rot number, both commands."""
        monkeypatch.chdir(mixed_project)
        dash_result = invoke_cli(cli_runner, ["dashboard"], cwd=mixed_project, json_mode=True)
        vibe_result = invoke_cli(cli_runner, ["vibe-check"], cwd=mixed_project, json_mode=True)

        dash = parse_json_output(dash_result, "dashboard")
        vibe = parse_json_output(vibe_result, "vibe-check")

        # vibe-check is canonical: summary.score
        vibe_score = vibe["summary"]["score"]

        # dashboard exposes the same score in two places now:
        # (a) ``vibe_check.score`` (legacy back-compat path)
        # (b) ``summary.ai_rot_score`` (new top-level for easy access)
        assert dash["vibe_check"]["score"] == vibe_score, (
            f"dashboard.vibe_check.score ({dash['vibe_check']['score']}) "
            f"!= vibe-check.summary.score ({vibe_score}) — Pattern 3 mismatch"
        )
        assert dash["summary"]["ai_rot_score"] == vibe_score, (
            f"dashboard.summary.ai_rot_score ({dash['summary']['ai_rot_score']}) "
            f"!= vibe-check.summary.score ({vibe_score})"
        )

    def test_dashboard_envelope_has_ai_rot_definition_field(self, cli_runner, mixed_project, monkeypatch):
        """Pattern 3 label fix: dashboard must attach the metric definition."""
        monkeypatch.chdir(mixed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=mixed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")

        # Summary-level label (new top-level field for downstream
        # consumers that read only the summary block).
        assert "ai_rot_definition" in data["summary"], "dashboard summary missing ai_rot_definition label"
        defn = data["summary"]["ai_rot_definition"]
        assert isinstance(defn, str) and len(defn) > 20
        # Definition must reference the canonical command so an agent
        # reading it knows where to dig deeper (LAW 11).
        assert "vibe-check" in defn

        # Nested vibe_check block also carries the label.
        assert "ai_rot_definition" in data["vibe_check"]

    def test_vibe_check_envelope_has_ai_rot_definition_field(self, cli_runner, mixed_project, monkeypatch):
        """Pattern 3 label fix: vibe-check (the canonical source) labels itself."""
        monkeypatch.chdir(mixed_project)
        result = invoke_cli(cli_runner, ["vibe-check"], cwd=mixed_project, json_mode=True)
        data = parse_json_output(result, "vibe-check")

        assert "ai_rot_definition" in data["summary"], "vibe-check summary missing ai_rot_definition label"
        # The canonical source also exposes ``ai_rot_score`` as a
        # top-level summary field so downstream commands can read it
        # without knowing the internal "score" name.
        assert "ai_rot_score" in data["summary"]
        assert data["summary"]["ai_rot_score"] == data["summary"]["score"]

    def test_dashboard_next_commands_mentions_vibe_check(self, cli_runner, mixed_project, monkeypatch):
        """LAW 11: dashboard must teach agents about the canonical command."""
        monkeypatch.chdir(mixed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=mixed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")

        agent_contract = data.get("agent_contract", {})
        next_commands = agent_contract.get("next_commands", [])
        # Match by substring: the entry is "roam vibe-check" exactly,
        # but be lenient against trailing args.
        assert any("vibe-check" in nc for nc in next_commands), (
            f"dashboard agent_contract.next_commands lacks vibe-check: {next_commands}"
        )

        # And the explicit discovery hint block also names vibe-check.
        signals = data.get("unique_signals", {})
        discoverable = signals.get("discoverable_via", {})
        assert discoverable.get("ai_rot_score") == "roam vibe-check", (
            f"discoverable_via.ai_rot_score should be 'roam vibe-check': {discoverable}"
        )

    def test_compute_ai_rot_score_is_idempotent(self, mixed_project, monkeypatch):
        """Calling compute_ai_rot_score twice on the same DB returns equal results.

        Idempotency is the precondition for the dashboard/vibe-check
        agreement to hold across repeated reads.
        """
        monkeypatch.chdir(mixed_project)

        from roam.db.connection import open_db
        from roam.quality.ai_rot import compute_ai_rot_score

        with open_db(readonly=True) as conn:
            first = compute_ai_rot_score(conn)
            second = compute_ai_rot_score(conn)

        assert first.score == second.score
        assert first.severity == second.severity
        assert first.total_issues == second.total_issues
        assert first.files_scanned == second.files_scanned
        # Pattern dicts must equal element-wise.
        assert set(first.patterns.keys()) == set(second.patterns.keys())
        for key in first.patterns:
            assert first.patterns[key] == second.patterns[key], f"non-idempotent pattern data for {key}"
        # Definition is the module constant; must be identical.
        assert first.definition == second.definition


class TestAiRotCanonicalModule:
    """Direct unit tests for the canonical module."""

    def test_definition_string_mentions_canonical_command(self):
        """The definition label must name `roam vibe-check`."""
        from roam.quality.ai_rot import DEFINITION, definition

        assert "vibe-check" in DEFINITION
        assert definition() == DEFINITION

    def test_score_module_lists_all_eight_patterns(self, mixed_project, monkeypatch):
        """The 8 canonical pattern keys must be present in the result."""
        monkeypatch.chdir(mixed_project)

        from roam.db.connection import open_db
        from roam.quality.ai_rot import compute_ai_rot_score

        with open_db(readonly=True) as conn:
            result = compute_ai_rot_score(conn)

        expected = {
            "dead_exports",
            "short_churn",
            "empty_handlers",
            "abandoned_stubs",
            "hallucinated_imports",
            "error_inconsistency",
            "comment_anomalies",
            "copy_paste",
        }
        assert set(result.patterns.keys()) == expected

    def test_score_dataclass_round_trips_to_envelope_dict(self, mixed_project, monkeypatch):
        """as_envelope_dict() carries the definition label inline."""
        monkeypatch.chdir(mixed_project)

        from roam.db.connection import open_db
        from roam.quality.ai_rot import compute_ai_rot_score

        with open_db(readonly=True) as conn:
            result = compute_ai_rot_score(conn)

        d = result.as_envelope_dict()
        assert d["score"] == result.score
        assert d["severity"] == result.severity
        assert "ai_rot_definition" in d
        assert "vibe-check" in d["ai_rot_definition"]
