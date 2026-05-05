"""Setup diagnostics command — checks environment, dependencies, and index state.

Exit codes:
  0  All checks passed.
  1  One or more checks failed.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import click

from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def _check_python_version() -> dict:
    """Python >= 3.9 required."""
    vi = sys.version_info
    version_str = f"{vi.major}.{vi.minor}.{vi.micro}"
    passed = (vi.major, vi.minor) >= (3, 9)
    return {
        "name": "Python version",
        "passed": passed,
        "detail": f"Python {version_str} (>= 3.9 required)",
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
    except Exception:
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
    try:
        from roam.cli import _COMMANDS
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
    return {
        "name": "CLI command registry",
        "passed": True,
        "detail": f"{len(_COMMANDS)} CLI commands import cleanly",
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
    if full_count and full_count != len(declared):
        detail = f"{len(declared)} MCP tools registered ({preset} preset; {full_count} in full preset)"
    else:
        detail = f"{len(declared)} MCP tools registered"
    return {
        "name": "MCP tool registry",
        "passed": True,
        "detail": detail,
    }


def _check_plugin_discovery() -> dict:
    """Plugin discovery completed without errors (Pass 35).

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
    """Required tables are present in the index (Pass 35).

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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("doctor")
@click.pass_context
def doctor(ctx):
    """Diagnose environment setup: Python, dependencies, and index state.

    Unlike ``health`` (which analyzes codebase structural quality), this command
    validates the local environment: Python, dependencies, git, and index state.
    Checks each requirement and reports PASS or FAIL. Useful for onboarding
    new developers or troubleshooting agent setup issues.

    \b
    Exit codes:
      0  All checks passed.
      1  One or more checks failed.

    \b
    Examples:
      roam doctor
      roam --json doctor
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # --- Run all checks ---
    checks: list[dict] = []

    checks.append(_check_python_version())
    checks.append(_check_tree_sitter())
    checks.append(_check_tree_sitter_language_pack())
    checks.append(_check_git())
    checks.append(_check_networkx())
    # Round 4 S3 — verify the CLI + MCP tool surfaces are intact.
    checks.append(_check_command_registry())
    checks.append(_check_mcp_registry())
    checks.append(_check_mcp_backpressure())
    checks.append(_check_plugin_discovery())
    checks.append(_check_required_tables())

    # Index checks: existence feeds into freshness and SQLite checks
    index_check = _check_index_exists()
    checks.append(index_check)

    db_path_str = index_check.get("_db_path")
    checks.append(_check_index_freshness(db_path_str))
    checks.append(_check_sqlite(db_path_str))

    # --- Compute summary ---
    total = len(checks)
    failed = [c for c in checks if not c["passed"]]
    passed_count = total - len(failed)

    if not failed:
        verdict = f"all {total} checks passed"
    elif len(failed) == 1:
        verdict = f"1 check failed ({failed[0]['name']})"
    else:
        verdict = f"{len(failed)} checks failed"

    # Strip private keys (prefixed with _) before output
    clean_checks = [{k: v for k, v in c.items() if not k.startswith("_")} for c in checks]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "doctor",
                    summary={
                        "verdict": verdict,
                        "total": total,
                        "passed": passed_count,
                        "failed": len(failed),
                        "all_passed": len(failed) == 0,
                    },
                    checks=clean_checks,
                    failed_checks=[c for c in clean_checks if not c["passed"]],
                )
            )
        )
        if failed:
            from roam.exit_codes import EXIT_ERROR

            ctx.exit(EXIT_ERROR)
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}\n")
    for c in clean_checks:
        label = "PASS" if c["passed"] else "FAIL"
        click.echo(f"  [{label}] {c['detail']}")

    if failed:
        click.echo()
        click.echo(f"  {len(failed)} check{'s' if len(failed) != 1 else ''} failed.")
        from roam.exit_codes import EXIT_ERROR

        ctx.exit(EXIT_ERROR)
