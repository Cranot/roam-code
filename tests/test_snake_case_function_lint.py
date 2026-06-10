"""Baseline-enumerating lint for the AGENTS.md 'Functions: snake_case (100%)' rule.

Scans `src/roam/**/*.py` for camelCase function definitions and compares the
findings to a frozen baseline at `tests/data/snake_case_baseline.json`.

Behavior:
  - New violations vs. baseline -> FAIL with the list of new violations.
  - Removed violations vs. baseline -> FAIL ("great, please remove from
    baseline") so the baseline shrinks monotonically.
  - Exact match -> PASS.

The scanner is also shipped as a standalone helper at
`scripts/scan_snake_case_violations.py`; regenerate the baseline with:

    python scripts/scan_snake_case_violations.py > tests/data/snake_case_baseline.json
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

from tests._helpers.repo_root import repo_root

_CAMEL_RE = re.compile(r"[a-z][A-Z]")

_SKIP_TOP_LEVEL_DIRS = ("tests", "dev", "internal", "templates")


def _repo_root() -> pathlib.Path:
    return repo_root()


def _baseline_path() -> pathlib.Path:
    return _repo_root() / "tests" / "data" / "snake_case_baseline.json"


def _src_root() -> pathlib.Path:
    return _repo_root() / "src" / "roam"


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_test_helper(name: str) -> bool:
    return name.startswith("_test_")


def _is_snake_case(name: str) -> bool:
    return _CAMEL_RE.search(name) is None


def _should_skip(name: str) -> bool:
    return _is_dunder(name) or _is_test_helper(name) or _is_snake_case(name)


def _is_under_skipped_dir(path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    try:
        rel_parts = path.relative_to(repo_root).parts
    except ValueError:
        return True
    return bool(rel_parts) and rel_parts[0] in _SKIP_TOP_LEVEL_DIRS


def _scan_file(path: pathlib.Path, repo_root: pathlib.Path) -> list[dict[str, object]]:
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


def _scan_repo() -> list[dict[str, object]]:
    repo_root = _repo_root()
    src_root = _src_root()
    findings: list[dict[str, object]] = []
    for path in sorted(src_root.rglob("*.py")):
        if _is_under_skipped_dir(path, repo_root):
            continue
        findings.extend(_scan_file(path, repo_root))
    findings.sort(key=lambda r: (r["file"], r["line"], r["function"]))
    return findings


def _as_key(entry: dict[str, object]) -> tuple[str, str, int]:
    return (str(entry["file"]), str(entry["function"]), int(entry["line"]))


def test_snake_case_function_lint_matches_baseline() -> None:
    baseline_path = _baseline_path()
    assert baseline_path.exists(), (
        f"Baseline file missing at {baseline_path}. "
        "Regenerate with: python scripts/scan_snake_case_violations.py "
        "> tests/data/snake_case_baseline.json"
    )

    baseline_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert isinstance(baseline_raw, list), "Baseline must be a JSON list"

    current = _scan_repo()
    print(f"\n[snake_case_lint] camelCase function definitions in src/roam: {len(current)}")

    current_keys = {_as_key(e) for e in current}
    baseline_keys = {_as_key(e) for e in baseline_raw}

    new_violations = sorted(current_keys - baseline_keys)
    removed_violations = sorted(baseline_keys - current_keys)

    messages: list[str] = []
    if new_violations:
        messages.append(
            "NEW camelCase function definitions detected (AGENTS.md mandates "
            "snake_case for functions). Rename to snake_case, or — if "
            "intentional and unavoidable — add to the baseline:\n"
            + "\n".join(f"  {f}:{ln}  {fn}" for (f, fn, ln) in new_violations)
        )
    if removed_violations:
        messages.append(
            "Baseline entries no longer present (great — please remove them "
            "from tests/data/snake_case_baseline.json so the baseline shrinks "
            "monotonically):\n" + "\n".join(f"  {f}:{ln}  {fn}" for (f, fn, ln) in removed_violations)
        )

    assert not messages, "\n\n".join(messages)
