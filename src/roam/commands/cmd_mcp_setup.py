"""Generate MCP server configuration for AI coding platforms."""

from __future__ import annotations

import json

import click

from roam.output.formatter import to_json, json_envelope


# Platform config templates
_CONFIGS = {
    "claude-code": {
        "description": "Claude Code CLI",
        "setup_command": "claude mcp add roam-code -- roam mcp",
        "instructions": [
            "Run: claude mcp add roam-code -- roam mcp",
            "Or add to .mcp.json in your project root:",
        ],
        "json_config": {
            "mcpServers": {
                "roam-code": {
                    "command": "roam",
                    "args": ["mcp"]
                }
            }
        },
    },
    "cursor": {
        "description": "Cursor IDE",
        "instructions": [
            "Add to .cursor/mcp.json in your project root:",
        ],
        "json_config": {
            "mcpServers": {
                "roam-code": {
                    "command": "roam",
                    "args": ["mcp"]
                }
            }
        },
    },
    "windsurf": {
        "description": "Windsurf IDE",
        "instructions": [
            "Add to ~/.codeium/windsurf/mcp_config.json:",
        ],
        "json_config": {
            "mcpServers": {
                "roam-code": {
                    "command": "roam",
                    "args": ["mcp"]
                }
            }
        },
    },
    "vscode": {
        "description": "VS Code (Copilot Agent Mode)",
        "instructions": [
            "Add to .vscode/mcp.json in your project root:",
        ],
        "json_config": {
            "servers": {
                "roam-code": {
                    "type": "stdio",
                    "command": "roam",
                    "args": ["mcp"]
                }
            }
        },
    },
    "gemini-cli": {
        "description": "Gemini CLI",
        "instructions": [
            "Add to ~/.gemini/settings.json:",
        ],
        "json_config": {
            "mcpServers": {
                "roam-code": {
                    "command": "roam",
                    "args": ["mcp"]
                }
            }
        },
    },
    "codex-cli": {
        "description": "OpenAI Codex CLI",
        "instructions": [
            "Add to ~/.codex/config.json or use:",
            "codex --mcp roam-code='roam mcp'",
        ],
        "json_config": {
            "mcpServers": {
                "roam-code": {
                    "command": "roam",
                    "args": ["mcp"]
                }
            }
        },
    },
}


@click.command("mcp-setup")
@click.argument("platform", type=click.Choice(sorted(_CONFIGS.keys())), required=False)
@click.pass_context
def mcp_setup(ctx, platform):
    """Generate MCP server config for AI coding platforms.

    \b
    Supported platforms:
      claude-code   Claude Code CLI
      cursor        Cursor IDE
      windsurf      Windsurf IDE
      vscode        VS Code (Copilot Agent Mode)
      gemini-cli    Gemini CLI
      codex-cli     OpenAI Codex CLI

    \b
    Examples:
      roam mcp-setup claude-code
      roam mcp-setup cursor
      roam --json mcp-setup vscode
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False

    if not platform:
        # List all platforms
        if json_mode:
            click.echo(to_json(json_envelope("mcp-setup",
                summary={"verdict": f"{len(_CONFIGS)} platforms supported"},
                platforms=list(_CONFIGS.keys()),
            )))
            return
        click.echo("Supported platforms:\n")
        for name, cfg in sorted(_CONFIGS.items()):
            click.echo(f"  {name:16s} {cfg['description']}")
        click.echo(f"\nUsage: roam mcp-setup <platform>")
        return

    cfg = _CONFIGS[platform]

    if json_mode:
        click.echo(to_json(json_envelope("mcp-setup",
            summary={
                "verdict": f"Config for {cfg['description']}",
                "platform": platform,
            },
            platform=platform,
            description=cfg["description"],
            instructions=cfg.get("instructions", []),
            config=cfg.get("json_config", {}),
            setup_command=cfg.get("setup_command"),
        )))
        return

    # Text output
    click.echo(f"=== {cfg['description']} ===\n")
    for instruction in cfg.get("instructions", []):
        click.echo(f"  {instruction}")

    if cfg.get("setup_command"):
        click.echo(f"\n  Quick setup:\n    {cfg['setup_command']}\n")

    json_config = cfg.get("json_config")
    if json_config:
        click.echo(f"\n  Configuration JSON:")
        click.echo(json.dumps(json_config, indent=2))
