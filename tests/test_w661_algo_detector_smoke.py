"""W661: catalog/detectors.py production loop must fail-loud on programmer
bugs (NameError / ImportError / AttributeError / TypeError) rather than swallow
them into the ``failed_detectors`` meta bucket.

Why this exists
---------------
W653 fixed the bare-except in ``smells.run_all_detectors``. W639's smoke test
already guards the 34 ALGO detectors in ``catalog/detectors.py::_DETECTOR_REGISTRY``
against import-time crashes on an empty corpus, but the production
``run_detectors`` orchestration loop had the same bug class as the pre-W653
smells code: a blanket ``except Exception: continue`` that buried programmer
errors in ``failed_detectors[].error`` strings inside the ``meta`` envelope.

W661 mirrors the W653 template at the algo-detector production loop:

* Programmer-class (NameError, ImportError, AttributeError, TypeError) →
  re-raise as RuntimeError so the W531 fail-loud discipline + CLAUDE.md
  Pattern-2 (always-emit, never silent fallback) hold at runtime.
* Data-class (sqlite3.Error) → log.warning + record in ``failed_detectors``
  + continue (preserves existing meta contract).
* Other exceptions → record in ``failed_detectors`` + continue (legacy
  behaviour preserved for OS-class / third-party plugin issues).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from roam.catalog import detectors as detectors_mod
from roam.db.connection import ensure_schema


@pytest.fixture
def empty_db(tmp_path: Path):
    """Fresh SQLite with the canonical schema. Mirrors the W639 fixture."""
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _patch_iter_registered(monkeypatch, cases):
    """Replace ``_iter_registered_detectors`` with a hermetic generator.

    ``cases`` is a list of ``(task_id, way_id, detect_fn)`` tuples. The
    production loop iterates ``_iter_registered_detectors()`` so we patch it
    instead of mutating ``_DETECTOR_REGISTRY`` (which would also flow through
    the plugin substrate + python-idioms branches we don't want to invoke).
    """

    def _fake_iter():
        yield from cases

    monkeypatch.setattr(detectors_mod, "_iter_registered_detectors", _fake_iter)


class TestW661AlgoDetectorFailLoud:
    """Programmer-error exceptions in algo detectors propagate out of
    ``run_detectors``. sqlite errors still continue + populate the
    ``failed_detectors`` meta."""

    def test_name_error_propagates(self, empty_db, monkeypatch):
        """A detector that raises NameError (e.g. missing import) must NOT
        be swallowed into ``failed_detectors``. This is the W601/W602
        Counter-import regression class — must fail-loud at runtime."""

        def _bad_detector(conn):
            raise NameError("name 'Counter' is not defined")

        _patch_iter_registered(monkeypatch, [("bad-task", "bad-way", _bad_detector)])

        with pytest.raises(RuntimeError, match="_bad_detector|NameError"):
            detectors_mod.run_detectors(empty_db)

    def test_import_error_propagates(self, empty_db, monkeypatch):
        """ImportError from a detector must also fail-loud."""

        def _bad_detector(conn):
            raise ImportError("cannot import name 'missing_helper'")

        _patch_iter_registered(monkeypatch, [("bad-task", "bad-way", _bad_detector)])

        with pytest.raises(RuntimeError, match="ImportError"):
            detectors_mod.run_detectors(empty_db)

    def test_attribute_error_propagates(self, empty_db, monkeypatch):
        """AttributeError (typical for signature drift / missing attr) must
        fail-loud."""

        def _bad_detector(conn):
            raise AttributeError("'NoneType' object has no attribute 'execute'")

        _patch_iter_registered(monkeypatch, [("bad-task", "bad-way", _bad_detector)])

        with pytest.raises(RuntimeError, match="AttributeError"):
            detectors_mod.run_detectors(empty_db)

    def test_type_error_propagates(self, empty_db, monkeypatch):
        """TypeError (typical for wrong-arity calls after signature drift)
        must fail-loud."""

        def _bad_detector(conn):
            raise TypeError("detect_fn() takes 2 positional arguments but 1 was given")

        _patch_iter_registered(monkeypatch, [("bad-task", "bad-way", _bad_detector)])

        with pytest.raises(RuntimeError, match="TypeError"):
            detectors_mod.run_detectors(empty_db)

    def test_runtime_error_includes_task_id_and_detector_name(self, empty_db, monkeypatch):
        """The fail-loud RuntimeError must name the offending detector AND
        its task_id so operators can triage the regression without re-reading
        a stack trace."""

        def _bad_detector(conn):
            raise NameError("name 'Counter' is not defined")

        _patch_iter_registered(monkeypatch, [("loop-allocation", "naive-prepend", _bad_detector)])

        with pytest.raises(RuntimeError) as exc_info:
            detectors_mod.run_detectors(empty_db)

        msg = str(exc_info.value)
        assert "_bad_detector" in msg, f"expected detector name in error; got: {msg}"
        assert "loop-allocation" in msg, f"expected task_id in error; got: {msg}"

    def test_sqlite_error_continues(self, empty_db, monkeypatch, caplog):
        """A per-detector sqlite3 error is a data/query issue, not a
        programmer bug — it should be logged + recorded in failed_detectors
        and other detectors should still run."""

        def _bad_detector(conn):
            raise sqlite3.OperationalError("no such table: ghost_table")

        def _good_detector(conn):
            # Real detector findings carry detected_way / suggested_way /
            # reason — synthesise the same shape so the enrichment pass
            # (_build_evidence_path) doesn't crash on missing keys.
            return [
                {
                    "task_id": "good-task",
                    "symbol_id": None,
                    "confidence": "low",
                    "reason": "synthetic finding for W661 sibling-survives test",
                    "detected_way": "naive",
                    "suggested_way": "better",
                }
            ]

        _patch_iter_registered(
            monkeypatch,
            [
                ("bad-task", "bad-way", _bad_detector),
                ("good-task", "good-way", _good_detector),
            ],
        )

        with caplog.at_level(logging.WARNING, logger="roam.catalog.detectors"):
            findings, meta = detectors_mod.run_detectors(empty_db, return_meta=True)

        # Good detector still ran and produced its finding.
        assert any(f.get("task_id") == "good-task" for f in findings), (
            f"expected good-task finding to survive sqlite-error in sibling; got: {findings}"
        )

        # Bad detector was recorded in failed_detectors.
        assert meta["detectors_failed"] == 1
        assert any(fd["detector"] == "_bad_detector" for fd in meta["failed_detectors"]), (
            f"expected failure record for _bad_detector; got: {meta['failed_detectors']}"
        )

        # Warning was logged for the bad detector.
        assert any("sqlite error" in rec.message and "_bad_detector" in rec.message for rec in caplog.records), (
            f"expected sqlite-warning log; got: {[r.message for r in caplog.records]}"
        )

    def test_other_exception_still_buckets(self, empty_db, monkeypatch):
        """Non-programmer, non-sqlite exceptions (e.g. OSError from a
        plugin) preserve the legacy behaviour — record in
        ``failed_detectors`` and continue rather than crash the run."""

        def _bad_detector(conn):
            raise OSError("disk read failed on plugin cache")

        def _good_detector(conn):
            return []

        _patch_iter_registered(
            monkeypatch,
            [
                ("bad-task", "bad-way", _bad_detector),
                ("good-task", "good-way", _good_detector),
            ],
        )

        findings, meta = detectors_mod.run_detectors(empty_db, return_meta=True)
        assert meta["detectors_failed"] == 1
        assert any(fd["detector"] == "_bad_detector" for fd in meta["failed_detectors"])

    def test_clean_run_unchanged(self, empty_db, monkeypatch):
        """Sanity: a clean detector that returns [] still produces an
        envelope with zero failures. The W661 fail-loud refactor must not
        regress the empty-corpus contract."""

        def _good_detector(conn):
            return []

        _patch_iter_registered(monkeypatch, [("good-task", "good-way", _good_detector)])

        findings, meta = detectors_mod.run_detectors(empty_db, return_meta=True)
        assert findings == []
        assert meta["detectors_failed"] == 0
        assert meta["detectors_executed"] == 1
