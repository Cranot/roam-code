"""Validates the mode-enforcement staged rollout is empirically green.

Runs the W26.4 / W23.4 fixture-swept test files with ROAM_MODE_ENFORCEMENT=1
to confirm the rollout is ready for PR-C (default flip).

This test does NOT flip the default. It documents what the green-light
evidence looks like - anyone deciding to ship PR-C reads this test result first.

If this test fails: investigate the staged-rollout, not the test.
Sources of failure (in priority order):
  1. New test file in the sweep set without monkeypatch.setenv("ROAM_AGENT_MODE", ...)
  2. New _COMMANDS entry without _MODE_EXTRAS classification (caught by
     test_mode_classification_coverage.py at module-import time)
  3. New _MODE_EXTRAS entry referencing a non-existent command
     (also caught by test_mode_classification_coverage.py)
  4. Genuine breakage in a fixture-swept test

Refresh this test if the fixture set changes (add new tests to _SWEPT_FILES below).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_SWEPT_FILES = [
    "tests/test_attest.py",
    "tests/test_cga.py",
    "tests/test_cga_fail_closed.py",
    "tests/test_cga_dirty_hash_binding.py",
    "tests/test_verify_imports.py",
    "tests/test_v1216_passes_51_60.py",
    "tests/test_v2_edge_cases.py",
    "tests/test_loop_performance.py",
    "tests/test_pr_comment_render.py",
    "tests/test_pr_analyze_edge_cases.py",
]


@pytest.mark.slow
def test_staged_rollout_enforcement_green():
    """Full enforcement-on pass across the W26.4 fixture-sweep set."""
    env = os.environ.copy()
    env["ROAM_MODE_ENFORCEMENT"] = "1"
    cmd = [sys.executable, "-m", "pytest", *_SWEPT_FILES, "--tb=short", "-q"]
    result = subprocess.run(
        cmd,
        env=env,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Staged rollout enforcement test failed.\n"
        f"STDOUT:\n{result.stdout[-2000:]}\n"
        f"STDERR:\n{result.stderr[-500:]}"
    )
