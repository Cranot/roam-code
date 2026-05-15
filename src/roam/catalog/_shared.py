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

``is_test_path`` (W873) is the most permissive of the historical
catalog-layer variants. It UNIONs the directory-pattern set used by
``detectors._is_test_path`` and the basename-pattern set used by
``type_switch._file_is_test``, plus the cross-language extension
suffixes the rules layer already recognised. Callers outside
``roam.catalog`` should keep using ``roam.commands.changed_files.
is_test_file`` (which delegates to ``roam.index.file_roles`` and is
the canonical commands-layer detector) — see W873-drive-by notes.

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

import re
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


# Directory components anywhere in the normalised path that mark a test
# tree. Matched after Windows-backslash normalisation + lowercasing.
_TEST_DIR_SEGMENTS: tuple[str, ...] = (
    "/tests/",
    "/test/",
    "/__tests__/",
    "/spec/",
    "/testing/",
)
# Same segments anchored at the start of the path (no leading slash).
_TEST_DIR_PREFIXES: tuple[str, ...] = (
    "tests/",
    "test/",
    "__tests__/",
    "spec/",
    "testing/",
)
# Cross-language basename suffixes for test files.
# ``_test.py`` / ``_tests.py`` (Python alt), ``_test.go`` (Go),
# ``_test.rs`` (Rust), ``_test.php`` (PHP), ``_test.exs`` (Elixir),
# ``_test.dart`` (Dart). The Elixir / Dart entries close W891 — the
# canonical ``roam.index.test_conventions.DEFAULT_TEST_PATTERNS``
# already covered both, so this is a pure parity fix at the catalog
# layer (same FP-risk class as the W889 camelCase widening).
_TEST_FILE_SUFFIXES: tuple[str, ...] = (
    "_test.py",
    "_tests.py",
    "_test.go",
    "_test.rs",
    "_test.php",
    "_test.exs",
    "_test.dart",
)
# Basename infixes for JS/TS/Vue/Svelte (Vitest, Jest, ``.spec.ts``).
_TEST_BASENAME_INFIXES: tuple[str, ...] = (".test.", ".spec.")

# camelCase / PascalCase test basenames for Java / Kotlin / C# / Swift /
# PHP / Scala / Apex. Matches the same set covered by
# ``roam.index.test_conventions.DEFAULT_TEST_PATTERNS`` so the catalog
# layer stays in parity with the canonical commands-layer detector
# (``commands.changed_files.is_test_file``) on cross-language repos
# where test files live outside an explicit ``tests/`` directory
# (W889 / drive-by from W886).
#
# Case-sensitivity matters here: the canonical
# ``DEFAULT_TEST_PATTERNS`` uses case-sensitive ``Tests?\.java`` so that
# ``latest.java`` (lowercase ``test`` inside ``latest``) is NOT
# misclassified as a test file. The catalog ``is_test_path`` lowercases
# its input for directory matching, but this camelCase check runs
# against the ORIGINAL-CASE basename to preserve that discipline.
# Scala adds a parallel ``Spec`` suffix (``FooSpec.scala``); the
# canonical pattern covers both.
_CAMELCASE_TEST_BASENAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Java, Kotlin, C#, Swift, Apex: ``*Test.<ext>`` / ``*Tests.<ext>``.
    # Uses ``.*`` (not ``.+``) to match the canonical
    # ``DEFAULT_TEST_PATTERNS`` exactly — a bare ``Test.java`` is treated
    # as a test file (boundary case: aligns with the canonical regex so
    # the two layers agree). Note: Apex ``cls`` is single-suffix-only in
    # the canonical (``^.*Test\.cls$``, no plural), so it's split out
    # below alongside PHP.
    re.compile(r"^.*Tests?\.(?:java|kt|cs|swift)$"),
    # PHP, Apex: ``*Test.<ext>`` only (no plural per canonical).
    re.compile(r"^.*Test\.(?:php|cls)$"),
    # Scala: ``*Test.scala`` / ``*Spec.scala``.
    re.compile(r"^.*(?:Test|Spec)\.scala$"),
)


def is_test_path(path: str) -> bool:
    """Return True if *path* points at a test file.

    Most permissive catalog-layer detector. Combines every directory
    pattern (``tests/``, ``test/``, ``__tests__/``, ``spec/``,
    ``testing/``) and basename pattern (``test_*.py``, ``conftest.py``,
    ``*_test.py``, ``*_tests.py``, ``*_test.go``, ``*_test.rs``,
    ``*_test.php``, ``*.test.ts``, ``*.spec.js``) seen across the
    historical in-file copies, PLUS the camelCase/PascalCase basenames
    common in Java / Kotlin / C# / Swift / PHP / Scala / Apex codebases
    (``UserTest.java``, ``UserTests.cs``, ``UserSpec.scala``, ...) so
    detectors on those stacks don't mis-classify test files as
    production code (W889 — parity with the canonical commands-layer
    detector at ``commands.changed_files.is_test_file``).

    Windows-safe: ``\\`` is normalised to ``/`` before matching, so a
    ``"tests\\foo\\test_bar.py"`` input matches the same way as the
    POSIX form. Directory matching is case-insensitive (mixed-case
    ``Tests/`` directories on case-insensitive filesystems still
    match). The camelCase basename check is case-SENSITIVE so that
    ``latest.java`` (lowercase ``test`` inside ``latest``) is NOT
    treated as a test file — matching the canonical
    ``DEFAULT_TEST_PATTERNS`` discipline.

    Returns ``False`` for empty / falsy inputs.
    """
    if not path:
        return False
    # Original-case forward-slash path for case-sensitive camelCase match.
    p_cs = path.replace("\\", "/")
    p = p_cs.lower()
    if any(seg in p for seg in _TEST_DIR_SEGMENTS):
        return True
    if any(p.startswith(prefix) for prefix in _TEST_DIR_PREFIXES):
        return True
    # Basename checks
    base = p.rsplit("/", 1)[-1]
    if base == "conftest.py":
        return True
    if base.startswith("test_"):
        return True
    if any(base.endswith(suffix) for suffix in _TEST_FILE_SUFFIXES):
        return True
    if any(infix in base for infix in _TEST_BASENAME_INFIXES):
        return True
    # camelCase / PascalCase basename match (Java / Kotlin / C# / Swift /
    # PHP / Scala / Apex) — case-sensitive against the original basename
    # so ``latest.java`` (lowercase ``test`` inside ``latest``) is NOT
    # treated as a test file. Parity with
    # ``index.test_conventions.DEFAULT_TEST_PATTERNS`` (W889).
    base_cs = p_cs.rsplit("/", 1)[-1]
    if any(pat.match(base_cs) for pat in _CAMELCASE_TEST_BASENAME_PATTERNS):
        return True
    return False


def enclosing_symbol(
    conn: sqlite3.Connection, file_id: int, line: int
) -> tuple[str, str, int]:
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
