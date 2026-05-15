"""W911 — drift-guard for the confidence-tier enum.

Two parallel representations of the same 4-value confidence-tier
vocabulary used to exist:

* ``src/roam/db/findings.py`` exports the canonical four
  ``CONFIDENCE_HEURISTIC`` / ``CONFIDENCE_STRUCTURAL`` /
  ``CONFIDENCE_STATIC_ANALYSIS`` / ``CONFIDENCE_RUNTIME`` module-level
  string constants. Detectors persist findings under those values.
* ``src/roam/catalog/detectors.py`` defined ``_CONFIDENCE_BASES`` — a
  frozenset of literal strings the ``@detector`` decorator uses to
  validate the ``confidence_basis=`` metadata argument.

W911 collapsed the duplicate into a derived view of the canonical
constants. This test makes the parity load-bearing: a new tier added
to ``findings.py`` without a matching update in ``detectors.py`` (or
vice-versa) fails this test loudly with a side-by-side diff rather
than silently letting bogus strings flow into JSON envelopes.
"""

from __future__ import annotations

from roam.catalog.detectors import _CONFIDENCE_BASES
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
)


_CANONICAL_TIERS = frozenset(
    {
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STRUCTURAL,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_RUNTIME,
    }
)


def test_confidence_bases_matches_findings_canonical() -> None:
    """``_CONFIDENCE_BASES`` exactly equals the findings.py canonical set."""
    extra_in_detectors = sorted(_CONFIDENCE_BASES - _CANONICAL_TIERS)
    missing_from_detectors = sorted(_CANONICAL_TIERS - _CONFIDENCE_BASES)
    assert _CONFIDENCE_BASES == _CANONICAL_TIERS, (
        "Confidence tier drift between roam.catalog.detectors._CONFIDENCE_BASES "
        "and roam.db.findings canonical constants:\n"
        f"  in detectors but not findings: {extra_in_detectors}\n"
        f"  in findings but not detectors: {missing_from_detectors}\n"
        "Update both sides (and the SARIF / smells tier maps) when adding a tier."
    )


def test_confidence_bases_has_expected_cardinality() -> None:
    """Guards against an accidental tier addition or removal.

    CLAUDE.md documents the confidence-tier vocabulary as a closed
    four-value enum (heuristic / structural / static_analysis /
    runtime). Adding a fifth tier is a deliberate design change that
    needs the docs, the findings-registry confidence column, and the
    detector decorator to move together — this test makes the change
    visible in a code review.
    """
    assert len(_CONFIDENCE_BASES) == 4
    assert len(_CANONICAL_TIERS) == 4


def test_confidence_constants_have_expected_string_values() -> None:
    """Pins the wire format of the four canonical constants.

    The string values appear in the SQLite ``findings.confidence``
    column, in JSON envelopes consumed by external tools, and in the
    smells-tier mapping. Changing a literal silently is a schema
    break — this test forces an explicit reckoning.
    """
    assert CONFIDENCE_HEURISTIC == "heuristic"
    assert CONFIDENCE_STRUCTURAL == "structural"
    assert CONFIDENCE_STATIC_ANALYSIS == "static_analysis"
    assert CONFIDENCE_RUNTIME == "runtime"
