"""Tests for ``roam mcp-setup --write``.

The ``--write`` flag actually writes the JSON config block to the
platform's expected on-disk location:

* project-scoped paths (``./.mcp.json``, ``./.cursor/mcp.json``,
  ``./.vscode/mcp.json``) are written under the current working dir.
* user-scoped paths (``~/.codeium/...``, ``~/.gemini/...``,
  ``~/.codex/...``) are written under ``Path.home()``.

These tests cover the three behaviours that matter:
1. Creating a fresh config file (file did not exist).
2. Merging into an existing config file without clobbering other
   ``mcpServers`` entries.
3. Refusing to overwrite a corrupt JSON file (and not destroying it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_mcp_setup import (
    _merge_config,
    _resolve_config_path,
    _write_config,
    mcp_setup,
)

# ---------------------------------------------------------------------------
# Unit tests for the small helpers
# ---------------------------------------------------------------------------


def test_resolve_config_path_handles_tilde(tmp_path, monkeypatch):
    # Path.expanduser consults HOME (POSIX) and USERPROFILE/HOMEDRIVE+
    # HOMEPATH (Windows). Set the major-platform env vars so the
    # substitution lands inside ``tmp_path`` regardless of OS.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = _resolve_config_path("~/.codex/config.json")
    assert p == tmp_path / ".codex" / "config.json"


def test_resolve_config_path_strips_dot_slash(tmp_path):
    p = _resolve_config_path("./.mcp.json", project_root=tmp_path)
    assert p == (tmp_path / ".mcp.json").resolve()


def test_merge_preserves_other_mcp_servers():
    existing = {
        "mcpServers": {
            "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]},
        }
    }
    incoming = {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}
    merged = _merge_config(existing, incoming)
    assert "filesystem" in merged["mcpServers"], "merge clobbered an unrelated server"
    assert merged["mcpServers"]["roam-code"]["command"] == "roam"


def test_merge_overwrites_existing_roam_entry():
    """If an old roam-code entry exists, the new one wins."""
    existing = {"mcpServers": {"roam-code": {"command": "old-roam", "args": []}}}
    incoming = {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}
    merged = _merge_config(existing, incoming)
    assert merged["mcpServers"]["roam-code"]["command"] == "roam"
    assert merged["mcpServers"]["roam-code"]["args"] == ["mcp"]


# ---------------------------------------------------------------------------
# _write_config end-to-end
# ---------------------------------------------------------------------------


def test_write_creates_fresh_file(tmp_path):
    target = tmp_path / "subdir" / "mcp.json"
    cfg = {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}
    result = _write_config(target, cfg)

    assert result["ok"] is True
    assert result["created"] is True
    assert result["backup"] is None
    assert target.is_file()
    assert json.loads(target.read_text()) == cfg


def test_write_merges_with_existing(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"filesystem": {"command": "npx", "args": []}}}),
        encoding="utf-8",
    )
    cfg = {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}
    result = _write_config(target, cfg)

    assert result["ok"] is True
    assert result["created"] is False
    assert result["merged"] is True
    assert result["backup"] is not None
    assert Path(result["backup"]).is_file(), "expected sibling .bak file"

    final = json.loads(target.read_text())
    assert "filesystem" in final["mcpServers"]
    assert "roam-code" in final["mcpServers"]


def test_write_refuses_corrupt_existing(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text("not valid json {{", encoding="utf-8")
    cfg = {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}
    result = _write_config(target, cfg)

    assert result["ok"] is False
    assert "not valid JSON" in result["error"]
    # File on disk must be untouched (not destroyed by the failed write).
    assert target.read_text() == "not valid json {{"


def test_write_refuses_top_level_array(tmp_path):
    """Some users may have a JSON file that's a list, not an object —
    refuse rather than guess where to merge."""
    target = tmp_path / "mcp.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    result = _write_config(target, {"mcpServers": {"roam-code": {}}})
    assert result["ok"] is False
    assert "object at the top level" in result["error"]


# ---------------------------------------------------------------------------
# CLI invocation via Click runner
# ---------------------------------------------------------------------------


def test_cli_write_creates_project_local_file(tmp_path):
    runner = CliRunner()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(mcp_setup, ["vscode", "--write"], obj={})
        assert result.exit_code == 0, result.output
        target = tmp_path / ".vscode" / "mcp.json"
        assert target.is_file()
        data = json.loads(target.read_text())
        assert data["servers"]["roam-code"]["command"] == "roam"
        # text output should mention the path
        assert "Created" in result.output or "Updated" in result.output
        assert ".vscode" in result.output
    finally:
        os.chdir(cwd)


def test_cli_write_with_preset_injects_env(tmp_path):
    runner = CliRunner()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(mcp_setup, ["cursor", "--preset", "compliance", "--write"], obj={})
        assert result.exit_code == 0, result.output
        target = tmp_path / ".cursor" / "mcp.json"
        data = json.loads(target.read_text())
        env = data["mcpServers"]["roam-code"].get("env", {})
        assert env.get("ROAM_MCP_PRESET") == "compliance"
    finally:
        os.chdir(cwd)


def test_cli_project_root_lookup_allows_filesystem_failure(tmp_path, monkeypatch):
    """Filesystem lookup failures fall back to the current directory."""

    from roam.db import connection

    def fail_lookup():
        raise PermissionError("cannot inspect parent")

    monkeypatch.setattr(connection, "find_project_root", fail_lookup)
    runner = CliRunner()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(mcp_setup, ["vscode", "--write"], obj={})
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".vscode" / "mcp.json").is_file()
    finally:
        os.chdir(cwd)


def test_cli_project_root_lookup_propagates_programmer_errors(tmp_path, monkeypatch):
    """Bug-class exceptions from project-root lookup stay visible."""

    from roam.db import connection

    def fail_lookup():
        raise TypeError("bad refactor")

    monkeypatch.setattr(connection, "find_project_root", fail_lookup)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(mcp_setup, ["vscode", "--write"], obj={})

    assert result.exit_code == 1
    assert isinstance(result.exception, TypeError)
    assert not (tmp_path / ".vscode" / "mcp.json").exists()


def test_cli_write_emits_json_envelope(tmp_path):
    """In ``--json --write`` mode, the envelope must carry ``write_result``."""
    runner = CliRunner()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(mcp_setup, ["claude-code", "--write"], obj={"json": True})
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope.get("write_result", {}).get("ok") is True
        assert ".mcp.json" in envelope["write_result"]["path"]
    finally:
        os.chdir(cwd)


def test_cli_write_failure_exits_nonzero(tmp_path):
    """If the existing file is corrupt, --write must fail loud (exit 1)."""
    runner = CliRunner()
    target_dir = tmp_path / ".vscode"
    target_dir.mkdir()
    (target_dir / "mcp.json").write_text("garbage", encoding="utf-8")

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(mcp_setup, ["vscode", "--write"], obj={})
        assert result.exit_code == 1, result.output
        # Original should still be there (not destroyed).
        assert (target_dir / "mcp.json").read_text() == "garbage"
    finally:
        os.chdir(cwd)
