"""W43 — ``roam deps --multi`` batched coupling probe.

The structural-coupling probe (`_probe_coupling` in
``src/roam/plan/compiler.py``) historically fired TWO parallel
subprocesses per target: ``roam deps <path>`` for the structural
axis + ``_git_cochange_counts(<path>)`` for the temporal axis.
W43 collapses those into ONE subprocess per target via the new
``roam deps --multi`` flag, which emits ``cochange_pairs`` on the
envelope alongside imports / imported_by.

Telemetry-derived ROI: 548 ``structural_coupling`` compiles in the
2-day window × ~200ms saved per compile = ~110 seconds cumulative
saving once W43 lands.

Coverage:
1. ``--multi`` adds ``cochange_pairs`` to the JSON envelope.
2. Without ``--multi`` the envelope omits ``cochange_pairs`` (no
   regression of the default shape).
3. ``_probe_coupling`` dispatch uses ``--multi`` (source-level pin
   so a refactor that drops the flag is caught at lint time).
4. The downstream ``temporal_coupling_pairs`` fact is still
   populated when ``--multi`` returns cochange data.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._helpers.repo_root import repo_root

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402


def _invoke_deps(runner: CliRunner, cwd, *extra, json_mode: bool = True, detail: bool = True):
    """Invoke ``roam deps`` through the group so ``--json`` is honoured.

    ``detail=True`` matches the call shape used by ``_probe_coupling``
    in the compiler: list payloads (incl. ``cochange_pairs``) survive
    the ``strip_list_payloads`` projection only in detail mode.
    """
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    if detail:
        args.append("--detail")
    args.append("deps")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def deps_w43_project(tmp_path, monkeypatch):
    """Indexed corpus with one ``consumer -> helper`` import edge.

    git_init creates a single commit so ``--multi`` can run the
    git-log + git-show subprocess chain without errors. With only
    one commit, cochange_pairs will be empty — that's fine; the
    test checks the KEY is present.
    """
    proj = tmp_path / "deps_w43_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "helper.py").write_text(
        "def helper_fn():\n    return 'help'\n",
        encoding="utf-8",
    )
    (src / "consumer.py").write_text(
        "from src.helper import helper_fn\n\ndef use_it():\n    return helper_fn()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def test_w43_multi_emits_cochange_pairs_key(cli_runner, deps_w43_project):
    """W43 — ``--multi`` adds ``cochange_pairs`` to the envelope."""
    result = _invoke_deps(cli_runner, deps_w43_project, "src/consumer.py", "--multi")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "deps"
    assert "cochange_pairs" in data, (
        f"W43: --multi must surface cochange_pairs on the envelope; got: {sorted(data.keys())!r}"
    )
    pairs = data["cochange_pairs"]
    assert isinstance(pairs, list), f"cochange_pairs must be a list; got {type(pairs)!r}"
    # Each entry, if present, must be a {file, count} dict.
    for entry in pairs:
        assert isinstance(entry, dict)
        assert "file" in entry
        assert "count" in entry
        assert isinstance(entry["file"], str)
        assert isinstance(entry["count"], int)


def test_w43_default_omits_cochange_pairs(cli_runner, deps_w43_project):
    """W43 — default deps envelope is unchanged (no cochange_pairs key)."""
    result = _invoke_deps(cli_runner, deps_w43_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert "cochange_pairs" not in data, (
        f"W43: default shape must NOT carry cochange_pairs; only --multi adds it. got: {sorted(data.keys())!r}"
    )


def test_w43_probe_coupling_uses_multi():
    """W43 — _probe_coupling dispatches ``roam deps --multi`` per target.

    Source-level pin: a future refactor that drops the --multi flag
    would silently regress the win. We grep the compiler source for
    the ``["deps", t, "--multi"]`` shape inside _probe_coupling.
    """
    src_path = repo_root() / "src" / "roam" / "plan" / "compiler.py"
    src = src_path.read_text(encoding="utf-8")

    # The dispatch line lives inside the _probe_coupling function.
    marker_a = '"deps"'
    marker_b = '"--multi"'
    assert marker_a in src and marker_b in src, "W43: expected ``roam deps --multi`` dispatch shape in compiler.py"

    # Confirm both tokens co-occur on the same _run_roam line inside
    # _probe_coupling — guards against a stray --multi elsewhere
    # giving a false positive.
    import re

    pattern = re.compile(r'_run_roam,\s*\["deps",\s*[\w.]+,\s*"--multi"\]')
    assert pattern.search(src), 'W43: _probe_coupling must call _run_roam with ["deps", target, "--multi"]'


def test_w43_probe_coupling_consumes_cochange_pairs():
    """W43 — _probe_coupling extracts cochange_pairs into temporal_coupling_pairs.

    Functional pin: when ``roam deps --multi`` returns cochange data, the
    downstream ``temporal_coupling_pairs`` fact must surface it. We stub
    _run_roam so the test runs without requiring a real git history.
    """
    from roam.plan import compiler as _compiler

    captured_calls: list[list[str]] = []

    def _fake_run_roam(args, cwd, timeout: float = 8.0, detail: bool = False):
        captured_calls.append(list(args))
        # Simulate the --multi envelope: imports + imported_by + cochange_pairs.
        if "--multi" in args:
            return {
                "imports": [{"path": "src/dep.py", "symbol_count": 1, "used_symbols": []}],
                "imported_by": [],
                "cochange_pairs": [
                    {"file": "src/other.py", "count": 7},
                    {"file": "src/third.py", "count": 3},
                ],
            }
        return None

    orig = _compiler._run_roam
    _compiler._run_roam = _fake_run_roam
    try:
        facts = _compiler._probe_coupling(["src/cli.py"], cwd=None)
    finally:
        _compiler._run_roam = orig

    # The --multi call shape was dispatched.
    multi_calls = [c for c in captured_calls if "--multi" in c]
    assert multi_calls, f"expected a --multi dispatch; got {captured_calls!r}"

    # temporal_coupling_pairs fact populated from cochange_pairs.
    pairs = facts.get("temporal_coupling_pairs")
    assert pairs, f"expected temporal_coupling_pairs to be populated; got facts={facts!r}"
    assert pairs[0]["file_b"] == "src/other.py"
    assert pairs[0]["cochange_count"] == 7
