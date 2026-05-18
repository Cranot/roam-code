"""W641-followup-G — ``roam dark-matter`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (cluster extension, seventh axis from W641 +
followup-A/B/C/D/E):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third axis
after W547 severity + W596 confidence). Follow-ups extended the discipline
to ``cmd_impact`` (W641-followup-A), ``cmd_critique`` (W641-followup-B),
``cmd_pr_bundle`` (W641-followup-C), ``cmd_attest`` (W641-followup-D), and
``cmd_diff`` (W641-followup-E). ``cmd_dark_matter`` is the cochange-coupling
specialist sibling: it detects hidden file-pair couplings (files that
historically change together despite no structural dependency).

cmd_dark_matter has NO native severity vocabulary — it emits per-pair
metrics (``npmi``, ``lift``, ``strength``, ``cochange_count``) but no
rollup risk tier. The canonical W631 risk-LEVEL bucket is derived from
two rollup signals:

  * ``total_pairs`` — the count of hidden couplings detected after NPMI +
    min-cochange filtering (already capped by ``-n``).
  * ``max_strength`` — the maximum cochange-strength across pairs (the
    ratio ``cochanges / avg_commits`` from ``dark_matter_edges``).

Strength is bounded below by 0; it can exceed 1 when ``cochanges >
avg_commits`` (the pair literally moves together MORE often than either
file moves alone). The 0.4 / 0.7 thresholds on the strength axis are
calibrated against ~0.5 being the "they move together half as often as
either moves at all" point — meaningful but not yet ubiquitous coupling.

This module pins the W641-followup-G emit contract on the JSON envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``_dark_matter_risk_level`` (cochange-coupling rollup threshold helper,
  mirroring the cmd_impact / cmd_diff polarity). Always in the W631
  closed-set vocabulary (``critical``/``high``/``medium``/``low``); empty
  rollup or any unknown signal floors to ``low`` (W531 CI-safety).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone: the verdict
  line names the canonical bucket without any other envelope field.
* Top-level ``risk_level_canonical`` + ``risk_rank`` mirrors so consumers
  that read the envelope head without descending into ``summary`` see the
  canonical bucket too (parity with the W641 cluster contract).
* Unknown-input safe-floor: a negative / non-numeric input collapses to
  ``risk_level_canonical="low"`` AND records a marker on
  ``summary.warnings_out`` under ``dark_matter_unknown_severity:<value>``
  so Pattern-2 silent-fallback discipline stays loud.

Conservative-on-critical: cmd_dark_matter structurally aligns with
cmd_impact / cmd_diff / cmd_critique — single-axis cochange-coupling
signal floored at ``high``. ``critical`` is reserved for the multi-factor
composite-score commands (cmd_attest's ``_collect_risk``). The W531
CI-safety lesson: a threshold wobble MUST NOT promote a finding into a
CI-gating rank.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 — relative import after sys.path mutation
    invoke_cli,
    parse_json_output,
)

from roam.commands.cmd_dark_matter import _dark_matter_risk_level  # noqa: E402
from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: indexed project with dark-matter pairs (mirrors test_dark_matter.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def dark_matter_project(project_factory, monkeypatch):
    """Project where billing.py <-> reporting.py co-change 3 times with no
    import edge — guarantees a non-empty dark-matter rollup."""
    billing_v1 = "def get_invoice(id):\n    return {'id': id, 'amount': 100}\n"
    reporting_v1 = "def monthly_report():\n    return {'total': 500}\n"
    models_v1 = "from billing import get_invoice\n\ndef load_model():\n    inv = get_invoice(1)\n    return inv\n"

    billing_v2 = "def get_invoice(id):\n    return {'id': id, 'amount': 200, 'tax': 20}\n"
    reporting_v2 = "def monthly_report():\n    return {'total': 600, 'tax_total': 60}\n"

    billing_v3 = "def get_invoice(id):\n    return {'id': id, 'amount': 300, 'tax': 30, 'discount': 10}\n"
    reporting_v3 = "def monthly_report():\n    return {'total': 700, 'tax_total': 70, 'discounts': 10}\n"

    proj = project_factory(
        {
            "billing.py": billing_v1,
            "reporting.py": reporting_v1,
            "models.py": models_v1,
        },
        extra_commits=[
            ({"billing.py": billing_v2, "reporting.py": reporting_v2}, "add tax"),
            ({"billing.py": billing_v3, "reporting.py": reporting_v3}, "add discount"),
        ],
    )
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def empty_dark_matter_project(project_factory, monkeypatch):
    """Single-commit project with no co-change history — exercises empty path."""
    proj = project_factory({"app.py": "def main():\n    return 1\n"})
    monkeypatch.chdir(proj)
    return proj


# ---------------------------------------------------------------------------
# Envelope-level contract — happy path
# ---------------------------------------------------------------------------


class TestEnvelopeFields:
    """Pin the W641-followup-G emit contract on the JSON envelope."""

    def test_envelope_has_risk_level_canonical(self, dark_matter_project, cli_runner, monkeypatch):
        """summary.risk_level_canonical is a string in the canonical W631 set."""
        monkeypatch.chdir(dark_matter_project)
        result = invoke_cli(
            cli_runner,
            ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
            cwd=dark_matter_project,
            json_mode=True,
        )
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-followup-G: summary.risk_level_canonical missing"
        assert isinstance(summary["risk_level_canonical"], str)
        assert summary["risk_level_canonical"] in RISK_LEVELS, (
            f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
        )
        # Top-level mirror lands (parity with cmd_impact / cmd_critique /
        # cmd_attest / cmd_diff W641 cluster).
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]

    def test_envelope_has_risk_rank(self, dark_matter_project, cli_runner, monkeypatch):
        """summary.risk_rank is an int matching risk_rank(risk_level_canonical)."""
        monkeypatch.chdir(dark_matter_project)
        result = invoke_cli(
            cli_runner,
            ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
            cwd=dark_matter_project,
            json_mode=True,
        )
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        assert "risk_rank" in summary, "W641-followup-G: summary.risk_rank missing"
        assert isinstance(summary["risk_rank"], int)
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
            f"floor drift: risk_rank={summary['risk_rank']} "
            f"but risk_rank({summary['risk_level_canonical']!r})="
            f"{risk_rank(summary['risk_level_canonical'])}"
        )
        assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach the envelope"
        # Top-level mirror lands.
        assert data.get("risk_rank") == summary["risk_rank"]

    def test_no_findings_emits_low_floor(self, empty_dark_matter_project, cli_runner, monkeypatch):
        """Empty cochange rollup safe-floors canonical risk_level to 'low'.

        Mirrors the cmd_diff W641-followup-E no-changes safe-floor and the
        cmd_attest W641-followup-D no-changes safe-floor: canonical fields
        ARE emitted on the empty / no-cochange path so consumers downstream
        call ``risk_rank(...)`` unconditionally.
        """
        monkeypatch.chdir(empty_dark_matter_project)
        result = invoke_cli(cli_runner, ["dark-matter"], cwd=empty_dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        # Empty rollup floors to canonical low (rank 1).
        assert summary.get("risk_level_canonical") == "low", (
            f"empty dark-matter rollup must safe-floor to 'low'; got {summary.get('risk_level_canonical')!r}"
        )
        assert summary.get("risk_rank") == 1
        # Top-level mirrors land on the empty path too.
        assert data.get("risk_level_canonical") == "low"
        assert data.get("risk_rank") == 1

    def test_verdict_includes_risk_level_token(self, dark_matter_project, cli_runner, monkeypatch):
        """LAW 6: verdict line names the canonical risk_level standalone.

        On the happy path (pairs present) the verdict terminates on a
        closed-enum ``(risk_level <canonical>)`` parenthesis. Regex pinned
        so a future edit can't silently drop the suffix.
        """
        monkeypatch.chdir(dark_matter_project)
        result = invoke_cli(
            cli_runner,
            ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
            cwd=dark_matter_project,
            json_mode=True,
        )
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        verdict = summary["verdict"]
        canonical = summary["risk_level_canonical"]
        # Regex on the closed-enum parenthesis form — exactly one
        # ``(risk_level <canonical>)`` suffix per the LAW 6 contract.
        assert re.search(r"\(risk_level (critical|high|medium|low)\)", verdict), (
            f"LAW 6 violated: verdict {verdict!r} missing closed-enum (risk_level <canonical>) suffix"
        )
        assert f"risk_level {canonical}" in verdict, (
            f"LAW 6 violated: verdict {verdict!r} does not name emitted canonical risk_level {canonical!r}"
        )

    def test_canonical_field_not_omitted_on_partial_success(self, empty_dark_matter_project, cli_runner, monkeypatch):
        """Even on the no-cochange / partial-success path, canonical fields land.

        Mirrors the W641-followup-D/E discipline: a degraded / empty branch
        must NOT drop the canonical risk-LEVEL fields. Agents downstream
        call ``risk_rank(summary["risk_level_canonical"])`` unconditionally
        without None-handling.
        """
        monkeypatch.chdir(empty_dark_matter_project)
        result = invoke_cli(cli_runner, ["dark-matter"], cwd=empty_dark_matter_project, json_mode=True)
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        # No-cochange path may set state="no_cochange" + partial_success=True.
        # Canonical fields land regardless.
        assert "risk_level_canonical" in summary, "canonical field MUST NOT be dropped on no-cochange / partial path"
        assert "risk_rank" in summary
        # Empty rollup floors to canonical low (rank 1).
        assert summary["risk_level_canonical"] == "low", (
            f"empty rollup must safe-floor to 'low'; got {summary['risk_level_canonical']!r}"
        )
        assert summary["risk_rank"] == 1


# ---------------------------------------------------------------------------
# Cochange-coupling → risk projection — unit-level (no DB / no engine call)
# ---------------------------------------------------------------------------


class TestCochangeCouplingProjection:
    """Direct unit tests on ``_dark_matter_risk_level`` — every branch canonical.

    Threshold table (mirrors W641-followup-A/E polarity, OR-aggregated):
      total_pairs >= 20 OR max_strength >= 0.7  -> "high"
      total_pairs >=  5 OR max_strength >= 0.4  -> "medium"
      total_pairs >   0 OR max_strength >  0    -> "low"
      total_pairs ==  0 AND max_strength == 0.0 -> "low"
    """

    def test_empty_rollup_projects_to_low(self):
        """total_pairs == 0 AND max_strength == 0.0 → canonical 'low'."""
        assert _dark_matter_risk_level(0, 0.0) == "low"

    def test_low_threshold_boundary(self):
        """Small rollup (< medium thresholds) → canonical 'low'."""
        assert _dark_matter_risk_level(1, 0.0) == "low"
        assert _dark_matter_risk_level(4, 0.39) == "low"
        assert _dark_matter_risk_level(0, 0.39) == "low"
        # Below count and strength medium thresholds simultaneously.
        assert _dark_matter_risk_level(3, 0.2) == "low"

    def test_medium_threshold_boundary(self):
        """total_pairs >= 5 or max_strength >= 0.4 → canonical 'medium'."""
        # Count axis at threshold.
        assert _dark_matter_risk_level(5, 0.0) == "medium"
        # Strength axis at threshold.
        assert _dark_matter_risk_level(0, 0.4) == "medium"
        # Both below high thresholds.
        assert _dark_matter_risk_level(19, 0.0) == "medium"
        assert _dark_matter_risk_level(0, 0.69) == "medium"
        assert _dark_matter_risk_level(10, 0.5) == "medium"

    def test_high_strength_projects_to_high(self):
        """total_pairs >= 20 or max_strength >= 0.7 → canonical 'high'."""
        # Count axis at threshold.
        assert _dark_matter_risk_level(20, 0.0) == "high"
        # Strength axis at threshold.
        assert _dark_matter_risk_level(0, 0.7) == "high"
        # Both axes high.
        assert _dark_matter_risk_level(50, 0.9) == "high"
        # Synthetic: dense coupling fixture.
        assert _dark_matter_risk_level(30, 0.8) == "high"

    def test_aggregation_uses_max(self):
        """Max-tier wins: high signal on either axis floors to ``high``.

        cmd_dark_matter uses an OR-aggregation across the two rollup axes
        (count, strength) — the worst signal wins (max-severity polarity,
        mirrors W641-followup-B cmd_critique / W641-followup-E cmd_diff).
        """
        # Strength axis high, count axis low → ``high``.
        assert _dark_matter_risk_level(1, 0.8) == "high"
        # Count axis high, strength axis low → ``high``.
        assert _dark_matter_risk_level(50, 0.0) == "high"
        # Strength axis medium, count axis low → ``medium``.
        assert _dark_matter_risk_level(2, 0.5) == "medium"
        # Count axis medium, strength axis low → ``medium``.
        assert _dark_matter_risk_level(10, 0.1) == "medium"

    def test_conservative_no_critical(self):
        """Conservative-on-critical: cmd_dark_matter saturates at ``high``.

        Per the W641-followup-A/B/E discipline: single-axis cochange-
        coupling signal MUST NOT escalate to ``critical`` without multi-
        factor composite-score evidence (cmd_attest's ``_collect_risk``).
        The W531 CI-safety lesson: a threshold wobble can't promote a
        finding into a CI-gating rank.
        """
        # Even extreme cochange-coupling stays at ``high`` (no ``critical``).
        assert _dark_matter_risk_level(10_000, 5.0) == "high"
        assert _dark_matter_risk_level(50_000, 100.0) == "high"
        # Direct assertion that ``critical`` is never emitted by the helper.
        for pairs, strength in (
            (20, 0.7),
            (1_000, 1.0),
            (100, 0.99),
            (10_000, 50.0),
        ):
            assert _dark_matter_risk_level(pairs, strength) != "critical", (
                f"helper escalated to critical on ({pairs}, {strength}) — "
                f"single-axis signal must floor at high per W641 discipline"
            )

    def test_unknown_severity_safe_floor(self):
        """Unknown / negative inputs → 'low' floor + warnings_out marker.

        Mirrors the W918 alerts / W989 pr-risk / W641-followup-B critique /
        W641-followup-D attest / W641-followup-E diff ``warnings_out``
        discipline.
        """
        # Negative counts — invalid input safe-floors + records marker.
        warnings_out: list[str] = []
        result = _dark_matter_risk_level(-1, 0.5, warnings_out=warnings_out)
        assert result == "low", f"negative input must safe-floor to 'low'; got {result!r}"
        assert any("dark_matter_unknown_severity:negative" in w for w in warnings_out), (
            f"warnings_out must record negative input as "
            f"dark_matter_unknown_severity:negative(...); got {warnings_out!r}"
        )

        # Negative strength.
        warnings_out_b: list[str] = []
        assert _dark_matter_risk_level(5, -0.5, warnings_out=warnings_out_b) == "low"
        assert any("dark_matter_unknown_severity:negative" in w for w in warnings_out_b)

        # Non-numeric — invalid type safe-floors + records marker.
        warnings_out2: list[str] = []
        result2 = _dark_matter_risk_level(  # type: ignore[arg-type]
            "bogus", 0.5, warnings_out=warnings_out2
        )
        assert result2 == "low"
        assert any("dark_matter_unknown_severity:non_numeric" in w for w in warnings_out2)

    def test_rank_matches_canonical(self):
        """Direct rank-comparator test (no envelope round-trip)."""
        assert risk_rank("critical") > risk_rank("high")
        assert risk_rank("high") > risk_rank("medium")
        assert risk_rank("medium") > risk_rank("low")
        assert risk_rank("low") == 1
        # Round-trip every ``_dark_matter_risk_level`` output through
        # ``normalize_risk_level`` — the helper MUST already emit canonical
        # tokens with no further normalization needed.
        for pairs, strength in (
            (0, 0.0),
            (3, 0.2),
            (5, 0.0),
            (0, 0.4),
            (20, 0.0),
            (0, 0.7),
            (10_000, 100.0),
        ):
            out = _dark_matter_risk_level(pairs, strength)
            assert out in RISK_LEVELS, f"_dark_matter_risk_level({pairs}, {strength}) emitted non-canonical {out!r}"
            assert normalize_risk_level(out) == out, (
                f"_dark_matter_risk_level({pairs}, {strength}) emit {out!r} "
                f"does not round-trip through normalize_risk_level"
            )


# ---------------------------------------------------------------------------
# Drift guards — canonical set + projection-helper closure + cluster parity
# ---------------------------------------------------------------------------


class TestCanonicalSetDriftGuard:
    """Pin W631 + W641-followup-G vocabularies.

    Mirrors the W641 / W641-followup-A/B/C/D/E drift-guard discipline: if a
    future edit ever changes the canonical vocabulary, this test fails fast
    so the dark-matter emit contract can be re-evaluated alongside.
    """

    def test_canonical_set_drift_guard(self):
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_dark_matter_helper_only_emits_canonical_tokens(self):
        """Every ``_dark_matter_risk_level`` branch lands on a canonical token.

        Closed-enum invariant pinned at the source so a future edit
        can't reintroduce a non-canonical intermediate token.
        """
        for pairs, strength, expected in [
            (0, 0.0, "low"),
            (1, 0.0, "low"),
            (4, 0.39, "low"),
            (5, 0.0, "medium"),
            (0, 0.4, "medium"),
            (19, 0.69, "medium"),
            (20, 0.0, "high"),
            (0, 0.7, "high"),
            (10_000, 5.0, "high"),
        ]:
            assert _dark_matter_risk_level(pairs, strength) == expected, (
                f"_dark_matter_risk_level({pairs}, {strength}) emit drift: expected {expected!r}"
            )
        # Floors + invalid inputs — all collapse to canonical low.
        assert _dark_matter_risk_level(-5, 0.5) == "low"
        assert _dark_matter_risk_level(5, -0.5) == "low"

    def test_w641_cluster_field_name_consistency(self, dark_matter_project, cli_runner, monkeypatch):
        """Field-name spelling matches sibling cmd_attest / cmd_critique / cmd_diff exactly.

        Drift guard: if a future edit renames ``risk_level_canonical`` →
        ``risk_level_normalised`` (or any other near-miss), the cross-
        command Pattern-3a vocabulary breaks. The W641 cluster pins a
        single field name — this test asserts cmd_dark_matter conforms.
        """
        monkeypatch.chdir(dark_matter_project)
        result = invoke_cli(
            cli_runner,
            ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
            cwd=dark_matter_project,
            json_mode=True,
        )
        data = parse_json_output(result, "dark-matter")
        summary = data["summary"]
        # Field names MUST be exact — no aliases, no near-misses.
        assert "risk_level_canonical" in summary, (
            "W641 cluster field-name drift: cmd_dark_matter emits something "
            "other than 'risk_level_canonical' on the summary block"
        )
        assert "risk_rank" in summary, (
            "W641 cluster field-name drift: cmd_dark_matter emits something other than 'risk_rank' on the summary block"
        )
        # Top-level mirrors use identical names.
        assert "risk_level_canonical" in data
        assert "risk_rank" in data

        # Cross-check: import the same constants every sibling imports.
        # If any cluster member is removed / renamed at the source, this
        # test fails fast (catches drift before it ships).
        from roam.output.risk import (  # noqa: F401 — name-pin only
            RISK_LEVELS,
            normalize_risk_level,
            risk_rank,
        )

    def test_w641_cluster_field_name_consistency_via_ast(self):
        """AST-grep cmd_dark_matter.py for the exact field-name spelling.

        Pattern-3a guard against silent renames: if a future commit renames
        ``risk_level_canonical`` at the source, the live-envelope drift
        guard above catches it on the wire, and this static check catches
        it at the literal string. Both axes must pass per the W978
        two-instrument-rule discipline (CP44).
        """
        src = Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_dark_matter.py"
        text = src.read_text(encoding="utf-8")
        # Field-name spelling — must match cmd_impact / cmd_diff / cmd_attest.
        assert "risk_level_canonical" in text, (
            "cmd_dark_matter.py source is missing the exact 'risk_level_canonical' field name — cluster spelling drift?"
        )
        assert "risk_rank" in text, "cmd_dark_matter.py source is missing the exact 'risk_rank' field name"
        # Imports from the canonical helper module.
        assert "from roam.output.risk import" in text, (
            "cmd_dark_matter.py must import from roam.output.risk (the canonical "
            "W631 risk-LEVEL helper module) — cluster import-source drift?"
        )
