"""Run labeled idiom detectors against external repositories for blind review.

Start every stranger-repository sweep with ``--self-check``. The positive-control
canary indexes the in-repository Flask fixture and proves that the detector
instrument can see its known ``debug=True`` finding.

This deterministic dev/QA script invokes the public CLI for buyer-path indexing
and calls the catalog idiom detectors in-process, following the project's
precision tests. It never calls an LLM or emits telemetry. Network access is
limited to shallow ``git clone`` operations for URL inputs. Index databases and
clones live in a temporary workdir; the only persistent writes are ``--out`` and
``--prompt-out``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from roam.catalog import python_idioms
from roam.db.connection import open_db

DEFAULT_DETECTORS = (
    "detect_django_n1",
    "detect_sqlalchemy_lazy",
    "detect_fastapi_depends",
    "detect_flask_debug_true",
)

SELF_CHECK_FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "detector_eval" / "flask"


def repo_short_name(repo: str) -> str:
    """Return a stable, ID-safe short name for a repository input."""
    stem = repo.rstrip("/").rsplit("/", 1)[-1]
    if ":" in stem:
        stem = stem.rsplit(":", 1)[-1]
    if stem.endswith(".git"):
        stem = stem[:-4]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-.") or "repo"


def _parse_location(finding: dict[str, Any]) -> tuple[str, int]:
    file_value = finding.get("file") or finding.get("path") or ""
    line_value = finding.get("line") or finding.get("line_number") or 0
    location = finding.get("location")
    if location and isinstance(location, str):
        location_file, separator, location_line = location.rpartition(":")
        if separator and location_line.isdigit():
            file_value = file_value or location_file
            line_value = line_value or int(location_line)
    try:
        line = int(line_value)
    except (TypeError, ValueError):
        line = 0
    return str(file_value), line


def _source_context(repo_path: Path, file_name: str, line: int) -> str:
    if not file_name:
        return ""
    path = Path(file_name)
    if not path.is_absolute():
        path = repo_path / path
    try:
        resolved = path.resolve()
        resolved.relative_to(repo_path.resolve())
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, ValueError):
        return ""
    if not lines:
        return ""
    target = max(1, line)
    start = max(1, target - 3)
    end = min(len(lines), target + 3)
    return "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))


def normalize_findings(
    payload: Any,
    *,
    repo: str,
    detector: str,
    repo_path: Path,
    max_findings: int = 25,
) -> list[dict[str, Any]]:
    """Normalize raw detector findings; malformed results become error rows."""
    findings = payload.get("findings") if isinstance(payload, dict) else payload
    if not isinstance(findings, list):
        return [{"repo": repo, "detector": detector, "error": "malformed detector JSON: missing findings list"}]

    parsed: list[tuple[str, int, str]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            return [{"repo": repo, "detector": detector, "error": "malformed detector JSON: invalid finding"}]
        file_name, line = _parse_location(finding)
        message = finding.get("message") or finding.get("reason") or finding.get("description") or ""
        parsed.append((file_name, line, str(message)))

    parsed.sort(key=lambda item: (item[0], item[1], item[2]))
    short = repo_short_name(repo)
    rows = []
    for number, (file_name, line, message) in enumerate(parsed[:max_findings], 1):
        rows.append(
            {
                "id": f"{short}-{detector}-{number}",
                "repo": repo,
                "detector": detector,
                "file": file_name,
                "line": line,
                "message": message,
                "context": _source_context(repo_path, file_name, line),
            }
        )
    return rows


def generate_prompt(findings: Iterable[dict[str, Any]]) -> str:
    """Build a detector-blind judging rubric for normalized findings."""
    rows = [row for row in findings if "id" in row]
    lines = [
        "Review each candidate issue using only its description and source context.",
        "Judge whether it identifies a real production concern at the stated location.",
        "A finding in test or vendored code that poses no production concern counts as false_positive.",
        "If the supplied evidence is insufficient, mark it unjudgeable.",
        "Return one line per finding, in the same order, using exactly one of these formats:",
        "ID: true_positive",
        "ID: false_positive",
        "ID: unjudgeable",
        "Do not add explanations or any other text.",
        "",
    ]
    for row in rows:
        description = f"{row.get('file', '')}:{row.get('line', 0)} — {row.get('message', '')}"
        context = row.get("context") or "[source context unavailable]"
        lines.extend((f"ID: {row['id']}", description, str(context), ""))
    return "\n".join(lines).rstrip() + "\n"


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def _error_text(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"exit {result.returncode}: {detail}" if detail else f"exit {result.returncode}"


def _materialize_repo(repo: str, workdir: Path) -> Path:
    local = Path(repo).expanduser()
    if local.exists():
        if not local.is_dir():
            raise ValueError("local repository path is not a directory")
        local = local.resolve()
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=local,
            text=True,
            capture_output=True,
            check=False,
        )
        if root_result.returncode == 0 and Path(root_result.stdout.strip()).resolve() == local:
            return local
        destination = workdir / "local" / repo_short_name(repo)
        shutil.copytree(local, destination)
        for command in (
            ["git", "init", "-q"],
            ["git", "add", "."],
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture"],
        ):
            result = subprocess.run(command, cwd=destination, text=True, capture_output=True, check=False)
            if result.returncode:
                raise RuntimeError(f"local repository staging failed: {_error_text(result)}")
        return destination
    destination = workdir / "clones" / repo_short_name(repo)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo, str(destination)], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(_error_text(result))
    return destination


def _detector_callable(name: str):
    detector = getattr(python_idioms, name, None)
    if name not in DEFAULT_DETECTORS or not callable(detector):
        return None
    return detector


@contextmanager
def _detector_context(repo_path: Path, db_dir: Path):
    """Point direct detector calls at the subprocess-built index and sources."""
    previous_cwd = Path.cwd()
    previous_db_dir = os.environ.get("ROAM_DB_DIR")
    os.chdir(repo_path)
    os.environ["ROAM_DB_DIR"] = str(db_dir)
    python_idioms.set_idiom_scope(None)
    python_idioms._clear_file_text_cache()
    try:
        yield
    finally:
        python_idioms.set_idiom_scope(None)
        python_idioms._clear_file_text_cache()
        os.chdir(previous_cwd)
        if previous_db_dir is None:
            os.environ.pop("ROAM_DB_DIR", None)
        else:
            os.environ["ROAM_DB_DIR"] = previous_db_dir


def _run_detector(name: str, repo_path: Path, db_dir: Path) -> list[dict[str, Any]]:
    detector = _detector_callable(name)
    if detector is None:
        raise ValueError(f"unknown catalog idiom detector: {name}")
    with _detector_context(repo_path, db_dir), open_db(readonly=False) as conn:
        return detector(conn)


def _link_indexed_sources(repo_path: Path, db_dir: Path) -> None:
    """Expose project-relative sources beside an externally located index."""
    with sqlite3.connect(db_dir / "index.db") as conn:
        paths = [row[0] for row in conn.execute("SELECT path FROM files")]
    for file_name in paths:
        relative = Path(file_name)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        source = repo_path / relative
        destination = db_dir / relative
        if not source.is_file() or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.symlink_to(source)
        except OSError:
            # Windows denies symlinks without elevation (WinError 1314);
            # a copy is semantically identical for read-only context lookup.
            shutil.copy2(source, destination)


def run_harness(
    repos: list[str], detectors: list[str], workdir: Path, max_findings: int
) -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    successful_repos: set[str] = set()
    for repo_number, repo in enumerate(repos, 1):
        try:
            repo_path = _materialize_repo(repo, workdir)
        except (OSError, ValueError, RuntimeError) as exc:
            rows.extend(
                {"repo": repo, "detector": detector, "error": f"repository setup failed: {exc}"}
                for detector in detectors
            )
            continue

        db_dir = workdir / "indexes" / f"{repo_number}-{repo_short_name(repo)}"
        env = os.environ.copy()
        env["ROAM_DB_DIR"] = str(db_dir)
        env["ROAM_TELEMETRY_LOCAL"] = "0"
        index_result = _run([sys.executable, "-m", "roam", "index"], cwd=repo_path, env=env)
        if index_result.returncode:
            error = f"index failed: {_error_text(index_result)}"
            rows.extend({"repo": repo, "detector": detector, "error": error} for detector in detectors)
            continue
        _link_indexed_sources(repo_path, db_dir)

        repo_had_success = False
        for detector in detectors:
            try:
                findings = _run_detector(detector, repo_path, db_dir)
            except (OSError, RuntimeError, ValueError) as exc:
                rows.append({"repo": repo, "detector": detector, "error": f"detector failed: {exc}"})
                continue
            normalized = normalize_findings(
                findings, repo=repo, detector=detector, repo_path=repo_path, max_findings=max_findings
            )
            rows.extend(normalized)
            if not normalized or "error" not in normalized[0]:
                repo_had_success = True
        if repo_had_success:
            successful_repos.add(repo)
    return rows, successful_repos


def _write_outputs(rows: list[dict[str, Any]], out: Path, prompt_out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt_out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    prompt_out.write_text(generate_prompt(rows), encoding="utf-8")


def _print_summary(rows: list[dict[str, Any]], repos: list[str], detectors: list[str], out: Path, prompt: Path) -> None:
    counts = Counter((row.get("repo"), row.get("detector")) for row in rows if "id" in row)
    errors = {(row.get("repo"), row.get("detector")) for row in rows if "error" in row}
    print("Stranger-repo precision sweep")
    for repo in repos:
        print(f"  {repo}")
        for detector in detectors:
            suffix = " (error recorded)" if (repo, detector) in errors else ""
            print(f"    {detector}: {counts[(repo, detector)]} findings{suffix}")
    print(f"Findings: {out}")
    print(f"Judging prompt: {prompt}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", help="Local path or git URL; repeatable")
    parser.add_argument("--detector", action="append")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--prompt-out", type=Path)
    parser.add_argument("--max-findings-per-detector", type=int, default=25)
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--self-check", action="store_true", help="Run the known-positive Flask canary and exit")
    args = parser.parse_args(argv)
    if args.max_findings_per_detector < 1:
        parser.error("--max-findings-per-detector must be at least 1")
    if not args.self_check and (not args.repo or args.out is None or args.prompt_out is None):
        parser.error("--repo, --out, and --prompt-out are required unless --self-check is used")
    return args


def _self_check(workdir: Path) -> bool:
    rows, _successful = run_harness(
        [str(SELF_CHECK_FIXTURE)], ["detect_flask_debug_true"], workdir, max_findings=25
    )
    return any(row.get("detector") == "detect_flask_debug_true" and "id" in row for row in rows)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        with tempfile.TemporaryDirectory(prefix="stranger-precision-self-check-") as temporary:
            if not _self_check(Path(temporary)):
                print(
                    "SELF-CHECK FAILED: detect_flask_debug_true returned zero findings for the known-positive Flask fixture",
                    file=sys.stderr,
                )
                return 2
        print("SELF-CHECK PASSED: known-positive Flask finding detected")
        return 0
    detectors = args.detector or list(DEFAULT_DETECTORS)
    if args.keep_workdir:
        workdir = Path(tempfile.mkdtemp(prefix="stranger-precision-"))
        print(f"Keeping workdir: {workdir}")
        rows, successful = run_harness(args.repo, detectors, workdir, args.max_findings_per_detector)
    else:
        with tempfile.TemporaryDirectory(prefix="stranger-precision-") as temporary:
            rows, successful = run_harness(args.repo, detectors, Path(temporary), args.max_findings_per_detector)
    _write_outputs(rows, args.out, args.prompt_out)
    _print_summary(rows, args.repo, detectors, args.out, args.prompt_out)
    return 0 if successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
