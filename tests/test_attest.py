"""Tests for the roam attest command (D2: proof-carrying PRs)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def attest_project(tmp_path, monkeypatch):
    """Project with multiple files, indexed, with uncommitted changes.

    Creates a baseline, indexes, then modifies files to create a diff.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Source files
    (proj / "models.py").write_text(
        'class User:\n'
        '    def __init__(self, name, email):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        '    def display_name(self):\n'
        '        return self.name.title()\n'
    )

    (proj / "service.py").write_text(
        'from models import User\n'
        '\n'
        'def create_user(name, email):\n'
        '    user = User(name, email)\n'
        '    return user\n'
        '\n'
        'def get_display(user):\n'
        '    return user.display_name()\n'
    )

    (proj / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    return f"{first} {last}"\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    # Modify files to create diff
    (proj / "service.py").write_text(
        'from models import User\n'
        '\n'
        'def create_user(name, email):\n'
        '    user = User(name, email)\n'
        '    if not email:\n'
        '        raise ValueError("email required")\n'
        '    return user\n'
        '\n'
        'def get_display(user):\n'
        '    return user.display_name()\n'
        '\n'
        'def new_helper():\n'
        '    return 42\n'
    )

    return proj


@pytest.fixture
def attest_no_changes(tmp_path, monkeypatch):
    """Project with no uncommitted changes."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "app.py").write_text(
        'def main():\n'
        '    return "hello"\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    return proj


# ---------------------------------------------------------------------------
# Basic command tests
# ---------------------------------------------------------------------------


class TestAttestCommand:
    """Test the attest CLI command."""

    def test_attest_runs(self, cli_runner, attest_project, monkeypatch):
        """Command exits 0."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        assert result.exit_code == 0

    def test_attest_json_envelope(self, cli_runner, attest_project, monkeypatch):
        """Valid JSON envelope."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        assert_json_envelope(data, "attest")

    def test_attest_verdict_line(self, cli_runner, attest_project, monkeypatch):
        """Text output starts with VERDICT:."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        assert result.output.strip().startswith("VERDICT:")

    def test_attest_no_changes(self, cli_runner, attest_no_changes, monkeypatch):
        """No changes -> graceful message."""
        monkeypatch.chdir(attest_no_changes)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_no_changes)
        assert result.exit_code == 0
        assert "no changes" in result.output.lower() or "No changes" in result.output

    def test_attest_no_changes_json(self, cli_runner, attest_no_changes, monkeypatch):
        """No changes in JSON mode -> valid envelope."""
        monkeypatch.chdir(attest_no_changes)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_no_changes,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        assert_json_envelope(data, "attest")
        assert "no changes" in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# Evidence section tests
# ---------------------------------------------------------------------------


class TestAttestEvidence:
    """Test that evidence sections are populated."""

    def test_attest_has_blast_radius(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains blast_radius evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        br = evidence.get("blast_radius", {})
        assert "changed_files" in br
        assert br["changed_files"] >= 1

    def test_attest_has_risk(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains risk evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        risk = evidence.get("risk")
        assert risk is not None
        assert "score" in risk
        assert "level" in risk

    def test_attest_has_breaking_changes(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains breaking_changes evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        bc = evidence.get("breaking_changes", {})
        assert "removed" in bc
        assert "signature_changed" in bc

    def test_attest_has_budget(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains budget evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        budget = evidence.get("budget", {})
        assert "rules_checked" in budget

    def test_attest_has_tests(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains tests evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        tests = evidence.get("tests", {})
        assert "selected" in tests
        assert "direct" in tests
        assert "transitive" in tests

    def test_attest_has_effects(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains effects evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        effects = evidence.get("effects")
        assert isinstance(effects, list)

    def test_attest_has_attestation_metadata(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains attestation metadata."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        att = data.get("attestation", {})
        assert att.get("version") == "1.0"
        assert att.get("tool") == "roam-code"
        assert "timestamp" in att
        assert "git_range" in att


# ---------------------------------------------------------------------------
# Format tests
# ---------------------------------------------------------------------------


class TestAttestFormats:
    """Test different output formats."""

    def test_attest_markdown_format(self, cli_runner, attest_project, monkeypatch):
        """--format markdown produces markdown output."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest", "--format", "markdown"],
                            cwd=attest_project)
        assert result.exit_code == 0
        assert "## Roam Attestation" in result.output
        assert "Blast Radius" in result.output

    def test_attest_json_format(self, cli_runner, attest_project, monkeypatch):
        """--format json produces JSON output."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest", "--format", "json"],
                            cwd=attest_project)
        data = json.loads(result.output)
        assert data["command"] == "attest"

    def test_attest_output_file(self, cli_runner, attest_project, monkeypatch):
        """--output writes to file."""
        monkeypatch.chdir(attest_project)
        out_path = attest_project / "attestation.txt"
        result = invoke_cli(cli_runner,
                            ["attest", "--output", str(out_path)],
                            cwd=attest_project)
        assert result.exit_code == 0
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "VERDICT:" in content


# ---------------------------------------------------------------------------
# Sign/hash tests
# ---------------------------------------------------------------------------


class TestAttestSign:
    """Test the --sign content hash feature."""

    def test_attest_sign_includes_hash(self, cli_runner, attest_project, monkeypatch):
        """--sign adds content_hash to attestation."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest", "--sign"],
                            cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        att = data.get("attestation", {})
        assert "content_hash" in att
        assert att["content_hash"].startswith("sha256:")

    def test_attest_sign_hash_consistent(self, cli_runner, attest_project, monkeypatch):
        """Same evidence should produce the same hash."""
        monkeypatch.chdir(attest_project)
        result1 = invoke_cli(cli_runner, ["attest", "--sign"],
                             cwd=attest_project, json_mode=True)
        result2 = invoke_cli(cli_runner, ["attest", "--sign"],
                             cwd=attest_project, json_mode=True)
        data1 = parse_json_output(result1, "attest")
        data2 = parse_json_output(result2, "attest")
        # Hashes should be the same since evidence is deterministic
        hash1 = data1["attestation"]["content_hash"]
        hash2 = data2["attestation"]["content_hash"]
        assert hash1 == hash2

    def test_attest_no_sign_no_hash(self, cli_runner, attest_project, monkeypatch):
        """Without --sign, no content_hash in attestation."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"],
                            cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        att = data.get("attestation", {})
        assert "content_hash" not in att


# ---------------------------------------------------------------------------
# Verdict tests
# ---------------------------------------------------------------------------


class TestAttestVerdict:
    """Test verdict computation."""

    def test_verdict_safe_by_default(self, cli_runner, attest_project, monkeypatch):
        """Small changes should be safe to merge by default."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        verdict = data.get("verdict", {})
        assert isinstance(verdict.get("safe_to_merge"), bool)

    def test_verdict_has_conditions(self, cli_runner, attest_project, monkeypatch):
        """Verdict should include conditions list."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        verdict = data.get("verdict", {})
        assert "conditions" in verdict
        assert isinstance(verdict["conditions"], list)

    def test_verdict_in_summary(self, cli_runner, attest_project, monkeypatch):
        """Summary should include safe_to_merge and risk info."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project,
                            json_mode=True)
        data = parse_json_output(result, "attest")
        summary = data.get("summary", {})
        assert "safe_to_merge" in summary
        assert "verdict" in summary


# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------


class TestContentHash:
    """Test the content hash helper."""

    def test_content_hash_deterministic(self):
        from roam.commands.cmd_attest import _content_hash
        evidence = {"risk": {"score": 42}, "tests": {"selected": 5}}
        h1 = _content_hash(evidence)
        h2 = _content_hash(evidence)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_content_hash_changes_with_data(self):
        from roam.commands.cmd_attest import _content_hash
        e1 = {"risk": {"score": 42}}
        e2 = {"risk": {"score": 99}}
        assert _content_hash(e1) != _content_hash(e2)


class TestComputeVerdict:
    """Test the verdict computation."""

    def test_safe_when_no_issues(self):
        from roam.commands.cmd_attest import _compute_verdict
        risk = {"score": 10, "level": "LOW"}
        breaking = {"removed": [], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 3, "failed": 0, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert v["safe_to_merge"] is True

    def test_unsafe_when_budget_exceeded(self):
        from roam.commands.cmd_attest import _compute_verdict
        risk = {"score": 10, "level": "LOW"}
        breaking = {"removed": [], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 2, "failed": 1, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert v["safe_to_merge"] is False

    def test_warnings_on_breaking_changes(self):
        from roam.commands.cmd_attest import _compute_verdict
        risk = {"score": 10, "level": "LOW"}
        breaking = {"removed": [{"name": "foo"}], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 3, "failed": 0, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert any("breaking" in w for w in v["warnings"])

    def test_warnings_on_high_risk(self):
        from roam.commands.cmd_attest import _compute_verdict
        risk = {"score": 80, "level": "CRITICAL"}
        breaking = {"removed": [], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 3, "failed": 0, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert any("CRITICAL" in w for w in v["warnings"])
