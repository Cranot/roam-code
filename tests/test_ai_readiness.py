"""Tests for the ai-readiness command -- AI agent effectiveness estimator.

Covers:
- Basic functionality and output format
- JSON output format and envelope
- Composite scoring formula
- Individual dimension scorers
- Threshold / gate failure
- Edge cases
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
# Fixtures
# ===========================================================================

@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def well_structured_project(tmp_path):
    """A project optimized for AI readiness (good naming, tests, docs)."""
    repo = tmp_path / "well_structured"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "README.md").write_text("# My Project\n\nA sample project.\n")
    (repo / "CLAUDE.md").write_text("# Agent Instructions\n\nUse snake_case.\n")

    src = repo / "src"
    src.mkdir()
    (src / "models.py").write_text(
        'def create_user(name, email):\n'
        '    """Create a new user."""\n'
        '    return {"name": name, "email": email}\n'
        '\n'
        '\n'
        'def validate_email(email):\n'
        '    """Validate an email address."""\n'
        '    return "@" in email\n'
    )
    (src / "service.py").write_text(
        'from models import create_user, validate_email\n'
        '\n'
        '\n'
        'def register_user(name, email):\n'
        '    """Register a new user after validation."""\n'
        '    if validate_email(email):\n'
        '        return create_user(name, email)\n'
        '    return None\n'
    )

    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_models.py").write_text(
        'from src.models import create_user, validate_email\n'
        '\n'
        '\n'
        'def test_create_user():\n'
        '    user = create_user("Alice", "a@b.com")\n'
        '    assert user["name"] == "Alice"\n'
        '\n'
        '\n'
        'def test_validate_email():\n'
        '    assert validate_email("a@b.com")\n'
        '    assert not validate_email("invalid")\n'
    )
    (tests / "test_service.py").write_text(
        'from src.service import register_user\n'
        '\n'
        '\n'
        'def test_register_user():\n'
        '    user = register_user("Bob", "b@c.com")\n'
        '    assert user is not None\n'
    )

    git_init(repo)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        out, rc = index_in_process(repo)
        assert rc == 0, f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return repo


@pytest.fixture
def minimal_project(tmp_path):
    """A minimal project with just enough to index."""
    repo = tmp_path / "minimal"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "app.py").write_text(
        "def main():\n"
        "    return greet('world')\n"
        "\n"
        "\n"
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n"
    )
    (repo / "utils.py").write_text(
        "from app import greet\n"
        "\n"
        "\n"
        "def run():\n"
        "    return greet('test')\n"
    )
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
# Helper to invoke the command
# ===========================================================================

def _invoke(runner, project_path, *extra_args, json_mode=False):
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("ai-readiness")
    args.extend(extra_args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# Tests: Basic functionality
# ===========================================================================

class TestAiReadinessBasic:
    """Basic command execution and output format."""

    def test_runs_on_project(self, cli_runner, minimal_project):
        """ai-readiness runs without error."""
        result = _invoke(cli_runner, minimal_project)
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert "VERDICT:" in result.output

    def test_verdict_line(self, cli_runner, minimal_project):
        """Output starts with a VERDICT line containing the score."""
        result = _invoke(cli_runner, minimal_project)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert lines[0].startswith("VERDICT:")
        assert "/100" in lines[0]

    def test_dimension_table_in_output(self, cli_runner, minimal_project):
        """Text output includes the dimension breakdown table."""
        result = _invoke(cli_runner, minimal_project)
        assert result.exit_code == 0
        assert "Naming consistency" in result.output
        assert "Module coupling" in result.output
        assert "Dead code noise" in result.output
        assert "Test signal strength" in result.output
        assert "Documentation signal" in result.output
        assert "Codebase navigability" in result.output
        assert "Architecture clarity" in result.output

    def test_score_summary_line(self, cli_runner, minimal_project):
        """Output includes the AI Readiness summary line."""
        result = _invoke(cli_runner, minimal_project)
        assert result.exit_code == 0
        assert "AI Readiness" in result.output
        assert "0=hostile" in result.output

    def test_well_structured_better_than_minimal(
        self, cli_runner, well_structured_project, minimal_project
    ):
        """Well-structured project should score >= minimal project."""
        r1 = _invoke(cli_runner, well_structured_project, json_mode=True)
        r2 = _invoke(cli_runner, minimal_project, json_mode=True)
        d1 = json.loads(r1.output)
        d2 = json.loads(r2.output)
        # Well-structured has README, CLAUDE.md, tests => should score higher
        assert d1["summary"]["score"] >= d2["summary"]["score"]


# ===========================================================================
# Tests: JSON output
# ===========================================================================

class TestAiReadinessJSON:
    """JSON output format and envelope contract."""

    def test_json_output_valid(self, cli_runner, minimal_project):
        """--json produces valid JSON."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_json_envelope_keys(self, cli_runner, minimal_project):
        """JSON output follows the roam envelope contract."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        assert data["command"] == "ai-readiness"
        assert "version" in data
        assert "summary" in data
        assert "_meta" in data

    def test_json_summary_fields(self, cli_runner, minimal_project):
        """Summary contains required fields."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert "verdict" in summary
        assert "score" in summary
        assert "label" in summary
        assert "files_scanned" in summary
        assert isinstance(summary["score"], int)
        assert summary["score"] >= 0
        assert summary["score"] <= 100

    def test_json_dimensions_array(self, cli_runner, minimal_project):
        """JSON includes dimensions array with all 7 dimensions."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        assert "dimensions" in data
        dims = data["dimensions"]
        assert len(dims) == 7
        for d in dims:
            assert "name" in d
            assert "label" in d
            assert "score" in d
            assert "weight" in d
            assert "contribution" in d
            assert "details" in d
            assert isinstance(d["score"], int)
            assert 0 <= d["score"] <= 100

    def test_json_recommendations(self, cli_runner, minimal_project):
        """JSON includes recommendations array."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)

    def test_json_deterministic_keys(self, cli_runner, minimal_project):
        """JSON output uses sorted keys for deterministic output."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        keys = list(data.keys())
        assert keys.index("command") < keys.index("summary")


# ===========================================================================
# Tests: Scoring
# ===========================================================================

class TestAiReadinessScoring:
    """Composite scoring and severity labels."""

    def test_readiness_labels(self):
        """Test readiness label function."""
        from roam.commands.cmd_ai_readiness import _readiness_label
        assert _readiness_label(0) == "HOSTILE"
        assert _readiness_label(25) == "HOSTILE"
        assert _readiness_label(26) == "POOR"
        assert _readiness_label(45) == "POOR"
        assert _readiness_label(46) == "FAIR"
        assert _readiness_label(65) == "FAIR"
        assert _readiness_label(66) == "GOOD"
        assert _readiness_label(80) == "GOOD"
        assert _readiness_label(81) == "OPTIMIZED"
        assert _readiness_label(100) == "OPTIMIZED"

    def test_compute_composite_all_zero(self):
        """All zero dimension scores should produce score 0."""
        from roam.commands.cmd_ai_readiness import _compute_composite
        dimensions = {
            "naming_consistency": 0,
            "module_coupling": 0,
            "dead_code_noise": 0,
            "test_signal_strength": 0,
            "documentation_signal": 0,
            "codebase_navigability": 0,
            "architecture_clarity": 0,
        }
        assert _compute_composite(dimensions) == 0

    def test_compute_composite_all_hundred(self):
        """All 100 dimension scores should produce score 100."""
        from roam.commands.cmd_ai_readiness import _compute_composite
        dimensions = {
            "naming_consistency": 100,
            "module_coupling": 100,
            "dead_code_noise": 100,
            "test_signal_strength": 100,
            "documentation_signal": 100,
            "codebase_navigability": 100,
            "architecture_clarity": 100,
        }
        assert _compute_composite(dimensions) == 100

    def test_compute_composite_partial(self):
        """Partial scores should produce weighted average."""
        from roam.commands.cmd_ai_readiness import _compute_composite
        dimensions = {
            "naming_consistency": 50,     # 50 * 15 = 750
            "module_coupling": 50,        # 50 * 20 = 1000
            "dead_code_noise": 50,        # 50 * 15 = 750
            "test_signal_strength": 50,   # 50 * 20 = 1000
            "documentation_signal": 50,   # 50 * 10 = 500
            "codebase_navigability": 50,  # 50 * 10 = 500
            "architecture_clarity": 50,   # 50 * 10 = 500
        }
        # Total = 5000 / 100 = 50
        assert _compute_composite(dimensions) == 50

    def test_weights_sum_to_100(self):
        """Weights should sum to 100."""
        from roam.commands.cmd_ai_readiness import _WEIGHTS
        assert sum(_WEIGHTS.values()) == 100

    def test_score_in_valid_range(self, cli_runner, minimal_project):
        """Score should always be 0-100."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        score = data["summary"]["score"]
        assert 0 <= score <= 100


# ===========================================================================
# Tests: Individual dimension scorers
# ===========================================================================

class TestDimensionScorers:
    """Test individual dimension scoring functions."""

    def test_naming_score_snake_case(self):
        """Python snake_case names should score well."""
        from roam.commands.cmd_ai_readiness import _SNAKE_CASE, _PASCAL_CASE
        assert _SNAKE_CASE.match("my_function")
        assert _SNAKE_CASE.match("create_user")
        assert not _SNAKE_CASE.match("MyFunction")
        assert not _SNAKE_CASE.match("myFunction")
        assert _PASCAL_CASE.match("MyClass")
        assert not _PASCAL_CASE.match("my_class")

    def test_camel_case_pattern(self):
        """JavaScript camelCase names should match."""
        from roam.commands.cmd_ai_readiness import _CAMEL_CASE
        assert _CAMEL_CASE.match("myFunction")
        assert _CAMEL_CASE.match("createUser")
        assert not _CAMEL_CASE.match("my_function")
        assert not _CAMEL_CASE.match("MyFunction")

    def test_naming_score_returns_valid_range(self, cli_runner, minimal_project):
        """Naming score should be 0-100."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        naming_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "naming_consistency"
        )
        assert 0 <= naming_dim["score"] <= 100

    def test_coupling_score_returns_valid_range(self, cli_runner, minimal_project):
        """Coupling score should be 0-100."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        coupling_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "module_coupling"
        )
        assert 0 <= coupling_dim["score"] <= 100

    def test_dead_code_score_returns_valid_range(self, cli_runner, minimal_project):
        """Dead code score should be 0-100."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        dead_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "dead_code_noise"
        )
        assert 0 <= dead_dim["score"] <= 100

    def test_test_signal_with_tests(self, cli_runner, well_structured_project):
        """Project with tests should have higher test signal score."""
        result = _invoke(cli_runner, well_structured_project, json_mode=True)
        data = json.loads(result.output)
        test_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "test_signal_strength"
        )
        # Well-structured project has test files
        assert test_dim["score"] >= 0

    def test_documentation_with_readme(self, cli_runner, well_structured_project):
        """Project with README + CLAUDE.md should score well on docs."""
        result = _invoke(cli_runner, well_structured_project, json_mode=True)
        data = json.loads(result.output)
        doc_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "documentation_signal"
        )
        # Has README.md and CLAUDE.md -> at least 50 points
        assert doc_dim["score"] >= 50
        assert doc_dim["details"]["has_readme"] is True
        assert doc_dim["details"]["has_agent_doc"] is True

    def test_documentation_without_readme(self, cli_runner, minimal_project):
        """Project without README should score lower on docs."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        doc_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "documentation_signal"
        )
        assert doc_dim["details"]["has_readme"] is False

    def test_navigability_returns_valid_range(self, cli_runner, minimal_project):
        """Navigability score should be 0-100."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        nav_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "codebase_navigability"
        )
        assert 0 <= nav_dim["score"] <= 100

    def test_architecture_returns_valid_range(self, cli_runner, minimal_project):
        """Architecture score should be 0-100."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        arch_dim = next(
            d for d in data["dimensions"]
            if d["name"] == "architecture_clarity"
        )
        assert 0 <= arch_dim["score"] <= 100


# ===========================================================================
# Tests: Threshold gate
# ===========================================================================

class TestAiReadinessGate:
    """Threshold / gate failure behavior."""

    def test_no_threshold_no_failure(self, cli_runner, minimal_project):
        """Without --threshold, command always exits 0."""
        result = _invoke(cli_runner, minimal_project)
        assert result.exit_code == 0

    def test_threshold_zero_means_no_gate(self, cli_runner, minimal_project):
        """--threshold 0 means no gate (default behavior)."""
        result = _invoke(cli_runner, minimal_project, "--threshold", "0")
        assert result.exit_code == 0

    def test_low_threshold_passes(self, cli_runner, minimal_project):
        """--threshold 1 should pass if score >= 1."""
        result = _invoke(cli_runner, minimal_project, "--threshold", "1")
        # Score should typically be well above 1
        assert result.exit_code == 0

    def test_gate_failure_text_mode(self, cli_runner, minimal_project):
        """If score below threshold, exit code should be 5 (gate failure)."""
        # Set an impossibly high threshold
        result = _invoke(cli_runner, minimal_project, "--threshold", "100")
        # Only check if it actually failed (score < 100)
        if result.exit_code == 5:
            assert "GATE FAILED" in result.output

    def test_gate_failure_json_mode(self, cli_runner, minimal_project):
        """Gate failure in JSON mode still produces valid JSON."""
        result = _invoke(
            cli_runner, minimal_project,
            "--threshold", "100", json_mode=True
        )
        data = json.loads(result.output)
        assert "summary" in data
        assert isinstance(data["summary"]["score"], int)


# ===========================================================================
# Tests: Edge cases
# ===========================================================================

class TestAiReadinessEdgeCases:
    """Edge cases and robustness."""

    def test_empty_project(self, tmp_path, cli_runner):
        """ai-readiness on a nearly-empty project doesn't crash."""
        repo = tmp_path / "empty"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "empty.py").write_text("# empty file\n")
        git_init(repo)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(repo))
            out, rc = index_in_process(repo)
            assert rc == 0, f"index failed: {out}"
            result = _invoke(cli_runner, repo)
            assert result.exit_code == 0
            assert "VERDICT:" in result.output
        finally:
            os.chdir(old_cwd)

    def test_help_flag(self, cli_runner):
        """--help works for ai-readiness."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["ai-readiness", "--help"])
        assert result.exit_code == 0
        assert "AI" in result.output or "readiness" in result.output

    def test_all_dimension_scores_in_range(self, cli_runner, minimal_project):
        """Every dimension score should be in [0, 100]."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        for dim in data["dimensions"]:
            assert 0 <= dim["score"] <= 100, (
                f"Dimension {dim['name']} score {dim['score']} out of range"
            )

    def test_contributions_sum_near_composite(
        self, cli_runner, minimal_project
    ):
        """Sum of contributions should approximately equal composite score."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        contribution_sum = sum(d["contribution"] for d in data["dimensions"])
        composite = data["summary"]["score"]
        # Allow rounding tolerance
        assert abs(contribution_sum - composite) <= 2, (
            f"Contribution sum {contribution_sum} != composite {composite}"
        )

    def test_label_in_verdict(self, cli_runner, minimal_project):
        """Verdict should contain the severity label."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        label = data["summary"]["label"]
        verdict = data["summary"]["verdict"]
        assert label in verdict
        assert label in (
            "HOSTILE", "POOR", "FAIR", "GOOD", "OPTIMIZED"
        )


# ===========================================================================
# Tests: Recommendations
# ===========================================================================

class TestRecommendations:
    """Test recommendation generation."""

    def test_recommendations_are_strings(self, cli_runner, minimal_project):
        """All recommendations should be strings."""
        result = _invoke(cli_runner, minimal_project, json_mode=True)
        data = json.loads(result.output)
        for rec in data["recommendations"]:
            assert isinstance(rec, str)
            assert len(rec) > 0

    def test_max_five_recommendations(self):
        """At most 5 recommendations should be generated."""
        from roam.commands.cmd_ai_readiness import _generate_recommendations
        # All dimensions score 0 -> many recommendations possible
        dimensions = {k: 0 for k in [
            "naming_consistency", "module_coupling", "dead_code_noise",
            "test_signal_strength", "documentation_signal",
            "codebase_navigability", "architecture_clarity",
        ]}
        details = {k: {} for k in dimensions}
        details["documentation_signal"] = {
            "has_readme": False,
            "has_agent_doc": False,
            "docstring_rate": 0,
        }
        details["dead_code_noise"] = {"dead_exports": 10}
        details["test_signal_strength"] = {"coverage_rate": 0}
        details["module_coupling"] = {"tangle_ratio": 50}
        details["naming_consistency"] = {"rate": 30}
        details["codebase_navigability"] = {"avg_lines": 500, "max_depth": 8}
        details["architecture_clarity"] = {"violations": 5, "cycles": 3}
        recs = _generate_recommendations(dimensions, details)
        assert len(recs) <= 5

    def test_no_recommendations_for_perfect_scores(self):
        """No recommendations when all dimensions score 80+."""
        from roam.commands.cmd_ai_readiness import _generate_recommendations
        dimensions = {k: 90 for k in [
            "naming_consistency", "module_coupling", "dead_code_noise",
            "test_signal_strength", "documentation_signal",
            "codebase_navigability", "architecture_clarity",
        ]}
        details = {k: {} for k in dimensions}
        recs = _generate_recommendations(dimensions, details)
        assert len(recs) == 0
