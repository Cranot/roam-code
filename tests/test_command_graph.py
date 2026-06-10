"""Tests for the G2 command graph (`roam.command_graph` + `roam commands`).

The command graph is the engine behind verification contracts + the Agent Change
Proof Bundle, so it must be deterministic, evidence-backed, and consistent with
the LAW-4 / CONSTRAINT-12 envelope discipline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from test_law4_lint import _is_concrete_anchored  # noqa: E402

from roam.cli import cli  # noqa: E402
from roam.command_graph import COSTS, KINDS, SCOPES, build_command_graph  # noqa: E402


def _parse_env(output: str) -> dict:
    return json.loads(output[output.index("{") :])


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class TestEngine:
    def test_package_json_pnpm_classification_and_prefix(self, tmp_path):
        _write(
            tmp_path,
            {
                "package.json": json.dumps(
                    {
                        "scripts": {
                            "test": "vitest run",
                            "typecheck": "tsc -b",
                            "lint": "eslint .",
                            "build": "vite build",
                            "dev": "vite",
                            "deploy": "gh-pages -d dist",
                        }
                    }
                ),
                "pnpm-lock.yaml": "",
                "vitest.config.ts": "export default {}",
            },
        )
        g = build_command_graph(tmp_path)
        by_id = {c["id"]: c for c in g["commands"]}
        assert g["package_manager"] == "pnpm"
        assert by_id["test.test"]["command"] == "pnpm test"
        assert by_id["test.test"]["kind"] == "test"
        assert by_id["test.test"]["cost"] == "high"
        assert by_id["test.test"]["targetable"] is True
        # corroborating config file becomes evidence + lifts confidence
        assert "vitest.config.ts" in by_id["test.test"]["evidence"]
        assert by_id["test.test"]["confidence"] >= 0.9
        assert by_id["typecheck.typecheck"]["kind"] == "typecheck"
        assert by_id["lint.lint"]["kind"] == "lint"
        assert by_id["build.build"]["kind"] == "build"
        assert by_id["run.dev"]["kind"] == "run"
        assert by_id["run.dev"]["safe_to_auto_run"] is False  # long-running
        assert by_id["other.deploy"]["mutates_state"] is True
        assert by_id["other.deploy"]["safe_to_auto_run"] is False

    def test_npm_prefix(self, tmp_path):
        _write(tmp_path, {"package.json": json.dumps({"scripts": {"test": "jest"}}), "package-lock.json": "{}"})
        g = build_command_graph(tmp_path)
        assert g["package_manager"] == "npm"
        assert g["commands"][0]["command"] == "npm run test"

    def test_makefile(self, tmp_path):
        _write(tmp_path, {"Makefile": "test:\n\tpytest\n\nbuild:\n\tgo build\n\n.PHONY: test build\n"})
        g = build_command_graph(tmp_path)
        ids = {c["id"] for c in g["commands"]}
        assert "test.make.test" in ids and "build.make.build" in ids
        assert "Makefile" in g["sources_scanned"]

    def test_python_fallback_only_without_explicit_scripts(self, tmp_path):
        _write(tmp_path, {"pyproject.toml": "[tool.pytest.ini_options]\n"})
        g = build_command_graph(tmp_path)
        assert any(c["command"] == "pytest" and c["kind"] == "test" for c in g["commands"])
        # fallback confidence is lower than an explicit manifest script
        assert g["commands"][0]["confidence"] <= 0.6

    def test_empty_repo_is_clean_envelope(self, tmp_path):
        g = build_command_graph(tmp_path)
        assert g["commands"] == []
        assert g["sources_scanned"] == []

    def test_all_enums_valid(self, tmp_path):
        _write(tmp_path, {"package.json": json.dumps({"scripts": {"test": "vitest", "x": "echo hi"}})})
        for c in build_command_graph(tmp_path)["commands"]:
            assert c["kind"] in KINDS and c["scope"] in SCOPES and c["cost"] in COSTS
            assert isinstance(c["confidence"], float) and 0.0 <= c["confidence"] <= 1.0
            assert c["evidence"], "every command must carry evidence (the moat)"

    def test_deterministic(self, tmp_path):
        _write(tmp_path, {"package.json": json.dumps({"scripts": {"b": "vite build", "a": "vitest", "c": "eslint ."}})})
        assert build_command_graph(tmp_path) == build_command_graph(tmp_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCli:
    def test_json_envelope_shape_and_discipline(self):
        r = CliRunner().invoke(cli, ["--json", "commands"])
        assert r.exit_code == 0, r.output
        env = _parse_env(r.output)
        assert env["command"] == "commands"
        s = env["summary"]
        assert s["verdict"] and isinstance(s["command_count"], int)
        # LAW 4: facts concrete-noun-anchored
        facts = env["agent_contract"]["facts"]
        weak = [f for f in facts if not _is_concrete_anchored(f)]
        assert not weak, f"weak facts (LAW 4): {weak}"
        # CONSTRAINT 12: next_commands are literal `roam <cmd>`
        from roam.cli import _COMMANDS

        for nc in env["agent_contract"]["next_commands"]:
            assert nc.startswith("roam ")
            sub = [t for t in nc.split()[1:] if not t.startswith("-")]
            assert sub and sub[0] in _COMMANDS, f"unresolved next_command: {nc}"

    def test_kind_filter(self):
        r = CliRunner().invoke(cli, ["--json", "commands", "--kind", "test"])
        assert r.exit_code == 0, r.output
        env = _parse_env(r.output)
        assert all(c["kind"] == "test" for c in env["commands"])


# ---------------------------------------------------------------------------
# MCP wrapper (module-cache safe: _TOOL_METADATA read, never popped)
# ---------------------------------------------------------------------------
class TestMcp:
    def test_wrapper_registered_read_only_and_imperative(self):
        from roam.mcp_server import _TOOL_METADATA

        assert "roam_commands" in _TOOL_METADATA
        meta = _TOOL_METADATA["roam_commands"]
        assert meta["read_only"] is True and meta["destructive"] is False
        # dogfood: the wrapper's own description is not declarative (LAW 2)
        from roam.agent_opt import detect_declarative_tool_description

        assert detect_declarative_tool_description({"roam_commands": meta["description"]}) == []
