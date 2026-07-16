"""Tests for ``roam calc-probe`` — the empirical rounding differential."""

from __future__ import annotations

import json
import shutil

import pytest
from click.testing import CliRunner

from roam.commands.cmd_calc_probe import (
    _IDIOMS,
    _PROBE_INPUTS,
    _normalize_cents,
    _run_idioms_for_runtime,
    calc_probe,
)


def test_normalize_cents():
    assert _normalize_cents("1") == "1.00"
    assert _normalize_cents("1.0") == "1.00"
    assert _normalize_cents("1.00") == "1.00"
    assert _normalize_cents("-0.00") == "0.00"
    assert _normalize_cents("2.675") == "2.67"  # float() then %.2f — representation-honest
    assert _normalize_cents("garbage") == "garbage"


def test_catalog_ids_unique_and_runtime_known():
    ids = [i["id"] for i in _IDIOMS]
    assert len(ids) == len(set(ids))
    assert {i["runtime"] for i in _IDIOMS} <= {"python", "node", "php"}


def test_python_runtime_executes_deterministically():
    py = [i for i in _IDIOMS if i["runtime"] == "python"]
    r1 = _run_idioms_for_runtime("python", py, _PROBE_INPUTS)
    r2 = _run_idioms_for_runtime("python", py, _PROBE_INPUTS)
    assert r1 and r1 == r2  # same inputs, same outputs — determinism contract
    assert all(len(v) == len(_PROBE_INPUTS) for v in r1.values())


def test_python_only_divergence_detected():
    """python:round vs Decimal HALF_UP disagree on 1.005 — so even with ONLY the
    always-available python runtime, the probe must report divergence. This is
    the CI-safe core assertion (node/php may be absent on runners)."""
    runner = CliRunner()
    result = runner.invoke(calc_probe, [], obj={"json": True})
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["summary"]["divergent_inputs"] >= 1
    diverging = {str(d["input"]) for d in env["divergences"]}
    assert "1.005" in diverging
    # every skipped runtime is disclosed, never silent
    for rt in ("node", "php"):
        if shutil.which(rt) is None:
            assert rt in env["summary"]["runtimes_skipped"]


def test_unknown_runtime_returns_empty():
    assert _run_idioms_for_runtime("cobol", [], _PROBE_INPUTS) == {}


def test_path_not_found_exits_2():
    runner = CliRunner()
    result = runner.invoke(calc_probe, ["/no/such/path/xyz"], obj={"json": True})
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "path_not_found"


def _grammars_available() -> bool:
    try:
        from tree_sitter_language_pack import get_parser

        get_parser("php")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _grammars_available(), reason="tree-sitter grammars unavailable")
def test_scoped_mode_narrows_to_used_idioms(tmp_path):
    (tmp_path / "a.php").write_text("<?php $vat = round($base * $rate, 2);")
    runner = CliRunner()
    result = runner.invoke(calc_probe, [str(tmp_path)], obj={"json": True})
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    ran = env["summary"]["idioms_ran"]
    skipped = env["summary"]["runtimes_skipped"]
    if shutil.which("php"):
        assert ran == ["php:round"]
    else:
        assert ran == [] and "php" in skipped
    assert "scoped to" in env["summary"]["scope"]


@pytest.mark.skipif(not _grammars_available(), reason="tree-sitter grammars unavailable")
def test_multi_path_unions_idioms(tmp_path):
    """Two 'repos' (a PHP backend and a TS frontend) probed together — the
    cross-repo comparison. The union must include both sides' idioms."""
    back = tmp_path / "back"
    front = tmp_path / "front"
    back.mkdir()
    front.mkdir()
    (back / "vat.php").write_text("<?php $vat = round($base * $rate, 2);")
    (front / "vat.ts").write_text("const vat = Math.round(base * rate * 100) / 100;")
    runner = CliRunner()
    result = runner.invoke(calc_probe, [str(back), str(front)], obj={"json": True})
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    expected = set()
    if shutil.which("php"):
        expected.add("php:round")
    if shutil.which("node"):
        expected.add("javascript:round")
    assert set(env["summary"]["idioms_ran"]) == expected
    # with both runtimes present, the frontend<->backend cent divergence on
    # 1.005 must surface (php 1.01 vs js 1.00)
    if shutil.which("php") and shutil.which("node"):
        assert any(d["input"] == 1.005 for d in env["divergences"])


@pytest.mark.skipif(not _grammars_available(), reason="tree-sitter grammars unavailable")
def test_unprobed_used_idioms_disclosed(tmp_path):
    # floor is a recognized rounding fn but has no probe-catalog entry -> disclosed
    (tmp_path / "f.php").write_text("<?php $cents = floor($x * 100);")
    runner = CliRunner()
    env = json.loads(runner.invoke(calc_probe, [str(tmp_path)], obj={"json": True}).output)
    assert any("floor" in u for u in env["summary"]["unprobed_used_idioms"])
