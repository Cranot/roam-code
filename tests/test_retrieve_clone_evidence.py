"""Reuse persisted clone observations without laundering their qualification."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.graph import clone_evidence
from roam.retrieve.pipeline import RetrieveOptions, run_retrieve
from tests import test_clone_scan_evidence as _fixtures

project = _fixtures.project
run = _fixtures.run


def retrieve(root):
    with open_db(readonly=True) as conn:
        return run_retrieve(conn, "first second", config_root=root, options=RetrieveOptions(k=20))


def test_current_observations_keep_their_boost(project):
    run("clones", "--persist")
    result = retrieve(project)
    assert result["clone_evidence"]["check_status"] == "ran"
    assert result["clone_evidence"]["partial_success"] is False
    tagged = [c["justifications"] for c in result["candidates"] if "clone_cluster" in c["justifications"]]
    assert tagged and all(c["clone_boost_applied"] for c in tagged)


@pytest.mark.parametrize("state", ["source_changed", "index_changed", "incomplete", "invalid", "missing"])
def test_unqualified_observations_remain_visible_but_do_not_boost(project, state):
    run("clones", "--persist")
    if state == "source_changed":
        path = project / "a.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# later edit\n", encoding="utf-8")
    else:
        with open_db() as conn:
            if state == "index_changed":
                conn.execute("UPDATE files SET hash = 'changed' WHERE path = 'a.py'")
            elif state == "missing":
                conn.execute("DELETE FROM clone_scan_state")
            elif state == "invalid":
                conn.execute("UPDATE clone_scan_state SET metadata_json = '{}' WHERE id = 1")
            else:
                saved = json.loads(conn.execute("SELECT metadata_json FROM clone_scan_state").fetchone()[0])
                saved["complete"] = False
                conn.execute("UPDATE clone_scan_state SET metadata_json = ?", (json.dumps(saved),))
    result = retrieve(project)
    assert result["clone_evidence"]["partial_success"] is True
    tagged = [c["justifications"] for c in result["candidates"] if "clone_cluster" in c["justifications"]]
    assert tagged
    assert all(c["clone_boost_applied"] is False and c["clone_check_status"] != "ran" for c in tagged)


@pytest.mark.parametrize("scanned", [False, True])
def test_missing_and_completed_empty_evidence_are_distinct(project, scanned):
    if scanned:
        run("clones", "--persist", "--min-lines", "100")
    evidence = retrieve(project)["clone_evidence"]
    assert evidence["partial_success"] is (not scanned)
    assert (evidence["check_status"] == "ran") is scanned


def test_evidence_is_checked_once_per_query_and_in_one_sql_snapshot(project, monkeypatch):
    run("clones", "--persist")
    original = clone_evidence.clone_check_status
    calls = []

    def checked(conn, **kwargs):
        assert conn.in_transaction
        calls.append(kwargs.get("project_root"))
        return original(conn, **kwargs)

    monkeypatch.setattr(clone_evidence, "clone_check_status", checked)
    retrieve(project)
    assert calls == [project]


def test_disabled_signal_does_not_hash_sources(project, monkeypatch):
    monkeypatch.setattr(clone_evidence, "clone_check_status", lambda *a, **kw: pytest.fail("disabled signal ran"))
    with open_db(readonly=True) as conn:
        result = run_retrieve(
            conn, "first second", config_root=project, options=RetrieveOptions(weights={"epsilon": 0})
        )
    assert result["clone_evidence"] == {"check_status": "skipped:disabled", "partial_success": False}
    assert result["candidates"]


def test_clone_failure_retains_other_retrieval_signals(project, monkeypatch):
    import sqlite3

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("locked clone evidence")

    monkeypatch.setattr(clone_evidence, "clone_check_status", fail)
    result = retrieve(project)
    assert result["candidates"]
    assert result["clone_evidence"]["check_status"] == "errored:clone_evidence:OperationalError"
    assert result["clone_evidence"]["partial_success"] is True


def test_clone_read_preserves_callers_transaction(project):
    from roam.retrieve.rerank import _clone_tags

    with open_db() as conn:
        conn.execute("UPDATE files SET hash = 'caller-owned' WHERE path = 'a.py'")
        _clone_tags(conn, [], project_root=project)
        assert conn.in_transaction
        assert conn.execute("SELECT hash FROM files WHERE path = 'a.py'").fetchone()[0] == "caller-owned"


def test_candidate_selection_and_clone_evidence_share_snapshot(project, monkeypatch):
    from roam.retrieve import pipeline

    original = pipeline._first_stage

    def first_stage(conn, *args, **kwargs):
        assert conn.in_transaction
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_first_stage", first_stage)
    assert retrieve(project)["candidates"]


def test_current_boost_is_numeric_and_stale_boost_is_zero(project):
    from roam.retrieve.rerank import structural_score

    run("clones", "--persist")
    with open_db(readonly=True) as conn:
        candidates = [
            dict(row) | {"fts_score": 1.0}
            for row in conn.execute(
                "SELECT s.id AS symbol_id, s.name, f.path AS file_path FROM symbols s "
                "JOIN files f ON f.id = s.file_id WHERE s.kind = 'function'"
            )
        ]
        baseline = structural_score(conn, candidates, {}, {"epsilon": 0}, config_root=project)
        current = structural_score(conn, candidates, {}, {"epsilon": 0.2}, config_root=project)
        (project / "a.py").write_text("# changed source\n", encoding="utf-8")
        stale = structural_score(conn, candidates, {}, {"epsilon": 0.2}, config_root=project)
    baseline_scores = {c["symbol_id"]: c["score"] for c in baseline}
    assert current and all(c["score"] - baseline_scores[c["symbol_id"]] == pytest.approx(0.2) for c in current)
    assert all(c["score"] == baseline_scores[c["symbol_id"]] for c in stale)


@pytest.mark.parametrize("mode", ["json", "text", "mcp"])
def test_qualification_survives_public_consumption(project, mode):
    run("clones", "--persist", "--scope", "a.py")
    if mode == "mcp":
        from roam.mcp_server import retrieve_context as mcp_retrieve

        data = mcp_retrieve(task="first second", root=str(project))
    else:
        args = ["retrieve", "first second"]
        result = CliRunner().invoke(cli, (["--json"] if mode == "json" else []) + args)
        assert result.exit_code == 0, result.output
        if mode == "text":
            assert "clone_scan_filtered" in result.output
            return
        data = json.loads(result.stdout)
    assert data["summary"]["partial_success"] is True
    assert "clone" in data["summary"]["verdict"]
    assert data["clone_evidence"]["check_status"] == "partial:clone_scan_filtered"
