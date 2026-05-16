"""Performance baselines for the full Roam Review loop.

Captures timing baselines for the critical paths so future waves surface
regressions. Targets:

    - ``roam init`` (index) on a 100-file fixture  < 10s
    - ``roam laws mine`` on the same fixture       <  5s
    - ``roam constitution init``                   <  1s
    - Full e2e loop end-to-end                     < 30s

These tests run by default but can be skipped with ``-m "not slow"``.
They mark themselves as ``slow`` and ``xdist_group("loop_perf")`` so
the timings aren't perturbed by parallel-test contention.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
)

# These tests measure wall-clock. Mark as slow + run in a single xdist
# group so they don't compete with each other for the CPU.
pytestmark = [
    pytest.mark.slow,
    pytest.mark.xdist_group("loop_perf"),
]


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Pre-elect autonomous_pr so privileged commands (`laws`, `constitution`,
    `agent-score`, etc.) work under future `ROAM_MODE_ENFORCEMENT`
    default-on (W23.3 staged-rollout PR-B). The end-to-end loop exercises
    the full agent-OS verb set, most of which is gated under `safe_edit`."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


# Wall-clock budgets. Tweak if hardware lottery causes flakes; the goal
# is to surface a 2x regression, not pinpoint a 5% slowdown.
INIT_BUDGET_S = 10.0
LAWS_MINE_BUDGET_S = 5.0
CONSTITUTION_INIT_BUDGET_S = 1.0
FULL_LOOP_BUDGET_S = 30.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(runner: CliRunner, args, **kwargs):
    from roam.cli import cli

    return runner.invoke(cli, args, catch_exceptions=False, **kwargs)


def _hundred_file_project(tmp_path: Path) -> Path:
    """Create a 100-file Python project for indexing benchmarks.

    10 packages * 10 files each = 100 Python files. Each file declares a
    class with three methods so the indexer has non-trivial symbol +
    reference work to do.
    """
    proj = tmp_path / "perf100"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    for pkg in range(10):
        pkg_dir = proj / f"pkg_{pkg}"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(f'"""Package {pkg}."""\n')
        for f in range(10):
            mod = pkg_dir / f"mod_{f}.py"
            mod.write_text(
                f'"""Module {pkg}.{f}."""\n'
                f"class Service{pkg}_{f}:\n"
                f"    def fetch_{pkg}_{f}(self):\n"
                f"        return {pkg * 10 + f}\n"
                f"\n"
                f"    def update_{pkg}_{f}(self, x):\n"
                f"        return x + {pkg * 10 + f}\n"
                f"\n"
                f"    def delete_{pkg}_{f}(self):\n"
                f"        return None\n"
            )
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. Index speed
# ---------------------------------------------------------------------------


def test_perf_index_100_files(tmp_path):
    """Indexing 100 files should complete within the budget."""
    proj = _hundred_file_project(tmp_path)
    start = time.perf_counter()
    out, rc = index_in_process(proj)
    elapsed = time.perf_counter() - start
    assert rc == 0, f"index failed:\n{out}"
    print(f"\n[PERF] index 100 files: {elapsed:.2f}s (budget {INIT_BUDGET_S}s)")
    assert elapsed < INIT_BUDGET_S, f"index took {elapsed:.2f}s, exceeds budget {INIT_BUDGET_S}s"


# ---------------------------------------------------------------------------
# 2. Laws mine speed
# ---------------------------------------------------------------------------


def test_perf_laws_mine_100_files(tmp_path, cli_runner, monkeypatch):
    """laws mine on a 100-file indexed project should complete within budget."""
    proj = _hundred_file_project(tmp_path)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    monkeypatch.chdir(proj)

    laws_out = proj / "roam-laws.yml"
    start = time.perf_counter()
    r = _invoke(
        cli_runner,
        ["--json", "laws", "mine", "--out", str(laws_out)],
    )
    elapsed = time.perf_counter() - start
    assert r.exit_code == 0, r.output
    print(f"\n[PERF] laws mine 100 files: {elapsed:.2f}s (budget {LAWS_MINE_BUDGET_S}s)")
    assert elapsed < LAWS_MINE_BUDGET_S, f"laws mine took {elapsed:.2f}s, exceeds budget {LAWS_MINE_BUDGET_S}s"


# ---------------------------------------------------------------------------
# 3. Constitution init speed
# ---------------------------------------------------------------------------


def test_perf_constitution_init(tmp_path, cli_runner, monkeypatch):
    """constitution init is policy-only; it should be sub-second."""
    proj = tmp_path / "perf_const"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main(): return 0\n")
    git_init(proj)
    monkeypatch.chdir(proj)

    start = time.perf_counter()
    r = _invoke(cli_runner, ["--json", "constitution", "init"])
    elapsed = time.perf_counter() - start
    assert r.exit_code == 0, r.output
    print(f"\n[PERF] constitution init: {elapsed:.2f}s (budget {CONSTITUTION_INIT_BUDGET_S}s)")
    assert elapsed < CONSTITUTION_INIT_BUDGET_S, (
        f"constitution init took {elapsed:.2f}s, exceeds budget {CONSTITUTION_INIT_BUDGET_S}s"
    )


# ---------------------------------------------------------------------------
# 4. Full loop end-to-end
# ---------------------------------------------------------------------------


def test_perf_full_loop_under_30s(tmp_path, cli_runner, monkeypatch):
    """Walk a representative subset of the full loop on a 100-file project.

    Steps: index -> laws mine -> constitution init -> mode -> runs start
        -> pr-bundle init -> preflight -> diff -> pr-bundle emit -> runs end
        -> replay -> agent-score.

    Budget: 30s wall-clock for the entire chain on the 100-file fixture.
    """
    proj = _hundred_file_project(tmp_path)
    monkeypatch.chdir(proj)

    start = time.perf_counter()

    # index
    out, rc = index_in_process(proj)
    assert rc == 0, out

    # laws mine
    laws_out = proj / "roam-laws.yml"
    r = _invoke(cli_runner, ["--json", "laws", "mine", "--out", str(laws_out)])
    assert r.exit_code == 0, r.output

    # constitution init
    r = _invoke(cli_runner, ["--json", "constitution", "init"])
    assert r.exit_code == 0, r.output

    # mode safe_edit
    r = _invoke(cli_runner, ["--json", "mode", "safe_edit"])
    assert r.exit_code == 0, r.output

    # runs start
    r = _invoke(
        cli_runner,
        ["--json", "runs", "start", "--agent", "perf-test"],
    )
    assert r.exit_code == 0, r.output
    sdata = json.loads(getattr(r, "stdout", None) or r.output)
    run_id = sdata["summary"]["run_id"]
    monkeypatch.setenv("ROAM_RUN_ID", run_id)

    # pr-bundle init
    r = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "init", "--intent", "perf loop"],
    )
    assert r.exit_code == 0, r.output

    # preflight (pick a known symbol)
    r = _invoke(cli_runner, ["--json", "preflight", "fetch_0_0"])
    assert r.exit_code in (0, 5), r.output

    # diff
    r = _invoke(cli_runner, ["--json", "diff"])
    assert r.exit_code in (0, 5), r.output

    # add affected
    r = _invoke(cli_runner, ["pr-bundle", "add", "affected", "fetch_0_0"])
    assert r.exit_code == 0, r.output

    # pr-bundle emit
    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert r.exit_code == 0, r.output

    # runs end
    r = _invoke(cli_runner, ["--json", "runs", "end"])
    assert r.exit_code == 0, r.output

    # replay
    r = _invoke(cli_runner, ["--json", "replay", run_id])
    assert r.exit_code == 0, r.output

    # agent-score
    r = _invoke(cli_runner, ["--json", "agent-score"])
    assert r.exit_code == 0, r.output

    elapsed = time.perf_counter() - start
    print(f"\n[PERF] full e2e loop on 100-file project: {elapsed:.2f}s (budget {FULL_LOOP_BUDGET_S}s)")
    assert elapsed < FULL_LOOP_BUDGET_S, f"full loop took {elapsed:.2f}s, exceeds budget {FULL_LOOP_BUDGET_S}s"
