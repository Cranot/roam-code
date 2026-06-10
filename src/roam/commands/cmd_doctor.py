"""Setup diagnostics command — checks environment, dependencies, and index state.

Exit codes:
  0  All checks passed.
  1  One or more checks failed.

Output formats: text (default) and ``--json``. SARIF is deliberately NOT
emitted because doctor checks are environment-scoped (Python version,
dependency availability, cache age) rather than file-located code
findings. SARIF requires locations[]; doctor has no source coordinates
to populate. See W1085 / W1144 for the audit + design rationale.
"""

from __future__ import annotations

import json as _json
import re
import shutil
import sys
import time
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# W156 — doctor is the first detector migrating an "environment" namespace
# onto the central findings registry (after clones / dead / complexity).
# HYBRID model: only BLOCKING check failures persist; advisory failures
# are by-design ephemeral environment diagnostics (cache age, cloud
# sync, dev/install drift) that would pollute the registry without
# being actionable to anyone but the local developer. The detector
# version stamp lets a consumer spot rows produced under an older
# blocking-vs-advisory partition; bump it when the partition shifts.
DOCTOR_DETECTOR_VERSION: str = "1.0.0"

# Mapping check name -> env.<sub-kind> for the registry finding_id_str.
# Names that don't fit a specific kind default to ``env.blocking``.
_DOCTOR_CHECK_SUBKIND: dict[str, str] = {
    "Python version": "env.python_version",
    "tree-sitter": "env.missing_language_pack",
    "tree-sitter-language-pack": "env.missing_language_pack",
    "git executable": "env.missing_git",
    "networkx": "env.missing_dependency",
    "Cache permissions": "env.cache_permissions",
    "CLI command registry": "env.command_registry_drift",
    "Required tables": "env.missing_schema_table",
    "SQLite operational": "env.sqlite_integrity_failure",
    "CI workflow drift": "env.ci_workflow_drift",
}


def _doctor_finding_id(check_name: str, sub_kind: str) -> str:
    """Stable, deterministic finding id for one blocking doctor failure.

    The (check_name, sub_kind) pair is enough to re-identify the same
    environment finding across runs — check names are stable strings
    defined in this module. Re-running ``roam doctor --persist`` on
    the same input upserts the existing row rather than duplicating.
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("doctor", sub_kind, check_name, sub_kind)


def _emit_doctor_findings(conn, results: list[dict], source_version: str) -> None:
    """Mirror BLOCKING check failures into the findings registry.

    HYBRID filter (W156): advisory failures are filtered out — they're
    ephemeral environment diagnostics (cache age, cloud sync, dev /
    install drift) with no permanent codebase-level meaning. Only
    blocking failures are persisted; passed checks are also skipped
    (W145/W146 precedent — the registry documents problems, not
    successes).

    ``results`` is the list of check-result dicts produced by the
    doctor pipeline: ``{"name", "passed", "detail", ...}``. The dict
    shape is the contract — emit doesn't peek at private keys.

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard doctor command.
    """
    # Local import keeps the cost out of the no-persist path —
    # callers without ``--persist`` never reach here.
    from roam.db.findings import (
        CONFIDENCE_STATIC_ANALYSIS,
        FindingRecord,
        emit_finding,
    )

    for r in results:
        if r.get("passed"):
            continue
        name = r.get("name") or ""
        # HYBRID filter — advisory failures are NOT persisted.
        if name in _ADVISORY_CHECK_NAMES:
            continue
        sub_kind = _DOCTOR_CHECK_SUBKIND.get(name, "env.blocking")
        finding_id = _doctor_finding_id(name, sub_kind)
        detail = r.get("detail") or ""
        evidence = {
            "check_name": name,
            "sub_kind": sub_kind,
            "detail": detail,
            "passed": False,
        }
        claim = f"Doctor blocking-failure: {name} — {detail}"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="environment",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=CONFIDENCE_STATIC_ANALYSIS,
                source_detector="doctor",
                source_version=source_version,
            ),
        )


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def _check_python_version() -> dict:
    """Python >= 3.10 required."""
    vi = sys.version_info
    version_str = f"{vi.major}.{vi.minor}.{vi.micro}"
    passed = (vi.major, vi.minor) >= (3, 10)
    return {
        "name": "Python version",
        "passed": passed,
        "detail": f"Python {version_str} (>= 3.10 required)",
    }


def _pkg_version(pkg_name: str, module_name: str | None = None) -> str:
    """Resolve an installed package version.

    Tries ``importlib.metadata.version(pkg_name)`` first (works for any
    pip-installed distribution); falls back to ``module.__version__`` if
    the dunder is set (some older C-extension packages); else ``unknown``.

    Several roam dependencies (tree-sitter, tree-sitter-language-pack)
    don't expose ``__version__`` so the previous direct attribute read
    yielded "unknown" even when the wheel was correctly installed —
    obscuring the real version in ``roam doctor`` diagnostics.
    """
    try:
        import importlib.metadata as _md

        return _md.version(pkg_name)
    except _md.PackageNotFoundError:
        pass
    if module_name:
        try:
            import importlib

            mod = importlib.import_module(module_name)
            return getattr(mod, "__version__", "unknown")
        except Exception:
            return "unknown"
    return "unknown"


def _check_tree_sitter() -> dict:
    """tree-sitter package importable."""
    try:
        import tree_sitter  # noqa: F401

        version = _pkg_version("tree-sitter", "tree_sitter")
        return {
            "name": "tree-sitter",
            "passed": True,
            "detail": f"tree-sitter {version}",
        }
    except ImportError as exc:
        return {
            "name": "tree-sitter",
            "passed": False,
            "detail": f"not installed: {exc}",
        }


def _check_tree_sitter_language_pack() -> dict:
    """tree-sitter-language-pack importable."""
    try:
        import tree_sitter_language_pack  # noqa: F401

        version = _pkg_version("tree-sitter-language-pack", "tree_sitter_language_pack")
        return {
            "name": "tree-sitter-language-pack",
            "passed": True,
            "detail": f"tree-sitter-language-pack {version}",
        }
    except ImportError as exc:
        return {
            "name": "tree-sitter-language-pack",
            "passed": False,
            "detail": f"not installed: {exc}",
        }


def _check_git() -> dict:
    """git executable available on PATH."""
    git_path = shutil.which("git")
    if git_path is None:
        return {
            "name": "git executable",
            "passed": False,
            "detail": "git not found on PATH",
        }
    # Get version string
    try:
        import subprocess

        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        version_line = result.stdout.strip() if result.returncode == 0 else ""
        # "git version 2.43.0" -> "2.43.0"
        version = version_line.replace("git version", "").strip() or "unknown"
    except Exception:
        version = "unknown"
    return {
        "name": "git executable",
        "passed": True,
        "detail": f"git {version}",
    }


def _check_networkx() -> dict:
    """networkx importable."""
    try:
        import networkx

        version = getattr(networkx, "__version__", "unknown")
        return {
            "name": "networkx",
            "passed": True,
            "detail": f"networkx {version}",
        }
    except ImportError as exc:
        return {
            "name": "networkx",
            "passed": False,
            "detail": f"not installed: {exc}",
        }


def _check_index_exists() -> dict:
    """Index DB file exists at the expected path."""
    try:
        from roam.db.connection import get_db_path

        db_path = get_db_path()
    except Exception as exc:
        return {
            "name": "Index exists",
            "passed": False,
            "detail": f"could not determine DB path: {exc}",
        }

    exists = db_path.exists()
    return {
        "name": "Index exists",
        "passed": exists,
        "detail": str(db_path) if exists else f"not found: {db_path} (run `roam init`)",
        "_db_path": str(db_path) if exists else None,
    }


def _check_index_freshness(db_path_str: str | None) -> dict:
    """Index mtime <= 24 hours old."""
    if db_path_str is None:
        return {
            "name": "Index freshness",
            "passed": False,
            "detail": "index does not exist (run `roam init`)",
        }
    db_path = Path(db_path_str)
    if not db_path.exists():
        return {
            "name": "Index freshness",
            "passed": False,
            "detail": "index does not exist (run `roam init`)",
        }

    age_s = time.time() - db_path.stat().st_mtime
    age_h = age_s / 3600.0
    stale = age_h > 24.0

    if age_s < 60:
        age_str = f"{int(age_s)} second{'s' if int(age_s) != 1 else ''} ago"
    elif age_s < 3600:
        age_m = int(age_s / 60)
        age_str = f"{age_m} minute{'s' if age_m != 1 else ''} ago"
    elif age_s < 86400:
        age_h_int = int(age_h)
        age_str = f"{age_h_int} hour{'s' if age_h_int != 1 else ''} ago"
    else:
        age_d = int(age_h / 24)
        age_str = f"{age_d} day{'s' if age_d != 1 else ''} ago"

    return {
        "name": "Index freshness",
        "passed": not stale,
        "detail": (f"fresh ({age_str})" if not stale else f"stale ({age_str}, run `roam index` to refresh)"),
        "_age_s": round(age_s, 1),
    }


def _check_sqlite(db_path_str: str | None) -> dict:
    """SQLite can open and query the index DB."""
    if db_path_str is None:
        return {
            "name": "SQLite operational",
            "passed": False,
            "detail": "index does not exist (run `roam init`)",
        }
    db_path = Path(db_path_str)
    if not db_path.exists():
        return {
            "name": "SQLite operational",
            "passed": False,
            "detail": "index does not exist (run `roam init`)",
        }

    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path), timeout=5)
        # PRAGMA integrity_check verifies the file is a real, non-corrupted DB
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        if rows and rows[0][0] == "ok":
            return {
                "name": "SQLite operational",
                "passed": True,
                "detail": "SQLite operational",
            }
        return {
            "name": "SQLite operational",
            "passed": False,
            "detail": "SQLite integrity check failed",
        }
    except Exception as exc:
        return {
            "name": "SQLite operational",
            "passed": False,
            "detail": f"SQLite error: {exc}",
        }


def _check_command_registry() -> dict:
    """Every CLI subcommand declared in roam.cli._COMMANDS must import.

    Catches the round-4 #16 class of bug — a documented command silently
    removed or renamed leaves the registry mismatch undetected until an
    agent calls it. Doctor runs the lazy-import for every entry up front.
    """
    # break the static cycle (cli ↔ cmd_doctor) by loading
    # ``roam.cli`` via importlib at runtime. This is the only static
    # edge that connected back to cli; the doctor's safety check still
    # verifies every registered command imports.
    try:
        import importlib

        cli_mod = importlib.import_module("roam.cli")
        _COMMANDS = cli_mod._COMMANDS
    except Exception as exc:
        return {
            "name": "CLI command registry",
            "passed": False,
            "detail": f"could not load roam.cli: {exc}",
        }

    failures: list[str] = []
    for cmd_name, target in _COMMANDS.items():
        try:
            module_name, attr = target
            mod = __import__(module_name, fromlist=[attr])
            if not hasattr(mod, attr):
                failures.append(f"{cmd_name}->{module_name}:{attr} (missing attr)")
        except Exception as exc:
            failures.append(f"{cmd_name}: {type(exc).__name__}: {exc}")
    if failures:
        sample = "; ".join(failures[:3])
        more = f" (+{len(failures) - 3} more)" if len(failures) > 3 else ""
        return {
            "name": "CLI command registry",
            "passed": False,
            "detail": f"{len(failures)} command(s) fail to import: {sample}{more}",
        }
    # W420: headline count must be plugin-invariant (AST-sourced) even
    # though the integrity-check loop above iterates the runtime
    # ``_COMMANDS`` dict (which is the right surface for import-test
    # integrity, including plugin-registered commands). Promoting the
    # runtime count into the user-visible detail string drifts the
    # same way ``cmd_surface.command_count`` did before W420.
    from roam.surface_counts import cli_commands as _cli_commands_ast  # noqa: PLC0415

    return {
        "name": "CLI command registry",
        "passed": True,
        "detail": f"{len(_cli_commands_ast())} CLI commands import cleanly",
    }


def _check_mcp_registry() -> dict:
    """MCP server registers tools without crashing.

    Round 4 #15: a tool listed in the declared tool set but not actually
    implemented gives agents `No such tool` errors. We import the server
    module and count registered tools so a registry mismatch surfaces
    here, not at agent call-time.
    """
    try:
        from roam import mcp_server  # noqa: PLC0415 — heavy import, only at doctor time
    except Exception as exc:
        return {
            "name": "MCP tool registry",
            "passed": False,
            "detail": f"mcp_server import failed: {type(exc).__name__}: {exc}",
        }

    declared = getattr(mcp_server, "_REGISTERED_TOOLS", [])
    if not declared:
        return {
            "name": "MCP tool registry",
            "passed": False,
            "detail": "no tools registered (FastMCP missing or registration broken)",
        }
    # Preset awareness — a fresh import only registers the tools allowed
    # by the active preset (default "core"). Without naming the preset
    # in the doctor output a user sees "36 MCP tools registered" and
    # wonders why the docs claim 122. v12.12.5: report the active
    # preset and the full-preset ceiling so the count makes sense.
    import os as _os
    import re as _re

    preset = _os.environ.get("ROAM_MCP_PRESET", "core")
    full_count = 0
    try:
        from roam.surface_counts import mcp_surface_counts

        full_count = int(mcp_surface_counts().get("registered_tools") or 0)
    except Exception:
        # Fallback: count @_tool decorators in the module source. The
        # surface_counts helper isn't importable in some minimal envs.
        try:
            module_path = Path(getattr(mcp_server, "__file__", ""))
            text = module_path.read_text(encoding="utf-8") if module_path.is_file() else ""
            full_count = len(_re.findall(r"^@_tool\(\s*name=", text, _re.MULTILINE))
        except Exception:
            full_count = 0
    # W420 dual-source: report runtime-active AND AST-shipped counts
    # side-by-side so the operator can see plugin-loading / preset state
    # at a glance. Single-source would hide one axis of drift.
    if full_count:
        detail = f"{len(declared)} active / {full_count} shipped MCP tools registered ({preset} preset)"
    else:
        detail = f"{len(declared)} active MCP tools registered ({preset} preset)"
    return {
        "name": "MCP tool registry",
        "passed": True,
        "detail": detail,
    }


def _check_plugin_discovery() -> dict:
    """Plugin discovery completed without errors.

    A user-installed plugin that fails to import would silently disappear
    from the surface; this check surfaces ``get_plugin_errors`` so the
    failure is loud during ``roam doctor``.
    """
    try:
        from roam.plugins import discover_plugins, get_plugin_errors
    except Exception as exc:
        return {
            "name": "Plugin discovery",
            "passed": False,
            "detail": f"plugins module import failed: {type(exc).__name__}: {exc}",
        }
    try:
        discover_plugins()
        errors = get_plugin_errors()
    except Exception as exc:
        return {
            "name": "Plugin discovery",
            "passed": False,
            "detail": f"discovery raised: {type(exc).__name__}: {exc}",
        }
    if errors:
        first = errors[0]
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return {
            "name": "Plugin discovery",
            "passed": False,
            "detail": f"{first}{more}",
        }
    return {
        "name": "Plugin discovery",
        "passed": True,
        "detail": "no errors during plugin discovery",
    }


def _check_required_tables() -> dict:
    """Required tables are present in the index.

    A failed mid-migration would leave the DB without expected tables.
    Surfacing it here is faster than discovering it via a downstream
    "no such table" sqlite error mid-command.
    """
    try:
        from roam.db.connection import db_exists, open_db
    except Exception as exc:
        return {
            "name": "Required tables",
            "passed": False,
            "detail": f"connection module import failed: {type(exc).__name__}: {exc}",
        }
    if not db_exists():
        return {
            "name": "Required tables",
            "passed": True,
            "detail": "no index — table check skipped",
        }
    required = {"files", "symbols", "edges", "git_commits", "file_stats"}
    try:
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            present = {r[0] for r in rows}
    except Exception as exc:
        return {
            "name": "Required tables",
            "passed": False,
            "detail": f"could not list tables: {type(exc).__name__}: {exc}",
        }
    missing = required - present
    if missing:
        return {
            "name": "Required tables",
            "passed": False,
            "detail": f"missing tables: {', '.join(sorted(missing))}; run `roam reset` to rebuild.",
        }
    return {
        "name": "Required tables",
        "passed": True,
        "detail": f"all {len(required)} required tables present",
    }


def _check_corpus_content() -> dict:
    """Verify the indexer extracted symbols from the corpus.

    W836 fix — an empty graph means either the corpus is genuinely empty
    OR the indexer is broken. Either way, doctor should disclose this
    instead of silently passing. Without this check the env-only pipeline
    emits "all N checks passed" against a clean env + empty corpus,
    masking the most actionable symptom (W835/W836 finding).

    States:
      no_index    - DB doesn't exist (skipped, advisory pass)
      empty       - DB exists but 0 symbols indexed (advisory fail)
      populated   - at least one symbol present (pass)
      error       - could not query symbols table
    """
    try:
        from roam.db.connection import db_exists, open_db
    except (ImportError, AttributeError) as exc:
        return {
            "name": "Corpus content",
            "passed": False,
            "detail": f"connection module import failed: {type(exc).__name__}: {exc}",
        }

    if not db_exists():
        return {
            "name": "Corpus content",
            "passed": True,
            "detail": "no index - corpus check skipped (run `roam init`)",
            "_state": "no_index",
        }

    import sqlite3 as _sqlite3

    try:
        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    except _sqlite3.Error as exc:
        return {
            "name": "Corpus content",
            "passed": False,
            "detail": f"could not query symbols: {exc}",
            "_state": "error",
        }

    count = int(count or 0)
    if count == 0:
        # Pattern 2: always-emit. Empty corpus is a real signal — disclose
        # it, do not silently pass. Advisory failure keeps this from
        # blocking CI for legitimately empty repos.
        return {
            "name": "Corpus content",
            "passed": False,
            "detail": "corpus empty (0 symbols indexed) - run `roam index --force` if unexpected",
            "_state": "empty",
            "_symbol_count": 0,
        }
    return {
        "name": "Corpus content",
        "passed": True,
        "detail": f"{count} symbols indexed",
        "_state": "populated",
        "_symbol_count": count,
    }


def _check_stale_math_signal_column() -> dict:
    """Detect a stale ``math_signals.loop_eq_with_dependent_write`` column.

    Migration #51 (USER_VERSION 12 -> 13, W36.4) added the
    ``loop_eq_with_dependent_write`` column to ``math_signals`` with
    ``DEFAULT 0``. Repos indexed before that migration landed have the
    column populated with zeros across every row — meaning ``roam algo``
    correctly reports zero false-positives but ALSO zero true-positives
    for the new dependent-write predicate.

    The fix is ``roam index --force`` to re-run the signal extractor.

    Advisory only: a stale-but-present column is informational, never
    blocking — the user is still getting correct (just incomplete)
    results.

    States:
      no_index   — DB doesn't exist (skipped)
      no_table   — math_signals table missing (older schema; skipped)
      no_column  — column not present (pre-USER_VERSION-13 DB; skipped —
                   migration will add it on next open)
      empty      — math_signals table is empty (fresh project; skipped)
      stale      — column exists, table non-empty, but all rows are 0
      populated  — at least one row has a non-zero value (healthy)
    """
    try:
        from roam.db.connection import db_exists, open_db
    except Exception as exc:
        return {
            "name": "Stale math_signals column",
            "passed": False,
            "detail": f"connection module import failed: {type(exc).__name__}: {exc}",
        }

    if not db_exists():
        return {
            "name": "Stale math_signals column",
            "passed": True,
            "detail": "no index - stale-signal check skipped",
            "_state": "no_index",
        }

    try:
        with open_db(readonly=True) as conn:
            # Does math_signals exist?
            row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='math_signals'").fetchone()
            if row is None:
                return {
                    "name": "Stale math_signals column",
                    "passed": True,
                    "detail": "math_signals table absent - older schema, skipped",
                    "_state": "no_table",
                }
            # Does the column exist? PRAGMA table_info returns (cid, name, ...)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(math_signals)").fetchall()}
            if "loop_eq_with_dependent_write" not in cols:
                return {
                    "name": "Stale math_signals column",
                    "passed": True,
                    "detail": (
                        "loop_eq_with_dependent_write column not present - "
                        "pre-migration-#51 DB, will populate on next open"
                    ),
                    "_state": "no_column",
                }
            # Is the table populated at all? Use COUNT + SUM in one query.
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(loop_eq_with_dependent_write), 0) FROM math_signals"
            ).fetchone()
    except Exception as exc:
        return {
            "name": "Stale math_signals column",
            "passed": False,
            "detail": f"could not probe math_signals: {type(exc).__name__}: {exc}",
        }

    total_rows = int(row[0] or 0)
    nonzero_sum = int(row[1] or 0)

    if total_rows == 0:
        return {
            "name": "Stale math_signals column",
            "passed": True,
            "detail": "math_signals empty - fresh project, skipped",
            "_state": "empty",
            "_row_count": 0,
        }

    if nonzero_sum == 0:
        return {
            "name": "Stale math_signals column",
            "passed": False,
            "detail": (
                f"ADVISORY: stale math_signals column 'loop_eq_with_dependent_write' - "
                f"schema v13 added this signal (W36.4 / migration #51) but no row "
                f"has a non-zero value across {total_rows} rows. This is normal for "
                f"repos indexed before migration #51 landed. "
                f"Fix: run `roam index --force` to rebuild signals."
            ),
            "_state": "stale",
            "_row_count": total_rows,
        }

    return {
        "name": "Stale math_signals column",
        "passed": True,
        "detail": (
            f"loop_eq_with_dependent_write populated ({nonzero_sum} non-zero value(s) across {total_rows} rows)"
        ),
        "_state": "populated",
        "_row_count": total_rows,
    }


def _format_age(seconds: float) -> str:
    """Format an age in seconds as a human-readable string."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        n = int(seconds)
        return f"{n} second{'s' if n != 1 else ''}"
    if seconds < 3600:
        n = int(seconds / 60)
        return f"{n} minute{'s' if n != 1 else ''}"
    if seconds < 86400:
        n = int(seconds / 3600)
        return f"{n} hour{'s' if n != 1 else ''}"
    n = int(seconds / 86400)
    return f"{n} day{'s' if n != 1 else ''}"


def _check_index_manifest() -> dict:
    """Surface manifest age + drift hints from the most recent index run.

    Drift between the recorded parser / grammar / roam version and what's
    currently installed means the index was built with a different
    toolchain — agents should ``roam index --force`` before trusting it.
    """
    try:
        from roam.db.connection import db_exists, find_project_root, open_db
        from roam.index.manifest import collect_manifest, latest_manifest, manifest_diff
    except Exception as exc:
        return {
            "name": "Index manifest",
            "passed": False,
            "detail": f"manifest module import failed: {type(exc).__name__}: {exc}",
        }

    if not db_exists():
        return {
            "name": "Index manifest",
            "passed": True,
            "detail": "no index — manifest check skipped",
        }

    try:
        with open_db(readonly=True) as conn:
            prev = latest_manifest(conn)
    except Exception as exc:
        return {
            "name": "Index manifest",
            "passed": False,
            "detail": f"could not read manifest: {type(exc).__name__}: {exc}",
        }

    if prev is None:
        return {
            "name": "Index manifest",
            "passed": False,
            "detail": "no manifest recorded — run `roam index --force` to refresh",
        }

    # Build a "current state" manifest and diff it. conn=None makes the
    # schema_version come from the running code's constant, which is the
    # right reference when checking for drift.
    project_root = find_project_root()
    current = collect_manifest(project_root, profile=prev.get("index_profile") or "all", conn=None)

    drift = manifest_diff(prev, current)

    age_s = max(0, int(time.time()) - int(prev.get("indexed_at") or 0))
    base = (
        f"index built {_format_age(age_s)} ago with roam-code "
        f"{prev.get('roam_version')}, schema v{prev.get('schema_version')}"
    )

    hints: list[str] = []
    parser_drift = "parser_versions" in drift or "grammar_versions" in drift
    if parser_drift or "schema_version" in drift or "roam_version" in drift:
        hints.append("WARN: parser version drift since last index — run `roam index --force`")

    if "git_head" in drift:
        old_head = drift["git_head"][0]
        new_head = drift["git_head"][1]
        if old_head and new_head:
            hints.append(f"INFO: index was built at commit {old_head[:7]}; current HEAD is {new_head[:7]}")
        elif new_head and not old_head:
            hints.append(f"INFO: current HEAD is {new_head[:7]}; index has no recorded commit")
        elif old_head and not new_head:
            hints.append(f"INFO: index was built at commit {old_head[:7]}; no git HEAD detectable now")

    if "git_dirty_hash" in drift:
        old_dirty, new_dirty = drift["git_dirty_hash"]
        if old_dirty is None and new_dirty is not None:
            hints.append("INFO: index was built on a clean tree; working tree now has uncommitted changes")
        elif old_dirty is not None and new_dirty is None:
            hints.append("INFO: index was built on a dirty tree; working tree is clean now")
        else:
            hints.append("INFO: working-tree dirty-hash differs from index time — uncommitted state has changed")

    if "config_hash" in drift:
        hints.append("WARN: roam config or .roamignore changed since last index — run `roam index --force`")

    if hints:
        detail = base + "; " + "; ".join(hints)
        passed = not (parser_drift or "schema_version" in drift or "roam_version" in drift or "config_hash" in drift)
    else:
        detail = base
        passed = True

    return {
        "name": "Index manifest",
        "passed": passed,
        "detail": detail,
        "_drift_fields": sorted(drift.keys()),
    }


def _check_index_manifest_history() -> dict:
    """Compare the two most recent manifest rows for structural drift.

    Complements ``_check_index_manifest`` (which diffs the latest row
    against the current environment). This check answers a different
    question: "did the last two recorded index runs produce a different
    toolchain / config / git state?" — i.e. is the index history itself
    moving around between runs?

    States:
      no_history     — 0 or 1 manifest rows recorded (advisory pass)
      stable         — 2+ rows, no fields differ between the latest two
      drift_detected — 2+ rows, one or more drift fields differ

    The drift fields are the ones manifest.manifest_diff already
    considers significant (roam_version, schema_version, parser/grammar
    versions, config_hash, git_head, git_dirty_hash, enabled_extras,
    index_profile). We surface the field names and counts; the per-field
    old/new values stay in the `_drift` private payload so JSON
    consumers that want them can opt in.
    """
    import json  # local — keeps doctor's import surface lazy

    try:
        from roam.db.connection import db_exists, open_db
        from roam.index.manifest import _DRIFT_FIELDS, manifest_diff
    except Exception as exc:
        return {
            "name": "Index manifest history",
            "passed": False,
            "detail": f"manifest module import failed: {type(exc).__name__}: {exc}",
        }

    if not db_exists():
        return {
            "name": "Index manifest history",
            "passed": True,
            "detail": "no index — manifest history skipped",
            "_state": "no_history",
        }

    # Pull the two most recent manifest rows (latest first). Stay inside a
    # single read-only connection so the check is cheap even on large DBs.
    def _decode_row(row) -> dict:
        # Decode the same fields latest_manifest() decodes, so the diff
        # function sees the same shape on both sides.
        parser_versions_raw = row[3] or "{}"
        grammar_versions_raw = row[4]
        enabled_extras_raw = row[8] or "[]"
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
            "indexed_at": row[0],
            "roam_version": row[1],
            "schema_version": row[2],
            "parser_versions": parser_versions,
            "grammar_versions": grammar_versions,
            "config_hash": row[5],
            "git_head": row[6],
            "git_dirty_hash": row[7],
            "enabled_extras": enabled_extras,
            "index_profile": row[9],
        }

    try:
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                """
                SELECT indexed_at, roam_version, schema_version,
                       parser_versions, grammar_versions, config_hash,
                       git_head, git_dirty_hash, enabled_extras,
                       index_profile
                  FROM index_manifest
              ORDER BY indexed_at DESC, id DESC
                 LIMIT 2
                """
            ).fetchall()
    except Exception as exc:
        return {
            "name": "Index manifest history",
            "passed": False,
            "detail": f"could not read manifest history: {type(exc).__name__}: {exc}",
        }

    row_count = len(rows)
    if row_count < 2:
        return {
            "name": "Index manifest history",
            "passed": True,
            "detail": (
                "manifest seeded — no prior run to diff against" if row_count == 1 else "no manifest rows recorded yet"
            ),
            "_state": "no_history",
            "_row_count": row_count,
        }

    current = _decode_row(rows[0])
    previous = _decode_row(rows[1])
    drift = manifest_diff(previous, current)

    if not drift:
        return {
            "name": "Index manifest history",
            "passed": True,
            "detail": "last two index runs are structurally identical",
            "_state": "stable",
            "_row_count": row_count,
        }

    # Drift detected — surface field names + a short summary. Old/new
    # values stay in the private payload so the human-facing detail stays
    # readable.
    fields_sorted = sorted(drift.keys())
    # Preserve docstring order for the first few fields when possible
    # (roam_version, schema_version, parser_versions, ...) so the most
    # important drift surfaces first.
    ordered = [f for f in _DRIFT_FIELDS if f in drift]
    extra = [f for f in fields_sorted if f not in ordered]
    headline_fields = ordered + extra
    detail = f"{len(drift)} field(s) drifted between the last two index runs: {', '.join(headline_fields)}"
    return {
        "name": "Index manifest history",
        "passed": False,
        "detail": detail,
        "_state": "drift_detected",
        "_row_count": row_count,
        "_drift_fields": fields_sorted,
        "_drift": {k: list(v) for k, v in drift.items()},
    }


def _check_index_step_failures() -> dict:
    """Surface per-sub-step failures recorded in the latest manifest.

    ROADMAP A8 / W82: ``Indexer`` runs ~12 best-effort sub-steps that
    each guard themselves with try/except (clustering, taint analysis,
    git stats, etc.). Without a per-step record, the index can ship
    "complete" while a key step silently failed — agents then consume
    stale data thinking it's fresh.

    The indexer now writes a JSON map into ``index_manifest.steps_status``
    on every run. This check reads that map and emits an advisory
    failure when ANY step has a ``failed:*`` status, naming the failing
    steps so the user knows what to retry.

    States:
      no_index      — DB not present (advisory pass)
      no_data       — manifest exists but no per-step record (legacy row
                      or import-only check); advisory pass
      all_ok        — every recorded step ran cleanly
      failures      — at least one step is ``failed:*`` — advisory FAIL
                      with the failing-step names + a retry hint
    """
    try:
        from roam.db.connection import db_exists, open_db
        from roam.index.manifest import latest_manifest
    except Exception as exc:
        return {
            "name": "Index step manifest",
            "passed": False,
            "detail": f"manifest module import failed: {type(exc).__name__}: {exc}",
        }

    if not db_exists():
        return {
            "name": "Index step manifest",
            "passed": True,
            "detail": "no index — step manifest check skipped",
            "_state": "no_index",
        }

    try:
        with open_db(readonly=True) as conn:
            prev = latest_manifest(conn)
    except Exception as exc:
        return {
            "name": "Index step manifest",
            "passed": False,
            "detail": f"could not read manifest: {type(exc).__name__}: {exc}",
        }

    if prev is None:
        return {
            "name": "Index step manifest",
            "passed": True,
            "detail": "no manifest recorded — run `roam index` to capture per-step status",
            "_state": "no_data",
        }

    steps_status = prev.get("steps_status") or {}
    if not steps_status:
        return {
            "name": "Index step manifest",
            "passed": True,
            "detail": "no per-step data recorded (legacy manifest row)",
            "_state": "no_data",
        }

    failures: list[tuple[str, str, str]] = []  # (step, error_class, excerpt)
    skipped: list[str] = []
    ok_count = 0
    for step, entry in steps_status.items():
        if not isinstance(entry, dict):
            # Defensive: tolerate the older string-only shape if a
            # mixed-vintage row sneaks in.
            status_str = str(entry)
            if status_str.startswith("failed:"):
                failures.append((step, status_str.split(":", 1)[1], ""))
            elif status_str.startswith("skipped:"):
                skipped.append(step)
            else:
                ok_count += 1
            continue
        status = str(entry.get("status") or "")
        if status.startswith("failed:"):
            err_class = status.split(":", 1)[1]
            excerpt = str(entry.get("error_excerpt") or "")
            failures.append((step, err_class, excerpt))
        elif status.startswith("skipped:"):
            skipped.append(step)
        else:
            ok_count += 1

    total = len(steps_status)
    if not failures:
        suffix = f" ({len(skipped)} skipped)" if skipped else ""
        return {
            "name": "Index step manifest",
            "passed": True,
            "detail": f"all {ok_count}/{total} recorded sub-steps succeeded{suffix}",
            "_state": "all_ok",
            "_steps_total": total,
            "_steps_ok": ok_count,
            "_steps_skipped": len(skipped),
        }

    # Surface up to the first 3 failing steps in the human-readable
    # detail so we name what failed without a wall of text.
    head = failures[:3]
    head_text = "; ".join(
        f"{step}: {err_class}" + (f" ({excerpt[:80]})" if excerpt else "") for step, err_class, excerpt in head
    )
    remainder = f"; +{len(failures) - len(head)} more" if len(failures) > len(head) else ""
    fail_names = ", ".join(step for step, _, _ in failures)
    detail = (
        f"{len(failures)}/{total} sub-step(s) failed in the last index run "
        f"({fail_names}). Your index is missing data because these steps "
        f"failed: {head_text}{remainder}. Run `roam index --force` to retry."
    )
    return {
        "name": "Index step manifest",
        "passed": False,
        "detail": detail,
        "_state": "failures",
        "_steps_total": total,
        "_steps_failed": len(failures),
        "_steps_skipped": len(skipped),
        "_failed_steps": [{"step": s, "error_class": ec, "error_excerpt": ex} for s, ec, ex in failures],
    }


def _check_phase_timings() -> dict:
    """Surface per-phase wall-clock from the latest index run (W408).

    The indexer (W408) records the 7 user-facing phases' wallclock into
    ``index_manifest.notes`` under the ``phase_timings`` key. This check
    reads them back so ``roam doctor --json`` exposes the data without
    needing a separate ``roam indexer-perf`` command. The W395-followup
    sprint can rank optimization candidates against the real numbers
    rather than the predicted 30-50% speedup that turned out to be ~5%.

    Always-advisory: a slow phase isn't broken — it's diagnostic. The
    check never blocks, but the ``_phase_timings`` private payload + the
    structured envelope let downstream tooling lint specific phases.

    States:
      no_index       — DB doesn't exist (advisory pass)
      no_data        — manifest exists but ``notes`` has no ``phase_timings``
                       (legacy row from before this wave) — advisory pass
      ok             — timings present; surface seconds per phase + total
    """
    import json as _json

    try:
        from roam.db.connection import db_exists, open_db
        from roam.index.manifest import latest_manifest
    except Exception as exc:
        return {
            "name": "Phase timings",
            "passed": True,
            "detail": f"manifest module import failed: {type(exc).__name__}: {exc}",
            "_state": "no_data",
        }

    if not db_exists():
        return {
            "name": "Phase timings",
            "passed": True,
            "detail": "no index — phase-timing check skipped",
            "_state": "no_index",
        }

    try:
        with open_db(readonly=True) as conn:
            prev = latest_manifest(conn)
    except Exception as exc:
        return {
            "name": "Phase timings",
            "passed": True,
            "detail": f"could not read manifest: {type(exc).__name__}: {exc}",
            "_state": "no_data",
        }

    if prev is None:
        return {
            "name": "Phase timings",
            "passed": True,
            "detail": "no manifest recorded — run `roam index` to capture phase timings",
            "_state": "no_data",
        }

    notes_raw = prev.get("notes") or ""
    if not notes_raw:
        return {
            "name": "Phase timings",
            "passed": True,
            "detail": "no phase-timing data recorded (legacy manifest row)",
            "_state": "no_data",
        }

    try:
        notes_obj = _json.loads(notes_raw)
        if not isinstance(notes_obj, dict):
            notes_obj = {}
    except (TypeError, ValueError):
        notes_obj = {}

    phase_timings = notes_obj.get("phase_timings") or {}
    if not isinstance(phase_timings, dict) or not phase_timings:
        return {
            "name": "Phase timings",
            "passed": True,
            "detail": "no phase-timing data recorded (legacy manifest row)",
            "_state": "no_data",
        }

    # Normalise to (phase, seconds) tuples in the canonical order. Any
    # extra keys the indexer added in a future revision come after the
    # known set, preserving discoverability without breaking the order.
    canonical_order = (
        "discover",
        "parse_extract",
        "resolve",
        "graph_metrics",
        "git_analysis",
        "effects_taint",
        "health_load",
        "search_indexes",
    )
    ordered: list[tuple[str, float]] = []
    seen: set[str] = set()
    for k in canonical_order:
        if k in phase_timings:
            ordered.append((k, float(phase_timings[k])))
            seen.add(k)
    for k, v in phase_timings.items():
        if k not in seen:
            try:
                ordered.append((k, float(v)))
            except (TypeError, ValueError):
                continue
    total = round(sum(s for _, s in ordered), 3)
    # Human detail: name the slowest phase + total. LAW 4 anchor: terminal
    # token "seconds" — included in tests/test_law4_lint.py's anchor set.
    slowest_phase, slowest_seconds = max(ordered, key=lambda p: p[1])
    detail = (
        f"index ran {total:.3f} seconds across {len(ordered)} phases; "
        f"slowest phase '{slowest_phase}' took {slowest_seconds:.3f} seconds"
    )
    return {
        "name": "Phase timings",
        "passed": True,
        "detail": detail,
        "_state": "ok",
        "_phase_timings": {k: round(s, 3) for k, s in ordered},
        "_total_seconds": total,
        "_slowest_phase": slowest_phase,
        "_slowest_seconds": round(slowest_seconds, 3),
    }


def _check_installed_binary_matches_source() -> dict:
    """Detect a stale ``roam`` binary that doesn't point at this source tree.

    A common foot-gun across multiple agent recheck rounds (W7.3, W13.5):
    the local repo's source has new commands but the installed ``roam``
    binary on PATH was built from an older checkout. Agents then see
    ``No such command`` errors at run-time.

    States:
      fresh         — imported roam module and on-PATH ``roam`` binary
                      point at the same site-packages / source tree
      stale_install — paths diverge (likely: editable import shadows a
                      uv-installed binary, or vice-versa)
      no_binary     — ``shutil.which("roam")`` returns None — likely a
                      bare ``python -m roam`` invocation, not actionable

    Advisory only — the running command obviously imported just fine,
    so this can't be blocking. It exists to surface the staleness so
    the user can refresh the binary before the next shell invocation.
    """
    try:
        import roam  # noqa: PLC0415 — local import keeps doctor's surface lazy
    except Exception as exc:
        return {
            "name": "Installed binary",
            "passed": False,
            "detail": f"could not import roam: {type(exc).__name__}: {exc}",
            "_state": "import_failed",
        }

    import_path_str = getattr(roam, "__file__", None)
    if not import_path_str:
        return {
            "name": "Installed binary",
            "passed": True,
            "detail": "roam module has no __file__ — namespace package or frozen build",
            "_state": "no_file",
        }
    import_path = Path(import_path_str).resolve()
    # ``roam/__init__.py`` -> the package dir is its parent.
    import_pkg_dir = import_path.parent

    binary_path_str = shutil.which("roam")
    if binary_path_str is None:
        return {
            "name": "Installed binary",
            "passed": True,
            "detail": "no `roam` binary on PATH — running via `python -m roam` or similar",
            "_state": "no_binary",
            "_import_path": str(import_pkg_dir),
        }

    # Resolve the binary's installed-package location. Two robust signals:
    # 1) shebang of the wrapper script (Unix), 2) ``roam`` exe living in
    # a Scripts/ or bin/ sibling of a site-packages/roam/ dir (Windows).
    # We don't try to execute the binary — that's slow and racey — we
    # only need the *location* it would run from.
    binary_path = Path(binary_path_str).resolve()

    # Search upward from the binary for a sibling site-packages/roam (or
    # Lib/site-packages/roam on Windows uv tool installs) and treat that
    # as the binary's effective source tree.
    binary_pkg_dir: Path | None = None
    for parent in binary_path.parents:
        for candidate in (
            parent / "site-packages" / "roam",
            parent / "Lib" / "site-packages" / "roam",
            parent / "lib" / "site-packages" / "roam",
        ):
            if candidate.is_dir():
                binary_pkg_dir = candidate.resolve()
                break
        if binary_pkg_dir is not None:
            break

    if binary_pkg_dir is None:
        # We can't resolve where the binary points; report the binary
        # path and treat it as advisory pass (insufficient evidence to
        # claim staleness).
        return {
            "name": "Installed binary",
            "passed": True,
            "detail": (
                f"`roam` binary at {binary_path} — could not locate its "
                f"site-packages/roam to compare against import {import_pkg_dir}"
            ),
            "_state": "unknown",
            "_import_path": str(import_pkg_dir),
            "_binary_path": str(binary_path),
        }

    if binary_pkg_dir == import_pkg_dir:
        return {
            "name": "Installed binary",
            "passed": True,
            "detail": f"`roam` binary and import share source tree at {import_pkg_dir}",
            "_state": "fresh",
            "_import_path": str(import_pkg_dir),
            "_binary_path": str(binary_path),
        }

    return {
        "name": "Installed binary",
        "passed": False,
        "detail": (
            f"`roam` binary points at {binary_pkg_dir} but the running "
            f"import is from {import_pkg_dir}. "
            "Run `pip install -e .` or `uv tool install -e .` to refresh the binary."
        ),
        "_state": "stale_install",
        "_import_path": str(import_pkg_dir),
        "_binary_pkg_dir": str(binary_pkg_dir),
        "_binary_path": str(binary_path),
    }


def _check_mcp_backpressure() -> dict:
    """MCP backpressure module loads with sensible limits.

    Round 4 / P: the bounded-semaphore guard wraps every MCP tool. If
    the limits are pathological (zero or negative) every call would
    return BUSY — surface that configuration error here.
    """
    try:
        from roam.mcp_extras import concurrency
    except Exception as exc:
        return {
            "name": "MCP backpressure",
            "passed": False,
            "detail": f"concurrency module import failed: {type(exc).__name__}: {exc}",
        }
    snapshot = concurrency.metrics()
    limit = snapshot.get("max_concurrent", 0)
    if limit < 1:
        return {
            "name": "MCP backpressure",
            "passed": False,
            "detail": f"max_concurrent={limit} (set ROAM_MCP_MAX_CONCURRENT to a positive integer)",
        }
    per_tool_count = len(snapshot.get("per_tool_limits", {}))
    return {
        "name": "MCP backpressure",
        "passed": True,
        "detail": f"max_concurrent={limit}, {per_tool_count} per-tool override(s) active",
    }


_CLOUD_PATH_MARKERS = (
    "OneDrive",
    "Dropbox",
    "iCloudDrive",
    "iCloud Drive",
    "Google Drive",
    "Google Drive File Stream",
    "GoogleDrive",
    "Box Sync",
    "pCloud",
)


def _check_cloud_sync() -> dict:
    """Warn if the project lives under a cloud-sync folder.

    Cloud-synced folders (OneDrive, Dropbox, iCloud, Google Drive) re-upload the
    SQLite index file on every write, which can corrupt the DB during a long
    index run, and slow `roam` calls considerably. Excluding `.roam/` from sync
    fixes both.
    """
    project_root = str(Path.cwd().resolve())
    matched = next((m for m in _CLOUD_PATH_MARKERS if m in project_root), None)
    if matched is None:
        return {
            "name": "Cloud sync",
            "passed": True,
            "detail": "project not under a known cloud-sync folder",
        }
    return {
        "name": "Cloud sync",
        "passed": False,
        "detail": (
            f"project root contains '{matched}' — exclude .roam/ from sync to "
            "avoid index corruption during long indexing runs and to reduce "
            "per-command latency."
        ),
    }


def _check_cache_permissions() -> dict:
    """`.roam/` and `.pytest_cache/` must be writable; locks here cause silent failures."""
    project_root = Path.cwd()
    issues: list[str] = []
    for sub in (".roam", ".pytest_cache"):
        d = project_root / sub
        if not d.exists():
            continue
        # Try to create a tiny probe file.
        probe = d / ".roam-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except (OSError, PermissionError) as exc:
            issues.append(f"{sub}/: {type(exc).__name__}")
    if issues:
        return {
            "name": "Cache permissions",
            "passed": False,
            "detail": "not writable: " + ", ".join(issues),
        }
    return {
        "name": "Cache permissions",
        "passed": True,
        "detail": "all probed cache dirs writable",
    }


def _check_optional_extras() -> dict:
    """Probe optional extras: semantic search, file watcher, MCP server."""
    extras: dict[str, str] = {}
    for mod, label in (
        ("onnxruntime", "semantic-search ONNX runtime"),
        ("watchdog", "file watcher"),
        ("fastmcp", "MCP server"),
        ("scipy", "spectral analysis"),
    ):
        try:
            __import__(mod)
            extras[mod] = "installed"
        except ImportError:
            extras[mod] = "missing (optional)"
    missing = [m for m, s in extras.items() if "missing" in s]
    detail_parts = [f"{m}={s.split()[0]}" for m, s in extras.items()]
    return {
        # Always pass — these are optional. Report status for visibility.
        "name": "Optional extras",
        "passed": True,
        "detail": ", ".join(detail_parts)
        + (f" ({len(missing)} missing — features degrade gracefully)" if missing else ""),
    }


# W482 — emitted-workflow drift detection (follow-up to W471 SLSA SRC-L3).
#
# When ``roam ci-setup --write`` (and ``--with-slsa-l3``) emit GitHub
# Actions YAML, those files are then committed by the user. If a future
# release ships an updated template (e.g. cosign-installer@v3 → v4, or a
# new permission added), the live workflow silently drifts. This check
# diffs every live workflow under ``.github/workflows/`` against the
# canonical bundled template and surfaces the diff to the user.
#
# Registry of (template_file_name_under_templates_ci, live_workflow_path
# relative to project root). Sourced from cmd_ci_setup so the truth stays
# single-sourced — adding a new GitHub template to ci-setup automatically
# extends doctor's drift check.
def _github_template_registry() -> list[tuple[str, str]]:
    """Return [(template_filename, live_workflow_path)] for GitHub templates.

    Only GitHub-Actions templates are checked: non-GitHub platforms
    (GitLab, Azure, Jenkins, Bitbucket) live OUTSIDE ``.github/workflows/``
    and have a different surface area; the drift question for those is
    distinct enough to warrant its own future check rather than mixing it
    in here. Drive-by W-task suggestion below.
    """
    return [
        # W471 — SLSA SRC-L3 auto-trigger workflow.
        ("slsa-src-l3.yml", ".github/workflows/roam-slsa-src-l3.yml"),
        # Pre-existing agent-review drop-in.
        ("agent-review.yml", ".github/workflows/agent-review.yml"),
        # W391 — roam SARIF + CodeQL co-deploy sample. Live filename is
        # documented in the template header ("Copy this file to
        # .github/workflows/security.yml"); drift advisory only fires once
        # the user has actually committed the sample under that path.
        ("roam-sarif-with-codeql.yml", ".github/workflows/security.yml"),
    ]


def _normalize_workflow_yaml(text: str) -> str:
    """Strip comments + trailing whitespace + collapse blank-line runs.

    Diff stability for the W482 drift check requires ignoring purely
    cosmetic differences: in-file comments (which often contain dates or
    URLs the user customised), trailing whitespace, and runs of blank
    lines. We do NOT parse YAML here — a full ruamel.yaml round-trip
    would normalize too aggressively (reorder keys, drop comments
    semantically meaningful to the workflow). Line-by-line strip is the
    right intermediate fidelity.
    """
    out: list[str] = []
    prev_blank = False
    for raw in text.splitlines():
        line = raw.rstrip()
        # Strip whole-line comments (# at column 0 or after leading whitespace).
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if not line:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(line)
    # Trim leading + trailing blank lines.
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


# Match `python-version:` value lines in a GitHub-Actions YAML.
# Accepts single-quoted, double-quoted, or unquoted scalar values. We
# anchor on lstrip to avoid grabbing comment-embedded mentions.
_PYTHON_VERSION_LINE_RE = re.compile(
    r"""^\s*python-version\s*:\s*['"]?([^\s'"#]+)['"]?\s*(?:\#.*)?$""",
    re.MULTILINE,
)


def _extract_python_version_from_workflow(text: str) -> str | None:
    """Return the first ``python-version:`` value in a workflow YAML.

    Drive-by fix for W515 — without this, drift compare uses the default
    ``3.12`` and any user who emitted with ``--python-version 3.11``
    gets a FALSE drift report on every ``roam doctor`` run. By feeding
    the live file's own pin back as the substitution variable, we only
    flag drift on *real* structural divergence.

    Returns ``None`` when no ``python-version:`` line is present (e.g.
    a template that doesn't take a python-version pin).
    """
    match = _PYTHON_VERSION_LINE_RE.search(text)
    if match is None:
        return None
    return match.group(1)


def _check_ci_workflow_drift() -> dict:
    """Detect drift between emitted GitHub workflows and canonical templates.

    Compares live ``.github/workflows/*.yml`` files (those that came from
    ``roam ci-setup --write``) against the bundled template under
    ``src/roam/templates/ci/``. Drift means the template was updated
    upstream but the user's workflow wasn't refreshed.

    States:
      not_applicable — no live workflow exists for any registered template
                       (project doesn't use this feature; advisory pass)
      clean          — every live workflow matches its template
      drift          — at least one live workflow differs from its template
      template_missing — packaging error: a registered template file is
                       absent from the install (advisory pass; doctor's
                       other checks already cover packaging integrity)

    Advisory only — drift is informational, never blocking. Users should
    refresh the live workflow with ``roam ci-setup --write`` (will warn
    on file exists) or re-emit after deleting the old file.
    """
    try:
        from roam.commands.cmd_ci_setup import (
            _GITHUB_TEMPLATE,
            _get_python_version,
            _substitute_vars,
            _templates_dir,
        )
    except Exception as exc:
        return {
            "name": "CI workflow drift",
            "passed": True,
            "detail": f"ci-setup module import failed: {type(exc).__name__}: {exc}",
            "_state": "import_failed",
        }

    project_root = Path.cwd()
    templates_dir = _templates_dir()

    # Default substitution variables — match cmd_ci_setup's defaults so the
    # rendered template matches what `ci-setup --write` would produce with
    # no flags. W515 — we ALSO read the live file's python-version pin and
    # re-substitute before diffing: a user who passed
    # `roam ci-setup --python-version 3.11 --write` should not be told they
    # have drift on every doctor run when the only divergence is the pin
    # they explicitly chose. Only structural divergence is real drift.
    default_python_version = _get_python_version()

    # Carry the raw template text per pair so we can re-render after
    # reading the live file's pin. Templates without a python_version
    # placeholder are unaffected (substitution is a no-op).
    inline_pairs: list[tuple[str, str, str]] = [
        # (label, raw_template_text, live_path_relative)
        ("roam.yml", _GITHUB_TEMPLATE, ".github/workflows/roam.yml"),
    ]
    file_pairs: list[tuple[str, str, str]] = []
    for template_filename, live_rel in _github_template_registry():
        template_path = templates_dir / template_filename
        if not template_path.exists():
            # Packaging error — record but don't fail the check; the
            # "Required tables" / installer-level checks cover this.
            file_pairs.append((template_filename, "", live_rel))
            continue
        file_pairs.append((template_filename, template_path.read_text(encoding="utf-8"), live_rel))

    all_pairs = inline_pairs + file_pairs

    checked = 0
    drifted: list[dict] = []
    missing: list[dict] = []
    template_missing: list[str] = []

    for label, raw_template, live_rel in all_pairs:
        if not raw_template:
            template_missing.append(label)
            continue
        live_path = project_root / live_rel
        if not live_path.exists():
            missing.append(
                {
                    "template": label,
                    "live_path": live_rel,
                    "state": "not_emitted",
                }
            )
            continue
        try:
            live_text = live_path.read_text(encoding="utf-8")
        except OSError as exc:
            drifted.append(
                {
                    "template": label,
                    "live_path": live_rel,
                    "state": "unreadable",
                    "diff_summary": f"{type(exc).__name__}: {exc}",
                }
            )
            checked += 1
            continue
        checked += 1
        # W515 — substitute with the live file's own python-version pin
        # if one is present. Falls back to the bundled default for
        # templates that don't declare the placeholder or live files
        # without a pin (in which case the result is identical to the
        # pre-W515 behaviour).
        live_python_version = _extract_python_version_from_workflow(live_text)
        rendered = _substitute_vars(
            raw_template,
            {"python_version": live_python_version or default_python_version},
        )
        live_norm = _normalize_workflow_yaml(live_text)
        tmpl_norm = _normalize_workflow_yaml(rendered)
        if live_norm == tmpl_norm:
            continue
        # Lightweight diff summary: line counts + first divergence. A full
        # unified diff would balloon the doctor envelope; we keep it
        # one-line and let the user re-emit to see the structural diff.
        live_lines = live_norm.splitlines()
        tmpl_lines = tmpl_norm.splitlines()
        first_diverge = 0
        for i, (a, b) in enumerate(zip(live_lines, tmpl_lines)):
            if a != b:
                first_diverge = i + 1  # 1-based
                break
        else:
            first_diverge = min(len(live_lines), len(tmpl_lines)) + 1
        drifted.append(
            {
                "template": label,
                "live_path": live_rel,
                "state": "drifted",
                "diff_summary": (
                    f"{len(live_lines)} live vs {len(tmpl_lines)} template lines; "
                    f"first divergence at line {first_diverge}"
                ),
            }
        )

    # If no live workflows AND no drifted entries, the feature isn't in
    # use here — advisory pass.
    if checked == 0 and not drifted:
        return {
            "name": "CI workflow drift",
            "passed": True,
            "detail": "no emitted roam workflows found under .github/workflows/",
            "_state": "not_applicable",
            "_checked": 0,
            "_drifted": [],
            "_missing": missing,
            "_template_missing": template_missing,
        }

    if drifted:
        names = ", ".join(d["template"] for d in drifted[:3])
        more = f" (+{len(drifted) - 3} more)" if len(drifted) > 3 else ""
        return {
            "name": "CI workflow drift",
            "passed": False,
            "detail": (
                f"ADVISORY: {len(drifted)} workflows drifted from template ({names}{more}). "
                "Run `roam ci-setup --platform github --write` (delete the live file first) "
                "to refresh."
            ),
            "_state": "drift",
            "_checked": checked,
            "_drifted": drifted,
            "_missing": missing,
            "_template_missing": template_missing,
        }

    return {
        "name": "CI workflow drift",
        "passed": True,
        "detail": f"all {checked} emitted workflows match templates",
        "_state": "clean",
        "_checked": checked,
        "_drifted": [],
        "_missing": missing,
        "_template_missing": template_missing,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


# Checks whose failure is advisory — these don't block normal usage.
# A failure here means "something to be aware of" rather than "roam is
# broken." CI can still pass with advisory failures unless ``--strict``
# is set. See cmd_doctor docstring for the full exit-code matrix.
_ADVISORY_CHECK_NAMES = frozenset(
    {
        "Optional extras",  # graceful degradation when extras missing
        "Cloud sync",  # OneDrive/Dropbox warning, not a hard break
        "MCP backpressure",  # only matters for MCP server users
        "MCP tool registry",  # only matters for MCP users
        "Plugin discovery",  # plugins are optional
        "Index exists",  # auto-created on first command
        "Index freshness",  # stale index is still functional
        "Index manifest",  # drift hints — informational
        "Index manifest history",  # cross-run drift — informational
        "Index step manifest",  # per-sub-step failures — informational
        #   (W82/A8: names which sub-step degraded)
        "Installed binary",  # stale-install hint — informational
        "Stale math_signals column",  # post-migration #51 re-index hint
        "Phase timings",  # W408 — per-phase wallclock; diagnostic only
        "CI workflow drift",  # W482 — emitted-workflow vs template drift
        "Corpus content",  # W836 — empty corpus is advisory, not blocking
    }
)


@roam_capability(
    name="doctor",
    category="health",
    summary="Diagnose environment: Python, dependencies, git, index state.",
    inputs=[],
    outputs=["checks", "verdict"],
    examples=["roam doctor", "roam doctor --strict"],
    tags=["diagnostics", "setup"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("doctor")
@click.option(
    "--strict",
    is_flag=True,
    help=(
        "Promote advisory check failures to blocking. CI gates that "
        "require zero drift use this; default behaviour treats advisory "
        "failures as warnings (exit 1) so cache-age and cloud-sync "
        "warnings don't fail every CI run."
    ),
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Write BLOCKING check failures into the central findings "
        "registry (subject_kind='environment'). Advisory failures are "
        "intentionally skipped — they are ephemeral environment "
        "diagnostics with no permanent codebase-level meaning."
    ),
)
@click.pass_context
def doctor(ctx, strict, persist):
    """Diagnose environment setup: Python, dependencies, and index state.

    Unlike ``health`` (which analyzes codebase structural quality), this command
    validates the local environment: Python, dependencies, git, and index state.
    Checks each requirement and reports PASS or FAIL. Useful for onboarding
    new developers or troubleshooting agent setup issues.

    \b
    Exit codes:
      0  All checks passed.
      1  Only advisory checks failed (cache age, cloud sync, optional extras).
      2  At least one blocking check failed (Python version, tree-sitter, ...).

    With ``--strict``, advisory failures are promoted to blocking — any
    failure exits 2. CI gates that require zero drift use this.

    \b
    Examples:
      roam doctor
      roam --json doctor
      roam doctor --strict   # CI mode

    See also ``health`` (codebase quality), ``init`` (initial setup),
    and ``mcp-setup`` (verify MCP server registration).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # W607-N: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the doctor pipeline. cmd_doctor is the DB-shape
    # aggregator that consumes findings + health + describe + retrieve +
    # index_status substrates through ~22 per-check helpers (corpus,
    # required tables, manifest, manifest history, index step failures,
    # phase timings, math_signals drift, CI workflow drift, …). Every
    # per-check helper already catches exceptions internally and emits a
    # ``passed: False`` record, BUT each helper itself can still raise
    # before reaching that floor (Python misconfig, networkx import
    # explosion, unhandled OSError during ``shutil.which``, etc.) — and
    # the outer ``checks.append(_check_X())`` loop has no guards. The
    # W607-N wrapper turns each helper invocation into a marker-emitting
    # try/except + skips the check on failure so the envelope still emits.
    #
    # Marker family ``doctor_*`` (DB / environment-aggregator scope,
    # distinct from W607-M's ``health_*`` flagship CI-gate family,
    # W607-K's ``describe_*`` flagship-aggregator family, W607-L's
    # ``minimap_*`` DB-shape family, and W607-G/H/I/J subprocess
    # families). The marker-prefix discipline keeps each consumer's
    # scope identifiable downstream.
    #
    # Complementary to W805-836 Pattern-2 silent "all checks passed" on
    # empty corpus (which pins the corpus-content advisory). W607-N
    # does NOT graduate any W805-836 bug — the corpus-content disclosure
    # is a separate Pattern-2 contract orthogonal to the per-helper
    # substrate-failure axis here.
    #
    # Empty bucket → byte-identical envelope (no warnings_out key in
    # either ``summary`` or top-level).
    _w607n_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args):
        """Run one ``_check_*`` helper with W607-N marker emission.

        On a clean call the result is appended to ``checks`` as before.
        On an uncaught exception (the helper itself raised before
        producing its own pass/fail dict), surface a
        ``doctor_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607n_warnings_out`` and skip the check — the envelope still
        emits cleanly with the remaining checks.
        """
        try:
            result = fn(*args)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607n_warnings_out.append(f"doctor_{phase}_failed:{type(exc).__name__}:{exc}")
            return None
        checks.append(result)
        return result

    # --- Run all checks ---
    checks: list[dict] = []

    _run_check("python_version", _check_python_version)
    _run_check("tree_sitter", _check_tree_sitter)
    _run_check("tree_sitter_language_pack", _check_tree_sitter_language_pack)
    _run_check("git", _check_git)
    _run_check("networkx", _check_networkx)
    _run_check("optional_extras", _check_optional_extras)
    _run_check("cloud_sync", _check_cloud_sync)
    _run_check("cache_permissions", _check_cache_permissions)
    _run_check("command_registry", _check_command_registry)
    _run_check("mcp_registry", _check_mcp_registry)
    _run_check("mcp_backpressure", _check_mcp_backpressure)
    _run_check("plugin_discovery", _check_plugin_discovery)
    _run_check("required_tables", _check_required_tables)
    _run_check("corpus_content", _check_corpus_content)
    _run_check("stale_math_signal_column", _check_stale_math_signal_column)

    # Index checks: existence feeds into freshness and SQLite checks
    index_check = _run_check("index_exists", _check_index_exists)

    db_path_str = index_check.get("_db_path") if index_check else None
    _run_check("index_freshness", _check_index_freshness, db_path_str)
    _run_check("sqlite", _check_sqlite, db_path_str)
    _run_check("index_manifest", _check_index_manifest)
    _run_check("index_manifest_history", _check_index_manifest_history)
    _run_check("index_step_failures", _check_index_step_failures)
    # W408: surface per-phase wallclock from the latest indexer run. The
    # check itself is always advisory; the JSON envelope adds a top-level
    # ``phase_timings`` block so consumers can read seconds-per-phase
    # without parsing the human-readable detail string.
    phase_check = _run_check("phase_timings", _check_phase_timings) or {}
    _run_check("installed_binary", _check_installed_binary_matches_source)
    # W482 — emitted-workflow drift (follow-up to W471 SLSA SRC-L3).
    ci_drift_check = _run_check("ci_workflow_drift", _check_ci_workflow_drift) or {}

    # --- Compute summary, with advisory / blocking severity split ---
    total = len(checks)
    failed = [c for c in checks if not c["passed"]]
    passed_count = total - len(failed)

    advisory_failed = [c for c in failed if c["name"] in _ADVISORY_CHECK_NAMES]
    blocking_failed = [c for c in failed if c["name"] not in _ADVISORY_CHECK_NAMES]

    # --- W156: mirror BLOCKING failures into the central findings registry.
    # HYBRID filter — advisory failures are by-design ephemeral environment
    # diagnostics (cache age, cloud sync, dev/install drift) and would
    # pollute the registry with non-codebase signal. Blocking failures ARE
    # reproducible problems the codebase or CI can act on, so they earn a
    # row. Wrapped defensively: a pre-W89 DB (no ``findings`` table) or a
    # missing index silently no-ops rather than crashing doctor.
    #
    # W607-BE: ADDITIVE substrate-boundary plumbing over the prior W607-N
    # closure. cmd_doctor's persist path collapses three distinct
    # registry-side substrate boundaries into a single ``except Exception:
    # _w607n_warnings_out.append(...)`` channel — losing the ability to
    # tell which step crashed (DB existence probe vs connection open vs
    # finding emit vs commit). W607-BE splits the boundary so each
    # registry-side raise surfaces a structured
    # ``doctor_<phase>_failed:<exc_class>:<detail>`` marker via
    # ``_w607be_warnings_out``; markers are merged into BOTH
    # ``summary.warnings_out`` and the top-level ``warnings_out`` at
    # output time. Empty bucket -> byte-identical envelope.
    _w607be_warnings_out: list[str] = []

    def _run_check_be(phase: str, fn, *args, default=None, **kwargs):
        """Run one persist-side substrate with W607-BE marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``doctor_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607be_warnings_out`` and return *default* — the
        envelope still emits cleanly with the remaining substrates. This
        complements W607-N's per-``_check_*`` wrapper (which catches
        helpers raising before producing their pass/fail dict) by
        disclosing which registry-side step crashed when ``--persist``
        is set.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607be_warnings_out.append(f"doctor_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    if persist and blocking_failed:
        from roam.db.connection import db_exists, open_db

        _db_present = _run_check_be("persist_db_exists", db_exists, default=False)
        if _db_present:
            _conn_ctx = _run_check_be(
                "persist_open_db",
                lambda: open_db(readonly=False),
                default=None,
            )
            if _conn_ctx is not None:
                try:
                    with _conn_ctx as _conn:
                        _run_check_be(
                            "persist_emit_findings",
                            _emit_doctor_findings,
                            _conn,
                            checks,
                            DOCTOR_DETECTOR_VERSION,
                        )
                        _run_check_be(
                            "persist_commit_findings",
                            _conn.commit,
                        )
                except Exception as exc:  # noqa: BLE001 — context-manager raise
                    # The ``with`` context may itself raise on exit (e.g.
                    # connection.__exit__ rolling back a transaction that
                    # hit a sqlite3.OperationalError on schema drift). The
                    # individual inner boundaries already disclosed via
                    # ``_run_check_be`` when they raised; this outer
                    # marker captures only the context-exit edge.
                    _w607be_warnings_out.append(f"doctor_persist_context_failed:{type(exc).__name__}:{exc}")

    # W607-DW: post-capture substrate-CALL plumbing LAYERED on top of
    # W607-N (capture-layer per-check wrap) and W607-BE (persist-side
    # boundaries). cmd_doctor's POST-capture path collapses five distinct
    # substrate boundaries into one unguarded sequence — score
    # computation, verdict composition, section assembly, envelope
    # serialization, and text formatting. A raise inside any of these
    # (e.g. a poisoned ``len(failed)``, an f-string format-spec crash on
    # a degraded ``_check_*`` result, a non-JSON-serializable section
    # payload, a ``__str__`` raise during click.echo) would torpedo the
    # envelope WITHOUT lineage.
    #
    # W607-DW splits the post-capture boundary into 5 wrapped substrate
    # calls so each raise surfaces a structured
    # ``doctor_<phase>_failed:<exc_class>:<detail>`` marker via
    # ``_w607dw_warnings_out``; markers merge into BOTH
    # ``summary.warnings_out`` and the top-level ``warnings_out`` at
    # output time. Empty bucket → byte-identical envelope.
    #
    # Phase sub-vocabulary (DISJOINT from W607-N's ``_check_*`` phase
    # names and W607-BE's ``persist_*`` phase names so the shared
    # ``doctor_*`` marker family carries no collision):
    #   compute_scores / compose_verdict / assemble_sections /
    #   serialize_envelope / format_text
    #
    # Mirrors the cmd_dashboard W607-DP shape exactly. The helper
    # template returns ``default`` VERBATIM on raise (NOT
    # ``default if default is not None else {}``) so the
    # serialize_envelope path's ``rendered is None`` guard works.
    _w607dw_warnings_out: list[str] = []

    def _run_check_dw(phase: str, fn, *args, default=None, **kwargs):
        """Run one W607-DW post-capture substrate with marker emission.

        Clean call returns the result as-is. On an uncaught raise,
        surface ``doctor_<phase>_failed:<exc_class>:<detail>`` via
        ``_w607dw_warnings_out`` and substitute *default* — the
        envelope still emits the remaining substrates cleanly.

        ``default`` is returned VERBATIM on raise (including ``None``)
        so callers can distinguish a degraded-but-empty result (``{}``)
        from a degraded-no-output result (``None``). This is critical
        for the ``serialize_envelope`` phase whose ``rendered is None``
        guard precedes the minimal-fallback echo (W978 #6).
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607dw_warnings_out.append(f"doctor_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DW ``compute_scores`` substrate: derive verdict from per-check
    # pass/fail counts. A raise inside ``len(failed)`` or
    # ``failed[0]['name']`` (e.g. a degraded ``_check_*`` returning a
    # non-dict surrogate) surfaces a canonical marker instead of crashing.
    def _compute_scores():
        if not failed:
            return {"verdict_kind": "all_passed", "verdict_arg": total}
        elif blocking_failed and len(failed) == 1:
            return {"verdict_kind": "single_fail", "verdict_arg": failed[0]["name"]}
        elif blocking_failed:
            return {
                "verdict_kind": "mixed_blocking",
                "verdict_arg": (len(blocking_failed), len(advisory_failed)),
            }
        else:
            return {
                "verdict_kind": "only_advisory",
                "verdict_arg": len(advisory_failed),
            }

    _scores = _run_check_dw(
        "compute_scores",
        _compute_scores,
        default={"verdict_kind": "degraded", "verdict_arg": None},
    )

    # W607-DW ``compose_verdict`` substrate: the LAW 6 single-line floor
    # lives here. A raise inside the f-string composition returns the
    # literal floor verdict instead of crashing.
    def _compose_verdict():
        kind = _scores.get("verdict_kind") if isinstance(_scores, dict) else "degraded"
        arg = _scores.get("verdict_arg") if isinstance(_scores, dict) else None
        if kind == "all_passed":
            return f"all {arg} checks passed"
        elif kind == "single_fail":
            return f"1 check failed ({arg})"
        elif kind == "mixed_blocking":
            return f"{arg[0]} blocking, {arg[1]} advisory"
        elif kind == "only_advisory":
            return f"{arg} non-blocking advisory check(s) failed"
        else:
            return "DOCTOR — verdict unavailable"

    # W978 #1: verdict floor is a non-empty literal string so a
    # degraded compose_verdict still satisfies LAW 6.
    verdict = _run_check_dw(
        "compose_verdict",
        _compose_verdict,
        default="DOCTOR — verdict unavailable",
    )

    # Issue-template-ready summary line: a single string capturing the
    # diagnostic in copy-paste form, so users filing GitHub bugs can
    # paste one line that contains all the relevant context.
    #
    # WMI-hang guard: do NOT use platform.system()/platform.release() on
    # Windows. On CPython 3.12 they route through platform.uname() ->
    # win32_ver() -> _wmi_query(), a WMI COM call that hangs indefinitely
    # when the WMI service is slow/contended — observed wedging `roam doctor`
    # past a 90s budget on Win11 (all checks complete; only this cosmetic
    # line blocked). sys.getwindowsversion() and sys.platform are WMI-free.
    if sys.platform == "win32":
        _wv = sys.getwindowsversion()
        _os_label = f"Windows {_wv.major}.{_wv.minor}.{_wv.build}"
    else:
        import platform as _platform

        _os_label = f"{_platform.system()} {_platform.release()}"

    issue_line = (
        f"Roam {_get_roam_version()} · "
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} · "
        f"{_os_label} · "
        f"{passed_count}/{total} checks pass · "
        f"{len(advisory_failed)} advisory · {len(blocking_failed)} blocking"
    )

    # Strip private keys (prefixed with _) before output
    clean_checks = [{k: v for k, v in c.items() if not k.startswith("_")} for c in checks]

    # W408: hoist the phase-timing payload into the envelope so consumers
    # (CI dashboards, W395-followup perf rankers) don't have to parse the
    # human-readable ``detail`` string. Top-level key stays compact when
    # no index exists yet.
    phase_timings_block: dict = {
        "state": phase_check.get("_state", "no_data"),
    }
    if phase_check.get("_phase_timings"):
        phase_timings_block["per_phase_seconds"] = phase_check["_phase_timings"]
        phase_timings_block["total_seconds"] = phase_check.get("_total_seconds", 0.0)
        phase_timings_block["slowest_phase"] = phase_check.get("_slowest_phase")
        phase_timings_block["slowest_seconds"] = phase_check.get("_slowest_seconds")

    # W482: hoist drift detail into a top-level ci_workflow_drift block so
    # consumers (CI dashboards, agent contracts) read structured drift
    # entries instead of parsing the human-readable detail string.
    ci_workflow_drift_block: dict = {
        "state": ci_drift_check.get("_state", "not_applicable"),
        "templates_checked": ci_drift_check.get("_checked", 0),
        "drifted": ci_drift_check.get("_drifted", []),
        "missing": ci_drift_check.get("_missing", []),
        "template_missing": ci_drift_check.get("_template_missing", []),
    }

    # Pattern-2 playbook (W836 follow-up): surface explicit partial_success +
    # state so downstream consumers don't need to count advisory_failed +
    # blocking_failed themselves. partial_success is True whenever ANY check
    # failed — both advisory and blocking degrade the run from "all clear".
    # state vocabulary: all_passed | advisory_warnings | blocking_failures.
    if blocking_failed:
        state = "blocking_failures"
    elif advisory_failed:
        state = "advisory_warnings"
    else:
        state = "all_passed"

    if json_mode:
        # W607-N + W607-BE + W607-DW: Pattern-2 consumer-layer wiring —
        # when any per-check helper, persist-side substrate, OR
        # post-capture substrate raised before producing its own dict,
        # the marker bucket carries the lineage. Surface on BOTH the
        # summary mirror (so consumers reading only ``summary`` see the
        # disclosure) AND the top-level (so the preserved-list field at
        # ``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py survives
        # detail-mode list-payload stripping). Non-empty bucket also
        # flips ``partial_success`` to True regardless of any check pass.
        #
        # W607-DW ``assemble_sections`` substrate boundary: build the
        # summary_block + envelope_kwargs in one wrapped call so a
        # ``.get`` chain on a degraded sub-envelope does not crash.
        def _assemble_sections():
            # Pattern-1 family canonical-failure envelope status (CLAUDE.md):
            #   all_passed         -> "ok"
            #   advisory_warnings  -> "advisory_warnings"
            #   blocking_failures  -> "hard_failure"
            # Distinguishes advisory-only (exit 0) from hard-failure (exit 2)
            # without forcing consumers to count advisory_failed/blocking_failed.
            if blocking_failed:
                _status = "hard_failure"
            elif advisory_failed:
                _status = "advisory_warnings"
            else:
                _status = "ok"
            _summary_block: dict = {
                "verdict": verdict,
                "status": _status,
                "issue_line": issue_line,
                "total": total,
                "passed": passed_count,
                "failed": len(failed),
                "advisory_failed": len(advisory_failed),
                "blocking_failed": len(blocking_failed),
                "all_passed": len(failed) == 0,
                "partial_success": (
                    bool(failed)
                    or bool(_w607n_warnings_out)
                    or bool(_w607be_warnings_out)
                    or bool(_w607dw_warnings_out)
                ),
                "state": state,
                "strict": bool(strict),
            }
            _envelope_kwargs: dict = {
                "checks": clean_checks,
                "failed_checks": [c for c in clean_checks if not c["passed"]],
                "advisory_failed": [c for c in clean_checks if c["name"] in _ADVISORY_CHECK_NAMES and not c["passed"]],
                "blocking_failed": [
                    c for c in clean_checks if c["name"] not in _ADVISORY_CHECK_NAMES and not c["passed"]
                ],
                "phase_timings": phase_timings_block,
                "ci_workflow_drift": ci_workflow_drift_block,
            }
            return {"summary_block": _summary_block, "envelope_kwargs": _envelope_kwargs}

        # Floor on degrade: minimal summary + empty kwargs so the
        # serialize_envelope substrate still has structurally valid
        # input. The verdict literal floor preserved separately.
        _assembled = _run_check_dw(
            "assemble_sections",
            _assemble_sections,
            default={
                "summary_block": {"verdict": verdict, "partial_success": True},
                "envelope_kwargs": {},
            },
        )
        summary_block = (
            _assembled.get("summary_block", {"verdict": verdict})
            if isinstance(_assembled, dict)
            else {"verdict": verdict}
        )
        envelope_kwargs: dict = _assembled.get("envelope_kwargs", {}) if isinstance(_assembled, dict) else {}

        # W607-N + W607-BE + W607-DW additive: merge all three marker
        # buckets into a single ``warnings_out`` channel. All three
        # prefix families use the ``doctor_<phase>_failed:<exc_class>:<detail>``
        # shape so downstream parsers see one closed-enum prefix family.
        _combined_markers = list(_w607n_warnings_out) + list(_w607be_warnings_out) + list(_w607dw_warnings_out)
        if _combined_markers:
            summary_block["partial_success"] = True
            summary_block["warnings_out"] = list(_combined_markers)
            envelope_kwargs["warnings_out"] = list(_combined_markers)

        # W607-DW ``serialize_envelope`` substrate boundary: a raise in
        # ``json_envelope`` or ``to_json`` (e.g. a non-serializable
        # section payload) surfaces as the canonical marker; the
        # command still emits a minimal envelope on the degraded path.
        def _serialize_envelope():
            return to_json(
                json_envelope(
                    "doctor",
                    summary=summary_block,
                    **envelope_kwargs,
                )
            )

        rendered = _run_check_dw("serialize_envelope", _serialize_envelope, default=None)
        # W978 #6: ``rendered is None`` guard before echo so a degraded
        # serialize_envelope does not crash on the print path. The
        # minimal hand-rolled fallback re-surfaces the markers + verdict
        # so consumers reading stdout see the disclosure.
        if rendered is None:
            import json as _json_fallback

            summary_block["partial_success"] = True
            summary_block["warnings_out"] = (
                list(_w607n_warnings_out) + list(_w607be_warnings_out) + list(_w607dw_warnings_out)
            )
            click.echo(
                _json_fallback.dumps(
                    {
                        "command": "doctor",
                        "summary": summary_block,
                        "warnings_out": summary_block["warnings_out"],
                    }
                )
            )
        else:
            click.echo(rendered)
        # Two-tier exit codes (Pattern-2 advisory-vs-blocker discipline):
        #   0 = clean OR advisory-only failures (state == "advisory_warnings")
        #   2 = blocking failures (state == "blocking_failures")
        # --strict promotes advisory to blocking. Advisory-only failures
        # MUST NOT exit non-zero on the default path — a fresh-install user
        # reading "exit 1" interprets it as "roam is broken" when the only
        # failure was a non-fatal warning. See Pattern-2 silent-fallback
        # discipline (CLAUDE.md): never emit a hard-failure signal on a
        # state the verdict explicitly labels advisory.
        if blocking_failed or (strict and advisory_failed):
            ctx.exit(2)
        return

    # --- Text output ---
    # W607-DW ``format_text`` substrate boundary: a raise during
    # click.echo formatting (e.g. a __str__ raise on a degraded check
    # entry, a missing key on a degraded ``clean_checks`` dict)
    # surfaces a marker rather than crashing.
    def _format_text():
        click.echo(f"VERDICT: {verdict}\n")
        for c in clean_checks:
            if c["passed"]:
                label = "PASS"
            elif c["name"] in _ADVISORY_CHECK_NAMES:
                label = "WARN"  # advisory failures get distinct visual weight
            else:
                label = "FAIL"
            click.echo(f"  [{label}] {c['detail']}")

        # One-line diagnostic, copy-pasteable into a GitHub issue or chat.
        click.echo()
        click.echo(f"  {issue_line}")

        if failed:
            click.echo()
            if blocking_failed and advisory_failed:
                click.echo(f"  {len(blocking_failed)} blocking, {len(advisory_failed)} advisory.")
            elif blocking_failed:
                click.echo(f"  {len(blocking_failed)} check{'s' if len(blocking_failed) != 1 else ''} failed.")
            else:
                note = " (use --strict to fail CI on advisory)" if not strict else ""
                click.echo(f"  {len(advisory_failed)} advisory check(s) — non-blocking{note}.")
        return None

    _run_check_dw("format_text", _format_text, default=None)

    # Two-tier exit codes — see JSON path above. Advisory-only failures
    # exit 0; only blocking failures or --strict-promoted advisories exit 2.
    if blocking_failed or (strict and advisory_failed):
        ctx.exit(2)


def _get_roam_version() -> str:
    """Best-effort roam-code version string for the issue-line summary."""
    try:
        import importlib.metadata as _md

        return _md.version("roam-code")
    except Exception:
        try:
            from roam import __version__

            return __version__
        except Exception:
            return "unknown"
