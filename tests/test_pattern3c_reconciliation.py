"""Tests for Pattern 3c vocabulary reconciliation (Wave 17.2).

After W16.3 reconciled ``ai_rot`` between dashboard and vibe-check,
5 more shared-metric disagreements remained:

A — ``cycles`` (health, describe, agent-export reported different counts)
B — god-component naming chaos (``god_objects``, ``god_classes``,
    ``god_components`` across 18 files)
C — ``compliance_score`` (audit_trail_conformance_check 0/6 vs
    article-12-check 4/6 — GENUINELY different metrics, label-only fix)
D — ``public_symbols`` (api 3931 vs docs-coverage 1206 — GENUINELY
    different inclusion criteria, label both)
E — ``health_label`` thresholds (dashboard vs vibe-check use coinciding
    "HEALTHY" word on different axes — label-only fix)

The fix mirrors W16.3: canonical computations live in ``roam.quality.*``;
every consuming command emits a ``<metric>_definition`` (or
``<metric>_inclusion_criterion`` / ``label_axis_definition`` for C/D/E)
field so agents reading the envelope know exactly what the number means.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli, parse_json_output

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def cyclic_project(tmp_path):
    """Small project with a 2-symbol cycle across 2 files.

    Two-file cycle keeps the cycle 'actionable' under
    ``mark_actionable_cycles`` (spans >=2 files, no test files).
    """
    repo = tmp_path / "cycp"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "a.py").write_text("from b import beta\n\n\ndef alpha():\n    return beta()\n")
    (repo / "b.py").write_text("from a import alpha\n\n\ndef beta():\n    return alpha()\n")
    # A handful of "public" symbols so api and docs-coverage have content
    # to count without overflowing into noise.
    (repo / "api_mod.py").write_text(
        '"""Module."""\n'
        "\n"
        "\n"
        "def public_one():\n"
        '    """Doc."""\n'
        "    return 1\n"
        "\n"
        "\n"
        "def public_two():\n"
        "    return 2\n"
        "\n"
        "\n"
        "def _private():\n"
        "    return 3\n"
    )
    git_init(repo)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        out, rc = index_in_process(repo)
        assert rc == 0, f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return repo


# ===========================================================================
# A — Cycles
# ===========================================================================


class TestCyclesReconciliation:
    def test_cycles_count_agrees_across_commands(self, cli_runner, cyclic_project, monkeypatch):
        """health, describe, agent-export must report the same canonical cycle counts."""
        monkeypatch.chdir(cyclic_project)

        h = parse_json_output(
            invoke_cli(cli_runner, ["health"], cwd=cyclic_project, json_mode=True),
            "health",
        )
        d = parse_json_output(
            invoke_cli(
                cli_runner,
                ["describe", "--agent-prompt"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "describe",
        )
        ae = parse_json_output(
            invoke_cli(
                cli_runner,
                ["agent-export"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "agent-export",
        )

        # Pull canonical numbers from each envelope.
        h_total = h["summary"].get("total_cycles")
        h_actionable = h["summary"].get("actionable_cycles")

        d_total = d.get("cycles_total")
        d_actionable = d.get("cycles_actionable")

        ae_health = ae.get("health_summary") or {}
        ae_total = ae_health.get("cycles_total")
        ae_actionable = ae_health.get("cycles_actionable")

        # Sanity — at minimum, the numbers should be defined integers.
        assert isinstance(h_total, int)
        assert isinstance(h_actionable, int)
        assert isinstance(d_total, int) or d_total == "N/A"
        assert isinstance(ae_total, int) or ae_total == 0

        # Agreement (skip when describe couldn't compute, e.g. tiny graph).
        if isinstance(d_total, int):
            assert d_total == h_total, f"describe.cycles_total={d_total} != health.total_cycles={h_total}"
            assert d_actionable == h_actionable, (
                f"describe.cycles_actionable={d_actionable} != health.actionable_cycles={h_actionable}"
            )
        assert ae_total == h_total, f"agent-export.cycles_total={ae_total} != health.total_cycles={h_total}"
        assert ae_actionable == h_actionable

    def test_each_command_has_cycles_definition_label(self, cli_runner, cyclic_project, monkeypatch):
        """Pattern 3 label fix: every cycle-emitting envelope names what it measures."""
        monkeypatch.chdir(cyclic_project)
        h = parse_json_output(
            invoke_cli(cli_runner, ["health"], cwd=cyclic_project, json_mode=True),
            "health",
        )
        d = parse_json_output(
            invoke_cli(
                cli_runner,
                ["describe", "--agent-prompt"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "describe",
        )
        ae = parse_json_output(
            invoke_cli(
                cli_runner,
                ["agent-export"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "agent-export",
        )

        assert "cycles_definition" in h["summary"], "health summary missing cycles_definition"
        # describe --agent-prompt spreads its data dict into the envelope
        # root, not the summary block.
        assert "cycles_definition" in d
        ae_health = ae.get("health_summary") or {}
        assert "cycles_definition" in ae_health

        # Every label must mention the canonical entry point so an
        # agent reading it discovers `roam health`.
        for env, name in [
            (h["summary"], "health"),
            (d, "describe"),
            (ae_health, "agent-export"),
        ]:
            defn = env["cycles_definition"]
            assert isinstance(defn, str) and len(defn) > 20
            assert "roam health" in defn, f"{name} cycles_definition should name `roam health`: {defn[:120]}"


# ===========================================================================
# B — God components
# ===========================================================================


class TestGodComponentsReconciliation:
    def test_god_components_canonical_name_used(self, cli_runner, cyclic_project, monkeypatch):
        """`god_components` (canonical name) appears in health + fingerprint envelopes."""
        monkeypatch.chdir(cyclic_project)
        h = parse_json_output(
            invoke_cli(cli_runner, ["health"], cwd=cyclic_project, json_mode=True),
            "health",
        )
        f = parse_json_output(
            invoke_cli(cli_runner, ["fingerprint"], cwd=cyclic_project, json_mode=True),
            "fingerprint",
        )

        # Health: god_components was already canonical; pin it.
        assert "god_components" in h["summary"].get("category_severity", {})
        # Fingerprint: the new canonical key must be present even though
        # the legacy `god_objects` alias is kept for back-compat.
        anti = f.get("fingerprint", {}).get("antipatterns", {})
        assert "god_components" in anti, f"fingerprint antipatterns missing `god_components`: {list(anti)}"
        # Legacy alias retained for back-compat.
        assert "god_objects" in anti or "god_components_legacy_god_objects" in anti

    def test_god_components_count_agrees(self, cli_runner, cyclic_project, monkeypatch):
        """health.god_components_total == fingerprint.antipatterns.god_components.

        Both must use the SAME canonical helper (degree-thresholded,
        utility-aware). The legacy fingerprint algorithm
        (avg_degree * 2) lives on as `god_components_legacy_god_objects`
        but is not the canonical number anymore.
        """
        monkeypatch.chdir(cyclic_project)
        h = parse_json_output(
            invoke_cli(cli_runner, ["health"], cwd=cyclic_project, json_mode=True),
            "health",
        )
        f = parse_json_output(
            invoke_cli(cli_runner, ["fingerprint"], cwd=cyclic_project, json_mode=True),
            "fingerprint",
        )

        # Health emits god_components as a list (each item) under the
        # top-level envelope key; count is the length.
        h_god_count = len(h.get("god_components", []) or [])
        f_god_count = f.get("fingerprint", {}).get("antipatterns", {}).get("god_components")
        assert h_god_count == f_god_count, (
            f"health.god_components={h_god_count} != fingerprint.antipatterns.god_components={f_god_count}"
        )

    def test_god_components_definition_label_present(self, cli_runner, cyclic_project, monkeypatch):
        """Pattern 3 label: god_components_definition must appear on both consumers."""
        monkeypatch.chdir(cyclic_project)
        h = parse_json_output(
            invoke_cli(cli_runner, ["health"], cwd=cyclic_project, json_mode=True),
            "health",
        )
        f = parse_json_output(
            invoke_cli(cli_runner, ["fingerprint"], cwd=cyclic_project, json_mode=True),
            "fingerprint",
        )

        assert "god_components_definition" in h["summary"]
        assert "god_components_definition" in f["summary"]


# ===========================================================================
# C — Compliance kind labeling
# ===========================================================================


class TestComplianceKindLabeling:
    def test_compliance_kind_definition_present(self, cli_runner, cyclic_project, monkeypatch):
        """Both compliance commands must publish compliance_kind_definition."""
        monkeypatch.chdir(cyclic_project)
        atc = parse_json_output(
            invoke_cli(
                cli_runner,
                ["audit-trail-conformance-check"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "audit-trail-conformance-check",
        )
        a12 = parse_json_output(
            invoke_cli(
                cli_runner,
                ["article-12-check"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "article-12-check",
        )

        assert "compliance_kind_definition" in atc["summary"], (
            "audit-trail-conformance-check missing compliance_kind_definition"
        )
        assert "compliance_kind_definition" in a12["summary"], "article-12-check missing compliance_kind_definition"

    def test_compliance_scores_have_distinct_kinds(self, cli_runner, cyclic_project, monkeypatch):
        """The two compliance commands measure DIFFERENT things — their
        ``compliance_kind`` identifiers must differ.
        """
        monkeypatch.chdir(cyclic_project)
        atc = parse_json_output(
            invoke_cli(
                cli_runner,
                ["audit-trail-conformance-check"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "audit-trail-conformance-check",
        )
        a12 = parse_json_output(
            invoke_cli(
                cli_runner,
                ["article-12-check"],
                cwd=cyclic_project,
                json_mode=True,
            ),
            "article-12-check",
        )

        atc_kind = atc["summary"].get("compliance_kind")
        a12_kind = a12["summary"].get("compliance_kind")
        assert atc_kind == "audit_trail_chain_integrity"
        assert a12_kind == "eu_ai_act_governance_readiness"
        assert atc_kind != a12_kind, "compliance kinds must differ — these are distinct metrics"


# ===========================================================================
# D — Public symbols inclusion criterion
# ===========================================================================


class TestPublicSymbolsLabeling:
    def test_public_symbols_count_agrees_or_documented(self, cli_runner, cyclic_project, monkeypatch):
        """`api` and `docs-coverage` either agree on the count OR
        publish distinct inclusion criteria so the disagreement is
        explicit. Pattern 3c label-fix.
        """
        monkeypatch.chdir(cyclic_project)
        api = parse_json_output(
            invoke_cli(cli_runner, ["api"], cwd=cyclic_project, json_mode=True),
            "api",
        )
        dc = parse_json_output(
            invoke_cli(cli_runner, ["docs-coverage"], cwd=cyclic_project, json_mode=True),
            "docs-coverage",
        )

        api_crit = api["summary"].get("public_symbols_inclusion_criterion")
        dc_crit = dc["summary"].get("public_symbols_inclusion_criterion")
        assert api_crit == "no_underscore_prefix", f"api criterion expected `no_underscore_prefix`, got {api_crit}"
        assert dc_crit == "has_export_marker", f"docs-coverage criterion expected `has_export_marker`, got {dc_crit}"

        # Both envelopes must carry the public_symbols_definition label.
        assert "public_symbols_definition" in api["summary"]
        assert "public_symbols_definition" in dc["summary"]


# ===========================================================================
# E — Health label axis
# ===========================================================================


class TestHealthLabelAxisLabeling:
    def test_health_label_axis_definition_present(self, cli_runner, cyclic_project, monkeypatch):
        """Dashboard and vibe-check use coinciding `HEALTHY` labels on
        different axes (project-health vs ai-rot). Both must publish
        ``label_axis_definition`` so agents never confuse them.
        """
        monkeypatch.chdir(cyclic_project)
        d = parse_json_output(
            invoke_cli(cli_runner, ["dashboard"], cwd=cyclic_project, json_mode=True),
            "dashboard",
        )
        v = parse_json_output(
            invoke_cli(cli_runner, ["vibe-check"], cwd=cyclic_project, json_mode=True),
            "vibe-check",
        )

        # Dashboard's label axis is in the health block.
        d_health = d.get("health", {})
        assert d_health.get("label_axis") == "project_health_score"
        assert "label_axis_definition" in d_health

        # Vibe-check's label axis is in the summary block.
        assert v["summary"].get("label_axis") == "ai_rot_score"
        assert "label_axis_definition" in v["summary"]

        # The two axis definitions must reference different ranges so
        # the distinction is unambiguous.
        d_def = d_health["label_axis_definition"]
        v_def = v["summary"]["label_axis_definition"]
        assert "higher = healthier" in d_def
        assert "lower = healthier" in v_def
