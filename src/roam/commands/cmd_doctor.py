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

from roam.output.formatter import to_json, json_envelope


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


def _check_tree_sitter() -> dict:
    """tree-sitter package importable."""
    try:
        import tree_sitter
        version = getattr(tree_sitter, "__version__", "unknown")
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
        import tree_sitter_language_pack
        version = getattr(tree_sitter_language_pack, "__version__", "unknown")
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
        "detail": (
            f"fresh ({age_str})"
            if not stale
            else f"stale ({age_str}, run `roam index` to refresh)"
        ),
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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("doctor")
@click.pass_context
def doctor(ctx):
    """Diagnose environment setup: Python, dependencies, index state, disk space.

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
    clean_checks = [
        {k: v for k, v in c.items() if not k.startswith("_")}
        for c in checks
    ]

    if json_mode:
        click.echo(to_json(json_envelope("doctor",
            summary={
                "verdict": verdict,
                "total": total,
                "passed": passed_count,
                "failed": len(failed),
                "all_passed": len(failed) == 0,
            },
            checks=clean_checks,
            failed_checks=[c for c in clean_checks if not c["passed"]],
        )))
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
