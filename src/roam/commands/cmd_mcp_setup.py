"""Generate MCP server configuration for AI coding platforms.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam mcp-setup`` is a setup/bootstrap command — its
output is human-facing setup status (MCP client config JSON or TOML written
for the detected platform), not analysis findings with file:line
coordinates. SARIF is reserved for scanning results. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

import click

from roam.atomic_io import atomic_write_bytes
from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# Platform config templates.
#
# ``config_path`` is the on-disk location for ``--write`` mode:
# * ``~/...`` paths resolve via ``Path.expanduser`` and are user-global.
# * ``./...`` paths resolve relative to the current working dir and are
#   project-local.
# When ``--write`` is set, the command merges the normalized ``json_config``
# block into the platform's JSON or TOML file (creating it if absent, otherwise
# merging without clobbering other entries). The existing file is backed up.
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
        "setup_command": "codex mcp add roam-code -- roam mcp",
        "instructions": [
            "Run: codex mcp add roam-code -- roam mcp",
            "Or add the TOML block below to ~/.codex/config.toml:",
        ],
        "config_path": "~/.codex/config.toml",
        "config_format": "toml",
        "json_config": {"mcp_servers": {"roam-code": {"command": "roam", "args": ["mcp"]}}},
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


def _existing_config_state(target: Path) -> tuple[bool | None, str | None]:
    """Classify a config target without following its final path component."""
    try:
        info = target.lstat()
    except FileNotFoundError:
        return False, None
    except OSError as error:
        return None, f"existing config cannot be inspected ({type(error).__name__}): {error}"
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return None, "existing config must be a single-link regular file"
    return True, None


def _publish_config_backup(target: Path, payload: bytes) -> tuple[Path | None, str | None]:
    """Atomically preserve parsed config bytes without following backup aliases."""
    backup = target.with_suffix(target.suffix + ".bak")
    backup_exists, backup_error = _existing_config_state(backup)
    if backup_exists is None:
        return None, f"backup target is unsafe: {backup_error}"
    try:
        atomic_write_bytes(backup, payload)
    except (OSError, RuntimeError) as error:
        return None, f"backup publication failed ({type(error).__name__}): {error}"
    return backup, None


def _assert_config_generation(target: Path, expected: bytes | None) -> None:
    """Fail when a config changed after it was parsed for a merge."""
    exists, error = _existing_config_state(target)
    if exists is None:
        raise RuntimeError(error or "config generation cannot be inspected")
    if expected is None:
        if exists:
            raise FileExistsError(f"config appeared during publication: {target}")
        return
    if not exists:
        raise RuntimeError("config disappeared during publication")
    actual = target.read_bytes()
    exists_after, error_after = _existing_config_state(target)
    if exists_after is not True:
        raise RuntimeError(error_after or "config changed while its generation was read")
    if actual != expected:
        raise RuntimeError("config changed after it was parsed; retry the merge")


def _write_config(target: Path, json_config: dict) -> dict[str, Any]:
    """Write ``json_config`` to ``target``, merging with any existing
    contents. Returns a summary describing what happened.

    On any merge / write failure the original file is left untouched
    (we only rename the backup INTO place, never overwrite blindly).
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    pre_existed, target_error = _existing_config_state(target)
    if pre_existed is None:
        return {"ok": False, "path": str(target), "error": target_error}
    backup_path: Path | None = None
    existing_bytes: bytes | None = None

    if pre_existed:
        try:
            existing_bytes = target.read_bytes()
            existing = json.loads(existing_bytes)
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
        backup_path, backup_error = _publish_config_backup(target, existing_bytes)
        if backup_path is None:
            return {"ok": False, "path": str(target), "error": backup_error}
        merged = _merge_config(existing, json_config)
    else:
        merged = json_config

    try:
        payload = (json.dumps(merged, indent=2) + "\n").encode("utf-8")
        atomic_write_bytes(
            target,
            payload,
            before_replace=lambda: _assert_config_generation(target, existing_bytes),
            require_absent=not pre_existed,
        )
    except (OSError, RuntimeError) as error:
        return {
            "ok": False,
            "path": str(target),
            "error": f"config publication failed ({type(error).__name__}): {error}",
        }
    return {
        "ok": True,
        "path": str(target),
        "created": not pre_existed,
        "merged": pre_existed,
        "backup": str(backup_path) if backup_path else None,
    }


_TOML_TABLE_HEADER = re.compile(r"^\s*(\[\[?[^\r\n]+?\]\]?)\s*(?:#.*)?$")
_TOML_MARKER = "__roam_mcp_setup_marker__"


def _load_toml(text: str) -> dict[str, Any]:
    """Parse TOML with the stdlib on 3.11+ and the package fallback on 3.10."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10 only
        import tomli as tomllib

    return tomllib.loads(text)


def _toml_header_path(header: str) -> tuple[str, ...] | None:
    """Return the semantic key path for one TOML table header."""
    try:
        parsed = _load_toml(f"{header}\n{_TOML_MARKER} = true\n")
    except Exception:
        return None

    def find(value: Any, path: tuple[str, ...]) -> tuple[str, ...] | None:
        if isinstance(value, dict):
            if value.get(_TOML_MARKER) is True:
                return path
            for key, child in value.items():
                found = find(child, (*path, key))
                if found is not None:
                    return found
        elif isinstance(value, list) and len(value) == 1:
            return find(value[0], path)
        return None

    return find(parsed, ())


def _render_codex_toml(config: dict[str, Any]) -> str:
    """Render the closed Roam STDIO server contract as Codex TOML."""
    try:
        server = config["mcp_servers"]["roam-code"]
    except (KeyError, TypeError) as error:
        raise ValueError("Codex config lacks mcp_servers.roam-code") from error
    if server.get("command") != "roam" or server.get("args") != ["mcp"]:
        raise ValueError("Codex config differs from the reviewed roam mcp STDIO contract")

    lines = [
        "[mcp_servers.roam-code]",
        'command = "roam"',
        'args = ["mcp"]',
    ]
    env = server.get("env") or {}
    if env:
        if set(env) != {"ROAM_MCP_PRESET"} or not isinstance(env["ROAM_MCP_PRESET"], str):
            raise ValueError("Codex config contains an unsupported environment contract")
        lines.extend(
            [
                "",
                "[mcp_servers.roam-code.env]",
                f"ROAM_MCP_PRESET = {json.dumps(env['ROAM_MCP_PRESET'])}",
            ]
        )
    return "\n".join(lines) + "\n"


def _write_codex_toml(target: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Merge Roam's table into Codex ``config.toml`` without touching peers.

    Existing TOML is parsed before mutation. Section-based Roam configuration
    is replaced while comments and unrelated tables remain byte-for-byte
    intact. Inline/dotted definitions of the same server fail closed because a
    safe surgical rewrite cannot preserve their formatting unambiguously.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed, target_error = _existing_config_state(target)
    if pre_existed is None:
        return {"ok": False, "path": str(target), "error": target_error}
    backup_path: Path | None = None
    existing_text = ""
    existing_bytes: bytes | None = None
    existing_server = False

    if pre_existed:
        try:
            existing_bytes = target.read_bytes()
            existing_text = existing_bytes.decode("utf-8")
            parsed = _load_toml(existing_text)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            return {
                "ok": False,
                "path": str(target),
                "error": f"existing file is not valid TOML ({type(error).__name__}): {error}",
            }
        servers = parsed.get("mcp_servers", {})
        if not isinstance(servers, dict):
            return {"ok": False, "path": str(target), "error": "mcp_servers is not a TOML table"}
        existing_server = "roam-code" in servers

    target_path = ("mcp_servers", "roam-code")
    lines = existing_text.splitlines(keepends=True)
    kept: list[str] = []
    skipped_comment_tail: list[str] = []
    removed_target = False
    skip = False
    for line in lines:
        match = _TOML_TABLE_HEADER.match(line.rstrip("\r\n"))
        if match:
            path = _toml_header_path(match.group(1))
            if path is None:
                return {"ok": False, "path": str(target), "error": "existing TOML has an unsupported table header"}
            next_skip = path[: len(target_path)] == target_path
            if skip and not next_skip:
                kept.extend(skipped_comment_tail)
            skipped_comment_tail.clear()
            skip = next_skip
            removed_target = removed_target or next_skip
        if not skip:
            kept.append(line)
        elif not match and (not line.strip() or line.lstrip().startswith("#")):
            skipped_comment_tail.append(line)
        else:
            skipped_comment_tail.clear()

    if existing_server and not removed_target:
        return {
            "ok": False,
            "path": str(target),
            "error": "existing roam-code MCP config uses an inline or dotted TOML form; use `codex mcp remove roam-code` then retry",
        }

    prefix = "".join(kept)
    rendered = _render_codex_toml(config)
    if not prefix:
        merged_text = rendered
    elif prefix.endswith(("\n\n", "\r\n\r\n")):
        merged_text = f"{prefix}{rendered}"
    elif prefix.endswith(("\n", "\r\n")):
        merged_text = f"{prefix}\n{rendered}"
    else:
        merged_text = f"{prefix}\n\n{rendered}"
    try:
        parsed_merged = _load_toml(merged_text)
    except ValueError as error:  # defensive: never publish an invalid merge
        return {"ok": False, "path": str(target), "error": f"generated TOML failed validation: {error}"}
    if parsed_merged.get("mcp_servers", {}).get("roam-code") != config["mcp_servers"]["roam-code"]:
        return {"ok": False, "path": str(target), "error": "generated TOML differs from the requested MCP contract"}

    if pre_existed:
        assert existing_bytes is not None
        backup_path, backup_error = _publish_config_backup(target, existing_bytes)
        if backup_path is None:
            return {"ok": False, "path": str(target), "error": backup_error}
    try:
        atomic_write_bytes(
            target,
            merged_text.encode("utf-8"),
            before_replace=lambda: _assert_config_generation(target, existing_bytes),
            require_absent=not pre_existed,
        )
    except (OSError, RuntimeError) as error:
        return {
            "ok": False,
            "path": str(target),
            "error": f"config publication failed ({type(error).__name__}): {error}",
        }
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
    type=click.Choice(["core", "review", "refactor", "debug", "architecture", "compliance", "compile-curated", "full"]),
    default=None,
    help=(
        "Pre-fill the generated config with ``ROAM_MCP_PRESET=<preset>``. "
        "Default = no env var (uses 'core'). The 'compliance' preset "
        "exposes 14 tools focused on AI-governance evidence workflows: "
        "preflight, taint, SBOM, and code-graph attest emit/verify. The "
        "'compile-curated' preset exposes EXACTLY the 8 graph tools "
        "compile-code pre-approves for its `wire --mcp` agent surface."
    ),
)
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help=(
        "Write the JSON or TOML config to the platform's expected location instead "
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

    Prints the exact JSON or TOML config block to paste into your AI coding tool.
    Unlike ``ci-setup`` (which generates CI/CD pipeline YAML files), this
    command generates MCP server configurations.

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
      core          default, balanced for daily agent use
      review        change-review subset
      refactor      refactoring subset
      debug         debugging subset
      architecture  architecture-analysis subset
      compliance    AI-governance evidence subset
      compile-curated  Compile's pre-approved graph subset
      full          every registered tool

    \b
    Examples:
      roam mcp-setup claude-code
      roam mcp-setup cursor --preset compliance
      roam mcp-setup vscode --write
      roam mcp-setup codex-cli --write
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
        for outer_key in ("mcpServers", "servers", "mcp_servers"):
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
            # Project-local configs (./.mcp.json, ./.cursor/mcp.json,
            # ./.vscode/mcp.json) must anchor on the project root, not the
            # current working dir — running `roam mcp-setup vscode --write`
            # from a subdirectory previously wrote into `<subdir>/.vscode/`
            # instead of `<project>/.vscode/`, a silent-write-to-wrong-dir
            # footgun in the spirit of W554.
            project_root: Path | None = None
            if config_path.startswith("./"):
                try:
                    from roam.db.connection import find_project_root

                    project_root = find_project_root()
                except (OSError, RuntimeError):
                    project_root = None
            target = _resolve_config_path(config_path, project_root)
            write_result = (
                _write_codex_toml(target, json_config)
                if cfg.get("config_format") == "toml"
                else _write_config(target, json_config)
            )

    if json_mode:
        envelope_kwargs: dict[str, Any] = {
            "platform": platform,
            "description": cfg["description"],
            "instructions": cfg.get("instructions", []),
            "config": cfg.get("json_config", {}),
            "config_format": cfg.get("config_format", "json"),
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
        if cfg.get("config_format") == "toml":
            click.echo("\n  Configuration TOML:")
            click.echo(_render_codex_toml(json_config), nl=False)
        else:
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
