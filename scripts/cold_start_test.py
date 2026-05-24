#!/usr/bin/env python3
"""Cold-start test harness for roam-code.

Exercises the canonical first-contact sequence
(``roam init && roam understand && roam health && roam brief``) against a
fixed list of public real-world repos covering Python / TypeScript / Go /
Ruby / Kotlin. The most embarrassing launch failure-mode is a fresh user
running ``pip install roam-code; roam init; roam understand`` and hitting
a traceback — this harness catches that class of regression before any
release tag.

Per ``internal/planning/COLD-START-TEST-PROTOCOL.md`` (workstream #5 in
``internal/planning/NEXT-PRIORITIES.md``). Stdlib-only by design; runs in
any Python 3.10+ environment with ``git`` and ``roam`` on PATH.

Usage
-----

    # Real run against the default 5-repo set:
    python3 scripts/cold_start_test.py

    # JSON envelope (LAW-6 verdict-first):
    python3 scripts/cold_start_test.py --json

    # Dry run — validate harness logic, do not clone or invoke roam:
    python3 scripts/cold_start_test.py --dry-run

    # Cache clones for re-runs (default uses a fresh tempdir each run):
    python3 scripts/cold_start_test.py --workspace /tmp/roam-coldtest

    # Tighter per-step timeout (default 300s):
    python3 scripts/cold_start_test.py --step-timeout 120

Exit code semantics: 0 on full pass (N == M), 1 if any repo failed. The
JSON envelope is emitted on stdout in both cases; stderr stays a separate
channel for per-step trace previews.

Authored as part of the polish-window cold-start gate (workstream #5).
Required gate before any public release tag.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

COMMAND_NAME = "cold-start-test"
SCHEMA_NAME = "roam.cold_start_test"
SCHEMA_VERSION = "1.0.0"

DEFAULT_REPOS: list[dict[str, str]] = [
    {
        "name": "requests",
        "url": "https://github.com/psf/requests.git",
        "language": "python",
        "ref": "main",
    },
    {
        "name": "TypeScript-Node-Starter",
        "url": "https://github.com/microsoft/TypeScript-Node-Starter.git",
        "language": "typescript",
        "ref": "master",
    },
    {
        "name": "hugo",
        "url": "https://github.com/gohugoio/hugo.git",
        "language": "go",
        "ref": "master",
    },
    {
        "name": "sinatra",
        "url": "https://github.com/sinatra/sinatra.git",
        "language": "ruby",
        "ref": "main",
    },
    {
        "name": "spring-petclinic-kotlin",
        "url": "https://github.com/spring-petclinic/spring-petclinic-kotlin.git",
        "language": "kotlin",
        "ref": "main",
    },
]

FIRST_CONTACT_STEPS: list[dict[str, object]] = [
    {"name": "init", "argv": ["roam", "init", "--yes"]},
    {"name": "understand", "argv": ["roam", "understand"]},
    {"name": "health", "argv": ["roam", "health"]},
    {"name": "brief", "argv": ["roam", "brief"]},
]

STDERR_TRUNCATE_CHARS = 4096
DEFAULT_STEP_TIMEOUT_S = 300
DEFAULT_CLONE_TIMEOUT_S = 600


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_envelope(
    summary: dict,
    per_repo: list[dict],
    started_at: str,
    finished_at: str,
    *,
    dry_run: bool = False,
) -> dict:
    return {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "command": COMMAND_NAME,
        "project": "roam-code",
        "summary": summary,
        "per_repo": per_repo,
        "_meta": {
            "timestamp": _now_iso(),
            "started_at": started_at,
            "finished_at": finished_at,
            "dry_run": dry_run,
        },
    }


def build_summary(
    total: int,
    passed: int,
    failed: int,
    *,
    dry_run: bool = False,
) -> dict:
    if dry_run:
        verdict = f"Dry-run validated {total} cold-start repos"
    elif total == 0:
        verdict = "No repos selected — nothing to test"
    else:
        verdict = f"{passed} of {total} cold-start repos passed"

    return {
        "verdict": verdict,
        "total": total,
        "passed": passed,
        "failed": failed,
        "partial_success": failed > 0,
        "verdict_definition": (
            "passed_repos = repos where all of init/understand/health/brief "
            "exit 0 within --step-timeout"
        ),
    }


def _truncate_text(s: str, limit: int = STDERR_TRUNCATE_CHARS) -> str:
    if len(s) <= limit:
        return s
    cut = limit - len("...[truncated]\n")
    return "...[truncated]\n" + s[-cut:]


def _run(
    argv: list[str],
    *,
    cwd: Path | None,
    timeout: int,
) -> dict:
    started = time.monotonic()
    result: dict[str, object] = {
        "argv": list(argv),
        "cwd": str(cwd) if cwd else None,
        "exit_code": None,
        "duration_s": 0.0,
        "timed_out": False,
        "stderr_tail": "",
        "error": None,
    }
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result["exit_code"] = proc.returncode
        result["stderr_tail"] = _truncate_text(proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        result["exit_code"] = -1
        stderr_raw = getattr(exc, "stderr", None) or b""
        if isinstance(stderr_raw, bytes):
            try:
                stderr_raw = stderr_raw.decode("utf-8", errors="replace")
            except Exception:
                stderr_raw = ""
        result["stderr_tail"] = _truncate_text(
            stderr_raw + f"\n[timeout after {timeout}s]"
        )
        result["error"] = f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        result["exit_code"] = -2
        result["error"] = f"binary not found: {exc.filename or argv[0]}"
    except Exception as exc:
        result["exit_code"] = -3
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["duration_s"] = round(time.monotonic() - started, 3)
    return result


def _clone_repo(
    repo: dict,
    workspace: Path,
    *,
    clone_timeout: int,
) -> tuple[Path, dict]:
    checkout = workspace / repo["name"]
    if checkout.exists() and (checkout / ".git").exists():
        return checkout, {
            "argv": ["git", "clone", "(cached)"],
            "cwd": str(workspace),
            "exit_code": 0,
            "duration_s": 0.0,
            "timed_out": False,
            "stderr_tail": "",
            "error": None,
            "cached": True,
        }
    argv = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
    ]
    ref = repo.get("ref")
    if ref:
        argv.extend(["--branch", str(ref)])
    argv.extend([repo["url"], str(checkout)])
    step = _run(argv, cwd=workspace, timeout=clone_timeout)
    step["cached"] = False
    return checkout, step


def _run_first_contact(
    checkout: Path,
    *,
    step_timeout: int,
) -> list[dict]:
    results: list[dict] = []
    for step_spec in FIRST_CONTACT_STEPS:
        step = _run(
            list(step_spec["argv"]),  # type: ignore[arg-type]
            cwd=checkout,
            timeout=step_timeout,
        )
        step["step"] = step_spec["name"]
        results.append(step)
        if step["exit_code"] != 0:
            break
    return results


def _per_repo_verdict(steps: list[dict], clone_step: dict) -> str:
    if clone_step.get("exit_code") not in (0, None):
        return "FAIL"
    if not steps:
        return "FAIL"
    for step in steps:
        if step.get("exit_code") != 0:
            return "FAIL"
    return "PASS" if len(steps) == len(FIRST_CONTACT_STEPS) else "FAIL"


def _failed_step_name(
    clone_step: dict,
    steps: list[dict],
) -> str | None:
    if clone_step.get("exit_code") not in (0, None):
        return "clone"
    for step in steps:
        if step.get("exit_code") != 0:
            return str(step.get("step") or step.get("argv", ["?"])[0])
    return None


def test_one_repo(
    repo: dict,
    workspace: Path,
    *,
    clone_timeout: int,
    step_timeout: int,
) -> dict:
    started = _now_iso()
    checkout, clone_step = _clone_repo(
        repo, workspace, clone_timeout=clone_timeout
    )
    if clone_step.get("exit_code") not in (0, None):
        return {
            "name": repo["name"],
            "url": repo["url"],
            "language": repo["language"],
            "verdict": "FAIL",
            "failed_step": "clone",
            "step_results": [clone_step],
            "started_at": started,
            "finished_at": _now_iso(),
        }
    steps = _run_first_contact(checkout, step_timeout=step_timeout)
    return {
        "name": repo["name"],
        "url": repo["url"],
        "language": repo["language"],
        "verdict": _per_repo_verdict(steps, clone_step),
        "failed_step": _failed_step_name(clone_step, steps),
        "step_results": [clone_step, *steps],
        "started_at": started,
        "finished_at": _now_iso(),
    }


def dry_run_one_repo(repo: dict) -> dict:
    started = _now_iso()
    step_results: list[dict] = [
        {
            "argv": [
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                str(repo.get("ref", "")),
                repo["url"],
                f"<workspace>/{repo['name']}",
            ],
            "cwd": "<workspace>",
            "exit_code": None,
            "duration_s": 0.0,
            "timed_out": False,
            "stderr_tail": "",
            "error": None,
            "cached": False,
            "dry_run": True,
        },
    ]
    for step_spec in FIRST_CONTACT_STEPS:
        step_results.append(
            {
                "step": step_spec["name"],
                "argv": list(step_spec["argv"]),  # type: ignore[arg-type]
                "cwd": f"<workspace>/{repo['name']}",
                "exit_code": None,
                "duration_s": 0.0,
                "timed_out": False,
                "stderr_tail": "",
                "error": None,
                "dry_run": True,
            }
        )
    return {
        "name": repo["name"],
        "url": repo["url"],
        "language": repo["language"],
        "verdict": "DRY-RUN",
        "failed_step": None,
        "step_results": step_results,
        "started_at": started,
        "finished_at": _now_iso(),
    }


def render_text(envelope: dict) -> str:
    summary = envelope["summary"]
    lines: list[str] = []
    lines.append(f"VERDICT: {summary['verdict']}")
    lines.append("")

    col_name = 26
    col_lang = 12
    col_verd = 9
    col_step = 14
    lines.append(
        f"{'REPO'.ljust(col_name)}"
        f"{'LANGUAGE'.ljust(col_lang)}"
        f"{'VERDICT'.ljust(col_verd)}"
        f"{'FAILED-STEP'.ljust(col_step)}"
        "DURATION"
    )
    lines.append("-" * (col_name + col_lang + col_verd + col_step + 10))
    for entry in envelope["per_repo"]:
        durations = [
            step.get("duration_s", 0.0) or 0.0
            for step in entry.get("step_results", [])
        ]
        total_s = round(sum(durations), 1)
        lines.append(
            f"{entry['name'][:col_name - 1].ljust(col_name)}"
            f"{entry['language'].ljust(col_lang)}"
            f"{entry['verdict'].ljust(col_verd)}"
            f"{str(entry.get('failed_step') or '-').ljust(col_step)}"
            f"{total_s}s"
        )

    lines.append("")
    lines.append(
        f"Total: {summary['total']}  Passed: {summary['passed']}  "
        f"Failed: {summary['failed']}"
    )
    if envelope["_meta"].get("dry_run"):
        lines.append("(dry-run — no repos were cloned, no roam invocations)")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cold_start_test.py",
        description=(
            "Run roam-code's canonical first-contact sequence against a "
            "fixed list of public repos and report a pass/fail verdict. "
            "Required gate before any public release tag."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full JSON envelope on stdout (default: plain text).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate harness logic without cloning or invoking roam. "
            "Exits 0 with a DRY-RUN per-repo verdict."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help=(
            "Pre-existing workspace directory to use for clones (enables "
            "caching across runs). Default: a fresh tempdir under "
            "TMPDIR, removed at exit."
        ),
    )
    parser.add_argument(
        "--step-timeout",
        type=int,
        default=DEFAULT_STEP_TIMEOUT_S,
        help=(
            f"Per-step timeout in seconds (default: "
            f"{DEFAULT_STEP_TIMEOUT_S})."
        ),
    )
    parser.add_argument(
        "--clone-timeout",
        type=int,
        default=DEFAULT_CLONE_TIMEOUT_S,
        help=(
            f"Per-clone timeout in seconds (default: "
            f"{DEFAULT_CLONE_TIMEOUT_S})."
        ),
    )
    parser.add_argument(
        "--repos-file",
        type=Path,
        default=None,
        help=(
            "Optional JSON file with a list of "
            "{name, url, language, ref?} entries to override the default "
            "5-repo set. YAML is intentionally NOT supported (stdlib-only)."
        ),
    )
    return parser.parse_args(argv)


def _load_repos(path: Path | None) -> list[dict[str, str]]:
    if not path:
        return list(DEFAULT_REPOS)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"WARNING: --repos-file {path} unreadable ({exc}); "
            f"falling back to defaults.",
            file=sys.stderr,
        )
        return list(DEFAULT_REPOS)
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"WARNING: --repos-file {path} is not valid JSON ({exc}); "
            f"falling back to defaults.",
            file=sys.stderr,
        )
        return list(DEFAULT_REPOS)
    if not isinstance(loaded, list) or not all(
        isinstance(entry, dict) and "name" in entry and "url" in entry
        for entry in loaded
    ):
        print(
            f"WARNING: --repos-file {path} must be a JSON list of "
            f"{{name, url, language, ref?}} entries; "
            f"falling back to defaults.",
            file=sys.stderr,
        )
        return list(DEFAULT_REPOS)
    return [
        {
            "name": str(entry["name"]),
            "url": str(entry["url"]),
            "language": str(entry.get("language", "unknown")),
            "ref": str(entry.get("ref", "")) if entry.get("ref") else "",
        }
        for entry in loaded
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repos = _load_repos(args.repos_file)
    started_at = _now_iso()

    if args.dry_run:
        per_repo = [dry_run_one_repo(repo) for repo in repos]
        summary = build_summary(
            total=len(repos),
            passed=len(repos),
            failed=0,
            dry_run=True,
        )
        envelope = build_envelope(
            summary,
            per_repo,
            started_at=started_at,
            finished_at=_now_iso(),
            dry_run=True,
        )
        if args.json:
            sys.stdout.write(json.dumps(envelope, indent=2) + "\n")
        else:
            sys.stdout.write(render_text(envelope))
        return 0

    if args.workspace:
        workspace = args.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        owns_workspace = False
    else:
        tmp = tempfile.mkdtemp(prefix="roam-coldtest-")
        workspace = Path(tmp)
        owns_workspace = True

    per_repo: list[dict] = []
    try:
        for repo in repos:
            entry = test_one_repo(
                repo,
                workspace,
                clone_timeout=args.clone_timeout,
                step_timeout=args.step_timeout,
            )
            per_repo.append(entry)
    finally:
        if owns_workspace and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    passed = sum(1 for entry in per_repo if entry["verdict"] == "PASS")
    failed = len(per_repo) - passed
    summary = build_summary(
        total=len(per_repo),
        passed=passed,
        failed=failed,
        dry_run=False,
    )
    envelope = build_envelope(
        summary,
        per_repo,
        started_at=started_at,
        finished_at=_now_iso(),
        dry_run=False,
    )

    if args.json:
        sys.stdout.write(json.dumps(envelope, indent=2) + "\n")
    else:
        sys.stdout.write(render_text(envelope))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    os.environ.setdefault("ROAM_NONINTERACTIVE", "1")
    raise SystemExit(main())
