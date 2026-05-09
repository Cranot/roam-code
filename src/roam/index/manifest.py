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
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

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
    except Exception:
        # Fall back to the package's __version__ string (which itself
        # falls back to "dev" when the distribution isn't installed).
        try:
            from roam import __version__

            return __version__
        except Exception:
            return "unknown"


def _pkg_version(pkg_name: str) -> str | None:
    """Return the installed version of *pkg_name*, or None if missing."""
    try:
        import importlib.metadata as _md

        return _md.version(pkg_name)
    except Exception:
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
        except Exception:
            continue
    return found


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
        except OSError:
            pass
    ignore_path = project_root / ".roamignore"
    if ignore_path.is_file():
        try:
            h.update(b"roamignore:")
            h.update(ignore_path.read_bytes())
            h.update(b"\n")
        except OSError:
            pass
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
        except sqlite3.DatabaseError:
            pass
    try:
        from roam.db.connection import USER_VERSION

        return int(USER_VERSION)
    except Exception:
        return 0


def collect_manifest(
    project_root: Path,
    *,
    profile: str = "all",
    conn: sqlite3.Connection | None = None,
    notes: str | None = None,
    extra_config_inputs: list[str] | None = None,
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
    }


def write_manifest(conn: sqlite3.Connection, manifest: dict) -> int:
    """Persist *manifest* as a new row in ``index_manifest``.

    Returns the inserted row id. JSON-encodes the dict/list fields so
    callers can pass plain Python structures.
    """
    parser_versions = manifest.get("parser_versions") or {}
    grammar_versions = manifest.get("grammar_versions")
    enabled_extras = manifest.get("enabled_extras") or []

    cursor = conn.execute(
        """
        INSERT INTO index_manifest (
            indexed_at, roam_version, schema_version,
            parser_versions, grammar_versions, config_hash,
            git_head, git_dirty_hash, enabled_extras,
            index_profile, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    inserted = cursor.lastrowid
    return int(inserted) if inserted is not None else 0


def latest_manifest(conn: sqlite3.Connection) -> dict | None:
    """Return the most recent manifest row, with JSON columns decoded.

    Returns None when the table is empty or doesn't exist.
    """
    try:
        row = conn.execute(
            """
            SELECT id, indexed_at, roam_version, schema_version,
                   parser_versions, grammar_versions, config_hash,
                   git_head, git_dirty_hash, enabled_extras,
                   index_profile, notes
              FROM index_manifest
          ORDER BY indexed_at DESC, id DESC
             LIMIT 1
            """
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None

    # Support both Row factories (sqlite3.Row vs tuple).
    def _at(idx, key):
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return row[idx]

    parser_versions_raw = _at(4, "parser_versions") or "{}"
    grammar_versions_raw = _at(5, "grammar_versions")
    enabled_extras_raw = _at(9, "enabled_extras") or "[]"

    try:
        parser_versions = json.loads(parser_versions_raw)
    except (TypeError, ValueError):
        parser_versions = {}
    try:
        grammar_versions = json.loads(grammar_versions_raw) if grammar_versions_raw else None
    except (TypeError, ValueError):
        grammar_versions = None
    try:
        enabled_extras = json.loads(enabled_extras_raw)
    except (TypeError, ValueError):
        enabled_extras = []

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
) -> int | None:
    """Convenience: collect + write a manifest in one call.

    Returns the inserted row id, or None if the table is missing (which
    should only happen on a stale schema — the indexer will have called
    ``ensure_schema`` already).
    """
    try:
        manifest = collect_manifest(
            project_root,
            profile=profile,
            conn=conn,
            notes=notes,
            extra_config_inputs=extra_config_inputs,
        )
        return write_manifest(conn, manifest)
    except sqlite3.DatabaseError:
        return None
    except Exception:
        # Manifest is best-effort — never let a bad probe crash an index run.
        if os.environ.get("ROAM_DEBUG"):
            raise
        return None
