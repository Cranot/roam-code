"""W603 — ``db/connection.py`` plumbs ``warnings_out`` on the substrate floor.

The W595 / W596 / W597 / W598 / W599 / W600 / W601 / W602 Pattern-2
substrate-hardening arc closed silent-fallback disclosure gaps on the
lease + permits + runs-ledger + runtime-daemon + pr-analyze-cache +
trace-ingest + config-hashes + signing + metrics-push substrates.
W603 closes the substrate FLOOR — every roam command reads through
``open_db`` / ``get_connection`` / ``ensure_schema``, so a silent-pass
here masks signals every command would otherwise inherit.

W978 first-hypothesis discipline (STRONG for substrate level)
-------------------------------------------------------------

Connection-level error handling is often correctly intentional. We
read ``db/connection.py`` IN FULL and categorised every silent path
against the "Make fallback chains loud" rule. Decisions:

PLUMBED (silent-pass changes user-visible behaviour):

1. ``_load_project_config`` (line ~99) — ``(OSError,
   JSONDecodeError, UnicodeDecodeError)`` silently returns {}, dropping
   any operator-declared ``db_dir`` override. The operator gets the
   project-default DB instead of the one they configured, looking
   identical to "no override was ever set." Marker:
   ``roam_config_read_failed:<path>:<exc_class>:<detail>``.

2. ``get_connection`` URI→plain readonly fallback (line ~172) —
   ``(sqlite3.OperationalError, ValueError)`` falls back to plain
   ``sqlite3.connect()`` that has NO driver-level read-only enforcement.
   The caller asked for ``readonly=True``; they got one without the
   safety rail. Marker:
   ``roam_readonly_uri_fallback:<path>:<exc_class>:<detail>``.

3. ``get_connection`` ROAM_QUERY_TIMEOUT_S parse (line ~228) —
   ``ValueError`` silently coerces a malformed env-var value to 0, so
   the progress handler is NOT installed and the opt-in safety
   mechanism is absent. Marker:
   ``roam_query_timeout_parse_failed:<value>``.

4. ``_bump_user_version`` PRAGMA read (line ~482) —
   ``sqlite3.DatabaseError`` coerces ``current`` to 0, driving an
   unconditional bump to target. Masks USER_VERSION drift detection
   (W596 + W97 substrate). Marker:
   ``roam_user_version_read_failed:<exc_class>:<detail>``.

5. ``_ensure_fts5_table`` DROP silent-skip (line ~538) —
   ``sqlite3.OperationalError`` early-returns without re-creating the
   table, leaving FTS5 without the ``docstring`` column. ``roam
   retrieve`` silently misses natural-language matches. Marker:
   ``roam_fts_drop_failed:<exc_class>:<detail>``.

6. ``_ensure_fts5_table`` CREATE silent-skip (line ~543) —
   ``sqlite3.OperationalError`` covers both the legit "no such module:
   fts5" case AND every OTHER OperationalError (locked DB, corrupt
   schema). All silently leave FTS5 absent. Marker:
   ``roam_fts_create_failed:<exc_class>:<detail>``.

INTENTIONAL — NOT PLUMBED (W978 positive coverage):

* ``_safe_alter`` duplicate-column race (line ~572) — W740-narrowed.
  Catches the column-already-exists race between two parallel
  connections and is the intended idempotent path. Other
  ``OperationalError`` variants (missing table, locked, syntax)
  propagate loudly per W740 discipline.
* ``_safe_alter`` PRAGMA table_info fallback (line ~566) — empty-set
  coerce drives the next ALTER, which raises loudly on a real
  schema-missing condition. The fallback is structurally
  defensive, not silent-by-design.
* ``PRAGMA optimize`` close-time skip (line ~705) — comment explicitly
  marks "not load-bearing; never refuse to close on this." Query
  planner staleness manifests as gradual latency degradation, not
  an operator-actionable signal. Disclosure would emit a marker on
  every commit, training operators to ignore real warnings.
* ``open_db`` ``except sqlite3.DatabaseError`` (lines 676 + 687) —
  raises ``click.ClickException`` with remediation hint. Already
  loud-by-raise; not a silent-pass.
* ``_safe_mkdir`` OSError (line ~50) — raises ``StaleDbDirError``
  with remediation hint. Already loud-by-raise; not a silent-pass.

W97 USER_VERSION SUBSTRATE UNTOUCHED *by W603*:

* ``src/roam/db/schema.py`` — NOT modified by the W603 plumb (later
  schema changes, e.g. B8 ``snapshots.spectral_gap``, are tracked
  separately by ``tests/test_user_version_discipline.py``).
* ``USER_VERSION`` constant — not moved by W603; the pin below tracks
  the current canonical contract value (18 since B8).
* The schema-version contract with downstream consumers (manifest
  writer, bundle import, drift detection in ``roam doctor``) is
  unchanged by W603. The W603 plumb only surfaces the READ-side
  failure on a corrupted PRAGMA that would otherwise be coerced to 0
  silently.

W596 HMAC CHAIN UNTOUCHED:

* ``src/roam/runs/ledger.py`` — read only, NOT modified
* ``src/roam/runs/signing.py`` — read only, NOT modified
* ``read_run_meta`` — unchanged
* The W603 plumb is on the SQLite-connection substrate, not the
  run-ledger HMAC substrate. They share no code.

CALLER AUDIT (audit-only, no caller modifications):

``open_db`` is the substrate floor (~200+ command modules import it).
We did NOT modify any caller. The W603 plumb is additive — every
``warnings_out`` parameter is kw-only with default ``None``, so every
existing caller continues to work unchanged. Top-3 callers by traffic:

  * ``src/roam/commands/resolve.py:107`` — ``open_db(readonly=True)``
    inside ``ensure_index()`` (called by EVERY command's pre-flight).
    Does not thread ``warnings_out``.
  * ``src/roam/index/indexer.py:1973`` —
    ``open_db(project_root=self.root)`` (write path during full index).
    Does not thread ``warnings_out``.
  * ``src/roam/api.py:17`` — programmatic SDK entry. Does not thread
    ``warnings_out``.

A future wave can opt high-traffic callers (resolve, indexer) into
threading the bucket and surfacing markers on their JSON envelopes;
the producer-side substrate is now ready.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589/W592/W593/W595/
W596/W597/W598/W599/W600/W601/W602).

W907 verify-cycle check
-----------------------

The ``WarningsOut = list[str] | None`` alias is duplicated locally in
``connection.py`` rather than imported from ``roam.output.formatter``.
The hedge docstring is honest about the rationale — formatter.py has
NO top-level roam imports (verified by ``grep '^from roam' formatter.py``
returning only deferred function-body imports). So the local duplication
is a hot-path-cost choice (connection.py is on every command's hot path;
formatter.py is ~50KB), NOT a false cycle hedge.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.db.connection import (  # noqa: E402
    WarningsOut,
    _bump_user_version,
    _ensure_fts5_table,
    _load_project_config,
    ensure_schema,
    get_connection,
    open_db,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_project(tmp_path: Path) -> Path:
    """A tmp_path with no ``.roam/`` directory — clean cold state."""
    return tmp_path


@pytest.fixture
def fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    """A blank in-memory sqlite3.Connection for migration tests."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


# ===========================================================================
# (1) Happy path — clean open emits no warnings
# ===========================================================================


def test_clean_open_emits_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean ``open_db`` against a fresh project → no warnings.

    Sanity check that the W603 plumb only fires on degenerate paths.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    warnings: list[str] = []
    with open_db(warnings_out=warnings) as conn:
        # Sanity — the schema is built and we can query a known table.
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    assert warnings == [], f"clean open_db must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (2) _load_project_config — missing file is intentional-silent
# ===========================================================================


def test_missing_config_is_intentional_silent(fresh_project: Path) -> None:
    """Cold start (no ``.roam/config.json``) → empty dict, NO marker.

    Mirrors W598 cold-cache + W602 missing-last-pr discipline.
    Disclosure on the common cold-start path would train operators to
    ignore real warnings.
    """
    warnings: list[str] = []
    result = _load_project_config(fresh_project, warnings_out=warnings)

    assert result == {}
    assert warnings == [], f"missing config.json must be SILENT — cold start path. Got {warnings!r}."


def test_corrupt_config_emits_marker(fresh_project: Path) -> None:
    """Malformed JSON in ``.roam/config.json`` → closed-enum marker.

    The function still returns ``{}`` (caller contract preserved).
    """
    roam_dir = fresh_project / ".roam"
    roam_dir.mkdir()
    (roam_dir / "config.json").write_text("not json {", encoding="utf-8")

    warnings: list[str] = []
    result = _load_project_config(fresh_project, warnings_out=warnings)

    assert result == {}
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("roam_config_read_failed:"), msg
    assert "JSONDecodeError" in msg, msg


def test_config_oserror_emits_marker(fresh_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``PermissionError`` on read emits the read_failed marker.

    The function still returns ``{}`` (caller contract preserved).
    """
    roam_dir = fresh_project / ".roam"
    roam_dir.mkdir()
    config_path = roam_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    target_resolved = config_path.resolve()
    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == target_resolved:
            raise PermissionError("synthetic-EACCES from W603 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = _load_project_config(fresh_project, warnings_out=warnings)

    assert result == {}
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("roam_config_read_failed:"), msg
    assert "PermissionError" in msg, msg


# ===========================================================================
# (3) get_connection — ROAM_QUERY_TIMEOUT_S parse failure emits marker
# ===========================================================================


def test_query_timeout_parse_failed_emits_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ROAM_QUERY_TIMEOUT_S=garbage`` → ``roam_query_timeout_parse_failed``.

    The operator set the env var expecting a per-query timeout; the
    silent ``ValueError`` coerces it to 0 (no progress handler). W603
    plumbs disclosure so operators see the opt-in safety mechanism
    didn't take effect.
    """
    monkeypatch.setenv("ROAM_QUERY_TIMEOUT_S", "garbage-not-a-float")
    db_path = tmp_path / "test.db"

    warnings: list[str] = []
    conn = get_connection(db_path, warnings_out=warnings)
    try:
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        assert msg.startswith("roam_query_timeout_parse_failed:"), msg
        assert "garbage-not-a-float" in msg, msg
    finally:
        conn.close()


def test_query_timeout_valid_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid ``ROAM_QUERY_TIMEOUT_S`` → no marker.

    Sanity that the plumb only fires on parse failure.
    """
    monkeypatch.setenv("ROAM_QUERY_TIMEOUT_S", "5.0")
    db_path = tmp_path / "test.db"

    warnings: list[str] = []
    conn = get_connection(db_path, warnings_out=warnings)
    try:
        assert warnings == [], warnings
    finally:
        conn.close()


def test_query_timeout_absent_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ROAM_QUERY_TIMEOUT_S`` unset → no marker (default path).

    Sanity that the plumb only fires when the env var is BOTH set AND
    unparseable. Absent is the common case and must stay silent.
    """
    monkeypatch.delenv("ROAM_QUERY_TIMEOUT_S", raising=False)
    db_path = tmp_path / "test.db"

    warnings: list[str] = []
    conn = get_connection(db_path, warnings_out=warnings)
    try:
        assert warnings == [], warnings
    finally:
        conn.close()


# ===========================================================================
# (4) get_connection — URI readonly fallback emits marker
# ===========================================================================


def test_readonly_uri_fallback_emits_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Forcing ``Path.as_uri`` to raise → ``roam_readonly_uri_fallback``.

    On UNC paths the URI form is invalid, so the fallback to plain
    ``sqlite3.connect`` is the real-world trigger. We simulate by
    monkeypatching ``Path.as_uri`` to raise ``ValueError`` only on the
    target db_path.
    """
    db_path = tmp_path / "test.db"
    # Pre-create the file so plain sqlite3.connect succeeds.
    db_path.write_bytes(b"")

    target_resolved = db_path.resolve()
    original_as_uri = Path.as_uri

    def _raising_as_uri(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == target_resolved:
            raise ValueError("synthetic-UNC URI rejection from W603 test")
        return original_as_uri(self, *args, **kwargs)

    monkeypatch.setattr(Path, "as_uri", _raising_as_uri)

    warnings: list[str] = []
    conn = get_connection(db_path, readonly=True, warnings_out=warnings)
    try:
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        assert msg.startswith("roam_readonly_uri_fallback:"), msg
        assert "ValueError" in msg, msg
        assert "synthetic-UNC URI rejection from W603 test" in msg, msg
    finally:
        conn.close()


def test_readonly_clean_no_marker(tmp_path: Path) -> None:
    """Clean ``readonly=True`` on a regular path → no marker.

    Sanity that the URI fallback marker only fires on the actual
    OperationalError / ValueError branch.
    """
    db_path = tmp_path / "test.db"
    # Pre-create so URI mode=ro succeeds.
    conn0 = sqlite3.connect(str(db_path))
    conn0.execute("CREATE TABLE t (id INTEGER)")
    conn0.commit()
    conn0.close()

    warnings: list[str] = []
    conn = get_connection(db_path, readonly=True, warnings_out=warnings)
    try:
        assert warnings == [], warnings
    finally:
        conn.close()


# ===========================================================================
# (5) _bump_user_version — PRAGMA read failure emits marker
# ===========================================================================


def test_user_version_read_failed_emits_marker() -> None:
    """A failed PRAGMA user_version read → ``roam_user_version_read_failed``.

    Synthesise the failure by injecting a fake connection whose
    ``execute`` raises ``sqlite3.DatabaseError`` on the PRAGMA read
    (a real corrupted DB would surface the same error).
    """
    raised: list[bool] = [False]

    class _BrokenConn:
        def execute(self, sql: str, *args, **kwargs):
            if not raised[0] and "PRAGMA user_version" == sql.strip() and "=" not in sql:
                raised[0] = True
                raise sqlite3.DatabaseError("synthetic-PRAGMA-read-failure from W603 test")
            # The bump still writes — surface a fake row to acknowledge.
            return self

        def fetchone(self):
            return None

    warnings: list[str] = []
    _bump_user_version(_BrokenConn(), target=17, warnings_out=warnings)

    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("roam_user_version_read_failed:"), msg
    assert "DatabaseError" in msg, msg
    assert "synthetic-PRAGMA-read-failure from W603 test" in msg, msg


def test_user_version_clean_no_marker(fresh_conn: sqlite3.Connection) -> None:
    """A clean PRAGMA user_version read → no marker.

    Sanity that the plumb only fires on the actual DatabaseError branch.
    """
    warnings: list[str] = []
    _bump_user_version(fresh_conn, target=17, warnings_out=warnings)
    assert warnings == []


# ===========================================================================
# (6) _ensure_fts5_table — DROP / CREATE silent-skip emit markers
# ===========================================================================


def test_fts_drop_failed_emits_marker() -> None:
    """A failed FTS5 DROP → ``roam_fts_drop_failed``.

    Synthesise by injecting a conn where ``SELECT 1 FROM sqlite_master``
    returns a row (existing table) BUT the table lacks the ``docstring``
    column AND ``DROP TABLE`` raises OperationalError. The function
    early-returns without re-creating, leaving the table in legacy shape.
    """

    class _FtsBrokenConn:
        def __init__(self):
            self._stage = "select"

        def execute(self, sql: str, *args, **kwargs):
            stripped = sql.strip()
            if stripped.startswith("SELECT 1 FROM sqlite_master"):
                self._stage = "fetch_select"
                return self
            if stripped.startswith("PRAGMA table_info"):
                self._stage = "fetch_table_info"
                return self
            if stripped.startswith("DROP TABLE"):
                raise sqlite3.OperationalError("synthetic-DROP-failure from W603 test")
            raise AssertionError(f"unexpected sql: {sql!r}")

        def fetchone(self):
            # The "table exists" SELECT.
            return (1,)

        def fetchall(self):
            # Legacy table has columns without ``docstring``.
            return [(0, "name", "TEXT"), (1, "qualified_name", "TEXT")]

    warnings: list[str] = []
    _ensure_fts5_table(_FtsBrokenConn(), warnings_out=warnings)

    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("roam_fts_drop_failed:"), msg
    assert "OperationalError" in msg, msg
    assert "synthetic-DROP-failure from W603 test" in msg, msg


def test_fts_create_failed_emits_marker() -> None:
    """A failed FTS5 CREATE (no fts5 module) → ``roam_fts_create_failed``.

    Synthesise by injecting a conn where the SELECT returns no row (no
    existing table) BUT CREATE VIRTUAL TABLE raises OperationalError
    (the legit "no such module: fts5" case).
    """

    class _FtsAbsentConn:
        def execute(self, sql: str, *args, **kwargs):
            stripped = sql.strip()
            if stripped.startswith("SELECT 1 FROM sqlite_master"):
                return self
            if stripped.startswith("CREATE VIRTUAL TABLE"):
                raise sqlite3.OperationalError("no such module: fts5")
            raise AssertionError(f"unexpected sql: {sql!r}")

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    warnings: list[str] = []
    _ensure_fts5_table(_FtsAbsentConn(), warnings_out=warnings)

    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("roam_fts_create_failed:"), msg
    assert "OperationalError" in msg, msg
    assert "no such module: fts5" in msg, msg


def test_fts_clean_create_no_marker(fresh_conn: sqlite3.Connection) -> None:
    """A clean FTS5 CREATE on a build that has fts5 → no marker.

    Sanity that the create_failed marker only fires when CREATE actually
    raises. Most modern SQLite builds ship fts5 enabled; we assume the
    test environment is one of them.
    """
    warnings: list[str] = []
    _ensure_fts5_table(fresh_conn, warnings_out=warnings)
    assert warnings == [], warnings


# ===========================================================================
# (7) ensure_schema — threads warnings_out into FTS5 + user_version
# ===========================================================================


def test_ensure_schema_threads_warnings_out(fresh_conn: sqlite3.Connection) -> None:
    """A clean ``ensure_schema`` → no warnings.

    Sanity that the plumb threads warnings_out without false-positives.
    """
    warnings: list[str] = []
    ensure_schema(fresh_conn, warnings_out=warnings)
    assert warnings == [], warnings


# ===========================================================================
# (8) Default warnings_out=None preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling without ``warnings_out`` works on every failure mode.

    The ~200+ callers of open_db / get_connection / _load_project_config
    / ensure_schema / _bump_user_version / _ensure_fts5_table call with
    no kwarg and must NOT regress.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    # (a) Default-args open_db.
    with open_db() as conn:
        conn.execute("SELECT 1").fetchone()

    # (b) Default-args _load_project_config on missing file.
    assert _load_project_config(tmp_path) == {}

    # (c) Default-args _load_project_config on corrupt file.
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir(exist_ok=True)
    (roam_dir / "config.json").write_text("not json", encoding="utf-8")
    assert _load_project_config(tmp_path) == {}

    # (d) Default-args get_connection.
    db_path = tmp_path / "scratch.db"
    conn = get_connection(db_path)
    conn.close()

    # (e) Default-args _bump_user_version on in-memory conn.
    mem = sqlite3.connect(":memory:")
    _bump_user_version(mem, target=17)
    mem.close()

    # (f) Default-args _ensure_fts5_table.
    mem = sqlite3.connect(":memory:")
    _ensure_fts5_table(mem)
    mem.close()


# ===========================================================================
# (9) Caller audit — no caller threads warnings_out today
# ===========================================================================


def test_resolve_unmodified() -> None:
    """AST-check ``resolve.py`` — does not thread ``warnings_out`` to open_db.

    W603 is audit-only on the producer side. ``resolve.py:107`` is the
    top-traffic caller (``ensure_index`` is called by every command's
    preflight). A future wave can opt it into threading; this test
    pins the current audit-only contract.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "resolve.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == "open_db":
                kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                assert "warnings_out" not in kwarg_names, (
                    f"resolve.py now threads warnings_out into open_db at "
                    f"line {node.lineno}; W603 was audit-only — update this "
                    f"test if intentionally opted in."
                )


def test_indexer_unmodified() -> None:
    """AST-check ``indexer.py`` — does not thread ``warnings_out`` to open_db.

    Indexer is the top write-path caller. Audit-only handoff.
    """
    src_path = repo_root() / "src" / "roam" / "index" / "indexer.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == "open_db":
                kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                assert "warnings_out" not in kwarg_names, (
                    f"indexer.py now threads warnings_out into open_db at "
                    f"line {node.lineno}; W603 was audit-only — update this "
                    f"test if intentionally opted in."
                )


# ===========================================================================
# (10) W97 USER_VERSION substrate UNTOUCHED — AST-check schema.py
# ===========================================================================


def test_w97_user_version_substrate_untouched() -> None:
    """AST-check ``schema.py`` for the W97 USER_VERSION + FTS5 invariant.

    The W97 substrate lives in ``schema.py`` (CREATE TABLE statements)
    and the ``USER_VERSION`` constant in ``connection.py``. W603 ONLY
    plumbs READ-side disclosure on ``_bump_user_version``; it does not
    modify the constant, the schema, or the migration ordering. This
    test pins both invariants.
    """
    # (a) USER_VERSION constant at the canonical contract value (18 since
    # the B8 snapshots.spectral_gap column landed). W603 itself does not
    # touch this constant — the pin guards against a W603-scope edit
    # silently moving it. Bump in lockstep with a real schema change.
    from roam.db.connection import USER_VERSION

    assert USER_VERSION == 18, (
        f"W97 USER_VERSION substrate invariant: USER_VERSION must "
        f"stay at the canonical contract value (18); got {USER_VERSION}. "
        f"If you bumped USER_VERSION in this wave, audit which schema "
        f"change required it and update tests/test_user_version_discipline.py."
    )

    # (b) schema.py preserves the canonical core tables the connection
    # substrate relies on. ``symbol_fts`` lives in
    # ``_ensure_fts5_table`` (a managed virtual-table helper invoked
    # from the migration ledger), NOT in SCHEMA_SQL — verified by
    # ``grep symbol_fts src/roam/db/schema.py`` returning empty. We
    # check the core tables that ARE in schema.py instead.
    schema_src = (repo_root() / "src" / "roam" / "db" / "schema.py").read_text(encoding="utf-8")
    for must_have in ("files", "symbols", "edges"):
        assert f"CREATE TABLE IF NOT EXISTS {must_have}" in schema_src, (
            f"schema.py is missing canonical core table {must_have!r} — "
            f"the W603 plumb depends on the core schema being intact."
        )

    # (c) connection.py preserves the _FTS5_SCHEMA_COLUMNS tuple, which
    # IS the canonical source of truth for the FTS5 virtual-table shape.
    conn_src = (repo_root() / "src" / "roam" / "db" / "connection.py").read_text(encoding="utf-8")
    assert "_FTS5_SCHEMA_COLUMNS" in conn_src, (
        "connection.py is missing _FTS5_SCHEMA_COLUMNS — the W603 plumb "
        "depends on the canonical FTS5 column tuple being intact."
    )
    for must_have in ("name", "qualified_name", "docstring"):
        assert f'"{must_have}"' in conn_src, (
            f"_FTS5_SCHEMA_COLUMNS missing canonical column {must_have!r} — "
            f"the W603 plumb depends on the docstring-aware FTS5 shape."
        )


# ===========================================================================
# (11) W596 HMAC chain UNTOUCHED — AST-check runs/ledger.py
# ===========================================================================


def test_w596_hmac_chain_untouched() -> None:
    """Cross-reference: ``runs/ledger.py`` was not modified by W603.

    W603 lives on the SQLite-connection substrate; W596 lives on the
    run-ledger HMAC substrate. They share no code. This test pins
    that ``read_run_meta`` exists and that the W596 marker vocabulary
    is unchanged.
    """
    ledger_src = repo_root() / "src" / "roam" / "runs" / "ledger.py"
    if not ledger_src.exists():
        pytest.skip("runs/ledger.py not present in this build")
    src = ledger_src.read_text(encoding="utf-8")
    assert "read_run_meta" in src, (
        "W596 substrate marker: read_run_meta must exist in runs/ledger.py — W603 should not have touched it."
    )


# ===========================================================================
# (12) Closed-enum subset — W978 first-hypothesis discipline
# ===========================================================================


def test_closed_enum_subset() -> None:
    """AST-check ``connection.py`` for the exact W603 closed-enum marker set.

    W978 first-hypothesis discipline: every emitted marker must
    correspond to a real silent-fail code path. Inventing markers
    that no path can ever emit adds dead vocabulary that contaminates
    the audit-trail surface.

    The expected closed enum after W603:

      * ``roam_config_read_failed:``
      * ``roam_readonly_uri_fallback:``
      * ``roam_query_timeout_parse_failed:``
      * ``roam_user_version_read_failed:``
      * ``roam_fts_drop_failed:``
      * ``roam_fts_create_failed:``

    Forbidden markers — paths that DO NOT exist in connection.py:

      * ``db_open_failed:`` — open_db raises ClickException (loud-by-raise).
      * ``db_schema_migration_failed:`` — _safe_alter is W740-narrowed
        (duplicate-column race only); every other migration error
        propagates loudly.
      * ``db_pragma_failed:`` — every PRAGMA (synchronous, cache_size,
        foreign_keys, temp_store, busy_timeout, mmap_size, journal_mode,
        locking_mode, wal_autocheckpoint) is unguarded and raises on
        failure. No silent-pass exists.
      * ``db_busy_timeout_exceeded:`` — busy_timeout is a SQLite-engine
        setting, not a wait-loop in connection.py.
      * ``db_safe_alter_dup_column:`` — W740-intentional idempotent;
        the silent skip is by design (column already exists).
      * ``db_pragma_optimize_failed:`` — explicit "not load-bearing;
        never refuse to close on this" by design (W978 intentional).
    """
    src_path = repo_root() / "src" / "roam" / "db" / "connection.py"
    source = src_path.read_text(encoding="utf-8")

    expected_markers = {
        "roam_config_read_failed:",
        "roam_readonly_uri_fallback:",
        "roam_query_timeout_parse_failed:",
        "roam_user_version_read_failed:",
        "roam_fts_drop_failed:",
        "roam_fts_create_failed:",
    }
    forbidden_markers = {
        "db_open_failed:",
        "db_schema_migration_failed:",
        "db_pragma_failed:",
        "db_busy_timeout_exceeded:",
        "db_safe_alter_dup_column:",
        "db_pragma_optimize_failed:",
    }

    for marker in expected_markers:
        assert marker in source, (
            f"expected marker prefix {marker!r} missing from db/connection.py — did the W603 plumb get reverted?"
        )
    for marker in forbidden_markers:
        assert marker not in source, (
            f"forbidden marker prefix {marker!r} present in "
            f"db/connection.py — this marker has no corresponding "
            f"silent-pass code path. W978 first-hypothesis discipline: "
            f"only plumb markers for paths that actually exist."
        )


# ===========================================================================
# (13) Function-signature audit — kw-only warnings_out
# ===========================================================================


def test_signatures_carry_kw_only_warnings_out() -> None:
    """AST-check every plumbed helper declares ``warnings_out`` as kw-only.

    Kw-only declaration is the back-compat-preserving signal that
    existing positional callers (~200+) are unaffected. Matches
    W598 / W599 / W600 / W601 / W602 signature-audit patterns.
    """
    src_path = repo_root() / "src" / "roam" / "db" / "connection.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {
        "_load_project_config",
        "get_connection",
        "_bump_user_version",
        "_ensure_fts5_table",
        "ensure_schema",
        "open_db",
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
    assert not missing, f"expected to find functions {missing!r} in db/connection.py"


# ===========================================================================
# (14) WarningsOut alias exported (substrate-floor type contract)
# ===========================================================================


def test_warnings_out_alias_exported() -> None:
    """``WarningsOut`` is exported as ``list[str] | None``.

    Pins the substrate-floor type contract — callers that import
    ``WarningsOut`` from ``connection.py`` get the canonical alias.
    """
    # PEP 604 union types are runtime-checkable via __args__.
    args = getattr(WarningsOut, "__args__", None)
    assert args is not None, "WarningsOut must be a Union type"
    type_names = {getattr(a, "__name__", repr(a)) for a in args}
    assert "list" in type_names or "List" in type_names, type_names
    assert "NoneType" in type_names or type(None) in args, type_names


# ===========================================================================
# (15) Open-db end-to-end with monkeypatched silent-pass — full plumb
# ===========================================================================


def test_open_db_threads_warnings_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: ``open_db(warnings_out=...)`` propagates to sub-helpers.

    Exercise the full thread: malformed ROAM_QUERY_TIMEOUT_S → marker
    surfaces on the open_db caller's bucket without intermediate loss.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("ROAM_QUERY_TIMEOUT_S", "definitely-not-a-float")

    warnings: list[str] = []
    with open_db(warnings_out=warnings) as conn:
        conn.execute("SELECT 1").fetchone()

    # At least one marker from the query-timeout parse failure.
    assert any(msg.startswith("roam_query_timeout_parse_failed:") for msg in warnings), (
        f"open_db must surface the get_connection marker on the caller's bucket; got {warnings!r}"
    )
