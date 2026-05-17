"""Tests for the --sarif global CLI flag.

Validates that `roam --sarif <command>` produces valid SARIF 2.1.0 JSON
for dead, health, complexity, and rules commands.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import invoke_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def invoke_sarif(runner, args, cwd):
    """Invoke a roam CLI command with --sarif flag."""
    from roam.cli import cli

    full_args = ["--sarif"] + args
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def parse_sarif(result):
    """Parse SARIF JSON from a CliRunner result."""
    assert result.exit_code == 0, f"Command failed (exit {result.exit_code}):\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid SARIF JSON: {e}\nOutput was:\n{result.output[:500]}")


def assert_valid_sarif(data):
    """Assert basic SARIF 2.1.0 structure."""
    assert "$schema" in data, "Missing $schema in SARIF output"
    assert data["version"] == "2.1.0", f"Expected version 2.1.0, got {data['version']}"
    assert "runs" in data, "Missing runs array in SARIF output"
    assert isinstance(data["runs"], list), "runs must be an array"
    assert len(data["runs"]) >= 1, "runs must have at least one entry"
    run = data["runs"][0]
    assert "tool" in run, "Missing tool in run"
    assert "driver" in run["tool"], "Missing driver in tool"
    assert "name" in run["tool"]["driver"], "Missing name in driver"
    assert "results" in run, "Missing results in run"
    assert isinstance(run["results"], list), "results must be an array"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed_proj(project_factory):
    """Create a small indexed Python project for SARIF tests."""
    return project_factory(
        {
            "src/models.py": (
                "class User:\n"
                '    """A user model."""\n'
                "    def __init__(self, name, email):\n"
                "        self.name = name\n"
                "        self.email = email\n"
                "\n"
                "    def display_name(self):\n"
                "        return self.name.title()\n"
            ),
            "src/service.py": (
                "from models import User\n"
                "\n"
                "def create_user(name, email):\n"
                "    user = User(name, email)\n"
                "    return user\n"
                "\n"
                "def unused_helper():\n"
                '    """This function is never called."""\n'
                "    return 42\n"
            ),
            "src/utils.py": (
                'def format_name(first, last):\n    return f"{first} {last}"\n\nUNUSED_CONSTANT = "never_referenced"\n'
            ),
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSarifFlagDead:
    """Test --sarif flag with the dead command."""

    def test_sarif_flag_dead(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        # Should find some dead code results
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"


class TestSarifFlagHealth:
    """Test --sarif flag with the health command."""

    def test_sarif_flag_health(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["health"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"


class TestSarifFlagComplexity:
    """Test --sarif flag with the complexity command."""

    def test_sarif_flag_complexity(self, indexed_proj):
        runner = CliRunner()
        # Use threshold 0 to ensure all symbols are included
        result = invoke_sarif(runner, ["complexity", "--threshold", "0"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"


class TestSarifFlagRules:
    """Test --sarif flag with the rules command."""

    def test_sarif_flag_rules(self, indexed_proj):
        runner = CliRunner()
        # Rules without a .roam/rules directory should still produce valid SARIF
        result = invoke_sarif(runner, ["rules"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"
        # No rules means 0 results
        assert len(run["results"]) == 0


class TestSarifStructure:
    """Test SARIF output structural validity."""

    def test_sarif_output_has_schema(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert "$schema" in data
        assert "sarif" in data["$schema"].lower()

    def test_sarif_output_has_runs(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert "runs" in data
        assert isinstance(data["runs"], list)
        assert len(data["runs"]) == 1

    def test_sarif_output_has_tool(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["health"], cwd=indexed_proj)
        data = parse_sarif(result)
        run = data["runs"][0]
        assert "tool" in run
        driver = run["tool"]["driver"]
        assert driver["name"] == "roam-code"
        assert "version" in driver
        assert "rules" in driver


class TestSarifHelp:
    """Test --sarif is a recognized CLI flag."""

    def test_sarif_flag_recognized(self):
        """Verify --sarif is accepted as a global flag (no 'no such option' error)."""
        from roam.cli import cli

        runner = CliRunner()
        # Just pass --sarif without a subcommand; should show usage, not an error
        result = runner.invoke(cli, ["--sarif", "--help"])
        assert result.exit_code == 0
        # The custom format_help does not list options, so instead verify
        # that the flag is a known parameter on the cli group.
        param_names = [p.name for p in cli.params]
        assert "sarif_mode" in param_names


class TestSarifAndJsonIndependent:
    """Test --sarif and --json work independently."""

    def test_sarif_and_json_exclusive(self, indexed_proj):
        runner = CliRunner()
        # SARIF produces SARIF format
        sarif_result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        sarif_data = parse_sarif(sarif_result)
        assert "$schema" in sarif_data
        assert "version" in sarif_data
        assert sarif_data["version"] == "2.1.0"

        # JSON produces JSON envelope format
        json_result = invoke_cli(runner, ["dead"], cwd=indexed_proj, json_mode=True)
        json_data = json.loads(json_result.output)
        assert "command" in json_data
        assert json_data["command"] == "dead"

        # They are different formats
        assert "$schema" not in json_data
        assert "command" not in sarif_data


class TestSarifNoFindings:
    """Test SARIF with no findings produces valid empty output."""

    def test_sarif_no_findings(self, project_factory):
        # Create a project where everything is used (no dead code)
        proj = project_factory(
            {
                "main.py": ("from helper import do_work\n\ndef main():\n    do_work()\n\nmain()\n"),
                "helper.py": ("def do_work():\n    return 1\n"),
            }
        )
        runner = CliRunner()
        result = invoke_sarif(runner, ["rules"], cwd=proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert len(run["results"]) == 0


class TestSarifSeverityMapping:
    """W531: ``severity: error`` taint rules MUST emit SARIF level=error.

    Before W531 ``_LEVEL_MAP`` had no entry for ``"ERROR"`` so every taint
    finding (every shipped SQLi / SSTI / deserialization rule ships
    ``severity: error``) silently downgraded to SARIF ``"note"``. GitHub
    Code Scanning + Defender + every CI gate keyed off ``level=error`` was
    broken. The fix is the closed mapping in ``_LEVEL_MAP``; this test
    locks it.
    """

    def test_to_level_maps_error_to_sarif_error(self):
        from roam.output.sarif import _to_level

        # Lowercase and uppercase both resolve to the SARIF "error" level.
        assert _to_level("error") == "error"
        assert _to_level("ERROR") == "error"

    def test_to_level_full_severity_table(self):
        from roam.output.sarif import _to_level

        # Closed mapping — every shipped severity tier resolves correctly.
        assert _to_level("CRITICAL") == "error"
        assert _to_level("critical") == "error"
        assert _to_level("ERROR") == "error"
        assert _to_level("error") == "error"
        assert _to_level("HIGH") == "warning"
        assert _to_level("WARNING") == "warning"
        assert _to_level("warning") == "warning"
        assert _to_level("MEDIUM") == "note"
        assert _to_level("LOW") == "note"
        assert _to_level("INFO") == "note"
        # Unknown labels default to "note" — never accidentally gates CI.
        assert _to_level("UNKNOWN") == "note"

    def test_taint_severity_error_emits_sarif_level_error(self):
        """A taint finding produced from a ``severity: error`` rule must
        emit a SARIF result with ``level: "error"`` AND the rule's
        ``defaultConfiguration.level`` must also be ``"error"``."""
        from roam.output.sarif import taint_to_sarif

        findings = [
            {
                "rule_id": "java-sqli",
                "severity": "error",
                "cwe": "CWE-89",
                "owasp_top10": "A03:2021_Injection",
                "source": {"name": "getParameter", "file": "S.java", "line": 1},
                "sink": {"name": "executeQuery", "file": "D.java", "line": 9},
                "path_length": 2,
                "path": [],
                "sanitizer_in_path": False,
                "vex_justification": None,
            }
        ]
        doc = taint_to_sarif(findings)
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "error", (
            "severity:error MUST surface as SARIF level=error so CI gates "
            "keyed off level=error fire; pre-W531 it downgraded to note."
        )
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["defaultConfiguration"]["level"] == "error"

    def test_taint_severity_warning_emits_sarif_level_warning(self):
        """``severity: warning`` resolves to SARIF ``level: "warning"``."""
        from roam.output.sarif import taint_to_sarif

        findings = [
            {
                "rule_id": "js-ssrf",
                "severity": "warning",
                "cwe": "CWE-918",
                "owasp_top10": "",
                "source": {"name": "req.query", "file": "x.js", "line": 1},
                "sink": {"name": "fetch", "file": "x.js", "line": 5},
                "path_length": 2,
                "path": [],
                "sanitizer_in_path": False,
                "vex_justification": None,
            }
        ]
        doc = taint_to_sarif(findings)
        assert doc["runs"][0]["results"][0]["level"] == "warning"


class TestW1062DashboardFilterTags:
    """W1062: SARIF ``result.properties.tags[]`` plumbing.

    GitHub Code Scanning / SonarQube / security-dashboard tools surface
    ``properties.tags`` as filter chips. Without normalised tags every
    roam finding looks uniform to the dashboard. The
    ``_derive_finding_tags`` helper threads the OWASP / CWE metadata
    roam already attaches (W492 ``owasp_top10`` + W374 CWE codes) into
    the SARIF surface under a single URL-safe vocabulary.
    """

    def test_normalize_tag_lowercase_hyphen(self) -> None:
        """W1062 tag normalisation is lowercase + hyphen URL-safe."""
        from roam.output.sarif import _normalize_tag

        assert _normalize_tag("CWE-89") == "cwe-89"
        assert _normalize_tag("A03:2021_Injection") == "a03-2021-injection"
        assert _normalize_tag("EU AI Act Article 12") == "eu-ai-act-article-12"
        assert _normalize_tag("security") == "security"
        # Parentheses + colons + leading/trailing whitespace collapse.
        assert _normalize_tag("  Foo:Bar (baz) ") == "foo-bar-baz"
        # Empty input -> empty string (caller drops).
        assert _normalize_tag("") == ""
        assert _normalize_tag("   ") == ""

    def test_derive_finding_tags_taint_sqli_shape(self) -> None:
        """A taint SQLI finding with W492 metadata emits the canonical
        ``[security, taint, cwe-89, owasp-a03, error]`` vocabulary."""
        from roam.output.sarif import _derive_finding_tags

        tags = _derive_finding_tags(
            cwe="CWE-89",
            owasp_top10="A03:2021_Injection",
            severity="error",
            family="security",
            extra=["taint"],
        )
        # Family + extras come first (anchors broad axis), then CWE +
        # OWASP, then severity.
        assert tags == ["security", "taint", "cwe-89", "owasp-a03", "error"]

    def test_derive_finding_tags_collapses_owasp_year_suffix(self) -> None:
        """``A03`` and ``A03:2021_Injection`` collapse to the same
        ``owasp-a03`` tag — the year + descriptive suffix is dashboard
        noise, the rank IS the OWASP Top 10 category identifier."""
        from roam.output.sarif import _derive_finding_tags

        rank_only = _derive_finding_tags(owasp_top10="A03", family="security")
        rank_year = _derive_finding_tags(
            owasp_top10="A03:2021_Injection",
            family="security",
        )
        assert rank_only == ["security", "owasp-a03"]
        assert rank_year == ["security", "owasp-a03"]

    def test_derive_finding_tags_drops_empty_axes(self) -> None:
        """Empty input on any axis drops the tag — no empty strings
        leak into the output."""
        from roam.output.sarif import _derive_finding_tags

        tags = _derive_finding_tags(family="vuln", severity="critical")
        assert tags == ["vuln", "critical"]

        # All-empty input yields empty list, not [""] / [None].
        assert _derive_finding_tags() == []

    def test_derive_finding_tags_dedups_preserve_order(self) -> None:
        """Duplicate inputs collapse, insertion order preserved."""
        from roam.output.sarif import _derive_finding_tags

        tags = _derive_finding_tags(
            family="security",
            extra=["security", "taint", "TAINT", "taint"],
        )
        assert tags == ["security", "taint"]

    def test_taint_to_sarif_emits_normalized_tags(self) -> None:
        """Taint emitter routes through ``_derive_finding_tags`` so
        the dashboard sees ``cwe-89`` + ``owasp-a03`` instead of the
        raw producer strings ``CWE-89`` + ``A03:2021_Injection``."""
        from roam.output.sarif import taint_to_sarif

        findings = [
            {
                "rule_id": "java-sqli",
                "severity": "error",
                "cwe": "CWE-89",
                "owasp_top10": "A03:2021_Injection",
                "source": {"name": "getParameter", "file": "S.java", "line": 1},
                "sink": {"name": "executeQuery", "file": "D.java", "line": 9},
                "path_length": 2,
                "path": [],
                "sanitizer_in_path": False,
                "vex_justification": None,
            }
        ]
        doc = taint_to_sarif(findings)
        result_tags = doc["runs"][0]["results"][0]["properties"]["tags"]
        rule_tags = doc["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]
        # The W1062 canonical taint shape, lowercase-hyphen.
        assert "security" in result_tags
        assert "taint" in result_tags
        assert "cwe-89" in result_tags
        assert "owasp-a03" in result_tags
        # No raw producer strings leak through.
        assert "CWE-89" not in result_tags
        assert "A03:2021_Injection" not in result_tags
        # Rule descriptor carries the same tags so consumers that group
        # by rule rather than result still get the filter chips.
        assert rule_tags == result_tags

    def test_vulns_to_sarif_emits_tags(self) -> None:
        """Vuln emitter stamps ``[vuln, severity, cve-...]`` so
        dashboards can filter by CVE / family / severity."""
        from roam.commands.cmd_vulns import _vulns_to_sarif

        vulns = [
            {
                "cve_id": "CVE-2024-12345",
                "package_name": "requests",
                "severity": "critical",
                "title": "Demo high-severity vuln",
                "matched_file": "requirements.txt",
                "reachable": 1,
            }
        ]
        doc = _vulns_to_sarif(vulns)
        result_tags = doc["runs"][0]["results"][0]["properties"]["tags"]
        assert "vuln" in result_tags
        assert "critical" in result_tags
        assert "cve-2024-12345" in result_tags
        assert "reachable" in result_tags

    def test_secrets_to_sarif_emits_normalized_tags(self) -> None:
        """W1062 followup: secrets emitter stamps
        ``[security, secret, <pattern-slug>, <severity>]`` on both the
        SARIF rule descriptor AND every result, so a security
        dashboard can slice the finding stream by detector family
        (``security``) / category (``secret``) / pattern slug
        (e.g. ``aws-access-key``) / severity. Secret findings have no
        CWE / OWASP anchors; family + category + pattern is the
        canonical filter chip shape."""
        from roam.output.sarif import secrets_to_sarif

        findings = [
            {
                "file": "config/prod.env",
                "line": 7,
                "severity": "high",
                "pattern_name": "AWS Access Key",
                "matched_text": "AKIA...XYZ9",
            }
        ]
        doc = secrets_to_sarif(findings)
        result_tags = doc["runs"][0]["results"][0]["properties"]["tags"]
        rule_tags = doc["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]

        # W1062 canonical secret shape: family + category + pattern + severity.
        assert result_tags == ["security", "secret", "aws-access-key", "high"]
        # Rule descriptor carries the same tags so consumers grouping by
        # rule still get the filter chips.
        assert rule_tags == result_tags

    def test_secrets_to_sarif_severity_normalised_lowercase(self) -> None:
        """W1062 followup: producer-side uppercase severity converges
        on the lowercase-hyphen tag vocabulary via ``_normalize_tag``.
        Confirms the helper threading on the secrets emitter (not just
        a literal pass-through)."""
        from roam.output.sarif import secrets_to_sarif

        findings = [
            {
                "file": "src/app.js",
                "line": 42,
                "severity": "MEDIUM",  # uppercase producer-side variant
                "pattern_name": "Slack Webhook",
                "matched_text": "https://hooks.slack.com/services/T.../B.../...",
            }
        ]
        doc = secrets_to_sarif(findings)
        result_tags = doc["runs"][0]["results"][0]["properties"]["tags"]
        assert result_tags == ["security", "secret", "slack-webhook", "medium"]
        assert "MEDIUM" not in result_tags  # no raw uppercase leaks through

    def test_auth_gaps_to_sarif_emits_normalized_tags(self) -> None:
        """W1062-followup-2: auth-gaps emitter stamps
        ``[security, auth, <kind>, <tier>, <severity>]`` on each result.

        The detector classifies each finding under one of three
        closed-enum kinds (``direct-unauthenticated-handler`` /
        ``helper-indirection`` / ``name-based``) bound to a confidence
        tier (``static_analysis`` / ``structural`` / ``heuristic``).
        Both axes plus the resolved SARIF level surface as filter chips
        so a security dashboard can isolate (e.g.) the deterministic
        ``static-analysis`` set from the heuristic ``name-based`` set.
        Tier underscore (``static_analysis``) collapses to the URL-safe
        hyphen form (``static-analysis``) via ``_normalize_tag``.
        """
        from roam.output.sarif import auth_gaps_to_sarif

        findings = [
            {
                "type": "route",
                "file": "routes/web.php",
                "line": 42,
                "verb": "GET",
                "path": "/admin/users",
                "confidence": "high",
                "fix": "wrap in auth middleware",
            }
        ]
        doc = auth_gaps_to_sarif(findings)
        result_tags = doc["runs"][0]["results"][0]["properties"]["tags"]

        # W1062-followup-2 canonical auth-gaps shape: family + category
        # + kind + tier + resolved level. The route finding maps onto
        # ``direct-unauthenticated-handler`` (static_analysis → error).
        assert result_tags == [
            "security",
            "auth",
            "direct-unauthenticated-handler",
            "static-analysis",
            "error",
        ]
        # Rule descriptor carries family + category + kind + tier
        # (no severity — that's per-result).
        rule_tags_by_id = {
            r["id"]: r.get("properties", {}).get("tags", [])
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
        }
        assert rule_tags_by_id["auth-gaps/direct-unauthenticated-handler"] == [
            "security",
            "auth",
            "direct-unauthenticated-handler",
            "static-analysis",
        ]
        # All 3 rules carry the security + auth anchors so dashboards
        # grouping by rule see the chips even on a clean run.
        for rule_id, tags in rule_tags_by_id.items():
            assert tags[:2] == ["security", "auth"], rule_id
        # Tier underscore must not leak through as a tag — confirms
        # the W1062 normalisation path runs.
        assert "static_analysis" not in result_tags

    def test_dead_to_sarif_emits_normalized_tags(self) -> None:
        """W1062-followup-2: dead emitter stamps
        ``[hygiene, dead-code, <action>, <level>]`` on each result.

        Dead-code findings carry no CWE / OWASP anchor; family
        (``hygiene``) + category (``dead-code``) + action
        (``safe`` / ``review``) + SARIF level (``warning`` / ``note``)
        is the canonical filter-chip shape — lets a triage user
        separate the ``SAFE`` removal candidates from the ``REVIEW``
        set without expanding every result. Producer-side uppercase
        ``SAFE`` / ``REVIEW`` converges on lowercase via the
        ``_normalize_tag`` chokepoint.
        """
        from roam.output.sarif import dead_to_sarif

        dead_exports = [
            {
                "name": "unused_helper",
                "kind": "function",
                "location": "src/util.py:17",
                "action": "SAFE",
            },
            {
                "name": "maybe_used",
                "kind": "class",
                "location": "src/util.py:42",
                "action": "REVIEW",
            },
        ]
        doc = dead_to_sarif(dead_exports)
        results = doc["runs"][0]["results"]
        assert len(results) == 2

        # SAFE → warning level; REVIEW → note level.
        safe_tags = results[0]["properties"]["tags"]
        review_tags = results[1]["properties"]["tags"]
        assert safe_tags == ["hygiene", "dead-code", "safe", "warning"]
        assert review_tags == ["hygiene", "dead-code", "review", "note"]
        # Raw uppercase action labels never leak through the tag list.
        assert "SAFE" not in safe_tags
        assert "REVIEW" not in review_tags

        # Rule descriptor carries family + category anchors so a
        # dashboard grouping by rule still gets the hygiene chips
        # even before any results land.
        rule_tags = doc["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]
        assert rule_tags == ["hygiene", "dead-code"]

    def test_clones_to_sarif_emits_normalized_tags(self) -> None:
        """W1062-followup-3: clones emitter stamps
        ``[hygiene, duplication, <pair|cluster>, <role_bucket>?, <severity>]``.

        Two finding shapes project onto two closed-enum rules:
        ``clones/pair`` (note default) and ``clones/cluster`` (warning
        default). Tags carry family (``hygiene``) + category
        (``duplication``) + the pair/cluster kind + ``role_bucket``
        when present (``production`` / ``test-intentional`` / ``mixed``;
        producer-side underscores collapse via ``_normalize_tag``) +
        resolved SARIF level. Pair severity is derived from
        ``_clones_pair_level`` (>=0.95 -> ``warning``, else ``note``).
        Clones carry no CWE / OWASP anchor — DRY findings aren't
        security findings.
        """
        from roam.output.sarif import clones_to_sarif

        data = {
            "clusters": [
                {
                    "cluster_id": "C1",
                    "avg_similarity": 0.97,
                    "size": 3,
                    "pattern": "shared validate logic",
                    "role_bucket": "production",
                    "members": [
                        {"file": "src/a.py", "line_start": 10},
                        {"file": "src/b.py", "line_start": 20},
                        {"file": "src/c.py", "line_start": 30},
                    ],
                },
            ],
            "pairs": [
                {
                    "file_a": "src/a.py",
                    "func_a": "do_thing",
                    "line_a": 10,
                    "file_b": "src/b.py",
                    "func_b": "do_other_thing",
                    "line_b": 20,
                    "similarity": 0.98,
                    "role_bucket": "test_intentional",
                },
            ],
        }
        doc = clones_to_sarif(data)
        results = doc["runs"][0]["results"]
        assert len(results) == 2

        # First result = cluster (warning level, production bucket).
        cluster_tags = results[0]["properties"]["tags"]
        assert cluster_tags == [
            "hygiene",
            "duplication",
            "cluster",
            "production",
            "warning",
        ]
        # Second result = pair (>=0.95 similarity -> warning level,
        # test_intentional bucket — underscore collapses to hyphen).
        pair_tags = results[1]["properties"]["tags"]
        assert pair_tags == [
            "hygiene",
            "duplication",
            "pair",
            "test-intentional",
            "warning",
        ]
        # Producer-side underscores never leak through the tag list.
        assert "test_intentional" not in pair_tags

        # Rule descriptors carry family + category + kind anchors so a
        # dashboard grouping by rule still gets the hygiene chips
        # even before any results land.
        rule_tags_by_id = {
            r["id"]: r.get("properties", {}).get("tags", [])
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
        }
        assert rule_tags_by_id["clones/pair"] == ["hygiene", "duplication", "pair"]
        assert rule_tags_by_id["clones/cluster"] == [
            "hygiene",
            "duplication",
            "cluster",
        ]

    def test_smells_to_sarif_emits_normalized_tags(self) -> None:
        """W1062-followup-3: smells emitter stamps
        ``[hygiene, smells, <smell_id>, <severity>]`` on each result.

        The smell registry surfaces 24 closed-enum smell ids; each
        finding projects onto a rule of the form ``smells/<smell_id>``.
        Tags carry family (``hygiene``) + category (``smells``) +
        per-finding smell id + resolved SARIF level. Producer severity
        (``critical`` / ``warning`` / ``info``) collapses to SARIF
        level (``error`` / ``warning`` / ``note``) via the canonical
        ``_to_level`` mapper. Smell findings have no CWE / OWASP
        anchor — structural code-smells aren't security findings.
        """
        from roam.output.sarif import smells_to_sarif

        findings = [
            {
                "smell_id": "god-class",
                "severity": "critical",
                "symbol_name": "MegaController",
                "kind": "class",
                "location": "src/big.py:5",
                "metric_value": 50,
                "threshold": 20,
                "description": "Class spans 50 methods",
            },
        ]
        doc = smells_to_sarif(findings)
        results = doc["runs"][0]["results"]
        assert len(results) == 1

        # critical -> error level; god-class smell id passes through.
        result_tags = results[0]["properties"]["tags"]
        assert result_tags == ["hygiene", "smells", "god-class", "error"]
        # Producer-side ``critical`` never leaks as a tag — the helper
        # routes the severity through ``_to_level`` so dashboards see
        # the canonical SARIF level chip (``error``) instead.
        assert "critical" not in result_tags

        # Rule descriptor carries family + category + smell id anchors
        # so a dashboard grouping by rule still gets the hygiene chips
        # even before any results land. Pick the god-class rule out of
        # the full 24-rule catalogue.
        rule_tags_by_id = {
            r["id"]: r.get("properties", {}).get("tags", [])
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
        }
        assert rule_tags_by_id["smells/god-class"] == [
            "hygiene",
            "smells",
            "god-class",
        ]
        # Every rule carries the family + category anchors so dashboards
        # grouping by rule see the hygiene chips on every smell kind.
        for rule_id, tags in rule_tags_by_id.items():
            assert tags[:2] == ["hygiene", "smells"], rule_id

    def test_over_fetch_to_sarif_emits_normalized_tags(self) -> None:
        """W1062-followup-3: over-fetch emitter stamps
        ``[performance, over-fetch, <scope>, <confidence>?, <severity>]``.

        Two parallel finding shapes (endpoint-level + model-level)
        project onto the same single-rule catalogue
        (``over-fetch/select-star-or-wide-query``). Tags carry family
        (``performance``) + category (``over-fetch``) + scope
        (``endpoint`` / ``model``) + per-finding confidence (model
        shape only) + resolved SARIF level. Producer-side ``H`` / ``L``
        severity letters collapse to SARIF level (``warning`` /
        ``note``) via ``_over_fetch_severity_level``. Over-fetch
        findings have no CWE / OWASP anchor — DB-perf / bandwidth
        concerns aren't security findings.
        """
        from roam.output.sarif import over_fetch_to_sarif

        findings = [
            # Endpoint-level finding (3-state classification).
            {
                "state": "BARE",
                "file": "app/Http/Controllers/UserController.php",
                "line": 42,
                "severity": "H",
                "endpoint": "GET /users",
                "evidence": "User::all()",
                "recommendation": "use User::select(...)",
            },
            # Model-level finding (fillable-without-hidden).
            {
                "model_path": "app/Models/User.php",
                "model_location": "app/Models/User.php:7",
                "model_name": "User",
                "confidence": "high",
                "fillable_count": 12,
                "hidden_count": 0,
                "exposed_count": 12,
                "reasons": ["no $hidden / $visible filtering"],
            },
        ]
        doc = over_fetch_to_sarif(findings)
        results = doc["runs"][0]["results"]
        assert len(results) == 2

        # Endpoint result: family + category + scope + resolved level.
        # ``H`` severity collapses to SARIF ``warning``.
        endpoint_tags = results[0]["properties"]["tags"]
        assert endpoint_tags == [
            "performance",
            "over-fetch",
            "endpoint",
            "warning",
        ]
        # Producer-side single-letter severity never leaks through.
        assert "h" not in endpoint_tags
        assert "H" not in endpoint_tags

        # Model result: scope + confidence (chip in own right) +
        # resolved level. ``high`` confidence -> SARIF ``warning``.
        model_tags = results[1]["properties"]["tags"]
        assert model_tags == [
            "performance",
            "over-fetch",
            "model",
            "high",
            "warning",
        ]

        # Rule descriptor carries family + category anchors so a
        # dashboard grouping by rule still gets the performance chips
        # even before any results land.
        rule_tags = doc["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]
        assert rule_tags == ["performance", "over-fetch"]

    def test_n1_emitter_tags_w1062_followup_4(self) -> None:
        """W1062-followup-4: n1 emitter stamps
        ``[performance, n1-query, <severity>]`` on each result.

        Three confidence-banded rules (``n1/high-confidence`` /
        ``n1/medium-confidence`` / ``n1/low-confidence``) carry family
        (``performance``) + category (``n1-query``) tags on the rule
        descriptor. Per-result tags additionally carry the resolved
        SARIF level (``error`` / ``warning`` / ``note`` via the
        confidence-to-level mapper). N+1 findings have no CWE / OWASP
        anchor — query-amplification pathology isn't a security
        finding — so family + category + severity is the canonical
        filter-chip shape.
        """
        from roam.output.sarif import n1_to_sarif

        findings = [
            # High-confidence finding — model used in collection
            # context, accessor triggers per-item I/O.
            {
                "accessor_location": "app/Models/Post.php:42",
                "confidence": "high",
                "model_name": "Post",
                "accessor_name": "comments",
                "appended_attribute": "post_id",
                "relationship": "hasMany",
                "io_type": "lazy_load",
                "suggestion": "use ->with('comments') eager load",
            },
            # Medium-confidence finding — accessor lazy-loads but no
            # strong collection-context signal.
            {
                "accessor_location": "app/Models/User.php:18",
                "confidence": "medium",
                "model_name": "User",
                "accessor_name": "profile",
                "relationship": "hasOne",
                "io_type": "lazy_load",
            },
        ]
        doc = n1_to_sarif(findings)
        results = doc["runs"][0]["results"]
        assert len(results) == 2

        # High-confidence -> error level.
        high_tags = results[0]["properties"]["tags"]
        assert high_tags == ["performance", "n1-query", "error"]
        # Medium-confidence -> warning level.
        medium_tags = results[1]["properties"]["tags"]
        assert medium_tags == ["performance", "n1-query", "warning"]

        # Rule descriptors carry family + category anchors on every
        # rule in the closed enum so dashboards grouping by rule see
        # the performance chips even before any results land.
        rule_tags_by_id = {
            r["id"]: r.get("properties", {}).get("tags", [])
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
        }
        for rule_id, tags in rule_tags_by_id.items():
            assert tags == ["performance", "n1-query"], rule_id

    def test_missing_index_emitter_tags_w1062_followup_4(self) -> None:
        """W1062-followup-4: missing-index emitter stamps
        ``[performance, missing-index, <severity>]`` on each result.

        Three confidence-banded rules
        (``missing-index/high-confidence`` /
        ``missing-index/medium-confidence`` /
        ``missing-index/low-confidence``) carry family (``performance``)
        + category (``missing-index``) on the rule descriptor.
        Per-result tags additionally carry the resolved SARIF level
        (``error`` / ``warning`` / ``note``) via
        ``_missing_index_confidence_level``. Missing-index findings
        carry no CWE / OWASP anchor — query-planner pathology isn't a
        security finding.
        """
        from roam.output.sarif import missing_index_to_sarif

        findings = [
            # High-confidence: paginated WHERE on unindexed column.
            {
                "query_location": "app/Http/Controllers/PostController.php:55",
                "confidence": "high",
                "table": "posts",
                "columns": ["author_id"],
                "pattern_type": "where_unindexed",
                "has_paginate": True,
                "issue": "scan on posts.author_id",
                "suggestion": "CREATE INDEX posts_author_id_idx",
            },
            # Low-confidence: existing index, not optimal composite.
            {
                "query_location": "app/Repos/UserRepo.php:101",
                "confidence": "low",
                "table": "users",
                "columns": ["status", "created_at"],
                "pattern_type": "orderby_with_where",
                "has_paginate": False,
                "suggestion": "consider composite index (status, created_at)",
            },
        ]
        doc = missing_index_to_sarif(findings)
        results = doc["runs"][0]["results"]
        assert len(results) == 2

        # High -> error.
        high_tags = results[0]["properties"]["tags"]
        assert high_tags == ["performance", "missing-index", "error"]
        # Low -> note.
        low_tags = results[1]["properties"]["tags"]
        assert low_tags == ["performance", "missing-index", "note"]

        # Rule descriptors carry family + category anchors on every
        # rule in the closed enum so dashboards grouping by rule see
        # the performance chips even on a clean run.
        rule_tags_by_id = {
            r["id"]: r.get("properties", {}).get("tags", [])
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
        }
        for rule_id, tags in rule_tags_by_id.items():
            assert tags == ["performance", "missing-index"], rule_id

    def test_orphan_imports_emitter_tags_w1062_followup_4(self) -> None:
        """W1062-followup-4: orphan-imports emitter stamps
        ``[hygiene, orphan-imports, <kind-slug>, <severity>]`` on each
        result.

        Three closed-enum rules
        (``orphan-imports/internal-typo`` /
        ``orphan-imports/missing-package`` /
        ``orphan-imports/missing-local``) project onto a family
        (``hygiene``) + category (``orphan-imports``) + per-kind tag
        shape. Producer-side kind labels use underscores
        (``internal_typo`` / ``missing_package`` / ``missing_local``)
        that collapse to URL-safe hyphen form via ``_normalize_tag``.
        Orphan imports are a hygiene / dead-edge concern with no
        CWE / OWASP anchor — family + category + kind + severity is
        the canonical filter-chip shape.
        """
        from roam.output.sarif import orphan_imports_to_sarif

        findings = [
            # Internal-typo: top-level package indexed, dotted submodule
            # is not — deterministic typo detection (-> error level).
            {
                "file": "src/app.py",
                "line": 3,
                "language": "python",
                "module": "roam.indxer",
                "kind": "internal_typo",
                "hint": "Did you mean 'roam.indexer'?",
            },
            # Missing-local: JS path import didn't resolve to indexed
            # file (-> warning level).
            {
                "file": "src/components/Foo.tsx",
                "line": 7,
                "language": "javascript",
                "module": "./utils/missing",
                "kind": "missing_local",
                "hint": "no matching file in index",
            },
        ]
        doc = orphan_imports_to_sarif(findings)
        results = doc["runs"][0]["results"]
        assert len(results) == 2

        # internal_typo -> error; underscore collapses to hyphen.
        typo_tags = results[0]["properties"]["tags"]
        assert typo_tags == ["hygiene", "orphan-imports", "internal-typo", "error"]
        # No raw underscore leaks through the tag list.
        assert "internal_typo" not in typo_tags

        # missing_local -> warning; underscore collapses to hyphen.
        local_tags = results[1]["properties"]["tags"]
        assert local_tags == [
            "hygiene",
            "orphan-imports",
            "missing-local",
            "warning",
        ]
        assert "missing_local" not in local_tags

        # Rule descriptors carry family + category + kind anchors on
        # every rule in the closed enum so dashboards grouping by rule
        # see the hygiene chips even on a clean run.
        rule_tags_by_id = {
            r["id"]: r.get("properties", {}).get("tags", [])
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
        }
        assert rule_tags_by_id["orphan-imports/internal-typo"] == [
            "hygiene",
            "orphan-imports",
            "internal-typo",
        ]
        assert rule_tags_by_id["orphan-imports/missing-package"] == [
            "hygiene",
            "orphan-imports",
            "missing-package",
        ]
        assert rule_tags_by_id["orphan-imports/missing-local"] == [
            "hygiene",
            "orphan-imports",
            "missing-local",
        ]
        # All rules carry the family + category anchors.
        for rule_id, tags in rule_tags_by_id.items():
            assert tags[:2] == ["hygiene", "orphan-imports"], rule_id
