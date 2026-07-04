"""SQLite connection management with adaptive journal mode and performance pragmas."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from roam.db.schema import SCHEMA_SQL

# W603 Pattern-2 disclosure substrate: surfaces silent-pass paths that
# change user-visible DB behavior. We duplicate the
# ``WarningsOut = list[str] | None`` alias locally instead of importing
# from ``roam.output.formatter`` to keep ``connection.py`` import-light
# (formatter is ~50KB / pulls in JSON-envelope helpers / runs on every
# command's hot path; connection.py is the substrate floor). W907
# verify-cycle check: there is NO actual top-level import cycle —
# formatter.py imports connection lazily inside functions — so this
# duplication is a hot-path-cost choice, NOT a cycle hedge.
WarningsOut = list[str] | None

DEFAULT_DB_DIR = ".roam"
DEFAULT_DB_NAME = "index.db"


class StaleDbDirError(RuntimeError):
    """Raised when a configured db_dir cannot be created or written to.

    Carries the stale path + the config source (which file declared it)
    + a remediation hint, so the surrounding error envelope can surface
    something useful instead of opaque WinError text.
    """

    def __init__(self, db_dir: str, source: str, original_error: BaseException | None = None):
        self.db_dir = db_dir
        self.source = source
        self.original_error = original_error
        msg = (
            f"db_dir {db_dir!r} (configured in {source}) is not usable: "
            f"{original_error}. "
            f"Remediate by editing {source} to remove the stale db_dir entry, "
            f"or running `roam config db-dir --reset` to fall back to the project default. "
            f"If this looks unexpected, run `roam doctor` to diagnose your install."
        )
        super().__init__(msg)


def _safe_mkdir(db_dir: Path | str, source: str = "") -> Path:
    """mkdir but raise StaleDbDirError on OSError/PermissionError.

    The MCP subprocess wrapper otherwise sees an empty stdout + opaque
    stderr (e.g. ``[WinError 5] Access denied``) when a stale ``db_dir``
    from a different machine/user is configured. Raising a structured
    exception lets the wrapper surface a remediation hint.
    """
    p = Path(db_dir)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        raise StaleDbDirError(str(p), source or "<unknown>", e) from e
    return p


def find_project_root(start: str = ".") -> Path:
    """Find the project root by looking for .git directory."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path(start).resolve()


def _load_project_config(project_root: Path, *, warnings_out: WarningsOut = None) -> dict:
    """Load .roam/config.json if it exists.

    Returns an empty dict if the file is missing or malformed.

    W740 rationale (narrowed from bare ``except Exception``)
    -------------------------------------------------------
    The original handler caught every ``Exception`` — including
    programmer-class bugs (``NameError`` / ``AttributeError`` /
    ``TypeError`` / ``ImportError``) — and silently returned ``{}``. Per
    W531 fail-loud + W653 incident discipline, bug-class exceptions MUST
    propagate so a degraded config-load path never masks a refactor that
    broke this function. The narrow set covers the legitimate "config
    file is unreadable or malformed" cases the empty-dict fallback was
    designed for:

    * ``OSError`` — covers ``FileNotFoundError`` (TOCTOU between
      ``exists()`` and ``read_text()``) and ``PermissionError``
      (filesystem ACLs / Windows handle locks).
    * ``json.JSONDecodeError`` — malformed JSON (incomplete write,
      hand-edited typo, partial truncation).
    * ``UnicodeDecodeError`` — file present but not valid UTF-8 (mojibake
      from a misconfigured editor or a binary blob mistakenly written
      here).

    W603 Pattern-2 disclosure: when ``config.json`` is present but
    unreadable / corrupt / mojibake, the silent empty-dict fallback
    silently DROPS any ``db_dir`` override the operator declared — they
    get the project-default DB path instead of the one they configured,
    looking identical to "no override was ever set." When
    ``warnings_out`` is threaded in, the silent-pass emits a
    ``roam_config_read_failed:<path>:<exc_class>:<detail>`` closed-enum
    marker so callers can disclose the drop. ``warnings_out=None``
    (default) preserves the legacy silent behaviour — no caller is
    forced to thread the bucket.

    Intentional-absence (NOT plumbed, W978 first-hypothesis discipline):
    the missing-file path (``config_path.exists() == False``) is the
    common cold-start case — every project without a custom ``db_dir``
    override hits it. Disclosing on cold-start would train operators
    to ignore real warnings.
    """
    import json

    config_path = project_root / DEFAULT_DB_DIR / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            if warnings_out is not None:
                warnings_out.append(f"roam_config_read_failed:{config_path}:{type(exc).__name__}:{exc}")
    return {}


def write_project_config(config: dict, project_root: Path | None = None) -> Path:
    """Write (or update) .roam/config.json.

    Merges *config* into the existing config so existing keys are preserved.
    Returns the path of the written file.
    """
    import json

    if project_root is None:
        project_root = find_project_root()
    roam_dir = project_root / DEFAULT_DB_DIR
    roam_dir.mkdir(exist_ok=True)
    config_path = roam_dir / "config.json"
    existing = _load_project_config(project_root)
    existing.update(config)
    config_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return config_path


def get_db_path(project_root: Path | None = None) -> Path:
    """Get the path to the index database.

    Resolution order (first match wins):

    1. ``ROAM_DB_DIR`` environment variable — redirect to a local directory,
       useful when the project lives on OneDrive/Dropbox or a network drive.
    2. ``.roam/config.json`` → ``"db_dir"`` key — persistent per-project
       alternative to the env-var (write once with ``roam config``).
    3. Default: ``<project_root>/.roam/index.db``.
    """
    override = os.environ.get("ROAM_DB_DIR")
    if override:
        db_dir = _safe_mkdir(override, source="ROAM_DB_DIR env")
        return db_dir / DEFAULT_DB_NAME
    if project_root is None:
        project_root = find_project_root()
    # Check .roam/config.json for a db_dir override
    config = _load_project_config(project_root)
    if config.get("db_dir"):
        db_dir = _safe_mkdir(config["db_dir"], source=".roam/config.json db_dir")
        return db_dir / DEFAULT_DB_NAME
    db_dir = _safe_mkdir(project_root / DEFAULT_DB_DIR, source="<project default>")
    return db_dir / DEFAULT_DB_NAME


def _is_cloud_synced(path: Path) -> bool:
    """Detect if *path* lives under a cloud-sync folder (OneDrive, Dropbox, etc.).

    WAL mode creates auxiliary ``-wal`` and ``-shm`` files that cloud sync
    services aggressively lock, causing SQLite writes to stall.  When we
    detect a cloud-synced path we fall back to DELETE journal mode.
    """
    markers = ("onedrive", "dropbox", "google drive", "icloud")
    resolved = str(path.resolve()).lower()
    return any(m in resolved for m in markers)


def _open_sqlite_connection(
    db_path: Path,
    readonly: bool,
    warnings_out: WarningsOut,
) -> sqlite3.Connection:
    """Open the raw SQLite connection, enforcing readonly at the URI level when possible.

    UNC network paths (e.g. \\\\server\\share\\...) cannot be expressed as
    valid SQLite file:// URIs — SQLite rejects authority-based URIs.
    Mapped drive letters (M:\\...) work fine.  We try the URI form first
    (which enforces read-only at the driver level) and fall back to a
    plain connection when the path cannot be expressed as a URI.

    W603: ``roam_readonly_uri_fallback`` is appended to ``warnings_out`` when
    the URI form fails and the caller loses the driver-level read-only rail.
    """
    if not readonly:
        return sqlite3.connect(str(db_path), timeout=30)
    try:
        uri = db_path.as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=30)
    except (sqlite3.OperationalError, ValueError) as exc:
        if warnings_out is not None:
            warnings_out.append(f"roam_readonly_uri_fallback:{db_path}:{type(exc).__name__}:{exc}")
        return sqlite3.connect(str(db_path), timeout=30)


def _apply_journal_and_checkpoint(conn: sqlite3.Connection, db_path: Path, readonly: bool) -> None:
    """Set journal mode and WAL checkpoint tuning based on write mode and path type.

    Cloud-sync services (OneDrive, Dropbox, etc.) lock WAL/SHM auxiliary
    files and even the main DB during sync, causing writes to stall.
    Mitigations on cloud paths:
      1. DELETE journal — avoids WAL/SHM auxiliary files entirely
      2. EXCLUSIVE locking — holds the file lock for the session
    WAL auto-checkpoint is raised to 10 000 pages (10× default) on non-cloud
    paths to reduce write amplification on heavy index loads.
    """
    if readonly:
        return
    cloud = _is_cloud_synced(db_path)
    if cloud:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=10000")


def _apply_base_pragmas(conn: sqlite3.Connection) -> None:
    """Apply performance and correctness PRAGMAs that are unconditional.

    Notes:
    - busy_timeout is set via PRAGMA (not only sqlite3.connect(timeout=)) so
      raw consumers (MCP test fixtures, direct sqlite3.connect) see the same
      retry budget as open_db.  (R9 perf recheck #5)
    - mmap_size=1GB: conservative bump from 256MB per audit B6; OS pager caps
      effective use at available RAM so the declared value is a ceiling, not a
      guarantee. On 32-bit builds SQLite silently maps less.
    """
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64 MB
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA mmap_size=1073741824")  # 1 GB


def _install_query_timeout(
    conn: sqlite3.Connection,
    timeout_str: str,
    warnings_out: WarningsOut,
) -> None:
    """Optionally install a progress-handler query timeout from ROAM_QUERY_TIMEOUT_S.

    W603: ``roam_query_timeout_parse_failed`` is appended to ``warnings_out``
    when the env var is set but unparseable so callers can detect the
    silent no-op.  Skipped entirely when ``timeout_str`` is empty.
    """
    if not timeout_str:
        return
    try:
        timeout_s = float(timeout_str)
    except ValueError:
        timeout_s = 0.0
        if warnings_out is not None:
            warnings_out.append(f"roam_query_timeout_parse_failed:{timeout_str}")
    if timeout_s > 0:
        import time as _time

        deadline = _time.monotonic() + timeout_s

        def _interrupter() -> int:
            # Returning non-zero from the progress handler aborts the query.
            return 1 if _time.monotonic() > deadline else 0

        conn.set_progress_handler(_interrupter, 1000)  # check every 1 000 vops


def get_connection(
    db_path: Path | None = None,
    readonly: bool = False,
    *,
    warnings_out: WarningsOut = None,
) -> sqlite3.Connection:
    """Get a SQLite connection with optimized settings.

    W603 Pattern-2 disclosure: ``warnings_out`` (kw-only) opts the call
    into structured surfacing of silent-pass paths that change observed
    DB behavior:

    * ``roam_readonly_uri_fallback:<path>:<exc_class>:<detail>`` — the
      requested ``readonly=True`` URI form failed (UNC path, malformed
      URI) and the function silently fell back to a plain
      ``sqlite3.connect()`` that has NO driver-level read-only
      enforcement. The caller asked for a read-only handle; they got
      one without the safety rail.
    * ``roam_query_timeout_parse_failed:<value>`` — the operator set
      ``ROAM_QUERY_TIMEOUT_S=<garbage>`` expecting a per-query timeout,
      but the parse failed and the env-var silently coerced to 0 (no
      progress handler installed). The opt-in safety mechanism is
      absent without disclosure.

    ``warnings_out=None`` (default) preserves the silent legacy
    behaviour — every existing caller stays unchanged.
    """
    if db_path is None:
        db_path = get_db_path()
    conn = _open_sqlite_connection(db_path, readonly, warnings_out)
    conn.row_factory = sqlite3.Row
    _apply_journal_and_checkpoint(conn, db_path, readonly)
    _apply_base_pragmas(conn)
    _install_query_timeout(conn, os.environ.get("ROAM_QUERY_TIMEOUT_S", "").strip(), warnings_out)
    return conn


# R9.A2 — Sequence-numbered migration ledger.
#
# Each migration is a tuple ``(seq, name, fn)`` where ``fn(conn)`` is
# idempotent (re-runnable without side effects on a DB that's already
# at or past this seq). The list IS the source of truth: contributors
# add one entry; ``MIGRATION_OPS_COUNT`` and the count test are both
# derived from it. No more manual count drift.
#
# ``USER_VERSION`` is still managed by hand because it's a contract
# with downstream consumers (manifest writer, bundle import, drift
# detection in ``roam doctor``) — bumping it announces to those
# consumers that the schema has changed in a way they should care
# about. Internal column-add migrations don't always need that
# announcement, hence the separation.
#
# The seq numbers are an internal sequence — adopting them now lets
# us later add a per-seq applied-marker table for partial-failure
# recovery without another refactor.


def _alter(table: str, col: str, type_: str):
    """Bind a ``_safe_alter`` call as a callable for the migration ledger."""
    return lambda c: _safe_alter(c, table, col, type_)


def _exec(sql: str):
    """Bind a literal-SQL ``conn.execute`` as a callable for the ledger.

    Only used for ``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF
    EXISTS`` style idempotent statements; ``_alter`` is preferred for
    column adds.
    """
    return lambda c: c.execute(sql)


_MIGRATIONS: list[tuple[int, str, "Callable[[sqlite3.Connection], object]"]] = [
    # symbols extras
    (1, "symbols.default_value", _alter("symbols", "default_value", "TEXT")),
    (2, "symbols.is_async", _alter("symbols", "is_async", "INTEGER DEFAULT 0")),
    (3, "symbols.decorators", _alter("symbols", "decorators", "TEXT DEFAULT ''")),
    # file_stats
    (4, "file_stats.health_score", _alter("file_stats", "health_score", "REAL")),
    (5, "file_stats.cochange_entropy", _alter("file_stats", "cochange_entropy", "REAL")),
    (6, "file_stats.cognitive_load", _alter("file_stats", "cognitive_load", "REAL")),
    (7, "file_stats.coverage_pct", _alter("file_stats", "coverage_pct", "REAL")),
    (8, "file_stats.covered_lines", _alter("file_stats", "covered_lines", "INTEGER")),
    (9, "file_stats.coverable_lines", _alter("file_stats", "coverable_lines", "INTEGER")),
    # snapshots
    (10, "snapshots.tangle_ratio", _alter("snapshots", "tangle_ratio", "REAL")),
    (11, "snapshots.avg_complexity", _alter("snapshots", "avg_complexity", "REAL")),
    (12, "snapshots.brain_methods", _alter("snapshots", "brain_methods", "INTEGER")),
    # symbol_metrics — Halstead + cyclomatic density (v7.4)
    (13, "symbol_metrics.cyclomatic_density", _alter("symbol_metrics", "cyclomatic_density", "REAL")),
    (14, "symbol_metrics.halstead_volume", _alter("symbol_metrics", "halstead_volume", "REAL")),
    (15, "symbol_metrics.halstead_difficulty", _alter("symbol_metrics", "halstead_difficulty", "REAL")),
    (16, "symbol_metrics.halstead_effort", _alter("symbol_metrics", "halstead_effort", "REAL")),
    (17, "symbol_metrics.halstead_bugs", _alter("symbol_metrics", "halstead_bugs", "REAL")),
    (18, "symbol_metrics.coverage_pct", _alter("symbol_metrics", "coverage_pct", "REAL")),
    (19, "symbol_metrics.covered_lines", _alter("symbol_metrics", "covered_lines", "INTEGER")),
    (20, "symbol_metrics.coverable_lines", _alter("symbol_metrics", "coverable_lines", "INTEGER")),
    # files (v7.6 file role)
    (21, "files.file_role", _alter("files", "file_role", "TEXT DEFAULT 'source'")),
    # math_signals — extended set (v8.4)
    (22, "math_signals.self_call_count", _alter("math_signals", "self_call_count", "INTEGER DEFAULT 0")),
    (23, "math_signals.str_concat_in_loop", _alter("math_signals", "str_concat_in_loop", "INTEGER DEFAULT 0")),
    (24, "math_signals.loop_invariant_calls", _alter("math_signals", "loop_invariant_calls", "TEXT")),
    (25, "math_signals.loop_bound_small", _alter("math_signals", "loop_bound_small", "INTEGER DEFAULT 0")),
    (26, "math_signals.calls_in_loops_qualified", _alter("math_signals", "calls_in_loops_qualified", "TEXT")),
    (27, "math_signals.loop_lookup_calls", _alter("math_signals", "loop_lookup_calls", "TEXT")),
    (28, "math_signals.front_ops_in_loop", _alter("math_signals", "front_ops_in_loop", "INTEGER DEFAULT 0")),
    (
        29,
        "math_signals.loop_with_multiplication",
        _alter("math_signals", "loop_with_multiplication", "INTEGER DEFAULT 0"),
    ),
    (30, "math_signals.loop_with_modulo", _alter("math_signals", "loop_with_modulo", "INTEGER DEFAULT 0")),
    # cross-language bridge metadata
    (31, "edges.bridge", _alter("edges", "bridge", "TEXT")),
    (32, "edges.confidence", _alter("edges", "confidence", "REAL")),
    # v11 — source file tracking for O(changed) incremental edge rebuild.
    # This column AND its supporting index must apply in this order
    # (column first, index second) so the index references a real column.
    (33, "edges.source_file_id", _alter("edges", "source_file_id", "INTEGER REFERENCES files(id) ON DELETE CASCADE")),
    (34, "idx_edges_source_file", _exec("CREATE INDEX IF NOT EXISTS idx_edges_source_file ON edges(source_file_id)")),
    # runtime_stats — OTel DB semantic attributes (v8.1)
    (35, "runtime_stats.otel_db_system", _alter("runtime_stats", "otel_db_system", "TEXT")),
    (36, "runtime_stats.otel_db_operation", _alter("runtime_stats", "otel_db_operation", "TEXT")),
    (37, "runtime_stats.otel_db_statement_type", _alter("runtime_stats", "otel_db_statement_type", "TEXT")),
    # graph_metrics — expanded SNA + composite debt score (v8.6)
    (38, "graph_metrics.closeness", _alter("graph_metrics", "closeness", "REAL DEFAULT 0")),
    (39, "graph_metrics.eigenvector", _alter("graph_metrics", "eigenvector", "REAL DEFAULT 0")),
    (40, "graph_metrics.clustering_coefficient", _alter("graph_metrics", "clustering_coefficient", "REAL DEFAULT 0")),
    (41, "graph_metrics.debt_score", _alter("graph_metrics", "debt_score", "REAL DEFAULT 0")),
    # v12.1 — Django framework awareness (ported from upstream fork work)
    (42, "symbols.framework_type", _alter("symbols", "framework_type", "TEXT")),
    (43, "symbols.field_type", _alter("symbols", "field_type", "TEXT")),
    (44, "symbols.field_base_type", _alter("symbols", "field_base_type", "TEXT")),
    (45, "symbols.field_metadata", _alter("symbols", "field_metadata", "TEXT")),
    (46, "edges.call_function", _alter("edges", "call_function", "TEXT")),
    (
        47,
        "idx_symbols_framework_type",
        _exec("CREATE INDEX IF NOT EXISTS idx_symbols_framework_type ON symbols(framework_type)"),
    ),
    # v11 — drop redundant idx_edges_kind (subsumed by idx_edges_kind_target)
    (48, "drop idx_edges_kind", _exec("DROP INDEX IF EXISTS idx_edges_kind")),
    # virtual / managed tables — both helpers are idempotent
    (49, "_ensure_tfidf_cascade", lambda c: _ensure_tfidf_cascade(c)),
    (50, "_ensure_fts5_table", lambda c: _ensure_fts5_table(c)),
    # Nested-lookup discriminator: tightens detect_nested_lookup's
    # predicate from the 3-signal triplet (nested_loops + subscript +
    # loop_compare) to a 4-signal predicate that also requires the inner
    # loop body to contain an equality test on two per-iteration keys
    # AND a write gated on that equality. Cuts the ~85% PHP FP rate
    # observed in the 2026-05 dogfood (streaming CSV / column-wise
    # output / matrix render). Pre-existing rows default to 0; after a
    # re-index the column reflects the actual structural signal.
    (
        51,
        "math_signals.loop_eq_with_dependent_write",
        _alter("math_signals", "loop_eq_with_dependent_write", "INTEGER DEFAULT 0"),
    ),
    # W82 / ROADMAP A8: index_manifest.steps_status — JSON map of per-sub-step
    # completion status ({step: {status, error_excerpt, duration_ms}}). Lets
    # `roam doctor` surface "your index is missing X because that step failed".
    # Older rows leave this NULL; the doctor check tolerates NULL as "no
    # per-step data recorded for this run" (treated as pass).
    (52, "index_manifest.steps_status", _alter("index_manifest", "steps_status", "TEXT")),
    # W81 / ROADMAP A6: per-component VERSION stamps for drift detection.
    # When a bridge / extractor / detector changes its inference logic,
    # the rows it produced under the older VERSION carry stale shape.
    # These columns let consumers (`roam doctor`, manifest diff, bundle
    # import) spot the drift WITHOUT a full re-index — they can compare
    # the stamped version against the ABC's current ``VERSION`` class
    # attribute. NULL means "produced by a pre-A6 indexer" (treated as
    # 1.0.0 for compatibility). Findings get no column because the
    # ``findings`` TABLE doesn't exist in the schema yet (A4 / hybrid
    # finding registry is queued; ``findings`` shows up only as a JSON
    # field in command envelopes today). When A4 lands and creates the
    # table, add a sibling ``findings.source_version`` migration.
    (53, "edges.bridge_version", _alter("edges", "bridge_version", "TEXT")),
    (54, "symbols.extractor_version", _alter("symbols", "extractor_version", "TEXT")),
    # index_manifest.component_versions — JSON object capturing the
    # version map at index time, shape:
    # ``{"bridges": {name: ver}, "detectors": {task: ver}, "extractors": {lang: ver}}``.
    # Consumers compare row-N to row-N-1 to spot a bump and decide
    # whether to invalidate stamped rows. Older runs leave this NULL.
    (55, "index_manifest.component_versions", _alter("index_manifest", "component_versions", "TEXT")),
    # W90 / ROADMAP A4: hybrid findings registry table. SCHEMA_SQL creates
    # it on fresh DBs (CREATE TABLE IF NOT EXISTS); this migration handles
    # legacy DBs created before A4. ``ON CREATE TABLE IF NOT EXISTS`` is
    # idempotent — re-running on an already-migrated DB is a no-op. The
    # source_version column was reserved by W81 (ROADMAP A6); landing the
    # table now activates that reservation. Indexes are created in the same
    # step (also idempotent) so a consumer that runs `roam findings` on a
    # legacy DB hits indexed scans, not full table scans.
    (
        56,
        "findings registry table + indexes",
        _exec(
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
        ),
    ),
    (
        57,
        "idx_findings_subject",
        _exec("CREATE INDEX IF NOT EXISTS idx_findings_subject ON findings(subject_kind, subject_id)"),
    ),
    (
        58,
        "idx_findings_detector",
        _exec("CREATE INDEX IF NOT EXISTS idx_findings_detector ON findings(source_detector)"),
    ),
    (59, "idx_findings_created", _exec("CREATE INDEX IF NOT EXISTS idx_findings_created ON findings(created_at)")),
    # B8 — per-snapshot spectral gap (algebraic connectivity / lambda2) so
    # `roam forecast` can project a TRUE historical gap-per-snapshot series
    # toward structural failure (vs the Option-B one-shot signal). Older
    # snapshot rows leave this NULL; `forecast_spectral_decay` skips NULLs
    # when assembling the series, so a partial-history series is honest.
    (60, "snapshots.spectral_gap", _alter("snapshots", "spectral_gap", "REAL")),
]


def ensure_schema(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> None:
    """Create tables if they don't exist, and apply migrations.

    R9.A2: migrations now run from the ``_MIGRATIONS`` ledger above
    so the contract (count, ordering, name → callable mapping) is
    visible in one place. Each migration is idempotent — re-running
    ``ensure_schema`` is safe at any seq.

    W603 Pattern-2 disclosure: ``warnings_out`` is threaded into the
    two sub-helpers with plumbable silent-pass paths — ``_ensure_fts5_table``
    (DROP / CREATE silent-skips) and ``_bump_user_version`` (PRAGMA
    user_version read coerce). The per-migration loop itself does NOT
    catch exceptions (intentional fail-loud — a migration that raises
    propagates as an unexpected DatabaseError to ``open_db``'s
    ``except sqlite3.DatabaseError`` clause, which surfaces a
    ``click.ClickException`` with remediation hints). Migration-step
    silent-swallow is therefore NOT plumbed — the W740-narrowed
    ``_safe_alter`` only swallows the duplicate-column race (intentional
    idempotent), and every other migration error propagates loudly.

    W97 substrate UNTOUCHED: ``USER_VERSION`` constant + the
    schema-version contract are unmodified. This plumb only opts in
    callers that want migration-time silent-pass disclosure.
    """
    conn.executescript(SCHEMA_SQL)
    for _seq, _name, fn in _MIGRATIONS:
        # The ``_MIGRATIONS`` ledger holds bound callables — most are
        # idempotent column-adds via ``_safe_alter`` (already W740-
        # narrowed). ``_ensure_fts5_table`` is the only entry with
        # plumbable silent-pass paths, so special-case it by name to
        # thread ``warnings_out`` through. Every other migration is
        # either fully loud or W740-intentional idempotent.
        if _name == "_ensure_fts5_table":
            _ensure_fts5_table(conn, warnings_out=warnings_out)
        else:
            fn(conn)
    # v12.x: index_manifest table is created in SCHEMA_SQL above. Bump
    # PRAGMA user_version so the manifest writer can mirror it. Migration
    # is idempotent — re-running ensure_schema() never lowers the value.
    _bump_user_version(conn, USER_VERSION, warnings_out=warnings_out)


# Current schema version. Bump this when adding migrations that consumers
# (manifest writer, bundle import, drift checks) need to detect. Mirrored
# into ``index_manifest.schema_version`` on every index run.
#
# This is a CONTRACT version — separate from ``len(_MIGRATIONS)`` which
# is the operation count. Bump USER_VERSION when downstream consumers
# need to invalidate caches / re-attempt schema-aware logic, not on
# every column add.
#
# USER_VERSION must be bumped on every change to src/roam/db/schema.py.
# The CI check tests/test_user_version_discipline.py enforces this by
# snapshotting a hash of schema.py; if the hash drifts, the test
# requires USER_VERSION to drift too (lockstep updates).
USER_VERSION = 18

# Derived from the ledger so adding/removing a migration auto-updates
# the count without a manual touch. The pin test in
# ``tests/test_db_user_version.py`` still catches "you added a migration
# but forgot to bump USER_VERSION when consumers need to know".
MIGRATION_OPS_COUNT = len(_MIGRATIONS)


def _bump_user_version(
    conn: sqlite3.Connection,
    target: int,
    *,
    warnings_out: WarningsOut = None,
) -> None:
    """Set ``PRAGMA user_version`` to *target* if it's currently lower.

    Never lowers the value — that would cause downgraded clients to think
    the DB is fresher than it really is.

    W603 Pattern-2 disclosure: the ``except sqlite3.DatabaseError``
    clause coerces ``current`` to 0 on a failed PRAGMA read, which then
    drives the unconditional bump to *target*. That silent reset masks
    a legitimate drift signal (the W596 + W97 USER_VERSION discipline
    relies on read-then-compare to detect downgraded/corrupted DBs).
    When ``warnings_out`` is threaded in, the silent-coerce emits a
    ``roam_user_version_read_failed:<exc_class>:<detail>`` marker so
    the manifest writer / drift detector can disclose the read failure
    rather than show a clean-looking jump. ``warnings_out=None``
    preserves the legacy silent behaviour.

    W97 substrate UNTOUCHED: the schema-level ``USER_VERSION`` constant
    + ``ensure_schema()`` invariant are not modified. This plumb only
    surfaces the read-path failure that would otherwise be lost.
    """
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        current = int(row[0]) if row else 0
    except sqlite3.DatabaseError as exc:
        if warnings_out is not None:
            warnings_out.append(f"roam_user_version_read_failed:{type(exc).__name__}:{exc}")
        current = 0
    if current < target:
        # PRAGMA can't take ? params — target is internal, not user input.
        conn.execute(f"PRAGMA user_version = {int(target)}")


def _ensure_tfidf_cascade(conn: sqlite3.Connection):
    """Ensure symbol_tfidf has ON DELETE CASCADE (missing in early schema)."""
    # Check if table exists and has proper FK — simplest: check table_info
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='symbol_tfidf'").fetchone()
    if row is None:
        # Table doesn't exist yet; SCHEMA_SQL will create it with CASCADE
        return
    sql = row[0] or ""
    if "ON DELETE CASCADE" in sql.upper():
        return  # Already correct
    # Recreate with proper FK (TF-IDF data is recomputed on every index)
    conn.execute("DROP TABLE IF EXISTS symbol_tfidf")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS symbol_tfidf ("
        "symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE, "
        "terms TEXT NOT NULL, "
        "updated_at TEXT DEFAULT (datetime('now'))"
        ")"
    )


_FTS5_SCHEMA_COLUMNS = ("name", "qualified_name", "signature", "docstring", "kind", "file_path")


def _fts5_table_is_current(conn: sqlite3.Connection) -> bool:
    """Return True if symbol_fts exists and already has the docstring column."""
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
    if not row:
        return False
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(symbol_fts)").fetchall()}
    return "docstring" in existing_cols


def _fts5_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True if symbol_fts exists (regardless of schema version)."""
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone())


def _drop_fts5_table(conn: sqlite3.Connection, warnings_out: WarningsOut) -> bool:
    """Drop symbol_fts. Returns False and appends a warning when the drop fails."""
    try:
        conn.execute("DROP TABLE symbol_fts")
        return True
    except sqlite3.OperationalError as exc:
        if warnings_out is not None:
            warnings_out.append(f"roam_fts_drop_failed:{type(exc).__name__}:{exc}")
        return False


def _create_fts5_table(conn: sqlite3.Connection, warnings_out: WarningsOut) -> None:
    """Create symbol_fts with the current schema. Appends a warning when FTS5 is unavailable."""
    try:
        cols = ", ".join(_FTS5_SCHEMA_COLUMNS)
        conn.execute(f"CREATE VIRTUAL TABLE symbol_fts USING fts5({cols}, tokenize='porter unicode61')")
    except sqlite3.OperationalError as exc:
        # Covers "no such module: fts5" (missing build) AND locked-DB / corrupt-schema
        # variants — operators need to know why FTS search is degraded.
        if warnings_out is not None:
            warnings_out.append(f"roam_fts_create_failed:{type(exc).__name__}:{exc}")


def _ensure_fts5_table(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None):
    """Create or upgrade the FTS5 full-text search virtual table.

    FTS5 pushes tokenization, indexing, and BM25 ranking entirely into
    SQLite's C engine — 1000x faster than the Python-side TF-IDF approach.
    Falls back gracefully if FTS5 is not compiled into the SQLite build.

    Schema migration (audit B8): the table now includes a ``docstring``
    column so ``roam retrieve`` and ``roam search-semantic`` can match
    against natural-language docstrings. Existing tables lacking the column
    are dropped and re-created (rows are repopulated by ``build_fts_index``
    on the next index run).

    W603 Pattern-2 disclosure: both silent-pass paths emit a closed-enum
    marker (``roam_fts_drop_failed:`` / ``roam_fts_create_failed:``) when
    ``warnings_out`` is provided so operators see WHY FTS search is degraded.
    ``warnings_out=None`` (default) preserves the legacy silent fallback.
    """
    if _fts5_table_is_current(conn):
        return
    if _fts5_table_exists(conn) and not _drop_fts5_table(conn, warnings_out):
        return
    _create_fts5_table(conn, warnings_out)


def _safe_alter(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't exist.

    Pattern-2 discipline (W740): the legacy form swallowed every
    ``sqlite3.OperationalError`` (locked DB, syntax error, missing table,
    FK constraint, duplicate column) and pretended the migration had
    applied. Narrowed to the actual idempotent signal:

    1. Pre-check via ``PRAGMA table_info`` — skip the ALTER when the
       column is already there. This is the common idempotent case
       (re-running ``ensure_schema``) and now never throws.
    2. Catch only the residual "duplicate column" race (another
       connection added the column between the PRAGMA read and the
       ALTER). Every other ``OperationalError`` (missing table,
       syntax, locked DB) propagates — those are real bugs that the
       silent swallow would have masked.
    """
    try:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        existing = set()
    if column in existing:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError as exc:
        # Narrow to the duplicate-column race; let other operational
        # errors (locked DB, syntax error, missing table) propagate so
        # they show up as real migration failures rather than silent
        # no-ops. SQLite's canonical message is "duplicate column name".
        if "duplicate column" not in str(exc).lower():
            raise


# ---------------------------------------------------------------------------
# Batched IN-clause helper — avoid SQLITE_MAX_VARIABLE_NUMBER (default 999)
# ---------------------------------------------------------------------------

# ``batched_in`` is referenced from 60+ non-test files — a shotgun-surgery
# hot spot where a change to the placeholder syntax or the chunking math
# ripples across every caller. The two contract anchors below are pinned as
# named constants so they change in exactly one place, and the variable
# limit is enforced at the boundary (Pattern-2: a would-be
# ``too many SQL variables`` error surfaces here, not inside a caller).
_PLACEHOLDER = "{ph}"  # marker every caller's SQL template must contain
_SQLITE_MAX_VARIABLES = 999  # default SQLITE_MAX_VARIABLE_NUMBER
_BATCH_SIZE = 500  # leaves room for extra pre/post params under the limit
_BATCH_OPTION_KEYS = frozenset({"pre", "post", "batch_size"})


class _BatchOptions(NamedTuple):
    pre: tuple
    post: tuple
    batch_size: int


def _parse_batch_options(function_name: str, batch_options: dict[str, object]) -> _BatchOptions:
    unexpected = set(batch_options) - _BATCH_OPTION_KEYS
    if unexpected:
        name = sorted(unexpected)[0]
        raise TypeError(f"{function_name}() got an unexpected keyword argument {name!r}")
    return _BatchOptions(
        pre=tuple(batch_options.get("pre", ())),
        post=tuple(batch_options.get("post", ())),
        batch_size=batch_options.get("batch_size", _BATCH_SIZE),
    )


def _iter_in_batches(sql: str, ids: list, options: _BatchOptions):
    """Yield ``(query, params)`` pairs for one batched IN-clause execution.

    Single source of truth for the chunking algorithm shared by
    ``batched_in`` and ``_legacy_batched_scalar_sum`` — the helper is
    referenced from 60+ files, so the substitution/param-ordering
    contract must change in exactly one place.

    Raises ``ValueError`` when *sql* lacks a ``{ph}`` marker: the legacy
    behavior re-executed the identical unbatched query once per chunk,
    silently duplicating rows (or double-counting aggregates). Also
    raises ``ValueError`` when the largest rendered batch would exceed
    SQLite's variable limit, so a chunking-math change fails here instead
    of rippling as a cryptic ``too many SQL variables`` error in a caller.
    """
    n_ph = sql.count(_PLACEHOLDER)
    if n_ph == 0:
        raise ValueError(f"batched_in SQL must contain at least one {_PLACEHOLDER} placeholder marker")
    pre = options.pre
    post = options.post
    batch_size = options.batch_size
    chunk = max(1, batch_size // n_ph)
    # Boundary guard: ``batch_size`` does not account for pre/post params,
    # so a too-large value could render a batch that blows SQLite's variable
    # limit. ``min(chunk, len(ids))`` is the largest possible batch (only the
    # final batch may be smaller), so one check covers every iteration.
    max_params = min(chunk, len(ids)) * n_ph + len(pre) + len(post)
    if max_params > _SQLITE_MAX_VARIABLES:
        raise ValueError(
            f"batched_in batch_size={batch_size} with {n_ph} placeholder(s) "
            f"renders up to {max_params} params per batch, exceeding SQLite's "
            f"{_SQLITE_MAX_VARIABLES}-variable limit; reduce batch_size"
        )
    for i in range(0, len(ids), chunk):
        batch = ids[i : i + chunk]
        ph = ",".join("?" for _ in batch)
        yield sql.replace(_PLACEHOLDER, ph), list(pre) + batch * n_ph + list(post)


def batched_in(
    conn: sqlite3.Connection,
    sql: str,
    ids,
    **batch_options,
) -> list:
    """Execute *sql* with ``{ph}`` placeholder(s) in batches.

    Handles single and double IN-clauses automatically::

        # Single IN
        batched_in(conn, "SELECT * FROM t WHERE id IN ({ph})", ids)

        # Double IN (same set)
        batched_in(conn, "... WHERE src IN ({ph}) AND tgt IN ({ph})", ids)

        # Extra params before / after
        batched_in(conn, "... WHERE kind=? AND id IN ({ph})", ids, pre=[kind])

    Returns a flat list of all rows across batches. Raises ``ValueError``
    if *sql* contains no ``{ph}`` marker (misuse formerly re-ran the same
    unbatched query once per chunk, duplicating rows).
    """
    options = _parse_batch_options("batched_in", batch_options)
    if not ids:
        return []
    rows = []
    for q, params in _iter_in_batches(sql, list(ids), options):
        rows.extend(conn.execute(q, params).fetchall())
    return rows


_LEGACY_BATCHED_COUNT_NAME = "batched_count"


def _legacy_batched_scalar_sum(
    conn: sqlite3.Connection,
    sql: str,
    ids,
    **batch_options,
) -> int:
    """Compatibility implementation for the removed public count helper."""
    options = _parse_batch_options(_LEGACY_BATCHED_COUNT_NAME, batch_options)
    if not ids:
        return 0
    total = 0
    for q, params in _iter_in_batches(sql, list(ids), options):
        total += conn.execute(q, params).fetchone()[0]
    return total


def __getattr__(name: str):
    if name == _LEGACY_BATCHED_COUNT_NAME:
        return _legacy_batched_scalar_sum
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def db_exists(project_root: Path | None = None) -> bool:
    """Check if an index database exists."""
    path = get_db_path(project_root)
    return path.exists() and path.stat().st_size > 0


def _open_validated_connection(
    db_path: Path,
    readonly: bool,
    warnings_out: WarningsOut,
) -> sqlite3.Connection:
    """Open the raw connection and convert DatabaseError → ClickException."""
    import click

    try:
        return get_connection(db_path, readonly=readonly, warnings_out=warnings_out)
    except sqlite3.DatabaseError as exc:
        raise click.ClickException(
            f"Database error: {exc}\n"
            "  The roam index may be corrupted. Run `roam init --force` to rebuild it\n"
            "  from scratch, or delete .roam/index.db and run `roam init`.\n"
            "  If this looks unexpected, run `roam doctor` to diagnose your install."
        ) from exc


def _setup_schema_or_raise(conn: sqlite3.Connection, warnings_out: WarningsOut) -> None:
    """Run ensure_schema and convert DatabaseError → ClickException, closing conn first."""
    import click

    try:
        ensure_schema(conn, warnings_out=warnings_out)
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise click.ClickException(
            f"Database schema error: {exc}\n"
            "  The roam index may be corrupted or from an incompatible version.\n"
            "  Run `roam init --force` to rebuild it, or delete .roam/index.db\n"
            "  and run `roam init`.\n"
            "  If this looks unexpected, run `roam doctor` to diagnose your install."
        ) from exc


def _commit_and_optimize(conn: sqlite3.Connection) -> None:
    """Commit pending writes and run PRAGMA optimize (logged + skipped on error)."""
    conn.commit()
    # PRAGMA optimize keeps the query planner's stats fresh after writes
    # (added in SQLite 3.18). Cheap on each commit; no-op when stats
    # haven't drifted. Not load-bearing — never refuse to close on this,
    # but log the rare failure to stderr so the only visible symptom
    # (gradual query-latency drift) is diagnosable instead of silent.
    # Not plumbed into ``warnings_out`` by design (W978 intentional-
    # absence): query-planner staleness carries no operator action item.
    try:
        conn.execute("PRAGMA optimize")
    except sqlite3.DatabaseError as exc:
        sys.stderr.write(
            f"roam: PRAGMA optimize skipped (non-fatal, stats not refreshed): {type(exc).__name__}: {exc}\n"
        )


@contextmanager
def open_db(
    readonly: bool = False,
    project_root: Path | None = None,
    *,
    warnings_out: WarningsOut = None,
) -> "Iterator[sqlite3.Connection]":
    """Context manager for database access. Creates schema if needed.

    Raises a descriptive ``click.ClickException`` if the database file is
    missing or corrupted so that agents receive actionable remediation steps
    instead of a raw SQLite traceback.

    W603 Pattern-2 disclosure: ``warnings_out`` (kw-only) is threaded
    through to ``get_connection`` (URI readonly fallback + query-timeout
    parse) and ``ensure_schema`` (FTS5 + user_version PRAGMA reads).
    The ``open_db`` shell itself has TWO error paths but neither is
    silent — both raise ``click.ClickException`` with remediation hints
    (already loud per W606-style discipline). The ``PRAGMA optimize``
    silent-skip at commit time is NOT plumbed: query-planner staleness
    is explicitly "not load-bearing; never refuse to close on this"
    (legacy comment), and surfaces only as gradual query-latency
    degradation — no actionable signal for an operator to take action.
    W978 intentional-absence.

    ``warnings_out=None`` (default) preserves the legacy silent-pass
    behaviour for every existing caller (~200+ commands import open_db).
    """
    db_path = get_db_path(project_root)
    conn = _open_validated_connection(db_path, readonly, warnings_out)
    try:
        if not readonly:
            _setup_schema_or_raise(conn, warnings_out)
        yield conn
        if not readonly:
            _commit_and_optimize(conn)
    finally:
        conn.close()
