"""Regression tests for the host-noise refusing performance harness."""

from __future__ import annotations

from pathlib import Path

from dev.perf_harness import measure_case, summarize_timings


def test_tight_timings_report_median_and_mad() -> None:
    result = summarize_timings(
        "tight",
        [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0],
        spread_threshold=0.10,
    )

    assert result["median"] == 100.0
    assert result["mad"] == 1.0
    assert result["spread_percent"] == 1.0
    assert result["conclusive"] is True


def test_noisy_timings_refuse_to_conclude() -> None:
    result = summarize_timings(
        "noisy",
        [80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0],
        spread_threshold=0.10,
    )

    assert result["median"] is None
    assert result["mad"] is None
    assert result["spread_percent"] == 18.181818181818183
    assert result["conclusive"] is False
    assert result["status"] == "inconclusive"


def test_measure_case_discards_warmups() -> None:
    calls: list[int] = []
    elapsed_ms = iter([900.0, 901.0, 100.0, 101.0, 99.0])

    def fake_runner(_argv, _cwd, _timeout):
        calls.append(1)
        return next(elapsed_ms), 0, ""

    result = measure_case(
        "synthetic",
        ("synthetic",),
        cwd=Path("."),
        warmups=2,
        iterations=3,
        timeout=1,
        spread_threshold=0.10,
        runner=fake_runner,
    )

    assert len(calls) == 5
    assert result["timings_ms"] == [100.0, 101.0, 99.0]
    assert result["summary"]["median"] == 100.0
