"""Canonical public-symbol count with explicit inclusion criteria.

This is the SINGLE SOURCE OF TRUTH for "how big is the public API
surface?". Before this module existed, two commands disagreed on the
same indexed codebase:

- ``roam api`` reported 3931 symbols. Inclusion = name doesn't start
  with ``_`` AND kind in {function, method, class, interface, enum}
  AND file is not a test.
- ``roam docs-coverage`` reported 1206 symbols. Inclusion = ``is_exported
  = 1`` (indexer's export-marker analysis) AND kind in {function, class,
  method, interface, struct, enum} AND file is not a test.

This is Pattern 3 ("vocabulary mismatch across commands") from the
dogfood ``SYNTHESIS-2026-05-12.md``. Unlike the cycles / god-components
chasms, this is NOT a single-algorithm reconciliation — the two
counts measure genuinely different things:

- ``no_underscore_prefix`` is a syntactic check: "what an importer
  would see if they imported from this file".
- ``has_export_marker`` is a semantic check: "what the indexer
  identified as an actual export (``module.exports``, ``export``
  keyword, ``__all__`` membership, etc.)".

The fix is to NAME the criterion in every envelope via a
``public_symbols_inclusion_criterion`` field, so consumers know which
subset they're seeing. Where appropriate, both counts can be reported
side-by-side via :class:`PublicSymbolsSummary`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kinds considered "public-shape" by the API surface walker. Kept
# minimal — adding kinds here changes both ``api`` and ``docs-coverage``
# numbers, which is a deliberate coupling.
_PUBLIC_KINDS = ("function", "method", "class", "interface", "enum", "struct")

# Inclusion-criterion identifiers — agents grep for these strings, so
# they must be stable. Use the same identifier in every envelope.
CRITERION_NO_UNDERSCORE = "no_underscore_prefix"
CRITERION_HAS_EXPORT_MARKER = "has_export_marker"

DEFINITION = (
    "Public-symbol counts depend on the inclusion criterion. "
    "`no_underscore_prefix`: name does not start with `_` AND kind in "
    "{function, method, class, interface, enum, struct} AND file is not "
    "a test (used by `roam api`). `has_export_marker`: indexer's "
    "`is_exported=1` flag AND same kind/test filter (used by "
    "`roam docs-coverage`). The two counts differ because the former "
    "is syntactic and the latter semantic."
)


def definition() -> str:
    """Return the canonical public-symbols metric definition string."""
    return DEFINITION


@dataclass
class PublicSymbolsSummary:
    """Canonical public-symbol summary across both inclusion criteria.

    Attributes
    ----------
    by_no_underscore : int
        Count under :data:`CRITERION_NO_UNDERSCORE` (the ``api`` rule).
    by_export_marker : int
        Count under :data:`CRITERION_HAS_EXPORT_MARKER` (the
        ``docs-coverage`` rule).
    definition : str
        :data:`DEFINITION` string.
    """

    by_no_underscore: int
    by_export_marker: int
    definition: str = DEFINITION

    def as_envelope_dict(self) -> dict:
        return {
            "by_no_underscore_prefix": self.by_no_underscore,
            "by_export_marker": self.by_export_marker,
            "public_symbols_definition": self.definition,
        }


def public_symbols_summary(conn) -> PublicSymbolsSummary:
    """Compute both canonical public-symbol counts on the indexed DB.

    Both queries share the same kind list + test-file exclusion. The
    difference is the inclusion rule (underscore-prefix vs
    ``is_exported``), which is the chasm we are documenting.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open roam DB connection (readonly is fine).

    Returns
    -------
    PublicSymbolsSummary
        Both counts side-by-side, plus the definition string.
    """
    placeholders = ",".join("?" for _ in _PUBLIC_KINDS)
    kinds = list(_PUBLIC_KINDS)

    # CRITERION_NO_UNDERSCORE — what `roam api` reports.
    no_us_sql = (
        "SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name NOT LIKE '\\_%' ESCAPE '\\' "
        f"AND s.kind IN ({placeholders}) "
        "AND COALESCE(f.file_role, 'source') NOT IN ('test', 'tests') "
        "AND f.path NOT LIKE 'tests/%'"
    )
    try:
        n_no_us = conn.execute(no_us_sql, kinds).fetchone()[0]
    except Exception:
        n_no_us = 0

    # CRITERION_HAS_EXPORT_MARKER — what `roam docs-coverage` reports.
    exp_sql = (
        "SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE s.kind IN ({placeholders}) "
        "AND s.is_exported = 1 "
        "AND f.path NOT LIKE '%/tests/%' "
        "AND f.path NOT LIKE '%/test/%' "
        "AND f.path NOT LIKE '%test\\_%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\'"
    )
    try:
        n_exp = conn.execute(exp_sql, kinds).fetchone()[0]
    except Exception:
        n_exp = 0

    return PublicSymbolsSummary(
        by_no_underscore=n_no_us,
        by_export_marker=n_exp,
    )
