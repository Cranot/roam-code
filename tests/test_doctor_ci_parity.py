"""Doctor check: does this environment resemble the one CI actually tests?

"It passes locally" is only evidence when local and CI agree. On 2026-07-26 the
full suite passed locally with exit 0 on the exact commit whose CI was failing,
and four CI round-trips were spent discovering defects one at a time that no
local run could reproduce — because the local interpreter was 3.14 while CI
tested 3.10-3.13, and ``roam`` on PATH resolved to a stale global install rather
than the working tree.

The second divergence is the dangerous one: it does not error, it silently
exercises different code and reports success.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from roam.commands.cmd_doctor import _check_ci_environment_parity


def _run_in(tmp: Path) -> dict:
    prev = os.getcwd()
    try:
        os.chdir(tmp)
        return _check_ci_environment_parity()
    finally:
        os.chdir(prev)


def _write_matrix(tmp: Path, versions: list[str]) -> None:
    wf = tmp / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{v}"' for v in versions)
    (wf / "ci.yml").write_text(
        f"jobs:\n  test:\n    strategy:\n      matrix:\n        python-version: [{listed}]\n",
        encoding="utf-8",
    )


def test_no_ci_matrix_is_not_applicable(tmp_path: Path) -> None:
    """Must stay quiet in a project that declares no matrix to compare against."""
    result = _run_in(tmp_path)
    assert result["passed"] is True
    assert result.get("_state") == "not_applicable"


def test_interpreter_outside_the_declared_matrix_is_reported(tmp_path: Path) -> None:
    _write_matrix(tmp_path, ["3.10", "3.11"])  # deliberately excludes the runner
    running = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if running in {"3.10", "3.11"}:
        pytest.skip("runner is inside the fixture matrix; the negative case covers this")
    result = _run_in(tmp_path)
    assert result["passed"] is False
    assert running in result["detail"]
    assert "3.10" in result["detail"]


def test_interpreter_inside_the_declared_matrix_does_not_fire(tmp_path: Path) -> None:
    """Negative control: matching CI must not be reported as divergence."""
    running = f"{sys.version_info[0]}.{sys.version_info[1]}"
    _write_matrix(tmp_path, [running])
    result = _run_in(tmp_path)
    # A stale `roam` on PATH can still fail this check, so assert on the
    # interpreter clause specifically rather than the overall verdict.
    assert "CI tests" not in result["detail"]


def test_scalar_python_version_is_not_treated_as_a_matrix(tmp_path: Path) -> None:
    """A single `python-version: "3.12"` is usually a lint or docs lane.

    Treating it as the test matrix would report divergence for every developer
    not on that exact version, which is noise rather than signal.
    """
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "lint.yml").write_text('jobs:\n  lint:\n    steps:\n      - python-version: "3.12"\n', encoding="utf-8")
    result = _run_in(tmp_path)
    assert result.get("_state") == "not_applicable"


def test_reports_a_console_script_belonging_to_another_install(tmp_path: Path) -> None:
    """`roam` on PATH must belong to the interpreter running the tests.

    When it does not, tests that shell out exercise a different build entirely —
    typically a stale global release — and pass while CI fails.
    """
    running = f"{sys.version_info[0]}.{sys.version_info[1]}"
    _write_matrix(tmp_path, [running])  # isolate the entry-point clause
    result = _run_in(tmp_path)

    on_path = shutil.which("roam")
    if on_path is None:
        pytest.skip("no `roam` on PATH in this environment")
    same_dir = Path(on_path).parent.resolve() == Path(sys.executable).parent.resolve()
    if same_dir:
        assert "not the one for this interpreter" not in result["detail"]
    else:
        assert result["passed"] is False
        assert "not the one for this interpreter" in result["detail"]
