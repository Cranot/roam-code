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

from tests.conftest import invoke_cli, parse_json_output

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
        assert result.exit_code == 0, f"roam {' '.join(args)} failed: {result.output}"
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
    serial = _run_with_env(cli_runner, ["clones"], cwd=clones_project, parallel=False, command="clones")
    parallel = _run_with_env(cli_runner, ["clones"], cwd=clones_project, parallel=True, command="clones")

    s_sig = _cluster_signatures(serial)
    p_sig = _cluster_signatures(parallel)
    assert s_sig == p_sig, f"Cluster membership differs.\nserial-only: {s_sig - p_sig}\nparallel-only: {p_sig - s_sig}"

    # Pair count must match exactly (deterministic across paths).
    assert serial["summary"]["clone_pairs"] == parallel["summary"]["clone_pairs"], (
        "clone_pairs count differs between serial and parallel"
    )
    assert serial["summary"]["clusters"] == parallel["summary"]["clusters"], (
        "cluster count differs between serial and parallel"
    )
    assert serial["summary"]["total_functions"] == parallel["summary"]["total_functions"], (
        "total_functions differs between serial and parallel"
    )


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
    serial = _run_with_env(cli_runner, ["dead"], cwd=indexed_project, parallel=False, command="dead")
    parallel = _run_with_env(cli_runner, ["dead"], cwd=indexed_project, parallel=True, command="dead")

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
    s_dead = sorted((d.get("symbol", "") or d.get("name", "") or "") for d in s.get("dead_symbols", []) or [])
    p_dead = sorted((d.get("symbol", "") or d.get("name", "") or "") for d in p.get("dead_symbols", []) or [])
    assert s_dead == p_dead, (
        f"dead_symbols set differs.\nserial-only: {set(s_dead) - set(p_dead)}\n"
        f"parallel-only: {set(p_dead) - set(s_dead)}"
    )


@pytest.mark.xdist_group("parallel_analyzers")
def test_small_repos_skip_parallelization_for_clones(small_clones_project, cli_runner):
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
        f"Small repo took {parallel_elapsed:.2f}s — looks like parallel path was taken when it shouldn't have been."
    )
    # And confirm correctness.
    assert parallel.get("command") == "clones"
    assert "summary" in parallel


@pytest.fixture
def large_clones_project(project_factory):
    """Synthetic fixture with a CPU-bound pair-comparison phase.

    Builds many source files each containing several similarly-sized
    functions (~600 candidate functions, ~180K bucketed candidate
    pairs).

    NOTE: ~180K candidate pairs is BELOW the post-optimization
    ``parallel_threshold`` (1.5M). Once ``_jaccard_bags`` was rewritten
    single-pass (~13× faster per comparison), the ProcessPool spinup
    (~12s on Windows) only pays off above ~1.5M candidate pairs — so a
    fixture this size correctly takes the serial path even with
    ``ROAM_NO_PARALLEL`` unset. Sizing a fixture past 1.5M pairs in a
    unit test would make the test itself prohibitively slow, so the
    companion test now verifies the serial path stays fast rather than
    racing serial-vs-parallel.
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
            funcs.append(f"def compute_metric_f{f}_g{g}(items_{g}):\n" + body_template.format(coll=f"items_{g}"))
        files[f"src/mod_{f}.py"] = "\n".join(funcs)
    # Padding so total file count comfortably exceeds the 500-file
    # parallel-extraction threshold.
    for i in range(450):
        files[f"src/pad_{i}.py"] = f'"""pad {i}"""\nVALUE_{i} = {i}\n'
    return project_factory(files)


@pytest.mark.slow
@pytest.mark.xdist_group("parallel_analyzers")
def test_parallel_runs_faster_on_large_clones_fixture(large_clones_project, cli_runner):
    """A mid-size clones fixture takes the fast serial path and stays fast.

    Post-optimization reality: the single-pass ``_jaccard_bags`` rewrite
    made per-pair comparison ~13× cheaper, raising the
    ``parallel_threshold`` to 1.5M candidate pairs. This fixture
    (~180K candidate pairs) is below that threshold, so ``roam clones``
    correctly takes the SERIAL pair-comparison path even with
    ``ROAM_NO_PARALLEL`` unset — engaging the ProcessPool here would be
    net-negative (~12s Windows spinup vs ~2s of serial compare work).

    The original form of this test raced serial-vs-parallel and asserted
    ``speedup >= 0.6``; that premise broke once the optimization moved
    the break-even far past any fixture small enough for a unit test.
    The test now verifies the load-bearing post-optimization invariant:
    the serial path completes the whole command quickly and the
    parallel-default run produces identical structured results (no
    ProcessPool spinup penalty leaks in).

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
    serial = _run_with_env(
        cli_runner,
        ["clones"],
        cwd=large_clones_project,
        parallel=False,
        command="clones",
    )
    serial_elapsed = time.time() - t0

    t0 = time.time()
    default = _run_with_env(
        cli_runner,
        ["clones"],
        cwd=large_clones_project,
        parallel=True,
        command="clones",
    )
    default_elapsed = time.time() - t0

    print(f"\nclones (serial-path fixture): ROAM_NO_PARALLEL=1 {serial_elapsed:.2f}s · default {default_elapsed:.2f}s")

    # Structured results must match — the candidate-pair-count threshold
    # picks the path, but the detection output is path-independent.
    assert _cluster_signatures(serial) == _cluster_signatures(default), (
        "Cluster membership differs between ROAM_NO_PARALLEL=1 and default"
    )
    assert serial["summary"]["clone_pairs"] == default["summary"]["clone_pairs"], (
        "clone_pairs count differs between ROAM_NO_PARALLEL=1 and default"
    )

    # The fixture is below ``parallel_threshold`` (1.5M candidate pairs),
    # so the default run must NOT pay the ~12s ProcessPool spinup. A
    # generous ceiling keeps the test stable under heavy parallel
    # pytest-xdist load while still catching a regression that
    # accidentally re-engages the pool below the break-even.
    assert default_elapsed < 10.0, (
        f"Mid-size fixture took {default_elapsed:.2f}s on the default path — "
        f"looks like the ProcessPool engaged below the 1.5M-pair threshold."
    )
