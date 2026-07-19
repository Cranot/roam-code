"""Read / write / verify the repo-local constitution.

A *constitution* is a single YAML file at ``.roam/constitution.yml``
that ties together every agent-OS substrate the repo has:

  * ``sources`` -- WHERE the supporting files live (AGENTS.md,
    roam-laws.yml, .roam/rules/*.yml, .roam/memory.jsonl).
  * ``required_checks`` -- WHICH roam commands an agent MUST run at
    each workflow gate (``before_edit``, ``after_edit``, ``before_pr``).
  * ``modes`` -- a curated allow-list of roam commands per agent mode
    (``read_only`` / ``safe_edit`` / ``migration`` / ``autonomous_pr``)
    so a harness can configure permissions declaratively. Since W37.1
    the default-mode template materialises from
    ``roam.modes.policy._MODE_EXTRAS`` (single source of truth).
  * ``policy`` -- numeric thresholds (blast-radius blocker, cycle cap,
    minimum test coverage) that the gate commands consult.
  * ``metadata_signals`` -- hints to ``roam next`` about which unique
    metrics this repo prefers to surface first.

The loader is intentionally tolerant: missing optional sections collapse
to safe defaults; an unknown key never raises. The constitution is
SUBSTRATE — failing to load it must not derail an agent.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from roam.atomic_io import atomic_write_bytes, atomic_write_text
from roam.commands._command_utils import bare_command_name as _bare_command_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONSTITUTION_DIR_NAME = ".roam"
CONSTITUTION_FILE_NAME = "constitution.yml"
CONSTITUTION_SCHEMA_VERSION = 1

# The constitution schema remains v1: generator provenance is additive
# metadata, not a change to the user-authored document shape. This separate
# version governs only the provenance contract used to distinguish an
# unchanged generated ``modes`` snapshot from a customized policy.
CONSTITUTION_GENERATOR_FORMAT_VERSION = 1
CONSTITUTION_GENERATOR_NAME = "roam constitution init"
_GENERATOR_METADATA_KEY = "generator"
_MANAGED_MODES_DIGEST_KEY = "managed_modes_sha256"

VALID_GATES = ("before_edit", "after_edit", "before_pr")

_YAML_INT_SCALAR_RE = re.compile(r"[+-]?\d+(?:_\d+)*\Z")
_YAML_FLOAT_SCALAR_RE = re.compile(
    r"""
    [+-]?
    (?:
        (?:(?:\d+(?:_\d+)*)?\.\d+(?:_\d+)*|\d+(?:_\d+)*\.)
        (?:[eE][+-]?\d+(?:_\d+)*)?
        |
        \d+(?:_\d+)*[eE][+-]?\d+(?:_\d+)*
        |
        inf(?:inity)?
        |
        nan
    )
    \Z
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Names match the well-known agent-OS substrate files. Each value is a
# relative path the loader resolves against ``repo_root``. We also probe
# alternative locations on init when the canonical one is missing.
DEFAULT_SOURCE_LOCATIONS: dict[str, tuple[str, ...]] = {
    "agents_md": ("AGENTS.md",),
    "laws": ("roam-laws.yml", ".roam/laws.yml"),
    "rules": (".roam/rules", "rules"),
    "memory": (".roam/memory.jsonl",),
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Constitution:
    """In-memory representation of ``.roam/constitution.yml``."""

    version: int = CONSTITUTION_SCHEMA_VERSION
    metadata: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    required_checks: dict[str, list[str]] = field(default_factory=dict)
    modes: dict[str, list[str]] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    metadata_signals: dict = field(default_factory=dict)

    # Bookkeeping (not serialised).
    _path: Optional[Path] = None

    def to_dict(self) -> dict:
        """Plain-dict view used by ``roam constitution show --json``.

        Referenced from ``cmd_constitution.py`` (the ``show`` envelope's
        ``constitution`` field). Named here because six same-named
        ``to_dict`` methods live in this module and reference resolvers
        routinely misattribute the call edges — reviewed 2026-07-02.
        """
        return {
            "version": self.version,
            "metadata": self.metadata,
            "sources": self.sources,
            "required_checks": self.required_checks,
            "modes": self.modes,
            "policy": self.policy,
            "metadata_signals": self.metadata_signals,
        }


@dataclass(frozen=True)
class ConstitutionInitOptions:
    """Options for generating a repo-local constitution file."""

    with_laws: bool = True
    with_rules: bool = True
    force: bool = False


@dataclass
class SourceStatus:
    """Per-source check result."""

    name: str
    path: str
    exists: bool
    state: str  # "ok" | "source_missing" | "unparseable" | "empty"
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
            "state": self.state,
            "detail": self.detail,
        }


@dataclass
class CommandStatus:
    """Per-required-check command resolution result."""

    gate: str
    command: str  # full template (e.g. "roam preflight ${symbol}")
    name: str  # bare verb (e.g. "preflight")
    resolved: bool
    state: str  # "ok" | "unknown_command" | "deprecated_command"

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "command": self.command,
            "name": self.name,
            "resolved": self.resolved,
            "state": self.state,
        }


@dataclass
class CheckReport:
    """Aggregated output of :func:`check_constitution`."""

    ok: bool
    state: str  # "ok" | "partial" | "missing"
    sources: list[SourceStatus] = field(default_factory=list)
    commands: list[CommandStatus] = field(default_factory=list)
    mode_issues: list[dict] = field(default_factory=list)
    mode_upgrade: Optional[ModePolicyUpgradeReport] = None
    summary_verdict: str = ""

    def to_dict(self) -> dict:
        """Full dict view of the check report.

        No in-tree production caller — ``cmd_constitution.py`` composes
        its ``check`` envelope field-by-field (it needs per-section
        counts alongside the lists). Deliberately retained: CheckReport
        is exported via ``roam.constitution.__all__``, and this is the
        one-call serialisation embedders get without reimplementing the
        per-child ``to_dict`` fan-out — reviewed 2026-07-02.
        """
        return {
            "ok": self.ok,
            "state": self.state,
            "sources": [s.to_dict() for s in self.sources],
            "commands": [c.to_dict() for c in self.commands],
            "mode_issues": self.mode_issues,
            "mode_upgrade": self.mode_upgrade.to_dict() if self.mode_upgrade else None,
            "summary_verdict": self.summary_verdict,
        }


@dataclass(frozen=True)
class ModePolicyUpgradeReport:
    """Preview of a generated constitution mode-policy upgrade.

    ``safe_to_apply`` is true only when applying cannot silently broaden a
    customized policy: either the current modes still match their recorded
    generator digest, or the modes already equal today's defaults and only a
    provenance stamp is missing. Legacy and modified policies require an
    explicit replacement acknowledgement.
    """

    state: str
    provenance: str
    summary_verdict: str
    safe_to_apply: bool
    requires_explicit_acceptance: bool
    policy_change: bool
    metadata_change: bool
    current_generator_format: Optional[int]
    target_generator_format: int
    recorded_modes_digest: str
    current_modes_digest: str
    target_modes_digest: str
    additions: dict[str, list[str]] = field(default_factory=dict)
    removals: dict[str, list[str]] = field(default_factory=dict)
    applied: bool = False

    @property
    def changed(self) -> bool:
        return self.policy_change or self.metadata_change

    @property
    def addition_total(self) -> int:
        return sum(len(items) for items in self.additions.values())

    @property
    def removal_total(self) -> int:
        return sum(len(items) for items in self.removals.values())

    @property
    def unique_addition_total(self) -> int:
        return len({command for commands in self.additions.values() for command in commands})

    @property
    def unique_removal_total(self) -> int:
        return len({command for commands in self.removals.values() for command in commands})

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "provenance": self.provenance,
            "summary_verdict": self.summary_verdict,
            "safe_to_apply": self.safe_to_apply,
            "requires_explicit_acceptance": self.requires_explicit_acceptance,
            "changed": self.changed,
            "policy_change": self.policy_change,
            "metadata_change": self.metadata_change,
            "applied": self.applied,
            "current_generator_format": self.current_generator_format,
            "target_generator_format": self.target_generator_format,
            "recorded_modes_digest": self.recorded_modes_digest,
            "current_modes_digest": self.current_modes_digest,
            "target_modes_digest": self.target_modes_digest,
            "addition_total": self.addition_total,
            "removal_total": self.removal_total,
            "change_metric_definition": "per_mode_allow_list_entries",
            "unique_addition_total": self.unique_addition_total,
            "unique_removal_total": self.unique_removal_total,
            "unique_change_metric_definition": "distinct_command_names_across_modes",
            "additions": self.additions,
            "removals": self.removals,
        }


class ConstitutionUpgradeRequiresAcceptance(RuntimeError):
    """Raised when an unproven/customized mode policy needs opt-in."""

    def __init__(self, report: ModePolicyUpgradeReport):
        super().__init__(report.summary_verdict)
        self.report = report


class ConstitutionUpgradePreviewMismatch(RuntimeError):
    """Raised when apply is not bound to the exact previewed modes digest."""

    def __init__(self, report: ModePolicyUpgradeReport, detail: str):
        super().__init__(detail)
        self.report = report


class ConstitutionConcurrentUpdate(RuntimeError):
    """Raised when constitution.yml changes during an upgrade write."""


@dataclass(frozen=True)
class _CheckVerdictInputs:
    """Grouped inputs for :func:`_build_check_verdict`."""

    sources: list[SourceStatus]
    commands: list[CommandStatus]
    mode_issues: list[dict[str, str]]
    gate_count: int
    unparseable: bool


@dataclass(frozen=True)
class _CheckVerdictCounts:
    """Derived counts for constitution check verdict synthesis."""

    missing_sources: int
    unparseable: int
    unknown_commands: int
    deprecated_commands: int
    mode_issues: int

    @classmethod
    def from_inputs(cls, inputs: _CheckVerdictInputs) -> _CheckVerdictCounts:
        return cls(
            missing_sources=sum(1 for source in inputs.sources if not source.exists),
            unparseable=1 if inputs.unparseable else 0,
            unknown_commands=sum(1 for command in inputs.commands if command.state == "unknown_command"),
            deprecated_commands=sum(1 for command in inputs.commands if command.state == "deprecated_command"),
            mode_issues=len(inputs.mode_issues),
        )

    @property
    def issue_total(self) -> int:
        """Total non-deprecated issues detected during constitution check.

        Referenced by ``_build_check_verdict`` at ``counts.issue_total``.
        The dead-export analyzer mis-flags this accessor as unreferenced
        because it does not resolve ``@property`` reads to the underlying
        method; verified referenced, 2026-07-04.
        """
        return self.missing_sources + self.unparseable + self.unknown_commands + self.mode_issues


@dataclass
class ApplyResult:
    """Single (gate-command, exit-code, verdict) result."""

    command: str
    invocation: str
    exit_code: int
    verdict: str
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        """True when a gate command executed and exited cleanly.

        Kept as a public ApplyResult convenience because the apply JSON
        row, ApplyReport counts, aggregate verdict, and CLI table all
        share this invariant -- six read sites in this module
        (``ApplyResult.to_dict``, ``passed_count``, ``failed_count``,
        ``_apply_aggregate_verdict``) plus the CLI table in
        ``cmd_constitution.py``. Centralising it here prevents each
        caller from re-defining how skipped checks differ from passing
        checks. The dead-export analyzer mis-flags it as unreferenced
        because tree-sitter does not resolve ``@property`` reads
        (``r.passed``) as edges to the accessor -- same blind spot as
        ``Constitution.to_dict``; verified referenced, 2026-07-04.
        """
        return not self.skipped and self.exit_code == 0

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "invocation": self.invocation,
            "exit_code": self.exit_code,
            "verdict": self.verdict,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "passed": self.passed,
        }


@dataclass
class ApplyReport:
    """Aggregated output of :func:`apply_constitution`."""

    gate: str
    results: list[ApplyResult] = field(default_factory=list)
    summary_verdict: str = ""
    state: str = "ok"  # "ok" | "partial" | "failed" | "no_checks"

    @property
    def any_failed(self) -> bool:
        return any(r.exit_code != 0 and not r.skipped for r in self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and not r.skipped)

    def to_dict(self) -> dict:
        """Full dict view of the apply report.

        No in-tree production caller — ``cmd_constitution.py`` composes
        its ``apply`` envelope field-by-field. Deliberately retained for
        the same reason as ``CheckReport.to_dict``: ApplyReport is
        public API (``roam.constitution.__all__``) and this is the
        embedder-facing one-call serialisation — reviewed 2026-07-02.
        """
        return {
            "gate": self.gate,
            "results": [r.to_dict() for r in self.results],
            "summary_verdict": self.summary_verdict,
            "state": self.state,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "total": len(self.results),
        }


@dataclass
class ApplyOptions:
    """Grouped execution knobs for :func:`apply_constitution`.

    Collecting ``gate`` / ``variables`` / ``runner`` / ``timeout`` on one
    object keeps :func:`apply_constitution`'s parameter list short. Each
    field mirrors a legacy keyword argument of that function; the legacy
    keywords remain accepted and override the matching field when both
    are supplied. ``runner`` follows the ``runner(argv, cwd, timeout) ->
    (exit_code, stdout, stderr)`` contract documented on
    :func:`apply_constitution` — reviewed 2026-07-03.
    """

    gate: str = "before_edit"
    variables: dict[str, str] = field(default_factory=dict)
    runner: Optional[Any] = None
    timeout: int = 120


# ---------------------------------------------------------------------------
# YAML helpers (PyYAML preferred, inline fallback)
# ---------------------------------------------------------------------------


def _dump_yaml(doc: dict) -> str:
    """Serialise *doc* to YAML. Uses PyYAML if available."""
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
    except ImportError:
        return _fallback_dump(doc)


def _load_yaml(text: str) -> dict:
    """Parse YAML *text* into a dict. Returns ``{}`` on any error.

    Lineage discipline: a parse error stashes a one-line reason on the
    module-level ``_last_yaml_parse_error`` so callers can disclose
    *why* the YAML was unreadable, instead of conflating "empty file"
    with "syntactically broken file" (Pattern-2 silent fallback).
    Callers must read the sentinel BEFORE the next ``_load_yaml`` call —
    it is overwritten per invocation by design.
    """
    global _last_yaml_parse_error
    _last_yaml_parse_error = None
    if not text or not text.strip():
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = _fallback_parse(text)
    except Exception as exc:
        # Stash the parse error so ``load_constitution`` can stamp it
        # onto the unparseable marker rather than throwing away the
        # diagnostic. Pattern-2 fix: distinguish missing-file vs
        # broken-file vs empty-file explicitly.
        _last_yaml_parse_error = f"{type(exc).__name__}: {exc}"[:200]
        return {}
    return data if isinstance(data, dict) else {}


# Sentinel populated by _load_yaml() on parse failure; consumed once by
# load_constitution() to stamp the unparseable-marker metadata.
_last_yaml_parse_error: Optional[str] = None


def _fallback_dump(doc: Any, indent: int = 0) -> str:
    """Minimal YAML writer covering the constitution shape.

    Mirrors the strategy in :mod:`roam.laws.serializer` so behaviour
    stays consistent across the repo. Not general-purpose.
    """
    if isinstance(doc, dict):
        return _dump_dict_entries(doc, indent)
    if isinstance(doc, list):
        return _dump_list_items(doc, indent)
    return "  " * indent + _yaml_scalar(doc)


def _dump_dict_entries(doc: dict, indent: int) -> str:
    """Serialize every key/value pair of a mapping."""
    lines: list[str] = []
    for key, value in doc.items():
        lines.extend(_dump_mapping_entry(key, value, indent))
    return "\n".join(filter(None, lines))


def _dump_mapping_entry(key: Any, value: Any, indent: int) -> list[str]:
    """Serialize one mapping entry, choosing block or inline shape.

    WHY: a single key's value can be an empty container, a nested
    container with children, or a scalar — each shape needs a
    different YAML layout.
    """
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{key}: {{}}"]
        return [f"{pad}{key}:", _fallback_dump(value, indent + 1)]
    if isinstance(value, list):
        if not value:
            return [f"{pad}{key}: []"]
        return [f"{pad}{key}:", _fallback_dump(value, indent + 1)]
    return [f"{pad}{key}: {_yaml_scalar(value)}"]


def _dump_list_items(doc: list, indent: int) -> str:
    """Serialize every item of a sequence."""
    lines: list[str] = []
    for item in doc:
        lines.extend(_dump_sequence_item(item, indent))
    return "\n".join(filter(None, lines))


def _dump_sequence_item(item: Any, indent: int) -> list[str]:
    """Serialize one sequence item, choosing block or inline shape.

    WHY: a list item can be a nested container (introduced by '-') or a
    scalar (introduced by '- '), and the indent and prefix differ.
    """
    pad = "  " * indent
    if isinstance(item, (dict, list)):
        return [f"{pad}-", _fallback_dump(item, indent + 1)]
    return [f"{pad}- {_yaml_scalar(item)}"]


def _yaml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in ":#&*!,[]{}\"'\n") or s in ("null", "true", "false"):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


@dataclass
class _ParseContext:
    """Per-line parse state shared across the ``_fallback_parse`` helpers.

    Bundles the current line's stripped text + indent with the mutable
    container stack so the dispatch helpers stop repeating the
    ``(stripped, indent, stack)`` parameter trio (data-clump fix).
    ``stack`` is the live parser state; the other two fields describe the
    current line.
    """

    stripped: str
    indent: int
    stack: list[tuple[int, Any, dict | None, str | None]]


def _promote_to_list(ctx: _ParseContext) -> list | None:
    """Return a list parent for the current list-item line.

    A `key:` line with no value creates a dict placeholder. The first `-`
    child underneath it proves the placeholder is actually a list, so we
    mutate the grandparent and retarget the stack frame. This late-bound
    container polymorphism is the main source of complexity in the parser.
    """
    stack = ctx.stack
    _, parent, grandparent, key_in_grand = stack[-1]
    if isinstance(parent, list):
        return parent
    if grandparent is not None and key_in_grand is not None:
        new_list: list = []
        grandparent[key_in_grand] = new_list  # type: ignore[index]
        stack[-1] = (stack[-1][0], new_list, grandparent, key_in_grand)
        return new_list
    return None


def _add_list_item(parent_list: list, value_part: str, ctx: _ParseContext) -> None:
    """Append one list item and push a dict frame for multi-line children."""
    if not value_part:
        new_dict: dict[str, Any] = {}
        parent_list.append(new_dict)
        ctx.stack.append((ctx.indent, new_dict, None, None))
    else:
        parent_list.append(_yaml_unscalar(value_part))


def _add_mapping_item(parent_dict: dict, ctx: _ParseContext) -> None:
    """Append one key/value pair and push a frame for nested children."""
    key, _, rest = ctx.stripped.partition(":")
    key = key.strip()
    rest = rest.strip()
    if rest == "":
        # Container — default to dict; will get promoted on first `- ` child.
        new_container: dict = {}
        parent_dict[key] = new_container
        ctx.stack.append((ctx.indent, new_container, parent_dict, key))
    elif rest == "[]":
        parent_dict[key] = []
    elif rest == "{}":
        parent_dict[key] = {}
    else:
        parent_dict[key] = _yaml_unscalar(rest)


def _is_blank_or_comment(raw_line: str) -> bool:
    """Skip empty lines and full-line comments."""
    return not raw_line.strip() or raw_line.lstrip().startswith("#")


def _rewind_stack(ctx: _ParseContext, root: dict) -> None:
    """Pop frames whose indent is >= this line's, guarding against underflow."""
    stack = ctx.stack
    while stack and stack[-1][0] >= ctx.indent:
        stack.pop()
    if not stack:
        stack.append((-1, root, None, None))


def _handle_list_line(ctx: _ParseContext) -> None:
    """Dispatch a `-` line to the current (or promoted) list parent."""
    parent_list = _promote_to_list(ctx)
    if parent_list is None:
        return
    value_part = ctx.stripped[2:].strip() if ctx.stripped.startswith("- ") else ""
    _add_list_item(parent_list, value_part, ctx)


def _handle_mapping_line(ctx: _ParseContext, parent: Any) -> None:
    """Dispatch a `key:` line to the current dict parent."""
    if not isinstance(parent, dict):
        return
    _add_mapping_item(parent, ctx)


def _fallback_parse(text: str) -> dict:
    """Very small YAML parser tolerant of the subset we emit.

    Two-space indentation. Handles nested dicts and lists of strings or
    dicts. Used only when PyYAML is unavailable.
    """
    root: dict[str, Any] = {}
    # Each stack frame is (indent, container, parent_key_in_grandparent).
    # parent_key_in_grandparent is only used when promoting an empty dict
    # placeholder to a list once we see its first `- ` child.
    stack: list[tuple[int, Any, dict | None, str | None]] = [(-1, root, None, None)]

    for raw_line in text.splitlines():
        if _is_blank_or_comment(raw_line):
            continue
        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)
        ctx = _ParseContext(stripped=stripped, indent=indent, stack=stack)
        _rewind_stack(ctx, root)
        _, parent, _, _ = stack[-1]

        if stripped.startswith("- ") or stripped == "-":
            _handle_list_line(ctx)
        elif ":" in stripped:
            _handle_mapping_line(ctx, parent)
    return root


def _yaml_unscalar(s: str) -> Any:
    s = s.strip()
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if _YAML_INT_SCALAR_RE.fullmatch(s):
        return int(s)
    if _YAML_FLOAT_SCALAR_RE.fullmatch(s):
        return float(s)
    return s


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def constitution_path(repo_root: Path) -> Path:
    """Canonical path: ``<repo_root>/.roam/constitution.yml``."""
    return Path(repo_root) / CONSTITUTION_DIR_NAME / CONSTITUTION_FILE_NAME


def _detect_source(repo_root: Path, locations: tuple[str, ...]) -> Optional[str]:
    """Return the first existing relative location, or ``None``."""
    for rel in locations:
        if (Path(repo_root) / rel).exists():
            return rel
    return None


def _project_name(repo_root: Path) -> str:
    """Best-effort project name from the repo dir."""
    try:
        return Path(repo_root).resolve().name
    except (OSError, RuntimeError):
        return "unknown"


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


_INIT_OPTION_KEYS = frozenset({"with_laws", "with_rules", "force"})


def _normalise_init_options(
    options: ConstitutionInitOptions | None,
    legacy_options: dict[str, Any],
) -> ConstitutionInitOptions:
    """Return init options while preserving legacy keyword compatibility."""
    unexpected = set(legacy_options) - _INIT_OPTION_KEYS
    if unexpected:
        key = sorted(unexpected)[0]
        raise TypeError(f"init_constitution() got an unexpected keyword argument {key!r}")
    if options is None:
        resolved = ConstitutionInitOptions()
    elif isinstance(options, ConstitutionInitOptions):
        resolved = options
    else:
        raise TypeError("init_constitution() options must be ConstitutionInitOptions")
    if not legacy_options:
        return resolved
    return ConstitutionInitOptions(
        with_laws=legacy_options.get("with_laws", resolved.with_laws),
        with_rules=legacy_options.get("with_rules", resolved.with_rules),
        force=legacy_options.get("force", resolved.force),
    )


def _default_required_checks() -> dict[str, list[str]]:
    """Sensible gate defaults derived from the agent-OS workflow.

    These mirror the verb ordering described in CLAUDE.md so a fresh
    constitution is immediately useful. Users can edit the file by hand
    afterwards.
    """
    return {
        "before_edit": [
            "roam preflight ${symbol}",
            "roam impact ${symbol}",
        ],
        "after_edit": [
            "roam diff",
            "roam critique",
        ],
        "before_pr": [
            "roam pr-bundle validate --strict",
            "roam laws check --strict",
        ],
    }


def _default_modes() -> dict[str, list[str]]:
    """Sensible mode defaults.

    Each list names roam verbs (no flags) an agent is allowed to invoke
    in that mode. Higher modes are strict supersets of lower modes.

    **W37.1 single-source-of-truth materialisation.** Previously, this
    function maintained a hand-edited mode-to-verbs map that had to be
    kept in lockstep with ``roam.modes.policy._MODE_EXTRAS``. W23.4
    found the trap: ``_MODE_EXTRAS`` had ``runs`` but ``_default_modes``
    did not, so after ``roam constitution init`` the on-disk
    constitution silently dropped ``runs`` (the loader prefers a
    declared mode list as a REPLACEMENT — see
    ``policy._materialise_from_constitution``).

    The fix is structural: materialise the default-mode template
    directly from ``_MODE_EXTRAS`` so the two cannot drift. The result
    is the cumulative union per ``VALID_MODES`` order, sorted for
    deterministic YAML output.
    """
    from roam.modes.policy import _MODE_EXTRAS, VALID_MODES

    out: dict[str, list[str]] = {}
    cumulative: set[str] = set()
    for mode in VALID_MODES:
        cumulative = cumulative | _MODE_EXTRAS.get(mode, set())
        out[mode] = sorted(cumulative)
    return out


def _normalise_modes(modes: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return the semantic mode-policy shape used for provenance hashing.

    Ordering and duplicate entries do not change policy semantics, so they do
    not invalidate generator ownership. Command flags are normalized through
    the same bare-command helper used by enforcement.
    """

    normalised: dict[str, list[str]] = {}
    for mode, commands in modes.items():
        if not isinstance(mode, str) or not isinstance(commands, list):
            continue
        names = {_bare_command_name(str(command)) for command in commands if command}
        normalised[mode] = sorted(name for name in names if name)
    return dict(sorted(normalised.items()))


def mode_policy_digest(modes: dict[str, list[str]]) -> str:
    """Return a stable semantic SHA-256 for a constitution ``modes`` block."""

    payload = json.dumps(
        _normalise_modes(modes),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _valid_modes_digest(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _generator_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get(_GENERATOR_METADATA_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _classify_modes_provenance(
    metadata: dict[str, Any],
    current_digest: str,
) -> tuple[str, Optional[int], str]:
    """Return ``(state, format_version, recorded_digest)`` fail-closed."""

    generator = _generator_metadata(metadata)
    raw_format = generator.get("format_version")
    current_format = raw_format if isinstance(raw_format, int) and not isinstance(raw_format, bool) else None
    recorded_digest = generator.get(_MANAGED_MODES_DIGEST_KEY)
    recorded_digest = recorded_digest if isinstance(recorded_digest, str) else ""
    if not generator:
        state = (
            "legacy_generated_unproven" if metadata.get("generated_by") == CONSTITUTION_GENERATOR_NAME else "unmanaged"
        )
    elif current_format != CONSTITUTION_GENERATOR_FORMAT_VERSION:
        state = "unsupported_generator_provenance"
    elif generator.get("name") != CONSTITUTION_GENERATOR_NAME or not _valid_modes_digest(recorded_digest):
        state = "invalid_generator_provenance"
    elif recorded_digest == current_digest:
        state = "managed_unchanged"
    else:
        state = "customized"
    return state, current_format, recorded_digest


def _stamp_modes_provenance(metadata: dict[str, Any], modes: dict[str, list[str]]) -> dict[str, Any]:
    """Copy metadata and record ownership of the exact generated modes."""

    stamped = dict(metadata)
    generator = _generator_metadata(stamped)
    generator.update(
        {
            "name": CONSTITUTION_GENERATOR_NAME,
            "format_version": CONSTITUTION_GENERATOR_FORMAT_VERSION,
            _MANAGED_MODES_DIGEST_KEY: mode_policy_digest(modes),
        }
    )
    stamped["generated_by"] = CONSTITUTION_GENERATOR_NAME
    stamped[_GENERATOR_METADATA_KEY] = generator
    return stamped


def _mode_deltas(
    current: dict[str, list[str]],
    target: dict[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    current_normalised = _normalise_modes(current)
    target_normalised = _normalise_modes(target)
    additions: dict[str, list[str]] = {}
    removals: dict[str, list[str]] = {}
    mode_names = dict.fromkeys([*target_normalised, *current_normalised])
    for mode in mode_names:
        current_names = set(current_normalised.get(mode, []))
        target_names = set(target_normalised.get(mode, []))
        added = sorted(target_names - current_names)
        removed = sorted(current_names - target_names)
        if added:
            additions[mode] = added
        if removed:
            removals[mode] = removed
    return additions, removals


def assess_constitution_upgrade(constitution: Constitution) -> ModePolicyUpgradeReport:
    """Preview whether and how the generated mode policy can be upgraded.

    A digest match proves only that the ``modes`` block is byte-independent,
    semantically unchanged from the snapshot the generator recorded. It is not
    an authenticity signature. That is sufficient for the narrow ownership
    decision here: automatic tracking is allowed only while users leave the
    generated policy unchanged.
    """

    target_modes = _default_modes()
    current_modes = dict(constitution.modes or {})
    current_digest = mode_policy_digest(current_modes)
    target_digest = mode_policy_digest(target_modes)
    additions, removals = _mode_deltas(current_modes, target_modes)
    policy_change = bool(additions or removals)
    unique_change_total = len({command for commands in additions.values() for command in commands}) + len(
        {command for commands in removals.values() for command in commands}
    )

    if not current_modes:
        return ModePolicyUpgradeReport(
            state="not_applicable",
            provenance="no_declared_modes",
            summary_verdict="constitution has no explicit modes block; runtime defaults remain authoritative",
            safe_to_apply=True,
            requires_explicit_acceptance=False,
            policy_change=False,
            metadata_change=False,
            current_generator_format=None,
            target_generator_format=CONSTITUTION_GENERATOR_FORMAT_VERSION,
            recorded_modes_digest="",
            current_modes_digest=current_digest,
            target_modes_digest=target_digest,
        )

    provenance, current_format, recorded_digest = _classify_modes_provenance(
        constitution.metadata,
        current_digest,
    )

    metadata_change = not (
        provenance == "managed_unchanged"
        and current_format == CONSTITUTION_GENERATOR_FORMAT_VERSION
        and recorded_digest == target_digest
    )

    if not policy_change:
        if not metadata_change:
            state = "up_to_date"
            verdict = "constitution mode policy matches current generated defaults"
        else:
            state = "provenance_upgrade"
            verdict = "mode policy matches current defaults; apply to record managed provenance"
        safe_to_apply = True
    elif provenance == "managed_unchanged":
        state = "upgrade_available"
        safe_to_apply = True
        verdict = f"{unique_change_total} generated command changes can be applied safely"
    else:
        state = "review_required"
        safe_to_apply = False
        verdict = (
            f"{unique_change_total} command changes require explicit mode-policy review and replacement acknowledgement"
        )

    return ModePolicyUpgradeReport(
        state=state,
        provenance=provenance,
        summary_verdict=verdict,
        safe_to_apply=safe_to_apply,
        requires_explicit_acceptance=policy_change and not safe_to_apply,
        policy_change=policy_change,
        metadata_change=metadata_change,
        current_generator_format=current_format,
        target_generator_format=CONSTITUTION_GENERATOR_FORMAT_VERSION,
        recorded_modes_digest=recorded_digest,
        current_modes_digest=current_digest,
        target_modes_digest=target_digest,
        additions=additions,
        removals=removals,
    )


def effective_constitution_modes(constitution: Constitution) -> dict[str, list[str]]:
    """Return declared modes, tracking new defaults only when ownership is proven."""

    declared = {mode: list(commands) for mode, commands in constitution.modes.items()}
    generator = _generator_metadata(constitution.metadata)
    if not declared or not generator:
        return declared
    provenance, _, _ = _classify_modes_provenance(
        constitution.metadata,
        mode_policy_digest(declared),
    )
    if provenance == "managed_unchanged":
        return _default_modes()
    return declared


def _default_policy() -> dict[str, Any]:
    return {
        "blast_radius": {
            "blocker_threshold": 100,
            "warning_threshold": 25,
        },
        "cycles": {
            "blocker_threshold": 5,
        },
        "test_coverage": {
            "minimum_pct": 60,
        },
    }


def _default_metadata_signals() -> dict[str, Any]:
    return {
        "prefer_unique_signals": [
            "danger_score",
            "ai_rot_score",
            "cohesion_pct",
        ],
    }


def _discover_sources(repo_root: Path, init_options: ConstitutionInitOptions) -> dict[str, str]:
    """Probe the repo for the supporting substrate files.

    Returns the ``sources`` mapping (relative ``./``-prefixed paths) for
    every file that exists. ``agents_md`` and ``memory`` are always probed;
    ``laws`` / ``rules`` are probed only when the corresponding option is
    set. Absent files are omitted so ``check`` does not flag stub paths.
    """
    sources: dict[str, str] = {}

    agents = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["agents_md"])
    if agents:
        sources["agents_md"] = f"./{agents}"

    if init_options.with_laws:
        laws = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["laws"])
        if laws:
            sources["laws"] = f"./{laws}"

    if init_options.with_rules:
        rules = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["rules"])
        if rules:
            # Convention: point at a glob within the directory, matching
            # the YAML rule convention used elsewhere in roam.
            sources["rules"] = f"./{rules}/*.yml"

    memory = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["memory"])
    if memory:
        sources["memory"] = f"./{memory}"

    return sources


def _build_constitution_doc(repo_root: Path, init_options: ConstitutionInitOptions) -> dict[str, Any]:
    """Assemble the constitution document dict from repo state."""
    sources = _discover_sources(repo_root, init_options)
    modes = _default_modes()

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata = _stamp_modes_provenance(
        {
            "name": _project_name(repo_root),
            "description": "Constitution for AI agents working on this codebase",
            "generated_at": now,
            "generated_by": CONSTITUTION_GENERATOR_NAME,
        },
        modes,
    )

    return {
        "version": CONSTITUTION_SCHEMA_VERSION,
        "metadata": metadata,
        "sources": sources,
        "required_checks": _default_required_checks(),
        "modes": modes,
        "policy": _default_policy(),
        "metadata_signals": _default_metadata_signals(),
    }


def _write_constitution(path: Path, doc: dict[str, Any]) -> None:
    """Dump *doc* to YAML and write it atomically to *path*."""
    text = _dump_yaml(doc)
    # Always end with a trailing newline so the file is POSIX-clean.
    if not text.endswith("\n"):
        text += "\n"
    # Atomic write: a crash mid-write would otherwise leave a torn YAML
    # file at .roam/constitution.yml — Pattern-1C territory because the
    # next ``load_constitution`` call would mark it ``unparseable`` and
    # callers would lose the otherwise-valid prior state. atomic_write_text
    # uses temp-file + os.replace, so the target file is never half-written.
    atomic_write_text(path, text)


def init_constitution(
    repo_root: Path,
    options: ConstitutionInitOptions | None = None,
    **legacy_options: Any,
) -> Path:
    """Generate ``.roam/constitution.yml`` from the current repo state.

    Auto-discovers ``AGENTS.md``, ``roam-laws.yml``/``.roam/laws.yml``,
    ``.roam/rules/`` (or top-level ``rules/``), and ``.roam/memory.jsonl``.
    Populates ``sources`` to point at what exists; absent files yield an
    absent key (NOT a stub path) so ``check`` does not flag them.

    Pass ``ConstitutionInitOptions`` to configure source discovery and
    overwrite behaviour. Legacy keyword flags remain accepted as
    overrides for existing callers.

    Raises ``FileExistsError`` if the file already exists and
    ``options.force`` is False.
    """
    init_options = _normalise_init_options(options, legacy_options)
    repo_root = Path(repo_root).resolve()
    path = constitution_path(repo_root)
    if path.exists() and not init_options.force:
        raise FileExistsError(f"constitution already exists at {path}; pass force=True to overwrite")

    doc = _build_constitution_doc(repo_root, init_options)
    _write_constitution(path, doc)
    return path


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def _constitution_from_loaded_data(data: dict, *, path: Path, source_text: str) -> Constitution:
    """Build a :class:`Constitution` from one already-read YAML snapshot."""

    if not data:
        meta: dict[str, Any] = {"unparseable": True}
        if _last_yaml_parse_error:
            meta["parse_error"] = _last_yaml_parse_error
        elif not source_text.strip():
            meta["reason"] = "empty_file"
        return Constitution(version=0, metadata=meta, _path=path)

    def _as_dict(value: Any) -> dict:
        return value if isinstance(value, dict) else {}

    def _as_list_of_str(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, (str, int))]

    required_checks_raw = _as_dict(data.get("required_checks"))
    required_checks: dict[str, list[str]] = {}
    for gate, items in required_checks_raw.items():
        if isinstance(gate, str):
            required_checks[gate] = _as_list_of_str(items)

    modes_raw = _as_dict(data.get("modes"))
    modes: dict[str, list[str]] = {}
    for mode, items in modes_raw.items():
        if isinstance(mode, str):
            modes[mode] = _as_list_of_str(items)

    return Constitution(
        version=int(data.get("version") or CONSTITUTION_SCHEMA_VERSION),
        metadata=_as_dict(data.get("metadata")),
        sources={str(k): str(v) for k, v in _as_dict(data.get("sources")).items() if isinstance(v, (str, int))},
        required_checks=required_checks,
        modes=modes,
        policy=_as_dict(data.get("policy")),
        metadata_signals=_as_dict(data.get("metadata_signals")),
        _path=path,
    )


def load_constitution(repo_root: Path) -> Optional[Constitution]:
    """Read ``.roam/constitution.yml``. Returns ``None`` if not found.

    Tolerates an unparseable file by returning a near-empty Constitution
    so the caller can still emit a diagnostic envelope; the loader never
    raises.
    """
    path = constitution_path(Path(repo_root))
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # Fail-soft by contract ("the loader never raises"), but disclose
        # the degraded state: the file EXISTS yet cannot be read, which is
        # not the same as "no constitution". Silent None here would be the
        # Pattern-2 silent fallback this module avoids elsewhere.
        sys.stderr.write(f"[constitution] cannot read {path}: {exc}\n")
        return None
    data = _load_yaml(text)
    return _constitution_from_loaded_data(data, path=path, source_text=text)


def upgrade_constitution(
    repo_root: Path,
    *,
    accept_mode_replacement: bool = False,
    expected_modes_digest: Optional[str] = None,
) -> ModePolicyUpgradeReport:
    """Apply the current generated mode policy with fail-safe ownership checks.

    The whole YAML document is read once, unknown top-level and metadata keys
    are retained, and only ``modes`` plus generator provenance are replaced.
    A compare-and-swap check immediately before the atomic rename prevents a
    concurrent user edit from being overwritten.
    """

    path = constitution_path(Path(repo_root))
    if not path.exists():
        raise FileNotFoundError(f"constitution does not exist at {path}")
    if path.is_symlink():
        raise OSError(f"refusing to upgrade symlinked constitution at {path}")

    original_bytes = path.read_bytes()
    try:
        original_text = original_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"constitution is not valid UTF-8: {exc}") from exc

    data = _load_yaml(original_text)
    constitution = _constitution_from_loaded_data(data, path=path, source_text=original_text)
    if constitution.metadata.get("unparseable"):
        raise ValueError("constitution is unparseable; repair it before upgrading")

    report = assess_constitution_upgrade(constitution)
    if report.requires_explicit_acceptance and not accept_mode_replacement:
        raise ConstitutionUpgradeRequiresAcceptance(report)
    if report.requires_explicit_acceptance:
        if not expected_modes_digest:
            raise ConstitutionUpgradePreviewMismatch(
                report,
                "an unproven mode replacement requires --expect-modes-digest from the preview",
            )
        if expected_modes_digest != report.current_modes_digest:
            raise ConstitutionUpgradePreviewMismatch(
                report,
                "mode policy changed since preview; run `roam constitution upgrade` again",
            )
    if not report.changed:
        return report

    target_modes = _default_modes()
    updated = dict(data)
    updated["modes"] = target_modes
    raw_metadata = updated.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    updated["metadata"] = _stamp_modes_provenance(metadata, target_modes)
    rendered = _dump_yaml(updated)
    if not rendered.endswith("\n"):
        rendered += "\n"
    rendered_bytes = rendered.encode("utf-8")

    def _assert_unchanged() -> None:
        try:
            current_bytes = path.read_bytes()
        except OSError as exc:
            raise ConstitutionConcurrentUpdate(
                f"constitution changed or became unreadable during upgrade: {exc}"
            ) from exc
        if current_bytes != original_bytes:
            raise ConstitutionConcurrentUpdate("constitution changed during upgrade; preview again before applying")

    atomic_write_bytes(
        path,
        rendered_bytes,
        before_replace=_assert_unchanged,
        durable=True,
        create_parents=False,
    )
    upgraded = _constitution_from_loaded_data(updated, path=path, source_text=rendered)
    post = assess_constitution_upgrade(upgraded)
    return replace(
        report,
        state="upgraded",
        provenance=post.provenance,
        summary_verdict=(
            f"constitution mode policy upgraded with {report.unique_addition_total} added command(s) "
            f"and {report.unique_removal_total} removed command(s)"
        ),
        safe_to_apply=True,
        requires_explicit_acceptance=False,
        metadata_change=False,
        current_generator_format=post.current_generator_format,
        recorded_modes_digest=post.recorded_modes_digest,
        current_modes_digest=post.current_modes_digest,
        applied=True,
    )


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


def _known_commands() -> set[str]:
    """Return the set of currently-registered roam command names.

    Lazy import inside the function: ``roam.cli`` is ~100ms to load
    (LazyGroup with 241 commands) and most ``load_constitution`` callers
    never hit this code path. No cycle exists with ``roam.cli`` — W902 /
    W878 verified the prior "avoid import cycle" hedge was false; the
    laziness is purely a cold-start cost optimisation. (Pattern-2
    lineage rule: name WHY it's lazy specifically.)
    """
    try:
        from roam.cli import _COMMANDS  # lazy: roam.cli is ~100ms to import

        return set(_COMMANDS.keys())
    except ImportError:
        return set()


def _deprecated_commands() -> set[str]:
    try:
        from roam.cli import _DEPRECATED_COMMANDS  # type: ignore

        return set(_DEPRECATED_COMMANDS.keys())
    except ImportError:
        return set()


def _resolve_source_path(repo_root: Path, raw: str) -> Path:
    """Resolve a source path string into an absolute Path."""
    raw = raw.strip()
    # Strip a leading "./".
    if raw.startswith("./"):
        raw = raw[2:]
    elif raw.startswith(".\\"):
        raw = raw[2:]
    p = Path(raw)
    if p.is_absolute():
        return p
    return Path(repo_root) / p


def _source_status_for_glob_without_blocking_loader(repo_root: Path, name: str, raw_path: str) -> SourceStatus:
    """Classify an optional glob source while keeping loader checks non-fatal."""
    try:
        rel = raw_path
        if rel.startswith("./"):
            rel = rel[2:]
        elif rel.startswith(".\\"):
            rel = rel[2:]
        matches = list(Path(repo_root).glob(rel))
    except (OSError, ValueError, NotImplementedError):
        matches = []
    if matches:
        return SourceStatus(
            name=name,
            path=raw_path,
            exists=True,
            state="ok",
            detail=f"{len(matches)} file(s) match",
        )
    return SourceStatus(
        name=name,
        path=raw_path,
        exists=False,
        state="source_missing",
        detail="no files match glob",
    )


def _source_status(repo_root: Path, name: str, raw_path: str) -> SourceStatus:
    """Check a single source. Globs (e.g. ``*.yml``) match if ANY file matches."""
    p = _resolve_source_path(repo_root, raw_path)
    raw = str(raw_path)
    if any(ch in raw for ch in "*?["):
        return _source_status_for_glob_without_blocking_loader(repo_root, name, raw)
    if not p.exists():
        return SourceStatus(
            name=name,
            path=raw_path,
            exists=False,
            state="source_missing",
            detail="path does not exist",
        )
    # Empty-file probe — empty AGENTS.md / memory.jsonl is suspicious but not fatal.
    try:
        if p.is_file() and p.stat().st_size == 0:
            return SourceStatus(
                name=name,
                path=raw_path,
                exists=True,
                state="empty",
                detail="file is zero bytes",
            )
    except OSError as exc:
        sys.stderr.write(f"[constitution] optional source empty-file probe failed for {raw_path}: {exc}\n")
    return SourceStatus(name=name, path=raw_path, exists=True, state="ok", detail="")


def _classify_command(bare: str, known: set[str], deprecated: set[str]) -> tuple[str, bool]:
    """Return the resolution state for a bare command name."""
    if bare in known:
        return "ok", True
    if bare in deprecated:
        return "deprecated_command", True
    return "unknown_command", False


def _resolve_required_checks(
    required_checks: dict[str, list[str]],
    known: set[str],
    deprecated: set[str],
) -> list[CommandStatus]:
    """Resolve every required-check template against the registered command set."""
    commands_out: list[CommandStatus] = []
    for gate, items in required_checks.items():
        for raw in items:
            bare = _bare_command_name(raw)
            if not bare:
                commands_out.append(
                    CommandStatus(
                        gate=gate,
                        command=raw,
                        name="",
                        resolved=False,
                        state="unknown_command",
                    )
                )
                continue
            state, resolved = _classify_command(bare, known, deprecated)
            commands_out.append(
                CommandStatus(
                    gate=gate,
                    command=raw,
                    name=bare,
                    resolved=resolved,
                    state=state,
                )
            )
    return commands_out


def _check_mode_allow_lists(
    modes: dict[str, list[str]],
    known: set[str],
    deprecated: set[str],
) -> list[dict[str, str]]:
    """Validate mode allow-lists: every member must resolve to a real command."""
    mode_issues: list[dict[str, str]] = []
    for mode, allowed in modes.items():
        for name in allowed:
            bare = _bare_command_name(name)
            if bare and bare not in known and bare not in deprecated:
                mode_issues.append(
                    {
                        "mode": mode,
                        "command": name,
                        "name": bare,
                        "state": "unknown_command",
                    }
                )
    return mode_issues


def _build_check_verdict(inputs: _CheckVerdictInputs) -> tuple[bool, str, str]:
    """Synthesize the aggregate state, ok flag, and human verdict."""
    counts = _CheckVerdictCounts.from_inputs(inputs)

    if inputs.unparseable:
        return (
            False,
            "missing",
            "constitution is unparseable -- re-run `roam constitution init --force`",
        )
    if counts.issue_total == 0 and counts.deprecated_commands == 0:
        verdict = (
            f"constitution is healthy "
            f"({len(inputs.sources)} source(s), "
            f"{len(inputs.commands)} required-check(s) across "
            f"{inputs.gate_count} gate(s))"
        )
        return True, "ok", verdict

    bits: list[str] = []
    if counts.missing_sources:
        bits.append(f"{counts.missing_sources} missing source(s)")
    if counts.unknown_commands:
        bits.append(f"{counts.unknown_commands} unknown command(s)")
    if counts.deprecated_commands:
        bits.append(f"{counts.deprecated_commands} deprecated command(s)")
    if counts.mode_issues:
        bits.append(f"{counts.mode_issues} mode allow-list issue(s)")
    return False, "partial", "constitution issues: " + ", ".join(bits)


def check_constitution(repo_root: Path, constitution: Constitution) -> CheckReport:
    """Verify that every declared source exists and every required-check
    command resolves to a real roam command.

    Returns a structured ``CheckReport`` so the caller can build an
    envelope. Never raises.
    """
    sources_out = [_source_status(repo_root, name, raw) for name, raw in constitution.sources.items()]

    known = _known_commands()
    deprecated = _deprecated_commands()
    commands_out = _resolve_required_checks(constitution.required_checks, known, deprecated)
    mode_issues = _check_mode_allow_lists(constitution.modes, known, deprecated)
    mode_upgrade = assess_constitution_upgrade(constitution)
    check_inputs = _CheckVerdictInputs(
        sources=sources_out,
        commands=commands_out,
        mode_issues=mode_issues,
        gate_count=len(constitution.required_checks),
        unparseable=bool(constitution.metadata.get("unparseable")),
    )
    ok, state, verdict = _build_check_verdict(check_inputs)

    return CheckReport(
        ok=ok,
        state=state,
        sources=sources_out,
        commands=commands_out,
        mode_issues=mode_issues,
        mode_upgrade=mode_upgrade,
        summary_verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _substitute_placeholders(template: str, vars: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``${name}`` placeholders. Returns (resolved, missing_vars).

    Used for the recorded ``invocation`` string + missing-var detection
    surfaced in :class:`ApplyResult`. The executed argv is produced by
    :func:`_tokenize_and_substitute` so user-supplied variables CANNOT
    introduce additional argv tokens.
    """
    out = template
    missing: list[str] = []
    # Find every placeholder and substitute if we have the value.
    import re

    for match in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
        name = match.group(1)
        value = vars.get(name)
        if value is None:
            missing.append(name)
        else:
            out = out.replace(match.group(0), value)
    return out, missing


def _tokenize_and_substitute(template: str, vars: dict[str, str]) -> tuple[list[str], list[str]]:
    """Token-safe placeholder substitution.

    Tokenizes the template FIRST (shlex.split), then replaces ``${name}``
    occurrences inside each token with the matching variable's raw value.
    This means a variable value like ``"useThemeClasses --evil-flag"``
    becomes a SINGLE argv token, not two — closing the post-substitution
    argv-injection vector where a value with whitespace would inject
    extra command-line flags into the invoked subprocess.

    Returns ``(argv, missing_vars)``. The argv may be empty (e.g. empty
    template) — callers handle that as a separate condition.
    """
    import re

    tokens = _split_invocation(template)
    missing: list[str] = []
    seen_missing: set[str] = set()
    out: list[str] = []
    placeholder_re = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for tok in tokens:
        # Resolve every placeholder inside this single token. The token
        # is the unit of argv-safety: substitution NEVER splits a token
        # into multiple argv entries, regardless of the variable value.
        def _replace(m: re.Match[str]) -> str:
            name = m.group(1)
            value = vars.get(name)
            if value is None:
                if name not in seen_missing:
                    seen_missing.add(name)
                    missing.append(name)
                return m.group(0)
            return value

        out.append(placeholder_re.sub(_replace, tok))
    return out, missing


def _split_invocation(s: str) -> list[str]:
    """Tokenize a shell-style invocation, preserving simple quoted strings.

    Sufficient for the simple verbs we emit; we do NOT run anything via
    ``shell=True`` so no full shell parsing is needed.
    """
    import shlex

    try:
        return shlex.split(s, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — fall back to a simple whitespace split
        # so we still try to run the command rather than failing silently.
        return s.split()


def _apply_default_runner(argv: list[str], cwd: Path, t: int) -> tuple[int, str, str]:
    """Default check-runner used when ``apply_constitution(runner=None)``.
    Execs the command as a subprocess via ``subprocess.run``. The first
    token is typically ``roam`` — on Windows resolves via PATHEXT; on
    POSIX must be in PATH. Never raises: timeouts return (124, '',
    'timeout after Ns'); missing binaries return (127, '', str(exc))."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {t}s"
    except (FileNotFoundError, OSError) as exc:
        return 127, "", str(exc)


def _apply_extract_verdict_line(stdout: str, stderr: str) -> str:
    """Best-effort one-liner verdict surface: prefer the first non-empty
    stdout line, fall back to the first non-empty stderr line. Truncated
    at 200 chars to keep envelope shape predictable."""
    for src in (stdout, stderr):
        if not src:
            continue
        for line in src.splitlines():
            line = line.strip()
            if line:
                return line[:200]
    return ""


def _apply_run_one_template(
    template: str,
    vars_in: dict,
    run: Any,
    repo_root: Path,
    timeout: int,
) -> ApplyResult:
    """Resolve placeholders in one check template and execute (or skip)
    it. Uses ``_tokenize_and_substitute`` for the executed argv so a
    variable value containing whitespace or shell metacharacters CANNOT
    introduce additional argv tokens (argv-injection guard); the
    human-readable ``invocation`` string still comes from the legacy
    string-substitution helper. Skips when required ``${variable}``
    substitutions are missing; produces an exit-code-2 envelope when the
    substitution yields an empty argv. Otherwise dispatches via runner
    and records the result with a best-effort verdict line."""
    bare = _bare_command_name(template)
    argv, missing = _tokenize_and_substitute(template, vars_in)
    resolved, _ = _substitute_placeholders(template, vars_in)
    if missing:
        return ApplyResult(
            command=bare,
            invocation=template,
            exit_code=0,
            verdict="",
            skipped=True,
            skip_reason=f"missing variables: {', '.join(sorted(set(missing)))}",
        )
    if not argv:
        return ApplyResult(
            command=bare,
            invocation=template,
            exit_code=2,
            verdict="empty invocation after substitution",
            skipped=False,
        )
    exit_code, stdout, stderr = run(argv, repo_root, timeout)
    return ApplyResult(
        command=bare,
        invocation=resolved,
        exit_code=exit_code,
        verdict=_apply_extract_verdict_line(stdout, stderr),
        skipped=False,
    )


def _apply_aggregate_verdict(gate: str, results: list[ApplyResult]) -> tuple[str, str]:
    """Aggregate per-check results into ``(state, summary_verdict)``.
    Three states: ``ok`` (all non-skipped passed), ``failed`` (none passed),
    ``partial`` (mixed). The verdict string surfaces the first failing
    command when applicable so a glance at the line names the breakage."""
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    total = len(results)

    if failed == 0 and passed == total - skipped:
        suffix = f" ({skipped} skipped)" if skipped else ""
        return "ok", f"{gate} gate: {passed}/{total} passed{suffix}"

    first_fail = next((r for r in results if not r.passed and not r.skipped), None)
    if passed == 0 and failed == total - skipped:
        if first_fail is not None:
            return (
                "failed",
                f"{gate} gate: 0/{total} passed; first failure: {first_fail.command} (exit={first_fail.exit_code})",
            )
        return "failed", f"{gate} gate: all checks failed"

    if first_fail is not None:
        return (
            "partial",
            f"{gate} gate: {passed} passed / {failed} failed (first failure: {first_fail.command})",
        )
    return "partial", f"{gate} gate: {passed} passed / {failed} failed"


_APPLY_LEGACY_KWARGS = frozenset({"gate", "variables", "runner", "timeout"})


def apply_constitution(
    repo_root: Path,
    constitution: Constitution,
    options: Optional[ApplyOptions] = None,
    **legacy: Any,
) -> ApplyReport:
    """Run the ``required_checks[gate]`` commands and return per-result data.

    Execution knobs live on :class:`ApplyOptions` (``gate`` / ``variables``
    / ``runner`` / ``timeout``); pass ``options=ApplyOptions(...)`` for the
    grouped, typed call shape. The four legacy keyword arguments are still
    accepted for backward compatibility and override the matching
    ``options`` field when both are given. Unknown keywords raise
    ``TypeError`` rather than vanishing into the legacy absorption.

    *variables* fills ``${symbol}`` / ``${file}`` placeholders. Anything
    unresolved skips that check (with a recorded reason) rather than
    invoking the literal string ``${symbol}``.

    *runner* is an optional callable (used by tests) with the signature
    ``runner(argv: list[str], cwd: Path, timeout: int) -> (exit_code, stdout, stderr)``.
    When None, we use ``subprocess.run`` against the system PATH.

    Never raises on bad input. Aggregates everything into an
    :class:`ApplyReport`. Implementation: split across ``_apply_*``
    helpers; this orchestrator wires gate-validation -> per-template
    execution -> verdict aggregation.
    """
    if legacy:
        unknown = set(legacy) - _APPLY_LEGACY_KWARGS
        if unknown:
            raise TypeError("apply_constitution() got unexpected keyword argument(s): " + ", ".join(sorted(unknown)))
    base = options or ApplyOptions()
    gate = legacy.get("gate", base.gate)
    variables = legacy.get("variables", base.variables)
    runner = legacy.get("runner", base.runner)
    timeout = legacy.get("timeout", base.timeout)

    if gate not in VALID_GATES:
        return ApplyReport(
            gate=gate,
            results=[],
            summary_verdict=f"unknown gate '{gate}' (valid: {', '.join(VALID_GATES)})",
            state="no_checks",
        )
    items = constitution.required_checks.get(gate) or []
    if not items:
        return ApplyReport(
            gate=gate,
            results=[],
            summary_verdict=f"no required checks declared for gate '{gate}'",
            state="no_checks",
        )
    vars_in = dict(variables or {})
    run = runner or _apply_default_runner
    results = [_apply_run_one_template(template, vars_in, run, repo_root, timeout) for template in items]
    state, verdict = _apply_aggregate_verdict(gate, results)
    return ApplyReport(gate=gate, results=results, summary_verdict=verdict, state=state)
