"""roam compatibility - detect outbound surface regressions vs a baseline.

W1293 (P1.2 from (internal memo)). Catches the same bug
class CLAUDE.md Constraint 8 protects against ("Use semantically meaningful
operation names - closed enumeration") but for OUTBOUND surface contracts
that users / agents / CI depend on:

  * CLI:     a command renamed or removed; a flag removed.
  * JSON:    a top-level envelope field removed; a closed-enum value removed.
  * MCP:     a tool renamed; a preset changed.

Scope (intentionally MVP):

  * Captures a snapshot of the current build via ``_build_snapshot()`` and
    compares it to a baseline JSON file (default
    ``dev/compatibility-baseline.json``).
  * Closed-enum verdict categories: ``no regressions`` / ``surface drift`` /
    ``breaking changes``.
  * ``--ci`` exits 5 (EXIT_GATE_FAILURE) on any entry classified
    ``breaking``.
  * Detection is structural ONLY: name presence, flag presence, MCP-tool
    presence, preset count. Behavior-regression detection is explicitly
    out of scope (a much larger problem).

The baseline is captured by running the command itself with
``--write-baseline``; commit the resulting snapshot so future runs gate
against the last-known-good surface.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because compatibility outputs are repo-scoped surface-contract
deltas (CLI / JSON / MCP name additions and removals) — not
per-location code violations at source coordinates. SARIF requires
``locations[]``; compatibility surface drifts have no file/line to
populate. The ``--ci`` exit-5 gate already provides CI integration.
See W1148 audit memo + (internal memo)
§8 for the disclosure framework. Introduced at W1293.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.exit_codes import EXIT_GATE_FAILURE
from roam.output.formatter import json_envelope, to_json

# Snapshot schema version. Bump on any structural change to the on-disk
# baseline shape (added/removed top-level keys, restructured per-command
# fields). The comparator falls back to "best-effort" against older
# snapshots and surfaces a partial_success=true verdict noting the drift.
SNAPSHOT_SCHEMA_VERSION = "1.0.0"

# Top-level envelope summary keys we want to gate on for the canonical
# ``roam surface --json`` envelope. The compatibility command treats THIS
# envelope as the canonical witness because it's the single envelope every
# downstream consumer (docs gen, contract tests, release notes, the
# marketing/landscape surfaces) already depends on. Removing a key here is
# a breaking change for those consumers.
_CANONICAL_ENVELOPE_KEYS: tuple[str, ...] = (
    "command_count",
    "canonical_count",
    "category_count",
    "mcp_tool_count",
    "mcp_tool_count_by_preset",
    "mcp_introspection_available",
    "by_maturity",
    "verdict",
)


def _build_snapshot() -> dict[str, Any]:
    """Capture the current build's outbound surface as a snapshot dict.

    Reads from ``roam.cli._COMMANDS`` + ``roam.cli._DEPRECATED_COMMANDS`` +
    Click param introspection per command + ``roam.surface_counts`` for the
    AST-derived MCP tool / preset enumeration. The runtime ``roam.mcp_server``
    import is deliberately avoided here for the same reason
    ``cmd_surface._build_surface()`` avoids it (fragile on fresh installs).
    """
    from roam.cli import _CATEGORIES, _COMMANDS, _DEPRECATED_COMMANDS
    from roam.surface_counts import mcp_preset_counts, mcp_tool_names

    # Commands + flags. Click param introspection per canonical command.
    commands: dict[str, dict[str, Any]] = {}
    canonical_seen: set[tuple[str, str]] = set()
    for name in sorted(_COMMANDS):
        module_path, func_name = _COMMANDS[name]
        canonical_seen.add((module_path, func_name))
        flags = _introspect_flags(module_path, func_name)
        commands[name] = {
            "module": module_path,
            "function": func_name,
            "flags": sorted(flags),
        }

    # Deprecated alias map (used by the diff to recognise graceful renames).
    deprecated = {
        name: dict(record) for name, record in _DEPRECATED_COMMANDS.items()
    }

    mcp_tools = sorted(mcp_tool_names())
    mcp_presets = dict(mcp_preset_counts())

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "commands": commands,
        "deprecated_aliases": deprecated,
        "mcp_tools": mcp_tools,
        "mcp_preset_counts": mcp_presets,
        "categories": list(_CATEGORIES.keys()),
        "envelope_summary_keys": list(_CANONICAL_ENVELOPE_KEYS),
    }


def _introspect_flags(module_path: str, func_name: str) -> list[str]:
    """Return the flag/option names declared by a Click command.

    Best-effort: imports the module and walks ``cmd.params``. On
    ImportError (missing optional extra, refactor in flight) returns an
    empty list - the diff then sees the command as "no flags" rather
    than crashing the snapshot build. The detector reports this honestly
    by marking the per-command flags entry as ``unavailable`` only when
    the import itself fails (vs the legitimate "command has zero
    options" case).
    """
    try:
        mod = importlib.import_module(module_path)
    except Exception:
        return []
    cmd = getattr(mod, func_name, None)
    if cmd is None or not hasattr(cmd, "params"):
        return []
    out: list[str] = []
    for p in cmd.params:
        # Argument: positional, identity is the param name.
        # Option:   identity is the long-form flag (e.g. ``--ci``).
        if hasattr(p, "opts") and p.opts:
            # Prefer the long-form ``--xxx`` over short-form ``-x``.
            long_opts = [o for o in p.opts if o.startswith("--")]
            out.append(long_opts[0] if long_opts else p.opts[0])
        else:
            out.append(p.name)
    return out


def _diff(
    baseline: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Compute the structural diff between two snapshots.

    Returns a dict with closed-enum categories:
      removed_commands, added_commands, renamed_commands,
      removed_flags, added_flags,
      removed_envelope_fields, added_envelope_fields,
      removed_mcp_tools, added_mcp_tools,
      changed_presets.

    The ``breaking`` count counts entries that would BREAK an existing
    consumer (removed commands without alias, removed flags, removed
    envelope fields, removed MCP tools). Added entries are never
    breaking. A graceful rename (command removed from canonical names
    BUT an alias from old->new now exists in ``deprecated_aliases``) is
    surfaced under ``renamed_commands`` and is NOT counted as breaking.
    """
    base_cmds = baseline.get("commands", {}) or {}
    cur_cmds = current.get("commands", {}) or {}
    cur_deprecated = current.get("deprecated_aliases", {}) or {}

    base_names = set(base_cmds.keys())
    cur_names = set(cur_cmds.keys())

    removed_raw = sorted(base_names - cur_names)
    added = sorted(cur_names - base_names)

    # A removed command that now appears as a deprecated alias pointing to
    # something in cur_names is a GRACEFUL RENAME.
    renamed: list[dict[str, str]] = []
    removed_commands: list[str] = []
    for name in removed_raw:
        record = cur_deprecated.get(name)
        if record and record.get("replacement") in cur_names:
            renamed.append({"from": name, "to": record["replacement"]})
        else:
            removed_commands.append(name)

    # Per-command flag diff. We only diff commands that exist in BOTH
    # snapshots - removed-command flags are already counted under
    # ``removed_commands``.
    removed_flags: list[dict[str, str]] = []
    added_flags: list[dict[str, str]] = []
    for name in sorted(base_names & cur_names):
        base_fl = set(base_cmds[name].get("flags", []) or [])
        cur_fl = set(cur_cmds[name].get("flags", []) or [])
        for f in sorted(base_fl - cur_fl):
            removed_flags.append({"command": name, "flag": f})
        for f in sorted(cur_fl - base_fl):
            added_flags.append({"command": name, "flag": f})

    # Envelope summary key diff (closed-enum on the canonical ``surface``
    # envelope - other commands carry their own envelope shapes; MVP
    # coverage is the witness envelope only).
    base_env = set(baseline.get("envelope_summary_keys", []) or [])
    cur_env = set(current.get("envelope_summary_keys", []) or [])
    removed_envelope_fields = sorted(base_env - cur_env)
    added_envelope_fields = sorted(cur_env - base_env)

    # MCP tool diff. A renamed MCP tool would appear as both a removal
    # AND an addition - the MVP doesn't try to detect rename pairs (no
    # canonical alias substrate yet for MCP names; the 4 historical
    # renames live in ``_NAMING_DRIFT_ALIAS`` per CLAUDE.md). Future
    # extension: read that alias table here.
    base_mcp = set(baseline.get("mcp_tools", []) or [])
    cur_mcp = set(current.get("mcp_tools", []) or [])
    removed_mcp_tools = sorted(base_mcp - cur_mcp)
    added_mcp_tools = sorted(cur_mcp - base_mcp)

    # Preset count delta (presets are a closed enum: core / review /
    # refactor / debug / architecture / compliance / full).
    base_presets = baseline.get("mcp_preset_counts", {}) or {}
    cur_presets = current.get("mcp_preset_counts", {}) or {}
    changed_presets: list[dict[str, Any]] = []
    for preset in sorted(set(base_presets) | set(cur_presets)):
        b = base_presets.get(preset)
        c = cur_presets.get(preset)
        if b != c:
            changed_presets.append(
                {"preset": preset, "baseline_count": b, "current_count": c}
            )

    # Tally breaking entries. Added items + renames are NOT breaking.
    # Preset count drops ARE breaking (fewer tools in a preset breaks
    # consumers gated on that preset); preset count INCREASES are not.
    preset_shrinks = [
        e
        for e in changed_presets
        if (e["baseline_count"] is not None)
        and (e["current_count"] is not None)
        and (e["current_count"] < e["baseline_count"])
    ]

    breaking = (
        len(removed_commands)
        + len(removed_flags)
        + len(removed_envelope_fields)
        + len(removed_mcp_tools)
        + len(preset_shrinks)
    )

    return {
        "removed_commands": removed_commands,
        "added_commands": added,
        "renamed_commands": renamed,
        "removed_flags": removed_flags,
        "added_flags": added_flags,
        "removed_envelope_fields": removed_envelope_fields,
        "added_envelope_fields": added_envelope_fields,
        "removed_mcp_tools": removed_mcp_tools,
        "added_mcp_tools": added_mcp_tools,
        "changed_presets": changed_presets,
        "breaking_count": breaking,
        "preset_shrinks": preset_shrinks,
    }


def _verdict_for(diff: dict[str, Any]) -> tuple[str, str]:
    """Return ``(verdict, level)`` matching the diff.

    Closed-enum verdicts:
      ``no regressions``       -> no removed/breaking entries, no additions.
      ``surface additions``    -> only additions; no breaking entries.
      ``surface drift``        -> mixed adds + non-breaking renames.
      ``breaking changes``     -> at least one breaking entry.
    """
    if diff["breaking_count"] > 0:
        return ("breaking changes", "blocker")
    any_added = bool(
        diff["added_commands"]
        or diff["added_flags"]
        or diff["added_envelope_fields"]
        or diff["added_mcp_tools"]
    )
    any_drift = bool(diff["renamed_commands"] or diff["changed_presets"])
    if any_drift:
        return ("surface drift", "warning")
    if any_added:
        return ("surface additions", "info")
    return ("no regressions", "info")


def _default_baseline_path() -> Path:
    """Resolve the canonical baseline path (``dev/compatibility-baseline.json``).

    Walks up from this file's location until the project root is found
    (same anchor as ``surface_counts._repo_root``). Tests pass an
    explicit ``--baseline`` so they don't depend on this resolution.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "roam" / "cli.py").exists():
            return parent / "dev" / "compatibility-baseline.json"
    # Fallback (shouldn't hit in normal builds): cwd/dev/...
    return Path.cwd() / "dev" / "compatibility-baseline.json"


@roam_capability(
    name="compatibility",
    category="quality",
    summary="Detect outbound surface regressions vs a baseline snapshot",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command("compatibility")
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Baseline snapshot JSON (default: dev/compatibility-baseline.json).",
)
@click.option(
    "--current",
    "current_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Current snapshot JSON. Default: capture the live build.",
)
@click.option(
    "--write-baseline",
    "write_baseline",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write a fresh snapshot to this path and exit (no diff).",
)
@click.option(
    "--ci",
    is_flag=True,
    default=False,
    help="Exit 5 (EXIT_GATE_FAILURE) if any breaking entries are detected.",
)
@click.pass_context
def compatibility(
    ctx,
    baseline_path: Path | None,
    current_path: Path | None,
    write_baseline: Path | None,
    ci: bool,
):
    """Detect outbound surface regressions vs a baseline snapshot.

    \b
    Examples:
      roam compatibility                              # diff live build vs dev/compatibility-baseline.json
      roam compatibility --baseline old.json          # explicit baseline
      roam compatibility --write-baseline cur.json    # capture a fresh baseline
      roam compatibility --ci                         # exit 5 on breaking changes

    Detection scope (MVP, closed-enum):
      - removed/renamed/added commands
      - removed/added per-command flags
      - removed/added top-level envelope summary fields (canonical witness)
      - removed/added MCP tools
      - MCP preset count changes (preset shrinkage = breaking)

    Out of scope: semantic-behavior regressions (a much larger problem).
    """
    json_mode = bool(ctx.obj and ctx.obj.get("json"))

    # Write-baseline path: capture + exit (no diff).
    if write_baseline is not None:
        snapshot = _build_snapshot()
        write_baseline.parent.mkdir(parents=True, exist_ok=True)
        write_baseline.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "compatibility",
                        summary={
                            "verdict": "baseline written",
                            "level": "info",
                            "partial_success": False,
                            "commands": len(snapshot["commands"]),
                            "mcp_tools": len(snapshot["mcp_tools"]),
                            "path": str(write_baseline),
                        },
                        agent_contract={
                            "facts": [
                                f"{len(snapshot['commands'])} commands captured",
                                f"{len(snapshot['mcp_tools'])} MCP tools captured",
                                f"baseline path {write_baseline}",
                            ],
                            "next_commands": [
                                f"roam compatibility --baseline {write_baseline}"
                            ],
                        },
                    )
                )
            )
        else:
            click.echo(
                f"VERDICT: baseline written ({len(snapshot['commands'])} commands, "
                f"{len(snapshot['mcp_tools'])} MCP tools) -> {write_baseline}"
            )
        return

    # Resolve baseline.
    if baseline_path is None:
        baseline_path = _default_baseline_path()
    if not baseline_path.exists():
        # Pattern-1 variant C: emit a structured envelope on missing input,
        # never empty stdout. The verdict is honestly degraded.
        msg = (
            f"baseline not found at {baseline_path} - "
            "capture one with `roam compatibility --write-baseline <path>`"
        )
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "compatibility",
                        summary={
                            "verdict": "baseline missing",
                            "level": "warning",
                            "partial_success": True,
                            "state": "baseline_missing",
                        },
                        error_code="USAGE_ERROR",
                        error=msg,
                        hint=f"roam compatibility --write-baseline {baseline_path}",
                        next_command=f"roam compatibility --write-baseline {baseline_path}",
                        agent_contract={
                            "facts": ["0 baselines available"],
                            "next_commands": [
                                f"roam compatibility --write-baseline {baseline_path}"
                            ],
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: baseline missing - {msg}", err=True)
        if ci:
            ctx.exit(EXIT_GATE_FAILURE)
        return

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    # Resolve current.
    if current_path is None:
        current = _build_snapshot()
    else:
        current = json.loads(current_path.read_text(encoding="utf-8"))

    diff = _diff(baseline, current)
    verdict, level = _verdict_for(diff)

    breaking = diff["breaking_count"]
    removed_n = (
        len(diff["removed_commands"])
        + len(diff["removed_flags"])
        + len(diff["removed_envelope_fields"])
        + len(diff["removed_mcp_tools"])
    )
    renamed_n = len(diff["renamed_commands"])
    added_n = (
        len(diff["added_commands"])
        + len(diff["added_flags"])
        + len(diff["added_envelope_fields"])
        + len(diff["added_mcp_tools"])
    )

    if json_mode:
        # LAW-4 anchored facts. Terminals: commands, flags, fields, tools.
        facts = [
            f"{len(diff['removed_commands'])} removed commands",
            f"{len(diff['removed_flags'])} removed flags",
            f"{len(diff['removed_envelope_fields'])} removed envelope fields",
            f"{len(diff['removed_mcp_tools'])} removed MCP tools",
            f"{len(diff['added_commands'])} added commands",
            f"{len(diff['added_mcp_tools'])} added MCP tools",
            f"{breaking} breaking entries",
        ]
        next_commands: list[str] = []
        if breaking:
            next_commands.append(
                "# inspect breaking entries, then either restore the surface "
                "or roll the baseline forward"
            )
            next_commands.append(
                f"roam compatibility --write-baseline {baseline_path}"
            )
        click.echo(
            to_json(
                json_envelope(
                    "compatibility",
                    summary={
                        "verdict": verdict,
                        "level": level,
                        "partial_success": breaking > 0,
                        "removed": removed_n,
                        "renamed": renamed_n,
                        "added": added_n,
                        "breaking": breaking,
                    },
                    removed_commands=diff["removed_commands"],
                    added_commands=diff["added_commands"],
                    renamed_commands=diff["renamed_commands"],
                    removed_flags=diff["removed_flags"],
                    added_flags=diff["added_flags"],
                    removed_envelope_fields=diff["removed_envelope_fields"],
                    added_envelope_fields=diff["added_envelope_fields"],
                    removed_mcp_tools=diff["removed_mcp_tools"],
                    added_mcp_tools=diff["added_mcp_tools"],
                    changed_presets=diff["changed_presets"],
                    baseline_path=str(baseline_path),
                    agent_contract={
                        "facts": facts,
                        "next_commands": next_commands,
                    },
                )
            )
        )
    else:
        click.echo(
            f"VERDICT: {verdict}  "
            f"(removed={removed_n} renamed={renamed_n} added={added_n} "
            f"breaking={breaking})"
        )
        if diff["removed_commands"]:
            click.echo("")
            click.echo("removed commands:")
            for n in diff["removed_commands"]:
                click.echo(f"  - {n}")
        if diff["renamed_commands"]:
            click.echo("")
            click.echo("renamed commands:")
            for r in diff["renamed_commands"]:
                click.echo(f"  - {r['from']} -> {r['to']}")
        if diff["removed_flags"]:
            click.echo("")
            click.echo("removed flags:")
            for f in diff["removed_flags"]:
                click.echo(f"  - {f['command']} {f['flag']}")
        if diff["removed_envelope_fields"]:
            click.echo("")
            click.echo("removed envelope fields:")
            for f in diff["removed_envelope_fields"]:
                click.echo(f"  - surface.summary.{f}")
        if diff["removed_mcp_tools"]:
            click.echo("")
            click.echo("removed MCP tools:")
            for t in diff["removed_mcp_tools"]:
                click.echo(f"  - {t}")
        if diff["changed_presets"]:
            click.echo("")
            click.echo("changed presets:")
            for e in diff["changed_presets"]:
                click.echo(
                    f"  - {e['preset']}: {e['baseline_count']} -> {e['current_count']}"
                )

    if ci and breaking > 0:
        ctx.exit(EXIT_GATE_FAILURE)
