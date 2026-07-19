"""Regression locks for Verify's fail-closed auto-fire boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli


def _indexed_project(tmp_path):
    project = tmp_path / "verify-autofire"
    project.mkdir()
    (project / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (project / "app.py").write_text("def ready():\n    return True\n", encoding="utf-8")
    git_init(project)
    output, exit_code = index_in_process(project, "--force")
    assert exit_code == 0, output
    return project


def _gate_json(result):
    assert result.exit_code == 5, result.output
    return json.loads(result.stdout)


def test_no_arg_verify_includes_untracked_files(tmp_path):
    project = _indexed_project(tmp_path)
    (project / "untracked.py").write_text("def broken(:\n", encoding="utf-8")

    result = invoke_cli(
        CliRunner(),
        ["verify", "--checks", "syntax"],
        cwd=project,
        json_mode=True,
    )

    envelope = _gate_json(result)
    assert any(item.get("file") == "untracked.py" for item in envelope["violations"])


def test_no_arg_verify_includes_staged_only_files(tmp_path):
    project = _indexed_project(tmp_path)
    (project / "app.py").write_text("def broken(:\n", encoding="utf-8")
    staged = subprocess.run(["git", "add", "app.py"], cwd=project, capture_output=True, text=True)
    assert staged.returncode == 0, staged.stderr

    result = invoke_cli(
        CliRunner(),
        ["verify", "--checks", "syntax"],
        cwd=project,
        json_mode=True,
    )

    envelope = _gate_json(result)
    assert any(item.get("file") == "app.py" for item in envelope["violations"])


def test_changed_file_discovery_failure_cannot_become_no_changes(tmp_path, monkeypatch):
    from roam.commands import cmd_verify

    project = _indexed_project(tmp_path)
    monkeypatch.setattr(
        cmd_verify,
        "_discover_verify_targets",
        lambda _root: {"paths": [], "state": "git_timeout", "partial_success": True},
    )

    result = invoke_cli(CliRunner(), ["verify"], cwd=project, json_mode=True)

    envelope = _gate_json(result)
    assert envelope["summary"]["state"] == "git_timeout"
    assert envelope["summary"]["verification_complete"] is False
    assert envelope["summary"]["partial_success"] is True


def test_index_refresh_failure_is_a_non_suppressible_gate_failure(tmp_path, monkeypatch):
    from roam.commands import cmd_verify

    project = _indexed_project(tmp_path)
    monkeypatch.setattr(
        cmd_verify,
        "_refresh_stale_verify_targets",
        lambda _root, _paths: {
            "state": "refresh_failed",
            "partial_success": True,
            "refreshed_file_count": 0,
        },
    )

    result = invoke_cli(
        CliRunner(),
        ["verify", "--checks", "syntax", "app.py"],
        cwd=project,
        json_mode=True,
    )

    envelope = _gate_json(result)
    assert envelope["summary"]["verification_complete"] is False
    assert "index_refresh_failed" in envelope["summary"]["incomplete_reasons"]
    assert envelope["categories"]["verification"]["violation_count"] >= 1


def test_json_report_remains_non_gating_even_with_fail_findings(tmp_path):
    project = _indexed_project(tmp_path)
    (project / "app.py").write_text("def broken(:\n", encoding="utf-8")

    result = invoke_cli(
        CliRunner(),
        ["verify", "--report", "--checks", "syntax", "app.py"],
        cwd=project,
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["summary"]["verdict"].startswith("FAIL")


def test_pytest_protocol_failure_without_node_ids_is_not_pass(monkeypatch, tmp_path):
    from roam.commands import cmd_verify

    class Result:
        returncode = 4
        stdout = ""
        stderr = "pytest: usage error"

    monkeypatch.setattr(cmd_verify.subprocess, "run", lambda *args, **kwargs: Result())
    result = cmd_verify._run_impacted_pytest(["tests/test_app.py"], tmp_path, timeout=1)

    assert result["score"] == 0
    assert result["execution_state"] == "failed"
    assert result["violations"][0]["hard_block"] is True


def test_capped_impacted_tests_are_disclosed_as_partial(monkeypatch, tmp_path):
    from roam.commands import cmd_verify

    class Result:
        returncode = 0
        stdout = "25 passed"
        stderr = ""

    monkeypatch.setattr(cmd_verify.subprocess, "run", lambda *args, **kwargs: Result())
    impacted = [f"tests/test_{index}.py" for index in range(cmd_verify._MAX_TEST_FILES + 1)]
    result = cmd_verify._run_impacted_pytest(impacted, tmp_path, timeout=1)

    assert result["score"] == 0
    assert result["partial_success"] is True
    assert result["capped"] is True
    assert any(item.get("hard_block") for item in result["violations"])


def test_selected_check_exception_is_a_non_suppressible_gate_failure(tmp_path, monkeypatch):
    from roam.commands import cmd_verify

    project = _indexed_project(tmp_path)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("detector canary")

    monkeypatch.setattr(cmd_verify, "_check_syntax", _raise)
    result = invoke_cli(
        CliRunner(),
        ["verify", "--checks", "syntax", "app.py"],
        cwd=project,
        json_mode=True,
    )

    envelope = _gate_json(result)
    assert envelope["summary"]["verification_complete"] is False
    assert "syntax_incomplete" in envelope["summary"]["incomplete_reasons"]
    assert envelope["categories"]["syntax"]["available"] is False
    assert envelope["categories"]["verification"]["violation_count"] >= 1


def test_selected_check_malformed_result_is_incomplete():
    from roam.commands import cmd_verify

    result = cmd_verify._maybe_run_verify_check(
        ["syntax"],
        "syntax",
        lambda: {"score": 100, "violations": "not-a-list"},
    )

    assert result["score"] == 0
    assert result["available"] is False
    assert result["partial_success"] is True
    assert result["execution_state"] == "failed"


def test_selected_check_scan_cap_is_incomplete():
    from roam.commands import cmd_verify

    result = cmd_verify._maybe_run_verify_check(
        ["calc_divergence"],
        "calc_divergence",
        lambda: {"score": 100, "violations": [], "capped": True, "scan_cap": 5000},
    )

    assert result["capped"] is True
    assert result["partial_success"] is True
    assert result["execution_state"] == "incomplete"


def test_explicit_selection_runs_default_off_detector(monkeypatch):
    from roam.commands import cmd_verify

    monkeypatch.delenv("ROAM_VERIFY_TAINT", raising=False)
    called = []
    result = cmd_verify._maybe_run_verify_check(
        cmd_verify.resolve_selected_checks("taint", False, {}, ["app.py"]),
        "taint",
        lambda: called.append(True) or {"score": 100, "violations": []},
    )

    assert called == [True]
    assert result["score"] == 100


def test_verify_receipt_binds_exact_requested_scope(tmp_path, monkeypatch):
    from roam.commands import cmd_verify

    project = _indexed_project(tmp_path)
    nonce = "0123456789abcdef0123456789abcdef"
    scope_digest = cmd_verify._verification_scope_sha256(["app.py"])
    content_digest, content_error = cmd_verify._verification_content_sha256(project, ["app.py"])
    assert content_error is None
    monkeypatch.setenv("ROAM_VERIFY_REQUEST_NONCE", nonce)
    monkeypatch.setenv("ROAM_VERIFY_SCOPE_SHA256", scope_digest)
    monkeypatch.setenv("ROAM_VERIFY_CONTENT_SHA256", content_digest)
    monkeypatch.setenv("ROAM_VERIFY_SCOPE_COUNT", "1")

    result = invoke_cli(
        CliRunner(),
        ["verify", "--checks", "syntax", "app.py"],
        cwd=project,
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    receipt = envelope["summary"]["verification_receipt"]
    assert receipt == {
        "schema": "roam.verify.receipt.v3",
        "request_nonce": nonce,
        "scope_sha256": scope_digest,
        "content_sha256": content_digest,
        "content_sha256_before": content_digest,
        "content_sha256_after": content_digest,
        "target_file_count": 1,
        "scope_stable": True,
        "request_match": True,
    }
    assert envelope["summary"]["verification_complete"] is True
    assert envelope["summary"]["partial_success"] is False


def test_verify_scope_binding_mismatch_fails_closed(tmp_path, monkeypatch):
    from roam.commands import cmd_verify

    project = _indexed_project(tmp_path)
    content_digest, content_error = cmd_verify._verification_content_sha256(project, ["app.py"])
    assert content_error is None
    monkeypatch.setenv("ROAM_VERIFY_REQUEST_NONCE", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("ROAM_VERIFY_SCOPE_SHA256", "f" * 64)
    monkeypatch.setenv("ROAM_VERIFY_CONTENT_SHA256", content_digest)
    monkeypatch.setenv("ROAM_VERIFY_SCOPE_COUNT", "1")
    syntax_called = []
    monkeypatch.setattr(
        cmd_verify,
        "_check_syntax",
        lambda *_args, **_kwargs: syntax_called.append(True) or {"score": 100, "violations": []},
    )

    result = invoke_cli(
        CliRunner(),
        ["verify", "--checks", "syntax", "app.py"],
        cwd=project,
        json_mode=True,
    )

    envelope = _gate_json(result)
    assert envelope["summary"]["verification_complete"] is False
    assert envelope["summary"]["partial_success"] is True
    assert "request_binding_failed" in envelope["summary"]["incomplete_reasons"]
    assert envelope["summary"]["verification_receipt"]["request_match"] is False
    assert syntax_called == []


def test_verify_receipt_detects_mutate_then_restore_by_file_identity(tmp_path, monkeypatch):
    from roam.commands import cmd_verify

    project = _indexed_project(tmp_path)
    target = project / "app.py"
    original = target.read_bytes()
    nonce = "0123456789abcdef0123456789abcdef"
    scope_digest = cmd_verify._verification_scope_sha256(["app.py"])
    content_digest, content_error = cmd_verify._verification_content_sha256(project, ["app.py"])
    assert content_error is None
    monkeypatch.setenv("ROAM_VERIFY_REQUEST_NONCE", nonce)
    monkeypatch.setenv("ROAM_VERIFY_SCOPE_SHA256", scope_digest)
    monkeypatch.setenv("ROAM_VERIFY_CONTENT_SHA256", content_digest)
    monkeypatch.setenv("ROAM_VERIFY_SCOPE_COUNT", "1")

    _pre_receipt, pre_error, initial = cmd_verify._verification_request_receipt(project, ["app.py"])
    assert pre_error is None
    replacement = project / "replacement.py"
    replacement.write_bytes(original)
    os.replace(replacement, target)
    receipt, post_error, _after = cmd_verify._verification_request_receipt(
        project,
        ["app.py"],
        initial_evidence=initial,
    )

    assert post_error == "verification_scope_mutated"
    assert receipt["content_sha256_before"] == content_digest
    assert receipt["content_sha256_after"] == content_digest
    assert receipt["scope_stable"] is False
    assert receipt["request_match"] is False
