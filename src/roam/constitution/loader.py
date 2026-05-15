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

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from roam.commands._command_utils import bare_command_name as _bare_command_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONSTITUTION_DIR_NAME = ".roam"
CONSTITUTION_FILE_NAME = "constitution.yml"
CONSTITUTION_SCHEMA_VERSION = 1

VALID_GATES = ("before_edit", "after_edit", "before_pr")

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
        """Plain-dict view used by ``roam constitution show --json``."""
        return {
            "version": self.version,
            "metadata": self.metadata,
            "sources": self.sources,
            "required_checks": self.required_checks,
            "modes": self.modes,
            "policy": self.policy,
            "metadata_signals": self.metadata_signals,
        }


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
        return {
            "gate": self.gate,
            "results": [r.to_dict() for r in self.results],
            "summary_verdict": self.summary_verdict,
            "state": self.state,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "total": len(self.results),
        }


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
    """Parse YAML *text* into a dict. Returns ``{}`` on any error."""
    if not text or not text.strip():
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = _fallback_parse(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)

        # Pop frames whose indent is >= this line's.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            stack.append((-1, root, None, None))
        _, parent, grandparent, key_in_grand = stack[-1]

        if stripped.startswith("- ") or stripped == "-":
            # List item under `parent`. Promote parent from dict to list if needed.
            if not isinstance(parent, list):
                if grandparent is not None and key_in_grand is not None:
                    new_list: list = []
                    grandparent[key_in_grand] = new_list  # type: ignore[index]
                    # Replace top stack frame.
                    stack[-1] = (stack[-1][0], new_list, grandparent, key_in_grand)
                    parent = new_list
                else:
                    continue
            value_part = stripped[2:].strip() if stripped.startswith("- ") else ""
            if not value_part:
                new_dict: dict[str, Any] = {}
                parent.append(new_dict)
                stack.append((indent, new_dict, None, None))
            else:
                parent.append(_yaml_unscalar(value_part))
        elif ":" in stripped:
            if not isinstance(parent, dict):
                continue
            key, _, rest = stripped.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                # Container — default to dict; will get promoted on first `- ` child.
                new_container: dict = {}
                parent[key] = new_container
                stack.append((indent, new_container, parent, key))
            elif rest == "[]":
                parent[key] = []
            elif rest == "{}":
                parent[key] = {}
            else:
                parent[key] = _yaml_unscalar(rest)
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
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
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
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


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
    from roam.modes.policy import VALID_MODES, _MODE_EXTRAS

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
    *,
    with_laws: bool = True,
    with_rules: bool = True,
    force: bool = False,
) -> Path:
    """Generate ``.roam/constitution.yml`` from the current repo state.

    Auto-discovers ``AGENTS.md``, ``roam-laws.yml``/``.roam/laws.yml``,
    ``.roam/rules/`` (or top-level ``rules/``), and ``.roam/memory.jsonl``.
    Populates ``sources`` to point at what exists; absent files yield an
    absent key (NOT a stub path) so ``check`` does not flag them.

    With *with_laws* / *with_rules* set to False, the corresponding
    source is omitted even if a candidate file exists -- supports the
    "disable this substrate" workflow.

    Raises ``FileExistsError`` if the file already exists and *force*
    is False.
    """
    repo_root = Path(repo_root).resolve()
    path = constitution_path(repo_root)
    if path.exists() and not force:
        raise FileExistsError(
            f"constitution already exists at {path}; pass force=True to overwrite"
        )

    sources: dict[str, str] = {}

    agents = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["agents_md"])
    if agents:
        sources["agents_md"] = f"./{agents}"

    if with_laws:
        laws = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["laws"])
        if laws:
            sources["laws"] = f"./{laws}"

    if with_rules:
        rules = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["rules"])
        if rules:
            # Convention: point at a glob within the directory, matching
            # the YAML rule convention used elsewhere in roam.
            sources["rules"] = f"./{rules}/*.yml"

    memory = _detect_source(repo_root, DEFAULT_SOURCE_LOCATIONS["memory"])
    if memory:
        sources["memory"] = f"./{memory}"

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )

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

    path.parent.mkdir(parents=True, exist_ok=True)
    text = _dump_yaml(doc)
    # Always end with a trailing newline so the file is POSIX-clean.
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
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
    except OSError:
        return None
    data = _load_yaml(text)
    if not data:
        # Empty / unparseable -- still return a marker so callers know
        # the file is there but unreadable.
        c = Constitution(version=0, metadata={"unparseable": True}, _path=path)
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
        sources={
            str(k): str(v)
            for k, v in _as_dict(data.get("sources")).items()
            if isinstance(v, (str, int))
        },
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

    Imports lazily inside the function so this module stays import-cycle
    safe (``roam.cli`` is the LazyGroup entry point).
    """
    try:
        from roam.cli import _COMMANDS  # type: ignore

        return set(_COMMANDS.keys())
    except Exception:
        return set()


def _deprecated_commands() -> set[str]:
    try:
        from roam.cli import _DEPRECATED_COMMANDS  # type: ignore

        return set(_DEPRECATED_COMMANDS.keys())
    except Exception:
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
        except Exception:
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
    except OSError:
        pass
    return SourceStatus(name=name, path=raw_path, exists=True, state="ok", detail="")


def check_constitution(repo_root: Path, constitution: Constitution) -> CheckReport:
    """Verify that every declared source exists and every required-check
    command resolves to a real roam command.

    Returns a structured ``CheckReport`` so the caller can build an
    envelope. Never raises.
    """
    sources_out: list[SourceStatus] = []
    for name, raw in constitution.sources.items():
        sources_out.append(_source_status(repo_root, name, raw))

    known = _known_commands()
    deprecated = _deprecated_commands()
    commands_out: list[CommandStatus] = []
    for gate, items in constitution.required_checks.items():
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
            if bare in known:
                state = "ok"
                resolved = True
            elif bare in deprecated:
                state = "deprecated_command"
                resolved = True
            else:
                state = "unknown_command"
                resolved = False
            commands_out.append(
                CommandStatus(
                    gate=gate,
                    command=raw,
                    name=bare,
                    resolved=resolved,
                    state=state,
                )
            )

    # Mode allow-lists: every member must be a known command.
    mode_issues: list[dict] = []
    for mode, allowed in constitution.modes.items():
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

    n_missing_sources = sum(1 for s in sources_out if not s.exists)
    n_unparseable = 1 if constitution.metadata.get("unparseable") else 0
    n_unknown_cmds = sum(1 for c in commands_out if c.state == "unknown_command")
    n_deprecated_cmds = sum(1 for c in commands_out if c.state == "deprecated_command")
    n_mode_issues = len(mode_issues)

    issues_total = (
        n_missing_sources + n_unparseable + n_unknown_cmds + n_mode_issues
    )

    if n_unparseable:
        state = "missing"
        ok = False
        verdict = "constitution is unparseable -- re-run `roam constitution init --force`"
    elif issues_total == 0 and n_deprecated_cmds == 0:
        state = "ok"
        ok = True
        verdict = (
            f"constitution is healthy "
            f"({len(sources_out)} source(s), "
            f"{len(commands_out)} required-check(s) across "
            f"{len(constitution.required_checks)} gate(s))"
        )
    else:
        state = "partial"
        ok = False
        bits: list[str] = []
        if n_missing_sources:
            bits.append(f"{n_missing_sources} missing source(s)")
        if n_unknown_cmds:
            bits.append(f"{n_unknown_cmds} unknown command(s)")
        if n_deprecated_cmds:
            bits.append(f"{n_deprecated_cmds} deprecated command(s)")
        if n_mode_issues:
            bits.append(f"{n_mode_issues} mode allow-list issue(s)")
        verdict = "constitution issues: " + ", ".join(bits)

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
    """Substitute ``${name}`` placeholders. Returns (resolved, missing_vars)."""
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


def apply_constitution(
    repo_root: Path,
    constitution: Constitution,
    *,
    gate: str = "before_edit",
    variables: Optional[dict[str, str]] = None,
    runner: Optional[Any] = None,
    timeout: int = 120,
) -> ApplyReport:
    """Run the ``required_checks[gate]`` commands and return per-result data.

    *variables* fills ``${symbol}`` / ``${file}`` placeholders. Anything
    unresolved skips that check (with a recorded reason) rather than
    invoking the literal string ``${symbol}``.

    *runner* is an optional callable (used by tests) with the signature
    ``runner(argv: list[str], cwd: Path, timeout: int) -> (exit_code, stdout, stderr)``.
    When None, we use ``subprocess.run`` against the system PATH.

    Never raises. Aggregates everything into an :class:`ApplyReport`.
    """
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

    def _default_runner(argv: list[str], cwd: Path, t: int):
        # If the caller didn't override, exec the command as a subprocess.
        # The first token is typically ``roam`` -- on Windows this resolves
        # via PATHEXT; on POSIX it just needs to be in PATH.
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

    run = runner or _default_runner

    results: list[ApplyResult] = []
    for template in items:
        bare = _bare_command_name(template)
        resolved, missing = _substitute_placeholders(template, vars_in)
        if missing:
            results.append(
                ApplyResult(
                    command=bare,
                    invocation=template,
                    exit_code=0,
                    verdict="",
                    skipped=True,
                    skip_reason=f"missing variables: {', '.join(sorted(set(missing)))}",
                )
            )
            continue

        argv = _split_invocation(resolved)
        if not argv:
            results.append(
                ApplyResult(
                    command=bare,
                    invocation=template,
                    exit_code=2,
                    verdict="empty invocation after substitution",
                    skipped=False,
                )
            )
            continue

        exit_code, stdout, stderr = run(argv, repo_root, timeout)
        # Best-effort verdict surface: prefer first non-empty stdout line,
        # fall back to stderr's first non-empty line.
        verdict_line = ""
        for src in (stdout, stderr):
            if not src:
                continue
            for line in src.splitlines():
                line = line.strip()
                if line:
                    verdict_line = line[:200]
                    break
            if verdict_line:
                break

        results.append(
            ApplyResult(
                command=bare,
                invocation=resolved,
                exit_code=exit_code,
                verdict=verdict_line,
                skipped=False,
            )
        )

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    total = len(results)

    if failed == 0 and passed == total - skipped:
        state = "ok"
        verdict = (
            f"{gate} gate: {passed}/{total} passed"
            + (f" ({skipped} skipped)" if skipped else "")
        )
    elif passed == 0 and failed == total - skipped:
        state = "failed"
        # Surface the first failing command in the verdict.
        first_fail = next((r for r in results if not r.passed and not r.skipped), None)
        if first_fail is not None:
            verdict = (
                f"{gate} gate: 0/{total} passed; first failure: "
                f"{first_fail.command} (exit={first_fail.exit_code})"
            )
        else:
            verdict = f"{gate} gate: all checks failed"
    else:
        state = "partial"
        first_fail = next((r for r in results if not r.passed and not r.skipped), None)
        if first_fail is not None:
            verdict = (
                f"{gate} gate: {passed} passed / {failed} failed"
                f" (first failure: {first_fail.command})"
            )
        else:
            verdict = f"{gate} gate: {passed} passed / {failed} failed"

    return ApplyReport(
        gate=gate,
        results=results,
        summary_verdict=verdict,
        state=state,
    )
