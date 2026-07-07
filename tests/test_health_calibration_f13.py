"""F13 regression — health-score calibration disclaimer.

D1b battery: express (a best-maintained repo) scored 7/100 "Unhealthy" — a
demo-killer when read as a quality grade. A band rename cascades across the
``understand`` parity command + an exact-match service-report assertion, so the
sanctioned minimum is a calibration disclaimer: the score travels with a caveat
that it is a structural-complexity INDEX, not a quality verdict.
"""

from __future__ import annotations

from roam.output.metric_definitions import HEALTH_SCORE_DEFINITION


def test_definition_carries_calibration_caveat() -> None:
    d = HEALTH_SCORE_DEFINITION.lower()
    assert "calibration" in d
    assert "not a code-quality verdict" in d or "not a code-quality grade" in d
    # It must name what the number actually measures.
    assert "structural" in d
    # And point users at the honest comparison.
    assert "baseline" in d
