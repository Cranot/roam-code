"""Full-parity lint for the decorator-driven smell registry (W871 bulk migration).

This is the post-migration backstop test for the W869 P0 work. As of the
W871 bulk wave EVERY detector in ``ALL_DETECTORS`` is registered via
``@detector`` (and the W647 rollup ``temporal-coupling-cluster`` via the
``rollup_kinds={"cluster": ...}`` kwarg on its parent). The decorator-driven
registry is now EQUAL to the hand-rolled collections, not just a subset.

W941 closed that transition: ``ALL_DETECTORS`` and ``_SMELL_KIND_TO_CONFIDENCE``
are now derived views from this registry. The W862 + W867 lints stay in place
as belt-and-braces regression guards (assert registry==registry indirectly);
this test pins the equality invariant that proved the migration completed:

1. ``registry.all_detectors()`` smell_ids EQUAL ``ALL_DETECTORS`` smell_ids.
2. ``registry.kind_to_confidence()`` keys EQUAL ``_SMELL_KIND_TO_CONFIDENCE``
   keys.
3. The two W870-P0 detectors (``speculative-generality`` and
   ``temporal-coupling``) and the W647 rollup
   (``temporal-coupling-cluster``) are present in the new registry -- a
   regression here means a decorator was accidentally removed.

W947 note (KEPT as regression guard, do not delete):

* Pre-W941: these tests caught the SUBSET -> EQUAL transition as the bulk
  migration converted hand-rolled rows into ``@detector`` declarations.
* Post-W941: ``ALL_DETECTORS`` and ``_SMELL_KIND_TO_CONFIDENCE`` are derived
  views over the same registry these assertions read from, so the equality
  comparisons are effectively ``registry-derived == registry-derived`` and
  pass trivially.
* They are kept on purpose: if a future refactor reverts W941 and
  re-introduces hand-rolled tables, the SUBSET-vs-EQUAL distinction
  re-acquires teeth and this lint immediately fires on the regression.
  Cost is one cheap set-comparison per test run; benefit is a structural
  trip-wire against re-de-deriving the canonical registry.
"""

from __future__ import annotations

# Importing smells triggers the @detector + register_rollup_kind side
# effects at module-import time. The import is intentional even though
# the symbols below come from a different module (``registry``).
import roam.catalog.smells  # noqa: F401  -- populates the decorator-driven registry
from roam.catalog import registry
from roam.catalog.smells import ALL_DETECTORS
from roam.commands.cmd_smells import _SMELL_KIND_TO_CONFIDENCE


def _hand_rolled_detector_ids() -> set[str]:
    return {smell_id for smell_id, _fn in ALL_DETECTORS}


def _hand_rolled_confidence_ids() -> set[str]:
    return set(_SMELL_KIND_TO_CONFIDENCE.keys())


def _registry_detector_ids() -> set[str]:
    return {smell_id for smell_id, _fn in registry.all_detectors()}


def _registry_confidence_ids() -> set[str]:
    return set(registry.kind_to_confidence().keys())


def test_registry_equals_hand_rolled_detectors() -> None:
    """The decorator-driven registry EQUALS ALL_DETECTORS (W871 bulk migration).

    Post-W871-bulk: every detector ships with its @detector decorator
    (or a ``detector(...)(fn)`` call for the three out-of-file
    detectors that live in their own modules). Subset is no longer
    enough -- equality is the contract.

    Either side having extras means the migration regressed:
      - extras_in_registry: a stale @detector annotation outlived an
        ALL_DETECTORS removal.
      - missing_in_registry: an ALL_DETECTORS entry lost its decorator
        during a rebase / merge.
    """
    registry_ids = _registry_detector_ids()
    hand_rolled_ids = _hand_rolled_detector_ids()
    extras_in_registry = sorted(registry_ids - hand_rolled_ids)
    missing_in_registry = sorted(hand_rolled_ids - registry_ids)

    if extras_in_registry or missing_in_registry:
        raise AssertionError(
            f"Decorator-driven registry and ALL_DETECTORS disagree:\n"
            f"   extras in registry: {extras_in_registry}\n"
            f"   missing from registry: {missing_in_registry}\n"
            f"   Fix: every entry in ALL_DETECTORS must have a matching "
            f"@detector(...) decoration in src/roam/catalog/smells.py. "
            f"For out-of-file detectors (imported helpers), call "
            f"detector(\"<smell_id>\", confidence=<TIER>)(fn) directly "
            f"after the imports."
        )


def test_registry_equals_hand_rolled_confidence() -> None:
    """The decorator-driven registry EQUALS _SMELL_KIND_TO_CONFIDENCE keys.

    Post-W871-bulk: every smell_id (including rollups) has both a
    decorator-driven row and a hand-rolled row. Equality is the contract;
    any drift means the migration regressed.
    """
    registry_ids = _registry_confidence_ids()
    hand_rolled_ids = _hand_rolled_confidence_ids()
    extras_in_registry = sorted(registry_ids - hand_rolled_ids)
    missing_in_registry = sorted(hand_rolled_ids - registry_ids)

    if extras_in_registry or missing_in_registry:
        raise AssertionError(
            f"Decorator-driven kind_to_confidence and "
            f"_SMELL_KIND_TO_CONFIDENCE disagree:\n"
            f"   extras in registry: {extras_in_registry}\n"
            f"   missing from registry: {missing_in_registry}\n"
            f"   Fix: every key in _SMELL_KIND_TO_CONFIDENCE must have a "
            f"matching @detector / register_rollup_kind / "
            f"rollup_kinds={{...}} declaration in "
            f"src/roam/catalog/smells.py."
        )


def test_poc_detectors_present_in_registry() -> None:
    """The two W870-P0 detectors and the W647 rollup are registered.

    A regression here means a decorator was accidentally removed during
    a rebase or merge -- which would silently drop the new registry
    back to empty and re-open the drift class W869 was designed to
    eliminate.
    """
    registry_detector_ids = _registry_detector_ids()
    assert "speculative-generality" in registry_detector_ids, (
        "speculative-generality is missing from the decorator-driven "
        "registry. Restore the @detector annotation on "
        "detect_speculative_generality in src/roam/catalog/smells.py."
    )
    assert "temporal-coupling" in registry_detector_ids, (
        "temporal-coupling is missing from the decorator-driven "
        "registry. Restore the @detector annotation on "
        "detect_temporal_coupling in src/roam/catalog/smells.py."
    )

    registry_confidence_ids = _registry_confidence_ids()
    assert "temporal-coupling-cluster" in registry_confidence_ids, (
        "temporal-coupling-cluster (the W647 rollup) is missing from "
        "the decorator-driven registry. Restore the "
        "rollup_kinds={\"cluster\": CONFIDENCE_STRUCTURAL} kwarg on the "
        "@detector(\"temporal-coupling\", ...) declaration in "
        "src/roam/catalog/smells.py."
    )
