"""W641-followup-B — ``roam critique`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (fifth axis, drive-by from W641 +
W641-followup-A):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third
axis after W547 severity + W596 confidence). W641-followup-A added it
to ``cmd_impact`` (fourth axis). ``cmd_critique`` is the natural #2
remaining candidate: it emits a ``severity`` field on each diff-region
finding (``high``/``medium``/``low``/``info``) but had no risk-axis
projection — so agents reading the envelope couldn't floor-compare a
critique's worst finding against pr-risk or impact on a single scale.

This module pins the W641-followup-B emit contract on the JSON
envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``_CRITIQUE_SEVERITY_TO_RISK_LEVEL`` (a critique-local table mirroring
  the W565 ``_DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL`` polarity but on
  the risk axis, not the confidence axis) + normalised through
  ``roam.output.risk.normalize_risk_level``. Aggregation rule:
  **max-severity** — the worst critique finding's projected risk_level
  wins. Empty findings → ``"low"`` floor (W531 CI-safety: no findings
  must NOT promote into a CI-failing rank).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone: the
  verdict line names the canonical bucket without any other envelope
  field.
* Top-level ``risk_level_canonical`` + ``risk_rank`` mirrors so
  consumers that read the envelope head without descending into
  ``summary`` see the canonical bucket too (parity with the W641-
  followup-A cmd_impact emit).
* Unknown-severity safe-floor: an unrecognised severity label collapses
  to ``risk_level_canonical="low"`` AND records a marker on
  ``summary.warnings_out`` under ``critique_unknown_severity:<label>``
  so Pattern-2 silent-fallback discipline stays loud (mirrors W989
  pr-risk + W918 alerts ``warnings_out``).

Conservative-on-critical: the critique severity vocabulary tops out at
``high`` (the closed ``Finding`` constructor vocab — see
``roam.critique.checks``). The projection saturates at ``high``; we do
NOT escalate to ``critical`` on a threshold the underlying detector
can't reach (mirrors the W641-followup-A discipline on
``_impact_risk_level``).
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

# Ensure tests/conftest helpers are importable.
sys.path.insert(0, str(Path(__file__).parent))

from conftest import make_src_project as _make_project  # noqa: E402

from roam.cli import cli  # noqa: E402
from roam.commands.cmd_critique import (  # noqa: E402
    _CRITIQUE_SEVERITY_TO_RISK_LEVEL,
    _critique_risk_level,
)
from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — a small indexed project + a reusable diff to feed critique
# ---------------------------------------------------------------------------


_PROJECT_FILES = {
    "auth.py": """
        class UserSession:
            def __init__(self, token):
                self.token = token

            def refresh(self):
                # CHANGE-ME line
                return self.token

            def revoke(self):
                return None

        def handle_login(user):
            s = UserSession(token="abc")
            return s.refresh()
    """,
    "billing.py": """
        class Invoice:
            def total(self):
                return self.amount
    """,
}


_DIFF_REFRESH_ONLY = textwrap.dedent(
    """\
    diff --git a/src/auth.py b/src/auth.py
    --- a/src/auth.py
    +++ b/src/auth.py
    @@ -5,3 +5,4 @@
         def refresh(self):
    -        return self.token
    +        # tweaked
    +        return str(self.token)
    """
)


@pytest.fixture
def critique_project(tmp_path, monkeypatch):
    """Indexed project that critique can run against."""
    proj = _make_project(tmp_path, _PROJECT_FILES)
    monkeypatch.chdir(str(proj))
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    return proj


def _run_critique_json(critique_project, diff_text: str) -> dict:
    """Invoke ``roam --json critique --input <diff>`` and return parsed envelope.

    critique exits 5 on high-severity findings; both 0 and 5 are legal exit
    codes (the JSON envelope is still emitted on stdout).
    """
    diff_path = critique_project / "patch.diff"
    diff_path.write_text(diff_text, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
    assert result.exit_code in (0, 5), f"critique failed: exit={result.exit_code}\n{result.output}"
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# Envelope-level contract
# ---------------------------------------------------------------------------


class TestEnvelopeFields:
    """Pin the W641-followup-B emit contract on the JSON envelope."""

    def test_canonical_fields_present_in_envelope(self, critique_project):
        """summary.risk_level_canonical + summary.risk_rank are ALWAYS emitted."""
        data = _run_critique_json(critique_project, _DIFF_REFRESH_ONLY)
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-followup-B: summary.risk_level_canonical missing"
        assert "risk_rank" in summary, "W641-followup-B: summary.risk_rank missing"
        assert isinstance(summary["risk_level_canonical"], str)
        assert isinstance(summary["risk_rank"], int)
        assert summary["risk_level_canonical"] in RISK_LEVELS, (
            f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
        )
        # Top-level mirrors must land too (parity with cmd_impact / cmd_pr_risk).
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]
        assert data.get("risk_rank") == summary["risk_rank"]

    def test_rank_matches_canonical_helper(self, critique_project):
        """summary.risk_rank == risk_rank(summary.risk_level_canonical)."""
        data = _run_critique_json(critique_project, _DIFF_REFRESH_ONLY)
        summary = data["summary"]
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
            f"floor drift: risk_rank={summary['risk_rank']} "
            f"but risk_rank({summary['risk_level_canonical']!r})="
            f"{risk_rank(summary['risk_level_canonical'])}"
        )
        # Sanity: all canonical levels rank in [1, 4]; the floor is 1 (low).
        assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach the envelope"
        # Ordering sanity: rank table polarity (higher = worse) reflects on
        # the canonical helper.
        assert risk_rank("high") > risk_rank("medium")
        assert risk_rank("medium") > risk_rank("low")

    def test_verdict_includes_risk_level_token(self, critique_project):
        """LAW 6: verdict line names the canonical risk_level standalone."""
        data = _run_critique_json(critique_project, _DIFF_REFRESH_ONLY)
        summary = data["summary"]
        verdict = summary["verdict"]
        canonical = summary["risk_level_canonical"]
        # The augmented suffix must be present and reflect the canonical
        # bucket — regex on the closed-enum parenthesis form.
        assert re.search(r"\(risk_level (critical|high|medium|low)\)", verdict), (
            f"LAW 6 violated: verdict {verdict!r} missing closed-enum (risk_level <canonical>) suffix"
        )
        assert f"risk_level {canonical}" in verdict, (
            f"LAW 6 violated: verdict {verdict!r} does not name the emitted canonical risk_level {canonical!r}"
        )


# ---------------------------------------------------------------------------
# Severity → risk projection — unit-level (no DB / no diff parsing)
# ---------------------------------------------------------------------------


class TestSeverityProjection:
    """Direct unit tests on ``_critique_risk_level`` — every branch canonical."""

    def test_critical_severity_projects_to_high(self):
        """severity=error → risk_level=high (critique vocab tops at high).

        The critique severity vocabulary's worst tier is ``high`` (closed
        ``Finding`` constructor vocab in :mod:`roam.critique.checks`); we
        also accept ``error``/``critical`` forward-compat in the projection
        table. Both map to canonical ``high`` per the conservative-on-
        critical discipline (mirrors W641-followup-A's ``_impact_risk_level``
        — never escalate to ``critical`` on a threshold the underlying
        detector cannot reach).
        """
        assert _critique_risk_level([{"severity": "error"}]) == "high"
        assert _critique_risk_level([{"severity": "critical"}]) == "high"
        assert _critique_risk_level([{"severity": "high"}]) == "high"

    def test_warning_projects_to_medium(self):
        assert _critique_risk_level([{"severity": "warning"}]) == "medium"
        assert _critique_risk_level([{"severity": "medium"}]) == "medium"

    def test_info_projects_to_low(self):
        assert _critique_risk_level([{"severity": "info"}]) == "low"
        assert _critique_risk_level([{"severity": "low"}]) == "low"
        assert _critique_risk_level([{"severity": "note"}]) == "low"

    def test_empty_findings_emits_low_floor(self):
        """NO findings → risk_level='low' (NOT missing/None).

        The W531 CI-safety floor: a zero-findings result must produce a
        canonical ``low`` value — never ``None``, never missing. Agents
        downstream can call ``risk_rank(summary['risk_level_canonical'])``
        unconditionally without None-handling.
        """
        assert _critique_risk_level([]) == "low"

    def test_aggregation_uses_max(self):
        """Multiple findings: max-severity wins (worst critique drives).

        Aggregation rule: the worst finding's projected risk_level wins.
        A diff with [info, error] findings has its risk_level driven by
        the error finding (→ high), not the info finding (→ low).
        """
        findings = [{"severity": "info"}, {"severity": "error"}]
        assert _critique_risk_level(findings) == "high"
        # Order-independence: aggregation is order-invariant.
        findings_reversed = [{"severity": "error"}, {"severity": "info"}]
        assert _critique_risk_level(findings_reversed) == "high"
        # Mixed warning + info → warning wins (medium).
        assert _critique_risk_level([{"severity": "info"}, {"severity": "warning"}]) == "medium"
        # All info → floors to low.
        assert _critique_risk_level([{"severity": "info"}, {"severity": "info"}]) == "low"

    def test_unknown_severity_safe_floor(self):
        """severity='bogus' → 'low' + warnings_out marker.

        Mirrors the W918 alerts / W989 pr-risk ``warnings_out`` discipline:
        unknown labels safe-floor to ``low`` (CI-safe) AND accumulate a
        marker so Pattern-2 silent-fallback stays loud.
        """
        warnings_out: list[str] = []
        result = _critique_risk_level([{"severity": "bogus"}], warnings_out=warnings_out)
        assert result == "low", f"unknown severity must safe-floor to 'low'; got {result!r}"
        assert any("critique_unknown_severity:bogus" in w for w in warnings_out), (
            f"warnings_out must record the unknown label; got {warnings_out!r}"
        )

    def test_rank_matches_canonical_helper_unit(self):
        """Direct rank-comparator test (no envelope round-trip)."""
        assert risk_rank("high") > risk_rank("medium")
        assert risk_rank("medium") > risk_rank("low")
        assert risk_rank("low") == 1
        # The projection table only emits canonical-set tokens.
        for sev, level in _CRITIQUE_SEVERITY_TO_RISK_LEVEL.items():
            assert level in RISK_LEVELS, f"projection table emits non-canonical {level!r} for severity {sev!r}"
            # Round-trip cleanly through normalize_risk_level.
            assert normalize_risk_level(level) == level


# ---------------------------------------------------------------------------
# Drift guards — canonical set + projection-table closure
# ---------------------------------------------------------------------------


class TestCanonicalSetDriftGuard:
    """Pin W631 + W641-followup-B vocabularies.

    Mirrors the W641 / W641-followup-A drift-guard discipline: if a
    future edit ever changes the canonical vocabulary, this test fails
    fast so the critique emit contract can be re-evaluated alongside.
    """

    def test_canonical_set_drift_guard(self):
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_projection_table_only_emits_canonical_tokens(self):
        """Every value in ``_CRITIQUE_SEVERITY_TO_RISK_LEVEL`` is canonical."""
        for sev, level in _CRITIQUE_SEVERITY_TO_RISK_LEVEL.items():
            assert level in RISK_LEVELS, f"projection drift: severity {sev!r} → non-canonical {level!r}"
