"""Tests for W331 ``<metric>_definition`` sidecar fields.

Pattern 3a from CLAUDE.md: every command that reports a "callers",
"complexity", "blast radius", "health score", etc. number SHOULD stamp
a ``<metric>_definition`` sidecar so consumers know what the number
actually measures. W331 wired definitions into the top-6 high-signal
commands that previously lacked them.

This file covers:

* The 6 W331 targets: ``impact``, ``health``, ``complexity``,
  ``preflight``, ``dead``, ``invariants``.
* A drift guard on the shared
  ``src/roam/output/metric_definitions.py`` constants module — every
  exported constant is non-empty and no two constants are byte-
  identical (i.e. two different metrics never carry the same prose).

Companion to ``tests/test_caller_metric_definition.py``, which already
covers the ``caller_metric_definition`` family. This file deliberately
does NOT re-test those — it focuses on the W331 additions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(result, command: str) -> dict:
    """Parse JSON output from a CliRunner result with helpful diagnostics."""
    assert result.exit_code == 0, (
        f"Command {command} failed (exit {result.exit_code}):\n{result.output}"
    )
    raw = getattr(result, "stdout", None) or result.output
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Invalid JSON from {command}: {exc}\n{raw[:500]}")


def _assert_definition_field(summary: dict, key: str, command: str) -> None:
    """Assert ``summary[key]`` is a present, non-empty string."""
    assert key in summary, (
        f"{command}: summary missing definition sidecar '{key}'.\n"
        f"  summary keys: {sorted(summary.keys())}"
    )
    value = summary[key]
    assert isinstance(value, str), (
        f"{command}.summary.{key} should be str, got {type(value).__name__}"
    )
    assert value.strip(), f"{command}.summary.{key} is empty"


# ---------------------------------------------------------------------------
# Per-command sidecar tests — one per metric/command pair.
# ---------------------------------------------------------------------------


class TestImpactDefinitions:
    """`roam impact` stamps 4 blast-radius definitions in summary."""

    def test_impact_emits_affected_symbols_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["impact", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "impact")
        _assert_definition_field(data["summary"], "affected_symbols_definition", "impact")

    def test_impact_emits_affected_files_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["impact", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "impact")
        _assert_definition_field(data["summary"], "affected_files_definition", "impact")

    def test_impact_emits_weighted_impact_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["impact", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "impact")
        _assert_definition_field(data["summary"], "weighted_impact_definition", "impact")

    def test_impact_emits_reach_pct_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["impact", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "impact")
        _assert_definition_field(data["summary"], "reach_pct_definition", "impact")


class TestHealthDefinitions:
    """`roam health` stamps health_score_definition and tangle_ratio_definition."""

    def test_health_emits_health_score_definition(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "health")
        _assert_definition_field(data["summary"], "health_score_definition", "health")

    def test_health_emits_tangle_ratio_definition(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "health")
        _assert_definition_field(data["summary"], "tangle_ratio_definition", "health")


class TestComplexityDefinitions:
    """`roam complexity` stamps complexity_definition (cognitive vs cyclomatic)."""

    def test_complexity_emits_complexity_definition(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["complexity"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "complexity")
        _assert_definition_field(data["summary"], "complexity_definition", "complexity")


class TestPreflightDefinitions:
    """`roam preflight` stamps risk_level + sub-block definitions."""

    def test_preflight_emits_risk_level_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["preflight", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "preflight")
        _assert_definition_field(data["summary"], "risk_level_definition", "preflight")

    def test_preflight_blast_radius_block_has_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["preflight", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "preflight")
        blast = data.get("blast_radius") or {}
        # Sub-blocks use the same _definition pattern but they live
        # outside summary, so call _assert_definition_field with the
        # block as the "summary" arg.
        _assert_definition_field(blast, "affected_symbols_definition", "preflight.blast_radius")

    def test_preflight_complexity_block_has_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["preflight", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "preflight")
        compl = data.get("complexity") or {}
        _assert_definition_field(compl, "complexity_definition", "preflight.complexity")


class TestDeadDefinitions:
    """`roam dead` stamps dead_export_definition + action_definition."""

    def test_dead_emits_dead_export_definition(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "dead")
        _assert_definition_field(data["summary"], "dead_export_definition", "dead")

    def test_dead_emits_action_definition(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "dead")
        _assert_definition_field(data["summary"], "action_definition", "dead")


class TestInvariantsDefinitions:
    """`roam invariants` stamps invariants_definition + breaking_risk_definition."""

    def test_invariants_emits_invariants_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["invariants", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "invariants")
        _assert_definition_field(data["summary"], "invariants_definition", "invariants")

    def test_invariants_emits_breaking_risk_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["invariants", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "invariants")
        _assert_definition_field(data["summary"], "breaking_risk_definition", "invariants")


# ---------------------------------------------------------------------------
# W331b — finish the W329 sweep for the remaining 3 high-signal commands:
# coverage-gaps, audit-trail-conformance-check, article-12-check.
# ---------------------------------------------------------------------------


class TestCoverageGapsDefinitions:
    """`roam coverage-gaps` stamps coverage_pct_definition on the gate-traversal envelope."""

    def test_coverage_gaps_emits_coverage_pct_definition(self, cli_runner, indexed_project):
        # `--gate validate_email` triggers the gate-traversal branch
        # (the one that emits coverage_pct in the gate-reachability
        # sense). validate_email is a real method in the fixture so the
        # gate-lookup succeeds and we exercise the main summary path.
        result = invoke_cli(
            cli_runner,
            ["coverage-gaps", "--gate", "validate_email"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = _parse_json(result, "coverage-gaps")
        _assert_definition_field(
            data["summary"], "coverage_pct_definition", "coverage-gaps"
        )


class TestAuditTrailConformanceDefinitions:
    """`roam audit-trail-conformance-check` stamps chain_compliance_score_definition."""

    def test_audit_trail_conformance_emits_score_definition(
        self, cli_runner, indexed_project
    ):
        # The fixture project has no audit trail, so the command emits
        # the explicit `no_trail` envelope. That envelope MUST still
        # carry chain_compliance_score_definition so consumers reading a
        # null score know what the score WOULD have measured.
        result = invoke_cli(
            cli_runner,
            ["audit-trail-conformance-check"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = _parse_json(result, "audit-trail-conformance-check")
        _assert_definition_field(
            data["summary"],
            "chain_compliance_score_definition",
            "audit-trail-conformance-check",
        )


class TestArticle12CheckDefinitions:
    """`roam article-12-check` stamps governance_compliance_score_definition."""

    def test_article_12_check_emits_score_definition(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["article-12-check"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "article-12-check")
        _assert_definition_field(
            data["summary"],
            "governance_compliance_score_definition",
            "article-12-check",
        )

    def test_article_12_definition_uses_assurance_safe_wording(
        self, cli_runner, indexed_project
    ):
        """The article-12-check definition must use "maps to" / "supports
        evidence for" wording per CLAUDE.md agentic-assurance guardrails
        — never "certifies" / "makes compliant".
        """
        result = invoke_cli(
            cli_runner, ["article-12-check"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "article-12-check")
        value = data["summary"]["governance_compliance_score_definition"].lower()
        forbidden = ("certifies", "certified", "makes compliant", "is compliant")
        for term in forbidden:
            assert term not in value, (
                f"article-12-check definition contains forbidden wording {term!r}: {value!r}"
            )
        # At least ONE of the assurance-safe phrasings must be present.
        approved = ("maps to", "supports evidence", "readiness")
        assert any(phrase in value for phrase in approved), (
            f"article-12-check definition lacks assurance-safe wording "
            f"(expected one of {approved}): {value!r}"
        )


# ---------------------------------------------------------------------------
# Drift guard on the shared constants module.
# ---------------------------------------------------------------------------


def test_metric_definitions_module_all_constants_non_empty() -> None:
    """Every constant in ``ALL_DEFINITIONS`` is a non-empty string."""
    from roam.output.metric_definitions import ALL_DEFINITIONS

    assert ALL_DEFINITIONS, "ALL_DEFINITIONS should not be empty"
    for name, value in ALL_DEFINITIONS.items():
        assert isinstance(value, str), f"{name} should be str, got {type(value).__name__}"
        assert value.strip(), f"{name} is empty / whitespace-only"


def test_metric_definitions_have_no_byte_identical_collisions() -> None:
    """Two different metrics must not share the same definition string.

    If two constants are byte-identical, they should be deduplicated into
    one constant. This guard catches accidental copy-paste before it
    becomes Pattern 3a drift.
    """
    from roam.output.metric_definitions import ALL_DEFINITIONS

    seen: dict[str, str] = {}
    collisions: list[str] = []
    for name, value in ALL_DEFINITIONS.items():
        if value in seen:
            collisions.append(f"{name} == {seen[value]} ({value!r})")
        seen[value] = name
    assert not collisions, (
        "Byte-identical metric definitions found — deduplicate:\n  "
        + "\n  ".join(collisions)
    )


def test_metric_definitions_module_is_importable() -> None:
    """The module imports cleanly with no side effects."""
    import roam.output.metric_definitions as md

    assert hasattr(md, "ALL_DEFINITIONS")
    assert hasattr(md, "BLAST_RADIUS_AFFECTED_SYMBOLS")
    assert hasattr(md, "HEALTH_SCORE_DEFINITION")
    assert hasattr(md, "COGNITIVE_COMPLEXITY_DEFINITION")
    assert hasattr(md, "DEAD_EXPORT_DEFINITION")
    assert hasattr(md, "INVARIANTS_DEFINITION")
    assert hasattr(md, "PREFLIGHT_RISK_LEVEL_DEFINITION")
    # W331b additions.
    assert hasattr(md, "COVERAGE_PCT_DEFINITION")
    assert hasattr(md, "GATE_VIOLATION_DEFINITION")
    assert hasattr(md, "CHAIN_COMPLIANCE_SCORE_DEFINITION")
    assert hasattr(md, "ARTICLE_12_READINESS_DEFINITION")
