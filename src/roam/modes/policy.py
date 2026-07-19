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

Generator-owned mode snapshots may follow newer defaults in memory only when
their recorded semantic digest still matches the on-disk block. A legacy,
malformed, or user-customized policy remains an authoritative replacement;
``roam constitution upgrade`` provides the previewable migration path.

Migration mode lives between ``safe_edit`` and ``autonomous_pr`` as
the home for DB migration commands (BACKLOG R16 spec). Since W37.1
the constitution loader materialises its default modes directly from
``_MODE_EXTRAS``, so a fresh ``roam constitution init`` emits all four
modes including ``migration``. A customized constitution that omits a
higher mode inherits only its declared lower-mode permissions; it never gains
the baked-in defaults merely because a key is absent.
"""

from __future__ import annotations

import os
import sys
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
        # commands lists the repo's own runnable commands (reads manifests
        # only — package.json/Makefile/justfile/pyproject); pure read-side.
        "commands",
        # docs-index scans local Markdown planning memos for orphan files +
        # broken local links (index-free; reads files only, no edits) — pure
        # read-side, same risk profile as `commands`.
        "docs-index",
        # observability-opt scans source for debug-print / weak-logging shape;
        # same pure read-side profile as agent-opt (--persist writes only the
        # local findings registry).
        "observability-opt",
        "describe",
        "impact",
        "fan",
        "preflight",
        "deps",
        "uses",
        "doctor",
        "health",
        # verify-imports is a pure index/source diagnostic. It opens the index
        # read-only and never writes source, index, evidence, or configuration.
        "verify-imports",
        "tour",
        "service-report",  # client report generator; reads the graph + writes a report artifact, no source edits (sibling of tour/describe)
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
        # blame-reviewers ranks suggested reviewers for a diff by git-blame
        # lines-added per author (reads indexed git history; read-only, no
        # edits) — same profile as the pr-risk reviewer field it was extracted from.
        "blame-reviewers",
        # 13.8.0 — read-side analysis commands: `cycle-break` recommends the smallest
        # extraction that breaks each import cycle (reads source, writes
        # nothing); `vue-emits` lists a Vue component's declared/used emits;
        # the vulnerability ingestion/reachability builders live in safe_edit
        # because they update index tables. Same profile as `describe`/`deps`.
        "cycle-break",
        "vue-emits",
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
        "profile-import",
        "workflow",
        # W1289 — both are pure DB reads:
        #   `weather` ranks files by churn × complexity (open_db readonly=True;
        #   capability metadata declares side_effect=False).
        #   `why` explains a symbol's role/reach/criticality (open_db
        #   readonly=True; networkx compute on graph snapshot).
        "weather",
        "why",
        # Roam Guard read-only family (Wave 11-20). All open the DB / load
        # bundles in readonly mode; the only writes are confined to the
        # safe_edit members below (guard-init, guard-clean, guard-pr).
        "guard-diff",
        "guard-doctor",
        "guard-history",
        "guard-rules",
        "proof-bundle",
        "verdict",
        "verification-contract",
        # W40 — compile-stats reads .roam/compile-runs.jsonl; no edits.
        "compile-stats",
        # Mixed groups remain queryable in read_only, while the dispatch gate
        # raises their mutating subcommands to safe_edit.
        # W56 — compile-cache: stats/vanilla-stats read; clear/evict/build write.
        "compile-cache",
        # S2-lite — compile-daemon: status reads; start/stop mutate process and
        # repo-local daemon state.
        "compile-daemon",
        # Pre-existing untracked alias surfaced by Wave 11-20 ceiling tightening:
        # `vulns` is a read-only vulnerability summary (alias of vuln-map family).
        "vulns",
        # 2026-06-05 — compiler-diagnostic + inspection family. Commands with USER-DIRECTED write
        # flags (compiler-health --emit-guard-findings, envelope-diff
        # --update-baseline) are classified one tier up in safe_edit instead.
        #   compiler-corpus  — analyzes a saved prompt corpus (read-only)
        #   dispatch-trace   — classifier path + per-probe fire/skip (read-only)
        #   magic-numbers    — AST/tree-sitter unnamed-constant scan (read-only)
        #   calc-inventory   — AST/tree-sitter computed-field + formula scan (read-only)
        #   calc-probe       — executes FIXED rounding snippets in local runtimes
        #                      (python/node/php) on tie inputs; no repo reads/writes
        #                      beyond an optional inventory scan (sibling of bench-compile)
        #   at               — show code at FILE:LINE + enclosing symbol (DB read)
        "compiler-corpus",
        "dispatch-trace",
        "magic-numbers",
        "calc-inventory",
        "calc-probe",
        # calc-golden audit/check are read-side; extract requires --out and the
        # dispatch gate raises that artifact-writing form to safe_edit.
        "calc-golden",
        # Default traversal is a graph read. --import-report and
        # --merge-imported update coverage tables and escalate at dispatch.
        "coverage-gaps",
        "at",
        # 2026-06-08 — `cycles` is a pure read-only graph query (Tarjan SCCs of
        # the symbol graph; open_db readonly=True), the focused sibling of
        # `clusters` / `layers`. Classifying it here keeps the unclassified
        # ceiling at 152 after the command was added (W248 precedent: classify
        # the new command rather than raise the ceiling).
        "cycles",
        # Enforcement-completeness audit (2026-07-17): every remaining
        # registered capability below explicitly declares side_effect=False
        # and destructive=False. Classifying the complete read surface removes
        # the legacy fail-closed gap where a freshly generated constitution
        # could permanently hide valid diagnostics at every authority tier.
        "adrs",
        "adversarial",
        "affected",
        "affected-tests",
        "ai-ratio",
        "ai-readiness",
        "alerts",
        "algo",
        "api",
        "api-changes",
        "api-drift",
        "architecture-drift",
        "audit",
        "batch-search",
        "bisect",
        "breaking",
        "brief",
        "capabilities",
        "causal-graph",
        "changelog",
        "check-rules",
        "churn",
        "closure",
        "clusters",
        "codeowners",
        "compare",
        "congestion",
        "coupling",
        "cut",
        "dashboard",
        "debt",
        "delete-check",
        "dev-profile",
        "dict-consistency",
        "disambiguate",
        "doc-staleness",
        "docs-coverage",
        "dogfood-aggregate",
        "drift",
        "effects",
        "endpoints",
        "entry-points",
        "evidence-diff",
        "evidence-doctor",
        "flag-dead",
        "fn-coupling",
        "forecast",
        "graph-stats",
        "idempotency",
        "index-stats",
        "intent",
        "invariants",
        "layers",
        "lsp",
        "math",
        "module",
        "onboard",
        "oracle",
        "orchestrate",
        "orphan-routes",
        "owner",
        "partition",
        "path-coverage",
        "patterns",
        "postmortem",
        "py-modern",
        "py-types",
        "pytest-fixtures",
        "recommend",
        "refs",
        "relate",
        "report",
        "risk",
        "safe-delete",
        "safe-zones",
        "schema",
        "secrets",
        "semantic-diff",
        "side-effects",
        "simulate-departure",
        "sketch",
        "spectral",
        "split",
        "suggest-reviewers",
        "supply-chain",
        "syntax-check",
        "test-gaps",
        "test-impact",
        "test-map",
        "test-pyramid",
        "tx-boundaries",
        "visualize",
    },
    "safe_edit": {
        "diff",
        "critique",
        "pr-bundle",
        # Post-edit proof maintenance. Verify may refresh stale index rows and
        # append run evidence, but it does not edit source files; the normal
        # safe_edit loop must be able to autofire it. It remains unavailable
        # in read_only and is inherited by migration/autonomous_pr.
        "verify",
        # These commands always mutate repo-local state, index tables, invoke a
        # metered external model, or materialize evidence. They previously sat
        # in read_only under a "local writes do not count" exception that
        # contradicted this module's no-writes contract.
        "bench-compile",
        "compile",
        "savings",
        "savings-backfill",
        "vuln-map",
        "vuln-reach",
        # Enforcement-readiness audit (2026-07-17). These capabilities all
        # declare side effects, but their writes are bounded to normal edit
        # workflow state: repo-local evidence/config, generated artifacts, or
        # caller-requested source/test scaffolds. Keeping them explicit avoids
        # an unclassified fail-closed denial while preserving read_only.
        "budget",
        "agents-md",
        "article-12-check",
        "audit-trail-verify",
        "auth-gaps",
        "boundary",
        "bus-factor",
        "capsule",
        "ci-setup",
        "clones",
        "compatibility",
        "complexity",
        "conventions",
        "dark-matter",
        "dead",
        "digest",
        "duplicates",
        "eval-retrieve",
        "evidence-oscal",
        "fitness",
        "fingerprint",
        "fleet",
        "graph-diff",
        "graph-export",
        "hotspots",
        "index-export",
        "ingest-trace",
        "lease",
        "llm-smells",
        "memory",
        "missing-index",
        "n1",
        "orphan-imports",
        "over-fetch",
        "reachability-triage",
        "rules",
        "rules-suggest",
        "sbom",
        "skill-generate",
        "smells",
        "snapshot",
        "suppress",
        "taint",
        "test-hermeticity",
        "test-scaffold",
        "trend",
        "trends",
        "triage",
        "vibe-check",
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
        # 2026-06-05 — compiler-diagnostic commands whose DEFAULT run is
        # read-only but which carry a USER-DIRECTED write flag (same basis as
        # audit-trail-conformance-check/--sarif-output and rules-validate/--fix
        # above). Classified at the most-conservative tier across invocations.
        #   compiler-health  — telemetry dashboard; --emit-guard-findings PATH writes
        #   envelope-diff    — diff two envelopes; --update-baseline DIR writes
        "compiler-health",
        "envelope-diff",
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
        # Roam Guard write-side family (Wave 11-20):
        #   guard-init  — creates .roam/ + bundle dir + optional rules stub.
        #   guard-clean — atomic rewrite of .roam/verdict-log.jsonl.
        #   guard-pr    — composes proof bundle + appends ONE log line +
        #                 optional GH Check POST. Writes confined to .roam/
        #                 artifacts; never touches index.db schema.
        "guard-init",
        "guard-clean",
        "guard-pr",
    },
    "migration": {
        "migration-plan",
        "migration-safety",
        # Destructive index maintenance and automated source rewrites use the
        # migration tier. Preview/default paths inherit the strictest behavior
        # because the current policy classifies Click groups/commands rather
        # than individual flags and subcommands.
        "clean",
        "index-import",
        "reset",
        "stale-refs",
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
        # These commands either compose autonomous PR analysis or mutate
        # integration state outside ordinary repo-local evidence (git hooks or
        # editor/MCP configuration). Preview modes inherit the strictest path.
        "dogfood",
        "hooks",
        "mcp-setup",
        "metrics-push",
        "pre-commit",
        # Note: ``commit`` was a phantom verb here — roam itself does
        # not run git commits. Removed in W37.1 once materialisation
        # surfaced it via the constitution check. ``pre-commit`` is now
        # deliberately classified above at autonomous_pr because installation
        # writes outside ordinary repo-local evidence.
        "attest",
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

# Experimental commands are absent from the static CLI surface and therefore
# must not be emitted into a fresh constitution while their feature flag is
# off. They still need an intentional runtime tier under the baked-in policy.
# Constitution-managed repos remain authoritative and must opt these verbs in
# explicitly. SPN is propose-only with respect to the consumer tree, but its
# ``apply`` subcommand can execute a caller-supplied validation command while
# applying candidate text in a throwaway worktree, so read_only is too weak.
_CONDITIONAL_MODE_MINIMUMS: dict[str, str] = {
    "sibling-patch": "safe_edit",
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModePolicy:
    """Materialised allow-list for one agent mode."""

    name: str
    allowed_commands: frozenset[str] = field(default_factory=frozenset)
    source: str = "default"  # "default" | "constitution" | "env" | "file"


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
    cumulative lists. For an unproven/customized policy, an absent higher mode
    inherits only the preceding declared permissions. This preserves the
    cumulative invariant without silently granting baked-in defaults.
    """
    try:
        from roam.constitution.loader import effective_constitution_modes, load_constitution
    except ImportError as exc:
        sys.stderr.write(f"[modes.policy] optional constitution loader unavailable: {exc}\n")
        return None

    try:
        constitution = load_constitution(repo_root)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"[modes.policy] constitution policy unavailable: {exc}\n")
        return None
    if constitution is None:
        return None

    # Generated snapshots track new defaults only while their recorded
    # semantic digest still matches the declared block. Legacy, malformed, or
    # customized constitutions remain authoritative and receive no implicit
    # permissions; ``roam constitution upgrade`` is their explicit path.
    declared: dict[str, list[str]] = effective_constitution_modes(constitution)
    if not declared:
        return None

    out: dict[str, set[str]] = {}
    inherited: set[str] = set()
    for mode in VALID_MODES:
        explicit: set[str] = set()
        for item in declared.get(mode, []):
            bare = _bare_command_name(item)
            if bare:
                explicit.add(bare)
        inherited = inherited | explicit
        out[mode] = set(inherited)

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
    except OSError as exc:
        sys.stderr.write(f"[modes.policy] active_mode read failed: {exc}\n")
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
    resolved = DEFAULT_MODE
    resolved_source = "default"
    mode_sources = (
        ("explicit", lambda: mode_name),
        ("env", lambda: os.environ.get(ENV_VAR, "").strip()),
        ("file", lambda: get_active_mode(repo_root)),
    )
    for source, load_mode in mode_sources:
        candidate = load_mode()
        if candidate in VALID_MODES:
            resolved = candidate
            resolved_source = source
            break

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

    conditional_minimum = _CONDITIONAL_MODE_MINIMUMS.get(bare)
    if conditional_minimum is not None and "constitution" not in mode.source:
        active_index = VALID_MODES.index(mode.name)
        required_index = VALID_MODES.index(conditional_minimum)
        if active_index >= required_index:
            return True, f"'{bare}' allowed in {mode.name} mode (conditional {conditional_minimum} minimum)"
        return (
            False,
            (f"'{bare}' not allowed in {mode.name} mode; run `roam mode {conditional_minimum}` to enable it"),
        )

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

    # The legacy taxonomy is intentionally incomplete. Under the baked-in
    # defaults, preserve read-side diagnostics by consulting their declarative
    # capability metadata only after proving the command has no explicit tier.
    # A repo constitution remains authoritative: omission there is an
    # intentional denial, not permission to fall back to metadata.
    uses_constitution = "constitution" in mode.source or any(
        "constitution" in policy.source for policy in policies.values()
    )
    if not uses_constitution and _declared_read_only_command(bare):
        return True, f"'{bare}' allowed in {mode.name} mode as a declared read-only diagnostic"

    return (
        False,
        (
            f"'{bare}' not in any mode's allow-list "
            f"(active mode: {mode.name}) — check the command spelling "
            f"or add it to your constitution's modes block"
        ),
    )


def _declared_read_only_command(command_name: str) -> bool:
    """Return whether a registered command explicitly declares no writes.

    Imports stay lazy so ordinary classified decisions retain the mode
    substrate's existing cold-start behavior. Missing or malformed metadata is
    never treated as read-only.
    """
    try:
        import importlib

        from roam.cli import _command_target

        target = _command_target(command_name)
        if target is None:
            return False
        module_name, attr_name = target
        command = getattr(importlib.import_module(module_name), attr_name)
        capability = getattr(command, "__roam_capability__", None)
        return bool(
            capability is not None
            and getattr(capability, "side_effect", True) is False
            and getattr(capability, "destructive", True) is False
        )
    except Exception:  # noqa: BLE001 - unknown metadata must not grant authority
        return False
