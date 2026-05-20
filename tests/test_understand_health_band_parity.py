"""F2 ((internal memo)) — health-band label parity between
``roam understand`` and ``roam health``.

Pattern 3a (cross-command metric divergence) + LAW 6 (the verdict line must be
self-consistent). Before this fix, the SAME health score produced two
different verdict labels:

- ``roam understand`` called 75/100 "healthy" via a ``score >= 70`` cutoff
  (``cmd_understand.py`` JSON-mode verdict block).
- ``roam health`` reserved "Healthy" for ``score >= 80`` and called 75 "Fair"
  (``cmd_health.py:_compose_verdict``).

An agent reading ``understand`` then ``health`` got contradictory verdicts.

The fix hoisted the canonical band table to
``roam.quality.health_band`` (single source of truth, per the Pattern-3a fix
template that ``roam.quality.cycles`` / ``god_components`` already follow).
``understand`` now delegates to ``health_band(score)``; ``health``'s
``_compose_verdict`` thresholds are mirrored verbatim in that module.

These tests pin:

1. The canonical cutoffs (>=80 Healthy / >=60 Fair / >=40 Needs attention /
   <40 Unhealthy), including the boundary scores 69/70/79/80.
2. That ``roam health``'s verdict leading-word == ``health_band(score)`` for
   the live corpus score (so the module stays mirror-faithful to health's
   actual ``_compose_verdict``).
3. That ``roam understand``'s emitted ``summary.health_band`` ==
   ``health_band(summary.health_score)`` and equals what ``roam health`` would
   say for the same score.

Run isolation:
    python -m pytest tests/test_understand_health_band_parity.py -x -n 0
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

from roam.quality.health_band import health_band  # noqa: E402

# Drift-resistant repo-root resolution (W572 helper).
from tests._helpers.repo_root import repo_root  # noqa: E402


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# 1. Canonical cutoffs + boundary scores (pure unit pin)
# ---------------------------------------------------------------------------


class TestCanonicalBands:
    """Pin the canonical score->label cutoffs, the single source of truth."""

    def test_representative_score_75_is_fair(self):
        """75/100 buckets to "Fair" — exactly what ``roam health`` prints.

        This is the F2 divergence: the OLD ``understand`` >=70 cutoff called
        75 "healthy"; the canonical health band calls it "Fair".
        """
        assert health_band(75) == "Fair"

    @pytest.mark.parametrize(
        ("score", "label"),
        [
            # Boundary scores around each cutoff.
            (100, "Healthy"),
            (80, "Healthy"),
            (79, "Fair"),
            (70, "Fair"),  # the OLD understand cutoff — now "Fair" not "healthy"
            (69, "Fair"),
            (60, "Fair"),
            (59, "Needs attention"),
            (40, "Needs attention"),
            (39, "Unhealthy"),
            (0, "Unhealthy"),
        ],
    )
    def test_boundary_scores_bucket_canonically(self, score, label):
        assert health_band(score) == label, f"health_band({score}) should be {label!r}"

    def test_old_understand_cutoff_no_longer_diverges(self):
        """The 70..79 range is where the two commands used to disagree.

        OLD understand: >=70 -> "healthy". Canonical health: 70..79 -> "Fair".
        Pin every score in the divergence window to "Fair" so a regression to
        the inline >=70 cutoff is caught.
        """
        for score in range(70, 80):
            assert health_band(score) == "Fair", f"score {score} must be Fair (was 'healthy' pre-fix)"


# ---------------------------------------------------------------------------
# 2 + 3. Cross-command parity on the live corpus.
# ---------------------------------------------------------------------------


def _health_leading_label(verdict: str) -> str:
    """Extract the band label from a ``roam health`` verdict line.

    ``_compose_verdict`` emits one of:
      "Healthy codebase (..)" / "Fair codebase (..)" /
      "Needs attention (..)" / "Unhealthy codebase (..)".
    Map the leading prose back to the canonical band label.
    """
    v = verdict.strip().lower()
    if v.startswith("healthy"):
        return "Healthy"
    if v.startswith("fair"):
        return "Fair"
    if v.startswith("needs attention"):
        return "Needs attention"
    if v.startswith("unhealthy"):
        return "Unhealthy"
    return f"<unmapped: {verdict!r}>"


class TestLiveCorpusParity:
    """Invoke both commands on the roam-code repo and confirm agreement."""

    def test_understand_health_band_matches_health_verdict(self, cli_runner):
        root = repo_root()

        understand = invoke_cli(cli_runner, ["understand"], cwd=root, json_mode=True)
        assert understand.exit_code == 0, f"understand failed:\n{understand.output[:600]}"
        u_payload = _json.loads(understand.output)
        u_summary = u_payload.get("summary") or {}
        u_score = u_summary.get("health_score")
        u_band = u_summary.get("health_band")

        assert isinstance(u_score, (int, float)), f"understand must emit health_score; got {u_score!r}"
        assert u_band, f"understand must emit summary.health_band; got summary keys {sorted(u_summary)}"

        # (3) understand's label == the shared band map for its own score.
        assert u_band == health_band(u_score), (
            f"understand.health_band ({u_band!r}) must equal health_band({u_score}) "
            f"({health_band(u_score)!r}) — understand must delegate to the shared map"
        )

        health = invoke_cli(cli_runner, ["health"], cwd=root, json_mode=True)
        assert health.exit_code == 0, f"health failed:\n{health.output[:600]}"
        h_payload = _json.loads(health.output)
        h_summary = h_payload.get("summary") or {}
        h_score = h_summary.get("health_score")
        h_verdict = h_summary.get("verdict", "")

        # Same metric, same score (both read collect_metrics()).
        assert h_score == u_score, (
            f"health and understand must report the SAME health_score; health={h_score}, understand={u_score}"
        )

        # (2) health's verdict leading-word == shared band map for the score.
        h_label = _health_leading_label(h_verdict)
        assert h_label == health_band(h_score), (
            f"health verdict label ({h_label!r}) must equal health_band({h_score}) "
            f"({health_band(h_score)!r}); verdict={h_verdict!r}"
        )

        # The cross-command invariant the dogfood F2 finding demanded:
        # the SAME score never maps to two different verdict labels.
        assert u_band == h_label, (
            f"LAW 6 / Pattern 3a: understand and health disagree on the verdict "
            f"label for score {h_score}: understand={u_band!r}, health={h_label!r}"
        )

    def test_understand_emits_band_definition_sidecar(self, cli_runner):
        """Pattern 3a sidecar: understand names the band source-of-truth."""
        root = repo_root()
        result = invoke_cli(cli_runner, ["understand"], cwd=root, json_mode=True)
        assert result.exit_code == 0
        summary = (_json.loads(result.output).get("summary")) or {}
        definition = summary.get("health_band_definition", "")
        assert "health_band" in definition and ">=80 Healthy" in definition, (
            f"health_band_definition must name the canonical cutoffs; got {definition!r}"
        )
