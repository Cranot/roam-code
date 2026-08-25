"""Exact-output controls for the production clone families consolidated in this lane."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._helpers.repo_root import repo_root


def _stable_envelope(command: str, *, summary: dict, **payload) -> dict:
    """Keep command-owned JSON byte snapshots free of generic envelope metadata."""
    return {"command": command, "summary": summary, **payload}


_WS_CASES = {
    "understand": {
        "args": [],
        "command_name": "ws-understand",
        "aggregator": "aggregate_understand",
        "aggregate_args": (),
        "config": {"workspace": "platform"},
        "data": {
            "repos": [
                {
                    "name": "frontend",
                    "languages": [{"language": "javascript"}, {"language": "css"}],
                    "files": 3,
                    "symbols": 7,
                    "edges": 9,
                    "key_symbols": [{"name": "App"}, {"name": "loadData"}],
                },
                {
                    "name": "backend",
                    "languages": [{"language": "java"}],
                    "files": 5,
                    "symbols": 11,
                    "edges": 13,
                    "key_symbols": [],
                },
            ],
            "total_files": 8,
            "total_symbols": 18,
            "total_edges": 22,
            "cross_repo_edges": 2,
            "cross_repo_connections": [
                {
                    "source_repo": "frontend",
                    "target_repo": "backend",
                    "edge_count": 2,
                    "samples": [
                        {"http_method": "GET", "url_pattern": "/api/items"},
                        {"http_method": "POST", "url_pattern": "/api/items"},
                    ],
                }
            ],
        },
        "summary": {
            "workspace": "platform",
            "repos": 2,
            "total_files": 8,
            "total_symbols": 18,
            "cross_repo_edges": 2,
            "verdict": "2 repos, 8 files, 18 symbols, 2 cross-repo edges",
        },
        "text": (
            "WORKSPACE: platform (2 repos, 8 files, 18 symbols)\n"
            "\n"
            "=== frontend (javascript, css) ===\n"
            "  3 files, 7 symbols, 9 edges\n"
            "  Key: App, loadData\n"
            "\n"
            "=== backend (java) ===\n"
            "  5 files, 11 symbols, 13 edges\n"
            "\n"
            "=== Cross-Repo Connections (2 edges) ===\n"
            "  frontend -> backend (2 edges)\n"
            "    GET    /api/items\n"
            "    POST   /api/items\n"
        ),
    },
    "context": {
        "args": ["loadData"],
        "command_name": "ws-context",
        "aggregator": "cross_repo_context",
        "aggregate_args": ("loadData",),
        "config": {"workspace": "platform"},
        "data": {
            "symbol": "loadData",
            "found_in": [
                {
                    "repo": "frontend",
                    "kind": "function",
                    "name": "loadData",
                    "file_path": "src/api.js",
                    "line_start": 4,
                    "signature": "function loadData()",
                    "callers": [{"name": "App.start", "file": "src/app.js", "line": 8}],
                    "callees": [{"name": "fetch", "file": "src/api.js", "line": 5}],
                }
            ],
            "cross_repo_edges": [
                {
                    "source_repo": "frontend",
                    "target_repo": "backend",
                    "http_method": "GET",
                    "url_pattern": "/api/items",
                    "kind": "rest-api",
                }
            ],
        },
        "summary": {
            "symbol": "loadData",
            "found_in_repos": ["frontend"],
            "cross_repo_edges": 1,
            "verdict": "Found in 1 repo(s), 1 cross-repo edges",
        },
        "text": (
            "[frontend] function loadData  src/api.js:4\n"
            "  function loadData()\n"
            "  Callers:\n"
            "    App.start  src/app.js:8\n"
            "  Callees:\n"
            "    fetch  src/api.js:5\n"
            "\n"
            "Cross-repo connections:\n"
            "  frontend -> backend  GET /api/items  (rest-api)\n"
        ),
    },
    "trace": {
        "args": ["loadData", "listItems"],
        "command_name": "ws-trace",
        "aggregator": "cross_repo_trace",
        "aggregate_args": ("loadData", "listItems"),
        "config": {"workspace": "platform"},
        "data": {
            "source": {
                "name": "loadData",
                "locations": [
                    {
                        "repo": "frontend",
                        "kind": "function",
                        "name": "loadData",
                        "file": "src/api.js",
                    }
                ],
            },
            "target": {
                "name": "listItems",
                "locations": [
                    {
                        "repo": "backend",
                        "kind": "method",
                        "name": "listItems",
                        "file": "src/Items.java",
                    }
                ],
            },
            "bridge_edges": [
                {
                    "source_repo": "frontend",
                    "target_repo": "backend",
                    "http_method": "GET",
                    "url_pattern": "/api/items",
                    "kind": "rest-api",
                }
            ],
            "same_repo": False,
            "verdict": "Cross-repo path found via 1 bridge edge",
        },
        "summary": {
            "source": "loadData",
            "target": "listItems",
            "bridge_edges": 1,
            "same_repo": False,
            "verdict": "Cross-repo path found via 1 bridge edge",
        },
        "text": (
            "VERDICT: Cross-repo path found via 1 bridge edge\n"
            "\n"
            "Source: loadData\n"
            "  [frontend] function loadData  src/api.js\n"
            "Target: listItems\n"
            "  [backend] method listItems  src/Items.java\n"
            "\n"
            "Cross-repo bridges:\n"
            "  frontend -> backend  GET /api/items  (rest-api)\n"
        ),
    },
}


@pytest.mark.parametrize("command", ["understand", "context", "trace"])
@pytest.mark.parametrize("json_mode", [False, True], ids=["text", "json"])
def test_workspace_view_command_exact_output_snapshot(monkeypatch, command, json_mode):
    """Pin dispatch parameters and every command-owned output byte."""
    from roam.commands import cmd_ws

    case = _WS_CASES[command]

    def read_view(ctx, command_name, aggregate_fn, *aggregate_args):
        assert command_name == case["command_name"]
        assert aggregate_fn.__name__ == case["aggregator"]
        assert aggregate_args == case["aggregate_args"]
        return case["config"], case["data"]

    monkeypatch.setattr(cmd_ws, "_read_consistent_workspace_view", read_view)
    monkeypatch.setattr(cmd_ws, "json_envelope", _stable_envelope)

    result = CliRunner().invoke(
        cmd_ws.ws,
        [command, *case["args"]],
        obj={"json": json_mode},
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    if json_mode:
        expected = _stable_envelope(
            case["command_name"],
            summary=case["summary"],
            **case["data"],
        )
        assert result.output == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    else:
        assert result.output == case["text"]


_SYMBOL_DEFAULTS = {
    "qualified_name": None,
    "kind": "class",
    "signature": None,
    "line_start": 1,
    "line_end": 1,
    "docstring": None,
    "visibility": "public",
    "is_exported": False,
    "parent_name": None,
    "default_value": None,
    "is_async": False,
    "decorators": "",
}


def _symbol(name: str, **overrides) -> dict:
    result = {"name": name, **_SYMBOL_DEFAULTS}
    result.update(overrides)
    if result["qualified_name"] is None:
        result["qualified_name"] = name
    return result


def _reference(target_name: str, kind: str, line: int, source_name: str) -> dict:
    return {
        "source_name": source_name,
        "target_name": target_name,
        "kind": kind,
        "line": line,
        "import_path": None,
    }


_CLASS_CASES = {
    "c_sharp": {
        "path": "Widget.cs",
        "source": (
            "namespace Demo {\n"
            "public sealed class Widget<T> : BaseWidget, IWorker where T : class {\n"
            "    public void Run() { Helper.Call(); }\n"
            "}\n"
            "}\n"
        ),
        "symbols": [
            _symbol(
                "Demo",
                qualified_name="Demo",
                kind="module",
                signature="namespace Demo",
                line_start=1,
                line_end=5,
                is_exported=True,
            ),
            _symbol(
                "Widget",
                qualified_name="Demo.Widget",
                signature="sealed class Widget<T> : BaseWidget, IWorker where T : class",
                line_start=2,
                line_end=4,
                is_exported=True,
                parent_name="Demo",
            ),
            _symbol(
                "Run",
                qualified_name="Demo.Widget.Run",
                kind="method",
                signature="void Run()",
                line_start=3,
                line_end=3,
                is_exported=True,
                parent_name="Demo.Widget",
            ),
        ],
        "references": [
            _reference("Call", "call", 3, "Demo.Widget.Run"),
            _reference("BaseWidget", "inherits", 2, "Demo.Widget"),
            _reference("IWorker", "implements", 2, "Demo.Widget"),
        ],
    },
    "java": {
        "path": "Widget.java",
        "source": (
            "package demo;\n"
            "@Deprecated\n"
            "public class Widget<T> extends BaseWidget implements Worker {\n"
            "    public void run() { Helper.call(); }\n"
            "}\n"
        ),
        "symbols": [
            _symbol(
                "demo",
                qualified_name="demo",
                kind="module",
                signature="package demo",
                line_start=1,
                line_end=1,
                is_exported=True,
            ),
            _symbol(
                "Widget",
                signature="@Deprecated\nclass Widget<T> extends BaseWidget implements Worker",
                line_start=2,
                line_end=5,
                is_exported=True,
            ),
            _symbol(
                "run",
                qualified_name="Widget.run",
                kind="method",
                signature="void run()",
                line_start=4,
                line_end=4,
                is_exported=True,
                parent_name="Widget",
            ),
        ],
        "references": [
            _reference("Helper.call", "call", 4, "Widget.run"),
            _reference("BaseWidget", "inherits", 2, "Widget"),
            _reference("Worker", "implements", 2, "Widget"),
        ],
    },
    "javascript": {
        "path": "widget.js",
        "source": "export class Widget extends BaseWidget {\n  run() { helper(); }\n}\n",
        "symbols": [
            _symbol(
                "Widget",
                signature="class Widget extends BaseWidget",
                line_start=1,
                line_end=3,
                is_exported=True,
            ),
            _symbol(
                "run",
                qualified_name="Widget.run",
                kind="method",
                signature="run()",
                line_start=2,
                line_end=2,
                parent_name="Widget",
            ),
        ],
        "references": [
            _reference("helper", "call", 2, "Widget"),
            _reference("BaseWidget", "inherits", 1, "Widget"),
        ],
    },
}


@pytest.mark.parametrize("language", ["c_sharp", "java", "javascript"])
def test_class_extractor_exact_output_snapshot(language):
    """Pin full symbol/reference outputs around each language's class syntax."""
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor

    case = _CLASS_CASES[language]
    source = case["source"].encode()
    tree = get_parser(GRAMMAR_ALIASES.get(language, language)).parse(source)
    extractor = get_extractor(language)

    symbols = extractor.extract_symbols(tree, source, case["path"])
    references = extractor.extract_references(tree, source, case["path"])

    assert symbols == case["symbols"]
    assert references == case["references"]


def _function_call_names(path: Path, function_names: set[str]) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text())
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names:
            body_nodes = [child for statement in node.body for child in ast.walk(statement)]
            calls[node.name] = {
                child.func.attr
                for child in body_nodes
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
            } | {
                child.func.id
                for child in body_nodes
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
    return calls


def test_workspace_family_uses_one_parameterized_implementation():
    """Refactor gate: the three Click members remain thin declarations."""
    calls = _function_call_names(
        repo_root() / "src/roam/commands/cmd_ws.py",
        {"ws_understand_command", "ws_context_cmd", "ws_trace"},
    )
    assert calls == {
        "ws_understand_command": {"_run_workspace_view_command"},
        "ws_context_cmd": {"_run_workspace_view_command"},
        "ws_trace": {"_run_workspace_view_command"},
    }


def test_language_class_family_uses_shared_skeleton():
    """Refactor gate: syntax-specific methods all delegate result shaping."""
    for filename in ("csharp_lang.py", "java_lang.py", "javascript_lang.py"):
        calls = _function_call_names(
            repo_root() / "src/roam/languages" / filename,
            {"_extract_class"},
        )
        assert "_extract_class_skeleton" in calls["_extract_class"]
