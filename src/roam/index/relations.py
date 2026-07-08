"""Import and call resolution into graph edges."""

from __future__ import annotations

import os
import re
from collections.abc import Callable

# ---------------------------------------------------------------------------
# W167 — import-edge text verification
# ---------------------------------------------------------------------------
#
# The resolver below maps unresolved import names to whichever indexed
# symbol shares the same simple name. When the imported module is a
# third-party / stdlib package that isn't itself indexed (``yaml``,
# ``time``, ``timezone``...), the resolver falls back to a local symbol
# of the same name — often a variable inside a test file. The result is
# a ``kind='import'`` edge that does not correspond to any real import
# statement in the source file. W158 caught the worst class of these
# (non-test -> test) at the laws-miner layer; W167 fixes the upstream
# generator by verifying every ``kind='import'`` edge against the raw
# source text before it lands in the database.
#
# The helpers below extract the *set of imported names* from a source
# file by scanning import statements directly. Multi-line ``from X
# import (a, b, c)`` blocks are handled. Docstrings and ``#`` comments
# are masked so prose like ``... should import yaml ...`` inside a
# docstring cannot fake an import. Non-Python languages are handled by
# generalising the keyword set (``import``, ``from ... import``,
# ``use``, ``require``, ``#include``) and treating any token following
# one of those keywords (on the same line or inside an immediately
# trailing ``(...)`` / ``{...}`` block) as an "imported name".

# Recognises any line whose first non-whitespace token is one of the import
# keywords we support across languages. We match an opening ``(`` / ``{``
# explicitly on the same line so the block-scanner can pick up multi-line
# import bodies.
_RX_IMPORT_LINE = re.compile(
    r"^[ \t]*"
    r"(?:"
    r"import\b"  # python / java / scala / kotlin / typescript / swift
    r"|from\s+\S+\s+import\b"  # python
    r"|use\b"  # rust / php / scala
    r"|using\b"  # c# / c++
    r"|require\b"  # ruby / php / node
    r"|require_relative\b"
    r"|include\b"  # ruby / php / perl
    r"|#\s*include\b"  # c / c++
    r"|@import\b"  # objective-c / css
    r"|package\b"  # go
    r")"
    r"([^\n]*)",
    re.MULTILINE,
)

# Capture token-like names within the body of an import line/block.
_RX_NAME_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IMPORT_NAME_SKIP_TOKENS = frozenset(
    {
        "as",
        "from",
        "import",
        "use",
        "using",
        "require",
        "require_relative",
        "include",
        "package",
    }
)


def _scan_line_comment(text: str, start: int, n: int) -> tuple[str, int]:
    """Consume a line comment starting at ``start`` and return its masked segment.

    The segment preserves newlines so downstream line-based logic stays
    intact.  Isolating this rule lets the main loop decide *whether* a
    line comment starts without also encoding how to find its end.
    """
    j = text.find("\n", start)
    if j == -1:
        j = n
    return " " * (j - start), j


def _scan_block_comment(text: str, start: int, n: int) -> tuple[str, int]:
    """Consume a ``/* ... */`` block comment and return its masked segment."""
    j = text.find("*/", start + 2)
    if j == -1:
        j = n
    else:
        j += 2
    return "".join(c if c == "\n" else " " for c in text[start:j]), j


def _scan_triple_quoted_string(text: str, start: int, n: int, quote: str) -> tuple[str, int]:
    """Consume a triple-quoted string and return its masked segment."""
    triple = quote * 3
    j = text.find(triple, start + 3)
    if j == -1:
        j = n
    else:
        j += 3
    return "".join(c if c == "\n" else " " for c in text[start:j]), j


def _scan_single_line_string(text: str, start: int, n: int, quote: str) -> tuple[str, int]:
    """Consume a single-line string/char/template literal with escapes."""
    j = start + 1
    while j < n and text[j] != quote and text[j] != "\n":
        if text[j] == "\\" and j + 1 < n:
            j += 2
        else:
            j += 1
    if j < n and text[j] == quote:
        j += 1
    return "".join(c if c == "\n" else " " for c in text[start:j]), j


def _lexical_construct_at(text: str, i: int, n: int) -> tuple[str, str]:
    """Return the lexical construct starting at ``i`` and its quote char.

    Centralising the lookahead rules keeps the main masking loop a plain
    dispatcher: it decides *that* a construct starts here, not *how* to
    recognise it.
    """
    ch = text[i]
    nxt = text[i + 1] if i + 1 < n else ""
    nxt2 = text[i + 2] if i + 2 < n else ""
    if ch == "#" or (ch == "/" and nxt == "/"):
        return "line_comment", ""
    if ch == "/" and nxt == "*":
        return "block_comment", ""
    if ch == '"' and nxt == '"' and nxt2 == '"':
        return "triple_string", '"'
    if ch == "'" and nxt == "'" and nxt2 == "'":
        return "triple_string", "'"
    if ch == '"' or ch == "'" or ch == "`":
        return "single_string", ch
    return "normal", ""


def _mask_strings_and_comments(text: str) -> str:
    # A regex-based masker is too brittle for the cases we care about: a
    # single-line Python string that embeds two consecutive triple-quote
    # tokens (an actual case seen in tests/test_python_pivot.py around
    # the generated-file fixtures) confuses a non-greedy "..."[\s\S]*?"..."
    # match into spanning across legitimate code.  We use a small forward
    # state machine instead.  At each character we are in exactly one of:
    #
    #   * normal code
    #   * inside a ``#`` line comment (python / shell / ruby)
    #   * inside a ``//`` line comment (c / cpp / java / js)
    #   * inside a ``/* ... */`` block comment
    #   * inside a single-line string ``"..."`` / ``'...'`` / ``\`...\```
    #   * inside a triple-quoted string (``"""..."""`` or ``'''...'''``)
    #
    # Within a single-line string we honour ``\\`` escapes so ``"a\\"b"``
    # stays a single string.  Backslash-newline (Python line continuation
    # inside a string) is treated as escape, the typical case.
    #
    # The output has the same length and line layout as ``text``: masked
    # regions are filled with spaces, newlines preserved.
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        kind, quote = _lexical_construct_at(text, i, n)
        if kind == "line_comment":
            segment, i = _scan_line_comment(text, i, n)
        elif kind == "block_comment":
            segment, i = _scan_block_comment(text, i, n)
        elif kind == "triple_string":
            segment, i = _scan_triple_quoted_string(text, i, n, quote)
        elif kind == "single_string":
            segment, i = _scan_single_line_string(text, i, n, quote)
        else:
            out.append(text[i])
            i += 1
            continue
        out.append(segment)
    return "".join(out)


def _import_block_span_for_phantom_guard(
    masked: str,
    match: re.Match[str],
    text_len: int,
    hoisted_find_in_masked: Callable[[str, int], int],
) -> tuple[str, int]:
    """Return the import span that balances broad scanning with phantom rejection."""
    rest = match.group(1) or ""
    line_end = hoisted_find_in_masked("\n", match.end())
    if line_end == -1:
        line_end = text_len

    block_text = rest
    cursor = match.end()
    if "(" in rest and ")" not in rest:
        close = hoisted_find_in_masked(")", cursor)
        if close != -1:
            block_text = masked[match.start(1) : close]
            cursor = close + 1
    elif "{" in rest and "}" not in rest:
        close = hoisted_find_in_masked("}", cursor)
        if close != -1:
            block_text = masked[match.start(1) : close]
            cursor = close + 1
    else:
        cursor = line_end
    return block_text, cursor


def _extract_imported_names(source_text: str) -> set[str]:
    """Return the set of names that ``source_text`` imports.

    A name is considered "imported" if it appears as a token on a line
    whose first keyword is one of the cross-language import keywords
    handled by :data:`_RX_IMPORT_LINE`, or inside a ``(...)`` / ``{...}``
    block opened on such a line. Multi-line ``from X import (a, b)`` and
    Go-style ``import ( ... )`` blocks are handled by the bracket-scanner.

    The set is deliberately permissive — false positives here only mean
    a phantom import edge slips through verification, never that a real
    import is dropped. Conversely, the set is the conservative side of
    the W167 fix: any name NOT in this set is guaranteed to not be a
    written import in ``source_text``.
    """
    if not source_text:
        return set()
    masked = _mask_strings_and_comments(source_text)
    names: set[str] = set()
    pos = 0
    text_len = len(masked)
    hoisted_find_in_masked = masked.find
    hoisted_name_token_findall = _RX_NAME_TOKEN.findall
    for match in _RX_IMPORT_LINE.finditer(masked):
        if match.start() < pos:
            continue  # inside a previously-scanned block
        # Track an open ``(`` / ``{`` so we can pick up multi-line imports.
        block_text, cursor = _import_block_span_for_phantom_guard(
            masked,
            match,
            text_len,
            hoisted_find_in_masked,
        )
        for token in hoisted_name_token_findall(block_text):
            if token in _IMPORT_NAME_SKIP_TOKENS:
                continue
            names.add(token)
        pos = cursor
    return names


def _verify_import_edges(
    edges: list[dict],
    imported_names_by_file: dict[str, set[str]],
    drop_counter: dict[str, int],
) -> list[dict]:
    """Drop ``kind='import'`` edges whose target name does not appear in the
    source file's actual import statements.

    ``edges`` is the in-place list of edge dicts produced by
    :func:`resolve_references` augmented with a transient ``_target_name``
    field (the ref's pre-resolution name). ``imported_names_by_file`` is a
    pre-computed cache of imported-name sets keyed by relative source path.
    ``drop_counter`` is mutated to expose ``dropped_import_edges`` for
    diagnostic reporting (read by the indexer log path; safe to ignore).

    Non-import edges and edges whose source file we could not read are
    passed through unchanged. The transient ``_target_name`` key is
    stripped before returning.
    """
    verified: list[dict] = []
    dropped = 0
    for edge in edges:
        kind = edge.get("kind", "")
        target_name = edge.pop("_target_name", None)
        if kind != "import" or not target_name:
            verified.append(edge)
            continue
        source_path = edge.pop("_source_path", "")
        names = imported_names_by_file.get(source_path)
        if names is None:
            # No text for the source file (unreadable / not on disk):
            # err on the side of keeping the edge. Better one stray
            # phantom than dropping a real import.
            verified.append(edge)
            continue
        if target_name in names:
            verified.append(edge)
        else:
            dropped += 1
    # Additive (not overwriting): the counter key may have been
    # touched by a previous resolve_references call that shared the
    # same drop_stats dict. W167 is the sole writer of this key —
    # W181's pre-filter in ``_resolve_standard`` deliberately does
    # NOT increment it (W1260): "dropped" means "edge was emitted
    # then dropped", and W181 rejects candidates pre-emission.
    drop_counter["dropped_import_edges"] = drop_counter.get("dropped_import_edges", 0) + dropped
    return verified


def _read_source_text(rel_path: str, project_root: str | None) -> str | None:
    """Read a source file relative to ``project_root`` (or CWD if None).

    Returns ``None`` if the read fails for any reason — callers treat
    "no source text" as "skip verification for this file".
    """
    if not rel_path:
        return None
    base = project_root or os.getcwd()
    full = os.path.join(base, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


# Path-priority weights for cross-file symbol resolution. When the same
# function name is defined in both a dev/ helper script and the canonical
# src/ library, calls should resolve to the canonical definition. Higher
# scores win; negative scores penalise.
_PATH_SCORE_RULES = (
    ("src/", 3),
    ("/src/", 3),
    ("lib/", 3),
    ("/lib/", 3),
    ("/dev/", -2),
    ("/scripts/", -2),
    ("/examples/", -2),
    ("/tests/", -1),
    ("/test/", -1),
    ("dev/", -2),
    ("scripts/", -2),
    ("examples/", -2),
    ("tests/", -1),
    ("test/", -1),
)


def _path_score(path: str) -> int:
    """Return a canonical-path weight for tie-breaking ambiguous symbol
    resolution. Higher = more canonical."""
    if not path:
        return 0
    p = path.replace("\\", "/")
    score = 0
    for needle, weight in _PATH_SCORE_RULES:
        if needle in p:
            score += weight
    return score


# ---------------------------------------------------------------------------
# W181 — import-target shape preference
# ---------------------------------------------------------------------------
#
# W158 dropped non-test -> test "import" edges at the laws-miner layer.
# W167 added a text-check at the resolver layer (drop kind='import' edges
# whose target name isn't written as an import in the source text). W181
# is the *upstream* fix: when resolving an ``import X`` reference, the
# fuzzy-name lookup must not pick a symbol whose kind is *intrinsically
# unable* to be an import target. Local variables, function parameters,
# block-local bindings, and instance properties are never the legitimate
# target of ``import X`` / ``from Y import X`` — when the resolver lands
# on one of those, it has fabricated an edge from name-collision noise.
#
# The cleanest fix is to PRE-FILTER the candidate set used for
# ``kind='import'`` resolution. If the filter empties the set, the
# resolver returns ``None`` and no edge is emitted (better than a
# phantom). This is strictly narrower than the W167 text-check: text
# verification still catches edges where the symbol IS a valid import
# target shape but the source file doesn't actually contain that
# import statement (a different bug class — e.g. resolver case-folding
# ``time`` to a ``TIME`` constant). Both layers are kept as
# defence-in-depth; W181 prevents the bug, W167 catches it if a
# language extractor regresses, W158 catches it at the law layer.
#
# Symbol kinds that are NEVER legitimate import targets. The Python
# extractor emits ``variable`` for module-level bindings (which CAN be
# imported — ``from foo import SOME_VAR`` works), so we deliberately
# DO NOT blanket-drop ``variable`` here. We only drop variables/
# parameters/locals whose file_path is a *test* file: real import
# targets do not live inside test modules.
#
# The "property" kind is always demoted: a class property is never an
# import target. The "local"/"parameter" kinds (if any extractor emits
# them) are also always demoted.
_IMPORT_TARGET_DEMOTE_KINDS = frozenset({"local", "parameter", "property"})


def _is_test_path(path: str) -> bool:
    """Cheap, intentionally narrow test-path check used at the indexer
    layer to guard against test-module variables being picked as
    import targets from non-test source.

    W928 verification (2026-05-15): the W873-era comment claimed this
    helper "deliberately avoids the roam.commands import cycle" — that
    claim was wrong. The transitive import set of
    ``roam.commands.changed_files`` is
    ``{roam.git_utils, roam.index.file_roles, roam.index.test_conventions}``
    and never reaches ``roam.index.relations``. No cycle exists.

    The helper is preserved as-is because its classification is
    deliberately narrower than
    :func:`roam.commands.changed_files.is_test_file`:

    * directory-only (``tests/``, ``test/``); no ``spec/`` /
      ``__tests__/``;
    * no basename heuristics (``test_*``, ``*_test.go``,
      ``conftest.py``, ``*.test.*``, ``*.spec.*``);
    * lower-cases input so e.g. uppercase ``TESTS/`` is treated as a
      test path on case-sensitive filesystems.

    Broadening this to ``is_test_file`` would change which variables
    survive :func:`_filter_import_candidates` (e.g. dropping a
    module-level constant exported from ``conftest.py`` into ``src/``
    imports) and reshape resolved import edges across the index. If
    the broader classification is ever wanted, that is a behavioural
    change to make deliberately with a dedicated reindex audit.
    """
    if not path:
        return False
    p = path.replace("\\", "/").lower()
    return "/tests/" in p or "/test/" in p or p.startswith("tests/") or p.startswith("test/")


def _filter_import_candidates(
    candidates: list[dict],
    source_file: str,
) -> list[dict]:
    """Return only candidates that can plausibly be ``import X`` targets.

    Rules:
    - Drop any candidate whose ``kind`` is in
      :data:`_IMPORT_TARGET_DEMOTE_KINDS` (``local``, ``parameter``,
      ``property``).
    - Drop ``variable`` candidates that live in a test file unless the
      *source* file is also a test file. A module-level constant in
      tests/ is never legitimately imported from src/.
    - If the rules empty the set, the caller emits NO edge — better
      than a phantom.
    """
    if not candidates:
        return candidates
    source_is_test = _is_test_path(source_file)
    out: list[dict] = []
    for cand in candidates:
        kind = (cand.get("kind") or "").lower()
        if kind in _IMPORT_TARGET_DEMOTE_KINDS:
            continue
        if kind == "variable" and not source_is_test:
            cand_path = cand.get("file_path") or ""
            if _is_test_path(cand_path):
                continue
        out.append(cand)
    return out


def _filter_symbols_for_import(
    symbols_by_name: dict[str, list[dict]],
    target_name: str,
    source_file: str,
) -> dict[str, list[dict]]:
    """Return a per-call view of ``symbols_by_name`` where the candidate
    list for ``target_name`` is filtered by :func:`_filter_import_candidates`.

    The returned dict is a shallow copy that overrides ONLY the
    ``target_name`` entry — every other key still aliases the original
    list. This lets us reuse :func:`_best_match` without rewriting the
    candidate-ranking logic for the import path.
    """
    original = symbols_by_name.get(target_name, [])
    filtered = _filter_import_candidates(original, source_file)
    if filtered is original:
        return symbols_by_name
    view = dict(symbols_by_name)
    view[target_name] = filtered
    return view


def _source_context_for_target_disambiguation(
    source_name: str,
    source_file: str,
    line: int | None,
    kind: str,
    symbols_by_name: dict[str, list[dict]],
    file_symbols: dict[str, list[dict]],
) -> tuple[dict, str] | None:
    """Resolve the caller context that target disambiguation reuses."""
    source_sym = _best_match(source_name, source_file, symbols_by_name)
    if source_sym is None:
        # Fallback for top-level code (e.g. Vue <script setup>, Python module scope):
        # pick the closest symbol at or before the reference line.
        # W742: for kind='import', _closest_symbol skips the syms[0]
        # fallback so module-scope imports don't mis-attribute to
        # whichever function happens to be first in the file.
        source_sym = _closest_symbol(source_file, line, file_symbols, kind=kind)
    if source_sym is None:
        return None

    # Extract parent context from source for same-file disambiguation
    # e.g. MyStruct::some_method -> parent = "MyStruct"
    source_parent = ""
    src_qn = source_sym.get("qualified_name", "")
    if "::" in src_qn:
        source_parent = src_qn.rsplit("::", 1)[0]
    elif "." in src_qn:
        source_parent = src_qn.rsplit(".", 1)[0]
    return source_sym, source_parent


_SALESFORCE_NAME_KINDS = frozenset({"controller", "soql", "metadata_ref", "component_ref"})


def _source_context_key_for_precise_reuse(ref: dict) -> tuple[str, str, int | None, str]:
    """Name the source-context identity so precision and reuse stay coupled."""
    return (
        ref.get("source_name", ""),
        ref.get("source_file", ""),
        ref.get("line"),
        ref.get("kind", "call"),
    )


def _precompute_contexts_for_precise_reuse(
    references: list[dict],
    symbols_by_name: dict[str, list[dict]],
    file_symbols: dict[str, list[dict]],
) -> dict[tuple[str, str, int | None, str], tuple[dict, str] | None]:
    """Resolve each distinct caller context once without weakening line precision."""
    hoisted_source_contexts: dict[
        tuple[str, str, int | None, str],
        tuple[dict, str] | None,
    ] = {}
    for ref in references:
        if not ref.get("target_name"):
            continue
        key = _source_context_key_for_precise_reuse(ref)
        if key in hoisted_source_contexts:
            continue
        source_name, source_file, line, kind = key
        hoisted_source_contexts[key] = _source_context_for_target_disambiguation(
            source_name,
            source_file,
            line,
            kind,
            symbols_by_name,
            file_symbols,
        )
    return hoisted_source_contexts


def _precompute_salesforce_targets_for_precise_reuse(
    references: list[dict],
    symbols_by_name: dict[str, list[dict]],
    symbols_by_qualified: dict[str, list[dict]],
    sf_file_priority: dict[str, int],
) -> tuple[dict[str, dict | None], dict[tuple[str, str], dict | None]]:
    """Resolve repeated Salesforce targets once while preserving target-kind precision."""
    hoisted_salesforce_imports: dict[str, dict | None] = {}
    hoisted_salesforce_names: dict[tuple[str, str], dict | None] = {}

    for ref in references:
        import_path = ref.get("import_path", "")
        if import_path and import_path.startswith("@salesforce/") and import_path not in hoisted_salesforce_imports:
            hoisted_salesforce_imports[import_path] = _resolve_salesforce_import(
                import_path,
                symbols_by_name,
                symbols_by_qualified,
            )

        kind = ref.get("kind", "call")
        if kind not in _SALESFORCE_NAME_KINDS:
            continue
        target_name = ref.get("target_name", "")
        if not target_name:
            continue
        sf_key = (target_name, kind)
        if sf_key in hoisted_salesforce_names:
            continue
        hoisted_salesforce_names[sf_key] = _resolve_salesforce_name(
            target_name,
            kind,
            symbols_by_name,
            sf_file_priority,
        )

    return hoisted_salesforce_imports, hoisted_salesforce_names


def _precompute_imported_names_for_precise_reuse(
    edges: list[dict],
    project_root: str | None,
) -> dict[str, set[str]]:
    """Read each import-source file once and extract its imported names.

    Trades memory (the per-file imported-name sets) for I/O time:
    import edges sharing a source file reuse one read/parse instead of
    calling ``_read_source_text`` / ``_extract_imported_names`` per edge.
    """
    imported_names_by_file: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("kind") != "import":
            continue
        src_path = edge.get("_source_path", "")
        if not src_path or src_path in imported_names_by_file:
            continue
        text = _read_source_text(src_path, project_root)
        if text is None:
            continue
        imported_names_by_file[src_path] = _extract_imported_names(text)
    return imported_names_by_file


def _build_qualified_symbols_for_exact_targets(symbols_by_name: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Group qualified names so exact target identity survives name collisions."""
    symbols_by_qualified: dict[str, list[dict]] = {}
    for sym_list in symbols_by_name.values():
        for sym in sym_list:
            qn = sym.get("qualified_name")
            if qn:
                symbols_by_qualified.setdefault(qn, []).append(sym)
    return symbols_by_qualified


def _build_casefolded_symbols_for_language_fallback(symbols_by_name: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Index lower-case names so case-insensitive languages keep resolving."""
    symbols_by_name_lower: dict[str, list[dict]] = {}
    for name, sym_list in symbols_by_name.items():
        lower = name.lower()
        if lower != name:  # Only add if case differs to save memory
            symbols_by_name_lower.setdefault(lower, []).extend(sym_list)
        # Always add lowercase key so lookups work
        if lower not in symbols_by_name_lower:
            symbols_by_name_lower[lower] = sym_list
    return symbols_by_name_lower


def _build_import_paths_for_target_disambiguation(references: list[dict]) -> dict[tuple[str, str], str]:
    """Map written imports so ambiguous names prefer the imported path."""
    import_map: dict[tuple[str, str], str] = {}
    for ref in references:
        if ref.get("kind") == "import" and ref.get("import_path"):
            key = (ref.get("source_file", ""), ref.get("target_name", ""))
            if key[0] and key[1]:
                import_map[key] = ref["import_path"]
    return import_map


def _build_file_symbols_for_scope_fallback(symbols_by_name: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Sort symbols by file/line so module-scope refs keep nearest scope."""
    file_symbols: dict[str, list[dict]] = {}
    for sym_list in symbols_by_name.values():
        for sym in sym_list:
            fp = sym.get("file_path", "")
            if fp:
                file_symbols.setdefault(fp, []).append(sym)
    for fp in file_symbols:
        file_symbols[fp].sort(key=lambda s: s.get("line_start") or 0)
    return file_symbols


def _target_for_reference_preserving_specialized_precedence(
    ref: dict,
    source_parent: str,
    hoisted_salesforce_imports: dict[str, dict | None],
    hoisted_salesforce_names: dict[tuple[str, str], dict | None],
    hoisted_standard_targets: dict[tuple[str, str, str, str], dict | None],
) -> dict | None:
    """Pick a target while preserving Salesforce-first resolver precedence."""
    target_name = ref.get("target_name", "")
    source_file = ref.get("source_file", "")
    kind = ref.get("kind", "call")

    import_path = ref.get("import_path", "")
    target_sym = None
    if import_path and import_path.startswith("@salesforce/"):
        target_sym = hoisted_salesforce_imports.get(import_path)
    elif kind in _SALESFORCE_NAME_KINDS:
        target_sym = hoisted_salesforce_names.get((target_name, kind))

    if target_sym is None:
        std_key = (target_name, source_file, source_parent, kind)
        target_sym = hoisted_standard_targets.get(std_key)
    return target_sym


def _edge_record_preserving_import_verification_metadata(
    ref: dict,
    source_id,
    target_id,
    files_by_path: dict[str, int],
) -> dict:
    """Keep import verification evidence attached only until W167 consumes it."""
    target_name = ref.get("target_name", "")
    kind = ref.get("kind", "call")
    source_file = ref.get("source_file", "")

    edge_record: dict = {
        "source_id": source_id,
        "target_id": target_id,
        "kind": kind,
        "line": ref.get("line"),
        "source_file_id": files_by_path.get(source_file),
    }
    if kind == "import":
        # Stash the pre-resolution target name and source path on the
        # edge so the W167 verification pass can text-check them
        # against the actual import statements in the source file.
        # Both keys are popped by ``_verify_import_edges`` before
        # the edge is returned to the caller.
        edge_record["_target_name"] = target_name
        edge_record["_source_path"] = source_file
    return edge_record


def _edge_for_reference_preserving_context_precision(
    ref: dict,
    files_by_path: dict[str, int],
    hoisted_source_contexts: dict[tuple[str, str, int | None, str], tuple[dict, str] | None],
    hoisted_salesforce_imports: dict[str, dict | None],
    hoisted_salesforce_names: dict[tuple[str, str], dict | None],
    hoisted_standard_targets: dict[tuple[str, str, str, str], dict | None],
    seen: set[tuple[object, object, str]],
) -> dict | None:
    """Resolve one ref only when its context, target, and identity agree."""
    target_name = ref.get("target_name", "")
    if not target_name:
        return None

    source_context_key = _source_context_key_for_precise_reuse(ref)
    source_context = hoisted_source_contexts.get(source_context_key)
    if source_context is None:
        return None
    source_sym, source_parent = source_context

    target_sym = _target_for_reference_preserving_specialized_precedence(
        ref,
        source_parent,
        hoisted_salesforce_imports,
        hoisted_salesforce_names,
        hoisted_standard_targets,
    )
    if target_sym is None:
        return None

    source_id = source_sym["id"]
    target_id = target_sym["id"]
    if source_id == target_id:
        return None

    kind = ref.get("kind", "call")
    edge_key = (source_id, target_id, kind)
    if edge_key in seen:
        return None
    seen.add(edge_key)

    return _edge_record_preserving_import_verification_metadata(
        ref,
        source_id,
        target_id,
        files_by_path,
    )


def _edges_for_references_preserving_context_precision(
    references: list[dict],
    files_by_path: dict[str, int],
    hoisted_source_contexts: dict[tuple[str, str, int | None, str], tuple[dict, str] | None],
    hoisted_salesforce_imports: dict[str, dict | None],
    hoisted_salesforce_names: dict[tuple[str, str], dict | None],
    hoisted_standard_targets: dict[tuple[str, str, str, str], dict | None],
) -> list[dict]:
    """Emit unique edges only after each ref reuses its exact source context."""
    edges: list[dict] = []
    seen: set[tuple[object, object, str]] = set()

    for ref in references:
        edge_record = _edge_for_reference_preserving_context_precision(
            ref,
            files_by_path,
            hoisted_source_contexts,
            hoisted_salesforce_imports,
            hoisted_salesforce_names,
            hoisted_standard_targets,
            seen,
        )
        if edge_record is not None:
            edges.append(edge_record)
    return edges


def resolve_references(
    references: list[dict],
    symbols_by_name: dict[str, list[dict]],
    files_by_path: dict[str, int],
    project_root: str | None = None,
    drop_stats: dict[str, int] | None = None,
) -> list[dict]:
    """Resolve references to concrete symbol edges.

    Args:
        references: List of reference dicts with source_name, target_name, kind, line.
        symbols_by_name: Mapping from symbol name -> list of symbol dicts
            (each with at least 'id', 'file_id', 'file_path', 'qualified_name').
        files_by_path: Mapping from file path -> file_id.
        project_root: Optional absolute project-root path used to resolve the
            relative ``source_file`` of each reference when reading source text
            for the W167 import-edge verification pass. Defaults to the
            current working directory, which is the project root during a
            normal ``roam init`` invocation.
        drop_stats: Optional mutable dict that the verification pass writes
            its dropped-edge counter into (under key
            ``dropped_import_edges``). Pass an empty dict to receive
            indexing-time diagnostics; pass ``None`` to discard them.

    Returns:
        List of edge dicts with source_id, target_id, kind, line, source_file_id.
    """
    # Build a lookup: qualified_name -> list of symbols (multiple files may define same qn)
    symbols_by_qualified = _build_qualified_symbols_for_exact_targets(symbols_by_name)

    # Case-insensitive fallback index for case-insensitive languages (VFP)
    symbols_by_name_lower = _build_casefolded_symbols_for_language_fallback(symbols_by_name)

    # Build import map: (source_file, imported_name) -> import_path
    import_map = _build_import_paths_for_target_disambiguation(references)

    # Build fallback map: file_path -> sorted list of symbols for line-based lookup
    # Used when source_name is None/empty (top-level code, e.g. Vue <script setup>)
    _file_symbols = _build_file_symbols_for_scope_fallback(symbols_by_name)

    hoisted_source_contexts = _precompute_contexts_for_precise_reuse(
        references,
        symbols_by_name,
        _file_symbols,
    )

    # Pre-compute Salesforce canonical file preferences
    sf_file_priority = _build_sf_file_priority(symbols_by_name)
    hoisted_salesforce_imports, hoisted_salesforce_names = _precompute_salesforce_targets_for_precise_reuse(
        references,
        symbols_by_name,
        symbols_by_qualified,
        sf_file_priority,
    )

    hoisted_standard_targets = _precompute_standard_targets_for_precise_reuse(
        references,
        hoisted_source_contexts,
        symbols_by_name,
        symbols_by_qualified,
        symbols_by_name_lower,
        import_map,
    )

    edges = _edges_for_references_preserving_context_precision(
        references,
        files_by_path,
        hoisted_source_contexts,
        hoisted_salesforce_imports,
        hoisted_salesforce_names,
        hoisted_standard_targets,
    )

    # W167: drop ``kind='import'`` edges whose target name isn't written as
    # an import in the source file. This filters resolver fuzzy-match
    # false positives (e.g. ``import yaml`` resolving to a ``yaml`` local
    # variable in some test file when no real ``yaml`` module is indexed).
    imported_names_by_file = _precompute_imported_names_for_precise_reuse(edges, project_root)
    counter: dict[str, int] = drop_stats if drop_stats is not None else {}
    edges = _verify_import_edges(edges, imported_names_by_file, counter)

    return edges


def _prefer_local(target_sym, target_name, source_file, symbols_by_name):
    """If target is in a different file, prefer same-file or same-dir candidate."""
    if target_sym is None or target_sym.get("file_path") == source_file:
        return target_sym
    candidates = symbols_by_name.get(target_name, [])
    for cand in candidates:
        if cand.get("file_path") == source_file:
            return cand
    source_dir = os.path.dirname(source_file) if source_file else ""
    if source_dir and os.path.dirname(target_sym.get("file_path", "")) != source_dir:
        for cand in candidates:
            if os.path.dirname(cand.get("file_path", "")) == source_dir:
                return cand
    return target_sym


def _resolve_standard(
    target_name,
    source_file,
    source_parent,
    kind,
    symbols_by_name,
    symbols_by_qualified,
    symbols_by_name_lower,
    import_map,
    drop_counter: dict[str, int] | None = None,
):
    """Standard multi-strategy resolution: qualified -> simple -> case-insensitive.

    ``drop_counter`` is accepted for signature symmetry with the
    W167 verification pass (``_verify_import_edges``) but is NOT
    written by this function. The counter ``dropped_import_edges``
    is owned by W167 and tracks edges that were emitted then
    dropped at the post-verification stage. W181's pre-filter (the
    ``kind == "import"`` branch below) rejects candidates BEFORE
    any edge is emitted, so its rejections are a different
    concept from "dropped" and do not increment the counter
    (W1260 / W1257-audit).
    """
    # W181: for ``kind='import'`` resolution, pre-filter the candidate
    # set to drop kinds that are never legitimate import targets
    # (``local``, ``parameter``, ``property``) plus ``variable``
    # candidates that live in test files (real source code does not
    # import FROM tests/). If the filter empties the candidate set,
    # we return ``None`` and emit no edge — strictly better than a
    # fabricated phantom edge to a same-named local. The W167 text
    # verification and W158 sanity filter remain as defence-in-depth.
    if kind == "import":
        # Compare against the unfiltered candidate set so we can tell
        # "no candidates ever existed" (which is NOT a phantom drop —
        # the symbol simply isn't indexed) from "candidates existed but
        # the import-filter emptied them" (which IS a phantom drop and
        # should be counted).
        raw_qn_candidates = symbols_by_qualified.get(target_name, [])
        # W1260 removed the `any_raw_candidates = bool(...)` roll-up that
        # also looked at symbols_by_name + symbols_by_name_lower; with the
        # W181 counter increment gone, only the qualified-name list is
        # still consumed (see line ~687 below). The simple/lower lookups
        # are obsolete; restore them if a future detector needs the bool.

        view_by_name = _filter_symbols_for_import(
            symbols_by_name,
            target_name,
            source_file,
        )
        view_by_name_lower = _filter_symbols_for_import(
            symbols_by_name_lower,
            target_name.lower(),
            source_file,
        )
        qn_candidates = _filter_import_candidates(raw_qn_candidates, source_file)
        # If qualified-name filtering produced an empty list but the raw
        # list was non-empty, do NOT fall through to simple-name lookup
        # (the simple-name lookup has its own filtered view and would
        # produce the same emptiness).
        candidates_for_simple = view_by_name.get(target_name, [])
        candidates_for_lower = view_by_name_lower.get(target_name.lower(), [])
        if not qn_candidates and not candidates_for_simple and not candidates_for_lower:
            # W1260: do NOT increment ``dropped_import_edges`` here.
            # The counter is owned by W167's post-verification path
            # (``_verify_import_edges``) and its semantic is "an edge
            # was emitted, then dropped". W181 is a *pre-filter* — it
            # rejects candidates BEFORE any edge is emitted, so no
            # edge exists to be "dropped". Conflating the two stages
            # in one counter inflates the W167 telemetry with W181
            # rejections that are a different concept (see W1257
            # audit + W181-vs-W167 distinction in CLAUDE.md).
            return None
    else:
        view_by_name = symbols_by_name
        view_by_name_lower = symbols_by_name_lower
        qn_candidates = symbols_by_qualified.get(target_name, [])

    # 1. Qualified name exact match
    qn_matches = qn_candidates
    target_sym = qn_matches[0] if len(qn_matches) == 1 else None
    if len(qn_matches) > 1:
        target_sym = _best_match(
            target_name,
            source_file,
            view_by_name,
            ref_kind=kind,
            source_parent=source_parent,
            import_map=import_map,
        )
    target_sym = _prefer_local(target_sym, target_name, source_file, view_by_name)

    # 2. Simple name with disambiguation
    if target_sym is None:
        target_sym = _best_match(
            target_name,
            source_file,
            view_by_name,
            ref_kind=kind,
            source_parent=source_parent,
            import_map=import_map,
        )

    # 3. Case-insensitive fallback (VFP and other case-insensitive langs)
    if target_sym is None:
        target_sym = _best_match(
            target_name.lower(),
            source_file,
            view_by_name_lower,
            ref_kind=kind,
            source_parent=source_parent,
            import_map=import_map,
        )

    return target_sym


def _precompute_standard_targets_for_precise_reuse(
    references: list[dict],
    hoisted_source_contexts: dict[tuple[str, str, int | None, str], tuple[dict, str] | None],
    symbols_by_name: dict[str, list[dict]],
    symbols_by_qualified: dict[str, list[dict]],
    symbols_by_name_lower: dict[str, list[dict]],
    import_map: dict[tuple[str, str], str],
) -> dict[tuple[str, str, str, str], dict | None]:
    """Resolve each distinct standard target once while preserving context precision.

    Trades memory (the resolution cache) for CPU time: repeated references
    with the same target name and source context reuse one
    ``_resolve_standard`` call instead of resolving per iteration.
    """
    hoisted_standard_targets: dict[tuple[str, str, str, str], dict | None] = {}
    for ref in references:
        target_name = ref.get("target_name", "")
        if not target_name:
            continue
        source_context_key = _source_context_key_for_precise_reuse(ref)
        source_context = hoisted_source_contexts.get(source_context_key)
        if source_context is None:
            continue
        _source_sym, source_parent = source_context
        source_file = ref.get("source_file", "")
        kind = ref.get("kind", "call")
        std_key = (target_name, source_file, source_parent, kind)
        if std_key in hoisted_standard_targets:
            continue
        hoisted_standard_targets[std_key] = _resolve_standard(
            target_name,
            source_file,
            source_parent,
            kind,
            symbols_by_name,
            symbols_by_qualified,
            symbols_by_name_lower,
            import_map,
            drop_counter=None,
        )
    return hoisted_standard_targets


_IMPORT_PATH_EXTENSIONS = (".ts", ".js", ".vue", ".tsx", ".jsx", ".py", ".prg", ".scx")


def _strip_extension_for_module_identity(path: str) -> str:
    """Compare imports by module identity, not by source-file suffix."""
    for ext in _IMPORT_PATH_EXTENSIONS:
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def _normalize_import_path_for_context_free_matching(import_path: str) -> str:
    """Preserve suffix semantics when the source file's directory is unknown."""
    normalized = import_path.replace("\\", "/")
    if normalized.startswith("@/"):
        normalized = "src/" + normalized[2:]
    elif normalized.startswith("./"):
        normalized = normalized[2:]
    else:
        while normalized.startswith("../"):
            normalized = normalized[3:]
    return _strip_extension_for_module_identity(normalized)


def _candidate_preserves_import_path_identity(candidate_path: str, normalized_import_path: str) -> bool:
    """Accept direct-file and barrel-export matches without widening to name-only."""
    fp = candidate_path.replace("\\", "/")
    fp_no_ext = _strip_extension_for_module_identity(fp)
    return (
        fp_no_ext.endswith("/" + normalized_import_path)
        or fp_no_ext == normalized_import_path
        or fp.startswith(normalized_import_path + "/")
        or ("/" + normalized_import_path + "/") in fp
    )


def _match_import_path(import_path: str, candidates: list[dict]) -> list[dict]:
    """Filter candidates whose file_path matches an import path string.

    Handles:
    - @/ alias → src/ (Vue convention)
    - ./ and ../ relative prefixes (stripped for suffix matching)
    - Barrel exports: import from '@/composables/transactions' matches
      'src/composables/transactions/types.ts'
    - File extension stripping on candidates
    """
    if not import_path:
        return []

    normalized = _normalize_import_path_for_context_free_matching(import_path)
    return [
        cand for cand in candidates if _candidate_preserves_import_path_identity(cand.get("file_path", ""), normalized)
    ]


def _bm_pick_class_constructor(candidates: list[dict], source_file: str, name: str, ref_kind: str) -> dict | None:
    """For `call` references with an uppercase name (constructor-call pattern),
    prefer a class candidate over other kinds. Selection order within the
    class subset: same-file -> same-dir -> first[0]. Returns None to fall
    through to the locality ladder when ref_kind/name don't match the
    constructor heuristic OR no class candidates exist."""
    if ref_kind != "call" or not name or not name[0].isupper():
        return None
    class_candidates = [c for c in candidates if c.get("kind") == "class"]
    if not class_candidates:
        return None
    for sym in class_candidates:
        if sym.get("file_path") == source_file:
            return sym
    source_dir = os.path.dirname(source_file) if source_file else ""
    for sym in class_candidates:
        if os.path.dirname(sym.get("file_path", "")) == source_dir:
            return sym
    return class_candidates[0]


def _bm_pick_same_file(candidates: list[dict], source_file: str, source_parent: str) -> dict | None:
    """Locality #1 — prefer candidates in the same file as the source ref.
    With multiple matches AND a non-empty source_parent (Rust `MyStruct::`
    / Go method-on-receiver), the W742-style tiebreak picks whichever
    candidate's qualified_name starts with the same parent. Singleton
    same-file match wins outright."""
    same_file = [s for s in candidates if s.get("file_path") == source_file]
    if not same_file:
        return None
    if len(same_file) == 1:
        return same_file[0]
    if source_parent:
        for s in same_file:
            qn = s.get("qualified_name", "")
            if qn.startswith(source_parent + "::") or qn.startswith(source_parent + "."):
                return s
    return same_file[0]


def _bm_pick_same_dir(candidates: list[dict], source_file: str) -> dict | None:
    """Locality #2 — prefer candidates in the same directory as the source
    ref. Within the same-dir subset, exported symbols (canonical
    definitions) beat local bindings (destructured imports). Returns None
    when no candidates share the source directory."""
    source_dir = os.path.dirname(source_file) if source_file else ""
    same_dir = [s for s in candidates if os.path.dirname(s.get("file_path", "")) == source_dir]
    if not same_dir:
        return None
    exported = [s for s in same_dir if s.get("is_exported")]
    if exported:
        return exported[0]
    return same_dir[0]


def _bm_pick_import_matched(
    candidates: list[dict],
    import_map: dict[tuple[str, str], str],
    source_file: str,
    name: str,
) -> dict | None:
    """Locality #3 — when an import map records `(source_file, name) ->
    import_path`, narrow the candidate pool to the import-path-matched
    subset via `_match_import_path`. Exported preferred within that subset.
    Returns None when no import path is recorded OR no candidates match."""
    imp_path = import_map.get((source_file, name))
    if not imp_path:
        return None
    import_matched = _match_import_path(imp_path, candidates)
    if not import_matched:
        return None
    exported = [s for s in import_matched if s.get("is_exported")]
    if exported:
        return exported[0]
    return import_matched[0]


def _bm_pick_canonical_fallback(candidates: list[dict]) -> dict:
    """Locality #4 — global fallback. Prefer exported symbols, then bias
    by `_path_score` so `src/lib/` paths win over `dev/scripts/tests`
    (prevents a dev/ helper defining its own ``open_db`` from shadowing
    `src/roam/db/connection.py:open_db` when the dev file is indexed
    first). Final tiebreak: deterministic by qualified_name. Always
    returns SOMETHING — the caller has already screened for empty
    candidates."""
    exported = [s for s in candidates if s.get("is_exported")]
    pool = exported or candidates
    return min(pool, key=lambda s: (-_path_score(s.get("file_path") or ""), s.get("qualified_name") or ""))


def _best_match(
    name: str,
    source_file: str,
    symbols_by_name: dict,
    ref_kind: str = "",
    source_parent: str = "",
    import_map: dict[tuple[str, str], str] | None = None,
) -> dict | None:
    """Find the best matching symbol for a name, preferring locality.

    Resolution ladder (first non-None wins):
      1. Class-constructor preference (uppercase-name `call` ref)
      2. Same-file (parent-aware tiebreak)
      3. Same-directory (exported-preferred)
      4. Import-path-narrowed (when import_map records the source's import)
      5. Canonical global fallback (path-score + qualified_name tiebreak)

    Empty-candidates and singleton-candidates fast-paths short-circuit
    the ladder. The class-constructor branch sits ABOVE same-file so an
    uppercase `MyClass()` call resolves to the class even when a
    same-file helper coincidentally shares the name.
    """
    candidates = symbols_by_name.get(name, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    constructor = _bm_pick_class_constructor(candidates, source_file, name, ref_kind)
    if constructor is not None:
        return constructor
    same_file = _bm_pick_same_file(candidates, source_file, source_parent)
    if same_file is not None:
        return same_file
    same_dir = _bm_pick_same_dir(candidates, source_file)
    if same_dir is not None:
        return same_dir
    if import_map:
        import_matched = _bm_pick_import_matched(candidates, import_map, source_file, name)
        if import_matched is not None:
            return import_matched
    return _bm_pick_canonical_fallback(candidates)


def _closest_symbol(
    source_file: str,
    ref_line: int | None,
    file_symbols: dict[str, list[dict]],
    kind: str = "call",
) -> dict | None:
    """Find the symbol that contains ref_line, or fall back to file-level source.

    Prefers the most-nested symbol whose line_start <= ref_line <= line_end.
    When no symbol contains the reference (module-scope code like watch callbacks),
    returns the first symbol in the file as a file-level source to avoid
    self-references from "closest before" matching a completed function.

    W742: For ``kind='import'`` references, the file-level ``syms[0]``
    fallback is suppressed. Module-scope imports must NOT be attributed
    to whichever function happens to be first in the file — that produces
    phantom IMPORT edges (e.g. a 3-line ``_format_count`` formatter
    inheriting 18 outgoing imports from ``indexer.py`` and, via the
    effects propagator, inheriting transitive filesystem/db/cache
    effects it never uses). Imports at module scope should not attribute
    to any symbol; the caller skips the edge when this returns ``None``.

    W1284 (G3): The W742 suppression has one well-defined exception —
    a Vue/Svelte SFC's synthetic component symbol (``kind='component'``
    at ``line_start=1``, created by ``TypeScriptExtractor.extract_symbols``
    for ``.vue`` / ``.svelte`` files). That synthetic IS the file's
    exported identity: there is no other top-level symbol to mis-attribute
    to, and attributing module-scope ``<script setup>`` imports to it
    matches how the component is consumed from outside (`roam why
    MyComponent` resolves here). The carve-out applies only when the
    first-line symbol is the synthetic component; non-component
    line-1 symbols still take the W742 path.
    """
    syms = file_symbols.get(source_file)
    if not syms:
        return None

    # W1284 helper: the Vue/Svelte synthetic component anchor — first
    # symbol in the file, ``kind='component'`` at ``line_start=1``.
    def _sfc_synthetic_anchor() -> dict | None:
        first = syms[0]
        if first.get("kind") == "component" and (first.get("line_start") or 0) == 1:
            return first
        return None

    if ref_line is None:
        # W742: imports without a line cannot be safely attributed
        # to syms[0] (module-scope imports). Other kinds keep the
        # legacy file-level placeholder behaviour.
        if kind == "import":
            # W1284 (G3): an SFC synthetic component IS the file-level
            # identity; attributing the import to it is correct.
            return _sfc_synthetic_anchor()
        return syms[0]

    # Prefer symbol that CONTAINS the reference line (most nested wins)
    containing = None
    for sym in syms:
        ls = sym.get("line_start") or 0
        le = sym.get("line_end") or 0
        if ls <= ref_line and le >= ref_line and le > 0:
            containing = sym  # last containing wins (most nested)
    if containing:
        return containing

    # No containing symbol — reference is at module scope.
    # W742: for kind='import', do NOT fall back to syms[0]. Module-scope
    # imports otherwise mis-attribute to whichever symbol happens to be
    # first in the file (see docstring). The caller drops the edge.
    if kind == "import":
        # W1284 (G3): SFC synthetic-component exception — see docstring.
        return _sfc_synthetic_anchor()
    # Return first symbol in file as a "file-level" source.
    return syms[0]


def build_file_edges(
    symbol_edges: list[dict],
    symbols: dict[int, dict],
) -> list[dict]:
    """Aggregate symbol-level edges into file-level edges.

    Args:
        symbol_edges: List of edge dicts with source_id, target_id.
        symbols: Mapping from symbol_id -> symbol dict (with 'file_id').

    Returns:
        List of file edge dicts with source_file_id, target_file_id, kind, symbol_count.
    """
    file_edge_counts: dict[tuple[int, int], int] = {}

    for edge in symbol_edges:
        src_sym = symbols.get(edge["source_id"])
        tgt_sym = symbols.get(edge["target_id"])
        if src_sym is None or tgt_sym is None:
            continue

        src_fid = src_sym["file_id"]
        tgt_fid = tgt_sym["file_id"]
        if src_fid == tgt_fid:
            continue

        key = (src_fid, tgt_fid)
        file_edge_counts[key] = file_edge_counts.get(key, 0) + 1

    return [
        {
            "source_file_id": src,
            "target_file_id": tgt,
            "kind": "imports",
            "symbol_count": count,
        }
        for (src, tgt), count in file_edge_counts.items()
    ]


# ---------------------------------------------------------------------------
# Salesforce cross-language resolution
# ---------------------------------------------------------------------------

# File extension priority for Salesforce disambiguation
_SF_EXT_PRIORITY = {
    ".cls": 0,
    ".trigger": 1,
    ".cmp": 2,
    ".app": 2,
    ".page": 3,
    ".component": 3,
}


def _build_sf_file_priority(symbols_by_name: dict) -> dict[str, int]:
    """Pre-compute file priority scores for Salesforce disambiguation."""
    priority = {}
    for sym_list in symbols_by_name.values():
        for sym in sym_list:
            fp = sym.get("file_path", "")
            if fp not in priority:
                _, ext = os.path.splitext(fp)
                priority[fp] = _SF_EXT_PRIORITY.get(ext, 10)
    return priority


def _resolve_apex_method_import_with_class_membership(
    class_name: str,
    method_name: str,
    symbols_by_name: dict,
    symbols_by_qualified: dict,
) -> dict | None:
    """Keep Apex method imports bound to the owning class."""
    qn = f"{class_name}.{method_name}"
    candidates = symbols_by_qualified.get(qn, [])
    if candidates:
        cls_candidates = [candidate for candidate in candidates if candidate.get("file_path", "").endswith(".cls")]
        return cls_candidates[0] if cls_candidates else candidates[0]

    for candidate in symbols_by_name.get(method_name, []):
        if not candidate.get("file_path", "").endswith(".cls"):
            continue
        cqn = candidate.get("qualified_name", "")
        if cqn.startswith(class_name + "."):
            return candidate
    return None


def _resolve_apex_import_with_owning_class(
    apex_ref: str,
    symbols_by_name: dict,
    symbols_by_qualified: dict,
) -> dict | None:
    """Preserve Apex owner precision across method and class imports."""
    if "." in apex_ref:
        class_name, method_name = apex_ref.rsplit(".", 1)
        return _resolve_apex_method_import_with_class_membership(
            class_name,
            method_name,
            symbols_by_name,
            symbols_by_qualified,
        )

    for candidate in symbols_by_name.get(apex_ref, []):
        if candidate.get("file_path", "").endswith(".cls") and candidate.get("kind") == "class":
            return candidate
    return None


def _resolve_salesforce_import(
    import_path: str,
    symbols_by_name: dict,
    symbols_by_qualified: dict,
) -> dict | None:
    """Resolve @salesforce/* import paths to symbols.

    Handles:
    - @salesforce/apex/ClassName.methodName → find method in .cls file
    - @salesforce/schema/ObjectName.FieldName → match by qualified name
    - @salesforce/label/c.LabelName → match CustomLabels
    """
    parts = import_path.split("/")
    if len(parts) < 3:
        return None

    category = parts[1]  # apex, schema, label, messageChannel, etc.

    if category == "apex":
        return _resolve_apex_import_with_owning_class(
            parts[2],
            symbols_by_name,
            symbols_by_qualified,
        )
    if category == "schema":
        # @salesforce/schema/Account.Name
        schema_ref = parts[2]
        candidates = symbols_by_qualified.get(schema_ref, [])
        if candidates:
            return candidates[0]
        name = schema_ref.rsplit(".", 1)[-1] if "." in schema_ref else schema_ref
        candidates = symbols_by_name.get(name, [])
        if candidates:
            return candidates[0]
    if category == "label":
        # @salesforce/label/c.MyLabel
        label_ref = parts[2]
        label_name = label_ref.split(".")[-1] if "." in label_ref else label_ref
        candidates = symbols_by_name.get(label_name, [])
        if candidates:
            return candidates[0]

    return None


def _resolve_salesforce_name(
    target_name: str,
    kind: str,
    symbols_by_name: dict,
    sf_file_priority: dict,
) -> dict | None:
    """Resolve Salesforce controller/component/SOQL references by name.

    Prefers .cls files for controller refs, applies SF file priority ordering.
    """
    candidates = symbols_by_name.get(target_name, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # For controller refs, prefer class symbols in .cls files
    if kind == "controller":
        cls_classes = [c for c in candidates if c.get("file_path", "").endswith(".cls") and c.get("kind") == "class"]
        if cls_classes:
            return cls_classes[0]

    # Sort by file priority
    def priority_key(sym):
        fp = sym.get("file_path", "")
        return sf_file_priority.get(fp, 10)

    sorted_cands = sorted(candidates, key=priority_key)
    return sorted_cands[0]
