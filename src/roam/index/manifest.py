"""Index manifest: per-run environment + project state snapshot.

A manifest row is written at the end of every successful index run. It
captures the roam-code version, sqlite schema version, parser / grammar
versions, a hash of the project config, the git HEAD (and dirty hash),
and which optional extras were importable at index time.

Consumers:
- ``roam doctor`` — flags parser/grammar drift, schema bumps, git-HEAD
  shifts since the last index.
- bundle import / export — surface a coarse compatibility check.
- future drift detection — anything that needs to know "was this index
  built with the same toolchain as the agent is running today?".

The schema is intentionally additive: new fields go into the JSON-encoded
columns (``parser_versions``, ``grammar_versions``, ``enabled_extras``)
so we don't need a SQLite migration every time we want to track a new
detail.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from roam.observability import log_swallowed

# Optional extras roam can take advantage of when present. Listed in
# preference order — `enabled_extras` in the manifest preserves this order.
_OPTIONAL_EXTRAS = (
    "networkx",
    "scipy",
    "onnxruntime",
    "watchdog",
    "fastmcp",
)


def _roam_version() -> str:
    """Resolve the roam-code package version via importlib.metadata."""
    try:
        import importlib.metadata as _md

        return _md.version("roam-code")
    except importlib.metadata.PackageNotFoundError:
        # Fall back to the package's __version__ string (which itself
        # falls back to "dev" when the distribution isn't installed).
        try:
            from roam import __version__

            return __version__
        except ImportError:
            return "unknown"


def _pkg_version(pkg_name: str) -> str | None:
    """Return the installed version of *pkg_name*, or None if missing."""
    try:
        import importlib.metadata as _md

        return _md.version(pkg_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _parser_versions() -> dict[str, str]:
    """Versions of the tree-sitter wheel + the language pack."""
    versions: dict[str, str] = {}
    ts = _pkg_version("tree-sitter")
    if ts:
        versions["tree_sitter"] = ts
    pack = _pkg_version("tree-sitter-language-pack")
    if pack:
        versions["tree_sitter_language_pack"] = pack
    return versions


def _grammar_versions() -> dict[str, str] | None:
    """Per-grammar versions, when the language pack exposes them.

    The language pack ships pre-compiled grammars and doesn't expose
    individual grammar versions in a stable way. Returning None lets the
    column stay NULL until we have a real per-grammar source.
    """
    return None


def _enabled_extras() -> list[str]:
    """Return the subset of optional extras that import cleanly right now."""
    import importlib

    found: list[str] = []
    for name in _OPTIONAL_EXTRAS:
        try:
            importlib.import_module(name)
            found.append(name)
        except Exception as _exc:  # noqa: BLE001 -- optional extra may raise any error on import; absence is the signal
            continue
    return found


def _component_versions() -> dict[str, dict[str, str]]:
    """Capture every component's ``VERSION`` at index time (Audit A6 / W81).

    Shape::

        {
            "bridges":    {bridge_name: version, ...},
            "detectors":  {task_id:     version, ...},
            "extractors": {language:    version, ...},
        }

    Drift detection: comparing this map between successive manifest rows
    surfaces a VERSION bump that invalidates rows stamped under the
    previous run. Each probe is wrapped so a broken plugin or import
    error can never block the manifest write — a partial map is more
    useful than a missing field.
    """
    out: dict[str, dict[str, str]] = {"bridges": {}, "detectors": {}, "extractors": {}}

    # Bridges — registry auto-discovers built-ins + plugin-contributed.
    try:
        from roam.bridges.base import LanguageBridge
        from roam.bridges.registry import _auto_discover, get_bridges

        _auto_discover()
        for bridge in get_bridges():
            try:
                name = bridge.name
                version = getattr(type(bridge), "VERSION", LanguageBridge.VERSION)
                out["bridges"][str(name)] = str(version)
            except (AttributeError, TypeError) as exc:
                # Per-bridge probe failure — one bridge's VERSION is absent;
                # the documented "partial map" behaviour keeps the rest. The
                # section-level catch below surfaces a wholesale loss.
                log_swallowed("index.manifest:component_versions:bridge_probe", exc)
                continue
        # Laravel post-resolver is bridge-shaped but module-level; pull
        # its VERSION alongside so the drift map covers every edge
        # source that stamps ``bridge``.
        try:
            from roam.index.laravel_post import VERSION as _laravel_version

            out["bridges"]["laravel"] = str(_laravel_version)
        except (ImportError, AttributeError) as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the
            # Laravel post-resolver VERSION dropped out of the drift map; a
            # bridge version bump would then go undetected. Surface the lineage.
            log_swallowed("index.manifest:component_versions:laravel", exc)
    except (ImportError, AttributeError, TypeError) as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the
        # bridges drift sub-map is missing due to registry absence/version skew.
        # Preserve manifest writes for known discovery-shape failures; let
        # unexpected bugs propagate so they are not hidden as an empty map.
        log_swallowed("index.manifest:component_versions:bridges", exc)

    # Detectors — function-based registry. The version map lives in
    # :mod:`roam.catalog.versions` (keyed by task_id), kept separate so
    # the manifest writer never has to touch detectors.py.
    try:
        from roam.catalog.detectors import _iter_registered_detectors
        from roam.catalog.versions import detector_version

        seen: set[str] = set()
        for task_id, _way_id, _fn in _iter_registered_detectors():
            if task_id in seen:
                continue
            seen.add(task_id)
            out["detectors"][str(task_id)] = detector_version(str(task_id))
    except (ImportError, AttributeError, TypeError) as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the entire
        # detectors drift sub-map is missing due to catalog import/version skew.
        # Preserve manifest writes for known section-shape failures; let
        # unexpected bugs propagate so they are not hidden as an empty map.
        log_swallowed("index.manifest:component_versions:detectors", exc)

    # Extractors — instantiate each supported language to pull its
    # class-level VERSION. Constructors are pure; the import cost is
    # the same one paid by the first index run, shifted to manifest time.
    try:
        from roam.languages.base import LanguageExtractor
        from roam.languages.registry import get_extractor, get_supported_languages

        for lang in get_supported_languages():
            try:
                ext = get_extractor(lang)
                version = getattr(type(ext), "VERSION", LanguageExtractor.VERSION)
                out["extractors"][str(lang)] = str(version)
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                # Per-language probe failure — one extractor's VERSION is
                # absent; the documented "partial map" behaviour keeps the
                # rest. The section-level catch below surfaces a wholesale loss.
                log_swallowed(f"index.manifest:component_versions:extractor_probe:{lang}", exc)
                continue
    except (ImportError, AttributeError, TypeError) as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the entire
        # extractors drift sub-map is missing (registry import failed). A
        # partial map is intentional, but the omission must be discoverable.
        log_swallowed("index.manifest:component_versions:extractors", exc)

    return out


def _config_hash(project_root: Path) -> str:
    """Hash the project's ``.roam/config.json`` + ``.roamignore`` content.

    Stable across runs as long as those files don't change. Used to detect
    when a config knob has shifted and the index might need a rebuild.
    """
    h = hashlib.sha256()
    config_path = project_root / ".roam" / "config.json"
    if config_path.is_file():
        try:
            h.update(b"config.json:")
            h.update(config_path.read_bytes())
            h.update(b"\n")
        except OSError as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" —
            # is_file() passed but the read failed, so the hash silently omits
            # real config content and drift detection may miss a config change.
            # Surface the lineage so a missed-rebuild has a discoverable cause.
            log_swallowed(f"index.manifest:config_hash:read_config:{config_path}", exc)
    ignore_path = project_root / ".roamignore"
    if ignore_path.is_file():
        try:
            h.update(b"roamignore:")
            h.update(ignore_path.read_bytes())
            h.update(b"\n")
        except OSError as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — see
            # the config.json branch above; an unreadable .roamignore silently
            # drops out of the drift hash.
            log_swallowed(f"index.manifest:config_hash:read_ignore:{ignore_path}", exc)
    return h.hexdigest()


def _git_head(project_root: Path) -> str | None:
    """Return ``git rev-parse HEAD`` for *project_root*, or None when n/a."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _git_dirty_hash(project_root: Path) -> str | None:
    """Hash of ``git status --porcelain`` output, or None if clean / non-git.

    Lets us tell "the working tree was clean at index time" vs "there were
    uncommitted edits" without storing the full diff.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout
    if not out.strip():
        return None  # clean
    return hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()


def _schema_version(conn: sqlite3.Connection | None) -> int:
    """Read PRAGMA user_version from *conn*, falling back to the module constant."""
    if conn is not None:
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            if row is not None:
                return int(row[0])
        except sqlite3.DatabaseError as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the
            # live PRAGMA read failed, so the manifest records the compiled-in
            # USER_VERSION constant instead of the DB's actual schema version.
            # A drift between the two would otherwise be invisible.
            log_swallowed("index.manifest:schema_version:pragma_read", exc)
    try:
        from roam.db.connection import USER_VERSION

        return int(USER_VERSION)
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the
        # USER_VERSION constant could not even be imported; 0 is a sentinel,
        # not a real schema version. Surface the lineage.
        log_swallowed("index.manifest:schema_version:constant_import", exc)
        return 0


def collect_manifest(
    project_root: Path,
    *,
    profile: str = "all",
    conn: sqlite3.Connection | None = None,
    notes: str | None = None,
    extra_config_inputs: list[str] | None = None,
    steps_status: dict | None = None,
) -> dict:
    """Build a fresh manifest dict from environment + project state.

    Args:
        project_root: Root of the project being indexed.
        profile: Which slice of the project was indexed — 'product',
            'tests', 'rules', 'docs', or 'all'.
        conn: Optional open DB connection for reading PRAGMA user_version.
            When None, the value is taken from the module-level constant.
        notes: Optional free-form note to persist alongside the row.
        extra_config_inputs: Strings to mix into the config hash. Use for
            CLI flags that change indexing behaviour ("--include-excluded",
            "--force") so a flag flip invalidates the manifest comparison.
        steps_status: Optional ``{step_name: {status, error_excerpt,
            duration_ms}}`` map produced by the indexer's step-tracking
            (W82/A8). Persisted into the ``steps_status`` JSON column so
            ``roam doctor`` can surface per-sub-step degraded-mode signals.

    Returns a dict with stringly-typed JSON-friendly values, ready to be
    handed to :func:`write_manifest`.
    """
    project_root = Path(project_root).resolve()

    config_h = _config_hash(project_root)
    if extra_config_inputs:
        h = hashlib.sha256()
        h.update(config_h.encode("ascii"))
        for item in extra_config_inputs:
            h.update(b"\n")
            h.update(str(item).encode("utf-8", "replace"))
        config_h = h.hexdigest()

    return {
        "indexed_at": int(time.time()),
        "roam_version": _roam_version(),
        "schema_version": _schema_version(conn),
        "parser_versions": _parser_versions(),
        "grammar_versions": _grammar_versions(),
        "config_hash": config_h,
        "git_head": _git_head(project_root),
        "git_dirty_hash": _git_dirty_hash(project_root),
        "enabled_extras": _enabled_extras(),
        "index_profile": profile,
        "notes": notes,
        "steps_status": steps_status,
        # W81 / A6 — per-component VERSION map for drift detection.
        "component_versions": _component_versions(),
    }


def write_manifest(conn: sqlite3.Connection, manifest: dict) -> int:
    """Persist *manifest* as a new row in ``index_manifest``.

    Returns the inserted row id. JSON-encodes the dict/list fields so
    callers can pass plain Python structures.

    ``steps_status`` (W82/A8) — when present in *manifest*, encoded into
    the dedicated column so ``roam doctor`` can surface per-sub-step
    failures without parsing the free-form ``notes`` blob.
    """
    parser_versions = manifest.get("parser_versions") or {}
    grammar_versions = manifest.get("grammar_versions")
    enabled_extras = manifest.get("enabled_extras") or []
    steps_status = manifest.get("steps_status")
    component_versions = manifest.get("component_versions")

    cursor = conn.execute(
        """
        INSERT INTO index_manifest (
            indexed_at, roam_version, schema_version,
            parser_versions, grammar_versions, config_hash,
            git_head, git_dirty_hash, enabled_extras,
            index_profile, notes, steps_status, component_versions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(manifest.get("indexed_at") or time.time()),
            str(manifest.get("roam_version") or "unknown"),
            int(manifest.get("schema_version") or 0),
            json.dumps(parser_versions, sort_keys=True),
            json.dumps(grammar_versions, sort_keys=True) if grammar_versions is not None else None,
            str(manifest.get("config_hash") or ""),
            manifest.get("git_head"),
            manifest.get("git_dirty_hash"),
            json.dumps(list(enabled_extras)),
            str(manifest.get("index_profile") or "all"),
            manifest.get("notes"),
            json.dumps(steps_status, sort_keys=True) if steps_status else None,
            json.dumps(component_versions, sort_keys=True) if component_versions else None,
        ),
    )
    inserted = cursor.lastrowid
    return int(inserted) if inserted is not None else 0


def _probe_optional_manifest_columns(
    conn: sqlite3.Connection,
) -> tuple[bool, bool] | None:
    """Detect which optional manifest columns the DB has.

    Returns ``(has_steps_status, has_component_versions)``, or ``None``
    when the table is missing / unreadable. ``steps_status`` landed in
    migration seq 52 and ``component_versions`` in seq 55, so older DBs
    may not have either.
    """
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(index_manifest)").fetchall()}
    except sqlite3.DatabaseError:
        return None
    return ("steps_status" in cols, "component_versions" in cols)


def _row_getter(row):
    """Build a positional/name-tolerant accessor over a manifest row.

    Resolves by column name when the row factory exposes one
    (``sqlite3.Row``) and falls back to positional index for plain
    tuples, so the index of each field stays stable regardless of which
    optional columns are present.
    """

    def _at(idx, key):
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return row[idx]

    return _at


def _decode_json(raw, default):
    """Decode a JSON manifest column, returning ``default`` on any failure."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _decode_json_dict(raw):
    """Decode a JSON manifest column that must be a dict, else ``None``."""
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def latest_manifest(conn: sqlite3.Connection) -> dict | None:
    """Return the most recent manifest row, with JSON columns decoded.

    Returns None when the table is empty or doesn't exist. The
    ``steps_status`` field is the W82/A8 per-sub-step completion map;
    rows written before that column existed come back with the field
    set to None (treated as "no per-step data recorded").
    """
    probed = _probe_optional_manifest_columns(conn)
    if probed is None:
        return None
    select_steps, select_components = probed

    # Build the column list dynamically so absent optional columns on
    # pre-migration DBs don't break the SELECT (they come back as None).
    extra_cols = []
    if select_steps:
        extra_cols.append("steps_status")
    if select_components:
        extra_cols.append("component_versions")
    extra_sql = ", " + ", ".join(extra_cols) if extra_cols else ""

    sql = f"""
        SELECT id, indexed_at, roam_version, schema_version,
               parser_versions, grammar_versions, config_hash,
               git_head, git_dirty_hash, enabled_extras,
               index_profile, notes{extra_sql}
          FROM index_manifest
      ORDER BY indexed_at DESC, id DESC
         LIMIT 1
    """
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None

    _at = _row_getter(row)

    parser_versions = _decode_json(_at(4, "parser_versions") or "{}", {})
    grammar_raw = _at(5, "grammar_versions")
    grammar_versions = _decode_json(grammar_raw, None) if grammar_raw else None
    enabled_extras = _decode_json(_at(9, "enabled_extras") or "[]", [])

    # Trailing optional columns are appended after the 12 fixed fields, in
    # the same order as ``extra_cols`` above. ``_at`` resolves them by name
    # on Row factories and falls back to the tracked positional index on
    # plain tuples (an absent earlier column shifts the later one's index).
    optional: dict[str, dict | None] = {}
    next_optional_idx = 12
    for col in extra_cols:
        optional[col] = _decode_json_dict(_at(next_optional_idx, col))
        next_optional_idx += 1

    return {
        "id": _at(0, "id"),
        "indexed_at": _at(1, "indexed_at"),
        "roam_version": _at(2, "roam_version"),
        "schema_version": _at(3, "schema_version"),
        "parser_versions": parser_versions,
        "grammar_versions": grammar_versions,
        "config_hash": _at(6, "config_hash"),
        "git_head": _at(7, "git_head"),
        "git_dirty_hash": _at(8, "git_dirty_hash"),
        "enabled_extras": enabled_extras,
        "index_profile": _at(10, "index_profile"),
        "notes": _at(11, "notes"),
        "steps_status": optional.get("steps_status"),
        "component_versions": optional.get("component_versions"),
    }


# Fields whose change implies the index needs (or might need) a rebuild.
# Used by stale-index detection in `roam doctor`. Order is presentation
# order in the diff dict.
_DRIFT_FIELDS = (
    "roam_version",
    "schema_version",
    "parser_versions",
    "grammar_versions",
    "config_hash",
    "git_head",
    "git_dirty_hash",
    "enabled_extras",
    "index_profile",
    # W81 / A6: per-component VERSION map. A bump here invalidates rows
    # stamped with the old version (``edges.bridge_version`` /
    # ``symbols.extractor_version``) — surfacing the delta lets the
    # doctor recommend a re-index even when nothing else has changed.
    "component_versions",
)


def manifest_diff(prev: dict, current: dict) -> dict:
    """Return ``{field: (old, new)}`` for fields that differ between manifests.

    Compares the drift-relevant fields only — ``id``, ``indexed_at`` and
    ``notes`` are deliberately excluded so a re-run on identical state
    produces an empty diff.
    """
    if not prev or not current:
        return {}
    diff: dict[str, tuple] = {}
    for field in _DRIFT_FIELDS:
        old = prev.get(field)
        new = current.get(field)
        if old != new:
            diff[field] = (old, new)
    return diff


# ---------------------------------------------------------------------------
# Internal hook for the indexer
# ---------------------------------------------------------------------------


def record_indexer_run(
    conn: sqlite3.Connection,
    project_root: Path,
    *,
    profile: str = "all",
    notes: str | None = None,
    extra_config_inputs: list[str] | None = None,
    steps_status: dict | None = None,
) -> int | None:
    """Convenience: collect + write a manifest in one call.

    Returns the inserted row id, or None if the table is missing (which
    should only happen on a stale schema — the indexer will have called
    ``ensure_schema`` already).

    *steps_status* (W82/A8): per-sub-step completion map produced by the
    indexer; persisted into the dedicated column.
    """
    try:
        manifest = collect_manifest(
            project_root,
            profile=profile,
            conn=conn,
            notes=notes,
            extra_config_inputs=extra_config_inputs,
            steps_status=steps_status,
        )
        return write_manifest(conn, manifest)
    except sqlite3.DatabaseError:
        return None
    except Exception as _exc:  # noqa: BLE001 -- manifest is best-effort; never let a bad probe crash an index run (ROAM_DEBUG re-raises)
        # Manifest is best-effort — never let a bad probe crash an index run.
        if os.environ.get("ROAM_DEBUG"):
            raise
        return None
