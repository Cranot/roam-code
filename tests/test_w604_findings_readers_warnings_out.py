"""W604 — ``db/findings.py`` reader audit: NO-OP wave, all silents legitimate.

The W595 / W596 / W597 / W598 / W599 / W600 / W601 / W602 / W603
Pattern-2 substrate-hardening arc closed silent-fallback disclosure
gaps on the lease + permits + runs-ledger + runtime-daemon +
pr-analyze-cache + trace-ingest + config-hashes + signing +
metrics-push + db-connection substrates. W604 audited the findings
registry substrate (``src/roam/db/findings.py``) — the W89 substrate
that ~26 detectors persist into and that ``roam findings`` consumes.

W978 first-hypothesis decision (STRONG, read source IN FULL)
------------------------------------------------------------

Read ``db/findings.py`` end-to-end (421 lines). Categorised every
silent-return site against the "Make fallback chains loud" rule:

ALL SILENT PATHS ARE FILTER-DRIVEN LEGITIMATE-EMPTY:

* ``get_finding`` (line ~296) — ``if row is None: return None``. The
  row is absent because the caller-supplied ``finding_id_str`` did not
  match any persisted row. NOT a substrate failure; filter-driven.
* ``list_findings`` (line ~312) — ``.fetchall()`` returning an empty
  list. The filters (``detector`` / ``subject_kind`` / ``subject_id``)
  matched no rows. NOT a substrate failure; filter-driven.
* ``count_by_detector`` (line ~351) — empty dict on empty table or
  zero rows. NOT a substrate failure; the registry is legitimately
  empty before any detector has emitted on this project (see W1259
  ``not_yet_emitted`` state in ``cmd_findings list``).
* ``known_detector_names`` (line ~126) — UNION of canonical names +
  live counts; always returns the canonical set as a floor. No silent
  path.
* ``emit_finding`` (line ~246) — writer path, explicitly OUT of W604
  scope per the brief. The fallback ``int(row[0]) if row else 0`` is
  an edge case for driver-quirk loss of ``lastrowid`` on the
  ``ON CONFLICT DO UPDATE`` branch, NOT a silent substrate-failure.

NO ``try/except sqlite3.*`` BLOCKS EXIST IN THE FILE:

``grep -E 'try:|except (sqlite3|except sqlite3' src/roam/db/findings.py``
returns ZERO matches. Every ``conn.execute(...)`` call propagates
``sqlite3.OperationalError`` (schema mismatch, malformed SQL, missing
table, etc.) loudly to the caller. The substrate is already
fail-loud-by-raise.

NO FTS5 PATH IN THE FINDINGS REGISTRY:

``grep -i 'fts|MATCH|symbol_fts' src/roam/db/findings.py`` returns
ZERO functional matches. The findings registry is a plain SQL table
queried with parameterised filters; FTS5 lives in ``symbol_fts``
(virtual table managed by ``db/connection.py::_ensure_fts5_table``,
already plumbed by W603). The W604 brief's proposed
``findings_fts_missing:<table>`` marker has no corresponding code
path in ``db/findings.py``.

NO SCHEMA-VERSION CHECK IN FINDINGS.PY:

``grep 'user_version|USER_VERSION|schema_version|PRAGMA'
src/roam/db/findings.py`` returns ZERO matches. The W97 USER_VERSION
substrate lives in ``db/connection.py::_bump_user_version`` (already
plumbed by W603 with ``roam_user_version_read_failed`` marker). The
W604 brief's proposed ``findings_schema_mismatch`` marker would
duplicate W603 disclosure on the same substrate-floor read path.

W604 OUTCOME: NO-OP — POSITIVE COVERAGE ONLY
--------------------------------------------

Per the W604 task brief:

  "If audit returns NO substrate-failure silent paths (all silents are
   filter-driven), STOP and report no-op"

The findings.py reader functions are intentionally fail-loud-by-raise:
substrate failures (missing table, schema drift, malformed query)
propagate as ``sqlite3.OperationalError`` to ``cmd_findings`` callers
where the surrounding ``open_db`` shell converts them into a
``click.ClickException`` with remediation hint. Plumbing
``warnings_out`` here would either:

1. Require adding ``try/except sqlite3.*`` blocks that would CHANGE
   substrate-failure behaviour from "raise loudly" to "return empty
   with marker" — a regression for callers that today see a clear
   error message + remediation hint.
2. Plumb markers for code paths that DO NOT EXIST (no FTS5 read, no
   schema-version check, no `try/except sqlite3` in the file).

This test SEALS the W604 audit conclusion: the findings.py readers
have no substrate-failure silent paths and should NOT acquire
``warnings_out`` parameters. The W978 first-hypothesis discipline
correctly prevented a regression here.

W603 CROSS-REFERENCE — write-side vs. read-side FTS5 markers
------------------------------------------------------------

W603 plumbed write-side FTS5 markers (``roam_fts_drop_failed:``,
``roam_fts_create_failed:``) in ``_ensure_fts5_table``. W604 inspected
the findings.py read side and found NO FTS5 read paths to plumb. The
``symbol_fts`` table is consumed by ``src/roam/search/index_embeddings.py``
(``fts5_available`` / ``fts5_populated``), which is a SEPARATE substrate
from the findings registry. That is the natural W604-followup-A
candidate: ``index_embeddings.py`` has bare ``except Exception: return
False`` patterns that DO silently swallow substrate failures and DO
bridge to the W603 write-side markers (when an operator sees
``roam_fts_create_failed`` on write but the read-side silently coerces
to False, the symmetry is incomplete).

W907 VERIFY-CYCLE CHECK
-----------------------

``db/findings.py`` has NO "duplicated here to avoid cycle" docstrings.
The file imports only stdlib (``hashlib``, ``sqlite3``, ``dataclasses``,
``typing``) — no roam-internal imports — so no cycle could exist to
hedge against. Clean.

CALLER AUDIT (audit-only, no caller modifications)
--------------------------------------------------

The findings.py readers have a wide caller base:

* ``src/roam/commands/cmd_findings.py`` — primary consumer
  (``findings list``, ``findings show``, ``findings count``). Already
  handles substrate-empty states explicitly (W1259 ``not_yet_emitted``
  + W1063 ``unknown_detector``).
* ~26 detector commands write via ``emit_finding`` but DO NOT read.
  Cross-detector consumers that READ findings include:
  - ``src/roam/commands/cmd_critique.py`` (re-emits derived findings)
  - ``src/roam/commands/cmd_health.py`` (aggregates findings into score)
  - ``src/roam/commands/cmd_doctor.py`` (substrate-status surfacing)
  - ``src/roam/commands/cmd_fingerprint.py`` (topology comparison)
  - ``src/roam/commands/cmd_fan.py`` (fan-in/fan-out re-emit)
  - ``src/roam/commands/cmd_llm_smells.py`` (consumer)

None of these callers thread ``warnings_out`` today. They rely on the
existing ``sqlite3.OperationalError``-propagates-to-ClickException
contract for substrate failures. W604 is audit-only on the producer
side; no caller modifications.

W89 SUBSTRATE UNTOUCHED
-----------------------

* ``src/roam/db/schema.py`` — read only, NOT modified.
* ``CREATE TABLE IF NOT EXISTS findings ...`` migration entry (seq 56
  in ``_MIGRATIONS``) — unchanged.
* The 3 supporting indexes (``idx_findings_subject``,
  ``idx_findings_detector``, ``idx_findings_created``) — unchanged.
* ``USER_VERSION = 17`` — unchanged.

LAW 4 note: warning kinds (when they would exist) are NOT
``agent_contract.facts`` strings and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.db.findings import (  # noqa: E402
    CANONICAL_DETECTOR_NAMES,
    FindingRecord,
    count_by_detector,
    emit_finding,
    get_finding,
    list_findings,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_conn() -> sqlite3.Connection:
    """A blank in-memory sqlite3.Connection with the findings table built."""
    conn = sqlite3.connect(":memory:")
    # Mirror the migration ledger seq 56 (CREATE TABLE) + seq 57-59 indexes.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS findings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "finding_id_str TEXT NOT NULL UNIQUE, "
        "subject_kind TEXT NOT NULL, "
        "subject_id INTEGER, "
        "claim TEXT NOT NULL, "
        "evidence_json TEXT, "
        "confidence TEXT, "
        "source_detector TEXT NOT NULL, "
        "source_version TEXT, "
        "supersedes_id INTEGER REFERENCES findings(id) ON DELETE SET NULL, "
        "suppressions_json TEXT DEFAULT '[]', "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    yield conn
    conn.close()


# ===========================================================================
# (1) Happy path — clean query emits no warnings (sanity)
# ===========================================================================


def test_clean_query_emits_no_warning(fresh_conn: sqlite3.Connection) -> None:
    """A clean ``list_findings`` against a populated table → no surprises.

    Sanity check: the readers return the right shape and the registry
    substrate is intact (positive baseline for the no-op decision).
    """
    emit_finding(
        fresh_conn,
        FindingRecord(
            finding_id_str="alpha:sym:1",
            subject_kind="symbol",
            claim="a1",
            source_detector="alpha",
        ),
    )
    rows = list_findings(fresh_conn, detector="alpha")
    assert len(rows) == 1
    assert rows[0]["source_detector"] == "alpha"


# ===========================================================================
# (2) W978 POSITIVE COVERAGE — filter-driven empties stay silent
# ===========================================================================


def test_filter_no_matches_silent_list(fresh_conn: sqlite3.Connection) -> None:
    """``list_findings`` with no matching rows → empty list, NO marker.

    W978 positive coverage: this is the COMMON case (detector hasn't
    emitted yet, filter is too narrow). Surfacing a warning here would
    train operators to ignore real signals — exactly the anti-pattern
    the substrate-hardening arc fights against.
    """
    emit_finding(
        fresh_conn,
        FindingRecord(
            finding_id_str="alpha:sym:1",
            subject_kind="symbol",
            claim="a1",
            source_detector="alpha",
        ),
    )
    # Filter to a detector that has NOT emitted.
    rows = list_findings(fresh_conn, detector="beta")
    assert rows == [], "filter-driven empty must stay silent; substrate is healthy."


def test_filter_no_matches_silent_get(fresh_conn: sqlite3.Connection) -> None:
    """``get_finding`` on an unknown id → None, NO marker.

    W978 positive coverage: legitimately-absent record is a filter
    miss, not a substrate failure. Disclosure here would fire on
    every typo + every "id from a previous detector run" lookup —
    real-world training-to-ignore territory.
    """
    result = get_finding(fresh_conn, "nonexistent:sym:zzz")
    assert result is None


def test_count_by_detector_empty_silent(fresh_conn: sqlite3.Connection) -> None:
    """``count_by_detector`` on an empty registry → ``{}``, NO marker.

    The common cold-start path: no detector has emitted yet. Empty
    dict is the right signal (and ``cmd_findings count`` already
    surfaces ``state=empty`` per LAW 6 disclosure discipline at the
    CONSUMER layer, not the substrate layer).
    """
    counts = count_by_detector(fresh_conn)
    assert counts == {}


# ===========================================================================
# (3) Substrate-failure paths PROPAGATE LOUDLY (not silent)
# ===========================================================================


def test_missing_table_raises_loudly() -> None:
    """A query against a DB WITHOUT the findings table → OperationalError.

    Confirms that schema-drift / table-absent scenarios are already
    fail-loud-by-raise — they would surface via the W603-plumbed
    ``open_db`` shell as a ``click.ClickException`` with remediation
    hint. No silent-empty path masks the substrate failure.

    This is the W604 core finding: the readers are already correct;
    plumbing ``warnings_out`` would CHANGE behaviour from "raise
    loudly" to "return empty with marker", which is a regression.
    """
    blank = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            list_findings(blank)
        assert "no such table" in str(exc_info.value).lower()
    finally:
        blank.close()


def test_missing_table_get_raises_loudly() -> None:
    """``get_finding`` against a missing table → OperationalError, not None.

    Sister assertion to the list_findings test above — confirms the
    single-row reader also fails loud rather than silently returning
    None on substrate absence.
    """
    blank = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            get_finding(blank, "any:id:zzz")
        assert "no such table" in str(exc_info.value).lower()
    finally:
        blank.close()


def test_missing_table_count_raises_loudly() -> None:
    """``count_by_detector`` against a missing table → OperationalError.

    Third reader function — same fail-loud-by-raise contract. None of
    the three readers silently coerce substrate failure to empty.
    """
    blank = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            count_by_detector(blank)
        assert "no such table" in str(exc_info.value).lower()
    finally:
        blank.close()


# ===========================================================================
# (4) No try/except in findings.py (substrate is fail-loud-by-raise)
# ===========================================================================


def test_no_try_except_in_findings_module() -> None:
    """AST-scan: ``db/findings.py`` contains ZERO ``try/except`` blocks.

    The substrate is intentionally fail-loud-by-raise: every reader
    propagates ``sqlite3.OperationalError`` (schema mismatch,
    malformed query, missing table) to the caller, where the
    surrounding ``open_db`` shell converts it into a
    ``click.ClickException`` with remediation hint. Adding a
    ``try/except`` to plumb ``warnings_out`` would change that
    contract.

    This test pins the substrate's fail-loud discipline. If a future
    wave intentionally adds a try/except, update this test with the
    rationale.
    """
    src_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    try_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    assert try_nodes == [], (
        f"db/findings.py is intentionally fail-loud-by-raise but found "
        f"{len(try_nodes)} ``try`` blocks at lines "
        f"{[n.lineno for n in try_nodes]}. W604 audit conclusion: the "
        f"readers should NOT silently swallow substrate errors. If a "
        f"future wave needs to plumb warnings_out, update this test + "
        f"the W604 docstring with the rationale."
    )


# ===========================================================================
# (5) No FTS5 path in findings.py (W603 cross-reference)
# ===========================================================================


def test_no_fts5_path_in_findings_module() -> None:
    """``db/findings.py`` does not reference FTS5 / symbol_fts / MATCH.

    The W604 brief proposed a ``findings_fts_missing`` marker, but the
    findings registry is a plain SQL table queried with parameterised
    filters — FTS5 lives in ``symbol_fts`` (a separate virtual table
    consumed by ``src/roam/search/index_embeddings.py``).

    The W603 write-side FTS5 markers (``roam_fts_drop_failed:``,
    ``roam_fts_create_failed:``) live in
    ``db/connection.py::_ensure_fts5_table``. W604 confirms there is no
    corresponding READ path in findings.py to plumb.

    Cross-references:
    * ``src/roam/search/index_embeddings.py`` is the W604-followup-A
      candidate — it has bare ``except Exception: return False``
      patterns that DO silently swallow FTS5 substrate failures.
    """
    src_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    source = src_path.read_text(encoding="utf-8")

    # Walk the AST and find string constants that look like SQL referencing
    # FTS5. SQL strings in findings.py are CONCRETE: each has FROM <table>
    # or VIRTUAL TABLE clauses. Skip docstrings (module/function/class
    # leading Expr-string nodes) so prose "match the SELECT" doesn't
    # trigger.
    tree = ast.parse(source)

    # Collect every docstring node id so we can exclude them.
    doc_constant_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body and isinstance(node.body[0], ast.Expr):
                inner = node.body[0].value
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    doc_constant_ids.add(id(inner))

    fts_sql_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_constant_ids:
                continue
            value = node.value.lower()
            # SQL-only triggers: a real FTS5 query would mention either the
            # ``symbol_fts`` table by name or the ``MATCH`` operator inside
            # a SELECT/FROM clause. Plain "fts" mentions in column lists
            # or constants don't count.
            mentions_fts_table = "symbol_fts" in value
            mentions_match_op = " match " in f" {value} " and (
                "from " in value or "where " in value or "select " in value
            )
            if mentions_fts_table or mentions_match_op:
                fts_sql_literals.append(value[:120])

    assert fts_sql_literals == [], (
        f"db/findings.py contains {len(fts_sql_literals)} FTS5-related SQL "
        f"string literal(s): {fts_sql_literals!r}. The W604 audit "
        f"conclusion assumed no FTS5 read path. If a future wave wires "
        f"FTS5 into findings.py, plumb a ``findings_fts_*`` marker family "
        f"consistent with the W603 write-side ``roam_fts_*`` prefix."
    )


# ===========================================================================
# (6) No schema-version check in findings.py (lives in connection.py)
# ===========================================================================


def test_no_schema_version_check_in_findings_module() -> None:
    """``db/findings.py`` does not run PRAGMA user_version checks.

    The W97 USER_VERSION substrate lives in
    ``db/connection.py::_bump_user_version`` (already plumbed by W603
    with ``roam_user_version_read_failed`` marker). The W604 brief's
    proposed ``findings_schema_mismatch`` marker would duplicate W603
    disclosure on the same substrate-floor read path.

    If a future wave adds schema-version validation to the findings
    readers, it must REUSE the W603 ``roam_user_version_read_failed``
    marker (not invent a parallel ``findings_schema_mismatch``).
    """
    src_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    source = src_path.read_text(encoding="utf-8")

    # The lower-cased ``user_version`` literal would land in SQL like
    # ``PRAGMA user_version``. The constant ``USER_VERSION`` lives in
    # connection.py only.
    assert "PRAGMA user_version" not in source, (
        "db/findings.py is not the schema-version owner — that lives in "
        "db/connection.py::_bump_user_version (plumbed by W603). Do not "
        "duplicate the read here."
    )
    assert "PRAGMA schema_version" not in source, (
        "db/findings.py should not query PRAGMA schema_version — that lives in the substrate floor (db/connection.py)."
    )


# ===========================================================================
# (7) Reader signatures DO NOT carry warnings_out (audit-only seal)
# ===========================================================================


def test_reader_signatures_have_no_warnings_out() -> None:
    """AST-check: the W604 readers DO NOT acquire ``warnings_out`` params.

    Pins the W604 no-op audit conclusion. If a future wave intentionally
    plumbs ``warnings_out`` onto one of the readers (perhaps because a
    real substrate-failure silent path emerges), update this test with
    the rationale + the new closed-enum marker.

    Sealed readers (no warnings_out):
      * ``get_finding``
      * ``list_findings``
      * ``count_by_detector``
      * ``known_detector_names``
      * ``emit_finding`` (writer — explicitly OUT of W604 scope)
      * ``supersede_finding`` (writer — explicitly OUT of W604 scope)
    """
    src_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {
        "get_finding",
        "list_findings",
        "count_by_detector",
        "known_detector_names",
        "emit_finding",
        "supersede_finding",
    }
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found.add(node.name)
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            regular_names = [a.arg for a in node.args.args]
            all_params = set(kwonly_names) | set(regular_names)
            assert "warnings_out" not in all_params, (
                f"{node.name} acquired a ``warnings_out`` param — W604 "
                f"audit conclusion was no-op. If you intentionally "
                f"plumbed this, update tests/test_w604_findings_readers_warnings_out.py "
                f"with the rationale + the new closed-enum marker."
            )

    missing = targets - found
    assert not missing, f"expected to find functions {missing!r} in db/findings.py"


# ===========================================================================
# (8) cmd_findings caller UNMODIFIED (audit-only handoff)
# ===========================================================================


def test_cmd_findings_caller_unmodified() -> None:
    """AST-check ``cmd_findings.py`` — does not thread ``warnings_out``.

    W604 is audit-only on the producer side; the consumer-side handoff
    in cmd_findings stays unchanged. A future wave can opt the CLI
    entry into threading the bucket IF AND ONLY IF a real
    substrate-failure silent path emerges (which today: there are
    none).
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_findings.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            # The 3 read-side functions cmd_findings calls.
            if name in {"list_findings", "get_finding", "count_by_detector"}:
                kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                assert "warnings_out" not in kwarg_names, (
                    f"cmd_findings.py now threads warnings_out into "
                    f"{name} at line {node.lineno}; W604 was audit-only "
                    f"— update this test if intentionally opted in."
                )


# ===========================================================================
# (9) W89 substrate UNTOUCHED — AST-check schema.py + connection.py migration
# ===========================================================================


def test_w89_substrate_untouched() -> None:
    """AST/text-check schema.py + connection.py preserve the W89 invariants.

    The W89 findings-registry substrate lives across two files:
    * ``schema.py`` — ``CREATE TABLE IF NOT EXISTS findings`` in
      SCHEMA_SQL (fresh-DB path).
    * ``connection.py::_MIGRATIONS`` — seq 56-59 (legacy-DB path:
      CREATE TABLE + 3 indexes).

    W604 does NOT modify either. This test pins both invariants.
    """
    schema_src = (repo_root() / "src" / "roam" / "db" / "schema.py").read_text(encoding="utf-8")
    conn_src = (repo_root() / "src" / "roam" / "db" / "connection.py").read_text(encoding="utf-8")

    # (a) schema.py — findings table definition.
    assert "CREATE TABLE IF NOT EXISTS findings" in schema_src, (
        "schema.py is missing the canonical findings table CREATE — the W89 substrate invariant is broken."
    )

    # (b) connection.py — seq 56-59 migration entries.
    for must_have in (
        "findings registry table + indexes",
        "idx_findings_subject",
        "idx_findings_detector",
        "idx_findings_created",
    ):
        assert must_have in conn_src, (
            f"connection.py is missing migration entry {must_have!r} — the W89 substrate invariant is broken."
        )

    # (c) USER_VERSION constant preserved.
    from roam.db.connection import USER_VERSION

    assert USER_VERSION == 18, (
        f"W89 substrate invariant: USER_VERSION must stay at the canonical contract value "
        f"(18 since the B8 snapshots.spectral_gap migration); got {USER_VERSION}. W604 must not bump this."
    )


# ===========================================================================
# (10) W603 FTS5 marker prefix family consistency
# ===========================================================================


def test_w603_fts_marker_consistency() -> None:
    """W603 write-side FTS5 markers exist; W604 confirms no read-side need.

    The W603 write-side closed-enum markers in
    ``db/connection.py::_ensure_fts5_table``:
      * ``roam_fts_drop_failed:``
      * ``roam_fts_create_failed:``

    Use the ``roam_fts_*`` prefix family. W604 confirms there is NO
    corresponding read-side FTS5 path in ``db/findings.py`` — so no
    ``roam_fts_*`` read markers are needed there.

    If a future wave plumbs FTS5 read-side markers (most likely in
    ``src/roam/search/index_embeddings.py``, the W604-followup-A
    candidate), they MUST use the same ``roam_fts_*`` prefix family
    for cross-substrate consistency.
    """
    # W603 write-side markers present in connection.py.
    conn_src = (repo_root() / "src" / "roam" / "db" / "connection.py").read_text(encoding="utf-8")
    assert "roam_fts_drop_failed:" in conn_src, (
        "W603 marker ``roam_fts_drop_failed:`` must be present in "
        "db/connection.py — W604 cross-references depend on it."
    )
    assert "roam_fts_create_failed:" in conn_src, (
        "W603 marker ``roam_fts_create_failed:`` must be present in "
        "db/connection.py — W604 cross-references depend on it."
    )

    # W604 read-side: no FTS5 markers introduced in findings.py.
    findings_src = (repo_root() / "src" / "roam" / "db" / "findings.py").read_text(encoding="utf-8")
    for marker in ("roam_fts_", "findings_fts_"):
        assert marker not in findings_src, (
            f"db/findings.py contains the FTS5 marker prefix {marker!r}. "
            f"W604 audit conclusion: no FTS5 read path exists in "
            f"findings.py, so no FTS5 marker should be present. If a "
            f"future wave wires FTS5 in, use the ``roam_fts_*`` family "
            f"(matching the W603 write-side) for consistency."
        )


# ===========================================================================
# (11) Closed-enum subset — W978 first-hypothesis discipline (no markers)
# ===========================================================================


def test_closed_enum_subset_w604() -> None:
    """AST-check ``db/findings.py`` for the W604 EMPTY closed-enum set.

    W978 first-hypothesis discipline (STRONG variant for substrate
    audits): every emitted marker must correspond to a real silent-
    fail code path. Inventing markers that no path can ever emit adds
    dead vocabulary that contaminates the audit-trail surface.

    The expected closed enum after W604: **EMPTY**. The findings
    registry substrate has zero substrate-failure silent paths to
    plumb. Forbidden markers — paths that DO NOT exist in
    ``db/findings.py``:

      * ``findings_schema_mismatch:`` — no PRAGMA user_version read
        (lives in connection.py, plumbed by W603 as
        ``roam_user_version_read_failed``).
      * ``findings_fts_missing:`` — no FTS5 read path in findings.py
        (FTS5 lives in symbol_fts, consumed by
        search/index_embeddings.py).
      * ``findings_query_failed:`` — no try/except around the readers
        (queries propagate OperationalError loudly).
      * ``findings_table_missing:`` — no swallow of "no such table"
        OperationalError (propagates loudly).
    """
    src_path = repo_root() / "src" / "roam" / "db" / "findings.py"
    source = src_path.read_text(encoding="utf-8")

    forbidden_markers = {
        "findings_schema_mismatch:",
        "findings_fts_missing:",
        "findings_query_failed:",
        "findings_table_missing:",
        # Also forbid the generic warnings_out marker family from
        # accidentally landing in findings.py.
        "warnings_out:",
    }
    for marker in forbidden_markers:
        assert marker not in source, (
            f"forbidden marker prefix {marker!r} present in "
            f"db/findings.py — this marker has no corresponding "
            f"silent-pass code path. W978 first-hypothesis discipline: "
            f"only plumb markers for paths that actually exist."
        )


# ===========================================================================
# (12) Default-args invocation never crashes (back-compat seal)
# ===========================================================================


def test_default_args_no_crash(fresh_conn: sqlite3.Connection) -> None:
    """Calling the readers with no kwargs works on every shape.

    The W604 readers have ZERO new parameters; this test pins that the
    pre-W604 caller signatures (~26 detectors + 6+ consumer commands)
    are unaffected.
    """
    # Reader 1: list_findings — default args.
    assert list_findings(fresh_conn) == []
    # Reader 1: list_findings — every filter kwarg.
    assert list_findings(fresh_conn, detector="x") == []
    assert list_findings(fresh_conn, subject_kind="symbol") == []
    assert list_findings(fresh_conn, subject_id=42) == []
    assert list_findings(fresh_conn, limit=10) == []

    # Reader 2: get_finding — default usage.
    assert get_finding(fresh_conn, "any:id:zzz") is None

    # Reader 3: count_by_detector — default usage.
    assert count_by_detector(fresh_conn) == {}


# ===========================================================================
# (13) CANONICAL_DETECTOR_NAMES vocabulary preserved (W1252 invariant)
# ===========================================================================


def test_canonical_detector_names_preserved() -> None:
    """W1252 invariant: CANONICAL_DETECTOR_NAMES is the source-of-truth set.

    W604 does NOT modify the canonical detector vocabulary. This test
    pins the floor (>= 25 known detectors per the W146/W1252 roster).

    If a new detector lands, it should be added to
    ``CANONICAL_DETECTOR_NAMES`` per the file's drift guard
    (``tests/test_findings_canonical_detectors.py``). W604 does not
    touch this set; the assertion is a back-compat seal.
    """
    assert isinstance(CANONICAL_DETECTOR_NAMES, frozenset), "CANONICAL_DETECTOR_NAMES must be a frozenset (immutable)."
    # Floor check: the original W146 roster + adds since.
    assert len(CANONICAL_DETECTOR_NAMES) >= 25, (
        f"CANONICAL_DETECTOR_NAMES has only {len(CANONICAL_DETECTOR_NAMES)} entries; expected >= 25."
    )
    # A sampling of the W146 canonical floor.
    for must_have in ("clones", "dead", "complexity", "smells", "n1", "taint"):
        assert must_have in CANONICAL_DETECTOR_NAMES, (
            f"CANONICAL_DETECTOR_NAMES is missing the canonical "
            f"detector {must_have!r} — W604 must not drop floor "
            f"entries."
        )
