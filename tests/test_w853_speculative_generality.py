"""W853 - speculative-generality smell detector.

Catches production symbols whose every incoming ref comes from a test
file: a YAGNI marker for abstractions that exist only to be tested.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from roam.catalog.smells import (
    ALL_DETECTORS,
    detect_speculative_generality,
)


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        """
    )
    return conn


def _add_file(conn: sqlite3.Connection, path: str, role: str = "source") -> int:
    cur = conn.execute(
        "INSERT INTO files (path, language, file_role) VALUES (?, 'python', ?)",
        (path, role),
    )
    return cur.lastrowid


def _add_sym(
    conn: sqlite3.Connection,
    file_id: int,
    name: str,
    kind: str = "function",
    line_start: int = 1,
    line_end: int = 10,
) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, name, kind, line_start, line_end) VALUES (?, ?, ?, ?, ?)",
        (file_id, name, kind, line_start, line_end),
    )
    return cur.lastrowid


def _add_edge(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'call')",
        (source_id, target_id),
    )


# ---------------------------------------------------------------------------
# Positive case
# ---------------------------------------------------------------------------


def test_test_only_symbol_is_flagged(tmp_path: Path) -> None:
    """Production symbol with 3 test-file refs and 0 prod refs is flagged."""
    conn = _make_db(tmp_path)
    src_file = _add_file(conn, "src/widget.py", role="source")
    test_file_a = _add_file(conn, "tests/test_widget_a.py", role="test")
    test_file_b = _add_file(conn, "tests/test_widget_b.py", role="test")

    target = _add_sym(conn, src_file, "speculative_helper")
    test_caller_1 = _add_sym(conn, test_file_a, "test_a1", kind="function")
    test_caller_2 = _add_sym(conn, test_file_a, "test_a2", kind="function")
    test_caller_3 = _add_sym(conn, test_file_b, "test_b1", kind="function")

    _add_edge(conn, test_caller_1, target)
    _add_edge(conn, test_caller_2, target)
    _add_edge(conn, test_caller_3, target)
    conn.commit()

    findings = detect_speculative_generality(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["smell_id"] == "speculative-generality"
    assert f["symbol_name"] == "speculative_helper"
    assert f["severity"] == "info"
    assert f["metric_value"] == 3


# ---------------------------------------------------------------------------
# Negative: mixed prod + test refs
# ---------------------------------------------------------------------------


def test_mixed_refs_not_flagged(tmp_path: Path) -> None:
    """Symbol with 2 test refs + 1 prod ref is NOT flagged."""
    conn = _make_db(tmp_path)
    src_file = _add_file(conn, "src/widget.py", role="source")
    other_src = _add_file(conn, "src/consumer.py", role="source")
    test_file = _add_file(conn, "tests/test_widget.py", role="test")

    target = _add_sym(conn, src_file, "used_helper")
    prod_caller = _add_sym(conn, other_src, "real_user")
    test_caller_1 = _add_sym(conn, test_file, "test_a", kind="function")
    test_caller_2 = _add_sym(conn, test_file, "test_b", kind="function")

    _add_edge(conn, prod_caller, target)
    _add_edge(conn, test_caller_1, target)
    _add_edge(conn, test_caller_2, target)
    conn.commit()

    findings = detect_speculative_generality(conn)
    assert findings == []


# ---------------------------------------------------------------------------
# Negative: zero incoming edges (dead, not speculative)
# ---------------------------------------------------------------------------


def test_zero_incoming_not_flagged(tmp_path: Path) -> None:
    """Symbol with 0 incoming edges is NOT flagged (dead's domain)."""
    conn = _make_db(tmp_path)
    src_file = _add_file(conn, "src/widget.py", role="source")
    _add_sym(conn, src_file, "orphan_helper")
    conn.commit()

    findings = detect_speculative_generality(conn)
    assert findings == []


# ---------------------------------------------------------------------------
# Negative: symbol itself lives in a test file
# ---------------------------------------------------------------------------


def test_test_fixture_symbol_not_flagged(tmp_path: Path) -> None:
    """Test-fixture symbol called only by other tests is legitimate."""
    conn = _make_db(tmp_path)
    test_fix = _add_file(conn, "tests/conftest.py", role="test")
    test_use = _add_file(conn, "tests/test_thing.py", role="test")

    fixture = _add_sym(conn, test_fix, "make_widget", kind="function")
    caller_1 = _add_sym(conn, test_use, "test_one", kind="function")
    caller_2 = _add_sym(conn, test_use, "test_two", kind="function")

    _add_edge(conn, caller_1, fixture)
    _add_edge(conn, caller_2, fixture)
    conn.commit()

    findings = detect_speculative_generality(conn)
    assert findings == []


# ---------------------------------------------------------------------------
# Negative: single test ref is below the >= 2 threshold
# ---------------------------------------------------------------------------


def test_single_test_ref_below_threshold(tmp_path: Path) -> None:
    """Single test ref does not flag; we require >= 2 to be confident."""
    conn = _make_db(tmp_path)
    src_file = _add_file(conn, "src/widget.py", role="source")
    test_file = _add_file(conn, "tests/test_widget.py", role="test")

    target = _add_sym(conn, src_file, "barely_tested")
    caller = _add_sym(conn, test_file, "test_one", kind="function")

    _add_edge(conn, caller, target)
    conn.commit()

    findings = detect_speculative_generality(conn)
    assert findings == []


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_registered_in_all_detectors() -> None:
    """speculative-generality must appear in ALL_DETECTORS."""
    smell_ids = [smell_id for smell_id, _fn in ALL_DETECTORS]
    assert "speculative-generality" in smell_ids
