"""Baseline-enumerating lint for the AGENTS.md 'Functions: snake_case (100%)' rule.

Scans `src/roam/**/*.py` for camelCase function definitions and compares the
findings to a frozen baseline at `tests/data/snake_case_baseline.json`.

Behavior:
  - New violations vs. baseline -> FAIL with the list of new violations.
  - Removed violations vs. baseline -> FAIL ("great, please remove from
    baseline") so the baseline shrinks monotonically.
  - Exact match -> PASS.

The scanner lives at `scripts/scan_snake_case_violations.py` and is IMPORTED
here rather than reimplemented; regenerate the baseline with:

    python scripts/scan_snake_case_violations.py > tests/data/snake_case_baseline.json

This file used to carry its own private copy of the scan. Two copies of a
detector is two places for it to silently stop detecting, and the copy here
inherited the same fail-open parse handler the script had — so the rewire is
part of the W1455 fix, not incidental tidying.

ZERO IS ONLY MEANINGFUL BESIDE ITS DENOMINATOR (W1455)
------------------------------------------------------
"Matches the baseline" is not evidence on its own: a scan that parsed
nothing matches an empty baseline, and before W1455 an unparseable file
returned `[]` — indistinguishable from a file with no camelCase functions.
So `files_parsed` / `files_unanalyzable` / the positive control are asserted
SEPARATELY from the baseline comparison.
"""

from __future__ import annotations

import pathlib
import sys

from tests._helpers.repo_root import repo_root

_SCRIPTS = str(repo_root() / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import scan_snake_case_violations as scanner  # noqa: E402


def _repo_root() -> pathlib.Path:
    return repo_root()


def _baseline_path() -> pathlib.Path:
    return _repo_root() / "tests" / "data" / "snake_case_baseline.json"


def _as_key(entry: dict[str, object]) -> tuple[str, str, int]:
    return (str(entry["file"]), str(entry["function"]), int(entry["line"]))


def test_snake_case_scan_publishes_a_trustworthy_denominator() -> None:
    """The baseline comparison below is only worth what the corpus is worth.

    Asserted separately so a parse regression fails as a parse regression
    instead of quietly shrinking the corpus until the baseline matches.
    """
    report = scanner.scan(_repo_root())

    assert report.files_parsed > 0, f"the scan parsed NO files under src/roam: {report.summary()}"
    assert report.files_unanalyzable == 0, (
        f"{report.files_unanalyzable} file(s) under src/roam could not be analysed, so the "
        "baseline comparison would be measured against a silently shrunken corpus:\n"
        + "\n".join(f"  {e['path']}: {e['reason']}" for e in report.unanalyzable)
    )
    assert report.positive_control.ok, (
        "the snake_case scanner failed its own positive control, so 'matches the "
        f"baseline' carries no information: {report.positive_control.detail}"
    )


def test_snake_case_function_lint_matches_baseline() -> None:
    import json

    baseline_path = _baseline_path()
    assert baseline_path.exists(), (
        f"Baseline file missing at {baseline_path}. "
        "Regenerate with: python scripts/scan_snake_case_violations.py "
        "> tests/data/snake_case_baseline.json"
    )

    baseline_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert isinstance(baseline_raw, list), "Baseline must be a JSON list"

    report = scanner.scan(_repo_root())
    current = report.findings
    print(
        f"\n[snake_case_lint] {report.summary()}; "
        f"{report.files_skipped} skipped; camelCase function definitions in src/roam: {len(current)}"
    )

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
