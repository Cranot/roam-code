"""W543-followup-C drift-guards — round-3 migration sweep (4 sites).

W543 / W544 promoted ``INHERITANCE_EDGE_KINDS`` / ``IMPORT_EDGE_KINDS`` +
the ``inheritance_in_clause`` / ``import_in_clause`` helpers in
``roam.db.edge_kinds``. W543-followup migrated 7 sites. This round-3 wave
migrates 4 more sibling sites surfaced after the follow-up:

* ``catalog/smells.py::detect_refused_bequest`` — was bare
  ``e.kind = 'inherits'``; now sources from
  :func:`roam.db.edge_kinds.inheritance_in_clause` so plugin-emitted
  ``'implements'`` / ``'uses_trait'`` rows reach the refused-bequest
  detector too.
* ``index/django_post.py::resolve_django_inheritance`` — was bare
  ``kind = 'inherits'``; now unions all three canonical kinds. Bridge
  contract (W156/W39.3) preserved: python_lang.py still emits the
  canonical ``'inherits'`` singular for Django models.
* ``index/django_post.py::resolve_django_custom_fields`` — same shape;
  custom-field resolver now also honours ``'implements'`` /
  ``'uses_trait'`` ancestors.
* ``commands/cmd_hover.py::_top_neighbour`` — cross-vocabulary
  ``IN ('call', 'calls', 'inherits', 'import', 'imports')`` literal now
  composed from canonical ``CALL_EDGE_KINDS + INHERITANCE_EDGE_KINDS +
  IMPORT_EDGE_KINDS``. Reference edges deliberately omitted (matches
  pre-migration intent per ``edge_kinds.py`` lines 56-58).

What this test asserts
----------------------

Per-site behavioural test: insert plural-form / non-canonical rows and
verify they surface through the migrated query path. One drift-guard
parametrised over the 3 migrated files (``smells.py``,
``django_post.py``, ``cmd_hover.py``) asserts no bare
``kind = 'inherits'`` / ``kind = 'import'`` SQL filters remain in scope.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"


# ---------------------------------------------------------------------------
# Tiny in-memory schema mirroring the production ``edges`` / ``symbols`` /
# ``files`` / ``graph_metrics`` columns that the migrated call sites read.
# Modelled on tests/test_w543_followup_migration.py so the two waves share
# the same helper shape and stay easy to read side-by-side.
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
            framework_type TEXT, field_base_type TEXT, field_type TEXT,
            field_metadata TEXT, call_function TEXT,
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
        CREATE TABLE graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            pagerank REAL DEFAULT 0
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
    framework_type: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO symbols "
        "(file_id, name, qualified_name, kind, line_start, line_end, framework_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, name, qname or name, kind, line, line + 5, framework_type),
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
# Site 1: catalog/smells.py::detect_refused_bequest — was bare
# e.kind = 'inherits'; now unions all three canonical kinds.
#
# detect_refused_bequest reads source files to count trivial overrides; we
# verify the migrated SQL surfaces the (child, parent) pair through ALL
# three canonical kinds by directly running the migrated IN-clause against
# our in-memory edges table. The full detector body needs a workspace +
# real source files; a query-shape test is the right granularity.
# ---------------------------------------------------------------------------


def test_smells_refused_bequest_query_unions_canonical_kinds() -> None:
    """The migrated inheritance IN-clause must surface all 3 canonical kinds."""
    from roam.db.edge_kinds import INHERITANCE_EDGE_KINDS, inheritance_in_clause

    conn = _make_db()
    f = _add_file(conn, "src/m.py")
    parent = _add_symbol(conn, file_id=f, name="Base", kind="class")

    # One child per canonical inheritance kind.
    child_ids = []
    for i, edge_kind in enumerate(INHERITANCE_EDGE_KINDS):
        child = _add_symbol(conn, file_id=f, name=f"Child{i}", kind="class", line=10 + i)
        _add_edge(conn, child, parent, edge_kind)
        child_ids.append(child)
    conn.commit()

    # Run the exact migrated WHERE clause shape against the in-memory db.
    sql = (
        "SELECT s_child.id AS child_id, s_parent.id AS parent_id "
        "FROM edges e "
        "JOIN symbols s_child ON e.source_id = s_child.id "
        "JOIN symbols s_parent ON e.target_id = s_parent.id "
        f"WHERE {inheritance_in_clause('e.kind')} "
        "AND s_child.kind = 'class' "
        "AND s_parent.kind = 'class'"
    )
    rows = conn.execute(sql).fetchall()
    surfaced = {r["child_id"] for r in rows}
    assert surfaced == set(child_ids), (
        "W543-followup-C: smells.detect_refused_bequest must union all 3 "
        f"canonical inheritance kinds; got child rows {surfaced!r} vs "
        f"expected {set(child_ids)!r}"
    )


# ---------------------------------------------------------------------------
# Site 2: index/django_post.py::resolve_django_inheritance — bridge
# contract preserved (canonical 'inherits' still tagged) AND plugin-emitted
# 'implements' chains now also propagate framework_type='django_model'.
# ---------------------------------------------------------------------------


def test_django_post_inheritance_propagates_through_implements_edges() -> None:
    """A class reachable via 'implements' edges should still be tagged django_model."""
    from roam.index.django_post import resolve_django_inheritance

    conn = _make_db()
    f = _add_file(conn, "myapp/models.py")
    # Seed: a Django model (framework_type already set — fast-path root).
    root = _add_symbol(
        conn,
        file_id=f,
        name="MyModel",
        kind="class",
        framework_type="django_model",
    )
    # Mid uses canonical 'inherits' (bridge-contract canonical kind).
    mid = _add_symbol(conn, file_id=f, name="MidModel", kind="class")
    _add_edge(conn, mid, root, "inherits")
    # Leaf uses the plural-alias 'implements' kind that the migration
    # widens to. Pre-migration this row would have been silently dropped
    # by the bare ``kind = 'inherits'`` filter.
    leaf = _add_symbol(conn, file_id=f, name="LeafModel", kind="class")
    _add_edge(conn, leaf, mid, "implements")
    conn.commit()

    updated = resolve_django_inheritance(conn)
    # Both ``mid`` and ``leaf`` should be tagged.
    assert updated == 2, f"expected 2 symbols tagged, got {updated}"

    # Verify by querying back.
    tagged = {r["name"] for r in conn.execute("SELECT name FROM symbols WHERE framework_type = 'django_model'")}
    assert tagged == {"MyModel", "MidModel", "LeafModel"}, (
        f"W543-followup-C: django_post.resolve_django_inheritance must follow 'implements' edges; tagged={tagged!r}"
    )


# ---------------------------------------------------------------------------
# Site 3: index/django_post.py::resolve_django_custom_fields — same
# migration shape; custom-field resolver should also walk
# 'uses_trait' edges.
# ---------------------------------------------------------------------------


def test_django_post_custom_fields_walks_uses_trait_edges() -> None:
    """A custom field class reachable via 'uses_trait' should resolve its base."""
    from roam.index.django_post import resolve_django_custom_fields

    conn = _make_db()
    f = _add_file(conn, "myapp/fields.py")
    # CharField is a builtin Django field (matches _DJANGO_FIELD_TYPES).
    char_field = _add_symbol(conn, file_id=f, name="CharField", kind="class")
    # Custom field uses ``uses_trait`` to extend CharField (e.g. a trait
    # composition pattern from a plugin extractor). Pre-migration the
    # bare ``kind = 'inherits'`` filter dropped this row.
    custom = _add_symbol(conn, file_id=f, name="EncryptedCharField", kind="class")
    _add_edge(conn, custom, char_field, "uses_trait")
    # Add a property that uses the custom field so updates is non-empty.
    parent_model = _add_symbol(conn, file_id=f, name="Holder", kind="class")
    prop = _add_symbol(conn, file_id=f, name="data", kind="property")
    # Set the call_function + parent_id directly.
    conn.execute(
        "UPDATE symbols SET call_function = ?, parent_id = ? WHERE id = ?",
        ("EncryptedCharField", parent_model, prop),
    )
    conn.commit()

    updated = resolve_django_custom_fields(conn)
    # The property should have field_base_type='CharField' resolved through
    # the ``uses_trait`` edge.
    assert updated == 1, f"expected 1 property updated, got {updated}"
    row = conn.execute(
        "SELECT field_type, field_base_type FROM symbols WHERE id = ?",
        (prop,),
    ).fetchone()
    assert row["field_type"] == "EncryptedCharField"
    assert row["field_base_type"] == "CharField", (
        "W543-followup-C: django_post.resolve_django_custom_fields must "
        f"resolve via 'uses_trait' edges; got field_base_type={row['field_base_type']!r}"
    )


# ---------------------------------------------------------------------------
# Site 4: commands/cmd_hover.py::_top_neighbour — cross-vocabulary union
# composed from CALL_EDGE_KINDS + INHERITANCE_EDGE_KINDS + IMPORT_EDGE_KINDS.
# Verify (a) the constants compose correctly and (b) the query returns
# neighbours through all three vocabularies (call + inheritance + import).
# ---------------------------------------------------------------------------


def test_cmd_hover_neighbour_kinds_compose_from_canonical_constants() -> None:
    """Composition: CALL + INHERIT + IMPORT canonical tuples reach the query."""
    from roam.commands.cmd_hover import _HOVER_NEIGHBOUR_KINDS
    from roam.db.edge_kinds import (
        CALL_EDGE_KINDS,
        IMPORT_EDGE_KINDS,
        INHERITANCE_EDGE_KINDS,
    )

    # Every canonical call kind reaches hover's neighbour query.
    for kind in CALL_EDGE_KINDS:
        assert kind in _HOVER_NEIGHBOUR_KINDS, f"W543-followup-C: cmd_hover lost canonical call kind '{kind}'"
    # Every canonical inheritance kind reaches it (this is the WIDENING
    # the migration delivers — pre-migration only ``'inherits'`` was in
    # the inline literal).
    for kind in INHERITANCE_EDGE_KINDS:
        assert kind in _HOVER_NEIGHBOUR_KINDS, f"W543-followup-C: cmd_hover lost canonical inheritance kind '{kind}'"
    # Every canonical import kind reaches it.
    for kind in IMPORT_EDGE_KINDS:
        assert kind in _HOVER_NEIGHBOUR_KINDS, f"W543-followup-C: cmd_hover lost canonical import kind '{kind}'"
    # Pre-migration intent preserved: reference edges deliberately omitted
    # (the original ``W524-fix`` literal listed call/inherits/import only).
    assert "reference" not in _HOVER_NEIGHBOUR_KINDS
    assert "references" not in _HOVER_NEIGHBOUR_KINDS


def test_cmd_hover_top_neighbour_surfaces_all_three_vocabularies() -> None:
    """_top_neighbour ranks across call + inheritance + import edges."""
    from roam.commands.cmd_hover import _top_neighbour

    conn = _make_db()
    f = _add_file(conn, "src/m.py")
    target = _add_symbol(conn, file_id=f, name="Target", kind="function", line=1)
    # Three callers, one per edge vocabulary, each with a distinct pagerank
    # so we can verify the IN-clause unions all three vocabularies (the
    # winner is the one with the highest pagerank).
    caller_call = _add_symbol(conn, file_id=f, name="Caller_call", kind="function", line=10)
    _add_edge(conn, caller_call, target, "calls")  # plural alias
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, pagerank) VALUES (?, ?)",
        (caller_call, 0.1),
    )

    caller_inherit = _add_symbol(conn, file_id=f, name="Caller_inherit", kind="class", line=20)
    _add_edge(conn, caller_inherit, target, "implements")  # canonical
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, pagerank) VALUES (?, ?)",
        (caller_inherit, 0.5),  # highest — winner
    )

    caller_import = _add_symbol(conn, file_id=f, name="Caller_import", kind="module", line=30)
    _add_edge(conn, caller_import, target, "imports")  # plural alias
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, pagerank) VALUES (?, ?)",
        (caller_import, 0.3),
    )
    conn.commit()

    # Direction ``in`` = callers of ``target``. The migrated IN-clause
    # must reach all three vocabularies — the highest pagerank wins.
    top = _top_neighbour(conn, target, direction="in")
    assert top is not None, "W543-followup-C: _top_neighbour must find at least one neighbour via the unioned IN-clause"
    assert top["name"] == "Caller_inherit", (
        f"W543-followup-C: _top_neighbour should rank 'implements' edges alongside call/import; got top={top!r}"
    )


# ---------------------------------------------------------------------------
# Drift-guard: scan the 3 round-3 migrated files. No re-introduction of
# inline ``kind = 'inherits'`` / ``kind = 'import'`` SQL-filter literals.
# Comments + docstrings that *describe* the canonical kind (e.g.
# ``edges.kind='inherits'`` in a comment) are allowed by the same
# leading-prefix skip the W543-followup drift guard uses.
# ---------------------------------------------------------------------------

_DRIFT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"""\.?kind\s*=\s*['"]import['"](?!s)""", re.IGNORECASE),
        "kind = 'import' (bare singular SQL filter) — use import_in_clause()",
    ),
    (
        re.compile(r"""\.?kind\s*=\s*['"]inherits['"]""", re.IGNORECASE),
        "kind = 'inherits' (bare singular SQL filter) — use inheritance_in_clause()",
    ),
]

_MIGRATED_FILES: tuple[str, ...] = (
    "catalog/smells.py",
    "index/django_post.py",
    "commands/cmd_hover.py",
)


def _scan_text_for_drift(text: str) -> list[tuple[str, str]]:
    # Return (label, snippet) hits inside ``text`` -- skip docs / kwargs.
    #
    # Skip rules (each matches the W543-followup guard style):
    #
    # * Comment lines (``#`` / ``//``) and triple-quote docstring
    #   delimiters at the start of the trimmed line.
    # * Lines where the match sits inside a backtick-quoted RST literal
    #   (the docstring shape ``edges.kind='inherits'`` is rendered with
    #   backticks for cross-reference and is documentation, not a
    #   runtime SQL filter).
    # * Lines that look like a Python kwarg writer (``kind="import",``
    #   trailing comma, no SQL keyword in the surrounding line).
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
            # Skip RST-literal documentation: ``edges.kind='inherits'``
            # rendered with backticks for cross-reference.
            match_before = text[start : m.start()]
            match_after = text[m.end() : end]
            if "``" in match_before and "``" in match_after:
                continue
            # Skip Python kwarg writer shape: a line that has no SQL
            # keyword and ends on a trailing comma is a writer
            # (e.g. ``kind="import",`` inside an ``Edge(...)`` ctor).
            sql_context = any(kw in snippet.upper() for kw in (" WHERE ", "WHERE ", " AND ", "FROM ", "SELECT "))
            if not sql_context and snippet.rstrip().endswith(","):
                continue
            hits.append((label, snippet))
    return hits


@pytest.mark.parametrize("rel_path", _MIGRATED_FILES)
def test_migrated_file_has_no_inline_kind_literal(rel_path: str) -> None:
    """Each round-3 migrated file must source kind-filtering from helpers."""
    py = SRC_ROOT / rel_path
    assert py.exists(), f"W543-followup-C: migrated file missing: {rel_path}"
    text = py.read_text(encoding="utf-8")
    hits = _scan_text_for_drift(text)
    if hits:
        lines = [f"  {label} :: {snippet}" for (label, snippet) in hits]
        pytest.fail(
            f"W543-followup-C: inline kind-literal drift in {rel_path}. "
            "Source the IN-clause from roam.db.edge_kinds.inheritance_in_clause "
            "/ import_in_clause:\n" + "\n".join(lines)
        )
