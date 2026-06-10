"""Scan src/roam/ for camelCase function definitions and emit a baseline JSON.

Run from repo root:

    python scripts/scan_snake_case_violations.py > tests/data/snake_case_baseline.json

Used to seed and re-seed the lint baseline consumed by
tests/test_snake_case_function_lint.py.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
from typing import Iterable

_CAMEL_RE = re.compile(r"[a-z][A-Z]")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_test_helper(name: str) -> bool:
    return name.startswith("_test_")


def _is_snake_case(name: str) -> bool:
    # Pure snake_case: all lowercase ASCII letters, digits, and underscores,
    # AND no camelCase boundary (no lower->Upper transition anywhere).
    return _CAMEL_RE.search(name) is None


def _should_skip(name: str) -> bool:
    return _is_dunder(name) or _is_test_helper(name) or _is_snake_case(name)


def scan_file(path: pathlib.Path, repo_root: pathlib.Path) -> list[dict[str, object]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    findings: list[dict[str, object]] = []
    rel = path.relative_to(repo_root).as_posix()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            if _should_skip(name):
                continue
            findings.append(
                {
                    "file": rel,
                    "function": name,
                    "line": int(node.lineno),
                }
            )
    return findings


def iter_python_files(src_root: pathlib.Path) -> Iterable[pathlib.Path]:
    for path in sorted(src_root.rglob("*.py")):
        yield path


def scan(repo_root: pathlib.Path) -> list[dict[str, object]]:
    src_root = repo_root / "src" / "roam"
    findings: list[dict[str, object]] = []
    for path in iter_python_files(src_root):
        findings.extend(scan_file(path, repo_root))
    # Sort deterministically: file, then line, then function name.
    findings.sort(key=lambda r: (r["file"], r["line"], r["function"]))
    return findings


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    findings = scan(repo_root)
    json.dump(findings, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
