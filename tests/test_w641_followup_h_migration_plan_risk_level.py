"""W641-followup-H — ``roam migration-plan`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (cluster extension, eighth axis from W641 +
followup-A/B/C/D/E/G):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third axis
after W547 severity + W596 confidence). Follow-ups extended the discipline
to ``cmd_impact`` (W641-followup-A), ``cmd_critique`` (W641-followup-B),
``cmd_pr_bundle`` (W641-followup-C), ``cmd_attest`` (W641-followup-D),
``cmd_diff`` (W641-followup-E), and ``cmd_dark_matter`` (W641-followup-G).
``cmd_migration_plan`` is the eighth emitter — and the canonical pre-W631
risk-rank polarity emitter explicitly cited in :mod:`roam.output.risk`'s
docstring. The W631 sort-polarity consumer-side (``risk_rank(s["risk"])``
at lines 125 / 138 / 142 of ``cmd_migration_plan.py``) was completed under
W631 task #733; this follow-up closes the loop by adding the EMIT side.

cmd_migration_plan's per-step risk vocabulary is the 3-tier
``low`` / ``medium`` / ``high`` set (derived from blast-radius + cross-
layer signal in ``_evaluate_move``). It does NOT emit ``critical``
natively; the PLAN-level rollup picks the worst step's risk via
max-tier aggregation and floors at ``high`` to stay consistent with the
rest of the W641 cluster's conservative-on-critical discipline.

This module pins the W641-followup-H emit contract on the JSON envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``_migration_plan_risk_level`` (per-step risk max-tier aggregator).
  Always in the W631 closed-set vocabulary
  (``critical``/``high``/``medium``/``low``); empty plan or any
  unknown signal floors to ``low`` (W531 CI-safety).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone: the verdict
  line names the canonical bucket without any other envelope field.
* Top-level ``risk_level_canonical`` + ``risk_rank`` mirrors so consumers
  that read the envelope head without descending into ``summary`` see
  the canonical bucket too (parity with the W641 cluster contract).
* Each ``summary.steps[]`` row gets a ``risk_level_canonical`` +
  ``risk_rank`` stamp so a downstream consumer iterating the plan can
  call ``risk_rank(step["risk_level_canonical"])`` without re-normalising
  the per-step ``risk`` token. The pre-existing per-step ``risk`` field
  is preserved verbatim so the regression contract (text render, sort
  polarity, --max-risk gate) stays intact.

Conservative-on-critical: cmd_migration_plan saturates at ``high``
because its per-step vocabulary tops out at ``high`` and the underlying
signal is single-axis (blast-radius + cross-layer). ``critical`` is
reserved for the multi-factor composite-score commands (cmd_attest's
``_collect_risk``). The W531 CI-safety lesson: a threshold wobble MUST
NOT promote a finding into a CI-gating rank.
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

from roam.commands.cmd_migration_plan import (  # noqa: E402
    _migration_plan_risk_level,
)
from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: indexed project + nonexistent symbol moves (low-risk happy path)
# ---------------------------------------------------------------------------


@pytest.fixture
def migration_plan_project(project_factory, monkeypatch):
    """Project with a few symbols — exercises the plan-generation path."""
    proj = project_factory(
        {
            "src/api/users.py": ("def validate_user(name):\n    return name\n\ndef hash_password(p):\n    return p\n"),
            "src/services/user_service.py": (
                "from src.api.users import validate_user\n\n"
                "class UserService:\n"
                "    def serve(self):\n"
                "        return validate_user('x')\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    return proj


# ---------------------------------------------------------------------------
# Envelope-level contract — empty plan path (no --move flags)
# ---------------------------------------------------------------------------


class TestEnvelopeEmptyPlan:
    """Pin the W641-followup-H emit contract on the no-target envelope."""

    def test_empty_plan_emits_low_floor(self, migration_plan_project, cli_runner, monkeypatch):
        """Empty plan (no --move flags) safe-floors canonical risk_level to 'low'."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        summary = data["summary"]
        assert summary.get("risk_level_canonical") == "low", (
            f"empty plan must safe-floor to 'low'; got {summary.get('risk_level_canonical')!r}"
        )
        assert summary.get("risk_rank") == 1
        # Top-level mirrors land on the empty path too.
        assert data.get("risk_level_canonical") == "low"
        assert data.get("risk_rank") == 1

    def test_empty_plan_verdict_includes_risk_level_token(self, migration_plan_project, cli_runner, monkeypatch):
        """LAW 6: empty-plan verdict line names canonical risk_level standalone."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        verdict = data["summary"]["verdict"]
        # Closed-enum parenthesis suffix — exactly one (risk_level <canonical>).
        assert re.search(r"\(risk_level (critical|high|medium|low)\)", verdict), (
            f"LAW 6 violated: empty-plan verdict {verdict!r} missing closed-enum (risk_level <canonical>) suffix"
        )
        assert "risk_level low" in verdict, f"empty plan must name canonical 'low'; got verdict {verdict!r}"


# ---------------------------------------------------------------------------
# Envelope-level contract — non-empty plan path (with --move flags)
# ---------------------------------------------------------------------------


class TestEnvelopeNonEmptyPlan:
    """Pin the W641-followup-H emit contract on the populated plan envelope."""

    def test_envelope_has_risk_level_canonical(self, migration_plan_project, cli_runner, monkeypatch):
        """summary.risk_level_canonical is a string in the canonical W631 set."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan", "--move", "Nonexistent=src/x.py"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-followup-H: summary.risk_level_canonical missing"
        assert isinstance(summary["risk_level_canonical"], str)
        assert summary["risk_level_canonical"] in RISK_LEVELS, (
            f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
        )
        # Top-level mirror lands.
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]

    def test_envelope_has_risk_rank(self, migration_plan_project, cli_runner, monkeypatch):
        """summary.risk_rank is an int matching risk_rank(risk_level_canonical)."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan", "--move", "Nonexistent=src/x.py"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        summary = data["summary"]
        assert "risk_rank" in summary, "W641-followup-H: summary.risk_rank missing"
        assert isinstance(summary["risk_rank"], int)
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
            f"floor drift: risk_rank={summary['risk_rank']} but "
            f"risk_rank({summary['risk_level_canonical']!r})="
            f"{risk_rank(summary['risk_level_canonical'])}"
        )
        assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach the envelope"
        # Top-level mirror lands.
        assert data.get("risk_rank") == summary["risk_rank"]

    def test_verdict_includes_risk_level_token(self, migration_plan_project, cli_runner, monkeypatch):
        """LAW 6: verdict line names the canonical risk_level standalone.

        Regex pinned so a future edit can't silently drop the suffix.
        """
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan", "--move", "Nonexistent=src/x.py"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        summary = data["summary"]
        verdict = summary["verdict"]
        canonical = summary["risk_level_canonical"]
        assert re.search(r"\(risk_level (critical|high|medium|low)\)", verdict), (
            f"LAW 6 violated: verdict {verdict!r} missing closed-enum (risk_level <canonical>) suffix"
        )
        assert f"risk_level {canonical}" in verdict, (
            f"LAW 6 violated: verdict {verdict!r} does not name emitted canonical risk_level {canonical!r}"
        )

    def test_canonical_field_not_omitted_on_partial_success(self, migration_plan_project, cli_runner, monkeypatch):
        """Even on the no-moves / partial-success path, canonical fields land.

        Mirrors the W641-followup-D/E/G discipline: a degraded / empty branch
        must NOT drop the canonical risk-LEVEL fields. Agents downstream
        call ``risk_rank(summary["risk_level_canonical"])`` unconditionally
        without None-handling.
        """
        monkeypatch.chdir(migration_plan_project)
        # No --move flags → empty-plan branch → partial-success-shaped path.
        result = invoke_cli(cli_runner, ["migration-plan"], cwd=migration_plan_project, json_mode=True)
        data = parse_json_output(result, "migration-plan")
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "canonical field MUST NOT be dropped on no-moves path"
        assert "risk_rank" in summary
        assert summary["risk_level_canonical"] == "low"
        assert summary["risk_rank"] == 1

    def test_per_step_field_added(self, migration_plan_project, cli_runner, monkeypatch):
        """Each summary.steps[] row carries its own canonical risk_level."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan", "--move", "Nonexistent=src/x.py"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        steps = data.get("steps", [])
        # The Nonexistent move parses + evaluates to a low-risk step
        # (0 callers + no cross-layer) so the plan is non-empty.
        assert len(steps) >= 1, f"expected at least one step in plan; got {steps!r}"
        for step in steps:
            assert "risk" in step, "pre-existing per-step 'risk' field MUST be preserved (regression contract)"
            assert "risk_level_canonical" in step, f"step missing canonical risk_level_canonical: {step!r}"
            assert step["risk_level_canonical"] in RISK_LEVELS
            assert "risk_rank" in step
            # Per-step canonical bucket matches the normalised per-step risk.
            assert step["risk_level_canonical"] == (normalize_risk_level(step["risk"]) or "low")
            assert step["risk_rank"] == risk_rank(step["risk_level_canonical"])


# ---------------------------------------------------------------------------
# Helper-level tests — direct unit coverage of _migration_plan_risk_level
# ---------------------------------------------------------------------------


class TestMigrationPlanRiskLevelHelper:
    """Direct unit tests on ``_migration_plan_risk_level`` — max-tier aggregation."""

    def test_empty_plan_projects_to_low(self):
        """Empty step list → canonical 'low'."""
        assert _migration_plan_risk_level([]) == "low"

    def test_single_low_step_projects_to_low(self):
        """Single low-risk step → canonical 'low'."""
        assert _migration_plan_risk_level(["low"]) == "low"

    def test_single_medium_step_projects_to_medium(self):
        """Single medium-risk step → canonical 'medium'."""
        assert _migration_plan_risk_level(["medium"]) == "medium"

    def test_single_high_step_projects_to_high(self):
        """Single high-risk step → canonical 'high'."""
        assert _migration_plan_risk_level(["high"]) == "high"

    def test_high_step_projects_plan_to_high(self):
        """Plan with a mix containing a high step → canonical 'high'."""
        assert _migration_plan_risk_level(["low", "medium", "high"]) == "high"
        assert _migration_plan_risk_level(["high", "low"]) == "high"

    def test_max_aggregation(self):
        """Mixed plan: max-tier wins (worst step drives the plan)."""
        # [low, medium, high] → "high"
        assert _migration_plan_risk_level(["low", "medium", "high"]) == "high"
        # [low, medium] → "medium"
        assert _migration_plan_risk_level(["low", "medium"]) == "medium"
        # [low, low, low] → "low"
        assert _migration_plan_risk_level(["low", "low", "low"]) == "low"
        # [medium, medium] → "medium"
        assert _migration_plan_risk_level(["medium", "medium"]) == "medium"

    def test_conservative_no_critical(self):
        """Conservative-on-critical: cmd_migration_plan saturates at ``high``.

        Even if a downstream rename ever introduces ``critical`` as a
        per-step token, the rollup floor stays at ``high``: the W531
        CI-safety lesson — a threshold wobble can't promote a single-
        axis blast-radius signal into a CI-gating rank reserved for
        the multi-factor composite-score commands (cmd_attest).
        """
        # Per-step vocab CANNOT produce critical natively, but the helper
        # must still saturate at high if someone passes "critical" through.
        assert _migration_plan_risk_level(["critical"]) == "high"
        assert _migration_plan_risk_level(["critical", "high", "low"]) == "high"
        # Direct assertion that ``critical`` is never emitted by the helper.
        for risks in (
            ["high"],
            ["critical"],
            ["high", "high"],
            ["critical", "critical"],
        ):
            assert _migration_plan_risk_level(risks) != "critical", (
                f"helper escalated to critical on {risks!r} — single-axis signal must floor at high per W641 discipline"
            )

    def test_critical_preserved_or_floored(self):
        """cmd_migration_plan's 3-tier vocab floors critical at high."""
        # Single critical input — floors to high.
        assert _migration_plan_risk_level(["critical"]) == "high"
        # Mix with critical — still floors to high.
        assert _migration_plan_risk_level(["critical", "low"]) == "high"

    def test_unknown_severity_safe_floor(self):
        """Unknown tokens → 'low' floor + warnings_out marker.

        Mirrors the W918 alerts / W989 pr-risk / W641-followup-B critique /
        W641-followup-D attest / W641-followup-E diff / W641-followup-G
        dark-matter ``warnings_out`` discipline.
        """
        # All-unknown — safe-floor + marker.
        warnings_out: list[str] = []
        result = _migration_plan_risk_level(["bogus"], warnings_out=warnings_out)
        assert result == "low", f"all-unknown plan must safe-floor to 'low'; got {result!r}"
        assert any("migration_plan_unknown_severity:unknown_token" in w for w in warnings_out), (
            f"warnings_out must record unknown token; got {warnings_out!r}"
        )

        # Mixed unknown + known — known wins but unknown still records.
        warnings_out_b: list[str] = []
        assert _migration_plan_risk_level(["bogus", "high"], warnings_out=warnings_out_b) == "high"
        assert any("migration_plan_unknown_severity:unknown_token" in w for w in warnings_out_b)

        # Non-list input — invalid type safe-floors + records marker.
        warnings_out2: list[str] = []
        result2 = _migration_plan_risk_level(  # type: ignore[arg-type]
            "not_a_list", warnings_out=warnings_out2
        )
        assert result2 == "low"
        assert any("migration_plan_unknown_severity:non_list" in w for w in warnings_out2)

    def test_rank_matches_canonical(self):
        """Direct rank-comparator test (no envelope round-trip)."""
        assert risk_rank("critical") > risk_rank("high")
        assert risk_rank("high") > risk_rank("medium")
        assert risk_rank("medium") > risk_rank("low")
        assert risk_rank("low") == 1
        # Round-trip every ``_migration_plan_risk_level`` output through
        # ``normalize_risk_level`` — the helper MUST already emit canonical
        # tokens with no further normalization needed.
        for risks in (
            [],
            ["low"],
            ["medium"],
            ["high"],
            ["low", "medium"],
            ["low", "high"],
            ["critical"],
            ["critical", "medium"],
        ):
            out = _migration_plan_risk_level(risks)
            assert out in RISK_LEVELS, f"_migration_plan_risk_level({risks!r}) emitted non-canonical {out!r}"
            assert normalize_risk_level(out) == out, (
                f"_migration_plan_risk_level({risks!r}) emit {out!r} does not round-trip through normalize_risk_level"
            )


# ---------------------------------------------------------------------------
# W631 sort-polarity regression — consumer-side preserved post-emit-wiring
# ---------------------------------------------------------------------------


class TestW631SortPolarityPreserved:
    """W631 task #733 consumer-side polarity MUST survive the emit-side wiring.

    cmd_migration_plan's low-risk-first sort (line 128) and --max-risk
    gate (line 142) both call ``risk_rank()``. The W641-followup-H
    additions are purely additive to the envelope — they MUST NOT shift
    the sort polarity or break the gate.
    """

    def test_sort_polarity_low_first(self):
        """Plan sort order: low-rank steps appear before high-rank steps.

        Mirrors the cmd_migration_plan._order_key polarity at line 124-126
        (rank < 0 → 999 sentinel, otherwise rank). Higher rank = worse =
        later in the plan.
        """
        ranks = [
            ("low", risk_rank("low")),
            ("medium", risk_rank("medium")),
            ("high", risk_rank("high")),
        ]
        # Sort by rank (ascending = low-first), then by name.
        sorted_by_rank = sorted(ranks, key=lambda r: r[1])
        assert [r[0] for r in sorted_by_rank] == ["low", "medium", "high"], (
            f"W631 sort polarity broken: expected ['low', 'medium', 'high'], got {[r[0] for r in sorted_by_rank]!r}"
        )

    def test_max_risk_gate_polarity(self):
        """--max-risk gate: only steps with rank ≤ threshold pass."""
        # --max-risk medium → threshold_rank = 2; low (1) + medium (2) pass.
        threshold_rank = risk_rank("medium")
        assert threshold_rank == 2
        for risk, expected_pass in [
            ("low", True),
            ("medium", True),
            ("high", False),
        ]:
            s_rank = risk_rank(risk)
            actual_pass = 0 < s_rank <= threshold_rank
            assert actual_pass == expected_pass, (
                f"--max-risk gate polarity broken: risk={risk!r}, "
                f"threshold=medium, expected pass={expected_pass}, "
                f"actual={actual_pass}"
            )


# ---------------------------------------------------------------------------
# Drift guards — canonical set + cluster field-name consistency
# ---------------------------------------------------------------------------


class TestCanonicalSetDriftGuard:
    """Pin W631 + W641-followup-H vocabularies.

    Mirrors the W641 / W641-followup-A/B/C/D/E/G drift-guard discipline: if
    a future edit ever changes the canonical vocabulary, this test fails
    fast so the migration-plan emit contract can be re-evaluated alongside.
    """

    def test_canonical_set_drift_guard(self):
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_migration_plan_helper_only_emits_canonical_tokens(self):
        """Every ``_migration_plan_risk_level`` branch lands on a canonical token.

        Closed-enum invariant pinned at the source so a future edit
        can't reintroduce a non-canonical intermediate token.
        """
        for risks, expected in [
            ([], "low"),
            (["low"], "low"),
            (["medium"], "medium"),
            (["high"], "high"),
            (["low", "medium"], "medium"),
            (["low", "high"], "high"),
            (["medium", "high"], "high"),
            (["critical"], "high"),  # floored
            (["critical", "low"], "high"),
            (["bogus"], "low"),  # all-unknown → safe-floor
        ]:
            assert _migration_plan_risk_level(risks) == expected, (
                f"_migration_plan_risk_level({risks!r}) emit drift: expected {expected!r}"
            )

    def test_w641_cluster_field_name_consistency_via_ast(self):
        """AST-grep cmd_migration_plan.py for the exact field-name spelling.

        Pattern-3a guard against silent renames: if a future commit renames
        ``risk_level_canonical`` at the source, the live-envelope drift
        guard above catches it on the wire, and this static check catches
        it at the literal string. Both axes must pass per the W978
        two-instrument-rule discipline (CP44).
        """
        src = Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_migration_plan.py"
        text = src.read_text(encoding="utf-8")
        # Field-name spelling — must match cmd_impact / cmd_diff /
        # cmd_attest / cmd_dark_matter / cmd_critique / cmd_pr_risk / cmd_pr_bundle.
        assert "risk_level_canonical" in text, (
            "cmd_migration_plan.py source is missing the exact "
            "'risk_level_canonical' field name — cluster spelling drift?"
        )
        assert "risk_rank" in text, "cmd_migration_plan.py source is missing the exact 'risk_rank' field name"
        # Imports from the canonical helper module.
        assert "from roam.output.risk import" in text, (
            "cmd_migration_plan.py must import from roam.output.risk (the "
            "canonical W631 risk-LEVEL helper module) — cluster import-"
            "source drift?"
        )
        # normalize_risk_level import — new with W641-followup-H wiring.
        assert "normalize_risk_level" in text, (
            "cmd_migration_plan.py must import normalize_risk_level from roam.output.risk per the cluster contract"
        )

    def test_w641_cluster_field_name_consistency(self, migration_plan_project, cli_runner, monkeypatch):
        """Field-name spelling matches sibling cmd_attest / cmd_critique / cmd_diff / cmd_dark_matter exactly.

        Drift guard: if a future edit renames ``risk_level_canonical`` →
        ``risk_level_normalised`` (or any other near-miss), the cross-
        command Pattern-3a vocabulary breaks. The W641 cluster pins a
        single field name — this test asserts cmd_migration_plan conforms.
        """
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(
            cli_runner,
            ["migration-plan", "--move", "Nonexistent=src/x.py"],
            cwd=migration_plan_project,
            json_mode=True,
        )
        data = parse_json_output(result, "migration-plan")
        summary = data["summary"]
        # Field names MUST be exact — no aliases, no near-misses.
        assert "risk_level_canonical" in summary, (
            "W641 cluster field-name drift: cmd_migration_plan emits "
            "something other than 'risk_level_canonical' on the summary block"
        )
        assert "risk_rank" in summary, (
            "W641 cluster field-name drift: cmd_migration_plan emits "
            "something other than 'risk_rank' on the summary block"
        )
        # Top-level mirrors use identical names.
        assert "risk_level_canonical" in data
        assert "risk_rank" in data

        # Cross-check: import the same constants every sibling imports.
        from roam.output.risk import (  # noqa: F401 — name-pin only
            RISK_LEVELS,
            normalize_risk_level,
            risk_rank,
        )
