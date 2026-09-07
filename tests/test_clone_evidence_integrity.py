"""Malformed complete scans must not become full clone-review evidence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from roam.graph import clone_detect, clone_evidence


@pytest.fixture
def saved_scan(tmp_path, monkeypatch):
    from roam.db import connection

    monkeypatch.setattr(connection, "find_project_root", lambda: tmp_path)
    source = tmp_path / "app.py"
    source.write_bytes(b"def app():\n    return 1\n")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE files (id INTEGER, path TEXT, language TEXT, hash TEXT);"
        "CREATE TABLE index_manifest (id INTEGER, indexed_at TEXT);"
        "CREATE TABLE clone_scan_state (id INTEGER, metadata_json TEXT);"
        "CREATE TABLE clone_pairs (id INTEGER);"
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    conn.execute("INSERT INTO files VALUES (1, 'app.py', 'python', ?)", (digest,))
    scan = {
        "format_version": 1,
        "complete": True,
        "scope": None,
        "exclude_tests": False,
        "exclude_fixtures": False,
        "detector_version": clone_detect.CLONES_DETECTOR_VERSION,
        "index_identity": clone_evidence.index_identity(conn),
        "eligible_files": 1,
        "unavailable_files": 0,
        "eligible_functions": 1,
        "compared_functions": 1,
        "max_functions": 2000,
        "min_lines": 5,
        "min_similarity": 0.7,
        "sources": {"app.py": digest},
    }
    conn.execute("INSERT INTO clone_scan_state VALUES (1, ?)", (json.dumps(scan),))
    yield conn, scan, source
    conn.close()


@pytest.mark.parametrize(
    "mutation",
    [
        {"sources": {}, "eligible_files": 0},
        {"compared_functions": 0},
        {"unavailable_files": 1},
        {"eligible_functions": True},
        {"min_similarity": float("nan")},
        {"max_functions": 0},
    ],
    ids=["missing-source", "uncompared", "unavailable", "boolean-count", "nan-threshold", "invalid-cap"],
)
def test_inconsistent_complete_scan_is_partial(saved_scan, mutation):
    conn, scan, _source = saved_scan
    scan.update(mutation)
    conn.execute("UPDATE clone_scan_state SET metadata_json = ?", (json.dumps(scan),))
    assert clone_evidence.clone_check_status(conn).startswith("partial:")


def test_valid_scan_remains_usable(saved_scan):
    assert clone_evidence.clone_check_status(saved_scan[0]) == "ran"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"])
def test_malformed_scan_summary_is_still_strict_json(saved_scan, number):
    conn, scan, _source = saved_scan
    scan["min_similarity"] = "__bad_number__"
    encoded = json.dumps(scan).replace('"__bad_number__"', number)
    conn.execute("UPDATE clone_scan_state SET metadata_json = ?", (encoded,))
    json.dumps(clone_evidence.scan_summary(conn), allow_nan=False)


def test_zero_minimum_lines_retains_supported_scan_semantics(saved_scan):
    conn, scan, _source = saved_scan
    scan["min_lines"] = 0
    conn.execute("UPDATE clone_scan_state SET metadata_json = ?", (json.dumps(scan),))
    assert clone_evidence.clone_check_status(conn) == "ran"


def test_freshness_does_not_allocate_entire_source(saved_scan, monkeypatch):
    conn, _scan, _source = saved_scan

    def disallow_unbounded_read(self):
        pytest.fail("clone freshness must stream source bytes, not read the entire file")

    monkeypatch.setattr(Path, "read_bytes", disallow_unbounded_read)
    assert clone_evidence.clone_check_status(conn) == "ran"


@pytest.mark.parametrize("size", [0, 1, 262143, 262144, 262145, 2097152])
def test_streamed_digest_matches_whole_file_sha256(tmp_path, size):
    data = (bytes(range(256)) * ((size + 255) // 256))[:size]
    path = tmp_path / "source.py"
    path.write_bytes(data)
    assert clone_evidence._source_digest(path) == hashlib.sha256(data).hexdigest()


def test_non_regular_source_is_unavailable_without_open(saved_scan, monkeypatch):
    conn, _scan, _source = saved_scan
    from types import SimpleNamespace

    # Keep the guard's mock local: global os.open also belongs to pytest's
    # capture/fixture teardown machinery, which must remain usable.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_file", lambda self: False)
        patch.setattr(
            clone_evidence, "os", SimpleNamespace(open=lambda *args, **kwargs: pytest.fail("source was opened"))
        )
        assert clone_evidence.clone_check_status(conn) == "partial:clone_scan_freshness_unavailable"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO/open race control")
def test_fifo_replacement_after_type_check_cannot_block(tmp_path):
    fifo = tmp_path / "source.py"
    os.mkfifo(fifo)
    script = (
        "from pathlib import Path\nfrom roam.graph.clone_evidence import _source_digest\n"
        "Path.is_file = lambda self: True\n"
        "try:\n"
        f"    _source_digest(Path({str(fifo)!r}))\n"
        "except OSError:\n    pass\n"
        "else:\n    raise AssertionError('FIFO was accepted as a source')\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=10)
