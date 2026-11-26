"""Exercise FastMCP's startup banner without any network or cache."""

import importlib

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("override", [None, "stable", "prerelease", "off"])
@pytest.mark.parametrize("preloaded", [False, True])
def test_mcp_startup_update_notice(monkeypatch, override, preloaded):
    fastmcp = pytest.importorskip("fastmcp")
    if not hasattr(fastmcp.settings, "check_for_updates"):
        pytest.skip("FastMCP does not expose the update check")
    from fastmcp.utilities import version_check
    from fastmcp.utilities.cli import log_server_banner

    server = importlib.import_module("roam.mcp_server")
    queries = []

    def latest(include_prereleases):
        queries.append(include_prereleases)
        return "999.0.0"

    monkeypatch.delenv("FASTMCP_CHECK_FOR_UPDATES", raising=False)
    if not preloaded and override is not None:
        monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", override)
    settings = type(fastmcp.settings)(_env_file=None)
    if preloaded and override is not None:
        monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", override)
    monkeypatch.setattr(fastmcp, "settings", settings)
    monkeypatch.setattr(version_check, "get_latest_version", latest)
    monkeypatch.setattr(server.mcp, "run", lambda: log_server_banner(server.mcp))

    result = CliRunner().invoke(cli, ["mcp", "--no-auto-index"], color=False)
    assert result.exit_code == 0, result.output
    enabled = override in ("stable", "prerelease")
    assert ("Update available: 999.0.0" in result.stderr) is enabled
    assert queries == ([override == "prerelease"] if enabled else [])
    assert settings.check_for_updates == ((None if preloaded else override) or "stable")
