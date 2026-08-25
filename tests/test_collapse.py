"""Precision and command-contract tests for the benign-default collapse detector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import _COMMANDS, _SARIF_CONSUMERS, cli
from roam.commands.cmd_collapse import (
    COLLAPSE_RULES,
    RULE_CATCH_TO_BENIGN_LITERAL,
    RULE_ENOENT_CONFLATION,
    RULE_FALLBACK_OR_ZERO,
    RULE_PARSE_FAILURE_MERGES_WITH_EMPTY,
    RULE_SHELL_ECHO_FALLBACK,
    SUPPORTED_LANGUAGES,
    scan_source,
)
from roam.output.sarif import collapse_to_sarif
from tests.conftest import git_init, index_in_process

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "collapse"


def _scan(name: str) -> list[dict]:
    path = FIXTURES / name
    return scan_source(name, path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture", "rule"),
    [
        ("tp_catch_literal.py", RULE_CATCH_TO_BENIGN_LITERAL),
        ("tp_catch_literal.js", RULE_CATCH_TO_BENIGN_LITERAL),
        ("tp_enoent_conflation.js", RULE_ENOENT_CONFLATION),
        ("tp_fallback_zero.py", RULE_FALLBACK_OR_ZERO),
        ("tp_fallback_zero.js", RULE_FALLBACK_OR_ZERO),
        ("tp_shell_echo_fallback.sh", RULE_SHELL_ECHO_FALLBACK),
        ("tp_parse_empty_merge.py", RULE_PARSE_FAILURE_MERGES_WITH_EMPTY),
        ("tp_parse_empty_merge.js", RULE_PARSE_FAILURE_MERGES_WITH_EMPTY),
    ],
)
def test_true_positive_fixtures_fire_the_expected_rule(fixture: str, rule: str) -> None:
    findings = _scan(fixture)
    assert findings, fixture
    assert {finding["rule"] for finding in findings} == {rule}


@pytest.mark.parametrize(
    "fixture",
    [
        "tn_catch_observed.py",
        "tn_catch_observed.js",
        "tn_enoent_distinguished.js",
        "tn_file_not_found_distinguished.py",
        "tn_fallback_zero_guarded.py",
        "tn_fallback_zero_guarded.js",
        "tn_shell_distinct_failure.sh",
        "tn_parse_distinct_state.py",
        "tn_exists_guard.js",
        "tn_exists_guarded_catch.js",
        "tn_best_effort_cache.py",
        "tn_inline_suppression.py",
    ],
)
def test_conservation_fixtures_stay_silent(fixture: str) -> None:
    assert _scan(fixture) == []


def test_finding_contract_states_the_collapsed_facts_and_repair() -> None:
    finding = _scan("tp_enoent_conflation.js")[0]
    assert finding["file"] == "tp_enoent_conflation.js"
    assert finding["line"] > 0
    assert finding["severity"] == "high"
    assert "unreadable" in finding["collapsed_facts"]
    assert "absent" in finding["collapsed_facts"]
    assert finding["repair"] == "Check the error code and preserve a distinct failure state."


def test_return_flow_is_high_and_local_fallback_is_medium() -> None:
    findings = _scan("tp_fallback_zero.py")
    by_line = {finding["line"]: finding for finding in findings}
    assert by_line[2]["severity"] == "medium"
    assert by_line[7]["severity"] == "high"


def test_rule_and_language_catalogs_are_closed() -> None:
    assert tuple(COLLAPSE_RULES) == (
        RULE_CATCH_TO_BENIGN_LITERAL,
        RULE_ENOENT_CONFLATION,
        RULE_FALLBACK_OR_ZERO,
        RULE_SHELL_ECHO_FALLBACK,
        RULE_PARSE_FAILURE_MERGES_WITH_EMPTY,
    )
    assert SUPPORTED_LANGUAGES == ("python", "javascript", "typescript", "tsx", "bash")


def test_sarif_projects_every_rule_with_file_line_and_message() -> None:
    findings = []
    for fixture in (
        "tp_catch_literal.py",
        "tp_enoent_conflation.js",
        "tp_fallback_zero.py",
        "tp_shell_echo_fallback.sh",
        "tp_parse_empty_merge.py",
    ):
        findings.append(_scan(fixture)[0])

    document = collapse_to_sarif(findings)
    run = document["runs"][0]
    assert len(run["tool"]["driver"]["rules"]) == 5
    assert len(run["results"]) == 5
    assert {row["ruleId"] for row in run["results"]} == {f"collapse/{rule}" for rule in COLLAPSE_RULES}
    for row in run["results"]:
        location = row["locations"][0]["physicalLocation"]
        assert location["artifactLocation"]["uri"]
        assert location["region"]["startLine"] > 0
        assert "Repair:" in row["message"]["text"]


def test_command_is_registered_in_health_and_sarif_surfaces() -> None:
    from roam.cli import _CATEGORIES

    assert _COMMANDS["collapse"] == ("roam.commands.cmd_collapse", "collapse")
    assert "collapse" in _CATEGORIES["Codebase Health"]
    assert "collapse" in _SARIF_CONSUMERS


def test_json_command_envelope_and_gate_exit(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "reader.py").write_text(
        "def read_rows(client):\n"
        "    try:\n"
        "        return client.fetch_rows()\n"
        "    except Exception:\n"
        "        return []\n",
        encoding="utf-8",
    )
    git_init(tmp_path)
    _, index_exit = index_in_process(tmp_path)
    assert index_exit == 0

    runner = CliRunner()
    old_cwd = Path.cwd()
    try:
        import os

        os.chdir(tmp_path)
        result = runner.invoke(cli, ["--json", "collapse"], catch_exceptions=False)
        gated = runner.invoke(cli, ["--json", "collapse", "--fail-on-found"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "collapse"
    assert payload["summary"]["total_findings"] == 1
    assert payload["summary"]["high_findings"] == 1
    assert payload["findings"][0]["collapsed_facts"]
    assert payload["findings"][0]["repair"]
    assert gated.exit_code == 5


def test_help_contains_when_to_use_line() -> None:
    result = CliRunner().invoke(cli, ["collapse", "--help"])
    assert result.exit_code == 0
    assert "WHEN TO USE:" in result.output


def test_fail_closed_sentinel_consumers_suppress_collapse() -> None:
    """Unreadable digest -> None is intentional when every caller refuses it."""
    source = """
from pathlib import Path

def _digest(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None

def verify(path):
    digest = _digest(path)
    if digest is None:
        return {"state": "unverifiable", "accepted": False}
    return {"state": "verified", "accepted": True}
"""
    assert scan_source("guard.py", source) == []


def test_mixed_polarity_sentinel_consumer_remains_a_positive() -> None:
    """Conservation: a caller that accepts the benign default keeps the finding."""
    source = """
from pathlib import Path

def _digest(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None

def display(path):
    return _digest(path)
"""
    assert scan_source("guard.py", source)


def test_inline_fail_soft_intent_annotation_suppresses_collapse() -> None:
    source = """
def optional_preview(client):
    try:
        return client.fetch()
    except Exception:
        return []  # Intentional fail-soft: preview is advisory
"""
    assert scan_source("preview.py", source) == []
