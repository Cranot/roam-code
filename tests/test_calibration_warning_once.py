"""The UNVALIDATED-profile warning fires once per profile, not per call.

`get_profile()` warns on stderr when a caller picks a profile absent from
`VALIDATED_PROFILES`. `route_for_plan` (compiler.py) calls `get_profile` once
per compile, and calibration sweeps re-emit routes repeatedly for the same
profile — so a per-call warning would flood stderr with duplicates. The
warning is deduplicated via `_WARNED_PROFILES` (one warning per profile per
process). These tests pin that contract.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import roam.plan.calibration as calibration
from roam.plan.calibration import (
    PROFILES,
    VALIDATED_PROFILES,
    CalibrationProfile,
    _WARNED_PROFILES,
    get_profile,
    reset_profile_warnings,
)


@pytest.fixture(autouse=True)
def _isolate_warning_memory() -> None:
    """Each test starts with empty warning memory and leaves it clean."""
    reset_profile_warnings()
    yield
    reset_profile_warnings()


def _unvalidated_profile_name() -> str:
    """A real registered profile that is NOT in VALIDATED_PROFILES."""
    unvalidated = [n for n in PROFILES if n not in VALIDATED_PROFILES]
    assert unvalidated, "no unvalidated profile available to exercise the warning"
    return unvalidated[0]


def test_warning_fires_once_for_repeated_calls(capsys: pytest.CaptureFixture[str]) -> None:
    """Repeated lookups of the same unvalidated profile warn exactly once."""
    name = _unvalidated_profile_name()

    get_profile(name)  # first lookup — warns
    for _ in range(5):  # repeated lookups — must stay silent
        get_profile(name)

    captured = capsys.readouterr()
    occurrences = captured.err.count("is UNVALIDATED")
    assert occurrences == 1, f"expected exactly one UNVALIDATED warning, got {occurrences}: {captured.err!r}"


def test_concurrent_warning_claims_one_slot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Concurrent lookups of one unvalidated profile claim one warning slot."""
    name = _unvalidated_profile_name()

    class SlowContainsSet(set[str]):
        def __contains__(self, item: object) -> bool:
            present = super().__contains__(item)
            if item == name and not present:
                time.sleep(0.01)
            return present

    monkeypatch.setattr(calibration, "_WARNED_PROFILES", SlowContainsSet())
    worker_count = 8
    start = Barrier(worker_count)

    def lookup() -> CalibrationProfile:
        start.wait()
        return get_profile(name)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda _: lookup(), range(worker_count)))

    assert results == [PROFILES[name]] * worker_count
    assert name in calibration._WARNED_PROFILES
    assert capsys.readouterr().err.count("is UNVALIDATED") == 1


def test_warning_refires_after_reset(capsys: pytest.CaptureFixture[str]) -> None:
    """`reset_profile_warnings()` re-arms the warning for a fresh batch."""
    name = _unvalidated_profile_name()

    get_profile(name)
    assert capsys.readouterr().err.count("is UNVALIDATED") == 1

    # Second call is silent (already warned).
    get_profile(name)
    assert capsys.readouterr().err == ""

    # Reset re-arms; the next call warns again.
    reset_profile_warnings()
    get_profile(name)
    assert capsys.readouterr().err.count("is UNVALIDATED") == 1


def test_distinct_unvalidated_profiles_each_warn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dedup key is the profile NAME, not a single global latch.

    Registering a second unvalidated profile must not be silenced by a prior
    warning for a *different* profile — each name earns its own warning slot.
    """
    name_a = _unvalidated_profile_name()
    name_b = "synthetic-unvalidated-for-test"

    profile_b = CalibrationProfile(
        name=name_b,
        family="open-weight",
        light_model="x",
        heavy_model="y",
        light_input_cost=0.0,
        light_output_cost=0.0,
        heavy_input_cost=0.0,
        heavy_output_cost=0.0,
        measured_at="UNVALIDATED",
    )
    monkeypatch.setitem(PROFILES, name_b, profile_b)

    get_profile(name_a)
    assert capsys.readouterr().err.count("is UNVALIDATED") == 1

    get_profile(name_b)  # different name — warns despite name_a already warned
    assert capsys.readouterr().err.count("is UNVALIDATED") == 1
    assert name_a in _WARNED_PROFILES
    assert name_b in _WARNED_PROFILES


def test_validated_profile_never_warns(capsys: pytest.CaptureFixture[str]) -> None:
    """The validated default profile emits no warning, no matter how many calls."""
    name = next(iter(VALIDATED_PROFILES))
    for _ in range(5):
        get_profile(name)
    captured = capsys.readouterr()
    assert captured.err == "", f"validated profile should not warn: {captured.err!r}"
