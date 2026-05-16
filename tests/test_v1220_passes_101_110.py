"""Tests for v12.20 quality passes 101-110."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_pass101_query_engine_split_helpers_callable():
    """Pass 101 split helpers are present and callable."""
    from roam.languages.query_engine import QueryEngine

    # Spot-check that the new helpers exist on the class
    assert hasattr(QueryEngine, "_find_name_node")
    assert hasattr(QueryEngine, "_decode_capture")
    assert hasattr(QueryEngine, "_resolve_kotlin_class_kind")
    assert hasattr(QueryEngine, "_build_symbol_from_def")


def test_pass102_render_helpers_extracted():
    """context helpers exist."""
    from roam.commands import cmd_context as m

    assert callable(m._render_async_badge)
    assert callable(m._render_idiom_badge)
    assert callable(m._render_decorators_block)
    # Paren-aware split must keep `parametrize("a,b", x)` together.
    out = m._split_decorators_paren_aware('parametrize("a,b", [1]),other')
    assert len(out) == 2
    assert 'parametrize("a,b", [1])' in out[0]
    assert "other" in out[1]


def test_pass104_doctor_command_registry_loads():
    """doctor still validates the command registry after the cycle break."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "doctor"])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    names = {c["name"] for c in payload.get("checks", [])}
    assert "CLI command registry" in names


def test_pass105_health_classifies_infrastructure_as_utility():
    """cli.py / mcp_server.py / graph/ counted as utilities."""
    from roam.commands.cmd_health import _is_utility_path

    assert _is_utility_path("src/roam/cli.py")
    assert _is_utility_path("src/roam/mcp_server.py")
    assert _is_utility_path("src/roam/graph/builder.py")
    assert _is_utility_path("src/roam/index/file_roles.py")
    # Non-utility paths still classified actionable
    assert not _is_utility_path("src/roam/commands/cmd_health.py")


def test_pass106_dataflow_dead_helpers_extracted():
    """dataflow analyzer split into per-pattern helpers."""
    from roam.commands import cmd_dead as m

    assert callable(m._table_exists)
    assert callable(m._detect_unused_returns)
    assert callable(m._detect_dead_param_chains)
    assert callable(m._detect_side_effect_only)
    # _parse_param_names should ignore self/cls
    assert m._parse_param_names("(self, x: int, y: str = 'a')") == ["x", "y"]
    assert m._parse_param_names("(*args, **kwargs)") == ["args", "kwargs"]


def test_pass108_test_map_unknown_symbol_emits_json():
    """test-map for a missing symbol returns a JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "test-map", "TotallyNotASymbolXYZ123"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["command"] == "test-map"
    assert payload["summary"]["found"] is False


def test_pass110_orphan_imports_no_false_internal_typos():
    """newly-added modules don't get flagged as internal_typo."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "orphan-imports", "--lang", "python"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    # R22 confidence triple shape — each orphan is now
    # {"value": {...}, "confidence": ..., "reason": ...}.
    typos = [o for o in payload.get("orphans", []) if o.get("value", o).get("kind") == "internal_typo"]
    # roam.telemetry / roam.observability used to dominate this list
    fp_modules = {o["value"]["module"] for o in typos}
    assert "roam.telemetry" not in fp_modules
    assert "roam.observability" not in fp_modules


def test_pass110_modules_from_path_helper():
    """_modules_from_path adds every dotted prefix."""
    from roam.commands.cmd_orphan_imports import _modules_from_path

    out: set[str] = set()
    _modules_from_path("src/roam/commands/cmd_x.py", out)
    assert "roam" in out
    assert "roam.commands" in out
    assert "roam.commands.cmd_x" in out
    # __init__.py collapses to package name
    out2: set[str] = set()
    _modules_from_path("src/roam/__init__.py", out2)
    assert "roam" in out2
