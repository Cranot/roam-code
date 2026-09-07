"""Completion and freshness of persisted clone scans, not correctness assurance."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import stat
from pathlib import Path


def _complete_scan_is_consistent(scan: dict) -> bool:
    """A completion bit cannot override missing counts or contradictory bounds."""
    counters = (
        "eligible_files",
        "unavailable_files",
        "eligible_functions",
        "compared_functions",
        "max_functions",
        "min_lines",
    )
    if any(type(scan.get(key)) is not int or scan[key] < 0 for key in counters):
        return False
    threshold = scan.get("min_similarity")
    return (
        scan["unavailable_files"] == 0
        and scan["compared_functions"] == scan["eligible_functions"] <= scan["max_functions"]
        and scan["max_functions"] > 0
        and type(threshold) in (int, float)
        and 0 <= threshold <= 1
        and scan.get("scope") is None
        and scan.get("exclude_tests") is False
        and scan.get("exclude_fixtures") is False
    )


def _reject_nonfinite(value: str):
    raise ValueError("clone scan contains a non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_nonfinite(value)
    return number


def _load_scan(value: str):
    return json.loads(value, parse_constant=_reject_nonfinite, parse_float=_finite_float)


def _source_digest(path: Path) -> str:
    """Stream regular source files with bounded allocation and no FIFO wait.

    NONBLOCK protects POSIX open if a source is replaced by a FIFO. Inspect the
    opened descriptor too, so a pre-open file-type race cannot enter the read.
    This is not a bound on network-filesystem latency or an atomic tree snapshot.
    """
    if not path.is_file():
        raise OSError("clone source is not a regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("clone source is not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 256 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def index_identity(conn: sqlite3.Connection) -> str:
    """Bind the scan to the indexed file set and its latest generation."""
    files = [tuple(row) for row in conn.execute("SELECT path, language, hash FROM files ORDER BY path")]
    row = conn.execute("SELECT id, indexed_at FROM index_manifest ORDER BY id DESC LIMIT 1").fetchone()
    payload = [files, tuple(row) if row else None]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def clone_check_status(conn: sqlite3.Connection, *, project_root: Path | None = None) -> str:
    """Qualify a review while retaining useful positive findings from older scans.

    Freshness covers indexed files in the detector's eligible languages. It
    does not assert that every source file or every kind of duplicate was found.
    """
    from roam.db.connection import find_project_root
    from roam.graph.clone_detect import CLONES_DETECTOR_VERSION, _fetch_candidate_files

    try:
        row = conn.execute("SELECT metadata_json FROM clone_scan_state WHERE id = 1").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        row = None
    if row is None:
        if conn.execute("SELECT 1 FROM clone_pairs LIMIT 1").fetchone():
            return "partial:clone_scan_metadata_missing (run `roam clones --persist`)"
        return "skipped:no_clone_pairs (run `roam clones --persist`)"
    try:
        scan = _load_scan(row[0])
        if not isinstance(scan, dict) or type(scan.get("format_version")) is not int or scan["format_version"] != 1:
            return "partial:clone_scan_metadata_invalid"
        if scan.get("complete") is not True:
            return "partial:clone_scan_incomplete"
        if scan.get("scope") or scan.get("exclude_tests") or scan.get("exclude_fixtures"):
            return "partial:clone_scan_filtered"
        if not _complete_scan_is_consistent(scan):
            return "partial:clone_scan_metadata_invalid"
        if scan.get("detector_version") != CLONES_DETECTOR_VERSION:
            return "partial:clone_scan_detector_changed"
        if scan.get("index_identity") != index_identity(conn):
            return "partial:clone_scan_stale_index"
        sources = scan.get("sources")
        if not isinstance(sources, dict) or len(sources) != scan.get("eligible_files"):
            return "partial:clone_scan_metadata_invalid"
        if set(sources) != {row["path"] for row in _fetch_candidate_files(conn, None)}:
            return "partial:clone_scan_metadata_invalid"
        root = (project_root if project_root is not None else find_project_root()).resolve()
        for name, digest in sources.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                return "partial:clone_scan_source_outside_project"
            if _source_digest(path) != digest:
                return "partial:clone_scan_stale_sources"
    except (ValueError, TypeError, OSError, RuntimeError):
        return "partial:clone_scan_freshness_unavailable"
    return "ran"


def scan_summary(conn: sqlite3.Connection) -> dict:
    """Expose the saved scan's bounds without its potentially large hash map."""
    try:
        row = conn.execute("SELECT metadata_json FROM clone_scan_state WHERE id = 1").fetchone()
        scan = _load_scan(row[0]) if row else {}
        if not isinstance(scan, dict):
            return {"state": "metadata_invalid"}
        return {k: v for k, v in scan.items() if k != "sources"}
    except (sqlite3.Error, ValueError, TypeError):
        return {"state": "metadata_unavailable"}
