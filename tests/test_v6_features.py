"""Integration tests for new commands and enhancements.

Covers: understand, dead --summary/--by-kind/--clusters, context (batch),
snapshot, trend, coverage-gaps, report, and the --json envelope.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit, index_in_process


# ============================================================================
# Shared fixture: a small Python project with known dependency structure
# ============================================================================

@pytest.fixture(scope="module")
def indexed_project(tmp_path_factory):
    """Create a temp directory with 3-4 Python files, init git, and index.

    Dependency structure:
      main.py  ->  service.py  ->  models.py
                   service.py  ->  utils.py
    """
    proj = tmp_path_factory.mktemp("newfeatures")

    (proj / "models.py").write_text(
        'class User:\n'
        '    """A user model."""\n'
        '    def __init__(self, name: str, email: str):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        '    def display(self):\n'
        '        return f"{self.name} <{self.email}>"\n'
        '\n'
        'class Role:\n'
        '    """A role model."""\n'
        '    def __init__(self, title):\n'
        '        self.title = title\n'
        '\n'
        '    def describe(self):\n'
        '        return f"Role: {self.title}"\n'
    )

    (proj / "utils.py").write_text(
        'def validate_email(email: str) -> bool:\n'
        '    """Check if email is valid."""\n'
        '    return "@" in email\n'
        '\n'
        'def format_name(first: str, last: str) -> str:\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        '\n'
        'def unused_helper():\n'
        '    """This function is never called."""\n'
        '    return 42\n'
    )

    (proj / "service.py").write_text(
        'from models import User, Role\n'
        'from utils import validate_email, format_name\n'
        '\n'
        'def create_user(name: str, email: str) -> User:\n'
        '    """Create and validate a user."""\n'
        '    if not validate_email(email):\n'
        '        raise ValueError("Invalid email")\n'
        '    return User(name, email)\n'
        '\n'
        'def get_user_role(user: User) -> Role:\n'
        '    """Get the role for a user."""\n'
        '    return Role("member")\n'
        '\n'
        'def list_users():\n'
        '    """List all users."""\n'
        '    return []\n'
    )

    (proj / "main.py").write_text(
        'from service import create_user, list_users\n'
        '\n'
        'def main():\n'
        '    """Application entry point."""\n'
        '    user = create_user("Alice", "alice@example.com")\n'
        '    print(user.display())\n'
        '    print(list_users())\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )

    git_init(proj)

    # Add a second commit for git history
    (proj / "service.py").write_text(
        'from models import User, Role\n'
        'from utils import validate_email, format_name\n'
        '\n'
        'def create_user(name: str, email: str) -> User:\n'
        '    """Create and validate a user."""\n'
        '    if not validate_email(email):\n'
        '        raise ValueError("Invalid email")\n'
        '    full = format_name(name, "")\n'
        '    return User(full, email)\n'
        '\n'
        'def get_user_role(user: User) -> Role:\n'
        '    """Get the role for a user."""\n'
        '    return Role("member")\n'
        '\n'
        'def list_users():\n'
        '    """List all users."""\n'
        '    return []\n'
    )
    git_commit(proj, "refactor service")

    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"Index failed: {out}"
    return proj


# ============================================================================
# TestUnderstand
# ============================================================================

class TestUnderstand:
    def test_understand_text(self, indexed_project):
        """roam understand should show Key abstractions, Health, and file counts."""
        out, rc = roam("understand", cwd=indexed_project)
        assert rc == 0, f"understand failed: {out}"
        assert "Key abstractions" in out, f"Missing 'Key abstractions' in: {out}"
        assert "Health:" in out or "Health" in out, f"Missing Health in: {out}"
        # Should mention file counts
        assert "files" in out.lower(), f"Missing file counts in: {out}"

    def test_understand_json(self, indexed_project):
        """roam --json understand should return valid JSON with expected keys."""
        out, rc = roam("--json", "understand", cwd=indexed_project)
        assert rc == 0, f"understand --json failed: {out}"
        data = json.loads(out)
        assert "command" in data, f"Missing 'command' key in JSON: {data.keys()}"
        assert data["command"] == "understand"
        assert "tech_stack" in data, f"Missing 'tech_stack' key in JSON: {data.keys()}"
        assert "architecture" in data, f"Missing 'architecture' key in JSON: {data.keys()}"
        assert "summary" in data
        assert "timestamp" in data


# ============================================================================
# TestDeadEnhanced
# ============================================================================

class TestDeadEnhanced:
    def test_dead_summary(self, indexed_project):
        """roam dead --summary should print a one-line summary."""
        out, rc = roam("dead", "--summary", cwd=indexed_project)
        assert rc == 0, f"dead --summary failed: {out}"
        assert "Dead exports:" in out or "safe" in out.lower(), \
            f"Missing summary line in: {out}"

    def test_dead_by_kind(self, indexed_project):
        """roam dead --by-kind should group dead symbols by kind."""
        out, rc = roam("dead", "--by-kind", cwd=indexed_project)
        assert rc == 0, f"dead --by-kind failed: {out}"
        # Grouped output should show the header mentioning 'by kind'
        assert "kind" in out.lower() or "Kind" in out, \
            f"Missing kind grouping header in: {out}"

    def test_dead_clusters(self, indexed_project):
        """roam dead --clusters should attempt cluster detection."""
        out, rc = roam("dead", "--clusters", cwd=indexed_project)
        assert rc == 0, f"dead --clusters failed: {out}"
        # Output should mention clusters or at least run without error.
        # If no clusters exist, the basic dead output still appears.
        assert "Unreferenced" in out or "cluster" in out.lower() or "Dead" in out.lower(), \
            f"Unexpected output from dead --clusters: {out}"


# ============================================================================
# TestContextBatch
# ============================================================================

class TestContextBatch:
    def test_context_single(self, indexed_project):
        """roam context <symbol> should show context for a single symbol."""
        out, rc = roam("context", "create_user", cwd=indexed_project)
        assert rc == 0, f"context failed: {out}"
        assert "Context for" in out, f"Missing 'Context for' in: {out}"

    def test_context_batch(self, indexed_project):
        """roam context <sym1> <sym2> should produce batch output."""
        out, rc = roam("context", "create_user", "list_users", cwd=indexed_project)
        assert rc == 0, f"context batch failed: {out}"
        # Batch mode should show 'Batch Context' header or 'Shared callers'
        assert "Batch Context" in out or "Shared callers" in out or \
            "shared" in out.lower() or "Files to read" in out, \
            f"Missing batch context output in: {out}"


# ============================================================================
# TestSnapshot
# ============================================================================

class TestSnapshot:
    def test_snapshot_creates(self, indexed_project):
        """roam snapshot --tag test should save a snapshot successfully."""
        out, rc = roam("snapshot", "--tag", "test", cwd=indexed_project)
        assert rc == 0, f"snapshot failed: {out}"
        assert "Snapshot saved" in out or "snapshot" in out.lower(), \
            f"Missing success message in: {out}"
        assert "test" in out, f"Tag 'test' not in output: {out}"


# ============================================================================
# TestTrend
# ============================================================================

class TestTrend:
    def test_trend_display(self, indexed_project):
        """roam trend should display a table of snapshots.

        Requires at least one snapshot to exist (created by indexing or
        the snapshot test).
        """
        # Ensure at least one snapshot exists
        roam("snapshot", "--tag", "trend-seed", cwd=indexed_project)
        out, rc = roam("trend", cwd=indexed_project)
        assert rc == 0, f"trend failed: {out}"
        assert "Health Trend" in out or "Score" in out or "Date" in out, \
            f"Missing table output in: {out}"

    def test_trend_assert_pass(self, indexed_project):
        """roam trend --assert 'cycles<=100' should pass (exit 0) for a healthy project."""
        roam("snapshot", "--tag", "assert-seed", cwd=indexed_project)
        out, rc = roam("trend", "--assert", "cycles<=100", cwd=indexed_project)
        assert rc == 0, f"trend --assert should pass but failed: {out}"
        assert "passed" in out.lower() or rc == 0

    def test_trend_assert_fail(self, indexed_project):
        """roam trend --assert 'cycles<=0' should handle strictness.

        Note: if cycles is 0 this assertion actually passes.  We use
        health_score>=999 which will definitely fail.
        """
        roam("snapshot", "--tag", "assert-fail-seed", cwd=indexed_project)
        out, rc = roam("trend", "--assert", "health_score>=999", cwd=indexed_project)
        # Should fail because health_score is never 999+
        assert rc != 0, f"Expected assertion failure, got rc=0: {out}"


# ============================================================================
# TestCoverageGaps
# ============================================================================

class TestCoverageGaps:
    def test_coverage_gaps_basic(self, indexed_project):
        """roam coverage-gaps with a non-matching pattern should handle gracefully."""
        out, rc = roam("coverage-gaps", "--gate-pattern", "nonexistent_xyz",
                       cwd=indexed_project)
        # Should either return 0 with 'No gate symbols found' or handle gracefully
        assert "No gate" in out or "gate" in out.lower() or rc == 0, \
            f"Unexpected coverage-gaps output: {out}"


# ============================================================================
# TestReport
# ============================================================================

class TestReport:
    def test_report_list(self, indexed_project):
        """roam report --list should show all preset names."""
        out, rc = roam("report", "--list", cwd=indexed_project)
        assert rc == 0, f"report --list failed: {out}"
        assert "first-contact" in out, f"Missing 'first-contact' preset in: {out}"
        assert "security" in out, f"Missing 'security' preset in: {out}"
        assert "pre-pr" in out, f"Missing 'pre-pr' preset in: {out}"
        assert "refactor" in out, f"Missing 'refactor' preset in: {out}"

    @pytest.mark.slow
    def test_report_run(self, indexed_project):
        """roam report first-contact should run all sections without crashing."""
        out, rc = roam("report", "first-contact", cwd=indexed_project)
        assert rc == 0, f"report first-contact failed: {out}"
        # Should mention the report name and section statuses
        assert "first-contact" in out or "Report" in out, \
            f"Missing report header in: {out}"
        assert "OK" in out or "pass" in out.lower(), \
            f"No section success indicators in: {out}"


# ============================================================================
# TestJsonEnvelope
# ============================================================================

class TestJsonEnvelope:
    """Verify that key commands produce valid JSON with the standard envelope."""

    def _assert_envelope(self, out, expected_command):
        """Parse JSON and assert standard envelope keys."""
        data = json.loads(out)
        assert "command" in data, f"Missing 'command' in JSON: {list(data.keys())}"
        assert data["command"] == expected_command, \
            f"Expected command={expected_command}, got {data['command']}"
        assert "timestamp" in data, f"Missing 'timestamp' in JSON: {list(data.keys())}"
        assert "summary" in data, f"Missing 'summary' in JSON: {list(data.keys())}"
        return data

    def test_json_dead(self, indexed_project):
        """roam --json dead should have standard envelope."""
        out, rc = roam("--json", "dead", cwd=indexed_project)
        assert rc == 0, f"dead --json failed: {out}"
        self._assert_envelope(out, "dead")

    def test_json_health(self, indexed_project):
        """roam --json health should have standard envelope."""
        out, rc = roam("--json", "health", cwd=indexed_project)
        assert rc == 0, f"health --json failed: {out}"
        self._assert_envelope(out, "health")

    def test_json_understand(self, indexed_project):
        """roam --json understand should have standard envelope."""
        out, rc = roam("--json", "understand", cwd=indexed_project)
        assert rc == 0, f"understand --json failed: {out}"
        data = self._assert_envelope(out, "understand")
        # Understand-specific keys
        assert "tech_stack" in data
        assert "architecture" in data

    def test_json_snapshot(self, indexed_project):
        """roam --json snapshot should have standard envelope."""
        out, rc = roam("--json", "snapshot", "--tag", "json-test",
                       cwd=indexed_project)
        assert rc == 0, f"snapshot --json failed: {out}"
        self._assert_envelope(out, "snapshot")

    def test_json_trend(self, indexed_project):
        """roam --json trend should have standard envelope."""
        # Ensure at least one snapshot exists
        roam("snapshot", "--tag", "json-trend-seed", cwd=indexed_project)
        out, rc = roam("--json", "trend", cwd=indexed_project)
        assert rc == 0, f"trend --json failed: {out}"
        data = self._assert_envelope(out, "trend")
        assert "snapshots" in data

    def test_json_context(self, indexed_project):
        """roam --json context should have standard envelope."""
        out, rc = roam("--json", "context", "create_user", cwd=indexed_project)
        assert rc == 0, f"context --json failed: {out}"
        data = self._assert_envelope(out, "context")
        assert "callers" in data or "symbol" in data or "symbols" in data


# ============================================================================
# v6.0 Commands — comprehensive tests for new intelligence features
# ============================================================================

class TestV6Complexity:
    """Tests for cognitive complexity analysis."""

    def test_complexity_runs(self, indexed_project):
        out, rc = roam("complexity", cwd=indexed_project)
        assert rc == 0, f"complexity failed: {out}"
        assert "complexity" in out.lower() or "analyzed" in out.lower()

    def test_complexity_bumpy_road(self, indexed_project):
        out, rc = roam("complexity", "--bumpy-road", cwd=indexed_project)
        # May not find bumpy-road files in small project - that's OK
        assert rc == 0, f"bumpy-road failed: {out}"

    def test_complexity_json(self, indexed_project):
        out, rc = roam("--json", "complexity", cwd=indexed_project)
        assert rc == 0, f"complexity --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "complexity"
        assert "symbols" in data or "summary" in data

    def test_complexity_by_file(self, indexed_project):
        out, rc = roam("complexity", "--by-file", cwd=indexed_project)
        assert rc == 0, f"complexity --by-file failed: {out}"

    def test_complexity_threshold(self, indexed_project):
        out, rc = roam("complexity", "--threshold", "0", cwd=indexed_project)
        assert rc == 0


class TestV6Conventions:
    """Tests for convention detection."""

    def test_conventions_runs(self, indexed_project):
        out, rc = roam("conventions", cwd=indexed_project)
        assert rc == 0, f"conventions failed: {out}"
        assert "Conventions" in out or "Naming" in out

    def test_conventions_json(self, indexed_project):
        out, rc = roam("--json", "conventions", cwd=indexed_project)
        assert rc == 0, f"conventions --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "conventions"


class TestV6Debt:
    """Tests for hotspot-weighted debt."""

    def test_debt_runs(self, indexed_project):
        out, rc = roam("debt", cwd=indexed_project)
        assert rc == 0, f"debt failed: {out}"

    def test_debt_json(self, indexed_project):
        out, rc = roam("--json", "debt", cwd=indexed_project)
        assert rc == 0, f"debt --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "debt"
        assert "summary" in data


class TestV6AffectedTests:
    """Tests for affected-tests command."""

    def test_affected_tests_by_file(self, indexed_project):
        out, rc = roam("affected-tests", "service.py", cwd=indexed_project)
        # May find tests or not depending on test file structure
        assert rc == 0, f"affected-tests failed: {out}"

    def test_affected_tests_json(self, indexed_project):
        out, rc = roam("--json", "affected-tests", "create_user", cwd=indexed_project)
        assert rc == 0, f"affected-tests --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "affected-tests"


class TestV6EntryPoints:
    """Tests for entry point catalog."""

    def test_entry_points_runs(self, indexed_project):
        out, rc = roam("entry-points", cwd=indexed_project)
        assert rc == 0, f"entry-points failed: {out}"

    def test_entry_points_json(self, indexed_project):
        out, rc = roam("--json", "entry-points", cwd=indexed_project)
        assert rc == 0, f"entry-points --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "entry-points"


class TestV6SafeZones:
    """Tests for safe refactoring zones."""

    def test_safe_zones_runs(self, indexed_project):
        out, rc = roam("safe-zones", "create_user", cwd=indexed_project)
        assert rc == 0, f"safe-zones failed: {out}"
        assert "zone" in out.lower() or "Zone" in out

    def test_safe_zones_json(self, indexed_project):
        out, rc = roam("--json", "safe-zones", "create_user", cwd=indexed_project)
        assert rc == 0, f"safe-zones --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "safe-zones"


class TestV6Patterns:
    """Tests for architectural pattern recognition."""

    def test_patterns_runs(self, indexed_project):
        out, rc = roam("patterns", cwd=indexed_project)
        assert rc == 0, f"patterns failed: {out}"

    def test_patterns_json(self, indexed_project):
        out, rc = roam("--json", "patterns", cwd=indexed_project)
        assert rc == 0, f"patterns --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "patterns"


class TestV6Fitness:
    """Tests for architectural fitness functions."""

    def test_fitness_init(self, indexed_project):
        out, rc = roam("fitness", "--init", cwd=indexed_project)
        assert rc == 0, f"fitness --init failed: {out}"
        assert (indexed_project / ".roam" / "fitness.yaml").exists()

    def test_fitness_runs(self, indexed_project):
        out, rc = roam("fitness", cwd=indexed_project)
        # May pass or fail depending on rules — either is valid
        assert "Fitness check" in out or "rules" in out.lower()

    def test_fitness_json(self, indexed_project):
        out, rc = roam("--json", "fitness", cwd=indexed_project)
        data = json.loads(out)
        assert data["command"] == "fitness"
        assert "rules" in data


class TestV6Preflight:
    """Tests for pre-flight checklist."""

    def test_preflight_symbol(self, indexed_project):
        out, rc = roam("preflight", "create_user", cwd=indexed_project)
        assert rc == 0, f"preflight failed: {out}"
        assert "Pre-flight" in out or "risk" in out.lower()

    def test_preflight_json(self, indexed_project):
        out, rc = roam("--json", "preflight", "create_user", cwd=indexed_project)
        assert rc == 0, f"preflight --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "preflight"
        assert "summary" in data
        assert "risk_level" in data["summary"]


class TestV6Alerts:
    """Tests for health trend alerts."""

    def test_alerts_runs(self, indexed_project):
        out, rc = roam("alerts", cwd=indexed_project)
        assert rc == 0, f"alerts failed: {out}"

    def test_alerts_json(self, indexed_project):
        out, rc = roam("--json", "alerts", cwd=indexed_project)
        assert rc == 0, f"alerts --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "alerts"


class TestV6BusFactor:
    """Tests for knowledge loss / bus factor."""

    def test_bus_factor_runs(self, indexed_project):
        out, rc = roam("bus-factor", cwd=indexed_project)
        assert rc == 0, f"bus-factor failed: {out}"

    def test_bus_factor_json(self, indexed_project):
        out, rc = roam("--json", "bus-factor", cwd=indexed_project)
        assert rc == 0, f"bus-factor --json failed: {out}"
        data = json.loads(out)
        assert data["command"] == "bus-factor"


class TestV6MapBudget:
    """Tests for token-budget-aware repo map."""

    def test_map_budget_limits_output(self, indexed_project):
        out_full, rc = roam("map", cwd=indexed_project)
        assert rc == 0

        out_budget, rc = roam("map", "--budget", "100", cwd=indexed_project)
        assert rc == 0
        assert "Token budget:" in out_budget
        # Budget output should be shorter or equal
        assert len(out_budget) <= len(out_full) + 50  # +50 for footer

    def test_map_budget_json(self, indexed_project):
        out, rc = roam("--json", "map", "--budget", "200", cwd=indexed_project)
        assert rc == 0
        data = json.loads(out)
        assert "budget" in data.get("summary", {}) or "token_budget" in str(data)


class TestV6TaskContext:
    """Tests for task-aware context mode."""

    def test_context_task_refactor(self, indexed_project):
        out, rc = roam("context", "--task", "refactor", "create_user", cwd=indexed_project)
        assert rc == 0, f"context --task refactor failed: {out}"
        assert "Refactor" in out or "refactor" in out.lower()

    def test_context_task_debug(self, indexed_project):
        out, rc = roam("context", "--task", "debug", "create_user", cwd=indexed_project)
        assert rc == 0, f"context --task debug failed: {out}"

    def test_context_task_extend(self, indexed_project):
        out, rc = roam("context", "--task", "extend", "create_user", cwd=indexed_project)
        assert rc == 0, f"context --task extend failed: {out}"

    def test_context_task_review(self, indexed_project):
        out, rc = roam("context", "--task", "review", "create_user", cwd=indexed_project)
        assert rc == 0, f"context --task review failed: {out}"

    def test_context_task_understand(self, indexed_project):
        out, rc = roam("context", "--task", "understand", "create_user", cwd=indexed_project)
        assert rc == 0, f"context --task understand failed: {out}"


class TestV6EnhancedUnderstand:
    """Tests for enhanced understand with conventions, complexity, patterns."""

    def test_understand_has_conventions(self, indexed_project):
        out, rc = roam("understand", cwd=indexed_project)
        assert rc == 0
        assert "Conventions" in out or "convention" in out.lower()

    def test_understand_has_complexity(self, indexed_project):
        out, rc = roam("understand", cwd=indexed_project)
        assert rc == 0
        assert "Complexity" in out or "complexity" in out.lower()

    def test_understand_json_has_new_fields(self, indexed_project):
        out, rc = roam("--json", "understand", cwd=indexed_project)
        assert rc == 0
        data = json.loads(out)
        assert "conventions" in data
        assert "complexity" in data or "complexity" in str(data)


class TestV6EnhancedDescribe:
    """Tests for enhanced describe with conventions and complexity."""

    def test_describe_has_conventions(self, indexed_project):
        out, rc = roam("describe", cwd=indexed_project)
        assert rc == 0
        assert "Conventions" in out or "convention" in out.lower()

    def test_describe_has_complexity(self, indexed_project):
        out, rc = roam("describe", cwd=indexed_project)
        assert rc == 0
        assert "Complexity" in out or "complexity" in out.lower()


class TestV6DocStaleness:
    """Tests for doc staleness detection."""

    def test_doc_staleness_runs(self, indexed_project):
        out, rc = roam("doc-staleness", cwd=indexed_project)
        assert rc == 0, f"doc-staleness failed: {out}"

    def test_doc_staleness_json(self, indexed_project):
        out, rc = roam("--json", "doc-staleness", cwd=indexed_project)
        assert rc == 0
        data = json.loads(out)
        assert data["command"] == "doc-staleness"


class TestV6FnCoupling:
    """Tests for function-level temporal coupling."""

    def test_fn_coupling_runs(self, indexed_project):
        out, rc = roam("fn-coupling", cwd=indexed_project)
        assert rc == 0, f"fn-coupling failed: {out}"

    def test_fn_coupling_json(self, indexed_project):
        out, rc = roam("--json", "fn-coupling", cwd=indexed_project)
        assert rc == 0
        data = json.loads(out)
        assert data["command"] == "fn-coupling"
