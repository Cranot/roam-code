"""W492 / W453 — owasp_top10 plumbing across TaintRule → findings → SARIF.

The taint rule YAMLs ship with ``owasp_top10`` keys (e.g.
``A03:2021_Injection``, ``A08:2021_Software_and_Data_Integrity_Failures``).
Before W492 the field was documentation-only — the TaintRule dataclass
didn't load it, the findings registry never persisted it, and the SARIF
exporter never surfaced it.

This module pins three properties end-to-end:

1. ``load_rules`` reads ``owasp_top10`` off the YAML into TaintRule.
2. The findings registry ``evidence_json`` payload carries
   ``owasp_top10`` when the rule declares one (W492).
3. The SARIF projection surfaces ``owasp_top10`` AND ``cwe`` together
   on ``result.properties.tags[]`` AND ``rule.properties.tags[]``
   (W453).
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.output.sarif import taint_to_sarif
from roam.security.taint_engine import load_rules
from tests._helpers.repo_root import repo_root
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# 1. TaintRule loads owasp_top10 off the YAML
# ---------------------------------------------------------------------------


def test_taint_rule_loads_owasp_top10_from_yaml(tmp_path):
    """A YAML rule with ``owasp_top10`` populates the dataclass field."""
    rule_yaml = tmp_path / "demo.yaml"
    rule_yaml.write_text(
        "id: demo-injection\n"
        "description: demo\n"
        "severity: error\n"
        "cwe: CWE-89\n"
        "owasp_top10: A03:2021_Injection\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - request.args\n"
        "sinks:\n"
        "  - eval\n",
        encoding="utf-8",
    )
    rules = load_rules(tmp_path)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == "demo-injection"
    assert rule.owasp_top10 == "A03:2021_Injection"
    assert rule.cwe == "CWE-89"


def test_taint_rule_owasp_top10_defaults_to_empty(tmp_path):
    """A YAML rule WITHOUT ``owasp_top10`` defaults to the empty string.

    Empty-string default keeps downstream consumers (findings emit,
    SARIF tags) simple — they can treat "" as "not tagged" rather than
    branching on None / KeyError.
    """
    rule_yaml = tmp_path / "demo.yaml"
    rule_yaml.write_text(
        "id: demo-no-owasp\n"
        "description: demo\n"
        "severity: warning\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - request.args\n"
        "sinks:\n"
        "  - eval\n",
        encoding="utf-8",
    )
    rules = load_rules(tmp_path)
    assert len(rules) == 1
    assert rules[0].owasp_top10 == ""


def test_builtin_pack_carries_owasp_top10_on_known_rules():
    """W492/W532: every shipped taint rule declares an owasp_top10 mapping.

    W492 surfaced the dataclass field for W373/W374/W375 (java-sqli,
    python-ssti, java-deserialization). W530 fixed A05->A03 for
    Injection-class rules (Injection is A03 in OWASP 2021). W532 then
    stamped owasp_top10 on every remaining shipped rule. This test locks
    that pack-wide coverage: no shipped rule may regress to an empty
    owasp_top10 — every rule maps to an OWASP 2021 category.
    """
    pack_dir = repo_root() / "src" / "roam" / "security" / "taint_rules"
    rules = {r.rule_id: r for r in load_rules(pack_dir)}
    # The W373/W374/W375 anchor rules carry the canonical Injection /
    # Software and Data Integrity Failures mappings.
    assert rules["java-sqli"].owasp_top10 == "A03:2021_Injection"
    assert rules["python-ssti"].owasp_top10 == "A03:2021_Injection"
    assert rules["java-deserialization"].owasp_top10 == "A08:2021_Software_and_Data_Integrity_Failures"
    # W532 stamp — python-command-injection now also resolves to A03.
    assert rules["python-command-injection"].owasp_top10 == "A03:2021_Injection"
    # Every shipped rule MUST declare an owasp_top10 mapping; an empty
    # value is a regression (Pattern 3a: cross-command claim integrity).
    untagged = sorted(rid for rid, r in rules.items() if not r.owasp_top10)
    assert not untagged, (
        f"shipped taint rules missing owasp_top10: {untagged!r}; every rule must declare an OWASP 2021 category (W532)"
    )


# ---------------------------------------------------------------------------
# 2. SARIF projection — tags[] on result + rule
# ---------------------------------------------------------------------------


def test_sarif_taint_result_tags_include_owasp_and_cwe():
    """Each SARIF result.properties.tags[] carries owasp_top10 + cwe + 'security' + 'taint'."""
    findings = [
        {
            "rule_id": "java-sqli",
            "severity": "error",
            "cwe": "CWE-89",
            "owasp_top10": "A03:2021_Injection",
            "source": {"name": "getParameter", "file": "Servlet.java", "line": 10},
            "sink": {"name": "executeQuery", "file": "Dao.java", "line": 50},
            "path_length": 2,
            "path": [
                {"name": "getParameter", "file": "Servlet.java", "line": 10},
                {"name": "executeQuery", "file": "Dao.java", "line": 50},
            ],
            "sanitizer_in_path": False,
            "vex_justification": None,
        }
    ]
    doc = taint_to_sarif(findings)
    assert doc["version"] == "2.1.0"
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    result = results[0]
    tags = result["properties"]["tags"]
    # CWE + OWASP travel through under the W1062 URL-safe normalised
    # vocabulary (``cwe-89`` / ``owasp-a03``). Raw producer strings
    # like ``CWE-89`` / ``A03:2021_Injection`` are intentionally
    # NOT emitted — see _derive_finding_tags + the
    # TestW1062DashboardFilterTags suite in tests/test_sarif_flag.py.
    assert "cwe-89" in tags
    assert "owasp-a03" in tags
    # Baseline categorisation tags are always present so consumers
    # filtering by "security" or "taint" pick the result up even when
    # the rule lacks owasp / cwe metadata.
    assert "security" in tags
    assert "taint" in tags


def test_sarif_taint_rule_tags_include_owasp_and_cwe():
    """The rule definition's properties.tags[] also carries the labels.

    GitHub Code Scanning uses rule-level tags for rule filtering;
    result-level tags for per-result filter chips. Both surfaces must
    carry the same labels for filter parity.
    """
    findings = [
        {
            "rule_id": "java-sqli",
            "severity": "error",
            "cwe": "CWE-89",
            "owasp_top10": "A03:2021_Injection",
            "source": {"name": "getParameter", "file": "Servlet.java", "line": 10},
            "sink": {"name": "executeQuery", "file": "Dao.java", "line": 50},
            "path_length": 2,
            "path": [],
            "sanitizer_in_path": False,
            "vex_justification": None,
        }
    ]
    doc = taint_to_sarif(findings)
    driver_rules = doc["runs"][0]["tool"]["driver"]["rules"]
    assert len(driver_rules) == 1
    rule = driver_rules[0]
    rule_tags = rule["properties"]["tags"]
    # W1062 canonical normalised vocabulary on rule.properties.tags[]
    # as well — same shape as result.properties.tags[] so filter
    # parity is preserved across both surfaces.
    assert "cwe-89" in rule_tags
    assert "owasp-a03" in rule_tags
    assert "security" in rule_tags
    assert "taint" in rule_tags


def test_sarif_taint_no_owasp_no_cwe_still_has_baseline_tags():
    """A finding without owasp/cwe still gets the baseline ('security','taint') tags."""
    findings = [
        {
            "rule_id": "python-command-injection",
            "severity": "error",
            "cwe": "",
            "owasp_top10": "",
            "source": {"name": "input", "file": "vuln.py", "line": 1},
            "sink": {"name": "eval", "file": "vuln.py", "line": 5},
            "path_length": 2,
            "path": [],
            "sanitizer_in_path": False,
            "vex_justification": None,
        }
    ]
    doc = taint_to_sarif(findings)
    result_tags = doc["runs"][0]["results"][0]["properties"]["tags"]
    assert result_tags == ["security", "taint"]


# ---------------------------------------------------------------------------
# 3. Findings registry — evidence_json carries owasp_top10
# ---------------------------------------------------------------------------


def _taint_project_command_injection(tmp_path):
    """Tiny Python project that triggers the python-command-injection rule.

    Mirrors the proven fixture from ``test_findings_taint.py`` — local
    stand-ins named ``input`` (source) and ``eval`` (sink) plus a
    ``handler`` that calls both. Engine produces the co-call shape; the
    finding persists when ``--persist`` is set.

    W532: python-command-injection now declares
    ``owasp_top10: A03:2021_Injection`` in YAML, so the persisted
    evidence carries that literal (key always present, value either an
    OWASP 2021 category string or empty — never missing).
    """
    return _make_project(
        tmp_path,
        {
            "tainted.py": """
            def input():
                return "untrusted"

            def eval(code):
                return code

            def handler():
                data = input()
                return eval(data)
            """,
        },
    )


def _taint_project_python_ssti(tmp_path):
    """Tiny Python project that triggers the python-ssti rule.

    Local stand-ins for ``request.args`` (source) and
    ``render_template_string`` (sink) — the rule matches qualified
    suffixes so a local ``request.args`` / ``render_template_string``
    name surfaces. python-ssti.yaml DOES declare owasp_top10, so the
    persisted evidence must carry the literal string.
    """
    return _make_project(
        tmp_path,
        {
            "tainted.py": """
            class _R:
                @staticmethod
                def args():
                    return "untrusted"

            request = _R()

            def render_template_string(template):
                return template

            def page():
                data = request.args()
                return render_template_string(data)
            """,
        },
    )


def test_taint_finding_evidence_json_carries_owasp_when_rule_tagged(tmp_path):
    """W532: python-command-injection ships with owasp_top10 ->
    evidence_json carries the literal OWASP 2021 category string.

    Downstream consumers (governance exports, dashboards) query
    ``evidence_json -> owasp_top10`` and need both:
      (a) the key always present (W492), and
      (b) the value populated when the rule declares one (W532).

    The dataclass default ``""`` remains the contract for out-of-tree
    rule packs that omit the field; this test asserts the shipped pack
    populates it.
    """
    proj = _taint_project_command_injection(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["taint", "--rule", "command-injection", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'taint'").fetchall()
            assert rows, "expected at least one taint finding to persist"
            for row in rows:
                evidence = json.loads(row[0])
                # owasp_top10 key MUST be present on every emitted row.
                assert "owasp_top10" in evidence
                # W532: python-command-injection now resolves to A03 Injection.
                assert evidence["owasp_top10"] == "A03:2021_Injection"
    finally:
        os.chdir(old_cwd)


def test_taint_rule_owasp_top10_default_empty_for_untagged_pack(tmp_path):
    """W492/W532 contract surface: a custom out-of-tree rule WITHOUT
    ``owasp_top10`` still loads cleanly with the empty-string default.

    The shipped pack now stamps owasp_top10 on every rule (W532), but
    the empty-string default stays a documented contract for users who
    author their own ``--rules-dir`` packs and choose not to map to an
    OWASP category.
    """
    rule_yaml = tmp_path / "custom.yaml"
    rule_yaml.write_text(
        "id: custom-untagged\n"
        "description: synthetic rule with no owasp mapping\n"
        "severity: warning\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - request.args\n"
        "sinks:\n"
        "  - eval\n",
        encoding="utf-8",
    )
    rules = load_rules(tmp_path)
    assert len(rules) == 1
    assert rules[0].owasp_top10 == ""
