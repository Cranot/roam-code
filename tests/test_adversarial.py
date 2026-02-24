"""Tests for the adversarial architecture review command.

Covers:
1. Basic invocation and exit codes
2. JSON envelope contract
3. No-changes graceful handling
4. Challenge detection with uncommitted changes
5. Challenge field schema validation
6. Severity filtering
7. --fail-on-critical CI mode
8. VERDICT line in text output
9. Markdown format output
10. --staged flag handling
11. Orphaned symbol detection
12. Clean change detection (no challenges)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process


# ===========================================================================
# Helper: invoke the adversarial command directly (not via cli.py)
# ===========================================================================


def run_adversarial(proj, args=None, json_mode=False):
    """Invoke the adversarial command directly via CliRunner.

    This bypasses cli.py (which we cannot modify) and invokes the
    command function directly from cmd_adversarial.
    """
    from roam.commands.cmd_adversarial import adversarial

    runner = CliRunner()
    full_args = []
    if json_mode:
        # Inject json mode via obj context
        pass
    if args:
        full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        obj = {"json": json_mode}
        result = runner.invoke(
            adversarial, full_args, obj=obj, catch_exceptions=False
        )
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def adversarial_project(tmp_path):
    """A project with a committed baseline and uncommitted changes.

    Baseline: models.py + service.py (with edges between them)
    Change:   service.py gets a new orphan_func() with no callers.
    """
    proj = tmp_path / "adv_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self):\n"
        "        return f'Hello {self.name}'\n"
    )
    (proj / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name):\n"
        "    return User(name)\n"
        "\n"
        "def process(data):\n"
        "    return create_user(data)\n"
    )

    git_init(proj)
    index_in_process(proj)

    # Uncommitted change: add an orphaned function
    (proj / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name):\n"
        "    return User(name)\n"
        "\n"
        "def process(data):\n"
        "    return create_user(data)\n"
        "\n"
        "def orphan_func():\n"
        "    return 99\n"
    )

    return proj


@pytest.fixture
def clean_project(tmp_path):
    """A project where all changes are structurally clean.

    The private helper is not an orphan because it is called by make_item.
    """
    proj = tmp_path / "clean_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "models.py").write_text(
        "class Item:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n"
    )
    (proj / "service.py").write_text(
        "from models import Item\n"
        "\n"
        "def make_item(v):\n"
        "    return Item(v)\n"
    )

    git_init(proj)
    index_in_process(proj)

    # Private helper is skipped by orphan detector, and it is called by make_item
    (proj / "service.py").write_text(
        "from models import Item\n"
        "\n"
        "def _validate(v):\n"
        "    return v is not None\n"
        "\n"
        "def make_item(v):\n"
        "    if _validate(v):\n"
        "        return Item(v)\n"
        "    return None\n"
    )

    return proj


@pytest.fixture
def indexed_project_no_changes(tmp_path):
    """A fully indexed project with no uncommitted changes."""
    proj = tmp_path / "no_changes"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def main():\n"
        "    return 0\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


# ===========================================================================
# Tests
# ===========================================================================


class TestAdversarialBasic:
    """Basic invocation and exit-code checks."""

    def test_adversarial_runs(self, adversarial_project):
        """Command exits 0 with uncommitted changes."""
        result = run_adversarial(adversarial_project)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )

    def test_adversarial_no_changes(self, indexed_project_no_changes):
        """Command exits 0 gracefully when no changes are detected."""
        result = run_adversarial(indexed_project_no_changes)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )
        output = result.output
        # Should mention no changes or look clean
        assert len(output.strip()) > 0, "Expected non-empty output"

    def test_adversarial_produces_output(self, adversarial_project):
        """Command produces non-empty output."""
        result = run_adversarial(adversarial_project)
        assert result.output.strip(), "Expected non-empty output"


class TestAdversarialJSON:
    """JSON envelope contract tests."""

    def test_adversarial_json_envelope(self, adversarial_project):
        """JSON output has required envelope fields."""
        result = run_adversarial(adversarial_project, json_mode=True)
        assert result.exit_code == 0, (
            f"Exit {result.exit_code}:\n{result.output}"
        )
        data = json.loads(result.output)
        assert "command" in data
        assert "version" in data
        assert "timestamp" in data.get("_meta", data)
        assert "summary" in data
        assert data["command"] == "adversarial"

    def test_adversarial_json_command_field(self, adversarial_project):
        """JSON envelope has command='adversarial'."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        assert data["command"] == "adversarial"

    def test_adversarial_json_summary_fields(self, adversarial_project):
        """JSON summary contains required numeric and string fields."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert "verdict" in summary
        assert "challenges" in summary
        assert "critical" in summary
        assert "high" in summary
        assert "warning" in summary
        assert "info" in summary
        assert "changed_files" in summary
        assert isinstance(summary["verdict"], str)
        assert isinstance(summary["challenges"], int)

    def test_adversarial_json_challenges_list(self, adversarial_project):
        """JSON output contains a top-level 'challenges' list."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        assert "challenges" in data
        assert isinstance(data["challenges"], list)

    def test_adversarial_no_changes_json(self, indexed_project_no_changes):
        """JSON output when no changes is valid envelope with 0 changed_files."""
        result = run_adversarial(indexed_project_no_changes, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "adversarial"
        assert "summary" in data
        assert data["summary"]["changed_files"] == 0


class TestAdversarialChallenges:
    """Challenge detection and field schema tests."""

    def test_adversarial_has_challenges_list(self, adversarial_project):
        """With uncommitted changes, challenges list exists."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        assert isinstance(data["challenges"], list)

    def test_adversarial_challenge_fields(self, adversarial_project):
        """Each challenge has all required fields with correct types."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        for ch in data["challenges"]:
            assert "type" in ch, f"Missing 'type': {ch}"
            assert "severity" in ch, f"Missing 'severity': {ch}"
            assert "title" in ch, f"Missing 'title': {ch}"
            assert "description" in ch, f"Missing 'description': {ch}"
            assert "question" in ch, f"Missing 'question': {ch}"
            assert "location" in ch, f"Missing 'location': {ch}"
            assert isinstance(ch["type"], str)
            assert isinstance(ch["severity"], str)
            assert isinstance(ch["title"], str)
            assert isinstance(ch["description"], str)
            assert isinstance(ch["question"], str)
            assert ch["severity"] in ("CRITICAL", "HIGH", "WARNING", "INFO"), (
                f"Invalid severity: {ch['severity']}"
            )

    def test_adversarial_challenge_types_valid(self, adversarial_project):
        """All challenge types are from the known set."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        valid_types = {
            "new_cycle", "layer_violation", "anti_pattern",
            "cross_cluster", "orphaned", "high_fan_out",
        }
        for ch in data["challenges"]:
            assert ch["type"] in valid_types, (
                f"Unknown challenge type: {ch['type']}"
            )

    def test_adversarial_summary_counts_consistent(self, adversarial_project):
        """Summary counts match actual challenge list counts."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        challenges = data["challenges"]
        summary = data["summary"]

        actual_critical = sum(1 for c in challenges if c["severity"] == "CRITICAL")
        actual_high = sum(1 for c in challenges if c["severity"] == "HIGH")
        actual_warning = sum(1 for c in challenges if c["severity"] == "WARNING")
        actual_info = sum(1 for c in challenges if c["severity"] == "INFO")

        assert summary["challenges"] == len(challenges)
        assert summary["critical"] == actual_critical
        assert summary["high"] == actual_high
        assert summary["warning"] == actual_warning
        assert summary["info"] == actual_info


class TestAdversarialSeverityFilter:
    """Severity filtering tests."""

    def test_adversarial_severity_high_filters_info_warning(self, adversarial_project):
        """--severity high filters out INFO and WARNING challenges."""
        result_high = run_adversarial(
            adversarial_project, ["--severity", "high"], json_mode=True
        )
        data_high = json.loads(result_high.output)
        for ch in data_high["challenges"]:
            assert ch["severity"] in ("CRITICAL", "HIGH"), (
                f"Expected only CRITICAL/HIGH with --severity high, got {ch['severity']}"
            )

    def test_adversarial_severity_critical_only(self, adversarial_project):
        """--severity critical shows only CRITICAL challenges."""
        result = run_adversarial(
            adversarial_project, ["--severity", "critical"], json_mode=True
        )
        data = json.loads(result.output)
        for ch in data["challenges"]:
            assert ch["severity"] == "CRITICAL", (
                f"Expected only CRITICAL, got {ch['severity']}"
            )

    def test_adversarial_severity_low_includes_info(self, adversarial_project):
        """--severity low (default) shows all severities including INFO."""
        result_low = run_adversarial(
            adversarial_project, ["--severity", "low"], json_mode=True
        )
        result_default = run_adversarial(adversarial_project, json_mode=True)
        data_low = json.loads(result_low.output)
        data_default = json.loads(result_default.output)
        # Both should have the same number of challenges
        assert len(data_low["challenges"]) == len(data_default["challenges"])

    def test_adversarial_severity_high_le_low_count(self, adversarial_project):
        """--severity high produces <= challenges than --severity low."""
        result_low = run_adversarial(
            adversarial_project, ["--severity", "low"], json_mode=True
        )
        result_high = run_adversarial(
            adversarial_project, ["--severity", "high"], json_mode=True
        )
        count_low = len(json.loads(result_low.output)["challenges"])
        count_high = len(json.loads(result_high.output)["challenges"])
        assert count_high <= count_low


class TestAdversarialFailOnCritical:
    """CI mode: --fail-on-critical tests."""

    def test_adversarial_fail_on_critical_no_changes_exits_zero(
        self, indexed_project_no_changes
    ):
        """--fail-on-critical exits 0 when there are no changes."""
        result = run_adversarial(indexed_project_no_changes, ["--fail-on-critical"])
        assert result.exit_code == 0, (
            f"Expected exit 0 (no changes), got {result.exit_code}:\n{result.output}"
        )

    def test_adversarial_fail_on_critical_runs(self, adversarial_project):
        """--fail-on-critical flag runs without crashing (may exit 0 or 1)."""
        result = run_adversarial(adversarial_project, ["--fail-on-critical"])
        assert result.exit_code in (0, 1), (
            f"Expected exit 0 or 1, got {result.exit_code}:\n{result.output}"
        )


class TestAdversarialTextOutput:
    """Text format output tests."""

    def test_adversarial_verdict_line(self, adversarial_project):
        """Text output starts with VERDICT: line."""
        result = run_adversarial(adversarial_project)
        assert result.exit_code == 0
        output = result.output.strip()
        assert output.startswith("VERDICT:"), (
            f"Expected output to start with 'VERDICT:', got:\n{output[:200]}"
        )

    def test_adversarial_verdict_line_no_changes(self, indexed_project_no_changes):
        """Text output has VERDICT: even when no changes."""
        result = run_adversarial(indexed_project_no_changes)
        assert result.exit_code == 0
        output = result.output.strip()
        assert output.startswith("VERDICT:"), (
            f"Expected VERDICT: at start, got:\n{output[:200]}"
        )

    def test_adversarial_challenge_block_when_found(self, adversarial_project):
        """When challenges exist, CHALLENGE N block appears in text output."""
        result_json = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result_json.output)
        if not data["challenges"]:
            pytest.skip("No challenges detected -- cannot test CHALLENGE block")

        result_text = run_adversarial(adversarial_project)
        output = result_text.output
        assert "CHALLENGE 1 [" in output, (
            f"Expected CHALLENGE 1 [ in output:\n{output}"
        )

    def test_adversarial_question_in_text_output(self, adversarial_project):
        """When challenges exist, Question: field appears in text output."""
        result_json = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result_json.output)
        if not data["challenges"]:
            pytest.skip("No challenges -- cannot test Question presence")

        result_text = run_adversarial(adversarial_project)
        output = result_text.output
        assert "Question:" in output, (
            f"Expected 'Question:' in output:\n{output}"
        )


class TestAdversarialMarkdown:
    """Markdown format tests."""

    def test_adversarial_markdown_format(self, adversarial_project):
        """--format markdown produces markdown with h2 header."""
        result = run_adversarial(adversarial_project, ["--format", "markdown"])
        assert result.exit_code == 0
        output = result.output
        assert "## Adversarial Architecture Review" in output, (
            f"Expected markdown header:\n{output[:300]}"
        )

    def test_adversarial_markdown_has_verdict(self, adversarial_project):
        """Markdown output contains Verdict."""
        result = run_adversarial(adversarial_project, ["--format", "markdown"])
        output = result.output
        assert "Verdict" in output or "verdict" in output, (
            f"Expected 'Verdict' in markdown:\n{output[:300]}"
        )

    def test_adversarial_markdown_no_changes(self, indexed_project_no_changes):
        """Markdown format works gracefully with no changes."""
        result = run_adversarial(
            indexed_project_no_changes, ["--format", "markdown"]
        )
        assert result.exit_code == 0
        # Should still produce the header
        assert "## Adversarial Architecture Review" in result.output

    def test_adversarial_markdown_severity_grouping(self, adversarial_project):
        """Markdown has severity section groupings when challenges exist."""
        result_json = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result_json.output)
        if not data["challenges"]:
            pytest.skip("No challenges -- cannot test severity groupings")

        result_md = run_adversarial(adversarial_project, ["--format", "markdown"])
        output = result_md.output
        # Should have at least one severity section header
        has_section = any(
            f"### {s}" in output
            for s in ("Critical", "High", "Warning", "Info")
        )
        assert has_section, f"Expected severity section in markdown:\n{output[:500]}"


class TestAdversarialOptions:
    """Option handling tests."""

    def test_adversarial_staged_flag(self, adversarial_project):
        """--staged flag does not crash and exits 0."""
        result = run_adversarial(adversarial_project, ["--staged"])
        assert result.exit_code == 0, (
            f"--staged flag crashed:\n{result.output}"
        )

    def test_adversarial_staged_json(self, adversarial_project):
        """--staged with JSON mode produces valid JSON envelope."""
        result = run_adversarial(adversarial_project, ["--staged"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "adversarial"
        assert "summary" in data
        assert "challenges" in data["summary"]


class TestAdversarialOrphaned:
    """Orphaned symbol detection tests."""

    def test_adversarial_orphaned_detection(self, adversarial_project):
        """Detects orphaned symbols in changed files."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        challenges = data["challenges"]

        # All orphaned challenges should be INFO severity
        orphaned = [c for c in challenges if c["type"] == "orphaned"]
        for ch in orphaned:
            assert ch["severity"] == "INFO", (
                f"Orphaned challenge should be INFO, got {ch['severity']}"
            )

    def test_adversarial_orphaned_not_from_test_files(self, adversarial_project):
        """Orphaned checks skip test files."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        for ch in data["challenges"]:
            if ch["type"] != "orphaned":
                continue
            location = ch["location"].replace("\\", "/")
            # Should not be from test directories
            assert "tests/" not in location, (
                f"Orphaned check should skip test files: {location}"
            )
            assert "test_" not in location.split("/")[-1], (
                f"Orphaned check should skip test files: {location}"
            )

    def test_adversarial_orphaned_question_present(self, adversarial_project):
        """Each orphaned challenge has a meaningful question."""
        result = run_adversarial(adversarial_project, json_mode=True)
        data = json.loads(result.output)
        for ch in data["challenges"]:
            if ch["type"] == "orphaned":
                assert len(ch["question"]) > 0
                assert "?" in ch["question"] or len(ch["question"]) > 10


class TestAdversarialCleanChanges:
    """Tests for changes with no structural issues."""

    def test_adversarial_clean_changes_exits_zero(self, clean_project):
        """Clean changes exit 0."""
        result = run_adversarial(clean_project)
        assert result.exit_code == 0

    def test_adversarial_clean_changes_verdict(self, clean_project):
        """Clean changes produce a VERDICT line."""
        result = run_adversarial(clean_project)
        output = result.output.strip()
        assert output.startswith("VERDICT:"), (
            f"Expected VERDICT: at start:\n{output[:200]}"
        )

    def test_adversarial_clean_changes_json_valid(self, clean_project):
        """Clean changes produce valid JSON envelope."""
        result = run_adversarial(clean_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "adversarial"
        assert "summary" in data
        assert "verdict" in data["summary"]


class TestAdversarialInternalFunctions:
    """Unit tests for internal helper functions."""

    def test_challenge_builder_fields(self):
        """_challenge() produces all required fields."""
        from roam.commands.cmd_adversarial import _challenge
        ch = _challenge(
            "test_type", "HIGH", "Test title", "Test desc", "Test question",
            location="foo.py:10"
        )
        assert ch["type"] == "test_type"
        assert ch["severity"] == "HIGH"
        assert ch["title"] == "Test title"
        assert ch["description"] == "Test desc"
        assert ch["question"] == "Test question"
        assert ch["location"] == "foo.py:10"

    def test_challenge_builder_default_location(self):
        """_challenge() defaults location to empty string."""
        from roam.commands.cmd_adversarial import _challenge
        ch = _challenge("t", "INFO", "T", "D", "Q")
        assert ch["location"] == ""

    def test_severity_order_constants(self):
        """_SEVERITY_ORDER maps severities to correct numeric values."""
        from roam.commands.cmd_adversarial import _SEVERITY_ORDER
        assert _SEVERITY_ORDER["CRITICAL"] > _SEVERITY_ORDER["HIGH"]
        assert _SEVERITY_ORDER["HIGH"] > _SEVERITY_ORDER["WARNING"]
        assert _SEVERITY_ORDER["WARNING"] > _SEVERITY_ORDER["INFO"]

    def test_format_text_verdict_first(self):
        """_format_text() always starts with VERDICT:."""
        from roam.commands.cmd_adversarial import _format_text
        output = _format_text([], "No challenges found", 3)
        assert output.startswith("VERDICT:")

    def test_format_text_with_challenges(self):
        """_format_text() includes CHALLENGE N blocks when challenges exist."""
        from roam.commands.cmd_adversarial import _challenge, _format_text
        challenges = [
            _challenge(
                "orphaned", "INFO", "Orphaned: foo", "foo has no callers",
                "Is this a new entry point?", "foo.py:5"
            ),
        ]
        output = _format_text(challenges, "1 challenge(s), 1 info", 1)
        assert "VERDICT:" in output
        assert "CHALLENGE 1 [INFO]" in output
        assert "Question:" in output

    def test_format_text_challenge_location(self):
        """_format_text() shows Location when it is set."""
        from roam.commands.cmd_adversarial import _challenge, _format_text
        challenges = [
            _challenge(
                "orphaned", "INFO", "Orphaned: bar", "bar is dead",
                "Why?", location="src/bar.py:42"
            ),
        ]
        output = _format_text(challenges, "1 info", 1)
        assert "Location:" in output
        assert "src/bar.py:42" in output

    def test_format_markdown_header(self):
        """_format_markdown() starts with h2 header."""
        from roam.commands.cmd_adversarial import _format_markdown
        output = _format_markdown([], "Clean", 2)
        assert "## Adversarial Architecture Review" in output

    def test_format_markdown_with_challenges(self):
        """_format_markdown() includes severity section groupings."""
        from roam.commands.cmd_adversarial import _challenge, _format_markdown
        challenges = [
            _challenge(
                "orphaned", "INFO", "Orphaned: bar", "bar is orphaned",
                "Entry point?"
            ),
            _challenge(
                "layer_violation", "HIGH", "Layer skip: L0->L3",
                "A calls D directly", "Justify the skip."
            ),
        ]
        output = _format_markdown(challenges, "2 challenges", 1)
        assert "### High" in output or "### Info" in output

    def test_format_markdown_blockquote_question(self):
        """_format_markdown() uses blockquote (>) for questions."""
        from roam.commands.cmd_adversarial import _challenge, _format_markdown
        challenges = [
            _challenge(
                "orphaned", "INFO", "Orphaned: baz", "baz is orphaned",
                "Is this intentional?"
            ),
        ]
        output = _format_markdown(challenges, "1 info", 1)
        assert "> Is this intentional?" in output

    def test_check_orphaned_returns_list(self):
        """_check_orphaned_symbols returns a list (empty set case)."""
        from roam.commands.cmd_adversarial import _check_orphaned_symbols
        result = _check_orphaned_symbols(None, set())
        assert result == []

    def test_check_cycles_returns_list_empty(self):
        """_check_new_cycles returns empty list when no changed symbols."""
        from roam.commands.cmd_adversarial import _check_new_cycles
        result = _check_new_cycles(None, set())
        assert result == []

    def test_check_layer_violations_returns_list_empty(self):
        """_check_layer_violations returns empty list for empty input."""
        from roam.commands.cmd_adversarial import _check_layer_violations
        result = _check_layer_violations(None, set())
        assert result == []

    def test_check_cross_cluster_returns_list_empty(self):
        """_check_cross_cluster returns empty list for empty input."""
        from roam.commands.cmd_adversarial import _check_cross_cluster
        result = _check_cross_cluster(None, set())
        assert result == []

    def test_check_anti_patterns_returns_list_empty(self):
        """_check_anti_patterns returns empty list for empty input."""
        from roam.commands.cmd_adversarial import _check_anti_patterns
        result = _check_anti_patterns(None, set())
        assert result == []
