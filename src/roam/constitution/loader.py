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

import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from roam.atomic_io import atomic_write_text
from roam.commands._command_utils import bare_command_name as _bare_command_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONSTITUTION_DIR_NAME = ".roam"
CONSTITUTION_FILE_NAME = "constitution.yml"
CONSTITUTION_SCHEMA_VERSION = 1

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
            "summary_verdict": self.summary_verdict,
        }


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
        share this invariant. Centralising it here prevents each caller
        from re-defining how skipped checks differ from passing checks -
        reviewed 2026-07-03.
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
    lines: list[str] = []
    pad = "  " * indent
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(v, dict):
                if not v:
                    lines.append(f"{pad}{k}: {{}}")
                else:
                    lines.append(f"{pad}{k}:")
                    lines.append(_fallback_dump(v, indent + 1))
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{pad}{k}: []")
                else:
                    lines.append(f"{pad}{k}:")
                    lines.append(_fallback_dump(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
    elif isinstance(doc, list):
        for item in doc:
            if isinstance(item, dict):
                lines.append(f"{pad}-")
                lines.append(_fallback_dump(item, indent + 1))
            elif isinstance(item, list):
                lines.append(f"{pad}-")
                lines.append(_fallback_dump(item, indent + 1))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    else:
        lines.append(f"{pad}{_yaml_scalar(doc)}")
    return "\n".join(filter(None, lines))


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

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    doc: dict[str, Any] = {
        "version": CONSTITUTION_SCHEMA_VERSION,
        "metadata": {
            "name": _project_name(repo_root),
            "description": "Constitution for AI agents working on this codebase",
            "generated_at": now,
            "generated_by": "roam constitution init",
        },
        "sources": sources,
        "required_checks": _default_required_checks(),
        "modes": _default_modes(),
        "policy": _default_policy(),
        "metadata_signals": _default_metadata_signals(),
    }

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
    return path


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


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
        print(
            f"[constitution] cannot read {path}: {exc}",
            file=sys.stderr,
        )
        return None
    data = _load_yaml(text)
    if not data:
        # Empty / unparseable -- still return a marker so callers know
        # the file is there but unreadable. Stamp the parse-error reason
        # when _load_yaml left one, so the diagnostic envelope can name
        # WHY the file failed to parse instead of conflating it with the
        # empty-file case (Pattern-2 lineage discipline).
        meta: dict[str, Any] = {"unparseable": True}
        if _last_yaml_parse_error:
            meta["parse_error"] = _last_yaml_parse_error
        elif not text.strip():
            meta["reason"] = "empty_file"
        c = Constitution(version=0, metadata=meta, _path=path)
        return c

    def _as_dict(v: Any) -> dict:
        return v if isinstance(v, dict) else {}

    def _as_list_of_str(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(item) for item in v if isinstance(item, (str, int))]

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


def _source_status(repo_root: Path, name: str, raw_path: str) -> SourceStatus:
    """Check a single source. Globs (e.g. ``*.yml``) match if ANY file matches."""
    p = _resolve_source_path(repo_root, raw_path)
    raw = str(raw_path)
    # Glob handling.
    if any(ch in raw for ch in "*?["):
        try:
            # Resolve glob against repo_root for robustness.
            rel = raw
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
        print(
            f"[constitution] optional source empty-file probe failed for {raw_path}: {exc}",
            file=sys.stderr,
        )
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


def _build_check_verdict(
    *,
    sources: list[SourceStatus],
    commands: list[CommandStatus],
    mode_issues: list[dict[str, str]],
    gate_count: int,
    unparseable: bool,
) -> tuple[bool, str, str]:
    """Synthesize the aggregate state, ok flag, and human verdict."""
    n_missing_sources = sum(1 for s in sources if not s.exists)
    n_unparseable = 1 if unparseable else 0
    n_unknown_cmds = sum(1 for c in commands if c.state == "unknown_command")
    n_deprecated_cmds = sum(1 for c in commands if c.state == "deprecated_command")
    n_mode_issues = len(mode_issues)

    issues_total = n_missing_sources + n_unparseable + n_unknown_cmds + n_mode_issues

    if unparseable:
        return (
            False,
            "missing",
            "constitution is unparseable -- re-run `roam constitution init --force`",
        )
    if issues_total == 0 and n_deprecated_cmds == 0:
        verdict = (
            f"constitution is healthy "
            f"({len(sources)} source(s), "
            f"{len(commands)} required-check(s) across "
            f"{gate_count} gate(s))"
        )
        return True, "ok", verdict

    bits: list[str] = []
    if n_missing_sources:
        bits.append(f"{n_missing_sources} missing source(s)")
    if n_unknown_cmds:
        bits.append(f"{n_unknown_cmds} unknown command(s)")
    if n_deprecated_cmds:
        bits.append(f"{n_deprecated_cmds} deprecated command(s)")
    if n_mode_issues:
        bits.append(f"{n_mode_issues} mode allow-list issue(s)")
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
    ok, state, verdict = _build_check_verdict(
        sources=sources_out,
        commands=commands_out,
        mode_issues=mode_issues,
        gate_count=len(constitution.required_checks),
        unparseable=bool(constitution.metadata.get("unparseable")),
    )

    return CheckReport(
        ok=ok,
        state=state,
        sources=sources_out,
        commands=commands_out,
        mode_issues=mode_issues,
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
