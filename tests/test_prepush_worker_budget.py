"""Regression tests for the local release gate's bounded xdist budget."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._helpers.repo_root import repo_root


def _load_gate_module():
    path = repo_root() / "scripts" / "prepush_check.py"
    spec = importlib.util.spec_from_file_location("roam_prepush_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_default_worker_count_caps_high_core_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _load_gate_module()
    monkeypatch.setattr(gate.os, "cpu_count", lambda: 64)
    assert gate._default_worker_count() == 4
    monkeypatch.setattr(gate.os, "cpu_count", lambda: 2)
    assert gate._default_worker_count() == 2
    monkeypatch.setattr(gate.os, "cpu_count", lambda: None)
    assert gate._default_worker_count() == 1


@pytest.mark.parametrize("value", ["0", "5", "auto", "1.5", ""])
def test_worker_count_rejects_unbounded_or_ambiguous_values(value: str) -> None:
    gate = _load_gate_module()
    with pytest.raises(argparse.ArgumentTypeError, match="integer from 1 to 4"):
        gate._bounded_worker_count(value)


def test_structural_bundle_uses_bounded_loadfile_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _load_gate_module()
    runner = gate.GateRunner(root=Path("."), pytest_workers=3)
    captured: dict[str, list[str]] = {}

    def capture(_name: str, argv: list[str], fix_hint: str):
        del fix_hint
        captured["argv"] = argv
        return None

    monkeypatch.setattr(runner, "_run", capture)
    runner.run_pytest_bundle(("test_example.py",), "FAST")

    argv = captured["argv"]
    assert argv[argv.index("-n") + 1] == "3"
    assert argv[argv.index("--dist") + 1] == "loadfile"


def test_release_temp_capacity_scales_with_bounded_worker_budget() -> None:
    gate = _load_gate_module()

    assert gate._release_temp_required_bytes(1) == 4 * 1024**3
    assert gate._release_temp_required_bytes(2) == 4 * 1024**3
    assert gate._release_temp_required_bytes(4) == 8 * 1024**3


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("ci", ["", "true"])
def test_release_suite_honors_worker_budget_and_group_markers(monkeypatch, capsys, workers, ci):
    from roam.testing.ci_xdist import xdist_args_to_inject

    gate = _load_gate_module()
    root = repo_root()
    monkeypatch.setattr(gate, "repo_root", lambda: root)
    monkeypatch.setenv("CI", ci)
    monkeypatch.setenv("ROAM_XDIST_WORKERS", "64")
    monkeypatch.setattr(gate.shutil, "disk_usage", lambda _path: SimpleNamespace(free=1 << 40))
    commands = []

    def capture(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", capture)
    assert gate.main(["--release", "--workers", str(workers)]) == 0
    suites = [argv for argv in commands if "tests/" in argv and "pytest" in argv]
    assert len(suites) == 1
    argv = suites[0]
    assert argv[argv.index("-n") + 1] == str(workers)
    assert argv[argv.index("--dist") + 1] == "loadgroup"
    assert argv[argv.index("-m", 3) + 1] == "not slow"
    assert "-rf" in argv
    assert xdist_args_to_inject(argv, dict(os.environ), True) == []
    output = capsys.readouterr().out
    assert f"{workers} workers, loadgroup" in output
    assert "serial" not in output.lower()


def test_release_temp_capacity_fails_closed_before_expensive_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gate = _load_gate_module()
    required = gate._release_temp_required_bytes(2)
    monkeypatch.setattr(gate.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(gate.shutil, "disk_usage", lambda _path: SimpleNamespace(free=required - 1))
    runner = gate.GateRunner(root=Path("."), pytest_workers=2)

    result = runner.run_release_temp_capacity_gate()

    assert result.passed is False
    assert result.fix_hint.startswith("remove abandoned pytest fixture trees")
    assert "required=4.00 GiB" in result.detail
    assert runner.results == [result]


def test_pytest_retains_only_one_failed_temp_tree() -> None:
    config = (repo_root() / "pyproject.toml").read_text(encoding="utf-8")

    assert "tmp_path_retention_count = 1" in config
    assert 'tmp_path_retention_policy = "failed"' in config


def test_gate_subprocess_env_drops_outer_repository_control_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fixture Git commands cannot inherit the hook worktree's index path."""

    gate = _load_gate_module()
    poisoned = {
        "GIT_INDEX_FILE": str(tmp_path / "outer-index"),
        "GIT_DIR": str(tmp_path / "outer-git-dir"),
        "GIT_WORK_TREE": str(tmp_path / "outer-work-tree"),
        "GIT_COMMON_DIR": str(tmp_path / "outer-common-dir"),
    }
    for name, value in poisoned.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "fixture-author")

    env = gate.GateRunner(root=repo_root())._env()

    assert all(name not in env for name in poisoned)
    assert env["GIT_AUTHOR_NAME"] == "fixture-author"


def test_gate_subprocess_env_enforces_one_native_thread_per_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native math runtimes cannot multiply the bounded xdist budget."""

    gate = _load_gate_module()
    for name in gate._NATIVE_THREAD_ENV:
        monkeypatch.setenv(name, "64")

    env = gate.GateRunner(root=repo_root())._env()

    assert gate._NATIVE_THREAD_ENV == (
        "BLIS_NUM_THREADS",
        "GOTO_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "OMP_NUM_THREADS",
        "OMP_THREAD_LIMIT",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    )
    assert all(env[name] == "1" for name in gate._NATIVE_THREAD_ENV)


def test_sanitized_gate_env_keeps_foreign_git_add_out_of_outer_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reproduce the hook/pytest boundary with two isolated repositories."""

    outer = tmp_path / "outer"
    foreign = tmp_path / "foreign"
    outer.mkdir()
    foreign.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    subprocess.run(["git", "init", "-q"], cwd=foreign, check=True)
    (outer / "outer.txt").write_text("outer\n", encoding="utf-8")
    (foreign / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "outer.txt"], cwd=outer, check=True)
    outer_index = outer / ".git" / "index"
    before = outer_index.read_bytes()
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer_index))
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))

    env = _load_gate_module().GateRunner(root=repo_root())._env()
    subprocess.run(["git", "add", "fixture.txt"], cwd=foreign, env=env, check=True)

    assert outer_index.read_bytes() == before
    listed = subprocess.run(["git", "ls-files"], cwd=foreign, env=env, check=True, capture_output=True, text=True)
    assert listed.stdout.splitlines() == ["fixture.txt"]


def test_shell_hook_clears_git_local_env_before_running_pytest() -> None:
    """The hook itself must sanitize Git's complete local-env vocabulary."""

    hook = (repo_root() / ".githooks" / "pre-push").read_text(encoding="utf-8")
    resolve_pos = hook.index("git rev-parse --show-toplevel")
    enumerate_pos = hook.index("git rev-parse --local-env-vars")
    unset_pos = hook.index('unset "$var"')
    gate_pos = hook.index('exec "$PY_BIN" "$REPO_ROOT/scripts/prepush_check.py" --fast')

    assert resolve_pos < enumerate_pos < unset_pos < gate_pos
    assert 'cd "$REPO_ROOT"' in hook[unset_pos:gate_pos]


def test_help_renders_on_legacy_windows_code_page() -> None:
    """The release gate must remain operable on a non-UTF-8 console."""
    path = repo_root() / "scripts" / "prepush_check.py"
    env = {**os.environ, "PYTHONIOENCODING": "cp1253"}

    result = subprocess.run(
        [sys.executable, str(path), "--help"],
        cwd=repo_root(),
        env=env,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"--workers" in result.stdout
