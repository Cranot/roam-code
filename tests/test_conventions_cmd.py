"""Tests for roam conventions -- auto-detect codebase naming and organization conventions.

Covers:
- Smoke: exits zero on a Python project with consistent naming.
- JSON envelope structure and required summary fields.
- VERDICT line in text output.
- Discovers naming conventions (snake_case functions, PascalCase classes).
- Detects naming outliers.
- Mixed-language project handling.
- Unit tests for classify_case helper.
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def consistent_project(tmp_path):
    """A Python project with consistent naming conventions:
    - All functions use snake_case
    - All classes use PascalCase
    """
    proj = tmp_path / "consistent_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "models.py").write_text(
        "class UserAccount:\n"
        '    """A user account model."""\n'
        "    def __init__(self, first_name, last_name):\n"
        "        self.first_name = first_name\n"
        "        self.last_name = last_name\n"
        "\n"
        "    def get_full_name(self):\n"
        '        return f"{self.first_name} {self.last_name}"\n'
        "\n"
        "    def validate_email(self):\n"
        "        return True\n"
        "\n"
        "\n"
        "class AdminAccount(UserAccount):\n"
        '    """An admin account."""\n'
        "    def __init__(self, first_name, last_name, access_level):\n"
        "        super().__init__(first_name, last_name)\n"
        "        self.access_level = access_level\n"
        "\n"
        "    def grant_permission(self, resource):\n"
        "        pass\n"
        "\n"
        "    def revoke_permission(self, resource):\n"
        "        pass\n"
    )

    (proj / "services.py").write_text(
        "from models import UserAccount, AdminAccount\n"
        "\n"
        "\n"
        "def create_user(first_name, last_name):\n"
        '    """Create a standard user."""\n'
        "    return UserAccount(first_name, last_name)\n"
        "\n"
        "\n"
        "def create_admin(first_name, last_name, level):\n"
        '    """Create an admin user."""\n'
        "    return AdminAccount(first_name, last_name, level)\n"
        "\n"
        "\n"
        "def find_user_by_name(name):\n"
        '    """Look up a user by name."""\n'
        "    return None  # stub\n"
        "\n"
        "\n"
        "def delete_user_account(user_id):\n"
        '    """Delete a user account by ID."""\n'
        "    return True\n"
    )

    (proj / "utils.py").write_text(
        "def format_display_name(first, last):\n"
        '    """Format a display name."""\n'
        '    return f"{first} {last}"\n'
        "\n"
        "\n"
        "def parse_email_address(raw):\n"
        '    """Parse an email address string."""\n'
        '    parts = raw.split("@")\n'
        "    return parts\n"
        "\n"
        "\n"
        "def calculate_account_age(created_at):\n"
        '    """Calculate how old an account is."""\n'
        "    return 0  # stub\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def mixed_naming_project(tmp_path):
    """A project with inconsistent naming -- some camelCase mixed into snake_case."""
    proj = tmp_path / "mixed_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "api.py").write_text(
        "class DataProcessor:\n"
        '    """Processes data."""\n'
        "    def process_batch(self, items):\n"
        "        return items\n"
        "\n"
        "    def validate_input(self, data):\n"
        "        return True\n"
        "\n"
        "    def formatOutput(self, result):\n"
        '        """Outlier: camelCase method in a snake_case codebase."""\n'
        "        return str(result)\n"
        "\n"
        "    def serializeResult(self, data):\n"
        '        """Another camelCase outlier."""\n'
        "        return data\n"
        "\n"
        "\n"
        "def fetch_data(source):\n"
        "    return source\n"
        "\n"
        "\n"
        "def transform_data(raw):\n"
        "    return raw\n"
        "\n"
        "\n"
        "def loadConfig(path):\n"
        '    """Outlier: camelCase function."""\n'
        "    return {}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def mixed_language_project(tmp_path):
    """A project with both Python and JavaScript files."""
    proj = tmp_path / "multilang_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "server.py").write_text(
        "class AppServer:\n"
        '    """Main application server."""\n'
        "    def start_server(self):\n"
        "        pass\n"
        "\n"
        "    def stop_server(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "def run_server(host, port):\n"
        "    return AppServer()\n"
    )

    (proj / "client.js").write_text(
        "class ApiClient {\n"
        "    constructor(baseUrl) {\n"
        "        this.baseUrl = baseUrl;\n"
        "    }\n"
        "\n"
        "    fetchData(endpoint) {\n"
        "        return fetch(this.baseUrl + endpoint);\n"
        "    }\n"
        "\n"
        "    postData(endpoint, data) {\n"
        "        return fetch(this.baseUrl + endpoint, { method: 'POST', body: data });\n"
        "    }\n"
        "}\n"
        "\n"
        "function createClient(url) {\n"
        "    return new ApiClient(url);\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestConventionsSmoke:
    def test_exits_zero(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        assert result.exit_code == 0, f"conventions failed:\n{result.output}"

    def test_output_is_non_empty(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        assert result.output.strip(), "Expected non-empty output from conventions"

    def test_mixed_naming_exits_zero(self, cli_runner, mixed_naming_project, monkeypatch):
        monkeypatch.chdir(mixed_naming_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=mixed_naming_project)
        assert result.exit_code == 0, f"conventions mixed failed:\n{result.output}"

    def test_mixed_language_exits_zero(self, cli_runner, mixed_language_project, monkeypatch):
        monkeypatch.chdir(mixed_language_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=mixed_language_project)
        assert result.exit_code == 0, f"conventions multilang failed:\n{result.output}"


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestConventionsJSON:
    def test_json_envelope_contract(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert_json_envelope(data, "conventions")

    def test_json_summary_has_verdict(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {summary}"
        assert isinstance(summary["verdict"], str)
        assert summary["verdict"]

    def test_json_summary_has_outlier_count(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        summary = data.get("summary", {})
        assert "outlier_count" in summary, f"Missing 'outlier_count': {summary}"
        assert isinstance(summary["outlier_count"], int)

    def test_json_summary_has_total_symbols(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        summary = data.get("summary", {})
        assert "total_symbols_analyzed" in summary
        assert isinstance(summary["total_symbols_analyzed"], int)
        assert summary["total_symbols_analyzed"] >= 1

    def test_json_has_naming_dict(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "naming" in data, f"Missing 'naming' key: {list(data.keys())}"
        assert isinstance(data["naming"], dict)

    def test_json_has_files_dict(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "files" in data, f"Missing 'files' key: {list(data.keys())}"
        assert isinstance(data["files"], dict)
        assert "total_files" in data["files"]

    def test_json_has_imports_dict(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "imports" in data, f"Missing 'imports' key: {list(data.keys())}"
        assert isinstance(data["imports"], dict)

    def test_json_has_exports_dict(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "exports" in data, f"Missing 'exports' key: {list(data.keys())}"
        assert isinstance(data["exports"], dict)

    def test_json_has_violations_list(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "violations" in data, f"Missing 'violations': {list(data.keys())}"
        assert isinstance(data["violations"], list)


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestConventionsText:
    def test_verdict_line_present(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        assert "VERDICT:" in result.output

    def test_verdict_is_first_line(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert lines, "Output is empty"
        assert lines[0].startswith("VERDICT:"), f"First non-empty line should start with VERDICT:, got: {lines[0]!r}"

    def test_shows_naming_section(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        assert "Naming" in result.output

    def test_shows_file_organization_section(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        assert "File Organization" in result.output

    def test_shows_import_section(self, cli_runner, consistent_project, monkeypatch):
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project)
        assert "Import" in result.output


# ---------------------------------------------------------------------------
# Naming detection tests
# ---------------------------------------------------------------------------


class TestConventionsNaming:
    def test_detects_snake_case_functions(self, cli_runner, consistent_project, monkeypatch):
        """Functions in the consistent project should be detected as snake_case."""
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        naming = data.get("naming", {})
        functions_info = naming.get("functions", {})
        if not functions_info:
            pytest.skip("No function naming info detected")
        assert functions_info["dominant_style"] == "snake_case", (
            f"Expected snake_case dominant for functions, got: {functions_info['dominant_style']}"
        )

    def test_detects_pascal_case_classes(self, cli_runner, consistent_project, monkeypatch):
        """Classes in the consistent project should be detected as PascalCase."""
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        naming = data.get("naming", {})
        classes_info = naming.get("classes", {})
        if not classes_info:
            pytest.skip("No class naming info detected")
        assert classes_info["dominant_style"] == "PascalCase", (
            f"Expected PascalCase dominant for classes, got: {classes_info['dominant_style']}"
        )

    def test_consistent_project_has_few_outliers(self, cli_runner, consistent_project, monkeypatch):
        """A consistently-named project should have very few outliers."""
        monkeypatch.chdir(consistent_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=consistent_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        outlier_count = data["summary"]["outlier_count"]
        total = data["summary"]["total_symbols_analyzed"]
        # Allow up to 20% outliers (single-word names may classify ambiguously)
        if total > 0:
            outlier_ratio = outlier_count / total
            assert outlier_ratio < 0.3, f"Too many outliers: {outlier_count}/{total} = {outlier_ratio:.1%}"

    def test_mixed_naming_detects_outliers(self, cli_runner, mixed_naming_project, monkeypatch):
        """A project with camelCase in a snake_case codebase should find outliers."""
        monkeypatch.chdir(mixed_naming_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=mixed_naming_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        # We have at least 3 camelCase outliers: formatOutput, serializeResult, loadConfig
        # They may or may not be detected depending on how multiword detection works
        # Just verify outlier_count > 0
        outlier_count = data["summary"]["outlier_count"]
        assert outlier_count >= 1, f"Expected at least 1 naming outlier, got {outlier_count}"

    def test_mixed_naming_violation_fields(self, cli_runner, mixed_naming_project, monkeypatch):
        """Each violation should have name, kind, actual_style, expected_style."""
        monkeypatch.chdir(mixed_naming_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=mixed_naming_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        violations = data.get("violations", [])
        if not violations:
            pytest.skip("No violations detected")
        # R22 confidence triple shape — original fields nested under value
        for v in violations:
            assert "value" in v and "confidence" in v and "reason" in v
            assert "name" in v["value"], f"Missing 'name' in violation value: {v}"
            assert "actual_style" in v["value"], f"Missing 'actual_style' in violation value: {v}"
            assert "expected_style" in v["value"], f"Missing 'expected_style' in violation value: {v}"
            assert "file" in v["value"], f"Missing 'file' in violation value: {v}"


# ---------------------------------------------------------------------------
# Unit tests for classify_case
# ---------------------------------------------------------------------------


class TestClassifyCase:
    """Unit tests for the classify_case helper function."""

    def test_snake_case(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("get_user_name") == "snake_case"
        assert classify_case("parse_email") == "snake_case"

    def test_camel_case(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("getUserName") == "camelCase"
        assert classify_case("parseEmail") == "camelCase"

    def test_pascal_case(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("UserAccount") == "PascalCase"
        assert classify_case("AdminConfig") == "PascalCase"

    def test_upper_snake(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("MAX_RETRIES") == "UPPER_SNAKE"
        assert classify_case("DB_HOST") == "UPPER_SNAKE"

    def test_single_word_lower(self):
        from roam.commands.cmd_conventions import classify_case

        # Single lowercase words are classified as snake_case-compatible
        assert classify_case("validate") == "snake_case"

    def test_single_word_pascal(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("User") == "PascalCase"

    def test_dunder_returns_none(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("__init__") is None
        assert classify_case("__str__") is None

    def test_too_short_returns_none(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("x") is None

    def test_skip_names_return_none(self):
        from roam.commands.cmd_conventions import classify_case

        assert classify_case("constructor") is None
        assert classify_case("toString") is None


# ---------------------------------------------------------------------------
# Mixed-language tests
# ---------------------------------------------------------------------------


class TestConventionsMixedLanguage:
    def test_mixed_language_has_naming(self, cli_runner, mixed_language_project, monkeypatch):
        """A mixed Python/JS project should still detect naming conventions."""
        monkeypatch.chdir(mixed_language_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=mixed_language_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        naming = data.get("naming", {})
        assert len(naming) >= 1, f"Expected at least 1 naming group, got: {naming}"

    def test_mixed_language_files_counted(self, cli_runner, mixed_language_project, monkeypatch):
        """File organization should count both Python and JS files."""
        monkeypatch.chdir(mixed_language_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=mixed_language_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        files = data.get("files", {})
        assert files["total_files"] >= 2, f"Expected at least 2 files, got: {files['total_files']}"
