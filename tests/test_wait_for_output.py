"""Regression tests for scripts/wait-for-output.sh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tests._helpers.repo_root import repo_root

WAITER = repo_root() / "scripts" / "wait-for-output.sh"

# Stands in for `grep`: the first call answers "not found" and then appends the
# sentinel, as a producer does when it writes its result and exits between the
# waiter's file check and its liveness check. Later calls are the real grep.
_GREP_SHIM = """#!/usr/bin/env bash
if [ ! -e "$SHIM_STATE" ]; then
  : > "$SHIM_STATE"
  printf 'DONE\\n' >> "${@: -1}"
  exit 1
fi
exec /usr/bin/grep "$@"
"""


def _run(tmp_path: Path, waiter_args: str, *, shim: bool) -> subprocess.CompletedProcess[str]:
    shutil.copy2(WAITER, tmp_path / "wait-for-output.sh")
    prefix = ""
    if shim:
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        (shim_dir / "grep").write_text(_GREP_SHIM, newline="\n")
        prefix = 'chmod +x shim/grep; export SHIM_STATE="$PWD/shim/state" PATH="$PWD/shim:$PATH"; '
    # The producer has already exited: its pid is one this shell started and reaped.
    script = prefix + "sh -c 'exit 0' & p=$!; wait $p; exec bash wait-for-output.sh " + waiter_args.replace("PID", "$p")
    return subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60)


def test_sentinel_written_just_before_the_producer_exits_is_found(tmp_path: Path) -> None:
    (tmp_path / "out.log").write_text("working\n")

    result = _run(tmp_path, "out.log '^DONE$' 30 PID 1", shim=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "matched" in result.stdout


def test_producer_that_exits_without_the_sentinel_is_reported(tmp_path: Path) -> None:
    (tmp_path / "out.log").write_text("working\n")

    result = _run(tmp_path, "out.log '^DONE$' 30 PID 1", shim=False)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "exited before" in result.stderr


def test_usage_names_the_line_anchor(tmp_path: Path) -> None:
    result = _run(tmp_path, "", shim=False)

    assert result.returncode == 64
    assert "^" in result.stderr
