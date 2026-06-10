"""Intra-catalog shared helpers (W864, W873, W877, W923).

This module is INTERNAL to ``roam.catalog`` — the leading underscore on
the module name conveys that it is not part of any public surface.

The detectors under ``roam.catalog`` historically duplicated a handful
of tiny helpers (``_loc``, ``_find_workspace_root``, ``_is_test_path``,
``_enclosing_symbol``, ``_finding``) across multiple files. Three
identical definitions is itself a W95-style clone (the very smell
catalog this codebase ships detects for). Hoist canonical copies here;
the per-file aliases now resolve via import rather than re-definition.

``loc`` / ``find_workspace_root`` behavior is byte-identical to the
prior in-file definitions in ``smells.py`` — pure relocation.

``is_test_path`` (W873 → W898) is now a thin delegate to the canonical
``roam.commands.changed_files.is_test_file`` — the long arc of
W873/W886/W889/W891/W893 patches consolidated every per-language edge
case onto the commands-layer detector (which itself delegates to
``roam.index.test_conventions.is_test_file``). Pre-W898 the catalog
re-implemented the same heuristics in parallel; W898 collapses both
onto one source of truth. The lazy import inside the body keeps
``catalog._shared`` import-cycle-free at module load (no cycle exists
today — verified per the CLAUDE.md "Verify the cycle before hedging"
rule W907 — but lazy import avoids accidentally introducing one if
``commands.changed_files`` ever needs to reach back into the catalog).

``enclosing_symbol`` (W877) is the defensive superset of the two
historical variants: it preserves ``type_switch._enclosing_symbol``'s
``sqlite3.OperationalError`` fallback (returns the ``"<module>"``
sentinel rather than propagating) while keeping the SQL + return
shape ``smells._enclosing_symbol`` relied on. Pure relocation for
``type_switch``; ``smells.py`` keeps its local non-defensive def for
now (4 call-sites, behavior-change risk if we silently swallow
``OperationalError`` in smell-detector hot paths). Note: the
unrelated ``python_idioms._enclosing_symbol`` has a different
signature (``line_no, sym_index``) — NOT a clone, NOT hoisted.

The module exports its 5 public helpers via an explicit ``__all__`` —
matches the ``index_embeddings.py`` discipline so static-analysis tools
and ``from roam.catalog._shared import *`` consumers see exactly the
intended public surface (W1033, mirroring the W886 / W923 narrative).

``make_smell_finding`` (W923) is the canonical structural-smell
envelope builder. Historical state: four sites built the same
8-key dict literal — ``smells._finding`` (24 call-sites, exactly
the 8-key shape), ``type_switch._finding`` (1 call-site, exactly
the 8-key shape and self-confessed as a mirror in its docstring),
``parallel_hierarchy._finding`` (1 call-site, detector-specific
wrapper that hardcodes smell_id/severity/kind and stamps
evidence/confidence/detector_version), and
``clones_cross_layer._make_finding`` (1 call-site, detector-specific
wrapper with kwargs-only signature that hardcodes
smell_id/severity/kind/confidence and composes description
internally). The canonical builder accepts the 8 positional args
plus optional ``evidence`` / ``confidence`` / ``detector_version``
kwargs — keys are emitted in the historical insertion order and
the optional kwargs are OMITTED ENTIRELY when ``None`` (so
8-key callers in ``smells.py`` get back an 8-key dict, NOT
an 11-key dict with three ``None``s). This preserves the JSON-
serialisation byte shape every finding-registry test asserts on.
The two detector-specific wrappers (``parallel_hierarchy._finding``
and ``clones_cross_layer._make_finding``) survive as thin
detector-arg adapters that delegate the dict construction to
``make_smell_finding``; only the structural duplication of the
dict literal is removed.
"""

from __future__ import annotations

import functools
import sqlite3
from pathlib import Path
from typing import Any

__all__ = [
    "loc",
    "find_workspace_root",
    "is_test_path",
    "enclosing_symbol",
    "make_smell_finding",
]


def loc(path: str, line: int | None) -> str:
    """Format a ``path:line`` location string.

    Returns ``f"{path}:{line}"`` when *line* is non-None, otherwise the
    bare *path*. ``line=0`` is treated as explicit (the helper tests
    ``is not None``, not truthiness — pinned by
    ``test_loc_line_zero_is_falsy_but_explicit``).
    """
    if line is not None:
        return f"{path}:{line}"
    return path


def find_workspace_root() -> Path:
    """Locate the project root the indexed files are relative to.

    Uses ``find_project_root()`` (walks up from cwd looking for ``.git``).
    Falls back to cwd as a last resort. Detector is best-effort — files
    that can't be read are silently skipped, so a misaligned root is a
    no-op rather than a crash.
    """
    try:
        from roam.db.connection import find_project_root

        return find_project_root()
    except (ImportError, OSError):
        # W660: narrowed from `except Exception` — ImportError covers the
        # `from roam.db.connection import find_project_root` line for
        # callers that import smells.py without the full roam package on
        # sys.path; OSError covers find_project_root's internal calls to
        # ``Path(start).resolve()`` and ``(current / ".git").exists()``
        # which can raise on permission errors, broken symlinks, or
        # missing parent directories. Programmer-class errors
        # (NameError / AttributeError / TypeError) propagate per W531
        # fail-loud discipline + W653 incident.
        return Path.cwd()


def is_test_path(path: str) -> bool:
    """Delegate to canonical ``changed_files.is_test_file`` (W898).

    Historical catalog-layer detector. Pre-W898 this module re-implemented
    the same test-path heuristics as the commands-layer
    ``changed_files.is_test_file`` (W873 hoist) and the two ran in
    parallel — same concept, two implementations, with W886/W889/W891/W893
    repeatedly patching one side to match the other. W898 collapses both
    onto the canonical commands-layer detector, which itself delegates to
    ``roam.index.test_conventions.is_test_file`` for cross-language
    coverage. No cycle exists: ``commands.changed_files`` does not import
    from ``roam.catalog`` (verified via grep both directions per the
    CLAUDE.md "Verify the cycle before hedging" rule, W907).

    Returns ``False`` for empty / falsy inputs.

    Memoized: test-path classification is a pure function of the path string
    (naming + directory conventions, no file content or index state), and the
    catalog detectors call this once per symbol/finding — ~129k calls in a
    project-wide ``run_detectors`` on roam-code, with heavy path repetition.
    The cache collapses that to one classification per distinct path.
    """
    return _is_test_path_cached(path)


@functools.lru_cache(maxsize=8192)
def _is_test_path_cached(path: str) -> bool:
    from roam.commands.changed_files import is_test_file

    return is_test_file(path)


def enclosing_symbol(conn: sqlite3.Connection, file_id: int, line: int) -> tuple[str, str, int]:
    """Return (symbol_name, kind, line_start) for the enclosing function.

    Looks up the innermost ``function`` / ``method`` recorded in the
    ``symbols`` table whose ``[line_start, line_end]`` span contains
    *line* for the given *file_id*. Falls back to
    (``"<module>"``, ``"file"``, *line*) when:
      * no enclosing function/method is recorded (top-level statement,
        class-body-only file, or an indexer gap), or
      * the query raises ``sqlite3.OperationalError`` (transient lock,
        missing column on a stale schema, etc.).

    The ``OperationalError`` fallback mirrors the defensive contract of
    the pre-W877 ``type_switch._enclosing_symbol``; the SQL + return
    shape matches the canonical ``smells._enclosing_symbol``.
    """
    try:
        row = conn.execute(
            "SELECT name, kind, line_start FROM symbols "
            "WHERE file_id = ? AND kind IN ('function', 'method') "
            "AND line_start <= ? AND COALESCE(line_end, line_start) >= ? "
            "ORDER BY line_start DESC LIMIT 1",
            (file_id, line, line),
        ).fetchone()
    except sqlite3.OperationalError:
        return "<module>", "file", line
    if row is not None:
        return row["name"], row["kind"], int(row["line_start"] or line)
    return "<module>", "file", line


def make_smell_finding(
    smell_id: str,
    severity: str,
    symbol_name: str,
    kind: str,
    location: str,
    metric_value: float | int,
    threshold: float | int,
    description: str,
    *,
    evidence: dict[str, Any] | None = None,
    confidence: str | None = None,
    detector_version: int | None = None,
) -> dict[str, Any]:
    """Canonical structural-smell finding envelope (W923).

    Returns the historical 8-key dict in INSERTION order:
    ``smell_id``, ``severity``, ``symbol_name``, ``kind``, ``location``,
    ``metric_value``, ``threshold``, ``description``. When any of the
    optional kwargs is non-None, the corresponding key (``evidence`` /
    ``confidence`` / ``detector_version``) is appended in that order.

    The non-None gating preserves byte-identical JSON output for the
    24 ``smells.py`` call-sites that historically built only the 8-key
    shape: the helper does NOT inject ``"evidence": None`` /
    ``"confidence": None`` / ``"detector_version": None`` filler keys.
    The richer-envelope sites (``type_switch``, ``parallel_hierarchy``,
    ``clones_cross_layer``) pass the kwargs explicitly and get the
    expected 11-key dict.

    NOTE: callers can still post-mutate the returned dict (e.g.
    ``type_switch`` historically built the 8-key dict, then assigned
    ``finding["evidence"] = ...`` / ``finding["confidence"] = ...`` /
    ``finding["detector_version"] = ...`` after the fact). That idiom
    keeps working — this helper is additive, not restrictive.
    """
    out: dict[str, Any] = {
        "smell_id": smell_id,
        "severity": severity,
        "symbol_name": symbol_name,
        "kind": kind,
        "location": location,
        "metric_value": metric_value,
        "threshold": threshold,
        "description": description,
    }
    if evidence is not None:
        out["evidence"] = evidence
    if confidence is not None:
        out["confidence"] = confidence
    if detector_version is not None:
        out["detector_version"] = detector_version
    return out
