#!/usr/bin/env python3
"""Check Python environment consistency for local development."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib


def _in_virtualenv() -> bool:
    return (
        getattr(sys, "real_prefix", None) is not None
        or sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or "VIRTUAL_ENV" in os.environ
    )


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _req_name(requirement: str) -> str | None:
    req_part = requirement
    marker = ""
    if ";" in requirement:
        req_part, marker = requirement.split(";", 1)
    # Skip optional extra-only dependencies from distribution metadata.
    if "extra ==" in marker.lower():
        return None
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", req_part)
    if not match:
        return None
    return _norm(match.group(1))


def _run_pip_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _project_roots(repo_root: Path, extras_mode: str) -> set[str]:
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.exists():
        return set()
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})

    roots: set[str] = set()
    name = project.get("name")
    if isinstance(name, str) and name.strip():
        roots.add(_norm(name))

    dependencies = project.get("dependencies", [])
    if isinstance(dependencies, list):
        for req in dependencies:
            if isinstance(req, str):
                parsed = _req_name(req)
                if parsed:
                    roots.add(parsed)

    include_optional: set[str] = set()
    if extras_mode == "all":
        include_optional = set(project.get("optional-dependencies", {}).keys())
    elif extras_mode != "core":
        include_optional = {item.strip() for item in extras_mode.split(",") if item.strip()}

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for extra_name, deps in optional.items():
            if extra_name not in include_optional:
                continue
            if not isinstance(deps, list):
                continue
            for req in deps:
                if isinstance(req, str):
                    parsed = _req_name(req)
                    if parsed:
                        roots.add(parsed)

    return roots


def _relevant_packages(repo_root: Path, extras_mode: str) -> set[str]:
    roots = _project_roots(repo_root, extras_mode=extras_mode)
    if not roots:
        return set()

    installed: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            installed[_norm(name)] = dist

    relevant: set[str] = set()
    queue = [name for name in roots if name in installed]
    while queue:
        current = queue.pop()
        if current in relevant:
            continue
        relevant.add(current)
        dist = installed.get(current)
        if dist is None:
            continue
        for requirement in dist.requires or []:
            req_name = _req_name(requirement)
            if req_name and req_name in installed and req_name not in relevant:
                queue.append(req_name)
    return relevant


@dataclass
class Conflict:
    line: str
    offender: str | None
    required: str | None
    provided: str | None
    relevant: bool


def _parse_conflict_line(line: str) -> tuple[str | None, str | None, str | None]:
    # Example:
    # fastapi 0.104.1 has requirement anyio<4.0.0,>=3.7.1, but you have anyio 4.12.0.
    match = re.match(
        r"^([A-Za-z0-9_.-]+)\s+[^ ]+\s+has requirement\s+(.+?),\s+but you have\s+([A-Za-z0-9_.-]+)\s+",
        line,
    )
    if not match:
        return None, None, None
    offender = _norm(match.group(1))
    required = _req_name(match.group(2))
    provided = _norm(match.group(3))
    return offender, required, provided


def _classify_conflicts(lines: list[str], relevant_pkgs: set[str]) -> tuple[list[Conflict], list[Conflict]]:
    relevant: list[Conflict] = []
    external: list[Conflict] = []
    for line in lines:
        offender, required, provided = _parse_conflict_line(line)
        is_relevant = any(pkg and pkg in relevant_pkgs for pkg in (offender, required, provided))
        conflict = Conflict(
            line=line,
            offender=offender,
            required=required,
            provided=provided,
            relevant=is_relevant,
        )
        if is_relevant:
            relevant.append(conflict)
        else:
            external.append(conflict)
    return relevant, external


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate development environment health.")
    parser.add_argument(
        "--require-venv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail when not running inside a virtual environment.",
    )
    parser.add_argument(
        "--strict-global",
        action="store_true",
        help="Fail on any pip conflict, even if unrelated to roam-code dependencies.",
    )
    parser.add_argument(
        "--extras",
        default="core",
        help="Dependency scope for relevance filtering: core, all, or comma-separated extra names.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    in_venv = _in_virtualenv()
    relevant_pkgs = _relevant_packages(repo_root, extras_mode=args.extras)

    payload: dict[str, object] = {
        "python": sys.executable,
        "version": sys.version.split()[0],
        "virtualenv": in_venv,
        "extras_mode": args.extras,
        "relevant_package_count": len(relevant_pkgs),
        "relevant_packages_sample": sorted(list(relevant_pkgs))[:20],
    }

    print(f"python: {sys.executable}")
    print(f"version: {sys.version.split()[0]}")
    print(f"virtualenv: {in_venv}")
    print(f"extras mode: {args.extras}")
    print(f"relevant package graph size: {len(relevant_pkgs)}")

    if args.require_venv and not in_venv:
        print("ERROR: not running inside a virtual environment.")
        print("Create one with: python -m venv .venv")
        print('Then install deps with: python -m pip install -e ".[dev,mcp,semantic]"')
        payload["status"] = "error"
        payload["reason"] = "missing_venv"
        if args.json:
            print(json.dumps(payload, indent=2))
        return 2

    result = _run_pip_check()
    raw_lines = [line for line in result.stdout.splitlines() if line.strip()]
    relevant_conflicts, external_conflicts = _classify_conflicts(raw_lines, relevant_pkgs)

    payload.update(
        {
            "pip_check_exit_code": result.returncode,
            "relevant_conflicts": [c.line for c in relevant_conflicts],
            "external_conflicts": [c.line for c in external_conflicts],
            "external_conflict_count": len(external_conflicts),
            "relevant_conflict_count": len(relevant_conflicts),
        }
    )

    if raw_lines:
        if relevant_conflicts:
            print("Relevant dependency conflicts:")
            for conflict in relevant_conflicts:
                print(conflict.line)
        if external_conflicts:
            print("External (non-project) dependency conflicts:")
            for conflict in external_conflicts:
                print(conflict.line)
    if result.stderr.strip():
        print(result.stderr.strip())

    if args.strict_global and result.returncode != 0:
        print("ERROR: dependency conflicts detected (strict global mode).")
        payload["status"] = "error"
        payload["reason"] = "global_conflicts"
        if args.json:
            print(json.dumps(payload, indent=2))
        return result.returncode

    if relevant_conflicts:
        print("ERROR: dependency conflicts detected.")
        payload["status"] = "error"
        payload["reason"] = "relevant_conflicts"
        if args.json:
            print(json.dumps(payload, indent=2))
        return 1

    if external_conflicts:
        print("Environment dependency check passed for roam-code graph.")
        print("Note: external conflicts exist in unrelated global packages.")
    else:
        print("Environment dependency check passed.")

    payload["status"] = "ok"
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
