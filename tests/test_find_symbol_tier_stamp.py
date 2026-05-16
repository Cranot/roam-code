"""W1249 — substrate test for ``_resolution_tier`` stamping on resolver returns.

W1242 / W1243 / W1244 / W1248 each had to re-derive resolver tier at the
caller site (compare row.name / qualified_name to input, or re-query
``symbols`` for an exact match). W1249 hoists that work into the resolver
itself: every row returned by ``find_symbol`` /
``find_symbol_with_alternatives`` carries ``_resolution_tier``, and
``find_symbol_id`` gains a tier-aware companion
``find_symbol_id_with_tier``. This file locks in:

* exact-name (qualified or simple) match -> ``_resolution_tier == "symbol"``
* LIKE-fallback match -> ``_resolution_tier == "fuzzy"``
* alternatives carry the same tier as the winning row
* backwards-compat default (``row.get("_resolution_tier", "symbol")``)
  works for callers that build rows independently
* ``find_symbol_id_with_tier`` returns ``(ids, tier)`` and pairs cleanly
  with the existing ``find_symbol_id`` single-return wrapper
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.commands.resolve import (  # noqa: E402
    find_symbol,
    find_symbol_with_alternatives,
)
from roam.db.connection import open_db  # noqa: E402
from roam.graph.pathfinding import (  # noqa: E402
    find_symbol_id,
    find_symbol_id_with_tier,
)

# ---------------------------------------------------------------------------
# Fixture — distinct symbols across exact-name and fuzzy-LIKE tiers.
# ---------------------------------------------------------------------------


@pytest.fixture
def tier_project(tmp_path):
    """Project with one distinctively-named symbol so we can exercise
    exact-name match (``handle_payment_event``) and LIKE-fallback
    (``payment_event`` -> matches via substring).
    """
    proj = tmp_path / "tier_stamp"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text(
        "def handle_payment_event(event):\n"
        "    return event.id\n"
        "\n"
        "def caller():\n"
        "    return handle_payment_event(None)\n"
    )
    git_init(proj)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        out, rc = index_in_process(proj)
        assert rc == 0, f"index failed:\n{out}"
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# find_symbol — exact-name + fuzzy + miss.
# ---------------------------------------------------------------------------


def test_find_symbol_exact_match_stamps_symbol_tier(tier_project):
    """Exact-name match must stamp ``_resolution_tier == "symbol"``.

    Both the simple-name and qualified-name rungs are exact-match tiers;
    W1242/W1244/W1248 detection helpers collapsed both into ``symbol`` and
    the W1249 substrate preserves that collapse.
    """
    with open_db(readonly=True) as conn:
        row = find_symbol(conn, "handle_payment_event")
    assert row is not None
    assert row["_resolution_tier"] == "symbol"
    # Row still exposes the original row keys -- stamp is additive.
    assert row["name"] == "handle_payment_event"


def test_find_symbol_fuzzy_match_stamps_fuzzy_tier(tier_project):
    """LIKE-fallback match must stamp ``_resolution_tier == "fuzzy"``.

    No symbol is literally named ``payment_event``; ``find_symbol`` only
    lands on ``handle_payment_event`` via the LIKE ``%payment_event%`` branch.
    """
    with open_db(readonly=True) as conn:
        row = find_symbol(conn, "payment_event")
    assert row is not None
    assert row["_resolution_tier"] == "fuzzy"
    # The resolved row is the underlying symbol -- fuzzy match landed on the
    # wider name, not the narrower one.
    assert row["name"] == "handle_payment_event"


def test_find_symbol_miss_returns_none_unchanged(tier_project):
    """Misses still return ``None``; stamping is a no-op on the failure path.

    The W1241 disclosure for unresolved targets is the caller's job
    (cmd_impact emits ``resolution_disclosure("unresolved", ...)``) -- the
    substrate's contract is "if a row comes back it carries a tier".
    """
    with open_db(readonly=True) as conn:
        row = find_symbol(conn, "definitely_not_a_real_symbol_xyz")
    assert row is None


# ---------------------------------------------------------------------------
# find_symbol_with_alternatives — winner + alternatives share the tier.
# ---------------------------------------------------------------------------


def test_find_symbol_with_alternatives_stamps_winner_and_alts(tmp_path):
    """All rows (winner + alternatives) share the same resolver tier.

    Two same-named functions across different files exercises the ambiguity
    branch; W1249 must stamp BOTH the winner and the alternatives so any
    caller iterating alternatives can read the tier without re-deriving.
    """
    proj = tmp_path / "alts"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text("def deleteRow(t, i):\n    pass\n")
    (proj / "b.py").write_text(
        "from a import deleteRow\n\ndef deleteRow(g, i):\n    pass\n\ndef caller():\n    return deleteRow(None, 0)\n"
    )
    git_init(proj)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        out, rc = index_in_process(proj)
        assert rc == 0, f"index failed:\n{out}"
        with open_db(readonly=True) as conn:
            winner, alts = find_symbol_with_alternatives(conn, "deleteRow")
    finally:
        os.chdir(old_cwd)

    assert winner is not None
    assert winner["_resolution_tier"] == "symbol"
    # Multiple matches at the exact-name rung -> at least one alternative.
    assert len(alts) >= 1
    for alt in alts:
        assert alt["_resolution_tier"] == "symbol"


# ---------------------------------------------------------------------------
# Backwards-compat default.
# ---------------------------------------------------------------------------


def test_default_tier_for_unstamped_row_is_symbol():
    """Callers reading ``row.get("_resolution_tier", "symbol")`` on a row
    built independently (no stamp) get ``"symbol"`` -- the safest default
    matches the historical assumption that any returned row was an
    exact-match success.
    """
    # Simulate a caller that constructs a row dict by hand (e.g. via
    # ``dict(some_row)`` in code that predates W1249).
    fake_row = {"id": 1, "name": "foo", "qualified_name": "mod.foo"}
    assert fake_row.get("_resolution_tier", "symbol") == "symbol"


# ---------------------------------------------------------------------------
# find_symbol_id_with_tier — companion helper for cmd_trace.
# ---------------------------------------------------------------------------


def test_find_symbol_id_with_tier_exact(tier_project):
    """Exact-name input must return ``"symbol"`` tier and a non-empty id list."""
    with open_db(readonly=True) as conn:
        ids, tier = find_symbol_id_with_tier(conn, "handle_payment_event")
    assert ids, "exact-name match must return at least one id"
    assert tier == "symbol"


def test_find_symbol_id_with_tier_fuzzy(tier_project):
    """LIKE-fallback input must return ``"fuzzy"`` tier and a non-empty id list."""
    with open_db(readonly=True) as conn:
        ids, tier = find_symbol_id_with_tier(conn, "payment_event")
    assert ids, "fuzzy LIKE match must return at least one id"
    assert tier == "fuzzy"


def test_find_symbol_id_with_tier_unresolved(tier_project):
    """No-match input must return ``"unresolved"`` tier and an empty id list.

    This branch is the one cmd_trace's W1248 helper guarded defensively
    ("empty input list -> unresolved"); W1249 makes it the substrate's
    contract.
    """
    with open_db(readonly=True) as conn:
        ids, tier = find_symbol_id_with_tier(conn, "definitely_not_a_real_symbol_xyz")
    assert ids == []
    assert tier == "unresolved"


def test_find_symbol_id_wrapper_returns_only_ids(tier_project):
    """Backwards-compat: ``find_symbol_id`` returns the id list alone.

    Callers that don't need the tier (no current callers other than
    cmd_trace, which migrated to the tier-aware helper in W1249) keep the
    old single-return shape.
    """
    with open_db(readonly=True) as conn:
        ids = find_symbol_id(conn, "handle_payment_event")
    assert isinstance(ids, list)
    assert all(isinstance(x, int) for x in ids)
    assert len(ids) >= 1
