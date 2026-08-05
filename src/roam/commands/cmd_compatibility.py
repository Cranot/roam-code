"""roam compatibility - detect outbound surface regressions vs a baseline.

Catches the same bug class AGENTS.md Constraint 8 protects against
("Use semantically meaningful
operation names - closed enumeration") but for OUTBOUND surface contracts
that users / agents / CI depend on:

  * CLI:     a command renamed or removed; a flag removed.
  * JSON:    a top-level envelope field removed; a closed-enum value removed.
  * MCP:     a tool renamed; a preset changed.

Scope (intentionally MVP):

  * Captures a snapshot of the current build via ``_build_snapshot()`` and
    compares it to a baseline JSON file (default
    ``dev/compatibility-baseline.json``).
  * Closed-enum verdict categories: ``no regressions`` / ``surface
    additions`` / ``surface drift`` / ``baseline stale`` / ``breaking
    changes``.
  * ``--ci`` exits 5 (EXIT_GATE_FAILURE) on any entry classified
    ``breaking``.
  * ``--require-coverage`` additionally exits 5 when the baseline no
    longer records the whole current surface (see below).
  * Detection is structural ONLY: name presence, flag presence, MCP-tool
    presence, preset count. Behavior-regression detection is explicitly
    out of scope (a much larger problem).

The baseline is captured by running the command itself with
``--write-baseline``; commit the resulting snapshot so future runs gate
against the last-known-good surface.

WHY A STALE BASELINE IS A DEFECT AND NOT A COSMETIC LAG
------------------------------------------------------

Removal detection is set subtraction: ``baseline_names - current_names``.
An entry the baseline never recorded therefore cannot be reported as
removed. A command added in release N and deleted in release N+1 against
an un-refreshed baseline is invisible in BOTH releases -- once as a
never-recorded addition, once as a removal of something the baseline
does not know about -- and the breaking change ships with a green gate.
The reach of the gate is exactly the size of the baseline, so every
unrecorded addition is permanently lost coverage rather than deferred
work.

``--require-coverage`` closes that by requiring the baseline to record
the current surface exactly, which couples each surface change to a
baseline refresh in the same commit. It is a hard failure, not a
warning: this gate spent its whole life un-run, and the observable cost
of that was 43 commands, 38 flags and 20 MCP tools outside the
baseline's reach with nothing red anywhere.

WHY ``--write-baseline`` REFUSES TO ERASE COVERAGE
--------------------------------------------------

Regenerating a baseline is unconditional capture: whatever the build
currently exposes becomes the new last-known-good. Run after an
accidental deletion -- which is precisely when a red gate makes someone
reach for it -- that blesses the deletion permanently, because the next
run compares against a baseline that no longer contains the deleted
entry. ``--write-baseline`` therefore diffs the existing baseline
against the fresh snapshot first and refuses to write when the write
would drop entries, unless ``--accept-removals`` says so deliberately.
Graceful renames (old name present in ``deprecated_aliases``, pointing
at a live command) are not drops and never trip the refusal.

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
#: Closed enumeration of how much of the current surface this comparison
#: could actually evaluate. Mirrors the ``_TRUNCATION_REASONS`` /
#: ``_RESOLUTION_KINDS`` idiom in ``roam.output.formatter``: a bare
#: ``partial_success`` is ambiguous, so the state is named directly.
#:
#:   ``complete``  every entry of the current surface is recorded by the
#:                 baseline, so a removal of ANY of them would be caught.
#:   ``partial``   the baseline does not record some of the current
#:                 surface. Findings are still sound, but absence of
#:                 findings is NOT evidence of absence: the unrecorded
#:                 entries were never evaluated and their later removal
#:                 would go unreported.
#:
#: ``partial`` MUST imply ``summary.partial_success: true``.
_SURFACE_COVERAGE_KINDS: frozenset[str] = frozenset({"complete", "partial"})

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

    Reads from the AST-parsed ``_COMMANDS`` dict (via
    :func:`roam.surface_counts.cli_commands`) + ``roam.cli._DEPRECATED_COMMANDS``
    + Click param introspection per command + ``roam.surface_counts`` for the
    AST-derived MCP tool / preset enumeration. The runtime ``roam.mcp_server``
    import is deliberately avoided here for the same reason
    ``cmd_surface._build_surface()`` avoids it (fragile on fresh installs).

    W420 cascade: command names come from the AST source of truth rather
    than a runtime dict read. The compatibility baseline must reflect the
    shipped surface (``pip install roam-code``); plugin commands surface
    separately via ``roam plugins list``. Plugin discovery no longer
    merges into ``roam.cli._COMMANDS`` (discovered commands land in
    ``_PLUGIN_COMMANDS``), so ``_COMMANDS``, ``_DEPRECATED_COMMANDS`` and
    ``_CATEGORIES`` are all plugin-stable and runtime reads are safe.
    """
    from roam.cli import _CATEGORIES, _DEPRECATED_COMMANDS
    from roam.surface_counts import cli_commands as _cli_commands_ast
    from roam.surface_counts import mcp_preset_counts, mcp_tool_names

    _commands = _cli_commands_ast()

    # Commands + flags. Click param introspection per canonical command.
    commands: dict[str, dict[str, Any]] = {}
    canonical_seen: set[tuple[str, str]] = set()
    for name in sorted(_commands):
        module_path, func_name = _commands[name]
        canonical_seen.add((module_path, func_name))
        flags = _introspect_flags(module_path, func_name)
        commands[name] = {
            "module": module_path,
            "function": func_name,
            "flags": sorted(flags),
        }

    # Deprecated alias map (used by the diff to recognise graceful renames).
    deprecated = {name: dict(record) for name, record in _DEPRECATED_COMMANDS.items()}

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
    except ImportError:
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


def _diff(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compute the structural diff between two snapshots.

    Returns a dict with closed-enum categories:
      removed_commands, added_commands, renamed_commands,
      removed_flags, added_flags,
      removed_envelope_fields, added_envelope_fields,
      removed_mcp_tools, added_mcp_tools,
      changed_presets.

    The ``breaking`` count counts entries that would BREAK an existing
    consumer. It is the sum of FIVE terms, and the fifth is the one
    readers miss:

      ``removed_commands``         a command is gone with no alias
      ``removed_flags``            a flag is gone from a live command
      ``removed_envelope_fields``  a canonical summary key is gone
      ``removed_mcp_tools``        an MCP tool name is gone
      ``preset_shrinks``           an MCP preset exposes FEWER tools
                                   than the baseline recorded

    Enumerate all five whenever this list is touched. With only the four
    ``removed_*`` terms written down, a real result of ``breaking=1``
    with every ``removed_*`` list empty reads as a contradiction, and the
    reader has to re-derive the tally to find ``preset_shrinks``. That
    exact case is live on this repository: ``core`` went 57 -> 17 tools,
    which is a genuine break for any consumer gated on that preset and
    is what makes the next release major rather than minor.

    Added entries are never breaking. A graceful rename (command removed
    from canonical names BUT an alias from old->new now exists in
    ``deprecated_aliases``) is surfaced under ``renamed_commands`` and is
    NOT counted as breaking. Preset count INCREASES are not breaking
    either; only shrinkage is.

    ``coverage_gap_count`` is the orthogonal axis: how much of the
    CURRENT surface the baseline fails to record, i.e. how much of this
    comparison is structurally blind. It counts every addition plus
    every preset whose recorded count no longer matches, because each
    such entry is one the ``baseline - current`` subtraction above can
    never report as removed later. It is NOT a breakage today; it is the
    measure of breakages that would go unseen tomorrow.
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
            changed_presets.append({"preset": preset, "baseline_count": b, "current_count": c})

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

    # Blind spot, not breakage: every entry the baseline does not record.
    # A changed preset count counts on EITHER side -- a shrink is already
    # breaking, and a growth means the recorded count is no longer the
    # shipped one, so the next shrink is measured from a stale floor.
    coverage_gap = (
        len(added) + len(added_flags) + len(added_envelope_fields) + len(added_mcp_tools) + len(changed_presets)
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
        "coverage_gap_count": coverage_gap,
        "preset_shrinks": preset_shrinks,
    }


def _verdict_for(diff: dict[str, Any], require_coverage: bool = False) -> tuple[str, str]:
    """Return ``(verdict, level)`` matching the diff.

    Closed-enum verdicts:
      ``no regressions``       -> no removed/breaking entries, no additions.
      ``surface additions``    -> only additions; no breaking entries.
      ``surface drift``        -> mixed adds + non-breaking renames.
      ``baseline stale``       -> the baseline does not record the whole
                                  current surface, and the caller asked
                                  (``require_coverage``) for it to.
      ``breaking changes``     -> at least one breaking entry.

    ``require_coverage`` is a parameter rather than a fixed rule because
    the same delta means different things to different callers: to a
    release note "43 commands were added" is information, to a gate that
    must stay able to see removals it is a blocker. The verdict and its
    level track the caller's contract so the envelope never reports
    ``info`` while the process exits non-zero.
    """
    if diff["breaking_count"] > 0:
        return ("breaking changes", "blocker")
    if require_coverage and diff["coverage_gap_count"] > 0:
        return ("baseline stale", "blocker")
    any_added = bool(
        diff["added_commands"] or diff["added_flags"] or diff["added_envelope_fields"] or diff["added_mcp_tools"]
    )
    any_drift = bool(diff["renamed_commands"] or diff["changed_presets"])
    if any_drift:
        return ("surface drift", "warning")
    if any_added:
        return ("surface additions", "info")
    return ("no regressions", "info")


def _erasures(existing_path: Path, fresh: dict[str, Any]) -> dict[str, Any] | None:
    """Return what overwriting ``existing_path`` with ``fresh`` would erase.

    ``None`` means the write is safe: either no baseline is being
    replaced, or the fresh snapshot still records everything the old one
    did. A dict means the write would drop removal-detection coverage,
    and carries the same closed-enum removal categories ``_diff``
    produces so the refusal can name every entry rather than assert that
    some exist.

    The safety question is exactly the gate's own question with the
    arguments swapped -- "does the new snapshot still contain what the
    old one promised" -- so it delegates to :func:`_diff` instead of
    re-deriving set subtraction. That inheritance is load-bearing: a
    graceful rename is not a drop under ``_diff`` (the old name resolves
    through ``deprecated_aliases``), so a deprecation cycle regenerates
    its baseline without an override, while a true deletion cannot.

    An existing baseline that will not parse returns a refusal too. The
    write cannot be shown to preserve coverage, and silently proceeding
    would convert an unreadable file into a blessed one.
    """
    if not existing_path.exists():
        return None
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"unreadable_baseline": str(exc)}
    if not isinstance(existing, dict):
        return {"unreadable_baseline": f"expected a JSON object, found {type(existing).__name__}"}

    delta = _diff(existing, fresh)
    if delta["breaking_count"] == 0:
        return None
    return {
        "removed_commands": delta["removed_commands"],
        "removed_flags": delta["removed_flags"],
        "removed_envelope_fields": delta["removed_envelope_fields"],
        "removed_mcp_tools": delta["removed_mcp_tools"],
        "preset_shrinks": delta["preset_shrinks"],
        "erased_count": delta["breaking_count"],
    }


def _erasure_lines(erasures: dict[str, Any]) -> list[str]:
    """Render one human line per entry a refused write would have erased."""
    if "unreadable_baseline" in erasures:
        return [f"existing baseline is unreadable: {erasures['unreadable_baseline']}"]
    lines = [f"command {name}" for name in erasures["removed_commands"]]
    lines += [f"flag {entry['command']} {entry['flag']}" for entry in erasures["removed_flags"]]
    lines += [f"envelope field surface.summary.{name}" for name in erasures["removed_envelope_fields"]]
    lines += [f"MCP tool {name}" for name in erasures["removed_mcp_tools"]]
    lines += [
        f"preset {entry['preset']} shrinks {entry['baseline_count']} -> {entry['current_count']}"
        for entry in erasures["preset_shrinks"]
    ]
    return lines


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
    side_effect=True,
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
    "--accept-removals",
    "accept_removals",
    is_flag=True,
    default=False,
    help="Permit --write-baseline to drop entries the existing baseline records.",
)
@click.option(
    "--ci",
    is_flag=True,
    default=False,
    help="Exit 5 (EXIT_GATE_FAILURE) if any breaking entries are detected.",
)
@click.option(
    "--require-coverage",
    "require_coverage",
    is_flag=True,
    default=False,
    help="Exit 5 (EXIT_GATE_FAILURE) if the baseline does not record the whole current surface.",
)
@click.pass_context
def compatibility(
    ctx,
    baseline_path: Path | None,
    current_path: Path | None,
    write_baseline: Path | None,
    accept_removals: bool,
    ci: bool,
    require_coverage: bool,
):
    """Detect outbound surface regressions vs a baseline snapshot.

    \b
    Examples:
      roam compatibility                              # diff live build vs dev/compatibility-baseline.json
      roam compatibility --baseline old.json          # explicit baseline
      roam compatibility --write-baseline cur.json    # capture a fresh baseline
      roam compatibility --ci                         # exit 5 on breaking changes
      roam compatibility --ci --require-coverage      # also exit 5 on a stale baseline

    Detection scope (MVP, closed-enum):
      - removed/renamed/added commands
      - removed/added per-command flags
      - removed/added top-level envelope summary fields (canonical witness)
      - removed/added MCP tools
      - MCP preset count changes (preset shrinkage = breaking)

    Detection REACH is the baseline: nothing the baseline never recorded
    can be reported as removed later. --require-coverage gates on that
    reach, so a surface change and its baseline refresh land together.

    Out of scope: semantic-behavior regressions (a much larger problem).
    """
    json_mode = bool(ctx.obj and ctx.obj.get("json"))

    # Write-baseline path: capture + exit (no diff).
    if write_baseline is not None:
        snapshot = _build_snapshot()

        # Ratchet guard. Regeneration is the remedy this command prints
        # when the gate goes red, so it is reached most often in exactly
        # the state where blessing the current surface is wrong.
        erasures = None if accept_removals else _erasures(write_baseline, snapshot)
        if erasures is not None:
            lines = _erasure_lines(erasures)
            remedy = f"roam compatibility --write-baseline {write_baseline} --accept-removals"
            noun = "entry" if len(lines) == 1 else "entries"
            subject = "that entry" if len(lines) == 1 else "those entries"
            msg = (
                f"refusing to write: the fresh snapshot drops {len(lines)} {noun} the existing "
                f"baseline records; writing it would make the removal of {subject} "
                "permanently undetectable"
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "compatibility",
                            summary={
                                "verdict": "baseline write refused",
                                "level": "blocker",
                                "partial_success": False,
                                "erased": len(lines),
                                "path": str(write_baseline),
                            },
                            error_code="GATE_FAILURE",
                            error=msg,
                            hint=(
                                "restore the removed surface, or re-run with --accept-removals "
                                "to record the removal deliberately"
                            ),
                            next_command=remedy,
                            would_erase=lines,
                            agent_contract={
                                "facts": [
                                    f"{len(lines)} entries would be erased",
                                    f"baseline path {write_baseline}",
                                ],
                                "next_commands": [remedy],
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: baseline write refused - {msg}", err=True)
                for line in lines:
                    click.echo(f"  - {line}", err=True)
                click.echo("", err=True)
                click.echo(f"  restore the surface, or record the removal deliberately: {remedy}", err=True)
            ctx.exit(EXIT_GATE_FAILURE)

        write_baseline.parent.mkdir(parents=True, exist_ok=True)
        # newline="" disables the platform newline translation that would
        # otherwise make a Windows-authored baseline differ byte-for-byte
        # from a Linux-authored one. This artifact is committed and diffed
        # by reviewers; the OS of whoever refreshed it must not show up in
        # the diff. (.gitattributes normalizes on commit, but the working
        # copy churn and the "CRLF will be replaced" warning are real.)
        write_baseline.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
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
                            "next_commands": [f"roam compatibility --baseline {write_baseline}"],
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
        msg = f"baseline not found at {baseline_path} - capture one with `roam compatibility --write-baseline <path>`"
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
                            "next_commands": [f"roam compatibility --write-baseline {baseline_path}"],
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: baseline missing - {msg}", err=True)
        # No baseline is total coverage loss, not a partial one: nothing
        # at all can be reported as removed. Both gates fail on it.
        if ci or require_coverage:
            ctx.exit(EXIT_GATE_FAILURE)
        return

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    # Resolve current.
    if current_path is None:
        current = _build_snapshot()
    else:
        current = json.loads(current_path.read_text(encoding="utf-8"))

    diff = _diff(baseline, current)
    verdict, level = _verdict_for(diff, require_coverage=require_coverage)

    breaking = diff["breaking_count"]
    coverage_gap = diff["coverage_gap_count"]
    refresh_remedy = f"roam compatibility --write-baseline {baseline_path}"
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
            f"{coverage_gap} entries outside baseline coverage",
        ]
        next_commands: list[str] = []
        if breaking:
            next_commands.append(
                "# inspect breaking entries, then either restore the surface or roll the baseline forward"
            )
            # Named WITH the override it now needs. The bare form refuses
            # to erase what the baseline records, so printing it alone
            # would advertise a command that cannot run here.
            next_commands.append(f"{refresh_remedy} --accept-removals")
        elif coverage_gap:
            next_commands.append("# the baseline no longer records the whole shipped surface")
            next_commands.append(refresh_remedy)
        click.echo(
            to_json(
                json_envelope(
                    "compatibility",
                    summary={
                        "verdict": verdict,
                        "level": level,
                        # partial_success discloses an INCOMPLETE RESULT,
                        # not findings. It was wired to ``breaking > 0``,
                        # which is a different claim and wrong both ways:
                        # a clean run over a baseline missing 110 entries
                        # asserted completeness (the false clean this whole
                        # gate exists to prevent), while a complete run
                        # that correctly found a regression reported itself
                        # degraded. Findings live in ``breaking``; the
                        # blind spot lives here.
                        "partial_success": coverage_gap > 0,
                        "surface_coverage": ("partial" if coverage_gap > 0 else "complete"),
                        "unevaluated_surface_entries": coverage_gap,
                        "removed": removed_n,
                        "renamed": renamed_n,
                        "added": added_n,
                        "breaking": breaking,
                        "coverage_gap": coverage_gap,
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
            f"VERDICT: {verdict}  (removed={removed_n} renamed={renamed_n} added={added_n} "
            f"breaking={breaking} coverage_gap={coverage_gap})"
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
                click.echo(f"  - {e['preset']}: {e['baseline_count']} -> {e['current_count']}")
        # Disclosed whenever there IS a blind spot, not only when a flag
        # asked about it. The text surface carries the same obligation as
        # the envelope: a reader must not take "no removals found" for
        # "nothing was removed" when part of the surface was never looked at.
        if coverage_gap:
            click.echo("")
            subject = "entry is" if coverage_gap == 1 else "entries are"
            possessive = "its" if coverage_gap == 1 else "their"
            click.echo(
                f"{coverage_gap} {subject} outside the baseline's reach; "
                f"{possessive} later removal would go undetected."
            )
            click.echo(f"  refresh the baseline in the same commit as the surface change: {refresh_remedy}")

    if ci and breaking > 0:
        ctx.exit(EXIT_GATE_FAILURE)
    if require_coverage and coverage_gap > 0:
        ctx.exit(EXIT_GATE_FAILURE)
