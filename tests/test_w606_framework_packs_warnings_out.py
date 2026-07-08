"""W606 — ``search/framework_packs.py`` reader audit: NO-OP wave, fail-loud-by-construction.

The W595 / W596 / W597 / W598 / W599 / W600 / W601 / W602 / W603 / W604
/ W605 Pattern-2 substrate-hardening arc closed silent-fallback
disclosure gaps on lease + permits + runs-ledger + runtime-daemon +
pr-analyze-cache + trace-ingest + config-hashes + signing +
metrics-push + db-connection + db-findings + semantic-search
substrates. W606 audits the framework-packs substrate
(``src/roam/search/framework_packs.py``) — the static curated-library
symbol packs that the W605 ``search_stored`` already wraps with
``semantic_pack_search_failed:`` at the caller layer.

W978 first-hypothesis decision (STRONG, read source IN FULL)
------------------------------------------------------------

Read ``framework_packs.py`` end-to-end (674 lines). The W605 agent's
hypothesis (``available_packs() + search_pack_symbols likely have
silent empty fallbacks on missing pack dir``) was FALSE on inspection.

Categorised every potential silent-pass site:

THE MODULE IS A PURE IN-MEMORY STATIC INDEX:

* ``_PACK_DEFINITIONS`` (line ~15-544) is a hardcoded dict literal of
  9 framework packs (python-stdlib / django / flask / fastapi /
  react / express / sqlalchemy / pytest, plus one). There is NO disk
  scan, NO ``.roam/packs/`` directory load, NO sqlite/FTS5 query, NO
  yaml/json deserialisation. The packs ship in the source file.
* ``_compile_entries()`` (line ~566) runs ONCE at module import time
  to build the TF-IDF vector index over the static dict. Any malformed
  entry would crash at import (not silently degrade).
* ``available_packs()`` (line ~622) returns
  ``sorted(_PACK_DEFINITIONS.keys())`` — pure dict-keys operation. No
  silent path.
* ``search_pack_symbols()`` (line ~627) performs pure math
  (tokenize + cosine + sort) over ``_PACK_ENTRIES``. No I/O, no
  ``try/except``.

ZERO ``try/except/raise`` STATEMENTS IN THE FILE:

``grep -E '^(\\s*)(try:|except |raise )' framework_packs.py`` returns
ZERO matches. (String-literal "exception" / "raises" mentions inside
pack metadata don't count.) Every code path either runs to completion
or propagates ``KeyError`` / ``TypeError`` loudly to the caller. The
substrate is fail-loud-by-construction.

W605 CALLER-LAYER ALREADY DISCLOSES PACK FAILURE:

``search/index_embeddings.py::search_stored`` (line ~577-585) wraps
the ``search_pack_symbols`` call in an explicit ``try/except`` and
emits ``semantic_pack_search_failed:<exc_class>:<detail>`` when the
pack import or call raises. That wrapper handles the substrate-failure
disclosure at the CALLER boundary, which is the correct layer because:

1. ``framework_packs.py`` has no warning context to attach (no
   conn / no project_root / no per-pack handle).
2. Plumbing ``warnings_out`` down into a pure-math module would
   require adding ``try/except`` to swallow exceptions that today
   propagate loudly — a regression.
3. The caller already owns the bucket and can attach run-context to
   the emitted marker.

W606 OUTCOME: NO-OP — POSITIVE COVERAGE ONLY
--------------------------------------------

Per the W606 task brief:

  "If framework_packs is ENTIRELY cold-pack-state (every silent is
   'pack not installed'), STOP and declare NO-OP — pin positive
   coverage. Only plumb paths where SUBSTRATE FAILURE (not pack-
   absence) silently affects output."

The framework_packs.py readers have:
* ZERO try/except blocks (substrate is fail-loud-by-construction).
* ZERO I/O paths (no disk / no DB / no network).
* ZERO conditional silent fallbacks (the only silent ``return []``
  is on empty query tokens — a legitimate filter, identical to
  W605's ``if not query_tokens: return []`` discipline).

Plumbing ``warnings_out`` here would either:

1. Require introducing ``try/except`` around pure math, changing
   well-defined Python errors (KeyError on a missing pack key,
   TypeError on a non-string query) from "raise loudly" to "return
   empty with marker" — a regression.
2. Plumb markers for code paths that DO NOT EXIST (no disk load, no
   metadata parse, no schema check).

This test SEALS the W606 audit conclusion: the framework_packs.py
readers have no substrate-failure silent paths and should NOT acquire
``warnings_out`` parameters. The W978 first-hypothesis discipline
correctly prevented a regression here.

W605 CROSS-REFERENCE — pack failure DISCLOSED AT CALLER LAYER
-------------------------------------------------------------

W605 plumbs ``semantic_pack_search_failed:<exc_class>:<detail>`` in
``search/index_embeddings.py::search_stored`` — the single caller of
``search_pack_symbols``. That is the correct disclosure layer because:

* The caller owns the ``warnings_out`` bucket and the run context.
* The caller already runs the ``try/except`` around the pack call
  to satisfy the fallback-contracts arc (pack failure must NOT take
  down the whole search; lexical + semantic results survive).
* The marker name ``semantic_pack_search_failed`` correctly identifies
  the SUBSTRATE LAYER (semantic search) where the failure was
  observed, not the producer module name.

W603 / W604 / W605 / W606 PREFIX FAMILY (closed enum, distinct)
---------------------------------------------------------------

The Pattern-2 substrate arc maintains DISTINCT prefix families per
substrate to ease operator triage:

* ``roam_*``        (W603, db/connection.py — DB substrate, write+read)
* ``findings_*``    (W604, db/findings.py — EMPTY, NO-OP, fail-loud-by-raise)
* ``semantic_*``    (W605, search/index_embeddings.py — semantic-search,
                     includes ``semantic_pack_search_failed`` for pack
                     failures observed at the caller boundary)
* ``framework_pack_*`` (W606 — EMPTY, NO-OP, fail-loud-by-construction.
                       Reserved namespace; not emitted today because
                       no substrate-failure silent path exists.)

If a future wave adds disk-loading / metadata parsing to
``framework_packs.py`` (e.g., loading user-installed packs from
``.roam/packs/``), it should plumb the ``framework_pack_*`` family
with the closed-enum shapes documented in the W606 brief:

* ``framework_pack_dir_missing:<path>``
* ``framework_pack_metadata_corrupt:<pack>:<exc>``
* ``framework_pack_schema_mismatch:<pack>:<expected>:<actual>``
* ``framework_pack_search_failed:<pack>:<query>:<exc>``

Today, NONE of those code paths exist. The closed-enum set is empty
because the substrate is empty.

W907 VERIFY-CYCLE CHECK
-----------------------

``framework_packs.py`` has NO "duplicated here to avoid cycle"
docstrings. The file imports only:

* stdlib: ``hashlib``, ``math``, ``collections.Counter``
* local: ``roam.search.tfidf`` (cosine_similarity, tokenize)

No roam-internal cycle hedges. Clean.

CALLER AUDIT (audit-only, no caller modifications)
--------------------------------------------------

The framework_packs.py public surface has exactly TWO consumers:

* ``src/roam/search/index_embeddings.py`` — imports
  ``search_pack_symbols`` at module-top. Called from ``search_stored``
  with ``try/except`` + ``semantic_pack_search_failed`` plumb (W605).
  THE caller-layer disclosure already exists.
* ``tests/test_semantic_search.py`` — imports ``available_packs`` and
  ``search_pack_symbols`` for happy-path tests only. No warnings_out
  threading needed.

No cmd_*.py module imports ``framework_packs`` directly. (Note:
``src/roam/catalog/detectors.py`` defines its own unrelated
``_framework_packs`` helper for IO-effect-pack detection — different
substrate, different scope, NOT the same module.)

W89 / W604 / W605 SUBSTRATE UNTOUCHED
-------------------------------------

* ``src/roam/db/schema.py`` — read only, NOT modified.
* ``src/roam/db/findings.py`` — read only, NOT modified.
* ``src/roam/search/index_embeddings.py`` — read only, NOT modified.
* ``USER_VERSION = 17`` — unchanged.

LAW 4 note: warning kinds (when they would exist) are NOT
``agent_contract.facts`` strings and therefore not subject to the
concrete-noun-terminal lint.

ARC CLOSURE
-----------

W606 is the capstone of the W595-W606 Pattern-2 substrate-hardening
arc. With this NO-OP audit sealed:

* W595 (read_permit), W596 (read_run_meta), W597 (daemon_state),
  W598 (_load_cache), W599 (trace_ingest), W600 (config_hashes),
  W601 (signing key), W602 (metrics_push), W603 (db/connection),
  W604 (db/findings NO-OP), W605 (search/index_embeddings),
  W606 (search/framework_packs NO-OP)

The substrate floor for silent-fallback disclosure is now CLOSED.
Future Pattern-2 waves should target CONSUMER layers (cmd_*.py
threading their existing ``warnings_out`` buckets into JSON
envelopes) rather than producer-side substrates.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.search.framework_packs import (  # noqa: E402
    _PACK_DEFINITIONS,
    _PACK_ENTRIES,
    available_packs,
    search_pack_symbols,
)

# ===========================================================================
# (1) Happy path — clean readers return the right shape
# ===========================================================================


def test_available_packs_returns_known_packs() -> None:
    """``available_packs()`` returns the sorted curated-pack roster.

    Sanity baseline: the substrate is intact and the readers return
    the expected shape (positive baseline for the NO-OP decision).
    """
    packs = available_packs()
    assert isinstance(packs, list)
    assert len(packs) >= 8, f"expected at least 8 curated packs; got {len(packs)}: {packs!r}"
    # A sampling of the canonical roster (matches test_semantic_search.py).
    for must_have in ("django", "react", "python-stdlib", "flask", "fastapi"):
        assert must_have in packs, f"curated roster missing canonical pack {must_have!r}: {packs!r}"
    # Sorted invariant.
    assert packs == sorted(packs), f"available_packs() must return sorted output; got {packs!r}"


def test_search_pack_symbols_returns_semantic_hits() -> None:
    """A clean ``search_pack_symbols`` returns ranked hits with no surprises.

    Mirrors the test_semantic_search.py::test_pack_search_returns_semantic_hits
    contract — the W606 audit must not regress the public-API shape.
    """
    results = search_pack_symbols("django queryset prefetch related", top_k=5)
    assert results, "django-related query must produce pack hits"
    assert any(r["pack"] == "django" for r in results)
    assert all(r["source"] == "pack" for r in results)
    # Score ordering invariant (descending).
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True), f"pack results must be sorted by score desc; got {scores!r}"


# ===========================================================================
# (2) W978 POSITIVE COVERAGE — filter-driven empties stay silent
# ===========================================================================


def test_empty_query_silent() -> None:
    """``search_pack_symbols('')`` → empty list, NO marker.

    W978 positive coverage: an empty query is a legitimate filter, not
    a substrate failure. Mirrors W605's ``if not query_tokens: return
    []`` discipline at line ~635 of framework_packs.py.
    """
    results = search_pack_symbols("")
    assert results == [], f"empty query must return empty list silently; got {results!r}"


def test_whitespace_query_silent() -> None:
    """``search_pack_symbols('   ')`` → empty list (tokenizer strips whitespace)."""
    results = search_pack_symbols("   \t\n  ")
    assert results == []


def test_no_token_match_silent() -> None:
    """A query with no token match → empty list, NO marker.

    The corpus is healthy; the user just queried for a term that
    doesn't occur in any pack. Same legitimate-filter discipline as
    the empty-query case.
    """
    results = search_pack_symbols("zzzzz_definitely_no_match_xyzzy")
    # We don't assert results == [] because tokenizer prefix-matching
    # may still rank a low-confidence hit; we just assert that the
    # call doesn't raise.
    assert isinstance(results, list)


def test_unknown_pack_filter_silent() -> None:
    """``packs=['nonexistent']`` → empty list, NO marker.

    Filtering to a pack that doesn't exist is a legitimate filter
    (the caller explicitly opted out of every other pack). Silently
    returning empty is the correct contract.
    """
    results = search_pack_symbols("anything", packs=["nonexistent-pack-zzz"])
    assert results == [], f"unknown-pack filter must return empty list silently; got {results!r}"


# ===========================================================================
# (3) Substrate is fail-loud-by-construction (no try/except)
# ===========================================================================


def test_no_try_except_in_framework_packs_module() -> None:
    """AST-scan: ``framework_packs.py`` contains ZERO ``try/except`` blocks.

    The substrate is intentionally fail-loud-by-construction: every
    code path either runs to completion or propagates a Python error
    (KeyError, TypeError) loudly to the caller. The W605 caller-layer
    wrap in ``search_stored`` (``semantic_pack_search_failed``) already
    handles disclosure at the correct boundary.

    Adding a ``try/except`` here to plumb ``warnings_out`` would
    change that contract — it would swallow programmer errors that
    today raise.
    """
    src_path = repo_root() / "src" / "roam" / "search" / "framework_packs.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    try_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    assert try_nodes == [], (
        f"framework_packs.py is intentionally fail-loud-by-construction "
        f"but found {len(try_nodes)} ``try`` block(s) at lines "
        f"{[n.lineno for n in try_nodes]}. W606 audit conclusion: the "
        f"readers should NOT silently swallow errors. If a future wave "
        f"intentionally adds disk-loading + try/except, update this test "
        f"+ the W606 docstring with the rationale and the new closed-enum "
        f"marker."
    )


def test_no_raise_in_framework_packs_module() -> None:
    """AST-scan: ``framework_packs.py`` contains ZERO explicit ``raise`` statements.

    The substrate is pure dict/math operations — Python's built-in
    behaviour (KeyError, TypeError, AttributeError) is the loud
    failure mode. An explicit ``raise`` would suggest a guarded path
    worth disclosing via ``warnings_out``; today there are none.
    """
    src_path = repo_root() / "src" / "roam" / "search" / "framework_packs.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    raise_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert raise_nodes == [], (
        f"framework_packs.py is pure-math fail-loud-by-construction but "
        f"found {len(raise_nodes)} explicit ``raise`` statement(s) at "
        f"lines {[n.lineno for n in raise_nodes]}. If a future wave adds "
        f"an explicit raise, audit whether a ``warnings_out`` plumb on "
        f"a sibling guarded path is appropriate."
    )


def test_no_io_imports_in_framework_packs_module() -> None:
    """AST-scan: ``framework_packs.py`` imports NO I/O modules.

    The substrate is a static in-memory index. If a future wave adds
    ``os`` / ``pathlib`` / ``sqlite3`` / ``json`` / ``yaml`` imports,
    audit whether the new I/O path needs ``warnings_out`` plumbing.

    Allowed imports:
      * ``hashlib`` (stable symbol-id hashing)
      * ``math`` (TF-IDF math)
      * ``collections.Counter`` (token counting)
      * ``roam.search.tfidf`` (cosine + tokenize)
    """
    src_path = repo_root() / "src" / "roam" / "search" / "framework_packs.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden_imports = {
        "os",
        "os.path",
        "pathlib",
        "sqlite3",
        "json",
        "yaml",
        "urllib",
        "urllib.request",
        "requests",
        "shutil",
        "tempfile",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)

    violations = imported & forbidden_imports
    assert violations == set(), (
        f"framework_packs.py imports I/O module(s) {violations!r}. The "
        f"W606 NO-OP audit conclusion assumed pure in-memory operation. "
        f"If a future wave adds disk/network I/O, plumb the "
        f"``framework_pack_*`` marker family per the W606 docstring."
    )


# ===========================================================================
# (4) Reader signatures DO NOT carry warnings_out (audit-only seal)
# ===========================================================================


def test_reader_signatures_have_no_warnings_out() -> None:
    """AST-check: the W606 readers DO NOT acquire ``warnings_out`` params.

    Pins the W606 no-op audit conclusion. If a future wave intentionally
    plumbs ``warnings_out`` onto one of these readers (because a real
    substrate-failure silent path emerges — e.g., disk-loading user
    packs from ``.roam/packs/``), update this test with the rationale +
    the new closed-enum marker shape from the W606 brief.

    Sealed readers (no warnings_out):
      * ``available_packs``
      * ``search_pack_symbols``
      * ``_compile_entries`` (module-init helper, runs once)
      * ``_build_doc_text`` (pure string helper)
      * ``_stable_pack_symbol_id`` (pure hash helper)
    """
    src_path = repo_root() / "src" / "roam" / "search" / "framework_packs.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {
        "available_packs",
        "search_pack_symbols",
        "_compile_entries",
        "_build_doc_text",
        "_stable_pack_symbol_id",
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found.add(node.name)
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            regular_names = [a.arg for a in node.args.args]
            all_params = set(kwonly_names) | set(regular_names)
            assert "warnings_out" not in all_params, (
                f"{node.name} acquired a ``warnings_out`` param — W606 "
                f"audit conclusion was NO-OP. If you intentionally "
                f"plumbed this, update "
                f"tests/test_w606_framework_packs_warnings_out.py with "
                f"the rationale + the new closed-enum marker."
            )

    missing = targets - found
    assert not missing, f"expected to find functions {missing!r} in framework_packs.py"


# ===========================================================================
# (5) Caller audit — W605 search_stored owns disclosure
# ===========================================================================


def test_search_stored_owns_pack_failure_disclosure() -> None:
    """``search_stored`` already plumbs ``semantic_pack_search_failed:``.

    W605 cross-reference: the caller-layer wrap is the correct
    disclosure boundary for pack failures. This test pins that the
    W605 plumb is intact and W606 doesn't need to duplicate it on the
    producer side.
    """
    src_path = repo_root() / "src" / "roam" / "search" / "index_embeddings.py"
    source = src_path.read_text(encoding="utf-8")
    assert "semantic_pack_search_failed:" in source, (
        "W605 marker ``semantic_pack_search_failed:`` must exist in "
        "search/index_embeddings.py — W606 cross-reference depends on "
        "the caller-layer disclosure being intact."
    )
    # The try/except wrap MUST be present in search_stored.
    # The pack-search wrap was factored out of ``search_stored`` into a
    # helper (``_merge_language_relevant_pack_results``) — the W605 contract
    # is delegation-shaped now. Follow the chain: find the function whose
    # Try body calls ``search_pack_symbols`` (wherever it lives), then
    # assert ``search_stored`` reaches it (directly or via that helper).
    tree = ast.parse(source)

    def _call_names(node) -> set[str]:
        names = set()
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Call):
                fn = stmt.func
                if isinstance(fn, ast.Name):
                    names.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    names.add(fn.attr)
        return names

    wrap_owner = None
    search_stored_calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "search_stored":
            search_stored_calls = _call_names(node)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Try) and "search_pack_symbols" in _call_names(inner):
                wrap_owner = node.name
    assert wrap_owner is not None, (
        "No function wraps search_pack_symbols in a try/except — W605 "
        "caller-layer plumb is the disclosure boundary for pack failures. "
        "If this regressed, restore the W605 try/except."
    )
    assert wrap_owner == "search_stored" or wrap_owner in search_stored_calls, (
        f"The try/except wrap lives in {wrap_owner!r} but search_stored "
        f"does not call it — the W605 disclosure boundary is disconnected "
        f"from the search_stored path. Re-wire the helper call."
    )


def test_no_direct_cmd_caller_for_framework_packs() -> None:
    """AST-check: no ``src/roam/commands/cmd_*.py`` imports framework_packs.

    The only intended consumer is ``search/index_embeddings.py``
    (W605 caller boundary). If a cmd_*.py begins importing
    framework_packs directly, audit whether the caller-layer
    disclosure pattern needs to be replicated.

    Note: ``catalog/detectors.py`` defines its own unrelated
    ``_framework_packs`` (IO-effect packs); that is NOT the same
    module and is intentionally excluded from this audit.
    """
    cmd_dir = repo_root() / "src" / "roam" / "commands"
    violations: list[str] = []
    for cmd_file in cmd_dir.glob("cmd_*.py"):
        source = cmd_file.read_text(encoding="utf-8")
        if "roam.search.framework_packs" in source:
            violations.append(str(cmd_file.relative_to(repo_root())))
        elif "from roam.search.framework_packs" in source:
            violations.append(str(cmd_file.relative_to(repo_root())))
    assert violations == [], (
        f"unexpected direct framework_packs caller(s) in commands/: "
        f"{violations!r}. The W605 caller boundary in "
        f"search/index_embeddings.py::search_stored owns disclosure; "
        f"a direct cmd_*.py call would bypass that. Update this test "
        f"if intentional."
    )


# ===========================================================================
# (6) Default-args invocation never crashes (back-compat seal)
# ===========================================================================


def test_default_args_no_crash() -> None:
    """Calling the readers with no kwargs works on every shape.

    The W606 readers have ZERO new parameters; this test pins that the
    pre-W606 caller signatures are unaffected.
    """
    # Reader 1: available_packs — no args.
    packs = available_packs()
    assert isinstance(packs, list)
    assert len(packs) >= 1

    # Reader 2: search_pack_symbols — default args.
    results = search_pack_symbols("django")
    assert isinstance(results, list)

    # Reader 2: search_pack_symbols — every supported kwarg.
    results = search_pack_symbols("django", top_k=3)
    assert isinstance(results, list)
    assert len(results) <= 3

    results = search_pack_symbols("django", top_k=3, packs=["django"])
    assert isinstance(results, list)
    assert all(r["pack"] == "django" for r in results)

    # Empty packs list = explicit no-filter (truthy check); we don't
    # assert specific behaviour, just no crash.
    _ = search_pack_symbols("django", packs=[])


# ===========================================================================
# (7) W603 / W604 / W605 / W606 prefix family consistency
# ===========================================================================


def test_w603_w604_w605_w606_prefix_family_distinct() -> None:
    """The four substrate prefix families are distinct + correctly placed.

    * ``roam_*``           W603 — db/connection.py (DB substrate)
    * ``findings_*``       W604 — db/findings.py (EMPTY, NO-OP)
    * ``semantic_*``       W605 — search/index_embeddings.py (semantic search)
    * ``framework_pack_*`` W606 — search/framework_packs.py (EMPTY, NO-OP)

    This test pins:
    1. W603 + W605 markers are present in their owning files.
    2. W604 + W606 ARE empty (no markers in their owning files).
    3. No prefix family bleeds into the wrong substrate.
    """
    # W603 markers in db/connection.py.
    conn_src = (repo_root() / "src" / "roam" / "db" / "connection.py").read_text(encoding="utf-8")
    assert "roam_fts_drop_failed:" in conn_src, (
        "W603 marker ``roam_fts_drop_failed:`` missing from db/connection.py — W606 cross-reference depends on it."
    )
    assert "roam_fts_create_failed:" in conn_src, (
        "W603 marker ``roam_fts_create_failed:`` missing from db/connection.py — W606 cross-reference depends on it."
    )

    # W605 markers in search/index_embeddings.py.
    ie_src = (repo_root() / "src" / "roam" / "search" / "index_embeddings.py").read_text(encoding="utf-8")
    for marker in (
        "semantic_fts_check_failed:",
        "semantic_pack_search_failed:",
    ):
        assert marker in ie_src, f"W605 marker {marker!r} missing from search/index_embeddings.py."

    # W604 EMPTY in db/findings.py.
    findings_src = (repo_root() / "src" / "roam" / "db" / "findings.py").read_text(encoding="utf-8")
    for forbidden in ("findings_query_failed:", "findings_schema_mismatch:"):
        assert forbidden not in findings_src, (
            f"W604 forbidden marker {forbidden!r} present in db/findings.py — the W604 NO-OP audit was violated."
        )

    # W606 EMPTY in search/framework_packs.py.
    fp_src = (repo_root() / "src" / "roam" / "search" / "framework_packs.py").read_text(encoding="utf-8")
    for forbidden in (
        "framework_pack_dir_missing:",
        "framework_pack_metadata_corrupt:",
        "framework_pack_schema_mismatch:",
        "framework_pack_search_failed:",
    ):
        assert forbidden not in fp_src, (
            f"W606 forbidden marker {forbidden!r} present in "
            f"search/framework_packs.py — the W606 NO-OP audit was "
            f"violated. If a future wave adds disk-loading or metadata "
            f"parsing that warrants this marker, update the W606 "
            f"docstring with the rationale."
        )

    # No prefix bleed: framework_pack_* must NOT appear in unrelated
    # substrate files (search/index_embeddings.py is the W605 layer
    # and uses ``semantic_pack_search_failed`` instead).
    for substrate_src, owner in [
        (conn_src, "db/connection.py"),
        (findings_src, "db/findings.py"),
        (ie_src, "search/index_embeddings.py"),
    ]:
        assert "framework_pack_" not in substrate_src, (
            f"unrelated substrate {owner} contains ``framework_pack_*`` "
            f"marker prefix — that family is reserved for "
            f"search/framework_packs.py (currently empty per W606)."
        )


# ===========================================================================
# (8) Closed-enum subset — W978 first-hypothesis discipline (no markers)
# ===========================================================================


def test_closed_enum_subset_w606() -> None:
    """AST-check ``framework_packs.py`` for the W606 EMPTY closed-enum set.

    W978 first-hypothesis discipline (STRONG variant for substrate
    audits): every emitted marker must correspond to a real silent-
    fail code path. Inventing markers that no path can ever emit adds
    dead vocabulary that contaminates the audit-trail surface.

    The expected closed enum after W606: **EMPTY**. The
    framework-packs substrate has zero substrate-failure silent paths
    to plumb. Forbidden markers — paths that DO NOT exist in
    ``framework_packs.py``:

      * ``framework_pack_dir_missing:``    — no disk scan
      * ``framework_pack_metadata_corrupt:`` — no metadata parse
      * ``framework_pack_schema_mismatch:`` — no schema validation
      * ``framework_pack_search_failed:``  — caller-layer
        (``semantic_pack_search_failed`` per W605) is the right boundary
      * ``framework_pack_compile_failed:`` — _compile_entries runs at
        import time; failure crashes import, not silently
      * ``warnings_out:``                  — generic guard
    """
    src_path = repo_root() / "src" / "roam" / "search" / "framework_packs.py"
    source = src_path.read_text(encoding="utf-8")

    forbidden_markers = {
        "framework_pack_dir_missing:",
        "framework_pack_metadata_corrupt:",
        "framework_pack_schema_mismatch:",
        "framework_pack_search_failed:",
        "framework_pack_compile_failed:",
        # Also forbid the generic warnings_out marker family from
        # accidentally landing in framework_packs.py.
        "warnings_out:",
    }
    for marker in forbidden_markers:
        assert marker not in source, (
            f"forbidden marker prefix {marker!r} present in "
            f"search/framework_packs.py — this marker has no "
            f"corresponding silent-pass code path. W978 first-hypothesis "
            f"discipline: only plumb markers for paths that actually "
            f"exist."
        )


# ===========================================================================
# (9) W89 / W604 / W605 substrate UNTOUCHED
# ===========================================================================


def test_w89_substrate_untouched() -> None:
    """W89 USER_VERSION + canonical schema invariant unchanged by W606.

    W606 lives on the framework-packs substrate (pure in-memory
    static index); W89 lives on the schema-version substrate. They
    share no code.
    """
    from roam.db.connection import USER_VERSION

    assert USER_VERSION == 18, (
        f"W89 substrate invariant: USER_VERSION must stay at canonical "
        f"value (18 since the B8 snapshots.spectral_gap migration); got "
        f"{USER_VERSION}. W606 must not bump this."
    )

    schema_src = (repo_root() / "src" / "roam" / "db" / "schema.py").read_text(encoding="utf-8")
    for must_have in ("files", "symbols", "edges"):
        assert f"CREATE TABLE IF NOT EXISTS {must_have}" in schema_src, (
            f"schema.py missing canonical core table {must_have!r} — W606 must not have touched the schema substrate."
        )


def test_w604_substrate_untouched() -> None:
    """W604 (db/findings.py) substrate boundary preserved.

    Per the W606 brief, db/findings.py is sibling territory and must
    remain unmodified by this wave.
    """
    findings_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    if not findings_path.exists():
        pytest.skip("db/findings.py not present in this build")
    src = findings_path.read_text(encoding="utf-8")
    assert "emit_finding" in src, (
        "W604 substrate marker: ``emit_finding`` must exist in db/findings.py — W606 should not have touched it."
    )


def test_w605_substrate_untouched() -> None:
    """W605 (search/index_embeddings.py) substrate boundary preserved.

    Per the W606 brief, ``index_embeddings.py`` is the most recently-
    sealed sibling and must remain unmodified by this wave.
    """
    ie_path = repo_root() / "src" / "roam" / "search" / "index_embeddings.py"
    if not ie_path.exists():
        pytest.skip("search/index_embeddings.py not present in this build")
    src = ie_path.read_text(encoding="utf-8")
    # All 6 W605 closed-enum prefixes must still be present.
    for marker in (
        "semantic_fts_check_failed:",
        "semantic_tfidf_check_failed:",
        "semantic_onnx_check_failed:",
        "semantic_fts_query_failed:",
        "semantic_vector_decode_failed:",
        "semantic_pack_search_failed:",
    ):
        assert marker in src, (
            f"W605 marker {marker!r} missing from search/index_embeddings.py — W606 must not have touched it."
        )


# ===========================================================================
# (10) Module-import baseline — _PACK_ENTRIES compiled at import time
# ===========================================================================


def test_pack_entries_compiled_at_import() -> None:
    """``_PACK_ENTRIES`` is populated at module import (no lazy loading).

    Confirms that the substrate is a pure in-memory index: failure
    would crash at import time (fail-loud-by-construction), not
    silently degrade. This is the inverse of "silent fallback":
    there's no path for the substrate to be partially-built and
    quietly return empty.
    """
    assert isinstance(_PACK_ENTRIES, list)
    assert len(_PACK_ENTRIES) > 0, (
        "_PACK_ENTRIES must be populated at import time; an empty "
        "compiled index would indicate _PACK_DEFINITIONS is broken."
    )
    # Each entry has the expected canonical shape.
    for entry in _PACK_ENTRIES:
        for must_have in ("pack", "name", "kind", "vector", "symbol_id", "file_path"):
            assert must_have in entry, f"_PACK_ENTRIES row missing canonical field {must_have!r}: {entry!r}"


def test_pack_definitions_static_in_source() -> None:
    """``_PACK_DEFINITIONS`` is a Python dict literal in source.

    Pins the "no disk scan" invariant — the module is fail-loud-by-
    construction because the packs are compiled in at source level.
    A future wave that adds dynamic pack-loading from disk must:

    1. Update this test (replace the "static-only" assertion).
    2. Add ``framework_pack_*`` marker plumbing per the W606 brief.
    3. Update the W606 docstring with the new disclosure rationale.
    """
    assert isinstance(_PACK_DEFINITIONS, dict)
    assert len(_PACK_DEFINITIONS) >= 8, (
        f"expected >= 8 curated packs in source; got "
        f"{len(_PACK_DEFINITIONS)}. If this dropped, audit whether a "
        f"future wave moved packs to disk (which would require W606+ "
        f"plumbing)."
    )
    # Each pack value is a list of entry-dict literals (no callables /
    # no lazy proxies).
    for pack_name, entries in _PACK_DEFINITIONS.items():
        assert isinstance(entries, list), (
            f"_PACK_DEFINITIONS[{pack_name!r}] must be a list of dicts; got {type(entries).__name__}"
        )
        for entry in entries:
            assert isinstance(entry, dict), (
                f"_PACK_DEFINITIONS[{pack_name!r}] entry must be a dict; got {type(entry).__name__}"
            )
