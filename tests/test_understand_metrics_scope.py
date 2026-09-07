"""Orientation must not pay for spectral history it does not expose."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.commands.metrics_history import append_snapshot, collect_metrics
from roam.db.connection import open_db
from roam.graph import spectral_forecast
from tests.conftest import git_init, index_in_process, invoke_cli


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "orientation"
    root.mkdir()
    (root / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (root / "main.py").write_text("from helper import answer\n\ndef main():\n    return answer()\n", encoding="utf-8")
    (root / "helper.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    git_init(root)
    monkeypatch.chdir(root)
    output, code = index_in_process(root)
    assert code == 0, output
    return root


@pytest.fixture
def spectral_calls(project, monkeypatch):
    calls = []

    def measure(conn):
        calls.append(conn)
        return 0.125

    monkeypatch.setattr(spectral_forecast, "compute_current_spectral_gap", measure)
    return calls


def test_opt_out_preserves_every_other_metric(project, spectral_calls):
    with open_db(readonly=True) as conn:
        complete = collect_metrics(conn)
        orientation = collect_metrics(conn, include_spectral=False)
    assert len(spectral_calls) == 1
    assert complete["spectral_gap"] == 0.125
    assert orientation == {**complete, "spectral_gap": None}


def test_default_collection_keeps_spectral_measurement(project, spectral_calls):
    with open_db(readonly=True) as conn:
        metrics = collect_metrics(conn)
    assert len(spectral_calls) == 1
    assert metrics["spectral_gap"] == 0.125


def test_snapshot_writer_still_persists_measured_spectral_gap(project, spectral_calls):
    with open_db() as conn:
        append_snapshot(conn, tag="spectral-control")
        row = conn.execute("SELECT spectral_gap FROM snapshots WHERE tag='spectral-control'").fetchone()
    assert len(spectral_calls) == 1
    assert row is not None and row[0] == 0.125


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("full", [False, True])
def test_real_understand_skips_unused_spectral_work(project, spectral_calls, json_mode, full):
    with open_db(readonly=True) as conn:
        expected = collect_metrics(conn)
    spectral_calls.clear()
    args = ["understand", "--full"] if full else ["understand"]
    result = invoke_cli(CliRunner(), args, cwd=project, json_mode=json_mode)
    assert result.exit_code == 0, result.output
    assert spectral_calls == [], "understand computed spectral history that its answer never uses"
    if json_mode:
        payload = json.loads(result.output)
        assert payload["summary"]["health_score"] == expected["health_score"]
        assert "spectral_gap" not in payload["health_summary"]
        for name in ("cycles", "god_components", "bottlenecks", "dead_exports", "layer_violations"):
            assert payload["health_summary"][name] == expected[name]
    else:
        assert "VERDICT:" in result.output
