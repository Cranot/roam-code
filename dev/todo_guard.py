#!/usr/bin/env python3
"""Enforce TODO/FIXME/HACK comment format in Python sources."""

from __future__ import annotations

import argparse
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

PREFIX_RE = r"^\s*#\s*(TODO|FIXME|HACK)\b"
REQUIRED_FORMAT_RE = r"^\s*#\s*(TODO|FIXME|HACK)\([A-Za-z0-9_.-]+,\d{4}-\d{2}-\d{2}\):\s+\S+"


@dataclass
class Violation:
    path: Path
    line: int
    comment: str


def _iter_python_files(root: Path, include_tests: bool) -> list[Path]:
    files = sorted(root.glob("src/**/*.py"))
    if include_tests:
        files.extend(sorted(root.glob("tests/**/*.py")))
    return files


def _collect_violations(path: Path) -> list[Violation]:
    prefix = re.compile(PREFIX_RE)
    required = re.compile(REQUIRED_FORMAT_RE)
    violations: list[Violation] = []

    content = path.read_text(encoding="utf-8")
    stream = io.StringIO(content)
    for token in tokenize.generate_tokens(stream.readline):
        if token.type != tokenize.COMMENT:
            continue
        text = token.string
        if not prefix.match(text):
            continue
        if required.match(text):
            continue
        violations.append(Violation(path=path, line=token.start[0], comment=text.strip()))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate TODO/FIXME/HACK comment format.")
    parser.add_argument("--include-tests", action="store_true", help="Scan tests/ as well as src/.")
    parser.add_argument(
        "--required-format",
        default="# TODO(owner,YYYY-MM-DD): description",
        help="Displayed in failure output for guidance.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    files = _iter_python_files(repo_root, include_tests=args.include_tests)

    violations: list[Violation] = []
    for path in files:
        violations.extend(_collect_violations(path))

    if not violations:
        print("TODO guard: no violations found.")
        return 0

    print("TODO guard violations:")
    for violation in violations:
        rel = violation.path.relative_to(repo_root).as_posix()
        print(f"- {rel}:{violation.line} {violation.comment}")
    print(f"Required format: {args.required_format}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
