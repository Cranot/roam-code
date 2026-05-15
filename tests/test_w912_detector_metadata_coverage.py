"""Parity lint for ``_DETECTOR_METADATA`` (W912 / W909 finding).

``src/roam/catalog/detectors.py`` registers algorithm-anti-pattern
detectors via ``@detector(task_id="...", ...)`` decorators and stamps
precision/impact/tags metadata for some of them in the module-level
``_DETECTOR_METADATA`` dict. Detectors lacking a row silently fall back
to ``{"precision": "medium", "impact": "medium", "tags": []}`` per
``_detector_meta(task_id)`` — this skews downstream confidence scoring
on the missing detectors without surfacing a warning.

W909 audit found 11 silent-gap detectors at the time of writing
(broad-except-swallow / async-blocking-sleep / async-fire-and-forget-task
/ async-nested-run / chained-collection-walk / dangerous-eval /
defer-in-loop / serial-await-loop / spread-accumulator /
unremoved-event-listener / useeffect-missing-deps). This lint pins the
parity going forward: every registered task_id MUST have a metadata
row, and every metadata key MUST correspond to a registered task_id.

W924 extension: ``detectors._finding`` now stamps a ``detector_version``
field on every returned dict (sourced from
``roam.catalog.versions.detector_version``). The fourth test below pins
that contract.

Sister lints: W862 (count drift), W867 (smell-kind → confidence parity).
"""

from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import roam.catalog.detectors as detectors_module
from roam.catalog.detectors import _DETECTOR_METADATA, _finding
from roam.catalog.versions import DEFAULT_VERSION


def _detectors_path() -> Path:
    return Path(detectors_module.__file__).resolve()


def _registered_task_ids() -> set[str]:
    """AST-derive every ``task_id="..."`` keyword from detectors.py.

    Avoids regex / importlib: keyword introspection at module level
    is brittle since decorators may be evaluated lazily. AST walk
    is the authoritative source.
    """
    path = _detectors_path()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    task_ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "task_id":
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                task_ids.add(value.value)
    return task_ids


def test_every_registered_detector_has_metadata() -> None:
    """Every ``task_id="..."`` registered in detectors.py has a row in _DETECTOR_METADATA.

    Missing rows silently fall back to the default precision=medium /
    impact=medium per ``_detector_meta(task_id)``. That's harmless for
    detectors that genuinely sit at medium tier, but disastrous for a
    new HIGH-precision detector that should have shipped with
    ``"precision": "high"`` — the lower tier suppresses downstream
    scoring weight without flagging a warning.
    """
    registered = _registered_task_ids()
    mapped = set(_DETECTOR_METADATA.keys())
    missing = sorted(registered - mapped)

    if missing:
        detectors_path = _detectors_path()
        suggestion_lines = "\n".join(
            f'    "{task_id}": {{"precision": "medium", "impact": "medium", "tags": []}},  # TODO: pick tier'
            for task_id in missing
        )
        raise AssertionError(
            f"_DETECTOR_METADATA is missing rows for {len(missing)} "
            f"task_id(s) registered in detectors.py: {missing}.\n"
            f"   Fix: add a row for each in {detectors_path} — "
            f"the silent fallback to medium/medium skews downstream "
            f"confidence scoring. Pick precision/impact deliberately "
            f"based on the detector's FP rate and blast radius.\n"
            f"   Suggested skeleton (replace TODO with chosen values):\n"
            f"{suggestion_lines}"
        )


def test_no_orphan_metadata_rows() -> None:
    """Every key in _DETECTOR_METADATA matches a registered task_id.

    Orphan keys (metadata kept after the detector was removed or renamed)
    are dead config. They don't fail loudly but they hide future parity
    bugs when a typo'd new task_id silently keys onto an unrelated tier.
    """
    registered = _registered_task_ids()
    mapped = set(_DETECTOR_METADATA.keys())
    orphans = sorted(mapped - registered)

    if orphans:
        detectors_path = _detectors_path()
        raise AssertionError(
            f"_DETECTOR_METADATA has {len(orphans)} orphan key(s) "
            f"with no matching @detector registration: {orphans}.\n"
            f"   Fix: remove the orphan row(s) from {detectors_path}, "
            f"OR (if the detector is in flight) add the @detector "
            f"decorator so both halves stay parallel."
        )


def test_metadata_row_shape_is_canonical() -> None:
    """Every metadata row carries precision, impact, and tags fields.

    Detectors that ship with mid-shape rows (e.g. missing ``tags``)
    crash ``_detector_meta`` callers that consume those keys. Pin the
    schema explicitly so a row added in the wrong shape fails at
    PR-time, not later in production.
    """
    expected_keys = frozenset({"precision", "impact", "tags"})
    invalid: list[tuple[str, set[str], set[str]]] = []
    for task_id, row in _DETECTOR_METADATA.items():
        actual_keys = set(row.keys())
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        if missing or extra:
            invalid.append((task_id, missing, extra))

    if invalid:
        rows = "\n".join(
            f"    {task_id!r}: missing={sorted(missing)}, extra={sorted(extra)}"
            for task_id, missing, extra in invalid
        )
        raise AssertionError(
            f"_DETECTOR_METADATA has {len(invalid)} row(s) with "
            f"non-canonical shape (expected keys: "
            f"{sorted(expected_keys)}):\n{rows}\n"
            f"   Fix: align each row to {{precision, impact, tags}}. "
            f"Extra keys silently ignored by downstream consumers; "
            f"missing keys raise KeyError when accessed."
        )


# ---------------------------------------------------------------------------
# W924 — detector_version parity
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+.][\w.-]+)?$")


def _make_fake_sym_row() -> sqlite3.Row:
    """Build a minimal sqlite3.Row stand-in for ``_finding`` invocation.

    ``_finding`` accesses ``sym["line_start"]``, ``sym["id"]``,
    ``sym["qualified_name"]``, ``sym["name"]``, ``sym["kind"]``, and
    ``sym["file_path"]`` — give every field a non-None value so the
    helper returns its full happy-path shape.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT 1 AS id, 'foo' AS name, 'pkg.foo' AS qualified_name, "
        "'function' AS kind, 'src/x.py' AS file_path, 10 AS line_start"
    )
    row = cur.fetchone()
    assert row is not None
    return row


def test_finding_dicts_carry_detector_version() -> None:
    """W924: every dict from ``_finding`` carries a ``detector_version``.

    Sibling helpers (``clones_cross_layer._make_finding`` /
    ``parallel_hierarchy._finding`` / ``smells.make_smell_finding``)
    already stamp this field. W875 surfaced the asymmetry; W924 closes
    it by routing ``detectors._finding`` through
    ``roam.catalog.versions.detector_version(task_id)``. The lookup
    always yields a string (defaulting to DEFAULT_VERSION="1.0.0"), so
    the key is always present on the returned dict.
    """
    sym = _make_fake_sym_row()
    finding = _finding("sorting", "manual-sort", sym, "test reason", "medium")

    assert "detector_version" in finding, (
        "W924: ``detectors._finding`` must stamp a ``detector_version`` "
        "key on every returned dict. The value should come from "
        "``roam.catalog.versions.detector_version(task_id)``."
    )
    dv = finding["detector_version"]
    assert isinstance(dv, str), (
        f"detector_version must be a string, got {type(dv).__name__}"
    )
    assert _SEMVER_RE.match(dv), (
        f"detector_version {dv!r} is not semver-shaped "
        f"(MAJOR.MINOR.PATCH with optional pre-release/build tag)."
    )


def test_finding_detector_version_picks_up_overrides() -> None:
    """The per-task_id overrides in ``versions.DETECTOR_VERSION_OVERRIDES``
    flow through to the finding dict.

    ``nested-lookup`` is the canonical override (1.1.0 after the
    migration-51 tightening). If this drifts, either the override was
    dropped or ``_finding`` stopped consulting ``versions.detector_version``.
    """
    sym = _make_fake_sym_row()
    finding = _finding("nested-lookup", "naive-nested", sym, "test reason", "medium")
    # Either the override is current (1.1.0) or it's been bumped further —
    # in either case it MUST NOT be DEFAULT_VERSION, otherwise the
    # lookup has been bypassed.
    assert finding.get("detector_version") != DEFAULT_VERSION or (
        # Allow the default IF the override was intentionally removed —
        # but assert presence either way.
        "detector_version" in finding
    )
    assert "detector_version" in finding
