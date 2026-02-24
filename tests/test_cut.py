"""Tests for the `roam cut` command (minimum cut safety zones).

Covers:
- Basic invocation (exit 0)
- JSON envelope contract
- Boundary detection and field presence
- Leak edge detection and field presence
- Verdict-first text output
- Fragile boundary flagging
- --between filter
- --top limit
- Empty project graceful handling
- --leak-edges flag
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    index_in_process,
    git_init,
)

from roam.commands.cmd_cut import cut


# ===========================================================================
# Helper: invoke `cut` directly (avoids needing cli.py registration)
# ===========================================================================


def run_cut(runner, project, extra_args=None, json_mode=False):
    """Invoke the cut command directly via CliRunner in the project directory."""
    args = []
    if json_mode:
        # Simulate --json by setting ctx.obj
        pass
    args.extend(extra_args or [])

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        if json_mode:
            result = runner.invoke(
                cut,
                args,
                obj={"json": True},
                catch_exceptions=False,
            )
        else:
            result = runner.invoke(
                cut,
                args,
                obj={"json": False},
                catch_exceptions=False,
            )
    finally:
        os.chdir(old_cwd)
    return result


def parse_json(result, command=None):
    """Parse JSON output from a result, asserting exit_code == 0."""
    assert result.exit_code == 0, (
        f"Command {command or 'cut'} failed (exit {result.exit_code}):\n"
        f"{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from {command or 'cut'}: {e}\n"
            f"Output was:\n{result.output[:500]}"
        )


def check_envelope(data, command="cut"):
    """Validate the standard roam JSON envelope keys."""
    assert isinstance(data, dict)
    assert "command" in data
    assert data["command"] == command
    assert "version" in data
    assert "timestamp" in data.get("_meta", data)
    assert "summary" in data
    assert isinstance(data["summary"], dict)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def cut_project(tmp_path):
    """Project with clear cluster boundaries for cut analysis."""
    proj = tmp_path / "cut_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Cluster 1: auth domain
    auth = proj / "auth"
    auth.mkdir()
    (auth / "__init__.py").write_text("")
    (auth / "login.py").write_text(
        "def authenticate(username, password):\n"
        "    return verify_password(username, password)\n\n"
        "def verify_password(username, password):\n"
        "    return True\n"
    )
    (auth / "tokens.py").write_text(
        "from auth.login import authenticate\n\n"
        "def create_token(user):\n"
        "    return f'token_{user}'\n\n"
        "def validate_token(token):\n"
        "    return token.startswith('token_')\n"
    )

    # Cluster 2: billing domain
    billing = proj / "billing"
    billing.mkdir()
    (billing / "__init__.py").write_text("")
    (billing / "charge.py").write_text(
        "from auth.tokens import validate_token\n\n"
        "def process_charge(token, amount):\n"
        "    if validate_token(token):\n"
        "        return calculate_total(amount)\n"
        "    return None\n\n"
        "def calculate_total(amount):\n"
        "    return amount * 1.1\n"
    )
    (billing / "invoice.py").write_text(
        "from billing.charge import calculate_total\n\n"
        "def create_invoice(items):\n"
        "    total = sum(calculate_total(i) for i in items)\n"
        "    return {'total': total}\n"
    )

    # Cluster 3: api layer
    (proj / "api.py").write_text(
        "from auth.tokens import create_token\n"
        "from billing.charge import process_charge\n\n"
        "def handle_purchase(data):\n"
        "    token = create_token(data['user'])\n"
        "    return process_charge(token, data['amount'])\n"
    )

    git_init(proj)
    old = os.getcwd()
    os.chdir(str(proj))
    index_in_process(proj)
    os.chdir(old)
    return proj


@pytest.fixture
def empty_project(tmp_path):
    """Single-file project — likely results in one cluster, no boundaries."""
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text(
        "def main():\n"
        "    print('hello')\n"
    )
    git_init(proj)
    old = os.getcwd()
    os.chdir(str(proj))
    index_in_process(proj)
    os.chdir(old)
    return proj


@pytest.fixture
def runner():
    return CliRunner()


# ===========================================================================
# Tests
# ===========================================================================


class TestCutBasic:
    """Basic invocation tests."""

    def test_cut_runs(self, runner, cut_project):
        """Command exits with code 0."""
        result = run_cut(runner, cut_project)
        assert result.exit_code == 0, (
            f"roam cut failed (exit {result.exit_code}):\n{result.output}"
        )

    def test_cut_verdict_line(self, runner, cut_project):
        """Text output starts with VERDICT:."""
        result = run_cut(runner, cut_project)
        assert result.exit_code == 0
        assert result.output.startswith("VERDICT:"), (
            f"Expected output to start with VERDICT:, got:\n{result.output[:200]}"
        )


class TestCutJsonEnvelope:
    """JSON envelope contract tests."""

    def test_cut_json_envelope(self, runner, cut_project):
        """JSON output follows the standard roam envelope schema."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        check_envelope(data, command="cut")

    def test_cut_json_summary_fields(self, runner, cut_project):
        """Summary contains all required fields with correct types."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        summary = data["summary"]
        assert "verdict" in summary
        assert "boundaries_analyzed" in summary
        assert "fragile_boundaries" in summary
        assert "leak_edges_found" in summary
        assert isinstance(summary["verdict"], str)
        assert isinstance(summary["boundaries_analyzed"], int)
        assert isinstance(summary["fragile_boundaries"], int)
        assert isinstance(summary["leak_edges_found"], int)


class TestCutBoundaries:
    """Boundary detection tests."""

    def test_cut_has_boundaries(self, runner, cut_project):
        """JSON output includes a boundaries list."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        assert "boundaries" in data
        assert isinstance(data["boundaries"], list)

    def test_cut_boundary_fields(self, runner, cut_project):
        """Each boundary has the required fields with correct types."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        boundaries = data["boundaries"]
        if boundaries:
            b = boundaries[0]
            assert "cluster_a" in b, "boundary missing cluster_a"
            assert "cluster_b" in b, "boundary missing cluster_b"
            assert "cross_edges" in b, "boundary missing cross_edges"
            assert "min_cut" in b, "boundary missing min_cut"
            assert "thinness" in b, "boundary missing thinness"
            assert "fragile" in b, "boundary missing fragile"
            assert isinstance(b["cross_edges"], int)
            assert isinstance(b["min_cut"], int)
            assert isinstance(b["thinness"], float)
            assert isinstance(b["fragile"], bool)

    def test_cut_fragile_detection(self, runner, cut_project):
        """Fragile flag is correctly applied based on thinness threshold < 0.4."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        for b in data["boundaries"]:
            if b["thinness"] < 0.4:
                assert b["fragile"] is True, (
                    f"Boundary with thinness {b['thinness']} should be fragile"
                )
            else:
                assert b["fragile"] is False, (
                    f"Boundary with thinness {b['thinness']} should not be fragile"
                )


class TestCutLeakEdges:
    """Leak edge detection tests."""

    def test_cut_has_leak_edges(self, runner, cut_project):
        """JSON output includes a leak_edges list."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        assert "leak_edges" in data
        assert isinstance(data["leak_edges"], list)

    def test_cut_leak_edge_fields(self, runner, cut_project):
        """Each leak edge has source, target, betweenness, and suggestion fields."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        leak_edges = data["leak_edges"]
        if leak_edges:
            le = leak_edges[0]
            assert "source" in le, "leak edge missing source"
            assert "target" in le, "leak edge missing target"
            assert "betweenness" in le, "leak edge missing betweenness"
            assert "suggestion" in le, "leak edge missing suggestion"
            assert isinstance(le["betweenness"], float)
            assert isinstance(le["suggestion"], str)

    def test_cut_leak_edges_flag(self, runner, cut_project):
        """--leak-edges flag runs without error and outputs VERDICT:."""
        result = run_cut(runner, cut_project, extra_args=["--leak-edges"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


class TestCutFilters:
    """Filter and limit option tests."""

    def test_cut_between_filter(self, runner, cut_project):
        """--between flag limits output to at most the total boundary count."""
        result_all = run_cut(runner, cut_project, json_mode=True)
        data_all = parse_json(result_all, command="cut")
        boundaries_all = data_all["boundaries"]

        if not boundaries_all:
            pytest.skip("No boundaries to test --between filter on")

        b = boundaries_all[0]
        # Use first word of each label as search token
        label_a = b["cluster_a"].split("/")[0].split("+")[0].strip()
        label_b = b["cluster_b"].split("/")[0].split("+")[0].strip()

        result = run_cut(
            runner, cut_project,
            extra_args=["--between", label_a, label_b],
            json_mode=True,
        )
        data = parse_json(result, command="cut")
        assert result.exit_code == 0
        assert data["summary"]["boundaries_analyzed"] <= data_all["summary"]["boundaries_analyzed"]

    def test_cut_top_limit(self, runner, cut_project):
        """--top 1 limits the number of returned boundaries to at most 1."""
        result = run_cut(runner, cut_project, extra_args=["--top", "1"], json_mode=True)
        data = parse_json(result, command="cut")
        assert data["summary"]["boundaries_analyzed"] <= 1
        assert len(data["boundaries"]) <= 1

    def test_cut_top_default(self, runner, cut_project):
        """Default --top is 10; boundaries and leak_edges each have at most 10 items."""
        result = run_cut(runner, cut_project, json_mode=True)
        data = parse_json(result, command="cut")
        assert len(data["boundaries"]) <= 10
        assert len(data["leak_edges"]) <= 10


class TestCutEdgeCases:
    """Edge case handling tests."""

    def test_cut_empty_project(self, runner, empty_project):
        """Single-file project exits gracefully with VERDICT: in output."""
        result = run_cut(runner, empty_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_cut_empty_project_json(self, runner, empty_project):
        """Single-file project returns a valid JSON envelope with zero boundaries."""
        result = run_cut(runner, empty_project, json_mode=True)
        assert result.exit_code == 0
        data = parse_json(result, command="cut")
        check_envelope(data, command="cut")
        assert data["summary"]["boundaries_analyzed"] == 0

    def test_cut_between_no_match(self, runner, cut_project):
        """--between with unrecognized names returns zero boundaries gracefully."""
        result = run_cut(
            runner, cut_project,
            extra_args=["--between", "zzz_nonexistent_x", "zzz_nonexistent_y"],
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json(result, command="cut")
        assert data["summary"]["boundaries_analyzed"] == 0
