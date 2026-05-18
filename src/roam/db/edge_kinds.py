"""Canonical edge-kind vocabulary for the ``edges`` table.

This module exists to seal the W493 / W499 / W511 / W522 / W524 silent-no-op
bug family. Every site that filters ``edges.kind`` was previously inlining
its own tuple literal — and a typo on any one of them (``'calls'`` instead
of ``'call'``) became a silent zero-result query. Empty result, no error,
no clue that the filter ran against the wrong vocabulary.

The W493-family fixes added defensive union-both-forms inline at five sites
(taint.py, dataflow.py, cmd_dead.py, critique/checks.py, side_effects.py,
cmd_risk.py, cmd_patterns.py, cmd_hover.py, cmd_oracle.py, cmd_taint.py,
taint_engine.py). W512 (this module) consolidates those literal tuples to
a single named constant so the next site is one import away from being
right by construction.

Canonical writer rule
---------------------
The reference-resolution layer (``src/roam/index/relations.py``) writes
SINGULAR forms: ``kind='call'`` and ``kind='reference'``. The plural
variants (``'calls'`` / ``'references'``) come from plugin extractors and
historical rows. Every reader MUST union both forms; pick the right
constant from this module.

Selection guide
---------------
- ``CALL_EDGE_KINDS`` — pure call edges only. Use when you want the
  function-call graph and nothing else (e.g. ``cmd_patterns`` middleware
  chain detection).
- ``REFERENCE_EDGE_KINDS`` — pure reference edges only. Currently no
  caller exists; provided for symmetry.
- ``CALL_OR_REF_KINDS`` — call + reference edges. Use when you want the
  "anyone uses this symbol" set (callers / blast-radius / taint reach /
  test-coverage oracle). The default choice for most readers.

W543 / W544 promoted two additional shared constants —
:data:`INHERITANCE_EDGE_KINDS` and :data:`IMPORT_EDGE_KINDS` — that
previously lived inline at multiple call sites. The 'imports' plural in
the symbol-level edges table is a *plugin-defensive* alias; the canonical
writer (relations.py) emits ``'import'`` singular and file-level
``'imports'`` lives in the separate ``file_edges`` table.

Phantom-kind audit (W543/W544 sealed):

* ``'extends'`` — appears as an inline literal in
  ``catalog/parallel_hierarchy.py``'s ``INHERITANCE_EDGE_KINDS`` but no
  edge writer ever emits it. Kept as a plugin-defensive alias only;
  documented + tested here so the inline literal in
  ``parallel_hierarchy.py`` reads as a deliberate widening, not a copy
  of canonical truth.
* ``'invokes'`` / ``'uses'`` — extended inline at
  ``world_model/side_effects.py``. Phantom for the in-tree writers;
  kept defensively per the W512 allowlist for plugin extractors that
  may emit them.
* ``'imports'`` — phantom for the symbol-level ``edges`` table; canonical
  for the file-level ``file_edges`` table (relations.py:_build_file_edges).
  ``cmd_hover`` deliberately unions both so the strongest-neighbour
  query catches file-level rows that survived a schema-revision drift.

Sites that mix in additional edge kinds (``'inherits'`` / ``'import'`` /
``'imports'`` for ``cmd_hover``; the ``'invokes'`` / ``'uses'`` phantom
guard in ``side_effects``) extend the relevant constant inline rather
than introducing a new vocabulary — keep the canonical set small and
named.
"""

from __future__ import annotations

# Canonical writer (relations.py) emits singular 'call'; plural 'calls' comes
# from plugin extractors and historical rows. Same pattern for references.
CALL_EDGE_KINDS: tuple[str, ...] = ("call", "calls")
REFERENCE_EDGE_KINDS: tuple[str, ...] = ("reference", "references")

# Anyone-uses-this-symbol set: callers + references. The default choice for
# blast-radius, taint reach, test-coverage oracle, critique impact, etc.
CALL_OR_REF_KINDS: tuple[str, ...] = CALL_EDGE_KINDS + REFERENCE_EDGE_KINDS

# W543 — class-inheritance edge kinds. Every language extractor in
# ``roam.languages`` emits ONE of these three: ``inherits`` (canonical
# parent-class link), ``implements`` (interface / protocol), or
# ``uses_trait`` (PHP traits, Rust ``impl Trait``, generic_lang's
# mixin pattern). The ``'extends'`` literal carried as a phantom alias
# in ``catalog/parallel_hierarchy.py`` is deliberately omitted here:
# no in-tree writer emits it (verified by the W543 drift-test). Sites
# that want plugin-defensive widening can build a local tuple
# ``INHERITANCE_EDGE_KINDS + ('extends',)`` like parallel_hierarchy does.
INHERITANCE_EDGE_KINDS: tuple[str, ...] = ("inherits", "implements", "uses_trait")

# W544 — import edge kinds. Canonical writer (every ``*_lang.py``
# extractor + ``index/relations.py``) emits singular ``'import'`` into
# the symbol-level ``edges`` table. The plural ``'imports'`` is what
# ``index/relations.py:_build_file_edges`` writes into the *file-level*
# ``file_edges`` table — a different table, not a kind variant. The
# plural is included here as a plugin-defensive alias for symbol-level
# queries that historically unioned both (``cmd_hover``); new symbol-
# level call sites should prefer the singular form and parameter-bind
# the tuple via :func:`import_in_clause`.
IMPORT_EDGE_KINDS: tuple[str, ...] = ("import", "imports")


def call_or_ref_in_clause(column: str = "kind") -> str:
    """Return a literal SQL fragment ``"<column> IN ('call', 'calls', 'reference', 'references')"``.

    Use when the surrounding query has no other ``?`` placeholders and a
    literal fragment is the cleanest substitution. For queries that already
    parameterize other values, prefer :func:`call_or_ref_placeholders` so the
    statement stays parameter-bound end to end.
    """
    quoted = ", ".join(f"'{k}'" for k in CALL_OR_REF_KINDS)
    return f"{column} IN ({quoted})"


def call_or_ref_placeholders() -> str:
    """Return ``"?, ?, ?, ?"`` — placeholder string for a parameterised IN clause.

    Pair with :data:`CALL_OR_REF_KINDS` as the params tuple. Use when the
    surrounding query already binds parameters and you want to keep the
    bind-everything discipline.
    """
    return ", ".join("?" for _ in CALL_OR_REF_KINDS)


def inheritance_in_clause(column: str = "kind") -> str:
    """Return ``"<column> IN ('inherits', 'implements', 'uses_trait')"``.

    Use for class-hierarchy queries (parallel-hierarchy, strategy-pattern
    detection, deep-inheritance lint, etc.). Pair with
    :data:`INHERITANCE_EDGE_KINDS` if your query already binds parameters.
    """
    quoted = ", ".join(f"'{k}'" for k in INHERITANCE_EDGE_KINDS)
    return f"{column} IN ({quoted})"


def import_in_clause(column: str = "kind") -> str:
    """Return ``"<column> IN ('import', 'imports')"``.

    Symbol-level import queries should prefer this over the bare
    singular literal. The plural ``'imports'`` is a plugin-defensive
    alias — canonical writers emit singular only — but keeping the
    union inline matches the historical ``cmd_hover`` pattern and is
    safe against plugin extractors that diverge from the canonical
    writer.
    """
    quoted = ", ".join(f"'{k}'" for k in IMPORT_EDGE_KINDS)
    return f"{column} IN ({quoted})"
