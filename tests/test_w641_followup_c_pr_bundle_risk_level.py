"""W641-followup-C — ``roam pr-bundle`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (sixth axis, after W547 severity + W596
confidence + W641 pr-risk + W641-followup-A impact + W641-followup-B
critique):

W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third
axis). W641-followup-A added it to ``cmd_impact`` (fourth axis).
W641-followup-B added it to ``cmd_critique`` (fifth axis). ``cmd_pr_bundle``
is the central evidence-compiler output — the highest fanout per edit:
the canonical risk-LEVEL axis propagates through the collector ->
ChangeEvidence packet -> exporters chain. Pinning it here closes the
W210 -> canonical-axis chain at the evidence-compiler boundary.

This module pins the W641-followup-C emit contract on the JSON
envelope:

* ``summary.risk_level_canonical`` — NEW. Projected via
  ``_pr_bundle_risk_level`` (max-wins across four bundle-domain signals:
  ``risk_severity_distribution`` H/M/L, ``causal_diff_high_severity_count``,
  ``unresolved_affected_symbols_count``, ``state``). Aggregation rule:
  **max-wins** — the worst signal's projected risk_level wins. Empty
  bundle / no signals -> ``"low"`` floor (W531 CI-safety).
* ``summary.risk_rank`` — NEW. Integer floor via the W631 ``risk_rank``
  table (``critical=4``/``high=3``/``medium=2``/``low=1``).
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone: the
  verdict line names the canonical bucket without any other envelope
  field.
* Top-level ``risk_level_canonical`` + ``risk_rank`` mirrors so
  consumers that read the envelope head without descending into
  ``summary`` see the canonical bucket too (parity with the W641-
  followup-A cmd_impact + W641-followup-B cmd_critique emit).
* BOTH ``pr-bundle emit`` and ``pr-bundle validate`` paths emit the
  field (both go through ``_build_envelope``); the mode-blocked early-
  return paths also emit it (Pattern 2: never omit the field).

Conservative-on-critical: the bundle's risk-severity emit vocab tops
out at ``H`` (no ``CRITICAL`` short-code). The projection saturates at
``high``; we do NOT escalate to ``critical`` on a threshold the
underlying emit vocab can't reach (mirrors the W641-followup-A
``_impact_risk_level`` + W641-followup-B ``_critique_risk_level``
discipline).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

# Ensure tests/conftest helpers are importable.
sys.path.insert(0, str(Path(__file__).parent))

from conftest import git_init, parse_json_output  # noqa: E402

from roam.cli import cli  # noqa: E402
from roam.commands.cmd_pr_bundle import (  # noqa: E402
    _PR_BUNDLE_RISK_SHORTCODE_TO_LEVEL,
    _pr_bundle_risk_level,
)
from roam.output.risk import RISK_LEVELS, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    """A minimal git repo so ``find_project_root()`` resolves correctly."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    subprocess.run(["git", "checkout", "-B", "test-branch"], cwd=proj, capture_output=True)
    monkeypatch.chdir(proj)
    return proj


def _invoke(cli_runner, args, **kw):
    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


# ---------------------------------------------------------------------------
# 1. Unit tests on _pr_bundle_risk_level — projection table
# ---------------------------------------------------------------------------


def test_empty_bundle_emits_low_floor():
    """No risks, complete, no causal-diff, no unresolved -> ``low``."""
    level = _pr_bundle_risk_level(
        state="complete",
        risk_severity_distribution={"H": 0, "M": 0, "L": 0},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=0,
    )
    assert level == "low"


def test_pass_assurance_projects_to_low():
    """``state="complete"`` + only L-tier risks -> ``low``."""
    level = _pr_bundle_risk_level(
        state="complete",
        risk_severity_distribution={"H": 0, "M": 0, "L": 3},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=0,
    )
    assert level == "low"


def test_insufficient_assurance_projects_to_medium():
    """``state="incomplete"`` (no risks, no diff) -> floor at ``medium``."""
    level = _pr_bundle_risk_level(
        state="incomplete",
        risk_severity_distribution={"H": 0, "M": 0, "L": 0},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=0,
    )
    assert level == "medium"


def test_fail_assurance_projects_to_high():
    """Any H-tier risk -> ``high`` regardless of state."""
    level = _pr_bundle_risk_level(
        state="complete",
        risk_severity_distribution={"H": 1, "M": 0, "L": 0},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=0,
    )
    assert level == "high"


def test_causal_diff_high_severity_promotes_to_high():
    """``causal_diff_high_severity_count > 0`` -> ``high`` (W15.3 signal)."""
    level = _pr_bundle_risk_level(
        state="complete",
        risk_severity_distribution={"H": 0, "M": 0, "L": 0},
        causal_diff_high_severity_count=2,
        unresolved_affected_symbols_count=0,
    )
    assert level == "high"


def test_unresolved_affected_promotes_to_medium():
    """Ghost affected_symbols -> ``medium`` floor (degraded resolution)."""
    level = _pr_bundle_risk_level(
        state="complete",
        risk_severity_distribution={"H": 0, "M": 0, "L": 0},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=1,
    )
    assert level == "medium"


def test_max_wins_aggregation():
    """When multiple signals fire, the worst level wins."""
    # M-tier risks + incomplete state + unresolved -> still capped at
    # medium because no high signal fired.
    level = _pr_bundle_risk_level(
        state="incomplete",
        risk_severity_distribution={"H": 0, "M": 4, "L": 7},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=2,
    )
    assert level == "medium"
    # Adding ANY H-tier risk promotes to high.
    level = _pr_bundle_risk_level(
        state="incomplete",
        risk_severity_distribution={"H": 1, "M": 4, "L": 7},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=2,
    )
    assert level == "high"


def test_conservative_no_critical_escalation():
    """No combination of bundle-domain signals can escalate to ``critical``.

    The bundle's risk-severity emit vocab tops out at ``H`` (no
    ``CRITICAL`` short-code; see ``_RISK_VALID_SHORTCODES``). Mirrors
    the W641-followup-A / B conservative-on-critical discipline.
    """
    # Maximum-signal pile-up: H-tier risks, causal-diff high, unresolved,
    # incomplete state. Still saturates at high.
    level = _pr_bundle_risk_level(
        state="incomplete",
        risk_severity_distribution={"H": 99, "M": 99, "L": 99},
        causal_diff_high_severity_count=99,
        unresolved_affected_symbols_count=99,
    )
    assert level == "high"
    # The level is in the canonical W631 vocabulary.
    assert level in RISK_LEVELS


def test_unknown_shortcode_safe_floor_with_warning():
    """A bogus H/M/L bucket key safe-floors to ``low`` + records a marker."""
    warnings_out: list[str] = []
    level = _pr_bundle_risk_level(
        state="complete",
        risk_severity_distribution={"BOGUS": 5, "L": 0},
        causal_diff_high_severity_count=0,
        unresolved_affected_symbols_count=0,
        warnings_out=warnings_out,
    )
    assert level == "low"
    assert any("BOGUS" in m for m in warnings_out), warnings_out
    assert any(m.startswith("pr_bundle_unknown_risk_severity:") for m in warnings_out)


def test_projection_table_shape():
    """The closed projection table mirrors the H/M/L emit vocab."""
    assert _PR_BUNDLE_RISK_SHORTCODE_TO_LEVEL == {
        "H": "high",
        "M": "medium",
        "L": "low",
    }
    # No critical key — conservative-on-critical contract.
    assert "CRITICAL" not in _PR_BUNDLE_RISK_SHORTCODE_TO_LEVEL
    # Every projected level is in the canonical W631 vocabulary.
    for v in _PR_BUNDLE_RISK_SHORTCODE_TO_LEVEL.values():
        assert v in RISK_LEVELS


# ---------------------------------------------------------------------------
# 2. Rank-helper invariants
# ---------------------------------------------------------------------------


def test_rank_matches_canonical_helper():
    """``risk_rank("high") > risk_rank("medium") > risk_rank("low")``."""
    assert risk_rank("high") > risk_rank("medium") > risk_rank("low")
    # All in the canonical 4-tier (W631).
    assert risk_rank("high") == 3
    assert risk_rank("medium") == 2
    assert risk_rank("low") == 1


# ---------------------------------------------------------------------------
# 3. CLI integration — emit envelope
# ---------------------------------------------------------------------------


def test_emit_envelope_has_risk_level_canonical(bundle_project):
    """``roam --json pr-bundle emit`` carries summary.risk_level_canonical."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "risk_level_canonical" in data["summary"], data["summary"]
    assert data["summary"]["risk_level_canonical"] in RISK_LEVELS


def test_emit_envelope_has_risk_rank(bundle_project):
    """``roam --json pr-bundle emit`` carries summary.risk_rank as int."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "risk_rank" in data["summary"]
    assert isinstance(data["summary"]["risk_rank"], int)
    # Rank consistency with the level.
    level = data["summary"]["risk_level_canonical"]
    assert data["summary"]["risk_rank"] == risk_rank(level)


def test_emit_envelope_top_level_mirrors(bundle_project):
    """Top-level mirrors risk_level_canonical + risk_rank on emit envelope."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "risk_level_canonical" in data
    assert "risk_rank" in data
    # Top-level mirrors the summary value.
    assert data["risk_level_canonical"] == data["summary"]["risk_level_canonical"]
    assert data["risk_rank"] == data["summary"]["risk_rank"]


# ---------------------------------------------------------------------------
# 4. CLI integration — validate envelope
# ---------------------------------------------------------------------------


def test_validate_envelope_has_risk_level_canonical(bundle_project):
    """``roam --json pr-bundle validate`` carries summary.risk_level_canonical."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "validate"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-validate")
    assert "risk_level_canonical" in data["summary"]
    assert data["summary"]["risk_level_canonical"] in RISK_LEVELS


def test_validate_envelope_has_risk_rank(bundle_project):
    """``roam --json pr-bundle validate`` carries summary.risk_rank as int."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "validate"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-validate")
    assert "risk_rank" in data["summary"]
    assert isinstance(data["summary"]["risk_rank"], int)
    # Rank consistency.
    level = data["summary"]["risk_level_canonical"]
    assert data["summary"]["risk_rank"] == risk_rank(level)


def test_validate_envelope_top_level_mirrors(bundle_project):
    """Top-level mirrors on validate envelope (parity with emit)."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "validate"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-validate")
    assert "risk_level_canonical" in data
    assert "risk_rank" in data


# ---------------------------------------------------------------------------
# 5. Verdict augmentation
# ---------------------------------------------------------------------------


def test_verdict_includes_risk_level_token(bundle_project):
    """Verdict line terminates on ``(risk_level <canonical>)``."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    verdict = data["summary"]["verdict"]
    # Regex: closed-enum risk_level parenthesis.
    pattern = r"\(risk_level (critical|high|medium|low)\)$"
    assert re.search(pattern, verdict), f"verdict missing risk_level token: {verdict!r}"


def test_verdict_token_matches_summary_field(bundle_project):
    """Verdict's risk_level token equals summary.risk_level_canonical."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(result, command="pr-bundle")
    verdict = data["summary"]["verdict"]
    canonical = data["summary"]["risk_level_canonical"]
    m = re.search(r"\(risk_level (\w+)\)$", verdict)
    assert m, f"verdict missing token: {verdict!r}"
    assert m.group(1) == canonical


def test_validate_verdict_includes_risk_level_token(bundle_project):
    """Validate-path verdict also terminates on the canonical token."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "validate"])
    data = parse_json_output(result, command="pr-bundle-validate")
    verdict = data["summary"]["verdict"]
    assert re.search(r"\(risk_level (critical|high|medium|low)\)$", verdict), verdict


# ---------------------------------------------------------------------------
# 6. Pattern 2 — never silent-omit on partial_success
# ---------------------------------------------------------------------------


def test_canonical_field_not_omitted_on_partial_success(bundle_project):
    """Even when partial_success=True, risk_level_canonical MUST be present.

    A freshly-initialised bundle is structurally incomplete (no affected,
    no context-cmd) so partial_success=True. The canonical risk-LEVEL
    field MUST still be emitted (Pattern 2: explicit-absence beats
    silent-absence).
    """
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["partial_success"] is True, "fixture sanity: should be incomplete"
    assert "risk_level_canonical" in data["summary"]
    assert "risk_rank" in data["summary"]
    # Bundle-domain projection: state="incomplete" + no signals -> medium.
    assert data["summary"]["risk_level_canonical"] == "medium"


# ---------------------------------------------------------------------------
# 7. emit vs validate consistency
# ---------------------------------------------------------------------------


def test_aggregation_consistent_emit_vs_validate(bundle_project):
    """Same bundle -> same risk_level on emit and validate."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    emit_result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    validate_result = _invoke(runner, ["--json", "pr-bundle", "validate"])
    emit_data = parse_json_output(emit_result, command="pr-bundle")
    validate_data = parse_json_output(validate_result, command="pr-bundle-validate")
    # Both paths derive risk_level from the same bundle state, so the
    # canonical value MUST match.
    assert emit_data["summary"]["risk_level_canonical"] == validate_data["summary"]["risk_level_canonical"]
    assert emit_data["summary"]["risk_rank"] == validate_data["summary"]["risk_rank"]


# ---------------------------------------------------------------------------
# 8. Complete-bundle path — happy case
# ---------------------------------------------------------------------------


def test_complete_bundle_with_unresolved_affected_projects_medium(bundle_project):
    """A complete bundle with ghost ``useRetry`` -> ``medium`` (degraded resolution).

    The bundle_project fixture has no symbol index, so any ``add affected``
    lands as an unresolved ghost (``resolution_state`` in
    ``no_db``/``not_found``/``lookup_failed``). The W641-followup-C
    projection floors the risk-LEVEL to ``medium`` when ANY affected
    symbol is unresolved (Pattern 2 visibility: degraded resolution must
    surface as a risk-axis signal).
    """
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry to S3"])
    _invoke(runner, ["pr-bundle", "add", "affected", "useRetry", "--blast-radius", "5"])
    _invoke(
        runner,
        ["pr-bundle", "add", "test-required", "tests/test_retry.py", "--reason", "covers"],
    )
    _invoke(runner, ["pr-bundle", "add", "test-run", "tests/test_retry.py", "--passed"])
    _invoke(runner, ["pr-bundle", "add", "context-cmd", "roam preflight useRetry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["state"] == "complete", data["summary"]
    # Ghost-symbol floor: unresolved_affected_symbols_count > 0 -> medium.
    assert data["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data["summary"]["risk_level_canonical"] == "medium"
    assert data["summary"]["risk_rank"] == 2


def test_complete_bundle_with_high_severity_risk_projects_high(bundle_project):
    """A complete bundle with an H-tier risk projects to ``high``."""
    runner = CliRunner()
    _invoke(runner, ["pr-bundle", "init", "--intent", "Add retry"])
    _invoke(runner, ["pr-bundle", "add", "affected", "useRetry", "--blast-radius", "5"])
    _invoke(
        runner,
        ["pr-bundle", "add", "risk", "external API", "--severity", "H"],
    )
    _invoke(
        runner,
        ["pr-bundle", "add", "test-required", "tests/test_retry.py", "--reason", "covers"],
    )
    _invoke(runner, ["pr-bundle", "add", "test-run", "tests/test_retry.py", "--passed"])
    _invoke(runner, ["pr-bundle", "add", "context-cmd", "roam preflight useRetry"])
    result = _invoke(runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["state"] == "complete", data["summary"]
    assert data["summary"]["risk_level_canonical"] == "high"
    assert data["summary"]["risk_rank"] == 3
