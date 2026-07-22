"""Tests for ``roam mcp-setup --write``.

The ``--write`` flag actually writes the JSON or TOML config block to the
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
4. Surgically merging Codex TOML while preserving unrelated settings.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

import roam.commands.cmd_mcp_setup as mcp_setup_module
from roam.commands.cmd_mcp_setup import (
    _merge_config,
    _resolve_config_path,
    _write_codex_toml,
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
    p = _resolve_config_path("~/.codex/config.toml")
    assert p == tmp_path / ".codex" / "config.toml"


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


@pytest.mark.parametrize("config_format", ["json", "toml"])
def test_config_writers_preserve_concurrent_updates(tmp_path, monkeypatch, config_format):
    """A config changed after parsing is never overwritten by the merge."""
    target = tmp_path / ("mcp.json" if config_format == "json" else "config.toml")
    original = b'{"mcpServers": {"peer": {"command": "old"}}}\n' if config_format == "json" else b'model = "old"\n'
    concurrent = b'{"mcpServers": {"peer": {"command": "new"}}}\n' if config_format == "json" else b'model = "new"\n'
    target.write_bytes(original)
    real_atomic_write = mcp_setup_module.atomic_write_bytes

    def race_target(path, content, **kwargs):
        if Path(path) == target:
            target.write_bytes(concurrent)
        return real_atomic_write(path, content, **kwargs)

    monkeypatch.setattr(mcp_setup_module, "atomic_write_bytes", race_target)
    if config_format == "json":
        result = _write_config(target, {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}})
    else:
        result = _write_codex_toml(
            target,
            {"mcp_servers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
        )

    assert result["ok"] is False
    assert "changed after it was parsed" in result["error"]
    assert target.read_bytes() == concurrent
    assert target.with_suffix(target.suffix + ".bak").read_bytes() == original


def test_json_and_toml_writers_refuse_symlink_targets(tmp_path):
    external = tmp_path / "external"
    external.write_text("{}", encoding="utf-8")
    json_target = tmp_path / "mcp.json"
    toml_target = tmp_path / "config.toml"
    try:
        json_target.symlink_to(external)
        toml_target.symlink_to(external)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    json_result = _write_config(json_target, {"mcpServers": {"roam-code": {}}})
    toml_result = _write_codex_toml(
        toml_target,
        {"mcp_servers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
    )

    assert json_result["ok"] is False
    assert toml_result["ok"] is False
    assert "single-link regular file" in json_result["error"]
    assert "single-link regular file" in toml_result["error"]
    assert external.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("config_format", ["json", "toml"])
@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_config_writers_refuse_aliased_backup_targets(tmp_path, config_format, alias_kind):
    victim = tmp_path / f"{config_format}-victim"
    victim.write_text("victim bytes", encoding="utf-8")
    target = tmp_path / ("mcp.json" if config_format == "json" else "config.toml")
    original = b'{"mcpServers": {}}\n' if config_format == "json" else b'model = "gpt-test"\n'
    target.write_bytes(original)
    backup = target.with_suffix(target.suffix + ".bak")
    try:
        if alias_kind == "symlink":
            backup.symlink_to(victim)
        else:
            os.link(victim, backup)
    except OSError as error:
        pytest.skip(f"{alias_kind} unavailable: {error}")

    if config_format == "json":
        result = _write_config(target, {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}})
    else:
        result = _write_codex_toml(
            target,
            {"mcp_servers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
        )

    assert result["ok"] is False
    assert "backup target is unsafe" in result["error"]
    assert target.read_bytes() == original
    assert victim.read_text(encoding="utf-8") == "victim bytes"


def test_write_codex_toml_creates_official_stdio_shape(tmp_path):
    target = tmp_path / ".codex" / "config.toml"
    cfg = {"mcp_servers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}

    result = _write_codex_toml(target, cfg)

    assert result["ok"] is True
    assert result["created"] is True
    text = target.read_text(encoding="utf-8")
    assert "[mcp_servers.roam-code]" in text
    assert 'command = "roam"' in text
    assert 'args = ["mcp"]' in text


def test_write_codex_toml_replaces_only_roam_tables_and_preserves_comments(tmp_path):
    target = tmp_path / "config.toml"
    original = (
        """# personal settings
model = "gpt-test"

[mcp_servers.filesystem]
command = "npx"

[mcp_servers."roam-code"]
command = "old-roam"
args = []

[mcp_servers."roam-code".env]
OLD = "value"

# explanation for the unrelated feature table
[features]
multi_agent = true

# preserve trailing whitespace exactly
"""
        + "   \n"
    )
    target.write_text(original, encoding="utf-8")
    cfg = {
        "mcp_servers": {
            "roam-code": {
                "command": "roam",
                "args": ["mcp"],
                "env": {"ROAM_MCP_PRESET": "review"},
            }
        }
    }

    result = _write_codex_toml(target, cfg)

    assert result["ok"] is True
    assert result["merged"] is True
    assert Path(result["backup"]).read_text(encoding="utf-8") == original
    text = target.read_text(encoding="utf-8")
    assert "# personal settings" in text
    assert "[mcp_servers.filesystem]" in text
    assert "# explanation for the unrelated feature table\n[features]" in text
    assert "[features]" in text
    assert "old-roam" not in text
    assert "OLD" not in text
    assert text.count("[mcp_servers.roam-code]") == 1
    assert 'ROAM_MCP_PRESET = "review"' in text
    assert "# preserve trailing whitespace exactly\n   \n\n[mcp_servers.roam-code]" in text


def test_write_codex_toml_refuses_invalid_or_inline_existing_server(tmp_path):
    target = tmp_path / "config.toml"
    cfg = {"mcp_servers": {"roam-code": {"command": "roam", "args": ["mcp"]}}}
    target.write_text("not = [valid", encoding="utf-8")
    invalid = _write_codex_toml(target, cfg)
    assert invalid["ok"] is False
    assert "not valid TOML" in invalid["error"]
    assert target.read_text(encoding="utf-8") == "not = [valid"

    inline = 'mcp_servers.roam-code = { command = "old", args = [] }\n'
    target.write_text(inline, encoding="utf-8")
    unsupported = _write_codex_toml(target, cfg)
    assert unsupported["ok"] is False
    assert "inline or dotted TOML form" in unsupported["error"]
    assert target.read_text(encoding="utf-8") == inline


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


def test_cli_codex_write_uses_user_toml_and_preset(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(mcp_setup, ["codex-cli", "--preset", "debug", "--write"], obj={})

    assert result.exit_code == 0, result.output
    target = tmp_path / ".codex" / "config.toml"
    text = target.read_text(encoding="utf-8")
    assert "[mcp_servers.roam-code]" in text
    assert "[mcp_servers.roam-code.env]" in text
    assert 'ROAM_MCP_PRESET = "debug"' in text


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
