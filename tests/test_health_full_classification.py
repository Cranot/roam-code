"""Complete classification must not silently redefine the historical score."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager

import pytest

from roam.commands import cmd_health
from tests import test_w1448_health_list_cap_disclosure as cap_fixture
from tests.conftest import invoke_cli


@pytest.mark.parametrize("detail", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_all_classifies_population_preserves_score(cli_runner, indexed_project, monkeypatch, detail, failed):
    # Fixture-local SQLite and real CLI serialization intentionally exercise
    # the producer. Injected failure has no network, clock or external state.
    god_pop, bn_pop = cap_fixture._seed_graph_metrics(indexed_project)
    legacy = invoke_cli(cli_runner, ["--json", "--budget", "0", "health"], cwd=indexed_project)
    legacy_summary = json.loads(legacy.stdout)["summary"]
    if failed:

        def fail(*args, **kwargs):
            raise RuntimeError("controlled classification dependency failure")

        monkeypatch.setattr(cmd_health, "detect_layers", fail)
    args = ["--json", "--budget", "0", *(["--detail"] if detail else []), "health", "--all-issues"]
    result = invoke_cli(cli_runner, args, cwd=indexed_project)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    summary = data["summary"]
    assert summary["issue_collection_scope"] == "all_qualifying_indexed_symbols"
    assert summary["health_score_scope"] == "legacy_top_candidates"
    assert summary["health_score"] == legacy_summary["health_score"]
    assert summary["god_components"] == god_pop
    assert sum(summary["category_severity"]["god_components"].values()) == god_pop
    assert sum(summary["category_severity"]["bottlenecks"].values()) == bn_pop
    assert summary["partial_success"] is failed
    if detail:
        assert len(data["god_components"]) == god_pop
        assert len(data["bottlenecks"]) == bn_pop
    else:
        assert data["list_counts"]["god_components"] == god_pop
    assert data["god_component_thresholds"]["list_limit"] is None
    assert data["bottleneck_thresholds"]["list_limit"] is None


def test_all_issues_refuses_incomparable_baseline(cli_runner, indexed_project):
    result = invoke_cli(cli_runner, ["--json", "health", "--all-issues", "--baseline", "last"], cwd=indexed_project)
    assert result.exit_code == 2, result.output
    assert "baseline" in result.output


@pytest.mark.parametrize("field", ["gm.in_degree", "gm.betweenness"])
def test_full_collection_query_failure_is_partial(cli_runner, indexed_project, monkeypatch, field):
    cap_fixture._seed_graph_metrics(indexed_project)
    original = cmd_health.open_db

    class FaultingConnection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, key):
            return getattr(self.conn, key)

        def execute(self, query, *args, **kwargs):
            if "SELECT s.name, s.kind, f.path AS file_path" in query and field in query:
                raise RuntimeError("controlled full collection failure")
            return self.conn.execute(query, *args, **kwargs)

    @contextmanager
    def faulting_db(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield FaultingConnection(conn)

    monkeypatch.setattr(cmd_health, "open_db", faulting_db)
    result = invoke_cli(cli_runner, ["--json", "--budget", "0", "health", "--all-issues"], cwd=indexed_project)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["partial_success"] is True
    assert "controlled full collection failure" in " ".join(summary["warnings_out"])


def test_full_collection_budget_loss_stays_partial(cli_runner, indexed_project):
    cap_fixture._seed_graph_metrics(indexed_project)
    result = invoke_cli(
        cli_runner, ["--json", "--budget", "1", "--detail", "health", "--all-issues"], cwd=indexed_project
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["partial_success"] is True
    assert summary["truncation_reason"] == "budget"


def test_full_framework_filter_preserves_score_and_population(cli_runner, indexed_project):
    god_pop, _ = cap_fixture._seed_graph_metrics(indexed_project)
    framework_name = sorted(cmd_health._FRAMEWORK_NAMES)[0]
    with closing(sqlite3.connect(indexed_project / ".roam" / "index.db")) as conn:
        conn.execute("UPDATE symbols SET name=? WHERE name='w1448_god_0'", (framework_name,))
        conn.commit()
    flags = ["--json", "--budget", "0", "health", "--no-framework"]
    legacy = invoke_cli(cli_runner, flags, cwd=indexed_project)
    full = invoke_cli(cli_runner, [*flags, "--all-issues"], cwd=indexed_project)
    assert legacy.exit_code == full.exit_code == 0
    legacy_data, full_data = json.loads(legacy.stdout), json.loads(full.stdout)
    assert full_data["summary"]["health_score"] == legacy_data["summary"]["health_score"]
    assert full_data["summary"]["god_components"] == god_pop - 1
    assert full_data["framework_filtered"] == 1
    assert full_data["summary"]["partial_success"] is False


@pytest.mark.parametrize("failed", [False, True])
def test_full_sarif_census_and_failure_disclosure(cli_runner, indexed_project, monkeypatch, failed):
    god_pop, bn_pop = cap_fixture._seed_graph_metrics(indexed_project)
    if failed:

        def fail(*args, **kwargs):
            raise RuntimeError("controlled SARIF dependency failure")

        monkeypatch.setattr(cmd_health, "detect_layers", fail)
    result = invoke_cli(cli_runner, ["--sarif", "health", "--all-issues"], cwd=indexed_project)
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    run = document["runs"][0]
    assert sum(row["ruleId"] == "health/god-component" for row in run["results"]) == god_pop
    assert sum(row["ruleId"] == "health/bottleneck" for row in run["results"]) == bn_pop
    notifications = [
        note["message"]["text"]
        for invocation in run.get("invocations", [])
        for note in invocation.get("toolExecutionNotifications", [])
    ]
    assert any("controlled SARIF dependency failure" in note for note in notifications) is failed


def test_full_gate_keeps_score_and_classification_scope(cli_runner, indexed_project):
    cap_fixture._seed_graph_metrics(indexed_project)
    flags = ["--json", "--budget", "0", "health", "--gate"]
    legacy = invoke_cli(cli_runner, flags, cwd=indexed_project)
    full = invoke_cli(cli_runner, [*flags, "--all-issues"], cwd=indexed_project)
    assert legacy.exit_code == full.exit_code
    legacy_data, full_data = json.loads(legacy.stdout), json.loads(full.stdout)
    assert full_data["summary"]["health_score"] == legacy_data["summary"]["health_score"]
    assert full_data["summary"]["issue_collection_scope"] == "all_qualifying_indexed_symbols"
    assert full_data["summary"]["health_score_scope"] == "legacy_top_candidates"
    assert full_data["summary"]["gate_passed"] == legacy_data["summary"]["gate_passed"]


@pytest.mark.parametrize("full", [False, True])
def test_persistence_emits_requested_classification_scope(cli_runner, indexed_project, full):
    god_pop, bn_pop = cap_fixture._seed_graph_metrics(indexed_project)
    args = ["--json", "--budget", "0", "health", "--persist", *(["--all-issues"] if full else [])]
    result = invoke_cli(cli_runner, args, cwd=indexed_project)
    assert result.exit_code == 0, result.output
    # Real fixture-local registry write/read, not a mocked emit helper.
    with closing(sqlite3.connect(indexed_project / ".roam" / "index.db")) as conn:
        rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector='health'").fetchall()
    kinds = [json.loads(row[0])["kind"] for row in rows]
    assert kinds.count("arch.god_component") == (god_pop if full else 50)
    assert kinds.count("arch.bottleneck") == (bn_pop if full else 15)
