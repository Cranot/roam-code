"""Per-detector precision audit.

For each labelled fixture directory under ``tests/fixtures/detector_eval/``,
indexes the fixture files in isolation, runs the named detector against
that index, and compares the firing line numbers against the
``expected.json`` ground truth. Computes precision and recall per
detector and asserts both are 1.0.

Adding a new labelled detector requires only:

1. ``tests/fixtures/detector_eval/<slug>/`` with ``*.py`` cases and an
   ``expected.json`` describing what each file's findings should be.
2. A ``(slug, detector_function)`` entry in ``_DETECTORS`` below.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from roam.catalog import python_idioms
from roam.db.connection import open_db
from roam.index.indexer import Indexer

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "detector_eval"

_DETECTORS = {
    "django_n1": python_idioms.detect_django_n1,
    "sqlalchemy_lazy": python_idioms.detect_sqlalchemy_lazy,
    "fastapi_depends": python_idioms.detect_fastapi_depends,
    "flask": python_idioms.detect_flask_debug_true,
}

# Per-detector floor for precision and recall. Recall is high because
# the detectors are tuned conservatively (high-confidence patterns).
# Precision baselines reflect known false-positive classes documented
# in docs/site/language-precision.md (e.g. django N+1 currently fires
# even when ``.select_related(...)`` defuses the query). Raise these
# floors as the detectors improve; lowering one is a regression and
# requires intent.
_THRESHOLDS = {
    "django_n1": {"precision": 1.0, "recall": 1.0},
    "sqlalchemy_lazy": {"precision": 1.0, "recall": 1.0},
    "fastapi_depends": {"precision": 1.0, "recall": 1.0},
    "flask": {"precision": 1.0, "recall": 1.0},
}


def _index_fixture_dir(work_dir: Path) -> None:
    """Stage the fixture dir as a tiny git project and index it."""
    subprocess.run(["git", "init", "-q"], cwd=work_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=work_dir,
        check=True,
    )
    Indexer().run(quiet=True)


def _run_detector_against(slug: str, detector_fn, tmp_path: Path, monkeypatch):
    """Copy the labelled fixtures into ``tmp_path``, index, run the
    detector, and group findings by file."""
    import shutil

    from roam.catalog.python_idioms import _clear_file_text_cache

    # The detector pipeline caches file text by (id(conn), file_id).
    # Across tests, Python may reuse the same object id for a fresh
    # connection, returning stale text from the previous test's
    # tmp_path. Clear before every run.
    _clear_file_text_cache()

    src = FIXTURE_ROOT / slug
    if not (src / "expected.json").exists():
        pytest.skip(f"no expected.json for {slug}")

    for f in src.rglob("*"):
        if f.suffix == ".py" or f.name == "expected.json":
            destination = tmp_path / f.relative_to(src)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, destination)

    monkeypatch.chdir(tmp_path)
    _index_fixture_dir(tmp_path)

    with open_db(readonly=False) as conn:
        findings = detector_fn(conn)

    by_file: dict[str, set[int]] = {}
    for f in findings:
        # Findings carry ``location`` as ``"<path>:<line>"``.
        loc = f.get("location", "")
        if ":" not in loc:
            continue
        path_str, line_str = loc.rsplit(":", 1)
        try:
            line_no = int(line_str)
        except ValueError:
            continue
        by_file.setdefault(Path(path_str).as_posix(), set()).add(line_no)
    return by_file


def _precision_recall(actual: dict[str, set[int]], expected: dict) -> tuple[float, float, dict]:
    """Compare detector output against ground truth.

    A finding line is a true positive if it falls within ±2 lines of an
    expected line (handles small line-number drift between heuristic and
    decorated/closing lines). False positives are findings on files
    where no firing was expected. False negatives are expected lines
    with no matching finding.
    """
    tp = fp = fn = 0
    misses_by_file: dict[str, list[int]] = {}
    spurious_by_file: dict[str, list[int]] = {}

    for entry in expected["fixtures"]:
        fname = entry["file"]
        want_lines = set(entry["should_fire_lines"])
        got_lines = actual.get(fname, set())

        # Tolerance window — heuristics can fire on the line *containing*
        # the trigger, the function def line, or anywhere inside ±2.
        for w in want_lines:
            matched = any(abs(g - w) <= 2 for g in got_lines)
            if matched:
                tp += 1
            else:
                fn += 1
                misses_by_file.setdefault(fname, []).append(w)

        for g in got_lines:
            if not any(abs(g - w) <= 2 for w in want_lines):
                fp += 1
                spurious_by_file.setdefault(fname, []).append(g)

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall, {"misses": misses_by_file, "spurious": spurious_by_file}


@pytest.mark.parametrize("slug,detector_fn", sorted(_DETECTORS.items()))
def test_detector_precision_recall(slug, detector_fn, tmp_path, monkeypatch):
    expected_path = FIXTURE_ROOT / slug / "expected.json"
    if not expected_path.exists():
        pytest.skip(f"no expected.json for {slug}")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    actual = _run_detector_against(slug, detector_fn, tmp_path, monkeypatch)
    precision, recall, diag = _precision_recall(actual, expected)

    floor = _THRESHOLDS.get(slug, {"precision": 1.0, "recall": 1.0})
    # Report headline numbers in any failure so a CI log tells the
    # maintainer exactly what regressed.
    diag_str = (
        f"{slug}: precision={precision:.2f} (floor {floor['precision']:.2f}), "
        f"recall={recall:.2f} (floor {floor['recall']:.2f})\n"
        f"  misses (expected, none reported):  {diag['misses']}\n"
        f"  spurious (reported, not expected): {diag['spurious']}"
    )
    assert precision >= floor["precision"], diag_str
    assert recall >= floor["recall"], diag_str


def test_flask_debug_true_suppresses_test_contexts_without_recall_loss(tmp_path, monkeypatch):
    actual = _run_detector_against("flask", python_idioms.detect_flask_debug_true, tmp_path, monkeypatch)

    assert actual == {
        "src/guard_flask_app.py": {8},
        "tp_flask_app.py": {29},
    }
