"""Tests for the `missing-index` command.

The command cross-references migration index definitions against query patterns
in PHP source files to detect WHERE / ORDER BY calls on non-indexed columns.

Test structure:
  - TestMissingIndexSmoke   : exit_code == 0 for PHP and non-PHP projects
  - TestMissingIndexJSON    : json_envelope contract, verdict in summary, findings list
  - TestMissingIndexText    : VERDICT line and index summary line present in output
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# PHP fixture content
# ---------------------------------------------------------------------------

# Migration: creates `users` table with an index on `email` but NOT on `phone`
_MIGRATION_PHP = """\
<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

class CreateUsersTable extends Migration
{
    public function up()
    {
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('email')->unique();
            $table->string('phone');
            $table->timestamps();

            $table->index('email');
        });
    }

    public function down()
    {
        Schema::dropIfExists('users');
    }
}
"""

# Controller: queries on `phone` (non-indexed) — should be detected
_CONTROLLER_PHP = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\User;
use Illuminate\\Http\\Request;

class UserController extends Controller
{
    public function findByPhone(Request $request)
    {
        $phone = $request->input('phone');
        $users = User::query()->where('phone', $phone)->get();
        return response()->json($users);
    }

    public function listByEmail(Request $request)
    {
        $email = $request->input('email');
        $user = User::query()->where('email', $email)->first();
        return response()->json($user);
    }

    public function sortByName(Request $request)
    {
        $users = User::query()->orderBy('name')->get();
        return response()->json($users);
    }
}
"""


# ---------------------------------------------------------------------------
# Shared fixture: PHP project with migration + controller
# ---------------------------------------------------------------------------


@pytest.fixture
def php_project(tmp_path):
    """Create a PHP Laravel-style project with a migration and controller.

    The migration creates a `users` table with an index on `email` but no
    index on `phone`.  The controller queries on `phone` (non-indexed) so
    the command should produce at least one finding.
    """
    proj = tmp_path / "php_app"
    proj.mkdir()

    # .gitignore
    (proj / ".gitignore").write_text(".roam/\nvendor/\n")

    # Migration file
    migration_dir = proj / "database" / "migrations"
    migration_dir.mkdir(parents=True)
    (migration_dir / "2024_01_01_000000_create_users_table.php").write_text(_MIGRATION_PHP)

    # Controller file
    controller_dir = proj / "app" / "Http" / "Controllers"
    controller_dir.mkdir(parents=True)
    (controller_dir / "UserController.php").write_text(_CONTROLLER_PHP)

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def non_php_project(tmp_path):
    """Create a minimal Python-only project (no PHP files at all)."""
    proj = tmp_path / "py_app"
    proj.mkdir()

    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def main():\n    pass\n")

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def cli_runner():
    """CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ===========================================================================
# TestMissingIndexSmoke — exit_code == 0
# ===========================================================================


class TestMissingIndexSmoke:
    """Smoke tests: command exits cleanly regardless of project content."""

    def test_exit_code_php_project(self, cli_runner, php_project, monkeypatch):
        """Command exits with code 0 on a PHP project that has migrations."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0, f"Unexpected exit code:\n{result.output}"

    def test_exit_code_non_php_project(self, cli_runner, non_php_project, monkeypatch):
        """Command exits with code 0 on a project with no PHP files."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=non_php_project)
        assert result.exit_code == 0, f"Unexpected exit code:\n{result.output}"

    def test_exit_code_with_limit_flag(self, cli_runner, php_project, monkeypatch):
        """--limit flag is accepted and command exits cleanly."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index", "--limit", "5"], cwd=php_project)
        assert result.exit_code == 0, f"Unexpected exit code:\n{result.output}"

    def test_exit_code_with_confidence_flag(self, cli_runner, php_project, monkeypatch):
        """--confidence flag is accepted and command exits cleanly."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--confidence", "high"],
            cwd=php_project,
        )
        assert result.exit_code == 0, f"Unexpected exit code:\n{result.output}"

    def test_exit_code_with_table_flag(self, cli_runner, php_project, monkeypatch):
        """--table flag is accepted and command exits cleanly."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--table", "users"],
            cwd=php_project,
        )
        assert result.exit_code == 0, f"Unexpected exit code:\n{result.output}"

    def test_exit_code_confidence_medium(self, cli_runner, php_project, monkeypatch):
        """--confidence medium is a valid choice."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--confidence", "medium"],
            cwd=php_project,
        )
        assert result.exit_code == 0

    def test_exit_code_confidence_low(self, cli_runner, php_project, monkeypatch):
        """--confidence low is a valid choice."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--confidence", "low"],
            cwd=php_project,
        )
        assert result.exit_code == 0


# ===========================================================================
# TestMissingIndexJSON — envelope contract + findings structure
# ===========================================================================


class TestMissingIndexJSON:
    """JSON output adheres to the json_envelope contract."""

    def test_json_envelope_structure(self, cli_runner, php_project, monkeypatch):
        """JSON output conforms to the roam envelope schema."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        assert_json_envelope(data, "missing-index")

    def test_json_command_name(self, cli_runner, php_project, monkeypatch):
        """JSON envelope has command == 'missing-index'."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        assert data["command"] == "missing-index"

    def test_json_summary_has_verdict(self, cli_runner, php_project, monkeypatch):
        """summary dict contains a non-empty 'verdict' string."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        summary = data["summary"]
        assert "verdict" in summary
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 0

    def test_json_summary_has_total(self, cli_runner, php_project, monkeypatch):
        """summary dict contains a 'total' integer count."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        summary = data["summary"]
        assert "total" in summary
        assert isinstance(summary["total"], int)

    def test_json_summary_has_by_confidence(self, cli_runner, php_project, monkeypatch):
        """summary dict contains 'by_confidence' mapping."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        summary = data["summary"]
        assert "by_confidence" in summary
        assert isinstance(summary["by_confidence"], dict)

    def test_json_summary_has_indexes_found(self, cli_runner, php_project, monkeypatch):
        """summary dict contains 'indexes_found' integer."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        summary = data["summary"]
        assert "indexes_found" in summary
        assert isinstance(summary["indexes_found"], int)

    def test_json_has_findings_list(self, cli_runner, php_project, monkeypatch):
        """Top-level 'findings' key is a list."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        assert "findings" in data
        assert isinstance(data["findings"], list)

    def test_json_detects_phone_column(self, cli_runner, php_project, monkeypatch):
        """Query on non-indexed 'phone' column is reported in findings."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        findings = data["findings"]
        # At least one finding should reference the 'phone' column
        phone_findings = [
            f for f in findings if "phone" in f.get("columns", []) or "phone" in str(f.get("issue", ""))
        ]
        assert len(phone_findings) >= 1, (
            f"Expected a finding for unindexed 'phone' column. All findings: {findings}"
        )

    def test_json_finding_has_required_keys(self, cli_runner, php_project, monkeypatch):
        """Each finding dict contains the required structural keys."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        findings = data["findings"]
        if not findings:
            pytest.skip("No findings produced — cannot check finding structure")
        required_keys = {"confidence", "columns", "issue", "query_location", "suggestion"}
        for f in findings:
            missing = required_keys - set(f.keys())
            assert not missing, f"Finding missing keys {missing}: {f}"

    def test_json_finding_confidence_values(self, cli_runner, php_project, monkeypatch):
        """All finding confidence values are one of: high, medium, low."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        valid_levels = {"high", "medium", "low"}
        for f in data["findings"]:
            assert f["confidence"] in valid_levels, (
                f"Unexpected confidence '{f['confidence']}' in finding: {f}"
            )

    def test_json_no_findings_non_php(self, cli_runner, non_php_project, monkeypatch):
        """Non-PHP project produces zero findings."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=non_php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        assert data["findings"] == []
        assert data["summary"]["total"] == 0

    def test_json_migrations_scanned_count(self, cli_runner, php_project, monkeypatch):
        """summary contains migrations_scanned >= 1 for the PHP project."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        assert data["summary"].get("migrations_scanned", 0) >= 1

    def test_json_source_files_scanned_count(self, cli_runner, php_project, monkeypatch):
        """summary contains source_files_scanned >= 1 for the PHP project."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project, json_mode=True)
        data = parse_json_output(result, "missing-index")
        assert data["summary"].get("source_files_scanned", 0) >= 1

    def test_json_confidence_filter_applied(self, cli_runner, php_project, monkeypatch):
        """--confidence high returns only high-confidence findings in JSON."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--confidence", "high"],
            cwd=php_project,
            json_mode=True,
        )
        data = parse_json_output(result, "missing-index")
        for f in data["findings"]:
            assert f["confidence"] == "high", (
                f"Expected only 'high' confidence with --confidence high, got: {f['confidence']}"
            )

    def test_json_table_filter_applied(self, cli_runner, php_project, monkeypatch):
        """--table users returns only findings for the users table."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--table", "users"],
            cwd=php_project,
            json_mode=True,
        )
        data = parse_json_output(result, "missing-index")
        for f in data["findings"]:
            assert f.get("table") == "users", (
                f"Expected only 'users' table with --table users, got: {f.get('table')}"
            )

    def test_json_table_filter_no_results_unknown_table(
        self, cli_runner, php_project, monkeypatch
    ):
        """--table on a non-existent table yields zero findings."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--table", "nonexistent_table_xyz"],
            cwd=php_project,
            json_mode=True,
        )
        data = parse_json_output(result, "missing-index")
        assert data["findings"] == []

    def test_json_limit_truncates_findings(self, cli_runner, php_project, monkeypatch):
        """--limit 1 returns at most 1 finding."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--limit", "1"],
            cwd=php_project,
            json_mode=True,
        )
        data = parse_json_output(result, "missing-index")
        assert len(data["findings"]) <= 1


# ===========================================================================
# TestMissingIndexText — text output checks
# ===========================================================================


class TestMissingIndexText:
    """Text output contains expected landmark lines."""

    def test_verdict_line_present(self, cli_runner, php_project, monkeypatch):
        """Text output starts with a VERDICT: line."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output, (
            f"Expected 'VERDICT:' in output but got:\n{result.output}"
        )

    def test_verdict_first_non_empty_line(self, cli_runner, php_project, monkeypatch):
        """The first non-empty output line starts with 'VERDICT:'."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0
        non_empty = [ln for ln in result.output.splitlines() if ln.strip()]
        assert non_empty, "No output produced"
        assert non_empty[0].startswith("VERDICT:"), (
            f"First non-empty line does not start with VERDICT:  got: {non_empty[0]!r}"
        )

    def test_index_summary_line_present(self, cli_runner, php_project, monkeypatch):
        """Text output contains the 'Indexes found:' summary line."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0
        assert "Indexes found:" in result.output, (
            f"Expected 'Indexes found:' in output but got:\n{result.output}"
        )

    def test_migrations_scanned_line_present(self, cli_runner, php_project, monkeypatch):
        """Text output contains the 'Migrations scanned:' summary line."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0
        assert "Migrations scanned:" in result.output, (
            f"Expected 'Migrations scanned:' in output but got:\n{result.output}"
        )

    def test_verdict_no_findings_non_php(self, cli_runner, non_php_project, monkeypatch):
        """Non-PHP project verdict says no missing indexes detected."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=non_php_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "No missing indexes detected" in result.output

    def test_phone_column_in_findings_text(self, cli_runner, php_project, monkeypatch):
        """Text output mentions the 'phone' column that lacks an index."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0
        # phone is queried without an index — should appear in output
        assert "phone" in result.output, (
            f"Expected 'phone' (non-indexed column) to appear in text output:\n{result.output}"
        )

    def test_users_table_in_text_output(self, cli_runner, php_project, monkeypatch):
        """Text output references the 'users' table where findings are found."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(cli_runner, ["missing-index"], cwd=php_project)
        assert result.exit_code == 0
        assert "users" in result.output.lower(), (
            f"Expected 'users' table name in text output:\n{result.output}"
        )

    def test_confidence_filter_text_output(self, cli_runner, php_project, monkeypatch):
        """--confidence high produces valid VERDICT: output."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--confidence", "high"],
            cwd=php_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_limit_flag_text_output(self, cli_runner, php_project, monkeypatch):
        """--limit 0 shows no findings beyond the truncation note."""
        monkeypatch.chdir(php_project)
        result = invoke_cli(
            cli_runner,
            ["missing-index", "--limit", "0"],
            cwd=php_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
