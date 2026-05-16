"""W1235 - state-family alias registry substrate tests.

The registry collapses 7 historical "prerequisite missing" spellings
(``not_initialized`` / ``uninitialized`` / ``no_trail`` / ``no_scan`` /
``no_migrations`` / ``no_index`` / ``no_data``) onto the canonical
``"not_initialized"``. These tests pin the substrate; producer-site
adoption is handled in later waves.

Mirrors the discipline of ``test_mcp_param_names.py`` for the
``_PARAM_ALIASES`` template the registry is modelled on.
"""

from __future__ import annotations

import pytest

from roam.output.formatter import (
    _STATE_FAMILY_ALIASES,
    _STATE_FAMILY_CANONICALS,
    canonicalize_state,
)

# The seven historical synonyms that surfaced across substrate commands.
# Sourced from the W1235 grep audit; extending this list requires a
# matching edit to ``_STATE_FAMILY_ALIASES``.
_EXPECTED_ALIASES = (
    "not_initialized",
    "uninitialized",
    "no_trail",
    "no_scan",
    "no_migrations",
    "no_index",
    "no_data",
)


@pytest.mark.parametrize("alias", _EXPECTED_ALIASES)
def test_alias_maps_to_canonical(alias: str) -> None:
    """Every historical synonym collapses onto ``"not_initialized"``."""
    assert canonicalize_state(alias) == "not_initialized"


def test_registry_membership_matches_expected_aliases() -> None:
    """``_STATE_FAMILY_ALIASES`` keys equal the expected synonym set.

    Drift-guard: a future edit that adds an alias without updating the
    expected list (or vice versa) fails here instead of silently
    widening the canonical vocabulary.
    """
    assert set(_STATE_FAMILY_ALIASES) == set(_EXPECTED_ALIASES)


def test_canonicals_set_matches_alias_values() -> None:
    """``_STATE_FAMILY_CANONICALS`` is exactly the set of map values.

    The lint catches a new entry that introduces an un-announced
    canonical (e.g. someone maps ``"no_chain"`` -> ``"chain_missing"``
    without extending the canonical set).
    """
    assert _STATE_FAMILY_CANONICALS == frozenset(_STATE_FAMILY_ALIASES.values())


def test_canonical_is_idempotent() -> None:
    """``canonicalize_state("not_initialized")`` returns itself.

    Idempotency means downstream code can call the helper twice
    (defensively, or at multiple layers) without changing the value.
    """
    assert canonicalize_state("not_initialized") == "not_initialized"
    assert canonicalize_state(canonicalize_state("uninitialized")) == "not_initialized"


def test_unknown_state_passes_through_unchanged() -> None:
    """Unknown inputs are not rewritten.

    The registry is closed-vocabulary; states outside the registered
    set must round-trip verbatim so callers can compose the helper with
    vocabularies that have NOT been folded into this registry.
    """
    assert canonicalize_state("unknown_state") == "unknown_state"
    assert canonicalize_state("ok") == "ok"
    assert canonicalize_state("captured") == "captured"


def test_empty_string_passes_through() -> None:
    """Empty string is not in the registry; passes through unchanged."""
    assert canonicalize_state("") == ""


def test_single_canonical_today() -> None:
    """Only ``"not_initialized"`` is canonical at W1235 land time.

    Extending the canonical set is a deliberate source-code edit; this
    test pins the W1235 baseline so a future wave that adds (e.g.)
    ``"chain_missing"`` as a second canonical is forced to revisit the
    grep audit and update both the registry and this test together.
    """
    assert _STATE_FAMILY_CANONICALS == frozenset({"not_initialized"})
