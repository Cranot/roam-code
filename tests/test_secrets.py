"""Tests for the secrets command -- secret scanning with regex patterns."""

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
def secrets_project(project_factory):
    """Create a project with deliberately planted fake secrets for testing."""
    return project_factory({
        "app.py": (
            "def main():\n"
            "    pass\n"
        ),
        "config/settings.py": (
            "# AWS credentials\n"
            "AWS_ACCESS_KEY = 'AKIA" + "IOSFODNN7TESTDATA'\n"
            "AWS_SECRET = 'wJalrXUtnFEMI" + "K7MDENGbPxRfiCYRRANDOMVALUE'\n"
        ),
        "src/api_client.py": (
            "import os\n"
            "\n"
            "GITHUB_TOKEN = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'\n"
            "def fetch_data():\n"
            "    pass\n"
        ),
        "src/email_service.py": (
            "# Email config\n"
            "SENDGRID_KEY = 'SG." + "abcdefghijklmnopqrstuv.ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefg'\n"
            "password = 'supersecretpassword123'\n"
        ),
        "src/database.py": (
            "DB_URL = 'postgresql://admin:secretpass@db.internal.io:5432/myapp'\n"
            "def connect():\n"
            "    pass\n"
        ),
        "src/auth.py": (
            "PRIVATE_KEY = '''-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF\n"
            "-----END RSA PRIVATE KEY-----'''\n"
        ),
        "src/clean.py": (
            "# No secrets here\n"
            "def process_data(x):\n"
            "    return x * 2\n"
        ),
    })


@pytest.fixture
def placeholder_project(project_factory):
    """Create a project with placeholder/example values that should be skipped."""
    return project_factory({
        "app.py": "def main(): pass\n",
        "docs/example_config.py": (
            "# Example configuration - these are placeholder values\n"
            "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
            "PASSWORD = 'changeme_password123'\n"
        ),
        "src/config_template.py": (
            "# TODO: Replace with your real token\n"
            "TOKEN = 'ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'\n"
        ),
    })


@pytest.fixture
def clean_project(project_factory):
    """Create a project with no secrets at all."""
    return project_factory({
        "app.py": (
            "def main():\n"
            "    print('Hello, world!')\n"
        ),
        "utils.py": (
            "def add(a, b):\n"
            "    return a + b\n"
        ),
    })


@pytest.fixture
def binary_project(tmp_path):
    """Create a project with both source and binary files."""
    proj = tmp_path / "binproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Source with a secret
    (proj / "app.py").write_text(
        "SECRET = 'AKIAIOSFODNN7TESTDATAX'\n"
        "def main(): pass\n"
    )
    # Binary file that should be skipped
    (proj / "data.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (proj / "archive.zip").write_bytes(b"PK" + b"\x00" * 100)

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke(args, cwd, json_mode=False, sarif_mode=False):
    """Invoke roam CLI in-process."""
    from roam.cli import cli
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    if sarif_mode:
        full_args.append("--sarif")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# 1. Pattern detection tests
# ===========================================================================

class TestPatternDetection:
    """Test that each secret pattern category is detected."""

    def test_aws_access_key_detected(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        names = [f["pattern"] for f in data.get("findings", [])]
        assert "AWS Access Key" in names

    def test_github_token_detected(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        names = [f["pattern"] for f in data.get("findings", [])]
        assert "GitHub Personal Access Token (classic)" in names

    def test_sendgrid_key_detected(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        names = [f["pattern"] for f in data.get("findings", [])]
        assert "SendGrid API Key" in names

    def test_password_detected(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        names = [f["pattern"] for f in data.get("findings", [])]
        assert "Generic Password Assignment" in names

    def test_database_connection_string_detected(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        names = [f["pattern"] for f in data.get("findings", [])]
        assert "Database Connection String" in names

    def test_private_key_detected(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        names = [f["pattern"] for f in data.get("findings", [])]
        assert "Private Key" in names

    def test_clean_project_no_findings(self, clean_project):
        result = _invoke(["secrets"], clean_project, json_mode=True)
        data = json.loads(result.output)
        assert data["summary"]["total_findings"] == 0
        assert len(data.get("findings", [])) == 0


# ===========================================================================
# 2. Masking tests -- never shows full secret
# ===========================================================================

class TestSecretMasking:
    """Ensure matched text is always masked and never shows full secrets."""

    def test_masked_output_contains_ellipsis(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        for finding in data.get("findings", []):
            matched = finding["matched_text"]
            # All masked values should contain "..."
            assert "..." in matched, f"Unmasked secret in output: {matched}"

    def test_mask_short_value(self):
        """Short values should show first 4 chars + ellipsis."""
        from roam.commands.cmd_secrets import mask_secret
        result = mask_secret("short12")
        assert result == "shor..."
        assert "short12" not in result

    def test_mask_long_value(self):
        """Long values should show first 4 + ... + last 4."""
        from roam.commands.cmd_secrets import mask_secret
        result = mask_secret("AKIAIOSFODNN7EXAMPLE")
        assert result.startswith("AKIA")
        assert result.endswith("MPLE")
        assert "..." in result
        # Must not contain the full key
        assert result != "AKIAIOSFODNN7EXAMPLE"

    def test_mask_medium_value(self):
        """Medium-length values should be partially masked."""
        from roam.commands.cmd_secrets import mask_secret
        result = mask_secret("abcdefghij")  # 10 chars
        assert result.startswith("abcd")
        assert "..." in result


# ===========================================================================
# 3. Severity filter tests
# ===========================================================================

class TestSeverityFilter:
    """Test the --severity filter option."""

    def test_filter_high_only(self, secrets_project):
        result = _invoke(["secrets", "--severity", "high"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        for finding in data.get("findings", []):
            assert finding["severity"] == "high"

    def test_filter_medium_includes_high(self, secrets_project):
        result = _invoke(["secrets", "--severity", "medium"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        severities = {f["severity"] for f in data.get("findings", [])}
        # Medium filter means medium and above (high)
        assert severities <= {"high", "medium"}

    def test_filter_all_includes_everything(self, secrets_project):
        result = _invoke(["secrets", "--severity", "all"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        assert data["summary"]["total_findings"] > 0


# ===========================================================================
# 4. --fail-on-found exit code tests
# ===========================================================================

class TestFailOnFound:
    """Test the --fail-on-found CI gate flag."""

    def test_fail_on_found_with_secrets(self, secrets_project):
        """Should exit with code 5 when secrets are found."""
        from roam.cli import cli
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(secrets_project))
            result = runner.invoke(cli, ["secrets", "--fail-on-found"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 5

    def test_fail_on_found_without_secrets(self, clean_project):
        """Should exit with code 0 when no secrets are found."""
        result = _invoke(["secrets", "--fail-on-found"], clean_project)
        assert result.exit_code == 0

    def test_fail_on_found_json_mode(self, secrets_project):
        """Should output JSON before failing."""
        from roam.cli import cli
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(secrets_project))
            result = runner.invoke(cli, ["--json", "secrets", "--fail-on-found"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 5
        # Should still have produced JSON output before the error
        assert "{" in result.output


# ===========================================================================
# 5. Binary file skipping tests
# ===========================================================================

class TestBinarySkipping:
    """Test that binary files are correctly skipped."""

    def test_binary_files_skipped(self, binary_project):
        result = _invoke(["secrets"], binary_project, json_mode=True)
        data = json.loads(result.output)
        # Should find the secret in app.py but not try to scan .png or .zip
        files_found = {f["file"] for f in data.get("findings", [])}
        assert not any(f.endswith(".png") for f in files_found)
        assert not any(f.endswith(".zip") for f in files_found)

    def test_is_binary_function(self):
        from roam.commands.cmd_secrets import _is_binary
        assert _is_binary("image.png")
        assert _is_binary("archive.zip")
        assert _is_binary("lib.dll")
        assert not _is_binary("app.py")
        assert not _is_binary("config.yaml")
        assert not _is_binary("main.go")


# ===========================================================================
# 6. Placeholder / example value skipping tests
# ===========================================================================

class TestPlaceholderSkipping:
    """Test that lines with placeholder indicators are skipped."""

    def test_placeholder_lines_skipped(self, placeholder_project):
        result = _invoke(["secrets"], placeholder_project, json_mode=True)
        data = json.loads(result.output)
        # Lines with "example", "changeme", "xxx", "TODO" should be skipped
        assert data["summary"]["total_findings"] == 0

    def test_is_placeholder_line_function(self):
        from roam.commands.cmd_secrets import _is_placeholder_line
        assert _is_placeholder_line("# This is an example config")
        assert _is_placeholder_line("TOKEN = 'changeme'")
        assert _is_placeholder_line("key = 'xxxxxxxx'")
        assert _is_placeholder_line("# TODO: replace with real key")
        assert _is_placeholder_line("# placeholder value here")
        assert not _is_placeholder_line("REAL_KEY = 'AKIA" + "IOSFODNN7REALKEY'")


# ===========================================================================
# 7. JSON output format tests
# ===========================================================================

class TestJsonOutput:
    """Test the JSON envelope structure."""

    def test_json_envelope_structure(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        assert data["command"] == "secrets"
        assert "summary" in data
        assert "findings" in data
        assert "version" in data

    def test_json_summary_fields(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert "verdict" in summary
        assert "total_findings" in summary
        assert "files_affected" in summary
        assert "by_severity" in summary
        assert isinstance(summary["by_severity"], dict)

    def test_json_finding_fields(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        findings = data.get("findings", [])
        assert len(findings) > 0
        f = findings[0]
        assert "file" in f
        assert "line" in f
        assert "severity" in f
        assert "pattern" in f
        assert "matched_text" in f

    def test_json_findings_sorted_by_severity(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, json_mode=True)
        data = json.loads(result.output)
        findings = data.get("findings", [])
        severity_order = {"high": 3, "medium": 2, "low": 1}
        severities = [severity_order.get(f["severity"], 0) for f in findings]
        # Should be sorted descending (high first)
        assert severities == sorted(severities, reverse=True)


# ===========================================================================
# 8. Text output tests
# ===========================================================================

class TestTextOutput:
    """Test the plain-text output format."""

    def test_text_output_has_verdict(self, secrets_project):
        result = _invoke(["secrets"], secrets_project)
        assert "VERDICT:" in result.output

    def test_text_output_has_recommendations(self, secrets_project):
        result = _invoke(["secrets"], secrets_project)
        assert "Recommendations:" in result.output
        assert "environment variables" in result.output

    def test_text_clean_project(self, clean_project):
        result = _invoke(["secrets"], clean_project)
        assert "No secrets found" in result.output

    def test_text_output_shows_file_locations(self, secrets_project):
        result = _invoke(["secrets"], secrets_project)
        # Should show file:line format
        assert ":" in result.output
        assert "HIGH" in result.output or "MEDIUM" in result.output


# ===========================================================================
# 9. SARIF output tests
# ===========================================================================

class TestSarifOutput:
    """Test the SARIF 2.1.0 output format."""

    def test_sarif_structure(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, sarif_mode=True)
        data = json.loads(result.output)
        assert data["version"] == "2.1.0"
        assert "$schema" in data
        assert "runs" in data
        assert len(data["runs"]) == 1

    def test_sarif_has_rules(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, sarif_mode=True)
        data = json.loads(result.output)
        run = data["runs"][0]
        rules = run["tool"]["driver"]["rules"]
        assert len(rules) > 0
        for rule in rules:
            assert rule["id"].startswith("secrets/")

    def test_sarif_has_results(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, sarif_mode=True)
        data = json.loads(result.output)
        run = data["runs"][0]
        results = run["results"]
        assert len(results) > 0
        for r in results:
            assert "ruleId" in r
            assert "level" in r
            assert "message" in r
            assert "locations" in r

    def test_sarif_locations_have_line_numbers(self, secrets_project):
        result = _invoke(["secrets"], secrets_project, sarif_mode=True)
        data = json.loads(result.output)
        run = data["runs"][0]
        results = run["results"]
        for r in results:
            for loc in r["locations"]:
                phys = loc["physicalLocation"]
                assert "artifactLocation" in phys
                assert "region" in phys
                assert "startLine" in phys["region"]


# ===========================================================================
# 10. Scan function unit tests
# ===========================================================================

class TestScanFunctions:
    """Test the lower-level scan functions directly."""

    def test_scan_file_finds_aws_key(self, tmp_path):
        """scan_file should find AWS access keys."""
        f = tmp_path / "test.py"
        f.write_text("KEY = 'AKIAIOSFODNN7TESTDATA'\n")
        from roam.commands.cmd_secrets import scan_file
        findings = scan_file(str(f))
        assert len(findings) >= 1
        assert any(f["pattern_name"] == "AWS Access Key" for f in findings)

    def test_scan_file_finds_jwt(self, tmp_path):
        """scan_file should find JWT tokens."""
        f = tmp_path / "test.py"
        # Minimal valid-looking JWT (3 base64url segments)
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        f.write_text(f"TOKEN = '{jwt}'\n")
        from roam.commands.cmd_secrets import scan_file
        findings = scan_file(str(f))
        assert any(f["pattern_name"] == "JWT Token" for f in findings)

    def test_scan_file_finds_private_key(self, tmp_path):
        """scan_file should detect private key headers."""
        f = tmp_path / "key.pem"
        f.write_text("-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n")
        from roam.commands.cmd_secrets import scan_file
        findings = scan_file(str(f))
        assert any(f["pattern_name"] == "Private Key" for f in findings)

    def test_scan_file_skips_placeholder(self, tmp_path):
        """Lines with placeholder words should be skipped."""
        f = tmp_path / "test.py"
        f.write_text("# example: AKIAIOSFODNN7EXAMPLE\n")
        from roam.commands.cmd_secrets import scan_file
        findings = scan_file(str(f))
        assert len(findings) == 0

    def test_scan_file_severity_filter(self, tmp_path):
        """min_severity should filter out lower severity findings."""
        f = tmp_path / "test.py"
        f.write_text("password = 'mysecretpassword'\n")
        from roam.commands.cmd_secrets import scan_file
        all_findings = scan_file(str(f), min_severity="all")
        high_findings = scan_file(str(f), min_severity="high")
        # Password is medium severity, should be excluded with high filter
        assert len(all_findings) > 0
        assert len(high_findings) == 0

    def test_scan_file_nonexistent(self):
        """Scanning a nonexistent file should return empty list, not crash."""
        from roam.commands.cmd_secrets import scan_file
        findings = scan_file("/nonexistent/path/file.py")
        assert findings == []


# ===========================================================================
# 11. Skip directory tests
# ===========================================================================

class TestDirectorySkipping:
    """Test that certain directories are skipped."""

    def test_in_skip_dir_function(self):
        from roam.commands.cmd_secrets import _in_skip_dir
        assert _in_skip_dir("node_modules/package/index.js")
        assert _in_skip_dir(".git/config")
        assert _in_skip_dir("vendor/lib/module.go")
        assert _in_skip_dir("path/to/__pycache__/module.pyc")
        assert not _in_skip_dir("src/app.py")
        assert not _in_skip_dir("lib/utils.js")


# ===========================================================================
# 12. Pattern compilation tests
# ===========================================================================

class TestPatternCompilation:
    """Test that patterns are compiled correctly at module level."""

    def test_patterns_compiled(self):
        from roam.commands.cmd_secrets import _COMPILED_PATTERNS
        assert len(_COMPILED_PATTERNS) >= 24
        for pat in _COMPILED_PATTERNS:
            assert "name" in pat
            assert "regex" in pat
            assert "severity" in pat
            assert pat["severity"] in ("high", "medium", "low")

    def test_all_patterns_are_valid_regex(self):
        from roam.commands.cmd_secrets import _COMPILED_PATTERNS
        import re
        for pat in _COMPILED_PATTERNS:
            # Compiled regex should be a Pattern object
            assert isinstance(pat["regex"], re.Pattern)


# ===========================================================================
# 13. Individual pattern verification tests
# ===========================================================================

class TestIndividualPatterns:
    """Test specific patterns match expected test values."""

    def _match(self, pattern_name, text):
        """Check if a pattern matches text."""
        from roam.commands.cmd_secrets import _COMPILED_PATTERNS
        for pat in _COMPILED_PATTERNS:
            if pat["name"] == pattern_name:
                return pat["regex"].search(text) is not None
        raise ValueError(f"Pattern '{pattern_name}' not found")

    def test_aws_access_key(self):
        assert self._match("AWS Access Key", "AKIAIOSFODNN7EXAMPLE")

    def test_github_token(self):
        assert self._match("GitHub Token", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl")

    def test_gitlab_token(self):
        assert self._match("GitLab Token", "glpat-abcdefghij0123456789")

    def test_stripe_secret_key(self):
        assert self._match("Stripe Secret Key", "sk_live_" + "abcdefghijklmnopqrstuv0123")

    def test_stripe_publishable_key(self):
        assert self._match("Stripe Publishable Key", "pk_live_abcdefghijklmnopqrstuv0123")

    def test_google_api_key(self):
        assert self._match("Google API Key", "AIzaSyA-abcdefghijklmnopqrstuvwxyz12345")

    def test_npm_token(self):
        assert self._match("NPM Token", "npm_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")

    def test_slack_bot_token(self):
        assert self._match("Slack Bot Token", "xoxb-" + "1234567890-1234567890123-abcdefghijklmnopqrstuvwx")

    def test_private_key_rsa(self):
        assert self._match("Private Key", "-----BEGIN RSA PRIVATE KEY-----")

    def test_private_key_ec(self):
        assert self._match("Private Key", "-----BEGIN EC PRIVATE KEY-----")

    def test_private_key_openssh(self):
        assert self._match("Private Key", "-----BEGIN OPENSSH PRIVATE KEY-----")

    def test_generic_password(self):
        assert self._match("Generic Password Assignment", "password = 'mysecretvalue123'")

    def test_generic_secret(self):
        assert self._match("Generic Secret Assignment", "api_key = 'abcdef12345678'")

    def test_bearer_token(self):
        assert self._match("Generic Bearer Token", "Bearer eyJhbGciOiJIUzI1NiJ9")

    def test_database_url_postgres(self):
        assert self._match("Database Connection String", "postgresql://user:pass@host:5432/db")

    def test_database_url_mysql(self):
        assert self._match("Database Connection String", "mysql://user:pass@localhost/mydb")

    def test_database_url_mongodb(self):
        assert self._match("Database Connection String", "mongodb://user:pass@cluster.example.com/db")

    def test_no_false_positive_on_normal_code(self):
        """Normal code should not trigger false positives."""
        from roam.commands.cmd_secrets import scan_file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def calculate(x, y):\n")
            f.write("    return x + y\n")
            f.write("result = calculate(1, 2)\n")
            f.write("print(result)\n")
            f.flush()
            findings = scan_file(f.name)
        os.unlink(f.name)
        assert len(findings) == 0
