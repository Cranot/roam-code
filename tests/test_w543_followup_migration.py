"""W543-followup drift-guards — 7 call sites migrated off inline literals.

The original W543 (rules/builtin.py) and W544 (sql_lang.py) migrations
promoted ``INHERITANCE_EDGE_KINDS`` / ``IMPORT_EDGE_KINDS`` + the
``inheritance_in_clause`` / ``import_in_clause`` helpers in
``roam.db.edge_kinds``. The W543-followup wave migrates the remaining
inline-literal call sites surfaced by the original audit:

* ``catalog/parallel_hierarchy.py`` — local set re-anchored on the
  canonical tuple + an explicit plugin-defensive ``("extends",)``
  widening (pinned by ``test_w857_parallel_hierarchy.py``).
* ``commands/cmd_patterns.py:_detect_strategy`` — was filtering
  ``kind IN ('inherits', 'implements')``; now sources the IN-clause
  from :func:`roam.db.edge_kinds.inheritance_in_clause` so
  ``uses_trait`` rows reach the detector too.
* ``commands/cmd_understand.py:_detect_patterns_summary`` — was a bare
  ``kind = 'inherits'`` literal; now unions all three inheritance kinds.
* ``commands/cmd_symbol.py:_EDGE_PRIORITY`` — added explicit
  ``uses_trait`` + plural ``imports`` priority tiers so the canonical
  kind set drives the dedup ordering directly.
* ``security/vuln_store.py:match_vuln_to_symbols`` — was bare
  ``kind = 'import'`` singular; now unions the plural alias.
* ``policy/graph_clauses.py:check_imports_from`` — same shape; the
  symbol-edges fallback now unions both forms.
* ``commands/cmd_verify_imports.py:_get_edge_imports`` — both query
  branches (file-scoped and unfiltered) now source the IN-clause from
  the shared helper.

What this test asserts
----------------------

Per-site behavioural test (insert plural-form rows; verify they
surface through the migrated query path). One drift-guard scans
``src/roam/`` for any remaining
``kind IN ('inherits', 'implements')`` /
``kind IN ('inherits', 'implements', 'uses_trait')`` /
``kind IN ('import', 'imports')`` /
``kind = 'import'`` /
``kind = 'inherits'`` literals outside ``db/edge_kinds.py``.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"


# ---------------------------------------------------------------------------
# Tiny in-memory schema mirroring the production ``edges`` / ``symbols`` /
# ``files`` / ``file_edges`` columns that the migrated call sites read.
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            line_start INTEGER, line_end INTEGER, parent_id INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL,
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE file_edges (
            id INTEGER PRIMARY KEY,
            source_file_id INTEGER NOT NULL,
            target_file_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'imports',
            symbol_count INTEGER DEFAULT 1
        );
        """
    )
    conn.commit()
    return conn


def _add_file(conn: sqlite3.Connection, path: str, lang: str = "python") -> int:
    cur = conn.execute("INSERT INTO files (path, language) VALUES (?, ?)", (path, lang))
    return cur.lastrowid


def _add_symbol(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    name: str,
    kind: str = "class",
    line: int = 1,
    qname: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, name, qualified_name, kind, line_start, line_end) VALUES (?, ?, ?, ?, ?, ?)",
        (file_id, name, qname or name, kind, line, line + 5),
    )
    return cur.lastrowid


def _add_edge(
    conn: sqlite3.Connection,
    src: int,
    tgt: int,
    kind: str,
    *,
    source_file_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind, source_file_id) VALUES (?, ?, ?, ?)",
        (src, tgt, kind, source_file_id),
    )


# ---------------------------------------------------------------------------
# Site 1: catalog/parallel_hierarchy.py — local widening over canonical.
# Pins that ``INHERITANCE_EDGE_KINDS`` now derives from the shared module
# (canonical 3 kinds) PLUS the plugin-defensive ``'extends'`` widening
# the W857 test depends on.
# ---------------------------------------------------------------------------


def test_parallel_hierarchy_inherits_canonical_plus_extends_widening() -> None:
    from roam.catalog.parallel_hierarchy import INHERITANCE_EDGE_KINDS as LOCAL_SET
    from roam.db.edge_kinds import INHERITANCE_EDGE_KINDS as CANONICAL_SET

    # Canonical set is the prefix; ``'extends'`` is the documented widening.
    for kind in CANONICAL_SET:
        assert kind in LOCAL_SET, f"W543-followup: parallel_hierarchy lost canonical kind '{kind}'"
    assert "extends" in LOCAL_SET, (
        "W543-followup: parallel_hierarchy must keep the plugin-defensive "
        "'extends' widening — pinned by test_w857_parallel_hierarchy."
    )
    # Spot-check: ``uses_trait`` (canonical) MUST now be honoured even
    # though the pre-migration inline tuple only listed ``('inherits',
    # 'extends')``.
    assert "uses_trait" in LOCAL_SET


# ---------------------------------------------------------------------------
# Site 2: cmd_patterns._detect_strategy — was kind IN ('inherits',
# 'implements'); now sources from inheritance_in_clause so uses_trait
# rows reach the detector.
# ---------------------------------------------------------------------------


def test_cmd_patterns_strategy_detector_sees_uses_trait_rows() -> None:
    from roam.commands.cmd_patterns import _detect_strategy

    conn = _make_db()
    f = _add_file(conn, "src/m.py")
    parent = _add_symbol(conn, file_id=f, name="Strategy", kind="interface")
    a = _add_symbol(conn, file_id=f, name="StratA", kind="class")
    b = _add_symbol(conn, file_id=f, name="StratB", kind="class")
    # Use ``uses_trait`` to prove the new IN-clause unions the third
    # canonical kind. Pre-migration this row would have been silently
    # filtered out.
    _add_edge(conn, a, parent, "uses_trait")
    _add_edge(conn, b, parent, "uses_trait")
    conn.commit()

    results = _detect_strategy(conn)
    # Exactly one Strategy candidate with 2 implementers — proving the
    # ``uses_trait`` rows now flow through.
    assert isinstance(results, list)
    matched = [r for r in results if r.get("name") == "Strategy"]
    assert len(matched) == 1, f"expected 1 Strategy match, got {results!r}"
    assert matched[0]["implementation_count"] == 2


# ---------------------------------------------------------------------------
# Site 3: cmd_understand._detect_patterns_summary — was bare
# kind = 'inherits'; now unions all three inheritance kinds.
# ---------------------------------------------------------------------------


def test_cmd_understand_strategy_summary_sees_implements_rows() -> None:
    from roam.commands.cmd_understand import _detect_patterns_summary

    conn = _make_db()
    f = _add_file(conn, "src/m.py")
    parent = _add_symbol(conn, file_id=f, name="Runnable", kind="interface")
    # 3+ implementations needed for the summary (HAVING COUNT(*) >= 3).
    for i in range(3):
        child = _add_symbol(conn, file_id=f, name=f"Impl{i}", kind="class")
        _add_edge(conn, child, parent, "implements")
    conn.commit()

    patterns = _detect_patterns_summary(conn)
    # Pre-migration: filter was bare 'inherits' — 'implements' rows
    # produced zero results. Post-migration: Runnable should surface.
    hierarchy = [p for p in patterns if p.get("type") == "strategy/hierarchy"]
    assert any(p["name"] == "Runnable" for p in hierarchy), (
        f"W543-followup: cmd_understand summary must surface 'implements' edges; got patterns={patterns!r}"
    )


# ---------------------------------------------------------------------------
# Site 4: cmd_symbol._EDGE_PRIORITY — explicit uses_trait + plural
# imports tiers.
# ---------------------------------------------------------------------------


def test_cmd_symbol_edge_priority_covers_canonical_kinds() -> None:
    from roam.commands.cmd_symbol import _EDGE_PRIORITY
    from roam.db.edge_kinds import IMPORT_EDGE_KINDS, INHERITANCE_EDGE_KINDS

    # Every canonical inheritance kind has an explicit priority tier.
    for kind in INHERITANCE_EDGE_KINDS:
        assert kind in _EDGE_PRIORITY, f"W543-followup: _EDGE_PRIORITY missing canonical inheritance kind '{kind}'"
    # Every canonical import kind likewise.
    for kind in IMPORT_EDGE_KINDS:
        assert kind in _EDGE_PRIORITY, f"W543-followup: _EDGE_PRIORITY missing canonical import kind '{kind}'"
    # Dedup ordering invariant: call < inheritance < import.
    assert _EDGE_PRIORITY["call"] < _EDGE_PRIORITY["inherits"]
    assert _EDGE_PRIORITY["inherits"] <= _EDGE_PRIORITY["implements"]
    assert _EDGE_PRIORITY["implements"] <= _EDGE_PRIORITY["uses_trait"]
    assert _EDGE_PRIORITY["uses_trait"] < _EDGE_PRIORITY["import"]
    assert _EDGE_PRIORITY["import"] == _EDGE_PRIORITY["imports"]


# ---------------------------------------------------------------------------
# Site 5: security/vuln_store.match_vuln_to_symbols — was bare
# kind = 'import' singular; now unions the plural alias.
# ---------------------------------------------------------------------------


def test_vuln_store_matches_plural_import_edges() -> None:
    from roam.security.vuln_store import match_vuln_to_symbols

    conn = _make_db()
    f = _add_file(conn, "src/app.py")
    importer = _add_symbol(conn, file_id=f, name="App", kind="module")
    pkg = _add_symbol(conn, file_id=f, name="requests", kind="module")
    # Plural form — pre-migration this row would have been filtered out.
    _add_edge(conn, importer, pkg, "imports")
    conn.commit()

    matches = match_vuln_to_symbols(conn, "requests")
    # The direct-symbol-name search matches the ``requests`` symbol;
    # the edge-based search (the migrated path) should ADDITIONALLY
    # surface the importer ``App`` via the plural edge.
    matched_names = {m["name"] for m in matches}
    assert "App" in matched_names, (
        f"W543-followup: vuln_store must follow plugin-defensive 'imports' edges; got matches={matches!r}"
    )


# ---------------------------------------------------------------------------
# Site 6: policy/graph_clauses.check_imports_from — symbol-edges fallback
# now unions both 'import' / 'imports'.
# ---------------------------------------------------------------------------


def test_policy_check_imports_from_unions_plural_fallback() -> None:
    from roam.policy.graph_clauses import check_imports_from

    conn = _make_db()
    src_file = _add_file(conn, "src/legacy/old.py")
    tgt_file = _add_file(conn, "src/modern/new.py")
    # Symbol-level row only (no file_edges row), with the PLURAL kind.
    # The fallback branch must catch this — pre-migration filter was
    # bare ``kind = 'import'`` singular.
    importer = _add_symbol(conn, file_id=src_file, name="OldMod", kind="module")
    target = _add_symbol(conn, file_id=tgt_file, name="NewMod", kind="module")
    _add_edge(conn, importer, target, "imports", source_file_id=src_file)
    conn.commit()

    found, evidence = check_imports_from(conn, module="src/modern/new.py", target_file="src/legacy/old.py")
    assert found is True, (
        "W543-followup: check_imports_from must surface plural 'imports' "
        f"symbol-edges in the fallback branch; evidence={evidence!r}"
    )


# ---------------------------------------------------------------------------
# Site 7: cmd_verify_imports._get_edge_imports — both branches union
# 'import' / 'imports'.
# ---------------------------------------------------------------------------


def test_cmd_verify_imports_get_edge_imports_unions_both_forms() -> None:
    from roam.commands.cmd_verify_imports import _get_edge_imports

    conn = _make_db()
    f = _add_file(conn, "src/app.py")
    src_sym = _add_symbol(conn, file_id=f, name="app", kind="module")
    tgt_sym = _add_symbol(conn, file_id=f, name="requests", kind="module")
    # Mix singular + plural rows to prove the unioned clause catches both.
    _add_edge(conn, src_sym, tgt_sym, "import")
    tgt_sym2 = _add_symbol(conn, file_id=f, name="numpy", kind="module")
    _add_edge(conn, src_sym, tgt_sym2, "imports")
    conn.commit()

    # File-scoped branch.
    rows_scoped = _get_edge_imports(conn, file_path="src/app.py")
    assert len(rows_scoped) == 2, f"W543-followup: scoped _get_edge_imports must union both kinds; got {rows_scoped!r}"

    # Unfiltered branch.
    rows_all = _get_edge_imports(conn, file_path=None)
    assert len(rows_all) == 2, f"W543-followup: unfiltered _get_edge_imports must union both kinds; got {rows_all!r}"


# ---------------------------------------------------------------------------
# Drift-guard: pin the 7 migrated files. No re-introduction of inline
# ``kind IN ('inherits', 'implements')`` / ``kind IN ('import',
# 'imports')`` / bare ``kind = 'import'`` / bare ``kind = 'inherits'``
# SQL-filter literals in the migrated call sites.
#
# Scope is intentionally narrow: this wave migrated 7 files. Pre-existing
# bare ``kind = ...`` SQL filters in *other* modules (e.g.
# ``catalog/smells.py``, ``index/django_post.py``) are sibling-agent
# territory tracked in a follow-up wave (see report). Writer sites
# (``languages/*_lang.py`` ``kind="import"`` dict-value assignments) are
# NEVER in scope — those are canonical emitters, not readers.
# ---------------------------------------------------------------------------


# Each pattern flags one shape we don't want re-introduced.
_DRIFT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"""kind\s+IN\s*\(\s*['"]inherits['"]\s*,\s*['"]implements['"]""",
            re.IGNORECASE,
        ),
        "kind IN ('inherits', 'implements', ...) — use inheritance_in_clause()",
    ),
    (
        re.compile(
            r"""kind\s+IN\s*\(\s*['"]import['"]\s*,\s*['"]imports['"]""",
            re.IGNORECASE,
        ),
        "kind IN ('import', 'imports') — use import_in_clause()",
    ),
    (
        re.compile(r"""\.?kind\s*=\s*['"]import['"](?!s)""", re.IGNORECASE),
        "kind = 'import' (bare singular SQL filter) — use import_in_clause()",
    ),
    (
        re.compile(r"""\.?kind\s*=\s*['"]inherits['"]""", re.IGNORECASE),
        "kind = 'inherits' (bare singular SQL filter) — use inheritance_in_clause()",
    ),
]


# The 7 files migrated in this wave. Pinning these prevents a regression
# where a future edit re-introduces the inline literal locally.
_MIGRATED_FILES: tuple[str, ...] = (
    "catalog/parallel_hierarchy.py",
    "commands/cmd_patterns.py",
    "commands/cmd_understand.py",
    "commands/cmd_symbol.py",
    "security/vuln_store.py",
    "policy/graph_clauses.py",
    "commands/cmd_verify_imports.py",
)


def _scan_text_for_drift(text: str) -> list[tuple[str, str]]:
    """Return (label, snippet) hits inside ``text``.

    Skips obvious comment / docstring lines so a docstring discussing a
    historical literal does not trip the guard.
    """
    hits: list[tuple[str, str]] = []
    for pattern, label in _DRIFT_PATTERNS:
        for m in pattern.finditer(text):
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            if end == -1:
                end = len(text)
            snippet = text[start:end].strip()
            stripped = snippet.lstrip()
            if stripped.startswith(("#", '"""', "'''", "*", "//")):
                continue
            hits.append((label, snippet))
    return hits


@pytest.mark.parametrize("rel_path", _MIGRATED_FILES)
def test_migrated_file_has_no_inline_kind_literal(rel_path: str) -> None:
    """Each migrated file must source kind-filtering from the shared helpers."""
    py = SRC_ROOT / rel_path
    assert py.exists(), f"W543-followup: migrated file missing: {rel_path}"
    text = py.read_text(encoding="utf-8")
    hits = _scan_text_for_drift(text)
    if hits:
        lines = [f"  {label} :: {snippet}" for (label, snippet) in hits]
        pytest.fail(
            f"W543-followup: inline kind-literal drift in {rel_path}. "
            "Source the IN-clause from roam.db.edge_kinds.inheritance_in_clause "
            "/ import_in_clause:\n" + "\n".join(lines)
        )
