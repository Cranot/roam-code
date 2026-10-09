from __future__ import annotations

import json

from tests.conftest import invoke_cli


def test_python_table(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "classify.py": """from enum import Enum
class Mode(Enum):
    FAST = "fast"
    SAFE = "safe"
def classify(mode: Mode, enabled: bool, failed: bool):
    if failed:
        raise ValueError("failed")
    if mode == Mode.FAST and enabled:
        return "fast"
    return "safe"
def helper(value):
    return value
def caller():
    helper(0)
    return helper(1)
class Service:
    def call(self):
        return helper(2)
""",
            "test_classify.py": """from classify import classify, Mode, helper
def test_fast():
    assert classify(Mode.FAST, True, False) == "fast"
def test_helper():
    assert helper(3) == 3
""",
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert [d["name"] for d in data["dimensions"]] == ["mode", "enabled", "failed"]
    assert data["coverage"]["total_rows"] == 8
    assert len(data["rows"]) == 8
    assert sum(r["coverage"] == "test_call_observed" for r in data["rows"]) == 1
    assert sum(r["coverage"] == "no_test_observed" for r in data["rows"]) == 7
    covered = next(r for r in data["rows"] if r["coverage"] == "test_call_observed")
    assert covered["outcome"]["value"] == "fast"
    assert any(r["outcome"]["kind"] == "raise" for r in data["rows"])
    helper = json.loads(invoke_cli(cli_runner, ["case-table", "helper", "--call-sites"], json_mode=True).stdout)
    assert helper["coverage"]["total_rows"] == 4
    assert {r["caller_kind"] for r in helper["rows"]} == {"function", "method", "test"}
    assert sum(r["coverage"] == "test_call_observed" for r in helper["rows"]) == 1


def test_javascript_table(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "classify.ts": """export enum Mode { FAST, SAFE }
export function classify(mode: Mode, enabled: boolean, code: number | null) {
  if (code === null) { throw new Error("missing"); }
  if (mode === Mode.FAST && enabled) { return "fast"; }
  if (code === 0) { return "ok"; }
  return "safe";
}
""",
            "test_classify.ts": """import { classify, Mode } from './classify';
function test_fast() { return classify(Mode.FAST, true, 0); }
""",
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert [d["name"] for d in data["dimensions"]] == ["mode", "enabled", "code"]
    assert data["coverage"]["total_rows"] == 12
    assert any(r["outcome"]["kind"] == "throw" for r in data["rows"])
    assert sum(r["coverage"] == "test_call_observed" for r in data["rows"]) == 1


def test_cap_pairwise_and_missing(project_factory, cli_runner, monkeypatch):
    project = project_factory({"flags.py": "def flags(a: bool, b: bool, c: bool):\n    return a\n"})
    monkeypatch.chdir(project)
    capped = json.loads(invoke_cli(cli_runner, ["case-table", "flags", "--max-rows", "2"], json_mode=True).stdout)
    assert len(capped["rows"]) == 2
    assert capped["coverage"]["truncated"]
    pairwise = json.loads(invoke_cli(cli_runner, ["case-table", "flags", "--pairwise"], json_mode=True).stdout)
    assert len(pairwise["rows"]) < 8
    assert pairwise["coverage"]["pairwise_complete"]
    for left, right in [(0, 1), (0, 2), (1, 2)]:
        names = ["a", "b", "c"]
        assert {(r["inputs"][names[left]], r["inputs"][names[right]]) for r in pairwise["rows"]} == {
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        }
    missing = json.loads(invoke_cli(cli_runner, ["case-table", "absent"], json_mode=True).stdout)
    assert missing["summary"]["status"] == "symbol_not_found"


def test_unknown_and_unsupported(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "sample.py": "def sample(value):\n    if external(value):\n        return 1\n    return 2\n",
            "sample.go": "package sample\nfunc Other(x int) int { return x }\n",
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "sample"], json_mode=True).stdout)
    assert data["rows"][0]["outcome"]["kind"] == "unknown"
    unsupported = json.loads(invoke_cli(cli_runner, ["case-table", "Other"], json_mode=True).stdout)
    assert unsupported["summary"]["status"] == "unsupported_language"


def test_mcp_dispatch(monkeypatch):
    from roam import mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "_run_roam", lambda args, root: calls.append((args, root)) or {"ok": True})
    tool = mcp_server.case_table_tool
    while hasattr(tool, "__wrapped__"):
        tool = tool.__wrapped__
    tool(symbol="classify", max_rows=12, pairwise=True, call_sites=True)
    assert calls == [(["case-table", "classify", "--max-rows", "12", "--pairwise", "--call-sites"], ".")]


def test_switch_and_undefined(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "decide.js": """export function decide(code, missing) {
  if (missing === undefined) { return "missing"; }
  switch (code) {
    case 0: return "ok";
    case 1: throw new Error("error");
    default: return "other";
  }
}
"""
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "decide"], json_mode=True).stdout)
    assert data["dimensions"][0]["values"][:2] == [0, 1]
    assert {"kind": "undefined"} in data["dimensions"][1]["values"]
    assert any(r["outcome"].get("value") == "missing" for r in data["rows"])
    # Symbolic remainder stays unknown.
    assert any(r["outcome"]["kind"] == "unknown" for r in data["rows"])


def test_nested_flags_and_nonexecution(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "flags.py": """def flags(a, b):
    if a and not b:
        return dangerous()
    return 0
"""
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "flags"], json_mode=True).stdout)
    assert data["coverage"]["total_rows"] == 4
    selected = next(r for r in data["rows"] if r["inputs"] == {"a": True, "b": False})
    assert selected["outcome"]["kind"] == "return"
    assert selected["outcome"]["value"] == {"kind": "unknown"}


def test_return_boolean_dimensions_and_match(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "decision.py": """def gate(enabled: bool, findings, incomplete: bool):
    if not enabled:
        return False
    return bool(findings) or bool(incomplete)
def classify(code):
    match code:
        case 0:
            return "ok"
        case None:
            raise ValueError("missing")
        case _:
            return "other"
"""
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "gate"], json_mode=True).stdout)
    assert data["coverage"]["total_rows"] == 8
    for row in data["rows"]:
        v = row["inputs"]
        assert row["outcome"]["value"] == (v["enabled"] and (v["findings"] or v["incomplete"]))
    assert data["agent_contract"]["next_commands"] == ["roam test-map gate", "roam context gate"]
    matched = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert matched["dimensions"][0]["values"] == [0, None, {"kind": "other"}]
    assert matched["rows"][0]["outcome"]["value"] == "ok"
    assert matched["rows"][1]["outcome"]["kind"] == "raise"


def test_text_and_degraded_source(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {"classify.py": "def classify(flag: bool):\n    if flag:\n        return 1\n    return 0\n"}
    )
    monkeypatch.chdir(project)
    result = invoke_cli(cli_runner, ["case-table", "classify"])
    assert result.exit_code == 0
    assert result.stdout.startswith("VERDICT: classify: 2 proposed cases;")
    assert "[NO TEST OBSERVED]" in result.stdout
    assert "Next: Run roam test-map classify" in result.stdout
    fuzzy = json.loads(invoke_cli(cli_runner, ["case-table", "classif"], json_mode=True).stdout)
    assert fuzzy["summary"]["partial_success"]
    assert fuzzy["summary"]["resolution"] == "fuzzy"
    (project / "classify.py").unlink()
    missing = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert missing["summary"]["status"] == "source_unavailable"
    assert missing["summary"]["partial_success"]


def test_pairwise_cap(project_factory, cli_runner, monkeypatch):
    project = project_factory({"flags.py": "def flags(a: bool, b: bool, c: bool):\n    return a\n"})
    monkeypatch.chdir(project)
    data = json.loads(
        invoke_cli(cli_runner, ["case-table", "flags", "--pairwise", "--max-rows", "1"], json_mode=True).stdout
    )
    assert data["coverage"]["pairwise_complete"] is False
    assert data["coverage"]["truncated"]
    assert len(data["rows"]) == 1


def test_mcp_real_command(project_factory, monkeypatch):
    from roam import mcp_server

    project = project_factory({"flags.py": "def flags(a: bool):\n    return a\n"})
    monkeypatch.chdir(project)
    tool = mcp_server.case_table_tool
    while hasattr(tool, "__wrapped__"):
        tool = tool.__wrapped__
    data = tool(symbol="flags", root=str(project))
    assert data["command"] == "case-table"
    assert data["coverage"]["total_rows"] == 2


def test_decorated_classifier(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "classify.py": """def identity(fn):
    return fn
@identity
def classify(flag: bool):
    if flag:
        return 1
    return 0
""",
            "test_classify.py": """from classify import classify
def test_true():
    assert classify(True) == 1
""",
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert data["coverage"]["total_rows"] == 2
    assert data["coverage"]["test_call_observed"] == 1
    assert data["summary"]["partial_success"] is False


def test_arrow_classifier(project_factory, cli_runner, monkeypatch):
    project = project_factory({"classify.ts": "export const classify = (enabled: boolean) => !enabled;\n"})
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert data["coverage"]["total_rows"] == 2
    assert [r["outcome"]["value"] for r in data["rows"]] == [True, False]


def test_single_dimension_pairwise_cap(project_factory, cli_runner, monkeypatch):
    project = project_factory({"flags.py": "def flags(a: bool):\n    return a\n"})
    monkeypatch.chdir(project)
    data = json.loads(
        invoke_cli(cli_runner, ["case-table", "flags", "--pairwise", "--max-rows", "1"], json_mode=True).stdout
    )
    assert data["coverage"]["truncated"]
    assert data["coverage"]["total_rows"] == 2
    assert len(data["rows"]) == 1


def test_non_json_literals_stay_unknown(project_factory, cli_runner, monkeypatch):
    project = project_factory({"sample.py": "def sample(value=b'x'):\n    return value\n"})
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "sample"], json_mode=True).stdout)
    assert data["dimensions"][0]["values"] == [{"kind": "unknown"}]
    assert invoke_cli(cli_runner, ["case-table", "sample"]).exit_code == 0


def test_negative_exit_code_dimensions(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "code.py": "def classify_py(code):\n    if code == -1:\n        return 'failed'\n    return 'ok'\n",
            "code.js": "export function classify_js(code) { if (code === -1) { return 'failed'; } return 'ok'; }\n"
            "export function classify_numeric(code = 0.0) { if (code === 0) { return 'ok'; } return 'other'; }\n",
        }
    )
    monkeypatch.chdir(project)
    for symbol in ("classify_py", "classify_js"):
        data = json.loads(invoke_cli(cli_runner, ["case-table", symbol], json_mode=True).stdout)
        assert -1 in data["dimensions"][0]["values"]
        row = next(r for r in data["rows"] if r["inputs"]["code"] == -1)
        assert row["outcome"]["value"] == "failed"


def test_javascript_numeric_equality(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {"code.js": "export function classify(code = 0.0) { if (code === 0) { return 'ok'; } return 'other'; }\n"}
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    rows = [r for r in data["rows"] if r["inputs"]["code"] == 0]
    assert rows
    assert all(r["outcome"]["value"] == "ok" for r in rows)


def test_coverage_preserves_receiver_identity(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "classify.py": """def classify(flag: bool):
    return flag
class Other:
    def classify(self, flag):
        return 42
""",
            "test_classify.py": """from classify import classify, Other
def test_true():
    assert classify(True)
    other = Other()
    assert other.classify(False) == 42
""",
        }
    )
    monkeypatch.chdir(project)
    data = json.loads(invoke_cli(cli_runner, ["case-table", "classify"], json_mode=True).stdout)
    assert data["coverage"]["test_call_observed"] == 1
    uncovered = next(r for r in data["rows"] if r["inputs"]["flag"] is False)
    assert uncovered["coverage"] == "no_test_observed"
