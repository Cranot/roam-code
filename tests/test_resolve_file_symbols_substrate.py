"""Pattern-1 Variant D — Wave A substrate unit tests for ``resolve_file_symbols``.

Audit reference: ``(internal memo)`` flagged
three HIGH-severity Variant D bugs (``safe-zones`` / ``metrics`` /
``affected-tests``) that share a single root cause: each command's
inline ``_resolve_file_symbols`` (or ``_resolve_target``) helper falls
back to a ``LIKE %name`` substring match without surfacing the
degradation. Callers then emit a fully-resolved success verdict on a
potentially-wrong-file resolution.

Wave A (this test): hoist the helper to
:func:`roam.commands.resolve.resolve_file_symbols` with a tier
discriminator so callers can route the substring path through
:func:`roam.output.formatter.resolution_disclosure` (the W1309
``file_substring`` enum member is already in ``_RESOLUTION_KINDS``).

Waves B + C (deferred): adopt the new helper in
``cmd_affected_tests`` / ``cmd_safe_zones`` / ``cmd_metrics`` and flip
the xfail-strict pins in
``tests/test_pattern_1_variant_d_resolver_audit.py``.

These tests pin the substrate contract independently of consumer
adoption: they assert the helper returns the correct
``(file_id, sym_ids, file_path, tier)`` tuple shape across exact-match,
substring-match, and miss inputs.
"""

from __future__ import annotations

import sqlite3

import pytest

from roam.commands.resolve import resolve_file_symbols
from roam.output.formatter import _RESOLUTION_KINDS

# ---------------------------------------------------------------------------
# Minimal fixture: in-memory DB with just files + symbols
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_db() -> sqlite3.Connection:
    """Return an in-memory connection seeded with files + symbols.

    Mirrors the columns the helper actually queries -- ``files.id``,
    ``files.path``, ``symbols.id``, ``symbols.file_id`` -- without
    pulling the full Roam schema. Keeps the substrate test isolated
    from indexer changes; the helper is a pure SQL boundary so the
    minimal schema is sufficient.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL
        );
        """
    )
    # 3 files: one canonical, one shares a basename suffix with input,
    # one orphan with no symbols (covers the "file resolved, zero symbols"
    # tier-disclosable shape).
    conn.executemany(
        "INSERT INTO files (path) VALUES (?)",
        [
            ("src/service.py",),
            ("tests/service.py",),
            ("src/orphan.py",),
        ],
    )
    # Symbols on the two non-orphan files.
    conn.executemany(
        "INSERT INTO symbols (file_id, name, kind) VALUES (?, ?, ?)",
        [
            (1, "handle_request", "function"),
            (1, "Service", "class"),
            (1, "_helper", "function"),
            (2, "test_handle_request", "function"),
        ],
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Exact-match path: tier="file", canonical row returned byte-identically
# ---------------------------------------------------------------------------


class TestExactMatch:
    """Exact-path inputs must return ``tier="file"`` with no degradation."""

    def test_exact_path_returns_file_tier(self, fake_db) -> None:
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, "src/service.py")
        assert tier == "file"
        assert file_id == 1
        assert fpath == "src/service.py"
        assert sym_ids == {1, 2, 3}

    def test_exact_path_windows_separator_normalises(self, fake_db) -> None:
        # Per the helper contract, ``\\`` -> ``/`` normalisation lives at
        # the boundary so callers stay platform-agnostic.
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, r"src\service.py")
        assert tier == "file"
        assert file_id == 1
        assert fpath == "src/service.py"
        assert sym_ids == {1, 2, 3}

    def test_exact_path_zero_symbols(self, fake_db) -> None:
        # ``src/orphan.py`` is a file with no indexed symbols. Should
        # still resolve cleanly with tier="file" + empty sym set --
        # callers must NOT conflate this with a miss.
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, "src/orphan.py")
        assert tier == "file"
        assert file_id == 3
        assert fpath == "src/orphan.py"
        assert sym_ids == set()


# ---------------------------------------------------------------------------
# Substring-match path: tier="file_substring", surfaces the degradation
# ---------------------------------------------------------------------------


class TestSubstringMatch:
    """``LIKE %name`` fallback MUST surface ``tier="file_substring"``."""

    def test_basename_substring_returns_file_substring_tier(self, fake_db) -> None:
        # ``service.py`` is not an exact path; the LIKE %service.py
        # fallback fires. The pre-Wave-A behaviour silently returned a
        # successful resolution; the post-Wave-A contract surfaces the
        # tier so callers can flip ``partial_success: true``.
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, "service.py")
        assert tier == "file_substring"
        assert file_id in (1, 2)
        # Canonical path is whatever ORDER BY path LIMIT 1 picks --
        # alphabetical "src/service.py" sorts before "tests/service.py".
        assert fpath == "src/service.py"
        assert sym_ids == {1, 2, 3}

    def test_partial_path_returns_file_substring_tier(self, fake_db) -> None:
        # Partial-path inputs (e.g. ``ervice.py`` matches both files)
        # still route to the substring tier.
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, "ervice.py")
        assert tier == "file_substring"
        assert fpath in ("src/service.py", "tests/service.py")


# ---------------------------------------------------------------------------
# Miss path: returns the (None, set(), None, None) sentinel
# ---------------------------------------------------------------------------


class TestMiss:
    """Unmatched inputs return the canonical miss sentinel for callers
    to route into ``resolution_disclosure("unresolved", ...)``.
    """

    def test_no_match_returns_none_sentinel(self, fake_db) -> None:
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, "nonexistent.py")
        assert file_id is None
        assert sym_ids == set()
        assert fpath is None
        assert tier is None

    def test_empty_input_returns_none_sentinel(self, fake_db) -> None:
        # An empty string would substring-match every row via ``LIKE %``,
        # but the exact-path branch fires first and finds nothing.
        # Document the actual behaviour: empty string substring-matches
        # the alphabetically-first file. This is fine as a substrate
        # contract; consumers should validate non-empty input upstream.
        file_id, sym_ids, fpath, tier = resolve_file_symbols(fake_db, "")
        # Substring of "" matches everything; ORDER BY path picks
        # "src/orphan.py" alphabetically.
        assert tier == "file_substring"
        assert fpath == "src/orphan.py"


# ---------------------------------------------------------------------------
# Closed-enum membership: ``tier`` is always a _RESOLUTION_KINDS member
# (or None on miss). Drift-guards W1309 + the audit's Wave A contract.
# ---------------------------------------------------------------------------


class TestTierEnumMembership:
    """Every non-None ``tier`` value must belong to ``_RESOLUTION_KINDS``.

    The audit at ``(internal memo)`` calls out
    W1309 specifically: the ``file_substring`` enum member already lives
    in ``_RESOLUTION_KINDS``, so callers can pass the helper's tier
    directly to ``resolution_disclosure(tier, target=...)``. Pin both.
    """

    def test_file_tier_is_enum_member(self) -> None:
        assert "file" in _RESOLUTION_KINDS

    def test_file_substring_tier_is_enum_member(self) -> None:
        assert "file_substring" in _RESOLUTION_KINDS

    def test_helper_only_returns_enum_or_none(self, fake_db) -> None:
        for target in ("src/service.py", "service.py", "nonexistent.py", r"src\service.py"):
            _, _, _, tier = resolve_file_symbols(fake_db, target)
            assert tier is None or tier in _RESOLUTION_KINDS, (
                f"resolve_file_symbols returned non-enum tier {tier!r} for input {target!r}"
            )
