"""Generate MCP server configuration for AI coding platforms.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam mcp-setup`` is a setup/bootstrap command — its
output is human-facing setup status (MCP client config JSON written
for the detected platform), not analysis findings with file:line
coordinates. SARIF is reserved for scanning results. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# Platform config templates.
#
# ``config_path`` is the on-disk location for ``--write`` mode:
# * ``~/...`` paths resolve via ``Path.expanduser`` and are user-global.
# * ``./...`` paths resolve relative to the current working dir and are
#   project-local.
# When ``--write`` is set, the command merges the relevant ``json_config``
# block into the file (creating it if absent, otherwise merging without
# clobbering other entries). The existing file is backed up alongside.
_CONFIGS = {
    "claude-code": {
        "description": "Claude Code CLI",
        "setup_command": "claude mcp add roam-code -- roam mcp",
        "instructions": [
            "Run: claude mcp add roam-code -- roam mcp",
            "Or add to .mcp.json in your project root:",
        ],
        "config_path": "./.mcp.json",
        "json_config": {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
    },
    "cursor": {
        "description": "Cursor IDE",
        "instructions": [
            "Add to .cursor/mcp.json in your project root:",
        ],
        "config_path": "./.cursor/mcp.json",
        "json_config": {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
    },
    "windsurf": {
        "description": "Windsurf IDE",
        "instructions": [
            "Add to ~/.codeium/windsurf/mcp_config.json:",
        ],
        "config_path": "~/.codeium/windsurf/mcp_config.json",
        "json_config": {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
    },
    "vscode": {
        "description": "VS Code (Copilot Agent Mode)",
        "instructions": [
            "Add to .vscode/mcp.json in your project root:",
        ],
        "config_path": "./.vscode/mcp.json",
        "json_config": {"servers": {"roam-code": {"type": "stdio", "command": "roam", "args": ["mcp"]}}},
    },
    "gemini-cli": {
        "description": "Gemini CLI",
        "instructions": [
            "Add to ~/.gemini/settings.json:",
        ],
        "config_path": "~/.gemini/settings.json",
        "json_config": {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
    },
    "codex-cli": {
        "description": "OpenAI Codex CLI",
        "instructions": [
            "Add to ~/.codex/config.json or use:",
            "codex --mcp roam-code='roam mcp'",
        ],
        "config_path": "~/.codex/config.json",
        "json_config": {"mcpServers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
    },
}


def _resolve_config_path(rel_path: str, project_root: Path | None = None) -> Path:
    """Resolve a config path string into an absolute Path.

    ``~`` paths expand against ``Path.home()``; ``./`` paths resolve
    against ``project_root`` (defaulting to the current working dir).
    """
    if rel_path.startswith("~"):
        return Path(rel_path).expanduser()
    base = project_root or Path.cwd()
    # Strip leading ``./`` to keep Path joining clean.
    p = rel_path.removeprefix("./")
    return (base / p).resolve()


def _merge_config(existing: dict, incoming: dict) -> dict:
    """Merge ``incoming`` into ``existing``, preserving any keys not
    touched by ``incoming``.

    The merge is shallow at the top level then deep within the
    ``mcpServers`` / ``servers`` sub-dict so that other server entries
    keep their config but ``roam-code``'s entry is updated to whatever
    we're writing now.
    """
    merged = dict(existing)
    for outer_key, server_block in incoming.items():
        if outer_key in merged and isinstance(merged[outer_key], dict) and isinstance(server_block, dict):
            inner = dict(merged[outer_key])
            inner.update(server_block)
            merged[outer_key] = inner
        else:
            merged[outer_key] = server_block
    return merged


def _write_config(target: Path, json_config: dict) -> dict[str, Any]:
    """Write ``json_config`` to ``target``, merging with any existing
    contents. Returns a summary describing what happened.

    On any merge / write failure the original file is left untouched
    (we only rename the backup INTO place, never overwrite blindly).
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    pre_existed = target.is_file()
    backup_path: Path | None = None

    if pre_existed:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return {
                "ok": False,
                "path": str(target),
                "error": f"existing file is not valid JSON ({type(e).__name__}): {e}",
            }
        if not isinstance(existing, dict):
            return {
                "ok": False,
                "path": str(target),
                "error": "existing file is JSON but not an object at the top level",
            }
        backup_path = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup_path)
        merged = _merge_config(existing, json_config)
    else:
        merged = json_config

    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "path": str(target),
        "created": not pre_existed,
        "merged": pre_existed,
        "backup": str(backup_path) if backup_path else None,
    }


@roam_capability(
    name="mcp-setup",
    category="getting-started",
    summary="Generate MCP server config for AI coding platforms",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=False,
    requires_index=False,
)
@click.command("mcp-setup")
@click.argument("platform", type=click.Choice(sorted(_CONFIGS.keys())), required=False)
@click.option(
    "--preset",
    type=click.Choice(["core", "review", "refactor", "debug", "architecture", "compliance", "full"]),
    default=None,
    help=(
        "Pre-fill the generated config with ``ROAM_MCP_PRESET=<preset>``. "
        "Default = no env var (uses 'core'). The 'compliance' preset "
        "exposes 13 tools focused on AI-governance evidence workflows: "
        "preflight, taint, SBOM, and code-graph attest emit/verify."
    ),
)
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help=(
        "Write the config to the platform's expected location instead "
        "of just printing it. Project-scoped configs (claude-code, "
        "cursor, vscode) write under the current directory; user-scoped "
        "configs (windsurf, gemini-cli, codex-cli) write under your "
        "home directory. Existing files are merged (never clobbered) "
        "and a sibling ``.bak`` copy is left behind."
    ),
)
@click.pass_context
def mcp_setup(ctx, platform, preset, write):
    """Generate MCP server config for AI coding platforms.

    Prints the exact JSON config block to paste into your AI coding tool.
    Unlike ``ci-setup`` (which generates CI/CD pipeline YAML files), this
    command generates MCP server JSON configurations.

    \b
    Supported platforms:
      claude-code   Claude Code CLI
      cursor        Cursor IDE
      windsurf      Windsurf IDE
      vscode        VS Code (Copilot Agent Mode)
      gemini-cli    Gemini CLI
      codex-cli     OpenAI Codex CLI

    \b
    Presets:
      core          49 tools — default, balanced for daily agent use
      compliance    13 tools — AI-governance evidence (taint, sbom, cga, …)
      full          137 tools — every tool exposed
      review/refactor/debug/architecture — task-specific subsets

    \b
    Examples:
      roam mcp-setup claude-code
      roam mcp-setup cursor --preset compliance
      roam mcp-setup vscode --write
      roam --json mcp-setup vscode

    See also ``init`` (project bootstrap) and ``doctor`` (verifies your
    MCP server is registered and reachable).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if not platform:
        # List all platforms
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "mcp-setup",
                        summary={"verdict": f"{len(_CONFIGS)} platforms supported"},
                        platforms=list(_CONFIGS.keys()),
                    )
                )
            )
            return
        click.echo("Supported platforms:\n")
        for name, cfg in sorted(_CONFIGS.items()):
            click.echo(f"  {name:16s} {cfg['description']}")
        click.echo("\nUsage: roam mcp-setup <platform>")
        return

    cfg = _CONFIGS[platform]

    # v12.2: when --preset is supplied, deep-copy the config and inject the
    # ROAM_MCP_PRESET env var into the server block. Each platform stores
    # its server entry under a slightly different key shape (mcpServers vs
    # servers) — handle both. Mutates a copy, not the module-level dict.
    if preset:
        import copy

        cfg = copy.deepcopy(cfg)
        jc = cfg.get("json_config") or {}
        # Walk to the first server entry and add an "env" block.
        for outer_key in ("mcpServers", "servers"):
            if outer_key not in jc:
                continue
            for server_name, server_block in jc[outer_key].items():
                env = server_block.setdefault("env", {})
                env["ROAM_MCP_PRESET"] = preset
        cfg["json_config"] = jc
        cfg["preset"] = preset

    write_result: dict[str, Any] | None = None
    if write:
        config_path = cfg.get("config_path")
        json_config = cfg.get("json_config") or {}
        if not config_path:
            write_result = {
                "ok": False,
                "path": None,
                "error": f"no config_path defined for platform {platform!r}",
            }
        else:
            target = _resolve_config_path(config_path)
            write_result = _write_config(target, json_config)

    if json_mode:
        envelope_kwargs: dict[str, Any] = {
            "platform": platform,
            "description": cfg["description"],
            "instructions": cfg.get("instructions", []),
            "config": cfg.get("json_config", {}),
            "setup_command": cfg.get("setup_command"),
        }
        if write_result is not None:
            envelope_kwargs["write_result"] = write_result
        verdict = (
            f"Config written to {write_result['path']}"
            if write_result and write_result.get("ok")
            else f"Config for {cfg['description']}"
        )
        click.echo(
            to_json(
                json_envelope(
                    "mcp-setup",
                    summary={"verdict": verdict, "platform": platform},
                    **envelope_kwargs,
                )
            )
        )
        if write_result is not None and not write_result.get("ok"):
            ctx.exit(1)
        return

    # Text output
    click.echo(f"=== {cfg['description']} ===\n")
    for instruction in cfg.get("instructions", []):
        click.echo(f"  {instruction}")

    if cfg.get("setup_command"):
        click.echo(f"\n  Quick setup:\n    {cfg['setup_command']}\n")

    json_config = cfg.get("json_config")
    if json_config:
        click.echo("\n  Configuration JSON:")
        click.echo(json.dumps(json_config, indent=2))

    if write_result is not None:
        click.echo()
        if write_result.get("ok"):
            action = "Created" if write_result.get("created") else "Updated"
            click.echo(f"  {action} {write_result['path']}")
            if write_result.get("backup"):
                click.echo(f"  Backup at {write_result['backup']}")
        else:
            click.echo(f"  WRITE FAILED: {write_result.get('error', 'unknown error')}", err=True)
            ctx.exit(1)
