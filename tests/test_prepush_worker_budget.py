"""Regression tests for the local release gate's bounded xdist budget."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

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
