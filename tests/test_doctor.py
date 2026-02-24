"""Tests for `roam doctor` — setup diagnostics command."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from roam.cli import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def invoke_doctor(*args):
    """Invoke `roam doctor` via CliRunner and return the result."""
    runner = CliRunner()
    return runner.invoke(cli, list(args) + ["doctor"], catch_exceptions=False)


def invoke_doctor_json(*extra_args):
    """Invoke `roam --json doctor` and return parsed JSON."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json"] + list(extra_args) + ["doctor"], catch_exceptions=False)
    return result, json.loads(result.output)


# ---------------------------------------------------------------------------
# Basic smoke tests
# ---------------------------------------------------------------------------


class TestDoctorSmoke:
    def test_doctor_runs(self):
        result = invoke_doctor()
        assert "VERDICT:" in result.output

    def test_doctor_shows_pass_fail(self):
        result = invoke_doctor()
        assert "[PASS]" in result.output or "[FAIL]" in result.output

    def test_doctor_json_runs(self):
        result, data = invoke_doctor_json()
        assert "command" in data
        assert data["command"] == "doctor"

    def test_doctor_json_has_summary(self):
        result, data = invoke_doctor_json()
        assert "summary" in data
        summary = data["summary"]
        assert "verdict" in summary
        assert "total" in summary
        assert "passed" in summary
        assert "failed" in summary
        assert "all_passed" in summary

    def test_doctor_json_has_checks(self):
        result, data = invoke_doctor_json()
        assert "checks" in data
        checks = data["checks"]
        assert isinstance(checks, list)
        assert len(checks) > 0

    def test_doctor_json_checks_structure(self):
        result, data = invoke_doctor_json()
        for check in data["checks"]:
            assert "name" in check
            assert "passed" in check
            assert "detail" in check
            assert isinstance(check["passed"], bool)
            assert isinstance(check["name"], str)
            assert isinstance(check["detail"], str)

    def test_doctor_json_has_failed_checks(self):
        result, data = invoke_doctor_json()
        assert "failed_checks" in data
        assert isinstance(data["failed_checks"], list)


# ---------------------------------------------------------------------------
# Check coverage — all expected checks are present
# ---------------------------------------------------------------------------


class TestDoctorCheckCoverage:
    _EXPECTED_CHECK_NAMES = {
        "Python version",
        "tree-sitter",
        "tree-sitter-language-pack",
        "git executable",
        "networkx",
        "Index exists",
        "Index freshness",
        "SQLite operational",
    }

    def test_all_expected_checks_present(self):
        result, data = invoke_doctor_json()
        names = {c["name"] for c in data["checks"]}
        for expected in self._EXPECTED_CHECK_NAMES:
            assert expected in names, f"Expected check '{expected}' not found. Got: {names}"

    def test_check_count(self):
        result, data = invoke_doctor_json()
        assert data["summary"]["total"] == 8

    def test_passed_plus_failed_equals_total(self):
        result, data = invoke_doctor_json()
        summary = data["summary"]
        assert summary["passed"] + summary["failed"] == summary["total"]


# ---------------------------------------------------------------------------
# Python version check
# ---------------------------------------------------------------------------


class TestDoctorPythonCheck:
    def test_python_check_passes_current_version(self):
        """Current Python must be >= 3.9 to run roam-code at all."""
        result, data = invoke_doctor_json()
        py_check = next(c for c in data["checks"] if c["name"] == "Python version")
        assert py_check["passed"] is True

    def test_python_check_detail_contains_version(self):
        result, data = invoke_doctor_json()
        py_check = next(c for c in data["checks"] if c["name"] == "Python version")
        version_str = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert version_str in py_check["detail"]

    def test_python_check_fails_old_version(self):
        from roam.commands.cmd_doctor import _check_python_version
        fake_vi = type("VI", (), {"major": 3, "minor": 8, "micro": 10})()
        with patch.object(sys, "version_info", fake_vi):
            check = _check_python_version()
        assert check["passed"] is False

    def test_python_check_passes_39(self):
        from roam.commands.cmd_doctor import _check_python_version
        fake_vi = type("VI", (), {"major": 3, "minor": 9, "micro": 0})()
        with patch.object(sys, "version_info", fake_vi):
            check = _check_python_version()
        assert check["passed"] is True

    def test_python_check_passes_310(self):
        from roam.commands.cmd_doctor import _check_python_version
        fake_vi = type("VI", (), {"major": 3, "minor": 10, "micro": 5})()
        with patch.object(sys, "version_info", fake_vi):
            check = _check_python_version()
        assert check["passed"] is True


# ---------------------------------------------------------------------------
# tree-sitter checks
# ---------------------------------------------------------------------------


class TestDoctorTreeSitterChecks:
    def test_tree_sitter_check_passes(self):
        """tree-sitter must be installed for roam-code to work."""
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "tree-sitter")
        assert check["passed"] is True

    def test_tree_sitter_check_detail_contains_version(self):
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "tree-sitter")
        assert "tree-sitter" in check["detail"]

    def test_tree_sitter_check_fails_on_import_error(self):
        from roam.commands.cmd_doctor import _check_tree_sitter
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tree_sitter":
                raise ImportError("mocked missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            check = _check_tree_sitter()
        assert check["passed"] is False
        assert "not installed" in check["detail"]

    def test_tree_sitter_language_pack_passes(self):
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "tree-sitter-language-pack")
        assert check["passed"] is True

    def test_tree_sitter_language_pack_fails_on_import_error(self):
        from roam.commands.cmd_doctor import _check_tree_sitter_language_pack
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tree_sitter_language_pack":
                raise ImportError("mocked missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            check = _check_tree_sitter_language_pack()
        assert check["passed"] is False


# ---------------------------------------------------------------------------
# git check
# ---------------------------------------------------------------------------


class TestDoctorGitCheck:
    def test_git_check_passes(self):
        """git must be on PATH in a standard development environment."""
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "git executable")
        # Only assert if git is present (CI environments may have it)
        if shutil.which("git"):
            assert check["passed"] is True

    def test_git_check_passes_detail_contains_git(self):
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "git executable")
        if check["passed"]:
            assert "git" in check["detail"]

    def test_git_check_fails_when_not_on_path(self):
        from roam.commands.cmd_doctor import _check_git
        with patch("shutil.which", return_value=None):
            check = _check_git()
        assert check["passed"] is False
        assert "not found" in check["detail"]

    def test_git_check_passes_when_on_path(self):
        from roam.commands.cmd_doctor import _check_git
        with patch("shutil.which", return_value="/usr/bin/git"):
            import subprocess
            fake_result = type("R", (), {"returncode": 0, "stdout": "git version 2.43.0\n"})()
            with patch("subprocess.run", return_value=fake_result):
                check = _check_git()
        assert check["passed"] is True
        assert "2.43.0" in check["detail"]


# ---------------------------------------------------------------------------
# networkx check
# ---------------------------------------------------------------------------


class TestDoctorNetworkxCheck:
    def test_networkx_check_passes(self):
        """networkx must be installed for roam-code."""
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "networkx")
        assert check["passed"] is True

    def test_networkx_check_detail_contains_version(self):
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "networkx")
        assert "networkx" in check["detail"]

    def test_networkx_check_fails_on_import_error(self):
        from roam.commands.cmd_doctor import _check_networkx
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "networkx":
                raise ImportError("mocked missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            check = _check_networkx()
        assert check["passed"] is False


# ---------------------------------------------------------------------------
# Index existence check
# ---------------------------------------------------------------------------


class TestDoctorIndexExistsCheck:
    def test_index_check_has_name(self):
        result, data = invoke_doctor_json()
        check = next(c for c in data["checks"] if c["name"] == "Index exists")
        assert check is not None

    def test_index_check_passes_when_db_exists(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_exists
        db_path = tmp_path / ".roam" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()

        with patch("roam.commands.cmd_doctor.Path") as mock_path_cls:
            # Patch get_db_path instead
            pass

        with patch("roam.db.connection.get_db_path", return_value=db_path):
            check = _check_index_exists()
        assert check["passed"] is True
        assert "_db_path" in check

    def test_index_check_fails_when_db_missing(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_exists
        missing_path = tmp_path / ".roam" / "index.db"
        # Ensure it doesn't exist
        assert not missing_path.exists()

        with patch("roam.db.connection.get_db_path", return_value=missing_path):
            check = _check_index_exists()
        assert check["passed"] is False
        assert "roam init" in check["detail"]

    def test_index_check_includes_path_on_pass(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_exists
        db_path = tmp_path / ".roam" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()

        with patch("roam.db.connection.get_db_path", return_value=db_path):
            check = _check_index_exists()
        if check["passed"]:
            assert check["_db_path"] is not None


# ---------------------------------------------------------------------------
# Index freshness check
# ---------------------------------------------------------------------------


class TestDoctorIndexFreshnessCheck:
    def test_freshness_check_none_db_path(self):
        from roam.commands.cmd_doctor import _check_index_freshness
        check = _check_index_freshness(None)
        assert check["passed"] is False
        assert "roam init" in check["detail"]

    def test_freshness_check_fresh_db(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        db_path = tmp_path / "index.db"
        db_path.touch()
        # mtime is now — should be fresh
        check = _check_index_freshness(str(db_path))
        assert check["passed"] is True
        assert "fresh" in check["detail"]

    def test_freshness_check_stale_db(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        db_path = tmp_path / "index.db"
        db_path.touch()
        # Set mtime to 25 hours ago
        old_time = time.time() - (25 * 3600)
        os.utime(str(db_path), (old_time, old_time))
        check = _check_index_freshness(str(db_path))
        assert check["passed"] is False
        assert "stale" in check["detail"]
        assert "roam index" in check["detail"]

    def test_freshness_check_24h_boundary_fresh(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        db_path = tmp_path / "index.db"
        db_path.touch()
        # 23 hours old — still fresh
        recent_time = time.time() - (23 * 3600)
        os.utime(str(db_path), (recent_time, recent_time))
        check = _check_index_freshness(str(db_path))
        assert check["passed"] is True

    def test_freshness_check_includes_age(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        db_path = tmp_path / "index.db"
        db_path.touch()
        check = _check_index_freshness(str(db_path))
        assert "_age_s" in check
        assert isinstance(check["_age_s"], float)

    def test_freshness_check_missing_file(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        missing = str(tmp_path / "nonexistent.db")
        check = _check_index_freshness(missing)
        assert check["passed"] is False

    def test_freshness_age_display_seconds(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        db_path = tmp_path / "index.db"
        db_path.touch()
        # Very fresh — seconds ago
        check = _check_index_freshness(str(db_path))
        assert "second" in check["detail"] or "minute" in check["detail"]

    def test_freshness_age_display_days(self, tmp_path):
        from roam.commands.cmd_doctor import _check_index_freshness
        db_path = tmp_path / "index.db"
        db_path.touch()
        two_days_ago = time.time() - (49 * 3600)
        os.utime(str(db_path), (two_days_ago, two_days_ago))
        check = _check_index_freshness(str(db_path))
        assert "day" in check["detail"]


# ---------------------------------------------------------------------------
# SQLite check
# ---------------------------------------------------------------------------


class TestDoctorSQLiteCheck:
    def test_sqlite_check_none_db_path(self):
        from roam.commands.cmd_doctor import _check_sqlite
        check = _check_sqlite(None)
        assert check["passed"] is False

    def test_sqlite_check_missing_file(self, tmp_path):
        from roam.commands.cmd_doctor import _check_sqlite
        missing = str(tmp_path / "nonexistent.db")
        check = _check_sqlite(missing)
        assert check["passed"] is False

    def test_sqlite_check_valid_db(self, tmp_path):
        from roam.commands.cmd_doctor import _check_sqlite
        db_path = tmp_path / "test.db"
        # Create a valid SQLite database
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.close()
        check = _check_sqlite(str(db_path))
        assert check["passed"] is True
        assert "operational" in check["detail"].lower()

    def test_sqlite_check_corrupted_db(self, tmp_path):
        from roam.commands.cmd_doctor import _check_sqlite
        db_path = tmp_path / "corrupt.db"
        # Write garbage bytes to simulate a corrupted DB
        db_path.write_bytes(b"this is not a valid sqlite database!!!")
        check = _check_sqlite(str(db_path))
        assert check["passed"] is False


# ---------------------------------------------------------------------------
# Exit code tests
# ---------------------------------------------------------------------------


class TestDoctorExitCodes:
    def test_exit_0_when_all_pass(self):
        """When all checks pass, exit code must be 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        # This may fail if environment is missing something; we only test
        # that exit code 0 occurs when no failures are reported
        if "[FAIL]" not in result.output:
            assert result.exit_code == 0

    def test_exit_1_when_check_fails(self, tmp_path):
        """When a check fails, exit code must be 1."""
        from roam.commands.cmd_doctor import doctor
        runner = CliRunner()
        # Force a failure by making git unavailable
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        assert result.exit_code == 1

    def test_json_exit_1_when_check_fails(self):
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        assert result.exit_code == 1

    def test_json_exit_0_when_all_pass(self):
        """JSON mode still exits 0 when all checks pass."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["summary"]["all_passed"] is True


# ---------------------------------------------------------------------------
# Verdict formatting tests
# ---------------------------------------------------------------------------


class TestDoctorVerdictFormat:
    def test_verdict_all_passed(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        if "[FAIL]" not in result.output:
            assert "all" in result.output and "passed" in result.output

    def test_verdict_one_failed(self):
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        assert "failed" in result.output.lower()

    def test_json_all_passed_flag(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        if data["summary"]["failed"] == 0:
            assert data["summary"]["all_passed"] is True

    def test_json_all_passed_false_when_failed(self):
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        assert data["summary"]["all_passed"] is False
        assert data["summary"]["failed"] > 0

    def test_verdict_starts_with_verdict_prefix(self):
        result = invoke_doctor()
        assert result.output.startswith("VERDICT:")

    def test_json_verdict_not_empty(self):
        result, data = invoke_doctor_json()
        assert data["summary"]["verdict"]
        assert len(data["summary"]["verdict"]) > 0


# ---------------------------------------------------------------------------
# failed_checks field tests
# ---------------------------------------------------------------------------


class TestDoctorFailedChecksField:
    def test_failed_checks_empty_when_all_pass(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        if data["summary"]["all_passed"]:
            assert data["failed_checks"] == []

    def test_failed_checks_contains_failures(self):
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        git_failed = any(c["name"] == "git executable" for c in data["failed_checks"])
        assert git_failed

    def test_failed_checks_no_private_keys(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        for check in data.get("failed_checks", []):
            for key in check:
                assert not key.startswith("_"), f"Private key '{key}' leaked into output"

    def test_checks_no_private_keys(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        for check in data.get("checks", []):
            for key in check:
                assert not key.startswith("_"), f"Private key '{key}' leaked into output"
