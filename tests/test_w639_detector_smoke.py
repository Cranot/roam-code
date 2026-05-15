"""W639 cross-detector empty-corpus smoke: every detector returns [] on an empty
DB without import-time crashes.

Why this exists
---------------
W603 caught a regression where a concurrent W601/W602 merge dropped a
``Counter`` import inside ``src/roam/catalog/smells.py``. The bug only
surfaced when tests exercised the detector against a corpus that produced
real findings; on an empty corpus the buggy code never ran and the broken
import would have shipped silently.

This smoke test is the uniform guard: it parametrises across both detector
registries (the ``smells`` catalog + the algorithm-anti-pattern catalog) and
calls each detector against a freshly bootstrapped empty SQLite index. Any
import-time crash, NameError, missing-table reference, or non-list return
type fails fast — before the corpus-specific tests get a chance to run.

It does NOT replace the per-detector behavioural tests; it complements them
by sealing the "empty corpus" axis that those tests don't exercise (their
fixtures all have populated rows).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roam.catalog.detectors import _DETECTOR_REGISTRY
from roam.catalog.smells import ALL_DETECTORS
from roam.db.connection import ensure_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_db(tmp_path: Path) -> sqlite3.Connection:
    """Fresh SQLite connection with the canonical schema bootstrapped.

    Uses ``roam.db.connection.ensure_schema`` so the test follows the same
    bootstrap path the real indexer uses — any migration that adds a new
    table will automatically be exercised by this smoke without code edits
    here.
    """
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Detector enumeration
# ---------------------------------------------------------------------------

# Smells catalog: list of (smell_id, fn) tuples. Each detector takes (conn,).
_SMELLS_CASES = [(name, fn) for name, fn in ALL_DETECTORS]

# Algorithm anti-pattern catalog: dict keyed by fn.__name__ with metadata.
# Each entry's "function" callable also takes (conn,).
_ALGO_CASES = [(name, entry["function"]) for name, entry in _DETECTOR_REGISTRY.items()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("detector_name", "detector_fn"),
    _SMELLS_CASES,
    ids=[name for name, _ in _SMELLS_CASES],
)
def test_w639_smells_empty_corpus(
    detector_name: str,
    detector_fn,
    empty_db: sqlite3.Connection,
) -> None:
    """Every ``smells`` detector returns [] on an empty DB without crashing."""
    result = detector_fn(empty_db)
    assert isinstance(result, list), (
        f"{detector_name}: expected list, got {type(result).__name__}"
    )
    assert result == [], (
        f"{detector_name}: expected [] on empty corpus, got {len(result)} findings"
    )


@pytest.mark.parametrize(
    ("detector_name", "detector_fn"),
    _ALGO_CASES,
    ids=[name for name, _ in _ALGO_CASES],
)
def test_w639_algo_detectors_empty_corpus(
    detector_name: str,
    detector_fn,
    empty_db: sqlite3.Connection,
) -> None:
    """Every algorithm-catalog detector returns [] on an empty DB without crashing."""
    result = detector_fn(empty_db)
    assert isinstance(result, list), (
        f"{detector_name}: expected list, got {type(result).__name__}"
    )
    assert result == [], (
        f"{detector_name}: expected [] on empty corpus, got {len(result)} findings"
    )


def test_w639_smells_registry_count_floor() -> None:
    """Lock in the smells-registry count so future concurrent merges cannot
    silently drop a detector. Floor matches the W370/W601/W602/W605 ship list
    (20 smells as of W605). Bump this number when adding a new detector — the
    bump forces a deliberate edit through the test, surfacing drops in code
    review."""
    assert len(ALL_DETECTORS) >= 20, (
        f"smells registry shrank: got {len(ALL_DETECTORS)} detectors, "
        f"expected >= 20 (floor pinned by W639)"
    )


def test_w639_algo_registry_count_floor() -> None:
    """Lock in the algorithm-catalog registry count. Floor is conservative —
    the registry has 25+ ``@detector``-decorated functions; bump on additions."""
    assert len(_DETECTOR_REGISTRY) >= 25, (
        f"algorithm-catalog registry shrank: got {len(_DETECTOR_REGISTRY)} detectors, "
        f"expected >= 25 (floor pinned by W639)"
    )
