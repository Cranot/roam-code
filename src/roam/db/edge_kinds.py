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
