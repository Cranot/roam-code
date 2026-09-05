"""Small browser/NodeNext fixtures for cross-command precision regressions."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.analysis.effects import READS_DB, WRITES_DB, classify_symbol_effects
from roam.cli import cli


@pytest.mark.parametrize(
    "specifier,source",
    [
        ("worker.js", "worker.ts"),
        ("worker.js", "worker.tsx"),
        ("worker.mjs", "worker.mts"),
        ("worker.cjs", "worker.cts"),
    ],
)
def test_nodenext_relative_resolution(tmp_path, specifier, source):
    from roam.commands.cmd_verify_imports import _js_relative_import_resolves

    (tmp_path / source).write_text("export const value = 1;\n", encoding="utf-8")
    assert _js_relative_import_resolves(str(tmp_path), "main.ts", "./" + specifier)
    assert not _js_relative_import_resolves(str(tmp_path), "main.ts", "./absent.js")


def test_import_commands_agree_on_nodenext_and_declared_packages(project_factory, monkeypatch):
    project = project_factory(
        {
            "package.json": '{"private":true,"workspaces":["client"]}',
            "client/package.json": '{"name":"browser-app","devDependencies":{"vitest":"^3.0.0"}}',
            "client/src/math.ts": "export function square(n: number) { return n * n; }\n",
            "client/src/main.ts": "import { square } from './math.js';\nimport { defineConfig } from 'vitest/config';\nexport const result = square(3);\n",
            "client/src/broken.ts": "import { missing } from './absent.js';\nexport const broken = missing;\n",
        }
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    orphan = runner.invoke(cli, ["--json", "orphan-imports"])
    assert orphan.exit_code == 0, orphan.output
    payload = json.loads(orphan.output)
    rows = [row["value"] for row in payload["orphans"]]
    assert [(r["module"], r["language"]) for r in rows] == [("./absent.js", "typescript")]
    verify = runner.invoke(cli, ["--json", "verify-imports"])
    assert verify.exit_code == 0, verify.output
    payload = json.loads(verify.output)
    assert payload["summary"]["unresolved"] == 1, payload


@pytest.mark.parametrize(
    "query,target",
    [
        ("what breaks if I change SEGMENT_LENGTH", "SEGMENT_LENGTH"),
        ("impact of traceBodyAlongPath", "traceBodyAlongPath"),
        ("what calls Renderer.update", "Renderer.update"),
    ],
)
def test_ask_preserves_js_identifiers(query, target):
    from roam.ask.runner import extract_recipe_symbol

    assert extract_recipe_symbol(query) == target


def test_ask_keywords_do_not_match_inside_identifiers():
    from roam.ask.classifier import classify

    assert classify("impact of traceBodyAlongPath")[0][0].name == "explore-impact"


def _js_signals(code, name):
    from tree_sitter_language_pack import get_parser

    from roam.index.complexity import _extract_math_signals

    source = code.encode()
    node = get_parser("typescript").parse(source).root_node.named_children[0]
    return _extract_math_signals(node, source, name)


def test_child_lifecycle_calls_are_not_recursion():
    signals = _js_signals("function destroy() { child.destroy(); other.destroy(); }", "destroy")
    assert signals["self_call_count"] == 0
    signals = _js_signals("function fib(n) { return fib(n-1) + fib(n-2); }", "fib")
    assert signals["self_call_count"] == 2


def test_loop_local_values_are_not_hoistable():
    signals = _js_signals(
        "function measure(points) { for (const p of points) { const dx = p.x; const dy = p.y; consume(Math.sqrt(dx*dx + dy*dy)); stable(config); } }",
        "measure",
    )
    assert "Math.sqrt" not in signals["loop_invariant_calls"]
    assert "stable" in signals["loop_invariant_calls"]


def test_scheduled_name_match_does_not_match_joystick():
    from roam.commands.cmd_entry_points import _classify_protocol

    assert _classify_protocol("joystickSize", "get joystickSize(): number") == "Export"
    assert _classify_protocol("tick", "tick()") == "Scheduled"


def test_test_signal_counts_import_mapping(project_factory, monkeypatch):
    from roam.commands.cmd_ai_readiness import _score_test_signal
    from roam.db.connection import open_db

    project = project_factory(
        {
            "src/geometry.ts": "export function square(n: number) { return n * n; }\n",
            "tests/roundtrip.test.ts": "import { square } from '../src/geometry';\nexport function testRoundtrip() { return square(5); }\n",
        }
    )
    monkeypatch.chdir(project)
    with open_db(readonly=True) as conn:
        score, details = _score_test_signal(conn)
    assert details["with_tests"] == 1, details
    assert score > 0


@pytest.mark.parametrize(
    "body",
    [
        "sprite.destroy();",
        "ctx.save();",
        "AudioContext.create();",
        "app.run();",
        "items.find(predicate);",
        "selection.select(node);",
    ],
)
def test_browser_methods_are_not_database_evidence(body):
    assert not ({WRITES_DB, READS_DB} & classify_symbol_effects(body, "typescript"))


def test_canvas_and_game_begin_are_not_transactions():
    from roam.world_model.tx_boundaries import _scan_body

    result = _scan_body(["ctx.beginPath();", "hud.beginRound();", "items.begin();"], 1)
    assert result["begin_markers"] == []


def test_magic_numbers_defaults_to_monorepo_sources(tmp_path, monkeypatch):
    from roam.commands.cmd_magic_numbers import magic_numbers

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "client" / "game.py"
    source.parent.mkdir()
    source.write_text("def update():\n    return 4321 + 4321\n", encoding="utf-8")
    ignored = tmp_path / "node_modules" / "dependency.py"
    ignored.parent.mkdir()
    ignored.write_text("def noisy():\n    return 5678 + 5678\n", encoding="utf-8")
    result = CliRunner().invoke(magic_numbers, [], obj={"json": True})
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["files_scanned"] == 1
    assert [f["value"] for f in data["findings"]] == [4321]
    assert data["findings"][0]["sites"][0]["file"] == "client/game.py"


def test_browser_mutations_are_visible_without_claiming_database_transactions():
    from roam.world_model.side_effects import SideEffectClassification, _classify_one_symbol
    from roam.world_model.tx_boundaries import _classify_one

    body = "element.classList.toggle('collapsed');\nlocalStorage.setItem('state', 'yes');\nctx.beginPath();"
    kinds, _, _ = _classify_one_symbol(body, [], set())
    assert {"mutation", "io_write"} <= set(kinds)
    classification = _classify_one(SideEffectClassification(symbol="render", file="ui.ts", kinds=kinds), body, 1)
    assert classification.classification == "non_transactional"
    assert classification.begin_markers == []


def test_browser_css_toggle_is_not_a_feature_flag(tmp_path):
    from roam.commands.cmd_flag_dead import scan_file_for_flags

    path = tmp_path / "view.ts"
    path.write_text("element.classList.toggle('collapsed');\nisFeatureEnabled('real-flag');\n", encoding="utf-8")
    assert [row["flag_name"] for row in scan_file_for_flags(str(path))] == ["real-flag"]


def test_cycle_paths_follow_real_edges_not_sorted_members():
    import networkx as nx

    from roam.commands.cmd_cycle_break import _analyze_cycle

    graph = nx.DiGraph()
    for i in range(10):
        graph.add_node(i, path=f"{i}.ts")
        graph.add_edge(i, (i + 3) % 10)
        graph.add_edge(i, (i + 7) % 10)
    result = _analyze_cycle(None, graph, list(graph), ".", {}, {})
    cycle = result["cycle_path"]
    assert cycle[0] == cycle[-1]
    assert all(graph.has_edge(int(a[:-3]), int(b[:-3])) for a, b in zip(cycle, cycle[1:]))
    assert len(result["members"]) == 10


def test_default_partition_is_bounded_with_isolated_symbols(project_factory, monkeypatch):
    from roam.commands.cmd_partition import compute_partition_manifest
    from roam.db.connection import open_db

    project = project_factory({"helpers.py": "\n".join(f"def helper_{n}(): return {n}" for n in range(24))})
    monkeypatch.chdir(project)
    with open_db(readonly=True) as conn:
        manifest = compute_partition_manifest(conn)
    assert manifest["n_agents"] <= 8
    assert len(manifest["partitions"]) <= 8
    assert sum(p["symbol_count"] for p in manifest["partitions"]) == 24


def test_algorithm_detectors_require_the_actual_loop_pattern(project_factory, monkeypatch):
    from roam.catalog.detectors import detect_manual_power, detect_serial_await_loop, detect_spread_accumulator
    from roam.db.connection import open_db

    project = project_factory(
        {
            "src/operations.ts": """
export function powerBonus(items) { for (const x of items) { return x.stacks * 20; } }
export function powerLoop(n) { let result = 1; for (let i = 0; i < n; i++) { result *= 2; } return result; }
export function copyState(values) { for (const x of values) { touch(x); } this.values = {...values}; }
export function grow(items) { let acc = []; for (const x of items) { acc = [...acc, x]; } return acc; }
export async function afterLoop(items) { for (const x of items) { touch(x); } await send(items); }
export async function insideLoop(items) { for (const x of items) { await send(x); } }
"""
        }
    )
    monkeypatch.chdir(project)
    with open_db(readonly=True) as conn:
        assert [r["symbol_name"] for r in detect_manual_power(conn)] == ["powerLoop"]
        assert [r["symbol_name"] for r in detect_spread_accumulator(conn)] == ["grow"]
        assert [r["symbol_name"] for r in detect_serial_await_loop(conn)] == ["insideLoop"]


def test_empty_handler_locations_are_actionable(project_factory, monkeypatch):
    from roam.commands.cmd_vibe_check import _detect_empty_handlers
    from roam.db.connection import open_db

    project = project_factory({"src/main.ts": "export function execute() {\n  try { run(); } catch (error) {}\n}\n"})
    monkeypatch.chdir(project)
    with open_db(readonly=True) as conn:
        found, _, details = _detect_empty_handlers(conn, project)
    assert found == 1
    assert details[0]["lines"] == [2]


def test_mutable_module_binding_keeps_variable_kind(project_factory, monkeypatch):
    from roam.db.connection import open_db

    project = project_factory({"src/settings.ts": "export let activeMode = true;\nexport const MAX_SIZE = 25;\n"})
    monkeypatch.chdir(project)
    with open_db(readonly=True) as conn:
        assert conn.execute("SELECT kind FROM symbols WHERE name = 'activeMode'").fetchone()[0] == "variable"


def test_safe_delete_does_not_prove_absence_from_file_imports(project_factory, monkeypatch):
    project = project_factory(
        {
            "src/library.py": "def unused_candidate(): return 1\ndef used(): return 2\n",
            "src/main.py": "from library import used\ndef main(): return used()\n",
        }
    )
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["--json", "safe-delete", "unused_candidate"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["file_imported"] is True
    assert payload["verdict"] == "REVIEW", payload


def test_imported_dead_confidence_is_not_delete_grade():
    from roam.commands.cmd_dead import _dead_action

    action, confidence = _dead_action(
        {"name": "unused_candidate", "file_path": "src/library.py", "kind": "function"}, file_imported=True
    )
    assert (action, confidence) == ("REVIEW", 30)


def test_ai_ratio_discloses_heuristic_legacy_fields():
    from roam.commands.cmd_ai_ratio import _ai_ratio_json_payload, _ai_ratio_verdict

    result = {
        "ai_ratio": 0.7,
        "confidence": "HIGH",
        "commits_analyzed": 80,
        "signals": {},
        "top_ai_files": [],
        "trend": {},
    }
    verdict = _ai_ratio_verdict(result)
    payload = _ai_ratio_json_payload(result, verdict, 90)
    assert "not an authorship estimate" in verdict
    assert payload["attribution_supported"] is False
    assert "uncalibrated" in payload["summary"]["ai_ratio_definition"]
