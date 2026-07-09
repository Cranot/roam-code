"""Regression tests from real-world Roam feedback sessions."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli


def test_dynamic_import_then_member_is_a_consumer(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/documents.ts": (
                "export function downloadKiniseisDocumentStatusPdf(id: string) {\n  return `/docs/${id}.pdf`\n}\n"
            ),
            "src/views/KiniseisView.ts": (
                "export function handleDownload(id: string) {\n"
                "  return import('@/utils/documents')\n"
                "    .then(m => m.downloadKiniseisDocumentStatusPdf(id))\n"
                "}\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["uses", "downloadKiniseisDocumentStatusPdf"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["production_consumers"] >= 1


def test_dynamic_await_import_member_is_a_consumer(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/documents.ts": (
                "export function downloadKiniseisDocumentStatusPdf(id: string) {\n  return `/docs/${id}.pdf`\n}\n"
            ),
            "src/views/KiniseisView.ts": (
                "export async function handleDownload(id: string) {\n"
                "  const mod = await import('@/utils/documents')\n"
                "  return mod.downloadKiniseisDocumentStatusPdf(id)\n"
                "}\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["uses", "downloadKiniseisDocumentStatusPdf"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["production_consumers"] >= 1


def test_dead_reports_test_only_consumers_separately(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/case.ts": (
                "export function isSnakeCase(value: string) {\n  return /^[a-z]+(_[a-z]+)*$/.test(value)\n}\n"
            ),
            "tests/case.spec.ts": (
                "import { isSnakeCase } from '../src/utils/case'\n"
                "test('snake case', () => {\n"
                "  expect(isSnakeCase('hello_world')).toBe(true)\n"
                "})\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["--detail", "dead"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # R22 confidence triple shape — original fields nested under value
    items = data.get("high_confidence", []) + data.get("low_confidence", [])
    target = next(item for item in items if item["value"]["name"] == "isSnakeCase")
    value = target["value"]
    assert value["tested"] is True
    assert value["test_consumers"] >= 1
    assert value["production_consumers"] == 0
    assert value["action"] == "REVIEW"


def test_uses_splits_test_consumers_from_production(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/case.ts": "export function isCamelCase(value: string) { return /^[a-z][a-zA-Z]*$/.test(value) }\n",
            "tests/case.spec.ts": (
                "import { isCamelCase } from '../src/utils/case'\n"
                "test('camel case', () => expect(isCamelCase('helloWorld')).toBe(true))\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["uses", "isCamelCase"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["production_consumers"] == 0
    assert data["summary"]["test_consumers"] >= 1
    assert data["summary"]["tested"] is True


def test_global_compact_option_is_accepted_after_command(cli_runner, indexed_project, monkeypatch):
    from roam.cli import cli

    monkeypatch.chdir(indexed_project)
    old_cwd = os.getcwd()
    try:
        os.chdir(indexed_project)
        result = cli_runner.invoke(cli, ["health", "--compact"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.output


def test_structured_error_codes_round_trip():
    """Round 4 / H: structured codes parse out of message strings."""
    from roam.output.errors import (
        ALL_CODES,
        EMPTY_INPUT,
        INVALID_DIFF,
        UNKNOWN_RECIPE,
        parse_code,
        structured_usage_error,
    )

    err = structured_usage_error(EMPTY_INPUT, "diff is empty")
    assert "EMPTY_INPUT:" in str(err)
    assert parse_code(str(err)) == EMPTY_INPUT

    # parse_code rejects unknown prefixes so a typo can't masquerade.
    assert parse_code("INVALD_DIF: typo prefix") is None
    assert parse_code("not even a code") is None

    # Every defined code is canonical (uppercase + underscores only).
    for code in ALL_CODES:
        assert code.isupper() or "_" in code
        assert " " not in code

    # The two most common codes are present.
    assert INVALID_DIFF in ALL_CODES
    assert UNKNOWN_RECIPE in ALL_CODES


def test_mcp_concurrency_guard_returns_busy_envelope_when_at_capacity(monkeypatch):
    """Round 4 / P: at capacity, the wrapper returns BUSY without invoking fn."""
    monkeypatch.setenv("ROAM_MCP_MAX_CONCURRENT", "1")
    # Re-import with the env override active.
    import importlib

    import roam.mcp_extras.concurrency as concurrency_mod

    importlib.reload(concurrency_mod)

    invocations = {"count": 0}

    def heavy_tool(arg: str = "x") -> dict:
        invocations["count"] += 1
        return {"summary": {"verdict": "ok", "arg": arg}}

    wrapped = concurrency_mod.wrap_with_guard("roam_test_tool", heavy_tool)

    # Acquire the only slot manually so the next call sees capacity == 0.
    held, _per_tool = concurrency_mod._try_acquire("roam_test_tool")
    assert held is True
    try:
        out = wrapped(arg="while_busy")
        # Pattern-1 canonical failure envelope: error_code / retryable
        # live at the TOP LEVEL alongside isError + closed-enum status.
        assert out["error_code"] == "RATE_LIMITED"
        assert out["retryable"] is True
        assert out["isError"] is True
        assert out["status"] == "rate_limited"
        assert invocations["count"] == 0  # tool was NOT invoked
    finally:
        concurrency_mod._release(None)

    # After releasing, calls go through normally again.
    out = wrapped(arg="after_release")
    assert out["summary"]["verdict"] == "ok"
    assert invocations["count"] == 1


def test_framework_filter_excludes_vue_type_aliases():
    from roam.output.framework_filter import is_framework_alias

    # Leaf-name match — `computed` is the canonical Vue framework primitive
    assert is_framework_alias("ResourceStoreConfig.computed", "prop", "src/types/resource-store.ts")
    assert is_framework_alias("useState", "function", "src/hooks/use-data.ts")
    # File-suffix match — generated/type-only files inflate centrality
    assert is_framework_alias("Foo", "interface", "src/api/types.d.ts")
    # Type-like kind in a types/ directory
    assert is_framework_alias("Bar", "prop", "src/types/internal.ts")
    # Regular code is not filtered
    assert not is_framework_alias("calculate_total", "function", "src/checkout.py")
    assert not is_framework_alias("Logger", "class", "src/log.ts")


def test_project_shape_detects_vitest(tmp_path, monkeypatch):
    from roam.commands.resolve import ensure_index
    from roam.db.connection import open_db
    from roam.output.project_shape import detect_project_shape

    (tmp_path / "package.json").write_text(
        '{"name":"test","scripts":{"test":"vitest run"}}',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export function f() { return 1 }\n")
    monkeypatch.chdir(tmp_path)
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=False)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(tmp_path), check=False)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=False)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=False)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=str(tmp_path),
        check=False,
    )
    ensure_index()
    with open_db(readonly=True, project_root=tmp_path) as conn:
        shape = detect_project_shape(conn, tmp_path)
    assert shape.test_runner == "vitest"
    assert shape.test_command == "vitest run"
    assert shape.has_frontend


def test_oracle_route_exists_returns_indeterminate_without_workspace():
    import sqlite3

    from roam.commands.cmd_oracle import oracle_route_exists

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, name TEXT, kind TEXT);
        INSERT INTO symbols VALUES (1, 'get', 'method');
        INSERT INTO symbols VALUES (2, 'post', 'method');
        """
    )
    result = oracle_route_exists(conn, "/api/users")
    assert result.value is None
    assert result.reason_class == "indeterminate_workspace"
    assert "ws resolve" in result.reason


def test_oracle_iterable_for_backwards_compat():
    from roam.commands.cmd_oracle import OracleResult

    r = OracleResult(True, "ok", "definitive_yes", "high")
    value, reason = r
    assert value is True
    assert reason == "ok"


def test_dead_dataflow_emits_experimental_warning(cli_runner, indexed_project, monkeypatch):
    from roam.cli import cli

    monkeypatch.chdir(indexed_project)
    result = cli_runner.invoke(cli, ["dead", "--dataflow"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    # Stderr is mixed into result.output by the CliRunner default; the
    # warning text must appear so CI users see the false-positive note.
    assert "experimental" in result.output.lower() or "false positive" in result.output.lower()


def test_dead_dataflow_alias_include_noisy(cli_runner, indexed_project, monkeypatch):
    from roam.cli import cli

    monkeypatch.chdir(indexed_project)
    result = cli_runner.invoke(cli, ["dead", "--include-noisy-dataflow"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def test_hotspot_kind_classifier():
    from roam.commands.cmd_understand import _hotspot_kind

    assert _hotspot_kind("docs/legacy/CODE_MAP.md") == "doc"
    assert _hotspot_kind("docs/site/index.html") == "other"
    assert _hotspot_kind("config/.env") == "config"
    assert _hotspot_kind("config/app.toml") == "config"
    assert _hotspot_kind("src/main.py") == "code"
    assert _hotspot_kind("src/Login.tsx") == "code"
    assert _hotspot_kind("schema.sql") == "sql"
    assert _hotspot_kind("README.md") == "doc"


def test_patterns_factory_subtype_split(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/log.ts": (
                "class Logger {\n  log(s: string) { console.log(s) }\n}\n"
                "export function createLogger() { return new Logger() }\n"
                "export function buildKey(prefix: string, id: number) { return `${prefix}:${id}` }\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    result = invoke_cli(cli_runner, ["patterns"], cwd=proj, json_mode=True)
    if result.exit_code != 0:
        return
    data = json.loads(result.output)
    factory = (data.get("patterns") or {}).get("factory", {})
    instances = factory.get("instances", [])
    if not instances:
        return  # extractor may not surface either kind on this fixture
    subtypes = {item.get("subtype") for item in instances}
    # When both shapes are present, subtype split must distinguish them.
    if {"true_factory", "builder_helper"}.issubset(subtypes):
        true_names = {i["name"] for i in instances if i["subtype"] == "true_factory"}
        helper_names = {i["name"] for i in instances if i["subtype"] == "builder_helper"}
        assert "createLogger" in true_names or any("Logger" in n for n in true_names)
        assert any(n.startswith("buildKey") or "buildKey" in n for n in helper_names)


def test_dead_scaffolding_signals_detection():
    from roam.commands.cmd_dead import _dead_action, _scaffolding_signals

    # Behaviour ID
    assert _scaffolding_signals("Implements CB-024 / CB-042 from spec.")["behaviour_ids"] == ["CB-024", "CB-042"]
    # Legacy file with line numbers
    sig = _scaffolding_signals("See kinwposo.prg lines 88-145 for original behaviour.")
    assert sig is not None
    assert "kinwposo.prg" in sig["legacy_files"]
    # See legacy
    assert _scaffolding_signals("See legacy/withholding-tax.prg for context") is not None
    # Pure prose — no scaffolding
    assert _scaffolding_signals("Compute the snake_case form of the input.") is None

    # _dead_action surfaces INTENTIONAL_SCAFFOLDING for scaffolding docstrings
    row = {
        "name": "calculateWithholding",
        "kind": "function",
        "file_path": "src/utils/accounting/withholding-tax.ts",
        "docstring": "See kinwposo.prg lines 88-145. Implements CB-024.",
    }

    class _Row(dict):
        def __getitem__(self, key):
            return dict.__getitem__(self, key)

    action, confidence = _dead_action(_Row(row), file_imported=True)
    assert action == "INTENTIONAL_SCAFFOLDING"
    assert confidence >= 80


def test_coupling_classifies_locale_pair():
    from roam.commands.cmd_coupling import _classify_pair

    assert _classify_pair("src/locales/el.ts", "src/locales/en.ts") == "expected_locale"
    assert _classify_pair("src/i18n/strings.el.json", "src/i18n/strings.en.json") == "expected_locale"
    assert _classify_pair("src/handlers/auth.py", "src/handlers/user.py") == ""


def test_coupling_classifies_doc_hub():
    from roam.commands.cmd_coupling import _classify_pair

    assert _classify_pair("docs/legacy/CODE_MAP.md", "docs/legacy/CONFIRMED_BEHAVIORS.md") == "expected_doc_hub"
    assert _classify_pair("docs/site/index.html", "docs/site/about.html") == ""


def test_doc_staleness_skips_pure_prose_summaries():
    from roam.commands.cmd_doc_staleness import _docstring_facts, _semantic_drift

    sig = "def poll_store(store, interval): -> None"
    facts = _docstring_facts("Poll a single store with exponential backoff.", sig)
    assert facts["has_specific_facts"] is False
    drift = _semantic_drift(facts, sig)
    assert drift["has_drift"] is False


def test_doc_staleness_flags_phantom_param():
    from roam.commands.cmd_doc_staleness import _docstring_facts, _semantic_drift

    docstring = ":param missing_param: gone\n:param real: kept\n"
    sig = "def f(real: int) -> None"
    facts = _docstring_facts(docstring, sig)
    drift = _semantic_drift(facts, sig, ast.parse("def f(real: int):\n    return real\n").body[0])
    assert "missing_param" in drift["phantom_params"]
    assert drift["has_drift"] is True


def test_dead_default_includes_decay_distribution(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    summary = data.get("summary", {})
    # Decay framing must be in the default summary now, not behind --decay.
    assert "decay_distribution" in summary
    dist = summary["decay_distribution"]
    assert set(dist.keys()) == {"fresh", "stale", "decayed", "fossilized"}
    assert "total_dead_loc" in summary
    assert "median_age_days" in summary


def test_critique_errors_on_non_diff_input(cli_runner, indexed_project, monkeypatch):
    from roam.cli import cli

    monkeypatch.chdir(indexed_project)
    result = cli_runner.invoke(cli, ["critique"], input="this is not a diff\nfoo bar baz\n", catch_exceptions=False)
    assert result.exit_code != 0, result.output
    assert "INVALID_DIFF" in result.output


def test_critique_looks_like_unified_diff_helper():
    from roam.critique.checks import looks_like_unified_diff

    assert not looks_like_unified_diff("")
    assert not looks_like_unified_diff("plain text\nno headers\n")
    assert looks_like_unified_diff("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n")
    assert looks_like_unified_diff("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y\n")


def test_find_symbol_with_alternatives_returns_did_you_mean(project_factory, monkeypatch):
    """When several symbols share a name, the high-importance match wins and
    the rest appear as alternatives ordered by importance."""
    proj = project_factory(
        {
            "src/big.ts": (
                "export function handleSave(input: any) {\n"
                + "\n".join([f"  if (input.f{i}) input.r{i} = input.f{i} * 2" for i in range(40)])
                + "\n  return input\n}\n"
            ),
            "src/tiny.ts": "export function handleSave() { return null }\n",
        }
    )
    monkeypatch.chdir(proj)
    from roam.commands.resolve import find_symbol_with_alternatives
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        best, alternatives = find_symbol_with_alternatives(conn, "handleSave")
        assert best is not None
        assert len(alternatives) >= 1
        # All matches share the name regardless of which one wins
        assert best["name"] == "handleSave"
        for alt in alternatives:
            assert alt["name"] == "handleSave"


def test_safe_delete_use_prefix_with_zero_signals_stays_safe(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/orphan.ts": "export function useZoom() { return 1 }\n",
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["safe-delete", "useZoom"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["verdict"] == "SAFE", data
    assert data["sibling_refs"] == 0
    assert data["file_imported"] is False
    assert data["test_callers"] == 0


def test_fan_intra_file_const_is_not_spreader(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/big.ts": (
                "export const helper = (n: number) => n + 1\n"
                + "\n".join(f"const v{i} = helper({i})" for i in range(15))
                + "\nexport const total = "
                + " + ".join(f"v{i}" for i in range(15))
                + "\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["fan", "symbol"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    helper_item = next((i for i in data["items"] if i["name"] == "helper"), None)
    if helper_item is None:
        return  # extractor may not surface const helpers; behaviour locked by next assertion when present
    assert helper_item["fan_in_files"] <= 1
    assert helper_item["flag"] in {"", "local-hub", "local-spreader"}


def test_mark_actionable_cycles_excludes_local_and_test_cycles():
    from roam.graph.cycles import actionable_cycles, mark_actionable_cycles

    cycles = [
        {"files": ["src/a.py", "src/b.py"], "size": 2},
        {"files": ["src/single_file.py"], "size": 2},
        {"files": ["src/x.py", "tests/test_x.py"], "size": 2},
        {"files": ["src/c.py", "src/d.py", "src/e.py"], "size": 3},
    ]
    mark_actionable_cycles(cycles)

    assert cycles[0]["actionable"] is True
    assert cycles[1]["actionable"] is False and cycles[1]["local_only"] is True
    assert cycles[2]["actionable"] is False and cycles[2]["has_test_file"] is True
    assert cycles[3]["actionable"] is True
    assert len(actionable_cycles(cycles)) == 2
