"""W543 / W544 drift-guards — canonical inheritance/import edge-kind sets.

W511 / W512 sealed the call/reference edge-kind families against
phantom-kind drift. W543 + W544 extend the same discipline to two
sibling families that previously lived inline at multiple call sites:

* :data:`roam.db.edge_kinds.INHERITANCE_EDGE_KINDS` —
  ``("inherits", "implements", "uses_trait")``. The canonical set
  emitted by every in-tree language extractor. The historical
  inline literal ``("inherits", "extends")`` in
  ``catalog/parallel_hierarchy.py`` carried a phantom ``'extends'``
  alias; no writer ever emits it. This module records the canonical
  truth so future readers don't reintroduce the phantom.

* :data:`roam.db.edge_kinds.IMPORT_EDGE_KINDS` — ``("import", "imports")``.
  Symbol-level ``edges`` rows are canonically singular ``'import'``;
  the plural ``'imports'`` belongs to the *file-level* ``file_edges``
  table (different table, not a kind variant). The plural is kept
  here as a plugin-defensive alias for symbol-level queries that
  historically unioned both forms (``cmd_hover``).

What this test asserts
----------------------

1. The canonical sets are non-empty + include the expected singular
   forms.
2. The two helper IN-clause builders return literal SQL fragments
   that include every member of each tuple.
3. ``rules/builtin.py`` (W543 migration target) imports the
   inheritance helper rather than re-defining the literal tuple.
"""

from __future__ import annotations

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"


def test_canonical_inheritance_set_pins_singular_writers() -> None:
    """INHERITANCE_EDGE_KINDS must include each canonical writer form.

    Every language extractor in ``roam.languages`` emits ONE of these
    three; ``catalog/parallel_hierarchy.py``'s historical inline
    ``("inherits", "extends")`` literal carried a phantom ``'extends'``
    that no writer ever emits.
    """
    from roam.db import edge_kinds

    assert edge_kinds.INHERITANCE_EDGE_KINDS, "W543: INHERITANCE_EDGE_KINDS is empty"
    assert "inherits" in edge_kinds.INHERITANCE_EDGE_KINDS
    assert "implements" in edge_kinds.INHERITANCE_EDGE_KINDS
    assert "uses_trait" in edge_kinds.INHERITANCE_EDGE_KINDS
    # 'extends' MUST NOT be in the canonical set — it's a documented
    # phantom kind (no writer emits it). Plugin-defensive widening
    # belongs at the call site, not in the canonical vocabulary.
    assert "extends" not in edge_kinds.INHERITANCE_EDGE_KINDS, (
        "W543: 'extends' is a phantom kind; do not add it to the canonical set"
    )

    clause = edge_kinds.inheritance_in_clause()
    assert "kind IN" in clause
    for k in edge_kinds.INHERITANCE_EDGE_KINDS:
        assert f"'{k}'" in clause, f"W543: inheritance_in_clause is missing '{k}'"


def test_canonical_import_set_pins_singular_plus_defensive_plural() -> None:
    """IMPORT_EDGE_KINDS unions the canonical singular and plugin-defensive plural.

    The canonical writer (every ``*_lang.py`` extractor plus
    ``index/relations.py``) emits singular ``'import'`` into the
    symbol-level ``edges`` table; the plural lives in the separate
    ``file_edges`` table. The plural alias here is a plugin-defensive
    union — kept so legacy symbol-level queries that wrote
    ``kind IN ('import', 'imports')`` against the symbol table can
    migrate to the helper without losing the union shape.
    """
    from roam.db import edge_kinds

    assert edge_kinds.IMPORT_EDGE_KINDS, "W544: IMPORT_EDGE_KINDS is empty"
    assert "import" in edge_kinds.IMPORT_EDGE_KINDS, "W544: canonical singular 'import' must be in the canonical set"
    assert "imports" in edge_kinds.IMPORT_EDGE_KINDS, (
        "W544: plugin-defensive plural 'imports' must be in the canonical set"
    )
    # Order matters for stable SQL fragment hashes — singular first.
    assert edge_kinds.IMPORT_EDGE_KINDS[0] == "import"

    clause = edge_kinds.import_in_clause()
    assert "kind IN" in clause
    for k in edge_kinds.IMPORT_EDGE_KINDS:
        assert f"'{k}'" in clause, f"W544: import_in_clause is missing '{k}'"


def test_rules_builtin_uses_shared_inheritance_helper() -> None:
    """W543 migrated rules/builtin.py off its inline 3-name tuple.

    The pre-W543 _check_no_deep_inheritance built its IN-clause by
    f-stringing ``("inherits", "implements", "uses_trait")`` inline.
    The migration pulls the clause from :func:`inheritance_in_clause`,
    so a future drop of one of the canonical writers is a one-file
    change in ``db/edge_kinds.py``.
    """
    text = (SRC_ROOT / "rules" / "builtin.py").read_text(encoding="utf-8")
    assert "inheritance_in_clause" in text, (
        "W543: rules/builtin.py should import inheritance_in_clause from roam.db.edge_kinds"
    )
    assert "roam.db.edge_kinds" in text, "W543: rules/builtin.py should import from roam.db.edge_kinds"
