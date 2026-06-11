"""W792 — Byte-identity guard for the three .well-known MCP card variants.

Three competing path conventions exist for MCP server card discovery
(W765-RESEARCH):

* SEP-1649 nested: ``/.well-known/mcp/server-card.json``
* SEP-2127 no-suffix: ``/.well-known/mcp-server-card``
* Current roam (flat, .json): ``/.well-known/mcp-server-card.json``

Spec is unsettled, so we serve all three and pin them byte-identical via
SHA256 — any drift in the future is caught immediately, before a stale
mirror can mislead an MCP client probing the alternate paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# xdist: these tests read or mutate the REAL repo card JSONs + the
# _EXPECTED_CARD_SHA256 pin (no --target override exists), so they must
# serialize on one worker. Surfaced on the first parallel CI run
# (2026-06-11): two w844 tests raced across workers and flagged a real
# --apply as non-idempotent.
pytestmark = pytest.mark.xdist_group("card_pin_mutation")

_WELL_KNOWN = Path(__file__).resolve().parent.parent / "templates" / "distribution" / "landing-page" / ".well-known"

_CANONICAL = _WELL_KNOWN / "mcp-server-card.json"
_SEP_1649 = _WELL_KNOWN / "mcp" / "server-card.json"
_SEP_2127 = _WELL_KNOWN / "mcp-server-card"

_VARIANTS = (_CANONICAL, _SEP_1649, _SEP_2127)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("path", _VARIANTS, ids=lambda p: p.name or p.parent.name)
def test_w792_variant_exists(path: Path) -> None:
    assert path.is_file(), f"Missing .well-known MCP card variant: {path}"


def test_w792_all_three_variants_byte_identical() -> None:
    """All three .well-known card paths must hash to the same SHA256."""
    hashes = {path: _sha256(path) for path in _VARIANTS}
    canonical_hash = hashes[_CANONICAL]
    drifted = {str(path): h for path, h in hashes.items() if h != canonical_hash}
    assert not drifted, (
        "MCP server-card .well-known mirrors drifted from canonical "
        f"({_CANONICAL.name}={canonical_hash}); drifted={drifted}"
    )


def test_w792_all_three_variants_same_size() -> None:
    """Defence-in-depth: byte length must match (catches truncation before hash)."""
    sizes = {str(path): path.stat().st_size for path in _VARIANTS}
    distinct = set(sizes.values())
    assert len(distinct) == 1, f"MCP server-card .well-known mirrors differ in size: {sizes}"
