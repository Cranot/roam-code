"""Tests for `roam doctor` — setup diagnostics command."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from unittest.mock import patch

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
        "Plugin discovery",
        "Required tables",
        "Corpus content",
    }

    def test_all_expected_checks_present(self):
        result, data = invoke_doctor_json()
        names = {c["name"] for c in data["checks"]}
        for expected in self._EXPECTED_CHECK_NAMES:
            assert expected in names, f"Expected check '{expected}' not found. Got: {names}"

    def test_check_count(self):
        result, data = invoke_doctor_json()
        # three checks (CLI registry + MCP registry +
        # MCP backpressure), bringing the total from 8 to 11.
        # v12.16 / Pass 35 added two more (Plugin discovery + Required
        # tables) for a total of 13. Three more (optional extras, cloud
        # sync, cache permissions) bring it to 16; the manifest check
        # makes it 17. S17 added the manifest-history cross-run drift
        # check — total 18. W14.3 added the stale-install advisory
        # ("Installed binary") — total 19. W38.x added the post-migration
        # #51 stale-signal advisory ("Stale math_signals column") — total 20.
        # W82 / ROADMAP A8 added the per-sub-step manifest advisory
        # ("Index step manifest") — total 21. W408 added the per-phase
        # wallclock advisory ("Phase timings") — total 22. W482 added
        # the emitted-workflow drift advisory ("CI workflow drift") — total 23.
        # W836 added the corpus-content advisory (Pattern 2 sweep) — total 24.
        assert data["summary"]["total"] == 24

    def test_passed_plus_failed_equals_total(self):
        result, data = invoke_doctor_json()
        summary = data["summary"]
        assert summary["passed"] + summary["failed"] == summary["total"]


# ---------------------------------------------------------------------------
# Python version check
# ---------------------------------------------------------------------------


class TestDoctorPythonCheck:
    def test_python_check_passes_current_version(self):
        """Current Python must be >= 3.10 to run roam-code at all."""
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

    def test_python_check_fails_39(self):
        from roam.commands.cmd_doctor import _check_python_version

        fake_vi = type("VI", (), {"major": 3, "minor": 9, "micro": 0})()
        with patch.object(sys, "version_info", fake_vi):
            check = _check_python_version()
        assert check["passed"] is False

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
        import builtins

        from roam.commands.cmd_doctor import _check_tree_sitter

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
        import builtins

        from roam.commands.cmd_doctor import _check_tree_sitter_language_pack

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
        import builtins

        from roam.commands.cmd_doctor import _check_networkx

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

        # Original test patched cmd_doctor.Path but used get_db_path patch
        # below, so the Path patch was a no-op left over from a refactor.

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
    """Three-tier exit codes: 0 = all clean, 1 = only advisory failures
    (cache age, cloud-sync, optional extras), 2 = blocking failures.
    ``--strict`` promotes advisory to blocking.
    """

    def test_exit_0_when_all_pass(self):
        """When no checks fail (no FAIL or WARN), exit code is 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        # Only assert the contract when the live env happens to be clean.
        if "[FAIL]" not in result.output and "[WARN]" not in result.output:
            assert result.exit_code == 0

    def test_exit_2_when_blocking_check_fails(self, tmp_path):
        """git executable missing is a BLOCKING failure → exit 2."""
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        assert result.exit_code == 2, f"git missing should exit 2 (blocking), got {result.exit_code}"

    def test_strict_promotes_advisory_to_blocking(self):
        """--strict makes any failure (advisory or blocking) exit 2."""
        runner = CliRunner()
        # Run with --strict on the live repo. If there's any advisory
        # failure (cloud-sync on OneDrive, stale manifest), --strict
        # should promote it. We can only assert the exit code maps
        # consistently with the failure presence.
        result = runner.invoke(cli, ["doctor", "--strict"], catch_exceptions=False)
        if "[WARN]" in result.output or "[FAIL]" in result.output:
            assert result.exit_code == 2
        else:
            assert result.exit_code == 0

    def test_json_exit_2_when_blocking_check_fails(self):
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        assert result.exit_code == 2

    def test_json_exit_0_when_all_pass(self):
        """JSON mode exits 0 on clean OR advisory-only failures.

        Two-tier exit-code contract (Pattern-2 advisory-vs-blocker):
          * exit 0 -> no blocking failures (advisory failures allowed)
          * exit 2 -> at least one blocking failure (or --strict promotes
            advisory to blocking)
        Pre-Pattern-2 behaviour was three-tier (0/1/2) which read as
        "broken" to fresh-install users when only advisories failed.
        """
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        if result.exit_code == 0:
            data = json.loads(result.output)
            # exit 0 is now a CONJUNCT of "no blockers" — advisory failures
            # are permitted. Assert the conjunct, not the strict all-pass.
            assert data["summary"]["blocking_failed"] == 0

    def test_json_envelope_carries_severity_split(self):
        """JSON envelope must surface the advisory/blocking split so
        CI tooling can branch on severity without parsing text.
        """
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        summary = data["summary"]
        # New fields the three-tier exit code introduces.
        assert "advisory_failed" in summary
        assert "blocking_failed" in summary
        assert "issue_line" in summary
        # advisory_failed + blocking_failed must equal failed total.
        assert summary["advisory_failed"] + summary["blocking_failed"] == summary["failed"]
        # And the issue-template-ready single-line summary is present.
        assert "Roam" in summary["issue_line"]
        assert "Python" in summary["issue_line"]
        assert "checks pass" in summary["issue_line"]


# ---------------------------------------------------------------------------
# Verdict formatting tests
# ---------------------------------------------------------------------------


class TestDoctorVerdictFormat:
    def test_verdict_all_passed(self):
        """When neither blocking [FAIL] nor advisory [WARN] entries appear,
        the verdict must surface the all-passed message.
        """
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        if "[FAIL]" not in result.output and "[WARN]" not in result.output:
            assert "all" in result.output and "passed" in result.output

    def test_verdict_one_failed(self):
        """A blocking-check failure surfaces failure language —
        either ``[FAIL]`` line or ``failed`` / ``blocking`` in verdict.
        """
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(cli, ["doctor"], catch_exceptions=False)
        out = result.output.lower()
        assert "[fail]" in out or "blocking" in out or "failed" in out

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


# ---------------------------------------------------------------------------
# Index manifest check
# ---------------------------------------------------------------------------


class TestDoctorIndexManifestCheck:
    """The doctor reads the most recent index_manifest row, builds a
    "what's true now" manifest, and surfaces drift fields as hints. Closes
    the loop on the just-shipped manifest table.
    """

    def test_manifest_check_present(self):
        result, data = invoke_doctor_json()
        names = {c["name"] for c in data["checks"]}
        assert "Index manifest" in names

    def test_manifest_check_skips_when_no_db(self):
        from roam.commands.cmd_doctor import _check_index_manifest

        with patch("roam.db.connection.db_exists", return_value=False):
            check = _check_index_manifest()
        assert check["passed"] is True
        assert "no index" in check["detail"].lower()

    def test_manifest_drift_dirty_hash_hint(self, tmp_path):
        """When the recorded manifest claims a clean tree but the live
        tree is dirty (or vice versa), the check surfaces an INFO hint
        pointing at the drift.
        """
        from roam.commands.cmd_doctor import _check_index_manifest

        # Build two manifests that differ only in git_dirty_hash.
        prev = {
            "indexed_at": int(time.time()),
            "roam_version": "test",
            "schema_version": 12,
            "parser_versions": {},
            "grammar_versions": None,
            "config_hash": "h1",
            "git_head": "abc123",
            "git_dirty_hash": None,  # clean at index time
            "enabled_extras": [],
            "index_profile": "all",
        }
        current = dict(prev)
        current["git_dirty_hash"] = "deadbeef" * 8  # dirty now

        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.index.manifest.latest_manifest", return_value=prev),
            patch("roam.index.manifest.collect_manifest", return_value=current),
            patch("roam.db.connection.find_project_root", return_value=tmp_path),
            patch("roam.db.connection.open_db") as mock_open,
        ):
            # open_db is a context manager; just need it to not raise.
            mock_open.return_value.__enter__.return_value = None
            mock_open.return_value.__exit__.return_value = False
            check = _check_index_manifest()
        # git_dirty_hash drift is an INFO-level hint, not a blocker.
        assert "uncommitted" in check["detail"].lower() or "dirty" in check["detail"].lower()

    def test_manifest_drift_config_hash_warns(self, tmp_path):
        """Config / .roamignore changes since index → WARN + check fails."""
        from roam.commands.cmd_doctor import _check_index_manifest

        prev = {
            "indexed_at": int(time.time()),
            "roam_version": "test",
            "schema_version": 12,
            "parser_versions": {},
            "grammar_versions": None,
            "config_hash": "h1",
            "git_head": None,
            "git_dirty_hash": None,
            "enabled_extras": [],
            "index_profile": "all",
        }
        current = dict(prev)
        current["config_hash"] = "h2"  # config changed

        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.index.manifest.latest_manifest", return_value=prev),
            patch("roam.index.manifest.collect_manifest", return_value=current),
            patch("roam.db.connection.find_project_root", return_value=tmp_path),
            patch("roam.db.connection.open_db") as mock_open,
        ):
            mock_open.return_value.__enter__.return_value = None
            mock_open.return_value.__exit__.return_value = False
            check = _check_index_manifest()
        assert check["passed"] is False
        assert "config" in check["detail"].lower()


# ---------------------------------------------------------------------------
# Index manifest history check (S17 — cross-run drift detector)
# ---------------------------------------------------------------------------


class TestDoctorIndexManifestHistoryCheck:
    """The doctor compares the two most recent index_manifest rows.

    Three states:
      * no_history    — 0 or 1 manifest rows (fresh DB / first index)
      * stable        — 2+ rows, no drift fields differ
      * drift_detected — 2+ rows, one or more drift fields differ
    """

    def _make_db_with_manifest(self, tmp_path, manifests):
        """Create a SQLite DB at tmp_path with `index_manifest` rows.

        Each entry in `manifests` is a dict supplying overrides for
        the row. The schema is inlined here so the test doesn't have
        to spin up a full roam index.
        """
        import sqlite3

        db_path = tmp_path / "index.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE index_manifest (
                id INTEGER PRIMARY KEY,
                indexed_at INTEGER NOT NULL,
                roam_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                parser_versions TEXT NOT NULL,
                grammar_versions TEXT,
                config_hash TEXT NOT NULL,
                git_head TEXT,
                git_dirty_hash TEXT,
                enabled_extras TEXT NOT NULL,
                index_profile TEXT DEFAULT 'all',
                notes TEXT
            )
            """
        )
        base_at = int(time.time()) - 10_000
        for i, m in enumerate(manifests):
            conn.execute(
                "INSERT INTO index_manifest (indexed_at, roam_version, schema_version, "
                "parser_versions, grammar_versions, config_hash, git_head, "
                "git_dirty_hash, enabled_extras, index_profile, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    m.get("indexed_at", base_at + i * 100),
                    m.get("roam_version", "1.0.0"),
                    m.get("schema_version", 12),
                    m.get("parser_versions", "{}"),
                    m.get("grammar_versions"),
                    m.get("config_hash", "hash0"),
                    m.get("git_head"),
                    m.get("git_dirty_hash"),
                    m.get("enabled_extras", "[]"),
                    m.get("index_profile", "all"),
                    m.get("notes"),
                ),
            )
        conn.commit()
        conn.close()
        return db_path

    def test_history_check_present(self):
        result, data = invoke_doctor_json()
        names = {c["name"] for c in data["checks"]}
        assert "Index manifest history" in names

    def test_history_check_no_history_when_no_db(self):
        from roam.commands.cmd_doctor import _check_index_manifest_history

        with patch("roam.db.connection.db_exists", return_value=False):
            check = _check_index_manifest_history()
        assert check["passed"] is True
        assert "no index" in check["detail"].lower()
        assert check.get("_state") == "no_history"

    def _fake_open_db(self, db_path):
        """Build a fake `open_db` that yields a sqlite3 connection to db_path."""
        import sqlite3

        class _Ctx:
            def __enter__(self_inner):
                self_inner.conn = sqlite3.connect(str(db_path))
                return self_inner.conn

            def __exit__(self_inner, *a):
                self_inner.conn.close()
                return False

        def _open_db(*args, **kwargs):
            return _Ctx()

        return _open_db

    def test_history_check_no_history_with_zero_rows(self, tmp_path):
        """0 manifest rows -> no_history, advisory pass."""
        from roam.commands.cmd_doctor import _check_index_manifest_history

        db_path = self._make_db_with_manifest(tmp_path, [])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=self._fake_open_db(db_path)),
        ):
            check = _check_index_manifest_history()
        assert check["passed"] is True
        assert check.get("_state") == "no_history"
        assert check.get("_row_count") == 0

    def test_history_check_no_history_with_one_row(self, tmp_path):
        """1 manifest row -> no_history (no prior run to diff)."""
        from roam.commands.cmd_doctor import _check_index_manifest_history

        db_path = self._make_db_with_manifest(tmp_path, [{"config_hash": "h1"}])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=self._fake_open_db(db_path)),
        ):
            check = _check_index_manifest_history()
        assert check["passed"] is True
        assert check.get("_state") == "no_history"
        assert check.get("_row_count") == 1
        assert "no prior run" in check["detail"].lower()

    def test_history_check_stable_when_two_identical_rows(self, tmp_path):
        """2 identical rows -> stable."""
        from roam.commands.cmd_doctor import _check_index_manifest_history

        identical = {"roam_version": "1.0.0", "schema_version": 12, "config_hash": "h1"}
        db_path = self._make_db_with_manifest(tmp_path, [identical, identical])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=self._fake_open_db(db_path)),
        ):
            check = _check_index_manifest_history()
        assert check["passed"] is True
        assert check.get("_state") == "stable"
        assert "identical" in check["detail"].lower() or "stable" in check["detail"].lower()

    def test_history_check_drift_detected_on_config_change(self, tmp_path):
        """2 rows where config_hash differs -> drift_detected."""
        from roam.commands.cmd_doctor import _check_index_manifest_history

        first = {"roam_version": "1.0.0", "schema_version": 12, "config_hash": "h1"}
        second = {"roam_version": "1.0.0", "schema_version": 12, "config_hash": "h2"}
        db_path = self._make_db_with_manifest(tmp_path, [first, second])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=self._fake_open_db(db_path)),
        ):
            check = _check_index_manifest_history()
        assert check["passed"] is False
        assert check.get("_state") == "drift_detected"
        assert "config_hash" in check.get("_drift_fields", [])
        assert "config_hash" in check["detail"]

    def test_history_check_drift_detected_on_roam_version_bump(self, tmp_path):
        """A roam_version change between consecutive index runs -> drift."""
        from roam.commands.cmd_doctor import _check_index_manifest_history

        first = {"roam_version": "1.0.0", "schema_version": 12}
        second = {"roam_version": "1.1.0", "schema_version": 12}
        db_path = self._make_db_with_manifest(tmp_path, [first, second])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=self._fake_open_db(db_path)),
        ):
            check = _check_index_manifest_history()
        assert check["passed"] is False
        assert check.get("_state") == "drift_detected"
        assert "roam_version" in check.get("_drift_fields", [])

    def test_history_check_is_advisory(self):
        """A drift in history must NOT block doctor (advisory only)."""
        from roam.commands.cmd_doctor import _ADVISORY_CHECK_NAMES

        assert "Index manifest history" in _ADVISORY_CHECK_NAMES
