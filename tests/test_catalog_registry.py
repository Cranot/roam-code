"""Unit tests for the decorator-driven smell-detector registry (W870 P0).

The registry module is the first construction-time-validated SSoT for the
smell-detector + confidence-tier pair. These tests pin the validator
contract:

* ``@detector(smell_id, confidence=...)`` registers the function AND its
  tier in one declarative call.
* Unknown confidence tiers raise ValueError at decoration time -- not at
  CI time, not at runtime, not silently via a default fallback.
* Duplicate smell_ids raise ValueError -- registering the same detector
  twice is a programmer bug, not a no-op.
* ``register_rollup_kind`` extends the kind->confidence map WITHOUT
  appending to the detector list (rollup smell_ids are emitted from the
  same function body as their parent).

The tests use a custom-stub detector function per case rather than the
real W852/W853/W602 detectors, so they're independent of which detectors
the real ``smells.py`` happens to have migrated.

Implementation note: the registry has module-level mutable state. Each
test snapshots the current ``_DETECTORS`` / ``_KIND_TO_CONFIDENCE`` at
setup and restores it at teardown via the ``_isolated_registry`` fixture
so test order has no effect on real-registry contents.
"""

from __future__ import annotations

import sqlite3

import pytest

from roam.catalog import registry
from roam.catalog.registry import (
    all_detectors,
    detector,
    freeze_registry,
    kind_to_confidence,
    register_rollup_kind,
)
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
)


@pytest.fixture
def _isolated_registry():
    """Snapshot + restore the module-level registry around each test.

    Without this fixture, the real ``smells.py`` registrations populated
    at import time would leak into test assertions, and one test's stub
    detector would persist into the next test's snapshot.
    """
    saved_detectors = list(registry._DETECTORS)
    saved_confidence = dict(registry._KIND_TO_CONFIDENCE)
    saved_parent_of_rollup = dict(registry._PARENT_OF_ROLLUP)
    registry._DETECTORS.clear()
    registry._KIND_TO_CONFIDENCE.clear()
    registry._PARENT_OF_ROLLUP.clear()
    try:
        yield
    finally:
        registry._DETECTORS.clear()
        registry._KIND_TO_CONFIDENCE.clear()
        registry._PARENT_OF_ROLLUP.clear()
        registry._DETECTORS.extend(saved_detectors)
        registry._KIND_TO_CONFIDENCE.update(saved_confidence)
        registry._PARENT_OF_ROLLUP.update(saved_parent_of_rollup)


def _stub(conn: sqlite3.Connection) -> list[dict]:
    """Trivial detector stub. Returns [] -- shape-only, no DB access."""
    return []


def test_decorator_registers_detector_and_confidence(_isolated_registry):
    """A single @detector() call appends to BOTH _DETECTORS and _KIND_TO_CONFIDENCE."""

    @detector("foo-smell", confidence=CONFIDENCE_STRUCTURAL)
    def detect_foo(conn: sqlite3.Connection) -> list[dict]:
        return []

    detectors = all_detectors()
    confidence = kind_to_confidence()

    assert ("foo-smell", detect_foo) in detectors
    assert confidence["foo-smell"] == CONFIDENCE_STRUCTURAL
    # Decorator returns the function unchanged so the call site is still
    # importable / testable as a plain function.
    assert detect_foo.__name__ == "detect_foo"


def test_decorator_registers_each_canonical_tier(_isolated_registry):
    """All four canonical tiers are accepted; no other strings are."""

    @detector("h-id", confidence=CONFIDENCE_HEURISTIC)
    def h_fn(conn):  # noqa: ARG001
        return []

    @detector("s-id", confidence=CONFIDENCE_STRUCTURAL)
    def s_fn(conn):  # noqa: ARG001
        return []

    @detector("sa-id", confidence=CONFIDENCE_STATIC_ANALYSIS)
    def sa_fn(conn):  # noqa: ARG001
        return []

    @detector("r-id", confidence=CONFIDENCE_RUNTIME)
    def r_fn(conn):  # noqa: ARG001
        return []

    confidence = kind_to_confidence()
    assert confidence["h-id"] == CONFIDENCE_HEURISTIC
    assert confidence["s-id"] == CONFIDENCE_STRUCTURAL
    assert confidence["sa-id"] == CONFIDENCE_STATIC_ANALYSIS
    assert confidence["r-id"] == CONFIDENCE_RUNTIME


def test_decorator_rejects_unknown_tier(_isolated_registry):
    """An unknown confidence string raises ValueError at decoration time."""
    with pytest.raises(ValueError) as exc:

        @detector("bad-tier", confidence="nonsense")
        def _bad(conn):  # noqa: ARG001
            return []

    msg = str(exc.value)
    assert "bad-tier" in msg
    assert "nonsense" in msg
    assert "heuristic" in msg  # the canonical tier list is named in the error


def test_decorator_rejects_duplicate_smell_id(_isolated_registry):
    """Registering the same smell_id twice raises ValueError on the second call."""

    @detector("dup-id", confidence=CONFIDENCE_STRUCTURAL)
    def first(conn):  # noqa: ARG001
        return []

    with pytest.raises(ValueError) as exc:

        @detector("dup-id", confidence=CONFIDENCE_HEURISTIC)
        def second(conn):  # noqa: ARG001
            return []

    msg = str(exc.value)
    assert "dup-id" in msg
    assert "duplicate" in msg.lower()


def test_all_detectors_returns_sorted_order(_isolated_registry):
    """all_detectors() returns SORTED by smell_id (W896, alphabetical).

    Registration order is preserved in the underlying ``_DETECTORS`` list
    (for debugging), but the public accessor sorts so SARIF / grep /
    diff outputs are stable across source-order edits.
    """

    # Register in deliberately non-alphabetical order to prove the sort.
    @detector("zebra", confidence=CONFIDENCE_STRUCTURAL)
    def d_zebra(conn):  # noqa: ARG001
        return []

    @detector("alpha", confidence=CONFIDENCE_HEURISTIC)
    def d_alpha(conn):  # noqa: ARG001
        return []

    @detector("mid", confidence=CONFIDENCE_STATIC_ANALYSIS)
    def d_mid(conn):  # noqa: ARG001
        return []

    names = [smell_id for smell_id, _fn in all_detectors()]
    assert names == ["alpha", "mid", "zebra"]


def test_all_detectors_returns_fresh_list(_isolated_registry):
    """Mutating the returned list does not corrupt the underlying registry."""

    @detector("inner", confidence=CONFIDENCE_STRUCTURAL)
    def fn(conn):  # noqa: ARG001
        return []

    snapshot = all_detectors()
    snapshot.append(("forged", _stub))

    # The forgery must not appear in a second fetch.
    re_fetched = all_detectors()
    assert ("forged", _stub) not in re_fetched
    assert [name for name, _fn in re_fetched] == ["inner"]


def test_kind_to_confidence_returns_fresh_dict(_isolated_registry):
    """Mutating the returned dict does not corrupt the underlying registry."""

    @detector("only", confidence=CONFIDENCE_STRUCTURAL)
    def fn(conn):  # noqa: ARG001
        return []

    snapshot = kind_to_confidence()
    snapshot["forged"] = CONFIDENCE_RUNTIME
    snapshot["only"] = CONFIDENCE_HEURISTIC  # try to overwrite, too

    re_fetched = kind_to_confidence()
    assert "forged" not in re_fetched
    assert re_fetched["only"] == CONFIDENCE_STRUCTURAL


def test_register_rollup_kind_smoke(_isolated_registry):
    """register_rollup_kind adds to the confidence map but NOT to all_detectors."""

    @detector("parent", confidence=CONFIDENCE_STRUCTURAL)
    def parent_fn(conn):  # noqa: ARG001
        return []

    register_rollup_kind("parent", "parent-cluster", confidence=CONFIDENCE_STRUCTURAL)

    detectors = all_detectors()
    confidence = kind_to_confidence()

    # The rollup id is in the confidence map ...
    assert confidence["parent-cluster"] == CONFIDENCE_STRUCTURAL
    # ... but NOT in the detector list (it has no function of its own).
    assert "parent-cluster" not in [smell_id for smell_id, _fn in detectors]
    # The parent IS in both.
    assert "parent" in confidence
    assert ("parent", parent_fn) in detectors


def test_register_rollup_kind_rejects_unknown_tier(_isolated_registry):
    """Unknown confidence raises at rollup-registration time."""
    with pytest.raises(ValueError) as exc:
        register_rollup_kind("parent", "parent-cluster", confidence="nonsense")
    msg = str(exc.value)
    assert "parent-cluster" in msg
    assert "nonsense" in msg


def test_register_rollup_kind_rejects_duplicate_rollup_id(_isolated_registry):
    """Registering the same rollup_id twice raises ValueError."""
    register_rollup_kind("p1", "rollup-id", confidence=CONFIDENCE_STRUCTURAL)

    with pytest.raises(ValueError) as exc:
        register_rollup_kind("p2", "rollup-id", confidence=CONFIDENCE_HEURISTIC)

    msg = str(exc.value)
    assert "rollup-id" in msg
    assert "duplicate" in msg.lower()


def test_register_rollup_kind_rejects_collision_with_detector(_isolated_registry):
    """rollup_id cannot collide with a smell_id already registered as a detector."""

    @detector("clash", confidence=CONFIDENCE_STRUCTURAL)
    def clash_fn(conn):  # noqa: ARG001
        return []

    with pytest.raises(ValueError) as exc:
        register_rollup_kind("parent", "clash", confidence=CONFIDENCE_HEURISTIC)

    msg = str(exc.value)
    assert "clash" in msg
    assert "duplicate" in msg.lower()


def test_detector_rejects_collision_with_rollup_kind(_isolated_registry):
    """A new detector with a smell_id already used by a rollup raises."""
    register_rollup_kind("p", "shared-id", confidence=CONFIDENCE_STRUCTURAL)

    with pytest.raises(ValueError) as exc:

        @detector("shared-id", confidence=CONFIDENCE_HEURISTIC)
        def fn(conn):  # noqa: ARG001
            return []

    msg = str(exc.value)
    assert "shared-id" in msg
    assert "duplicate" in msg.lower()


# ---------------------------------------------------------------------------
# W895 -- rollup_kinds kwarg on @detector
# ---------------------------------------------------------------------------


def test_detector_with_rollup_kinds_kwarg(_isolated_registry):
    """``rollup_kinds={"suffix": tier}`` registers parent + rollup in one call."""

    @detector(
        "parent-id",
        confidence=CONFIDENCE_HEURISTIC,
        rollup_kinds={"cluster": CONFIDENCE_STRUCTURAL},
    )
    def parent_fn(conn):  # noqa: ARG001
        return []

    confidence = kind_to_confidence()
    detectors = [smell_id for smell_id, _fn in all_detectors()]

    # Parent appears in both detector list and confidence map.
    assert "parent-id" in detectors
    assert confidence["parent-id"] == CONFIDENCE_HEURISTIC
    # Rollup id (parent-id + "-" + suffix) appears only in confidence map.
    assert "parent-id-cluster" in confidence
    assert confidence["parent-id-cluster"] == CONFIDENCE_STRUCTURAL
    assert "parent-id-cluster" not in detectors


def test_detector_with_multiple_rollup_kinds(_isolated_registry):
    """``rollup_kinds`` accepts multiple suffix entries in one declaration."""

    @detector(
        "multi-parent",
        confidence=CONFIDENCE_STRUCTURAL,
        rollup_kinds={
            "cluster": CONFIDENCE_STRUCTURAL,
            "summary": CONFIDENCE_HEURISTIC,
        },
    )
    def fn(conn):  # noqa: ARG001
        return []

    confidence = kind_to_confidence()
    assert confidence["multi-parent"] == CONFIDENCE_STRUCTURAL
    assert confidence["multi-parent-cluster"] == CONFIDENCE_STRUCTURAL
    assert confidence["multi-parent-summary"] == CONFIDENCE_HEURISTIC


def test_detector_rollup_kinds_rejects_unknown_tier(_isolated_registry):
    """A rollup tier that isn't canonical raises at decoration time."""
    with pytest.raises(ValueError) as exc:

        @detector(
            "rollup-bad",
            confidence=CONFIDENCE_HEURISTIC,
            rollup_kinds={"cluster": "nonsense"},
        )
        def fn(conn):  # noqa: ARG001
            return []

    msg = str(exc.value)
    assert "rollup-bad-cluster" in msg
    assert "nonsense" in msg


# ---------------------------------------------------------------------------
# W897 -- freeze_registry()
# ---------------------------------------------------------------------------


def test_freeze_registry_accepts_clean_state(_isolated_registry):
    """A registry populated only via the public API passes freeze."""

    @detector("clean-detector", confidence=CONFIDENCE_STRUCTURAL)
    def fn(conn):  # noqa: ARG001
        return []

    register_rollup_kind(
        "clean-detector",
        "clean-detector-rollup",
        confidence=CONFIDENCE_HEURISTIC,
    )

    # Should not raise.
    freeze_registry()


def test_freeze_registry_rejects_orphan_confidence_id(_isolated_registry):
    """A confidence-mapped id with no detector and no parent raises."""
    # Inject directly into the mutable state to simulate the bug class.
    registry._KIND_TO_CONFIDENCE["orphan-id"] = CONFIDENCE_HEURISTIC

    with pytest.raises(ValueError) as exc:
        freeze_registry()

    msg = str(exc.value)
    assert "orphan-id" in msg
    assert "no detector" in msg or "no recorded parent" in msg


def test_freeze_registry_rejects_rollup_with_missing_parent(_isolated_registry):
    """A rollup whose recorded parent is missing from _DETECTORS raises."""
    # register_rollup_kind doesn't validate the parent exists at registration
    # time (module-import order isn't guaranteed); freeze_registry catches it.
    register_rollup_kind(
        "ghost-parent",
        "ghost-parent-rollup",
        confidence=CONFIDENCE_STRUCTURAL,
    )

    with pytest.raises(ValueError) as exc:
        freeze_registry()

    msg = str(exc.value)
    assert "ghost-parent-rollup" in msg
    assert "ghost-parent" in msg


def test_freeze_registry_rejects_non_canonical_tier(_isolated_registry):
    """A confidence value that isn't canonical raises at freeze time."""

    @detector("legit-detector", confidence=CONFIDENCE_STRUCTURAL)
    def fn(conn):  # noqa: ARG001
        return []

    # Inject a bogus tier directly to simulate a future refactor bug.
    registry._KIND_TO_CONFIDENCE["legit-detector"] = "made-up-tier"

    with pytest.raises(ValueError) as exc:
        freeze_registry()

    msg = str(exc.value)
    assert "made-up-tier" in msg


def test_freeze_registry_validates_live_registry() -> None:
    """The live (real) registry populated by smells.py passes freeze.

    This is the END-TO-END proof: after every detector + rollup
    registers via real module imports, freeze_registry() should pass
    without modification. A failure here means the bulk migration
    introduced a real registry-state bug.
    """
    # Importing smells triggers the real registrations. Not using the
    # _isolated_registry fixture deliberately -- this is the live check.
    import roam.catalog.smells  # noqa: F401

    freeze_registry()
