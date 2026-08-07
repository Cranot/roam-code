"""roam compatibility - detect outbound surface regressions vs a baseline.

Catches the same bug class AGENTS.md Constraint 8 protects against
("Use semantically meaningful
operation names - closed enumeration") but for OUTBOUND surface contracts
that users / agents / CI depend on:

  * CLI:     a command renamed or removed; a flag removed.
  * JSON:    a top-level envelope field removed; a closed-enum value removed.
  * MCP:     a tool renamed; a tool parameter removed; a preset changed
             in size OR in membership.

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
    presence, MCP-tool-parameter presence, preset count and preset
    membership.
    Behavior-regression detection is explicitly out of scope (a much
    larger problem).

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

WHY COVERAGE IS CLAIMED PER DIMENSION AND NEVER OVER "THE SURFACE"
------------------------------------------------------------------

``surface_coverage: complete`` used to be printed next to
``unevaluated_surface_entries: 0`` over a baseline that recorded MCP
tools as a flat list of names. A tool's PARAMETERS were therefore not in
the snapshot at all, so ``coverage_gap`` — which sums additions across
the dimensions the snapshot collects — could not count them: a dimension
the snapshot never collects contributes 0 by construction, which makes
the completeness claim unfalsifiable rather than merely unmeasured.

Commit 67a09fd1 is the proof it mattered. It removed three CLI flags AND
their two MCP wrapper mirrors (``staged`` from ``roam_budget_check``,
``model_tier`` from ``roam_compile``). The CLI half took CI red and
forced a deliberate roll-forward; the MCP half was invisible in the same
commit, in the same gate, in the same run, while the envelope printed
``0 removed MCP tools`` and ``surface_coverage: complete``. Snapshots
built from the two revisions were byte-identical, so the artifact could
not distinguish them.

Both halves of that are now fixed, and in the only two honest ways:

  1. The measurement was WIDENED. ``mcp_tools`` records
     ``{tool: [parameter, ...]}`` (schema 1.1.0), and a removed
     parameter is a breaking entry, so the completeness claim over that
     dimension is now true rather than vacuous.
  2. The remaining claim was NARROWED to what it measures. Coverage is
     asserted over :data:`_COVERED_DIMENSIONS` and the envelope names
     :data:`_UNCOVERED_DIMENSIONS` alongside it, because parameter
     types, defaults, tool descriptions and CLI categories are still not
     recorded and their removal still would not be reported.

A baseline written before 1.1.0 does not record the parameter dimension.
It is treated as UNRECORDED — a coverage gap that ``--require-coverage``
fails on — never as "this tool has no parameters", which would read an
absent measurement as a benign definite value and reproduce the exact
defect one schema version later.

PRESET MEMBERSHIP: THE SAME DEFECT ONE DIMENSION OVER (schema 1.2.0)
--------------------------------------------------------------------

``mcp_preset_counts`` was covered and preset MEMBERSHIP was in neither
list, which is the harder failure to notice: the snapshot recorded a
number, the number was diffed, and the claim over "presets" read as
complete. A count is a lossy projection of a set. Swapping one real
member of ``_CORE_TOOLS`` for another real tool — ``roam_taint`` for
``roam_complexity_report``, 17 tools before and 17 after — produced a
BYTE-IDENTICAL snapshot, ``no regressions  removed=0 breaking=0
coverage_gap=0``, ``surface_coverage: complete``,
``unevaluated_surface_entries: 0`` and exit 0, while every MCP client on
the core preset lost a tool. That is exactly the 67a09fd1 shape, one
schema version later.

The measurement was WIDENED rather than the claim narrowed, because the
gate already asserts preset coverage and already treats preset shrinkage
as breaking: a swap breaks the same consumer as a shrink, with the same
severity, so declaring membership "not evaluated" would have kept a
covered dimension that could not see its own headline failure. Snapshots
record ``mcp_preset_members`` (schema 1.2.0) and a removed member is a
breaking entry naming the tool and the preset.

The count term is now the FALLBACK for presets whose membership one side
did not record. When both sides record it, the individual removed members
carry the break and the count delta is derivable from them, so counting
both would tally one real break twice.

``commands[].module`` / ``commands[].function`` went the other way. They
are recorded and never diffed — the second inclusion rule for
:data:`_UNCOVERED_DIMENSIONS` — but they are not outbound surface: a
consumer invokes ``roam health``, not
``roam.commands.cmd_health:health``, so a module move breaks nobody and
gating it would turn every internal refactor red. The honest repair there
is disclosure, so ``cli_command_implementation_targets`` is named in
:data:`_UNCOVERED_DIMENSIONS` and asserted unread by
``tests/test_w1491_compatibility_gate_wired.py``.

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
SNAPSHOT_SCHEMA_VERSION = "1.2.0"

#: The surface dimensions this comparison actually reads out of a
#: snapshot and diffs. ``surface_coverage`` is a claim about THESE and
#: nothing else; the envelope publishes the list so a reader never has to
#: infer the scope of a "complete" verdict from the word alone.
_COVERED_DIMENSIONS: tuple[str, ...] = (
    "cli_commands",
    "cli_flags",
    "envelope_summary_keys",
    "mcp_tools",
    "mcp_tool_parameters",
    "mcp_preset_counts",
    "mcp_preset_membership",
)

#: Surface a consumer can depend on that this gate does NOT evaluate.
#: Published next to the coverage verdict for the same reason
#: ``partial_success`` is published next to the findings: absence of a
#: finding in an unread dimension is not evidence of absence. Entries
#: here are either not recorded by ``_build_snapshot`` at all, or
#: recorded and never diffed (``cli_categories``,
#: ``cli_command_implementation_targets``), and each one is asserted
#: unread by ``tests/test_w1491_compatibility_gate_wired.py`` rather than
#: merely asserted in prose.
#:
#: A dimension belongs here only when its removal genuinely breaks no
#: consumer. ``cli_command_implementation_targets`` (``commands[].module``
#: / ``commands[].function``) qualifies: callers invoke ``roam health``,
#: not the module path behind it. Preset MEMBERSHIP did NOT qualify and
#: was moved into :data:`_COVERED_DIMENSIONS` instead — see the module
#: docstring.
_UNCOVERED_DIMENSIONS: tuple[str, ...] = (
    "cli_categories",
    "cli_command_implementation_targets",
    "cli_flag_types_and_defaults",
    "mcp_tool_parameter_types_and_defaults",
    "mcp_tool_descriptions",
    "runtime_behavior",
)

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
#:   ``complete``  every entry of the current surface IN THE COVERED
#:                 DIMENSIONS is recorded by the baseline, so a removal
#:                 of any of them would be caught.
#:   ``partial``   the baseline does not record some of the current
#:                 surface. Findings are still sound, but absence of
#:                 findings is NOT evidence of absence: the unrecorded
#:                 entries were never evaluated and their later removal
#:                 would go unreported.
#:
#: ``partial`` MUST imply ``summary.partial_success: true``.
#:
#: Neither value is a claim about the whole outbound surface. The scope
#: is exactly :data:`_COVERED_DIMENSIONS`, which the envelope publishes
#: beside this field together with :data:`_UNCOVERED_DIMENSIONS`, so
#: ``complete`` cannot be read as "nothing at all could have regressed".
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
    from roam.surface_counts import mcp_preset_counts, mcp_preset_members, mcp_tool_params

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

    # ``{tool: [parameter, ...]}``, not a flat name list. Read from the
    # traversal that already reads the names, so the two dimensions cannot
    # disagree about which tools exist; ``mcp_tool_params`` keeps
    # ``mcp_tool_names``' fail-loud duplicate check.
    mcp_tools = {name: sorted(params) for name, params in sorted(mcp_tool_params().items())}
    # Counts AND members, both projections of the same ``_PRESETS``
    # resolution in ``surface_counts``, so the two cannot disagree about
    # what a preset holds. The count alone cannot see a 1-for-1 swap.
    mcp_presets = dict(mcp_preset_counts())
    mcp_preset_member_map = {name: sorted(members) for name, members in sorted(mcp_preset_members().items())}

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "commands": commands,
        "deprecated_aliases": deprecated,
        "mcp_tools": mcp_tools,
        "mcp_preset_counts": mcp_presets,
        "mcp_preset_members": mcp_preset_member_map,
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


def _mcp_tools_of(snapshot: dict[str, Any]) -> tuple[set[str], dict[str, set[str]]]:
    """Split a snapshot's ``mcp_tools`` into ``(names, recorded parameters)``.

    Accepts both on-disk shapes:

      schema <= 1.0.0   ``["roam_a", "roam_b"]`` -- names only.
      schema >= 1.1.0   ``{"roam_a": ["root"], ...}`` -- names + params.

    The returned parameter map contains an entry ONLY for tools whose
    parameters the snapshot actually recorded. A tool absent from the map
    is UNRECORDED, which is a different fact from "recorded as having no
    parameters", and callers must keep them apart: collapsing the two
    would let a 1.0.0 baseline read as "no parameters were removed" and
    republish an absent measurement as a benign definite value -- the
    exact defect widening the snapshot exists to close.
    """
    raw = snapshot.get("mcp_tools") or {}
    if isinstance(raw, dict):
        names = {name for name in raw if isinstance(name, str)}
        params = {
            name: {p for p in value if isinstance(p, str)}
            for name, value in raw.items()
            if isinstance(name, str) and isinstance(value, (list, tuple))
        }
        return names, params
    return {name for name in raw if isinstance(name, str)}, {}


def _preset_members_of(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    """Return ``{preset: {tool, ...}}`` for the presets a snapshot RECORDED.

    A preset absent from the returned map is UNRECORDED, which is a
    different fact from "recorded as empty" -- exactly the distinction
    :func:`_mcp_tools_of` keeps for parameters. Collapsing the two would
    let a pre-1.2.0 baseline (schemas 1.0.0 / 1.1.0, which have no
    ``mcp_preset_members`` key at all) read as "every preset was empty and
    every current member is an addition", turning an absent measurement
    into a definite one and republishing the false clean this dimension
    was widened to close.
    """
    raw = snapshot.get("mcp_preset_members")
    if not isinstance(raw, dict):
        return {}
    return {
        name: {tool for tool in value if isinstance(tool, str)}
        for name, value in raw.items()
        if isinstance(name, str) and isinstance(value, (list, tuple, set))
    }


def _diff(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compute the structural diff between two snapshots.

    Returns a dict with closed-enum categories:
      removed_commands, added_commands, renamed_commands,
      removed_flags, added_flags,
      removed_envelope_fields, added_envelope_fields,
      removed_mcp_tools, added_mcp_tools,
      removed_mcp_tool_params, added_mcp_tool_params,
      unrecorded_mcp_tool_params,
      removed_preset_members, added_preset_members,
      unrecorded_preset_members,
      changed_presets.

    The ``breaking`` count counts entries that would BREAK an existing
    consumer. It is the sum of SEVEN terms, and the last two are the ones
    readers miss:

      ``removed_commands``          a command is gone with no alias
      ``removed_flags``             a flag is gone from a live command
      ``removed_envelope_fields``   a canonical summary key is gone
      ``removed_mcp_tools``         an MCP tool name is gone
      ``removed_mcp_tool_params``   a live MCP tool no longer accepts a
                                    parameter the baseline recorded
      ``removed_preset_members``    an MCP preset no longer contains a
                                    tool the baseline recorded in it
      ``preset_shrinks``            an MCP preset exposes FEWER tools
                                    than the baseline recorded, and this
                                    comparison could not read its members

    Enumerate all seven whenever this list is touched. With only the
    ``removed_*`` command/flag/field/tool terms written down, a real
    result of ``breaking=1`` with those lists empty reads as a
    contradiction, and the reader has to re-derive the tally to find
    ``preset_shrinks``. That exact case is live on this repository:
    ``core`` went 57 -> 17 tools, which is a genuine break for any
    consumer gated on that preset and is what makes the next release
    major rather than minor.

    ``removed_preset_members`` and ``preset_shrinks`` are mutually
    exclusive by construction and that is deliberate. A count is a lossy
    projection of a membership: when BOTH snapshots record members, every
    real removal is named individually and the count delta is derivable
    from those names, so adding the count term as well would tally one
    break twice. When either side did not record members -- a pre-1.2.0
    baseline -- the count is the only evidence there is, and it carries
    the break alone. ``removed_preset_members`` is the term the
    ``roam_taint`` -> ``roam_complexity_report`` swap needed and did not
    have: ``core`` stayed at 17, the snapshot was byte-identical, and the
    gate published ``no regressions`` while every MCP client on that
    preset lost a tool.

    ``unrecorded_preset_members`` mirrors ``unrecorded_mcp_tool_params``:
    a preset present in both snapshots whose membership at least one side
    never recorded. Counted into ``coverage_gap`` so the run reports
    itself blind, never read as "this preset is empty".

    ``removed_mcp_tool_params`` is the term commit 67a09fd1 needed and
    did not have: it removed ``staged`` from ``roam_budget_check`` and
    ``model_tier`` from ``roam_compile`` alongside three CLI flags, the
    flags went red, and the two MCP parameters passed the same gate in
    the same run because the baseline recorded MCP tools as bare names.

    ``unrecorded_mcp_tool_params`` is neither a break nor an addition: it
    is a tool present in both snapshots whose parameters at least one
    side never recorded (a pre-1.1.0 baseline, or a ``--current`` file
    captured by an older build). Those tools are counted into
    ``coverage_gap`` so the run reports itself blind, and are NEVER read
    as "this tool has no parameters" -- that inference is what would turn
    a schema migration back into a silent false clean.

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
    base_mcp, base_mcp_params = _mcp_tools_of(baseline)
    cur_mcp, cur_mcp_params = _mcp_tools_of(current)
    removed_mcp_tools = sorted(base_mcp - cur_mcp)
    added_mcp_tools = sorted(cur_mcp - base_mcp)

    # Per-tool parameter diff, mirroring the per-command flag diff above.
    # Only tools present in BOTH snapshots -- a removed tool's parameters
    # are already counted under ``removed_mcp_tools``. A tool whose
    # parameters either side did not record is reported as unrecorded
    # rather than compared against an assumed-empty set.
    removed_mcp_tool_params: list[dict[str, str]] = []
    added_mcp_tool_params: list[dict[str, str]] = []
    unrecorded_mcp_tool_params: list[str] = []
    for tool in sorted(base_mcp & cur_mcp):
        if tool not in base_mcp_params or tool not in cur_mcp_params:
            unrecorded_mcp_tool_params.append(tool)
            continue
        base_params = base_mcp_params[tool]
        cur_params = cur_mcp_params[tool]
        for p in sorted(base_params - cur_params):
            removed_mcp_tool_params.append({"tool": tool, "parameter": p})
        for p in sorted(cur_params - base_params):
            added_mcp_tool_params.append({"tool": tool, "parameter": p})

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

    # Per-preset MEMBERSHIP diff, mirroring the per-tool parameter diff.
    # Only presets both snapshots know about; a preset whose members
    # either side did not record is reported as unrecorded rather than
    # compared against an assumed-empty set.
    #
    # Scoped to tools that EXIST in both snapshots, the same way the flag
    # diff is scoped to commands in both and the parameter diff to tools in
    # both. Deleting a tool outright drops it from every preset that held
    # it, so without this scoping one deletion would tally as one
    # ``removed_mcp_tools`` plus up to six ``removed_preset_members`` --
    # counting one break seven times, which is the arithmetic version of
    # the over-claim this gate exists to prevent. A tool moved OUT of a
    # preset while still registered is the case this dimension is for, and
    # it survives the scoping intact.
    base_preset_members = _preset_members_of(baseline)
    cur_preset_members = _preset_members_of(current)
    surviving_tools = base_mcp & cur_mcp
    removed_preset_members: list[dict[str, str]] = []
    added_preset_members: list[dict[str, str]] = []
    unrecorded_preset_members: list[str] = []
    comparable_presets: set[str] = set()
    for preset in sorted(set(base_presets) & set(cur_presets)):
        if preset not in base_preset_members or preset not in cur_preset_members:
            unrecorded_preset_members.append(preset)
            continue
        comparable_presets.add(preset)
        base_members = base_preset_members[preset] & surviving_tools
        cur_members = cur_preset_members[preset] & surviving_tools
        for tool in sorted(base_members - cur_members):
            removed_preset_members.append({"preset": preset, "tool": tool})
        for tool in sorted(cur_members - base_members):
            added_preset_members.append({"preset": preset, "tool": tool})

    # Tally breaking entries. Added items + renames are NOT breaking.
    # Preset count drops ARE breaking (fewer tools in a preset breaks
    # consumers gated on that preset); preset count INCREASES are not.
    #
    # Restricted to presets whose membership this comparison could NOT
    # read. Where it could, ``removed_preset_members`` already names every
    # missing tool and the count delta is a projection of those names;
    # counting both would report one break twice.
    preset_shrinks = [
        e
        for e in changed_presets
        if e["preset"] not in comparable_presets
        and (e["baseline_count"] is not None)
        and (e["current_count"] is not None)
        and (e["current_count"] < e["baseline_count"])
    ]
    count_only_changed_presets = [e for e in changed_presets if e["preset"] not in comparable_presets]

    breaking = (
        len(removed_commands)
        + len(removed_flags)
        + len(removed_envelope_fields)
        + len(removed_mcp_tools)
        + len(removed_mcp_tool_params)
        + len(removed_preset_members)
        + len(preset_shrinks)
    )

    # Blind spot, not breakage: every entry the baseline does not record.
    # A changed preset count counts on EITHER side -- a shrink is already
    # breaking, and a growth means the recorded count is no longer the
    # shipped one, so the next shrink is measured from a stale floor.
    # ``unrecorded_mcp_tool_params`` belongs here for the same reason an
    # addition does: the parameters of those tools cannot be reported as
    # removed later, so they are lost reach, not a finding.
    coverage_gap = (
        len(added)
        + len(added_flags)
        + len(added_envelope_fields)
        + len(added_mcp_tools)
        + len(added_mcp_tool_params)
        + len(unrecorded_mcp_tool_params)
        + len(added_preset_members)
        + len(unrecorded_preset_members)
        # Same exclusivity rule as ``preset_shrinks``: a comparable
        # preset's count delta is already accounted for member by member.
        + len(count_only_changed_presets)
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
        "removed_mcp_tool_params": removed_mcp_tool_params,
        "added_mcp_tool_params": added_mcp_tool_params,
        "unrecorded_mcp_tool_params": unrecorded_mcp_tool_params,
        "removed_preset_members": removed_preset_members,
        "added_preset_members": added_preset_members,
        "unrecorded_preset_members": unrecorded_preset_members,
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
        diff["added_commands"]
        or diff["added_flags"]
        or diff["added_envelope_fields"]
        or diff["added_mcp_tools"]
        or diff["added_mcp_tool_params"]
        or diff["added_preset_members"]
    )
    # A dimension one side never recorded is drift between the baseline
    # and the build, not a clean comparison. Without this the verdict
    # line could read the flat "no regressions" while ``coverage_gap``
    # counted hundreds of unevaluated tools -- the same false clean this
    # command's ``partial_success`` wiring was fixed for.
    any_drift = bool(
        diff["renamed_commands"]
        or diff["changed_presets"]
        or diff["unrecorded_mcp_tool_params"]
        or diff["unrecorded_preset_members"]
    )
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
        "removed_mcp_tool_params": delta["removed_mcp_tool_params"],
        "removed_preset_members": delta["removed_preset_members"],
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
        f"MCP tool parameter {entry['tool']}({entry['parameter']})" for entry in erasures["removed_mcp_tool_params"]
    ]
    lines += [f"MCP preset member {entry['preset']}/{entry['tool']}" for entry in erasures["removed_preset_members"]]
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
      - removed/added per-tool MCP parameters
      - removed/added per-preset MCP tools (a removed member = breaking,
        including a 1-for-1 swap that leaves the count unchanged)
      - MCP preset count changes on a baseline that records no membership
        (preset shrinkage = breaking)

    Detection REACH is the baseline: nothing the baseline never recorded
    can be reported as removed later. --require-coverage gates on that
    reach, so a surface change and its baseline refresh land together.

    NOT in scope, and named on every run so a clean verdict cannot be
    read as "nothing regressed": command categories, flag/parameter types
    and defaults, MCP tool descriptions, and semantic-behavior
    regressions (a much larger problem).
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
        captured_params = sum(len(params) for params in snapshot["mcp_tools"].values())
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
                            "mcp_tool_parameters": captured_params,
                            "path": str(write_baseline),
                        },
                        covered_dimensions=list(_COVERED_DIMENSIONS),
                        uncovered_dimensions=list(_UNCOVERED_DIMENSIONS),
                        agent_contract={
                            "facts": [
                                f"{len(snapshot['commands'])} commands captured",
                                f"{len(snapshot['mcp_tools'])} MCP tools captured",
                                f"{captured_params} MCP tool parameters captured",
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
                f"{len(snapshot['mcp_tools'])} MCP tools, {captured_params} MCP tool parameters) "
                f"-> {write_baseline}"
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
        + len(diff["removed_mcp_tool_params"])
        + len(diff["removed_preset_members"])
    )
    renamed_n = len(diff["renamed_commands"])
    added_n = (
        len(diff["added_commands"])
        + len(diff["added_flags"])
        + len(diff["added_envelope_fields"])
        + len(diff["added_mcp_tools"])
        + len(diff["added_mcp_tool_params"])
        + len(diff["added_preset_members"])
    )

    if json_mode:
        # LAW-4 anchored facts. Terminals: commands, flags, fields, tools,
        # tool parameters, dimensions. The last two facts are the scope of
        # every count above them: "0 removed X" is only a claim about the
        # dimensions this comparison reads, and the unread ones are named
        # rather than left for the reader to infer from silence.
        facts = [
            f"{len(diff['removed_commands'])} removed commands",
            f"{len(diff['removed_flags'])} removed flags",
            f"{len(diff['removed_envelope_fields'])} removed envelope fields",
            f"{len(diff['removed_mcp_tools'])} removed MCP tools",
            f"{len(diff['removed_mcp_tool_params'])} removed MCP tool parameters",
            f"{len(diff['removed_preset_members'])} removed MCP preset tools",
            f"{len(diff['added_commands'])} added commands",
            f"{len(diff['added_mcp_tools'])} added MCP tools",
            f"{len(diff['unrecorded_mcp_tool_params'])} MCP tools whose parameters the baseline does not record",
            f"{len(diff['unrecorded_preset_members'])} unevaluated MCP presets",
            f"{breaking} breaking entries",
            f"{coverage_gap} entries outside baseline coverage",
            f"{len(_COVERED_DIMENSIONS)} surface dimensions evaluated: {', '.join(_COVERED_DIMENSIONS)}",
            f"{len(_UNCOVERED_DIMENSIONS)} surface dimensions NOT evaluated by this gate: "
            f"{', '.join(_UNCOVERED_DIMENSIONS)}",
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
                        # Scoped to ``covered_dimensions`` below, never to
                        # "the surface". ``complete`` here means the
                        # baseline records every entry of the dimensions
                        # this comparison reads -- it is not, and must not
                        # be read as, a claim that nothing else could have
                        # regressed. ``uncovered_dimensions`` names the
                        # rest in the same envelope so the scope travels
                        # with the verdict.
                        "surface_coverage": ("partial" if coverage_gap > 0 else "complete"),
                        "surface_coverage_scope": "covered_dimensions",
                        "unevaluated_surface_entries": coverage_gap,
                        "covered_dimension_count": len(_COVERED_DIMENSIONS),
                        "uncovered_dimension_count": len(_UNCOVERED_DIMENSIONS),
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
                    removed_mcp_tool_params=diff["removed_mcp_tool_params"],
                    added_mcp_tool_params=diff["added_mcp_tool_params"],
                    unrecorded_mcp_tool_params=diff["unrecorded_mcp_tool_params"],
                    removed_preset_members=diff["removed_preset_members"],
                    added_preset_members=diff["added_preset_members"],
                    unrecorded_preset_members=diff["unrecorded_preset_members"],
                    changed_presets=diff["changed_presets"],
                    covered_dimensions=list(_COVERED_DIMENSIONS),
                    uncovered_dimensions=list(_UNCOVERED_DIMENSIONS),
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
        if diff["removed_mcp_tool_params"]:
            click.echo("")
            click.echo("removed MCP tool parameters:")
            for e in diff["removed_mcp_tool_params"]:
                click.echo(f"  - {e['tool']}({e['parameter']})")
        if diff["removed_preset_members"]:
            click.echo("")
            click.echo("removed MCP preset members:")
            for e in diff["removed_preset_members"]:
                click.echo(f"  - {e['preset']} no longer contains {e['tool']}")
        if diff["unrecorded_preset_members"]:
            click.echo("")
            click.echo(
                f"{len(diff['unrecorded_preset_members'])} MCP presets have membership this comparison "
                f"could not evaluate (one side's snapshot predates schema {SNAPSHOT_SCHEMA_VERSION}):"
            )
            for p in diff["unrecorded_preset_members"]:
                click.echo(f"  - {p}")
        if diff["unrecorded_mcp_tool_params"]:
            click.echo("")
            unrecorded = diff["unrecorded_mcp_tool_params"]
            click.echo(
                f"{len(unrecorded)} MCP tools have parameters this comparison could not evaluate "
                f"(one side's snapshot predates schema {SNAPSHOT_SCHEMA_VERSION}):"
            )
            for t in unrecorded[:10]:
                click.echo(f"  - {t}")
            if len(unrecorded) > 10:
                click.echo(f"  ... and {len(unrecorded) - 10} more")
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
        # The scope of every count above. Printed on EVERY run, including a
        # clean one, because a clean run is exactly when "no regressions"
        # is most likely to be read as "nothing regressed" -- and this gate
        # does not read parameter types, defaults, tool descriptions,
        # command categories or any runtime behavior.
        click.echo("")
        click.echo(f"evaluated dimensions ({len(_COVERED_DIMENSIONS)}): {', '.join(_COVERED_DIMENSIONS)}")
        click.echo(f"NOT evaluated ({len(_UNCOVERED_DIMENSIONS)}): {', '.join(_UNCOVERED_DIMENSIONS)}")

    if ci and breaking > 0:
        ctx.exit(EXIT_GATE_FAILURE)
    if require_coverage and coverage_gap > 0:
        ctx.exit(EXIT_GATE_FAILURE)
