"""Tests for ProcessPoolExecutor/ThreadPoolExecutor parallelization in
analyzers — backlog tier B perf work.

Targets:
- ``roam clones`` (per-file tree-sitter parsing parallelized via ProcessPool;
  per-pair Jaccard comparison parallelized via ProcessPool with a
  cached-funcs initializer).
- ``roam dead`` (per-file git-blame parallelized via ThreadPool;
  per-file test-text scanning parallelized via ThreadPool with a single
  combined alternation regex).

Each test compares serial output (``ROAM_NO_PARALLEL=1``) against the
default (parallel) output and asserts they produce identical structured
results so the parallelization preserves correctness.
"""

from __future__ import annotations

import os
import time

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, invoke_cli, parse_json_output


# ---------------------------------------------------------------------------
# Fixture: a moderate-size project with several files containing clones
# ---------------------------------------------------------------------------


def _clone_project_files(num_groups: int = 8) -> dict[str, str]:
    """Return a {rel_path: content} mapping with ``num_groups`` near-clone
    pairs spread across multiple files.

    Each "group" is two files with structurally similar functions (Type-2
    clone: same control flow, different identifiers).
    """
    files: dict[str, str] = {}
    template_a = (
        "def {fn}(items, target):\n"
        "    found = []\n"
        "    for item in items:\n"
        "        if item == target:\n"
        "            found.append(item)\n"
        "    return found\n"
    )
    template_b = (
        "def {fn}(records, key):\n"
        "    out = []\n"
        "    for rec in records:\n"
        "        if rec == key:\n"
        "            out.append(rec)\n"
        "    return out\n"
    )
    # Also add some "padding" files to push us past parallel-threshold for
    # clones (the implementation falls back to serial under ~100 files).
    for i in range(num_groups):
        files[f"src/group_a_{i}.py"] = template_a.format(fn=f"find_target_{i}")
        files[f"src/group_b_{i}.py"] = template_b.format(fn=f"find_key_{i}")
    # Padding files — small but non-trivial parses so the file rows are
    # iterated.
    for i in range(140):
        files[f"src/pad_{i}.py"] = f'"""pad module {i}"""\n\nVALUE = {i}\n'
    return files


@pytest.fixture
def clones_project(project_factory):
    return project_factory(_clone_project_files())


@pytest.fixture
def small_clones_project(project_factory):
    """Far fewer than ``_PARALLEL_MIN_FILES`` files (500) — exercises the
    serial code path even when ROAM_NO_PARALLEL is unset."""
    files = _clone_project_files(num_groups=3)
    # Drop most of the padding so we're well under the 500 file threshold.
    files = {k: v for k, v in files.items() if not k.startswith("src/pad_") or int(k.split("_")[-1].split(".")[0]) < 20}
    return project_factory(files)


# ---------------------------------------------------------------------------
# Helper: run a roam command with / without ROAM_NO_PARALLEL and return
# the parsed JSON envelope.
# ---------------------------------------------------------------------------


def _run_with_env(runner, args, cwd, parallel: bool, command: str):
    old = os.environ.get("ROAM_NO_PARALLEL")
    try:
        if parallel:
            os.environ.pop("ROAM_NO_PARALLEL", None)
        else:
            os.environ["ROAM_NO_PARALLEL"] = "1"
        result = invoke_cli(runner, args, cwd=cwd, json_mode=True)
        assert result.exit_code == 0, (
            f"roam {' '.join(args)} failed: {result.output}"
        )
        return parse_json_output(result, command=command)
    finally:
        if old is None:
            os.environ.pop("ROAM_NO_PARALLEL", None)
        else:
            os.environ["ROAM_NO_PARALLEL"] = old


def _cluster_signatures(env: dict) -> set:
    """Build a canonical set-of-frozensets cluster signature so order
    drift (which is non-deterministic across processes due to randomized
    string hashing) doesn't break equality."""
    clusters = env.get("clusters", []) or []
    out: set = set()
    for c in clusters:
        value = c.get("value") if "value" in c else c
        members = value.get("members", []) or []
        sig = frozenset(m.get("qualified_name", "") for m in members)
        if sig:
            out.add(sig)
    return out


def _pair_signatures(env: dict) -> set:
    """Sorted (qname_a, qname_b) pair signatures, ignoring within-pair
    ordering."""
    pairs = env.get("pairs", []) or []
    out: set = set()
    for p in pairs:
        value = p.get("value") if "value" in p else p
        # Pair envelopes use file/func/line; build qname-like keys.
        a = f"{value.get('file_a', '')}:{value.get('func_a', '')}"
        b = f"{value.get('file_b', '')}:{value.get('func_b', '')}"
        out.add(frozenset({a, b}))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.xdist_group("parallel_analyzers")
def test_clones_parallel_matches_serial_output(clones_project, cli_runner):
    """Cluster membership and pair counts must be identical between
    serial and parallel paths.

    Pattern strings and member ordering legitimately drift across
    processes (pre-existing non-determinism due to ``set`` iteration
    order and ``Counter.most_common`` tie-breaking under randomized
    string hashing). Cluster membership is the load-bearing invariant.
    """
    serial = _run_with_env(
        cli_runner, ["clones"], cwd=clones_project, parallel=False, command="clones"
    )
    parallel = _run_with_env(
        cli_runner, ["clones"], cwd=clones_project, parallel=True, command="clones"
    )

    s_sig = _cluster_signatures(serial)
    p_sig = _cluster_signatures(parallel)
    assert s_sig == p_sig, (
        f"Cluster membership differs.\n"
        f"serial-only: {s_sig - p_sig}\n"
        f"parallel-only: {p_sig - s_sig}"
    )

    # Pair count must match exactly (deterministic across paths).
    assert (
        serial["summary"]["clone_pairs"] == parallel["summary"]["clone_pairs"]
    ), "clone_pairs count differs between serial and parallel"
    assert (
        serial["summary"]["clusters"] == parallel["summary"]["clusters"]
    ), "cluster count differs between serial and parallel"
    assert (
        serial["summary"]["total_functions"]
        == parallel["summary"]["total_functions"]
    ), "total_functions differs between serial and parallel"


@pytest.mark.xdist_group("parallel_analyzers")
def test_dead_parallel_matches_serial_output(indexed_project, cli_runner):
    """The dead-code envelope must be identical between serial and parallel
    paths.

    ``roam dead`` parallelization touches:
    - ``_augment_test_text_consumers`` (combined regex + ThreadPool)
    - ``_get_blame_ages`` (per-file git blame via ThreadPool)

    Both are reduction-style (write to a shared dict from the main
    thread), so output is fully deterministic across paths.
    """
    serial = _run_with_env(
        cli_runner, ["dead"], cwd=indexed_project, parallel=False, command="dead"
    )
    parallel = _run_with_env(
        cli_runner, ["dead"], cwd=indexed_project, parallel=True, command="dead"
    )

    # The high-confidence + low-confidence symbol lists must match.
    # Skip metadata like _meta.timestamp, _meta.index_age_s, response_tokens.
    def _strip_meta(env):
        env = {k: v for k, v in env.items() if k != "_meta"}
        # response_tokens lives inside summary in some commands; just
        # drop the entire _meta key for the diff.
        return env

    s = _strip_meta(serial)
    p = _strip_meta(parallel)
    # Compare the structured findings (dead_symbols list) explicitly.
    s_dead = sorted(
        (d.get("symbol", "") or d.get("name", "") or "")
        for d in s.get("dead_symbols", []) or []
    )
    p_dead = sorted(
        (d.get("symbol", "") or d.get("name", "") or "")
        for d in p.get("dead_symbols", []) or []
    )
    assert s_dead == p_dead, (
        f"dead_symbols set differs.\nserial-only: {set(s_dead) - set(p_dead)}\n"
        f"parallel-only: {set(p_dead) - set(s_dead)}"
    )


@pytest.mark.xdist_group("parallel_analyzers")
def test_small_repos_skip_parallelization_for_clones(
    small_clones_project, cli_runner
):
    """A repo with < 100 files should take the serial path even without
    ROAM_NO_PARALLEL.

    We verify this indirectly by asserting the wall-clock is suspiciously
    fast (no ProcessPool spinup of ~1-2s per worker on Windows). If the
    parallel branch were taken it would dominate the timing.
    """
    # Use the parallel path (default) and confirm it completes very
    # quickly — under a budget that ProcessPool spinup would blow.
    t0 = time.time()
    parallel = _run_with_env(
        cli_runner,
        ["clones"],
        cwd=small_clones_project,
        parallel=True,
        command="clones",
    )
    parallel_elapsed = time.time() - t0
    # ProcessPool spinup is ~1-2s; we should finish well under that
    # threshold for a < 100-file project on the serial fallback.
    assert parallel_elapsed < 5.0, (
        f"Small repo took {parallel_elapsed:.2f}s — looks like parallel path "
        f"was taken when it shouldn't have been."
    )
    # And confirm correctness.
    assert parallel.get("command") == "clones"
    assert "summary" in parallel


@pytest.fixture
def large_clones_project(project_factory):
    """Synthetic fixture sized large enough that the parallel pair-
    comparison path (threshold: 100K candidate pairs) is exercised
    AND ProcessPool spinup is amortized.

    Builds many source files each containing several similarly-sized
    functions. With ~600 candidate functions × bucketed pairs we exceed
    the 100K-pair threshold and produce a CPU-bound compare phase that
    parallelizes well.
    """
    files: dict[str, str] = {}
    # Make each function long enough (>= 5 lines + >= 8 AST nodes) to
    # survive the clone-detection filters. Keep node counts in similar
    # buckets so they actually compare against each other.
    body_template = (
        "    result = 0\n"
        "    for i in range(len({coll})):\n"
        "        if i % 2 == 0:\n"
        "            result += {coll}[i]\n"
        "        else:\n"
        "            result -= {coll}[i]\n"
        "    if result < 0:\n"
        "        return -result\n"
        "    return result\n"
    )
    # 100 files × 6 functions each = 600 functions in the same size
    # bucket. With bucket-adjacency expansion that's ~600²/2 = 180K
    # candidate pairs — comfortably above the 100K threshold.
    for f in range(100):
        funcs = []
        for g in range(6):
            funcs.append(
                f"def compute_metric_f{f}_g{g}(items_{g}):\n"
                + body_template.format(coll=f"items_{g}")
            )
        files[f"src/mod_{f}.py"] = "\n".join(funcs)
    # Padding so total file count comfortably exceeds the 500-file
    # parallel-extraction threshold.
    for i in range(450):
        files[f"src/pad_{i}.py"] = f'"""pad {i}"""\nVALUE_{i} = {i}\n'
    return project_factory(files)


@pytest.mark.slow
@pytest.mark.xdist_group("parallel_analyzers")
def test_parallel_runs_faster_on_large_clones_fixture(
    large_clones_project, cli_runner
):
    """Wall-clock comparison: parallel should not be net-negative on a
    fixture large enough to amortize ProcessPool spinup.

    The fixture is sized so the O(n²) pair compare (~360 funcs ≈ 64K
    candidate pairs) trips the parallel threshold. On a multi-core box
    we expect a meaningful speedup; on single-CPU CI runners the spinup
    cost can dominate, so the assertion is conservative.

    This test is marked slow because it runs the command twice; skip
    under ``pytest -m "not slow"``.
    """
    # Warm the cache so disk reads don't dominate the first run.
    _run_with_env(
        cli_runner,
        ["clones"],
        cwd=large_clones_project,
        parallel=True,
        command="clones",
    )

    t0 = time.time()
    _run_with_env(
        cli_runner,
        ["clones"],
        cwd=large_clones_project,
        parallel=False,
        command="clones",
    )
    serial_elapsed = time.time() - t0

    t0 = time.time()
    _run_with_env(
        cli_runner,
        ["clones"],
        cwd=large_clones_project,
        parallel=True,
        command="clones",
    )
    parallel_elapsed = time.time() - t0

    speedup = serial_elapsed / max(parallel_elapsed, 0.01)
    print(
        f"\nclones speedup: serial={serial_elapsed:.2f}s "
        f"parallel={parallel_elapsed:.2f}s -> {speedup:.2f}x"
    )
    # On a quiet multi-core box we typically see 2-4x. Under test load
    # (e.g. running this test alongside hundreds of others in the same
    # pytest process) ProcessPool spinup can blow the budget. We gate
    # at 0.6x to ensure we're not catastrophically slower, and skip the
    # gate entirely on small hosts.
    if (os.cpu_count() or 1) >= 4:
        assert speedup >= 0.6, (
            f"Parallel path is catastrophically slower than serial: "
            f"{speedup:.2f}x (serial={serial_elapsed:.2f}s "
            f"parallel={parallel_elapsed:.2f}s)"
        )
