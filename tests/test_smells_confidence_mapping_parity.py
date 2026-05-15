"""Parity lint for the smell-detector confidence-tier mapping (W862 sister).

``src/roam/commands/cmd_smells.py`` maps every emitted smell_id to a
confidence tier via ``_SMELL_KIND_TO_CONFIDENCE``. The mapping and the
set of smell_ids actually emitted by detectors in
``src/roam/catalog/smells.py`` must stay parallel:

1. Every detector in ``ALL_DETECTORS`` (and every rollup smell_id its
   detector emits) MUST have a row in ``_SMELL_KIND_TO_CONFIDENCE``.
   A missing row silently falls back to ``_SMELL_DEFAULT_CONFIDENCE =
   "heuristic"`` -- which can mis-classify a deterministic AST detector
   as a regex heuristic and tank its weight in the findings registry.
2. Every key in ``_SMELL_KIND_TO_CONFIDENCE`` MUST correspond to a
   smell_id that some detector actually emits. Orphan keys are dead
   config that drift over time; they hide future parity bugs when a
   typo'd new name silently keys onto an unrelated tier.
3. Every value MUST be one of the four canonical confidence tiers
   defined in ``src/roam/db/findings.py``. Anything else is a typo
   that downstream consumers won't reject (the column is plain TEXT).

The reference set of emitted smell_ids is derived by AST-walking
``smells.py`` for every ``_finding("<smell_id>", ...)`` call. This
captures both top-level detectors (one entry in ``ALL_DETECTORS``) AND
rollup smell_ids that a single detector emits alongside its primary
findings -- e.g. ``temporal-coupling-cluster`` is emitted by
``detect_temporal_coupling`` (W647) but has no separate ALL_DETECTORS row.

W852 caught the (a) drift accidentally (3 detectors:
speculative-generality, parallel-hierarchy, cross-layer-clone). This
standing test catches the next drift at PR time. Sister lint:
``tests/test_smells_detector_count_drift.py``.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import roam.catalog.smells as smells_module
from roam.catalog.registry import kind_to_confidence as registry_kind_to_confidence
from roam.catalog.smells import ALL_DETECTORS
from roam.commands.cmd_smells import (
    _SMELL_DEFAULT_CONFIDENCE,
    _SMELL_KIND_TO_CONFIDENCE,
)
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
)

# The canonical tier set. Imported by-name from the four module-level
# constants in ``roam.db.findings`` rather than hard-coding the strings
# here -- if a fifth tier is added, the consumer must add the import
# explicitly, which is the correct review surface.
_CANONICAL_CONFIDENCE_TIERS: frozenset[str] = frozenset(
    {
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STRUCTURAL,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_RUNTIME,
    }
)

# W917: smells use a 3-of-4 subset of the canonical tiers. ``runtime`` is
# reserved for hotspots (requires ingested traces) and never applies to
# a static-AST smell detector. This sub-allowlist is what
# ``test_all_confidence_values_are_canonical`` validates against, so
# the error message names the smells-specific set instead of suggesting
# ``runtime`` as a valid choice for a smell. ``_CANONICAL_CONFIDENCE_TIERS``
# stays defined for sibling tests that still want the full four.
_SMELL_CONFIDENCE_TIERS: frozenset[str] = frozenset(
    {
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STRUCTURAL,
        CONFIDENCE_STATIC_ANALYSIS,
    }
)


def _cmd_smells_path() -> Path:
    """Resolve cmd_smells.py via importlib (no hard-coded absolute paths)."""
    module = importlib.import_module("roam.commands.cmd_smells")
    return Path(module.__file__).resolve()


def _smells_catalog_path() -> Path:
    return Path(smells_module.__file__).resolve()


def _emitted_smell_ids() -> set[str]:
    """AST-derive every smell_id literal passed as ``_finding(<id>, ...)``.

    This is the authoritative emitted-id set: it covers both top-level
    detectors registered in ``ALL_DETECTORS`` AND any rollup smell_ids
    a single detector emits as a side-effect (e.g.
    ``temporal-coupling-cluster``). A name-pattern grep would miss
    dynamically-built ids; restricting to string-literal first args is
    deliberate -- detectors are expected to emit a closed enumeration,
    not f-string-built smell_ids.
    """
    path = _smells_catalog_path()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    emitted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_finding"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            emitted.add(first.value)
    return emitted


def _registered_detector_ids() -> set[str]:
    return {smell_id for smell_id, _fn in ALL_DETECTORS}


def test_all_detectors_have_confidence_tier() -> None:
    """Every emitted smell_id has a row in _SMELL_KIND_TO_CONFIDENCE.

    Missing rows silently fall back to the default tier (``heuristic``).
    That's fine for genuinely heuristic detectors but disastrous for a
    new AST-deterministic detector that should have shipped as
    ``static_analysis`` or ``structural`` -- the lower tier suppresses
    the finding's weight in downstream scoring.

    The required-key set is the union of:
      - smell_ids registered in ``ALL_DETECTORS`` (top-level detectors), and
      - smell_ids emitted via ``_finding("<id>", ...)`` literal calls in
        ``smells.py`` (catches rollup smell_ids whose detector function
        registers under a different name).
    """
    required = _registered_detector_ids() | _emitted_smell_ids()
    mapped = set(_SMELL_KIND_TO_CONFIDENCE.keys())
    missing = sorted(required - mapped)

    if missing:
        cmd_smells_path = _cmd_smells_path()
        suggestion_lines = "\n".join(
            f'    "{smell_id}": "{_SMELL_DEFAULT_CONFIDENCE}",  # TODO: pick tier'
            for smell_id in missing
        )
        raise AssertionError(
            f"_SMELL_KIND_TO_CONFIDENCE is missing rows for "
            f"{len(missing)} smell_id(s) that are emitted by detectors "
            f"in src/roam/catalog/smells.py: {missing}.\n"
            f"   Fix: add a row for each in "
            f"{cmd_smells_path} -- the canonical tiers are "
            f"{sorted(_CANONICAL_CONFIDENCE_TIERS)}. Default to "
            f'"{_SMELL_DEFAULT_CONFIDENCE}" only if the detector is a '
            f"regex/name-pattern heuristic; pick `structural` for "
            f"AST-edge predicates and `static_analysis` for "
            f"AST-metric or taint/dataflow analysis.\n"
            f"   Suggested skeleton (replace TODO with chosen tier):\n"
            f"{suggestion_lines}"
        )


def test_no_orphan_confidence_keys() -> None:
    """Every key in _SMELL_KIND_TO_CONFIDENCE matches an emitted smell_id.

    Orphan keys (mapping kept after the detector was removed or renamed)
    are dead config: they don't fail loudly but they hide future parity
    bugs when a typo'd new name silently keys onto an unrelated tier.
    A key is "real" if either ALL_DETECTORS registers it OR
    ``smells.py`` emits it via ``_finding("<id>", ...)``.
    """
    real = _registered_detector_ids() | _emitted_smell_ids()
    mapped = set(_SMELL_KIND_TO_CONFIDENCE.keys())
    orphans = sorted(mapped - real)

    if orphans:
        cmd_smells_path = _cmd_smells_path()
        smells_path = _smells_catalog_path()
        raise AssertionError(
            f"_SMELL_KIND_TO_CONFIDENCE has {len(orphans)} orphan "
            f"key(s) with no matching detector or emit-site: "
            f"{orphans}.\n"
            f"   Fix: remove the orphan row(s) from "
            f"{cmd_smells_path}, OR (if the detector is in flight) add "
            f"it to ALL_DETECTORS / a ``_finding(\"<id>\", ...)`` call "
            f"in {smells_path} so both halves stay parallel."
        )


def test_all_confidence_values_are_canonical() -> None:
    """Every value in _SMELL_KIND_TO_CONFIDENCE is a smells-canonical tier.

    The findings registry stores ``confidence`` as plain TEXT, so a typo
    here will not be caught at insert time -- it will silently flow
    through to downstream consumers that filter on the canonical four.

    Smells use a 3-of-4 subset of the canonical tiers: ``runtime`` is
    reserved for hotspots (requires ingested traces) and never applies
    to a static-AST smell. The allowlist this test enforces is
    ``_SMELL_CONFIDENCE_TIERS``; the error message names that
    smells-specific set rather than suggesting ``runtime`` as a valid
    fix.
    """
    invalid: list[tuple[str, str]] = sorted(
        (smell_id, tier)
        for smell_id, tier in _SMELL_KIND_TO_CONFIDENCE.items()
        if tier not in _SMELL_CONFIDENCE_TIERS
    )

    if invalid:
        cmd_smells_path = _cmd_smells_path()
        bad_rows = "\n".join(
            f"    {smell_id!r}: {tier!r}  -- not a smells-canonical tier"
            for smell_id, tier in invalid
        )
        raise AssertionError(
            f"_SMELL_KIND_TO_CONFIDENCE has {len(invalid)} non-canonical "
            f"value(s):\n{bad_rows}\n"
            f"   Fix: replace each value at {cmd_smells_path} with one "
            f"of the 3-of-4 canonical tiers smells may use from "
            f"src/roam/db/findings.py ({sorted(_SMELL_CONFIDENCE_TIERS)}). "
            f"Smells don't use ``runtime`` -- that tier is reserved for "
            f"hotspots (requires ingested traces). If a genuinely new "
            f"tier is needed, add a module-level constant in "
            f"roam.db.findings AND extend the import block in this "
            f"test so the new tier becomes part of the canonical set "
            f"by deliberate review, not accident."
        )


def test_decorator_and_handrolled_agree_on_confidence_tier() -> None:
    """The decorator-driven registry and hand-rolled dict must agree on VALUES.

    W871 introduced the @detector decorator + register_rollup_kind in
    ``roam.catalog.registry`` as the future single source of truth. During
    the staged migration BOTH registries coexist: the decorator-populated
    ``kind_to_confidence()`` AND the hand-rolled ``_SMELL_KIND_TO_CONFIDENCE``.

    Keys are checked by the sibling lints above. This lint checks VALUES:
    for every smell_id present in BOTH, the two registries MUST map to
    the same confidence tier. W871 surfaced exactly this drift class —
    ``temporal-coupling`` registered as STRUCTURAL via the decorator while
    the hand-rolled dict had HEURISTIC. Without this lint the disagreement
    would survive until the migration deletes the hand-rolled side, then
    silently flip downstream consumer behavior.
    """
    decorator_map = registry_kind_to_confidence()
    handrolled_map = dict(_SMELL_KIND_TO_CONFIDENCE)
    shared = sorted(set(decorator_map) & set(handrolled_map))

    disagreements: list[tuple[str, str, str]] = []
    for smell_id in shared:
        dec_tier = decorator_map[smell_id]
        hr_tier = handrolled_map[smell_id]
        if dec_tier != hr_tier:
            disagreements.append((smell_id, dec_tier, hr_tier))

    if disagreements:
        rows = "\n".join(
            f"    {smell_id!r}: decorator={dec!r}, handrolled={hr!r}"
            for smell_id, dec, hr in disagreements
        )
        raise AssertionError(
            f"Decorator-registered confidence tiers disagree with "
            f"_SMELL_KIND_TO_CONFIDENCE on {len(disagreements)} smell_id(s):\n"
            f"{rows}\n"
            f"   Fix: pick the correct tier per the CLAUDE.md confidence "
            f"vocabulary and align BOTH registries. The hand-rolled dict "
            f"in cmd_smells.py is the canonical source of truth during the "
            f"W871 migration; update the @detector / register_rollup_kind "
            f"call in roam.catalog.smells to match. Once the migration "
            f"completes the decorator becomes canonical."
        )
