"""Scan src/roam/ for camelCase function definitions and emit a baseline JSON.

Run from repo root:

    python scripts/scan_snake_case_violations.py > tests/data/snake_case_baseline.json

Used to seed and re-seed the lint baseline consumed by
tests/test_snake_case_function_lint.py.

THE SCANNER FAILS CLOSED (W1455)
--------------------------------
Every file gets one of THREE outcomes — ``clean``, ``violation``, or
``unanalyzable`` — never the two-valued ``[] means fine`` this scanner
shipped with. A file that could not be read or parsed used to return ``[]``,
i.e. it was indistinguishable from a file with no camelCase functions, so a
syntax error silently removed a module from the lint while the baseline
comparison still passed.

The report therefore publishes its DENOMINATOR (``files_parsed``,
``files_unanalyzable``, ``files_skipped``), exits non-zero when anything is
unanalyzable, and runs a POSITIVE CONTROL on every invocation against
``tests/fixtures/scanner_positive_controls/snake_case``: a planted
``sentinelCamelCase`` that must be found, beside snake_case and dunder names
that must not be. Without it, "0 findings" and "the detector stopped
working" are the same output.

STDOUT stays the bare findings list so the redirect above still regenerates
the baseline; the denominator and control verdict go to STDERR.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import re
import sys
from typing import Iterable

_CAMEL_RE = re.compile(r"[a-z][A-Z]")

#: A file was read, parsed, and found free of camelCase definitions.
CLEAN = "clean"
#: A file was read, parsed, and found to contain camelCase definitions.
VIOLATION = "violation"
#: A file could NOT be read or parsed. This is the third value the scanner
#: used to collapse into ``[]`` — i.e. into CLEAN.
UNANALYZABLE = "unanalyzable"
#: Deliberately not covered, recorded WITH a reason.
SKIPPED = "skipped"

EXIT_OK = 0
EXIT_UNANALYZABLE = 2
EXIT_SCANNER_BROKEN = 3

#: Trees under ``src/roam`` the lint does not cover. Recorded as skipped so
#: the exclusion is a number in the report, not an invisible hole.
SKIP_DIRS: tuple[str, ...] = ("templates",)

POSITIVE_CONTROL_DIR = pathlib.PurePosixPath("tests/fixtures/scanner_positive_controls/snake_case")

#: The sentinel: the planted camelCase name the scanner must find on every run.
POSITIVE_CONTROL_SENTINEL = "sentinelCamelCase"
#: The other half of the control: names in the same fixture that must NOT be
#: reported, so an "always fire" regression cannot satisfy the check.
POSITIVE_CONTROL_SILENT: tuple[str, ...] = ("sentinel_snake_case", "__sentinel_dunder__")


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


@dataclasses.dataclass(frozen=True)
class FileResult:
    """The outcome of ONE file. ``status`` is never inferred from emptiness."""

    path: str
    status: str
    findings: list[dict[str, object]] = dataclasses.field(default_factory=list)
    reason: str | None = None

    def as_record(self) -> dict[str, object]:
        return {"path": self.path, "status": self.status, "reason": self.reason}


@dataclasses.dataclass(frozen=True)
class ControlResult:
    """Did the detector detect its own planted defect on this invocation?"""

    status: str  # "ok" | "broken"
    detail: str
    fixture: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_record(self) -> dict[str, object]:
        return {"status": self.status, "detail": self.detail, "fixture": self.fixture}


@dataclasses.dataclass(frozen=True)
class ScanReport:
    """Findings AND the denominator they were counted against."""

    findings: list[dict[str, object]]
    files_parsed: int
    files_unanalyzable: int
    files_skipped: int
    unanalyzable: list[dict[str, object]]
    skipped: list[dict[str, object]]
    positive_control: ControlResult
    root: str

    @property
    def ok(self) -> bool:
        return self.files_unanalyzable == 0 and self.positive_control.ok

    def summary(self) -> str:
        """The disclosure is the COUNT, never the silence."""
        return (
            f"checked {self.files_parsed} files, "
            f"{len(self.findings)} violations, "
            f"{self.files_unanalyzable} unanalyzable, "
            f"positive control {'OK' if self.positive_control.ok else 'BROKEN'}"
        )

    def as_record(self) -> dict[str, object]:
        return {
            "root": self.root,
            "findings": self.findings,
            "files_parsed": self.files_parsed,
            "files_unanalyzable": self.files_unanalyzable,
            "files_skipped": self.files_skipped,
            "unanalyzable": self.unanalyzable,
            "skipped": self.skipped,
            "positive_control": self.positive_control.as_record(),
            "summary": self.summary(),
        }


def _relative(path: pathlib.Path, repo_root: pathlib.Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def scan_file(path: pathlib.Path, repo_root: pathlib.Path) -> FileResult:
    """Analyse one file. NEVER returns an empty result for a file it failed on.

    Every exception is caught PER FILE and recorded as ``UNANALYZABLE`` with
    its reason, so a parse regression is a loud number in the report rather
    than a module quietly dropping out of the lint.
    """
    rel = _relative(path, repo_root)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return FileResult(rel, UNANALYZABLE, reason=f"read failed: {type(exc).__name__}: {exc}")
    try:
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError, RecursionError) as exc:
        return FileResult(rel, UNANALYZABLE, reason=f"parse failed: {type(exc).__name__}: {exc}")

    try:
        findings: list[dict[str, object]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if _should_skip(name):
                    continue
                findings.append({"file": rel, "function": name, "line": int(node.lineno)})
    except Exception as exc:  # noqa: BLE001 — a scanner crash is UNANALYZABLE, not clean
        return FileResult(rel, UNANALYZABLE, reason=f"analysis failed: {type(exc).__name__}: {exc}")
    return FileResult(rel, VIOLATION if findings else CLEAN, findings=findings)


def iter_python_files(src_root: pathlib.Path) -> Iterable[pathlib.Path]:
    for path in sorted(src_root.rglob("*.py")):
        yield path


def _is_skipped(path: pathlib.Path, src_root: pathlib.Path) -> str | None:
    try:
        parts = path.relative_to(src_root).parts
    except ValueError:
        return None
    if parts and parts[0] in SKIP_DIRS:
        return f"under src/roam/{parts[0]}/ (excluded tree)"
    return None


def scan_tree(src_root: pathlib.Path, repo_root: pathlib.Path) -> list[FileResult]:
    """Per-file outcomes for a tree, including the files it did not cover."""
    results: list[FileResult] = []
    try:
        paths = list(iter_python_files(src_root))
    except OSError as exc:
        return [FileResult(_relative(src_root, repo_root), UNANALYZABLE, reason=f"walk failed: {exc}")]
    for path in paths:
        reason = _is_skipped(path, src_root)
        if reason is not None:
            results.append(FileResult(_relative(path, repo_root), SKIPPED, reason=reason))
            continue
        results.append(scan_file(path, repo_root))
    return results


def positive_control_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root.joinpath(*POSITIVE_CONTROL_DIR.parts)


def run_positive_control(repo_root: pathlib.Path) -> ControlResult:
    """Re-prove, on THIS invocation, that the detector still detects.

    The fixture holds one planted camelCase name beside snake_case and dunder
    names. The control passes only if the scanner fires on the first and
    stays silent on the others — an "always fire" or "never fire" regression
    fails it either way.
    """
    fixture = positive_control_dir(repo_root)
    if not fixture.is_dir():
        return ControlResult("broken", f"positive-control fixture directory missing: {fixture}", str(fixture))

    results = scan_tree(fixture, repo_root)
    broken = [r for r in results if r.status == UNANALYZABLE]
    if broken:
        return ControlResult("broken", f"control fixture unanalyzable: {broken[0].reason}", str(fixture))
    if not any(r.status in (CLEAN, VIOLATION) for r in results):
        return ControlResult("broken", f"control fixture parsed no files: {fixture}", str(fixture))

    names = [str(f["function"]) for r in results for f in r.findings]
    noisy = sorted(set(names) & set(POSITIVE_CONTROL_SILENT))
    if noisy:
        return ControlResult(
            "broken",
            f"compliant control names were flagged — the scanner is over-firing: {noisy}",
            str(fixture),
        )
    if POSITIVE_CONTROL_SENTINEL not in names:
        return ControlResult(
            "broken",
            f"the planted sentinel {POSITIVE_CONTROL_SENTINEL!r} was NOT detected — a "
            f"'matches the baseline' verdict from this scanner is meaningless. found {names}",
            str(fixture),
        )
    return ControlResult("ok", f"sentinel detected: {POSITIVE_CONTROL_SENTINEL}; compliant names silent", str(fixture))


def scan(repo_root: pathlib.Path, src_root: pathlib.Path | None = None) -> ScanReport:
    """Scan ``src/roam`` and return findings WITH their denominator.

    The positive control runs on every invocation; ``report.ok`` is false when
    it fails even if nothing was found, because in that case "nothing found"
    carries no information.
    """
    root = src_root if src_root is not None else repo_root / "src" / "roam"
    results = scan_tree(root, repo_root)
    findings = [f for r in results for f in r.findings]
    findings.sort(key=lambda r: (str(r["file"]), int(r["line"]), str(r["function"])))  # type: ignore[arg-type]
    unanalyzable = [r.as_record() for r in results if r.status == UNANALYZABLE]
    skipped = [r.as_record() for r in results if r.status == SKIPPED]
    return ScanReport(
        findings=findings,
        files_parsed=sum(1 for r in results if r.status in (CLEAN, VIOLATION)),
        files_unanalyzable=len(unanalyzable),
        files_skipped=len(skipped),
        unanalyzable=unanalyzable,
        skipped=skipped,
        positive_control=run_positive_control(repo_root),
        root=str(root),
    )


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    report = scan(repo_root)

    # stdout stays the bare baseline artifact; the denominator goes to stderr.
    json.dump(report.findings, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")

    print(report.summary(), file=sys.stderr)
    print(
        f"skipped {report.files_skipped} file(s): {sorted({str(s['reason']) for s in report.skipped})}", file=sys.stderr
    )
    for entry in report.unanalyzable:
        print(f"UNANALYZABLE {entry['path']}: {entry['reason']}", file=sys.stderr)
    if not report.positive_control.ok:
        print(f"POSITIVE CONTROL BROKEN: {report.positive_control.detail}", file=sys.stderr)
        return EXIT_SCANNER_BROKEN
    if report.files_parsed == 0:
        print("zero files parsed — that is zero coverage, not zero violations", file=sys.stderr)
        return EXIT_SCANNER_BROKEN
    if report.files_unanalyzable:
        return EXIT_UNANALYZABLE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
