"""Normalise and safely delegate symbol/reference extraction to language-specific extractors.

This module is the isolation boundary between tree-sitter language extractors
and the indexer: it guarantees every emitted symbol and reference dict carries
the canonical key set, coerces extractor-specific quirks (``is_async``,
``decorators``, fallback ``qualified_name``), and swallows extractor failures so
one broken language plugin cannot abort the whole indexing run.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

log = logging.getLogger(__name__)


class _Extractor(Protocol):
    def extract_symbols(self, tree: object, source: bytes, file_path: str) -> list[dict[str, object]]: ...
    def extract_references(self, tree: object, source: bytes, file_path: str) -> list[dict[str, object]]: ...


# Normalisation defaults for the canonical symbol schema. Keeping them in one
# place makes the contract between language extractors and the indexer explicit
# and easier to extend (e.g. when Python pivot v12.4 added ``is_async`` and
# ``decorators``).
_SYMBOL_DEFAULTS = {
    "name": "",
    "qualified_name": "",
    "kind": "unknown",
    "signature": None,
    "line_start": None,
    "line_end": None,
    "docstring": None,
    "visibility": "public",
    "is_exported": True,
    "parent_name": None,
    "default_value": None,
    "is_async": False,
    "decorators": "",
}


# Normalisation defaults for the canonical reference schema.
_REFERENCE_DEFAULTS = {
    "source_name": "",
    "target_name": "",
    "kind": "call",
    "line": None,
    "import_path": None,
}


def _normalize_symbol(sym: dict) -> dict | None:
    """Return a symbol dict guaranteed to contain every canonical key.

    _SYMBOL_DEFAULTS is the single source of truth for all keys.  Three
    fields have semantics that a plain .get(k, default) cannot capture:
      qualified_name — falls back to `name`, not the empty-string default
      is_async       — coerced to bool (extractor may return 0/1/None)
      decorators     — any falsy value (None, "") normalises to ""

    Returns None if the symbol is malformed (missing required fields).
    Required fields: name, line_start. These are critical:
      - name: empty/None breaks symbol lookup
      - line_start: missing breaks containment-based features (_containing_symbol.py)
    """
    # Validate required fields before building the full result dict so that
    # rejected symbols pay only two dict.get() calls instead of 13+.
    name = sym.get("name", "")
    if isinstance(name, str):
        name = name.strip()
    if not name:
        return None

    line_start = sym.get("line_start")
    if line_start is None or not isinstance(line_start, int):
        return None

    result = {k: sym.get(k, v) for k, v in _SYMBOL_DEFAULTS.items()}
    result["name"] = name  # already stripped; overrides the raw comprehension value

    # Read decorators from the already-built result dict (no second sym.get() needed).
    decorators = result["decorators"]
    if decorators is not None and not isinstance(decorators, str):
        decorators = ""
    result["decorators"] = decorators or ""

    result["qualified_name"] = sym.get("qualified_name", name)
    result["is_async"] = bool(result["is_async"])
    return result


def _normalize_reference(ref: dict) -> dict | None:
    """Return a reference dict guaranteed to contain every canonical key.

    Returns None if the reference is malformed.
    Required fields: target_name, line. These are critical:
      - target_name: empty/None has no edge target → cannot build an edge
      - line: missing breaks reference resolution (_relations.py)

    ``source_name`` is OPTIONAL: an empty/None source denotes a module-scope /
    top-level reference (FoxPro file-level ``DO``/``SET``, Vue ``<script setup>``,
    Python module scope). ``relations.py`` resolves these via its top-level-code
    fallback (relations.py:483/514), so they MUST NOT be dropped here — normalise
    to "" and let resolution attribute them to the file's module symbol.
    """
    source_name = ref.get("source_name", "") or ""
    source_name = source_name.strip() if isinstance(source_name, str) else ""

    target_name = ref.get("target_name", "")
    if isinstance(target_name, str):
        target_name = target_name.strip()
    if not target_name:
        return None

    line = ref.get("line")
    if line is None or not isinstance(line, int):
        return None

    # Build the result directly with already-validated values; avoids a 5-key
    # comprehension that would fetch source_name, target_name, and line a second
    # time only to immediately override them.
    return {
        "source_name": source_name,
        "target_name": target_name,
        "kind": ref.get("kind", "call"),
        "line": line,
        "import_path": ref.get("import_path"),
    }


def _safe_extract_normalized(
    label: str,
    file_path: str,
    extractor: object,
    extract_fn: Callable[[], list[dict]],
    normalize_fn: Callable[[dict], dict | None],
) -> list[dict]:
    """Run ``extract_fn`` and return a normalised list.

    Swallows extractor exceptions so a bug in one language plugin cannot crash
    the whole indexing run. Logs the failure so zero output stays observable
    (Pattern-2 lineage: never silently produce empty results).

    Normalizers can return None to filter out malformed items; skipped items
    are logged as warnings so degradation is visible.
    """
    if extractor is None:
        return []
    try:
        raw = extract_fn()
    except Exception as exc:  # noqa: BLE001 - intentional isolation boundary
        log.warning(
            "%s failed for %s (%s: %s); emitting empty list",
            label,
            file_path,
            type(exc).__name__,
            exc,
        )
        return []

    normalised = []
    for i, item in enumerate(raw):
        normalized = normalize_fn(item)
        if normalized is None:
            # Per-item skip is benign and high-volume (tens of thousands on a large
            # repo) — keep it at DEBUG so `roam init` does not look like a crash on a
            # newcomer's first command. The per-file extractor failure above stays a
            # warning (that one is rare and actionable).
            log.debug(
                "%s: item %d in %s malformed; skipping",
                label,
                i,
                file_path,
            )
        else:
            normalised.append(normalized)
    return normalised


def extract_symbols(tree: object, source: bytes, file_path: str, extractor: _Extractor | None) -> list[dict]:
    """Extract symbol definitions from a parsed AST.

    Uses a language-specific extractor that implements:
        extractor.extract_symbols(tree, source, file_path) -> list[dict]

    Each returned dict has:
        name, qualified_name, kind, signature, line_start, line_end,
        docstring, visibility, is_exported, parent_name
    """
    if tree is None and source is None:
        return []
    if extractor is None:
        return []
    return _safe_extract_normalized(
        "extract_symbols",
        file_path,
        extractor,
        lambda: extractor.extract_symbols(tree, source, file_path),
        _normalize_symbol,
    )


def extract_references(tree: object, source: bytes, file_path: str, extractor: _Extractor | None) -> list[dict]:
    """Extract references (calls, imports) from a parsed AST.

    Uses a language-specific extractor that implements:
        extractor.extract_references(tree, source, file_path) -> list[dict]

    Each returned dict has:
        source_name, target_name, kind, line, import_path
    """
    if tree is None and source is None:
        return []
    if extractor is None:
        return []
    return _safe_extract_normalized(
        "extract_references",
        file_path,
        extractor,
        lambda: extractor.extract_references(tree, source, file_path),
        _normalize_reference,
    )
