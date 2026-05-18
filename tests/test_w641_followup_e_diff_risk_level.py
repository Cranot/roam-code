"""W641-followup-E — ``roam diff`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (cluster extension, sixth axis from W641 +
followup-A/B/C/D):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third axis
after W547 severity + W596 confidence). Follow-ups extended the discipline
to ``cmd_impact`` (W641-followup-A), ``cmd_critique`` (W641-followup-B),
``cmd_pr_bundle`` (W641-followup-C), and ``cmd_attest`` (W641-followup-D —
cluster closer at the time). ``cmd_diff`` is the cluster-closer EXTENSION:
it shows uncommitted-tree blast radius + per-region change severity,
the canonical sibling of ``critique`` / ``impact`` in the agent post-edit
workflow (``git diff | roam critique`` and ``roam diff`` are siblings).

cmd_diff has NO native severity vocabulary — it emits volumetric counts
(``changed_files``, ``affected_symbols``, ``affected_files``). The
canonical W631 risk-LEVEL bucket is derived from the same blast-radius
thresholds cmd_impact's W641-followup-A helper uses (single source of
projection polarity).

This module pins the W641-followup-E emit contract on the JSON envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``_diff_risk_level`` (a blast-radius threshold helper, mirroring the
  cmd_impact W641-followup-A polarity). Always in the W631 closed-set
  vocabulary (``low``/``medium``/``high``/``critical``); clean diffs or
  any unknown signal floors to ``low`` (W531 CI-safety).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented on the happy path to terminate on a
  closed-enum ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone:
  the verdict line names the canonical bucket without any other envelope
  field. The ``no_changes`` empty-state path preserves the legacy literal
  ``"no changes"`` verdict to keep the pre-W641-followup-E regression
  contract intact (state="no_changes" + canonical fields disambiguate).
* Top-level ``risk_level_canonical`` + ``risk_rank`` mirrors so consumers
  that read the envelope head without descending into ``summary`` see
  the canonical bucket too (parity with the W641-followup-A cmd_impact
  + W641-followup-B cmd_critique + W641-followup-D cmd_attest contract).
* Unknown-input safe-floor: a negative / non-int blast-radius count
  collapses to ``risk_level_canonical="low"`` AND records a marker on
  ``summary.warnings_out`` under ``diff_unknown_severity:<value>`` so
  Pattern-2 silent-fallback discipline stays loud (mirrors W989 pr-risk
  + W918 alerts + W641-followup-B critique + W641-followup-D attest).

Conservative-on-critical: cmd_diff structurally aligns with cmd_impact /
cmd_critique — single-axis blast-radius signal floored at ``high``.
``critical`` is reserved for the multi-factor composite-score commands
(cmd_attest's ``_collect_risk``). The W531 CI-safety lesson: a threshold
wobble MUST NOT promote a finding into a CI-gating rank.
"""

from __future__ import annotations

import json as _json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 — relative import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
)

from roam.commands.cmd_diff import _diff_risk_level  # noqa: E402
from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — indexed project with / without uncommitted changes
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def clean_diff_project(tmp_path, monkeypatch):
    """Indexed project with NO uncommitted changes — exercises empty path."""
    proj = tmp_path / "clean-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\ndef main():\n    return greet('world')\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def dirty_diff_project(clean_diff_project):
    """Extend ``clean_diff_project`` with an uncommitted edit on disk.

    Tiny diff intentionally — happy-path canonical projection should land
    on ``low`` because affected_symbols / files stay below the thresholds.
    """
    (clean_diff_project / "app.py").write_text(
        "def greet(name):\n"
        "    return f'hello {name}'\n"
        "\n"
        "def main():\n"
        "    return greet('world')\n"
        "\n"
        "def farewell(name):\n"
        "    return f'bye {name}'\n"
    )
    return clean_diff_project


# ---------------------------------------------------------------------------
# Envelope-level contract — happy path
# ---------------------------------------------------------------------------


class TestEnvelopeFields:
    """Pin the W641-followup-E emit contract on the JSON envelope."""

    def test_envelope_has_risk_level_canonical(self, cli_runner, dirty_diff_project, monkeypatch):
        """summary.risk_level_canonical is a string in the canonical W631 set."""
        monkeypatch.chdir(dirty_diff_project)
        result = invoke_cli(cli_runner, ["diff"], cwd=dirty_diff_project, json_mode=True)
        assert result.exit_code == 0, f"diff exited {result.exit_code}: {result.output}"
        data = _json.loads(result.output)
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-followup-E: summary.risk_level_canonical missing"
        assert isinstance(summary["risk_level_canonical"], str)
        assert summary["risk_level_canonical"] in RISK_LEVELS, (
            f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
        )
        # Top-level mirrors land (parity with cmd_impact / cmd_critique /
        # cmd_attest W641 cluster).
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]

    def test_envelope_has_risk_rank(self, cli_runner, dirty_diff_project, monkeypatch):
        """summary.risk_rank is an int matching risk_rank(summary.risk_level_canonical)."""
        monkeypatch.chdir(dirty_diff_project)
        result = invoke_cli(cli_runner, ["diff"], cwd=dirty_diff_project, json_mode=True)
        assert result.exit_code == 0
        data = _json.loads(result.output)
        summary = data["summary"]
        assert "risk_rank" in summary, "W641-followup-E: summary.risk_rank missing"
        assert isinstance(summary["risk_rank"], int)
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
            f"floor drift: risk_rank={summary['risk_rank']} "
            f"but risk_rank({summary['risk_level_canonical']!r})="
            f"{risk_rank(summary['risk_level_canonical'])}"
        )
        assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach the envelope"
        # Top-level mirror lands.
        assert data.get("risk_rank") == summary["risk_rank"]

    def test_clean_diff_emits_low_floor(self, cli_runner, clean_diff_project, monkeypatch):
        """Clean diff (no changes) floors canonical risk_level to 'low'.

        Mirrors the cmd_attest W641-followup-D no-changes safe-floor
        discipline. The verdict on the no-changes path stays literal
        ``"no changes"`` (W641-followup-E preserves the legacy regression
        contract pinned in test_diff_empty_state.py); state="no_changes"
        + canonical fields disambiguate the standalone-parse.
        """
        monkeypatch.chdir(clean_diff_project)
        result = invoke_cli(cli_runner, ["diff"], cwd=clean_diff_project, json_mode=True)
        assert result.exit_code == 0
        data = _json.loads(result.output)
        summary = data["summary"]
        # Empty-changeset path is partial_success=False with state=no_changes.
        assert summary.get("state") == "no_changes"
        # Canonical fields ARE emitted on the empty path.
        assert summary.get("risk_level_canonical") == "low", (
            f"clean diff must safe-floor to 'low'; got {summary.get('risk_level_canonical')!r}"
        )
        assert summary.get("risk_rank") == 1
        # Top-level mirrors land on the empty path too.
        assert data.get("risk_level_canonical") == "low"
        assert data.get("risk_rank") == 1

    def test_verdict_includes_risk_level_token(self, cli_runner, dirty_diff_project, monkeypatch):
        """LAW 6: verdict line names the canonical risk_level standalone.

        On the happy path (changes present) the verdict terminates on a
        closed-enum ``(risk_level <canonical>)`` parenthesis. Regex pinned
        so a future edit can't silently drop the suffix.
        """
        monkeypatch.chdir(dirty_diff_project)
        result = invoke_cli(cli_runner, ["diff"], cwd=dirty_diff_project, json_mode=True)
        assert result.exit_code == 0
        data = _json.loads(result.output)
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

    def test_canonical_field_not_omitted_on_partial_success(self, cli_runner, clean_diff_project, monkeypatch):
        """Even on the no-changes path, canonical fields are emitted.

        Mirrors the W641-followup-D discipline: a degraded / empty branch
        must NOT drop the canonical risk-LEVEL fields. Agents downstream
        call ``risk_rank(summary["risk_level_canonical"])`` unconditionally
        without None-handling.
        """
        monkeypatch.chdir(clean_diff_project)
        result = invoke_cli(cli_runner, ["diff"], cwd=clean_diff_project, json_mode=True)
        assert result.exit_code == 0
        data = _json.loads(result.output)
        summary = data["summary"]
        # No-changes path defines partial_success=False with state=no_changes,
        # which is the cmd_diff empty-state contract pinned in
        # test_diff_empty_state.py. Canonical fields land regardless.
        assert summary.get("state") == "no_changes"
        assert "risk_level_canonical" in summary, "canonical field MUST NOT be dropped on no-changes / partial path"
        assert "risk_rank" in summary
        # Empty-changeset floors to canonical low (rank 1).
        assert summary["risk_level_canonical"] == "low", (
            f"empty changeset must safe-floor to 'low'; got {summary['risk_level_canonical']!r}"
        )
        assert summary["risk_rank"] == 1


# ---------------------------------------------------------------------------
# Blast-radius → risk projection — unit-level (no DB / no diff parsing)
# ---------------------------------------------------------------------------


class TestBlastRadiusProjection:
    """Direct unit tests on ``_diff_risk_level`` — every branch canonical.

    Mirrors the W641-followup-A ``_impact_risk_level`` polarity:
      affected_symbols >= 50  OR  affected_files >= 20  -> "high"
      affected_symbols >= 10  OR  affected_files >= 5   -> "medium"
      affected_symbols > 0                              -> "low"
      affected_symbols == 0                             -> "low"
    """

    def test_clean_diff_projects_to_low(self):
        """affected_symbols == 0 and affected_files == 0 → canonical 'low'."""
        assert _diff_risk_level(0, 0) == "low"

    def test_small_diff_projects_to_low(self):
        """Small blast (< medium thresholds) → canonical 'low'."""
        assert _diff_risk_level(1, 0) == "low"
        assert _diff_risk_level(5, 2) == "low"
        assert _diff_risk_level(9, 4) == "low"

    def test_moderate_diff_projects_to_medium(self):
        """affected_symbols >= 10 or affected_files >= 5 → canonical 'medium'."""
        assert _diff_risk_level(10, 0) == "medium"
        assert _diff_risk_level(20, 3) == "medium"
        assert _diff_risk_level(0, 5) == "medium"
        assert _diff_risk_level(49, 19) == "medium"

    def test_high_severity_change_projects_to_high(self):
        """affected_symbols >= 50 or affected_files >= 20 → canonical 'high'."""
        assert _diff_risk_level(50, 0) == "high"
        assert _diff_risk_level(0, 20) == "high"
        assert _diff_risk_level(100, 30) == "high"
        assert _diff_risk_level(500, 100) == "high"

    def test_aggregation_uses_max(self):
        """Max-tier wins: high signal on either axis floors to ``high``.

        cmd_diff uses an OR-aggregation across the two blast-radius axes
        (symbols, files) — the worst signal wins (max-severity polarity,
        mirrors W641-followup-B cmd_critique).
        """
        # Files axis high, symbols axis low → ``high``.
        assert _diff_risk_level(1, 20) == "high"
        # Symbols axis high, files axis low → ``high``.
        assert _diff_risk_level(50, 0) == "high"
        # Files axis medium, symbols axis low → ``medium``.
        assert _diff_risk_level(2, 5) == "medium"

    def test_critical_floored_to_high(self):
        """Conservative-on-critical: cmd_diff saturates at ``high``.

        Per the W641-followup-A/B discipline: single-axis blast-radius
        signal MUST NOT escalate to ``critical`` without multi-factor
        composite-score evidence (cmd_attest's ``_collect_risk``). The
        W531 CI-safety lesson: a threshold wobble can't promote a finding
        into a CI-gating rank.
        """
        # Even extreme blast radius stays at ``high`` (no ``critical`` tier).
        assert _diff_risk_level(10_000, 5_000) == "high"
        assert _diff_risk_level(50_000, 500_000) == "high"

    def test_unknown_severity_safe_floor(self):
        """Unknown / negative inputs → 'low' floor + warnings_out marker.

        Mirrors the W918 alerts / W989 pr-risk / W641-followup-B critique /
        W641-followup-D attest ``warnings_out`` discipline.
        """
        # Negative counts — invalid input safe-floors + records marker.
        warnings_out: list[str] = []
        result = _diff_risk_level(-1, 5, warnings_out=warnings_out)
        assert result == "low", f"negative input must safe-floor to 'low'; got {result!r}"
        assert any("diff_unknown_severity:negative" in w for w in warnings_out), (
            f"warnings_out must record negative input as diff_unknown_severity:negative(...); got {warnings_out!r}"
        )

        # Non-int counts — invalid type safe-floors + records marker.
        warnings_out2: list[str] = []
        result2 = _diff_risk_level("bogus", 5, warnings_out=warnings_out2)  # type: ignore[arg-type]
        assert result2 == "low"
        assert any("diff_unknown_severity:non_int_counts" in w for w in warnings_out2)

    def test_rank_matches_canonical(self):
        """Direct rank-comparator test (no envelope round-trip)."""
        assert risk_rank("critical") > risk_rank("high")
        assert risk_rank("high") > risk_rank("medium")
        assert risk_rank("medium") > risk_rank("low")
        assert risk_rank("low") == 1
        # Round-trip every ``_diff_risk_level`` output through
        # ``normalize_risk_level`` — the helper MUST already emit
        # canonical-set tokens with no further normalization needed.
        for symbols, files in ((0, 0), (5, 2), (15, 3), (60, 1), (1, 25), (10_000, 5_000)):
            out = _diff_risk_level(symbols, files)
            assert out in RISK_LEVELS, f"_diff_risk_level({symbols}, {files}) emitted non-canonical {out!r}"
            assert normalize_risk_level(out) == out, (
                f"_diff_risk_level({symbols}, {files}) emit {out!r} does not round-trip through normalize_risk_level"
            )


# ---------------------------------------------------------------------------
# Drift guards — canonical set + projection-helper closure + cluster parity
# ---------------------------------------------------------------------------


class TestCanonicalSetDriftGuard:
    """Pin W631 + W641-followup-E vocabularies.

    Mirrors the W641 / W641-followup-A/B/C/D drift-guard discipline: if a
    future edit ever changes the canonical vocabulary, this test fails
    fast so the diff emit contract can be re-evaluated alongside.
    """

    def test_canonical_set_drift_guard(self):
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_diff_helper_only_emits_canonical_tokens(self):
        """Every ``_diff_risk_level`` branch lands on a canonical token.

        Closed-enum invariant pinned at the source so a future edit
        can't reintroduce a non-canonical intermediate token.
        """
        # All three threshold bands + the floor.
        for symbols, files, expected in [
            (0, 0, "low"),
            (1, 0, "low"),
            (9, 4, "low"),
            (10, 0, "medium"),
            (0, 5, "medium"),
            (49, 19, "medium"),
            (50, 0, "high"),
            (0, 20, "high"),
            (10_000, 5_000, "high"),
        ]:
            assert _diff_risk_level(symbols, files) == expected, (
                f"_diff_risk_level({symbols}, {files}) emit drift: expected {expected!r}"
            )
        # Floors + invalid inputs — all collapse to canonical low.
        assert _diff_risk_level(-5, 5) == "low"
        assert _diff_risk_level(5, -5) == "low"

    def test_w641_cluster_consistency(self, cli_runner, dirty_diff_project, monkeypatch):
        """Field-name spelling matches sibling cmd_attest / cmd_critique exactly.

        Drift guard: if a future edit renames ``risk_level_canonical`` →
        ``risk_level_normalised`` (or any other near-miss), the cross-
        command Pattern-3a vocabulary breaks. The W641 cluster pins a
        single field name — this test asserts cmd_diff conforms.
        """
        monkeypatch.chdir(dirty_diff_project)
        result = invoke_cli(cli_runner, ["diff"], cwd=dirty_diff_project, json_mode=True)
        assert result.exit_code == 0
        data = _json.loads(result.output)
        summary = data["summary"]
        # Field names MUST be exact — no aliases, no near-misses.
        assert "risk_level_canonical" in summary, (
            "W641 cluster field-name drift: cmd_diff emits something other "
            "than 'risk_level_canonical' on the summary block"
        )
        assert "risk_rank" in summary, (
            "W641 cluster field-name drift: cmd_diff emits something other than 'risk_rank' on the summary block"
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
