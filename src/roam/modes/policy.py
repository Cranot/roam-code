"""Mode-policy resolution and persistence (R16).

Four canonical agent modes:

  * ``read_only``     — explore, inspect, advise (no edits, no writes)
  * ``safe_edit``     — read-only + diff/critique/pr-bundle review surfaces
  * ``migration``     — safe_edit + DB migration planning + plan apply
  * ``autonomous_pr`` — migration + commit/attest/pr-prep/pr-analyze surface

Modes are CUMULATIVE: every command allowed at a lower mode is also
allowed at every higher mode. This mirrors the ``+command`` notation
documented in the BACKLOG (R16) and the constitution loader's
``_default_modes()``.

Resolution priority (highest wins):

  1. Explicit ``mode_name`` argument (CLI flag).
  2. ``ROAM_AGENT_MODE`` env var.
  3. ``.roam/active_mode`` file (sticky session state).
  4. Default: ``"safe_edit"``.

The constitution loader is consumed (not modified) — if
``.roam/constitution.yml`` exists and declares a ``modes:`` block, we
materialise the allow-list from that block. Otherwise we fall back to
``DEFAULT_MODE_POLICIES`` defined below.

Migration mode lives between ``safe_edit`` and ``autonomous_pr`` as
the home for DB migration commands (BACKLOG R16 spec). Since W37.1
the constitution loader materialises its default modes directly from
``_MODE_EXTRAS``, so a fresh ``roam constitution init`` emits all four
modes including ``migration``. If a repo's constitution omits
``migration`` entirely, we still synthesise it from
``DEFAULT_MODE_POLICIES`` (see ``_materialise_from_constitution``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.atomic_io import atomic_write_text
from roam.commands._command_utils import bare_command_name as _bare_command_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_MODES: tuple[str, ...] = ("read_only", "safe_edit", "migration", "autonomous_pr")
DEFAULT_MODE: str = "safe_edit"

ACTIVE_MODE_FILE = "active_mode"  # lives under .roam/
ENV_VAR = "ROAM_AGENT_MODE"

# Per-mode "extras" — what each mode ADDS on top of the previous. The
# materialised policy is the cumulative union (see ``_materialise()``).
_MODE_EXTRAS: dict[str, set[str]] = {
    "read_only": {
        "search",
        "search-semantic",
        "retrieve",
        "context",
        "understand",
        # agent-opt analyzes roam's OWN tool descriptions + envelopes; pure
        # read-side surface, no edits (the optional --persist writes only to
        # the local findings registry, same risk profile as `findings`).
        "agent-opt",
        "describe",
        "impact",
        "fan",
        "preflight",
        "deps",
        "uses",
        "doctor",
        "health",
        "tour",
        "next",
        "explain-command",
        "surface",
        "file",
        "symbol",
        "grep",
        "refs-text",
        "history-grep",
        "minimap",
        "map",
        "metrics",
        "trace",
        "complete",
        "db-check",
        "help-search",
        "ask",
        "mode",
        "intent-check",
        # W107 — demoted from safe_edit. Both are pure DB queries with no
        # edit semantics: `findings` is a read-side surface over the central
        # findings registry (all subcommands open the DB with readonly=True
        # and never mutate); `x-lang` lists cross-language bridges by
        # reading `files`/`symbols`. Same risk profile as `search` /
        # `describe` / `fan` — belongs at read_only, not safe_edit.
        "findings",
        "x-lang",
        # W1288 — all three are read-only:
        #   `why-fail` / `why-slow` open the DB with readonly=True for
        #   diagnostic narration of test failures / slow tests.
        #   `workflow` enumerates ask-workflow recipes (pure metadata,
        #   no DB touch — composes other commands without executing them).
        "why-fail",
        "why-slow",
        "workflow",
        # W1289 — both are pure DB reads:
        #   `weather` ranks files by churn × complexity (open_db readonly=True;
        #   capability metadata declares side_effect=False).
        #   `why` explains a symbol's role/reach/criticality (open_db
        #   readonly=True; networkx compute on graph snapshot).
        "weather",
        "why",
    },
    "safe_edit": {
        "diff",
        "critique",
        "pr-bundle",
        "annotate",
        "annotations",
        "permit",
        "guard",
        "plan",
        "hover",
        "diagnose",
        # W26.4 surfaced — read-only inspection commands missing from taxonomy.
        # `timeline`/`stats` are pure DB reads (no FS writes).
        # `audit-trail-conformance-check` writes only via user-directed --sarif-output.
        # `rules-validate` writes only via user-supplied positional path under --fix.
        "timeline",
        "stats",
        "audit-trail-conformance-check",
        "rules-validate",
        # W248 — `ws` is a Click GROUP with 7 subcommands. Classified at
        # the group level (most-conservative tier across subcommands):
        #   read-only:  ws status, ws understand, ws health, ws context,
        #               ws trace          (all open the workspace DB with
        #                                  readonly=True)
        #   writes:     ws init           (creates .roam-workspace.json +
        #                                  workspace DB, calls upsert_repo)
        #               ws resolve        (clear_cross_edges +
        #                                  build_cross_repo_edges +
        #                                  upsert_repo)
        # Group-level safe_edit because writes are confined to a NEW
        # workspace artifact (.roam-workspace.json + workspace DB) — they
        # never touch a repo's main `.roam/index.db` schema, so migration
        # is the wrong tier. Per-subcommand split (read-only for
        # status/understand/health/context/trace; safe_edit for
        # init/resolve) is possible if/when the mode policy gains
        # subcommand-path granularity — left as a future refinement.
        "ws",
        # W1289 — `watch` is a daemon that re-indexes on filesystem events
        # (open_db readonly=False at cmd_watch.py:191). It writes to the
        # main `.roam/index.db` like `init`/`index`, but it's not a
        # bootstrap-deadlock path (an agent can still `init` instead), so
        # it lives in safe_edit rather than `_MODE_ALWAYS_ALLOWED`.
        "watch",
    },
    "migration": {
        "migration-plan",
        "migration-safety",
        # Note: ``validate-plan`` / ``apply-plan`` are MCP-only tools
        # (see roam.mcp_server) — they are not CLI commands and so are
        # NOT listed here. The W37.1 lint
        # (``test_mode_extras_entries_are_real_commands``) enforces this.
        "simulate",
        "mutate",
        "plan-refactor",
        "suggest-refactoring",
    },
    "autonomous_pr": {
        "pr-prep",
        "pr-analyze",
        "pr-replay",
        "pr-risk",
        "pr-diff",
        "pr-comment-render",
        # Note: ``commit`` was a phantom verb here — roam itself does
        # not run git commits. Removed in W37.1 once materialisation
        # surfaced it via the constitution check. The ``pre-commit``
        # hook-installer is intentionally left UN-classified (it writes
        # outside ``.roam/``); add deliberately to a mode if the
        # workflow needs it.
        "attest",
        "verify",
        "verify-imports",
        "cga",
        "agent-plan",
        "agent-context",
        "agent-score",
        "agent-export",
        "replay",
        # W26.4 surfaced — write .roam/ runtime state.
        # `laws mine --out` accepts `.roam/laws.yml` (and `laws check` writes
        # auto_log records to the active run ledger).
        # `constitution init/apply` writes `.roam/constitution.yml`.
        # `audit-trail-export --finalize` appends to `.roam/audit-trail.jsonl`
        # (the default --output path is user-directed, but --finalize mutates
        # `.roam/` runtime state regardless of --output).
        # `runs start/log/end` writes `.roam/runs/<id>.jsonl` (HMAC-chained
        # event ledger; surfaced by W26.4's full-loop perf test).
        "laws",
        "constitution",
        "audit-trail-export",
        "runs",
    },
}


def _materialise(extras_map: dict[str, set[str]]) -> dict[str, set[str]]:
    """Return cumulative allow-lists from per-mode extras.

    The order of ``VALID_MODES`` determines the inheritance chain.
    """
    out: dict[str, set[str]] = {}
    cumulative: set[str] = set()
    for mode in VALID_MODES:
        cumulative = cumulative | extras_map.get(mode, set())
        out[mode] = set(cumulative)
    return out


# Cumulative defaults — what every mode allows when no constitution speaks.
DEFAULT_MODE_POLICIES: dict[str, set[str]] = _materialise(_MODE_EXTRAS)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModePolicy:
    """Materialised allow-list for one agent mode."""

    name: str
    allowed_commands: frozenset[str] = field(default_factory=frozenset)
    source: str = "default"  # "default" | "constitution" | "env" | "file"

    def allows(self, command: str) -> bool:
        return _bare_command_name(command) in self.allowed_commands


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _materialise_from_constitution(
    repo_root: Path,
) -> Optional[dict[str, set[str]]]:
    """Materialise the cumulative allow-list from ``.roam/constitution.yml``.

    Returns ``None`` if no constitution exists OR the constitution has no
    ``modes:`` block. The constitution's ``modes`` lists are treated as
    REPLACEMENTS (not extras), because the loader's default already emits
    cumulative lists. If a mode is absent from the constitution, we fall
    back to the hardcoded default for that mode so a partial constitution
    never produces a partially-empty policy.
    """
    try:
        from roam.constitution.loader import load_constitution
    except Exception:
        return None

    try:
        constitution = load_constitution(repo_root)
    except Exception:
        return None
    if constitution is None:
        return None

    declared: dict[str, list[str]] = dict(constitution.modes or {})
    if not declared:
        return None

    out: dict[str, set[str]] = {}
    for mode in VALID_MODES:
        if mode in declared and declared[mode]:
            out[mode] = {_bare_command_name(item) for item in declared[mode] if item}
        else:
            # Mode absent from constitution -> use baked default so the
            # caller sees a complete policy.
            out[mode] = set(DEFAULT_MODE_POLICIES[mode])

    return out


def _active_mode_file(repo_root: Path) -> Path:
    return Path(repo_root) / ".roam" / ACTIVE_MODE_FILE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_modes(repo_root: Optional[Path] = None) -> dict[str, ModePolicy]:
    """Return ``{mode_name: ModePolicy}`` for every valid mode."""
    policies_map: dict[str, set[str]] = {}
    source = "default"
    if repo_root is not None:
        from_constitution = _materialise_from_constitution(repo_root)
        if from_constitution is not None:
            policies_map = from_constitution
            source = "constitution"
    if not policies_map:
        policies_map = {m: set(DEFAULT_MODE_POLICIES[m]) for m in VALID_MODES}

    out: dict[str, ModePolicy] = {}
    for mode in VALID_MODES:
        allowed = policies_map.get(mode) or set()
        out[mode] = ModePolicy(
            name=mode,
            allowed_commands=frozenset(allowed),
            source=source,
        )
    return out


def get_active_mode(repo_root: Path) -> Optional[str]:
    """Read ``.roam/active_mode`` if present. Returns the mode name or ``None``.

    An invalid mode name in the file is treated as missing (returns ``None``)
    rather than silently mapping to the default — the caller can then warn.
    """
    path = _active_mode_file(repo_root)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw in VALID_MODES:
        return raw
    return None


def set_active_mode(repo_root: Path, mode_name: str) -> Path:
    """Persist *mode_name* to ``.roam/active_mode``.

    Raises ``ValueError`` if *mode_name* is not in ``VALID_MODES``.
    Returns the path written.
    """
    if mode_name not in VALID_MODES:
        raise ValueError(f"unknown mode '{mode_name}' (valid: {', '.join(VALID_MODES)})")
    path = _active_mode_file(repo_root)
    # Atomic write: a torn .roam/active_mode would parse to an unknown
    # mode and ``get_active_mode`` would silently fall back to the
    # default (Pattern-2). Temp-file + os.replace keeps the prior mode
    # intact on crash.
    atomic_write_text(path, mode_name + "\n")
    return path


def resolve_mode(
    repo_root: Path,
    mode_name: Optional[str] = None,
) -> ModePolicy:
    """Resolve the active :class:`ModePolicy`.

    Priority:
      1. Explicit ``mode_name`` (CLI flag).
      2. ``ROAM_AGENT_MODE`` env var.
      3. ``.roam/active_mode`` file.
      4. Default: ``safe_edit``.

    If a higher-priority source names an UNKNOWN mode, we fall through
    to the next source so a typo can never lock an agent out — but we
    record the resolved source on the returned policy.
    """
    resolved_source = "default"
    resolved: Optional[str] = None

    if mode_name and mode_name in VALID_MODES:
        resolved = mode_name
        resolved_source = "explicit"
    if resolved is None:
        env_val = os.environ.get(ENV_VAR, "").strip()
        if env_val in VALID_MODES:
            resolved = env_val
            resolved_source = "env"
    if resolved is None:
        file_val = get_active_mode(repo_root)
        if file_val in VALID_MODES:
            resolved = file_val
            resolved_source = "file"
    if resolved is None:
        resolved = DEFAULT_MODE
        resolved_source = "default"

    policies = list_modes(repo_root)
    base = policies.get(resolved) or ModePolicy(
        name=resolved,
        allowed_commands=frozenset(DEFAULT_MODE_POLICIES[resolved]),
        source="default",
    )
    # Preserve allow-list source ("constitution" vs "default") but tag
    # how *this resolution* happened by overriding the source field.
    return ModePolicy(
        name=base.name,
        allowed_commands=base.allowed_commands,
        source=resolved_source if base.source == "default" else f"{resolved_source}+{base.source}",
    )


def check_command_allowed(
    repo_root: Path,
    command_name: str,
    mode: Optional[ModePolicy] = None,
) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for *command_name* under the active mode.

    When not allowed, ``reason`` names the active mode and suggests the
    lowest mode that WOULD allow the command (or notes that no mode
    allows it — likely a typo).
    """
    if mode is None:
        mode = resolve_mode(repo_root)
    bare = _bare_command_name(command_name)
    if not bare:
        return False, "empty command name"
    if bare in mode.allowed_commands:
        return True, f"'{bare}' allowed in {mode.name} mode"

    # Find the lowest mode that DOES allow it, so the reason can suggest
    # an upgrade path.
    policies = list_modes(repo_root)
    upgrade_to: Optional[str] = None
    for candidate in VALID_MODES:
        if bare in policies[candidate].allowed_commands:
            upgrade_to = candidate
            break

    if upgrade_to:
        return (
            False,
            (f"'{bare}' not allowed in {mode.name} mode; run `roam mode {upgrade_to}` to enable it"),
        )
    return (
        False,
        (
            f"'{bare}' not in any mode's allow-list "
            f"(active mode: {mode.name}) — check the command spelling "
            f"or add it to your constitution's modes block"
        ),
    )
