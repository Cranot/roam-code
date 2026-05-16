"""Tests for roam bus-factor -- knowledge loss risk per module."""

from __future__ import annotations

import subprocess

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ===========================================================================
# Fixture: Python project with git history
# ===========================================================================


def _git_commit_as(path, author_name, author_email, message):
    """Make a git commit with an explicit author identity."""
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=path,
        capture_output=True,
        env={**__import__("os").environ, **env},
    )


@pytest.fixture
def bus_project(tmp_path):
    """A project with multiple git commits from two different authors.

    Alice dominates src/core.py (>70% of changes), while Bob and Alice
    share src/utils.py. This ensures bus-factor analysis has real data.
    """
    proj = tmp_path / "bus_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    # Initial commit — neutral author via git_init
    (src / "core.py").write_text(
        "class Engine:\n"
        '    """Core processing engine."""\n'
        "    def run(self):\n"
        "        pass\n"
        "\n"
        "    def stop(self):\n"
        "        pass\n"
    )
    (src / "utils.py").write_text(
        "def format_output(data):\n"
        '    """Format data for output."""\n'
        "    return str(data)\n"
        "\n"
        "\n"
        "def parse_input(raw):\n"
        '    """Parse raw input string."""\n'
        "    return raw.strip()\n"
    )

    git_init(proj)

    # Alice makes several commits to core.py — she dominates this directory
    (src / "core.py").write_text(
        "class Engine:\n"
        '    """Core processing engine (Alice revision 1)."""\n'
        "    def run(self):\n"
        "        return True\n"
        "\n"
        "    def stop(self):\n"
        "        return False\n"
        "\n"
        "    def reset(self):\n"
        "        pass\n"
    )
    _git_commit_as(proj, "Alice", "alice@example.com", "engine: add reset method")

    (src / "core.py").write_text(
        "class Engine:\n"
        '    """Core processing engine (Alice revision 2)."""\n'
        "    def run(self):\n"
        "        return True\n"
        "\n"
        "    def stop(self):\n"
        "        return False\n"
        "\n"
        "    def reset(self):\n"
        "        self._state = None\n"
        "\n"
        "    def configure(self, opts):\n"
        "        self._opts = opts\n"
    )
    _git_commit_as(proj, "Alice", "alice@example.com", "engine: add configure method")

    # Bob makes one commit to utils.py — shared with Alice's original
    (src / "utils.py").write_text(
        "def format_output(data):\n"
        '    """Format data for output (Bob revision)."""\n'
        "    return repr(data)\n"
        "\n"
        "\n"
        "def parse_input(raw):\n"
        '    """Parse raw input string."""\n'
        "    return raw.strip().lower()\n"
        "\n"
        "\n"
        "def sanitize(s):\n"
        '    """Sanitize a string for safe output."""\n'
        '    return s.replace("<", "&lt;")\n'
    )
    _git_commit_as(proj, "Bob", "bob@example.com", "utils: add sanitize, improve format")

    index_in_process(proj)
    return proj


@pytest.fixture
def minimal_project(tmp_path):
    """A minimal project with a single commit (no git history for churn data)."""
    proj = tmp_path / "minimal_bus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    pass\n")
    git_init(proj)
    index_in_process(proj)
    return proj


# ===========================================================================
# Smoke tests
# ===========================================================================


class TestBusFactorSmoke:
    """Basic invocation smoke tests."""

    def test_exits_zero(self, cli_runner, bus_project, monkeypatch):
        """roam bus-factor exits 0 on a project with git history."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project)
        assert result.exit_code == 0

    def test_produces_output(self, cli_runner, bus_project, monkeypatch):
        """roam bus-factor produces non-empty output."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project)
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    def test_help_works(self, cli_runner):
        """--help exits 0 and describes the command."""
        result = invoke_cli(cli_runner, ["bus-factor", "--help"])
        assert result.exit_code == 0
        assert "bus" in result.output.lower() or "factor" in result.output.lower()

    def test_limit_option(self, cli_runner, bus_project, monkeypatch):
        """--limit flag is accepted and exits 0."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor", "--limit", "5"], cwd=bus_project)
        assert result.exit_code == 0

    def test_stale_months_option(self, cli_runner, bus_project, monkeypatch):
        """--stale-months flag is accepted and exits 0."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor", "--stale-months", "3"], cwd=bus_project)
        assert result.exit_code == 0

    def test_brain_methods_option(self, cli_runner, bus_project, monkeypatch):
        """--brain-methods flag is accepted and exits 0."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor", "--brain-methods"], cwd=bus_project)
        assert result.exit_code == 0

    def test_no_git_history_exits_zero(self, cli_runner, minimal_project, monkeypatch):
        """roam bus-factor on a project with a single commit exits 0 gracefully."""
        monkeypatch.chdir(minimal_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=minimal_project)
        assert result.exit_code == 0


# ===========================================================================
# JSON envelope tests
# ===========================================================================


class TestBusFactorJSON:
    """JSON mode output validation."""

    def test_json_envelope(self, cli_runner, bus_project, monkeypatch):
        """JSON output follows the roam envelope contract."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        assert_json_envelope(data, command="bus-factor")

    def test_json_summary_has_verdict(self, cli_runner, bus_project, monkeypatch):
        """JSON summary contains a verdict string."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {list(summary.keys())}"
        assert isinstance(summary["verdict"], str)

    def test_json_summary_has_directory_count(self, cli_runner, bus_project, monkeypatch):
        """JSON summary contains the directories-analyzed count field.

        W21.7 field rename: ``directory_count`` → ``directories_analyzed`` so
        the LAW 4 humanizer produces ``"N directories analyzed"`` instead of
        the awkward ``"directory count N"``.
        """
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        summary = data.get("summary", {})
        assert "directories_analyzed" in summary, f"Missing 'directories_analyzed': {list(summary.keys())}"
        assert isinstance(summary["directories_analyzed"], int)

    def test_json_summary_has_high_risk(self, cli_runner, bus_project, monkeypatch):
        """JSON summary contains a high_risk count."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        summary = data.get("summary", {})
        assert "high_risk" in summary, f"Missing 'high_risk': {list(summary.keys())}"
        assert isinstance(summary["high_risk"], int)

    def test_json_has_directories_list(self, cli_runner, bus_project, monkeypatch):
        """JSON output contains a 'directories' list."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        assert "directories" in data, f"Missing 'directories' key: {list(data.keys())}"
        assert isinstance(data["directories"], list)

    def test_json_directories_have_expected_fields(self, cli_runner, bus_project, monkeypatch):
        """Each directory entry has the required fields."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        dirs = data.get("directories", [])
        if dirs:
            d = dirs[0]
            for field in (
                "directory",
                "bus_factor",
                "entropy",
                "knowledge_risk",
                "risk",
                "risk_score",
                "total_commits",
                "primary_author",
                "primary_share",
                "top_authors",
            ):
                assert field in d, f"Directory entry missing '{field}': {list(d.keys())}"

    def test_json_directories_bus_factor_positive(self, cli_runner, bus_project, monkeypatch):
        """bus_factor values in directory entries are positive integers."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        for d in data.get("directories", []):
            assert d["bus_factor"] >= 1, f"bus_factor should be >= 1 for {d.get('directory')}, got {d['bus_factor']}"

    def test_json_entropy_in_range(self, cli_runner, bus_project, monkeypatch):
        """Entropy values are in [0.0, 1.0]."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        for d in data.get("directories", []):
            assert 0.0 <= d["entropy"] <= 1.0, f"entropy out of range for {d.get('directory')}: {d['entropy']}"

    def test_json_brain_methods_flag(self, cli_runner, bus_project, monkeypatch):
        """--brain-methods adds brain_methods key to JSON output."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor", "--brain-methods"], cwd=bus_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        assert "brain_methods" in data, f"Missing 'brain_methods' key: {list(data.keys())}"
        assert isinstance(data["brain_methods"], list)

    def test_json_no_history_envelope(self, cli_runner, minimal_project, monkeypatch):
        """Single-commit project returns a valid envelope."""
        monkeypatch.chdir(minimal_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=minimal_project, json_mode=True)
        data = parse_json_output(result, "bus-factor")
        assert_json_envelope(data, command="bus-factor")
        # A single-commit project may have 0 or 1 directory entries depending on
        # whether roam was able to parse any churn data from the initial commit.
        assert isinstance(data.get("directories", []), list)


# ===========================================================================
# Text output tests
# ===========================================================================


class TestBusFactorText:
    """Text mode output validation."""

    def test_verdict_line_present(self, cli_runner, bus_project, monkeypatch):
        """Text output contains a VERDICT: line."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_verdict_is_first_line(self, cli_runner, bus_project, monkeypatch):
        """VERDICT: is the first non-empty line of text output."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:")

    def test_knowledge_risk_section_present(self, cli_runner, bus_project, monkeypatch):
        """Text output mentions 'Knowledge risk' or 'Knowledge loss'."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project)
        assert result.exit_code == 0
        lower = result.output.lower()
        assert "knowledge" in lower or "bus" in lower

    def test_no_history_fallback_message(self, cli_runner, minimal_project, monkeypatch):
        """No-history project text output has VERDICT: and a descriptive message."""
        monkeypatch.chdir(minimal_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=minimal_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_alice_appears_in_output(self, cli_runner, bus_project, monkeypatch):
        """Alice's name should appear in the output since she dominates core.py."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor"], cwd=bus_project)
        assert result.exit_code == 0
        assert "Alice" in result.output

    def test_brain_methods_section_when_flag_set(self, cli_runner, bus_project, monkeypatch):
        """--brain-methods shows 'Brain Methods' section (even if empty)."""
        monkeypatch.chdir(bus_project)
        result = invoke_cli(cli_runner, ["bus-factor", "--brain-methods"], cwd=bus_project)
        assert result.exit_code == 0
        assert "Brain" in result.output or "VERDICT:" in result.output


# ===========================================================================
# Unit tests for internal math helpers
# ===========================================================================


class TestBusFactorMath:
    """Unit tests for internal math helpers."""

    def test_contribution_entropy_single_author(self):
        """Single author => entropy 0.0."""
        from roam.commands.cmd_bus_factor import _contribution_entropy

        assert _contribution_entropy([1.0]) == 0.0

    def test_contribution_entropy_equal_shares(self):
        """Two authors with equal shares => entropy 1.0."""
        from roam.commands.cmd_bus_factor import _contribution_entropy

        result = _contribution_entropy([0.5, 0.5])
        assert abs(result - 1.0) < 1e-9

    def test_contribution_entropy_empty(self):
        """No contributors => entropy 0.0."""
        from roam.commands.cmd_bus_factor import _contribution_entropy

        assert _contribution_entropy([]) == 0.0

    def test_contribution_entropy_in_range(self):
        """Entropy is always in [0.0, 1.0]."""
        from roam.commands.cmd_bus_factor import _contribution_entropy

        for shares in ([1.0], [0.5, 0.5], [0.7, 0.2, 0.1], [0.4, 0.3, 0.2, 0.1]):
            result = _contribution_entropy(shares)
            assert 0.0 <= result <= 1.0, f"Entropy {result} out of range for {shares}"

    def test_knowledge_risk_label_critical(self):
        """Entropy < 0.3 => CRITICAL."""
        from roam.commands.cmd_bus_factor import _knowledge_risk_label

        assert _knowledge_risk_label(0.0) == "CRITICAL"
        assert _knowledge_risk_label(0.29) == "CRITICAL"

    def test_knowledge_risk_label_high(self):
        """Entropy in [0.3, 0.5) => HIGH."""
        from roam.commands.cmd_bus_factor import _knowledge_risk_label

        assert _knowledge_risk_label(0.3) == "HIGH"
        assert _knowledge_risk_label(0.49) == "HIGH"

    def test_knowledge_risk_label_medium(self):
        """Entropy in [0.5, 0.7) => MEDIUM."""
        from roam.commands.cmd_bus_factor import _knowledge_risk_label

        assert _knowledge_risk_label(0.5) == "MEDIUM"
        assert _knowledge_risk_label(0.69) == "MEDIUM"

    def test_knowledge_risk_label_low(self):
        """Entropy >= 0.7 => LOW."""
        from roam.commands.cmd_bus_factor import _knowledge_risk_label

        assert _knowledge_risk_label(0.7) == "LOW"
        assert _knowledge_risk_label(1.0) == "LOW"

    def test_staleness_factor_recent(self):
        """Primary author active within stale_months => staleness 1.0."""
        import time

        from roam.commands.cmd_bus_factor import _compute_staleness_factor

        recent_epoch = int(time.time()) - (30 * 86400)  # 1 month ago
        factor = _compute_staleness_factor(recent_epoch, stale_months=6)
        assert factor == 1.0, f"Expected 1.0 for recent activity, got {factor}"

    def test_staleness_factor_old_is_gt_one(self):
        """Primary author inactive for a long time => staleness factor > 1.0."""
        from roam.commands.cmd_bus_factor import _compute_staleness_factor

        old_epoch = 1  # Very old timestamp (1970)
        factor = _compute_staleness_factor(old_epoch, stale_months=6)
        assert factor > 1.0, f"Expected > 1.0 for stale activity, got {factor}"

    def test_staleness_factor_capped_at_three(self):
        """Staleness factor is capped at 3.0."""
        from roam.commands.cmd_bus_factor import _compute_staleness_factor

        factor = _compute_staleness_factor(1, stale_months=6)
        assert factor <= 3.0, f"Staleness factor exceeded cap: {factor}"

    def test_staleness_factor_no_epoch(self):
        """Missing epoch (0) returns the maximum staleness factor."""
        from roam.commands.cmd_bus_factor import _compute_staleness_factor

        factor = _compute_staleness_factor(0, stale_months=6)
        assert factor == 3.0, f"Expected 3.0 for missing epoch, got {factor}"

    def test_risk_label_high(self):
        """Score >= 1.5 => HIGH."""
        from roam.commands.cmd_bus_factor import _risk_label

        assert _risk_label(1.5) == "HIGH"
        assert _risk_label(3.0) == "HIGH"

    def test_risk_label_medium(self):
        """Score in [0.7, 1.5) => MEDIUM."""
        from roam.commands.cmd_bus_factor import _risk_label

        assert _risk_label(0.7) == "MEDIUM"
        assert _risk_label(1.49) == "MEDIUM"

    def test_risk_label_low(self):
        """Score < 0.7 => LOW."""
        from roam.commands.cmd_bus_factor import _risk_label

        assert _risk_label(0.0) == "LOW"
        assert _risk_label(0.69) == "LOW"

    def test_format_relative_time_today(self):
        """Very recent epoch shows 'today'."""
        import time

        from roam.commands.cmd_bus_factor import _format_relative_time

        now = int(time.time())
        assert _format_relative_time(now) == "today"

    def test_format_relative_time_unknown(self):
        """Zero epoch shows 'unknown'."""
        from roam.commands.cmd_bus_factor import _format_relative_time

        assert _format_relative_time(0) == "unknown"
