"""Tests for composite difficulty scoring in partition command (#128)."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import (
    invoke_cli,
    index_in_process,
    git_init,
    git_commit,
    parse_json_output,
    assert_json_envelope,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_module_project(project_factory):
    """A project with distinct modules for partition difficulty testing."""
    return project_factory({
        "auth/login.py": (
            "from auth.tokens import create_token\n"
            "def authenticate(u, p):\n"
            "    if not u or not p:\n"
            "        raise ValueError('bad')\n"
            "    return create_token(u)\n"
        ),
        "auth/tokens.py": (
            "def create_token(user): return 'tok'\n"
            "def verify_token(t): return True\n"
        ),
        "billing/invoice.py": (
            "from billing.tax import calc_tax\n"
            "def create_invoice(order):\n"
            "    total = order * 2\n"
            "    tax = calc_tax(order)\n"
            "    return total + tax\n"
        ),
        "billing/tax.py": (
            "def calc_tax(order): return order * 0.1\n"
        ),
        "api/routes.py": (
            "from auth.login import authenticate\n"
            "from billing.invoice import create_invoice\n"
            "def handle(r):\n"
            "    authenticate(r, r)\n"
            "    return create_invoice(r)\n"
        ),
    })


@pytest.fixture
def single_file_project(project_factory):
    """Tiny project for edge case tests."""
    return project_factory({
        "app.py": "def main(): pass\n",
    })


# ---------------------------------------------------------------------------
# Unit tests: compute_difficulty_score
# ---------------------------------------------------------------------------


class TestComputeDifficultyScore:
    """Tests for the compute_difficulty_score function."""

    def test_empty_partitions(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        result = compute_difficulty_score([])
        assert result == []

    def test_single_partition_gets_max_score(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        partitions = [{
            "complexity": 100,
            "cross_partition_edges": 10,
            "churn": 50,
            "symbol_count": 20,
        }]
        result = compute_difficulty_score(partitions)
        assert len(result) == 1
        # Single partition: all metrics are max, so normalization gives 100 for each
        assert result[0]["difficulty_score"] == 100.0
        assert result[0]["difficulty_label"] == "Critical"

    def test_two_partitions_relative_scoring(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        partitions = [
            {
                "complexity": 100,
                "cross_partition_edges": 20,
                "churn": 50,
                "symbol_count": 30,
            },
            {
                "complexity": 10,
                "cross_partition_edges": 2,
                "churn": 5,
                "symbol_count": 5,
            },
        ]
        result = compute_difficulty_score(partitions)
        # First partition should score higher than second
        assert result[0]["difficulty_score"] > result[1]["difficulty_score"]

    def test_all_zero_partitions(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        partitions = [
            {
                "complexity": 0,
                "cross_partition_edges": 0,
                "churn": 0,
                "symbol_count": 0,
            },
            {
                "complexity": 0,
                "cross_partition_edges": 0,
                "churn": 0,
                "symbol_count": 0,
            },
        ]
        result = compute_difficulty_score(partitions)
        # All zeros: normalized to 0/1 = 0 for each
        assert result[0]["difficulty_score"] == 0.0
        assert result[0]["difficulty_label"] == "Easy"

    def test_score_in_range(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        partitions = [
            {"complexity": 50, "cross_partition_edges": 5, "churn": 20, "symbol_count": 10},
            {"complexity": 100, "cross_partition_edges": 10, "churn": 40, "symbol_count": 20},
            {"complexity": 10, "cross_partition_edges": 1, "churn": 5, "symbol_count": 3},
        ]
        result = compute_difficulty_score(partitions)
        for p in result:
            assert 0 <= p["difficulty_score"] <= 100, (
                f"Score {p['difficulty_score']} out of 0-100 range"
            )

    def test_custom_weights(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        partitions = [
            {"complexity": 100, "cross_partition_edges": 0, "churn": 0, "symbol_count": 0},
        ]
        # 100% complexity weight
        result = compute_difficulty_score(
            partitions,
            complexity_weight=1.0,
            coupling_weight=0.0,
            churn_weight=0.0,
            size_weight=0.0,
        )
        assert result[0]["difficulty_score"] == 100.0

    def test_difficulty_labels(self):
        from roam.commands.cmd_partition import _difficulty_label
        assert _difficulty_label(0) == "Easy"
        assert _difficulty_label(24.9) == "Easy"
        assert _difficulty_label(25) == "Medium"
        assert _difficulty_label(49.9) == "Medium"
        assert _difficulty_label(50) == "Hard"
        assert _difficulty_label(74.9) == "Hard"
        assert _difficulty_label(75) == "Critical"
        assert _difficulty_label(100) == "Critical"

    def test_adds_fields_to_existing_dicts(self):
        from roam.commands.cmd_partition import compute_difficulty_score
        partitions = [
            {
                "id": 1, "label": "test",
                "complexity": 50, "cross_partition_edges": 5,
                "churn": 10, "symbol_count": 15,
            },
        ]
        result = compute_difficulty_score(partitions)
        # Existing keys preserved
        assert result[0]["id"] == 1
        assert result[0]["label"] == "test"
        # New keys added
        assert "difficulty_score" in result[0]
        assert "difficulty_label" in result[0]


# ---------------------------------------------------------------------------
# Integration: difficulty in partition manifest
# ---------------------------------------------------------------------------


class TestDifficultyInManifest:
    """Test that difficulty scoring is integrated into compute_partition_manifest."""

    def test_partitions_have_difficulty_score(self, multi_module_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(multi_module_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    assert "difficulty_score" in p, "Missing difficulty_score"
                    assert "difficulty_label" in p, "Missing difficulty_label"
                    assert 0 <= p["difficulty_score"] <= 100
                    assert p["difficulty_label"] in ("Easy", "Medium", "Hard", "Critical")
        finally:
            os.chdir(old_cwd)

    def test_partitions_have_churn(self, multi_module_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(multi_module_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    assert "churn" in p, "Missing churn"
                    assert p["churn"] >= 0
        finally:
            os.chdir(old_cwd)

    def test_difficulty_in_json_output(self, multi_module_project, cli_runner):
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2"],
            cwd=multi_module_project, json_mode=True,
        )
        data = parse_json_output(result, command="partition")
        for p in data["partitions"]:
            assert "difficulty_score" in p
            assert "difficulty_label" in p
            assert "churn" in p

    def test_difficulty_in_text_output(self, multi_module_project, cli_runner):
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2"],
            cwd=multi_module_project,
        )
        assert result.exit_code == 0
        assert "Difficulty:" in result.output

    def test_single_file_difficulty(self, single_file_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(single_file_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    assert "difficulty_score" in p
                    assert "difficulty_label" in p
        finally:
            os.chdir(old_cwd)
