"""Tests for secrets command v2 features: test suppression, env-var detection,
Shannon entropy, and remediation suggestions."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, git_init


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_file_project(project_factory):
    """Project with secrets in test files that should be suppressed."""
    return project_factory({
        "app.py": "def main(): pass\n",
        "tests/test_auth.py": (
            "# Test credentials\n"
            "TEST_KEY = 'AKIAIOSFODNN7TESTDATA'\n"
            "def test_login(): pass\n"
        ),
        "test/test_api.py": (
            "API_TOKEN = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'\n"
        ),
        "__tests__/auth.test.js": (
            "const secret = 'sk_live_" + "abcdefghijklmnopqrstuv0123';\n"
        ),
        "spec/api_spec.rb": (
            "STRIPE_KEY = 'sk_live_" + "abcdefghijklmnopqrstuv0123'\n"
        ),
        "fixtures/data.py": (
            "DB_URL = 'postgresql://admin:pass1234@db.host:5432/myapp'\n"
        ),
        "docs/setup.md": (
            "Set your key: `AKIAIOSFODNN7DOCEXAMP`\n"
        ),
        "examples/config.py": (
            "TOKEN = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'\n"
        ),
    })


@pytest.fixture
def env_var_project(project_factory):
    """Project with env-var lookups that should be suppressed."""
    return project_factory({
        "app.py": "def main(): pass\n",
        "config/settings.py": (
            "import os\n"
            "SECRET_KEY = os.environ.get('SECRET_KEY')\n"
            "DB_PASSWORD = os.environ['DB_PASSWORD']\n"
            "API_KEY = config.get('API_KEY')\n"
        ),
        "config/node_config.js": (
            "const token = process.env.GITHUB_TOKEN;\n"
            "const secret = process.env.SECRET_KEY;\n"
        ),
        "config/ruby_config.rb": (
            "password = ENV['DATABASE_PASSWORD']\n"
        ),
        "config/go_config.go": (
            'key := settings.Get("api_key")\n'
        ),
        "config/python_env.py": (
            "import os\n"
            "token = os.getenv('AUTH_TOKEN')\n"
        ),
    })


@pytest.fixture
def entropy_project(project_factory):
    """Project with high and low entropy strings."""
    return project_factory({
        "app.py": "def main(): pass\n",
        "src/high_entropy.py": (
            "# This has high entropy (random-looking)\n"
            "secret = 'aB3$kL9mNpQrStUvWxYz1234'\n"
        ),
        "src/low_entropy.py": (
            "# This has low entropy (repetitive)\n"
            "key = 'aaaaaaaaaaaaaaaaaaaaaaaaa'\n"
        ),
    })


@pytest.fixture
def remediation_project(project_factory):
    """Project with various secret types to test remediation suggestions."""
    return project_factory({
        "app.py": "def main(): pass\n",
        "config/aws.py": (
            "AWS_KEY = 'AKIAIOSFODNN7TESTDATA'\n"
        ),
        "config/db.py": (
            "DB_URL = 'postgresql://admin:secretpass@db.internal.io:5432/myapp'\n"
        ),
        "config/auth.py": (
            "password = 'supersecretpassword123'\n"
        ),
    })


@pytest.fixture
def mixed_project(project_factory):
    """Project with secrets in both source and test files."""
    return project_factory({
        "app.py": "def main(): pass\n",
        "src/config.py": (
            "REAL_KEY = 'AKIAIOSFODNN7REALDATA'\n"
        ),
        "tests/test_config.py": (
            "TEST_KEY = 'AKIAIOSFODNN7TESTDATA'\n"
        ),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke(args, cwd, json_mode=False):
    """Invoke roam CLI in-process."""
    from roam.cli import cli
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# 1. Test file suppression
# ===========================================================================

class TestTestFileSuppression:
    """Test that files in test/fixture/docs directories are suppressed."""

    def test_tests_dir_suppressed(self, test_file_project):
        """Files under tests/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any("tests/" in f for f in files)

    def test_test_dir_suppressed(self, test_file_project):
        """Files under test/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any("test/" in f for f in files)

    def test_dunder_tests_dir_suppressed(self, test_file_project):
        """Files under __tests__/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any("__tests__/" in f for f in files)

    def test_spec_dir_suppressed(self, test_file_project):
        """Files under spec/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any("spec/" in f for f in files)

    def test_fixtures_dir_suppressed(self, test_file_project):
        """Files under fixtures/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any("fixtures/" in f for f in files)

    def test_docs_md_suppressed(self, test_file_project):
        """Markdown files under docs/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any(f.endswith(".md") for f in files)

    def test_examples_dir_suppressed(self, test_file_project):
        """Files under examples/ should be suppressed by default."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert not any("examples/" in f for f in files)

    def test_no_findings_in_test_only_project(self, test_file_project):
        """A project with secrets only in test files should have zero findings."""
        result = _invoke(["secrets"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        assert data["summary"]["total_findings"] == 0


# ===========================================================================
# 2. --include-tests flag
# ===========================================================================

class TestIncludeTestsFlag:
    """Test that --include-tests overrides suppression."""

    def test_include_tests_finds_test_secrets(self, test_file_project):
        """With --include-tests, test file secrets should be reported."""
        result = _invoke(["secrets", "--include-tests"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        assert data["summary"]["total_findings"] > 0

    def test_include_tests_reports_test_file_paths(self, test_file_project):
        """With --include-tests, test file paths should appear in findings."""
        result = _invoke(["secrets", "--include-tests"], test_file_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        test_files = [f for f in files if "test" in f.lower() or "spec" in f.lower()
                      or "fixtures" in f.lower() or "docs" in f.lower()
                      or "examples" in f.lower()]
        assert len(test_files) > 0

    def test_mixed_project_default_excludes_tests(self, mixed_project):
        """Default: only source secrets, not test secrets."""
        result = _invoke(["secrets"], mixed_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert any("src/" in f for f in files)
        assert not any("tests/" in f for f in files)

    def test_mixed_project_include_tests_finds_all(self, mixed_project):
        """With --include-tests: both source and test secrets."""
        result = _invoke(["secrets", "--include-tests"], mixed_project, json_mode=True)
        data = json.loads(result.output)
        files = {f["file"] for f in data.get("findings", [])}
        assert any("src/" in f for f in files)
        assert any("tests/" in f for f in files)


# ===========================================================================
# 3. Environment variable suppression
# ===========================================================================

class TestEnvVarSuppression:
    """Test that env-var lookups are not flagged as hardcoded secrets."""

    def test_os_environ_suppressed(self, env_var_project):
        """Lines with os.environ should not produce findings."""
        result = _invoke(["secrets", "--include-tests"], env_var_project, json_mode=True)
        data = json.loads(result.output)
        # None of the env-var files should produce findings
        assert data["summary"]["total_findings"] == 0

    def test_env_var_detection_unit(self):
        """Unit test: _is_env_var_line detects env-var patterns."""
        from roam.commands.cmd_secrets import _is_env_var_line
        assert _is_env_var_line("SECRET = os.environ.get('SECRET')")
        assert _is_env_var_line("token = process.env.TOKEN")
        assert _is_env_var_line("key = config.get('API_KEY')")
        assert _is_env_var_line("pwd = os.getenv('PASSWORD')")
        assert _is_env_var_line("secret = ENV['SECRET']")
        assert _is_env_var_line("val = settings.get('key')")
        assert _is_env_var_line("x = Config.get('x')")
        assert not _is_env_var_line("SECRET = 'hardcoded_value_here'")
        assert not _is_env_var_line("password = 'mysecretpassword'")

    def test_scan_file_skips_env_lines(self, tmp_path):
        """scan_file should skip lines with env-var patterns."""
        from roam.commands.cmd_secrets import scan_file
        f = tmp_path / "config.py"
        f.write_text(
            "import os\n"
            "password = os.environ['DB_PASSWORD']\n"
            "token = os.getenv('TOKEN')\n"
        )
        findings = scan_file(str(f))
        assert len(findings) == 0


# ===========================================================================
# 4. Shannon entropy detection
# ===========================================================================

class TestShannonEntropy:
    """Test the Shannon entropy detector."""

    def test_entropy_function_high(self):
        """High entropy strings (random-looking) should exceed threshold."""
        from roam.commands.cmd_secrets import _shannon_entropy
        # Random-looking string has high entropy
        ent = _shannon_entropy("aB3kL9mNpQrStUvWxYz1234")
        assert ent > 4.0

    def test_entropy_function_low(self):
        """Low entropy strings (repetitive) should be below threshold."""
        from roam.commands.cmd_secrets import _shannon_entropy
        # Repetitive string has low entropy
        ent = _shannon_entropy("aaaaaaaaaaaaaaaaaaaaa")
        assert ent < 1.0

    def test_entropy_function_empty(self):
        """Empty string should return 0."""
        from roam.commands.cmd_secrets import _shannon_entropy
        assert _shannon_entropy("") == 0.0

    def test_entropy_threshold_filtering(self, tmp_path):
        """High Entropy String pattern should only report above threshold."""
        from roam.commands.cmd_secrets import scan_file, _shannon_entropy
        f = tmp_path / "config.py"
        # Low entropy: repetitive pattern (should be filtered out)
        f.write_text("secret = 'aaaaaabbbbbbccccccdddddd'\n")
        findings = scan_file(str(f))
        entropy_findings = [x for x in findings if x["pattern_name"] == "High Entropy String"]
        assert len(entropy_findings) == 0

    def test_high_entropy_detected(self, tmp_path):
        """A truly random-looking secret-assigned string should be detected."""
        from roam.commands.cmd_secrets import scan_file
        f = tmp_path / "config.py"
        # High entropy: random-looking chars in key assignment context
        f.write_text("auth = 'xK9mBz3LpQr7WvNfTy2Gs5Hj'\n")
        findings = scan_file(str(f))
        entropy_findings = [x for x in findings if x["pattern_name"] == "High Entropy String"]
        assert len(entropy_findings) == 1


# ===========================================================================
# 5. Remediation suggestions
# ===========================================================================

class TestRemediation:
    """Test that remediation suggestions are present in output."""

    def test_remediation_in_json_findings(self, remediation_project):
        """JSON findings should include a remediation field."""
        result = _invoke(["secrets"], remediation_project, json_mode=True)
        data = json.loads(result.output)
        for finding in data.get("findings", []):
            assert "remediation" in finding
            assert len(finding["remediation"]) > 0

    def test_remediation_aws_specific(self, remediation_project):
        """AWS findings should have AWS-specific remediation."""
        result = _invoke(["secrets"], remediation_project, json_mode=True)
        data = json.loads(result.output)
        aws_findings = [f for f in data.get("findings", [])
                        if "AWS" in f["pattern"]]
        assert len(aws_findings) > 0
        for f in aws_findings:
            assert "AWS" in f["remediation"] or "os.environ" in f["remediation"]

    def test_remediation_db_specific(self, remediation_project):
        """Database findings should have DB-specific remediation."""
        result = _invoke(["secrets"], remediation_project, json_mode=True)
        data = json.loads(result.output)
        db_findings = [f for f in data.get("findings", [])
                       if "Database" in f["pattern"]]
        assert len(db_findings) > 0
        for f in db_findings:
            assert "DSN" in f["remediation"] or "env" in f["remediation"].lower()

    def test_remediation_in_text_output(self, remediation_project):
        """Text output should show per-pattern remediation."""
        result = _invoke(["secrets"], remediation_project)
        assert "Recommendations:" in result.output
        # Should have specific pattern-based recommendations
        assert "AWS" in result.output or "environment variable" in result.output

    def test_remediation_dict_coverage(self):
        """Every pattern should have a remediation entry."""
        from roam.commands.cmd_secrets import _SECRET_PATTERN_DEFS, _REMEDIATION
        for pdef in _SECRET_PATTERN_DEFS:
            assert pdef["name"] in _REMEDIATION, (
                f"Missing remediation for pattern: {pdef['name']}"
            )

    def test_scan_file_includes_remediation(self, tmp_path):
        """scan_file findings should include remediation field."""
        from roam.commands.cmd_secrets import scan_file
        f = tmp_path / "test.py"
        f.write_text("KEY = 'AKIAIOSFODNN7TESTDATA'\n")
        findings = scan_file(str(f))
        assert len(findings) >= 1
        for finding in findings:
            assert "remediation" in finding
            assert len(finding["remediation"]) > 0


# ===========================================================================
# 6. _is_test_or_doc_path unit tests
# ===========================================================================

class TestIsTestOrDocPath:
    """Unit tests for _is_test_or_doc_path."""

    def test_tests_directory(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("tests/test_auth.py")
        assert _is_test_or_doc_path("test/test_api.py")

    def test_dunder_tests(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("__tests__/auth.test.js")

    def test_spec_directory(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("spec/api_spec.rb")

    def test_fixtures_directory(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("fixtures/data.json")

    def test_docs_directory(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("docs/guide.md")

    def test_examples_directory(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("examples/config.py")

    def test_test_prefix_file(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("test_config.py")

    def test_test_suffix_file(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("auth_test.py")

    def test_markdown_files(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("README.md")
        assert _is_test_or_doc_path("docs/setup.md")

    def test_rst_files(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("docs/index.rst")

    def test_txt_files(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("notes.txt")

    def test_source_files_not_suppressed(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert not _is_test_or_doc_path("src/config.py")
        assert not _is_test_or_doc_path("lib/auth.js")
        assert not _is_test_or_doc_path("app.py")
        assert not _is_test_or_doc_path("main.go")

    def test_nested_test_directory(self):
        from roam.commands.cmd_secrets import _is_test_or_doc_path
        assert _is_test_or_doc_path("src/tests/integration/test_api.py")


# ===========================================================================
# 7. Backward compatibility
# ===========================================================================

class TestBackwardCompatibility:
    """Ensure existing functionality still works with new features."""

    def test_existing_patterns_still_work(self, tmp_path):
        """All original patterns should still detect secrets."""
        from roam.commands.cmd_secrets import scan_file
        f = tmp_path / "config.py"
        f.write_text(
            "AWS_KEY = 'AKIAIOSFODNN7TESTDATA'\n"
            "password = 'supersecretpassword123'\n"
        )
        findings = scan_file(str(f))
        patterns = {x["pattern_name"] for x in findings}
        assert "AWS Access Key" in patterns
        assert "Generic Password Assignment" in patterns

    def test_placeholder_skipping_still_works(self, tmp_path):
        """Placeholder lines should still be skipped."""
        from roam.commands.cmd_secrets import scan_file
        f = tmp_path / "config.py"
        f.write_text("# example: AKIAIOSFODNN7EXAMPLE\n")
        findings = scan_file(str(f))
        assert len(findings) == 0

    def test_severity_filter_still_works(self, tmp_path):
        """--severity flag should still filter correctly."""
        from roam.commands.cmd_secrets import scan_file
        f = tmp_path / "config.py"
        f.write_text("password = 'mysecretpassword'\n")
        all_findings = scan_file(str(f), min_severity="all")
        high_findings = scan_file(str(f), min_severity="high")
        assert len(all_findings) > 0
        assert len(high_findings) == 0

    def test_masking_still_works(self):
        """Secret masking should still function correctly."""
        from roam.commands.cmd_secrets import mask_secret
        result = mask_secret("AKIAIOSFODNN7EXAMPLE")
        assert "..." in result
        assert result != "AKIAIOSFODNN7EXAMPLE"

    def test_pattern_count_includes_new(self):
        """Pattern list should include the new High Entropy String pattern."""
        from roam.commands.cmd_secrets import _COMPILED_PATTERNS
        names = {p["name"] for p in _COMPILED_PATTERNS}
        # Original patterns
        assert "AWS Access Key" in names
        assert "GitHub Token" in names
        assert "Private Key" in names
        # New pattern
        assert "High Entropy String" in names
        # At least the original 24 + 1 new = 25
        assert len(_COMPILED_PATTERNS) >= 25
