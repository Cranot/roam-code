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
dogfood `the dogfood synthesis notes`. Unlike the cycles / god-components
chasms, this is NOT a single-algorithm reconciliation — the two
counts measure genuinely different things:

- ``no_underscore_prefix`` is a syntactic check: "what an importer
  would see if they imported from this file".
- ``has_export_marker`` is a semantic check: "what the indexer
  identified as an actual export (``module.exports``, ``export``
  keyword, ``__all__`` membership, etc.)".

The fix is to NAME the criterion in every envelope via a
``public_symbols_inclusion_criterion`` field, so consumers know which
subset they're seeing.
"""

from __future__ import annotations

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
