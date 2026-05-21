"""W605 — ``search/index_embeddings.py`` plumbs ``warnings_out`` on TF-IDF / FTS5 read paths.

The W595 / W596 / W597 / W598 / W599 / W600 / W601 / W602 / W603 / W604
Pattern-2 substrate-hardening arc closed silent-fallback disclosure
gaps on lease + permits + runs-ledger + runtime-daemon +
pr-analyze-cache + trace-ingest + config-hashes + signing +
metrics-push + db/connection + db/findings substrates.

W605 plumbs READ-side disclosure on the semantic-search substrate:
when ``search_stored`` / ``search_fts`` / ``load_*_vectors`` /
``fts5_populated`` / ``tfidf_populated`` / ``onnx_populated`` silently
mask substrate failure (corrupt sqlite_master, malformed FTS5 query,
broken JSON vector blob), the new ``warnings_out`` channel surfaces
the failure WHILE preserving the empty-list / zero-hits return
semantic for ~3 callers (cmd_search_semantic, cmd_retrieve,
retrieve.seeds) that haven't opted in yet.

W978 first-hypothesis discipline (STRONG for search substrate)
--------------------------------------------------------------

Semantic-search has many legitimately-empty paths. Decisions:

PLUMBED (silent-pass changes user-visible behaviour):

1. ``fts5_available`` (line ~58) — ``except Exception`` silently
   returns False, masking sqlite_master corruption / locked-DB.
   Marker: ``semantic_fts_check_failed:symbol_fts:<exc>:<detail>``.

2. ``fts5_populated`` (line ~72) — same shape on the COUNT query.
   Marker: ``semantic_fts_check_failed:symbol_fts_count:<exc>:<detail>``.

3. ``tfidf_populated`` (line ~84) — same shape on TF-IDF count.
   Marker: ``semantic_tfidf_check_failed:symbol_tfidf:<exc>:<detail>``.

4. ``onnx_populated`` (line ~96) — same shape on ONNX count.
   Marker: ``semantic_onnx_check_failed:symbol_embeddings:<exc>:<detail>``.

5. ``search_fts`` first-pass query (line ~313) — ``except Exception``
   falls through to prefix-only fallback; an invalid FTS5 expression
   on the input is disclosed even when the fallback rescues it.
   Marker: ``semantic_fts_query_failed:<query>:<exc>:<detail>``.

6. ``search_fts`` fallback query (line ~329) — both passes failed,
   caller sees empty AND a marker. Marker:
   ``semantic_fts_query_failed:<prefix_query>:fallback:<exc>:<detail>``.

7. ``load_onnx_vectors`` per-row JSON decode (line ~654) — silently
   drops corrupt vectors from the result. Marker:
   ``semantic_vector_decode_failed:onnx:<symbol_id>:<exc>:<detail>``.

8. ``load_tfidf_vectors`` per-row JSON decode (line ~787) — same
   shape. Marker:
   ``semantic_vector_decode_failed:tfidf:<symbol_id>:<exc>:<detail>``.

9. ``search_stored`` pack-search ImportError / pack failure
   (line ~520) — silent fallback to empty pack list. Marker:
   ``semantic_pack_search_failed:<exc>:<detail>``.

INTENTIONAL — NOT PLUMBED (W978 positive coverage):

* Empty query / whitespace query → empty list (legitimate filter).
* Empty corpus on first run → empty list (legitimate cold start,
  same discipline as W598 ``_load_cache`` cold-cache + W603
  config-missing).
* ONNX-not-ready / no embedder / empty query vector → empty list.
  The fallback-contracts arc (``test_fallback_contracts.py``) already
  discloses the degraded-but-correct contract loudly at the backend-
  readiness layer (``_onnx_ready``). Plumbing here would double-emit.
* ``build_and_store_onnx_embeddings`` write-side except (line 148 /
  193 / 245) — W531 narrow-by-comment, ONNX optional fallback.
  Out of W605 read-side scope (and W603 already covers write-side
  FTS5 with ``roam_fts_drop_failed`` / ``roam_fts_create_failed``).
* ``_fuse_hybrid_results`` empty-results pass-through — legitimate
  data-flow, not a silent substrate failure.

W603 / W604 cross-reference (prefix-family rationale)
------------------------------------------------------

W603 (write-side FTS5 substrate in db/connection.py) uses the
``roam_fts_*`` prefix family for write-side markers:
``roam_fts_drop_failed`` / ``roam_fts_create_failed``.

W605 (read-side semantic-search substrate in
search/index_embeddings.py) uses the ``semantic_*`` prefix family
intentionally. Rationale:

* DIFFERENT SUBSTRATE: W603 is schema-creation time; W605 is
  query/retrieval time. An operator triaging a marker should be
  able to tell from the prefix alone whether the failure happened
  during indexing or during search.
* PRECEDENT: every Pattern-2 wave to date scopes its marker prefix
  to the substrate, not the underlying table — W602
  ``metrics_push_*``, W598 ``pr_analyze_cache_*``, W596
  ``runs_signing_*``. ``semantic_*`` follows the same discipline.
* W604 (in flight on db/findings.py) is expected to land on the
  findings-registry READ substrate; its prefix family is
  ``findings_*`` per W604's own substrate scope. W603's
  ``roam_fts_*`` and W605's ``semantic_fts_*`` are NOT in conflict:
  they live on different layers and surface different signals.

Caller audit (W605 audit-only — no caller modifications)
---------------------------------------------------------

The plumbed helpers have these call-sites today (top traffic):

  * ``cmd_search_semantic.search_semantic`` (line ~66) —
    ``search_stored(conn, query, top_k=top_k, semantic_backend=backend)``.
    Does NOT thread warnings_out.
  * ``cmd_search`` (line ~50) — imports ``_build_fts_query``; does
    not call any plumbed helper directly.
  * ``retrieve.seeds`` (line ~468) — imports ``_camel_split`` only;
    does not call any plumbed helper.
  * ``index/indexer.py`` (line ~1800) — calls ``build_fts_index``
    and ``fts5_available`` during indexing. Write path; out of
    read-side scope.

A future wave can opt cmd_search_semantic / cmd_retrieve into
threading the bucket and surfacing markers on their JSON envelopes;
the producer-side substrate is now ready.

W89 substrate UNTOUCHED:

* ``src/roam/db/schema.py`` — read only, NOT modified.
* ``USER_VERSION`` constant — unchanged.
* The schema-version contract is unaffected; W605 lives on the
  search-substrate read path, not the schema substrate.

W604 substrate UNTOUCHED:

* ``src/roam/db/findings.py`` — NOT modified (sibling-agent territory).

Fallback-contracts preserved:

* numpy / onnxruntime absent → degraded-but-correct behaviour
  unchanged (``_onnx_ready`` returns False; the early returns in
  ``_search_onnx_stored`` stay silent because the degraded contract
  is already loud at the fallback-contracts layer).

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings
and therefore not subject to the concrete-noun-terminal lint. They
are internal diagnostic markers (same discipline as W589/W592/W593/
W595/W596/W597/W598/W599/W600/W601/W602/W603).

W907 verify-cycle check
-----------------------

The ``WarningsOut = list[str] | None`` alias is duplicated locally
in ``index_embeddings.py`` rather than imported from
``roam.output.formatter``. Verified that ``formatter.py`` has NO
top-level roam imports (``grep '^from roam' formatter.py`` returns
empty / only deferred function-body imports). The duplication is
a hot-path-cost choice (the search substrate is on every
``cmd_retrieve`` / ``cmd_search_semantic`` / ``retrieve.seeds`` path),
NOT a false cycle hedge. The W907 docstring on the alias states
this honestly.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.search.index_embeddings import (  # noqa: E402
    WarningsOut,
    fts5_available,
    fts5_populated,
    load_onnx_vectors,
    load_tfidf_vectors,
    onnx_populated,
    search_fts,
    search_stored,
    tfidf_populated,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fts5_db(tmp_path: Path) -> sqlite3.Connection:
    """A sqlite3 DB initialised with the canonical roam schema + a seed symbol.

    Uses ``roam.db.connection.ensure_schema`` so the full schema (matching
    ``USER_VERSION`` migrations) is in place — this matters because
    ``load_tfidf_vectors`` calls ``ensure_tfidf_table`` which runs the full
    ``SCHEMA_SQL`` and requires every canonical column to exist.
    """
    from roam.db.connection import ensure_schema

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/auth.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
        "docstring, kind, line_start, line_end) VALUES "
        "(1, 1, 'authenticate', 'auth.authenticate', 'def authenticate(user)', "
        "'auth helper', 'function', 1, 10)"
    )
    if _has_fts5(conn):
        try:
            conn.execute(
                "INSERT INTO symbol_fts(rowid, name, qualified_name, "
                "signature, docstring, kind, file_path) VALUES "
                "(1, 'authenticate', 'auth authenticate', "
                "'def authenticate user', 'auth helper', 'function', "
                "'src/auth.py')"
            )
        except sqlite3.OperationalError:
            # symbol_fts column layout may differ on older builds;
            # skip the FTS seed (tests that need it will check).
            pass
    conn.commit()
    return conn


def _has_fts5(conn: sqlite3.Connection) -> bool:
    """True iff the test build supports FTS5 (most modern sqlite ships it)."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        conn.execute("DROP TABLE _probe")
        return True
    except sqlite3.OperationalError:
        return False


# ===========================================================================
# (1) Happy path — clean search emits no warnings
# ===========================================================================


def test_clean_search_emits_no_warning(tmp_path: Path) -> None:
    """A clean search against a populated corpus → no warnings.

    Sanity check that the W605 plumb only fires on degenerate paths.
    """
    conn = _make_fts5_db(tmp_path)
    try:
        warnings: list[str] = []
        results = search_stored(conn, "authenticate", top_k=5, warnings_out=warnings)
        assert results, "populated corpus must produce results"
        assert warnings == [], f"clean search must NOT emit warnings; got {warnings!r}"
    finally:
        conn.close()


# ===========================================================================
# (2) Empty corpus is intentional-silent (W978 positive coverage)
# ===========================================================================


def test_empty_corpus_silent(tmp_path: Path) -> None:
    """Cold start (no symbols indexed) → empty results, NO marker.

    Mirrors W598 cold-cache + W602 missing-last-pr + W603 config-missing
    discipline. Disclosure on the common cold-start path would train
    operators to ignore real warnings.
    """
    from roam.db.connection import ensure_schema

    conn = sqlite3.connect(str(tmp_path / "cold.db"))
    conn.row_factory = sqlite3.Row
    # Canonical schema, no symbol rows — pure cold state.
    ensure_schema(conn)
    try:
        warnings: list[str] = []
        results = search_stored(conn, "anything", top_k=5, warnings_out=warnings)
        # Empty results expected; no markers either.
        assert results == [], results
        # Cold-state probes (empty tables, valid schema) must be silent.
        # Filter out any pack-pack ImportError that may surface on some
        # test environments without framework_packs assets.
        non_pack = [m for m in warnings if not m.startswith("semantic_pack_search_failed:")]
        assert non_pack == [], f"cold-start empty corpus must be SILENT; got {non_pack!r}."
    finally:
        conn.close()


# ===========================================================================
# (3) Filter no matches is intentional-silent
# ===========================================================================


def test_filter_no_matches_silent(tmp_path: Path) -> None:
    """A query with no matches in a populated corpus → empty, NO marker.

    The corpus is healthy; the user just queried for a term that doesn't
    occur. Same legitimate-filter discipline as the empty-corpus case.
    """
    conn = _make_fts5_db(tmp_path)
    try:
        warnings: list[str] = []
        # "zzzzz" doesn't occur in our fixture corpus.
        _ = search_stored(conn, "zzzzz_no_match", top_k=5, warnings_out=warnings)
        # We don't assert results == [] because hybrid fusion may still
        # return prefix-match hits; we just assert that any markers
        # emitted are NOT from substrate-failure paths.
        substrate_markers = [
            m
            for m in warnings
            if m.startswith("semantic_fts_check_failed:")
            or m.startswith("semantic_tfidf_check_failed:")
            or m.startswith("semantic_onnx_check_failed:")
            or m.startswith("semantic_vector_decode_failed:")
        ]
        assert substrate_markers == [], (
            f"filter-driven empty must NOT emit substrate-failure markers; got {substrate_markers!r}"
        )
    finally:
        conn.close()


# ===========================================================================
# (4) fts5_available substrate-failure emits marker
# ===========================================================================


def test_fts5_available_substrate_failure_emits_marker() -> None:
    """A failed sqlite_master probe → ``semantic_fts_check_failed``.

    Synthesise by injecting a connection whose ``execute`` raises
    ``sqlite3.DatabaseError`` on the substrate probe (a corrupted master
    table would surface the same error). The function still returns
    False (caller contract preserved).
    """

    class _BrokenConn:
        def execute(self, sql: str, *args, **kwargs):
            raise sqlite3.DatabaseError("synthetic-master-table-corruption from W605 test")

    warnings: list[str] = []
    result = fts5_available(_BrokenConn(), warnings_out=warnings)
    assert result is False
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("semantic_fts_check_failed:symbol_fts:"), msg
    assert "DatabaseError" in msg, msg
    assert "synthetic-master-table-corruption from W605 test" in msg, msg


# ===========================================================================
# (5) fts5_populated COUNT failure emits marker (after fts5_available passes)
# ===========================================================================


def test_fts5_populated_count_failure_emits_marker() -> None:
    """A failed COUNT(*) on symbol_fts → ``semantic_fts_check_failed:symbol_fts_count``.

    Synthesise by injecting a conn where sqlite_master returns a row
    (the table exists) BUT the COUNT raises OperationalError. The
    function still returns False.
    """

    class _CountBrokenConn:
        def __init__(self) -> None:
            self._stage = "select"

        def execute(self, sql: str, *args, **kwargs):
            stripped = sql.strip()
            if stripped.startswith("SELECT 1 FROM sqlite_master"):
                self._stage = "fetch_select"
                return self
            if "COUNT(*) FROM symbol_fts" in sql:
                raise sqlite3.OperationalError("synthetic-COUNT-failure from W605 test")
            raise AssertionError(f"unexpected sql: {sql!r}")

        def fetchone(self):
            return (1,)

    warnings: list[str] = []
    result = fts5_populated(_CountBrokenConn(), warnings_out=warnings)
    assert result is False
    # Expect EXACTLY one marker — for the COUNT failure, not the
    # sqlite_master probe.
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("semantic_fts_check_failed:symbol_fts_count:"), msg
    assert "OperationalError" in msg, msg


# ===========================================================================
# (6) tfidf_populated substrate-failure emits marker
# ===========================================================================


def test_tfidf_populated_substrate_failure_emits_marker() -> None:
    """A failed COUNT(*) on symbol_tfidf → ``semantic_tfidf_check_failed``."""

    class _TfidfBrokenConn:
        def execute(self, sql: str, *args, **kwargs):
            raise sqlite3.OperationalError("synthetic-tfidf-table-missing from W605 test")

    warnings: list[str] = []
    result = tfidf_populated(_TfidfBrokenConn(), warnings_out=warnings)
    assert result is False
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("semantic_tfidf_check_failed:symbol_tfidf:"), msg
    assert "OperationalError" in msg, msg


# ===========================================================================
# (7) onnx_populated substrate-failure emits marker
# ===========================================================================


def test_onnx_populated_substrate_failure_emits_marker() -> None:
    """A failed COUNT(*) on symbol_embeddings → ``semantic_onnx_check_failed``."""

    class _OnnxBrokenConn:
        def execute(self, sql: str, *args, **kwargs):
            raise sqlite3.OperationalError("synthetic-embeddings-table-missing from W605 test")

    warnings: list[str] = []
    result = onnx_populated(_OnnxBrokenConn(), warnings_out=warnings)
    assert result is False
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("semantic_onnx_check_failed:symbol_embeddings:"), msg
    assert "OperationalError" in msg, msg


# ===========================================================================
# (8) search_fts query failure emits marker (both passes)
# ===========================================================================


def test_search_fts_query_failed_emits_marker(tmp_path: Path) -> None:
    """A failing FTS5 query → ``semantic_fts_query_failed:<query>:...``.

    Synthesise by injecting a conn that raises ``OperationalError`` on
    EVERY FTS5 execute (covers both the first-pass and the prefix-only
    fallback). The function still returns [].
    """

    class _FtsQueryBrokenConn:
        def execute(self, sql: str, *args, **kwargs):
            raise sqlite3.OperationalError("synthetic-fts-query-failure from W605 test")

    warnings: list[str] = []
    result = search_fts(_FtsQueryBrokenConn(), "auth user", top_k=5, warnings_out=warnings)
    assert result == [], result
    # Expect 2 markers — first-pass + fallback. Both must use the
    # ``semantic_fts_query_failed:`` prefix.
    assert len(warnings) == 2, warnings
    assert warnings[0].startswith("semantic_fts_query_failed:"), warnings[0]
    assert "OperationalError" in warnings[0], warnings[0]
    assert warnings[1].startswith("semantic_fts_query_failed:"), warnings[1]
    assert ":fallback:" in warnings[1], warnings[1]
    assert "OperationalError" in warnings[1], warnings[1]


# ===========================================================================
# (9) load_onnx_vectors per-row decode failure emits marker
# ===========================================================================


def test_load_onnx_vectors_decode_failure_emits_marker(tmp_path: Path) -> None:
    """A corrupt JSON blob → ``semantic_vector_decode_failed:onnx:<sid>:...``.

    Inject a row with non-JSON ``vector`` data; the load drops it but
    discloses the drop via warnings_out.
    """
    conn = _make_fts5_db(tmp_path)
    try:
        conn.execute(
            "INSERT INTO symbol_embeddings (symbol_id, vector, dims, provider, model_id) VALUES (?, ?, ?, ?, ?)",
            (1, "definitely-not-json", 0, "onnx", "test-model"),
        )
        # Add a valid one as well — it should survive the load.
        conn.execute(
            "INSERT INTO symbol_embeddings (symbol_id, vector, dims, provider, model_id) VALUES (?, ?, ?, ?, ?)",
            (2, json.dumps([0.1, 0.2, 0.3]), 3, "onnx", "test-model"),
        )
        conn.commit()

        warnings: list[str] = []
        vectors = load_onnx_vectors(conn, warnings_out=warnings)
        # The corrupt row is silently dropped from the result; the
        # valid row survives. Caller contract preserved.
        assert 1 not in vectors, vectors
        assert 2 in vectors, vectors
        # Marker disclosure on the dropped row.
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        assert msg.startswith("semantic_vector_decode_failed:onnx:1:"), msg
        # JSONDecodeError or TypeError depending on python build.
        assert "JSONDecodeError" in msg or "TypeError" in msg, msg
    finally:
        conn.close()


# ===========================================================================
# (10) load_tfidf_vectors per-row decode failure emits marker
# ===========================================================================


def test_load_tfidf_vectors_decode_failure_emits_marker(tmp_path: Path) -> None:
    """A corrupt JSON blob → ``semantic_vector_decode_failed:tfidf:<sid>:...``."""
    conn = _make_fts5_db(tmp_path)
    try:
        conn.execute(
            "INSERT INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
            (1, "definitely-not-json"),
        )
        conn.execute(
            "INSERT INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
            (2, json.dumps({"auth": 0.7, "user": 0.3})),
        )
        conn.commit()

        warnings: list[str] = []
        vectors = load_tfidf_vectors(conn, warnings_out=warnings)
        assert 1 not in vectors, vectors
        assert 2 in vectors, vectors
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        assert msg.startswith("semantic_vector_decode_failed:tfidf:1:"), msg
    finally:
        conn.close()


# ===========================================================================
# (11) Default warnings_out=None preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(tmp_path: Path) -> None:
    """Calling without ``warnings_out`` works on every failure mode.

    The ~3 callers of these helpers (cmd_search_semantic, cmd_retrieve,
    retrieve.seeds) call with no kwarg and MUST NOT regress.
    """
    # (a) Clean default-args path against populated corpus.
    conn = _make_fts5_db(tmp_path)
    try:
        results = search_stored(conn, "authenticate", top_k=5)
        assert results, "default-args search must still return hits"
    finally:
        conn.close()

    # (b) Default-args probe helpers on a broken conn.
    class _BrokenConn:
        def execute(self, sql: str, *args, **kwargs):
            raise sqlite3.DatabaseError("synthetic")

    assert fts5_available(_BrokenConn()) is False
    assert fts5_populated(_BrokenConn()) is False
    assert tfidf_populated(_BrokenConn()) is False
    assert onnx_populated(_BrokenConn()) is False

    # (c) Default-args search_fts on a broken conn.
    class _FtsBroken:
        def execute(self, sql: str, *args, **kwargs):
            raise sqlite3.OperationalError("synthetic")

    assert search_fts(_FtsBroken(), "auth", top_k=5) == []


# ===========================================================================
# (12) Caller audit — no caller threads warnings_out today
# ===========================================================================


def test_cmd_search_semantic_threads_warnings_out() -> None:
    """AST-check ``cmd_search_semantic.py`` — DOES thread warnings_out (W607-A).

    W605 was producer-side / audit-only. W607-A is the first consumer-
    layer opt-in: cmd_search_semantic now threads warnings_out into
    ``search_stored`` and surfaces markers on the JSON envelope (see
    ``tests/test_w607_a_cmd_search_semantic_warnings_out_envelope.py``
    for the envelope-shape pin). This drift guard inverts the original
    W605 audit-only assertion: future regressions that DROP the
    threading break the guard with a pointer back to W607-A.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_search_semantic.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    found_threaded_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == "search_stored":
                kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                if "warnings_out" in kwarg_names:
                    found_threaded_call = True
                    break
    assert found_threaded_call, (
        "cmd_search_semantic.py must thread warnings_out into "
        "search_stored (W607-A consumer-layer Pattern-2 disclosure). "
        "If you intentionally dropped the threading, update "
        "tests/test_w607_a_cmd_search_semantic_warnings_out_envelope.py "
        "to match the new contract first."
    )


def test_cmd_search_unmodified() -> None:
    """AST-check ``cmd_search.py`` — does not thread warnings_out.

    cmd_search uses ``_build_fts_query`` (helper, not plumbed) and its
    own local ``_fts5_available`` (separate code path). Pin that it
    does not now call any plumbed helper with warnings_out.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_search.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name in {
                "search_stored",
                "search_fts",
                "load_onnx_vectors",
                "load_tfidf_vectors",
                "fts5_populated",
                "tfidf_populated",
                "onnx_populated",
            }:
                kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                assert "warnings_out" not in kwarg_names, (
                    f"cmd_search.py now threads warnings_out into {name} at "
                    f"line {node.lineno}; W605 was audit-only — update this "
                    f"test if intentionally opted in."
                )


def test_retrieve_seeds_unmodified() -> None:
    """AST-check ``retrieve/seeds.py`` — uses only ``_camel_split``.

    seeds.py imports ``_camel_split`` (a pure-string helper not plumbed
    by W605). Pin that it does not now call any plumbed helper.
    """
    src_path = repo_root() / "src" / "roam" / "retrieve" / "seeds.py"
    if not src_path.exists():
        pytest.skip("retrieve/seeds.py not present in this build")
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name in {
                "search_stored",
                "search_fts",
                "load_onnx_vectors",
                "load_tfidf_vectors",
                "fts5_populated",
                "tfidf_populated",
                "onnx_populated",
            }:
                kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                assert "warnings_out" not in kwarg_names, (
                    f"retrieve/seeds.py now threads warnings_out into {name} "
                    f"at line {node.lineno}; W605 was audit-only — update "
                    f"this test if intentionally opted in."
                )


# ===========================================================================
# (13) W603 / W604 cross-reference — marker prefix family consistency
# ===========================================================================


def test_w603_w604_fts_marker_consistency() -> None:
    """W603 uses ``roam_fts_*`` for write-side; W605 uses ``semantic_*``.

    Rationale (also documented at the top of this file): different
    substrates, different prefix families. W603's
    ``roam_fts_drop_failed`` / ``roam_fts_create_failed`` are
    indexing-time markers; W605's ``semantic_fts_*`` /
    ``semantic_tfidf_*`` / ``semantic_onnx_*`` /
    ``semantic_vector_decode_failed:`` are retrieval-time markers.
    """
    # W603 substrate (read only — pin the markers remain).
    conn_src = (repo_root() / "src" / "roam" / "db" / "connection.py").read_text(encoding="utf-8")
    assert "roam_fts_drop_failed" in conn_src, "W603 marker roam_fts_drop_failed missing from db/connection.py"
    assert "roam_fts_create_failed" in conn_src, "W603 marker roam_fts_create_failed missing from db/connection.py"

    # W605 substrate (READ semantic side — distinct prefix family).
    ie_src = (repo_root() / "src" / "roam" / "search" / "index_embeddings.py").read_text(encoding="utf-8")
    for marker in (
        "semantic_fts_check_failed:",
        "semantic_tfidf_check_failed:",
        "semantic_onnx_check_failed:",
        "semantic_fts_query_failed:",
        "semantic_vector_decode_failed:",
        "semantic_pack_search_failed:",
    ):
        assert marker in ie_src, f"W605 marker {marker!r} missing from search/index_embeddings.py"

    # W605 must NOT reuse W603's write-side prefix family — those are
    # for indexing-time signals, not retrieval-time signals.
    assert "roam_fts_drop_failed" not in ie_src, "W605 must not emit W603's write-side marker on the read path"
    assert "roam_fts_create_failed" not in ie_src, "W605 must not emit W603's write-side marker on the read path"


# ===========================================================================
# (14) Fallback-contract preserved — degraded-but-correct semantics
# ===========================================================================


def test_fallback_contract_preserved(tmp_path: Path) -> None:
    """ONNX-not-ready / numpy-absent → degraded-but-correct, no marker.

    The fallback-contracts arc guarantees: when the ONNX backend is
    not configured / not installed, the search substrate falls back
    to TF-IDF and returns correct results. W605 plumbing must NOT
    change that contract — the degraded path is already loud at the
    backend-readiness layer (``_onnx_ready``).

    We simulate the degraded path by passing an explicit
    ``semantic_backend='tfidf'`` against a corpus that has only
    TF-IDF data; the ONNX branch is skipped silently.
    """
    conn = _make_fts5_db(tmp_path)
    try:
        # Populate ONLY the TF-IDF table.
        conn.execute(
            "INSERT INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
            (1, json.dumps({"authent": 1.0, "user": 0.5})),
        )
        conn.commit()

        warnings: list[str] = []
        results = search_stored(
            conn,
            "authenticate",
            top_k=5,
            semantic_backend="tfidf",
            warnings_out=warnings,
        )
        # TF-IDF path still produces hits (degraded-but-correct).
        # The exact match count depends on tokenization; we assert
        # the contract: no substrate-failure markers fired.
        substrate_failures = [
            m
            for m in warnings
            if m.startswith("semantic_fts_check_failed:")
            or m.startswith("semantic_tfidf_check_failed:")
            or m.startswith("semantic_onnx_check_failed:")
            or m.startswith("semantic_vector_decode_failed:")
            or m.startswith("semantic_fts_query_failed:")
        ]
        assert substrate_failures == [], (
            f"ONNX-absent + TF-IDF fallback must NOT emit substrate-failure markers; got {substrate_failures!r}"
        )
        # Note: ``results`` may be empty if the test tokenization /
        # IDF computation doesn't surface a hit; that's a property
        # of the TF-IDF helper, not a substrate failure.
        _ = results
    finally:
        conn.close()


# ===========================================================================
# (15) W89 substrate UNTOUCHED — AST-check schema.py boundaries
# ===========================================================================


def test_w89_substrate_untouched() -> None:
    """W89 USER_VERSION + canonical schema invariant unchanged by W605.

    W605 lives on the search substrate (read path); W89 lives on the
    schema-version substrate. They share no code. This test pins that
    USER_VERSION stays at the canonical contract value and the core
    tables are intact.
    """
    from roam.db.connection import USER_VERSION

    assert USER_VERSION == 18, (
        f"W89 substrate invariant: USER_VERSION must stay at canonical "
        f"value (18 since the B8 snapshots.spectral_gap migration); got "
        f"{USER_VERSION}. W605 must not bump this."
    )

    schema_src = (repo_root() / "src" / "roam" / "db" / "schema.py").read_text(encoding="utf-8")
    for must_have in ("files", "symbols", "edges"):
        assert f"CREATE TABLE IF NOT EXISTS {must_have}" in schema_src, (
            f"schema.py missing canonical core table {must_have!r} — W605 must not have touched the schema substrate."
        )


# ===========================================================================
# (16) W604 substrate UNTOUCHED — findings.py boundary
# ===========================================================================


def test_w604_substrate_untouched() -> None:
    """W604 (in flight) lives on db/findings.py; W605 must not touch it.

    Per the W605 brief, db/findings.py is sibling-agent territory and
    must remain unmodified by this wave. We can't pin specific markers
    (W604 hasn't landed) but we can assert the file exists and contains
    its canonical export (``emit_finding``) — W605 has no reason to
    have edited it.
    """
    findings_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    if not findings_path.exists():
        pytest.skip("db/findings.py not present in this build")
    src = findings_path.read_text(encoding="utf-8")
    assert "emit_finding" in src, (
        "W604 substrate marker: emit_finding must exist in db/findings.py — W605 should not have touched it."
    )


# ===========================================================================
# (17) Closed-enum subset — W978 first-hypothesis discipline
# ===========================================================================


def test_closed_enum_subset() -> None:
    """AST-check ``index_embeddings.py`` for the exact W605 closed-enum set.

    W978 first-hypothesis discipline: every emitted marker must
    correspond to a real silent-fail code path. Inventing markers
    that no path can ever emit adds dead vocabulary that contaminates
    the audit-trail surface.

    The expected closed enum after W605:

      * ``semantic_fts_check_failed:``
      * ``semantic_tfidf_check_failed:``
      * ``semantic_onnx_check_failed:``
      * ``semantic_fts_query_failed:``
      * ``semantic_vector_decode_failed:``
      * ``semantic_pack_search_failed:``

    Forbidden markers — shapes that DO NOT correspond to a silent-pass
    code path in index_embeddings.py:

      * ``semantic_corpus_empty:`` — empty corpus is INTENTIONAL silent
        (legitimate cold start); plumbing here would train operators
        to ignore real warnings (W978 positive coverage).
      * ``semantic_corpus_load_failed:`` — no separate corpus-load
        site exists in the read path; build_corpus runs on the write
        side (build_and_store_tfidf), not the read side.
      * ``semantic_embeddings_absent:`` — handled at the backend-
        readiness layer (``_onnx_ready``), not the search layer.
        Plumbing here would double-emit.
      * ``semantic_search_query_failed:`` — generic; the actual marker
        is the more-specific ``semantic_fts_query_failed:`` because
        the only retrieval-path silent-pass is the FTS5 query.
    """
    src_path = repo_root() / "src" / "roam" / "search" / "index_embeddings.py"
    source = src_path.read_text(encoding="utf-8")

    expected_markers = {
        "semantic_fts_check_failed:",
        "semantic_tfidf_check_failed:",
        "semantic_onnx_check_failed:",
        "semantic_fts_query_failed:",
        "semantic_vector_decode_failed:",
        "semantic_pack_search_failed:",
    }
    forbidden_markers = {
        "semantic_corpus_empty:",
        "semantic_corpus_load_failed:",
        "semantic_embeddings_absent:",
        "semantic_search_query_failed:",
    }

    for marker in expected_markers:
        assert marker in source, (
            f"expected marker prefix {marker!r} missing from "
            f"search/index_embeddings.py — did the W605 plumb get reverted?"
        )
    for marker in forbidden_markers:
        assert marker not in source, (
            f"forbidden marker prefix {marker!r} present in "
            f"search/index_embeddings.py — this marker has no corresponding "
            f"silent-pass code path. W978 first-hypothesis discipline: "
            f"only plumb markers for paths that actually exist."
        )


# ===========================================================================
# (18) Function-signature audit — kw-only warnings_out
# ===========================================================================


def test_signatures_carry_kw_only_warnings_out() -> None:
    """AST-check every plumbed helper declares warnings_out kw-only.

    Kw-only declaration is the back-compat-preserving signal that
    existing positional callers are unaffected. Matches
    W598/W599/W600/W601/W602/W603 signature-audit patterns.
    """
    src_path = repo_root() / "src" / "roam" / "search" / "index_embeddings.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {
        "fts5_available",
        "fts5_populated",
        "tfidf_populated",
        "onnx_populated",
        "search_fts",
        "search_stored",
        "load_onnx_vectors",
        "load_tfidf_vectors",
        "_search_onnx_stored",
        "_search_tfidf_stored",
    }
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found.add(node.name)
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" in kwonly_names, (
                f"{node.name} must declare ``warnings_out`` as a kw-only parameter (got kwonly={kwonly_names!r})"
            )

    missing = targets - found
    assert not missing, f"expected to find functions {missing!r} in index_embeddings.py"


# ===========================================================================
# (19) WarningsOut alias exported
# ===========================================================================


def test_warnings_out_alias_exported() -> None:
    """``WarningsOut`` is exported as ``list[str] | None``.

    Pins the substrate-floor type contract — callers that import
    ``WarningsOut`` from index_embeddings get the canonical alias.
    """
    args = getattr(WarningsOut, "__args__", None)
    assert args is not None, "WarningsOut must be a Union type"
    type_names = {getattr(a, "__name__", repr(a)) for a in args}
    assert "list" in type_names or "List" in type_names, type_names
    assert "NoneType" in type_names or type(None) in args, type_names


# ===========================================================================
# (20) End-to-end: search_stored threads warnings through sub-helpers
# ===========================================================================


def test_search_stored_threads_warnings_through(tmp_path: Path) -> None:
    """End-to-end: ``search_stored(warnings_out=...)`` propagates to sub-helpers.

    Inject a corrupt TF-IDF row — search_stored → tfidf_populated →
    _search_tfidf_stored → load_tfidf_vectors emits the decode marker
    on the caller's bucket without intermediate loss.
    """
    conn = _make_fts5_db(tmp_path)
    try:
        # Inject a corrupt TF-IDF row + a valid one so tfidf_populated
        # returns True.
        conn.execute(
            "INSERT INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
            (1, "definitely-not-json"),
        )
        conn.execute(
            "INSERT INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
            (2, json.dumps({"authent": 1.0})),
        )
        conn.commit()

        warnings: list[str] = []
        _ = search_stored(
            conn,
            "authenticate",
            top_k=5,
            semantic_backend="tfidf",
            warnings_out=warnings,
        )
        # The corrupt-row marker must surface on the top-level bucket.
        decode_markers = [m for m in warnings if m.startswith("semantic_vector_decode_failed:tfidf:1:")]
        assert decode_markers, (
            f"search_stored must surface the load_tfidf_vectors marker on the caller's bucket; got {warnings!r}"
        )
    finally:
        conn.close()
