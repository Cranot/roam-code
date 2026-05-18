"""W641-followup-A — ``roam impact`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (fourth axis, drive-by from W641):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (the third
axis after W547 severity + W596 confidence). The follow-up audit surfaced
``cmd_impact`` as the top candidate for the same projection: it emits
blast/risk metrics (blast_radius, reach_pct, weighted_impact) but had no
canonical ``risk_level`` field, so agents reading the envelope couldn't
floor-compare against the canonical ``risk_rank()`` table.

This module pins the W641-followup-A emit contract on the JSON envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``normalize_risk_level`` from a derived blast-radius tier
  (``_impact_risk_level`` in ``cmd_impact``): Large→high, Moderate→medium,
  Small/None→low. Always in the W631 closed-set vocabulary
  (``low``/``medium``/``high``/``critical``); a typo'd label or absent
  state floors to ``low`` (the W531 CI-safety lesson).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone: the verdict
  line names the canonical bucket without any other envelope field.
* Empty-corpus / unresolved / not-in-graph / no-dependents paths all
  emit ``risk_level_canonical="low"`` + ``risk_rank=1`` unconditionally
  so consumers can call ``risk_rank(summary["risk_level_canonical"])``
  without None-handling.

Same Pattern-3a discipline as W632 / W641: re-use the canonical module
instead of re-deriving the rank vocabulary at the call site, so a single
edit to ``RISK_LEVELS`` / ``_RISK_RANK`` propagates to every consumer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 — relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — small + high-fan-in projects to exercise low + high blast tiers.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def small_impact_project(tmp_path):
    """Project with one leaf symbol + one caller — exercises the LOW tier.

    The leaf symbol ``leaf_fn`` has 1 caller, well below the 10-symbol /
    2% reach threshold for Moderate. Canonical risk_level == "low".
    """
    proj = tmp_path / "small_impact"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    (src / "core.py").write_text("def leaf_fn():\n    return 42\n\ndef caller_fn():\n    return leaf_fn()\n")

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def high_impact_project(tmp_path):
    """Project with one hub symbol called by 60 callers — exercises HIGH tier.

    Crosses the 50-dependents-or-10%-reach threshold so the canonical
    risk_level lands on "high". Mirrors the high_fan_in_project pattern in
    ``tests/test_impact_bounded.py`` but with enough callers (60 > 50) that
    the affected_symbols absolute-count threshold fires deterministically.
    """
    proj = tmp_path / "high_impact"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    (src / "hub.py").write_text("def hub_fn():\n    return 1\n")

    callers_dir = src / "callers"
    callers_dir.mkdir()
    (callers_dir / "__init__.py").write_text("")
    for i in range(60):
        (callers_dir / f"c{i:03d}.py").write_text(f"from hub import hub_fn\n\ndef c{i:03d}():\n    return hub_fn()\n")

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Canonical projection — small project (low tier).
# ---------------------------------------------------------------------------


class TestRiskLevelCanonicalEmit:
    """Pin the W641-followup-A emit contract on the JSON envelope."""

    def test_envelope_emits_risk_level_canonical(self, cli_runner, small_impact_project, monkeypatch):
        """summary.risk_level_canonical is a string in the canonical W631 4-tier set."""
        monkeypatch.chdir(small_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=small_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-followup-A: summary.risk_level_canonical missing"
        assert isinstance(summary["risk_level_canonical"], str)
        assert summary["risk_level_canonical"] in RISK_LEVELS, (
            f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
        )

    def test_risk_level_canonical_matches_normalize_helper(self, cli_runner, small_impact_project, monkeypatch):
        """risk_level_canonical == normalize_risk_level(risk_level_canonical) — projection consistency.

        Drift guard: feeding the emitted canonical back through the helper
        must return the same canonical token. If a future edit ever projects
        through a different (non-canonical) intermediate token, this test
        fails fast.
        """
        monkeypatch.chdir(small_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=small_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        canonical = summary["risk_level_canonical"]
        # The emitted value must round-trip cleanly through the canonical
        # normalizer — proves the projection landed on a canonical token, not
        # a domain alias.
        assert normalize_risk_level(canonical) == canonical, (
            f"projection drift: emitted {canonical!r} does not round-trip through normalize_risk_level"
        )

    def test_risk_rank_floor_comparator(self, cli_runner, small_impact_project, monkeypatch):
        """summary.risk_rank == risk_rank(summary.risk_level_canonical) — floor round-trip."""
        monkeypatch.chdir(small_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=small_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert "risk_rank" in summary, "W641-followup-A: summary.risk_rank missing"
        assert isinstance(summary["risk_rank"], int)
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
            f"floor drift: risk_rank={summary['risk_rank']} "
            f"but risk_rank({summary['risk_level_canonical']!r})={risk_rank(summary['risk_level_canonical'])}"
        )
        # All known canonical levels rank in [1, 4]; the floor is 1 (low).
        assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach here"

    def test_verdict_disambiguates_canonical_level(self, cli_runner, small_impact_project, monkeypatch):
        """Verdict text mentions the canonical risk_level in a closed-enum parenthesis.

        LAW 6: the verdict line works standalone. After W641-followup-A,
        the line terminates on ``(risk_level <canonical>)`` so a consumer
        that reads only the verdict string parses the canonical bucket
        from the line itself, without loading the rest of the envelope.
        """
        monkeypatch.chdir(small_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=small_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        verdict = summary["verdict"]
        canonical = summary["risk_level_canonical"]
        assert f"risk_level {canonical}" in verdict, (
            f"LAW 6 violated: verdict {verdict!r} does not name canonical risk_level {canonical!r}"
        )
        # The verdict's parenthesised canonical label must come from the
        # closed W631 enum — sanity-check parser-side that no off-vocab
        # token slipped in.
        for level in RISK_LEVELS:
            if f"risk_level {level}" in verdict:
                found = level
                break
        else:
            raise AssertionError(f"verdict {verdict!r} does not contain any canonical risk_level label")
        assert found == canonical


# ---------------------------------------------------------------------------
# Both --target paths emit canonical (cmd_impact has only the target mode;
# there is no --changed mode — the task brief mentioned it as a possibility
# but the implementation confirms a single positional ``SYMBOL`` argument).
# We exercise the target-mode happy path AND the not-found / not-in-graph
# / no-dependents branches as the "no-target safe paths".
# ---------------------------------------------------------------------------


class TestRiskLevelTargetMode:
    """Target-symbol mode (the canonical ``roam impact <name>`` invocation)."""

    def test_target_symbol_path_emits_canonical(self, cli_runner, small_impact_project, monkeypatch):
        """Resolved-target path emits both canonical fields and a verdict suffix."""
        monkeypatch.chdir(small_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=small_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        # All three contract fields present + consistent.
        assert "risk_level_canonical" in summary
        assert "risk_rank" in summary
        assert summary["risk_level_canonical"] in RISK_LEVELS
        assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"])
        # Verdict suffix names the canonical bucket.
        assert f"risk_level {summary['risk_level_canonical']}" in summary["verdict"]
        # Top-level mirrors land too (parity with cmd_pr_risk W641 pattern).
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]
        assert data.get("risk_rank") == summary["risk_rank"]


class TestRiskLevelEdgeCases:
    """Edge cases — unresolved / not-in-graph / no-dependents 'safe' paths."""

    def test_no_target_safe_path_emits_low_unresolved(self, cli_runner, small_impact_project, monkeypatch):
        """An unresolved symbol must emit ``risk_level_canonical="low"`` + rank 1.

        The W631 polarity is higher=worse; an unresolved symbol has zero
        blast radius (nothing to analyze), so the canonical floor is "low"
        (rank 1). Emitted unconditionally — agents can call
        ``risk_rank(summary["risk_level_canonical"])`` without None-handling.
        """
        monkeypatch.chdir(small_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "nonexistent_symbol_xyz_abc"],
            cwd=small_impact_project,
            json_mode=True,
        )
        # Per W1272: unresolved is exit 0 with partial_success=True.
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert summary["risk_level_canonical"] == "low", (
            f"Unresolved symbol must emit canonical 'low' floor; got {summary.get('risk_level_canonical')!r}"
        )
        assert summary["risk_rank"] == 1
        assert summary["risk_rank"] == risk_rank("low")

    def test_changed_mode_emits_canonical_high(self, cli_runner, high_impact_project, monkeypatch):
        """High-fan-in symbol (60 callers) must emit canonical 'high'.

        Task brief mentioned a potential ``--changed`` mode; cmd_impact has
        only the target-symbol mode in practice. This test stands in for
        the second-path coverage: it exercises the high-blast tier of the
        target-mode envelope so both the LOW and HIGH branches of
        ``_impact_risk_level`` are pinned end-to-end.
        """
        monkeypatch.chdir(high_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "hub_fn", "--depth", "10", "--max-callers", "0"],
            cwd=high_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert summary["affected_symbols"] >= 50, (
            f"Fixture should produce >=50 dependents to exercise HIGH tier; got {summary['affected_symbols']}"
        )
        # 60 callers >> 50-symbol threshold -> canonical "high" -> rank 3.
        assert summary["risk_level_canonical"] == "high", (
            f"High-fan-in fixture must emit canonical 'high'; got {summary.get('risk_level_canonical')!r}"
        )
        assert summary["risk_rank"] == 3
        assert summary["risk_rank"] == risk_rank("high")
        # Verdict still discloses the canonical bucket per LAW 6.
        assert "risk_level high" in summary["verdict"]


# ---------------------------------------------------------------------------
# Canonical vocabulary drift guard — pin W631.
# ---------------------------------------------------------------------------


class TestCanonicalSetDriftGuard:
    """W631 ``RISK_LEVELS`` stays the canonical 4-tier vocabulary.

    Mirrors the W641 test's drift guard discipline: if a future edit ever
    changes the canonical vocabulary, this test fails fast so cmd_impact's
    emit contract can be re-evaluated alongside.
    """

    def test_canonical_set_drift_guard(self):
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_impact_helper_only_emits_canonical_tokens(self):
        """Direct unit test on ``_impact_risk_level`` — every branch is canonical.

        The helper is the only place cmd_impact derives a tier label; every
        returned token must already be a member of ``RISK_LEVELS`` (no
        normalization needed at the call site). Pins the closed-enum
        invariant at the source so a future edit can't reintroduce a
        non-canonical intermediate token.
        """
        from roam.commands.cmd_impact import _impact_risk_level

        # Every threshold combination — pinned at canonical tokens.
        assert _impact_risk_level(0, 0.0) == "low"
        assert _impact_risk_level(1, 0.5) == "low"
        assert _impact_risk_level(10, 1.0) == "medium"  # >=10 syms threshold
        assert _impact_risk_level(5, 2.5) == "medium"  # >=2% reach threshold
        assert _impact_risk_level(50, 5.0) == "high"  # >=50 syms threshold
        assert _impact_risk_level(20, 10.5) == "high"  # >=10% reach threshold
        # Every emitted token must be canonical.
        for syms, reach in [(0, 0), (1, 0.5), (10, 1.0), (50, 5.0), (1000, 99.9)]:
            assert _impact_risk_level(syms, reach) in RISK_LEVELS


# ---------------------------------------------------------------------------
# Regression — W336 weighted_impact rounding + W462 PageRank precision.
# ---------------------------------------------------------------------------


class TestRegressionGuards:
    """Pin W336 + W462 — the canonical projection must not regress prior fixes."""

    def test_W336_W462_not_regressed(self, cli_runner, high_impact_project, monkeypatch):
        """W336 weighted_impact rounded to 6 decimals; W462 affected_file_list importance not 4-decimal-truncated.

        W336 widened ``weighted_impact`` rounding from 4 -> 6 decimals so
        per-node PageRank sums on multi-thousand-node graphs (1e-5 to 1e-3
        range) didn't truncate to 0.0. W462 audited the PageRank precision
        on affected_file_list ``importance``. Both fixes must survive the
        W641-followup-A canonical projection edit.
        """
        monkeypatch.chdir(high_impact_project)
        result = invoke_cli(
            cli_runner,
            ["impact", "hub_fn", "--depth", "10", "--max-callers", "0"],
            cwd=high_impact_project,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]

        # W336 — weighted_impact must be > 0 (60 callers => real blast).
        assert summary["weighted_impact"] > 0, (
            f"W336 regression: weighted_impact must be > 0 with 60 affected symbols; got {summary['weighted_impact']!r}"
        )
        # W336 — value must NOT be 4-decimal-truncated. A 6-decimal value
        # rounded at 4 decimals would lose any digit beyond 0.xxxx; verify
        # the round(x, 6) call still permits >4-decimal precision OR keeps
        # a recognizable non-zero value.
        wi = summary["weighted_impact"]
        wi_str = repr(wi)
        # The number must be expressible with up to 6 decimal places. A
        # round(_, 4) bug would round to 4 decimals max — i.e. 0.0123 with
        # the 5th/6th digits lost. We can't directly observe that from the
        # rounded value alone, but we CAN observe that the value isn't
        # 0.0 (which is what the W336 bug produced on small fan-ins).
        assert wi >= 1e-6, (
            f"W336 regression: weighted_impact {wi_str} below 6-decimal precision floor; "
            "a 4-decimal round() bug would zero this metric on small fan-ins."
        )

        # W462 — affected_file_list importance rounded to 6 decimals (not 4).
        # The affected_file_list dicts carry ``importance`` per-file; same
        # PageRank-precision rule applies.
        file_list = data.get("affected_file_list", [])
        assert file_list, "expected non-empty affected_file_list on high-fan-in fixture"
        for entry in file_list:
            assert "importance" in entry
            imp = entry["importance"]
            # Must be a float (not int-truncated) and within [0, 1].
            assert isinstance(imp, (int, float))
            assert 0.0 <= imp <= 1.0
        # At least one non-zero importance — confirms the same PageRank
        # precision protection on the file-importance ranking.
        nonzero = [e for e in file_list if e["importance"] > 0]
        assert nonzero, (
            "W462 regression: every affected_file importance is 0.0 — "
            "PageRank precision may have been truncated to 4 decimals."
        )
