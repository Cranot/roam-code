"""Tests for roam migration-safety -- PHP migration idempotency analysis.

Covers:
- Smoke: exit code 0 for PHP project with issues and non-PHP project
- JSON: json_envelope contract, verdict in summary, findings array present
- Text: VERDICT line, findings reported in output
- Filters: --confidence high filter, --limit flag, --include-archive flag
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# PHP migration content constants
# ---------------------------------------------------------------------------

# Migration with deliberate idempotency issues:
#   1. Schema::create('orders') without hasTable guard  -> high confidence
#   2. Schema::drop('legacy_table') bare drop          -> high confidence
_MIGRATION_WITH_ISSUES = """\
<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

class CreateOrdersTable extends Migration
{
    public function up()
    {
        // BUG: no hasTable guard -- will fail on second run
        Schema::create('orders', function (Blueprint $table) {
            $table->id();
            $table->string('customer_name');
            $table->decimal('total', 10, 2);
            $table->timestamps();
        });

        // BUG: bare Schema::drop -- should be dropIfExists
        Schema::drop('legacy_table');
    }

    public function down()
    {
        Schema::dropIfExists('orders');
    }
}
"""

# Clean migration: Schema::create is wrapped in a hasTable guard.
_MIGRATION_CLEAN = """\
<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

class CreateUsersTable extends Migration
{
    public function up()
    {
        if (!Schema::hasTable('users')) {
            Schema::create('users', function (Blueprint $table) {
                $table->id();
                $table->string('name');
                $table->string('email')->unique();
                $table->timestamps();
            });
        }
    }

    public function down()
    {
        Schema::dropIfExists('users');
    }
}
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def php_migration_project(tmp_path):
    """Laravel-style PHP project with database/migrations/ directory.

    Contains one migration with idempotency issues and one clean migration.
    """
    proj = tmp_path / "php_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Laravel conventional migration directory
    migrations_dir = proj / "database" / "migrations"
    migrations_dir.mkdir(parents=True)

    (migrations_dir / "2023_01_01_000001_create_orders_table.php").write_text(_MIGRATION_WITH_ISSUES)
    (migrations_dir / "2023_01_01_000002_create_users_table.php").write_text(_MIGRATION_CLEAN)

    # Minimal PHP app file so the index has at least one non-migration file
    (proj / "app.php").write_text("<?php\n\nrequire 'vendor/autoload.php';\n\necho 'hello';\n")

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def non_php_project(tmp_path):
    """Pure Python project — no PHP files, no migration files.

    migration-safety should exit 0 and report no issues.
    """
    proj = tmp_path / "py_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def main():\n    return 'hello'\n")
    (proj / "utils.py").write_text("def helper(x):\n    return x + 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# TestMigrationSafetySmoke
# ---------------------------------------------------------------------------


class TestMigrationSafetySmoke:
    def test_exits_zero_php_project(self, cli_runner, php_migration_project, monkeypatch):
        """Command exits 0 even when idempotency issues are found."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=php_migration_project)
        assert result.exit_code == 0

    def test_exits_zero_non_php_project(self, cli_runner, non_php_project, monkeypatch):
        """Command exits 0 for a project with no PHP migration files."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=non_php_project)
        assert result.exit_code == 0

    def test_limit_flag_accepted(self, cli_runner, php_migration_project, monkeypatch):
        """--limit flag is accepted without error."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(cli_runner, ["migration-safety", "--limit", "5"], cwd=php_migration_project)
        assert result.exit_code == 0

    def test_include_archive_flag_accepted(self, cli_runner, php_migration_project, monkeypatch):
        """--include-archive flag is accepted without error."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--include-archive"],
            cwd=php_migration_project,
        )
        assert result.exit_code == 0

    def test_confidence_filter_flag_accepted(self, cli_runner, php_migration_project, monkeypatch):
        """--confidence flag with a valid value is accepted without error."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--confidence", "high"],
            cwd=php_migration_project,
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# TestMigrationSafetyJSON
# ---------------------------------------------------------------------------


class TestMigrationSafetyJSON:
    def test_json_envelope(self, cli_runner, php_migration_project, monkeypatch):
        """Output follows the roam json_envelope contract."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert_json_envelope(data, "migration-safety")

    def test_json_summary_has_verdict(self, cli_runner, php_migration_project, monkeypatch):
        """JSON summary dict contains a 'verdict' key."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert "verdict" in data["summary"], "summary must include a 'verdict' field"
        assert isinstance(data["summary"]["verdict"], str)

    def test_json_summary_has_total(self, cli_runner, php_migration_project, monkeypatch):
        """JSON summary dict contains 'total' count."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert "total" in data["summary"]
        assert isinstance(data["summary"]["total"], int)

    def test_json_summary_has_by_confidence(self, cli_runner, php_migration_project, monkeypatch):
        """JSON summary dict contains 'by_confidence' breakdown."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert "by_confidence" in data["summary"]
        bc = data["summary"]["by_confidence"]
        assert isinstance(bc, dict)
        # All three confidence keys must be present
        for key in ("high", "medium", "low"):
            assert key in bc, f"by_confidence must include '{key}'"

    def test_json_summary_has_truncated(self, cli_runner, php_migration_project, monkeypatch):
        """JSON summary dict contains 'truncated' boolean."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert "truncated" in data["summary"]
        assert isinstance(data["summary"]["truncated"], bool)

    def test_json_has_findings_array(self, cli_runner, php_migration_project, monkeypatch):
        """Top-level JSON has a 'findings' list."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert "findings" in data, "JSON output must include a 'findings' key"
        assert isinstance(data["findings"], list)

    def test_json_findings_have_required_fields(self, cli_runner, php_migration_project, monkeypatch):
        """Each finding dict contains the required fields."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        findings = data["findings"]
        assert len(findings) > 0, "Expected at least one finding from the buggy migration"
        for finding in findings:
            for field in ("file", "line", "confidence", "issue", "fix", "category"):
                assert field in finding, f"Finding missing required field '{field}': {finding}"

    def test_json_detects_high_confidence_issues(self, cli_runner, php_migration_project, monkeypatch):
        """High-confidence findings are present for the Schema::create and Schema::drop issues."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        high_findings = [f for f in data["findings"] if f["confidence"] == "high"]
        assert len(high_findings) >= 1, "Expected at least one high-confidence finding (Schema::create or Schema::drop)"

    def test_json_non_php_empty_findings(self, cli_runner, non_php_project, monkeypatch):
        """Non-PHP project produces a valid envelope with zero findings."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=non_php_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert_json_envelope(data, "migration-safety")
        assert data["summary"]["total"] == 0
        assert data["findings"] == []


# ---------------------------------------------------------------------------
# TestMigrationSafetyText
# ---------------------------------------------------------------------------


class TestMigrationSafetyText:
    def test_verdict_line_present(self, cli_runner, php_migration_project, monkeypatch):
        """Text output starts with a VERDICT: line."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=php_migration_project)
        assert "VERDICT:" in result.output, f"Expected 'VERDICT:' in output, got:\n{result.output[:300]}"

    def test_verdict_line_is_first_line(self, cli_runner, php_migration_project, monkeypatch):
        """VERDICT: is the first non-empty line of text output."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=php_migration_project)
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert lines, "Output should not be empty"
        assert lines[0].startswith("VERDICT:"), f"First output line should start with 'VERDICT:', got: {lines[0]!r}"

    def test_verdict_line_non_php_project(self, cli_runner, non_php_project, monkeypatch):
        """VERDICT: line is present even when there are no issues."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=non_php_project)
        assert "VERDICT:" in result.output

    def test_findings_reported_in_output(self, cli_runner, php_migration_project, monkeypatch):
        """Issues from the buggy migration appear in text output."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=php_migration_project)
        # The output should mention either 'high' confidence or specific issue keywords
        output_lower = result.output.lower()
        assert "high" in output_lower or "create" in output_lower or "drop" in output_lower, (
            f"Expected finding keywords in output:\n{result.output[:500]}"
        )

    def test_migration_file_path_in_output(self, cli_runner, php_migration_project, monkeypatch):
        """Text output references the migration file that contains issues."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(cli_runner, ["migration-safety"], cwd=php_migration_project)
        # The problematic migration file name should appear in the output
        assert "create_orders_table" in result.output or "orders" in result.output, (
            f"Expected orders migration to be mentioned:\n{result.output[:500]}"
        )

    def test_clean_migration_not_flagged_for_create(self, cli_runner, php_migration_project, monkeypatch):
        """The clean migration (with hasTable guard) should not flag a create issue for users."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        # Findings from the clean migration should not include create_without_check
        clean_create_issues = [
            f for f in data["findings"] if f["category"] == "create_without_check" and "users" in f.get("issue", "")
        ]
        assert len(clean_create_issues) == 0, "The guarded Schema::create('users') should NOT be flagged as unsafe"


# ---------------------------------------------------------------------------
# TestMigrationSafetyFilters
# ---------------------------------------------------------------------------


class TestMigrationSafetyFilters:
    def test_confidence_high_filter(self, cli_runner, php_migration_project, monkeypatch):
        """--confidence high returns only high-confidence findings."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--confidence", "high"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        for finding in data["findings"]:
            assert finding["confidence"] == "high", (
                f"Expected only 'high' confidence findings, got: {finding['confidence']}"
            )

    def test_confidence_medium_filter(self, cli_runner, php_migration_project, monkeypatch):
        """--confidence medium returns medium-or-higher findings.

        W1005-followup-D: equality→floor semantic change. Pre-fix kept only
        findings with EXACTLY ``confidence == "medium"``; post-fix keeps
        every finding where ``severity_rank(f.confidence) >=
        severity_rank("medium")`` — i.e. high AND medium pass.
        """
        from roam.output._severity import severity_rank

        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--confidence", "medium"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        floor = severity_rank("medium")
        for finding in data["findings"]:
            assert severity_rank(finding["confidence"]) >= floor, (
                f"Expected confidence at or above 'medium' floor (rank {floor}), "
                f"got: {finding['confidence']} (rank "
                f"{severity_rank(finding['confidence'])})"
            )

    def test_confidence_low_filter(self, cli_runner, php_migration_project, monkeypatch):
        """--confidence low returns low-or-higher findings (i.e. everything).

        W1005-followup-D: equality→floor semantic change. Pre-fix kept only
        findings with EXACTLY ``confidence == "low"``; post-fix keeps every
        finding where ``severity_rank(f.confidence) >= severity_rank("low")``
        — i.e. high AND medium AND low all pass.
        """
        from roam.output._severity import severity_rank

        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--confidence", "low"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        floor = severity_rank("low")
        for finding in data["findings"]:
            assert severity_rank(finding["confidence"]) >= floor, (
                f"Expected confidence at or above 'low' floor (rank {floor}), "
                f"got: {finding['confidence']} (rank "
                f"{severity_rank(finding['confidence'])})"
            )

    def test_limit_restricts_findings(self, cli_runner, php_migration_project, monkeypatch):
        """--limit 1 returns at most 1 finding."""
        monkeypatch.chdir(php_migration_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--limit", "1"],
            cwd=php_migration_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert len(data["findings"]) <= 1, f"Expected at most 1 finding with --limit 1, got {len(data['findings'])}"

    def test_include_archive_skips_archive_by_default(self, cli_runner, tmp_path, monkeypatch):
        """Migrations in archive/ subdirectory are skipped unless --include-archive is given."""
        proj = tmp_path / "archive_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        # Put a buggy migration inside an archive/ subdirectory
        archive_dir = proj / "database" / "migrations" / "archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2020_01_01_000001_old_migration.php").write_text(_MIGRATION_WITH_ISSUES)

        # Also place a minimal PHP file so index has something
        (proj / "index.php").write_text("<?php echo 'hi';\n")

        git_init(proj)
        index_in_process(proj)

        monkeypatch.chdir(proj)

        # Without --include-archive: archived migration should be excluded
        result_default = invoke_cli(cli_runner, ["migration-safety"], cwd=proj, json_mode=True)
        data_default = parse_json_output(result_default, "migration-safety")
        assert data_default["summary"]["total"] == 0, "Archive migrations should be excluded by default"

        # With --include-archive: archived migration findings should appear
        result_archive = invoke_cli(
            cli_runner,
            ["migration-safety", "--include-archive"],
            cwd=proj,
            json_mode=True,
        )
        data_archive = parse_json_output(result_archive, "migration-safety")
        assert data_archive["summary"]["total"] >= 1, "Archive migrations should be included with --include-archive"

    def test_confidence_filter_exits_zero_no_matches(self, cli_runner, non_php_project, monkeypatch):
        """--confidence high on a project with no PHP files exits 0 with empty findings."""
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(
            cli_runner,
            ["migration-safety", "--confidence", "high"],
            cwd=non_php_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-safety")
        assert result.exit_code == 0
        assert data["findings"] == []
        assert data["summary"]["total"] == 0
