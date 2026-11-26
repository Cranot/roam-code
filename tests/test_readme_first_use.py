"""Keep the README's MCP onboarding runnable from the public CLI."""

import re

from click.testing import CliRunner

from roam.cli import cli
from tests._helpers.repo_root import repo_root


def _first_use():
    readme = (repo_root() / "README.md").read_text(encoding="utf-8")
    return readme.split("## MCP Server", 1)[0]


def test_readme_connect_step():
    first_use = _first_use()
    connect = first_use.split("## Connect your agent", 1)[1].split("## Try a task", 1)[0]
    assert "roam init\nroam mcp-setup claude-code --write" in connect
    assert "roam mcp-setup <agent> --write" in connect
    assert "https://roam-code.com/setup#install-mcp" in connect

    runner = CliRunner()
    setup_help = runner.invoke(cli, ["mcp-setup", "--help"])
    assert setup_help.exit_code == 0, setup_help.output
    platforms = ("claude-code", "cursor", "windsurf", "vscode", "gemini-cli", "codex-cli")
    for platform in platforms:
        assert f"`{platform}`" in connect
        assert re.search(rf"\b{platform}\s", setup_help.stdout)


def test_readme_first_use_commands():
    first_use = _first_use()
    help_all = CliRunner().invoke(cli, ["--help-all"], terminal_width=100)
    assert help_all.exit_code == 0, help_all.output
    commands = set(re.findall(r"^  ([a-z][a-z0-9-]*)(?: {2,}|$)", help_all.stdout, re.M))
    examples = set(re.findall(r"(?:^`?|[`| ])roam ([a-z][a-z0-9-]*)(?=\s|`|$)", first_use, re.M))
    assert {"index", "init", "health", "search", "preflight"} <= examples
    assert examples <= commands
