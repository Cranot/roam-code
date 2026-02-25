"""Tests for the ``roam rules`` command and rules engine.

Covers:
1. Rule YAML loading from directory
2. Path match evaluation (pass/fail)
3. Symbol match evaluation
4. Exemptions
5. Severity levels
6. CLI output (text, JSON, verdict, CI mode)
7. --init flag
8. Edge cases (no rules dir, invalid YAML, empty dir)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, git_init, index_in_process

from roam.cli import cli
from roam.rules.engine import evaluate_all, load_rules

# ===========================================================================
# Helpers
# ===========================================================================


def _make_project_with_rules(tmp_path, source_files, rule_files, *, index=True):
    """Create a git project with source files and .roam/rules/ YAML files.

    Args:
        tmp_path: pytest tmp_path
        source_files: dict of {relative_path: content}
        rule_files: dict of {rule_name.yaml: content}
        index: if True, run roam index

    Returns the project path.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    for rel, content in source_files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)

    rules_dir = proj / ".roam" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for name, content in rule_files.items():
        (rules_dir / name).write_text(content, encoding="utf-8")

    git_init(proj)

    if index:
        out, rc = index_in_process(proj)
        assert rc == 0, f"roam index failed:\n{out}"

    return proj


# ===========================================================================
# 1. test_load_rules_from_dir
# ===========================================================================


class TestLoadRules:
    """Tests for load_rules()."""

    def test_load_rules_from_dir(self, tmp_path):
        """load_rules finds .yaml and .yml files and returns rule dicts."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "rule1.yaml").write_text('name: "Rule One"\nseverity: error\n', encoding="utf-8")
        (rules_dir / "rule2.yml").write_text('name: "Rule Two"\nseverity: warning\n', encoding="utf-8")
        # Non-YAML file should be ignored
        (rules_dir / "readme.txt").write_text("ignore me\n")

        rules = load_rules(rules_dir)
        assert len(rules) == 2
        names = {r["name"] for r in rules}
        assert "Rule One" in names
        assert "Rule Two" in names

    def test_load_empty_dir(self, tmp_path):
        """load_rules on empty directory returns an empty list."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rules = load_rules(rules_dir)
        assert rules == []

    def test_load_nonexistent_dir(self, tmp_path):
        """load_rules on missing directory returns an empty list."""
        rules = load_rules(tmp_path / "does_not_exist")
        assert rules == []


# ===========================================================================
# 2. test_evaluate_path_match_pass
# ===========================================================================


class TestPathMatch:
    """Tests for path_match rule evaluation."""

    def test_evaluate_path_match_pass(self, tmp_path):
        """When no edges match the from/to pattern, the rule passes."""
        source_files = {
            "src/service.py": ("def create_user():\n    return 42\n"),
            "src/utils.py": ("def helper():\n    return 1\n"),
        }
        rule_yaml = (
            'name: "No service calls DB"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/controllers/**"\n'
            "    kind: [function]\n"
            "  to:\n"
            '    file_glob: "**/db/**"\n'
            "    kind: [function]\n"
            "  max_distance: 1\n"
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"no_svc_db.yaml": rule_yaml})

        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            with open_db(readonly=True) as conn:
                results = evaluate_all(proj / ".roam" / "rules", conn)
        finally:
            os.chdir(old_cwd)

        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["violations"] == []

    def test_evaluate_path_match_fail(self, tmp_path):
        """When edges match from/to pattern, violations are reported."""
        source_files = {
            "controllers/user_ctrl.py": (
                "from db.queries import get_user\n\ndef handle_request():\n    return get_user()\n"
            ),
            "db/queries.py": ("def get_user():\n    return {'name': 'Alice'}\n"),
        }
        rule_yaml = (
            'name: "No controller calls DB"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/controllers/**"\n'
            "    kind: [function]\n"
            "  to:\n"
            '    file_glob: "**/db/**"\n'
            "    kind: [function]\n"
            "  max_distance: 1\n"
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"no_ctrl_db.yaml": rule_yaml})

        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            with open_db(readonly=True) as conn:
                results = evaluate_all(proj / ".roam" / "rules", conn)
        finally:
            os.chdir(old_cwd)

        assert len(results) == 1
        assert results[0]["passed"] is False
        assert len(results[0]["violations"]) > 0
        # Verify the violation mentions the source symbol
        v = results[0]["violations"][0]
        assert "handle_request" in v["symbol"] or "handle_request" in v.get("reason", "")


# ===========================================================================
# 3. test_evaluate_symbol_match
# ===========================================================================


class TestSymbolMatch:
    """Tests for symbol_match rule evaluation."""

    def test_evaluate_symbol_match(self, tmp_path):
        """symbol_match finds symbols matching kind/exported criteria."""
        source_files = {
            "src/app.py": ("def public_fn():\n    return 1\n\ndef another_fn():\n    return 2\n"),
        }
        # Match all exported functions (violations = symbols that match)
        rule_yaml = (
            'name: "Find all exported functions"\nseverity: info\nmatch:\n  kind: [function]\n  exported: true\n'
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"find_fns.yaml": rule_yaml})

        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            with open_db(readonly=True) as conn:
                results = evaluate_all(proj / ".roam" / "rules", conn)
        finally:
            os.chdir(old_cwd)

        assert len(results) == 1
        r = results[0]
        # There should be violations (symbols matching the criteria)
        assert r["passed"] is False
        names = {v["symbol"] for v in r["violations"]}
        assert "public_fn" in names
        assert "another_fn" in names


# ===========================================================================
# 4. test_exemptions_applied
# ===========================================================================


class TestExemptions:
    """Tests for rule exemptions."""

    def test_exemptions_applied(self, tmp_path):
        """Exempt symbols and files should be excluded from violations."""
        source_files = {
            "controllers/user_ctrl.py": (
                "from db.queries import get_user\n\ndef handle_request():\n    return get_user()\n"
            ),
            "controllers/admin_ctrl.py": (
                "from db.queries import get_user\n\ndef admin_request():\n    return get_user()\n"
            ),
            "db/queries.py": ("def get_user():\n    return {'name': 'Alice'}\n"),
        }
        # Exempt admin_request by symbol name
        rule_yaml = (
            'name: "No controller calls DB"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/controllers/**"\n'
            "    kind: [function]\n"
            "  to:\n"
            '    file_glob: "**/db/**"\n'
            "    kind: [function]\n"
            "  max_distance: 1\n"
            "exempt:\n"
            "  symbols: [admin_request]\n"
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"no_ctrl_db.yaml": rule_yaml})

        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            with open_db(readonly=True) as conn:
                results = evaluate_all(proj / ".roam" / "rules", conn)
        finally:
            os.chdir(old_cwd)

        assert len(results) == 1
        r = results[0]
        # admin_request should be exempt, but handle_request should be a violation
        violation_symbols = {v["symbol"] for v in r["violations"]}
        assert "admin_request" not in violation_symbols
        assert "handle_request" in violation_symbols


# ===========================================================================
# 5. test_severity_levels
# ===========================================================================


class TestSeverity:
    """Tests for severity level handling."""

    def test_severity_levels(self, tmp_path):
        """Rules with different severities are preserved in results."""
        source_files = {
            "src/app.py": "def hello():\n    return 1\n",
        }
        rule_error = (
            'name: "Error rule"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nope/**"\n'
            "  to:\n"
            '    file_glob: "**/nope/**"\n'
        )
        rule_warning = (
            'name: "Warning rule"\n'
            "severity: warning\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nope/**"\n'
            "  to:\n"
            '    file_glob: "**/nope/**"\n'
        )
        rule_info = (
            'name: "Info rule"\n'
            "severity: info\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nope/**"\n'
            "  to:\n"
            '    file_glob: "**/nope/**"\n'
        )
        proj = _make_project_with_rules(
            tmp_path,
            source_files,
            {
                "a_error.yaml": rule_error,
                "b_warning.yaml": rule_warning,
                "c_info.yaml": rule_info,
            },
        )

        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            with open_db(readonly=True) as conn:
                results = evaluate_all(proj / ".roam" / "rules", conn)
        finally:
            os.chdir(old_cwd)

        assert len(results) == 3
        severities = {r["name"]: r["severity"] for r in results}
        assert severities["Error rule"] == "error"
        assert severities["Warning rule"] == "warning"
        assert severities["Info rule"] == "info"


# ===========================================================================
# 6-10. CLI tests
# ===========================================================================


class TestRulesCLI:
    """Tests for the roam rules CLI command."""

    def test_rules_help(self):
        """roam rules --help should work."""
        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--help"])
        assert result.exit_code == 0
        assert "governance rules" in result.output.lower() or "rules" in result.output.lower()

    def test_rules_cli_runs(self, tmp_path, monkeypatch):
        """roam rules exits 0 when all rules pass."""
        source_files = {
            "src/app.py": "def hello():\n    return 1\n",
        }
        # A rule that won't match anything (passes)
        rule_yaml = (
            'name: "No X calls Y"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nonexistent/**"\n'
            "  to:\n"
            '    file_glob: "**/nonexistent/**"\n'
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"pass_rule.yaml": rule_yaml})
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_rules_verdict_line(self, tmp_path, monkeypatch):
        """roam rules text output starts with VERDICT:."""
        source_files = {
            "src/app.py": "def hello():\n    return 1\n",
        }
        rule_yaml = (
            'name: "Passes"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nonexistent/**"\n'
            "  to:\n"
            '    file_glob: "**/nonexistent/**"\n'
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"pass_rule.yaml": rule_yaml})
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_rules_json_envelope(self, tmp_path, monkeypatch):
        """roam --json rules returns a valid JSON envelope."""
        source_files = {
            "src/app.py": "def hello():\n    return 1\n",
        }
        rule_yaml = (
            'name: "Passes"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nonexistent/**"\n'
            "  to:\n"
            '    file_glob: "**/nonexistent/**"\n'
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"pass_rule.yaml": rule_yaml})
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "rules"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, "rules")
        assert "verdict" in data["summary"]
        assert "results" in data

    def test_rules_ci_mode(self, tmp_path, monkeypatch):
        """roam rules --ci exits 1 when error-severity violations exist."""
        source_files = {
            "controllers/ctrl.py": ("from db.queries import get_data\n\ndef handle():\n    return get_data()\n"),
            "db/queries.py": ("def get_data():\n    return []\n"),
        }
        rule_yaml = (
            'name: "No ctrl -> DB"\n'
            "severity: error\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/controllers/**"\n'
            "    kind: [function]\n"
            "  to:\n"
            '    file_glob: "**/db/**"\n'
            "    kind: [function]\n"
            "  max_distance: 1\n"
        )
        proj = _make_project_with_rules(tmp_path, source_files, {"no_ctrl_db.yaml": rule_yaml})
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--ci"], catch_exceptions=False)
        assert result.exit_code == 1


# ===========================================================================
# 11. test_rules_init
# ===========================================================================


class TestRulesInit:
    """Tests for --init flag."""

    def test_rules_init(self, tmp_path, monkeypatch):
        """roam rules --init creates example rule files."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text("def main():\n    pass\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--init"], catch_exceptions=False)
        assert result.exit_code == 0

        rules_dir = proj / ".roam" / "rules"
        assert rules_dir.is_dir()

        yaml_files = list(rules_dir.glob("*.yaml"))
        assert len(yaml_files) >= 2

        # Verify the files have content
        for yf in yaml_files:
            content = yf.read_text(encoding="utf-8")
            assert "name:" in content


# ===========================================================================
# 12. test_rules_no_rules_dir
# ===========================================================================


class TestNoRulesDir:
    """Tests for graceful handling when .roam/rules/ is missing."""

    def test_rules_no_rules_dir(self, tmp_path, monkeypatch):
        """roam rules exits 0 gracefully when no rules directory exists."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text("def main():\n    pass\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "no rules" in result.output.lower()


# ===========================================================================
# 13. test_invalid_yaml
# ===========================================================================


class TestInvalidYaml:
    """Tests for graceful error handling on bad YAML."""

    def test_invalid_yaml(self, tmp_path, monkeypatch):
        """Rules with invalid YAML should produce a parse error, not crash."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text("def main():\n    pass\n")
        git_init(proj)
        index_in_process(proj)

        rules_dir = proj / ".roam" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "bad.yaml").write_text(
            "{{{{invalid yaml content\n  broken: [[\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules"], catch_exceptions=False)
        # Should not crash; may report the bad rule as failed
        assert result.exit_code == 0 or result.exit_code == 1


# ===========================================================================
# 14. test_rules_ci_warning_passes
# ===========================================================================


class TestCIWarningPasses:
    """CI mode only fails on error severity, not warnings."""

    def test_rules_ci_warning_passes(self, tmp_path, monkeypatch):
        """roam rules --ci exits 0 when only warning-severity violations exist."""
        source_files = {
            "src/app.py": ("def public_fn():\n    return 1\n"),
        }
        # This symbol_match rule will find violations but severity=warning
        rule_yaml = 'name: "Warn about functions"\nseverity: warning\nmatch:\n  kind: [function]\n  exported: true\n'
        proj = _make_project_with_rules(tmp_path, source_files, {"warn.yaml": rule_yaml})
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--ci"], catch_exceptions=False)
        # warnings should NOT cause exit 1
        assert result.exit_code == 0


# ===========================================================================
# 15. test_custom_rules_dir
# ===========================================================================


class TestCustomRulesDir:
    """Tests for --rules-dir option."""

    def test_custom_rules_dir(self, tmp_path, monkeypatch):
        """roam rules --rules-dir <path> uses the specified directory."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text("def main():\n    pass\n")
        git_init(proj)
        index_in_process(proj)

        custom_dir = tmp_path / "custom_rules"
        custom_dir.mkdir()
        (custom_dir / "r1.yaml").write_text(
            'name: "Custom rule"\n'
            "severity: info\n"
            "match:\n"
            "  from:\n"
            '    file_glob: "**/nope/**"\n'
            "  to:\n"
            '    file_glob: "**/nope/**"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--rules-dir", str(custom_dir)], catch_exceptions=False)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "Custom rule" in result.output or "1 rules passed" in result.output
