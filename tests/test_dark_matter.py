"""Tests for dark-matter command: co-changing files with no structural dependency."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import invoke_cli, parse_json_output, assert_json_envelope
from roam.graph.dark_matter import dark_matter_edges, HypothesisEngine


# ---------------------------------------------------------------------------
# Fixture: project with dark-matter pairs
# ---------------------------------------------------------------------------

@pytest.fixture
def dark_matter_project(project_factory, monkeypatch):
    """Project where billing.py <-> reporting.py co-change 3+ times with no
    import edge, while models.py imports billing.py (structural edge)."""
    billing_v1 = (
        "def get_invoice(id):\n"
        "    return {'id': id, 'amount': 100}\n"
    )
    reporting_v1 = (
        "def monthly_report():\n"
        "    return {'total': 500}\n"
    )
    models_v1 = (
        "from billing import get_invoice\n"
        "\n"
        "def load_model():\n"
        "    inv = get_invoice(1)\n"
        "    return inv\n"
    )

    billing_v2 = (
        "def get_invoice(id):\n"
        "    return {'id': id, 'amount': 200, 'tax': 20}\n"
    )
    reporting_v2 = (
        "def monthly_report():\n"
        "    return {'total': 600, 'tax_total': 60}\n"
    )

    billing_v3 = (
        "def get_invoice(id):\n"
        "    return {'id': id, 'amount': 300, 'tax': 30, 'discount': 10}\n"
    )
    reporting_v3 = (
        "def monthly_report():\n"
        "    return {'total': 700, 'tax_total': 70, 'discounts': 10}\n"
    )

    proj = project_factory(
        {
            "billing.py": billing_v1,
            "reporting.py": reporting_v1,
            "models.py": models_v1,
        },
        extra_commits=[
            ({"billing.py": billing_v2, "reporting.py": reporting_v2}, "add tax"),
            ({"billing.py": billing_v3, "reporting.py": reporting_v3}, "add discount"),
        ],
    )
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def empty_project(project_factory, monkeypatch):
    """Project with no co-change data."""
    proj = project_factory({"app.py": "def main(): pass\n"})
    monkeypatch.chdir(proj)
    return proj


# ===========================================================================
# Integration tests
# ===========================================================================

class TestDarkMatterCommand:

    def test_dark_matter_runs(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter"], cwd=dark_matter_project)
        assert result.exit_code == 0

    def test_dark_matter_json_envelope(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter"], cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        assert_json_envelope(data, "dark-matter")

    def test_dark_matter_finds_hidden_pair(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
                            cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        pairs = data.get("dark_matter_pairs", [])
        paths = [(p["file_a"], p["file_b"]) for p in pairs]
        # billing <-> reporting should appear (no import edge, co-change 3+ times)
        found = any(
            ("billing.py" in a and "reporting.py" in b) or
            ("reporting.py" in a and "billing.py" in b)
            for a, b in paths
        )
        assert found, f"Expected billing<->reporting in {paths}"

    def test_dark_matter_excludes_structural_pair(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "1"],
                            cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        pairs = data.get("dark_matter_pairs", [])
        # billing <-> models should NOT appear (models imports billing)
        found = any(
            ("billing.py" in p["file_a"] and "models.py" in p["file_b"]) or
            ("models.py" in p["file_a"] and "billing.py" in p["file_b"])
            for p in pairs
        )
        assert not found, f"billing<->models should be excluded (structural edge)"

    def test_dark_matter_json_summary(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
                            cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        assert "verdict" in summary
        assert "total_dark_matter_edges" in summary
        assert "by_category" in summary

    def test_dark_matter_explain_flag(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--explain", "--min-npmi", "0.0", "--min-cochanges", "2"],
                            cwd=dark_matter_project)
        assert result.exit_code == 0
        assert "Hypothesis:" in result.output

    def test_dark_matter_min_npmi_filter(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--min-npmi", "0.99"],
                            cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        # Very high threshold should return 0 or very few pairs
        assert data["summary"]["total_dark_matter_edges"] <= 1

    def test_dark_matter_min_cochanges_filter(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--min-cochanges", "100"],
                            cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        assert data["summary"]["total_dark_matter_edges"] == 0

    def test_dark_matter_category_flag(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--category", "--min-npmi", "0.0", "--min-cochanges", "2"],
                            cwd=dark_matter_project)
        assert result.exit_code == 0
        # Category mode should show bracketed category headers
        assert "[" in result.output

    def test_dark_matter_empty_project(self, empty_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter"], cwd=empty_project)
        assert result.exit_code == 0
        assert "0 dark-matter" in result.output

    def test_dark_matter_verdict_line(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
                            cwd=dark_matter_project)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_dark_matter_limit_flag(self, dark_matter_project, cli_runner):
        result = invoke_cli(cli_runner, ["dark-matter", "-n", "1", "--min-npmi", "0.0", "--min-cochanges", "2"],
                            cwd=dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        assert len(data.get("dark_matter_pairs", [])) <= 1


# ===========================================================================
# Unit tests: dark_matter_edges function
# ===========================================================================

class TestDarkMatterEdgesFunction:

    def test_dark_matter_edges_function(self, dark_matter_project):
        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            edges = dark_matter_edges(conn, min_cochanges=2, min_npmi=0.0)
        assert isinstance(edges, list)
        if edges:
            e = edges[0]
            assert "file_id_a" in e
            assert "file_id_b" in e
            assert "path_a" in e
            assert "path_b" in e
            assert "npmi" in e
            assert "lift" in e
            assert "strength" in e
            assert "cochange_count" in e


# ===========================================================================
# Unit tests: HypothesisEngine
# ===========================================================================

class TestHypothesisEngine:

    def test_hypothesis_shared_db(self, tmp_path):
        (tmp_path / "a.py").write_text(
            "rows = conn.execute('SELECT * FROM invoices WHERE id = ?')\n"
        )
        (tmp_path / "b.py").write_text(
            "conn.execute('INSERT INTO invoices (id, amount) VALUES (?, ?)')\n"
        )
        engine = HypothesisEngine(tmp_path)
        result = engine.hypothesize("a.py", "b.py")
        assert result["category"] == "SHARED_DB"
        assert "invoices" in result["detail"]

    def test_hypothesis_shared_config(self, tmp_path):
        (tmp_path / "a.py").write_text(
            'db_url = os.environ["DATABASE_URL"]\n'
        )
        (tmp_path / "b.py").write_text(
            'url = os.environ["DATABASE_URL"]\n'
        )
        engine = HypothesisEngine(tmp_path)
        result = engine.hypothesize("a.py", "b.py")
        assert result["category"] == "SHARED_CONFIG"
        assert "DATABASE_URL" in result["detail"]

    def test_hypothesis_event_bus(self, tmp_path):
        (tmp_path / "a.py").write_text(
            'bus.emit("order_created", data)\n'
        )
        (tmp_path / "b.py").write_text(
            'bus.on("order_created", handle_order)\n'
        )
        engine = HypothesisEngine(tmp_path)
        result = engine.hypothesize("a.py", "b.py")
        assert result["category"] == "EVENT_BUS"
        assert "order_created" in result["detail"]

    def test_hypothesis_text_similarity(self, tmp_path):
        content = "def process_data(x):\n    return x * 2\n" * 10
        (tmp_path / "a.py").write_text(content)
        (tmp_path / "b.py").write_text(content)
        engine = HypothesisEngine(tmp_path)
        result = engine.hypothesize("a.py", "b.py")
        assert result["category"] == "TEXT_SIMILARITY"

    def test_hypothesis_unknown(self, tmp_path):
        (tmp_path / "a.py").write_text(
            "import logging\n\ndef setup_logging(level):\n"
            "    logger = logging.getLogger(__name__)\n"
            "    logger.setLevel(level)\n"
            "    return logger\n"
        )
        (tmp_path / "b.py").write_text(
            "from dataclasses import dataclass\n\n@dataclass\n"
            "class Point:\n    x: float\n    y: float\n"
            "    def distance(self): return (self.x**2 + self.y**2)**0.5\n"
        )
        engine = HypothesisEngine(tmp_path)
        result = engine.hypothesize("a.py", "b.py")
        assert result["category"] == "UNKNOWN"

    def test_hypothesis_shared_api(self, tmp_path):
        (tmp_path / "a.py").write_text(
            'resp = requests.get("/api/users/list")\n'
        )
        (tmp_path / "b.py").write_text(
            'app.route("/api/users/list")\n'
        )
        engine = HypothesisEngine(tmp_path)
        result = engine.hypothesize("a.py", "b.py")
        assert result["category"] == "SHARED_API"
        assert "/api/users/list" in result["detail"]
