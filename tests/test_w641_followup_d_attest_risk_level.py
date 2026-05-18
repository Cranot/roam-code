"""W641-followup-D — ``roam attest`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (cluster-closer, fifth+ axis from W641 +
followup-A/B/C):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third axis
after W547 severity + W596 confidence). Follow-ups extended the discipline
to ``cmd_impact`` (W641-followup-A), ``cmd_critique`` (W641-followup-B),
and ``cmd_pr_bundle`` (W641-followup-C). ``cmd_attest`` is the natural
cluster closer: it aggregates risk + breaking + budget + fitness signals
into a single proof-carrying artifact and already emits an internal
``risk_level`` (``LOW``/``MODERATE``/``HIGH``/``CRITICAL``) from
``_collect_risk`` — but pre-W641-followup-D never projected onto the
canonical W631 set, so agents comparing attest's worst-case against
pr-risk / impact / critique on a single floor had to re-derive the rank.

This module pins the W641-followup-D emit contract on the JSON envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``_attest_risk_level`` (a thin shim around
  ``roam.output.risk.normalize_risk_level`` since the attest internal
  vocabulary — ``LOW``/``MODERATE``/``HIGH``/``CRITICAL`` — is already a
  near-mirror of W631 with one alias ``MODERATE`` → ``medium`` resolved
  by ``RISK_ALIASES``). Always in the W631 closed-set vocabulary
  (``low``/``medium``/``high``/``critical``); missing risk or a typo'd
  label floors to ``low`` (W531 CI-safety).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone: the verdict
  line names the canonical bucket without any other envelope field.
* Top-level ``risk_level_canonical`` + ``risk_rank`` mirrors so consumers
  that read the envelope head without descending into ``summary`` see
  the canonical bucket too (parity with the W641-followup-A cmd_impact
  + W641-followup-B cmd_critique contract).
* Unknown-status safe-floor: an unrecognised ``risk.level`` collapses to
  ``risk_level_canonical="low"`` AND records a marker on
  ``summary.warnings_out`` under ``attest_unknown_status:<value>`` so
  Pattern-2 silent-fallback discipline stays loud (mirrors W989 pr-risk
  + W918 alerts + W641-followup-B critique).

Conservative-on-critical: unlike critique / impact which saturate at
``high``, the attest composite-risk score IS allowed to reach
``critical`` (the >75/100 tier of ``_collect_risk``). We preserve that
escalation through the projection — attest's composite is a multi-factor
blend that can legitimately reach the critical threshold.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 — relative import after sys.path mutation
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

from roam.commands.cmd_attest import _attest_risk_level  # noqa: E402
from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — indexed project with uncommitted changes (mirrors test_attest.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Pre-elect autonomous_pr so ``roam attest`` works under future
    ``ROAM_MODE_ENFORCEMENT`` default-on (W23.3). Mirrors test_attest.py."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def attest_project(tmp_path, monkeypatch):
    """Project with an indexed baseline + uncommitted diff.

    Mirrors the ``attest_project`` fixture in ``tests/test_attest.py`` —
    small enough to land on the LOW risk tier so the canonical emit is
    deterministic on the happy path.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display_name(self):\n"
        "        return self.name.title()\n"
    )
    (proj / "service.py").write_text(
        "from models import User\n\ndef create_user(name, email):\n    user = User(name, email)\n    return user\n"
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    # Modify to create uncommitted diff for the attestation.
    (proj / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name, email):\n"
        "    user = User(name, email)\n"
        "    if not email:\n"
        '        raise ValueError("email required")\n'
        "    return user\n"
    )
    return proj


@pytest.fixture
def attest_no_changes_project(tmp_path, monkeypatch):
    """Indexed project with NO uncommitted changes — exercises the empty path."""
    proj = tmp_path / "repo_empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text('def main():\n    return "hello"\n')

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Envelope-level contract — happy path
# ---------------------------------------------------------------------------


class TestEnvelopeFields:
    """Pin the W641-followup-D emit contract on the JSON envelope."""

    def test_envelope_has_risk_level_canonical(self, cli_runner, attest_project, monkeypatch):
        """summary.risk_level_canonical is a string in the canonical W631 set."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        assert_json_envelope(data, "attest")
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-followup-D: summary.risk_level_canonical missing"
        assert isinstance(summary["risk_level_canonical"], str)
        assert summary["risk_level_canonical"] in RISK_LEVELS, (
            f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
        )
        # Top-level mirrors land (parity with cmd_impact / cmd_critique W641 cluster).
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]

    def test_envelope_has_risk_rank(self, cli_runner, attest_project, monkeypatch):
        """summary.risk_rank is an int matching risk_rank(summary.risk_level_canonical)."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        assert "risk_rank" in summary, "W641-followup-D: summary.risk_rank missing"
        assert isinstance(summary["risk_rank"], int)
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
            f"floor drift: risk_rank={summary['risk_rank']} "
            f"but risk_rank({summary['risk_level_canonical']!r})="
            f"{risk_rank(summary['risk_level_canonical'])}"
        )
        # All canonical levels rank in [1, 4]; the floor is 1 (low).
        assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach the envelope"
        # Top-level mirror lands.
        assert data.get("risk_rank") == summary["risk_rank"]

    def test_verdict_includes_risk_level_token(self, cli_runner, attest_project, monkeypatch):
        """LAW 6: verdict line names the canonical risk_level standalone."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
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

    def test_canonical_field_not_omitted_on_partial_success(self, cli_runner, attest_no_changes_project, monkeypatch):
        """Even on the no-changes partial_success path, canonical fields are emitted.

        Mirrors the W641-followup-A discipline: a degraded-resolution
        branch must NOT drop the canonical risk-LEVEL fields. Agents
        downstream call ``risk_rank(summary["risk_level_canonical"])``
        unconditionally without None-handling.
        """
        monkeypatch.chdir(attest_no_changes_project)
        result = invoke_cli(
            cli_runner,
            ["attest"],
            cwd=attest_no_changes_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        # partial_success path emits the fields anyway.
        assert summary.get("partial_success") is True, "no-changes path must set summary.partial_success=True"
        assert "risk_level_canonical" in summary, "canonical field MUST NOT be dropped on partial_success path"
        assert "risk_rank" in summary
        # Empty-changeset floors to canonical low (rank 1).
        assert summary["risk_level_canonical"] == "low", (
            f"empty changeset must safe-floor to 'low'; got {summary['risk_level_canonical']!r}"
        )
        assert summary["risk_rank"] == 1


# ---------------------------------------------------------------------------
# Severity → risk projection — unit-level (no DB / no diff parsing)
# ---------------------------------------------------------------------------


class TestSeverityProjection:
    """Direct unit tests on ``_attest_risk_level`` — every branch canonical."""

    def test_verified_attestation_projects_to_low(self):
        """risk.level='LOW' → canonical 'low' (the safe-merge case).

        The attest internal vocabulary spells the lowest tier ``LOW``;
        ``normalize_risk_level`` lowercases on lookup so this resolves
        cleanly into the canonical set.
        """
        assert _attest_risk_level({"level": "LOW", "score": 12}) == "low"
        assert _attest_risk_level({"level": "low", "score": 8}) == "low"

    def test_unverified_attestation_projects_to_medium(self):
        """risk.level='MODERATE' → canonical 'medium' (RISK_ALIASES resolution).

        ``MODERATE`` is the W134 pr-risk composite vocabulary's mid-tier
        spelling; it resolves into the canonical ``medium`` via
        ``RISK_ALIASES`` and lowercases via ``normalize_risk_level``.
        """
        assert _attest_risk_level({"level": "MODERATE", "score": 40}) == "medium"
        assert _attest_risk_level({"level": "moderate", "score": 35}) == "medium"
        assert _attest_risk_level({"level": "medium", "score": 42}) == "medium"

    def test_failed_attestation_projects_to_high(self):
        """risk.level='HIGH' → canonical 'high'."""
        assert _attest_risk_level({"level": "HIGH", "score": 65}) == "high"
        assert _attest_risk_level({"level": "high", "score": 70}) == "high"

    def test_critical_attestation_preserves_critical(self):
        """risk.level='CRITICAL' → canonical 'critical' (conservative-on-critical rule).

        Unlike critique / impact which saturate at ``high``, attest's
        composite-risk score IS allowed to reach ``critical`` (the
        >75/100 tier). The projection MUST preserve this escalation.
        """
        assert _attest_risk_level({"level": "CRITICAL", "score": 90}) == "critical"
        assert _attest_risk_level({"level": "critical", "score": 85}) == "critical"

    def test_missing_attestation_safe_floor(self):
        """Missing risk dict → canonical 'low' (NOT None, NOT missing).

        The W531 CI-safety floor: ``_collect_risk`` returns None when
        networkx is unavailable OR a degraded path produced no bundle.
        The projection MUST emit a canonical value — never None — so
        agents can call ``risk_rank(...)`` unconditionally.
        """
        assert _attest_risk_level(None) == "low"
        assert _attest_risk_level({}) == "low"
        assert _attest_risk_level({"level": None, "score": 0}) == "low"
        assert _attest_risk_level({"level": "", "score": 0}) == "low"

    def test_attestation_status_unknown_emits_low_plus_warning(self):
        """Unknown status → 'low' floor + warnings_out marker.

        Mirrors the W918 alerts / W989 pr-risk / W641-followup-B critique
        ``warnings_out`` discipline: unknown labels safe-floor to ``low``
        (CI-safe) AND accumulate a marker so Pattern-2 silent-fallback
        stays loud.
        """
        warnings_out: list[str] = []
        result = _attest_risk_level(
            {"level": "BOGUS_STATUS", "score": 0},
            warnings_out=warnings_out,
        )
        assert result == "low", f"unknown status must safe-floor to 'low'; got {result!r}"
        assert any("attest_unknown_status:BOGUS_STATUS" in w for w in warnings_out), (
            f"warnings_out must record the unknown label as attest_unknown_status:<value>; got {warnings_out!r}"
        )

    def test_rank_matches_canonical(self):
        """Direct rank-comparator test (no envelope round-trip)."""
        assert risk_rank("critical") > risk_rank("high")
        assert risk_rank("high") > risk_rank("medium")
        assert risk_rank("medium") > risk_rank("low")
        assert risk_rank("low") == 1
        # Round-trip every ``_attest_risk_level`` output through
        # ``normalize_risk_level`` — the helper MUST already emit
        # canonical-set tokens with no further normalization needed.
        for level_in in ("LOW", "MODERATE", "HIGH", "CRITICAL", "low", "moderate", "high", "critical"):
            out = _attest_risk_level({"level": level_in, "score": 50})
            assert out in RISK_LEVELS, f"_attest_risk_level({level_in!r}) emitted non-canonical {out!r}"
            assert normalize_risk_level(out) == out, (
                f"_attest_risk_level({level_in!r}) emit {out!r} does not round-trip through normalize_risk_level"
            )


# ---------------------------------------------------------------------------
# Drift guards — canonical set + projection-helper closure
# ---------------------------------------------------------------------------


class TestCanonicalSetDriftGuard:
    """Pin W631 + W641-followup-D vocabularies.

    Mirrors the W641 / W641-followup-A / W641-followup-B drift-guard
    discipline: if a future edit ever changes the canonical vocabulary,
    this test fails fast so the attest emit contract can be re-evaluated
    alongside.
    """

    def test_canonical_set_drift_guard(self):
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_attest_helper_only_emits_canonical_tokens(self):
        """Every ``_attest_risk_level`` branch lands on a canonical token.

        Closed-enum invariant pinned at the source so a future edit
        can't reintroduce a non-canonical intermediate token.
        """
        # All four canonical tiers + the MODERATE alias.
        for level_in, expected in [
            ("LOW", "low"),
            ("MODERATE", "medium"),
            ("HIGH", "high"),
            ("CRITICAL", "critical"),
            ("low", "low"),
            ("moderate", "medium"),
            ("high", "high"),
            ("critical", "critical"),
        ]:
            assert _attest_risk_level({"level": level_in, "score": 50}) == expected, (
                f"_attest_risk_level({level_in!r}) emit drift: expected {expected!r}"
            )
        # Floors + unknown — all collapse to canonical low.
        assert _attest_risk_level(None) == "low"
        assert _attest_risk_level({}) == "low"
        assert _attest_risk_level({"level": "garbage", "score": 0}) == "low"
