"""Keep every human-facing MCP preset inventory bound to the registry."""

from __future__ import annotations

from click.testing import CliRunner

from tests._helpers.repo_root import repo_root

ROOT = repo_root()


def _preset_names() -> tuple[str, ...]:
    from roam.mcp_server import _PRESETS

    return tuple(_PRESETS)


def test_cli_help_advertises_every_registered_mcp_preset():
    from roam.commands.cmd_mcp import mcp
    from roam.commands.cmd_mcp_setup import mcp_setup
    from roam.mcp_server import mcp_cmd

    names = _preset_names()
    runner = CliRunner()

    for command in (mcp, mcp_cmd):
        result = runner.invoke(command, ["--help"])
        assert result.exit_code == 0, result.output
        environment = result.output.split("environment:", 1)[1].split("integration:", 1)[0]
        missing = [name for name in names if name not in environment]
        assert not missing, f"{command.name} environment help omits MCP presets: {missing}"

    result = runner.invoke(mcp_setup, ["--help"])
    assert result.exit_code == 0, result.output
    inventory = result.output.split("Presets:", 1)[1].split("Examples:", 1)[0]
    missing = [name for name in names if name not in inventory]
    assert not missing, f"mcp-setup help omits MCP presets: {missing}"


def test_public_docs_advertise_every_registered_mcp_preset():
    from roam.surface_counts import mcp_preset_counts

    names = _preset_names()
    full_count = mcp_preset_counts()["full"]
    markdown_inventory = ", ".join(f"`{name}`" for name in names)
    slash_inventory = " / ".join(names)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"{len(names)} selectable presets ({markdown_inventory})" in readme

    llms_install = (ROOT / "llms-install.md").read_text(encoding="utf-8")
    llms_install = " ".join(llms_install.split())
    assert f"Presets (env var `ROAM_MCP_PRESET`): {markdown_inventory}." in llms_install

    usage = (ROOT / "templates/distribution/landing-page/docs/mcp-usage.html").read_text(encoding="utf-8")
    assert f"Inspect preset contents ({slash_inventory})." in usage
    assert "Changing the active toolset requires a server restart." in usage
    assert f"{full_count} MCP tools, {len(names)} presets" in usage
