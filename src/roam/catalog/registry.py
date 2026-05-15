"""Decorator-driven smell-detector registry (W870 P0 + W895/W896/W897 finalization).

This is the W869 registry-parity-pattern memo P0 deliverable. It bridges two
parallel collections that currently must be hand-maintained in lockstep:

* ``ALL_DETECTORS`` in ``src/roam/catalog/smells.py`` -- the canonical
  (smell_id, fn) list.
* ``_SMELL_KIND_TO_CONFIDENCE`` in ``src/roam/commands/cmd_smells.py`` -- the
  smell_id -> confidence-tier mapping.

The W862 + W867 lints catch drift at PR time. This module makes drift
impossible-to-write at construction time: ``@detector("foo", confidence=...)``
populates BOTH collections in one declarative call.

W941 sealed this: ``ALL_DETECTORS`` and ``_SMELL_KIND_TO_CONFIDENCE`` are now
derived views from this registry (per Gate 1 of W940's milestone). The
decorator IS the canonical source of truth. The W862 + W867 parity lints stay
in place as belt-and-braces regression guards in case a future refactor
un-derives the hand-rolled tables.

Archetype B (decorator) + Archetype E (construction-time validation) from
``(internal memo)``. The rollup-kind side-channel
(``register_rollup_kind`` and the ``rollup_kinds=`` decorator kwarg) covers the
W647 case where one detector emits a parent smell_id (``temporal-coupling``)
and a rollup smell_id (``temporal-coupling-cluster``) from the same function
body -- the rollup has no entry in ``ALL_DETECTORS`` but still needs a
confidence tier.

W895/W896/W897 finalization (the W940 sequencing memo):

* W895 -- ``@detector`` accepts ``rollup_kinds={"<suffix>": <tier>}`` so a
  parent + rollup pair register in a single declaration. Each entry creates
  ``<parent_id>-<suffix>`` via :func:`register_rollup_kind` internally.
* W896 -- :func:`all_detectors` returns SORTED output (alphabetical by
  smell_id). The underlying ``_DETECTORS`` list keeps registration order
  for debugging; only the public accessor sorts. SARIF-stable, grep-friendly,
  reproducible across source-order edits.
* W897 -- :func:`freeze_registry` validates the live registry: every
  smell_id in ``_KIND_TO_CONFIDENCE`` is either in ``_DETECTORS`` or has a
  recorded parent in ``_PARENT_OF_ROLLUP``; every tier is canonical; no
  duplicate smell_ids. Called from ``roam.catalog.smells.run_all_detectors``
  at entry as a final correctness gate before any detector runs.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Mapping

from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
)

# The canonical confidence vocabulary. Imported by-name from the four
# module-level constants in ``roam.db.findings`` rather than hard-coding
# the strings here -- if a fifth tier is added, the consumer must add
# the import explicitly, which is the correct review surface.
_CANONICAL_TIERS: frozenset[str] = frozenset(
    {
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STRUCTURAL,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_RUNTIME,
    }
)


# The source-of-truth collections populated by the decorator at module-import
# time. Hand-rolled ``ALL_DETECTORS`` and ``_SMELL_KIND_TO_CONFIDENCE`` are
# derived from these per W941 (Gate 1 of W940), so these dicts are the only
# place a smell_id / confidence-tier is authored.
# ``_DETECTORS`` is a list of (smell_id, fn) tuples, registration-order-
# preserved (the public ``all_detectors()`` accessor sorts on read per W896).
# ``_KIND_TO_CONFIDENCE`` covers both top-level detector smell_ids AND any
# rollup smell_ids registered via ``register_rollup_kind``.
# ``_PARENT_OF_ROLLUP`` tracks rollup_id -> parent_id so :func:`freeze_registry`
# can validate every confidence-mapped id has either a detector function or a
# parent that does.
_DETECTORS: list[tuple[str, Callable[[sqlite3.Connection], list[dict]]]] = []
_KIND_TO_CONFIDENCE: dict[str, str] = {}
_PARENT_OF_ROLLUP: dict[str, str] = {}


def _check_tier(tier: str, *, context: str) -> None:
    """Raise ValueError if ``tier`` is not a canonical confidence tier.

    ``context`` is a short phrase used in the error message so the caller
    sees which decorator / helper triggered the failure -- e.g.
    ``"detector('foo')"`` vs ``"register_rollup_kind('foo', 'foo-cluster')"``.
    """
    if tier not in _CANONICAL_TIERS:
        raise ValueError(
            f"{context}: unknown confidence tier {tier!r}; "
            f"must be one of {sorted(_CANONICAL_TIERS)}"
        )


def detector(
    smell_id: str,
    *,
    confidence: str,
    rollup_kinds: Mapping[str, str] | None = None,
) -> Callable[
    [Callable[[sqlite3.Connection], list[dict]]],
    Callable[[sqlite3.Connection], list[dict]],
]:
    """Register a smell detector function with its registry metadata.

    Usage::

        @detector("speculative-generality", confidence=CONFIDENCE_STRUCTURAL)
        def detect_speculative_generality(conn: sqlite3.Connection) -> list[dict]:
            ...

        @detector(
            "temporal-coupling",
            confidence=CONFIDENCE_HEURISTIC,
            rollup_kinds={"cluster": CONFIDENCE_STRUCTURAL},
        )
        def detect_temporal_coupling(conn: sqlite3.Connection) -> list[dict]:
            ...  # emits BOTH temporal-coupling AND temporal-coupling-cluster

    The decorator appends ``(smell_id, fn)`` to the detector registry and
    records ``smell_id -> confidence`` in the kind-to-confidence mapping --
    no separate dict to maintain.

    W895: ``rollup_kinds`` (optional) registers ``<smell_id>-<suffix>`` rollup
    ids alongside the parent in one declaration. Each mapping entry delegates
    to :func:`register_rollup_kind`. The standalone helper stays available for
    rollup ids that do not follow the ``<parent>-<suffix>`` naming pattern
    (none today, kept for forward-compat).

    Construction-time validation rules:

    * ``smell_id`` must be unique across the registry (no double-registration).
    * ``confidence`` must be one of the four canonical tiers from
      ``roam.db.findings`` (``CONFIDENCE_HEURISTIC`` / ``_STRUCTURAL`` /
      ``_STATIC_ANALYSIS`` / ``_RUNTIME``).
    * Each ``rollup_kinds`` entry obeys the same uniqueness + canonical-tier
      rules; collisions raise ValueError at decoration time.
    """
    _check_tier(confidence, context=f"detector({smell_id!r})")
    if smell_id in _KIND_TO_CONFIDENCE:
        raise ValueError(
            f"detector({smell_id!r}): duplicate smell_id in registry "
            f"(already mapped to {_KIND_TO_CONFIDENCE[smell_id]!r}). "
            f"Each detector must register exactly once."
        )

    def wrapper(
        fn: Callable[[sqlite3.Connection], list[dict]],
    ) -> Callable[[sqlite3.Connection], list[dict]]:
        _DETECTORS.append((smell_id, fn))
        _KIND_TO_CONFIDENCE[smell_id] = confidence
        if rollup_kinds:
            for suffix, rollup_tier in rollup_kinds.items():
                rollup_id = f"{smell_id}-{suffix}"
                register_rollup_kind(smell_id, rollup_id, confidence=rollup_tier)
        return fn

    return wrapper


def register_rollup_kind(
    parent_id: str,
    rollup_id: str,
    *,
    confidence: str,
) -> None:
    """Register a rollup smell_id emitted alongside a parent detector.

    Pattern: W647's ``detect_temporal_coupling`` emits BOTH
    ``temporal-coupling`` (parent, registered via :func:`detector`) AND
    ``temporal-coupling-cluster`` (rollup, registered here). The rollup
    has no ``ALL_DETECTORS`` row of its own but still needs a confidence
    tier so the findings registry doesn't silently fall back to the
    ``heuristic`` default.

    .. note::

       Prefer the ``rollup_kinds={"<suffix>": <tier>}`` kwarg on
       :func:`detector` for suffix-pattern rollups (the common case;
       this is what every shipped rollup uses today, including the W647
       ``temporal-coupling-cluster``). The decorator delegates to this
       helper internally, so the two paths are byte-equivalent — the
       kwarg just keeps the parent + rollup declaration co-located.

       This standalone API is the **escape hatch** for non-suffix
       rollup ids (e.g. a rollup that needs a completely unrelated
       smell_id rather than ``<parent>-<suffix>``). No detector in
       ``roam.catalog.smells`` exercises this path today — W895 folded
       every existing rollup onto the kwarg — but the API is kept
       stable for forward-compat. Mostly: registry evolution is the
       kind of thing where having a deliberate escape hatch is
       cheap insurance, and the surface is small (~30 lines including
       this docstring).

    The ``parent_id`` is recorded in ``_PARENT_OF_ROLLUP`` so
    :func:`freeze_registry` can validate that every confidence-mapped id
    is anchored to a real detector function via either ``_DETECTORS`` or
    a parent in ``_DETECTORS``.

    Construction-time validation:

    * ``rollup_id`` must be unique across the registry (no
      double-registration via either ``detector`` or this helper).
    * ``confidence`` must be one of the four canonical tiers.
    """
    context = f"register_rollup_kind({parent_id!r}, {rollup_id!r})"
    _check_tier(confidence, context=context)
    if rollup_id in _KIND_TO_CONFIDENCE:
        raise ValueError(
            f"{context}: duplicate rollup_id in registry "
            f"(already mapped to {_KIND_TO_CONFIDENCE[rollup_id]!r}). "
            f"Each rollup smell_id must register exactly once."
        )
    _KIND_TO_CONFIDENCE[rollup_id] = confidence
    _PARENT_OF_ROLLUP[rollup_id] = parent_id


def all_detectors() -> list[tuple[str, Callable[[sqlite3.Connection], list[dict]]]]:
    """Return the registered ``(smell_id, detect_fn)`` pairs.

    W896: returns SORTED by smell_id (alphabetical, ascending). The
    underlying ``_DETECTORS`` list preserves registration order for
    debugging, but the public accessor sorts so SARIF / grep / diff
    outputs are stable across source-order edits.

    A fresh list is returned each call so callers cannot mutate the
    registry by appending to the result.
    """
    return sorted(_DETECTORS, key=lambda pair: pair[0])


def kind_to_confidence() -> dict[str, str]:
    """Return the ``smell_id -> confidence_tier`` mapping.

    Covers BOTH top-level detector smell_ids AND rollup smell_ids
    registered via :func:`register_rollup_kind`. A fresh dict is returned
    each call so callers cannot mutate the registry by writing to the
    result.
    """
    return dict(_KIND_TO_CONFIDENCE)


def freeze_registry() -> None:
    """Validate registry consistency before any detector runs (W897).

    Three invariants are checked, all already enforced at decoration
    time -- ``freeze_registry`` is the belt-and-braces final gate so a
    direct write to ``_KIND_TO_CONFIDENCE`` (e.g. from a buggy test
    fixture or future refactor) can't poison a real ``run_all_detectors``
    call without surfacing a clear error.

    Invariants (checked in this order; first violation raises):

    1. No duplicate smell_ids across ``_DETECTORS`` — cheapest check,
       runs first so a registration-time mistake surfaces before the
       per-tier loop.
    2. Every ``smell_id`` in ``_KIND_TO_CONFIDENCE`` is anchored: it is
       either in ``_DETECTORS`` directly, OR ``_PARENT_OF_ROLLUP[smell_id]``
       is in ``_DETECTORS``. An orphan confidence-mapped id with no
       backing detector function is dead config that hides drift bugs.
    3. Every confidence tier is canonical (one of the four from
       ``roam.db.findings``).

    Raises ``ValueError`` on the first violation found.
    """
    detector_ids = {smell_id for smell_id, _fn in _DETECTORS}

    # Invariant 1: no duplicates in _DETECTORS list.
    if len(detector_ids) != len(_DETECTORS):
        seen: set[str] = set()
        dups: list[str] = []
        for smell_id, _fn in _DETECTORS:
            if smell_id in seen:
                dups.append(smell_id)
            seen.add(smell_id)
        raise ValueError(
            f"freeze_registry: duplicate smell_ids in _DETECTORS: "
            f"{sorted(set(dups))}. Each detector must register exactly once."
        )

    # Invariants 2 + 3: every confidence-mapped id is anchored + canonical-tier.
    for smell_id, tier in _KIND_TO_CONFIDENCE.items():
        _check_tier(tier, context=f"freeze_registry({smell_id!r})")
        if smell_id in detector_ids:
            continue
        parent = _PARENT_OF_ROLLUP.get(smell_id)
        if parent is None:
            raise ValueError(
                f"freeze_registry: smell_id {smell_id!r} has a confidence "
                f"tier {tier!r} but no detector function and no recorded "
                f"parent. Register it via @detector or "
                f"register_rollup_kind(parent_id, rollup_id, confidence=...)."
            )
        if parent not in detector_ids:
            raise ValueError(
                f"freeze_registry: rollup {smell_id!r} names parent "
                f"{parent!r} which is not a registered detector. Register "
                f"the parent via @detector first."
            )
