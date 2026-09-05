"""Cold analysis has parseable output and an explicit, write-free opt-out."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest


@pytest.fixture
def cold_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n", encoding="utf-8"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("ROAM_")}
    env.pop("PYTHONPATH", None)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", ROAM_TELEMETRY_LOCAL="0")
    for args in (
        ["init", "--quiet"],
        ["add", "app.py"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=project, env=env, capture_output=True, check=True)
    return project, env


def _run(cold_project, *args, **overrides):
    project, env = cold_project
    return subprocess.run(
        [sys.executable, "-I", "-m", "roam", *args],
        cwd=project,
        env={**env, **overrides},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("value", ["1", "true", "yes", " ON "])
def test_auto_index_opt_out_truthy_values(monkeypatch, value):
    from roam.commands.resolve import auto_index_disabled

    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", value)
    assert auto_index_disabled()


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no"])
def test_auto_index_opt_out_false_values(monkeypatch, value):
    from roam.commands.resolve import auto_index_disabled

    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", value)
    assert not auto_index_disabled()


def test_cold_json_analysis_keeps_progress_off_stdout(cold_project):
    result = _run(cold_project, "--json", "preflight", "helper")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["command"] == "preflight"
    assert "No roam index found" in result.stderr


@pytest.mark.parametrize("command", [["preflight", "helper"], ["health"], ["dead"]])
@pytest.mark.parametrize("proof_bundle", [False, True])
def test_opt_out_refusal_is_structured_and_does_not_write(cold_project, tmp_path, command, proof_bundle):
    project, _ = cold_project
    store = tmp_path / "external-store"
    if proof_bundle:
        bundle = project / ".roam" / "pr-bundles" / "fixture.json"
        bundle.parent.mkdir(parents=True)
        bundle.write_text("{}", encoding="utf-8")
    before = _snapshot(project)
    result = _run(
        cold_project, "--json", *command, ROAM_NO_AUTO_INDEX="1", ROAM_DB_DIR=str(store), ROAM_RUN_ID="fixture-run"
    )
    assert result.returncode == 3, result.stderr
    data = json.loads(result.stdout)
    assert data["error_code"] == "INDEX_MISSING"
    assert data["summary"]["state"] == "not_initialized"
    assert data["summary"]["partial_success"] is True
    assert "ROAM_NO_AUTO_INDEX" in data["error"]
    assert _snapshot(project) == before
    assert not store.exists()


def test_explicit_index_redirects_database_and_control_sidecars(cold_project, tmp_path):
    project, _ = cold_project
    store = tmp_path / "external-store"
    result = _run(cold_project, "--json", "index", ROAM_NO_AUTO_INDEX="1", ROAM_DB_DIR=str(store))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["command"] == "index"
    assert (store / "index.db").is_file()
    assert (store / "index.state").is_file()
    assert not (project / ".roam").exists()
    before = _snapshot(project)
    warm = _run(cold_project, "--json", "preflight", "helper", ROAM_NO_AUTO_INDEX="1", ROAM_DB_DIR=str(store))
    assert warm.returncode == 0, warm.stderr
    assert json.loads(warm.stdout)["command"] == "preflight"
    assert _snapshot(project) == before


def test_explicit_init_still_builds_with_opt_out(cold_project, tmp_path):
    project, _ = cold_project
    store = tmp_path / "external-store"
    result = _run(cold_project, "--json", "init", ROAM_NO_AUTO_INDEX="1", ROAM_DB_DIR=str(store))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["command"] == "init"
    assert (store / "index.db").is_file()
    assert (store / "index.state").is_file()
    # init intentionally creates configuration; only index storage is redirected.
    assert (project / ".roam").is_dir()


def test_incomplete_redirected_index_is_refused_without_rebuild(cold_project, tmp_path):
    project, _ = cold_project
    store = tmp_path / "external-store"
    result = _run(cold_project, "--json", "index", ROAM_DB_DIR=str(store))
    assert result.returncode == 0, result.stderr
    (store / "index.state").write_text("interrupted-invalid-marker", encoding="utf-8")
    before_project, before_store = _snapshot(project), _snapshot(store)
    result = _run(
        cold_project,
        "--json",
        "preflight",
        "helper",
        ROAM_NO_AUTO_INDEX="1",
        ROAM_DB_DIR=str(store),
        ROAM_RUN_ID="fixture-run",
    )
    assert result.returncode == 3, result.stderr
    data = json.loads(result.stdout)
    assert data["summary"]["state"] == "incomplete"
    assert data["error_code"] == "INDEX_MISSING"
    assert _snapshot(project) == before_project
    assert _snapshot(store) == before_store


def test_recovery_path_probe_never_creates_redirected_directory(cold_project, tmp_path, monkeypatch):
    from roam.commands.resolve import _existing_index_recovery_state
    from roam.db.connection import db_exists, get_db_path

    project, _ = cold_project
    store = tmp_path / "missing-store"
    monkeypatch.chdir(project)
    monkeypatch.setenv("ROAM_DB_DIR", str(store))
    assert get_db_path(create=False) == store / "index.db"
    assert not db_exists()
    assert _existing_index_recovery_state() is None
    assert not store.exists()
    assert not (project / ".roam").exists()


@pytest.mark.parametrize("transport", ["inprocess", "subprocess"])
def test_mcp_bridge_preserves_refusal_without_followup_index_probe(cold_project, tmp_path, monkeypatch, transport):
    from roam import mcp_server as mcp

    project, _ = cold_project
    store = tmp_path / "missing-store"
    monkeypatch.chdir(project)
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")
    monkeypatch.setenv("ROAM_DB_DIR", str(store))
    monkeypatch.setenv("ROAM_RUN_ID", "fixture-run")
    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "0")
    monkeypatch.setattr(mcp, "_ROAM_RESULT_CACHE", {})

    def forbidden_probe():
        pytest.fail("a refused index must not be probed for a stale banner")

    monkeypatch.setattr(mcp, "_check_stale_with_cache", forbidden_probe)
    before = _snapshot(project)
    result = mcp._run_roam(["preflight", "helper"], root="." if transport == "inprocess" else str(project))
    assert result["isError"] is True
    assert result["error_code"] == "INDEX_NOT_FOUND"
    assert result["status"] == "index_not_built"
    assert result["_meta"]["cli_exit_code"] == 3
    assert result["_meta"]["cli_error_code"] == "INDEX_MISSING"
    assert result["summary"]["partial_success"] is True
    assert _snapshot(project) == before
    assert not store.exists()


@pytest.mark.parametrize("compact", [False, True])
def test_refusal_formatter_does_not_probe_or_persist(monkeypatch, compact):
    from roam.output import formatter

    monkeypatch.setattr(formatter, "_compact_mode_enabled", lambda: compact)

    def forbidden(*args, **kwargs):
        pytest.fail("refusal formatting must not read index state or persist a response")

    monkeypatch.setattr(formatter, "_index_age_seconds", forbidden)
    monkeypatch.setattr(formatter, "_envelope_index_status", forbidden)
    monkeypatch.setattr(formatter, "_write_response_to_responses_dir", forbidden)
    result = formatter.json_envelope(
        "health",
        summary={"verdict": "No initialized files", "partial_success": True},
        persist_response=False,
        include_index_metadata=False,
    )
    assert result["summary"]["partial_success"] is True
    if not compact:
        assert result["_meta"]["index_metadata"] == "not_read"
