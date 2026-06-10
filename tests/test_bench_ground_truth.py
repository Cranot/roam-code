"""Tests for `roam bench-compile --ground-truth` oracle wiring.

Stubs the cell runner so we don't pay for actual `claude -p` calls, and
patches the two oracles so we can assert dispatch happens with the
correct artifacts and the resulting `ground_truth_score` is recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_bench
from tests._helpers.repo_root import repo_root

# The benchmark oracles live under the gitignored `internal/` and are absent in
# a clean public checkout / CI; skip oracle-importing tests when they aren't
# present so the public suite stays green without them.
_HAS_INTERNAL = (repo_root() / "internal" / "benchmarks" / "oracle_fix_bug.py").exists()
_requires_internal = pytest.mark.skipif(not _HAS_INTERNAL, reason="internal/ benchmark oracles absent")

# ----- artifact extraction helpers -----


def test_extract_pytest_source_fenced_python_block() -> None:
    text = "Here is a test.\n\n```python\nimport pytest\n\ndef test_one():\n    assert 1 + 1 == 2\n```\nDone.\n"
    source = cmd_bench._extract_pytest_source(text)
    assert source is not None
    assert "def test_one" in source
    assert "import pytest" in source


def test_extract_pytest_source_returns_none_when_no_test() -> None:
    assert cmd_bench._extract_pytest_source("just prose, no code") is None
    assert cmd_bench._extract_pytest_source("```python\nprint('hi')\n```") is None


def test_extract_patch_fenced_diff_block() -> None:
    text = "Here is a patch.\n\n```diff\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n```\n"
    patch = cmd_bench._extract_patch(text)
    assert patch is not None
    assert "--- a/foo.py" in patch


def test_extract_patch_raw_git_diff() -> None:
    text = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    patch = cmd_bench._extract_patch(text)
    assert patch is not None
    assert "diff --git" in patch


def test_extract_patch_returns_none_when_absent() -> None:
    assert cmd_bench._extract_patch("hello, no patches here") is None


# ----- classification -----


def test_classify_task_shape_write_pytest() -> None:
    assert cmd_bench._classify_task_shape("Write a pytest for foo") == "write_pytest"


def test_classify_task_shape_stack_trace_fix() -> None:
    assert cmd_bench._classify_task_shape("Fix the bug shown in this stack trace") == "stack_trace_fix"


def test_classify_task_shape_other() -> None:
    assert cmd_bench._classify_task_shape("What files are coupled to X?") == "other"


# ----- oracle dispatch -----


@_requires_internal
def test_ground_truth_score_pytest_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When shape=write_pytest and a test block exists, oracle_pytest is called."""
    calls: list[dict] = []

    def fake_run_produced_test(source, project_root, *, timeout=30):  # noqa: ANN001
        calls.append({"source": source, "project_root": str(project_root)})
        return {"exit_code": 0, "stdout_tail": "", "stderr_tail": "", "duration_ms": 1, "timed_out": False}

    import internal.benchmarks.oracle_pytest as op

    monkeypatch.setattr(op, "run_produced_test", fake_run_produced_test)

    task = "Write a pytest for the foo() helper"
    result_text = "```python\ndef test_foo():\n    assert True\n```\n"
    score = cmd_bench._ground_truth_score(task, result_text, str(Path.cwd()))
    assert score == "1"
    assert len(calls) == 1
    assert "def test_foo" in calls[0]["source"]


@_requires_internal
def test_ground_truth_score_pytest_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_produced_test(source, project_root, *, timeout=30):  # noqa: ANN001
        return {"exit_code": 1, "stdout_tail": "", "stderr_tail": "", "duration_ms": 1, "timed_out": False}

    import internal.benchmarks.oracle_pytest as op

    monkeypatch.setattr(op, "run_produced_test", fake_run_produced_test)

    task = "Write a test for X"
    result_text = "```python\ndef test_x():\n    assert 1==2\n```"
    score = cmd_bench._ground_truth_score(task, result_text, str(Path.cwd()))
    assert score == "0"


@_requires_internal
def test_ground_truth_score_fix_bug_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When shape=stack_trace_fix and a diff exists, oracle_fix_bug is called."""
    calls: list[dict] = []

    def fake_apply_and_measure(project_root, patch_text, selector, *, timeout=60):  # noqa: ANN001
        calls.append({"patch": patch_text, "selector": selector})
        return {
            "failing_before": 1,
            "failing_after": 0,
            "transitioned_to_passing": 1,
            "transitioned_to_failing": 0,
            "patch_applied": True,
            "stdout_tail_before": "",
            "stdout_tail_after": "",
            "duration_ms": 1,
            "timed_out": False,
        }

    import internal.benchmarks.oracle_fix_bug as ob

    monkeypatch.setattr(ob, "apply_and_measure", fake_apply_and_measure)

    task = "Fix the bug exposed by tests/test_x.py::test_one"
    result_text = "```diff\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n```\n"
    score = cmd_bench._ground_truth_score(task, result_text, str(Path.cwd()))
    assert score == "1"
    assert len(calls) == 1
    assert "tests/test_x.py" in calls[0]["selector"]
    assert "--- a/foo.py" in calls[0]["patch"]


def test_ground_truth_score_unsupported_shape_returns_empty() -> None:
    score = cmd_bench._ground_truth_score(
        "What files are coupled to X?",
        "some prose, no artifact",
        str(Path.cwd()),
    )
    assert score == ""


def test_ground_truth_score_missing_artifact_returns_empty() -> None:
    score = cmd_bench._ground_truth_score(
        "Write a pytest for foo",
        "Sorry, I can't.",  # no python block
        str(Path.cwd()),
    )
    assert score == ""


# ----- end-to-end CLI wiring -----


def test_bench_compile_help_shows_ground_truth_flag() -> None:
    runner = CliRunner()
    res = runner.invoke(cli, ["bench-compile", "--help"])
    assert res.exit_code == 0
    assert "--ground-truth" in res.output


@_requires_internal
def test_bench_compile_default_off_no_oracle_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without `--ground-truth`, neither oracle is invoked."""
    pytest_calls: list[int] = []
    bug_calls: list[int] = []

    def fake_pytest(*a, **kw):  # noqa: ANN001
        pytest_calls.append(1)
        return {"exit_code": 0}

    def fake_bug(*a, **kw):  # noqa: ANN001
        bug_calls.append(1)
        return {"transitioned_to_passing": 1}

    import internal.benchmarks.oracle_fix_bug as ob
    import internal.benchmarks.oracle_pytest as op

    monkeypatch.setattr(op, "run_produced_test", fake_pytest)
    monkeypatch.setattr(ob, "apply_and_measure", fake_bug)

    def fake_run_claude_p(prompt, out_path, timeout_sec, model=None):  # noqa: ANN001
        out_path.write_text(
            json.dumps(
                {
                    "type": "result",
                    "num_turns": 1,
                    "duration_ms": 100,
                    "total_cost_usd": 0.001,
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "result": "```python\ndef test_x():\n    assert True\n```",
                }
            )
        )
        return {"ok": True, "elapsed": 0.1}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", fake_run_claude_p)
    monkeypatch.setattr(cmd_bench, "_compile_envelope", lambda task, cwd: "")

    runner = CliRunner()
    out_dir = tmp_path / "cells"
    res = runner.invoke(
        cli,
        [
            "bench-compile",
            "Write a pytest for foo",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--workers",
            "1",
            "--out-dir",
            str(out_dir),
        ],
    )
    assert res.exit_code == 0, res.output
    assert pytest_calls == []
    assert bug_calls == []


@_requires_internal
def test_bench_compile_ground_truth_records_score(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With `--ground-truth`, oracle is called and TSV column is populated."""
    pytest_calls: list[dict] = []

    def fake_pytest(source, project_root, *, timeout=30):  # noqa: ANN001
        pytest_calls.append({"source": source})
        return {"exit_code": 0}

    import internal.benchmarks.oracle_pytest as op

    monkeypatch.setattr(op, "run_produced_test", fake_pytest)

    def fake_run_claude_p(prompt, out_path, timeout_sec, model=None):  # noqa: ANN001
        out_path.write_text(
            json.dumps(
                {
                    "type": "result",
                    "num_turns": 1,
                    "duration_ms": 100,
                    "total_cost_usd": 0.001,
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "result": ("```python\ndef test_passes():\n    assert True\n```"),
                }
            )
        )
        return {"ok": True, "elapsed": 0.1}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", fake_run_claude_p)
    monkeypatch.setattr(cmd_bench, "_compile_envelope", lambda task, cwd: "")

    runner = CliRunner()
    out_dir = tmp_path / "cells"
    res = runner.invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "Write a pytest for foo",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--workers",
            "1",
            "--out-dir",
            str(out_dir),
            "--ground-truth",
        ],
    )
    assert res.exit_code == 0, res.output
    assert len(pytest_calls) == 1

    # TSV column is APPENDED at end, header includes `ground_truth_score`.
    tsv_path = out_dir / "cells.tsv"
    assert tsv_path.exists()
    tsv_text = tsv_path.read_text()
    header = tsv_text.splitlines()[0]
    assert header.endswith("ground_truth_score")
    # The one data row should have score == "1".
    data_row = tsv_text.splitlines()[1].split("\t")
    assert data_row[-1] == "1"


def test_bench_compile_ground_truth_unsupported_shape_empty_score(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """For non-test/non-bug-fix shapes, ground_truth_score is empty string."""

    def fake_run_claude_p(prompt, out_path, timeout_sec, model=None):  # noqa: ANN001
        out_path.write_text(
            json.dumps(
                {
                    "type": "result",
                    "num_turns": 1,
                    "duration_ms": 100,
                    "total_cost_usd": 0.001,
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "result": "Files coupled to X: a.py, b.py",
                }
            )
        )
        return {"ok": True, "elapsed": 0.1}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", fake_run_claude_p)
    monkeypatch.setattr(cmd_bench, "_compile_envelope", lambda task, cwd: "")

    runner = CliRunner()
    out_dir = tmp_path / "cells"
    res = runner.invoke(
        cli,
        [
            "bench-compile",
            "What files are coupled to X?",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--workers",
            "1",
            "--out-dir",
            str(out_dir),
            "--ground-truth",
        ],
    )
    assert res.exit_code == 0, res.output
    tsv_text = (out_dir / "cells.tsv").read_text()
    data_row = tsv_text.splitlines()[1].split("\t")
    assert data_row[-1] == ""


# ----- cwd-decoupling regression (SWE-bench readiness) -----


@_requires_internal
def test_ensure_internal_importable_true() -> None:
    """The dev-repo root is derivable from roam.__file__ so internal/ resolves."""
    assert cmd_bench._ensure_internal_importable() is True


@pytest.mark.skipif(not _HAS_INTERNAL, reason="internal/ benchmark oracles absent")
def test_ground_truth_oracle_resolves_from_foreign_cwd(tmp_path, monkeypatch) -> None:
    """Grading an EXTERNAL repo from a non-roam cwd must still reach the oracle.

    Regression for the silent-empty bug: the ground-truth oracle lived under the
    roam repo's `internal/`, imported by bare package path, so running from a
    SWE-bench instance dir (foreign cwd) ImportError'd and emptied the score.
    """
    import subprocess

    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "__init__.py").write_text("")
    (proj / "pkg" / "m.py").write_text("def f():\n    return 1\n")  # BUG: want 2
    (proj / "test_m.py").write_text("from pkg.m import f\n\n\ndef test_f():\n    assert f() == 2\n")
    (proj / "conftest.py").write_text("import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n")
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "buggy HEAD"],
        cwd=proj,
        check=True,
    )
    # chdir AWAY from the roam repo: without the fix, `import internal...` fails.
    monkeypatch.chdir(tmp_path)
    task = "Fix the bug so the failing test test_m.py::test_f passes."
    result = "```diff\n--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n```\n"
    score = cmd_bench._ground_truth_score(task, result, str(proj))
    assert score == "1", f"expected fix to grade as 1, got {score!r}"
