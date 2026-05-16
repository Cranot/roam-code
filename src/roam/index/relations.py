"""Import and call resolution into graph edges."""

from __future__ import annotations

import os
import re

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
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        nxt2 = text[i + 2] if i + 2 < n else ""
        # ``#`` line comment (python / shell / ruby — and harmless in C++)
        if ch == "#":
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        # ``//`` line comment (c / c++ / java / js)
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        # ``/* ... */`` block comment
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
            continue
        # String-prefix handling — ``r``/``b``/``rb``/``br`` (any case)
        # may precede a string literal in Python. We don't blank the
        # prefix letters (they're part of the source code), but we need
        # to skip past them so the next iteration sees the quote.
        # Triple-quoted ``"""..."""`` or ``'''...'''``
        if (ch == '"' and nxt == '"' and nxt2 == '"') or (ch == "'" and nxt == "'" and nxt2 == "'"):
            quote = ch * 3
            j = text.find(quote, i + 3)
            if j == -1:
                j = n
            else:
                j += 3
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
            continue
        # Single-line ``"..."`` / ``'...'`` / ``\`...\```
        if ch in "\"'`":
            quote = ch
            j = i + 1
            while j < n and text[j] != quote and text[j] != "\n":
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j < n and text[j] == quote:
                j += 1
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
            continue
        # Normal code character — pass through
        out.append(ch)
        i += 1
    return "".join(out)


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
    for match in _RX_IMPORT_LINE.finditer(masked):
        if match.start() < pos:
            continue  # inside a previously-scanned block
        rest = match.group(1) or ""
        # Track an open ``(`` / ``{`` so we can pick up multi-line imports.
        line_end = masked.find("\n", match.end())
        if line_end == -1:
            line_end = text_len
        block_text = rest
        cursor = match.end()
        if "(" in rest and ")" not in rest:
            close = masked.find(")", cursor)
            if close != -1:
                block_text = masked[match.start(1) : close]
                cursor = close + 1
        elif "{" in rest and "}" not in rest:
            close = masked.find("}", cursor)
            if close != -1:
                block_text = masked[match.start(1) : close]
                cursor = close + 1
        else:
            block_text = rest
            cursor = line_end
        for token in _RX_NAME_TOKEN.findall(block_text):
            if token in {"as", "from", "import", "use", "using", "require", "require_relative", "include", "package"}:
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
    symbols_by_qualified: dict[str, list[dict]] = {}
    for name, sym_list in symbols_by_name.items():
        for sym in sym_list:
            qn = sym.get("qualified_name")
            if qn:
                symbols_by_qualified.setdefault(qn, []).append(sym)

    # Case-insensitive fallback index for case-insensitive languages (VFP)
    symbols_by_name_lower: dict[str, list[dict]] = {}
    for name, sym_list in symbols_by_name.items():
        lower = name.lower()
        if lower != name:  # Only add if case differs to save memory
            symbols_by_name_lower.setdefault(lower, []).extend(sym_list)
        # Always add lowercase key so lookups work
        if lower not in symbols_by_name_lower:
            symbols_by_name_lower[lower] = sym_list

    # Build import map: (source_file, imported_name) -> import_path
    import_map: dict[tuple[str, str], str] = {}
    for ref in references:
        if ref.get("kind") == "import" and ref.get("import_path"):
            key = (ref.get("source_file", ""), ref.get("target_name", ""))
            if key[0] and key[1]:
                import_map[key] = ref["import_path"]

    # Build fallback map: file_path -> sorted list of symbols for line-based lookup
    # Used when source_name is None/empty (top-level code, e.g. Vue <script setup>)
    _file_symbols: dict[str, list[dict]] = {}
    for sym_list in symbols_by_name.values():
        for sym in sym_list:
            fp = sym.get("file_path", "")
            if fp:
                _file_symbols.setdefault(fp, []).append(sym)
    # Sort each file's symbols by line_start for binary-search-style lookup
    for fp in _file_symbols:
        _file_symbols[fp].sort(key=lambda s: s.get("line_start") or 0)

    # Also index source symbols by name for finding the caller
    edges = []
    seen = set()

    # Pre-compute Salesforce canonical file preferences
    sf_file_priority = _build_sf_file_priority(symbols_by_name)

    for ref in references:
        source_name = ref.get("source_name", "")
        target_name = ref.get("target_name", "")
        kind = ref.get("kind", "call")
        line = ref.get("line")
        source_file = ref.get("source_file", "")

        if not target_name:
            continue

        # Find source symbol (the caller)
        source_sym = _best_match(source_name, source_file, symbols_by_name)
        if source_sym is None:
            # Fallback for top-level code (e.g. Vue <script setup>, Python module scope):
            # pick the closest symbol at or before the reference line.
            # W742: for kind='import', _closest_symbol skips the syms[0]
            # fallback so module-scope imports don't mis-attribute to
            # whichever function happens to be first in the file.
            source_sym = _closest_symbol(source_file, line, _file_symbols, kind=kind)
        if source_sym is None:
            continue

        # Extract parent context from source for same-file disambiguation
        # e.g. MyStruct::some_method -> parent = "MyStruct"
        source_parent = ""
        src_qn = source_sym.get("qualified_name", "")
        if "::" in src_qn:
            source_parent = src_qn.rsplit("::", 1)[0]
        elif "." in src_qn:
            source_parent = src_qn.rsplit(".", 1)[0]

        # Salesforce resolution: handle @salesforce/ imports and controller refs
        import_path = ref.get("import_path", "")
        target_sym = None
        if import_path and import_path.startswith("@salesforce/"):
            target_sym = _resolve_salesforce_import(
                import_path,
                symbols_by_name,
                symbols_by_qualified,
            )
        elif kind in ("controller", "soql", "metadata_ref", "component_ref"):
            target_sym = _resolve_salesforce_name(
                target_name,
                kind,
                symbols_by_name,
                sf_file_priority,
            )

        # Standard resolution (skip if Salesforce already resolved)
        if target_sym is None:
            target_sym = _resolve_standard(
                target_name,
                source_file,
                source_parent,
                kind,
                symbols_by_name,
                symbols_by_qualified,
                symbols_by_name_lower,
                import_map,
                drop_counter=drop_stats,
            )

        if target_sym is None:
            continue

        source_id = source_sym["id"]
        target_id = target_sym["id"]

        if source_id == target_id:
            continue

        edge_key = (source_id, target_id, kind)
        if edge_key in seen:
            continue
        seen.add(edge_key)

        edge_record: dict = {
            "source_id": source_id,
            "target_id": target_id,
            "kind": kind,
            "line": line,
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
        edges.append(edge_record)

    # W167: drop ``kind='import'`` edges whose target name isn't written as
    # an import in the source file. This filters resolver fuzzy-match
    # false positives (e.g. ``import yaml`` resolving to a ``yaml`` local
    # variable in some test file when no real ``yaml`` module is indexed).
    if edges:
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

    # Normalize import path: strip prefix, normalize separators
    normalized = import_path.replace("\\", "/")
    if normalized.startswith("@/"):
        normalized = "src/" + normalized[2:]
    elif normalized.startswith("./"):
        normalized = normalized[2:]
    elif normalized.startswith("../"):
        # Preserve suffix semantics for relative imports without requiring
        # source-file context. "../src/utils/case" should match
        # "src/utils/case.ts", and "../utils/case" should match any
        # ".../utils/case.ts" candidate.
        while normalized.startswith("../"):
            normalized = normalized[3:]

    # Strip trailing extension from normalized path if present
    for ext in (".ts", ".js", ".vue", ".tsx", ".jsx", ".py", ".prg", ".scx"):
        if normalized.endswith(ext):
            normalized = normalized[: -len(ext)]
            break

    matched = []
    for cand in candidates:
        fp = cand.get("file_path", "").replace("\\", "/")
        # Strip file extension from candidate
        fp_no_ext = fp
        for ext in (".ts", ".js", ".vue", ".tsx", ".jsx", ".py", ".prg", ".scx"):
            if fp_no_ext.endswith(ext):
                fp_no_ext = fp_no_ext[: -len(ext)]
                break

        # Direct match: candidate path ends with normalized import path
        if fp_no_ext.endswith("/" + normalized) or fp_no_ext == normalized:
            matched.append(cand)
        # Barrel export: import path is a directory prefix of the candidate
        elif fp.startswith(normalized + "/") or ("/" + normalized + "/") in fp:
            matched.append(cand)

    return matched


def _best_match(
    name: str,
    source_file: str,
    symbols_by_name: dict,
    ref_kind: str = "",
    source_parent: str = "",
    import_map: dict[tuple[str, str], str] | None = None,
) -> dict | None:
    """Find the best matching symbol for a name, preferring locality."""
    candidates = symbols_by_name.get(name, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # For call references with an uppercase name, prefer class (constructor call pattern)
    if ref_kind == "call" and name and name[0].isupper():
        class_candidates = [c for c in candidates if c.get("kind") == "class"]
        if class_candidates:
            for sym in class_candidates:
                if sym.get("file_path") == source_file:
                    return sym
            source_dir = os.path.dirname(source_file) if source_file else ""
            for sym in class_candidates:
                if os.path.dirname(sym.get("file_path", "")) == source_dir:
                    return sym
            return class_candidates[0]

    # Prefer same file — with parent-aware tie-breaking for Rust/Go impl blocks
    same_file = [s for s in candidates if s.get("file_path") == source_file]
    if len(same_file) == 1:
        return same_file[0]
    if len(same_file) > 1:
        # If source has a parent (e.g. MyStruct::some_method calling new()),
        # prefer the candidate whose qualified_name starts with the same parent
        if source_parent:
            for s in same_file:
                qn = s.get("qualified_name", "")
                if qn.startswith(source_parent + "::") or qn.startswith(source_parent + "."):
                    return s
        return same_file[0]

    # Prefer same directory — with exported definitions over local bindings
    source_dir = os.path.dirname(source_file) if source_file else ""
    same_dir = [s for s in candidates if os.path.dirname(s.get("file_path", "")) == source_dir]
    if same_dir:
        # Prefer exported symbols (canonical definitions, not destructured imports)
        exported = [s for s in same_dir if s.get("is_exported")]
        if exported:
            return exported[0]
        return same_dir[0]

    # Import-aware resolution: use import path data to narrow candidates
    if import_map:
        imp_path = import_map.get((source_file, name))
        if imp_path:
            import_matched = _match_import_path(imp_path, candidates)
            if import_matched:
                # Prefer exported among import-matched candidates
                exported = [s for s in import_matched if s.get("is_exported")]
                if exported:
                    return exported[0]
                return import_matched[0]

    # Fall back: prefer exported symbols globally, with a canonical-path
    # bias as a tiebreak. Without the bias a dev/ helper script that
    # defines its own ``open_db`` shadows the canonical
    # ``src/roam/db/connection.py:open_db`` whenever the dev file is
    # indexed first (e.g. alphabetically). The order is:
    # 1) src/lib/ paths win over dev/scripts/tests
    # 2) exported wins over local
    # 3) deterministic by qualified_name as last tiebreak
    exported = [s for s in candidates if s.get("is_exported")]
    pool = exported or candidates
    return min(pool, key=lambda s: (-_path_score(s.get("file_path") or ""), s.get("qualified_name") or ""))


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

    if category == "apex" and len(parts) >= 3:
        # @salesforce/apex/MyController.myMethod
        apex_ref = parts[2]
        if "." in apex_ref:
            class_name, method_name = apex_ref.rsplit(".", 1)
            # Try qualified name first: ClassName.methodName
            qn = f"{class_name}.{method_name}"
            candidates = symbols_by_qualified.get(qn, [])
            if candidates:
                # Prefer candidates from .cls files
                cls_cands = [c for c in candidates if c.get("file_path", "").endswith(".cls")]
                return cls_cands[0] if cls_cands else candidates[0]
            # Try just the method name
            method_cands = symbols_by_name.get(method_name, [])
            for c in method_cands:
                if c.get("file_path", "").endswith(".cls"):
                    # Check if it belongs to the right class
                    cqn = c.get("qualified_name", "")
                    if cqn.startswith(class_name + "."):
                        return c
        else:
            # Just class name: @salesforce/apex/MyController
            candidates = symbols_by_name.get(apex_ref, [])
            cls_cands = [c for c in candidates if c.get("file_path", "").endswith(".cls") and c.get("kind") == "class"]
            if cls_cands:
                return cls_cands[0]

    elif category == "schema" and len(parts) >= 3:
        # @salesforce/schema/Account.Name
        schema_ref = parts[2]
        candidates = symbols_by_qualified.get(schema_ref, [])
        if candidates:
            return candidates[0]
        # Try simple name
        name = schema_ref.rsplit(".", 1)[-1] if "." in schema_ref else schema_ref
        candidates = symbols_by_name.get(name, [])
        if candidates:
            return candidates[0]

    elif category == "label" and len(parts) >= 3:
        # @salesforce/label/c.MyLabel
        label_ref = parts[2]
        # Strip namespace prefix (e.g. "c.MyLabel" → "MyLabel")
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
