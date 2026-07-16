"""MCP (Model Context Protocol) server for roam-code.

Exposes roam codebase-comprehension commands as structured MCP tools
so that AI coding agents can query project structure, health, dependencies,
and change-risk through a standard tool interface.

Usage:
    roam mcp                    # stdio (for Claude Code, Cursor, etc.)
    roam mcp --transport sse    # SSE on localhost:8000
    roam mcp --transport streamable-http  # Streamable HTTP on localhost:8000
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import stat as _stat_mod
import subprocess
import sys
import time as _time
import warnings
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import click
from click.testing import CliRunner as _CliRunner

from roam.ask.workflow import workflow_metadata_for_recipe
from roam.cli import _COMMANDS  # W907 verified: no cycle exists; see tests/test_w907_cycle_hedge_audit.py
from roam.observability import (
    log_swallowed,
)  # "Make fallback chains loud" — surface swallowed exceptions under ROAM_VERBOSE

_FASTMCP_IMPORT_ERROR: str | None = None
try:
    with warnings.catch_warnings(), contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore")
        from fastmcp import Context as _Context
        from fastmcp import FastMCP
except Exception as exc:
    _Context = None
    FastMCP = None
    _FASTMCP_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"

try:
    from mcp.shared.exceptions import McpError as _McpError
except ImportError:
    # Older `mcp` packages may not expose McpError; the sampling error
    # tuple will degrade to the transport/timeout classes below.
    _McpError = None  # type: ignore[misc,assignment]

# Exception types that MCP sampling is expected to raise on transient or
# capability-missing failures. Caught and reported as llm_skip_reason.
# All other exceptions propagate so programming bugs are not swallowed.
_EXPECTED_SAMPLING_ERRORS: tuple[type[BaseException], ...] = ((_McpError,) if _McpError is not None else ()) + (
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)


try:
    from mcp.types import ToolAnnotations as _ToolAnnotations
except ImportError:
    # Expected-missing on older `mcp` packages: ToolAnnotations is an optional
    # type the decorator stack treats as best-effort metadata. Narrow
    # `except ImportError` (not bare `Exception`) so a genuine error inside
    # mcp.types still propagates; `None` is the named absent-state sentinel.
    _ToolAnnotations = None

_TASKCONFIG_IMPORT_ERROR: str | None = None
try:
    with warnings.catch_warnings(), contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore")
        from fastmcp.server.tasks.config import TaskConfig as _TaskConfig
except Exception as exc:  # noqa: BLE001 — optional-feature import; degrades gracefully
    # Expected-missing on older FastMCP (<2.14): TaskConfig is optional and the
    # decorator stack degrades to the no-task path. Record the import error
    # AND surface it under ROAM_VERBOSE so "old FastMCP" is distinguishable
    # from a real breakage inside fastmcp.server.tasks.
    _TaskConfig = None
    _TASKCONFIG_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"
    log_swallowed("mcp_server:import_taskconfig", exc)


def _fastmcp_unavailable_message() -> str:
    """Return the operator-facing message for an unavailable MCP transport."""
    if _FASTMCP_IMPORT_ERROR and "No module named 'fastmcp'" not in _FASTMCP_IMPORT_ERROR:
        return (
            "fastmcp transport unavailable for the MCP server.\n"
            f"import error: {_FASTMCP_IMPORT_ERROR}\n"
            "repair it with:  pip install roam-code[mcp]\n"
            "if this looks unexpected, run `roam doctor` to diagnose your install."
        )
    return (
        "fastmcp is required for the MCP server.\n"
        "install it with:  pip install roam-code[mcp]\n"
        "if this looks unexpected, run `roam doctor` to diagnose your install."
    )


# MCP-native enhancements (sampling, watcher, session, progress, completions).
# Each module is best-effort -- import failures degrade gracefully.
_MCP_EXTRAS_IMPORT_ERROR: str | None = None
try:
    from roam.mcp_extras import completions as _mcp_completions
    from roam.mcp_extras import preflight as _mcp_preflight
    from roam.mcp_extras import progress as _mcp_progress
    from roam.mcp_extras import sampling as _mcp_sampling
    from roam.mcp_extras import session as _mcp_session
    from roam.mcp_extras import watcher as _mcp_watcher
except Exception as exc:  # noqa: BLE001 — optional-feature import; degrades gracefully
    # Best-effort: the mcp_extras package (watchdog-backed watcher, sampling,
    # completions) is optional and the server degrades to the core path
    # without it. Record the import error AND surface it under ROAM_VERBOSE so
    # a real breakage (e.g. a syntax error inside mcp_extras) is
    # distinguishable from an absent optional dep rather than silently
    # disabling six feature surfaces.
    _MCP_EXTRAS_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"
    log_swallowed("mcp_server:import_mcp_extras", exc)
    _mcp_completions = None  # type: ignore[assignment]
    _mcp_preflight = None  # type: ignore[assignment]
    _mcp_progress = None  # type: ignore[assignment]
    _mcp_sampling = None  # type: ignore[assignment]
    _mcp_session = None  # type: ignore[assignment]
    _mcp_watcher = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Tool presets — named sets of tools exposed to agents.
# Default: "core" (15 tools + `roam_expand_toolset` meta-tool = 16 total).
# The dogfood-firing surface: 7 flagship tools targeted for selection
# lift + 8 tools observed firing in the 2026-05-24 audit. Shrunk from
# 57 in the dogfood wave (per Intervention A of the
# dogfood-next-interventions design memo). Tools that left core are
# in `_WORKFLOW_TOOLS` below
# and remain part of the specialised presets (review / refactor /
# debug / architecture) so power users keep their full surface area.
# Authoritative count via `python -m roam.surface_counts` (mcp.core_tools).
# Override: ROAM_MCP_PRESET=review|refactor|debug|architecture|full
# Legacy: ROAM_MCP_LITE=0 maps to "full" preset.
# ---------------------------------------------------------------------------

_CORE_TOOLS = {
    # Empirical winners (2026-05-23 A/B, 25+ runs, 3-run variance) — these
    # fire reliably and earn their always-loaded slot:
    "roam_ask",  # natural-language dispatcher; replaces Grep+Read
    "roam_understand",  # codebase briefing; replaces Glob exploration
    "roam_search_symbol",  # symbol lookup; replaces Bash:grep
    "roam_uses",  # reference lookup; replaces Bash:grep
    "roam_prepare_change",  # pre-edit safety gate; bundles preflight+context+effects
    "roam_diagnose_issue",  # root-cause triage for failing symbols
    "roam_batch_search",  # multi-symbol search (up to 10 in 1 call) [-69 to -79% tokens]
    "roam_coupling",  # file/module coupling [-84% tokens, -70% time — biggest single win]
    "roam_deps",  # file imports + importers; paired with coupling
    "roam_grep",  # index-aware grep with reachability annotation
    "roam_fetch_handle",  # handle-pattern companion (Pattern 6a)
    "roam_alerts",  # lightweight health alerts
    "roam_dead_code",  # deletion-candidate surface
    "roam_taint",  # OpenVEX-shaped taint findings
    "roam_file_info",  # file skeleton; the Read-displacement target
    "roam_metrics",  # per-symbol metric vector
    # Removed 2026-05-24 (empirical losers, 25+ A/B runs):
    #   - roam_critique: model bypasses without git diff piped in; CLI still works
    #   - roam_complexity_report: 3/3 variance runs LOST vs vanilla; deny-busted hook also blocks it
}

# Workflow tools — the surface that USED to live in core (pre-2026-05-24
# preset shrink) but moved to opt-in to tighten the default prompt.
# Still bundled into the specialised presets below (review / refactor /
# debug / architecture) so power users on those presets keep their full
# analytical surface. Access from core via `roam_expand_toolset` or by
# setting `ROAM_MCP_PRESET=full`.
_WORKFLOW_TOOLS = {
    # Compound bundles
    "roam_explore",
    "roam_review_change",
    # Batch ops
    "roam_batch_get",
    # Comprehension extras
    "roam_complete",
    "roam_context",
    # Daily workflow
    "roam_preflight",
    "roam_diff",
    "roam_pr_risk",
    "roam_affected_tests",
    "roam_impact",
    "roam_syntax_check",
    # Code quality
    "roam_health",
    "roam_diagnose",
    "roam_trace",
    # Python-pivot
    "roam_py_types",
    "roam_py_modern",
    # Retrieval + auxiliary
    "roam_retrieve",
    "roam_fleet_plan",
    # Boolean precondition oracles
    "roam_oracle_symbol_exists",
    "roam_oracle_route_exists",
    "roam_oracle_is_test_only",
    "roam_oracle_is_reachable_from_entry",
    "roam_oracle_is_clone_of",
    # LLM-augmented taint classification
    "roam_taint_classify",
    # Machine-readable tool catalog
    "roam_catalog",
    # Agent-actionable wrappers for previously CLI-only signals
    "roam_timeline",
    "roam_test_impact",
    "roam_disambiguate",
    "roam_why_fail",
    # Roam Agent Review + Cloud Lite engines
    "roam_pr_analyze",
    "roam_pr_comment_render",
    "roam_metrics_push",
    "roam_audit_trail_verify",
    "roam_audit_trail_export",
    "roam_audit_trail_conformance_check",
    "roam_rules_validate",
    "roam_dogfood",
    # Telemetry + plan validator
    "roam_session_metrics",
    "roam_validate_plan",
    # Situation-keyed compound entry points
    "roam_for_new_feature",
    "roam_for_bug_fix",
    "roam_for_refactor",
    "roam_for_security_review",
}

_PRESETS: dict[str, set[str]] = {
    "core": _CORE_TOOLS.copy(),
    "review": _CORE_TOOLS
    | _WORKFLOW_TOOLS
    | {
        "roam_breaking_changes",
        "roam_pr_diff",
        "roam_effects",
        "roam_adversarial_review",
        "roam_budget_check",
        "roam_attest",
        "roam_rules_check",
        "roam_weather",
        "roam_debt",
        "roam_symbol",
        "roam_algo",
        "roam_secrets",
        "roam_docs_coverage",
    },
    "refactor": _CORE_TOOLS
    | _WORKFLOW_TOOLS
    | {
        "roam_simulate",
        "roam_closure",
        "roam_mutate",
        "roam_generate_plan",
        "roam_suggest_refactoring",
        "roam_plan_refactor",
        "roam_get_invariants",
        "roam_cut_analysis",
        "roam_fingerprint",
        "roam_relate",
        "roam_symbol",
        "roam_visualize",
        "roam_pytest_fixtures",
    },
    "debug": _CORE_TOOLS
    | _WORKFLOW_TOOLS
    | {
        "roam_effects",
        "roam_path_coverage",
        "roam_bisect_blame",
        "roam_forecast",
        "roam_vuln_map",
        "roam_vuln_reach",
        "roam_ingest_trace",
        "roam_runtime_hotspots",
        "roam_relate",
        "roam_symbol",
        "roam_algo",
        "roam_pytest_fixtures",
    },
    "architecture": _CORE_TOOLS
    | _WORKFLOW_TOOLS
    | {
        "roam_visualize",
        "roam_tour",
        "roam_dark_matter",
        "roam_repo_map",
        "roam_simulate",
        "roam_fingerprint",
        "roam_orchestrate",
        "roam_capsule_export",
        "roam_cut_analysis",
        "roam_forecast",
        "roam_algo",
        "roam_symbol",
        "roam_relate",
        "roam_agent_export",
    },
    # Compliance preset — focused tool set for AI-governance evidence
    # workflows (SOC 2 CC8.1, ISO 42001, internal AI policy). Covers
    # everything an auditor needs to verify an AI-assisted codebase
    # via roam: pre-change checks, patch verification, taint analysis,
    # SBOM, and code-graph attestation.
    "compliance": {
        # Pre-change checks (read)
        "roam_preflight",
        "roam_uses",
        "roam_impact",
        # Patch verification (read)
        "roam_critique",
        "roam_diff",
        # Security / supply chain (read + emit)
        "roam_taint",
        "roam_taint_classify",
        "roam_secrets",
        "roam_supply_chain",
        "roam_sbom",
        # Attestation chain (emit + verify)
        "roam_attest",
        "roam_cga_emit",
        "roam_cga_verify",
    },
    # compile-code curated surface -- EXACTLY the tools compile-code's
    # ``wire claude --mcp`` pre-approves (compile-code/cli.py
    # ``CURATED_MCP_TOOLS``). Tightens the server-side surface from "core"
    # (16) down to the curated graph tools so the visible MCP surface == the
    # pre-approved allow-list (plus the always-on ``roam_expand_toolset``
    # escape hatch). Like ``compliance``, a focused subset, not a core++
    # superset. MUST stay in sync with compile-code ``CURATED_MCP_TOOLS``.
    "compile-curated": {
        "roam_impact",  # blast radius of a change
        "roam_uses",  # reference lookup (replaces grep)
        "roam_affected_tests",  # tests exercising a change
        "roam_conventions",  # local conventions for a symbol/area
        "roam_coupling",  # file/module coupling
        "roam_search_semantic",  # semantic code search
        "roam_critique",  # patch-vs-graph review on a mid-task pivot
        "roam_breaking_changes",  # will this break callers, on demand
    },
    "full": set(),  # empty = all tools exposed
}

# Meta-tool is always available regardless of preset
_META_TOOL = "roam_expand_toolset"


def _resolve_preset() -> str:
    """Determine the active preset from environment variables."""
    # Explicit preset takes priority
    preset = os.environ.get("ROAM_MCP_PRESET", "").lower()
    if preset in _PRESETS:
        return preset
    # Legacy ROAM_MCP_LITE=0 maps to "full"
    lite = os.environ.get("ROAM_MCP_LITE", "1").lower()
    if lite in ("0", "false", "no"):
        return "full"
    return "core"


_ACTIVE_PRESET = _resolve_preset()
_ACTIVE_TOOLS = _PRESETS[_ACTIVE_PRESET]


# ---------------------------------------------------------------------------
# Wave C1 (W767) — compat profile env-vars.
#
# Three env-vars under the ``ROAM_MCP_COMPAT_*`` prefix wire client-compat
# shims through the ``@_tool`` registration path. The legacy
# ``_compat_profile_payload`` namespace is the *client-config-template*
# emitter (mcp-setup) and is intentionally distinct from this server-side
# runtime knob; do not merge the two namespaces (W976 lock-comment on
# ``_compat_profile_payload`` covers the inverse direction).
#
# - ``ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA`` (default ``0``). When ``1``,
#   strip ``output_schema=`` from ``@_tool`` decorations at registration
#   time. Load-bearing compat shim for Claude Code #41361 / #45839: the
#   client's ``safeParse → return null`` guard silently bails on any
#   schema mismatch on every release **2.1.88 through 2.1.107+**.
#   Mirrors j0hanz filesystem-mcp ``FS_CONTEXT_STRIP_STRUCTURED=false``
#   default. Wave A text-mirror still emits structured JSON in a
#   ``TextContent`` block; JSON-path projection still works.
# - ``ROAM_MCP_COMPAT_STRICT`` (default ``1``). When ``0``, Wave B
#   per-command schemas are advertised on ``tools/list`` but
#   server-side validation of the response payload is skipped before
#   emit. Local dev escape hatch for in-flight schema drift; never set
#   ``0`` in CI.
#
# Override precedence (LAW 11): explicit env > auto-detected client
# profile > defaults. Wave C2 will add the client-profile layer; today
# only explicit env + defaults are in play.
# ---------------------------------------------------------------------------


def _env_truthy(name: str, default: str) -> bool:
    """Parse a boolean env-var with closed truthy/falsy vocabulary.

    Truthy: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    Falsy: everything else (including unset → fall back to ``default``).
    """
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


_COMPAT_STRIP_OUTPUT_SCHEMA: bool = _env_truthy("ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA", "0")
_COMPAT_STRICT: bool = _env_truthy("ROAM_MCP_COMPAT_STRICT", "1")


# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

if FastMCP is not None:
    mcp = FastMCP(
        "roam-code",
        instructions=(
            "Codebase intelligence for AI coding agents. "
            "TIP: call `roam_expand_toolset` first to scope tools to your task "
            "(core / review / refactor / debug / architecture / compliance / full) — "
            "the default surface is intentionally narrow to keep the prompt tight. "
            "For multi-symbol verification use `roam_batch_get` instead of N "
            # W787: canonical MCP tool name is `roam_search_symbol` (bare `roam_search` is not registered)
            "sequential `roam_uses` / `roam_search_symbol` calls. "
            "Concurrency: tool calls are bounded (default 8 in flight, "
            "tighter for retrieve/taint_classify). When the server is at "
            "capacity, a structured envelope with error_code='RATE_LIMITED' "
            "is returned — back off and retry. Tune via "
            "ROAM_MCP_MAX_CONCURRENT / ROAM_MCP_LIMITS env vars. "
            "Pre-indexes symbols, call graphs, dependencies, architecture, and "
            "git history into a local SQLite DB. One tool call replaces 5-10 "
            "Glob/Grep/Read calls. Most tools are read-only; side-effect tools "
            "are explicitly marked."
        ),
    )
else:
    mcp = None


_REGISTERED_TOOLS: list[str] = []
# parallel registry of tool metadata so ``roam_catalog`` can
# enumerate every tool in one call without re-introspecting FastMCP.
_TOOL_METADATA: dict[str, dict] = {}

# ROADMAP A1 / W108: ``_NON_READ_ONLY_TOOLS`` is no longer a hand-maintained
# set — it is a *derived view* of ``_TOOL_METADATA[name]["read_only"]`` (any
# tool flagged ``read_only=False`` is non-read-only). The decorator is the
# single source of truth; this module-level alias is rebuilt during
# module-load finalization. The initial empty ``frozenset`` is so any early
# importer sees a valid iterable; the real derived membership replaces it
# during module-load finalization.
_NON_READ_ONLY_TOOLS: frozenset[str] = frozenset()
# ROADMAP A1 / W74: ``_DESTRUCTIVE_TOOLS`` is no longer a hand-maintained
# set — it is a *derived view* of ``_TOOL_METADATA[name]["destructive"]``,
# rebuilt at module-load after every ``@_tool`` decorator has run (see the
# block just before the "Entry point" section). This bootstrap value is an
# empty ``frozenset`` so any early importer sees a valid iterable; the real
# derived membership replaces it during module-load finalization.
_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset()
# ROADMAP A1 / W113: ``_NON_IDEMPOTENT_TOOLS`` is no longer derived from
# ``_NON_READ_ONLY_TOOLS`` — it is a *derived view* of
# ``_TOOL_METADATA[name]["idempotent"]``. Independent axis: in principle
# a read-only tool could be non-idempotent (returns a UUID etc.) though
# in current data they coincide. Decorator is single source of truth.
# Initial empty ``frozenset`` is a bootstrap; module-load finalization
# replaces it with the real derived membership.
_NON_IDEMPOTENT_TOOLS: frozenset[str] = frozenset()

# ROADMAP A1 / W99 + W107: ``_TASK_REQUIRED_TOOLS`` is no longer a
# hand-maintained set — it is a *derived view* of
# ``_TOOL_METADATA[name]["task_mode"] == "required"``, rebuilt at module-load
# after every ``@_tool`` decorator has populated ``_TOOL_METADATA`` (see the
# block just before the "Entry point" section). This bootstrap value is an
# empty ``frozenset`` so any early importer sees a valid iterable; the real
# derived membership replaces it during module-load finalization. The source
# of truth is the ``task_mode="required"`` kwarg on the ``@_tool`` decorator
# (legacy ``task_required=True`` still works via the deprecation shim).
#
# Members (pre-collapse, locked by tests/test_task_required_tools_derived.py):
# v12.2 promoted `roam_health`, `roam_understand`, `roam_simulate` to
# required-task per MCP spec 2025-11-25 (SEP-1686). These all run >2s on
# a 14k-symbol repo — blocking the client is wrong UX. Tasks/get + cancel
# work end-to-end. roam was the first code-intel MCP server to ship this.
_TASK_REQUIRED_TOOLS: frozenset[str] = frozenset()

# ROADMAP A1 / W105 + W107: ``_TASK_OPTIONAL_TOOLS`` is no longer a
# hand-maintained set — it is a *derived view* of
# ``_TOOL_METADATA[name]["task_mode"] == "optional"``, rebuilt at module-load
# after every ``@_tool`` decorator has populated ``_TOOL_METADATA`` (see the
# block just before the "Entry point" section). This bootstrap value is an
# empty ``frozenset`` so any early importer sees a valid iterable; the real
# derived membership replaces it during module-load finalization. The source
# of truth is the ``task_mode="optional"`` kwarg on the ``@_tool`` decorator
# (legacy ``task_optional=True`` still works via the deprecation shim).
#
# Members (pre-collapse, locked by tests/test_task_optional_tools_derived.py):
# Long-running tools where task support is useful when FastMCP task extras
# exist but is not required (the dispatch falls back to a blocking call when
# the task extras aren't available).
_TASK_OPTIONAL_TOOLS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Client compatibility matrix (conformance profile baseline)
# ---------------------------------------------------------------------------

_KNOWN_INSTRUCTION_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CODEX.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
    ".cursorrules",
    ".cursor/rules/roam.mdc",
)

_CLIENT_COMPAT_PROFILES: dict[str, dict] = {
    "claude": {
        "display_name": "Claude Code",
        "instruction_precedence": ["CLAUDE.md", "AGENTS.md", "GEMINI.md", "CODEX.md"],
        "mcp_capabilities": {
            "tools": "supported",
            "resources": "supported",
            "prompts": "supported",
        },
        "remote_auth": "oauth2.1-compatible",
        "constraints": [],
    },
    "codex": {
        "display_name": "OpenAI Codex",
        "instruction_precedence": ["AGENTS.md", "CODEX.md", "CLAUDE.md", "GEMINI.md"],
        "mcp_capabilities": {
            "tools": "supported",
            "resources": "unknown",
            "prompts": "unknown",
        },
        "remote_auth": "client-dependent",
        "constraints": [],
    },
    "gemini": {
        "display_name": "Gemini CLI",
        "instruction_precedence": ["GEMINI.md", "AGENTS.md", "CLAUDE.md", "CODEX.md"],
        "mcp_capabilities": {
            "tools": "supported",
            "resources": "unknown",
            "prompts": "unknown",
        },
        "remote_auth": "client-dependent",
        "constraints": ["config-schema-strictness"],
    },
    "copilot": {
        "display_name": "GitHub Copilot Coding Agent",
        "instruction_precedence": [
            ".github/copilot-instructions.md",
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
        ],
        "mcp_capabilities": {
            "tools": "supported",
            "resources": "unsupported",
            "prompts": "unsupported",
        },
        "remote_auth": "limited",
        "constraints": ["tools-only"],
    },
    "vscode": {
        "display_name": "VS Code Agent Mode",
        "instruction_precedence": ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "CODEX.md"],
        "mcp_capabilities": {
            "tools": "supported",
            "resources": "client-dependent",
            "prompts": "client-dependent",
        },
        "remote_auth": "client-dependent",
        "constraints": [],
    },
    "cursor": {
        "display_name": "Cursor",
        "instruction_precedence": [
            ".cursor/rules/roam.mdc",
            ".cursorrules",
            "AGENTS.md",
            "CLAUDE.md",
        ],
        "mcp_capabilities": {
            "tools": "supported",
            "resources": "unknown",
            "prompts": "unknown",
        },
        "remote_auth": "client-dependent",
        "constraints": [],
    },
}


def _detect_instruction_files(root: str = ".") -> list[str]:
    """Detect known agent instruction/config files in the project root."""
    base = Path(root)
    found: list[str] = []
    for rel in _KNOWN_INSTRUCTION_FILES:
        if (base / rel).exists():
            found.append(rel)
    return found


def _select_instruction_file(precedence: list[str], existing: list[str]) -> str:
    """Pick the highest-precedence existing instruction file, else AGENTS.md."""
    for rel in precedence:
        if rel in existing:
            return rel
    return "AGENTS.md"


# W976: loose-but-honest per W966 — merges user-controlled profile
# overrides via ``.update()``; do NOT TypedDict this return without an
# at-boundary validator. See W933 _resolved_thresholds for the canonical
# case study.
def _compat_profile_payload(profile: str, root: str = ".") -> dict:
    """Build MCP client compatibility payload for one profile or all profiles."""
    existing = _detect_instruction_files(root)

    def _build(name: str, data: dict) -> dict:
        selected = _select_instruction_file(data["instruction_precedence"], existing)
        return {
            "id": name,
            "display_name": data["display_name"],
            "instruction_precedence": data["instruction_precedence"],
            "selected_instruction_file": selected,
            "preferred_instruction_missing": selected not in existing,
            "mcp_capabilities": data["mcp_capabilities"],
            "remote_auth": data["remote_auth"],
            "constraints": data["constraints"],
        }

    if profile == "all":
        profiles = {name: _build(name, data) for name, data in _CLIENT_COMPAT_PROFILES.items()}
        return {
            "server": "roam-code",
            "compat_version": "2026-02-22",
            "detected_instruction_files": existing,
            "profiles": profiles,
        }

    data = _CLIENT_COMPAT_PROFILES[profile]
    payload = _build(profile, data)
    payload.update(
        {
            "server": "roam-code",
            "compat_version": "2026-02-22",
            "detected_instruction_files": existing,
            "profile": profile,
        }
    )
    return payload


# ---------------------------------------------------------------------------
# Output schemas — JSON Schema dicts for MCP tool return types.
# All tools default to the envelope schema; compound/core tools override.
# ---------------------------------------------------------------------------

_ENVELOPE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
            },
        },
    },
}


def _make_schema(summary_fields: dict | None = None, **payload_fields) -> dict:
    """Build a JSON Schema extending the standard envelope with custom fields."""
    summary_props: dict = {
        "verdict": {"type": "string", "description": "One-line result summary"},
    }
    if summary_fields:
        summary_props.update(summary_fields)

    props: dict = {
        "command": {"type": "string"},
        "summary": {"type": "object", "properties": summary_props},
    }
    if payload_fields:
        props.update(payload_fields)

    return {"type": "object", "properties": props}


def _strict_validate_envelope(envelope: dict, schema: dict) -> list[str]:
    """Wave C1 (W767): server-side envelope validation gate.

    Honors ``ROAM_MCP_COMPAT_STRICT`` — when ``0``, returns ``[]``
    unconditionally so optional-field absence (or any other shape
    drift) does not raise. When ``1`` (default), runs the same minimal
    structural walk the Wave B tests use: required-keys present.

    The check is intentionally narrow — we don't pull in ``jsonschema``
    here; the runtime contract is *required-keys-only*. Per-type
    validation and enum checking remain Wave B test discipline. The
    point of this hook is to give ``ROAM_MCP_COMPAT_STRICT=0`` a real
    runtime effect (a callable that can be exercised in tests + Wave
    C2 doctor) without coupling dispatch to ``jsonschema``.
    """
    if not _COMPAT_STRICT:
        return []
    errors: list[str] = []
    if not isinstance(envelope, dict):
        return [f"<root>: expected object, got {type(envelope).__name__}"]
    for required_key in schema.get("required", []):
        if required_key not in envelope:
            errors.append(f"<root>: missing required key {required_key!r}")
    props = schema.get("properties", {})
    for key, sub_schema in props.items():
        if key in envelope and isinstance(envelope[key], dict):
            for sub_required in sub_schema.get("required", []):
                if sub_required not in envelope[key]:
                    errors.append(f"{key}: missing required key {sub_required!r}")
    return errors


# -- Compound operation schemas ------------------------------------------------

_WORKFLOW_SCHEMA = {
    "type": "object",
    "description": "Workflow recipe metadata: phase, review lenses, gates, and follow-up commands.",
}

_SCHEMA_EXPLORE = _make_schema(
    {"sections": {"type": "array", "items": {"type": "string"}}},
    workflow=_WORKFLOW_SCHEMA,
    understand={"type": "object", "description": "Full codebase briefing"},
    context={"type": "object", "description": "Symbol context (when symbol provided)"},
)

_SCHEMA_PREPARE_CHANGE = _make_schema(
    {"sections": {"type": "array"}, "symbol": {"type": "string"}},
    workflow=_WORKFLOW_SCHEMA,
    preflight={"type": "object", "description": "Safety check: blast radius, tests, fitness"},
    context={"type": "object", "description": "Files and line ranges to read"},
    effects={"type": "object", "description": "Side effects of the symbol"},
)

_SCHEMA_REVIEW_CHANGE = _make_schema(
    {"sections": {"type": "array"}},
    workflow=_WORKFLOW_SCHEMA,
    pr_risk={"type": "object", "description": "Risk score and per-file breakdown"},
    breaking_changes={"type": "object", "description": "Removed/changed API signatures"},
    pr_diff={"type": "object", "description": "Structural graph delta"},
)

_SCHEMA_DIAGNOSE_ISSUE = _make_schema(
    {"sections": {"type": "array"}, "target": {"type": "string"}},
    workflow=_WORKFLOW_SCHEMA,
    diagnose={"type": "object", "description": "Root cause suspects ranked by risk"},
    effects={"type": "object", "description": "Side effects of the symbol"},
)

# -- Core tool schemas ---------------------------------------------------------

# Wave B2 (W767): specialised per-command schema for ``roam_understand``.
# Mirrors the actual envelope emitted by ``cmd_understand`` in the
# default success-path codebase-briefing mode (``--json`` without
# ``--agent-prompt`` / ``--skeleton`` sub-modes). Required is narrow —
# only ``verdict`` is emitted on every exit path (e.g. the
# ``--skeleton DIR`` empty-symbols branch at cmd_understand.py:1199 omits
# health_score / languages / files). The agent-prompt and skeleton
# branches use the same envelope shape so additional summary props are
# declared but not required.
_SCHEMA_UNDERSTAND = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "health_score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
                "files": {"type": "integer", "minimum": 0},
                "symbols": {"type": "integer", "minimum": 0},
                "languages": {"type": "integer", "minimum": 0},
                "caller_metric_definition": {"type": "string"},
                # --skeleton sub-mode (cmd_understand.py:1199-1255):
                "file_count": {"type": "integer", "minimum": 0},
                "symbol_count": {"type": "integer", "minimum": 0},
                # --agent-prompt sub-mode (cmd_understand.py:1150):
                "mode": {"type": "string", "enum": ["agent"]},
                "partial_success": {"type": "boolean"},
            },
        },
        "project": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "root": {"type": "string"},
                "files": {"type": "integer", "minimum": 0},
                "symbols": {"type": "integer", "minimum": 0},
                "edges": {"type": "integer", "minimum": 0},
            },
        },
        "tech_stack": {
            "type": "object",
            "properties": {
                "languages": {"type": "array"},
                "frameworks": {"type": "array"},
                "build": {"type": ["string", "null"]},
            },
        },
        "architecture": {
            "type": "object",
            "properties": {
                "layers": {"type": "array"},
                "layer_count": {"type": "integer", "minimum": 0},
                "entry_points": {"type": "array"},
                "key_abstractions": {"type": "array"},
                "clusters": {"type": "array"},
            },
        },
        "health_summary": {
            "type": "object",
            "properties": {
                "score": {"type": ["number", "null"]},
                "cycles": {"type": ["integer", "array"]},
                "god_components": {"type": ["integer", "array"]},
                "bottlenecks": {"type": ["integer", "array"]},
                "dead_exports": {"type": ["integer", "array"]},
                "layer_violations": {"type": ["integer", "array"]},
                "worst_issues": {"type": "array"},
            },
        },
        "conventions": {"type": "object"},
        "complexity": {"type": "object"},
        "patterns": {"type": ["array", "object"]},
        "debt_hotspots": {"type": "array"},
        "hotspots": {"type": "array"},
        "suggested_reading_order": {"type": "array"},
        "discoverable_via": {"type": ["array", "object"]},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "tour": {"type": "object"},
        # --skeleton sub-mode top-level payload:
        "directory": {"type": "string"},
        "files": {"type": ["object", "array"]},
        "file_count": {"type": "integer", "minimum": 0},
        "symbol_count": {"type": "integer", "minimum": 0},
        # --agent-prompt sub-mode top-level payload:
        "agent_prompt": {"type": "string"},
    },
}

# Wave B2 (W767): specialised per-command schema for ``roam_health``.
# Mirrors the 3 distinct exit paths in ``cmd_health``:
#   1. Empty-corpus W834 branch (cmd_health.py:1180): verdict + state +
#      partial_success + health_score=None + zeros. Stamped via Fix E.
#   2. Baseline-mode branch (cmd_health.py:990, 1027): verdict in
#      {DEGRADED, IMPROVED, REGRESSED, ...} + baseline_ref + new_findings_count.
#   3. Default scoring branch (cmd_health.py:1795): full health_score +
#      tangle_ratio + propagation_cost + issue_count + 4 issue categories.
# Gate-mode (cmd_health.py:1675) emits a 4th shape with gate_passed +
# gate_results. ``required`` is narrowed to ``verdict`` only because the
# empty-corpus branch sets health_score=None and the baseline+gate
# branches both emit health_score but other fields differ.
_SCHEMA_HEALTH = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "health_score": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 100,
                },
                "health_score_definition": {"type": "string"},
                "tangle_ratio": {"type": ["number", "null"]},
                "tangle_ratio_definition": {"type": "string"},
                "propagation_cost": {"type": ["number", "null"]},
                "algebraic_connectivity": {"type": ["number", "null"]},
                # null when numpy+scipy substrate missing; the flag disambiguates
                # "couldn't compute" from a legitimate 0.0 disconnected reading.
                "algebraic_connectivity_available": {"type": "boolean"},
                "issue_count": {"type": "integer", "minimum": 0},
                "severity": {"type": "object"},
                "category_severity": {"type": "object"},
                "actionable_cycles": {"type": "integer", "minimum": 0},
                "ignored_cycles": {"type": "integer", "minimum": 0},
                "total_cycles": {"type": "integer", "minimum": 0},
                "cycles_total": {"type": "integer", "minimum": 0},
                "cycles_actionable": {"type": "integer", "minimum": 0},
                "god_components": {"type": "integer", "minimum": 0},
                "cycles_definition": {"type": "string"},
                "god_components_definition": {"type": "string"},
                "imported_coverage_pct": {"type": ["number", "null"]},
                "imported_coverage_files": {"type": "integer", "minimum": 0},
                # Empty-corpus branch (cmd_health.py:1183):
                "state": {
                    "type": "string",
                    "enum": ["empty_corpus"],
                },
                "partial_success": {"type": "boolean"},
                # Baseline-mode branch (cmd_health.py:992, 1030):
                "reason": {"type": "string"},
                "baseline_ref": {"type": "string"},
                "baseline_taken_at": {"type": ["string", "null"]},
                "new_findings_count": {"type": "integer", "minimum": 0},
                "fixed_findings_count": {"type": "integer", "minimum": 0},
                "regressed_count": {"type": "integer", "minimum": 0},
                "score_delta": {"type": "number"},
                # Gate-mode branch (cmd_health.py:1662):
                "gate_passed": {"type": "boolean"},
                "warnings_out": {"type": "array"},
            },
        },
        "health_score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "tangle_ratio": {"type": ["number", "null"]},
        "propagation_cost": {"type": ["number", "null"]},
        "algebraic_connectivity": {"type": ["number", "null"]},
        "algebraic_connectivity_available": {"type": "boolean"},
        "issue_count": {"type": "integer", "minimum": 0},
        "severity": {"type": "object"},
        "category_severity": {"type": "object"},
        "actionable_cycles": {"type": "integer", "minimum": 0},
        "ignored_cycles": {"type": "integer", "minimum": 0},
        "total_cycles": {"type": "integer", "minimum": 0},
        "cycles_total": {"type": "integer", "minimum": 0},
        "cycles_actionable": {"type": "integer", "minimum": 0},
        "indexed_symbols": {"type": "integer", "minimum": 0},
        "imported_coverage_pct": {"type": ["number", "null"]},
        "imported_coverage_files": {"type": "integer", "minimum": 0},
        "imported_covered_lines": {"type": "integer", "minimum": 0},
        "imported_coverable_lines": {"type": "integer", "minimum": 0},
        "score_breakdown": {"type": "array"},
        "framework_filtered": {"type": "integer", "minimum": 0},
        "actionable_count": {"type": "integer", "minimum": 0},
        "utility_count": {"type": "integer", "minimum": 0},
        "cycles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "size": {"type": "integer", "minimum": 1},
                    "severity": {"type": "string"},
                    "directories": {"type": "array"},
                    "symbols": {"type": "array"},
                    "files": {"type": "array"},
                },
            },
        },
        "cycle_break_suggestions": {"type": "array"},
        "god_components": {"type": "array"},
        "bottleneck_thresholds": {"type": "object"},
        "bottlenecks": {"type": "array"},
        "layer_violations": {"type": "array"},
        "next_steps": {"type": "array"},
        "index_status": {"type": ["object", "null"]},
        # Baseline-mode top-level fields:
        "delta": {"type": "object"},
        "baseline_ref": {"type": "string"},
        "message": {"type": "string"},
        # Gate-mode top-level fields:
        "gate_results": {"type": "array"},
        # Empty-corpus branch top-level field (cmd_health.py:1206):
        "agent_contract": {"type": "object"},
    },
}

_SCHEMA_SEARCH = _make_schema(
    {"total_matches": {"type": "integer"}, "query": {"type": "string"}},
    results={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "file_path": {"type": "string"},
                "line_start": {"type": "integer"},
            },
        },
    },
)

# Wave B1 (W767): specialised per-command schema for ``roam_preflight``.
# Mirrors the actual envelope emitted by ``cmd_preflight``:
# 6 signal dimensions (blast_radius / tests / complexity / coupling /
# conventions / fitness) plus a summary that always carries
# verdict + target + risk_level. ``required`` is restricted to fields
# emitted on EVERY exit path — including ``not_found`` (where
# risk_level="UNKNOWN") — to avoid Pattern 1-variant-D schema lies.
_SCHEMA_PREFLIGHT = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict", "target", "risk_level"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "target": {"type": "string"},
                "risk_level": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"],
                },
                "symbols_checked": {"type": "integer", "minimum": 0},
                "files_checked": {"type": "integer", "minimum": 0},
                "fitness_violations": {"type": "array"},
                "risk_level_definition": {"type": "string"},
                "partial_success": {"type": "boolean"},
                "resolution": {
                    "type": "string",
                    "enum": ["symbol", "file", "unresolved", "fuzzy"],
                },
                "error": {"type": "string"},
                "alias_warnings": {"type": "array"},
            },
        },
        "blast_radius": {
            "type": "object",
            "properties": {
                "affected_symbols": {"type": "integer", "minimum": 0},
                "affected_files": {"type": "integer", "minimum": 0},
                "affected_file_list": {"type": "array"},
                "severity": {"type": "string"},
                "affected_symbols_definition": {"type": "string"},
                "affected_files_definition": {"type": "string"},
            },
        },
        "tests": {
            "type": "object",
            "properties": {
                "direct": {"type": "integer", "minimum": 0},
                "transitive": {"type": "integer", "minimum": 0},
                "colocated": {"type": "integer", "minimum": 0},
                "total": {"type": "integer", "minimum": 0},
                "test_files": {"type": "array", "items": {"type": "string"}},
                "pytest_command": {"type": ["string", "null"]},
                "severity": {"type": "string"},
            },
        },
        "complexity": {
            "type": "object",
            "properties": {
                "max_cognitive_complexity": {"type": "number"},
                "max_nesting_depth": {"type": "integer", "minimum": 0},
                "high_complexity_symbols": {"type": "array"},
                "severity": {"type": "string"},
                "complexity_definition": {"type": "string"},
            },
        },
        "coupling": {
            "type": "object",
            "properties": {
                "coupled_files": {"type": "integer", "minimum": 0},
                "missing_partners": {"type": "array"},
                "severity": {"type": "string"},
            },
        },
        "conventions": {
            "type": "object",
            "properties": {
                "violation_count": {"type": "integer", "minimum": 0},
                "violations": {"type": "array"},
                "severity": {"type": "string"},
                "majority_threshold_pct": {"type": ["number", "null"]},
                "kinds_with_majority": {"type": ["integer", "null"], "minimum": 0},
            },
        },
        "fitness": {
            "type": "object",
            "properties": {
                "rules_checked": {"type": "integer", "minimum": 0},
                "rules_failed": {"type": "integer", "minimum": 0},
                "total_violations": {"type": "integer", "minimum": 0},
                "failed_rules": {"type": "array"},
                "rule_details": {"type": "array"},
                "severity": {"type": "string"},
            },
        },
        "resolution": {
            "type": "string",
            "enum": ["symbol", "file", "unresolved", "fuzzy"],
        },
        "partial_success": {"type": "boolean"},
    },
}

_SCHEMA_CONTEXT = _make_schema(
    {"target": {"type": "string"}},
    definition={"type": "object"},
    callers={"type": "array"},
    callees={"type": "array"},
    files_to_read={"type": "array"},
)

_SCHEMA_RETRIEVE = _make_schema(
    {
        "candidates": {"type": "integer"},
        "total_candidates": {"type": "integer"},
        "budget": {"type": "integer"},
        "budget_used": {"type": "integer"},
        "k": {"type": "integer"},
        "rerank": {"type": "string"},
        "seed_count": {"type": "integer"},
    },
    task={"type": "string"},
    weights={"type": "object", "description": "Reranker weight vector"},
    seeds={"type": "array", "description": "Resolved seed symbols"},
    candidates={
        "type": "array",
        "description": "Ranked code spans with file/line/score/justifications",
    },
)

_SCHEMA_CRITIQUE = _make_schema(
    {
        "changed_files": {"type": "integer"},
        "changed_symbols": {"type": "integer"},
        "findings": {"type": "integer"},
        "high_severity": {"type": "integer"},
    },
    severity_breakdown={"type": "object"},
    findings={
        "type": "array",
        "description": "Ranked findings (severity-ordered): clones-not-edited, impact, etc.",
    },
    top_finding={"type": ["object", "null"]},
    changed_symbols={"type": "array"},
)

_SCHEMA_STALE_REFS = _make_schema(
    {
        "missing_targets": {"type": "integer"},
        "stale_refs": {"type": "integer"},
        "files_scanned": {"type": "integer"},
        "refs_checked": {"type": "integer"},
        "scan_seconds": {"type": "number"},
        "anchor_findings": {"type": "integer"},
        "fixable_count": {
            "type": "integer",
            "description": "HIGH-confidence rename hints --fix apply would act on.",
        },
        "by_kind": {
            "type": "object",
            "description": "Per-kind tally: md_inline / md_reference / html_attr / backtick / anchor.",
        },
        "by_confidence": {
            "type": "object",
            "description": "Per-confidence-band tally: HIGH / MEDIUM / LOW / NONE.",
        },
        "next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Actionable command suggestions for the agent's next move.",
        },
        "sort_by": {"type": "string"},
        "diff_base": {
            "type": "string",
            "description": "Merge-base SHA when --diff is active (otherwise absent).",
        },
    },
    targets={
        "type": "array",
        "description": (
            "Per-missing-target findings with sources, ref_count, and "
            "confidence-tagged hint (target / confidence / reason / source). "
            "Hint sources: git-history, basename, symbol-graph, anchor-similarity."
        ),
    },
)

_SCHEMA_FLEET_PLAN = _make_schema(
    {
        "agents": {"type": "integer"},
        "conflict_hotspots": {"type": "integer"},
        "overall_conflict_probability": {"type": "number"},
        "adapter": {"type": "string"},
    },
    fleet={
        "type": "object",
        "description": "Adapter-formatted fleet manifest (raw / composio / copilot).",
    },
)

# Wave B1 (W767): specialised per-command schema for ``roam_impact``.
# Mirrors the actual envelope emitted by ``cmd_impact``:
# blast-radius integers + ranked file list + indirect refs + truncation
# state. ``required`` is restricted to ``verdict`` because the not_found
# / not_in_graph / no_dependents / success branches all emit the same
# summary base shape but only the success branch carries every counter
# (Pattern 1-variant-D avoidance: don't require fields that not_found
# omits).
_SCHEMA_IMPACT = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "affected_symbols": {"type": "integer", "minimum": 0},
                "affected_files": {"type": "integer", "minimum": 0},
                "weighted_impact": {"type": "number", "minimum": 0},
                "reach_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "sf_convention_tests": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
                "partial_success": {"type": "boolean"},
                "state": {
                    "type": "string",
                    "enum": [
                        "ok",
                        "timeout",
                        "caller_cap",
                        "depth_cap",
                        "not_found",
                    ],
                },
                "limits": {
                    "type": "object",
                    "properties": {
                        "depth": {"type": ["integer", "null"]},
                        "max_callers": {"type": ["integer", "null"]},
                        "timeout_s": {"type": ["number", "null"]},
                    },
                },
                "affected_symbols_definition": {"type": "string"},
                "affected_files_definition": {"type": "string"},
                "weighted_impact_definition": {"type": "string"},
                "reach_pct_definition": {"type": "string"},
                "resolution": {
                    "type": "string",
                    "enum": ["symbol", "file", "unresolved", "fuzzy"],
                },
                "in_graph": {"type": "boolean"},
            },
        },
        "symbol": {"type": "string"},
        "affected_symbols": {"type": "integer", "minimum": 0},
        "affected_files": {"type": "integer", "minimum": 0},
        "weighted_impact": {"type": "number", "minimum": 0},
        "reach_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "direct_dependents": {
            "type": "object",
            "description": (
                "Per-edge-kind buckets of direct callers; keys are edge kinds "
                "(calls / inherits / type_ref / ...), values are arrays of "
                "{name, kind, file} records."
            ),
        },
        "affected_file_list": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "importance": {"type": "number"},
                },
            },
        },
        "sf_convention_tests": {"type": "array", "items": {"type": "string"}},
        "indirect_refs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer", "minimum": 1},
                    "match": {"type": "string"},
                },
            },
        },
        "truncated": {"type": "boolean"},
        "partial_success": {"type": "boolean"},
        "state": {"type": "string"},
        "limits": {"type": "object"},
        "resolution": {
            "type": "string",
            "enum": ["symbol", "file", "unresolved", "fuzzy"],
        },
        "tip": {"type": "string"},
    },
}

_SCHEMA_PR_RISK = _make_schema(
    {"risk_score": {"type": "number"}, "risk_level": {"type": "string"}},
    per_file={"type": "array"},
)

# Wave B4 (W767): specialised per-command schema for ``roam_timeline``.
# Mirrors the actual envelope emitted by ``cmd_timeline`` -- the 7-field
# summary (verdict + commit_count + file_path + added_total +
# removed_total + distinct_authors + top_author) + ``commits[]`` array
# of {sha,date,author,added,removed,subject} records + ``authors{}`` map
# of author->commit_count. ``required`` is intentionally narrow
# (``verdict`` only) because the no-symbol-found path emits only
# ``{verdict, commit_count: 0}`` + ``commits: []`` and omits
# ``file_path`` / ``top_author`` / etc.
_SCHEMA_TIMELINE = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"const": "timeline"},
        "summary": {
            "type": "object",
            "required": ["verdict", "commit_count"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "commit_count": {"type": "integer", "minimum": 0},
                "file_path": {
                    "type": "string",
                    "description": "Path of the file owning the resolved symbol.",
                },
                "added_total": {"type": "integer", "minimum": 0},
                "removed_total": {"type": "integer", "minimum": 0},
                "distinct_authors": {"type": "integer", "minimum": 0},
                "top_author": {
                    "type": ["string", "null"],
                    "description": (
                        "Author with the most commits in this window; ``null`` "
                        "when the file has no commits in the window."
                    ),
                },
                "partial_success": {"type": "boolean"},
            },
        },
        "commits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sha": {"type": "string", "description": "12-char short SHA."},
                    "date": {"type": "string", "description": "YYYY-MM-DD (or ``?`` if absent)."},
                    "author": {"type": "string"},
                    "added": {"type": "integer", "minimum": 0},
                    "removed": {"type": "integer", "minimum": 0},
                    "subject": {"type": "string", "description": "First line of commit message."},
                },
            },
        },
        "authors": {
            "type": "object",
            "description": "Map of author name -> commit count in the returned window.",
            "additionalProperties": {"type": "integer", "minimum": 0},
        },
    },
}

# Wave B4 (W767): specialised per-command schema for ``roam_test_impact``.
# High-leverage agent-input envelope -- this is what an agent reads to
# decide which tests to run after a code change. Mirrors the envelope
# emitted by ``cmd_test_impact``: 2-field summary (verdict + count) +
# ``changed_files[]`` (non-test source files in the diff) +
# ``tests[{file, reach_count}]`` ranked by reach. ``required`` is narrow
# (``verdict`` + ``count``) because both are guaranteed on every emit
# path (the 3 paths: no-changes / no-symbols / normal). ``changed_files``
# is omitted on the very-first no-changes path; ``tests`` is always
# emitted (possibly empty).
_SCHEMA_TEST_IMPACT = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"const": "test-impact"},
        "summary": {
            "type": "object",
            "required": ["verdict", "count"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "count": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Number of distinct test files reachable from the changed scope.",
                },
                "partial_success": {"type": "boolean"},
            },
        },
        "changed_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Non-test source files in the diff that seeded the reverse BFS. Omitted on the empty-changeset branch."
            ),
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "reach_count"],
                "properties": {
                    "file": {"type": "string", "description": "Test file path."},
                    "reach_count": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Number of changed seed symbols that transitively reach this test (higher = more relevant)."
                        ),
                    },
                },
            },
        },
    },
}

# Wave B3 (W767): one shared schema for the 5 boolean-oracle wrappers
# (``roam_oracle_symbol_exists``, ``roam_oracle_route_exists``,
# ``roam_oracle_is_test_only``, ``roam_oracle_is_reachable_from_entry``,
# ``roam_oracle_is_clone_of``) plus the ``roam_oracle_test_only`` short-name
# alias. All six share the tri-state envelope shape emitted by
# ``cmd_oracle._emit`` -- closed ``verdict`` / ``value`` / ``confidence``
# enums + ``reason_class`` taxonomy. ``command`` is the dotted form
# ``oracle:<oracle-name>`` (NOT a const here because 5 oracles share the
# schema). The top-level payload is open: each oracle stamps the query
# arguments back onto the envelope (``name``, ``path``, ``max_hops``).
_SCHEMA_ORACLE = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {
            "type": "string",
            "description": "Oracle command name, e.g. ``oracle:symbol-exists``.",
        },
        "summary": {
            "type": "object",
            "required": ["verdict", "value", "reason", "reason_class", "confidence"],
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["true", "false", "indeterminate"],
                    "description": "Tri-state answer. ``indeterminate`` when the oracle lacks data.",
                },
                "value": {
                    "type": ["boolean", "null"],
                    "description": (
                        "Machine-readable answer. ``true`` / ``false`` when provable; "
                        "``null`` when the oracle cannot decide (workspace not configured, "
                        "clone table absent, etc)."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Human-readable rationale for the answer.",
                },
                "reason_class": {
                    "type": "string",
                    "enum": [
                        "definitive_yes",
                        "definitive_no",
                        "indeterminate_workspace",
                        "indeterminate_no_data",
                        "unreachable_dead",
                        "unreachable_scaffolding",
                        "unreachable_test_only",
                        "unreachable_dynamic_import",
                    ],
                    "description": "Short tag for downstream branching (see OracleResult docstring).",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "indeterminate"],
                },
                "caller_metric_definition": {
                    "type": "string",
                    "description": (
                        "Per Pattern 3a: short label naming the caller metric "
                        "this oracle's answer was derived from (when applicable)."
                    ),
                },
            },
        },
        # Top-level arg echo -- populated by ``_emit(**extra)`` with the
        # original query inputs so agents can correlate response to call.
        "name": {"type": "string"},
        "path": {"type": "string"},
        "max_hops": {"type": "integer", "minimum": 1},
    },
}

_SCHEMA_DIFF = _make_schema(
    # Summary mirrors what cmd_diff.py emits at lines 962-974: verdict +
    # 4 integer counts + canonical risk-level pair. ``affected_symbols``
    # is the COUNT (integer), not the array — the array payloads live
    # at the top level under ``per_file`` and ``blast_radius``.
    {
        "changed_files": {"type": "integer"},
        "affected_symbols": {"type": "integer"},
        "affected_files": {"type": "integer"},
        "risk_level_canonical": {"type": "string"},
        "risk_rank": {"type": "integer"},
    },
    # Top-level mirrors cmd_diff.py:946-960 envelope_data: 4 integer
    # mirrors of the summary counts + symbols_defined + the two array
    # payloads + label + canonical risk-level pair. ``files`` was a
    # stale name that no emit path ever produced.
    label={"type": "string"},
    changed_files={"type": "integer"},
    symbols_defined={"type": "integer"},
    affected_symbols={"type": "integer"},
    affected_files={"type": "integer"},
    per_file={"type": "array"},
    blast_radius={"type": "array"},
    risk_level_canonical={"type": "string"},
    risk_rank={"type": "integer"},
)

# Wave B5 (W767): specialised per-command schema for ``roam_diagnose``.
# Mirrors the actual envelope emitted by ``cmd_diagnose`` -- summary +
# target_metrics + upstream[] + downstream[] + cochange_partners[] +
# recent_commits[] + did_you_mean[] + resolution disclosure. ``required``
# is narrowed to ``verdict`` only because cmd_diagnose has 2 distinct
# emit paths: success (everything populated) and not_found
# (state="not_found" + resolution="unresolved" + partial_success=True,
# all other fields omitted). Required suspect-item fields are narrow
# (``name`` only) because the not_found branch never emits a row but
# every fuzzy/success row does carry ``name``. The batch-mode envelope
# (``command="diagnose.batch"``) is emitted by a different code path
# the MCP wrapper does NOT trigger -- this schema covers the
# single-symbol shape only.
_SCHEMA_DIAGNOSE = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "target": {"type": "string"},
                "upstream_count": {"type": "integer", "minimum": 0},
                "downstream_count": {"type": "integer", "minimum": 0},
                "ambiguous": {"type": "boolean"},
                "caller_metric_definition": {"type": "string"},
                "complexity_definition": {"type": "string"},
                "partial_success": {"type": "boolean"},
                "state": {
                    "type": "string",
                    "enum": ["not_found"],
                },
                "resolution": {
                    "type": "string",
                    "enum": ["symbol", "file", "unresolved", "fuzzy"],
                },
            },
        },
        # Echo of the original query symbol (not_found branch).
        "symbol": {"type": "string"},
        "target_metrics": {
            "type": "object",
            "properties": {
                "complexity": {"type": "number"},
                "nesting": {"type": "integer", "minimum": 0},
                "line_count": {"type": "integer", "minimum": 0},
                "pagerank": {"type": "number"},
                "in_degree": {"type": "integer", "minimum": 0},
                "out_degree": {"type": "integer", "minimum": 0},
                "betweenness": {"type": "number"},
                "commits": {"type": "integer", "minimum": 0},
                "churn": {"type": "integer", "minimum": 0},
                "entropy": {"type": "number"},
                "health": {"type": "number"},
                "file_path": {"type": "string"},
            },
        },
        "upstream": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "location": {"type": "string"},
                    "risk_score": {"type": "number", "minimum": 0},
                    "complexity": {"type": "number"},
                    "commits": {"type": "integer", "minimum": 0},
                    "health": {"type": "number"},
                    "entropy": {"type": "number"},
                    "direction": {
                        "type": "string",
                        "enum": ["upstream", "downstream"],
                    },
                },
            },
        },
        "downstream": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "location": {"type": "string"},
                    "risk_score": {"type": "number", "minimum": 0},
                    "complexity": {"type": "number"},
                    "commits": {"type": "integer", "minimum": 0},
                    "health": {"type": "number"},
                    "entropy": {"type": "number"},
                    "direction": {
                        "type": "string",
                        "enum": ["upstream", "downstream"],
                    },
                },
            },
        },
        "cochange_partners": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "cochange_count": {"type": "integer", "minimum": 0},
                },
            },
        },
        "recent_commits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hash": {"type": "string"},
                    "author": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
        "did_you_mean": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "location": {"type": "string"},
                },
            },
        },
        "next_steps": {"type": "array"},
        "index_status": {"type": ["object", "null"]},
        "resolution": {
            "type": "string",
            "enum": ["symbol", "file", "unresolved", "fuzzy"],
        },
        "partial_success": {"type": "boolean"},
    },
}

# Wave B5 (W767): specialised per-command schema for
# ``roam_audit_trail_verify``. Mirrors the actual envelope emitted by
# ``cmd_audit_trail_verify`` -- a 3-state machine (valid / broken /
# uninitialized) with chain-anomaly issues[]. ``required`` is restricted
# to ``verdict`` + ``state`` because every exit path stamps those
# uniformly (see cmd_audit_trail_verify.py:326-337). Top-level
# ``records`` is an integer (count of records, NOT the records
# themselves) per the envelope construction at cmd_audit_trail_verify.py:346.
_SCHEMA_AUDIT_TRAIL_VERIFY = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict", "state"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                "state": {
                    "type": "string",
                    "enum": ["valid", "broken", "uninitialized"],
                },
                "partial_success": {"type": "boolean"},
                "chain_valid": {"type": "boolean"},
                "total_records": {"type": "integer", "minimum": 0},
                "issues_count": {"type": "integer", "minimum": 0},
                "first_timestamp": {"type": ["string", "null"]},
                "last_timestamp": {"type": ["string", "null"]},
                "first_actor": {"type": ["string", "null"]},
                "audit_trail_path": {"type": "string"},
            },
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["line", "issue"],
                "properties": {
                    "line": {"type": "integer", "minimum": 0},
                    "issue": {"type": "string"},
                    "expected_prev": {"type": "string"},
                    "computed_prev": {"type": "string"},
                    "timestamp": {"type": ["string", "null"]},
                    "verdict": {"type": ["string", "null"]},
                    "detail": {"type": "string"},
                },
            },
        },
        "records": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Count of records walked (NOT the records themselves). "
                "Envelope-construction parity with cmd_audit_trail_verify.py:346."
            ),
        },
    },
}

# Wave B5b (W767): specialised per-command schema for
# ``roam_audit_trail_conformance_check``. Mirrors the envelope emitted by
# ``cmd_audit_trail_conformance.audit_trail_conformance_check`` (the
# 6-check EU AI Act Article 12 readiness gate). ``required`` is narrowed
# to ``verdict`` because the no_trail branch (Fix E) sets ``score: null``
# and skips ``checks_passed``/``checks_total`` semantics. The
# ``compliance_kind: "audit_trail_chain_integrity"`` literal is the
# Pattern 3c discriminator that distinguishes this command from
# ``article-12-check`` (repo-level readiness score) per W17.2 — both
# commands publish ``compliance_kind`` + ``compliance_kind_definition``
# so consumers never confuse them. ``summary.state`` is the closed
# enum disclosing whether the 6 checks ran (``no_trail`` skips them).
# Each ``checks[]`` item carries the per-check verdict shape; an item's
# ``state`` is only emitted on the no_trail branch (``not_run``).
_SCHEMA_AUDIT_TRAIL_CONFORMANCE = {
    "type": "object",
    "required": ["command", "summary"],
    "properties": {
        "command": {"const": "audit-trail-conformance-check"},
        "summary": {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                # ``no_trail`` is the only closed-enum state emitted today
                # (Fix E disclosure: the 6 checks did not run). The
                # conformant / partial / NON-conformant branches all skip
                # ``state`` because the score field carries the verdict.
                "state": {"type": "string", "enum": ["no_trail"]},
                "partial_success": {"type": "boolean"},
                # Score is null on the no_trail branch; integer on every
                # other path. The ``null`` permission is what forces
                # ``required`` to be narrow.
                "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                "chain_compliance_score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                # W331b + W17.2 Pattern 3c — definition sidecars that
                # name the score computation + disambiguate this command
                # from article-12-check (repo-level readiness).
                "chain_compliance_score_definition": {"type": "string"},
                "compliance_kind": {
                    "type": "string",
                    "enum": ["audit_trail_chain_integrity"],
                    "description": (
                        "Pattern 3c discriminator: distinguishes this command's "
                        "chain-of-custody score from article-12-check's repo-level "
                        "readiness score (different metrics, same regulation)."
                    ),
                },
                "compliance_kind_definition": {"type": "string"},
                "checks_passed": {"type": "integer", "minimum": 0},
                "checks_total": {"type": "integer", "minimum": 0},
                "total_records": {"type": "integer", "minimum": 0},
                "audit_trail_path": {"type": "string"},
                "retention_days_required": {"type": "integer", "minimum": 0},
                "schema_reference": {"type": "string"},
                "disclaimer": {"type": "string"},
                # no_trail branch only.
                "reason": {"type": "string"},
                "fix": {"type": "string"},
            },
        },
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "passed"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": (
                            "One of the 6 Article 12 check ids: chain_integrity, "
                            "timestamp_completeness, actor_attribution, "
                            "reproducibility_metadata, verdict_and_rationale, retention."
                        ),
                        "enum": [
                            "chain_integrity",
                            "timestamp_completeness",
                            "actor_attribution",
                            "reproducibility_metadata",
                            "verdict_and_rationale",
                            "retention",
                        ],
                    },
                    "passed": {"type": "boolean"},
                    "message": {"type": "string"},
                    # ``state`` is only emitted on the no_trail branch
                    # (per-check disclosure that the predicate did not
                    # run). Closed enum so the only allowed value is the
                    # explicit not_run marker.
                    "state": {"type": "string", "enum": ["not_run"]},
                },
            },
        },
        "disclaimer": {"type": "string"},
        "schema_reference": {"type": "string"},
    },
}

# Wave B5b (W767): specialised per-command schema for ``roam_fetch_handle``.
# 3-mode dispatch (byte_slice / section / jq) per W333 v2.0.0 paginated
# handle fetch. Chose FLAT schema with ``mode`` closed enum (NOT oneOf)
# because the 3 modes share the bulk of their envelope shape — every
# emit path carries ``handle`` + ``total_size`` + ``total_keys`` — and
# the per-mode payload differences (``data`` is str vs list vs object;
# ``offset``/``end`` only on byte_slice; ``section`` only on section
# pick; ``jq`` only on jq projection) are surfaced as optional fields.
# ``required`` is narrow: ``command`` + ``summary`` + ``handle``.
# ``data`` is ``{"type": ["string", "array", "object", "null"]}`` to
# cover all 3 modes' payload variants without forcing a oneOf branch
# the agent would have to discriminate on. The ``mode`` enum
# (``byte_slice`` / ``section`` / ``jq``) drives agent dispatch.
_SCHEMA_FETCH_HANDLE = {
    "type": "object",
    "required": ["command", "summary", "handle"],
    "properties": {
        "command": {"const": "roam_fetch_handle"},
        "summary": {
            "type": "object",
            "required": ["verdict", "mode"],
            "properties": {
                "verdict": {"type": "string", "description": "One-line result summary"},
                # 3-mode closed enum drives agent dispatch — flat-schema
                # discriminator. See W333 v2.0.0 paginated handle fetch.
                "mode": {
                    "type": "string",
                    "enum": ["byte_slice", "section", "jq"],
                },
                "total_size": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Byte size of the full stored payload.",
                },
                "total_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Sorted top-level keys of the stored payload when it's a "
                        "JSON object; empty when the payload is a list / scalar."
                    ),
                },
                # byte_slice mode only.
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 0},
                "end": {"type": "integer", "minimum": 0},
                "has_more": {"type": "boolean"},
                "next_offset": {"type": ["integer", "null"], "minimum": 0},
                "partial_success": {"type": "boolean"},
                # section mode only.
                "section": {"type": "string"},
                # jq mode only.
                "jq": {"type": "string"},
            },
        },
        "handle": {
            "type": "string",
            "description": "16-char lowercase hex handle id.",
            "pattern": "^[0-9a-f]{16}$",
        },
        # byte_slice top-level fields.
        "offset": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 0},
        "total_size": {"type": "integer", "minimum": 0},
        "has_more": {"type": "boolean"},
        "next_offset": {"type": ["integer", "null"], "minimum": 0},
        # section top-level fields.
        "section": {"type": "string"},
        # jq top-level fields.
        "jq": {"type": "string"},
        # Common to all 3 modes.
        "total_keys": {"type": "array", "items": {"type": "string"}},
        # ``data`` shape varies by mode: byte_slice => string (UTF-8 text
        # slice); section => the parsed value (any JSON type) of the
        # picked top-level key; jq => the projected value (any JSON type).
        # The 4-type union covers the cartesian without a oneOf.
        "data": {"type": ["string", "array", "object", "number", "integer", "boolean", "null"]},
        # Parity-only field: when byte_slice covers offset=0 and
        # has_more=False, the wrapper also returns the parsed JSON for
        # convenience.
        "parsed": {"type": ["object", "array", "string", "number", "integer", "boolean", "null"]},
    },
}

# Wave B5b (W767): specialised per-command schema for ``roam_validate_plan``.
# Plan-status closed enum drives agent dispatch (``ok`` /
# ``needs-review`` / ``blocked``); structured per-operation ``blockers[]``
# + ``warnings[]`` + ``advice[]`` arrays let the agent triage by
# operation index. Mechanical clone of the ``_SCHEMA_PR_RISK`` shape
# per the inventory memo.
#
# BAIL drift caught (2 prongs):
# 1. The SUCCESS envelope at ``mcp_server.py:5417`` does NOT set
#    ``command`` (only the 4 error-path envelopes do). So ``command``
#    cannot be required at the root.
# 2. The ERROR envelopes (USAGE_ERROR / etc) emit ``isError`` +
#    ``error`` + ``error_code`` but NO ``summary``. So ``summary``
#    cannot be required at the root either.
# Net: ``required: []`` at the root; the schema still pins ``command``
# (when present) to the const literal AND pins the success-path
# ``summary.verdict`` to the 3-tier closed enum. Both branches validate
# under one schema without forcing a oneOf split. The producer gaps
# stay surfaced via inline comments; fixing them is outside Wave B's
# "schema follows cmd" constraint.
_SCHEMA_VALIDATE_PLAN = {
    "type": "object",
    "required": [],
    "properties": {
        "command": {"const": "roam_validate_plan"},
        "schema": {"type": "string"},
        "schema_version": {"type": "string"},
        "summary": {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                # 3-tier closed enum drives agent dispatch:
                # ``ok`` (no findings) / ``needs-review`` (warnings only)
                # / ``blocked`` (any blocker — do NOT call mutate).
                "verdict": {
                    "type": "string",
                    "enum": ["ok", "needs-review", "blocked"],
                    "description": (
                        "Plan status: ok / needs-review / blocked. ``blocked`` means do not apply the plan."
                    ),
                },
                "operations": {"type": "integer", "minimum": 0},
                "blockers_count": {"type": "integer", "minimum": 0},
                "warnings_count": {"type": "integer", "minimum": 0},
                "verdict_text": {"type": "string"},
            },
        },
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "kind", "ok"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "kind": {
                        "type": "string",
                        "description": (
                            "Operation kind from the plan: rename / move / remove / modify / add / unknown (malformed)."
                        ),
                    },
                    "ok": {"type": "boolean"},
                    "blockers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["code"],
                            "properties": {
                                "code": {"type": "string"},
                                "detail": {"type": "string"},
                                # Optional structured per-blocker fields the
                                # validators stamp (line/severity/symbol/etc.).
                                "symbol": {"type": "string"},
                                "severity": {"type": "string"},
                                "line": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                    "warnings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                        },
                    },
                    "advice": {"type": "array"},
                    "facts": {"type": "object"},
                },
            },
        },
        # Error-path fields (per-envelope error vocab).
        "error": {"type": "string"},
        "error_code": {"type": "string"},
        "hint": {"type": "string"},
        "isError": {"type": "boolean"},
    },
}

_SCHEMA_TRACE = _make_schema(
    {"source": {"type": "string"}, "target": {"type": "string"}, "hop_count": {"type": "integer"}},
    path={"type": "array"},
)

_SCHEMA_BATCH_SEARCH = _make_schema(
    {"queries_executed": {"type": "integer"}, "total_matches": {"type": "integer"}},
    results={
        "type": "object",
        "description": "Map of query -> list of matching symbols",
        "additionalProperties": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "file_path": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "pagerank": {"type": "number"},
                },
            },
        },
    },
    errors={
        "type": "object",
        "description": "Map of query -> error message (only present if some queries failed)",
        "additionalProperties": {"type": "string"},
    },
)

_SCHEMA_BATCH_GET = _make_schema(
    {"symbols_resolved": {"type": "integer"}, "symbols_requested": {"type": "integer"}},
    results={
        "type": "object",
        "description": "Map of symbol name -> symbol details dict",
        "additionalProperties": {"type": "object"},
    },
    errors={
        "type": "object",
        "description": "Map of symbol name -> error message for unresolved symbols",
        "additionalProperties": {"type": "string"},
    },
)

_SCHEMA_INIT = _make_schema(
    {
        "had_index": {"type": "boolean"},
        "created_count": {"type": "integer"},
        "skipped_count": {"type": "integer"},
    },
    created={"type": "array", "items": {"type": "string"}},
    skipped={"type": "array", "items": {"type": "string"}},
    had_index={"type": "boolean"},
    health={"type": "object"},
)

_SCHEMA_REINDEX = _make_schema(
    {
        "files": {"type": "integer"},
        "symbols": {"type": "integer"},
        "edges": {"type": "integer"},
        "cancelled": {"type": "boolean"},
    },
    files={"type": "integer"},
    symbols={"type": "integer"},
    edges={"type": "integer"},
    elapsed_s={"type": "number"},
    cancelled={"type": "boolean"},
    force={"type": "boolean"},
)


def _tool_title(name: str) -> str:
    """Convert roam tool name to a human title."""
    short = name.removeprefix("roam_").replace("_", " ")
    return short.title()


def _tool_annotations(name: str) -> dict:
    """Build MCP tool annotations with capability hints."""
    # ROADMAP A1 / W74 + W108 + W113: read ``destructive``, ``read_only``, and
    # ``idempotent`` from ``_TOOL_METADATA`` (the source of truth) rather than
    # the module-level ``_DESTRUCTIVE_TOOLS`` / ``_NON_READ_ONLY_TOOLS`` /
    # ``_NON_IDEMPOTENT_TOOLS`` sets, which are now derived views rebuilt only
    # after all ``@_tool`` decorators have run. ``_TOOL_METADATA[name]`` is
    # populated at the top of the ``@_tool`` decorator, so it is always
    # available by the time ``_tool_annotations`` is called downstream.
    meta = _TOOL_METADATA.get(name, {})
    read_only = meta.get("read_only", True)
    annotations = {
        "title": _tool_title(name),
        "readOnlyHint": read_only,
        "destructiveHint": meta.get("destructive", False),
        "idempotentHint": meta.get("idempotent", True),
        "openWorldHint": False,
    }
    # Non-core tools are lazily discoverable in clients that support this extension.
    if name not in _CORE_TOOLS and name != _META_TOOL:
        annotations["deferLoading"] = True
    return annotations


def _resolve_task_mode(
    name: str,
    task_mode: Literal["required", "optional"] | None,
    task_required: bool | None,
    task_optional: bool | None,
) -> Literal["required", "optional"] | None:
    """Collapse the deprecated two-bool task shape onto the canonical enum.

    ROADMAP A1 / W107: ``task_mode`` wins when both are supplied; warn so the
    caller updates. ``stacklevel=3`` points the warning at the ``@_tool`` call
    site (helper -> ``_tool`` -> caller).
    """
    if task_required is not None or task_optional is not None:
        if task_mode is None:
            if task_required:
                task_mode = "required"
            elif task_optional:
                task_mode = "optional"
        else:
            import warnings as _w

            _w.warn(
                f"_tool({name!r}): task_mode={task_mode!r} overrides legacy "
                f"task_required/task_optional kwargs — drop the legacy kwargs",
                DeprecationWarning,
                stacklevel=3,
            )
    # Defensive: a tool can't be both required AND optional. With the enum,
    # this is impossible by construction; with the legacy bools it required
    # a separate test. Pin it here so a future re-introduction of the bool
    # shape can't silently regress.
    if task_required and task_optional:
        raise ValueError(
            f"_tool({name!r}): task_required and task_optional are disjoint — "
            f'pick one (preferably task_mode="required" or "optional")'
        )
    return task_mode


def _parse_tool_doc(doc: str) -> tuple[str, list[str]]:
    """Extract the ``WHEN TO USE:`` line and up to 3 ``>>> roam`` examples."""
    when_to_use = ""
    examples: list[str] = []
    if doc:
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("WHEN TO USE:"):
                when_to_use = stripped.split(":", 1)[1].strip()
                break
        for line in doc.splitlines():
            ls = line.strip()
            if ls.startswith(">>>") and "roam" in ls:
                examples.append(ls[3:].strip())
            if len(examples) >= 3:
                break
    return when_to_use, examples


def _build_registration_kwargs(
    name: str,
    effective_description: str,
    output_schema: dict | None,
    task_mode: Literal["required", "optional"] | None,
) -> dict:
    """Assemble the FastMCP ``mcp.tool(...)`` kwargs for one tool."""
    kwargs: dict = {"name": name, "title": _tool_title(name)}
    # ``effective_description`` was computed BEFORE the fastmcp-presence
    # gate (W296). It carries the cold-start hint when the tool is gated
    # by the cold-start guard, or the original description verbatim
    # otherwise. Using it here keeps the catalog-visible description
    # (``_TOOL_METADATA``) and the MCP-protocol description
    # (this ``kwargs["description"]``) in lockstep.
    if effective_description:
        kwargs["description"] = effective_description
    schema = output_schema if output_schema is not None else _ENVELOPE_SCHEMA
    # Wave C1 (W767): compat shim for Claude Code #41361 / #45839.
    # ``ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA=1`` drops the declared
    # schema entirely so the client's ``safeParse → return null``
    # guard no longer silently bails. Wave A text-mirror still
    # ships structured JSON in a ``TextContent`` block; agents that
    # JSON-path-project still get the payload. Captured into
    # ``_TOOL_METADATA[name]["output_schema_stripped"]`` so
    # ``roam mcp doctor`` (Wave C2) can surface the runtime state.
    # The ``output_schema_stripped`` sidecar on ``_TOOL_METADATA`` is
    # populated above the ``if mcp is None`` gate in ``_tool`` so the
    # catalog surface stays honest in fastmcp-less environments; here we
    # only decide what rides on the wire.
    if _COMPAT_STRIP_OUTPUT_SCHEMA:
        kwargs["output_schema"] = None
    else:
        kwargs["output_schema"] = schema
    kwargs["annotations"] = _tool_annotations(name)

    # ROADMAP A1 / W99 + W105 + W107: ``task_mode`` is the canonical 3-way
    # enum captured from the decorator kwarg. The legacy
    # ``_TASK_REQUIRED_TOOLS`` / ``_TASK_OPTIONAL_TOOLS`` sets are derived
    # views of ``task_mode`` rebuilt at module-load finalization.
    if task_mode is not None:
        # Metadata fallback for clients even when FastMCP task extras are absent.
        kwargs["meta"] = {"taskSupport": task_mode}
        if _TaskConfig is not None:
            kwargs["task"] = _TaskConfig(mode=task_mode)
    return kwargs


def _register_with_fallbacks(fn, kwargs: dict):
    """Register ``fn`` with FastMCP, degrading kwargs for older versions.

    Attempts, in order:
    1) Full feature set
    2) Drop task support when tasks extras aren't installed
    3) Legacy FastMCP without output_schema/annotations/title/meta/task
    """
    attempts = [dict(kwargs)]
    if "task" in kwargs:
        no_task = dict(kwargs)
        no_task.pop("task", None)
        attempts.append(no_task)
    legacy = dict(kwargs)
    for key in ("output_schema", "annotations", "title", "meta", "task"):
        legacy.pop(key, None)
    attempts.append(legacy)

    last_error: Exception | None = None
    seen: set[tuple[str, ...]] = set()
    for attempt in attempts:
        signature = tuple(sorted(attempt.keys()))
        if signature in seen:
            continue
        seen.add(signature)
        try:
            return mcp.tool(**attempt)(fn)
        except (TypeError, ImportError, ValueError) as exc:
            # ValueError covers the FastMCP 2.14+ guard that rejects
            # a sync function with task config enabled (the legacy
            # fallback attempt — which strips ``task`` — will retry
            # without it and succeed).
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    return fn


def _should_register_tool(name: str) -> bool:
    """Return whether ``name`` should be exposed on the active MCP surface."""
    if name == _META_TOOL:
        return True
    if _ACTIVE_TOOLS and name not in _ACTIVE_TOOLS:
        return False
    return True


def _build_tool_metadata(
    name: str,
    fn,
    description: str,
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    task_mode: str | None,
    version: str,
) -> dict:
    """Build the ``_TOOL_METADATA`` entry for one ``@_tool`` registration.

    Plain Python state, populated UNCONDITIONALLY at decorator-top (before the
    fastmcp-presence check) so ``roam_catalog`` and the version surface work
    even in environments where fastmcp isn't installed (tests, CLI-only
    installs) — metadata is orthogonal to whether the MCP transport can serve.
    Extracted from ``_tool.decorator`` to keep the registration sequence flat
    (W-brain-method); the field set and the derived ``task_required`` /
    ``task_optional`` bools now live in one named, single-purpose place.
    """
    when_to_use_pre, examples_pre = _parse_tool_doc(fn.__doc__ or "")
    return {
        "name": name,
        "title": _tool_title(name),
        "description": description,
        "when_to_use": when_to_use_pre,
        "examples": examples_pre,
        "core": name in _CORE_TOOLS,
        # ROADMAP A1 / W108: ``read_only`` now flows from the decorator
        # kwarg into ``_TOOL_METADATA`` directly. The module-level
        # ``_NON_READ_ONLY_TOOLS`` is built as a derived view of the
        # NEGATION of this flag after all ``@_tool`` decorators have run.
        "read_only": read_only,
        # ROADMAP A1 / W74: ``destructive`` now flows from the decorator
        # kwarg into ``_TOOL_METADATA`` directly. The module-level
        # ``_DESTRUCTIVE_TOOLS`` is built as a derived view of this flag
        # after all ``@_tool`` decorators have run.
        "destructive": destructive,
        # ROADMAP A1 / W113: ``idempotent`` is a first-class axis stored in
        # ``_TOOL_METADATA``. Independent from ``read_only`` (in current
        # data they coincide — destructive tools are all non-idempotent —
        # but the semantic distinction matters: a read-only tool can be
        # non-idempotent when it returns a fresh UUID or timestamp on
        # every call). The module-level ``_NON_IDEMPOTENT_TOOLS`` is
        # built as a derived view of the NEGATION of this flag after all
        # ``@_tool`` decorators have run.
        "idempotent": idempotent,
        # ROADMAP A1 / W99 + W105 + W107: ``task_mode`` is the canonical
        # 3-way enum stored in ``_TOOL_METADATA``. The legacy boolean
        # ``task_required`` / ``task_optional`` fields are DERIVED from it
        # for back-compat with the W99/W105 derived-view tests and any
        # downstream consumer that introspects ``_TOOL_METADATA``. The
        # module-level ``_TASK_REQUIRED_TOOLS`` / ``_TASK_OPTIONAL_TOOLS``
        # sets are built as derived views of ``task_mode`` after all
        # ``@_tool`` decorators have run.
        "task_mode": task_mode,
        "task_required": task_mode == "required",
        "task_optional": task_mode == "optional",
        # Version stamp — agents can compare against a cached value
        # to detect schema drift without re-enumerating tools.
        # Bump when the input or output schema for ``name`` changes.
        "version": version,
    }


def _prepare_tool_body(
    name: str,
    fn,
    description: str,
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    task_mode: str | None,
    version: str,
):
    """Populate catalog metadata and apply wrappers shared by every surface.

    This runs before the FastMCP-presence gate so CLI-only installs,
    preset-filtered in-process callers, and registered MCP tools all get the
    same metadata, cold-start guard, receipt wrapper, exception envelope, and
    compat sidecars.
    """
    _TOOL_METADATA[name] = _build_tool_metadata(
        name,
        fn,
        description,
        read_only=read_only,
        destructive=destructive,
        idempotent=idempotent,
        task_mode=task_mode,
        version=version,
    )

    if _mcp_preflight is not None:
        effective_description = _mcp_preflight.maybe_decorate_description(name, description or "")
    else:
        effective_description = description or ""
    if effective_description:
        _TOOL_METADATA[name]["description"] = effective_description

    fn = _wrap_with_receipt(name, fn)
    fn = _wrap_with_cold_start_guard(name, fn)
    fn = _wrap_with_exception_envelope(name, fn)
    _TOOL_METADATA[name]["output_schema_stripped"] = bool(_COMPAT_STRIP_OUTPUT_SCHEMA)
    return fn, effective_description


def _tool(
    name: str,
    description: str = "",
    output_schema: dict | None = None,
    *,
    version: str = "1.0.0",
    destructive: bool = False,
    read_only: bool = True,
    idempotent: bool = True,
    task_mode: Literal["required", "optional"] | None = None,
    task_required: bool | None = None,  # DEPRECATED: pass task_mode="required"
    task_optional: bool | None = None,  # DEPRECATED: pass task_mode="optional"
):
    """Register an MCP tool if it belongs to the active preset.

    Automatically sets ``deferLoading`` in MCP tool annotations:
    - Core tools and the meta-tool: ``deferLoading`` is absent (always loaded)
    - All other tools: ``deferLoading=True`` (loaded on-demand via Tool Search)

    This enables Claude Code's Tool Search feature to achieve ~85% context
    reduction by only loading non-core tool descriptions when needed.

    Per audit A7 / R8: every registered tool carries a ``version`` string
    (semver). When a tool's input/output schema changes — meaning agents
    holding cached schemas may misbehave — bump the version. ``roam_catalog``
    surfaces the current version so agents can detect and refresh stale
    schema caches without re-enumerating the whole surface.

    ROADMAP A1 / W74: ``destructive=True`` marks tools that mutate persistent
    state (filesystem writes, schema migrations, …). The legacy module-level
    ``_DESTRUCTIVE_TOOLS`` set is now a *derived view* of this flag — see the
    module-load block after the last ``@_tool`` declaration. Adding a new
    destructive tool only requires ``destructive=True`` here; do not add the
    name to a separate hand-maintained set.

    ROADMAP A1 / W99 + W105 + W107: the ``task_mode`` kwarg is the canonical
    3-way enum describing how a tool interacts with MCP task mode:

    - ``task_mode="required"`` — tool MUST run under MCP task mode (non-blocking;
      tasks/get + cancel work end-to-end). These are tools that exceed ~2s on a
      real-size repo, so blocking the client is wrong UX.
    - ``task_mode="optional"`` — tool opts INTO task mode when FastMCP task extras
      are available, but falls back to a blocking call when they aren't.
    - ``task_mode=None`` (default) — blocking call only.

    The legacy module-level ``_TASK_REQUIRED_TOOLS`` and ``_TASK_OPTIONAL_TOOLS``
    sets are *derived views* of ``task_mode`` (see the module-load block after
    the last ``@_tool`` declaration). Adding a new task-aware tool only requires
    setting ``task_mode=...`` here; do not add the name to a separate set.

    W107 collapsed the legacy two-bool shape (``task_required`` / ``task_optional``)
    into this single enum. The dispatch logic was always fundamentally 3-way; the
    enum encodes that structurally so a tool cannot be marked both required and
    optional by construction. The two bool kwargs are RETAINED as DEPRECATED
    back-compat aliases — passing either issues a ``DeprecationWarning`` when
    combined with an explicit ``task_mode``, and otherwise maps onto it
    transparently.
    """

    # ROADMAP A1 / W107: resolve the deprecated two-bool shape onto the new enum.
    task_mode = _resolve_task_mode(name, task_mode, task_required, task_optional)

    def decorator(fn):
        fn, effective_description = _prepare_tool_body(
            name,
            fn,
            description,
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            task_mode=task_mode,
            version=version,
        )

        if mcp is None:
            return fn
        # R8.E8 / Fix F: apply the handle-off wrapper UP-FRONT, before the
        # preset filter. Even when this tool is hidden by the active
        # preset (so MCP-protocol registration is skipped), an in-process
        # caller — including the compound tools (``for_*``, ``prepare_*``)
        # and internal helpers that call the tool directly via module
        # attribute — still benefits from automatic handle-off when the
        # response exceeds ``ROAM_MCP_HANDLE_KB``. The cost is one extra
        # function call when no handle-off fires (sub-microsecond).
        fn = _wrap_with_handle_off(name, fn)

        # W670 / Fix D-extension: apply the alias-normalization wrapper
        # UP-FRONT, before the preset filter — same rationale as the
        # handle-off wrapper above. In-process callers (compound tools,
        # tests, internal helpers that grab the tool by module attribute)
        # must get param-alias normalization regardless of which preset
        # is active. Pre-W670 this wrap sat after the preset filter, so
        # ``roam_plan(file_path=...)`` on a default-core preset would
        # raise ``TypeError`` instead of normalizing to ``path=...``.
        # The "outermost so FastMCP sees the synthesised signature"
        # rationale still holds for the registered path —
        # ``mcp.tool(**attempt)(fn)`` below is only reached when the
        # tool is in-preset, and at that point ``fn`` is already
        # alias-wrapped with the merged signature attached.
        fn = _wrap_with_alias_normalization(name, fn)

        # Meta-tool is always registered; others are filtered by preset.
        # When filtered out, return the alias-wrapped + handle-off-wrapped
        # function so in-process callers still get the same safety net
        # as MCP clients.
        if not _should_register_tool(name):
            return fn
        # Round 4 #14 / P: bound parallel tool invocations so the
        # FastMCP executor doesn't drop connections under burst load.
        # Over-capacity calls return a structured BUSY envelope with
        # a retry hint. Below capacity, overhead is one non-blocking
        # semaphore acquire (sub-microsecond).
        from roam.mcp_extras.concurrency import wrap_with_guard

        fn = wrap_with_guard(name, fn)
        # W445: fail-loud on duplicate tool registration. W432 sealed one
        # specific duplicate (roam_oracle_route_exists); this guard prevents
        # a future drive-by from silently shadowing an existing registration.
        if name in _REGISTERED_TOOLS:
            raise RuntimeError(f"Duplicate MCP tool registration: {name}")
        _REGISTERED_TOOLS.append(name)
        # Note: ``_TOOL_METADATA`` is populated at the top of ``decorator``
        # so ``roam_catalog`` works even when fastmcp is absent. The
        # wrappers above (handle-off, guard, alias-normalization) only
        # affect dispatch behavior, not the catalog surface.
        kwargs = _build_registration_kwargs(name, effective_description, output_schema, task_mode)
        return _register_with_fallbacks(fn, kwargs)

    return decorator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, error_code, hint) — checked in order, first match wins.
    # More specific patterns MUST come before broader ones.
    ("no .roam", "INDEX_NOT_FOUND", "run `roam init` to create the codebase index."),
    ("not found in index", "INDEX_NOT_FOUND", "run `roam init` to create the codebase index."),
    ("index is stale", "INDEX_STALE", "run `roam index` to refresh."),
    ("out of date", "INDEX_STALE", "run `roam index` to refresh."),
    ("not a git repository", "NOT_GIT_REPO", "some commands require git history. run: git init."),
    (
        "database is locked",
        "DB_LOCKED",
        "another roam process is running. wait or delete .roam/index.lock.",
    ),
    ("permission denied", "PERMISSION_DENIED", "check file permissions."),
    ("cannot open index", "INDEX_NOT_FOUND", "run `roam init` to create the codebase index."),
    ("symbol not found", "NO_RESULTS", "try a different search term or check spelling."),
    ("no matches", "NO_RESULTS", "try a different search term or check spelling."),
    ("no results", "NO_RESULTS", "try a different search term or check spelling."),
]


_RETRYABLE_CODES = {"DB_LOCKED", "INDEX_STALE"}


# Doc-link table — agents use this to fetch the full self-service playbook
# for a given error code. Anchors point at the troubleshooting section of
# the public docs site so the URL stays stable across roam versions.
#
# Only error codes whose remediation is captured in a dedicated section
# carry a fragment. Codes without a matching section fall through to
# the page-level URL (better than a broken fragment that scrolls
# nowhere).
_DOC_LINKS: dict[str, str] = {
    # Both index errors share the rebuild-from-scratch playbook.
    "INDEX_NOT_FOUND": "https://roam-code.com/docs/troubleshooting#index-stale",
    "INDEX_STALE": "https://roam-code.com/docs/troubleshooting#index-stale",
    # DB_LOCKED is dominantly a cloud-sync conflict (OneDrive / Dropbox /
    # iCloud racing the indexer for index.db); section 2 covers it.
    "DB_LOCKED": "https://roam-code.com/docs/troubleshooting#db-locked",
    "PERMISSION_DENIED": "https://roam-code.com/docs/troubleshooting#permission-denied",
    # Page-level fallbacks (no section dedicated to this error code yet).
    "NOT_GIT_REPO": "https://roam-code.com/docs/troubleshooting",
    "NO_RESULTS": "https://roam-code.com/docs/troubleshooting",
    "USAGE_ERROR": "https://roam-code.com/docs/troubleshooting",
    "GATE_FAILURE": "https://roam-code.com/docs/troubleshooting",
    "PARTIAL_FAILURE": "https://roam-code.com/docs/troubleshooting",
    "RATE_LIMITED": "https://roam-code.com/docs/troubleshooting",
    "COMMAND_FAILED": "https://roam-code.com/docs/troubleshooting",
    # R9 security recheck #4 — codes emitted by mcp_server.py that
    # were missing from this map. Falling through to the generic page
    # was lossy UX; agents got an "UNKNOWN" doc_link for diff/critique
    # paths that hit these codes.
    "EMPTY_INPUT": "https://roam-code.com/docs/troubleshooting",
    "INVALID_DIFF": "https://roam-code.com/docs/troubleshooting",
    "RUN_FAILED": "https://roam-code.com/docs/troubleshooting",
    "JSON_DECODE": "https://roam-code.com/docs/troubleshooting",
    "ELICITATION_REQUIRED": "https://roam-code.com/docs/troubleshooting",
    "FILE_NOT_FOUND": "https://roam-code.com/docs/troubleshooting",
    "DIRTY_TREE": "https://roam-code.com/docs/troubleshooting",
    # R10.1 — emitted by ``_apply_move`` when a destructive op partially
    # fails and the rollback path runs. Agents need a doc link to the
    # recovery playbook (verify rollback, re-stage, retry).
    "APPLY_FAILED": "https://roam-code.com/docs/troubleshooting",
    # Task 2a — stale .roam/config.json db_dir (typically from a moved /
    # deleted external drive). Section anchor is fall-through; the
    # surrounding envelope's hint carries the per-config remediation.
    "STALE_DB_DIR": "https://roam-code.com/docs/troubleshooting",
    # MCP-P0.2 — 4-mode policy denied a destructive tool call at the MCP
    # boundary. Section anchor falls through to the troubleshooting page;
    # the envelope's ``hint`` + ``next_command`` carry the per-call remediation
    # (e.g. ``roam mode migration``).
    "MODE_BLOCKED": "https://roam-code.com/docs/troubleshooting",
    "UNKNOWN": "https://roam-code.com/docs/troubleshooting",
}


def _classify_error(stderr: str, exit_code: int) -> tuple[str, str, bool]:
    """Classify error and return (error_code, hint, retryable).

    Checks standardized exit codes first (more reliable than text matching),
    then falls back to text pattern matching for legacy/subprocess output.
    The *retryable* flag indicates whether the agent should retry the call
    (True for DB_LOCKED, INDEX_STALE; False for everything else).
    """
    from roam.exit_codes import (
        EXIT_GATE_FAILURE,
        EXIT_INDEX_MISSING,
        EXIT_INDEX_STALE,
        EXIT_PARTIAL,
        EXIT_USAGE,
    )

    # Standardized exit code mapping (takes priority)
    _EXIT_CODE_MAP: dict[int, tuple[str, str]] = {
        EXIT_USAGE: ("USAGE_ERROR", "invalid arguments or flags. check --help."),
        EXIT_INDEX_MISSING: ("INDEX_NOT_FOUND", "run `roam init` to create the codebase index."),
        EXIT_INDEX_STALE: ("INDEX_STALE", "run `roam index` to refresh."),
        EXIT_GATE_FAILURE: ("GATE_FAILURE", "quality gate check failed."),
        EXIT_PARTIAL: ("PARTIAL_FAILURE", "command completed with warnings."),
    }
    if exit_code in _EXIT_CODE_MAP:
        code, hint = _EXIT_CODE_MAP[exit_code]
        return (code, hint, code in _RETRYABLE_CODES)

    # Fall back to text pattern matching
    s = stderr.lower()
    for pattern, code, hint in _ERROR_PATTERNS:
        if pattern in s:
            return (code, hint, code in _RETRYABLE_CODES)
    if exit_code != 0:
        return ("COMMAND_FAILED", "check arguments and try again.", False)
    return ("UNKNOWN", "check the error message for details.", False)


# severity bucket per error code. Lets agents branch on
# "warning vs error vs fatal" without parsing the message.
_SEVERITY_MAP: dict[str, str] = {
    "INDEX_NOT_FOUND": "error",
    "INDEX_STALE": "warning",
    "DB_LOCKED": "warning",
    "NOT_GIT_REPO": "warning",
    "PERMISSION_DENIED": "error",
    "NO_RESULTS": "info",
    "USAGE_ERROR": "error",
    "GATE_FAILURE": "error",
    "PARTIAL_FAILURE": "warning",
    "RATE_LIMITED": "warning",
    "COMMAND_FAILED": "error",
    # R9 security recheck #4 — codes emitted by mcp_server.py that
    # were falling through to the default "error" severity. Pin them
    # so agents can branch on severity without parsing the message.
    "EMPTY_INPUT": "error",  # missing required input
    "INVALID_DIFF": "error",  # malformed git diff in critique
    "RUN_FAILED": "error",  # subprocess returned non-zero
    "JSON_DECODE": "error",  # downstream produced non-JSON
    "ELICITATION_REQUIRED": "warning",  # awaits user response, retryable
    "FILE_NOT_FOUND": "error",  # specific file missing
    "DIRTY_TREE": "warning",  # uncommitted changes block emit
    # INVALID_JSON envelopes are emitted inline by ``_run_roam_inprocess`` /
    # ``_run_roam_subprocess`` / ``_parse_subprocess_result`` when stdout
    # exists but won't json.loads(). They carry ``summary.partial_success:
    # True`` -- this is a degraded but recoverable signal, not a fatal
    # error. Pin severity to ``warning`` so any future call site that
    # routes the code through ``_structured_error`` gets the right
    # branching (rather than falling through to the default "error").
    "INVALID_JSON": "warning",
    # R10.1 — destructive op partial failure (rollback ran). Severity is
    # ``error`` because the working tree may carry partial edits even
    # after rollback; the agent must re-verify before retrying.
    "APPLY_FAILED": "error",
    # Task 2a — stale db_dir is a config drift problem; severity is
    # ``error`` because the request fully failed, but ``retryable`` is
    # False so agents stop hammering the same broken path.
    "STALE_DB_DIR": "error",
    # MCP-P0.2 — mode denied a destructive tool call. Severity is ``error``
    # (the call did not run), but ``retryable`` is False — the agent must
    # switch modes (or set ``--override-mode`` equivalent at the CLI side)
    # rather than hammer the same call.
    "MODE_BLOCKED": "error",
    "UNKNOWN": "error",
}


# Pattern-1 conformance — map every ``error_code`` ``_structured_error``
# can receive onto the CLAUDE.md canonical ``status`` closed enum
# (``index_not_built | advisory_warnings | partial_failure |
# hard_failure | usage_error | rate_limited | stale_index``). The
# canonical failure envelope pairs ``isError: true`` with a
# closed-enum ``status``; before this map, error envelopes carried
# ``isError`` + ``error_code`` but never ``status``. Every code listed
# in ``_SEVERITY_MAP`` / ``_DOC_LINKS`` / ``_classify_error`` is covered
# here; codes absent from the map fall through to ``hard_failure`` via
# the ``.get(code, "hard_failure")`` default in ``_structured_error``.
_ERROR_CODE_TO_STATUS: dict[str, str] = {
    # usage-error class — invalid arguments / missing required input.
    "USAGE_ERROR": "usage_error",
    "EMPTY_INPUT": "usage_error",
    "INVALID_DIFF": "usage_error",
    "ELICITATION_REQUIRED": "usage_error",
    # index-missing class.
    "INDEX_NOT_FOUND": "index_not_built",
    # stale-index class.
    "INDEX_STALE": "stale_index",
    "STALE_DB_DIR": "stale_index",
    # rate-limited class.
    "RATE_LIMITED": "rate_limited",
    # partial-failure class — degraded but recoverable signal.
    "PARTIAL_FAILURE": "partial_failure",
    "INVALID_JSON": "partial_failure",
    "JSON_DECODE": "partial_failure",
    # hard-failure class — the request fully failed, agent must change
    # something material (perms, repo state, mode) before retrying.
    "COMMAND_FAILED": "hard_failure",
    "RUN_FAILED": "hard_failure",
    "NOT_GIT_REPO": "hard_failure",
    "DB_LOCKED": "hard_failure",
    "PERMISSION_DENIED": "hard_failure",
    "GATE_FAILURE": "hard_failure",
    "NO_RESULTS": "hard_failure",
    "FILE_NOT_FOUND": "hard_failure",
    "DIRTY_TREE": "hard_failure",
    "APPLY_FAILED": "hard_failure",
    "MODE_BLOCKED": "hard_failure",
    "UNKNOWN": "hard_failure",
}


# error storm rate-limit. When the same error_code fires N
# times in a row, the verbose envelope (hint, suggested_action,
# doc_link, severity) is dropped on subsequent fires and replaced with
# a tight ``{error_code, repeat_count}`` shape that still keeps command
# identity when the caller supplied it. The full envelope returns the
# moment a different error_code fires (resets the counter).
_ERROR_STORM_THRESHOLD = 3
_ERROR_STORM_STATE: dict[str, object] = {"_last_code": 0, "_count": 0}
_FIRST_ERROR_COMMAND_SEP = "\x1f"

# Task 2 (IMPLEMENTATION-2026-05-12) — preserve the FIRST error message
# observed for a given error_code so trimmed-envelope replies still
# carry the actionable stderr text. Without this cache the agent loop
# loses the remediation hint after the 3rd fire (storm trim drops the
# verbose `error` field) and has to guess from `error_code` alone.
# Keyed by `error_code` for legacy/no-command callers, plus
# `error_code + separator + command` for MCP tools that provide command
# identity. Cleared by `_reset_error_storm`.
_first_error_message: dict[str, str] = {}

# Fix E (Sub-task 4) — session-wide counter for ``partial_success: true``
# envelopes. The ``session_metrics`` tool used to report
# ``error_count: 0`` even when many calls in the session returned
# ``summary.partial_success: true``; agents reading the metrics
# concluded the session was clean when it wasn't. We increment this
# counter at the central dispatch points (``_run_roam`` /
# ``_compound_envelope``) so any envelope flowing through them is
# observed exactly once.
_session_partial_success_count: int = 0


def _note_partial_success(envelope: dict | None) -> None:
    """If *envelope* declares ``summary.partial_success: true``, tick the
    session counter. Idempotent on non-dict inputs."""
    global _session_partial_success_count
    if not isinstance(envelope, dict):
        return
    summary = envelope.get("summary")
    if isinstance(summary, dict) and summary.get("partial_success") is True:
        _session_partial_success_count += 1


def _reset_session_partial_success_count() -> None:
    """Test helper — reset the session-wide partial_success counter."""
    global _session_partial_success_count
    _session_partial_success_count = 0


# ---------------------------------------------------------------------------
# Fix D (SYNTHESIS Pattern 3b — vocabulary harmonization at the MCP boundary)
#
# Different tools historically named the SAME concept differently:
#   - symbol identifier:  ``symbol`` / ``name`` / ``target``
#   - file location:      ``path`` / ``file`` / ``paths``
#   - free-text query:    ``query`` / ``pattern`` / ``prefix``
#   - input file path:    ``input_path`` / ``rules_path`` / ``rules_file``
#                         / ``statement_path`` / ``envelope_path`` (W332)
#
# We pick a canonical name per concept (``symbol`` / ``path`` / ``query`` /
# ``input_path``) and accept the historical aliases at the dispatch boundary,
# emitting a deprecation warning so agents migrate without breaking. The alias
# layer is ADDITIVE — no parameter is ever removed, only translated.
#
# Concepts intentionally NOT collapsed:
#   - ``paths`` (plural list) vs ``path`` (singular) — semantically distinct
#   - ``prefix`` — literal-prefix-match semantics, not free-text query
#   - ``trace_file`` — refers to an OTel/Jaeger/Zipkin trace, not source code
#   - ``rules_dir`` / ``redact_paths`` — directories / flags, distinct concepts
#   - ``from_pattern`` / ``to_pattern`` — regex patterns for path filtering
#   - ``diff_path`` — a diff file is one of TWO inputs to pr-analyze and is the
#     primary input; collapsing would clash with its sidecar ``input_path``
#     (rules YAML). See W332 design note in this comment block.
#
# W332 design note — single-input-file canonical
# -----------------------------------------------
# All 5 historical "path to a file the tool reads" parameters (``rules_path``,
# ``rules_file``, ``statement_path``, ``envelope_path``, plus the already-
# canonical ``input_path`` on the audit_trail_* tools) collapse to one
# canonical: ``input_path``. The agent's mental model is: "the file the tool
# reads as its primary input." The tool's docstring explains WHAT KIND of file.
# This mirrors the Pattern 3b fix for symbol/path/query.
#
# Acknowledged ambiguity: ``pr_analyze`` declares BOTH ``diff_path`` (primary
# input, the PR diff) AND ``input_path`` (sidecar, the rules YAML). The
# docstring is explicit that ``diff_path`` is primary and ``input_path`` is
# the sidecar rules pack. Both names stay distinct — only the legacy
# ``rules_path`` alias on ``pr_analyze`` collapses to ``input_path``. A
# future sweep may introduce a finer-grained canonical
# (``primary_input_path`` vs ``config_input_path``) if the dogfood corpus
# shows agents tripping on the ``diff_path`` + ``input_path`` split.
# ---------------------------------------------------------------------------

# Per-concept alias map: {canonical_name: {alias: canonical}}. The redundant
# value half lets callers also do reverse lookup if needed; the helper only
# uses the keys.
_PARAM_ALIASES: dict[str, dict[str, str]] = {
    # W347 — ``subject`` reserved as a future symbol-shaped alias; no wrapper
    # currently declares it. Pre-registering keeps the alias machinery
    # forward-compatible for a future wrapper that picks that spelling.
    "symbol": {"name": "symbol", "target": "symbol", "subject": "symbol"},
    # W347 — extend ``path`` aliases to cover the file-path cluster
    # (``file`` was already aliased pre-W332; ``file_path`` / ``filename`` /
    # ``filepath`` join it here). The lint catches new wrappers that pick
    # any of these as their canonical and forces a rename to ``path``.
    "path": {
        "file": "path",
        "file_path": "path",
        "filename": "path",
        "filepath": "path",
    },
    "query": {"pattern": "query"},
    # W332 — single-input-file canonical. Four legacy names collapse here.
    "input_path": {
        "rules_path": "input_path",
        "rules_file": "input_path",
        "statement_path": "input_path",
        "envelope_path": "input_path",
    },
}

# W332 — closed set of legacy names that MUST NOT be declared by any new
# ``@_tool`` wrapper. The lint test ``test_mcp_param_names.py`` parametrizes
# over this set + ``_PARAM_ALIASES`` so that a future divergent param-name
# family is caught the moment it lands. Existing callers that pass these
# names still work via the alias machinery; the lint targets the WRAPPER
# DECLARATION side, not the call site.
_W332_DEPRECATED_INPUT_PATH_PARAMS: frozenset[str] = frozenset(
    {"rules_path", "rules_file", "statement_path", "envelope_path"}
)

# W347 — closed set of legacy names from the symbol / file-path clusters
# that MUST NOT be declared by any new ``@_tool`` wrapper. The matching
# lint case in ``test_mcp_param_names.py`` parametrizes over this set;
# existing wrappers carrying these names are listed in the lint's
# ``_PRE_W332_EXEMPT`` table with a per-entry rationale.
_W347_DEPRECATED_PARAMS: frozenset[str] = frozenset({"file_path", "filename", "filepath", "subject"})


def _normalize_aliases(
    tool_name: str,
    kwargs: dict,
    accepted: set[str],
    defaults: dict | None = None,
) -> tuple[dict, list[str]]:
    """Rewrite alias keys in ``kwargs`` to canonical names.

    Parameters
    ----------
    tool_name:
        MCP tool name (e.g. ``"roam_uses"``). Used for warning text only.
    kwargs:
        The keyword arguments the dispatcher received from the client.
    accepted:
        The canonical kwarg names the tool's Python signature actually
        declares. Only aliases for keys in this set are rewritten — that
        way a tool whose ``target`` parameter means "git ref" (not
        "symbol") is unaffected because ``symbol`` is not in its
        ``accepted`` set, so the ``target → symbol`` alias is skipped.
    defaults:
        Optional mapping ``{canonical_name: default_value}`` from the
        wrapped function's signature. Used to detect the
        "FastMCP filled the canonical with its declared default while
        the caller only set the alias" case — without this, the BOTH-
        supplied branch fires and the alias value is dropped, leaving
        only the canonical's default. See 2026-05-24 bug investigation.

    Returns
    -------
    (new_kwargs, warnings) — ``new_kwargs`` is a fresh dict with aliases
    rewritten; ``warnings`` is a list of human-readable deprecation
    strings (empty when no alias was used).

    Rules
    -----
    1. If ONLY the alias is supplied (e.g. ``name=X``) and the canonical
       (``symbol``) is not, the alias is renamed to canonical and a
       deprecation warning is recorded.
    2. If BOTH are supplied:
       a. If the canonical equals its declared default in ``defaults``
          (i.e. FastMCP filled it from the wrapped fn's signature, the
          user did not explicitly set it), the alias value is promoted
          to canonical with a deprecation warning — same as rule 1.
       b. Otherwise (both user-set), the canonical wins and the alias
          is dropped with a "duplicate / ignoring alias" warning. We
          never silently merge or prefer the alias.
    3. Aliases for canonicals NOT in ``accepted`` are left untouched —
       same-named parameters with different semantics across tools stay
       independent.
    """
    warnings: list[str] = []
    out = dict(kwargs)
    defaults = defaults or {}
    for canon, alias_map in _PARAM_ALIASES.items():
        if canon not in accepted:
            continue
        for alias in alias_map:
            if alias == canon:
                # ``symbol → symbol`` is a no-op identity that lives in the
                # map only for documentation; skip it.
                continue
            if alias in out and canon not in out:
                out[canon] = out.pop(alias)
                warnings.append(f"{tool_name}: param '{alias}' is deprecated; use '{canon}'")
            elif alias in out and canon in out:
                # Rule 2a: canonical at its signature default while alias is
                # user-set — FastMCP/Pydantic filled the canonical from the
                # wrapped function's declared default. Promote the alias to
                # canonical (deprecation, not duplicate) so the user-set
                # alias value actually reaches the wrapped function.
                # Detected via the ``defaults`` map passed by the wrapper.
                if canon in defaults and out.get(canon) == defaults[canon]:
                    out[canon] = out.pop(alias)
                    warnings.append(f"{tool_name}: param '{alias}' is deprecated; use '{canon}'")
                else:
                    # Rule 2b: both user-set — canonical wins, alias is
                    # dropped loudly so the agent knows its alias was
                    # ignored (not silently merged).
                    out.pop(alias)
                    warnings.append(f"{tool_name}: ignoring '{alias}' (use '{canon}' only)")
    return out, warnings


def _attach_alias_warnings(result, warnings: list[str]):
    """Surface alias-deprecation warnings inside the tool's envelope so the
    agent sees them without scraping logs.

    Mutates and returns ``result`` when it's a dict; otherwise returns it
    unchanged (e.g. None, a non-dict primitive). Warnings land under
    ``summary.alias_warnings`` — extending any existing list rather than
    clobbering. Idempotent: calling with an empty warnings list is a no-op.
    """
    if not warnings:
        return result
    if not isinstance(result, dict):
        return result
    summary = result.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        result["summary"] = summary
    existing = summary.get("alias_warnings")
    if isinstance(existing, list):
        existing.extend(warnings)
    else:
        summary["alias_warnings"] = list(warnings)
    return result


def _collect_alias_candidates(sig) -> tuple[set[str], list[str]]:
    """W607 — Pure: discover the alias surface for a given signature.

    Returns ``(accepted, aliases_for_tool)``:

    * ``accepted`` — the canonical names from ``_PARAM_ALIASES`` that the
      signature actually declares (e.g. ``{"symbol", "path"}``).
    * ``aliases_for_tool`` — the legacy alias names that should be
      synthesised as keyword-only params on the wrapper (e.g.
      ``["name", "target", "file"]``). Aliases already declared by the
      wrapper itself are skipped — the wrapper owns that name.

    Aliases are emitted in iteration order of ``_PARAM_ALIASES`` →
    ``accepted`` (whose iteration order is set-insertion-derived); the
    resulting list is deterministic across CPython runs for the same
    signature because ``_PARAM_ALIASES`` is module-level and ``accepted``
    is built by iterating over ``sig.parameters`` which preserves
    declaration order.
    """
    accepted: set[str] = {p.name for p in sig.parameters.values() if p.name in _PARAM_ALIASES}
    if not accepted:
        return accepted, []

    declared = {p.name for p in sig.parameters.values()}
    aliases_for_tool: list[str] = []
    for canon in accepted:
        for alias in _PARAM_ALIASES[canon]:
            if alias == canon or alias in declared:
                continue
            aliases_for_tool.append(alias)
    return accepted, aliases_for_tool


def _build_merged_signature(sig, accepted: set[str], aliases_for_tool: list[str]):
    """W607 — Pure: build the merged ``inspect.Signature`` for the wrapper.

    Constraints reconciled:

    1. Any CANONICAL param that has at least one alias is demoted to
       ``default=""`` so FastMCP / Pydantic schema generation does not
       reject calls supplying only the legacy alias.
    2. A positional-or-keyword param with a default cannot be followed by
       a positional-or-keyword param WITHOUT a default (Python grammar).
       So when a canonical-with-alias gets demoted AND any subsequent
       positional-or-keyword param is still required, the demoted
       canonical is promoted to ``KEYWORD_ONLY`` instead. This is the
       W595 fix path.
    3. Aliases are appended as ``KEYWORD_ONLY`` with ``default=None`` and
       ``annotation=str`` — agents always invoke by keyword, so the
       positional/keyword-only distinction is invisible at the wire level.
    4. A ``**kwargs`` sink (VAR_KEYWORD) is preserved at the tail.

    Pure: no I/O, no module imports beyond the local ``inspect``, no
    mutation of ``sig`` (uses ``sig.replace(...)``).
    """
    import inspect as _inspect

    canonicals_with_alias = {canon for canon in accepted if any(a != canon for a in _PARAM_ALIASES[canon])}

    original_params = list(sig.parameters.values())

    # W595 — pre-scan: which canonicals must become KEYWORD_ONLY rather
    # than stay positional-or-keyword-with-default?
    must_promote_to_kwonly: set[str] = set()
    for i, p in enumerate(original_params):
        if p.kind != _inspect.Parameter.POSITIONAL_OR_KEYWORD:
            continue
        if p.name not in canonicals_with_alias:
            continue
        if p.default is not _inspect.Parameter.empty:
            continue
        for q in original_params[i + 1 :]:
            if q.kind != _inspect.Parameter.POSITIONAL_OR_KEYWORD:
                break
            if q.default is _inspect.Parameter.empty:
                must_promote_to_kwonly.add(p.name)
                break

    positional_params: list[_inspect.Parameter] = []
    keyword_only_params: list[_inspect.Parameter] = []
    var_keyword_param: _inspect.Parameter | None = None
    for p in original_params:
        if p.kind == _inspect.Parameter.VAR_KEYWORD:
            var_keyword_param = p
            continue
        if p.kind == _inspect.Parameter.KEYWORD_ONLY:
            keyword_only_params.append(p)
            continue
        # POSITIONAL_OR_KEYWORD or POSITIONAL_ONLY
        if p.name in canonicals_with_alias and p.default is _inspect.Parameter.empty:
            if p.name in must_promote_to_kwonly:
                keyword_only_params.append(p.replace(kind=_inspect.Parameter.KEYWORD_ONLY, default=""))
            else:
                positional_params.append(p.replace(default=""))
        else:
            positional_params.append(p)

    for alias in aliases_for_tool:
        keyword_only_params.append(
            _inspect.Parameter(
                alias,
                kind=_inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=str,
            )
        )

    new_params: list[_inspect.Parameter] = positional_params + keyword_only_params
    if var_keyword_param is not None:
        new_params.append(var_keyword_param)

    return sig.replace(parameters=new_params)


def _build_merged_annotations(fn, aliases_for_tool: list[str]) -> dict:
    """W607 — Pure: build the merged ``__annotations__`` dict.

    FastMCP / Pydantic call ``typing.get_type_hints(wrapper_fn)`` to
    derive the input schema, and ``get_type_hints`` looks at
    ``__annotations__``, NOT ``__signature__``. Without syncing the two,
    the synthesised alias params would be missing their type and pydantic
    raises ``KeyError: '<alias>'`` during schema generation.

    All aliases get ``str`` — mirrors the ``_build_merged_signature``
    contract where alias params are typed ``str`` with ``default=None``.
    """
    merged: dict = dict(getattr(fn, "__annotations__", {}) or {})
    for alias in aliases_for_tool:
        merged[alias] = str
    return merged


def _wrap_with_alias_normalization(name: str, fn):
    """Wrap an MCP tool so legacy parameter aliases are accepted.

    The wrapper:
      1. Introspects the wrapped function's signature to discover which
         canonical names (``symbol`` / ``path`` / ``query``) the tool
         actually declares.
      2. On call, rewrites legacy alias kwargs (``name`` → ``symbol``,
         ``target`` → ``symbol``, ``file`` → ``path``, ``pattern`` →
         ``query``) to their canonical names.
      3. Synthesises a merged ``__signature__`` that exposes BOTH the
         canonical params AND their aliases (all aliases marked optional
         with ``None`` default), so FastMCP / Pydantic schema generation
         advertises both spellings to the client.
      4. Surfaces a deprecation warning under
         ``summary.alias_warnings`` in the returned envelope.

    Aliases that are positional in the wrapped function become keyword-only
    in the synthesised signature — this avoids ambiguity when a caller
    supplies both via positional + keyword. In practice clients call MCP
    tools by keyword, so this has no observable cost.

    W607 — Signature-rebuild concerns are extracted into three pure
    helpers (``_collect_alias_candidates`` / ``_build_merged_signature``
    / ``_build_merged_annotations``). This function is now ~30 lines of
    orchestration + the sync/async closure construction.
    """
    import functools as _functools
    import inspect as _inspect

    try:
        sig = _inspect.signature(fn)
    except (TypeError, ValueError):
        # Builtins / C-extensions don't expose a signature. Fall back to a
        # pure passthrough — the alias layer can't help here, but we should
        # never break the tool.
        return fn

    accepted, aliases_for_tool = _collect_alias_candidates(sig)
    if not accepted:
        # Tool declares no canonical concept name — nothing to alias.
        return fn

    merged_signature = _build_merged_signature(sig, accepted, aliases_for_tool)
    merged_annotations = _build_merged_annotations(fn, aliases_for_tool)

    # Snapshot the defaults FastMCP will actually fill for the canonical
    # params. _normalize_aliases uses this to distinguish "user-set canon"
    # from "FastMCP-filled-with-default canon" — fixes the 2026-05-24 bug
    # where calling pattern=X (with query at "") would drop the user's
    # value because both kwargs were present.
    #
    # MUST read from ``merged_signature``, not ``sig``: a REQUIRED canonical
    # with an alias (e.g. roam_deps's ``path``) is demoted to ``default=""``
    # in the merged sig so alias-only calls pass schema validation. Reading
    # from the original ``sig`` would omit it (no declared default there),
    # so rule 2a could not fire and rule 2b would silently DROP the caller's
    # alias value — e.g. ``roam_deps(file="x.py")`` analysing ``path=""``
    # (the first repo file) instead of x.py. The merged sig is the source of
    # truth for what FastMCP fills.
    canon_defaults: dict = {
        p.name: p.default
        for p in merged_signature.parameters.values()
        if p.name in accepted and p.default is not _inspect.Parameter.empty
    }

    def _prepare_kwargs(kwargs: dict) -> tuple[dict, list]:
        # Drop alias-only kwargs that are still ``None`` — they were
        # injected by the synthetic signature but the client didn't
        # actually pass them. Otherwise they'd shadow a canonical
        # default and confuse the inner function.
        for alias in aliases_for_tool:
            if alias in kwargs and kwargs[alias] is None:
                kwargs.pop(alias)
        return _normalize_aliases(name, kwargs, accepted, defaults=canon_defaults)

    if _inspect.iscoroutinefunction(fn):

        @_functools.wraps(fn)
        async def _alias_wrapped(*args, **kwargs):
            kwargs, warns = _prepare_kwargs(kwargs)
            result = await fn(*args, **kwargs)
            return _attach_alias_warnings(result, warns)

    else:

        @_functools.wraps(fn)
        def _alias_wrapped(*args, **kwargs):
            kwargs, warns = _prepare_kwargs(kwargs)
            result = fn(*args, **kwargs)
            return _attach_alias_warnings(result, warns)

    _alias_wrapped.__signature__ = merged_signature  # type: ignore[attr-defined]
    _alias_wrapped.__annotations__ = merged_annotations
    return _alias_wrapped


# ---------------------------------------------------------------------------
# R8.E8 — reference-based result handles for large responses
#
# Some tools (roam_understand, roam_health, roam_taint) can produce
# 50KB+ JSON envelopes. Returning the full blob over the MCP wire eats
# the agent's context budget. With handle-off enabled, large responses
# are written to ``.roam/responses/<sha16>.json`` and replaced with a
# tiny envelope carrying just the handle, byte size, and a preview.
# The agent fetches the full payload on demand via ``roam_fetch_handle``.
#
# Tunable via the ``ROAM_MCP_HANDLE_KB`` env var (default 50, 0 = disable).
# ---------------------------------------------------------------------------


def _handle_storage_dir() -> Path:
    """Where large-response payloads are written. Relative to cwd so
    each project's handles stay scoped to its workspace."""
    return Path.cwd() / ".roam" / "responses"


# Default GC tunables. Overridable via env vars at call time.
# - TTL: evict files whose mtime is older than this many hours (default 7d).
# - Max bytes: evict oldest-first until total dir size is under this cap.
# - Min files before GC kicks in: amortise the cost; never run on every write.
_HANDLE_GC_DEFAULT_TTL_HOURS = 168  # 7 days
_HANDLE_GC_DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100MB
_HANDLE_GC_MIN_FILES = 50  # only consider GC once dir has > this many entries


def _gc_dir_is_usable(handle_dir: Path) -> bool:
    """Return True iff ``handle_dir`` exists and is a directory. False on
    missing path, regular-file shadow, or any OSError during the check —
    silent-skip is the GC contract (never poison an in-flight tool response)."""
    try:
        return handle_dir.is_dir()
    except OSError:
        return False


def _gc_resolve_tunables() -> tuple[int, int]:
    """Read GC tunables from env. Bad values fall back to defaults silently
    (one bad env var doesn't taint the other). Returns ``(ttl_hours, max_bytes)``.
    Either ``<= 0`` disables the corresponding pass."""
    try:
        ttl_hours = int(os.environ.get("ROAM_MCP_HANDLE_TTL_HOURS", str(_HANDLE_GC_DEFAULT_TTL_HOURS)))
    except ValueError:
        ttl_hours = _HANDLE_GC_DEFAULT_TTL_HOURS
    try:
        max_bytes = int(os.environ.get("ROAM_MCP_HANDLE_MAX_BYTES", str(_HANDLE_GC_DEFAULT_MAX_BYTES)))
    except ValueError:
        max_bytes = _HANDLE_GC_DEFAULT_MAX_BYTES
    return ttl_hours, max_bytes


def _gc_snapshot_entries(handle_dir: Path) -> list[Path] | None:
    """Snapshot directory contents. Returns ``None`` if the directory
    vanished between the ``is_dir`` check and ``iterdir`` (rmtree race);
    caller treats ``None`` as a silent skip."""
    try:
        return list(handle_dir.iterdir())
    except (OSError, FileNotFoundError):
        return None


def _gc_stat_json_files(entries: list[Path]) -> list[tuple[Path, float, int]]:
    """Filter to ``*.json`` regular files + stat each, tolerating per-entry
    vanish/permission races. Each file is wrapped in its own try so one
    missing entry doesn't poison the whole sweep. Returns
    ``[(path, mtime, size), ...]`` in iterdir order (not sorted)."""
    stats: list[tuple[Path, float, int]] = []
    for p in entries:
        if p.suffix != ".json":
            continue
        try:
            st = p.stat()
        except (FileNotFoundError, OSError):
            continue
        # S_ISREG keeps the race-window tight: one stat call instead of
        # stat + is_file (which would re-stat under the hood).
        if not _stat_mod.S_ISREG(st.st_mode):
            continue
        stats.append((p, st.st_mtime, st.st_size))
    return stats


def _gc_safe_unlink(path: Path, handle_dir: Path) -> bool:
    """Path-confined unlink. Confirms ``path.parent`` resolves to
    ``handle_dir`` (belt-and-suspenders against a tainted ``handle_dir``
    arg). Tolerates the file vanishing between check and unlink. Returns
    True iff the unlink succeeded — caller uses this to adjust the
    rolling-bytes total in the size pass."""
    try:
        if path.parent.resolve() != handle_dir.resolve():
            return False
        path.unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def _gc_apply_ttl_pass(
    stats: list[tuple[Path, float, int]],
    handle_dir: Path,
    ttl_hours: int,
    now: float,
) -> list[tuple[Path, float, int]]:
    """Drop any *.json file whose mtime is older than ``now - ttl_hours*3600``;
    return the survivor list (FRESH list, so the size pass can sort it in
    place without coupling to the TTL pass's iteration order). When
    ``ttl_hours <= 0`` the TTL pass is disabled — survivors == stats."""
    if ttl_hours <= 0:
        return list(stats)
    cutoff = now - (ttl_hours * 3600)
    survivors: list[tuple[Path, float, int]] = []
    for entry in stats:
        path, mtime, _size = entry
        if mtime < cutoff:
            # Eviction candidate. _gc_safe_unlink handles the path-confinement
            # check + race-tolerant unlink; we accept either outcome silently
            # (a survived-but-not-evictable file becomes a survivor; an
            # actually-deleted file just drops out).
            if not _gc_safe_unlink(path, handle_dir):
                survivors.append(entry)
        else:
            survivors.append(entry)
    return survivors


def _gc_apply_size_pass(
    stats: list[tuple[Path, float, int]],
    handle_dir: Path,
    max_bytes: int,
) -> None:
    """If total bytes exceed ``max_bytes``, evict oldest-mtime files first
    until under cap. ``max_bytes <= 0`` disables the pass. Sorts a copy of
    ``stats`` (not in-place) so callers' iteration order survives."""
    if max_bytes <= 0:
        return
    total = sum(size for _p, _m, size in stats)
    if total <= max_bytes:
        return
    # Oldest-first eviction. mtime is the access proxy; with content
    # addressing, "oldest" means "least recently regenerated".
    ordered = sorted(stats, key=lambda triple: triple[1])
    for path, _mtime, size in ordered:
        if total <= max_bytes:
            break
        if _gc_safe_unlink(path, handle_dir):
            total -= size


def _gc_handle_dir(handle_dir: Path) -> None:
    """Best-effort LRU/TTL cleanup of the on-disk handle store.

    Tunable via env vars:
      ROAM_MCP_HANDLE_TTL_HOURS  (default 168 = 7 days; <=0 disables TTL)
      ROAM_MCP_HANDLE_MAX_BYTES  (default 100MB; <=0 disables size cap)

    Eviction order:
      1. TTL pass — drop any *.json file whose mtime is older than the TTL.
      2. Size pass — if total bytes still exceed the cap, delete oldest-mtime
         files first until under the cap.

    Hardening:
      * Only operates on files matching ``*.json`` directly inside the handle
        dir — never recurses, never follows symlinks out of scope.
      * Tolerates files vanishing mid-iteration (another process / GC race).
      * If ``handle_dir`` is missing or not a directory, returns silently.
      * Never raises — GC is opportunistic; the caller has more important
        work (a tool response is mid-flight).

    Implementation: split across ``_gc_*`` helpers; this orchestrator
    wires them in eviction-order. Path-confinement is centralised in
    ``_gc_safe_unlink`` so the TTL pass and size pass share one check.
    """
    if not _gc_dir_is_usable(handle_dir):
        return
    ttl_hours, max_bytes = _gc_resolve_tunables()
    raw_entries = _gc_snapshot_entries(handle_dir)
    if raw_entries is None:
        return
    stats = _gc_stat_json_files(raw_entries)
    survivors = _gc_apply_ttl_pass(stats, handle_dir, ttl_hours, _time.time())
    _gc_apply_size_pass(survivors, handle_dir, max_bytes)


# Per-process counter used to amortise GC. Even when the dir is large,
# we run cleanup at most once every N writes — prevents pathological
# O(N) scans on every tool call in a hot loop.
_HANDLE_GC_WRITE_COUNTER: dict[str, int] = {"n": 0}
_HANDLE_GC_RUN_EVERY = 25


# W671: closed enumeration of MCP tools whose response IS the answer and
# must be returned inline regardless of size. These are session-bootstrap
# / pure-metadata surfaces where forcing the agent through a second
# ``roam_fetch_handle`` round-trip defeats the contract:
#
# * ``roam_catalog`` — the canonical "what tools exist?" call. WHEN TO
#   USE doc literally says "at the start of a long session ... one
#   round-trip". On a 223-tool registry the JSON payload is ~126KB,
#   which previously tripped the 50KB handle threshold and returned a
#   handle envelope, forcing every agent to fetch the catalog twice.
# * ``roam_session_metrics`` — local-only invocation telemetry; agents
#   typically read it at end-of-session, not as a queryable dataset.
# * ``roam_expand_toolset`` — already exempted below via ``_META_TOOL``
#   (kept in the readable set here for documentation parity).
#
# This is a strict subset of ``mcp_extras.preflight._NO_INDEX_NEEDED``
# (the cold-start skip set). Membership rule: "the response is one
# message agents consume whole; pagination/sub-fetch makes no sense."
# Tools that operate on caller-supplied paths (``roam_evidence_doctor``,
# ``roam_pr_comment_render``) and bootstrap tools (``roam_init`` /
# ``roam_reindex`` / ``roam_doctor``) all produce small envelopes in
# practice and don't need the exemption — keeping the set narrow.
_INLINE_RESPONSE_TOOLS: frozenset[str] = frozenset(
    {
        "roam_catalog",
        "roam_session_metrics",
        "roam_expand_toolset",
    }
)


def _should_bypass_handle_off(payload: dict, tool_name: str) -> bool:
    """Return True when ``payload`` MUST be returned inline (handle-off skipped).

    Bypass reasons, in evaluation order:

    * payload is not a dict (defensive — wrappers may return raw values);
    * payload carries ``isError`` (agent needs the full structured-error
      envelope to decide whether to retry);
    * tool is ``roam_fetch_handle`` itself (would self-loop) or the
      meta-tool ``_META_TOOL`` (already small);
    * W671: tool is in ``_INLINE_RESPONSE_TOOLS`` — session-bootstrap /
      pure-metadata responses where the payload IS the answer;
    * payload already IS a handle envelope (``is_handle`` set) — avoid
      double-handling on composed internal calls.
    """
    if not isinstance(payload, dict):
        return True
    if payload.get("isError"):
        return True
    if tool_name in {"roam_fetch_handle", _META_TOOL}:
        return True
    if tool_name in _INLINE_RESPONSE_TOOLS:
        return True
    if payload.get("is_handle"):
        return True
    return False


def _serialise_for_handle(payload: dict) -> tuple[str, int, str] | None:
    """Serialise ``payload`` to JSON and return ``(blob, size, sha)``.

    Reads the ``ROAM_MCP_HANDLE_KB`` env var (default 50KB) to determine
    the threshold; returns ``None`` when:

    * threshold is disabled (``<= 0``);
    * payload is not JSON-serialisable (let the regular code path raise);
    * serialised size is below threshold (no handle-off needed).

    ``size`` is the UTF-8-encoded byte length (matches the byte-size field
    in the handle envelope). The 16-hex-char sha256 prefix is the
    content-addressed handle id.
    """
    import hashlib as _hashlib
    import json as _json

    try:
        threshold_kb = int(os.environ.get("ROAM_MCP_HANDLE_KB", "50"))
    except ValueError:
        threshold_kb = 50
    if threshold_kb <= 0:
        return None
    threshold = threshold_kb * 1024

    try:
        blob = _json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return None
    encoded = blob.encode("utf-8")
    size = len(encoded)
    if size < threshold:
        return None

    sha = _hashlib.sha256(encoded).hexdigest()[:16]
    return blob, size, sha


def _persist_handle_blob(handle_dir: Path, sha: str, blob: str) -> Path | None:
    """Write ``blob`` to a content-addressed file under ``handle_dir``.

    Tightens a freshly-created directory to ``0o700`` (owner-only) since
    handle payloads can include source excerpts, taint findings, and
    PageRank data. Returns the target ``Path`` on success, or ``None``
    on ``OSError`` (read-only filesystem / permission issue — caller
    should ship the fat envelope rather than fail the call).

    Idempotent: identical payloads reuse the same file (content-addressed).
    """
    try:
        # First-creation: tighten the directory to 0o700 (owner-only).
        # mkdir's mode arg is masked by umask, so chmod after creation.
        was_new = not handle_dir.exists()
        handle_dir.mkdir(parents=True, exist_ok=True)
        if was_new:
            try:
                handle_dir.chmod(0o700)
            except OSError:
                # POSIX-only semantics; on Windows chmod is a near no-op
                # but the call shouldn't fail. Swallow and continue.
                pass
        target = handle_dir / f"{sha}.json"
        # content-addressed → identical payload reuses the same file
        if not target.is_file():
            target.write_text(blob, encoding="utf-8")
    except OSError:
        return None
    return target


def _maybe_run_amortised_gc(handle_dir: Path) -> None:
    """Best-effort handle-directory GC, amortised across many calls.

    Runs ``_gc_handle_dir`` when EITHER:

    * directory has grown past ``_HANDLE_GC_MIN_FILES`` entries, OR
    * we've written ``_HANDLE_GC_RUN_EVERY`` times since the last pass.

    Both gates are wrapped in try/except so a failure here never poisons
    the response we're about to return; recurring GC breakage surfaces
    under ``ROAM_VERBOSE`` via ``log_swallowed``.
    """
    try:
        _HANDLE_GC_WRITE_COUNTER["n"] += 1
        run_gc = False
        if _HANDLE_GC_WRITE_COUNTER["n"] >= _HANDLE_GC_RUN_EVERY:
            run_gc = True
        else:
            try:
                # Cheap len() check on listdir; bounded by directory size.
                if len(os.listdir(handle_dir)) > _HANDLE_GC_MIN_FILES:
                    run_gc = True
            except OSError:
                run_gc = False
        if run_gc:
            _HANDLE_GC_WRITE_COUNTER["n"] = 0
            _gc_handle_dir(handle_dir)
    except Exception as exc:  # noqa: BLE001 — GC must never break the tool response
        log_swallowed("mcp_server:handle_gc", exc)


def _build_handle_preview(payload: dict) -> dict:
    """Build the tiny ``preview`` dict embedded in the handle envelope.

    Includes (when present):

    * ``summary`` — the full summary dict (already small by contract);
    * ``command`` / ``schema`` / ``schema_version`` / ``version`` —
      orientation metadata;
    * ``sections`` — the names of any compound-envelope sections, so
      the agent knows what's inside without fetching the full payload.

    Kept VERY small so the agent has orientation without a round-trip.
    """
    preview: dict = {}
    if isinstance(payload.get("summary"), dict):
        preview["summary"] = payload["summary"]
    for key in ("command", "schema", "schema_version", "version"):
        if key in payload:
            preview[key] = payload[key]
    # If the payload has a list of "sections" (compound envelope),
    # include their names so the agent knows what's inside.
    if isinstance(payload.get("summary"), dict):
        sections = payload["summary"].get("sections")
        if isinstance(sections, list):
            preview["sections"] = sections
    return preview


def _build_handle_envelope(*, sha: str, size: int, target: Path, tool_name: str, preview: dict) -> dict:
    """Assemble the canonical handle envelope returned in place of a fat payload.

    Shape is the public ``roam-code.com/spec/handle/v1`` contract — agents
    branch on ``is_handle`` to decide whether to call ``roam_fetch_handle``.
    Byte-stable: any change here must update the lock-in tests in
    ``tests/test_mcp_handle_off.py`` and ``tests/test_response_volume_handles.py``.
    """
    return {
        "schema": "roam-code.com/spec/handle/v1",
        "schema_version": "1.0.0",
        "is_handle": True,
        "handle": sha,
        "byte_size": size,
        "stored_at": str(target),
        "tool": tool_name,
        "preview": preview,
        "fetch_with": f"roam_fetch_handle(handle='{sha}')",
        "summary": {
            "verdict": (
                f"large response ({size:,} bytes) stored as handle {sha}; fetch the full payload via roam_fetch_handle"
            ),
            "byte_size": size,
            "handle": sha,
        },
    }


def _maybe_handle_off(payload: dict, *, tool_name: str = "") -> dict:
    """If ``payload`` JSON-serialises larger than the threshold, write
    it to a content-addressed file and return a small handle envelope.
    Otherwise return ``payload`` unchanged.

    Inputs:
      payload     — the dict the @_tool function returned
      tool_name   — passed in by the wrapper; certain tools (notably
                    ``roam_fetch_handle`` itself, and the meta-tool)
                    bypass handle-off to avoid infinite recursion.

    Threshold: ``ROAM_MCP_HANDLE_KB`` env var, default 50KB.
    Set to 0 to disable for all tools.
    """
    if _should_bypass_handle_off(payload, tool_name):
        return payload

    serialised = _serialise_for_handle(payload)
    if serialised is None:
        return payload
    blob, size, sha = serialised

    handle_dir = _handle_storage_dir()
    target = _persist_handle_blob(handle_dir, sha, blob)
    if target is None:
        return payload

    _maybe_run_amortised_gc(handle_dir)

    preview = _build_handle_preview(payload)
    return _build_handle_envelope(sha=sha, size=size, target=target, tool_name=tool_name, preview=preview)


def _wrap_with_handle_off(name: str, fn):
    """Wrap an MCP tool so its return value passes through
    :func:`_maybe_handle_off`. Async-aware so coroutines stay
    coroutines.

    Uses ``functools.wraps`` so the wrapped function keeps its
    annotations + signature — FastMCP / Pydantic introspect those to
    derive the tool's input schema, and a bare ``*args, **kwargs``
    wrapper would break schema generation.
    """
    import functools as _functools
    import inspect as _inspect

    if _inspect.iscoroutinefunction(fn):

        @_functools.wraps(fn)
        async def _async_wrapped(*args, **kwargs):
            r = await fn(*args, **kwargs)
            return _maybe_handle_off(r, tool_name=name)

        return _async_wrapped

    @_functools.wraps(fn)
    def _sync_wrapped(*args, **kwargs):
        r = fn(*args, **kwargs)
        return _maybe_handle_off(r, tool_name=name)

    return _sync_wrapped


# ---------------------------------------------------------------------------
# W196 - McpDecisionReceipt emission for sensitive tool calls
# ---------------------------------------------------------------------------
#
# Per ``(internal memo)`` §"MCP trust boundary"
# (lines 244-262): sensitive tools produce decision receipts so that "who
# invoked what tool with what args, and what did the policy layer decide?"
# is locally verifiable evidence. Receipts live as JSON files under
# ``.roam/mcp_receipts/<run_id>/<tool_call>.json`` (or
# ``.roam/mcp_receipts/_no_run/<tool_call>.json`` when no run is open).
#
# Sensitive = at least one of: ``destructive=True``, ``read_only=False``,
# ``idempotent=False``, ``task_mode="required"``. Read-only / idempotent
# tools (search/symbol/describe/...) skip emission - they are high-volume
# and benign; receipts are for the WRITE audit trail.
#
# Best-effort discipline: a receipt write that fails must NEVER break the
# underlying tool call. The audit trail is opportunistic.


_MCP_RECEIPT_MAX_INLINE_OUTPUT_BYTES = 8 * 1024


def _is_sensitive(meta: dict) -> bool:
    """A tool is 'sensitive' if it writes, mutates state, or is non-idempotent."""
    return (
        meta.get("destructive", False)
        or not meta.get("read_only", True)
        or not meta.get("idempotent", True)
        or meta.get("task_mode") == "required"
    )


def _declared_side_effects_for(meta: dict) -> tuple[str, ...]:
    """Translate ``_TOOL_METADATA`` flags into the receipt's side-effect tuple.

    Order is documented for stability: ``destructive`` wins over ``write``;
    ``non_idempotent`` is appended when ``idempotent=False`` and is independent
    of the destructive/write axis.
    """
    effects: list[str] = []
    if meta.get("destructive", False):
        effects.append("destructive")
    elif not meta.get("read_only", True):
        effects.append("write")
    if not meta.get("idempotent", True):
        effects.append("non_idempotent")
    return tuple(effects)


def _resolve_active_run_id() -> str | None:
    """Best-effort lookup of the currently-active run id.

    Reads ``ROAM_RUN_ID`` first (explicit handle-set signal from the agent /
    harness), then falls back to ``runs.helpers.get_active_run_id`` which
    scans ``.roam/runs/*`` for the newest in-progress run. Returns ``None``
    when no run is open or anything blows up - the caller will route the
    receipt to ``_no_run/`` instead.
    """
    env_id = os.environ.get("ROAM_RUN_ID", "").strip()
    if env_id:
        return env_id
    try:
        from pathlib import Path as _Path

        from roam.db.connection import find_project_root
        from roam.runs.helpers import get_active_run_id

        return get_active_run_id(_Path(find_project_root()))
    except Exception as exc:  # noqa: BLE001 — receipt routing must never break a tool call
        # No run open OR the ledger scan blew up — route the receipt to
        # `_no_run/`. Surface the failure under ROAM_VERBOSE so a real
        # ledger-scan breakage is distinguishable from "no run is active".
        log_swallowed("mcp_server:resolve_active_run_id", exc)
        return None


def _mcp_receipts_root() -> "Path":
    """Repo-local home for MCP decision receipts.

    Lives under ``<repo_root>/.roam/mcp_receipts/`` so a receipt sits
    alongside the run ledger it links to. Falls back to ``.roam/`` under the
    current working directory when ``find_project_root`` cannot locate a
    ``.git`` root (rare; e.g. running ``roam mcp`` outside any repo).
    """
    try:
        from roam.db.connection import find_project_root

        root = Path(find_project_root())
    except Exception as exc:  # noqa: BLE001 — receipt-root resolution must never break a tool call
        # No `.git` root found (running `roam mcp` outside a repo) OR the
        # resolver raised — fall back to CWD. Surface under ROAM_VERBOSE so a
        # real find_project_root regression doesn't hide as "outside a repo".
        log_swallowed("mcp_server:mcp_receipts_root", exc)
        root = Path(".").resolve()
    return root / ".roam" / "mcp_receipts"


def _receipt_serialize_args(
    args: "Mapping[str, object] | None",  # noqa: F821 — string annotation; `from __future__ import annotations` keeps it lazy
) -> dict[str, object]:
    """JSON-safe view of the input args used for the receipt's input_hash.

    Drops the FastMCP ``Context`` object (``ctx`` key) and falls back to
    ``repr(v)`` for any value that fails ``json.dumps``. The goal is a
    stable hash, not a faithful replay of the call."""
    safe_args: dict[str, object] = {}
    for k, v in (args or {}).items():
        if k == "ctx":
            continue
        try:
            json.dumps(v)
            safe_args[k] = v
        except (TypeError, ValueError):
            safe_args[k] = repr(v)
    return safe_args


def _receipt_resolve_output(state: dict) -> tuple[str | None, str | None]:
    """Resolve ``(output_ref, output_hash)`` for the receipt.

    Caller-provided values on ``state`` win. When both are absent and a
    ``result`` was captured, compute from the canonical bytes: small
    payloads hash inline (sha256), large payloads with an active handle
    become an ``"handle:<id>"`` output_ref, otherwise the canonical-bytes
    hash is still computed so the receipt always carries a fingerprint.
    The dataclass enforces the output_ref OR output_hash invariant."""
    output_ref = state.get("output_ref")
    output_hash = state.get("output_hash")
    if output_ref is not None or output_hash is not None:
        return output_ref, output_hash
    result = state.get("result")
    if result is None:
        return None, None
    try:
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":"))
        payload = canonical.encode("utf-8")
        import hashlib as _hashlib

        if len(payload) <= _MCP_RECEIPT_MAX_INLINE_OUTPUT_BYTES:
            return None, _hashlib.sha256(payload).hexdigest()
        # Large result: prefer the handle-off layer's path; else hash the
        # canonical bytes so the receipt still carries a fingerprint.
        if isinstance(result, dict) and result.get("is_handle"):
            handle = result.get("summary", {}).get("handle")
            if isinstance(handle, str) and handle:
                return f"handle:{handle}", None
        return None, _hashlib.sha256(payload).hexdigest()
    except (TypeError, ValueError):
        return None, None


def _receipt_build_extra(state: dict) -> dict[str, object]:
    """Assemble the receipt's ``extra`` dict from MCP-P0.1 / P1.1 / P1.2 lineage.

    Each block is omitted when its source state is absent so pre-feature
    receipts stay byte-identical (hash-stable across feature waves):
    - ``redaction_details`` (MCP-P0.1) — per-pattern egress-redaction hits.
    - ``injection_markers`` (MCP-P1.2) — per-marker prompt-injection hits;
      bytes were NOT altered, so this rides as a signal not a redaction.
    - ``shadow_mode`` + ``would_deny_reason`` (MCP-P1.1) — only when the
      gate short-circuited a deny under ``ROAM_MODE_DRY_RUN``."""
    extra: dict[str, object] = {}
    redaction_details = state.get("redaction_details") or {}
    if redaction_details:
        extra["redaction_details"] = dict(redaction_details)
    injection_markers = state.get("injection_markers") or {}
    if injection_markers:
        extra["injection_markers"] = dict(injection_markers)
    if state.get("shadow_mode"):
        extra["shadow_mode"] = True
        extra["would_deny_reason"] = state.get("would_deny_reason") or ""
    return extra


def _receipt_resolve_required_mode(state: dict, meta: dict) -> str:
    """MCP-P0.2: source ``required_mode`` from the 4-mode policy gate (closed
    enum: read_only/safe_edit/migration/autonomous_pr). Falls back to a
    side-effect-based default when the receipt was emitted from a code path
    that bypassed the gate (e.g. a direct ``_mcp_receipt_for`` use in a
    test). Distinct from the ``task_mode`` axis (required/optional/None)
    which historically poisoned this field."""
    required_mode = state.get("required_mode")
    if required_mode:
        return required_mode
    return _required_mode_from_side_effects(meta)


def _receipt_link_to_ledger(
    run_id: str,
    tool_name: str,
    tool_call_id: str,
    canonical: str,
) -> None:
    """MCP-P0.3 — HMAC-link the on-disk receipt to the signed event stream.

    Appends ONE ledger event carrying the sha256 of the canonical receipt
    bytes; the rolling-HMAC chain then locks that hash into the chain.
    ``verify_chain`` walks these events, re-hashes the on-disk receipts,
    and reports mismatch / missing / not_linked sub-states.

    Best-effort: any failure here is swallowed so an audit-trail outage
    cannot break the tool call. The receipt is still on disk;
    ``verify_chain`` will report ``receipt_integrity="not_linked"`` for
    it. ROAM_VERBOSE surfaces the failure so a regression doesn't silently
    lose tamper-evidence."""
    try:
        import hashlib as _hashlib

        from roam.db.connection import find_project_root
        from roam.runs.ledger import log_event

        receipt_hash = _hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        log_event(
            Path(find_project_root()),
            run_id,
            action="mcp_receipt",
            tool_name=tool_name,
            tool_call=tool_call_id,
            receipt_hash=receipt_hash,
        )
    except Exception as exc:  # noqa: BLE001 — ledger-link failure must never break the tool call
        log_swallowed("mcp_server:receipt_ledger_link", exc)


def _write_mcp_receipt(
    tool_name: str,
    args: "Mapping[str, object]",  # noqa: F821 — string annotation; `from __future__ import annotations` keeps it lazy, no runtime import needed
    state: dict,
) -> None:
    """Construct an ``McpDecisionReceipt`` and atomically write it to disk.

    Raises on any failure; the public ``_mcp_receipt_for`` context manager
    swallows the exception so an audit-trail failure cannot break the tool
    call. Implementation is split across ``_receipt_*`` helpers; this
    orchestrator wires them together and emits the canonical receipt +
    optional ledger link. Hash stability is preserved across the split:
    helper outputs are assembled in the same order as the legacy inline
    body, so existing receipts hash byte-identically (W210 discipline).
    """
    import uuid as _uuid

    from roam.atomic_io import atomic_write_text
    from roam.evidence.mcp_receipt import McpDecisionReceipt, hash_input_args

    meta = _TOOL_METADATA.get(tool_name, {})
    safe_args = _receipt_serialize_args(args)
    tool_call_id = f"{tool_name}_{_uuid.uuid4().hex[:12]}"
    input_hash = hash_input_args(safe_args)
    run_id = _resolve_active_run_id()
    output_ref, output_hash = _receipt_resolve_output(state)
    extra = _receipt_build_extra(state)
    required_mode = _receipt_resolve_required_mode(state, meta)
    # MCP-P0.2: ``policy_decision`` defaults to ``"not_evaluated"`` when no
    # gate ran — the dataclass refuses unknown verbs.
    decision = state.get("policy_decision") or "not_evaluated"
    receipt = McpDecisionReceipt(
        tool_call=tool_call_id,
        client_id=os.environ.get("ROAM_MCP_CLIENT_ID", "<unknown>"),
        tool_name=tool_name,
        actor_ref_id=os.environ.get("ROAM_AGENT_ID"),
        declared_side_effects=_declared_side_effects_for(meta),
        required_mode=required_mode,
        input_hash=input_hash,
        policy_decision=decision,
        output_ref=output_ref,
        output_hash=output_hash,
        run_event_id=run_id,
        redactions=tuple(state.get("redactions") or ()),
        extra=extra,
    )

    bucket = run_id if run_id else "_no_run"
    target = _mcp_receipts_root() / bucket / f"{tool_call_id}.json"
    canonical = receipt.to_canonical_json()
    atomic_write_text(target, canonical + "\n")
    if run_id:
        _receipt_link_to_ledger(run_id, tool_name, tool_call_id, canonical)


import contextlib as _contextlib


@_contextlib.contextmanager
def _mcp_receipt_for(
    tool_name: str,
    args: "Mapping[str, object]",  # noqa: F821 — string annotation; `from __future__ import annotations` keeps it lazy
):
    """Emit an ``McpDecisionReceipt`` for a sensitive tool call.

    Yields a mutable dict the caller can populate with output info
    (``output_ref`` OR ``output_hash``, ``policy_decision``). On exit, writes
    the receipt to disk best-effort: errors are swallowed so an audit-trail
    failure never breaks the tool call itself.

    For read-only tools the helper yields an empty dict and writes nothing -
    receipts are reserved for the WRITE audit trail.
    """
    meta = _TOOL_METADATA.get(tool_name, {})
    if not _is_sensitive(meta):
        yield {}
        return

    receipt_state: dict = {
        # MCP-P0.2: default "not_evaluated" so an unguarded surface honestly
        # reports the absence of a policy decision rather than synthesising
        # "allow". The gate inside ``_wrap_with_receipt`` overwrites this
        # with the real decision when it runs.
        "policy_decision": "not_evaluated",
        # MCP-P0.2: ``required_mode`` is filled by the gate using the
        # closed enum from :data:`roam.modes.policy.VALID_MODES`
        # (read_only/safe_edit/migration/autonomous_pr). The legacy field
        # used to be sourced from ``meta["task_mode"]`` which is the
        # task-mode taxonomy (required/optional/None) — wrong axis.
        "required_mode": None,
        "output_ref": None,
        "output_hash": None,
        "result": None,
        "redactions": (),
        "redaction_details": {},
        # MCP-P1.2 — per-marker prompt-injection hit counts; populated by
        # the egress scan in ``_wrap_with_receipt`` when a marker fires.
        "injection_markers": {},
    }
    try:
        yield receipt_state
    finally:
        try:
            _write_mcp_receipt(tool_name, args, receipt_state)
        except Exception as exc:  # noqa: BLE001 — receipts must never break the tool call
            # Best-effort: receipts must NEVER break the tool call. Surface
            # under ROAM_VERBOSE — a silent receipt-write failure means the
            # WRITE audit trail has a hole no one is told about.
            log_swallowed("mcp_server:write_mcp_receipt", exc)


def _should_skip_cold_start_guard(name: str) -> bool:
    """Return True when the cold-start guard should be a pass-through.

    Two early-exits collapse here: (1) the preflight helper module
    failed to import (best-effort -- never break the MCP surface), and
    (2) the tool is in :data:`_NO_INDEX_NEEDED` so an index is not a
    precondition (bootstrap / metadata / file-path tools).
    """
    if _mcp_preflight is None:
        return True
    return not _mcp_preflight.needs_index(name)


def _check_cold_start(name: str, kwargs: dict):
    """Probe ``root`` from kwargs and return a cold-start envelope or None.

    Returns the structured "no index yet" envelope when the index is
    missing so the wrapper can short-circuit; returns ``None`` when the
    tool should proceed to its real body.
    """
    root = kwargs.get("root", ".")
    return _mcp_preflight.maybe_cold_start_envelope(name, root)


def _wrap_with_cold_start_guard(name: str, fn):
    """Wrap an MCP tool with the W296 "no index yet" short-circuit.

    Per CLAUDE.md Pattern 1 anti-pattern ("JSON-parse-on-empty-input"):
    every MCP tool MUST return a structured envelope the client can act on.
    On a fresh project where ``.roam/index.db`` does not exist, the
    underlying ``_run_roam`` call would auto-trigger a full index build
    that typically exceeds the MCP client's call timeout, leaving the
    client to time out with no signal.

    This wrapper short-circuits BEFORE any other wrapper runs (it is the
    outermost layer applied last in the decorator chain, so it executes
    first on dispatch). When the tool is in :data:`_NO_INDEX_NEEDED` --
    i.e. it is a bootstrap / metadata / file-path tool -- the wrapper is
    a pass-through (zero overhead beyond a single set-membership check).

    Cost on the hot path (index already built): one ``Path.exists()``
    call. The result is NOT cached at the module level because an agent
    might run ``roam init`` mid-session and the next tool call should
    immediately stop returning the cold-start envelope.
    """
    import functools as _functools
    import inspect as _inspect

    if _should_skip_cold_start_guard(name):
        return fn

    if _inspect.iscoroutinefunction(fn):

        @_functools.wraps(fn)
        async def _async_cold_start_wrapped(*args, **kwargs):
            envelope = _check_cold_start(name, kwargs)
            if envelope is not None:
                return envelope
            return await fn(*args, **kwargs)

        return _async_cold_start_wrapped

    @_functools.wraps(fn)
    def _sync_cold_start_wrapped(*args, **kwargs):
        envelope = _check_cold_start(name, kwargs)
        if envelope is not None:
            return envelope
        return fn(*args, **kwargs)

    return _sync_cold_start_wrapped


def _exception_envelope(name: str, exc: Exception) -> dict:
    """Build the canonical ``_structured_error`` envelope for a backstop exception."""
    return _structured_error(
        {
            "command": name,
            "error": str(exc) or exc.__class__.__name__,
            "error_code": "UNKNOWN",
            "hint": "an unexpected error occurred inside the tool — retry or report it.",
        }
    )


def _build_async_exception_wrapper(name: str, fn):
    """Wrap an async ``fn`` so any escaped ``Exception`` becomes the canonical envelope."""
    import functools as _functools

    @_functools.wraps(fn)
    async def _async_exception_wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — deliberate backstop
            return _exception_envelope(name, exc)

    return _async_exception_wrapped


def _build_sync_exception_wrapper(name: str, fn):
    """Wrap a sync ``fn`` so any escaped ``Exception`` becomes the canonical envelope."""
    import functools as _functools

    @_functools.wraps(fn)
    def _sync_exception_wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — deliberate backstop
            return _exception_envelope(name, exc)

    return _sync_exception_wrapped


def _wrap_with_exception_envelope(name: str, fn):
    """Backstop: convert an uncaught tool-body exception into a structured envelope.

    Pattern-1 conformance (CLAUDE.md "canonical failure envelope"): no
    other layer in the ``@_tool`` decorator stack converts a generic
    uncaught ``Exception`` raised inside a tool body into a structured
    envelope the MCP client can branch on. Without this backstop a tool
    that raises propagates as a protocol-level error, which does NOT
    reliably reach the LLM context window.

    This is a BACKSTOP, not a replacement — every existing per-tool /
    per-helper ``except`` block runs first and only an exception that
    escapes ALL of them reaches here. The result is a
    ``_structured_error`` envelope with ``error_code="UNKNOWN"`` (which
    maps to ``status="hard_failure"``), so the agent gets ``isError`` +
    ``status`` + a copy-pasteable ``command`` instead of a dropped call.

    Catches :class:`Exception` only — :class:`SystemExit` /
    :class:`KeyboardInterrupt` (which subclass ``BaseException``) pass
    through untouched so process-control signals are never swallowed.
    """
    import inspect as _inspect

    if _inspect.iscoroutinefunction(fn):
        return _build_async_exception_wrapper(name, fn)
    return _build_sync_exception_wrapper(name, fn)


# ---------------------------------------------------------------------------
# MCP-P0.2 — 4-mode policy enforcement at the MCP boundary
# ---------------------------------------------------------------------------
#
# The 4-mode substrate (read_only / safe_edit / migration / autonomous_pr) is
# CLI-only via ``cli._enforce_mode_gate``. In-process MCP dispatch bypasses
# that gate because ``_run_roam_inprocess`` invokes the CLI through Click's
# CliRunner which DOES re-enter the gate — BUT the gate only fires when a
# fresh ``ROAM_MODE_ENFORCEMENT=1`` env-var is set. The MCP server needs its
# own gate so a single ``ROAM_MODE_ENFORCEMENT=1`` toggle covers both
# surfaces, and so the gate's decision can be folded into the
# ``McpDecisionReceipt`` (``policy_decision`` field) without going through a
# CLI subprocess.
#
# Naming convention (W961): MCP tool names use ``roam_<name>`` with
# underscores; the policy / capability registry uses ``<name>`` with dashes.
# The 4 historical renames live in ``_NAMING_DRIFT_ALIAS`` (see
# tests/test_w954_core_tools_capability_drift.py); everything else maps via
# the uniform ``removeprefix("roam_").replace("_", "-")`` rule.
#
# Required-mode derivation: walk ``VALID_MODES`` from lowest tier to highest
# and return the FIRST mode whose allow-list contains the CLI command name.
# Falls back to side-effect-based defaults when the command isn't in any
# mode's allow-list (a typo, or a tool whose backing CLI command was renamed
# without updating the policy).


_MODE_BLOCKED_ERROR_CODE = "MODE_BLOCKED"


# MCP-P1.1 — shadow-mode flag (``ROAM_MODE_DRY_RUN``).
#
# Operators previewing the 4-mode enforcement policy in production need a
# way to see WHAT the gate would block without actually blocking it. When
# ``ROAM_MODE_DRY_RUN`` is set to a truthy value, the policy path runs
# normally (so ``required_mode`` / ``active_mode`` populate as usual) but
# the deny branch in ``_wrap_with_receipt`` is short-circuited: the tool
# call proceeds, the receipt records ``policy_decision="would_deny_dry_run"``
# (extended closed enum), and ``extra["shadow_mode"] = True`` +
# ``extra["would_deny_reason"]`` capture the original deny reason for
# audit. A single WARN line per dry-run-blocked call lets operators grep
# ledgers ahead of flipping enforcement.
#
# Allow paths are unchanged under dry-run (no marker, no log) — observe-
# only rollout cares about what WOULD have been blocked, not what was
# already allowed. Enforcement-off (the steady-state advisory path) is
# also unchanged when dry-run is off: pre-P1.1 receipts stay byte-
# identical, satisfying the hash-stability discipline (W210 omit-when-
# default pattern, ledger-event layer).
_DRY_RUN_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _is_mode_dry_run() -> bool:
    """Return True when ``ROAM_MODE_DRY_RUN`` names a truthy value.

    Accepts ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive,
    surrounding whitespace stripped). Anything else — empty string,
    unset, ``0``, ``false`` — returns False. The read is intentionally
    NOT cached at module-import time because operators flipping the flag
    mid-process (e.g. in test fixtures) should see the change on the
    next tool call.
    """
    raw = os.environ.get("ROAM_MODE_DRY_RUN", "")
    return raw.strip().lower() in _DRY_RUN_TRUTHY


def _log_mode_dry_run_would_deny(tool_name: str, reason: str) -> None:
    """Emit a single WARN line per dry-run-blocked call so operators can grep.

    Uses ``logging.getLogger(__name__)`` so the line lands in the same
    logging surface as the rest of the MCP server. Best-effort: any
    logging failure is swallowed (the policy path must never break a
    tool call).
    """
    try:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "mcp.mode_policy.dry_run: tool=%s would_deny reason=%s",
            tool_name,
            reason,
        )
    except Exception as exc:  # noqa: BLE001 — the policy path must never break a tool call
        # A logging failure must not break the tool call. Route through the
        # swallow-logger so even a broken logging surface leaves a trace
        # under ROAM_VERBOSE rather than vanishing entirely.
        log_swallowed("mcp_server:log_mode_dry_run_would_deny", exc)


# Mirrors tests/test_w954_core_tools_capability_drift.py. Kept local to
# avoid coupling production code to a test module; the lint in that test
# pins both tables in sync.
_MCP_TO_CLI_RENAME_ALIAS: dict[str, str] = {
    "roam_dead_code": "dead",
    "roam_complexity_report": "complexity",
    "roam_search_symbol": "search",
    "roam_file_info": "file",
}


def _mcp_tool_to_cli_command(tool_name: str) -> str:
    """Map an MCP tool name (``roam_<x>``) to its policy / CLI command name.

    The 4 historical renames live in :data:`_MCP_TO_CLI_RENAME_ALIAS`; every
    other tool follows the uniform "strip ``roam_`` prefix + swap ``_`` for
    ``-``" convention (W961).
    """
    if tool_name in _MCP_TO_CLI_RENAME_ALIAS:
        return _MCP_TO_CLI_RENAME_ALIAS[tool_name]
    return tool_name.removeprefix("roam_").replace("_", "-")


def _required_mode_from_side_effects(meta: dict) -> str:
    """Derive the conservative required mode from the tool's metadata flags.

    Closed enum matching :data:`roam.modes.policy.VALID_MODES`:

    * destructive=True → ``migration`` (move/rename/extract live here)
    * read_only=False (writes but not destructive) → ``safe_edit``
    * read_only=True, idempotent=True → ``read_only``
    * read_only=True, idempotent=False → ``safe_edit`` (non-idempotent
      reads carry hidden state changes — fresh UUID, write to ``.roam/runs/``,
      etc. — and should not be free in read_only mode)
    """
    if meta.get("destructive", False):
        return "migration"
    if not meta.get("read_only", True):
        return "safe_edit"
    if not meta.get("idempotent", True):
        return "safe_edit"
    return "read_only"


def _resolve_required_mode_for_tool(tool_name: str, repo_root) -> str:
    """Return the lowest mode that allows *tool_name* under the active policy.

    Walks :data:`roam.modes.policy.VALID_MODES` in cumulative order. Falls
    back to :func:`_required_mode_from_side_effects` when no mode allows the
    underlying CLI command (typo, renamed command, etc.) so the receipt's
    ``required_mode`` field is never empty.
    """
    meta = _TOOL_METADATA.get(tool_name, {})
    fallback = _required_mode_from_side_effects(meta)
    try:
        from roam.modes.policy import VALID_MODES, list_modes
    except Exception as exc:  # noqa: BLE001 — mode resolution must never break a tool call
        # roam.modes.policy unavailable — fall back to the side-effect-derived
        # mode. Surface under ROAM_VERBOSE: a missing policy module is a real
        # substrate breakage, not a routine absence.
        log_swallowed("mcp_server:resolve_required_mode:import", exc)
        return fallback
    cli_name = _mcp_tool_to_cli_command(tool_name)
    try:
        policies = list_modes(repo_root)
    except Exception as exc:  # noqa: BLE001 — mode resolution must never break a tool call
        # Policy files unreadable/malformed — fall back to the side-effect
        # mode. Surface under ROAM_VERBOSE so a corrupt .roam/ policy set is
        # distinguishable from the no-policy-configured default path.
        log_swallowed("mcp_server:resolve_required_mode:list_modes", exc)
        return fallback
    for mode_name in VALID_MODES:
        policy = policies.get(mode_name)
        if policy is None:
            continue
        if cli_name in policy.allowed_commands:
            return mode_name
    return fallback


def _evaluate_mcp_mode_policy(tool_name: str) -> dict:
    """Run the MCP-boundary mode gate for *tool_name*.

    Returns a dict with:

    * ``decision``: one of ``"allow"`` / ``"deny"`` (matches the closed
      enum in :data:`roam.evidence.mcp_receipt._POLICY_DECISIONS`).
    * ``enforcement``: bool — whether ``ROAM_MODE_ENFORCEMENT`` is on.
    * ``active_mode``: name of the resolved active mode (string).
    * ``required_mode``: lowest mode that allows the underlying command.
    * ``reason``: human-readable explanation when ``decision`` is ``"deny"``;
      empty string otherwise.

    All exceptions are swallowed — a policy-check failure must NEVER break
    the tool call. On any unexpected error the gate fails OPEN
    (``decision="allow"``).
    """
    enforcement_raw = os.environ.get("ROAM_MODE_ENFORCEMENT", "").strip()
    enforcement = enforcement_raw in {"1", "true", "yes", "on"}
    try:
        from roam.db.connection import find_project_root
        from roam.modes.policy import check_command_allowed, resolve_mode
    except Exception as exc:  # noqa: BLE001 — policy check must never break a tool call
        # Policy substrate unavailable — gate fails OPEN. Surface under
        # ROAM_VERBOSE: a missing policy module silently disables the entire
        # MCP-boundary mode gate, which is a security-relevant degradation.
        log_swallowed("mcp_server:evaluate_mode_policy:import", exc)
        return {
            "decision": "allow",
            "enforcement": enforcement,
            "active_mode": "",
            "required_mode": _required_mode_from_side_effects(_TOOL_METADATA.get(tool_name, {})),
            "reason": "",
        }
    try:
        repo_root = find_project_root()
    except Exception as exc:  # noqa: BLE001 — policy check must never break a tool call
        # No repo root — gate fails OPEN. Surface under ROAM_VERBOSE so a
        # find_project_root regression doesn't masquerade as "outside a repo".
        log_swallowed("mcp_server:evaluate_mode_policy:find_root", exc)
        return {
            "decision": "allow",
            "enforcement": enforcement,
            "active_mode": "",
            "required_mode": _required_mode_from_side_effects(_TOOL_METADATA.get(tool_name, {})),
            "reason": "",
        }
    try:
        active = resolve_mode(repo_root)
        active_name = active.name
    except Exception as exc:  # noqa: BLE001 — policy check must never break a tool call
        # Active-mode resolution failed — proceed with an empty active mode.
        # Surface under ROAM_VERBOSE: a corrupt mode file silently weakens
        # the gate's view of what mode the agent is actually in.
        log_swallowed("mcp_server:evaluate_mode_policy:resolve_mode", exc)
        active_name = ""
    cli_name = _mcp_tool_to_cli_command(tool_name)
    try:
        allowed, reason = check_command_allowed(repo_root, cli_name)
    except Exception as exc:  # noqa: BLE001 — policy check must never break a tool call
        # check_command_allowed raised — gate fails OPEN (allowed=True).
        # Surface under ROAM_VERBOSE so a policy-evaluation crash doesn't
        # silently turn the gate into a no-op for this tool.
        log_swallowed("mcp_server:evaluate_mode_policy:check_allowed", exc)
        allowed, reason = True, ""
    required_mode = _resolve_required_mode_for_tool(tool_name, repo_root)
    return {
        "decision": "allow" if allowed else "deny",
        "enforcement": enforcement,
        "active_mode": active_name,
        "required_mode": required_mode,
        "reason": "" if allowed else reason,
    }


def _build_mode_blocked_envelope(tool_name: str, policy_result: dict) -> dict:
    """Pattern-1 canonical envelope for a MODE_BLOCKED denial.

    Shape matches CLAUDE.md ``canonical failure envelope`` (isError inside
    the result, copy-pasteable ``next_command``, imperative ``hint``,
    LAW-4 concrete-noun-anchored ``agent_contract.facts``).
    """
    required = policy_result.get("required_mode") or "safe_edit"
    active = policy_result.get("active_mode") or "<unknown>"
    cli_name = _mcp_tool_to_cli_command(tool_name)
    reason = policy_result.get("reason") or (
        f"'{cli_name}' not allowed in {active} mode; run `roam mode {required}` to enable it"
    )
    envelope = {
        "command": tool_name,
        "status": "hard_failure",
        "isError": True,
        "summary": {
            "verdict": f"BLOCKED: '{cli_name}' requires {required} mode (active: {active})",
            "level": "blocker",
            "partial_success": False,
            "state": "mode_blocked",
        },
        "error_code": _MODE_BLOCKED_ERROR_CODE,
        "error": reason,
        "hint": f"Run `roam mode {required}` to switch into a mode that allows this tool.",
        "next_command": f"roam mode {required}",
        "agent_contract": {
            "facts": [
                f"active mode: {active}",
                f"required mode: {required}",
                f"blocked tool: {tool_name}",
            ],
            "next_commands": [
                f"roam mode {required}",
                f"# then re-run {tool_name}",
            ],
        },
        "_meta": {
            "policy_decision": "deny",
            "policy_active_mode": active,
            "policy_required_mode": required,
        },
    }
    return _structured_error(envelope)


def _redact_result_for_egress(result):
    """MCP-P0.1 — scrub secret-shaped strings from a tool result before egress.

    Walks the tool's return value (dict/list/tuple/string) and replaces any
    string matching a producer-boundary secret pattern with ``[REDACTED]``
    before the bytes cross the MCP boundary. Returns
    ``(redacted_result, hit_counts_by_pattern)``; ``hit_counts_by_pattern``
    is empty when nothing fired. Non-string scalars (int / bool / None /
    floats — including ``_meta.cli_exit_code``) ride through untouched, so
    the wrapper-bridge passthrough behavior stays intact.

    Defensive: any exception is swallowed and the original result is
    returned unredacted (with empty counts). Egress redaction must never
    break the tool call itself — the audit-trail and security boundary
    are best-effort, like the rest of ``_wrap_with_receipt``.
    """
    try:
        from roam.security.redact import redact_secrets_in_value

        return redact_secrets_in_value(result)
    except Exception as exc:  # noqa: BLE001 — egress redaction must never break the tool call
        # Redaction failed — ship the result UNREDACTED rather than break the
        # call. Surface under ROAM_VERBOSE: a silent failure here means a
        # secret-shaped string could cross the MCP boundary unredacted with
        # nobody told the scrubber didn't run.
        log_swallowed("mcp_server:redact_result_for_egress", exc)
        return result, {}


def _scan_result_for_injection_markers(result):
    """MCP-P1.2 — scan a tool result for prompt-injection markers before egress.

    Walks the tool's return value (dict/list/tuple/string) and matches a
    conservative set of known prompt-injection markers (override phrases,
    chat-template control tokens, spoofed turn headers, tool-result spoof
    tags — see ``roam.security.redact.PROMPT_INJECTION_MARKERS``). Returns a
    ``{marker_id: hit_count}`` dict; empty when nothing fired.

    Unlike :func:`_redact_result_for_egress`, this scan NEVER alters the
    output bytes — a prompt-injection marker is a *signal*, not a secret.
    The caller stamps the closed-enum reason ``"prompt_injection_marker"``
    onto the receipt's ``redactions[]`` audit trail when the dict is
    non-empty, leaving the offending bytes intact for a downstream gateway
    / host to inspect.

    Defensive: any exception is swallowed and an empty dict is returned.
    The egress marker scan must never break the tool call itself — like
    the rest of ``_wrap_with_receipt`` it is best-effort.
    """
    try:
        from roam.security.redact import scan_prompt_injection_in_value

        return scan_prompt_injection_in_value(result)
    except Exception as exc:  # noqa: BLE001 — egress marker scan must never break the tool call
        # Scan failed — return no markers rather than break the call. Surface
        # under ROAM_VERBOSE: a silent failure here means prompt-injection
        # markers go undetected with no entry in the receipt's redactions[].
        log_swallowed("mcp_server:scan_result_for_injection_markers", exc)
        return {}


def _stamp_egress_redactions(state, secret_hits, injection_hits):
    """Stamp egress-scan lineage onto the MCP receipt state (P0.1 + P1.2).

    Builds the receipt's ``redactions`` tuple from the two egress scans:

    * ``secret_hits`` (P0.1) — secret-pattern hit counts; a non-empty dict
      adds the closed-enum reason ``"secret"``.
    * ``injection_hits`` (P1.2) — prompt-injection marker hit counts; a
      non-empty dict adds the closed-enum reason
      ``"prompt_injection_marker"``.

    Both reasons are members of ``REDACTION_REASONS`` so the
    ``McpDecisionReceipt`` construction-time validator accepts the tuple.
    Per-pattern / per-marker counts ride in ``extra`` (``redaction_details``
    for secrets, ``injection_markers`` for markers) so the closed-enum
    invariant on ``redactions`` holds while the detail is still auditable.

    The order is stable (``secret`` before ``prompt_injection_marker``) so
    a receipt's canonical JSON is byte-deterministic for a given scan
    outcome.
    """
    reasons: list[str] = []
    if secret_hits:
        reasons.append("secret")
        state["redaction_details"] = secret_hits
    if injection_hits:
        reasons.append("prompt_injection_marker")
        # Per-marker counts ride in ``extra`` — the ``redactions`` tuple
        # itself stays inside the closed REDACTION_REASONS enum.
        state["injection_markers"] = injection_hits
    if reasons:
        state["redactions"] = tuple(reasons)


def _wrap_with_receipt(name: str, fn):
    """Wrap an MCP tool so a decision receipt is emitted per sensitive call.

    Non-sensitive (read-only + idempotent + no required task mode) tools are
    returned unchanged so we don't pay the overhead. Sensitive tools emit a
    receipt to ``.roam/mcp_receipts/<run_id>/<tool_call>.json`` on every
    invocation - capturing input args hash, declared side effects, the
    resolved active run id, and the output hash (or handle ref for large
    outputs).

    MCP-P0.1 egress redaction (W195): for sensitive tools, the result is
    scrubbed of producer-boundary secret patterns BEFORE returning to the
    MCP client AND before the receipt's ``output_hash`` is computed — so
    the client never sees a verbatim secret and the hash reflects what
    the client actually received.

    MCP-P0.2 mode enforcement (W196.2): before invoking ``fn``, run the
    4-mode policy gate. When ``ROAM_MODE_ENFORCEMENT=1`` AND the active mode
    does not allow the tool, return a Pattern-1 ``MODE_BLOCKED`` envelope
    without invoking the tool. When enforcement is off (default), proceed
    but record ``policy_decision="deny"`` on the receipt (advisory-shadow
    mode) so an audit trail shows what WOULD have been blocked.

    The redaction wiring (P0.1) is preserved on the allow path; on the deny
    path the tool never runs, so the egress walk only sees the
    MODE_BLOCKED envelope (which contains no tool-side payload).
    """
    import functools as _functools
    import inspect as _inspect

    meta = _TOOL_METADATA.get(name, {})
    if not _is_sensitive(meta):
        return fn

    if _inspect.iscoroutinefunction(fn):

        @_functools.wraps(fn)
        async def _async_receipt_wrapped(*args, **kwargs):
            with _mcp_receipt_for(name, kwargs) as state:
                policy = _evaluate_mcp_mode_policy(name)
                state["policy_decision"] = policy["decision"]
                state["required_mode"] = policy["required_mode"]
                # MCP-P1.1 — shadow-mode short-circuit. When dry-run is ON
                # and the gate would normally deny, stamp the receipt with
                # the ``would_deny_dry_run`` verdict + shadow markers and
                # let the tool call proceed for observe-only rollout. The
                # allow path is untouched.
                if policy["decision"] == "deny" and policy["enforcement"] and _is_mode_dry_run():
                    reason = policy.get("reason") or ""
                    state["policy_decision"] = "would_deny_dry_run"
                    state["shadow_mode"] = True
                    state["would_deny_reason"] = reason
                    _log_mode_dry_run_would_deny(name, reason)
                elif policy["decision"] == "deny" and policy["enforcement"]:
                    envelope = _build_mode_blocked_envelope(name, policy)
                    state["result"] = envelope
                    return envelope
                result = await fn(*args, **kwargs)
                redacted, hits = _redact_result_for_egress(result)
                # MCP-P1.2 — scan the (secret-redacted) bytes for prompt-
                # injection markers. Non-mutating: the marker scan only
                # annotates the receipt, the output bytes are unchanged.
                injection_hits = _scan_result_for_injection_markers(redacted)
                state["result"] = redacted
                _stamp_egress_redactions(state, hits, injection_hits)
                return redacted

        return _async_receipt_wrapped

    @_functools.wraps(fn)
    def _sync_receipt_wrapped(*args, **kwargs):
        with _mcp_receipt_for(name, kwargs) as state:
            policy = _evaluate_mcp_mode_policy(name)
            state["policy_decision"] = policy["decision"]
            state["required_mode"] = policy["required_mode"]
            # MCP-P1.1 — see async branch above for rationale. Both paths
            # share the same shadow-mode semantics; the only difference is
            # the ``await`` keyword on the underlying tool call.
            if policy["decision"] == "deny" and policy["enforcement"] and _is_mode_dry_run():
                reason = policy.get("reason") or ""
                state["policy_decision"] = "would_deny_dry_run"
                state["shadow_mode"] = True
                state["would_deny_reason"] = reason
                _log_mode_dry_run_would_deny(name, reason)
            elif policy["decision"] == "deny" and policy["enforcement"]:
                envelope = _build_mode_blocked_envelope(name, policy)
                state["result"] = envelope
                return envelope
            result = fn(*args, **kwargs)
            redacted, hits = _redact_result_for_egress(result)
            # MCP-P1.2 — scan the (secret-redacted) bytes for prompt-
            # injection markers. Non-mutating: the marker scan only
            # annotates the receipt, the output bytes are unchanged.
            injection_hits = _scan_result_for_injection_markers(redacted)
            state["result"] = redacted
            _stamp_egress_redactions(state, hits, injection_hits)
            return redacted

    return _sync_receipt_wrapped


def _structured_error(error_dict: dict) -> dict:
    """Wrap error dict with MCP-compliant structured error fields (#116, #117).

    also fills the ``doc_link`` field so agents have a stable
    URL for self-service troubleshooting per error code.
    adds a ``severity`` field (info | warning | error | fatal)
    so agents can branch on severity without parsing the message.
    when the same ``error_code`` fires ≥
    ``_ERROR_STORM_THRESHOLD`` times in a row, drop the verbose fields
    on subsequent fires to save tokens in agent loops while preserving
    command identity when available.
    """
    error_dict["isError"] = True
    code = error_dict.get("error_code", "UNKNOWN")
    command = error_dict.get("command")
    command_key = f"{code}{_FIRST_ERROR_COMMAND_SEP}{command}" if isinstance(command, str) and command else None
    error_dict["retryable"] = code in _RETRYABLE_CODES
    error_dict["suggested_action"] = error_dict.get("hint", "check the error message")
    error_dict.setdefault("doc_link", _DOC_LINKS.get(code, _DOC_LINKS["UNKNOWN"]))
    error_dict.setdefault("severity", _SEVERITY_MAP.get(code, "error"))
    # Pattern-1 conformance — stamp the canonical closed-enum ``status``.
    # ``setdefault`` so an explicit caller-supplied status (e.g.
    # ``partial_failure`` on a COMMAND_FAILED envelope that completed
    # partially) wins over the code-derived default.
    error_dict.setdefault("status", _ERROR_CODE_TO_STATUS.get(code, "hard_failure"))

    if _ERROR_STORM_STATE.get("_last_code") == code:
        _ERROR_STORM_STATE["_count"] = int(_ERROR_STORM_STATE.get("_count", 0)) + 1
    else:
        # error_code changed — capture the new code's first message and
        # drop any stale cache entry for the previous code so we never
        # leak one code's text into another's trimmed envelope.
        prev_code = _ERROR_STORM_STATE.get("_last_code")
        if isinstance(prev_code, str) and prev_code != code:
            _first_error_message.pop(prev_code, None)
            prev_prefix = f"{prev_code}{_FIRST_ERROR_COMMAND_SEP}"
            for key in list(_first_error_message):
                if key.startswith(prev_prefix):
                    _first_error_message.pop(key, None)
        _ERROR_STORM_STATE["_last_code"] = code
        _ERROR_STORM_STATE["_count"] = 1
    repeat = int(_ERROR_STORM_STATE["_count"])
    # Task 2 (IMPLEMENTATION-2026-05-12) — on the FIRST occurrence of a
    # given error_code, snapshot the human-readable stderr message so we
    # can replay it in trimmed envelopes that would otherwise drop the
    # `error` field. Always overwrite on storm-reset so a stale first
    # message doesn't survive a code change.
    msg = error_dict.get("error")
    if isinstance(msg, str) and msg:
        if repeat == 1:
            _first_error_message[code] = msg
        if command_key and command_key not in _first_error_message:
            _first_error_message[command_key] = msg
    if repeat >= _ERROR_STORM_THRESHOLD:
        # R9 security recheck #3: keep ``retryable`` and ``doc_link`` in
        # the trimmed envelope. Agents that branch on ``retryable``
        # (e.g. retry on ``DB_LOCKED`` / ``INDEX_STALE``) used to stop
        # retrying after the third fire because the field went missing —
        # silent behaviour change. Same for ``doc_link``: dropping it
        # stripped the self-service URL from every recurring error.
        trimmed = {
            "isError": True,
            "error_code": code,
            # Pattern-1 conformance — keep the canonical ``status`` in the
            # storm-trimmed envelope too, so a recurring error stays
            # branchable on the closed enum without re-inflating it.
            "status": error_dict["status"],
            "severity": error_dict["severity"],
            "retryable": error_dict["retryable"],
            "doc_link": error_dict["doc_link"],
            "repeat_count": repeat,
            "trimmed": True,
            "trimmed_hint": (
                f"same error fired {repeat}× — fetch the full envelope by varying inputs "
                "or by calling another tool first to reset the counter."
            ),
        }
        if isinstance(command, str) and command:
            trimmed["command"] = command
        # Task 2 — propagate the first occurrence's stderr text so the
        # agent still sees WHY the storm started without having to break
        # the storm to recover it. For command-scoped errors, do not
        # fall back to the plain error_code cache; that can leak another
        # command's first error into the current tool's trimmed envelope.
        if command_key:
            first_msg = _first_error_message.get(command_key)
        else:
            first_msg = _first_error_message.get(code)
        if first_msg:
            trimmed["first_error_message"] = first_msg
        return trimmed
    return error_dict


def _reset_error_storm() -> None:
    """Test helper — reset the storm counter.

    Task 2 (IMPLEMENTATION-2026-05-12): also drop the first-error-message
    cache. Otherwise a test that asserts a fresh error path would still
    see a stale ``first_error_message`` from a previous test poisoning
    its trimmed envelope.
    """
    _ERROR_STORM_STATE["_last_code"] = 0
    _ERROR_STORM_STATE["_count"] = 0
    _first_error_message.clear()


def _ensure_fresh_index(root: str = ".") -> dict | None:
    """Run incremental index to ensure freshness. Returns None on success."""
    result = _run_roam(["index"], root)
    if "error" in result:
        return {"error": f"index update failed: {result['error']}"}
    return None


# 12.15 — in-process result cache for read-only MCP tool calls. The MCP
# server is long-running; an agent doing five consecutive
# ``roam_preflight`` / ``roam_context`` / ``roam_impact`` calls on the
# same symbol within a session pays the full DB+graph cost every time.
# A small TTL cache keyed on (cmd, args-tuple, index-mtime) returns the
# same envelope when the index hasn't changed — typical hit window for
# an agent reasoning about one symbol is 30–120 seconds, well under
# the 300s TTL.
_ROAM_RESULT_CACHE: dict[tuple, tuple[float, float, dict]] = {}
_ROAM_CACHE_TTL_S = 300.0
# Read-only commands eligible for caching. Adding a write-y command
# here would violate the "fresh after mutation" invariant; never list
# ``init``, ``index``, ``mutate``, ``annotate``, ``ingest-trace``, etc.
_CACHEABLE_COMMANDS = frozenset(
    {
        "search",
        "context",
        "impact",
        "uses",
        "refs",
        "preflight",
        "tour",
        "understand",
        "onboard",
        "health",
        "describe",
        "module",
        "file",
        "symbol",
        "fan",
        "trace",
        "relate",
        "diagnose",
        "smells",
        "complexity",
        "dead",
        "duplicates",
        "patterns",
        "layers",
        "clusters",
        "endpoints",
        "orphan-routes",
        "fingerprint",
        "weather",
        "churn",
        "hotspots",
        "doctor",
        "map",
    }
)


def _index_mtime() -> float:
    """Return current index DB mtime (cache invalidation key)."""
    try:
        from roam.db.connection import get_db_path

        p = get_db_path()
        if p and p.exists():
            return p.stat().st_mtime
    except Exception as exc:  # noqa: BLE001 — cache-key probe must never break a tool call
        # DB path unresolvable / stat failed — fall back to 0.0 (treats the
        # cache as cold). Surface under ROAM_VERBOSE so a recurring stat
        # failure doesn't silently disable mtime-based cache invalidation.
        log_swallowed("mcp_server:index_mtime", exc)
    return 0.0


# Tool names whose response should NOT be decorated with the stale-index
# banner — running these is the recovery path, so saying "INDEX STALE"
# in the response would be both noisy and confusing.
_STALE_BANNER_SKIP = frozenset({"index", "init", "reindex", "doctor", "watch"})

_STALE_CHECK_CACHE: dict[str, tuple[float, bool, str | None]] = {}
_STALE_CHECK_TTL_S = 30.0


def _check_stale_with_cache() -> tuple[bool, str | None]:
    """Cached wrapper around ``stale_index.check_stale`` for the MCP path.

    Per audit E5: every read-only MCP tool should surface a stale-index
    affordance — the agent shouldn't search a renamed symbol, get
    nothing, and conclude the symbol doesn't exist. The full check
    stats the DB and reads the manifest, so we cache for 30 seconds
    to keep batch tool calls fast.
    """
    now = _time.time()
    cached = _STALE_CHECK_CACHE.get("default")
    if cached is not None:
        ts, is_stale, reason = cached
        if (now - ts) < _STALE_CHECK_TTL_S:
            return is_stale, reason
    try:
        from roam.commands.stale_index import check_stale

        is_stale, reason = check_stale(sensitivity="medium")
    except Exception as exc:  # noqa: BLE001 — stale-index probe must never break a tool call
        # check_stale raised — assume not-stale rather than break the call.
        # Surface under ROAM_VERBOSE: a silent failure here suppresses the
        # stale-index banner agents rely on to avoid acting on a stale graph.
        log_swallowed("mcp_server:check_stale_with_cache", exc)
        is_stale, reason = False, None
    _STALE_CHECK_CACHE["default"] = (now, is_stale, reason)
    return is_stale, reason


def _annotate_stale(result: dict, command: str) -> dict:
    """If the index is stale and *command* isn't a recovery tool,
    prepend a banner to the verdict and stamp ``_meta.stale_index``.

    Fix E (Sub-task 4): _annotate_stale runs on every envelope returned
    by ``_run_roam`` — both inprocess and subprocess paths. Tick the
    session-wide ``partial_success`` counter here so the
    ``session_metrics`` envelope reflects ANY partial-success envelope,
    not just errors.
    """
    _note_partial_success(result)
    if command in _STALE_BANNER_SKIP:
        return result
    if not isinstance(result, dict):
        return result
    is_stale, reason = _check_stale_with_cache()
    if not is_stale:
        return result

    summary = result.get("summary")
    if isinstance(summary, dict):
        verdict = summary.get("verdict") or ""
        banner = "INDEX STALE — call roam_reindex first."
        if banner not in verdict:
            summary["verdict"] = f"{banner} {verdict}".strip()
    meta = dict(result.get("_meta") or {})
    meta["stale_index"] = True
    if reason:
        meta["stale_reason"] = reason
    result["_meta"] = meta
    return result


# W325 — module-scope set of exit codes treated as "command completed normally".
# Hoisted out of ``_run_roam_inprocess`` / ``_run_roam_subprocess`` (where it
# was duplicated) so any future exit-code addition only edits one place.
# Imports are module-top-level; ``roam.exit_codes`` is a leaf module (depends
# only on stdlib + click) and does not import ``roam.mcp_server`` (W907 hedge
# audit verified — the prior "Imported lazily to avoid a circular import on
# cold-start" comment was a false cycle claim).
from roam.exit_codes import EXIT_GATE_FAILURE as _EXIT_GATE_FAILURE
from roam.exit_codes import EXIT_SUCCESS as _EXIT_SUCCESS

_SUCCESS_EXIT_CODES: frozenset[int] = frozenset({_EXIT_SUCCESS, _EXIT_GATE_FAILURE})


def _maybe_pass_through_structured_json(output: str, exit_code: int) -> dict | None:
    """W325 — Pattern-1 Variant B pass-through.

    When the CLI emits valid JSON on stdout AND exits with a code outside
    :data:`_SUCCESS_EXIT_CODES` (e.g. 1 = advisory failure in ``doctor``,
    6 = ``EXIT_PARTIAL`` in ``stale-refs``, or the
    ``symbol_not_found`` + ``SystemExit(1)`` pattern in
    ``cmd_test_scaffold``), we previously buried the structured stdout in
    the ``error`` field of a generic error envelope. That hid the real
    diagnostic from the agent.

    This helper attempts a single ``json.loads`` on the stripped stdout
    and, on success, returns the parsed dict annotated with:

    * ``_meta.cli_exit_code`` — the original exit code so consumers can
      distinguish 1 (advisory) from 6 (partial). Preserved if the inner
      JSON already set it.
    * ``isError`` — ``True`` per the MCP-spec convention (W328 codified)
      that ``isError`` belongs INSIDE the result dict. Preserved if the
      inner JSON already set it (don't overwrite a deliberate
      ``isError: false``).

    Returns ``None`` when:

    * ``output`` is empty or whitespace
    * the stripped output does not start with ``{``, ``[``, or ``"``
      (cheap fast-reject so we don't burn JSON-parsing on stack traces)
    * ``json.loads`` raises
    * the parsed value is not a dict (top-level arrays or strings stay
      in the error envelope where structure can be wrapped around them)

    Pure function: no env reads, no logging side-effects, no module
    state mutation. Both ``_run_roam_inprocess`` and
    ``_run_roam_subprocess`` call it.
    """
    if not output:
        return None
    stripped = output.strip()
    if not stripped:
        return None
    if stripped[0] not in '{["':
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    meta = parsed.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
        parsed["_meta"] = meta
    meta.setdefault("cli_exit_code", exit_code)
    parsed.setdefault("isError", True)
    return parsed


def _run_roam(args: list[str], root: str = ".") -> dict:
    """Run a roam CLI command with ``--json`` and return parsed output.

    Uses in-process Click invocation (fast, no subprocess overhead) when
    *root* is ``"."``.  Falls back to subprocess for non-local roots.

    v12.15: caches read-only command results in-memory for 5 minutes
    (TTL keyed on the index DB's mtime so any reindex invalidates the
    cache automatically). Same call within the window returns the
    same envelope without re-running the command.

    v12.51: post-process responses with a stale-index banner so agents
    don't search a renamed symbol, get nothing, and conclude the
    symbol doesn't exist (audit E5).
    """
    if root != ".":
        result = _run_roam_subprocess(args, root)
        return _annotate_stale(result, args[0] if args else "")

    # Cache lookup — only for read-only commands.
    if args and args[0] in _CACHEABLE_COMMANDS:
        cache_key = tuple(args)
        cached = _ROAM_RESULT_CACHE.get(cache_key)
        if cached is not None:
            ts, mt, payload = cached
            now = _time.time()
            if (now - ts) < _ROAM_CACHE_TTL_S and mt == _index_mtime():
                # Stamp the envelope with a cache-hit marker so callers
                # can observe the optimisation. Don't mutate the cached
                # dict in case a future caller compares object identity.
                if isinstance(payload, dict):
                    out = dict(payload)
                    meta = dict(out.get("_meta") or {})
                    meta["cache_hit"] = True
                    out["_meta"] = meta
                    return _annotate_stale(out, args[0])
        result = _run_roam_inprocess(args)
        if isinstance(result, dict) and "error" not in result:
            _ROAM_RESULT_CACHE[cache_key] = (_time.time(), _index_mtime(), result)
        return _annotate_stale(result, args[0] if args else "")
    result = _run_roam_inprocess(args)
    return _annotate_stale(result, args[0] if args else "")


def _build_no_data_envelope(args: list[str]) -> dict:
    """Build the canonical Pattern-1c ``no_data`` envelope.

    Used by ``_run_roam_inprocess`` / ``_run_roam_subprocess`` /
    ``_parse_subprocess_result`` when the CLI succeeded but emitted no
    stdout (e.g. ``roam diff`` on a clean tree). Centralises the literal
    dict shape so the three sites stay byte-identical -- key order is
    preserved exactly as the original inline literals (``command`` ->
    ``summary{verdict, state, partial_success}`` -> ``data``).
    """
    cmd_name = args[0] if args else ""
    is_diff_like = "diff" in cmd_name
    return {
        "command": "roam_" + cmd_name.replace("-", "_") if cmd_name else "roam",
        "summary": {
            "verdict": "no changes" if is_diff_like else "no data",
            "state": "no_data",
            "partial_success": False,
        },
        "data": [],
    }


def _build_invalid_json_envelope(args: list[str], error_message: str, preview: str) -> dict:
    """Build the canonical ``INVALID_JSON`` envelope.

    Used by ``_run_roam_inprocess`` / ``_run_roam_subprocess`` /
    ``_parse_subprocess_result`` when the CLI exited cleanly but emitted
    output that does not parse as JSON. The ``error_message`` /
    ``preview`` arguments preserve each call site's historical wording
    (the inprocess + phase-progress paths render ``"Failed to parse
    JSON output: {exc}"``; the subprocess path renders ``str(exc)`` --
    that divergence is preserved on purpose, this is a no-behavior-
    change refactor). Key order matches the original inline literals.
    """
    cmd_name = args[0] if args else ""
    return {
        "command": "roam_" + cmd_name.replace("-", "_") if cmd_name else "roam",
        # Pattern-1 conformance — INVALID_JSON is a degraded-but-
        # recoverable failure envelope: stamp ``isError`` + the canonical
        # closed-enum ``status`` so it branches like any other error path.
        # ``partial_failure`` (not ``hard_failure``) because the agent CAN
        # still recover — the CLI exited cleanly, only the output was
        # unparseable, and the raw preview is surfaced for inspection.
        "isError": True,
        "status": "partial_failure",
        "summary": {
            "verdict": "invalid output from underlying command",
            "state": "invalid_output",
            "partial_success": True,
        },
        "error_code": "INVALID_JSON",
        "error": error_message,
        "raw_stdout_preview": preview[:500],
    }


def _run_roam_inprocess(args: list[str]) -> dict:
    """Run a roam CLI command in-process via Click CliRunner (no subprocess)."""
    from roam.cli import cli as _cli
    from roam.db.connection import StaleDbDirError

    runner = _CliRunner()
    cmd_args = ["--json"] + args
    try:
        result = runner.invoke(_cli, cmd_args, catch_exceptions=True)
    except StaleDbDirError as exc:
        # Task 2a — surface the stale-db-dir failure as a structured
        # envelope so the agent gets the configured path + remediation
        # hint instead of opaque WinError text. ``partial_success`` is
        # True because the agent CAN still recover (edit the config) —
        # the request itself just couldn't proceed.
        return _structured_error(
            {
                "error": str(exc),
                "error_code": "STALE_DB_DIR",
                "state": "stale_db_dir",
                "partial_success": True,
                "hint": (
                    f"db_dir {exc.db_dir!r} (from {exc.source}) is not writable — "
                    "edit the config or run `roam config db-dir --reset` to fall back to the project default."
                ),
            }
        )

    output = result.output.strip() if result.output else ""

    # Gate failure (exit code 5) still produces valid JSON output — the
    # command completed but found issues.  Treat it like success for output
    # parsing, but annotate the result with gate_failure=True.
    # W325: ``_SUCCESS_EXIT_CODES`` is the hoisted module constant.
    EXIT_GATE_FAILURE = _EXIT_GATE_FAILURE
    _success_codes = _SUCCESS_EXIT_CODES

    # Fix A (SYNTHESIS Pattern 1 — JSON-parse-on-empty-input):
    # If the CLI succeeded but emitted no stdout (e.g. ``roam diff`` on a
    # clean tree, ``roam file_info`` on a path with no symbols), feeding
    # that to ``json.loads`` raises. Treat empty-on-success as the
    # canonical no_data envelope so downstream compounds (for_bug_fix,
    # pr_analyze) don't crash on it.
    if result.exit_code in _success_codes and not output:
        return _build_no_data_envelope(args)

    # Successful JSON output — look for JSON object in output
    if result.exit_code in _success_codes and output:
        try:
            parsed = json.loads(output)
            if result.exit_code == EXIT_GATE_FAILURE:
                parsed["gate_failure"] = True
                parsed["exit_code"] = EXIT_GATE_FAILURE
            return parsed
        except json.JSONDecodeError as exc:
            # Fix A — distinguish empty (handled above) from corrupted
            # output. Corrupted JSON gets an INVALID_JSON envelope with a
            # preview so the agent can see what came back.
            return _build_invalid_json_envelope(
                args,
                f"Failed to parse JSON output: {exc}",
                output,
            )

    # W325 — Pattern-1 Variant B pass-through: if the CLI emitted valid
    # JSON on stdout but exited with a non-success code (1 = advisory,
    # 6 = EXIT_PARTIAL, or symbol_not_found + SystemExit(1) in
    # cmd_test_scaffold), surface the structured envelope to the agent
    # annotated with ``_meta.cli_exit_code`` + ``isError: True`` rather
    # than burying it under a generic error envelope.
    passthrough = _maybe_pass_through_structured_json(output, result.exit_code)
    if passthrough is not None:
        return passthrough

    # Error path — classify and return structured error.
    # Task 2a: CliRunner(catch_exceptions=True) parks the raised exception
    # on ``result.exception``. Recover StaleDbDirError out of that slot so
    # the surrounding storm-rate-limited envelope still carries the
    # configured-path + remediation hint instead of opaque WinError text.
    if isinstance(result.exception, StaleDbDirError):
        sde = result.exception
        return _structured_error(
            {
                "error": str(sde),
                "error_code": "STALE_DB_DIR",
                "state": "stale_db_dir",
                "partial_success": True,
                "hint": (
                    f"db_dir {sde.db_dir!r} (from {sde.source}) is not writable — "
                    "edit the config or run `roam config db-dir --reset` to fall back to the project default."
                ),
                "exit_code": result.exit_code,
                "command": "roam --json " + " ".join(args),
            }
        )
    error_text = output
    if result.exception:
        error_text = error_text or str(result.exception)

    error_code, hint, _retryable = _classify_error(error_text, result.exit_code)
    return _structured_error(
        {
            "error": error_text or "command failed",
            "error_code": error_code,
            "hint": hint,
            "exit_code": result.exit_code,
            "command": "roam --json " + " ".join(args),
        }
    )


def _run_roam_subprocess(args: list[str], root: str = ".") -> dict:
    """Run a roam CLI command via subprocess (fallback for non-local roots)."""
    # W325: ``_SUCCESS_EXIT_CODES`` is the hoisted module constant.
    EXIT_GATE_FAILURE = _EXIT_GATE_FAILURE
    _success_codes = _SUCCESS_EXIT_CODES

    cmd = ["roam", "--json"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
            timeout=60,
        )
        stdout_text = (result.stdout or "").strip()
        # Fix A (SYNTHESIS Pattern 1) — success + empty stdout returns a
        # no_data envelope rather than crashing the wrapper on
        # ``json.loads("")``.
        if result.returncode in _success_codes and not stdout_text:
            return _build_no_data_envelope(args)
        if result.returncode in _success_codes and stdout_text:
            try:
                parsed = json.loads(stdout_text)
            except json.JSONDecodeError as exc:
                # MM4 follow-up: normalized to the prefixed form used by
                # the other two INVALID_JSON sites so agents parsing the
                # ``error`` field can match the family with a single
                # regex.
                return _build_invalid_json_envelope(
                    args,
                    f"Failed to parse JSON output: {exc}",
                    stdout_text,
                )
            if result.returncode == EXIT_GATE_FAILURE:
                parsed["gate_failure"] = True
                parsed["exit_code"] = EXIT_GATE_FAILURE
            return parsed
        # W325 — Pattern-1 Variant B pass-through (subprocess path):
        # mirror ``_run_roam_inprocess``. If stdout parsed cleanly as JSON
        # but the CLI exited outside ``_SUCCESS_EXIT_CODES``, surface the
        # structured diagnostic to the agent rather than burying it in
        # the generic error envelope.
        passthrough = _maybe_pass_through_structured_json(stdout_text, result.returncode)
        if passthrough is not None:
            return passthrough
        stderr = result.stderr.strip()
        error_code, hint, _retryable = _classify_error(stderr, result.returncode)
        return _structured_error(
            {
                "error": stderr or "command failed",
                "error_code": error_code,
                "hint": hint,
                "exit_code": result.returncode,
                "command": " ".join(cmd),
            }
        )
    except subprocess.TimeoutExpired:
        return _structured_error(
            {
                "error": "Command timed out after 60s",
                "error_code": "COMMAND_FAILED",
                "hint": "the command took too long. try a smaller scope or check system load.",
            }
        )
    except json.JSONDecodeError as exc:
        return _structured_error(
            {
                "error": f"Failed to parse JSON output: {exc}",
                "error_code": "COMMAND_FAILED",
                "hint": "command produced invalid JSON output.",
            }
        )
    except Exception as exc:
        return _structured_error(
            {
                "error": str(exc),
                "error_code": "UNKNOWN",
                "hint": "an unexpected error occurred.",
            }
        )


async def _run_roam_async(args: list[str], root: str = ".") -> dict:
    """Run a roam CLI command in a worker thread from async tool handlers."""
    return await asyncio.to_thread(_run_roam, args, root)


def _parse_subprocess_result(args: list[str], exit_code: int, stdout: str, stderr: str) -> dict:
    """Parse the (exit, stdout, stderr) tuple produced by phase-progress runs.

    Mirrors the success / error handling in :func:`_run_roam_subprocess` so
    callers get the same envelope shape regardless of which path was used.
    """
    from roam.exit_codes import EXIT_GATE_FAILURE

    success_codes = {0, EXIT_GATE_FAILURE}
    stdout_text = (stdout or "").strip()
    # Fix A (SYNTHESIS Pattern 1) — empty-stdout-on-success emits a
    # no_data envelope instead of falling into the error path.
    if exit_code in success_codes and not stdout_text:
        return _build_no_data_envelope(args)
    if exit_code in success_codes and stdout_text:
        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            return _build_invalid_json_envelope(
                args,
                f"Failed to parse JSON output: {exc}",
                stdout_text,
            )
        if exit_code == EXIT_GATE_FAILURE:
            parsed["gate_failure"] = True
            parsed["exit_code"] = EXIT_GATE_FAILURE
        return parsed

    # W325 — Pattern-1 Variant B pass-through (phase-progress path):
    # mirror ``_run_roam_inprocess`` / ``_run_roam_subprocess``. If the
    # phase-progress run emitted valid JSON on stdout but exited with a
    # non-success code (e.g. 1 for advisory failure in ``init``, 6 for
    # EXIT_PARTIAL), surface the structured diagnostic to the agent
    # rather than burying it in the generic error envelope.
    passthrough = _maybe_pass_through_structured_json(stdout_text, exit_code)
    if passthrough is not None:
        return passthrough

    err_code, hint, _retryable = _classify_error(stderr, exit_code)
    return _structured_error(
        {
            "error": stderr.strip() or "command failed",
            "error_code": err_code,
            "hint": hint,
            "exit_code": exit_code,
            "command": " ".join(args),
        }
    )


async def _ctx_report_progress(
    ctx: _Context | None,
    progress: float,
    total: float | None = None,
    message: str | None = None,
) -> None:
    """Best-effort MCP progress reporting (safe on clients without support)."""
    if ctx is None or not hasattr(ctx, "report_progress"):
        return
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception as exc:  # noqa: BLE001 — progress reporting must never break a tool call
        # Client doesn't support progress OR the channel dropped — surface
        # under ROAM_VERBOSE so a transport-level breakage is visible rather
        # than presenting as a client that simply lacks progress support.
        log_swallowed("mcp_server:ctx_report_progress", exc)


async def _ctx_info(ctx: _Context | None, message: str) -> None:
    """Best-effort MCP log message to the client."""
    if ctx is None or not hasattr(ctx, "info"):
        return
    try:
        await ctx.info(message)
    except Exception as exc:  # noqa: BLE001 — log messaging must never break a tool call
        # Client doesn't support log messages OR the channel dropped —
        # surface under ROAM_VERBOSE so a transport-level breakage is visible
        # rather than presenting as a client that just lacks log support.
        log_swallowed("mcp_server:ctx_info", exc)


def _coerce_yes_no(value) -> bool | None:
    """Normalize elicitation payloads into True/False when possible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in {"y", "yes", "true", "1", "continue", "proceed", "confirm"}:
            return True
        if norm in {"n", "no", "false", "0", "cancel", "stop", "decline"}:
            return False
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _coerce_yes_no(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, dict):
        for key in ("value", "confirm", "decision", "answer", "choice"):
            if key in value:
                parsed = _coerce_yes_no(value[key])
                if parsed is not None:
                    return parsed
        for item in value.values():
            parsed = _coerce_yes_no(item)
            if parsed is not None:
                return parsed
    return None


def _default_summarize_enabled() -> bool:
    """Default value for compound-tool ``summarize`` parameter.

    Audit E6: when ``ROAM_AI_ENABLED=1`` is set the user has explicitly
    consented to sampling, so the bigger compound tools
    (``roam_explore`` / ``roam_understand`` / ``roam_health``) should
    default to compressed responses — that's the 50:1 context budget
    win the audit flagged. Without the env var, default stays False so
    no payload leaves the local process.

    The ``compliance`` preset is excluded — audit-trail evidence must
    be deterministic, and sampled prose isn't.

    ``ROAM_AI_DISABLED=1`` is the explicit opt-out for users who set
    ROAM_AI_ENABLED globally but want a specific call uncompressed.
    """
    if os.environ.get("ROAM_AI_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.environ.get("ROAM_AI_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return False
    if _ACTIVE_PRESET == "compliance":
        return False
    return True


async def _maybe_summarize(
    result: dict,
    *,
    ctx: _Context | None,
    summarize: bool | None,
    task: str = "",
    target: str = "",
) -> dict:
    """Apply MCP sampling-driven compression when requested + supported.

    ``summarize=None`` (the default for compound tools) resolves via
    :func:`_default_summarize_enabled` so users with ``ROAM_AI_ENABLED=1``
    set get compressed responses by default. Pass ``True`` / ``False``
    explicitly to override.
    """
    effective = summarize if summarize is not None else _default_summarize_enabled()
    if not effective or _mcp_sampling is None or ctx is None:
        return result
    if not isinstance(result, dict):
        return result
    compressed = await _mcp_sampling.compress_with_sampling(
        ctx,
        result,
        task=task,
        target=target,
    )
    return _mcp_sampling.maybe_apply_compression(result, compressed)


async def _confirm_force_reindex(ctx: _Context | None) -> bool | None:
    """Ask the client to confirm force reindex via MCP elicitation."""
    if ctx is None or not hasattr(ctx, "elicit"):
        return None
    try:
        response = await ctx.elicit(
            "Force reindex may take longer and rewrites index metadata. Continue?",
            ["continue", "cancel"],
        )
    except Exception as exc:  # noqa: BLE001 — elicitation must never break a tool call
        # Client doesn't support elicitation OR the prompt failed — return
        # None (caller treats as "not confirmed"). Surface under ROAM_VERBOSE
        # so an elicitation transport breakage is distinguishable from a
        # client that simply lacks elicitation support.
        log_swallowed("mcp_server:confirm_force_reindex", exc)
        return None

    if getattr(response, "action", "") != "accept":
        return False
    parsed = _coerce_yes_no(getattr(response, "data", None))
    return parsed if parsed is not None else False


async def _force_reindex_stop_response(
    force: bool,
    confirm_force: bool,
    ctx: _Context | None,
) -> dict | None:
    """Return a stop response when force reindex lacks confirmation."""
    if not force or confirm_force:
        return None

    approved = await _confirm_force_reindex(ctx)
    if approved is None:
        return _structured_error(
            {
                "error": "force reindex requires confirmation but elicitation is unavailable.",
                "error_code": "ELICITATION_REQUIRED",
                "hint": "rerun with confirm_force=true or use a client with elicitation support.",
                "command": "roam_reindex",
            }
        )
    if approved:
        return None
    return {
        "command": "index",
        "summary": {
            "verdict": "force reindex cancelled by user",
            "cancelled": True,
            "force": True,
        },
        "cancelled": True,
        "force": True,
    }


# ===================================================================
# Compound operations -- each replaces 2-4 individual tool calls
# ===================================================================

_COMPOUND_WORKFLOW_RECIPES = {
    "explore": "onboard",
    "prepare-change": "safe-delete-check",
    "review-change": "verify-patch",
    "diagnose-issue": "find-bug",
}


def _extract_signal(data: dict, *path: str, default=0):
    """Walk a nested dict by keys and return the leaf, or default."""
    cur: object = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key, default)
    if isinstance(cur, (int, float)):
        return cur
    return default


def _score_prepare_change_recipe(sub_results: list[tuple[str, dict]]) -> str:
    """Pick a recipe for ``prepare-change`` based on the symbol's profile.

    Round 4 #9: previously defaulted to ``safe-delete-check`` for every
    symbol, even orchestrators with cc=694, fan-out=13, churn=3244. The
    auto-detector now scores all candidate recipes by signal vector and
    returns the highest-scoring match.
    """
    pf = next((d for n, d in sub_results if n == "preflight" and isinstance(d, dict)), {})
    if not pf:
        return "safe-delete-check"
    summary = pf if "summary" not in pf else pf.get("summary", {})

    fan_in = _extract_signal(pf, "preflight", "fan_in") or _extract_signal(summary, "fan_in")
    fan_out = _extract_signal(pf, "preflight", "fan_out") or _extract_signal(summary, "fan_out")
    complexity = (
        _extract_signal(pf, "complexity", "max_cognitive_complexity")
        or _extract_signal(summary, "max_complexity")
        or _extract_signal(pf, "complexity", "complexity")
    )
    churn = _extract_signal(pf, "blast", "churn") or _extract_signal(summary, "churn")
    callers = _extract_signal(pf, "blast", "direct_callers") or _extract_signal(summary, "direct_callers")
    transitive = _extract_signal(pf, "blast", "transitive_dependents") or _extract_signal(
        summary, "transitive_dependents"
    )

    scores = {
        "safe-delete-check": 1.0,  # baseline
        "refactor-orchestrator": 0.0,
        "api-change-impact": 0.0,
        "find-bug": 0.0,
    }

    # High complexity + high fan-out = orchestrator.
    if complexity >= 50:
        scores["refactor-orchestrator"] += 1.5
    if fan_out >= 8:
        scores["refactor-orchestrator"] += 1.2
    if churn >= 1000:
        scores["refactor-orchestrator"] += 1.0

    # Wide blast radius — every consumer breaks.
    if fan_in >= 20 or callers >= 20:
        scores["api-change-impact"] += 1.5
    if transitive >= 50:
        scores["api-change-impact"] += 1.0

    # Strong delete signal: nothing depends on it, low complexity.
    if (fan_in or 0) <= 2 and (callers or 0) <= 2 and (complexity or 0) < 20 and (churn or 0) < 100:
        scores["safe-delete-check"] += 1.0
    else:
        # Penalise the default when the symbol clearly isn't deletable.
        scores["safe-delete-check"] -= 1.0

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "safe-delete-check"


def _compound_envelope(
    command: str,
    sub_results: list[tuple[str, dict]],
    **meta,
) -> dict:
    """Build a compound operation response from multiple sub-command results."""
    errors: list[dict] = []
    sections: dict = {}
    workflow_recipe = meta.pop("workflow_recipe", None) or _COMPOUND_WORKFLOW_RECIPES.get(command)
    # Round 4 #9: replace the static recipe pick with a signal-based
    # scorer when we have enough data to choose intelligently.
    if command == "prepare-change":
        scored = _score_prepare_change_recipe(sub_results)
        if scored:
            workflow_recipe = scored

    for name, data in sub_results:
        # W805-TTTTT — the error-storm coalescer's trimmed envelope
        # (mcp_server.py error-storm trim path) carries ``isError: True`` +
        # ``first_error_message`` but OMITS the top-level ``error`` key. The
        # narrow ``"error" in data`` check let a trimmed-isError child slip
        # into the success bucket (``sections``) — a Pattern-2 silent SAFE.
        # Widen the failure classification to also catch ``isError is True``,
        # and fall back to ``first_error_message`` so the trimmed child still
        # surfaces an actionable message.
        if not data or "error" in data or (isinstance(data, dict) and data.get("isError") is True):
            # err_msg fallback chain: a trimmed-isError child has no
            # top-level ``error`` key — fall back to ``first_error_message``
            # (the coalescer's captured first-fire text), then to a
            # structured ``isError`` envelope's ``summary.verdict`` (e.g.
            # "Symbol not found: X"), and only then to the opaque sentinel.
            err_msg = "empty result"
            if data:
                child_summary = data.get("summary")
                err_msg = (
                    data.get("error")
                    or data.get("first_error_message")
                    or (child_summary.get("verdict") if isinstance(child_summary, dict) else None)
                    or "empty result"
                )
            errors.append({"command": name, "error": err_msg})
        else:
            sections[name] = data

    # Build compound verdict from sub-verdicts
    verdicts: list[str] = []
    for name, data in sub_results:
        if isinstance(data, dict):
            summary = data.get("summary", {})
            if isinstance(summary, dict) and "verdict" in summary:
                verdicts.append(f"{name}: {summary['verdict']}")

    failed_subcommands = [e.get("command", "?") for e in errors] if errors else []
    # SYNTHESIS Pattern 2 (silent fallback) — partial_success MUST flip
    # True whenever ANY subcommand failed, not only the mixed-result
    # case. ``for_refactor`` previously reported ``partial_success:
    # False`` while 4/4 subcommands errored because ``and bool(sections)``
    # required at least one survivor. Drop the guard so an all-failed
    # compound is correctly partial_success=True.
    partial_success = bool(failed_subcommands)
    all_failed = bool(failed_subcommands) and not bool(sections)
    # Default verdict pick: aggregate sub-verdicts; fall back to a
    # diagnostic string that names the count instead of the silent
    # "compound operation completed" that lied about all-failed runs.
    if verdicts:
        default_verdict = " | ".join(verdicts)
    elif all_failed:
        default_verdict = (
            f"compound operation: {len(failed_subcommands)} subcommand(s) failed ({', '.join(failed_subcommands)})"
        )
    else:
        default_verdict = "compound operation completed"
    result: dict = {
        "command": command,
        "summary": {
            "verdict": default_verdict,
            "sections": list(sections.keys()),
            "errors": len(errors),
            # Round 4 #8, N: surface partial failure at the top level so
            # agents don't read a successful-looking envelope while a
            # subcommand silently failed.
            "partial_success": partial_success,
            "failed_subcommands": failed_subcommands,
            **meta,
        },
    }
    if partial_success and bool(sections):
        # Mention the failures explicitly in the verdict line so an LLM
        # reading just the summary doesn't miss them. (All-failed runs
        # already get the count-in-the-verdict treatment above.)
        # ASCII-only output convention (CLAUDE.md) — use '--' not an em-dash.
        prefix = f"PARTIAL ({len(failed_subcommands)} failed: {', '.join(failed_subcommands)}) -- "
        result["summary"]["verdict"] = prefix + result["summary"]["verdict"]
    if all_failed:
        # Pattern-1 conformance — an all-subcommands-failed compound is a
        # failure envelope: stamp ``isError`` + the canonical closed-enum
        # ``status`` so consumers branch on it like any other error path.
        result["isError"] = True
        result["status"] = "partial_failure"
    workflow = workflow_metadata_for_recipe(str(workflow_recipe)) if workflow_recipe else None
    if workflow:
        result["workflow"] = workflow
        result["summary"]["workflow_phase"] = workflow["phase"]
        result["summary"]["workflow_recipe"] = workflow["recipe"]
    result.update(sections)

    if errors:
        result["_errors"] = errors

    # Fix E (Sub-task 4) — compound envelopes don't all flow back
    # through ``_run_roam`` / ``_annotate_stale``. Tick the session-wide
    # counter here too so the session_metrics envelope reflects partial
    # compound outcomes.
    _note_partial_success(result)
    return result


def _apply_budget(data: dict, budget: int) -> dict:
    """Apply token budget truncation to a compound operation result.

    Delegates to :func:`budget_truncate_json` from the formatter module.
    """
    if budget <= 0:
        return data
    from roam.output.formatter import budget_truncate_json

    return budget_truncate_json(data, budget)


def _append_context_personalization_args(
    args: list[str], session_hint: str = "", recent_symbols: str = ""
) -> list[str]:
    """Append optional context personalization flags to a roam CLI arg list."""
    if session_hint:
        args.extend(["--session-hint", session_hint])
    if recent_symbols:
        for raw in str(recent_symbols).split(","):
            sym = raw.strip()
            if sym:
                args.extend(["--recent-symbol", sym])
    return args


@_tool(
    name="roam_explore",
    description="Codebase exploration bundle: understand overview + optional symbol deep-dive in one call.",
    output_schema=_SCHEMA_EXPLORE,
)
async def explore(
    symbol: str = "",
    budget: int = 0,
    session_hint: str = "",
    recent_symbols: str = "",
    summarize: bool | None = None,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Full codebase exploration in one call.

    WHEN TO USE: Call this FIRST when starting work on a new codebase.
    If you have a specific symbol in mind, pass it to also get focused
    context (callers, callees, files to read). Replaces calling
    ``understand`` + ``context`` separately — saves one round-trip.

    Parameters
    ----------
    symbol:
        Optional symbol to deep-dive into after the overview.
    budget:
        Max output tokens (0 = unlimited). Truncates intelligently.
    session_hint:
        Optional conversation hint used to personalize context ranking.
        If empty, the server uses any task hint cached for this MCP session.
    recent_symbols:
        Comma-separated recently discussed symbols for rank biasing.
        If empty, the server fills this in from session memory.
    summarize:
        If True and the client supports MCP sampling, the server asks
        the client's own LLM to compress the overview into a short
        briefing. Reduces output from ~50KB JSON to ~1-2KB prose.
        Falls back to the raw envelope when sampling is unavailable.

    Returns: codebase overview (tech stack, architecture, health) and
    optionally focused context for the given symbol.
    """
    if _mcp_session is not None:
        session_hint, recent_symbols = _mcp_session.merge_with_explicit(
            ctx,
            explicit_recent=recent_symbols,
            explicit_hint=session_hint,
        )
        if symbol:
            _mcp_session.remember_symbol(ctx, symbol)
        if session_hint:
            _mcp_session.remember_task_hint(ctx, session_hint)

    budget_args = ["--budget", str(budget)] if budget else []
    overview = _run_roam(budget_args + ["understand"], root)

    if not symbol:
        result = _compound_envelope("explore", [("understand", overview)])
        result = _apply_budget(result, budget)
        return await _maybe_summarize(result, ctx=ctx, summarize=summarize, task=session_hint, target="")

    ctx_args = budget_args + ["context", symbol, "--task", "understand"]
    _append_context_personalization_args(
        ctx_args,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
    )
    sym_ctx = _run_roam(ctx_args, root)
    result = _compound_envelope(
        "explore",
        [
            ("understand", overview),
            ("context", sym_ctx),
        ],
        target=symbol,
    )
    result = _apply_budget(result, budget)
    return await _maybe_summarize(result, ctx=ctx, summarize=summarize, task=session_hint, target=symbol)


@_tool(
    name="roam_prepare_change",
    description="Pre-change safety gate. Run before any non-trivial edit — returns blast radius, affected tests, and fitness gates.",
    output_schema=_SCHEMA_PREPARE_CHANGE,
)
def prepare_change(
    symbol: str,
    staged: bool = False,
    budget: int = 0,
    session_hint: str = "",
    recent_symbols: str = "",
    root: str = ".",
) -> dict:
    """Everything needed before modifying code, in one call.

    WHEN TO USE: Call this BEFORE making any non-trivial code change.
    Bundles safety check (blast radius, tests, fitness), context (files
    and line ranges to read), and side effects into a single response.
    Replaces calling ``preflight`` + ``context`` + ``effects`` separately
    — saves two round-trips.

    Parameters
    ----------
    symbol:
        Symbol name or file path to prepare for changing. W430/Fix-D
        canonical; legacy ``target=`` callers are accepted via
        ``_PARAM_ALIASES`` with a deprecation warning.
    staged:
        If True, check staged (git add-ed) changes instead.
    budget:
        Max output tokens (0 = unlimited). Truncates intelligently.
    session_hint:
        Optional conversation hint used to personalize context ranking.
    recent_symbols:
        Comma-separated recently discussed symbols for rank biasing.

    Returns: preflight safety data, context files to read, and side
    effects of the symbol. Each section includes its own verdict.
    """
    budget_args = ["--budget", str(budget)] if budget else []
    pf_args = budget_args + ["preflight"]
    if symbol:
        pf_args.append(symbol)
    if staged:
        pf_args.append("--staged")

    preflight_data = _run_roam(pf_args, root)

    ctx_data: dict = {}
    effects_data: dict = {}
    if symbol:
        ctx_args = budget_args + ["context", symbol, "--task", "refactor"]
        _append_context_personalization_args(
            ctx_args,
            session_hint=session_hint,
            recent_symbols=recent_symbols,
        )
        ctx_data = _run_roam(ctx_args, root)
        effects_data = _run_roam(budget_args + ["effects", symbol], root)

    result = _compound_envelope(
        "prepare-change",
        [
            ("preflight", preflight_data),
            ("context", ctx_data),
            ("effects", effects_data),
        ],
        symbol=symbol,
    )
    return _apply_budget(result, budget)


@_tool(
    name="roam_review_change",
    description="Change review bundle: pr-risk + breaking changes + structural diff in one call.",
    output_schema=_SCHEMA_REVIEW_CHANGE,
)
def review_change(staged: bool = False, commit_range: str = "", budget: int = 0, root: str = ".") -> dict:
    """Review pending changes in one call.

    WHEN TO USE: Call this before committing or creating a PR.
    Bundles risk assessment, breaking API changes, and structural
    graph delta into a single response. Replaces calling ``pr_risk`` +
    ``breaking_changes`` + ``pr_diff`` separately — saves two round-trips.

    Parameters
    ----------
    staged:
        If True, analyze staged changes only.
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.
    budget:
        Max output tokens (0 = unlimited). Truncates intelligently.

    Returns: risk score, breaking changes, and structural delta.
    Each section includes its own verdict.
    """
    budget_args = ["--budget", str(budget)] if budget else []
    risk_args = budget_args + ["pr-risk"]
    breaking_args = budget_args + ["breaking"]
    diff_args = budget_args + ["pr-diff"]

    if staged:
        risk_args.append("--staged")
        diff_args.append("--staged")
    if commit_range:
        breaking_args.append(commit_range)
        diff_args.extend(["--range", commit_range])

    risk_data = _run_roam(risk_args, root)
    breaking_data = _run_roam(breaking_args, root)
    diff_data = _run_roam(diff_args, root)

    result = _compound_envelope(
        "review-change",
        [
            ("pr_risk", risk_data),
            ("breaking_changes", breaking_data),
            ("pr_diff", diff_data),
        ],
    )
    return _apply_budget(result, budget)


@_tool(
    name="roam_diagnose_issue",
    description=(
        "Root-cause triage for a failing symbol. Pass the suspect symbol. "
        "Ranks upstream / downstream callers by risk + lists side effects "
        "+ transactional boundaries. Replaces manual call-graph Grep+Read. "
        "Triggers: 'X is broken', 'test Y fails', 'why does Z return null?'."
    ),
    output_schema=_SCHEMA_DIAGNOSE_ISSUE,
)
def diagnose_issue(symbol: str, depth: int = 2, budget: int = 0, root: str = ".") -> dict:
    """Debug a failing symbol in one call.

    WHEN TO USE: Call this when debugging a bug or test failure.
    Bundles root-cause analysis (upstream/downstream suspects ranked
    by composite risk) with side-effect analysis into one response.
    Replaces calling ``diagnose`` + ``effects`` separately — saves
    one round-trip.

    Parameters
    ----------
    symbol:
        The symbol suspected of being involved in the bug.
    depth:
        How many hops upstream/downstream to analyze (default 2).
    budget:
        Max output tokens (0 = unlimited). Truncates intelligently.

    Returns: root cause suspects ranked by risk and side effects
    of the target symbol.
    """
    budget_args = ["--budget", str(budget)] if budget else []
    diag_data = _run_roam(budget_args + ["diagnose", symbol, "--depth", str(depth)], root)
    effects_data = _run_roam(budget_args + ["effects", symbol], root)

    result = _compound_envelope(
        "diagnose-issue",
        [
            ("diagnose", diag_data),
            ("effects", effects_data),
        ],
        target=symbol,
    )
    return _apply_budget(result, budget)


# ===================================================================
# Batch operations — 10x fewer MCP round trips for agents
# ===================================================================

from roam.commands.batch_search_core import (
    BATCH_LIKE_SQL as _BATCH_LIKE_SQL,
)
from roam.commands.batch_search_core import (
    BATCH_LIKE_WITH_PATHS_SQL as _BATCH_LIKE_WITH_PATHS_SQL,
)
from roam.commands.batch_search_core import (
    MAX_BATCH_QUERIES as _MAX_BATCH_QUERIES,
)
from roam.commands.batch_search_core import (
    batch_search_one as _shared_batch_search_one,
)

_MAX_BATCH_SYMBOLS = 50


def _batch_search_one(conn, q: str, limit: int, include_paths: bool = False) -> tuple[list, str | None]:
    """Compatibility shim for tests/imports; implementation lives in commands."""
    return _shared_batch_search_one(
        conn,
        q,
        limit,
        include_paths=include_paths,
        like_sql=_BATCH_LIKE_SQL,
        like_with_paths_sql=_BATCH_LIKE_WITH_PATHS_SQL,
    )


def _batch_get_one(conn, sym: str) -> tuple[dict | None, str | None]:
    """Retrieve full details for a single symbol in an open DB connection.

    Returns (details_dict, error_or_None).
    Uses the same lookup chain as find_symbol(): qualified -> name -> fuzzy.
    """
    import sqlite3

    from roam.commands.resolve import find_symbol
    from roam.db.queries import CALLEES_OF, CALLERS_OF, METRICS_FOR_SYMBOL
    from roam.output.formatter import loc

    try:
        s = find_symbol(conn, sym)
    except sqlite3.DatabaseError as exc:
        return None, f"db error resolving {sym!r}: {exc}"

    if s is None:
        return None, f"symbol not found: {sym!r}"

    try:
        metrics = conn.execute(METRICS_FOR_SYMBOL, (s["id"],)).fetchone()
        callers = conn.execute(CALLERS_OF, (s["id"],)).fetchall()
        callees = conn.execute(CALLEES_OF, (s["id"],)).fetchall()
    except sqlite3.DatabaseError as exc:
        return None, f"db error fetching details for {sym!r}: {exc}"

    details: dict = {
        "name": s["qualified_name"] or s["name"],
        "kind": s["kind"],
        "signature": s["signature"] or "",
        "location": loc(s["file_path"], s["line_start"]),
        "docstring": s["docstring"] or "",
    }
    if metrics:
        details["pagerank"] = round(float(metrics["pagerank"] or 0), 4)
        details["in_degree"] = metrics["in_degree"]
        details["out_degree"] = metrics["out_degree"]

    details["callers"] = [
        {
            "name": c["name"],
            "kind": c["kind"],
            "edge_kind": c["edge_kind"],
            "location": loc(c["file_path"], c["edge_line"]),
        }
        for c in callers
    ]
    details["callees"] = [
        {
            "name": c["name"],
            "kind": c["kind"],
            "edge_kind": c["edge_kind"],
            "location": loc(c["file_path"], c["edge_line"]),
        }
        for c in callees
    ]
    return details, None


def _batch_get_many(conn, symbols: list[str]) -> tuple[dict, dict]:
    results: dict = {}
    errors: dict = {}

    for sym in symbols:
        # W103-loud: isolate a single symbol's crash so it never aborts the
        # whole batch (the tool's contract), but capture it loudly.
        try:
            details, err = _batch_get_one(conn, sym)
        except Exception as exc:  # noqa: BLE001 -- isolated + logged, not silent
            log_swallowed("mcp_server:batch_get_symbol", exc)
            errors[sym] = f"lookup crashed: {type(exc).__name__}: {exc}"
            continue
        if err or details is None:
            errors[sym] = err or "not found"
            continue
        results[sym] = details

    return results, errors


def _batch_get_summary(verdict: str, symbols_requested: int, symbols_resolved: int) -> dict:
    return {
        "verdict": verdict,
        "symbols_requested": symbols_requested,
        "symbols_resolved": symbols_resolved,
    }


def _batch_get_payload(summary: dict, results: dict | None = None, errors: dict | None = None) -> dict:
    payload: dict = {
        "command": "batch-get",
        "summary": summary,
        "results": results or {},
    }
    if errors is not None:
        payload["errors"] = errors
    return payload


@_tool(
    name="roam_batch_search",
    description="Search up to 10 patterns in one call. Replaces 10 sequential roam_search_symbol calls.",
    output_schema=_SCHEMA_BATCH_SEARCH,
)
def batch_search(
    queries: list,
    limit_per_query: int = 5,
    include_paths: bool = False,
    root: str = ".",
) -> dict:
    """Batch symbol search: run multiple name queries in one MCP call.

    WHEN TO USE: Use this instead of calling roam_search_symbol 3+ times
    in a row with different queries. Executes all queries over a single
    DB connection — dramatically fewer round trips for agents doing broad
    symbol discovery (e.g. finding all auth, user, and request symbols at once).

    Parameters
    ----------
    queries:
        List of name substrings to search for (up to 10). Each entry is
        treated the same as a single roam_search_symbol query.
    limit_per_query:
        Max results per query (default 5, max 50).
    include_paths:
        If True, also match against file paths. Off by default — the
        previous wide-match behaviour caused spurious matches when a
        query string happened to appear in a directory or fixture name
        (e.g. ``useAccountBalance`` matching ``setup`` from
        ``tests/composables/transactions/useAccountBalance.test.ts``).
        Mirrors the ``--include-paths`` flag on the CLI.
    root:
        Project root directory (default ".").

    Returns: per-query result lists plus aggregate match count.
    Partial failures are collected in ``errors``; remaining queries still run.
    """
    import sqlite3

    from roam.commands.resolve import ensure_index
    from roam.db.connection import StaleDbDirError, open_db

    ensure_index()

    queries_list: list[str] = [str(q) for q in (queries or [])][:_MAX_BATCH_QUERIES]
    limit = max(1, min(int(limit_per_query), 50))
    include_paths_flag = bool(include_paths)

    results: dict = {}
    errors: dict = {}

    if not queries_list:
        return {
            "command": "batch-search",
            "summary": {
                "verdict": "no queries provided",
                "queries_executed": 0,
                "total_matches": 0,
                "include_paths": include_paths_flag,
                "match_mode": "name+path" if include_paths_flag else "name-only",
            },
            "results": {},
            "errors": {},
        }

    try:
        conn_ctx = open_db(readonly=True)
    except (click.ClickException, sqlite3.DatabaseError, OSError, StaleDbDirError) as exc:
        # Expected DB/config boundary failures get a structured MCP payload;
        # programmer-class failures propagate instead of looking like no data.
        return {
            "command": "batch-search",
            "summary": {
                "verdict": f"batch search failed: {exc}",
                "queries_executed": 0,
                "total_matches": 0,
                "include_paths": include_paths_flag,
                "match_mode": "name+path" if include_paths_flag else "name-only",
            },
            "results": {},
            "errors": {"_fatal": str(exc)},
        }

    # W607: open_db() and the per-query loop are kept in separate blocks so a
    # connection failure short-circuits with a _fatal payload while DB-level
    # query errors stay isolated inside _batch_search_one's return value.
    # Unexpected programming errors now propagate instead of being swallowed.
    with conn_ctx as conn:
        for q in queries_list:
            # W103-loud: a batch tool must isolate a single query's failure, not
            # abort the whole batch (its documented contract: "remaining queries
            # still run"). A raised exception is captured per-query but LOUD --
            # log_swallowed surfaces it, so W607's "don't swallow silently" holds.
            try:
                rows, err = _batch_search_one(conn, q, limit, include_paths=include_paths_flag)
            except Exception as exc:  # noqa: BLE001 -- isolated + logged, not silent
                log_swallowed("mcp_server:batch_search_query", exc)
                errors[q] = f"query crashed: {type(exc).__name__}: {exc}"
                continue
            if err:
                errors[q] = err
            else:
                results[q] = rows

    total_matches = sum(len(v) for v in results.values())
    verdict = f"{total_matches} matches across {len(results)} queries" if results else "no matches found"
    if errors:
        verdict += f", {len(errors)} queries failed"

    payload: dict = {
        "command": "batch-search",
        "summary": {
            "verdict": verdict,
            "queries_executed": len(queries_list),
            "total_matches": total_matches,
            "include_paths": include_paths_flag,
            "match_mode": "name+path" if include_paths_flag else "name-only",
        },
        "results": results,
    }
    if errors:
        payload["errors"] = errors

    return payload


@_tool(
    name="roam_batch_get",
    description="Get details for up to 50 symbols in one call. Replaces 50 sequential roam_symbol calls.",
    output_schema=_SCHEMA_BATCH_GET,
)
def batch_get(symbols: list, root: str = ".") -> dict:
    """Batch symbol detail retrieval: fetch multiple symbol definitions in one MCP call.

    WHEN TO USE: Use this instead of calling a symbol lookup tool 3+ times.
    Common pattern: after roam_batch_search or roam_search_symbol returns
    several candidates, call this to get callers/callees/metrics for all of
    them at once instead of one tool call per symbol.

    Parameters
    ----------
    symbols:
        List of symbol names or qualified names to look up (up to 50).
        Accepts the same formats as roam_symbol: bare name, qualified name,
        or ``file.py:SymbolName`` syntax.
    root:
        Project root directory (default ".").

    Returns: per-symbol detail dicts with callers, callees, pagerank, and
    location. Unresolved symbols appear in ``errors``; resolved symbols
    appear in ``results``.
    """
    import sqlite3

    from roam.commands.resolve import ensure_index
    from roam.db.connection import StaleDbDirError, open_db

    ensure_index()

    symbols_list: list[str] = [str(s) for s in (symbols or [])][:_MAX_BATCH_SYMBOLS]

    if not symbols_list:
        return _batch_get_payload(
            _batch_get_summary("no symbols provided", 0, 0),
            errors={},
        )

    # W103/W607: open_db() and the per-symbol loop are kept in separate blocks
    # so a connection failure short-circuits with a _fatal payload while
    # DB-level lookup errors stay isolated inside _batch_get_one's return
    # value. This also keeps the wrapper shallow, matching batch_search.
    # Programmer-class failures propagate instead of looking like no data.
    try:
        conn_ctx = open_db(readonly=True)
    except (click.ClickException, sqlite3.DatabaseError, OSError, StaleDbDirError) as exc:
        # Expected DB/config boundary failures get a structured MCP payload;
        # programmer-class failures propagate instead of looking like no data.
        return _batch_get_payload(
            _batch_get_summary(f"batch get failed: {exc}", len(symbols_list), 0),
            errors={"_fatal": str(exc)},
        )

    with conn_ctx as conn:
        results, errors = _batch_get_many(conn, symbols_list)

    resolved = len(results)
    verdict = f"{resolved}/{len(symbols_list)} symbols resolved"
    if errors:
        verdict += f", {len(errors)} not found"

    return _batch_get_payload(
        _batch_get_summary(verdict, len(symbols_list), resolved),
        results=results,
        errors=errors if errors else None,
    )


# ===================================================================
# Tier 1 tools -- the most valuable for day-to-day AI agent work
# ===================================================================


@_tool(
    name="roam_expand_toolset",
    description="List available tool presets or show contents of a preset. "
    "Presets: core (minimal), review, refactor, debug, architecture, compliance, compile-curated, and full (every tool). Pass a preset name to list its tools.",
)
def expand_toolset(preset: str = "") -> dict:
    """List available presets and their tools. Call to discover tools beyond the active preset.

    Parameters
    ----------
    preset:
        Preset name to inspect. If empty, lists all presets with tool counts.

    Returns: preset contents, active preset name, and restart instructions.
    """
    if preset and preset in _PRESETS:
        tools = sorted(_PRESETS[preset]) if _PRESETS[preset] else sorted(_REGISTERED_TOOLS)
        return {
            "active_preset": _ACTIVE_PRESET,
            "requested_preset": preset,
            "tool_count": len(tools),
            "tools": tools,
            "switch_instructions": (
                f"To switch to '{preset}' preset, restart the MCP server with: ROAM_MCP_PRESET={preset} roam mcp"
            ),
        }
    # List all presets
    presets_info = {}
    for name, tool_set in _PRESETS.items():
        count = len(tool_set) if tool_set else "all"
        presets_info[name] = {"tool_count": count}
    return {
        "active_preset": _ACTIVE_PRESET,
        "active_tool_count": len(_REGISTERED_TOOLS),
        "presets": presets_info,
        "switch_instructions": "Restart with ROAM_MCP_PRESET=<name> roam mcp",
    }


@_tool(
    name="roam_init",
    description="Initialize roam and build the first index. Task-mode for non-blocking setup.",
    output_schema=_SCHEMA_INIT,
    read_only=False,
    idempotent=False,
    task_mode="required",
)
async def roam_init(root: str = ".", yes: bool = True, ctx: _Context | None = None) -> dict:
    """Initialize roam for a repo and create the first index.

    WHEN TO USE: first run in a repository without a `.roam/index.db`.
    This is task-enabled for non-blocking setup in MCP clients.
    Streams phase-aware progress (discover → parse → extract → graph → metrics)
    when the client supports MCP progress notifications.
    """
    args = ["init"]
    if yes:
        args.append("--yes")
    if root != ".":
        args.extend(["--root", root])

    await _ctx_info(ctx, "Starting roam initialization.")

    if _mcp_progress is not None and ctx is not None:
        exit_code, stdout, stderr = await _mcp_progress.run_with_phase_progress(
            args, ctx=ctx, cwd=root, initial_message="initializing"
        )
        return _parse_subprocess_result(args, exit_code, stdout, stderr)

    await _ctx_report_progress(ctx, 5, total=100, message="initializing")
    result = await _run_roam_async(args, root)
    await _ctx_report_progress(ctx, 100, total=100, message="completed")
    return result


@_tool(
    name="roam_reindex",
    description="Incremental or force reindex. Task-mode + elicited confirmation for force runs.",
    output_schema=_SCHEMA_REINDEX,
    read_only=False,
    idempotent=False,
    task_mode="required",
)
async def roam_reindex(
    force: bool = False,
    verbose: bool = False,
    confirm_force: bool = False,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Refresh the code index (`roam index`) with async task support.

    WHEN TO USE: after large code changes, generated file churn, or parser upgrades.
    Use `force=True` for a full rebuild. If `force=True` and `confirm_force=False`,
    the tool requests user confirmation via MCP elicitation when available.
    """
    stop_response = await _force_reindex_stop_response(force, confirm_force, ctx)
    if stop_response is not None:
        return stop_response

    args = ["index"]
    if force:
        args.append("--force")
    if verbose:
        args.append("--verbose")

    await _ctx_info(ctx, "Starting index refresh.")

    if _mcp_progress is not None and ctx is not None:
        exit_code, stdout, stderr = await _mcp_progress.run_with_phase_progress(
            args, ctx=ctx, cwd=root, initial_message="indexing"
        )
        result = _parse_subprocess_result(args, exit_code, stdout, stderr)
    else:
        await _ctx_report_progress(ctx, 5, total=100, message="indexing")
        result = await _run_roam_async(args, root)
        await _ctx_report_progress(ctx, 100, total=100, message="completed")

    if force and "error" not in result:
        result["force"] = True
    return result


@_tool(
    name="roam_understand",
    description=(
        "Codebase briefing in one call. Returns stack + architecture "
        "layers + entry points + hotspots + conventions in ~2-4K tokens. "
        "Triggers: 'what is this repo?', 'where do I start?', 'give me "
        "the lay of the land'. Run this FIRST in an unfamiliar repo — "
        "Glob/Grep around comes later."
    ),
    output_schema=_SCHEMA_UNDERSTAND,
    task_mode="required",
)
async def roam_understand(
    root: str = ".",
    summarize: bool | None = None,
    ctx: _Context | None = None,
) -> dict:
    """Get a full codebase briefing in a single call.

    Call this FIRST when starting work on a new or unfamiliar codebase.
    Covers tech stack, architecture (layers, clusters, entry points),
    health score, hotspots, conventions, and patterns. ~2-4K token output.
    Do NOT explore manually with Glob/Grep/Read -- use this instead.
    Follow with search_symbol or context to drill into specifics.

    Parameters
    ----------
    summarize:
        If True and the client supports MCP sampling, returns a sampled
        prose briefing instead of the full JSON envelope. Falls back to
        the raw envelope when sampling is unavailable.
    """
    result = _run_roam(["understand"], root)
    task = _mcp_session.session_hint(ctx) if _mcp_session is not None else ""
    return await _maybe_summarize(result, ctx=ctx, summarize=summarize, task=task)


@_tool(name="roam_onboard", description="Generate a new-developer onboarding guide for the codebase.")  # W459
def onboard(detail: str = "normal", root: str = ".") -> dict:
    """Generate a new-developer onboarding guide for the codebase.

    WHEN TO USE: Call this when onboarding to a codebase and you need
    a structured learning path. Returns architecture overview, entry
    points, critical paths, risk areas, reading order, and conventions.
    More comprehensive than `understand` for onboarding; use `understand`
    for a quick briefing.

    Args: detail: 'brief', 'normal', or 'full' (default: 'normal').
    """
    args = ["onboard"]
    if detail and detail != "normal":
        args.extend(["--detail", detail])
    return _run_roam(args, root)


@_tool(
    name="roam_ask",
    description=(
        "Natural-language codebase question dispatcher. Examples: "
        "'is it safe to delete X?', 'where does login validate?', "
        "'what just broke?', 'who owns module Y?'. Routes intent to "
        "one recipe in the graph-aware 31-recipe registry. One call "
        "replaces Grep+Read for most questions. Run this FIRST when "
        "the user asks a code-comprehension question."
    ),
)
def ask(
    query: str,
    recipe: str = "",
    explain: bool = False,
    list_recipes: bool = False,
    root: str = ".",
) -> dict:
    """Run the recipe that matches a free-form query.

    WHEN TO USE: when you have an intent ("is it safe to delete X",
    "where does login validate", "what just broke") but don't know
    which roam command(s) compose into the answer. Maps your query
    to the right recipe via TF-IDF intent classification, runs the
    chosen recipe, and returns the composed result.

    Returns the standard ``ask`` envelope with ``summary.recipe``,
    ``summary.confidence``, and per-step results in ``steps``. On
    low confidence, returns the top-3 candidates with ``low_confidence``
    flag — caller can re-call with explicit ``recipe=...`` to force one.

    Parameters
    ----------
    query:
        Free-form natural-language question.
    recipe:
        Force a specific recipe by name (skips classification). Use
        ``list_recipes=True`` first to see available names.
    explain:
        Emit the recipe plan + intent + perspectives + gates without
        running it. Useful when verifying intent classification.
    list_recipes:
        Return the full recipe registry instead of running anything.
    """
    args = ["ask"]
    if list_recipes:
        args.append("--list")
    else:
        if explain:
            args.append("--explain")
        if recipe:
            args.extend(["--recipe", recipe])
        if query:
            args.append(query)
    return _run_roam(args, root)


@_tool(
    name="roam_health",
    description="Codebase health score (0-100) with issue breakdown, cycles, bottlenecks.",
    output_schema=_SCHEMA_HEALTH,
    task_mode="required",
)
async def health(
    root: str = ".",
    summarize: bool | None = None,
    ctx: _Context | None = None,
) -> dict:
    """Codebase health score (0-100) with issue breakdown.

    Call this to assess overall code quality before deciding where to
    focus refactoring, or to check whether recent changes degraded health.
    Skip if you already called understand (includes health) or preflight
    (includes it per-symbol).

    Parameters
    ----------
    summarize:
        If True and the client supports MCP sampling, returns a sampled
        prose summary that highlights the top 3 issues to fix. Falls
        back to the raw envelope when sampling is unavailable.
    """
    result = _run_roam(["health"], root)
    # when the issue count is huge, drop the verbose lists
    # and keep only the score + per-category counts. The full payload is
    # always fetched on disk (cache hit on the next call would still
    # have it); this just trims the per-call MCP transport. Caller can
    # still pass ``summarize=True`` for prose, or call without the
    # token-conscious shape by setting ``ROAM_MCP_HEALTH_FULL=1``.
    if isinstance(result, dict) and not os.environ.get("ROAM_MCP_HEALTH_FULL"):
        issue_count = (result.get("summary") or {}).get("issue_count", 0) or 0
        if issue_count >= 50:
            keep = {
                "_meta",
                "command",
                "schema",
                "schema_version",
                "summary",
                "health_score",
                "tangle_ratio",
                "propagation_cost",
                "category_severity",
                "score_breakdown",
            }
            trimmed = {k: v for k, v in result.items() if k in keep}
            trimmed["truncated"] = True
            trimmed["truncated_hint"] = (
                "issue list dropped (>= 50 issues). Run `roam health` from CLI for the full list, "
                "or set ROAM_MCP_HEALTH_FULL=1 for the unfiltered MCP envelope."
            )
            result = trimmed
    task = _mcp_session.session_hint(ctx) if _mcp_session is not None else ""
    return await _maybe_summarize(result, ctx=ctx, summarize=summarize, task=task)


@_tool(
    name="roam_preflight",
    description="Pre-change safety check: blast radius, tests, complexity, fitness. Call BEFORE modifying code.",
    output_schema=_SCHEMA_PREFLIGHT,
)
def roam_preflight(symbol: str = "", staged: bool = False, root: str = ".") -> dict:
    """Pre-change safety check. Call this BEFORE modifying any symbol or file.

    Combines blast radius, affected tests, complexity, coupling, and
    fitness violations in one call. Replaces 5-6 separate tool calls.
    Do NOT call context, impact, affected_tests, or complexity_report
    separately if preflight covers your need.

    Fix D: legacy alias ``target`` is still accepted (translates to
    ``symbol``) with a deprecation warning in ``summary.alias_warnings``.
    """
    args = ["preflight"]
    if symbol:
        args.append(symbol)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


preflight = roam_preflight


# ---------------------------------------------------------------------------
# R8.E3 — pre-apply change-plan validator
# ---------------------------------------------------------------------------


def _vp_check_symbol_exists(symbol: str, root: str = ".") -> tuple[bool, list[dict]]:
    """Return (found, candidates). Candidates are search-result rows
    (subset) so the caller can suggest alternatives on miss.
    """
    if not symbol:
        return False, []
    res = _run_roam(["search", symbol], root)
    if not isinstance(res, dict):
        return False, []
    matches = res.get("matches") or res.get("results") or []
    if not isinstance(matches, list):
        return False, []
    # Exact-name match first, qualified-name match second.
    exact = [m for m in matches if isinstance(m, dict) and m.get("name") == symbol]
    if exact:
        return True, exact[:5]
    qual = [m for m in matches if isinstance(m, dict) and m.get("qualified_name") == symbol]
    if qual:
        return True, qual[:5]
    return False, matches[:5] if isinstance(matches, list) else []


def _vp_blast_radius(symbol: str, root: str = ".") -> int | None:
    """Return number of incoming callers for ``symbol``, or None on
    error / when impact data is unavailable.

    The ``roam impact`` envelope exposes the count under
    ``summary.affected_symbols``; the top-level ``direct_dependents``
    and ``affected_symbols`` arrays are used as fallbacks if the
    summary shape ever changes.
    """
    res = _run_roam(["impact", symbol], root)
    if not isinstance(res, dict):
        return None
    summary = res.get("summary") or {}
    if isinstance(summary, dict):
        for key in ("affected_symbols", "callers_count", "affected", "total"):
            n = summary.get(key)
            if isinstance(n, int):
                return n
    for key in ("direct_dependents", "affected_symbols", "callers", "affected_callers"):
        arr = res.get(key)
        if isinstance(arr, list):
            return len(arr)
    return None


def _vp_check_target_file(file_path: str, must_exist: bool, root: str = ".") -> tuple[bool, str]:
    """Return (ok, reason). For ``move`` we want the file to exist OR be
    a writable new path. For ``add`` we want it to NOT exist (would
    collide). The implementation is filesystem-only — fast, no DB hit."""
    from pathlib import Path as _Path

    if not file_path:
        return False, "file_path is empty"
    base = _Path(root).resolve() if root and root != "." else _Path.cwd()
    target = (base / file_path).resolve()
    # Path-traversal guard: target must remain inside the project root.
    try:
        target.relative_to(base)
    except ValueError:
        return False, f"path escapes project root: {file_path}"
    exists = target.is_file()
    if must_exist and not exists:
        return False, f"target file does not exist: {file_path}"
    if (not must_exist) and exists:
        return False, f"target file already exists: {file_path}"
    if not target.parent.is_dir():
        return False, f"parent directory missing: {target.parent}"
    return True, "ok"


# Fix E (Sub-task 4) — registry of supported plan-operation kinds and
# the fields each one EXPECTS so the ``UNKNOWN_KIND`` blocker can name
# the alternatives instead of forcing the agent to guess. Keep the
# field lists short: the canonical schema lives in the validate_plan
# docstring; this registry is just the LLM-facing hint.
_VP_PLAN_KIND_FIELDS: dict[str, list[str]] = {
    "rename": ["kind", "symbol", "new_name"],
    "move": ["kind", "symbol", "target_file"],
    "remove": ["kind", "symbol"],
    "modify": ["kind", "symbol"],
    "add": ["kind", "file"],
}


class _VpAcc:
    """Mutable accumulator for findings emitted while validating one plan
    operation. Helpers push blockers / warnings / advice / facts; the
    orchestrator copies them into the response envelope at the end."""

    __slots__ = ("blockers", "warnings", "advice", "facts")

    def __init__(self) -> None:
        self.blockers: list[dict] = []
        self.warnings: list[dict] = []
        self.advice: list[str] = []
        self.facts: dict = {}

    def block(self, code: str, detail: str, **extras) -> None:
        """Append a blocker dict with ``code`` + ``detail`` + arbitrary extras."""
        b = {"code": code, "detail": detail}
        b.update(extras)
        self.blockers.append(b)

    def warn(self, code: str, detail: str) -> None:
        """Append a warning dict with ``code`` + ``detail``."""
        self.warnings.append({"code": code, "detail": detail})


def _vp_symbol_not_found_detail(symbol: str, candidates: list[dict]) -> str:
    """Format the ``SYMBOL_NOT_FOUND`` blocker detail with up to 3 candidate
    suggestions when fuzzy matches exist; bare not-in-index detail otherwise."""
    if not candidates:
        return f"symbol {symbol!r} not in index"
    names = ", ".join(c.get("name") or c.get("qualified_name") or "?" for c in candidates[:3])
    return f"symbol {symbol!r} not in index — did you mean: {names}"


def _vp_emit_blast_warning(symbol: str, blast: int, acc: _VpAcc) -> None:
    """Emit HIGH_BLAST_RADIUS (>50) or MEDIUM_BLAST_RADIUS (>10) warning
    based on caller count; stay silent below 10."""
    if blast > 50:
        acc.warn(
            "HIGH_BLAST_RADIUS",
            f"{symbol} has {blast} incoming callers — review impact before applying.",
        )
    elif blast > 10:
        acc.warn(
            "MEDIUM_BLAST_RADIUS",
            f"{symbol} has {blast} incoming callers — proceed with care.",
        )


def _vp_resolve_subject_symbol(op: dict, acc: _VpAcc, root: str) -> None:
    """For kinds that target an existing symbol (rename/move/remove/modify):
    require ``op['symbol']``, look it up in the index, populate
    ``facts['symbol_found']`` + ``facts['blast_radius']``, and emit
    MISSING_SYMBOL / SYMBOL_NOT_FOUND blockers or blast-radius warnings."""
    kind = (op.get("kind") or "").lower()
    symbol = op.get("symbol") or ""
    if not symbol:
        acc.block("MISSING_SYMBOL", f"{kind} requires 'symbol'")
        return
    found, candidates = _vp_check_symbol_exists(symbol, root)
    acc.facts["symbol_found"] = found
    if not found:
        acc.block("SYMBOL_NOT_FOUND", _vp_symbol_not_found_detail(symbol, candidates))
        return
    blast = _vp_blast_radius(symbol, root)
    acc.facts["blast_radius"] = blast
    if isinstance(blast, int):
        _vp_emit_blast_warning(symbol, blast, acc)


def _vp_validate_rename(op: dict, acc: _VpAcc, root: str) -> None:
    """Validate a rename op: require ``new_name``, warn on collisions with
    an existing symbol of the same name."""
    new_name = op.get("new_name") or ""
    if not new_name:
        acc.block("MISSING_NEW_NAME", "rename requires 'new_name'")
        return
    new_found, _ = _vp_check_symbol_exists(new_name, root)
    acc.facts["new_name_collision"] = new_found
    if new_found:
        acc.warn(
            "NAME_COLLISION",
            f"another symbol already uses {new_name!r} — rename may shadow it.",
        )
        acc.advice.append("run `roam search <new_name>` to inspect the collision.")


def _vp_validate_move(op: dict, acc: _VpAcc, root: str) -> None:
    """Validate a move op: require ``target_file``, accept both existing
    files (append) and new files (create); only block on invalid paths.
    A pre-existing target is signalled via ``facts['target_file_ok']`` but
    not blocked — the diff shape pins the merge semantics."""
    target_file = op.get("target_file") or ""
    if not target_file:
        acc.block("MISSING_TARGET_FILE", "move requires 'target_file'")
        return
    ok, reason = _vp_check_target_file(target_file, must_exist=False, root=root)
    acc.facts["target_file_ok"] = ok
    if not ok and "already exists" not in reason:
        acc.block("INVALID_TARGET_FILE", reason)


def _vp_validate_remove(op: dict, acc: _VpAcc, root: str) -> None:
    """Validate a remove op: block when the resolved symbol still has
    callers (positive blast radius) — they would break on removal.
    Reads ``facts['blast_radius']`` populated by ``_vp_resolve_subject_symbol``."""
    blast = acc.facts.get("blast_radius")
    if isinstance(blast, int) and blast > 0:
        acc.block(
            "REMOVE_HAS_CALLERS",
            f"cannot remove {op.get('symbol')!r} — {blast} callers would break. Migrate or update them first.",
        )


def _vp_validate_modify(op: dict, acc: _VpAcc, root: str) -> None:
    """Validate a modify op: soft op signalling intent. Surfaces preflight
    verdict + fitness-violation count as advisory warning; never blocks."""
    symbol = op.get("symbol") or ""
    if not symbol:
        return
    pre = _run_roam(["preflight", symbol], root)
    if not isinstance(pre, dict):
        return
    summary = pre.get("summary") or {}
    if not isinstance(summary, dict):
        return
    acc.facts["preflight_verdict"] = summary.get("verdict")
    fitness = summary.get("fitness_violations") or summary.get("violations")
    if isinstance(fitness, list) and fitness:
        acc.warn(
            "FITNESS_VIOLATIONS",
            f"{symbol} has {len(fitness)} fitness violation(s) — fix before adding new logic.",
        )


def _vp_validate_add(op: dict, acc: _VpAcc, root: str) -> None:
    """Validate an add op: require ``file``, verify the target path is sane
    (parent exists, not escaping the project root, not already present)."""
    file_path = op.get("file") or ""
    if not file_path:
        acc.block("MISSING_FILE", "add requires 'file'")
        return
    ok, reason = _vp_check_target_file(file_path, must_exist=False, root=root)
    acc.facts["file_ok"] = ok
    if not ok:
        acc.block("INVALID_ADD_FILE", reason)


def _vp_block_unknown_kind(kind: str, acc: _VpAcc) -> None:
    """Fix E (Sub-task 4) — emit UNKNOWN_KIND blocker enumerating supported
    kinds + per-kind expected fields so the agent can recover without
    dipping into docs. Per-kind field lists come from ``_VP_PLAN_KIND_FIELDS``."""
    supported = sorted(_VP_PLAN_KIND_FIELDS.keys())
    acc.block(
        "UNKNOWN_KIND",
        f"unsupported operation kind: {kind!r}. supported kinds: {', '.join(supported)}.",
        supported_kinds=supported,
        expected_fields=dict(_VP_PLAN_KIND_FIELDS),
    )


# Per-kind validator dispatch table. The 4 SUBJECT kinds (rename/move/
# remove/modify) get symbol resolution BEFORE their per-kind validator
# fires; ``add`` skips symbol resolution. Unknown kinds route to
# ``_vp_block_unknown_kind``.
_VP_KIND_VALIDATORS = {
    "rename": _vp_validate_rename,
    "move": _vp_validate_move,
    "remove": _vp_validate_remove,
    "modify": _vp_validate_modify,
    "add": _vp_validate_add,
}

_VP_SUBJECT_KINDS = frozenset({"rename", "move", "remove", "modify"})


def _vp_validate_one(idx: int, op: dict, root: str = ".") -> dict:
    """Validate a single change-plan operation. See :func:`validate_plan`
    for the operation schema. Implementation is split across ``_vp_*``
    helpers + a per-kind dispatch table; this orchestrator wires them
    together. Subject-kind ops run symbol resolution first so the
    per-kind validator can read ``facts['blast_radius']``."""
    kind = (op.get("kind") or "").lower()
    acc = _VpAcc()
    if kind in _VP_SUBJECT_KINDS:
        _vp_resolve_subject_symbol(op, acc, root)
    validator = _VP_KIND_VALIDATORS.get(kind)
    if validator is not None:
        validator(op, acc, root)
    else:
        _vp_block_unknown_kind(kind, acc)
    return {
        "index": idx,
        "kind": kind,
        "ok": not acc.blockers,
        "blockers": acc.blockers,
        "warnings": acc.warnings,
        "advice": acc.advice,
        "facts": acc.facts,
    }


def _vp_parse_plan_json(plan_json: str) -> tuple[list | None, dict | None]:
    """Parse ``plan_json`` (a JSON string) into an operations list.

    Extracted from :func:`validate_plan` to flatten its nesting. Returns
    ``(operations, None)`` on success, ``(None, error_envelope)`` when
    ``plan_json`` is not valid JSON, or ``(None, None)`` when the JSON is
    valid but carries no operations list (the caller then falls through to
    its own "no operations supplied" error).
    """
    import json as _json

    try:
        parsed = _json.loads(plan_json)
    except _json.JSONDecodeError as e:
        return None, _structured_error(
            {
                "error": f"plan_json is not valid JSON: {e}",
                "error_code": "USAGE_ERROR",
                "hint": "pass operations=[{...}] or plan_json='{\"operations\":[...]}'",
                "command": "roam_validate_plan",
            }
        )
    if isinstance(parsed, list):
        return parsed, None
    ops = parsed.get("operations") if isinstance(parsed, dict) else None
    if isinstance(ops, list):
        return ops, None
    return None, None


def _vp_malformed_op_result(idx: int) -> dict:
    return {
        "index": idx,
        "kind": "unknown",
        "ok": False,
        "blockers": [{"code": "MALFORMED_OP", "detail": f"operation {idx} is not an object"}],
        "warnings": [],
        "advice": [],
        "facts": {},
    }


def _vp_validate_operations(operations: list, root: str) -> tuple[list[dict], int, int]:
    op_results: list[dict] = []
    total_blockers = 0
    total_warnings = 0

    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            op_results.append(_vp_malformed_op_result(i))
            total_blockers += 1
            continue

        result = _vp_validate_one(i, op, root)
        op_results.append(result)
        total_blockers += len(result.get("blockers", []))
        total_warnings += len(result.get("warnings", []))

    return op_results, total_blockers, total_warnings


def _vp_plan_verdict(total_blockers: int, total_warnings: int) -> str:
    if total_blockers:
        return "blocked"
    if total_warnings:
        return "needs-review"
    return "ok"


@_tool(
    name="roam_validate_plan",
    description="Pre-apply validator for a multi-step change plan. Returns blockers, warnings, advice per operation.",
    version="1.0.0",
    output_schema=_SCHEMA_VALIDATE_PLAN,
)
def validate_plan(
    operations: list[dict] | None = None,
    plan_json: str = "",
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Validate a structured change plan BEFORE applying any operations.

    WHEN TO USE: After you've drafted a multi-step refactor (rename +
    move + remove + …) but BEFORE you start calling ``roam_mutate``.
    This tool runs the right safety checks per operation in one
    round-trip — symbol existence, name collisions, blast radius,
    target-file sanity, fitness violations — and returns a verdict
    plus per-operation findings.

    Cheaper and safer than calling ``roam_preflight`` + ``roam_impact``
    + ``roam_search_symbol`` separately for every operation in the plan.

    Operation schemas:

        {kind: "rename", symbol: "old_name", new_name: "new_name"}
        {kind: "move",   symbol: "MyClass.method", target_file: "src/new.py"}
        {kind: "remove", symbol: "deprecated_func"}
        {kind: "modify", symbol: "complex_func"}
        {kind: "add",    file: "src/new_module.py"}

    Parameters
    ----------
    operations:
        List of operation dicts. Pass this OR ``plan_json`` (not both).
    plan_json:
        Alternative: a JSON string with either ``[{...}, ...]`` or
        ``{"operations": [...]}``. Useful when the agent has the plan
        as a string already.
    root:
        Project root for the index lookup.

    Returns: ``{summary: {verdict, blockers_count, warnings_count, ...},
    operations: [...]}``. ``verdict`` is one of ``ok`` (no findings),
    ``needs-review`` (warnings only), or ``blocked`` (any blocker —
    do NOT call mutate until resolved).
    """
    if not operations and plan_json:
        parsed_ops, parse_err = _vp_parse_plan_json(plan_json)
        if parse_err is not None:
            return parse_err
        operations = parsed_ops

    if not operations or not isinstance(operations, list):
        return _structured_error(
            {
                "error": "no operations supplied",
                "error_code": "USAGE_ERROR",
                "hint": "pass operations=[{kind:'rename', symbol:'x', new_name:'y'}, ...]",
                "command": "roam_validate_plan",
            }
        )

    op_results, total_blockers, total_warnings = _vp_validate_operations(operations, root)
    verdict = _vp_plan_verdict(total_blockers, total_warnings)

    summary_text = (
        f"{verdict}: {len(op_results)} operation(s), {total_blockers} blocker(s), {total_warnings} warning(s)"
    )

    return {
        "schema": "roam-code.com/spec/validate-plan/v1",
        "schema_version": "1.0.0",
        "summary": {
            "verdict": verdict,
            "operations": len(op_results),
            "blockers_count": total_blockers,
            "warnings_count": total_warnings,
            "verdict_text": summary_text,
        },
        "operations": op_results,
    }


# ---------------------------------------------------------------------------
# R8.E8 — fetch a large response by handle
# ---------------------------------------------------------------------------


# Default byte limit returned by fetch_handle when no slice / section /
# jq projection is specified. Picked to fit comfortably inside the
# 100KB MCP envelope cap while still giving an agent a useful chunk.
_FETCH_HANDLE_DEFAULT_LIMIT = 20000
_FETCH_HANDLE_MAX_LIMIT = 200000  # hard cap so an agent can't ask for 10MB


def _try_real_jq(payload: object, expr: str) -> tuple[object, str | None] | None:
    """Delegate to the optional ``jq`` library when it is installed.

    Returns ``(result, None)`` on success, ``(None, error)`` on a jq-level
    evaluation failure, or ``None`` when the library is not installed so the
    caller can fall back to the built-in subset.
    """
    try:
        import jq as _jq  # type: ignore[import-not-found]
    except ImportError:
        # Expected-missing: the `jq` library is an optional dependency. Its
        # absence is the designed-for path. Narrow `except ImportError` so a
        # real error inside the jq package still propagates.
        return None

    try:
        return _jq.compile(expr).input(payload).first(), None
    except (ValueError, StopIteration) as exc:
        return None, f"jq evaluation failed: {exc}"


# Token regex for the built-in jq subset: a field name, an integer index,
# or a slice with optional endpoints. The literal '.' is a separator.
_JQ_TOKEN_RE = re.compile(
    r"(?P<field>[A-Za-z_][A-Za-z0-9_]*)|"
    r"\[(?P<idx>-?\d+)\]|"
    r"\[(?P<a>-?\d*):(?P<b>-?\d*)\]|"
    r"\."
)


def _token_from_match(match: re.Match[str]) -> tuple[str, object] | None:
    """Convert one regex match into a jq token tuple.

    Returns ``None`` for the literal '.' separator. Raises ``ValueError``
    for unsupported token shapes so the tokenizer can report a clean error.
    """
    if match.group("field") is not None:
        return "field", match.group("field")
    if match.group("idx") is not None:
        return "idx", int(match.group("idx"))
    a_raw = match.group("a")
    b_raw = match.group("b")
    if a_raw is not None or b_raw is not None:
        a = int(a_raw) if a_raw else None
        b = int(b_raw) if b_raw else None
        return "slice", (a, b)
    return None


def _tokenize_jq_subset(expr: str) -> tuple[list[tuple[str, object]], str | None]:
    """Validate and tokenize a jq expression into the supported built-in subset.

    Each token is a ``(kind, value)`` tuple where kind is one of
    ``field``, ``idx``, or ``slice``. Returns ``(tokens, None)`` on success,
    or ``([], error)`` for unsupported syntax so evaluation never touches the
    payload.
    """
    if expr == ".":
        return [], None
    if not expr.startswith("."):
        return [], (
            f"unsupported jq expression {expr!r}: must start with '.' (try '.field' or '.list[0]'); "
            "install the optional 'jq' Python package for full jq support."
        )

    rest = expr[1:]
    pos = 0
    tokens: list[tuple[str, object]] = []
    while pos < len(rest):
        m = _JQ_TOKEN_RE.match(rest, pos)
        if not m or m.start() != pos:
            return [], (
                f"unsupported jq token at {expr[pos + 1 :]!r}: only '.field', '[N]', '[a:b]' subset is supported. "
                "Install the optional 'jq' Python package for full jq support."
            )
        token = _token_from_match(m)
        if token is not None:
            tokens.append(token)
        pos = m.end()
    return tokens, None


def _apply_jq_field(cur: object, val: object) -> tuple[object, str | None]:
    """Project one object key access."""
    if not isinstance(cur, dict):
        return None, f"cannot apply .{val} to non-object (got {type(cur).__name__})"
    if val not in cur:
        return None, f"key {val!r} not found in object (available: {sorted(cur.keys())[:10]!r})"
    return cur[val], None


def _apply_jq_index(cur: object, val: object) -> tuple[object, str | None]:
    """Project one array index access."""
    if not isinstance(cur, list):
        return None, f"cannot apply [{val}] to non-array (got {type(cur).__name__})"
    try:
        return cur[val], None  # type: ignore[index]
    except IndexError:
        return None, f"index {val} out of range for array of length {len(cur)}"


def _apply_jq_slice(cur: object, a: int | None, b: int | None) -> tuple[object, str | None]:
    """Project one array slice."""
    if not isinstance(cur, list):
        return None, f"cannot apply slice to non-array (got {type(cur).__name__})"
    return cur[a:b], None  # type: ignore[index]


def _apply_jq_token(cur: object, kind: str, val: object, expr: str) -> tuple[object, str | None]:
    """Apply a single jq token to the current value.

    Returns ``(next_value, None)`` or ``(None, error_message)``. This keeps
    the main projection loop free of per-token type-checking and error
    formatting.
    """
    if kind == "field":
        return _apply_jq_field(cur, val)
    if kind == "idx":
        return _apply_jq_index(cur, val)
    if kind == "slice":
        a, b = val  # type: ignore[misc]
        return _apply_jq_slice(cur, a, b)
    return None, f"unsupported jq token kind {kind!r} in expression {expr!r}"


def _apply_jq_projection(payload: object, expr: str) -> tuple[object, str | None]:
    """Apply a jq-style projection expression to ``payload``.

    Returns ``(result, error_or_None)``. If the optional ``jq`` library is
    importable we delegate to it; otherwise we fall back to a tiny built-in
    parser that supports a useful subset:

      - ``.``                       — identity
      - ``.field`` / ``.field.sub`` — nested object key access
      - ``[N]`` / ``[-N]``          — list index
      - ``[start:end]``             — list slice
      - mixed e.g. ``.context.callers[0].name``, ``.list[:5]``

    Anything outside that subset (filters, pipes, functions, ``select``,
    arithmetic, etc.) returns a clean error envelope rather than crashing.
    """
    expr = (expr or "").strip()
    if not expr:
        return payload, None

    real_jq = _try_real_jq(payload, expr)
    if real_jq is not None:
        return real_jq

    tokens, err = _tokenize_jq_subset(expr)
    if err is not None:
        return None, err

    cur: object = payload
    for kind, val in tokens:
        cur, err = _apply_jq_token(cur, kind, val, expr)
        if err is not None:
            return None, err
    return cur, None


def _fetch_handle_section(
    *,
    handle: str,
    payload: object,
    section: str,
    top_keys: list[str],
    total_size: int,
) -> dict:
    if not isinstance(payload, dict):
        return _structured_error(
            {
                "error": f"section= requires the stored payload to be a JSON object (got {type(payload).__name__})",
                "error_code": "USAGE_ERROR",
                "hint": "use jq= for non-object payloads, or omit section= to get the byte-sliced default.",
                "command": "roam_fetch_handle",
            }
        )
    if section not in payload:
        return _structured_error(
            {
                "error": f"section {section!r} not found in payload",
                "error_code": "NO_RESULTS",
                "hint": f"available top-level keys: {top_keys[:20]!r}",
                "command": "roam_fetch_handle",
                "total_keys": top_keys,
            }
        )
    return {
        "command": "roam_fetch_handle",
        "summary": {
            "verdict": f"section {section!r} of handle {handle}",
            "mode": "section",
            "section": section,
            "total_size": total_size,
            "total_keys": top_keys,
        },
        "handle": handle,
        "section": section,
        "total_keys": top_keys,
        "data": payload[section],
    }


def _fetch_handle_jq(
    *,
    handle: str,
    payload: object,
    jq: str,
    top_keys: list[str],
    total_size: int,
) -> dict:
    result, err = _apply_jq_projection(payload, jq)
    if err is not None:
        return _structured_error(
            {
                "error": err,
                "error_code": "USAGE_ERROR",
                "hint": (
                    "supported subset: '.field', '.field.sub', '[N]', '[start:end]'. "
                    "Install the optional 'jq' Python package for full jq language support."
                ),
                "command": "roam_fetch_handle",
                "jq": jq,
            }
        )
    return {
        "command": "roam_fetch_handle",
        "summary": {
            "verdict": f"jq {jq!r} on handle {handle}",
            "mode": "jq",
            "jq": jq,
            "total_size": total_size,
            "total_keys": top_keys,
        },
        "handle": handle,
        "jq": jq,
        "total_keys": top_keys,
        "data": result,
    }


def _decode_fetch_handle_slice(slice_bytes: bytes) -> str:
    try:
        return slice_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return slice_bytes.decode("utf-8", errors="replace")


def _parse_fetch_handle_full_slice(slice_text: str) -> object | None:
    try:
        return json.loads(slice_text)
    except json.JSONDecodeError:
        return None


def _fetch_handle_byte_slice(
    *,
    handle: str,
    raw_bytes: bytes,
    offset: int,
    limit: int,
    top_keys: list[str],
    total_size: int,
) -> dict:
    if offset < 0:
        return _structured_error(
            {
                "error": f"offset must be >= 0 (got {offset})",
                "error_code": "USAGE_ERROR",
                "hint": "use offset=0 for the start of the payload; chain calls with next_offset to page through.",
                "command": "roam_fetch_handle",
            }
        )
    if limit < 0:
        return _structured_error(
            {
                "error": f"limit must be >= 0 (got {limit})",
                "error_code": "USAGE_ERROR",
                "hint": "use limit=0 for the safe default (20000 bytes), or a positive int for a custom chunk.",
                "command": "roam_fetch_handle",
            }
        )

    effective_limit = _FETCH_HANDLE_DEFAULT_LIMIT if limit == 0 else min(int(limit), _FETCH_HANDLE_MAX_LIMIT)
    end = min(offset + effective_limit, total_size)
    slice_bytes = raw_bytes[offset:end]
    has_more = end < total_size
    next_offset = end if has_more else None

    slice_text = _decode_fetch_handle_slice(slice_bytes)
    parsed_full = _parse_fetch_handle_full_slice(slice_text) if offset == 0 and not has_more else None

    envelope: dict = {
        "command": "roam_fetch_handle",
        "summary": {
            "verdict": (
                f"bytes [{offset}:{end}] of handle {handle} ({total_size:,} bytes total)"
                + (" — more available" if has_more else " — full payload returned")
            ),
            "mode": "byte_slice",
            "offset": offset,
            "limit": effective_limit,
            "end": end,
            "total_size": total_size,
            "has_more": has_more,
            "next_offset": next_offset,
            "total_keys": top_keys,
            "partial_success": has_more,
        },
        "handle": handle,
        "offset": offset,
        "end": end,
        "total_size": total_size,
        "has_more": has_more,
        "next_offset": next_offset,
        "total_keys": top_keys,
        "data": slice_text,
    }
    if parsed_full is not None:
        envelope["parsed"] = parsed_full
    return envelope


def _fetch_handle_request_error(handle: str, section: str, jq: str) -> dict | None:
    if not handle or not re.fullmatch(r"[0-9a-f]{16}", handle):
        return _structured_error(
            {
                "error": "handle must be a 16-char lowercase hex string",
                "error_code": "USAGE_ERROR",
                "hint": "pass the handle from a prior tool response — e.g. 'a1b2c3d4...' (16 chars).",
                "command": "roam_fetch_handle",
            }
        )
    if section and jq:
        return _structured_error(
            {
                "error": "section and jq are mutually exclusive",
                "error_code": "USAGE_ERROR",
                "hint": "pick one retrieval mode: section= picks one top-level key, jq= applies a projection.",
                "command": "roam_fetch_handle",
            }
        )
    return None


def _read_fetch_handle_payload(handle: str) -> tuple[str, object, dict | None]:
    target = _handle_storage_dir() / f"{handle}.json"
    if not target.is_file():
        return (
            "",
            None,
            _structured_error(
                {
                    "error": f"handle {handle!r} not found in {target.parent}",
                    "error_code": "NO_RESULTS",
                    "hint": (
                        "the response may have been cleaned up. Re-run the original tool call to regenerate the handle."
                    ),
                    "command": "roam_fetch_handle",
                }
            ),
        )
    try:
        raw_text = target.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as e:
        return (
            "",
            None,
            _structured_error(
                {
                    "error": f"could not read handle file: {type(e).__name__}: {e}",
                    "error_code": "PERMISSION_DENIED" if isinstance(e, OSError) else "COMMAND_FAILED",
                    "hint": "check filesystem permissions on .roam/responses/, then retry.",
                    "command": "roam_fetch_handle",
                }
            ),
        )
    return raw_text, payload, None


@_tool(
    name="roam_fetch_handle",
    description="Fetch all or part of a large payload by handle — supports byte slice, section pick, jq projection.",
    version="2.0.0",
    output_schema=_SCHEMA_FETCH_HANDLE,
)
def fetch_handle(
    handle: str = "",
    offset: int = 0,
    limit: int = 0,
    section: str = "",
    jq: str = "",
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Retrieve a large MCP response previously written to disk under a
    content-addressed handle. Supports chunked / projected retrieval so
    the agent never has to re-load the full 1MB+ payload in one shot.

    WHEN TO USE: When a tool returned a small envelope with
    ``is_handle=true`` and a ``handle: "<sha16>"`` field, call this to
    fetch part or all of the payload. Pick the retrieval mode that
    matches what you need:

    - **No params** (default): returns the first 20000 bytes of the
      serialised payload along with ``has_more`` and ``next_offset`` so
      you can page through with subsequent calls.
    - ``offset=N, limit=M``: returns the byte slice ``data[N:N+M]``.
    - ``section="key"``: returns the value of one top-level key (plus
      ``total_keys`` so you know what else is available).
    - ``jq=".context.callers[:10]"``: jq-style projection. Uses the
      ``jq`` Python library when available; otherwise falls back to a
      built-in subset covering ``.field``, ``.field.sub``, ``[N]``,
      ``[start:end]``.

    Parameters
    ----------
    handle:
        The 16-char hex handle returned by an earlier tool call.
    offset:
        Starting byte offset for the byte-slice mode (default 0).
    limit:
        Max bytes to return in byte-slice mode. 0 (default) means
        "use the safe default of 20000 bytes".
    section:
        Top-level key to extract. Mutually exclusive with ``jq``.
    jq:
        jq-style projection expression. Mutually exclusive with
        ``section``.
    root:
        Project root for the handle store (default ".").

    Returns: an envelope containing the requested slice/section/projection
    plus pagination metadata, or a structured error if the handle is
    unknown / arguments conflict.
    """
    request_error = _fetch_handle_request_error(handle, section, jq)
    if request_error is not None:
        return request_error

    raw_text, payload, read_error = _read_fetch_handle_payload(handle)
    if read_error is not None:
        return read_error

    raw_bytes = raw_text.encode("utf-8")
    total_size = len(raw_bytes)
    top_keys: list[str] = sorted(payload.keys()) if isinstance(payload, dict) else []

    # --- section pick ----------------------------------------------------
    if section:
        return _fetch_handle_section(
            handle=handle,
            payload=payload,
            section=section,
            top_keys=top_keys,
            total_size=total_size,
        )

    # --- jq projection ---------------------------------------------------
    if jq:
        return _fetch_handle_jq(
            handle=handle,
            payload=payload,
            jq=jq,
            top_keys=top_keys,
            total_size=total_size,
        )

    # --- byte slice (default) -------------------------------------------
    return _fetch_handle_byte_slice(
        handle=handle,
        raw_bytes=raw_bytes,
        offset=offset,
        limit=limit,
        top_keys=top_keys,
        total_size=total_size,
    )


# ---------------------------------------------------------------------------
# R8.E4 — situation-keyed compound entry points
#
# Each tool packages the 3-4 inspect/plan calls an agent typically makes for a
# given engineering situation into one round-trip. Cuts agent-loop chatter
# (and therefore tokens) without losing any signal — the agent gets the same
# data, just in one envelope instead of four.
#
# All four delegate to ``_compound_envelope`` which already handles the
# verdict aggregation + partial-success bookkeeping used by the existing
# ``roam_explore`` / ``roam_diagnose_issue`` compounds.
# ---------------------------------------------------------------------------


_EXPECTED_COMPOUND_RUN_ERRORS = (
    OSError,
    subprocess.SubprocessError,
    TimeoutError,
    json.JSONDecodeError,
)


def _safe_run(args: list[str], root: str) -> dict:
    """Wrapper around _run_roam that converts exceptions into structured
    error dicts so a partial failure in one sub-command doesn't poison
    the whole compound envelope."""
    try:
        out = _run_roam(args, root)
        if isinstance(out, dict):
            return out
        return {"error": f"unexpected return type: {type(out).__name__}"}
    except _EXPECTED_COMPOUND_RUN_ERRORS as exc:
        log_swallowed("mcp_server:safe_run_compound_command", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def _run_substrate(
    recipe_name: str,
    warnings_out: list[str],
    phase: str,
    fn: Callable,
    *args,
    default=None,
    **kwargs,
):
    """Run one substrate helper, catching recoverable boundary exceptions.

    On a clean call the result is returned as-is. On ``OSError`` or
    ``RuntimeError`` append a structured
    ``<recipe>_<phase>_failed:<exc_class>:<detail>`` marker to
    *warnings_out* and return *default*. Logic errors propagate:
    ``_safe_run`` already absorbs subcommand failures into error dicts,
    so anything else reaching here is a wiring bug that must surface.
    """
    try:
        return fn(*args, **kwargs)
    except (OSError, RuntimeError) as exc:
        warnings_out.append(f"{recipe_name}_{phase}_failed:{type(exc).__name__}:{exc}")
        return default


def _compound_child_default(error_code: str) -> dict:
    """Return the child-error shape used when a phase fails before JSON."""
    return {"error": error_code}


def _compound_phase_result_or_default(result: dict | None, error_code: str) -> dict:
    """Keep a compound section present when a child phase returns nothing."""
    return result or _compound_child_default(error_code)


def _materialize_compound_sections_preserving_recipe_order(
    section_factories: list[tuple[str, Callable[[], dict]]],
) -> list[tuple[str, dict]]:
    """Run child-section factories without hiding recipe-specific phases."""
    return [(name, factory()) for name, factory in section_factories]


def _bug_fix_sections_preserving_failure_provenance(
    _run_check_ao: Callable,
    symbol: str,
    root: str,
) -> list[tuple[str, dict]]:
    """Assemble bug-fix sections without losing the W607-AO boundary."""
    sections = []
    for section_name in ("diagnose", "affected_tests", "diff", "context"):
        if section_name == "diagnose":
            phase_result = _compound_phase_result_or_default(
                _run_check_ao(
                    "diagnose",
                    _safe_run,
                    [_cr("diagnose"), symbol],
                    root,
                    default=_compound_child_default("diagnose_w607ao_default"),
                ),
                "diagnose_w607ao_default",
            )
        elif section_name == "affected_tests":
            phase_result = _compound_phase_result_or_default(
                _run_check_ao(
                    "affected_tests",
                    _safe_run,
                    [_cr("affected-tests"), symbol],
                    root,
                    default=_compound_child_default("affected_tests_w607ao_default"),
                ),
                "affected_tests_w607ao_default",
            )
        elif section_name == "diff":
            phase_result = _compound_phase_result_or_default(
                _run_check_ao(
                    "diff",
                    _safe_run,
                    [_cr("diff")],
                    root,
                    default=_compound_child_default("diff_w607ao_default"),
                ),
                "diff_w607ao_default",
            )
        else:
            phase_result = _compound_phase_result_or_default(
                _run_check_ao(
                    "context",
                    _safe_run,
                    [_cr("context"), symbol],
                    root,
                    default=_compound_child_default("context_w607ao_default"),
                ),
                "context_w607ao_default",
            )
        sections.append((section_name, phase_result))
    return sections


def _finalize_compound_recipe(
    envelope: dict | None,
    command: str,
    sections: list[tuple[str, dict]],
    warnings_out: list[str],
    situation: str,
    target: str,
) -> dict:
    """Synthesize fallback envelope and merge substrate-CALL markers.

    If *envelope* is ``None`` (the aggregator raised), build a minimal
    envelope so the marker still rides home. Then thread any
    substrate-CALL markers onto both ``summary.warnings_out`` and the
    top-level ``warnings_out``, flipping ``partial_success`` when the
    bucket is non-empty.
    """
    if envelope is None:
        envelope = {
            "command": command,
            "summary": {
                "verdict": "PARTIAL — compound aggregator raised; see warnings_out",
                "partial_success": True,
                "failed_subcommands": [name for name, _ in sections],
                "sections": [],
                "situation": situation,
                "target": target,
            },
        }
    if warnings_out:
        summary = envelope.setdefault("summary", {})
        existing_summary_wo = list(summary.get("warnings_out") or [])
        summary["warnings_out"] = existing_summary_wo + list(warnings_out)
        summary["partial_success"] = True
        existing_top_wo = list(envelope.get("warnings_out") or [])
        envelope["warnings_out"] = existing_top_wo + list(warnings_out)
    return envelope


# ---------------------------------------------------------------------------
# Fix B (SYNTHESIS Pattern 5) — Compound recipe registry
#
# Every internal subcommand referenced by a compound recipe (for_refactor,
# for_security_review, for_bug_fix, …) MUST go through this map. The map's
# values are the CLI keys in ``roam.cli._COMMANDS``; the import-time
# sanity check below fails fast if a value drifts out of the registry.
#
# Why a registry instead of literal strings inline?
#   * The 212-eval dogfood caught two compound bugs that shipped silently:
#       - ``for_security_review`` called ``roam vuln`` (CLI key is
#         ``vulns``)
#       - ``for_refactor`` called ``roam complexity-report`` (CLI key is
#         ``complexity``)
#     A registry-key lookup raises ``ImportError`` at module load when
#     either value disappears from the CLI surface, so the next typo
#     never ships.
# ---------------------------------------------------------------------------

_COMPOUND_REGISTRY: dict[str, str] = {
    # safety / preflight gates
    "preflight": "preflight",
    "impact": "impact",
    "fitness": "fitness",
    "diff": "diff",
    "critique": "critique",
    # analysis
    "complexity": "complexity",  # was: complexity-report (typo)
    "clones": "clones",
    "taint": "taint",
    "vulns": "vulns",  # was: vuln (typo)
    "adversarial": "adversarial",
    # navigation
    "understand": "understand",
    "search": "search",
    "context": "context",
    "diagnose": "diagnose",
    "affected-tests": "affected-tests",
    # destructive helpers
    "safe-delete": "safe-delete",
}


def _verify_compound_registry() -> None:
    """Import-time gate: every registry value must be a live CLI command.

    Fail-fast prevents the ``vuln``/``vulns``-class typo from ever
    shipping again — the module won't import if a compound recipe key
    references a command that isn't in ``roam.cli._COMMANDS``.
    """
    missing = [v for v in _COMPOUND_REGISTRY.values() if v not in _COMMANDS]
    if missing:
        raise ImportError(
            "_COMPOUND_REGISTRY references CLI commands that don't exist: "
            f"{missing}. Update src/roam/mcp_server.py:_COMPOUND_REGISTRY or "
            "add the missing commands to src/roam/cli.py:_COMMANDS."
        )


_verify_compound_registry()


def _cr(key: str) -> str:
    """Resolve a compound-registry key to its live CLI command name.

    Raises ``KeyError`` (with the missing key) if a compound author
    references an unregistered key — caught immediately during the
    compound-level test rather than emitting a partial-success envelope
    with an opaque sub-error.
    """
    if key not in _COMPOUND_REGISTRY:
        raise KeyError(
            f"_COMPOUND_REGISTRY missing key {key!r} — add it to the "
            "registry in src/roam/mcp_server.py before invoking from a "
            "compound recipe."
        )
    return _COMPOUND_REGISTRY[key]


@_tool(
    name="roam_for_new_feature",
    description="Compound: understand + search + context + complexity for an area you're about to add code to.",
    version="1.0.0",
)
def for_new_feature(area: str = "", root: str = ".", ctx: _Context | None = None) -> dict:
    """Bundle the calls an agent typically makes when starting a new feature.

    WHEN TO USE: First tool to call when an agent gets a "build feature X"
    task. Returns codebase orientation, related symbols matching ``area``,
    the most relevant context block, and a complexity report so the agent
    knows whether the surrounding code is hot or simple.

    Parameters
    ----------
    area:
        Free-form area / feature name — e.g. ``"authentication"`` or
        ``"PaymentGateway"``. Used as the search query and (if it
        matches a real symbol) as the context anchor. Empty is allowed:
        the tool will return orientation + complexity-report only.

    Returns: compound envelope with sections {understand, search,
    context, complexity_report}. ``summary.verdict`` aggregates each
    sub-verdict.
    """
    _w607ar_warnings_out: list[str] = []

    def _run_check_ar(phase, fn, *args, default=None, **kwargs):
        """Run W607-AR substrate marker for ``for_new_feature_{phase}_failed``."""
        return _run_substrate("for_new_feature", _w607ar_warnings_out, phase, fn, *args, default=default, **kwargs)

    sections = _materialize_compound_sections_preserving_recipe_order(
        [
            (
                "understand",
                lambda: _compound_phase_result_or_default(
                    _run_check_ar(
                        "understand",
                        _safe_run,
                        [_cr("understand")],
                        root,
                        default=_compound_child_default("understand_w607ar_default"),
                    ),
                    "understand_w607ar_default",
                ),
            ),
            (
                "complexity_report",
                lambda: _compound_phase_result_or_default(
                    _run_check_ar(
                        "complexity_report",
                        _safe_run,
                        [_cr("complexity"), "--limit", "10"],
                        root,
                        default=_compound_child_default("complexity_w607ar_default"),
                    ),
                    "complexity_w607ar_default",
                ),
            ),
        ]
    )
    if area:
        search_sections = _materialize_compound_sections_preserving_recipe_order(
            [
                (
                    "search",
                    lambda: _compound_phase_result_or_default(
                        _run_check_ar(
                            "search",
                            _safe_run,
                            [_cr("search"), area],
                            root,
                            default=_compound_child_default("search_w607ar_default"),
                        ),
                        "search_w607ar_default",
                    ),
                )
            ]
        )
        sections.extend(search_sections)
        _search_result = search_sections[0][1]
        # Only fetch context if search found a symbol — context for an
        # unmatched query is wasted tokens.
        matches = []
        if isinstance(_search_result, dict):
            matches = _search_result.get("matches") or _search_result.get("results") or []
        if matches:
            top = matches[0] if isinstance(matches, list) and matches else None
            anchor = top.get("qualified_name") or top.get("name") if isinstance(top, dict) else None
            if anchor:
                sections.extend(
                    _materialize_compound_sections_preserving_recipe_order(
                        [
                            (
                                "context",
                                lambda: _compound_phase_result_or_default(
                                    _run_check_ar(
                                        "context",
                                        _safe_run,
                                        [_cr("context"), anchor],
                                        root,
                                        default=_compound_child_default("context_w607ar_default"),
                                    ),
                                    "context_w607ar_default",
                                ),
                            )
                        ]
                    )
                )

    envelope = _run_check_ar(
        "compound_envelope",
        _compound_envelope,
        "for-new-feature",
        sections,
        situation="new_feature",
        target=area,
        default=None,
    )
    return _finalize_compound_recipe(
        envelope,
        "for-new-feature",
        sections,
        _w607ar_warnings_out,
        situation="new_feature",
        target=area,
    )


@_tool(
    name="roam_for_bug_fix",
    description="Compound: diagnose + affected_tests + diff + context for a symbol you're about to debug.",
    version="1.0.0",
)
def for_bug_fix(symbol: str, root: str = ".", ctx: _Context | None = None) -> dict:
    """Bundle the calls an agent typically makes when investigating a bug.

    WHEN TO USE: When you've got a failing test or a reported bug and a
    suspect symbol in hand. Returns root-cause ranking, recent diffs
    that touched this area, the tests that cover it, and a context
    deep-dive — everything you'd otherwise fetch in 4 separate calls.

    Parameters
    ----------
    symbol:
        Required — the symbol you suspect is the bug source.

    Returns: compound envelope with sections {diagnose, affected_tests,
    diff, context}.
    """
    if not symbol:
        return _structured_error(
            {
                "error": "symbol is required for roam_for_bug_fix",
                "error_code": "USAGE_ERROR",
                "hint": "pass the symbol you suspect is the bug source — e.g. 'auth_handler' or 'User.save'.",
                "command": "roam_for_bug_fix",
            }
        )
    _w607ao_warnings_out: list[str] = []

    def _run_check_ao(phase, fn, *args, default=None, **kwargs):
        """Run W607-AO substrate marker for ``for_bug_fix_{phase}_failed``."""
        return _run_substrate("for_bug_fix", _w607ao_warnings_out, phase, fn, *args, default=default, **kwargs)

    # `roam diff` of the working tree shows what's recently been
    # touched in the area — context for whether this is a new
    # regression or a long-standing issue.
    sections = _bug_fix_sections_preserving_failure_provenance(_run_check_ao, symbol, root)
    envelope = _run_check_ao(
        "compound_envelope",
        _compound_envelope,
        "for-bug-fix",
        sections,
        situation="bug_fix",
        target=symbol,
        default=None,
    )
    return _finalize_compound_recipe(
        envelope,
        "for-bug-fix",
        sections,
        _w607ao_warnings_out,
        situation="bug_fix",
        target=symbol,
    )


@_tool(
    name="roam_for_refactor",
    description="Compound: preflight + impact + complexity_report + clones for a symbol you're about to refactor.",
    version="1.0.0",
)
def for_refactor(symbol: str, root: str = ".", ctx: _Context | None = None) -> dict:
    """Bundle the calls an agent typically makes before refactoring.

    WHEN TO USE: When the agent is about to restructure a symbol and
    needs blast radius + complexity + duplicate detection in one go.
    The output tells the agent (a) is this safe to touch, (b) is the
    body too complex to refactor in one pass, (c) are there other
    copies of this code that should be consolidated together.

    Parameters
    ----------
    symbol:
        Required — the symbol you're about to refactor.

    Returns: compound envelope with sections {preflight, impact,
    complexity_report, clones}.
    """
    if not symbol:
        return _structured_error(
            {
                "error": "symbol is required for roam_for_refactor",
                "error_code": "USAGE_ERROR",
                "hint": "pass the symbol you're about to refactor.",
                "command": "roam_for_refactor",
            }
        )
    _w607ag_warnings_out: list[str] = []

    def _run_check(phase, fn, *args, default=None, **kwargs):
        """Run W607-AG substrate marker for ``for_refactor_{phase}_failed``."""
        return _run_substrate("for_refactor", _w607ag_warnings_out, phase, fn, *args, default=default, **kwargs)

    # Fix B — go through ``_cr`` so the ``complexity-report`` typo can
    # never come back: any drift between this dict and the live CLI
    # surface raises ImportError at module load.
    # Cap at top-20 clone clusters; --top is the right flag (clones
    # uses --top, not --limit; CLI surface drift caught here).
    sections = _materialize_compound_sections_preserving_recipe_order(
        [
            (
                "preflight",
                lambda: _compound_phase_result_or_default(
                    _run_check(
                        "preflight",
                        _safe_run,
                        [_cr("preflight"), symbol],
                        root,
                        default=_compound_child_default("preflight_w607ag_default"),
                    ),
                    "preflight_w607ag_default",
                ),
            ),
            (
                "impact",
                lambda: _compound_phase_result_or_default(
                    _run_check(
                        "impact",
                        _safe_run,
                        [_cr("impact"), symbol],
                        root,
                        default=_compound_child_default("impact_w607ag_default"),
                    ),
                    "impact_w607ag_default",
                ),
            ),
            (
                "complexity_report",
                lambda: _compound_phase_result_or_default(
                    _run_check(
                        "complexity_report",
                        _safe_run,
                        [_cr("complexity"), "--limit", "5"],
                        root,
                        default=_compound_child_default("complexity_report_w607ag_default"),
                    ),
                    "complexity_report_w607ag_default",
                ),
            ),
            (
                "clones",
                lambda: _compound_phase_result_or_default(
                    _run_check(
                        "clones",
                        _safe_run,
                        [_cr("clones"), "--top", "20"],
                        root,
                        default=_compound_child_default("clones_w607ag_default"),
                    ),
                    "clones_w607ag_default",
                ),
            ),
        ]
    )
    envelope = _run_check(
        "compound_envelope",
        _compound_envelope,
        "for-refactor",
        sections,
        situation="refactor",
        target=symbol,
        default=None,
    )
    return _finalize_compound_recipe(
        envelope,
        "for-refactor",
        sections,
        _w607ag_warnings_out,
        situation="refactor",
        target=symbol,
    )


@_tool(
    name="roam_for_security_review",
    description="Compound: taint + vuln + critique + adversarial for a security review pass.",
    version="1.0.0",
)
def for_security_review(symbol: str = "", root: str = ".", ctx: _Context | None = None) -> dict:
    """Bundle the calls an agent typically makes during a security review.

    WHEN TO USE: When the agent needs to assess attack surface — either
    for a specific symbol (focused review) or across the whole codebase
    (broad sweep). Returns taint flows, known vulnerabilities, the
    critique of any staged diff, and an adversarial scan so the agent
    can ladder findings into the right layer.

    Parameters
    ----------
    symbol:
        Optional — when provided, scopes the adversarial scan to this
        symbol. Empty does a broad sweep.

    Returns: compound envelope with sections {taint, vuln, critique,
    adversarial}.
    """
    # W607-AJ — the substrate boundary mirrors the sibling compounds
    # (for_refactor / for_bug_fix / for_new_feature): every phase runs
    # through ``_run_substrate`` so recoverable boundary failures emit a
    # ``for_security_review_<phase>_failed:<exc>:<detail>`` marker into the
    # accumulator, while ``_safe_run`` keeps absorbing subcommand failures
    # into child ``error`` dicts. (An automated hygiene batch disconnected
    # this recipe from the marker layer — the only compound out of step
    # with its siblings; restored to the shared pattern.)
    _w607aj_warnings_out: list[str] = []

    def _run_check_aj(phase, fn, *args, default=None, **kwargs):
        """Run W607-AJ substrate marker for ``for_security_review_{phase}_failed``."""
        return _run_substrate("for_security_review", _w607aj_warnings_out, phase, fn, *args, default=default, **kwargs)

    # Fix B — go through ``_cr`` so the ``vuln`` typo (CLI key is
    # ``vulns``) can never come back. ``vulns list`` is the
    # subcommand-style invocation the CLI expects. ``critique`` reads the
    # working-tree diff (no-op if nothing's staged); it pairs naturally
    # here because the agent is often reviewing a PR's worth of changes.
    adv_args = [_cr("adversarial")]
    if symbol:
        adv_args.append(symbol)
    sections = _materialize_compound_sections_preserving_recipe_order(
        [
            (
                "taint",
                lambda: _compound_phase_result_or_default(
                    _run_check_aj(
                        "taint",
                        _safe_run,
                        [_cr("taint")],
                        root,
                        default=_compound_child_default("taint_w607aj_default"),
                    ),
                    "taint_w607aj_default",
                ),
            ),
            (
                "vulns",
                lambda: _compound_phase_result_or_default(
                    _run_check_aj(
                        "vulns",
                        _safe_run,
                        [_cr("vulns"), "list"],
                        root,
                        default=_compound_child_default("vulns_w607aj_default"),
                    ),
                    "vulns_w607aj_default",
                ),
            ),
            (
                "critique",
                lambda: _compound_phase_result_or_default(
                    _run_check_aj(
                        "critique",
                        _safe_run,
                        [_cr("critique")],
                        root,
                        default=_compound_child_default("critique_w607aj_default"),
                    ),
                    "critique_w607aj_default",
                ),
            ),
            (
                "adversarial",
                lambda: _compound_phase_result_or_default(
                    _run_check_aj(
                        "adversarial",
                        _safe_run,
                        adv_args,
                        root,
                        default=_compound_child_default("adversarial_w607aj_default"),
                    ),
                    "adversarial_w607aj_default",
                ),
            ),
        ]
    )
    envelope = _run_check_aj(
        "compound_envelope",
        _compound_envelope,
        "for-security-review",
        sections,
        situation="security_review",
        target=symbol or "(full repo)",
        default=None,
    )
    return _finalize_compound_recipe(
        envelope,
        "for-security-review",
        sections,
        _w607aj_warnings_out,
        situation="security_review",
        target=symbol or "(full repo)",
    )


@_tool(
    name="roam_search_symbol",
    description=(
        "Use for: 'where is X defined?' / 'find function Y' / 'locate "
        "class Z'. Pick over Bash grep for function/class/method lookups — "
        "PageRank-ranked file:line + qualified names, no string/comment "
        "false positives. For 3+ symbols use roam_batch_search; for "
        "callers use roam_uses."
    ),
    output_schema=_SCHEMA_SEARCH,
)
def search_symbol(query: str, root: str = ".") -> dict:
    """Find symbols by name (case-insensitive substring match).

    Call this when you know part of a symbol name and need the exact
    qualified name, file location, or kind. Use before context or impact
    to get the correct identifier. Do NOT use Grep for function
    definitions -- this is faster and returns structured data with
    PageRank importance."""
    return _run_roam(["search", query], root)


@_tool(
    name="roam_complete",
    description="Prefix completion for symbols / file paths / commands. Faster than search; returns just names.",
)
def complete_tool(prefix: str, kind: str = "symbol", limit: int = 30, root: str = ".") -> dict:
    """Autocomplete a partial symbol name, file path, or command.

    WHEN TO USE: Call this when you have a partial identifier (e.g.
    user typed ``user_lo``) and want a short list of valid completions
    before invoking a heavier tool like ``context`` or ``preflight``.
    Much cheaper than ``search_symbol`` because it only returns names.

    Parameters
    ----------
    prefix:
        Partial token to complete.
    kind:
        ``"symbol"`` (default), ``"path"``, ``"command"``, or ``"all"``.
    limit:
        Max completions to return (default 30).
    root:
        Project root for the index lookup.
    """
    if _mcp_completions is None:
        return _structured_error(
            {
                "error": "completion module unavailable",
                "error_code": "UNKNOWN",
                "hint": "the mcp_extras package is missing or failed to import.",
                "command": "roam_complete",
            }
        )
    # W3.1 parity fix: for ``kind == 'symbol'`` we go through the CLI's
    # strict LIKE-based prefix matcher (``_prefix_symbols``) instead of
    # the FTS5-backed helper. FTS5's camelCase tokenizer expands
    # ``MyUseFoo`` -> ``My Use Foo``, so a ``use*`` query would return
    # ``MyUseFoo`` even though its NAME doesn't start with "use" —
    # violating the literal-left-anchored-prefix contract this tool
    # promises. Mirrors the CLI ``roam complete`` semantics exactly.
    kind_norm = (kind or "symbol").lower()
    limit_clamped = max(1, int(limit))

    payload: dict[str, list[str]] = {}
    if kind_norm in ("symbol", "all"):
        try:
            from roam.commands.cmd_complete import _prefix_symbols
        except Exception as exc:  # noqa: BLE001 — completion degrades to empty list
            # cmd_complete unimportable — symbol completion returns []. Surface
            # under ROAM_VERBOSE: a broken cmd_complete module is a real bug,
            # not an expected absence.
            log_swallowed("mcp_server:complete:prefix_symbols_import", exc)
            _prefix_symbols = None  # type: ignore[assignment]
        if _prefix_symbols is not None:
            try:
                payload["symbols"] = _prefix_symbols(prefix, limit=limit_clamped)
            except Exception as exc:  # noqa: BLE001 — completion degrades to empty list
                # Prefix query raised (e.g. DB locked) — return [] rather than
                # break the call. Surface under ROAM_VERBOSE so a recurring
                # query failure isn't masked as "no completions found".
                log_swallowed("mcp_server:complete:prefix_symbols_query", exc)
                payload["symbols"] = []
        else:
            payload["symbols"] = []
    if kind_norm in ("path", "all"):
        payload["paths"] = _mcp_completions.complete_paths(prefix, limit=limit_clamped, root=root)
    if kind_norm in ("command", "all"):
        payload["commands"] = _mcp_completions.complete_commands(prefix, limit=limit_clamped)

    total = sum(len(v) for v in payload.values())
    return {
        "command": "roam_complete",
        "summary": {
            "verdict": f"{total} completion{'s' if total != 1 else ''} for {prefix!r}",
            "prefix": prefix,
            "kind": kind_norm,
            "match_mode": "prefix",
            "total": total,
        },
        **payload,
    }


# Back-compat for tests and in-process callers that imported the tool function
# before the MCP wrapper name was disambiguated from the CLI entrypoint.
complete = complete_tool


@_tool(
    name="roam_context",
    description=(
        "Get the minimum files + line ranges needed to understand or modify a "
        "symbol. Use when user says 'show me X', 'I need to change Y', 'how does "
        "Z work?'. Returns targeted reads ranked by PageRank — cheaper than "
        "Read'ing whole files. For pre-change safety (blast radius + tests + "
        "effects), use roam_prepare_change instead."
    ),
    output_schema=_SCHEMA_CONTEXT,
)
def context(
    symbol: str,
    task: str = "",
    session_hint: str = "",
    recent_symbols: str = "",
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Get the minimal context needed to work with a specific symbol.

    Call this when you need to understand or modify a function, class,
    or method. Returns exact files and line ranges to read. More targeted
    than understand. For pre-change safety checks, prefer preflight
    instead (includes context plus blast radius and tests).

    Session memory: if ``recent_symbols`` is empty, the server fills it
    in from this MCP session's symbol history; if ``session_hint`` is
    empty, the cached task hint is used. The ``symbol`` argument is
    recorded into session memory after the call.
    """
    if _mcp_session is not None:
        session_hint, recent_symbols = _mcp_session.merge_with_explicit(
            ctx,
            explicit_recent=recent_symbols,
            explicit_hint=session_hint,
        )
        _mcp_session.remember_symbol(ctx, symbol)
        if task:
            _mcp_session.remember_task_hint(ctx, task)
    args = ["context", symbol]
    if task:
        args.extend(["--task", task])
    _append_context_personalization_args(
        args,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
    )
    return _run_roam(args, root)


@_tool(
    name="roam_retrieve",
    description="Graph-aware context for free-form tasks: FTS5 + structural rerank (PageRank + clones) + token budget.",
    output_schema=_SCHEMA_RETRIEVE,
)
def retrieve_context(
    task: str,
    budget: int = 0,
    k: int = 0,
    rerank: str = "fast",
    seed_files: str = "",
    dry_run: bool = False,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Return ranked code spans for a free-form task.

    WHEN TO USE: when you have a natural-language description of what
    the user wants ("trace the login flow", "where is the n+1 query in
    checkout") rather than a known symbol. Picks the right spans by
    fusing FTS5 lexical match with structural ranking (personalised
    PageRank biased on inferred or supplied seeds, plus clone-class
    membership). Honours a token budget so the returned context fits
    inside the agent's working window.

    Differs from ``roam_context`` (which expects a symbol) and
    ``roam_search_symbol`` (which only returns names without budget /
    rerank). Reaches for the ``clone_pairs`` table populated by
    ``roam clones --persist`` for the clone-canonical signal —
    persisted clones are not required, but they make rankings sharper.

    Parameters
    ----------
    task: free-form description of what to find.
    budget: max output tokens (0 = use config default, typically 4000).
    k: max number of spans to return (0 = use config default, typically 20).
    rerank: ``"fast"`` (default — structural rerank with personalised PR)
        or ``"off"`` (lexical only). The "heavy" mode (ColBERT/jina-v3) was
        cut from the MVP per CodeRAG-Bench evidence; will be re-introduced
        when eval proves ≥3pt recall@20 lift.
    seed_files: comma-separated file paths to seed personalised PageRank
        (e.g. ``"src/auth.py,src/session.py"``). Falls back to
        symbol-token inference from *task* when empty.
    dry_run: return the search plan (candidate ids, scores, locations)
        without fetching span content. Useful when an agent wants to
        preview what *would* be retrieved before paying the token cost.

    The response's ``summary.low_confidence`` boolean is True when
    the top result probably doesn't match the task — branch on this
    to ask the user for clarification rather than chasing a likely
    red herring. (v12.12: this was previously only available by
    parsing the verdict string.)
    """
    if _mcp_session is not None:
        _mcp_session.remember_task_hint(ctx, task)
        # Inject session-recent symbols as additional seeds when caller didn't
        # supply explicit ones -- a small budget to avoid drowning the rerank.
        if not seed_files:
            recent = _mcp_session.recent_symbols(ctx, limit=3)
            if recent and not task.endswith(" ".join(recent)):
                # Append symbol names as additional task tokens; the retrieve
                # pipeline already extracts identifiers from the task string.
                task = f"{task} {' '.join(recent)}".strip()
    args = ["retrieve"]
    if budget:
        args.extend(["--budget", str(budget)])
    if k:
        args.extend(["--k", str(k)])
    if rerank:
        args.extend(["--rerank", rerank])
    for raw in (seed_files or "").split(","):
        path = raw.strip()
        if path:
            args.extend(["--seed-file", path])
    if dry_run:
        args.append("--dry-run")
    # `--` halts Click option parsing so a leading-dash task ("-v trace the
    # login flow") is treated as the positional task text instead of an
    # unknown option (which would drop the retrieval). Mirrors the compiler
    # trace probe's fix at compiler._probe_trace_for_task.
    args.extend(["--", task])
    return _run_roam(args, root)


@_tool(
    name="roam_fleet_plan",
    description="Plan a multi-agent fleet for a goal — graph-aware partition (Louvain + co-change) emits .roam-fleet.json for Composio / Copilot CLI / raw.",
    output_schema=_SCHEMA_FLEET_PLAN,
)
def fleet_plan(
    goal: str,
    n_agents: int = 0,
    adapter: str = "raw",
    branch_prefix: str = "fleet",
    root: str = ".",
) -> dict:
    """Plan a multi-agent fleet for a free-form goal.

    WHEN TO USE: when an agent runtime (Composio Agent Orchestrator,
    GitHub Copilot CLI ``/fleet``, Cursor Background Agents) is about
    to dispatch parallel sub-tasks across a codebase. Roam's planner
    runs Louvain partitioning + dark-matter co-change + personalised
    PageRank to emit a `.roam-fleet.json` envelope where every task
    has a file scope, conflict-risk label, and a suggested branch
    name. Competitors compute this with an LLM scoping pass over file
    paths; roam-code computes it deterministically over the indexed
    graph in seconds.

    Parameters
    ----------
    goal: free-form description of the fleet's intent.
    n_agents: number of agents (``0`` = auto-detect from cluster count).
    adapter: ``"raw"`` (default), ``"composio"``, ``"copilot"``.
    branch_prefix: prefix for suggested per-task branches
        (e.g. ``"fleet/3-billing"``).
    """
    args = ["fleet", "plan", goal]
    if n_agents:
        args.extend(["--n-agents", str(n_agents)])
    if adapter and adapter != "raw":
        args.extend(["--adapter", adapter])
    if branch_prefix and branch_prefix != "fleet":
        args.extend(["--branch-prefix", branch_prefix])
    return _run_roam(args, root)


@_tool(
    name="roam_critique",
    description=(
        "Post-edit patch verifier. Pass `git diff` output as diff_text. "
        "Catches clones-not-edited (sibling duplicates the agent missed) "
        "and high-blast-radius edits. Grounded in the indexed graph, not "
        "heuristics. Triggers: 'review my patch', 'is this PR safe?', "
        "after generating any non-trivial diff."
    ),
    output_schema=_SCHEMA_CRITIQUE,
)
def critique_patch(
    diff_text: str,
    high_callers: int = 10,
    intent: str = "",
    root: str = ".",
) -> dict:
    """Verify a unified diff against the indexed roam graph.

    WHEN TO USE: after generating or reviewing a patch, before merging.
    Runs the killer **clones-not-edited** check (requires
    ``roam clones --persist`` to have been run) plus a blast-radius
    finding for symbols with a high direct-caller count. Output is
    severity-ranked; the top finding is surfaced in ``top_finding``.

    Differs from ``roam_diff`` (which gives the structural delta of
    *uncommitted* changes against the working tree) and ``roam_pr_risk``
    (which produces a vibe score). Critique grounds every finding in a
    DB query.

    The response carries ``bench_hint`` at top level and inside
    ``summary`` when the diff touches a structurally hot path
    (retrieve/, graph/, languages/, taint, critique). Branch on this
    field to suggest the right validation step alongside the standard
    findings.

    Parameters
    ----------
    diff_text:
        A unified diff. Pass the literal output of ``git diff`` /
        ``gh pr diff <id>``.
    high_callers:
        Threshold for the blast-radius warning. Default 10.
    intent:
        Optional PR title or commit subject. When supplied, the
        intent-vs-semantic-diff check fires (a rename intent that
        produces non-rename changes flags as misalignment). Falls
        back to the latest git commit subject when empty.
    """
    import json as _json
    import subprocess
    import sys

    if not diff_text or not diff_text.strip():
        return _structured_error(
            {
                "error": "empty diff",
                "error_code": "EMPTY_INPUT",
                "hint": "pass the output of `git diff` (or `gh pr diff <id>`) as diff_text",
                "command": "roam_critique",
            }
        )

    # Catch the common "shell substitution silently produced non-diff text"
    # case (truncated buffers, wrong format) before invoking the subprocess
    # — gives a faster, clearer failure than RUN_FAILED on stderr.
    from roam.critique.checks import looks_like_unified_diff as _looks_diff

    if not _looks_diff(diff_text):
        return _structured_error(
            {
                "error": "diff_text is not a recognisable unified diff",
                "error_code": "INVALID_DIFF",
                "hint": (
                    "expected unified diff with diff/---/+++/@@ headers — pass "
                    "the literal output of `git diff` or `gh pr diff <id>`"
                ),
                "command": "roam_critique",
            }
        )

    # Use a JSON-mode subprocess so we get the structured envelope.
    args = [sys.executable, "-m", "roam", "--json", "critique"]
    if high_callers != 10:
        args += ["--high-callers", str(high_callers)]
    if intent:
        args += ["--intent", intent]
    proc = subprocess.run(
        args,
        cwd=root,
        input=diff_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    # Exit code 5 = high-severity gate; not an error.
    if proc.returncode not in (0, 5):
        return _structured_error(
            {
                "error": (proc.stderr or "critique failed").strip()[:600],
                "error_code": "RUN_FAILED",
                "hint": "ensure the index is fresh (`roam index`) and the diff is well-formed",
                "command": "roam_critique",
            }
        )
    try:
        return _json.loads(proc.stdout)
    except _json.JSONDecodeError as exc:
        return _structured_error(
            {
                "error": f"could not parse critique JSON: {exc}",
                "error_code": "JSON_DECODE",
                "hint": "this is a roam bug — please file an issue",
                "command": "roam_critique",
            }
        )


# ---------------------------------------------------------------------------
# v12.1 — Boolean oracle alias + batch helper
# ---------------------------------------------------------------------------
# The five canonical boolean-oracle wrappers (``roam_oracle_symbol_exists``,
# ``roam_oracle_route_exists``, ``roam_oracle_is_test_only``,
# ``roam_oracle_is_reachable_from_entry``, ``roam_oracle_is_clone_of``)
# live in the W305 cluster further down in this file. They previously
# also lived here as a duplicate older block; W432 removed the duplicates
# (two ``@_tool(name=...)`` decorations with the same name silently
# overwrite ``_TOOL_METADATA`` and produce undefined dispatch behaviour
# under FastMCP). This region retains only the two wrappers that are NOT
# duplicated by the W305 cluster: the ``roam_oracle_test_only`` short-name
# alias and the ``roam_oracle_batch`` multi-query helper.


@_tool(
    name="roam_oracle_test_only",
    description="Alias of roam_oracle_is_test_only — preserves the shorter name agents sometimes guess.",
    output_schema=_SCHEMA_ORACLE,
)
def oracle_test_only_alias(symbol: str, root: str = ".") -> dict:
    """Alias of :func:`oracle_is_test_only`.

    Round 4 #15 reported agents calling ``roam_oracle_test_only``
    (without the ``is_`` prefix) and getting ``No such tool``. The alias
    keeps the canonical name discoverable while accepting the shorter
    form so a typo doesn't cost an MCP round-trip.

    Fix D: legacy alias ``name`` is still accepted.
    """
    return _run_roam(["oracle", "is-test-only", symbol], root)


@_tool(
    name="roam_oracle_batch",
    description=(
        "Run multiple oracle queries in one call. Items: [{name, oracle, "
        "max_hops?}, ...] where oracle is one of symbol-exists, route-exists, "
        "is-test-only, is-reachable-from-entry, is-clone-of."
    ),
)
def oracle_batch(items: list, root: str = ".") -> dict:
    """Batch multiple oracle queries.

    WHEN TO USE: replaces N round-trips when verifying multiple symbols.
    Each item declares which oracle to invoke and the symbol/path to query.
    Supports the full tri-state envelope per result.

    Parameters
    ----------
    items: list of dicts. Required keys:
      - ``oracle``: one of ``symbol-exists``, ``route-exists``,
        ``is-test-only``, ``is-reachable-from-entry``, ``is-clone-of``.
      - ``name`` (or ``path`` for route-exists): the query target.
      - ``max_hops`` (optional, is-reachable-from-entry only).

    Returns: ``{"results": [{...envelope per query...}]}``. Each entry
    carries its own ``summary.value`` / ``summary.reason_class`` /
    ``summary.confidence`` fields so a single batch call answers many
    independent assumption-checks at once.
    """
    from roam.commands.cmd_oracle import (
        oracle_is_clone_of as _is_clone_of,
    )
    from roam.commands.cmd_oracle import (
        oracle_is_reachable_from_entry as _is_reachable,
    )
    from roam.commands.cmd_oracle import (
        oracle_is_test_only as _is_test_only,
    )
    from roam.commands.cmd_oracle import (
        oracle_route_exists as _route_exists,
    )
    from roam.commands.cmd_oracle import (
        oracle_symbol_exists as _symbol_exists,
    )
    from roam.commands.resolve import ensure_index
    from roam.db.connection import open_db

    if not isinstance(items, list) or not items:
        return _structured_error(
            {
                "error": "items must be a non-empty list",
                "error_code": "EMPTY_INPUT",
                "hint": "pass [{oracle: 'symbol-exists', name: 'foo'}, ...]",
                "command": "roam_oracle_batch",
            }
        )

    from pathlib import Path

    ensure_index()
    results: list[dict] = []
    project_root_path = Path(root) if isinstance(root, str) else Path(".")

    def _error_row(item, message: str) -> dict:
        return {
            "status": "error",
            "error": message,
            "input": item,
        }

    with open_db(readonly=True, project_root=project_root_path) as conn:
        for item in items:
            if not isinstance(item, dict):
                results.append(_error_row(item, "item must be a dict"))
                continue
            oracle_name = (item.get("oracle") or "").strip()
            target = item.get("name") or item.get("path") or ""
            try:
                if oracle_name == "symbol-exists":
                    r = _symbol_exists(conn, target)
                elif oracle_name == "route-exists":
                    r = _route_exists(conn, target)
                elif oracle_name == "is-test-only":
                    r = _is_test_only(conn, target)
                elif oracle_name == "is-reachable-from-entry":
                    r = _is_reachable(conn, target, max_hops=int(item.get("max_hops", 10)))
                elif oracle_name == "is-clone-of":
                    r = _is_clone_of(conn, target)
                else:
                    results.append(_error_row(item, f"unknown oracle '{oracle_name}'"))
                    continue
            except Exception as exc:
                results.append(_error_row(item, f"{oracle_name} crashed: {exc}"))
                continue
            results.append(
                {
                    "oracle": oracle_name,
                    "target": target,
                    "value": r.value,
                    "reason": r.reason,
                    "reason_class": r.reason_class,
                    "confidence": r.confidence,
                }
            )

    return {
        "command": "oracle:batch",
        "summary": {"verdict": f"{len(results)} oracle queries", "count": len(results)},
        "results": results,
    }


# ---------------------------------------------------------------------------
# v12.2 — compliance preset MCP tools (4): taint, sbom, cga_emit, cga_verify
# Direct exposure of the security/attestation surface so agents in the
# `compliance` preset can audit a codebase end-to-end without shelling
# out. Counter to the v12.2 audit-finding gap (these were CLI-only).
# ---------------------------------------------------------------------------


@_tool(
    name="roam_taint",
    description=(
        "Graph-reach taint analysis. Returns OpenVEX-shaped findings "
        "(spec-legal status + justification — never `code_not_reachable`). "
        "10 starter rule packs: sqli, xss, ssrf, path-traversal, "
        "command-injection, deserialization, open-redirect, urllib, "
        "socketio, fileupload. Pair with --ci to gate on findings (exit 5)."
    ),
)
def taint(
    rules_dir: str = "",
    rule: str = "",
    rules_pack: str = "",
    ci: bool = False,
    root: str = ".",
) -> dict:
    """Run static taint analysis on the indexed graph.

    WHEN TO USE: as the first stage of a security audit, before running
    the LLM-augmented ``roam_taint_classify``. Returns reach-only
    findings with OpenVEX-shaped status/justification fields ready for
    SBOM/CGA embedding.

    Parameters
    ----------
    rules_pack: shorthand for filtering to a single starter pack.
        Accepts ``sqli``, ``xss``, ``ssrf``, ``path-traversal``,
        ``command-injection``, ``deserialization``, ``open-redirect``,
        ``urllib``, ``socketio``, ``fileupload``. Equivalent to
        passing ``--rules-pack`` on the CLI.
    """
    args = ["taint"]
    if rules_dir:
        args.extend(["--rules-dir", rules_dir])
    if rule:
        args.extend(["--rule", rule])
    if rules_pack:
        args.extend(["--rules-pack", rules_pack])
    if ci:
        args.append("--ci")
    return _run_roam(args, root)


@_tool(
    name="roam_sbom",
    description=(
        "Emit a Software Bill of Materials (CycloneDX 1.7 by default, or "
        "SPDX 2.3) enriched with call-graph reachability — distinguishes "
        "phantom dependencies from those actually exercised. Pair with "
        "--aibom for the AIBOM extension required by EU AI Act Art. 50."
    ),
)
def sbom(
    fmt: str = "cyclonedx",
    aibom: bool = False,
    root: str = ".",
) -> dict:
    """Emit an SBOM in the requested format.

    WHEN TO USE: regulatory artifact emission. The CycloneDX 1.7 path
    embeds an AIBOM extension when ``aibom=True`` — binds AI-authored
    commits to the indexed symbols they touched. Required for the
    EU AI Act Art. 50 disclosure (effective 2026-08-02) and the GPAI
    Code of Practice.

    Parameters
    ----------
    fmt: ``"cyclonedx"`` (default) or ``"spdx"``.
    aibom: include the AIBOM extension (CycloneDX only).
    """
    args = ["sbom", "--format", fmt, "--stdout"] if False else ["sbom", "--format", fmt]
    if aibom:
        args.append("--aibom")
    # Unlike most other tools, sbom writes to a file by default. We pass
    # an explicit --output so the result is captured in the JSON envelope
    # and the agent can read it back.
    return _run_roam(args, root)


@_tool(
    name="roam_cga_emit",
    description=(
        "Emit a Code Graph Attestation — in-toto v1 statement with "
        "predicate type `https://roam-code.com/spec/CodeGraph/v1` (or "
        "`https://roam-code.com/spec/CodeGraph-AIBOM/v1` with --aibom). "
        "Merkle root over symbol fingerprints + "
        "edge-bundle digest. Optional cosign keyless or offline signing."
    ),
)
def _mcp_cga_emit(
    include_taint: bool = False,
    aibom: bool = False,
    sign: bool = False,
    key: str = "",
    keyless: bool = False,
    root: str = ".",
) -> dict:
    """Emit a Code Graph Attestation.

    WHEN TO USE: at release / merge time. The attestation is reproducible
    — same source tree + same git HEAD → same Merkle root. Pair with
    cosign signing to add identity proof on top of the deterministic
    fingerprint. With ``aibom=True`` the predicate promotes to
    ``CodeGraph-AIBOM/v1`` and embeds AI-authored commit attribution.
    """
    args = ["cga", "emit"]
    if include_taint:
        args.append("--include-taint")
    if aibom:
        args.append("--aibom")
    if sign:
        args.append("--sign")
        if key:
            args.extend(["--key", key])
        if keyless:
            args.append("--keyless")
    return _run_roam(args, root)


@_tool(
    name="roam_cga_verify",
    description=(
        "Verify a Code Graph Attestation — re-derives the Merkle root + "
        "edge-bundle digest from the live DB and compares to the bundled "
        "predicate, AND verifies the cosign signature on the sibling "
        "`.bundle`. Fails closed (exit 5) when no bundle is present unless "
        "no_cosign=True is passed to acknowledge predicate-only verification."
    ),
)
def cga_verify(
    input_path: str = "",
    cosign_bundle: str = "",
    cosign_key: str = "",
    no_cosign: bool = False,
    root: str = ".",
) -> dict:
    """Verify a CGA statement file against the live indexed DB.

    WHEN TO USE: at audit / receipt time. Pair with the public key
    distributed alongside the codebase to verify both the Merkle digest
    AND the cosign identity in one call.

    Parameters
    ----------
    input_path:
        Path to the CGA statement JSON file to verify (required at call
        time; defaulted to ``""`` to keep the alias-wrapper schema
        permissive — the CLI returns exit 2 on an empty path). Legacy
        callers using ``statement_path=`` are translated transparently
        with a deprecation warning under ``summary.alias_warnings``.
    """
    args = ["cga", "verify", input_path]
    if cosign_bundle:
        args.extend(["--cosign-bundle", cosign_bundle])
    if cosign_key:
        args.extend(["--cosign-key", cosign_key])
    if no_cosign:
        args.append("--no-cosign")
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# v12.1 — LLM-augmented taint classification (1)
# ---------------------------------------------------------------------------


@_tool(
    name="roam_taint_classify",
    description=(
        "Run `roam taint` then ask the agent's own LLM (via MCP sampling) to "
        "classify each reachable finding as IDOR/AUTHZ/SQLI/XSS/CMD_INJECTION/etc. "
        "with confidence + reasoning. Counter to Semgrep Multimodal — same LLM-"
        "reasoning narrative without a hosted API key."
    ),
)
async def taint_classify(
    rules_dir: str = "",
    rule: str = "",
    skip_sanitized: bool = True,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Run taint analysis + LLM classification of each reachable finding.

    WHEN TO USE: when you need IDOR / broken-authz / business-logic
    classification on top of a graph-reach taint result. The static engine
    proves a path exists; this tool asks the agent's model what *kind*
    of vulnerability that path constitutes. Sanitized findings are skipped
    by default (they're already OpenVEX ``not_affected``).

    Sampling is opt-in on the client side. When the client doesn't expose
    ``ctx.sample`` (no sampling capability, or the tool ran outside MCP),
    each finding is returned unchanged — the static taint output is the
    floor, classification is the ceiling.

    Parameters
    ----------
    rules_dir: directory of YAML taint rules (default = built-in pack).
    rule: filter to a single rule id.
    skip_sanitized: when True (default), skip findings the static engine
        already marked sanitized. Set False to also classify ``not_affected``
        results — useful for double-checking false-clean signals.
    """

    from roam.security.taint_classifier import (
        ClassifyOptions,
        classify_findings,
    )

    # First run roam taint --json to get the structured findings list.
    args = ["taint"]
    if rules_dir:
        args.extend(["--rules-dir", rules_dir])
    if rule:
        args.extend(["--rule", rule])
    base_result = _run_roam(args, root)

    findings = base_result.get("findings") if isinstance(base_result, dict) else None
    if not findings:
        # Either taint failed or returned zero findings; pass through.
        return base_result

    # Now classify each finding via sampling. Graceful pass-through when no ctx.
    options = ClassifyOptions(skip_sanitized=skip_sanitized)
    classified = await classify_findings(findings, ctx, options=options)
    out = dict(base_result)
    out["findings"] = classified

    # Roll classification labels into the summary so agents can see at a
    # glance which categories were detected.
    label_counts: Counter[str] = Counter(
        lbl for f in classified if (lbl := (f.get("classification") or {}).get("label"))
    )
    summary = dict(out.get("summary") or {})
    summary["classification_counts"] = dict(label_counts)
    summary["classified_count"] = sum(label_counts.values())
    out["summary"] = summary
    return out


@_tool(
    name="roam_trace",
    description="Shortest dependency path between two symbols with hop details.",
    output_schema=_SCHEMA_TRACE,
)
def trace_tool(source: str, symbol: str, root: str = ".") -> dict:
    """Find the shortest dependency path between two symbols.

    Call this to understand HOW a change in one symbol could affect
    another. Shows path hops with symbol names, edge types, locations,
    and coupling strength.

    Parameters
    ----------
    source:
        The starting symbol of the dependency path.
    symbol:
        The destination symbol. W430/Fix-D canonical; legacy
        ``target=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning.
    """
    return _run_roam(["trace", source, symbol], root)


@_tool(
    name="roam_impact",
    description=(
        "Blast radius for 'is it safe to change?' — symbols + files affected, in "
        "5 lines. Compact decision-support output. Round 4 / S: the right "
        "default tool for safety-checks; preflight is heavier."
    ),
    output_schema=_SCHEMA_IMPACT,
)
def impact(symbol: str, root: str = ".") -> dict:
    """Show the blast radius of changing a symbol.

    WHEN TO USE: as the FIRST safety check before touching a symbol.
    Compact output (typical: 2-5 lines) directly answers "if I change
    this, what breaks?". Round 4 dogfood promoted this as the default
    decision-support tool — cleaner than ``roam_diagnose`` for the
    binary safety question, and lighter than ``roam_prepare_change``.

    Everything that would break if the signature or behavior changed.
    Affected symbols by hop distance, affected files, severity. Step up
    to ``roam_preflight`` when you also need test coverage + fitness
    rule analysis on the same target."""
    return _run_roam(["impact", symbol], root)


@_tool(
    name="roam_file_info",
    description="File skeleton: all symbols with signatures, kinds, line ranges.",
)
def file_info(path: str, root: str = ".") -> dict:
    """Show a file skeleton: every symbol definition with its signature.

    Call this to understand what a file contains without reading the
    full source. More useful than Read for getting a file overview.

    Fix A (SYNTHESIS Pattern 1 — JSON-parse-on-empty-input): pre-validate
    that *path* is non-empty and exists; emit a clean ``state=no_data``
    envelope instead of letting the downstream CLI emit empty stdout
    that the agent would then feed to ``json.loads`` and crash on.
    """
    if not path or not isinstance(path, str) or not path.strip():
        return {
            "command": "roam_file_info",
            "summary": {
                "verdict": "no data",
                "state": "no_data",
                "partial_success": False,
            },
            "data": [],
            "hint": "pass a project-relative file path — e.g. src/roam/cli.py",
        }
    # Resolve under root so a non-default project cwd still validates.
    try:
        candidate = Path(root) / path if root and root != "." else Path(path)
        candidate_exists = candidate.exists()
    except OSError:
        candidate_exists = False
    if not candidate_exists:
        return {
            "command": "roam_file_info",
            "summary": {
                "verdict": "no data",
                "state": "no_data",
                "partial_success": False,
            },
            "data": [],
            "path": path,
            "hint": f"path {path!r} does not exist under root {root!r}",
        }
    result = _run_roam(["file", path], root)
    # Belt-and-braces: if the CLI surfaced an empty list (no symbols
    # extracted, e.g. a YAML/JSON file with no roam-known symbols), still
    # return the structured no_data shape rather than letting an agent
    # parse a thin envelope as "the file has no content".
    if isinstance(result, dict):
        symbols = result.get("symbols") or result.get("data") or []
        if isinstance(symbols, list) and not symbols and "error" not in result:
            summary = result.get("summary")
            if not isinstance(summary, dict):
                summary = {}
            summary.setdefault("verdict", "no data")
            summary.setdefault("state", "no_data")
            summary.setdefault("partial_success", False)
            result["summary"] = summary
            result.setdefault("data", [])
    return result


# ===================================================================
# Tier 2 tools -- change-risk and deeper analysis
# ===================================================================


@_tool(
    name="roam_pr_risk",
    description="Risk score (0-100) for pending changes with per-file breakdown.",
    output_schema=_SCHEMA_PR_RISK,
)
def pr_risk(staged: bool = False, root: str = ".") -> dict:
    """Compute a risk score (0-100) for pending changes.

    Call this before committing or creating a PR. Produces LOW/MODERATE/
    HIGH/CRITICAL rating with per-file breakdown, risk factors, and
    suggested reviewers."""
    args = ["pr-risk"]
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_pr_analyze", description="Agent-aware PR risk verdict — INTENTIONAL / SAFE / REVIEW / BLOCK.")  # W459
def pr_analyze(
    diff_path: str = "",
    commit_range: str = "",
    staged: bool = False,
    input_path: str = "",
    intent: str = "",
    block_threshold: int = 85,
    with_reviewers: bool = False,
    audit_trail: bool = False,
    language: str = "",
    explain: bool = False,
    root: str = ".",
) -> dict:
    """Agent-aware PR risk verdict — INTENTIONAL / SAFE / REVIEW / BLOCK.

    WHEN TO USE: Call this on a PR diff (path, commit range, or staged) to
    aggregate pr-prep (diff + critique + pr-risk) with AI-likelihood
    heuristics, ``.roam/rules.yml`` enforcement, and a verdict mapping
    suitable for posting as a single GitHub PR comment. The CLI engine
    behind Roam Agent Review.

    Parameters
    ----------
    diff_path:
        Optional path to a unified-diff file. If omitted, ``commit_range`` /
        ``staged`` / unstaged ``git diff`` is used. Note: this is the
        PRIMARY input; ``input_path`` below is the sidecar rules pack.
    commit_range:
        Git range (e.g. ``"main..HEAD"``).
    staged:
        Analyse staged changes.
    input_path:
        Sidecar path to ``.roam/rules.yml`` (default: auto-detect). Legacy
        callers using ``rules_path=`` are translated transparently with a
        deprecation warning under ``summary.alias_warnings``.
    intent:
        PR title or commit message — checked for the ``[intentional]`` marker.
    block_threshold:
        Blast-radius score at or above which the verdict becomes BLOCK.
    with_reviewers:
        Suggest reviewers for the touched files (calls suggest-reviewers).
    audit_trail:
        Append an EU AI Act Article 12 record to ``.roam/audit-trail.jsonl``.
    language:
        Override auto-detected primary language for AI-likelihood weighting.
    explain:
        Include the verbose human-readable rationale block.

    Returns: verdict envelope with summary, rationale, ai-likelihood signals,
    rule violations, optional reviewer suggestions, optional audit-trail record.
    """
    args = ["pr-analyze"]
    if commit_range:
        args.append(commit_range)
    if diff_path:
        args.extend(["--input", diff_path])
    if staged:
        args.append("--staged")
    if input_path:
        args.extend(["--rules", input_path])
    if intent:
        args.extend(["--intent", intent])
    if block_threshold != 85:
        args.extend(["--block-threshold", str(block_threshold)])
    if with_reviewers:
        args.append("--with-reviewers")
    if audit_trail:
        args.append("--audit-trail")
    if language:
        args.extend(["--language", language])
    if explain:
        args.append("--explain")
    return _run_roam(args, root)


@_tool(
    name="roam_pr_comment_render", description="Render a markdown PR comment from a pr-analyze JSON envelope."
)  # W459
def pr_comment_render(input_path: str = "", style: str = "github", include_links: bool = True, root: str = ".") -> dict:
    """Render a markdown PR comment from a pr-analyze JSON envelope.

    WHEN TO USE: After ``roam_pr_analyze``, render the verdict as a sticky
    GitHub / GitLab PR comment. Used by the Roam Agent Review GitHub App
    worker; useful locally to dogfood the comment shape before the bot
    posts it.

    Parameters
    ----------
    input_path:
        Path to a saved ``roam pr-analyze --json`` envelope on disk.
        Legacy callers using ``envelope_path=`` are translated
        transparently with a deprecation warning under
        ``summary.alias_warnings``.
    style:
        ``github`` / ``gitlab`` / ``plain`` (default: ``github``).
    include_links:
        Append the small attribution + docs footer.

    Returns: ``{summary: {...}, markdown: "..."}`` — the rendered comment
    in the ``markdown`` field plus a small summary block.
    """
    args = ["pr-comment-render", "--input", input_path, "--style", style]
    if not include_links:
        args.append("--no-links")
    return _run_roam(args, root)


@_tool(
    name="roam_audit_trail_verify",
    description="Verify SHA-256 chain integrity of a roam audit trail.",
    output_schema=_SCHEMA_AUDIT_TRAIL_VERIFY,
)  # W459
def audit_trail_verify(input_path: str = "", root: str = ".") -> dict:
    """Verify SHA-256 chain integrity of a roam audit trail.

    WHEN TO USE: After ``roam_pr_analyze`` with ``audit_trail=True`` has
    written records, call this to confirm the EU AI Act Article 12 audit
    log hasn't been tampered with. Returns a verdict block with
    ``chain_valid`` boolean plus per-issue line numbers for any breaks.

    Parameters
    ----------
    input_path:
        Path to audit-trail JSONL (default: ``.roam/audit-trail.jsonl``).

    Returns: ``{summary: {chain_valid, total_records, issues_count, ...},
    issues: [...]}``.
    """
    args = ["audit-trail-verify"]
    if input_path:
        args.extend(["--input", input_path])
    return _run_roam(args, root)


@_tool(
    name="roam_audit_trail_export",
    description="Export the audit trail as markdown / json / csv for procurement review.",
)  # W459
def audit_trail_export(
    input_path: str = "",
    fmt: str = "md",
    since: str = "",
    until: str = "",
    verdict_filter: str = "",
    root: str = ".",
) -> dict:
    """Export the audit trail as markdown / json / csv for procurement review.

    WHEN TO USE: After ``roam_audit_trail_verify`` confirms integrity,
    export the records in a procurement-friendly format. Supports
    date-range and verdict filtering.

    Parameters
    ----------
    input_path:
        Audit-trail JSONL path (default: ``.roam/audit-trail.jsonl``).
    fmt:
        ``md`` / ``json`` / ``csv`` (default ``md``).
    since:
        ISO-8601 timestamp lower bound.
    until:
        ISO-8601 timestamp upper bound.
    verdict_filter:
        Comma-separated verdicts to keep (e.g. ``"REVIEW,BLOCK"``).

    Returns: ``{summary: {total_records, filtered_records, ...}, content: "..."}``.
    """
    args = ["audit-trail-export", "--format", fmt]
    if input_path:
        args.extend(["--input", input_path])
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if verdict_filter:
        args.extend(["--verdict", verdict_filter])
    return _run_roam(args, root)


@_tool(
    name="roam_metrics_push", description="Push metrics-only summary to Roam Cloud Lite. **Default is dry-run.**"
)  # W459
def metrics_push_tool(
    token: str = "",
    repo: str = "",
    endpoint: str = "",
    anonymize: bool = False,
    include_hotspots: bool = True,
    dry_run: bool = True,
    root: str = ".",
) -> dict:
    """Push metrics-only summary to Roam Cloud Lite. **Default is dry-run.**

    WHEN TO USE: After ``roam_audit``, push the numerical summary (no source
    code) to Roam Cloud Lite for trend storage. Defaults to ``dry_run=True``
    so the agent never sends anything outside the local machine without
    explicit opt-in. The CLI engine behind Roam Cloud Lite.

    Parameters
    ----------
    token:
        Auth token. Required when ``dry_run=False``.
    repo:
        Override repo identifier (default: derived from git origin).
    endpoint:
        Override the Cloud Lite endpoint URL.
    anonymize:
        Replace file paths with SHA-256 hash prefixes.
    include_hotspots:
        Include top 10 danger-zone hotspot rows in the payload.
    dry_run:
        Print the payload without POSTing. **Default True** for safety.

    Returns: ``{summary: {...}, payload: {...}}`` showing the exact JSON
    that would be transmitted.
    """
    args = ["metrics-push"]
    if dry_run:
        args.append("--dry-run")
    if token:
        args.extend(["--token", token])
    if repo:
        args.extend(["--repo", repo])
    if endpoint:
        args.extend(["--endpoint", endpoint])
    if anonymize:
        args.append("--anonymize")
    if not include_hotspots:
        args.append("--no-hotspots")
    return _run_roam(args, root)


@_tool(
    name="roam_audit_trail_conformance_check",
    description="Score the audit trail against an EU AI Act Article 12 checklist.",
    output_schema=_SCHEMA_AUDIT_TRAIL_CONFORMANCE,
)  # W459
def audit_trail_conformance_check(
    input_path: str = "",
    retention_days: int = 180,
    root: str = ".",
) -> dict:
    """Score the audit trail against an EU AI Act Article 12 checklist.

    WHEN TO USE: Quarterly compliance gate, or before a procurement review.
    Six checks: chain integrity, timestamp completeness, actor attribution,
    reproducibility metadata, verdict + rationale present, and retention
    (≥ ``retention_days`` days of history).

    Parameters
    ----------
    input_path:
        Audit-trail JSONL path (default: ``.roam/audit-trail.jsonl``).
    retention_days:
        Minimum retention requirement (Article 12 floor: 180 days).

    Returns: ``{summary: {score, checks_passed, checks_total, ...}, checks: [...]}``.
    NOT legal advice — triage signal for procurement readiness.
    """
    args = ["audit-trail-conformance-check", "--retention-days", str(retention_days)]
    if input_path:
        args.extend(["--input", input_path])
    return _run_roam(args, root)


@_tool(
    name="roam_dogfood", description="One-shot full-stack run: audit + pr-analyze + audit-trail + conformance."
)  # W459
def dogfood(
    audit: bool = True,
    pr_analyze_on: bool = True,
    audit_trail_on: bool = True,
    input_path: str = "",
    root: str = ".",
) -> dict:
    """One-shot full-stack run: audit + pr-analyze + audit-trail + conformance.

    WHEN TO USE: First-touch demo for a new repo, or as a quick local
    self-check. Bundles the entire hosted-product surface (Cloud metrics,
    Agent Review verdict, AI-governance audit-trail, conformance score)
    into one envelope so the agent / user sees everything in one call.

    Parameters
    ----------
    audit:
        Include the audit envelope (health + debt + dead + danger).
    pr_analyze_on:
        Run pr-analyze on uncommitted diff.
    audit_trail_on:
        Append an audit-trail record + run conformance check.
    input_path:
        Pass-through rules YAML to pr-analyze (default: auto-detect
        ``.roam/rules.yml``). Legacy callers using ``rules_file=`` are
        translated transparently with a deprecation warning under
        ``summary.alias_warnings``.

    Returns: ``{summary: {verdict, health_score, pr_verdict, conformance_score},
    sections: {audit, pr_analyze, conformance}}``.
    """
    args = ["dogfood"]
    if not audit:
        args.append("--no-audit")
    if not pr_analyze_on:
        args.append("--no-pr-analyze")
    if not audit_trail_on:
        args.append("--no-audit-trail")
    if input_path:
        args.extend(["--rules", input_path])
    return _run_roam(args, root)


@_tool(
    name="roam_rules_validate", description="Lint a `.roam/rules.yml` for shippability before customers see it."
)  # W459
def rules_validate(
    input_path: str = ".roam/rules.yml",
    against: str = "",
    strict: bool = False,
    root: str = ".",
) -> dict:
    """Lint a `.roam/rules.yml` for shippability before customers see it.

    WHEN TO USE: Before committing a new rule pack, or in CI on every push
    that touches rules.yml. Catches typos like ``severity: BLOK``, missing
    required fields, unknown pattern names, duplicate rule IDs, and
    unbalanced glob brackets — all silent failures in the pr-analyze
    consumer if not caught here.

    Parameters
    ----------
    input_path:
        Path to the rules YAML to validate (default: ``.roam/rules.yml``).
        Legacy callers using ``rules_path=`` are translated transparently
        with a deprecation warning under ``summary.alias_warnings``.
    against:
        Optional sample diff path; the rules will be dry-run against it
        and matching violations reported.
    strict:
        Treat warnings (missing severity, missing description, unknown
        keys) as failures.

    Returns: ``{summary: {verdict, errors_count, warnings_count, ...},
    errors: [...], warnings: [...], dry_run_violations: [...]}``.
    """
    args = ["rules-validate", input_path]
    if against:
        args.extend(["--against", against])
    if strict:
        args.append("--strict")
    return _run_roam(args, root)


@_tool(name="roam_suggest_reviewers", description="Suggest optimal code reviewers for changed files.")  # W459
def mcp_suggest_reviewers(top: int = 3, exclude: str = "", changed: bool = True, root: str = ".") -> dict:
    """Suggest optimal code reviewers for changed files.

    WHEN TO USE: Call this before creating a PR to find the best reviewers.
    Scores candidates using blame ownership, CODEOWNERS, recency, and
    expertise breadth across the changed files.

    Parameters
    ----------
    top:
        Number of reviewers to suggest (default 3).
    exclude:
        Comma-separated developer names to exclude (e.g. the PR author).
    changed:
        Use git diff to detect changed files (default True).

    Returns: ranked reviewers with per-signal scores, file coverage stats.
    """
    args = ["suggest-reviewers", "--top", str(top)]
    if changed:
        args.append("--changed")
    if exclude:
        for name in exclude.split(","):
            name = name.strip()
            if name:
                args.extend(["--exclude", name])
    return _run_roam(args, root)


@_tool(
    name="roam_verify", description="Check changed files for naming, import, error-handling, and duplicate issues."
)  # W459
def verify(threshold: int = 70, root: str = ".") -> dict:
    """Check changed files for naming, import, error-handling, and duplicate issues.

    WHEN TO USE: Call this before committing to check that changed files
    follow established codebase conventions. Scores 5 categories (naming,
    imports, error handling, duplicates, syntax) and returns PASS/WARN/FAIL.

    Parameters
    ----------
    threshold:
        Minimum passing score (default 70).
    root:
        Working directory (project root).

    Returns: composite score, per-category scores and violations, verdict.
    """
    args = ["verify", "--changed", "--threshold", str(threshold)]
    return _run_roam(args, root)


@_tool(
    name="roam_breaking_changes",
    description="Detect breaking API changes between git refs: removed exports, changed signatures.",
)
def breaking_changes(target: str = "HEAD~1", root: str = ".") -> dict:
    """Detect breaking API changes between git refs.

    WHEN TO USE: Call this before releasing or merging to check if any
    public APIs were broken. Finds removed exports, changed signatures,
    and reordered parameters.

    Parameters
    ----------
    target:
        Git ref to compare against (default: HEAD~1).

    Returns: each breaking change with old/new signatures, the affected
    symbol location, and the change type (removed/signature_changed/
    params_reordered).
    """
    return _run_roam(["breaking", target], root)


@_tool(name="roam_api_changes", description="Detect breaking and non-breaking API changes vs a git ref.")  # W459
def api_changes(base: str = "HEAD~1", severity: str = "warning", root: str = ".") -> dict:
    """Detect breaking and non-breaking API changes vs a git ref.

    WHEN TO USE: Before merging or releasing, check for removed symbols,
    changed signatures, visibility reductions, type changes, and renames.
    More detailed than breaking_changes with severity filtering.

    Parameters
    ----------
    base:
        Git ref to compare against (default: HEAD~1).
    severity:
        Minimum severity: breaking, warning, or info (default: warning).

    Returns: changes list with category, severity, symbol info, and
    old/new signatures.
    """
    args = ["api-changes", "--base", base, "--severity", severity]
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# Fix F (Pattern 6) — MCP wrappers for the four large-response CLI commands
# that previously had no MCP surface. Without these, agents can't invoke
# ``api`` / ``conventions`` / ``verify-imports`` / ``changelog`` through MCP
# at all (only via shell-out). Wrapping them via ``_run_roam`` routes their
# response through the @_tool decorator's ``_wrap_with_handle_off``, which
# auto-stores any >50KB envelope under ``.roam/responses/<sha>.json`` and
# replaces the wire response with a tiny handle envelope. Closes the
# capsule/partition/conventions/verify-imports/api/changelog leg of the
# response-volume audit findings.
# ---------------------------------------------------------------------------


@_tool(
    name="roam_api",
    description="List the public API surface — exported public symbols with signatures and docs.",
)
def api(limit: int = 0, scope: str = "", root: str = ".") -> dict:
    """List the public API surface (exported public symbols).

    WHEN TO USE: Call this to enumerate the symbols a downstream
    consumer can rely on — what's exported, what their signatures are,
    and what their docstrings say. Pair with ``roam_api_changes`` to
    audit breaking changes vs a git ref.

    Parameters
    ----------
    limit:
        Cap output to the first ``limit`` symbols (0 = no cap).
    scope:
        Restrict to symbols whose file path begins with this prefix
        (e.g. ``src/auth/``).

    Returns: list of public symbols with name, kind, qualified_name,
    signature, docstring head, file path, and line.
    Large payloads auto-handle-off to ``.roam/responses/``.
    """
    args = ["api"]
    if limit and limit > 0:
        args.extend(["--limit", str(int(limit))])
    if scope:
        args.extend(["--scope", scope])
    return _run_roam(args, root)


@_tool(
    name="roam_conventions",
    description="Auto-detect codebase naming, file, import, and export conventions with outliers.",
)
def roam_conventions(max_outliers: int = 10, root: str = ".") -> dict:
    """Auto-detect codebase conventions.

    WHEN TO USE: Call this on a new-to-you codebase to learn its
    actual naming/import/export style before generating code, or as
    a one-shot audit when you need a structured catalog of the
    codebase's conventions (instead of the lighter rollup inside
    ``roam_understand`` / ``roam_describe``).

    Note: ``roam_understand`` uses the same canonical detector, so
    the verdicts on naming/style agree. Use ``roam_understand`` when
    you also want hotspots and tech stack; use this when you want
    the full convention catalog with per-category outliers.

    Parameters
    ----------
    max_outliers:
        Max outliers shown per category (default 10).

    Returns: per-category conventions with detected style, dominant
    pattern, confidence, and outliers. Large payloads auto-handle-off.
    """
    args = ["conventions"]
    if max_outliers != 10:
        args.extend(["-n", str(int(max_outliers))])
    return _run_roam(args, root)


@_tool(
    name="roam_verify_imports",
    description="Hallucination firewall: validate import statements resolve to indexed symbols.",
)
def verify_imports(file: str = "", root: str = ".") -> dict:
    """Validate import/require statements against the indexed symbol table.

    WHEN TO USE: Call this after an AI generates code with imports, OR
    before merging a PR, to catch hallucinated / typo'd imports that
    don't actually resolve. Flags unresolvable imports and suggests
    corrections via fuzzy matching against the real symbol table.

    Parameters
    ----------
    file:
        Optional file path to limit verification to a single file.
        Fix D: legacy alias ``file_path`` accepted.

    Returns: per-file import lists tagged resolved/unresolved with
    fuzzy correction suggestions. Large payloads auto-handle-off.
    """
    args = ["verify-imports"]
    if file:
        args.extend(["--path", file])
    return _run_roam(args, root)


@_tool(
    name="roam_changelog",
    description="List commits since last tag, optionally formatted as a markdown CHANGELOG draft.",
)
def changelog(since: str = "", suggest: bool = False, root: str = ".") -> dict:
    """List commits since the last tag, optionally as a markdown draft.

    WHEN TO USE: Call this when cutting a release to enumerate
    commits since the prior tag, or with ``suggest=True`` to also
    bucket them by Conventional Commit type and emit a ready-to-paste
    ``## [Unreleased]`` section for CHANGELOG.md.

    Parameters
    ----------
    since:
        Git rev to start from (default: last tag, or HEAD~30 if no tag).
    suggest:
        If True, emit a draft markdown CHANGELOG section grouped by
        Conventional Commit buckets.

    Returns: commit list (or grouped buckets + markdown draft if
    suggest=True). Large payloads auto-handle-off.
    """
    args = ["changelog"]
    if since:
        args.extend(["--since", since])
    if suggest:
        args.append("--suggest")
    return _run_roam(args, root)


@_tool(
    name="roam_affected_tests",
    description=(
        "List the tests you actually need to run after editing a symbol or file. "
        "Use when user asks 'which tests do I run?', 'what tests cover X?', or "
        "after Edit/Write. Walks reverse-dependencies with hop distance — closer "
        "hops run first. For a full pre-commit check (blast radius + fitness + "
        "tests), use roam_prepare_change."
    ),
)
def affected_tests(symbol: str = "", staged: bool = False, root: str = ".") -> dict:
    """Find test files that exercise changed code.

    Call this to know which tests to run after making changes. Walks
    reverse dependency edges from changed code to find test files. For
    a full pre-change check, prefer preflight (includes affected tests
    plus blast radius and fitness).

    Parameters
    ----------
    symbol:
        Symbol name or file path to find tests for. W430/Fix-D
        canonical; legacy ``target=`` callers are accepted via
        ``_PARAM_ALIASES`` with a deprecation warning.
    staged:
        If True, analyze staged changes only.
    """
    args = ["affected-tests"]
    if symbol:
        args.append(symbol)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_test_gaps", description="Find changed symbols missing test coverage, ranked by severity.")  # W459
def test_gaps(changed: bool = True, severity: str = "medium", files: str = "", root: str = ".") -> dict:
    """Find changed symbols missing test coverage, ranked by severity.

    WHEN TO USE: Call this after making changes to see which new or
    modified symbols have no tests. Returns gaps classified as HIGH
    (public + high PageRank), MEDIUM (public), or LOW (private), plus
    stale tests that need updating.

    Parameters
    ----------
    changed:
        If True, analyze files from git diff.
    severity:
        Minimum severity: high, medium, or low (default: medium).
    files:
        Space-separated file paths to analyze (alternative to changed).

    Returns: gap lists by severity, stale tests, recommendations.
    """
    args = ["test-gaps"]
    if changed:
        args.append("--changed")
    if severity and severity != "medium":
        args.extend(["--severity", severity])
    if files:
        args.extend(files.split())
    return _run_roam(args, root)


@_tool(
    name="roam_algo",
    description="Detect suboptimal algorithms with better alternatives and complexity analysis.",
)
def algo(task: str = "", confidence: str = "", root: str = ".") -> dict:
    """Detect suboptimal algorithms and suggest better approaches.

    WHEN TO USE: Call this to find code that uses naive algorithms when
    better alternatives exist (e.g., manual sort instead of built-in,
    linear scan instead of binary search, nested-loop lookup instead of
    hash join). Returns specific suggestions with complexity analysis.

    Parameters
    ----------
    task:
        Filter by task ID (e.g., "sorting", "membership", "nested-lookup").
        Empty means all tasks.
    confidence:
        Filter by confidence level: "high", "medium", or "low".

    Returns: findings grouped by algorithm category, each with current
    vs. better approach, complexity comparison, and improvement tips.
    """
    args = ["algo"]
    if task:
        args.extend(["--task", task])
    if confidence:
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@_tool(
    name="roam_agent_opt",
    description="Detect weak agent-contract shape in roam's tool descriptions and envelopes and recommend the stronger shape.",
)
def agent_opt(
    only: str = "", scope: str = "full", confidence: str = "", profile: str = "balanced", root: str = "."
) -> dict:
    """Optimize roam's own agent-facing contract surface.

    WHEN TO USE: Call this to audit roam's MCP tool descriptions and
    `roam --json` envelopes for agi-in-md LAW violations — declarative
    (non-imperative) tool descriptions (LAW 2/11), summary verdicts that don't
    work standalone (LAW 6), and findings with missing or non-runnable
    next_commands (CONSTRAINT 12). Returns one finding per violation with the
    rank-1 compliant shape to adopt. The substrate that protects the envelope
    contract as new commands land.

    Parameters
    ----------
    only:
        Restrict to a task id: "tool-description-declarative", "weak-verdict",
        or "missing-next-command". Empty means all tasks.
    scope:
        Tool-description scope: "core" (core preset) or "full" (default).
    confidence:
        Minimum confidence floor: "high", "medium", or "low".
    profile:
        "balanced" (default), "strict" (drops heuristic-tier findings), or
        "aggressive".

    Returns: findings grouped by task, each with the detected weak shape vs the
    recommended LAW-compliant shape.
    """
    args = ["agent-opt"]
    if only:
        args.extend(["--only", only])
    if scope:
        args.extend(["--scope", scope])
    if confidence:
        args.extend(["--confidence", confidence])
    if profile:
        args.extend(["--profile", profile])
    return _run_roam(args, root)


@_tool(
    name="roam_observability_opt",
    description="Detect code that leaves systems hard to debug (raw debug prints, ...) and recommend the structured-logging shape.",
)
def observability_opt(
    only: str = "",
    language: str = "",
    confidence: str = "",
    profile: str = "balanced",
    max_files: int = 0,
    root: str = ".",
) -> dict:
    """Optimize a repo's diagnosability surface.

    WHEN TO USE: Call this to find code that leaves a system hard to debug —
    raw debug prints left in non-test source (print / console.log / var_dump /
    dbg!), with string-only logs and traces-without-status to follow. Returns
    one finding per violation (path:line) with the rank-1 structured-logging
    shape to adopt.

    Parameters
    ----------
    only:
        Restrict to a task id (e.g. "print-debug-leftover"). Empty means all.
    language:
        Restrict to one language (e.g. "python"). Empty means all supported.
    confidence:
        Minimum confidence floor: "high", "medium", or "low".
    profile:
        "balanced" (default), "strict" (drops heuristic-tier findings), or
        "aggressive".
    max_files:
        Cap the number of source files harvested (0 = no cap).

    Returns: findings grouped by task, each with the detected weak shape vs the
    recommended structured-logging shape.
    """
    args = ["observability-opt"]
    if only:
        args.extend(["--only", only])
    if language:
        args.extend(["--language", language])
    if confidence:
        args.extend(["--confidence", confidence])
    if profile:
        args.extend(["--profile", profile])
    if max_files:
        args.extend(["--max-files", str(max_files)])
    return _run_roam(args, root)


@_tool(
    name="roam_commands",
    description="List the repo's own runnable build/test/lint commands, classified by kind/scope/cost with evidence.",
)
def commands_tool(kind: str = "", scope: str = "", safe_only: bool = False, root: str = ".") -> dict:
    """List the repo's runnable command graph — the build/test/lint commands it exposes.

    WHEN TO USE: Call this BEFORE guessing how to build/test/lint a repo. It
    returns the ACTUAL commands (`pnpm test` vs `npm run test` vs `pytest`), each
    with kind / scope / cost / confidence and the EVIDENCE that proves it
    (`package.json:scripts.test`, `vitest.config.ts`, ...). Powers verification
    contracts and the agent-change proof bundle — agents stop guessing.

    Parameters
    ----------
    kind:
        Filter to one kind: test / typecheck / lint / build / run / other.
    scope:
        Filter to repo / package / file.
    safe_only:
        Only commands marked safe to auto-run.

    Returns: classified, evidence-backed runnable commands for the repo.
    """
    args = ["commands"]
    if kind:
        args.extend(["--kind", kind])
    if scope:
        args.extend(["--scope", scope])
    if safe_only:
        args.append("--safe-only")
    return _run_roam(args, root)


@_tool(
    name="roam_dark_matter",
    description="File pairs that co-change without structural links (hidden coupling).",
)
def dark_matter(min_npmi: float = 0.3, min_cochanges: int = 3, root: str = ".") -> dict:
    """Detect dark matter: file pairs that co-change but have no structural link.

    WHEN TO USE: Call this when you suspect hidden coupling between files
    that don't import each other but always change together. Returns
    dark-matter pairs with hypothesized reasons (shared DB, event bus,
    config, copy-paste). Complements `coupling` which shows all co-change
    pairs -- this filters to only structurally unlinked ones.

    Parameters
    ----------
    min_npmi:
        Minimum NPMI threshold (default 0.3). Higher = stronger coupling.
    min_cochanges:
        Minimum co-change count (default 3).

    Returns: dark-matter pairs with NPMI, lift, strength, co-change count,
    and hypothesis (category + detail + confidence) for each pair.
    """
    args = [
        "dark-matter",
        "--explain",
        "--min-npmi",
        str(min_npmi),
        "--min-cochanges",
        str(min_cochanges),
    ]
    return _run_roam(args, root)


@_tool(
    name="roam_dead_code",
    description=(
        "Use for: 'what can I safely delete?' / 'find dead code' / "
        "'list unused exports'. Pick over manual grep sweeps — filters "
        "out entry points and framework lifecycle hooks, ranks candidates "
        "by deletion safety. Pair with roam_safe_delete for per-symbol "
        "deletion verdicts."
    ),
)
def dead_code(root: str = ".") -> dict:
    """List unreferenced exported symbols (dead code candidates).

    Call this to find code that can be safely removed. Finds symbols
    with zero incoming edges, filtering out known entry points and
    framework lifecycle hooks. Includes safety verdict per symbol."""
    return _run_roam(["dead"], root)


@_tool(name="roam_duplicates", description="Detect semantically duplicate functions via structural similarity.")  # W459
def duplicates_tool(
    threshold: float = 0.75,
    min_lines: int = 5,
    scope: str = "",
    sample: int = 0,
    max_pairs: int = 1000,
    root: str = ".",
) -> dict:
    """Detect semantically duplicate functions via structural similarity.

    WHEN TO USE: Call this to find functions with similar structure that
    could be consolidated. Unlike textual clone detection, this finds
    functions with the same control flow but different implementations.

    Parameters
    ----------
    threshold:
        Similarity threshold 0.0-1.0 (default 0.75).
    min_lines:
        Minimum function size to consider (default 5).
    scope:
        Limit analysis to files under this path prefix.
    sample:
        Deterministically sample at most N candidates (0=disabled, use all).
    max_pairs:
        Cap on number of duplicate-pair clusters reported (default 1000,
        0=unlimited).

    Returns: duplicate clusters with similarity scores, shared patterns,
    and refactoring suggestions.
    """
    args = ["duplicates", "--threshold", str(threshold), "--min-lines", str(min_lines)]
    if scope:
        args.extend(["--scope", scope])
    if sample:
        args.extend(["--sample", str(sample)])
    # max_pairs default in CLI is 1000; only pass when caller explicitly
    # overrides (preserves existing behavior + allows unlimited via 0).
    if max_pairs != 1000:
        args.extend(["--max-pairs", str(max_pairs)])
    return _run_roam(args, root)


@_tool(name="roam_clones", description="Detect near-duplicate code via AST structural hashing (Type-2 clones).")  # W459
def clones_tool(threshold: float = 0.70, min_lines: int = 5, scope: str = "", top: int = 10, root: str = ".") -> dict:
    """Detect near-duplicate code via AST structural hashing (Type-2 clones).

    WHEN TO USE: Call this to find functions with identical control flow
    structure but different identifiers/literals. More precise than
    ``roam_duplicates`` (which uses metric-based similarity). Best for
    detecting copy-pasted code across files.

    Parameters
    ----------
    threshold:
        Minimum Jaccard similarity 0.0-1.0 (default 0.70).
    min_lines:
        Minimum function size to consider (default 5).
    scope:
        Limit analysis to files under this path prefix.
    top:
        Show only top N clusters (default 10, 0=all).

    Returns: clone clusters with AST similarity scores, member functions,
    patterns, and refactoring suggestions.
    """
    args = ["clones", "--threshold", str(threshold), "--min-lines", str(min_lines)]
    if scope:
        args.extend(["--scope", scope])
    if top:
        args.extend(["--top", str(top)])
    return _run_roam(args, root)


@_tool(
    name="roam_vibe_check",
    description="AI rot score (0-100): 8-pattern taxonomy of AI code anti-patterns.",
)
def vibe_check_tool(threshold: int = 0, root: str = ".") -> dict:
    """Detect AI code anti-patterns and compute composite AI rot score.

    WHEN TO USE: Call this to audit a codebase for AI-generated code
    smells: dead exports, empty handlers, stubs, hallucinated imports,
    copy-paste functions, comment anomalies, error inconsistency, churn.

    Parameters
    ----------
    threshold:
        Fail (exit 5) if score exceeds threshold (0=no gate).

    Returns: 0-100 score with per-pattern breakdown and worst files.
    """
    args = ["vibe-check"]
    if threshold > 0:
        args.extend(["--threshold", str(threshold)])
    return _run_roam(args, root)


# W415: roam_llm_smells -- first multi-provider LLM-API linter.
@_tool(
    name="roam_llm_smells",
    description=(
        "Run LLM-API integration linter over indexed files: detects unpinned "
        "model versions, missing max_tokens, prompt injection via user-input "
        "concatenation, unvalidated json.loads on LLM output, and missing "
        "temperature. Different from ``roam_vibe_check`` (AI-generated code "
        "shape) and ``roam_smells`` (structural anti-patterns) -- this is "
        "the production gate for human-authored LLM-using code."
    ),
)
def roam_llm_smells(
    min_severity: str = "info",
    root: str = ".",
) -> dict:
    """Detect LLM-API anti-patterns: unpinned models, missing token bounds, prompt injection.

    WHEN TO USE: Run this before shipping any code that calls an LLM
    provider SDK (openai, anthropic, langchain, litellm, …) to catch
    cost / reproducibility / prompt-injection regressions early.

    Parameters
    ----------
    min_severity:
        Minimum severity to surface: ``info`` / ``warning`` / ``critical``.
        Defaults to ``info`` (everything).
    root:
        Repo root (default current directory).

    Returns: per-pattern counts, findings list, scanned LLM files, and a
    ``next_steps`` pointer to ``roam findings list --detector llm-smells``.
    """
    args: list[str] = ["llm-smells"]
    if min_severity and min_severity.lower() != "info":
        args.extend(["--min-severity", min_severity])
    return _run_roam(args, root)


# W421: roam_boundary -- public-by-accident exports + changed-range layer
# violations. Closed-enum kinds: public_by_accident (warning),
# wrong_direction_import (high). Pure read-only detector; --persist mirrors
# into the findings registry but is a per-row UPSERT (idempotent across reruns).
@_tool(
    name="roam_boundary",
    description=(
        "Surface public-by-accident exports + changed-range layer violations. "
        "Two closed-enum kinds: public_by_accident (warning, _-prefixed name in "
        "__all__) and wrong_direction_import (high, lower-layer module imports "
        "from higher-layer caller)."
    ),
    read_only=True,
    destructive=False,
    idempotent=True,
)
def boundary_tool(
    changed_range: str = "working",
    base_ref: str = "main",
    persist: bool = False,
    root: str = ".",
) -> dict:
    """Surface public-by-accident exports + changed-range layer violations.

    WHEN TO USE: Run before merging a PR to catch (a) symbols whose
    underscore-prefix says "private" but whose ``__all__`` membership
    says "public", and (b) imports that reach back up the dependency
    layer stack (foundation modules importing from caller-shaped
    modules). The wrong-direction kind is changed-range scoped — layer
    numbering is derived (no config-pinned DAG), so partial_success is
    surfaced on clean runs.

    Parameters
    ----------
    changed_range:
        Diff source for wrong_direction_import scope.
        ``pr`` / ``working`` (default) / ``staged`` / ``head`` / ``all``.
    base_ref:
        Base branch for ``--changed-range pr`` (default ``main``).
    persist:
        Mirror each finding into the central findings registry
        (``roam findings list --detector boundary``).

    Returns: per-kind counts, findings list with file/line + evidence,
    LAW-4 anchored facts.
    """
    args: list[str] = ["boundary", "--changed-range", changed_range]
    if base_ref and base_ref != "main":
        args.extend(["--base-ref", base_ref])
    if persist:
        args.append("--persist")
    return _run_roam(args, root)


# W421: roam_test_hermeticity -- AI-generated test risk detector. Six
# closed-enum kinds (network/time/random/filesystem/env/subprocess) via
# AST-driven call-classifier; module-level suppression for monkeypatch /
# freezegun / responses / random.seed. --persist is per-row UPSERT.
@_tool(
    name="roam_test_hermeticity",
    description=(
        "Detect non-hermetic test patterns that cause CI flakiness. Six "
        "closed-enum kinds: network, time, random, filesystem, env, "
        "subprocess. AST-driven (not regex) with module-level suppression "
        "for monkeypatch / freezegun / responses / random.seed."
    ),
    read_only=True,
    destructive=False,
    idempotent=True,
)
def test_hermeticity_tool(
    persist: bool = False,
    ci_mode: bool = False,
    root: str = ".",
) -> dict:
    """Scan Python test files for non-hermetic patterns (AI-test flakiness risk).

    WHEN TO USE: Run after an agent generates or modifies tests, before
    merging. Catches the common AI failure mode where generated tests
    reach for the real network, the wall clock, ``random.*`` without a
    seed, the filesystem, ``os.environ``, or ``subprocess.run`` without
    mocking. Each call site flags as one of six closed-enum kinds. False
    positives suppressed by ``monkeypatch.setenv``, ``random.seed``,
    ``freezegun`` / ``time_machine`` / ``responses`` / ``httpx_mock``.

    Parameters
    ----------
    persist:
        Mirror each non-hermetic finding into the central findings
        registry (``roam findings list --detector test-hermeticity``).
    ci_mode:
        Exit 5 when any non-hermetic finding is detected (CI gate).

    Returns: hermeticity_rate (% hermetic), per-kind counts, findings
    list with file/line + evidence call-chain.
    """
    args: list[str] = ["test-hermeticity"]
    if persist:
        args.append("--persist")
    if ci_mode:
        args.append("--ci")
    return _run_roam(args, root)


# W421: roam_compatibility -- outbound surface regression detector vs a
# baseline snapshot. Read-only diff mode only; --write-baseline writes to
# disk (one-time human-driven setup) and stays CLI-only by design.
@_tool(
    name="roam_compatibility",
    description=(
        "Detect outbound surface regressions vs a baseline snapshot. "
        "Closed-enum verdicts: no regressions / surface additions / "
        "surface drift / breaking changes. Compares commands, flags, "
        "envelope summary fields, MCP tools, and preset counts. Capture "
        "the baseline via CLI: roam compatibility --write-baseline PATH."
    ),
    read_only=True,
    destructive=False,
    idempotent=True,
)
def compatibility_tool(
    baseline: str = "",
    current: str = "",
    ci_mode: bool = False,
    root: str = ".",
) -> dict:
    """Diff the live build's outbound surface against a baseline snapshot.

    WHEN TO USE: Before tagging a release or merging a refactor that may
    rename/remove commands, flags, envelope fields, or MCP tools. Catches
    the same bug class CLAUDE.md Constraint 8 protects against (closed
    enumeration vs free string composition) but for OUTBOUND contracts
    consumers depend on.

    The ``--write-baseline`` path is deliberately CLI-only — it's a
    one-time human-driven setup action that writes to disk. Run
    ``roam compatibility --write-baseline dev/compatibility-baseline.json``
    from the shell to capture a fresh baseline; then this MCP tool gates
    every subsequent diff.

    Parameters
    ----------
    baseline:
        Path to baseline snapshot JSON (default
        ``dev/compatibility-baseline.json``).
    current:
        Path to a captured current snapshot JSON. Default: capture the
        live build.
    ci_mode:
        Exit 5 (EXIT_GATE_FAILURE) on any breaking entry.

    Returns: closed-enum verdict, per-category counts (removed_commands,
    removed_flags, removed_envelope_fields, removed_mcp_tools,
    changed_presets), full per-category lists.
    """
    args: list[str] = ["compatibility"]
    if baseline:
        args.extend(["--baseline", baseline])
    if current:
        args.extend(["--current", current])
    if ci_mode:
        args.append("--ci")
    return _run_roam(args, root)


@_tool(
    name="roam_supply_chain",
    description="Dependency risk dashboard: pin coverage, risk scoring, supply-chain health.",
)
def supply_chain_tool(top: int = 5, root: str = ".") -> dict:
    """Analyze project dependency files for supply-chain risk.

    WHEN TO USE: Call this to audit dependency pin coverage and identify
    unpinned or loosely-versioned dependencies across requirements.txt,
    package.json, go.mod, Cargo.toml, Gemfile, pom.xml, and pyproject.toml.

    Parameters
    ----------
    top:
        Number of riskiest dependencies to highlight (default 5).

    Returns: risk score (0-100), pin coverage %, unpinned/range/exact counts,
    ecosystem breakdown, and top riskiest dependencies.
    """
    args = ["supply-chain"]
    if top != 5:
        args.extend(["--top", str(top)])
    return _run_roam(args, root)


@_tool(
    name="roam_dashboard",
    description="Unified single-screen codebase status: health, hotspots, bus factor, dead code, AI rot.",
)
def dashboard_tool(root: str = ".") -> dict:
    """One-call codebase status combining health, hotspots, risks, and AI rot.

    WHEN TO USE: Call this for a quick unified overview instead of running
    health, hotspot, bus-factor, dead, and vibe-check separately.
    Returns health score, top hotspots, risk areas, and approximate AI rot.
    """
    return _run_roam(["dashboard"], root)


@_tool(
    name="roam_ai_readiness",
    description="AI readiness score (0-100): how effectively AI agents can work on this codebase.",
)
def ai_readiness_tool(threshold: int = 0, root: str = ".") -> dict:
    """Estimate AI agent effectiveness on this codebase across 7 dimensions.

    WHEN TO USE: Call this to assess whether AI agents will work well
    on this codebase. Scores naming, coupling, dead code, tests, docs,
    navigability, and architecture clarity.

    Parameters
    ----------
    threshold:
        Fail (exit 5) if score is below threshold (0=no gate).

    Returns: 0-100 composite score with per-dimension breakdown and recommendations.
    """
    args = ["ai-readiness"]
    if threshold > 0:
        args.extend(["--threshold", str(threshold)])
    return _run_roam(args, root)


@_tool(
    name="roam_check_rules",
    description="Run 10 built-in structural rules: cycles, fan-out, complexity, tests, god classes, layer violations.",
)
def check_rules(
    rule: str = "",
    severity: str = "",
    config: str = "",
    root: str = ".",
) -> dict:
    """Run built-in structural governance rules against the codebase.

    WHEN TO USE: Call this for governance checks including circular imports,
    excessive coupling, missing tests, oversized classes, and layer violations.
    Use rule to run a single check. Use severity to filter results.

    Built-in rule IDs:
      no-circular-imports, max-fan-out, max-fan-in, max-file-complexity,
      max-file-length, test-file-exists, no-god-classes,
      no-deep-inheritance, layer-violation, no-orphan-symbols

    Parameters
    ----------
    rule:
        Run only this rule ID.
    severity:
        Filter to this severity (error/warning/info).
    config:
        Path to .roam-rules.yml override file.

    Returns: per-rule pass/fail with violation details. FAIL = exit 1.
    """
    args = ["check-rules"]
    if rule:
        args.extend(["--rule", rule])
    if severity:
        args.extend(["--severity", severity])
    if config:
        args.extend(["--config", config])
    return _run_roam(args, root)


@_tool(
    name="roam_complexity_report",
    description="Functions ranked by cognitive complexity above threshold.",
)
def complexity_report(threshold: int = 15, root: str = ".") -> dict:
    """Rank functions by cognitive complexity.

    Call this to find the most complex functions that should be
    refactored. For checking a single symbol, prefer context or
    preflight which include complexity data."""
    return _run_roam(["complexity", "--threshold", str(threshold)], root)


@_tool(
    name="roam_py_types",
    description="Python type-annotation health: % public fns fully typed, Any usage, legacy typing.",
)
def py_types_report(detail: bool = False, include_tests: bool = False, root: str = ".") -> dict:
    """Type-annotation coverage for the indexed Python project.

    Reports % of public functions/methods with full annotations,
    ``Any`` usage, legacy ``typing.Optional/Dict/List`` (PEP 585/604
    modernisation candidates), and per-file worst offenders. Use this
    to direct typing-fix sprints. v12.5+.

    Parameters
    ----------
    detail:
        Include per-file breakdown of worst offenders.
    include_tests:
        Include test files in the coverage stats. Default False — test
        functions rarely have annotations and would drown the production
        signal.
    """
    args = ["py-types"]
    if detail:
        args.append("--detail")
    if include_tests:
        args.append("--include-tests")
    return _run_roam(args, root)


@_tool(
    name="roam_py_modern",
    description="Python modernisation signal: walrus, match, PEP 604/585, f-strings vs legacy.",
)
def py_modern_report(detail: bool = False, root: str = ".") -> dict:
    """Modern-Python adoption signal — walrus operator, match
    statements, PEP 604 (``X | None``), PEP 585 (``dict[…]``), PEP
    695 type aliases, f-strings vs ``.format()``.

    Use this to gauge how modernised a codebase is and where to focus
    migration sprints. Counterpart to ``roam_py_types``. v12.6+.

    Parameters
    ----------
    detail:
        Include per-file breakdown of feature usage.
    """
    args = ["py-modern"]
    if detail:
        args.append("--detail")
    return _run_roam(args, root)


@_tool(
    name="roam_pytest_fixtures",
    description="pytest fixture chain: top fixtures by dependent count, or per-symbol dependency walk.",
)
def pytest_fixtures_report(symbol: str = "", max_depth: int = 6, root: str = ".") -> dict:
    """Show the implicit pytest fixture dependency graph.

    pytest fixtures depend on each other through their parameter names.
    The relationship is invisible to call-graph analysis, so changing
    one fixture can break tests several files away with no edge to
    follow. Indexing materialises this as ``pytest_fixture_dep`` edges;
    this tool exposes them. v12.9+.

    Parameters
    ----------
    symbol:
        Fixture name, qualified name, or ``test_*`` function. When
        empty, returns a project-wide summary with the top fixtures by
        dependent count (a blast-radius proxy).
    max_depth:
        Cap the dependency walk at this depth. Default 6 — enough for
        any sensible fixture chain.
    """
    args = ["pytest-fixtures"]
    if symbol:
        args.append(symbol)
    args += ["--max-depth", str(max_depth)]
    return _run_roam(args, root)


@_tool(
    name="roam_hover",
    description="One-line architectural summary for a symbol — kind, location, blast-radius bucket, top caller, top callee.",
)
def hover_summary(symbol: str, root: str = ".") -> dict:
    """Compact architectural gloss for a symbol, bounded at ~200
    tokens. Pairs with IDE hover-on-symbol plugins where ``roam
    context`` is too verbose. v12.8+.

    Parameters
    ----------
    symbol:
        Symbol name, qualified name, or ``file:symbol`` hint. Required.
    """
    return _run_roam(["hover", symbol], root)


@_tool(
    name="roam_at",
    description="Show the code AT a file:line with its enclosing symbol + callers. Targeted alternative to Read-ing the whole file. location is 'file:line'.",
)
def at_location(location: str, context: int = 5, callers: bool = False, root: str = ".") -> dict:
    """Return the source slice around ``location`` (``file:line``) plus the
    enclosing symbol (which function/class contains the line) and, when
    ``callers`` is set, who calls it. Inverse of ``roam_search_symbol``
    (symbol→location); use when you already have a file:line and want the
    code + structural context without reading the whole file.
    """
    args = ["at", location, "--context", str(context)]
    if callers:
        args.append("--callers")
    return _run_roam(args, root)


@_tool(
    name="roam_repo_map",
    description="Compact project skeleton with key symbols per file, by PageRank.",
)
async def repo_map(
    budget: int = 0,
    summarize: bool | None = None,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Show a compact project skeleton with key symbols.

    WHEN TO USE: Call this for a spatial overview of the repository
    structure -- files grouped by directory, annotated with their most
    important symbols (by PageRank). Lighter than `understand`, useful
    when you just need the file layout.

    Parameters
    ----------
    budget:
        Approximate token budget for the output. 0 means no limit.
    summarize:
        If True and the client supports MCP sampling, returns a
        narrative tour of the repo instead of the full skeleton.

    Returns: files grouped by directory with top symbols per file,
    annotated with kind and importance.
    """
    args = ["map"]
    if budget > 0:
        args.extend(["--budget", str(budget)])
    result = _run_roam(args, root)
    task = _mcp_session.session_hint(ctx) if _mcp_session is not None else ""
    return await _maybe_summarize(result, ctx=ctx, summarize=summarize, task=task)


@_tool(
    name="roam_tour",
    description="Codebase onboarding guide: reading order, entry points, architecture roles.",
)
def tour(root: str = ".") -> dict:
    """Generate a codebase onboarding guide.

    WHEN TO USE: Call this when onboarding to a new codebase or helping
    a developer understand the project structure. Produces a structured
    architecture tour: top symbols by importance, reading order based on
    topological layers, entry points, and language breakdown. More
    detailed than `understand` for onboarding; use `understand` for a
    quick briefing.

    Returns: language breakdown, codebase statistics (files, symbols,
    edges, test ratio, avg health), top-10 symbols with roles
    (Hub/Core utility/Orchestrator/Leaf), suggested file reading order
    by topological layer, and entry points for exploration.
    """
    return _run_roam(["tour"], root)


@_tool(
    name="roam_agent_export",
    description="Generate AI agent context file (CLAUDE.md/AGENTS.md/.cursorrules) from index.",
)
def agent_export(format: str = "claude", root: str = ".") -> dict:
    """Generate an AI agent context file from the roam index.

    WHEN TO USE: Call this to generate a CLAUDE.md, AGENTS.md, or
    .cursorrules file that gives AI coding agents instant codebase
    comprehension.  The output covers architecture, key files, entry
    points, hotspots, test patterns, and health -- all derived from
    the pre-built index.

    Args:
        format: Output format variant -- "claude" (CLAUDE.md),
                "agents" (AGENTS.md), or "cursor" (.cursorrules).
        root: Project root directory.

    Returns: JSON envelope with project overview, architecture,
    key files, entry points, hotspots, test info, and health summary.
    """
    return _run_roam(["agent-export", "--format", format], root)


@_tool(
    name="roam_visualize",
    description="Generate Mermaid/DOT architecture diagram with smart filtering.",
)
def visualize_tool(
    focus: str = "",
    format: str = "mermaid",
    depth: int = 1,
    limit: int = 30,
    direction: str = "TD",
    no_clusters: bool = False,
    file_level: bool = False,
    root: str = ".",
) -> dict:
    """Generate a Mermaid or DOT architecture diagram from the codebase graph.

    WHEN TO USE: Call this to get a visual dependency diagram of the
    codebase architecture. Uses smart filtering (PageRank, clusters,
    cycle highlighting) to produce readable diagrams. Paste Mermaid
    output into markdown or use DOT with Graphviz.

    Parameters
    ----------
    focus:
        Focus on a specific symbol (BFS neighborhood). If empty,
        shows the top-N most important symbols by PageRank.
    format:
        Output format: "mermaid" or "dot".
    depth:
        BFS depth for focus mode (default 1).
    limit:
        Max nodes in overview mode (default 30).
    direction:
        Mermaid direction: "TD" (top-down) or "LR" (left-right).
    no_clusters:
        Disable Louvain cluster grouping.
    file_level:
        Use file-level graph instead of symbol graph.

    Returns: diagram text (Mermaid or DOT), node/edge counts, and
    format metadata.
    """
    args = [
        "visualize",
        "--format",
        format,
        "--depth",
        str(depth),
        "--limit",
        str(limit),
        "--direction",
        direction,
    ]
    if focus:
        args.extend(["--focus", focus])
    if no_clusters:
        args.append("--no-clusters")
    if file_level:
        args.append("--file-level")
    return _run_roam(args, root)


visualize = visualize_tool


@_tool(
    name="roam_diagnose",
    description="Root cause analysis: upstream/downstream suspects ranked by composite risk.",
    output_schema=_SCHEMA_DIAGNOSE,
)
def diagnose(symbol: str, depth: int = 2, root: str = ".") -> dict:
    """Root cause analysis for a failing symbol or test.

    Call this when debugging a bug or test failure to find the likely
    root cause. Ranks upstream/downstream suspects by risk (git churn,
    complexity, health, co-change entropy). Faster than manually tracing
    call chains. Returns verdict naming top suspect."""
    args = ["diagnose", symbol, "--depth", str(depth)]
    return _run_roam(args, root)


@_tool(
    name="roam_relate",
    description="How symbols connect: shared deps, call chains, conflicts, cohesion score.",
)
def relate_symbols(symbols: list[str], files: list[str] | None = None, depth: int = 3, root: str = ".") -> dict:
    """Show how a set of symbols relate: shared deps, call chains, conflicts.

    WHEN TO USE: Call this when you have queried multiple symbols via
    ``context`` and need to understand HOW they connect. Shows direct
    edges, shared dependencies, shared callers, conflict risks, distance
    matrix, and a cohesion score. More useful than running ``trace``
    pairwise for 3+ symbols.

    Parameters
    ----------
    symbols:
        List of symbol names to analyze relationships between.
    files:
        Optional file/directory paths to include all symbols from.
    depth:
        Max hops for connecting paths (default 3).

    Returns: relationships, shared dependencies, shared callers,
    conflict risks, distance matrix, and cohesion score.
    """
    args = ["relate"] + symbols
    if files:
        for f in files:
            args.extend(["--path", f])
    if depth != 3:
        args.extend(["--depth", str(depth)])
    return _run_roam(args, root)


# ===================================================================
# Tier 3 tools -- agentic memory
# ===================================================================


@_tool(
    name="roam_annotate_symbol",
    description="Add persistent annotation to a symbol/file for future agent sessions.",
    read_only=False,
    idempotent=False,
)
def annotate_symbol(
    symbol: str,
    content: str,
    tag: str = "",
    author: str = "",
    expires: str = "",
    root: str = ".",
) -> dict:
    """Add a persistent annotation to a symbol or file.

    WHEN TO USE: Call this to leave a note for future agent sessions.
    Annotations survive reindexing and are auto-injected into ``context``
    output, giving every subsequent session institutional knowledge about
    the codebase.

    Parameters
    ----------
    symbol:
        Symbol name or file path to annotate. W430/Fix-D canonical;
        legacy ``target=`` callers are accepted via ``_PARAM_ALIASES``
        with a deprecation warning.
    content:
        The annotation text (e.g., "O(n^2) loop, see PR #42").
    tag:
        Category tag: security, performance, gotcha, review, wip.
    author:
        Who is annotating (agent name or user).
    expires:
        Optional expiry datetime (ISO 8601, e.g. "2025-12-31").

    Returns: confirmation with the resolved symbol and tag.
    """
    args = ["annotate", symbol, content]
    if tag:
        args.extend(["--tag", tag])
    if author:
        args.extend(["--author", author])
    if expires:
        args.extend(["--expires", expires])
    return _run_roam(args, root)


@_tool(
    name="roam_get_annotations",
    description="Read annotations for symbols, files, or project. Filter by tag/date.",
)
def get_annotations(
    symbol: str = "",
    tag: str = "",
    since: str = "",
    root: str = ".",
) -> dict:
    """Read annotations for a symbol, file, or the whole project.

    WHEN TO USE: Call this to retrieve institutional knowledge left by
    previous agent sessions or human reviewers. If you called ``context``
    with a task mode, annotations are already included in the output.

    Parameters
    ----------
    symbol:
        Symbol name or file path. If empty, returns all annotations.
        W430/Fix-D canonical; legacy ``target=`` callers are accepted
        via ``_PARAM_ALIASES`` with a deprecation warning.
    tag:
        Filter by tag (e.g., "security", "performance").
    since:
        Only annotations created after this datetime (ISO 8601).

    Returns: list of annotations with content, tag, author, and timestamps.
    """
    args = ["annotations"]
    if symbol:
        args.append(symbol)
    if tag:
        args.extend(["--tag", tag])
    if since:
        args.extend(["--since", since])
    return _run_roam(args, root)


@_tool(
    name="roam_endpoints",
    description="List all REST/GraphQL/gRPC endpoints with handlers, methods, and locations.",
)
def endpoints_tool(framework: str = "", method: str = "", group_by: str = "framework", root: str = ".") -> dict:
    """List all detected API endpoints in the codebase.

    WHEN TO USE: Call this to get a full map of all API routes and their
    handlers. Supports Flask, FastAPI, Django, Express, Spring, Rails,
    Laravel, Go net/http, GraphQL schemas, and gRPC .proto files.

    Parameters
    ----------
    framework:
        Filter to a specific framework (e.g. "flask", "express", "django").
    method:
        Filter by HTTP method (e.g. "GET", "POST").
    group_by:
        Group output by: "framework" (default), "file", or "method".

    Returns: verdict, count, frameworks detected, and list of endpoints
    each with method, path, handler, file, and line number.
    """
    args = ["endpoints"]
    if framework:
        args.extend(["--framework", framework])
    if method:
        args.extend(["--method", method])
    if group_by and group_by != "framework":
        args.extend(["--group-by", group_by])
    return _run_roam(args, root)


# ===================================================================
# MCP Resources -- static/cached summaries available at fixed URIs
# ===================================================================

if mcp is not None:

    @mcp.resource("roam://health")
    def get_health_resource() -> str:
        """Current codebase health snapshot (JSON).

        Provides the same data as the ``health`` tool but exposed as an
        MCP resource so agents can subscribe to or poll it. When an optional
        MCP-server dependency failed to import at startup, a
        ``mcp_server_degradations`` block names the absent feature surface so
        the degradation is observable rather than silent ("Make fallback
        chains loud").
        """
        data = _run_roam(["health"])
        degradations: dict[str, str] = {}
        if _MCP_EXTRAS_IMPORT_ERROR is not None:
            degradations["mcp_extras"] = _MCP_EXTRAS_IMPORT_ERROR
        if _TASKCONFIG_IMPORT_ERROR is not None:
            degradations["task_config"] = _TASKCONFIG_IMPORT_ERROR
        if degradations and isinstance(data, dict):
            data = {**data, "mcp_server_degradations": degradations}
        return json.dumps(data, indent=2)

    @mcp.resource("roam://summary")
    def get_summary_resource() -> str:
        """Full codebase summary (JSON).

        Equivalent to calling the ``understand`` tool, exposed as a
        resource for agents that prefer resource-based access.
        """
        data = _run_roam(["understand"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://architecture")
    def get_architecture_resource() -> str:
        """Architectural layers and module boundaries (JSON)."""
        data = _run_roam(["layers"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://hotspots")
    def get_hotspots_resource() -> str:
        """Complexity and churn hotspots (JSON)."""
        data = _run_roam(["hotspots"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://tech-stack")
    def get_tech_stack_resource() -> str:
        """Language and framework breakdown (JSON)."""
        data = _run_roam(["understand"])
        # Extract just the tech stack portion
        if isinstance(data, dict):
            return json.dumps(
                {
                    "languages": data.get("languages", {}),
                    "files": data.get("files", {}),
                    "frameworks": data.get("frameworks", []),
                },
                indent=2,
            )
        return json.dumps(data, indent=2)

    @mcp.resource("roam://dead-code")
    def get_dead_code_resource() -> str:
        """Dead/unreferenced symbols (JSON)."""
        data = _run_roam(["dead"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://recent-changes")
    def get_recent_changes_resource() -> str:
        """Recent git changes and their impact (JSON)."""
        data = _run_roam(["diff"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://dependencies")
    def get_dependencies_resource() -> str:
        """Module dependency graph overview (JSON)."""
        data = _run_roam(["deps"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://test-coverage")
    def get_test_coverage_resource() -> str:
        """Test file coverage analysis (JSON)."""
        data = _run_roam(["test-gaps"])
        return json.dumps(data, indent=2)

    @mcp.resource("roam://complexity")
    def get_complexity_resource() -> str:
        """Cognitive complexity analysis (JSON)."""
        data = _run_roam(["complexity"])
        return json.dumps(data, indent=2)


# ===================================================================
# Workspace tools -- multi-repo analysis
# ===================================================================


@_tool(
    name="roam_ws_understand",
    description="Multi-repo workspace overview: per-repo stats, cross-repo connections.",
)
def ws_understand(root: str = ".") -> dict:
    """Get a unified overview of a multi-repo workspace.

    WHEN TO USE: Call this when working with a project that spans
    multiple repositories (e.g., frontend + backend). Returns stats
    for each repo, cross-repo API connections, and key symbols.
    Requires a workspace to be initialized with `roam ws init`.

    Parameters
    ----------
    root:
        Working directory (must be within the workspace).

    Returns: per-repo stats (files, symbols, languages, key symbols),
    cross-repo edge count, and connection details.
    """
    return _run_roam(["ws", "understand"], root)


@_tool(
    name="roam_ws_context",
    description="Cross-repo augmented context for a symbol spanning multiple repos.",
)
def ws_context(symbol: str, root: str = ".") -> dict:
    """Get cross-repo augmented context for a symbol.

    WHEN TO USE: Call this when you need to understand a symbol that
    participates in cross-repo API calls. For example, querying a
    backend controller will also show frontend callers that hit its
    endpoints. Requires `roam ws init` + `roam ws resolve`.

    Parameters
    ----------
    symbol:
        Symbol name to search for across all workspace repos.

    Returns: symbol definition(s) found across repos, callers/callees
    within each repo, and cross-repo API edges.
    """
    return _run_roam(["ws", "context", symbol], root)


@_tool(
    name="roam_pr_diff",
    description="Structural graph delta of code changes: metric deltas, layer violations.",
)
def pr_diff_tool(staged: bool = False, commit_range: str = "", root: str = ".") -> dict:
    """Show structural consequences of code changes (graph delta).

    WHEN TO USE: Call this during code review to understand the
    architectural impact of a PR. Shows metric deltas (health score,
    cycles, complexity), cross-cluster edges, layer violations, symbol
    changes, and graph footprint. Much richer than a text diff.

    Parameters
    ----------
    staged:
        If True, analyse only staged changes.
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.

    Returns: verdict, metric deltas, edge analysis, symbol changes,
    and graph footprint.
    """
    args = ["pr-diff"]
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    return _run_roam(args, root)


@_tool(
    name="roam_effects",
    description="Side effects of functions: DB writes, network, filesystem (direct + transitive).",
)
def effects(symbol: str = "", path: str = "", effect_type: str = "", root: str = ".") -> dict:
    """Show side effects of functions (DB writes, network, filesystem, etc.).

    WHEN TO USE: Call this to understand what a function actually DOES
    beyond its signature. Shows both direct effects (from the function
    body) and transitive effects (inherited from callees via the call
    graph). Useful for assessing change risk and understanding data flow.

    Parameters
    ----------
    symbol:
        Symbol name to inspect effects for.
        Fix D: legacy alias ``target`` still accepted.
    path:
        File path to show effects per function.
        Fix D: legacy alias ``file`` still accepted.
    effect_type:
        Filter by effect type (e.g. "writes_db", "network").

    Returns: classified effects (direct and transitive) for the symbol,
    file, or entire codebase.
    """
    args = ["effects"]
    if symbol:
        args.append(symbol)
    if path:
        args.extend(["--path", path])
    if effect_type:
        args.extend(["--type", effect_type])
    return _run_roam(args, root)


@_tool(
    name="roam_budget_check",
    description="Check changes against architectural budgets (cycles, health floor, complexity).",
)
def budget_check(config: str = "", staged: bool = False, commit_range: str = "", root: str = ".") -> dict:
    """Check pending changes against architectural budgets.

    WHEN TO USE: Call this as a CI gate or before merging to verify
    that changes stay within defined quality budgets (max cycles,
    health floor, complexity ceiling, etc.). Exit code 1 if any
    budget is exceeded.

    Parameters
    ----------
    config:
        Path to custom budget YAML config.
    staged:
        If True, analyse only staged changes.
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.

    Returns: verdict, per-rule pass/fail results, and whether a
    baseline snapshot was available.
    """
    args = ["budget"]
    if config:
        args.extend(["--config", config])
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    return _run_roam(args, root)


@_tool(
    name="roam_attest",
    description="Proof-carrying PR attestation: evidence bundle + merge verdict.",
)
def attest(
    commit_range: str = "",
    staged: bool = False,
    output_format: str = "json",
    sign: bool = False,
    root: str = ".",
) -> dict:
    """Generate a proof-carrying PR attestation with all evidence bundled.

    WHEN TO USE: Call this before merging or in CI to get a single
    verifiable artifact that bundles blast radius, risk score, breaking
    changes, fitness violations, budget consumed, affected tests, and
    effects. The verdict indicates whether it is safe to merge.

    Parameters
    ----------
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.
    staged:
        If True, attest only staged changes.
    output_format:
        Output format: ``json``, ``text``, or ``markdown``.
    sign:
        If True, include SHA-256 content hash for tamper detection.

    Returns: attestation metadata, evidence bundle, and merge verdict.
    """
    args = ["attest"]
    if commit_range:
        args.append(commit_range)
    if staged:
        args.append("--staged")
    if output_format:
        args.extend(["--format", output_format])
    if sign:
        args.append("--sign")
    return _run_roam(args, root)


@_tool(
    name="roam_capsule_export",
    description="Sanitized structural graph export without code bodies (privacy-safe).",
)
def capsule_export(redact_paths: bool = False, no_signatures: bool = False, root: str = ".") -> dict:
    """Export a sanitized structural graph without function bodies.

    WHEN TO USE: Call this to create a privacy-safe export of the
    codebase architecture for external review, audits, or consulting.
    Contains symbols, edges, clusters, and health metrics but no
    implementation code.

    Parameters
    ----------
    redact_paths:
        If True, anonymize file paths with hashes.
    no_signatures:
        If True, omit function signatures.

    Returns: topology, symbols, edges, clusters, and health metrics.
    """
    args = ["capsule"]
    if redact_paths:
        args.append("--redact-paths")
    if no_signatures:
        args.append("--no-signatures")
    return _run_roam(args, root)


@_tool(
    name="roam_path_coverage",
    description="Critical call paths with zero test protection, ranked by risk.",
    task_mode="optional",
)
def path_coverage_tool(from_pattern: str = "", to_pattern: str = "", max_depth: int = 8, root: str = ".") -> dict:
    """Find critical call paths with zero test protection.

    WHEN TO USE: Call this to discover untested paths from entry
    points to sensitive sinks (DB writes, network, filesystem).
    Shows which paths are most at risk and suggests optimal test
    insertion points for maximum coverage.

    Parameters
    ----------
    from_pattern:
        Glob to filter entry points by file path.
    to_pattern:
        Glob to filter sinks by file path.
    max_depth:
        Maximum path depth (default: 8).

    Returns: untested paths ranked by risk, with test suggestions.
    """
    args = ["path-coverage"]
    if from_pattern:
        args.extend(["--from", from_pattern])
    if to_pattern:
        args.extend(["--to", to_pattern])
    if max_depth != 8:
        args.extend(["--max-depth", str(max_depth)])
    return _run_roam(args, root)


@_tool(
    name="roam_forecast",
    description="Predict when metrics will exceed thresholds (Theil-Sen regression).",
    task_mode="optional",
)
def forecast(symbol: str = "", horizon: int = 30, alert_only: bool = False, root: str = ".") -> dict:
    """Predict when metrics will exceed thresholds.

    WHEN TO USE: Call this to identify functions with accelerating
    complexity or metrics trending toward dangerous thresholds.
    Uses Theil-Sen regression on snapshot history for aggregate
    trends and churn-weighted analysis for per-symbol risk.

    Parameters
    ----------
    symbol:
        Specific symbol to forecast.
    horizon:
        Number of snapshots to look ahead (default: 30).
    alert_only:
        If True, only show non-stable trends.

    Returns: aggregate metric trends and at-risk symbols.
    """
    args = ["forecast"]
    if symbol:
        args.extend(["--symbol", symbol])
    if horizon != 30:
        args.extend(["--horizon", str(horizon)])
    if alert_only:
        args.append("--alert-only")
    return _run_roam(args, root)


@_tool(
    name="roam_generate_plan",
    description="Structured execution plan for code modification: read order, invariants, tests.",
    task_mode="optional",
)
def generate_plan(
    symbol: str = "",
    task: str = "refactor",
    path: str = "",
    staged: bool = False,
    depth: int = 2,
    root: str = ".",
) -> dict:
    """Generate a structured execution plan for modifying code.

    WHEN TO USE: Call this before any non-trivial code modification.
    Returns a step-by-step strategy: read order, invariants to preserve,
    safe modification points, touch-carefully warnings, test shortlist,
    and post-change verification commands.

    Parameters
    ----------
    symbol:
        Symbol name to plan for. W430/Fix-D canonical; legacy
        ``target=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning.
    task:
        Task type: refactor, debug, extend, review, understand.
    path:
        File to plan for (alternative to symbol). W347/Fix-D canonical
        for filesystem paths -- legacy ``file_path=`` / ``filename=`` /
        ``filepath=`` / ``file=`` callers are accepted via
        ``_PARAM_ALIASES`` with a deprecation warning.
    staged:
        Plan for staged changes.
    depth:
        Call graph depth for read order (default: 2).

    Returns: structured plan with 6 sections.
    """
    args = ["plan"]
    if symbol:
        args.append(symbol)
    if task != "refactor":
        args.extend(["--task", task])
    if path:
        args.extend(["--file", path])
    if staged:
        args.append("--staged")
    if depth != 2:
        args.extend(["--depth", str(depth)])
    return _run_roam(args, root)


@_tool(
    name="roam_adversarial_review",
    description="Adversarial architecture review: challenges about cycles, anti-patterns, coupling.",
    task_mode="optional",
)
def adversarial_review(staged: bool = False, commit_range: str = "", severity: str = "low", root: str = ".") -> dict:
    """Adversarial architecture review — challenge code changes.

    WHEN TO USE: Call this after making changes to get targeted
    architectural challenges. Acts as a "Dungeon Master" generating
    questions about cycles, layer violations, anti-patterns, and
    cross-cluster coupling that the developer must address.

    Parameters
    ----------
    staged:
        Review staged changes only.
    commit_range:
        Review a commit range (e.g. main..HEAD).
    severity:
        Minimum severity filter: low, medium, high, critical.

    Returns: list of architectural challenges with severity and questions.
    """
    args = ["adversarial"]
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    if severity != "low":
        args.extend(["--severity", severity])
    return _run_roam(args, root)


@_tool(
    name="roam_cut_analysis",
    description="Minimum cut analysis: fragile domain boundaries, highest-impact leak edges.",
    task_mode="optional",
)
def cut_analysis(
    between_a: str = "",
    between_b: str = "",
    leak_edges: bool = False,
    top_n: int = 10,
    root: str = ".",
) -> dict:
    """Minimum cut analysis — find fragile domain boundaries.

    WHEN TO USE: Call this to identify the thinnest boundaries between
    architectural clusters and the highest-impact "leak edges" whose
    removal would best improve domain isolation. Useful for targeted
    refactoring decisions.

    Parameters
    ----------
    between_a:
        First cluster name (use with between_b for specific pair).
    between_b:
        Second cluster name.
    leak_edges:
        Focus on leak edge analysis.
    top_n:
        Show top N boundaries (default: 10).

    Returns: boundary analysis with min-cut sizes, thinness, and leak edges.
    """
    args = ["cut"]
    if between_a and between_b:
        args.extend(["--between", between_a, between_b])
    if leak_edges:
        args.append("--leak-edges")
    if top_n != 10:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@_tool(
    name="roam_get_invariants",
    description="Implicit contracts for symbols: signature stability, usage spread, breaking risk.",
)
def get_invariants(
    symbol: str = "",
    public_api: bool = False,
    breaking_risk: bool = False,
    top_n: int = 20,
    root: str = ".",
) -> dict:
    """Discover implicit contracts for symbols.

    WHEN TO USE: Call this before modifying a symbol to understand what
    must remain true. Returns signature contracts, caller stability,
    usage spread, and breaking risk scores.

    Parameters
    ----------
    symbol:
        Symbol name or file path to analyze. W430/Fix-D canonical;
        legacy ``target=`` callers are accepted via ``_PARAM_ALIASES``
        with a deprecation warning.
    public_api:
        Analyze all exported/public symbols.
    breaking_risk:
        Rank symbols by breaking risk (callers * file spread).
    top_n:
        Max symbols to show (default: 20).

    Returns: invariants per symbol with breaking risk scores.
    """
    args = ["invariants"]
    if symbol:
        args.append(symbol)
    if public_api:
        args.append("--public-api")
    if breaking_risk:
        args.append("--breaking-risk")
    if top_n != 20:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@_tool(
    name="roam_bisect_blame",
    description="Find snapshots that caused architectural degradation, ranked by impact.",
    task_mode="optional",
)
def bisect_blame(
    metric: str = "health_score",
    threshold: float = 0,
    direction: str = "degraded",
    top_n: int = 10,
    root: str = ".",
) -> dict:
    """Find which snapshots caused architectural degradation.

    WHEN TO USE: Call this when health score has dropped or metrics
    have worsened. Walks snapshot history and ranks snapshots by the
    magnitude of metric changes to identify the commits that caused
    the biggest structural regressions.

    Parameters
    ----------
    metric:
        Metric to track (health_score, cycles, avg_complexity, etc.).
    threshold:
        Only show deltas exceeding this threshold.
    direction:
        Filter: degraded, improved, or both.
    top_n:
        Show top N snapshots by impact (default: 10).

    Returns: ranked list of snapshots by architectural impact.
    """
    args = ["bisect", "--metric", metric]
    if threshold > 0:
        args.extend(["--threshold", str(threshold)])
    if direction != "degraded":
        args.extend(["--direction", direction])
    if top_n != 10:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@_tool(
    name="roam_simulate",
    description="Predict metric deltas from move/extract/merge/delete operations.",
    task_mode="required",
)
def simulate(
    operation: str,
    symbol: str = "",
    target_file: str = "",
    file_a: str = "",
    file_b: str = "",
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Simulate a structural change and predict metric deltas.

    WHEN TO USE: Call this before making architectural changes (moving,
    extracting, merging, or deleting symbols/files) to predict the impact
    on health score, modularity, cycles, and other metrics. Enables
    gradient-descent on architecture by testing "what if" scenarios.

    Parameters
    ----------
    operation:
        One of: "move", "extract", "merge", "delete".
    symbol:
        Symbol name for move/extract/delete operations.
    target_file:
        Destination file for move/extract operations.
    file_a:
        Target file for merge (file_b merges into file_a).
    file_b:
        Source file for merge (merged into file_a).

    Returns: predicted metric deltas (health score, cycles, modularity,
    layer violations, etc.), operation summary, verdict, and warnings.
    """
    args = ["simulate", operation]
    if operation in ("move", "extract"):
        if symbol:
            args.append(symbol)
        if target_file:
            args.append(target_file)
    elif operation == "merge":
        if file_a:
            args.append(file_a)
        if file_b:
            args.append(file_b)
    elif operation == "delete":
        if symbol:
            args.append(symbol)
    result = _run_roam(args, root)
    # Record this as a satisfied prerequisite for follow-up roam_mutate.
    if _mcp_session is not None:
        _mcp_session.record_tool_call(ctx, "roam_simulate", target=symbol or file_a or file_b)
    return result


@_tool(
    name="roam_closure",
    description="Minimal set of changes needed for rename/delete/modify (exact files + lines).",
    task_mode="optional",
)
def closure(symbol: str, rename: str = "", delete: bool = False, root: str = ".") -> dict:
    """Compute the minimal set of changes needed when modifying a symbol.

    WHEN TO USE: Call this when you need to know EXACTLY what must change
    for a rename, deletion, or modification. Unlike ``impact`` (blast
    radius -- what MIGHT break), closure tells you what MUST change.
    Returns the exact files and locations that need updating.

    Parameters
    ----------
    symbol:
        Symbol name to compute closure for.
    rename:
        New name for a rename operation. If provided, also searches
        for string references in doc/config files.
    delete:
        If True, compute deletion closure.

    Returns: list of changes grouped by type (update_call, update_import,
    update_test, update_doc), with file paths and line numbers.
    """
    args = ["closure", symbol]
    if rename:
        args.extend(["--rename", rename])
    if delete:
        args.append("--delete")
    return _run_roam(args, root)


@_tool(
    name="roam_doc_intent",
    description="Link documentation to code: find drift, dead refs, undocumented symbols.",
)
def doc_intent(
    symbol: str = "",
    doc: str = "",
    drift: bool = False,
    undocumented: bool = False,
    top_n: int = 20,
    root: str = ".",
) -> dict:
    """Link documentation to code — find what docs describe what code.

    WHEN TO USE: Call this to understand the relationship between
    documentation and code. Finds doc-to-code links, drift (dead
    references to removed symbols), and undocumented high-centrality
    symbols that should have docs.

    Parameters
    ----------
    symbol:
        Find docs mentioning this specific symbol.
    doc:
        Find code referenced by this specific doc file.
    drift:
        Show references to symbols that no longer exist.
    undocumented:
        Show important symbols not mentioned in any docs.
    top_n:
        Max items to show (default: 20).

    Returns: doc-code links, drift, and undocumented symbols.
    """
    args = ["intent"]
    if symbol:
        args.extend(["--symbol", symbol])
    if doc:
        args.extend(["--doc", doc])
    if drift:
        args.append("--drift")
    if undocumented:
        args.append("--undocumented")
    if top_n != 20:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@_tool(
    name="roam_fingerprint",
    description="Topology fingerprint for cross-repo comparison or structural drift tracking.",
)
def mcp_fingerprint(compact: bool = False, export_path: str = "", compare_path: str = "", root: str = ".") -> dict:
    """Extract a topology fingerprint for cross-repo comparison.

    WHEN TO USE: Call this to get the structural signature of a codebase
    (layers, modularity, connectivity, clusters, hub/bridge ratio,
    PageRank distribution). Use --compare to diff against another repo's
    saved fingerprint. Useful for identifying similar architectures or
    tracking structural drift over time.

    Parameters
    ----------
    compact:
        If True, return a single-line summary.
    export_path:
        If provided, save fingerprint JSON to this file path.
    compare_path:
        If provided, compare with a previously saved fingerprint JSON.

    Returns: topology metrics, cluster summaries, hub/bridge ratio,
    PageRank Gini, dependency direction, and anti-patterns.
    """
    args = ["fingerprint"]
    if compact:
        args.append("--compact")
    if export_path:
        args.extend(["--export", export_path])
    if compare_path:
        args.extend(["--compare", compare_path])
    return _run_roam(args, root)


@_tool(
    name="roam_rules_check",
    description="Evaluate custom governance rules from .roam/rules/ YAML files.",
)
def rules_check(ci: bool = False, rules_dir: str = "", root: str = ".") -> dict:
    """Evaluate custom governance rules defined in .roam/rules/.

    WHEN TO USE: Call this to check architectural constraints defined as
    YAML rule files. Supports path_match rules (no direct edges between
    from/to patterns) and symbol_match rules (symbols matching criteria
    must satisfy requirements like test coverage). Use ``--ci`` in CI
    pipelines to fail on error-severity violations.

    Parameters
    ----------
    ci:
        If True, exit code 1 on error-severity violations.
    rules_dir:
        Custom rules directory path.

    Returns: per-rule pass/fail results with violation details.
    """
    args = ["rules"]
    if ci:
        args.append("--ci")
    if rules_dir:
        args.extend(["--rules-dir", rules_dir])
    return _run_roam(args, root)


@_tool(
    name="roam_orchestrate",
    description="Partition codebase for parallel multi-agent work with exclusive write zones.",
    task_mode="optional",
)
async def orchestrate(
    n_agents: int,
    files: list[str] | None = None,
    staged: bool = False,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Partition codebase for parallel multi-agent work (swarm orchestration).

    WHEN TO USE: Call this before splitting work across multiple AI agents.
    Assigns exclusive write zones, read-only dependencies, interface
    contracts, a merge order, and a conflict probability score so agents
    can work in parallel without stepping on each other.

    Parameters
    ----------
    n_agents:
        Number of agents to partition work for.
    files:
        Optional list of files or directories to restrict to.
    staged:
        If True, restrict to files in the git staging area.

    Returns: per-agent write/read file lists, contracts, merge order,
    conflict probability, and shared interface symbols.
    """
    args = ["orchestrate", "--agents", str(n_agents)]
    if files:
        for f in files:
            args.extend(["--file", f])
    if staged:
        args.append("--staged")
    if _mcp_progress is not None and ctx is not None:
        exit_code, stdout, stderr = await _mcp_progress.run_with_phase_progress(
            args, ctx=ctx, cwd=root, initial_message="partitioning"
        )
        return _parse_subprocess_result(args, exit_code, stdout, stderr)
    return await _run_roam_async(args, root)


@_tool(
    name="roam_mutate",
    description="Agentic editing: move/rename/add-call/extract symbols with auto-import rewrite.",
    destructive=True,
    read_only=False,
    idempotent=False,
    task_mode="optional",
)
def mutate(
    operation: str,
    symbol: str = "",
    target_file: str = "",
    new_name: str = "",
    from_symbol: str = "",
    to_symbol: str = "",
    args: str = "",
    lines: str = "",
    apply: bool = False,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Syntax-less agentic editing -- move, rename, add-call, extract symbols.

    WHEN TO USE: Call this when you need to make structural code changes
    (move a symbol to a new file, rename across the codebase, add a call
    between functions, or extract lines into a new function). Automatically
    rewrites imports and updates references. Default is dry-run (preview);
    set apply=True to write changes.

    Parameters
    ----------
    operation:
        One of: "move", "rename", "add-call", "extract".
    symbol:
        Symbol name for move/rename/extract operations.
    target_file:
        Destination file for move operation.
    new_name:
        New name for rename or extract operations.
    from_symbol:
        Calling symbol for add-call operation.
    to_symbol:
        Callee symbol for add-call operation.
    args:
        Arguments string for add-call (e.g. "data, config").
    lines:
        Line range for extract (e.g. "5-10").
    apply:
        If True, write changes to disk. Default is dry-run.

    Returns: change plan with files modified, per-file changes, and verdict.
    """
    cmd_args = ["mutate", operation]
    if operation == "move":
        if symbol:
            cmd_args.append(symbol)
        if target_file:
            cmd_args.append(target_file)
    elif operation == "rename":
        if symbol:
            cmd_args.append(symbol)
        if new_name:
            cmd_args.append(new_name)
    elif operation == "add-call":
        if from_symbol:
            cmd_args.extend(["--from", from_symbol])
        if to_symbol:
            cmd_args.extend(["--to", to_symbol])
        if args:
            cmd_args.extend(["--args", args])
    elif operation == "extract":
        if symbol:
            cmd_args.append(symbol)
        if lines:
            cmd_args.extend(["--lines", lines])
        if new_name:
            cmd_args.extend(["--name", new_name])
    if apply:
        cmd_args.append("--apply")
    result = _run_roam(cmd_args, root)

    # Soft contract enforcement — a destructive operation should follow
    # ``roam_simulate`` for the same target. Inject a compliance hint
    # without refusing the call. Read-only (apply=False) is harmless;
    # only the actual write needs the gate.
    if apply and _mcp_session is not None:
        compliance = _mcp_session.contract_check(
            ctx,
            current_tool="roam_mutate",
            target=symbol or new_name or to_symbol,
            prerequisites=("roam_simulate",),
        )
        if isinstance(result, dict):
            result["contract_compliance"] = compliance
        # Record this destructive call so a follow-up mutate can see it.
        _mcp_session.record_tool_call(ctx, "roam_mutate", target=symbol or new_name or to_symbol)

    return result


@_tool(
    name="roam_vuln_map",
    description="Ingest vulnerability scanner reports (npm/pip/trivy/osv), match to symbols.",
    read_only=False,
    idempotent=False,
    task_mode="optional",
)
def vuln_map(
    npm_audit: str = "",
    pip_audit: str = "",
    trivy: str = "",
    osv: str = "",
    generic: str = "",
    root: str = ".",
) -> dict:
    """Ingest vulnerability scanner reports and match to codebase symbols.

    WHEN TO USE: Call this to import vulnerability data from security scanners
    (npm audit, pip-audit, Trivy, OSV, or a generic JSON format). Each
    vulnerability is matched to symbols in the codebase index so you can
    assess real exposure. After ingestion, use ``vuln_reach`` to check
    reachability.

    Parameters
    ----------
    npm_audit:
        Path to npm audit JSON report.
    pip_audit:
        Path to pip-audit JSON report.
    trivy:
        Path to Trivy JSON report.
    osv:
        Path to OSV scanner JSON report.
    generic:
        Path to generic JSON vulnerability list.

    Returns: ingested vulnerabilities with symbol match status.
    """
    args = ["vuln-map"]
    if npm_audit:
        args.extend(["--npm-audit", npm_audit])
    if pip_audit:
        args.extend(["--pip-audit", pip_audit])
    if trivy:
        args.extend(["--trivy", trivy])
    if osv:
        args.extend(["--osv", osv])
    if generic:
        args.extend(["--generic", generic])
    return _run_roam(args, root)


@_tool(
    name="roam_vuln_reach",
    description="Vulnerability reachability through call graph: paths, hops, blast radius.",
)
def vuln_reach(from_entry: str = "", cve: str = "", root: str = ".") -> dict:
    """Query reachability of ingested vulnerabilities through the call graph.

    WHEN TO USE: Call this after ``vuln_map`` to determine which vulnerabilities
    are actually reachable from entry points in your code. Unreachable vulns
    can be safely deprioritized. Shows shortest path, hop count, and blast
    radius for each reachable vulnerability.

    Parameters
    ----------
    from_entry:
        Check reachability from a specific entry point symbol.
    cve:
        Analyze a specific CVE ID.

    Returns: reachability status, paths, hop counts, and blast radius
    for each vulnerability.
    """
    args = ["vuln-reach"]
    if from_entry:
        args.extend(["--from", from_entry])
    if cve:
        args.extend(["--cve", cve])
    return _run_roam(args, root)


@_tool(
    name="roam_reachability_triage",
    description=(
        "Classify vulnerability-flow findings as reachable or not reachable "
        "from entrypoints through local call-graph evidence. This MCP tool "
        "is read-only: it does not write or move the reachability baseline; "
        "use the CLI for baseline management."
    ),
)
def roam_reachability_triage(commit_range: str = "", root: str = ".") -> dict:
    """Classify vulnerability-flow findings by entrypoint reachability.

    Parameters
    ----------
    commit_range:
        Optional git range, such as ``main..HEAD``, used to limit the facts
        to changed files.
    root:
        Project root to inspect.

    Returns
    -------
    dict
        Envelope containing ``summary``, ``agent_contract``,
        ``wrapper_version``, ``delegated_compose``, ``primitives``,
        ``metrics``, ``flows``, ``gate``, ``missing_primitives``,
        ``honesty``, and ``budget``.
    """
    args = ["reachability-triage"]
    if commit_range:
        args.extend(["--range", commit_range])
    return _run_roam(args, root)


@_tool(
    name="roam_secrets",
    description="Scan for hardcoded secrets, API keys, tokens, passwords (25 patterns).",
)
def secrets_scan(severity: str = "all", root: str = ".") -> dict:
    """Scan indexed files for hardcoded secrets and credentials.

    WHEN TO USE: Call this to find leaked API keys, tokens, passwords,
    private keys, and database connection strings in source code.
    Supports 24 detection patterns covering AWS, GitHub, Slack, Stripe,
    Google, database URIs, JWTs, and generic secrets.

    Parameters
    ----------
    severity:
        Filter by minimum severity: "all", "high", "medium", "low".

    Returns: list of findings with file, line, severity, pattern name,
    and masked matched text. Never exposes full secret values.
    """
    args = ["secrets"]
    if severity != "all":
        args.extend(["--severity", severity])
    return _run_roam(args, root)


# ===================================================================
# Runtime trace tools
# ===================================================================


@_tool(
    name="roam_ingest_trace",
    description="Ingest runtime traces (OTel/Jaeger/Zipkin), match spans to symbols.",
    read_only=False,
    idempotent=False,
    task_mode="optional",
)
def ingest_trace(trace_file: str, format: str = "", root: str = ".") -> dict:
    """Ingest runtime traces and match spans to symbols.

    WHEN TO USE: Call this to overlay runtime performance data on top of
    the static codebase graph. Supports OpenTelemetry, Jaeger, Zipkin,
    and a simple generic JSON format. After ingestion, use ``hotspots``
    to find discrepancies between static and runtime rankings.

    Parameters
    ----------
    trace_file:
        Path to the JSON trace file.
    format:
        Trace format: "otel", "jaeger", "zipkin", "generic".
        If empty, auto-detects from the JSON structure.

    Returns: ingested span count, matched/unmatched symbols, and per-span
    details including call count, latency, and error rate.
    """
    args = ["ingest-trace"]
    if format:
        args.extend([f"--{format}", trace_file])
    else:
        args.append(trace_file)
    return _run_roam(args, root)


@_tool(
    name="roam_runtime_hotspots",
    description="Runtime hotspots where static and runtime rankings disagree (UPGRADE/DOWNGRADE).",
)
def runtime_hotspots(runtime_sort: bool = False, discrepancy: bool = False, root: str = ".") -> dict:
    """Show runtime hotspots where static and runtime rankings disagree.

    WHEN TO USE: Call this after ingesting traces to find hidden hotspots
    -- symbols that static analysis considers safe but are runtime-critical
    (UPGRADE), or statically risky symbols with low traffic (DOWNGRADE).

    Parameters
    ----------
    runtime_sort:
        If True, sort by runtime metrics (call count).
    discrepancy:
        If True, only show static/runtime mismatches (UPGRADE/DOWNGRADE).

    Returns: hotspots with classification, static rank, runtime rank,
    and both static and runtime metrics.
    """
    args = ["hotspots"]
    if runtime_sort:
        args.append("--runtime")
    if discrepancy:
        args.append("--discrepancy")
    return _run_roam(args, root)


# ===================================================================
# Semantic search
# ===================================================================


@_tool(
    name="roam_search_semantic",
    description="Find symbols by natural language query (hybrid BM25 + vector + framework packs).",
    task_mode="optional",
)
def search_semantic(query: str, top: int = 10, threshold: float = 0.05, root: str = ".") -> dict:
    """Find symbols by natural language query using hybrid BM25+vector search.

    WHEN TO USE: Call this when you have a conceptual description of what
    you are looking for rather than an exact symbol name. For example,
    "database connection handling" or "user authentication logic". Uses
    hybrid BM25 + TF-IDF vector fusion plus pre-indexed framework/library
    packs to improve cold-start retrieval for common stacks. For exact name
    matching, use ``search_symbol`` instead.

    Parameters
    ----------
    query:
        Natural language search query.
    top:
        Number of results to return (default 10).
    threshold:
        Minimum similarity score (default 0.05).

    Returns: ranked list of matching symbols with similarity scores,
    file paths, kinds, and line numbers.
    """
    # Options first, then a `--` delimiter so an option-shaped query
    # (e.g. one beginning with `--top` or `--help`) is parsed as the
    # positional query rather than silently consumed as a flag.
    args = ["search-semantic", "--top", str(top), "--threshold", str(threshold), "--", query]
    return _run_roam(args, root)


# ===================================================================
# Daily workflow tools
# ===================================================================


@_tool(
    name="roam_diff",
    description=(
        "Show the blast radius of your edits BEFORE you commit. Run after "
        "Edit/Write tools to see affected symbols, files, tests, plus coupling "
        "and fitness warnings. Use when user asks 'what did my change break?', "
        "'safe to commit?'. Replaces ad-hoc `git diff --stat` inspection with "
        "graph-aware impact data. For PR-level risk verdict, use roam_pr_risk."
    ),
    output_schema=_SCHEMA_DIFF,
)
def roam_diff(commit_range: str = "", staged: bool = False, root: str = ".") -> dict:
    """Blast radius of uncommitted or committed changes.

    Call this after making code changes to see what's affected BEFORE
    committing. Shows affected symbols, files, tests, coupling warnings,
    and fitness violations. For pre-PR analysis, use pr_risk instead."""
    args = ["diff"]
    if commit_range:
        args.append(commit_range)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(
    name="roam_symbol",
    description="Symbol definition, callers, callees, PageRank, fan-in/out metrics.",
)
def roam_symbol(symbol: str, full: bool = False, root: str = ".") -> dict:
    """Symbol definition, callers, callees, and graph metrics.

    WHEN TO USE: when you need detailed info about a specific symbol --
    definition, who calls it, what it calls, PageRank, fan-in/out.
    More focused than roam_context (which adds files-to-read).

    Parameters
    ----------
    symbol:
        Symbol name. Supports ``file:symbol`` for disambiguation.
        Fix D: legacy alias ``name`` still accepted.
    full:
        Show all callers/callees without truncation.
    root:
        Working directory (project root).

    Returns: name, kind, signature, location, docstring, PageRank,
    in_degree, out_degree, callers list, callees list.
    """
    args = ["symbol", symbol]
    if full:
        args.append("--full")
    return _run_roam(args, root)


@_tool(
    name="roam_deps",
    description=(
        "Use for: 'what does file X import?' / 'which files depend on "
        "module Y?' / 'show me the importers of Z'. Pick this for "
        "file/module-level coupling before refactors; symbol-level "
        "lookups belong in roam_uses. Set multi=True to get imports + "
        "importers + git co-change coupling in ONE envelope (do this "
        "instead of shelling out to `roam deps --multi` or hand-querying "
        "the index). Run in parallel with roam_coupling for the biggest "
        "token win."
    ),
)
def roam_deps(path: str, full: bool = False, multi: bool = False, root: str = ".") -> dict:
    """File-level import/imported-by relationships.

    Call this to understand a file's dependencies -- what it imports
    and what imports it. Use for module boundary analysis and
    refactoring impact. ``multi=True`` mirrors the CLI ``--multi`` flag:
    imports + importers + git co-change coupling in one call (saves the
    agent a CLI/SQL round-trip)."""
    args = ["deps", path]
    if full:
        args.append("--full")
    if multi:
        args.append("--multi")
    return _run_roam(args, root)


@_tool(
    name="roam_uses",
    description=(
        "Use for: 'who calls X?' / 'where is Y referenced?' / 'what "
        "breaks if I rename Z?'. Pick over multi-pattern grep — "
        "graph-resolved callers, importers, and subclasses grouped by "
        "edge type, zero comment/string-literal false positives. For 3+ "
        "symbols use roam_batch_get; for counts only, roam_impact."
    ),
)
def roam_uses(symbol: str, full: bool = False, root: str = ".") -> dict:
    """All consumers of a symbol: callers, importers, inheritors.

    WHEN TO USE: this is the right tool for "find every reference to X"
    queries. Multi-shape regex grep — ``->X|\\.X\\b|'X'|"X"`` — is the
    standard way to do this with raw text tools, but it produces false
    positives in comments / docstrings / unrelated string literals,
    and the agent then has to filter those out. ``roam_uses`` resolves
    references through the indexed call/import/inherit graph: every
    result is a real symbol that depends on ``symbol``, grouped by
    edge type (calls, imports, inheritance, trait usage). Broader
    than ``roam_impact`` (which counts symbols only); use ``uses``
    for planning API changes or "what would break if I delete X".

    For verifying multiple symbols (a typical "is X really dead?"
    sweep), call ``roam_batch_get`` instead — one round-trip resolves
    up to 50 symbols with full caller/callee metadata.

    Fix D: legacy alias ``name`` is still accepted (translates to
    ``symbol``) with a deprecation warning in ``summary.alias_warnings``.
    """
    args = ["uses", symbol]
    if full:
        args.append("--full")
    result = _run_roam(args, root)
    # Enrich the envelope with a discoverability hint so agents that
    # repeatedly call roam_uses learn about the cheaper batch path.
    if isinstance(result, dict):
        summary = result.setdefault("summary", {}) if "summary" in result or True else {}
        if isinstance(summary, dict):
            summary.setdefault(
                "hint",
                "verifying multiple symbols? call roam_batch_get(names=[...]) for one round-trip",
            )
    return result


# ===================================================================
# Health tools
# ===================================================================


@_tool(
    name="roam_weather",
    description="Churn x complexity hotspot ranking: highest-leverage refactoring targets.",
)
def roam_weather(count: int = 20, root: str = ".") -> dict:
    """Code hotspots: churn x complexity ranking.

    WHEN TO USE: to find highest-leverage refactoring targets -- files
    that are both complex AND frequently changed. Complements roam_health
    with temporal data.

    Parameters
    ----------
    count:
        Number of hotspots to return (default 20).
    root:
        Working directory (project root).

    Returns: hotspots list with score, churn, complexity, commit count,
    author count, reason classification, and file path.
    """
    args = ["weather", "-n", str(count)]
    return _run_roam(args, root)


@_tool(
    name="roam_debt",
    description=(
        "Rank files by tech-debt score with SQALE remediation-cost estimates. "
        "Triggers: 'where's the worst debt?', 'what should we refactor next?', "
        "'estimate cleanup cost'. Pair with roam_complexity_report for "
        "per-function brain-method targeting."
    ),
)
def roam_debt(
    limit: int = 20,
    by_kind: bool = False,
    threshold: float = 0.0,
    roi: bool = False,
    root: str = ".",
) -> dict:
    """Hotspot-weighted technical debt prioritization with remediation costs.

    WHEN TO USE: to get a prioritized refactoring list. Combines health
    signals (complexity, cycles, god components, dead exports) with churn.
    Includes SQALE remediation cost estimates in dev-minutes.

    Parameters
    ----------
    limit:
        Number of files to return (default 20).
    by_kind:
        Group results by parent directory.
    threshold:
        Only show files with debt score >= this value.
    roi:
        Include estimated refactoring ROI (hours saved per quarter).
    root:
        Working directory (project root).

    Returns: summary (total_files, total_debt, remediation time),
    suggestions, optional ROI estimate, and items list with debt_score,
    health_penalty, hotspot_factor, breakdown, commit_count, distinct_authors.
    """
    args = ["debt", "-n", str(limit)]
    if by_kind:
        args.append("--by-kind")
    if threshold > 0:
        args.extend(["--threshold", str(threshold)])
    if roi:
        args.append("--roi")
    return _run_roam(args, root)


@_tool(
    name="roam_docs_coverage",
    description="Doc coverage + stale-doc drift with PageRank-ranked missing docs.",
)
def roam_docs_coverage(limit: int = 20, days: int = 90, threshold: int = 0, root: str = ".") -> dict:
    """Analyze exported-symbol documentation coverage and staleness drift.

    WHEN TO USE: documentation hygiene audits and CI gating for doc quality.
    Combines:
    - coverage of exported/public symbols with docs,
    - stale docs where implementation changed long after docs,
    - missing-doc hotlist ranked by symbol PageRank.

    Parameters
    ----------
    limit:
        Max number of missing/stale symbols returned (default 20).
    days:
        Staleness threshold in days (default 90).
    threshold:
        Fail gate when coverage percentage is below this threshold (0 disables gate).
    root:
        Working directory (project root).

    Returns: summary (coverage_pct, documented/public counts, stale/missing),
    stale_docs list, and missing_docs list.
    """
    args = ["docs-coverage", "--limit", str(limit), "--days", str(days)]
    if threshold > 0:
        args.extend(["--threshold", str(threshold)])
    return _run_roam(args, root)


_LLM_ENRICH_SYSTEM_PROMPT = (
    "You are a repository hygiene assistant. The user's docs reference "
    "files that no longer exist. For each missing target, propose up to "
    "three candidate paths from the supplied repository file list, ranked "
    "best first. Use semantic + path similarity. Use an empty array when "
    "no candidate is plausible. Reply ONLY with valid compact JSON, no prose."
)


def _build_llm_enrich_prompt(unresolved: list[str], repo_paths: list[str]) -> str:
    """Construct the user-side prompt for the LLM enricher.

    Strict-shape JSON instructions because we parse the response.

    The shape changed in v12.50: instead of a single best guess per
    missing target we ask for a *ranked array* of up to three candidate
    paths (best first). This lets the enricher surface alternatives in
    ``--with-candidates`` mode and the verdict UI even when the top
    candidate isn't trustworthy on its own.

    The parser also accepts the legacy single-string shape, so older
    LLMs / cached responses still flow through.
    """
    targets_block = "\n".join(f"- {t}" for t in unresolved[:40])
    paths_block = "\n".join(f"- {p}" for p in repo_paths[:500])
    return (
        "Missing references in the repo's docs:\n"
        f"{targets_block}\n\n"
        "Available repository file paths:\n"
        f"{paths_block}\n\n"
        'Reply with this exact JSON shape: {"<missing>": ["<path1>", "<path2>", ...], ...}. '
        "Each value is a list of up to three repository paths in best-first "
        "order. Use [] when no candidate is plausible. Do not include "
        "anything outside the JSON object."
    )


def _parse_llm_enrich_response(raw_text: str) -> dict[str, list[str]]:
    r"""Pull the candidate map out of an LLM response, robustly.

    Returns ``{target: [paths…]}``. Empty list = the LLM had no plausible
    suggestion for that target. Returns ``{}`` on parse failure (any
    kind) — the enricher is best-effort and never fatal.

    Accepted input shapes:
    * ``{target: [path1, path2, …]}`` — current shape (ranked candidates).
    * ``{target: path}`` — legacy single-best shape (still in the wild).
    * ``{target: null}`` — legacy "no plausible candidate".

    The LLM occasionally wraps JSON in ``\`\`\`json`` fences, so we strip
    those before json.loads().
    """
    import json
    import re

    if not raw_text:
        return {}
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in parsed.items():
        sk = str(key)
        if value is None:
            out[sk] = []
        elif isinstance(value, str) and value.strip():
            out[sk] = [value.strip()]
        elif isinstance(value, list):
            cleaned = [v.strip() for v in value if isinstance(v, str) and v.strip()]
            # Cap at three — anything beyond is noise and bloats the
            # downstream serialised envelope.
            out[sk] = cleaned[:3]
    return out


def _set_llm_skip_reason(envelope: dict, reason: str) -> dict:
    """Attach a ``summary.llm_skip_reason`` so callers can debug why
    enrichment didn't fire (env not set, no sampling, no candidates,
    parse failure, etc.). The envelope shape is left otherwise intact."""
    if not isinstance(envelope, dict):
        return envelope
    summary = envelope.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    summary["llm_skip_reason"] = reason
    envelope["summary"] = summary
    return envelope


def _set_llm_sampling_failure(envelope: dict, started: float, reason: str) -> dict:
    """Record latency and attach a skip reason when LLM sampling fails."""
    elapsed_ms = int((_time.monotonic() - started) * 1000)
    envelope = _set_llm_skip_reason(envelope, reason)
    envelope.setdefault("summary", {})["llm_latency_ms"] = elapsed_ms
    return envelope


def _is_missing_sampling_capability(exc: ValueError) -> bool:
    """Recognize the FastMCP sentinel for clients without sampling support."""
    return str(exc) == "Client does not support sampling"


def _index_repo_paths_for_llm_hints(repo_paths: list[str]) -> tuple[set[str], dict[str, list[str]]]:
    """Build lookup tables that keep LLM hints grounded in repo paths."""
    basename_to_paths: dict[str, list[str]] = {}
    for path in repo_paths:
        basename_to_paths.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    return set(repo_paths), basename_to_paths


def _resolve_repo_grounded_llm_candidate(
    candidate: str,
    valid_paths: set[str],
    basename_to_paths: dict[str, list[str]],
) -> str | None:
    """Return the repo path a candidate proves, or None for hallucinations."""
    if not candidate or "://" in candidate or candidate.startswith("/"):
        return None
    if candidate in valid_paths:
        return candidate
    matches = basename_to_paths.get(candidate.rsplit("/", 1)[-1])
    if not matches:
        return None
    return min(matches, key=len)


def _validate_repo_grounded_llm_candidates(
    candidates: list[str],
    valid_paths: set[str],
    basename_to_paths: dict[str, list[str]],
) -> tuple[list[str], list[dict]]:
    """Separate repo-real LLM candidates from rejected suggestions."""
    accepted: list[str] = []
    rejected: list[dict] = []
    for raw_candidate in candidates:
        resolved = _resolve_repo_grounded_llm_candidate(
            raw_candidate,
            valid_paths,
            basename_to_paths,
        )
        if resolved is None:
            rejected.append({"candidate": raw_candidate, "reason": "not in repo"})
            continue
        accepted.append(resolved)
    return accepted, rejected


def _diagnose_repo_grounded_llm_candidates(
    candidates: list[str] | None,
    valid_paths: set[str],
    basename_to_paths: dict[str, list[str]],
) -> tuple[dict, list[str]]:
    """Explain why an LLM target did or did not earn a repo-grounded hint."""
    if candidates is None:
        return {
            "skip_reason": "target not present in LLM response",
            "candidates_returned": 0,
        }, []

    diagnostic: dict = {
        "candidates_returned": len(candidates),
        "candidates_raw": list(candidates),
    }
    if not candidates:
        diagnostic["skip_reason"] = "LLM returned empty candidate list"
        return diagnostic, []

    accepted, rejected = _validate_repo_grounded_llm_candidates(
        candidates,
        valid_paths,
        basename_to_paths,
    )
    diagnostic["candidates_validated"] = accepted
    if rejected:
        diagnostic["candidates_rejected"] = rejected
    if not accepted:
        diagnostic["skip_reason"] = "all candidates failed validation"
    return diagnostic, accepted


def _attach_repo_grounded_llm_hint(target: dict, accepted: list[str], diagnostic: dict) -> None:
    """Attach the first proven path while preserving ranked alternatives."""
    chosen = accepted[0]
    target["hint"] = {
        "target": chosen,
        "confidence": "MEDIUM",
        "reason": "LLM-suggested semantic match",
        "source": "llm-sampling",
    }
    target["rename_hint"] = chosen
    # Surface the runners-up so callers (CI / agents / verdict UI)
    # can present alternatives without re-asking the LLM.
    if len(accepted) > 1:
        target["llm_alternates"] = accepted[1:]
    diagnostic["chosen"] = chosen


def _apply_repo_grounded_llm_hints(
    unresolved: list[dict],
    suggestions: dict[str, list[str]],
    repo_paths: list[str],
) -> tuple[int, dict[str, dict]]:
    """Apply only LLM suggestions that resolve to repository-real paths."""
    valid_paths, basename_to_paths = _index_repo_paths_for_llm_hints(repo_paths)
    added = 0
    per_target: dict[str, dict] = {}
    for target in unresolved:
        target_name = target["target"]
        diagnostic, accepted = _diagnose_repo_grounded_llm_candidates(
            suggestions.get(target_name),
            valid_paths,
            basename_to_paths,
        )
        if accepted:
            _attach_repo_grounded_llm_hint(target, accepted, diagnostic)
            added += 1
        per_target[target_name] = diagnostic
    return added, per_target


async def _enrich_stale_refs_with_llm_hints(envelope: dict, ctx: _Context | None) -> dict:
    """Add LLM-suggested hints to NONE/LOW-confidence findings, in place.

    Best-effort. Returns the envelope unchanged when enrichment can't
    fire for any reason. When that happens, ``summary.llm_skip_reason``
    is populated so callers (CI / agents) can distinguish "the LLM
    found no matches" from "I never asked the LLM at all":

    * ``ROAM_AI_ENABLED`` not set → ``"ROAM_AI_ENABLED env var not set"``
    * No MCP sampling on this client → ``"MCP context lacks sample()"``
    * Envelope malformed → ``"envelope shape unexpected"``
    * No targets → ``"no findings to enrich"``
    * No ``--with-candidates`` → ``"summary.repo_paths_sample missing"``
    * Nothing to enrich (everything HIGH/MEDIUM already) →
      ``"all findings already have HIGH/MEDIUM hints"``
    * Sampling raised → ``"sampling raised: <exc class>"``
    * No suggestions parsed → ``"LLM response unparseable"``

    On success, attaches a hint with ``source="llm-sampling"``,
    ``confidence="MEDIUM"``, ``reason="LLM-suggested semantic match"``
    to each target it could resolve. ``summary.llm_hints_added`` reports
    the count.
    """
    if os.environ.get("ROAM_AI_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return _set_llm_skip_reason(envelope, "ROAM_AI_ENABLED env var not set")
    if ctx is None or not callable(getattr(ctx, "sample", None)):
        return _set_llm_skip_reason(envelope, "MCP context lacks sample()")
    if not isinstance(envelope, dict):
        return envelope  # Can't even attach a skip reason — bail.
    summary = envelope.get("summary") or {}
    targets = envelope.get("targets") or []
    repo_paths = summary.get("repo_paths_sample") or []
    if not targets:
        return _set_llm_skip_reason(envelope, "no findings to enrich")
    if not repo_paths:
        return _set_llm_skip_reason(envelope, "summary.repo_paths_sample missing")

    # Pick targets that need help: no hint, or LOW/NONE confidence,
    # AND not anchor-only findings (those have their own provider).
    unresolved: list[dict] = []
    for tgt in targets:
        hint = tgt.get("hint") or {}
        if hint.get("confidence") in {"HIGH", "MEDIUM"}:
            continue
        first_source = (tgt.get("sources") or [{}])[0]
        if first_source.get("kind") == "anchor":
            continue
        unresolved.append(tgt)
    if not unresolved:
        return _set_llm_skip_reason(envelope, "all findings already have HIGH/MEDIUM hints")

    repo_paths = list(repo_paths)
    user_prompt = _build_llm_enrich_prompt([t["target"] for t in unresolved], repo_paths)

    started = _time.monotonic()
    try:
        result = await ctx.sample(
            user_prompt,
            system_prompt=_LLM_ENRICH_SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.1,
        )
    except _EXPECTED_SAMPLING_ERRORS as exc:
        return _set_llm_sampling_failure(envelope, started, f"sampling raised: {type(exc).__name__}")
    except ValueError as exc:
        if _is_missing_sampling_capability(exc):
            return _set_llm_sampling_failure(envelope, started, "sampling raised: ValueError")
        raise
    elapsed_ms = int((_time.monotonic() - started) * 1000)

    raw_text = ""
    text_attr = getattr(result, "text", None)
    if isinstance(text_attr, str):
        raw_text = text_attr

    # Capture diagnostic metadata regardless of parse success — operators
    # tuning the enricher want to see latency / response size before
    # they see hint counts.
    summary["llm_latency_ms"] = elapsed_ms
    summary["llm_response_chars"] = len(raw_text)
    summary["llm_targets_asked"] = len(unresolved)
    summary["llm_prompt_chars"] = len(user_prompt)
    envelope["summary"] = summary

    suggestions = _parse_llm_enrich_response(raw_text)
    if not suggestions:
        return _set_llm_skip_reason(envelope, "LLM response unparseable")

    added, per_target = _apply_repo_grounded_llm_hints(unresolved, suggestions, repo_paths)

    summary["llm_hints_added"] = added
    summary["llm_per_target"] = per_target
    if added:
        # Re-derive ``by_confidence`` so the count reflects the new hints.
        new_by_confidence = Counter((t.get("hint") or {}).get("confidence", "NONE") for t in targets)
        summary["by_confidence"] = dict(new_by_confidence)
    envelope["summary"] = summary
    return envelope


@_tool(
    name="roam_stale_refs",
    description="Find dangling file references — markdown links / HTML href-src / backtick paths whose target is missing. v12.48 adds anchor validation, confidence-tagged hints, --diff branch filter, --fix preview/apply, and --sort-by ranking. Set enrich_with_llm=True for LLM-sampled hints on findings the deterministic providers couldn't resolve.",
    output_schema=_SCHEMA_STALE_REFS,
)
async def roam_stale_refs(
    limit: int = 20,
    rename_hint: bool = True,
    kind: str = "",
    ignore: str = "",
    ignore_target: str = "",
    check_absolute_routes: bool = False,
    no_anchors: bool = False,
    diff: str = "",
    sort_by: str = "priority",
    fix: str = "",
    by_file: bool = False,
    enrich_with_llm: bool = False,
    root: str = ".",
    ctx: _Context | None = None,
) -> dict:
    """Detect references to files that no longer exist.

    WHEN TO USE: docs hygiene, post-refactor / post-rename cleanup, CI gate
    against broken doc links. Pure filesystem scan — no index required.
    Catches what symbol-graph commands miss: prose mentions of paths,
    markdown links, HTML href/src, backtick file references. Also
    validates ``#anchor`` fragments — refs to headers that no longer
    exist in target files surface as ``kind=anchor`` findings.

    Parameters
    ----------
    limit:
        Maximum number of missing targets returned (default 20).
    rename_hint:
        If True, surface a confidence-tagged rename hint per target.
        Hints carry ``{target, confidence, reason, source}`` —
        confidence is HIGH (deterministic git-history rename or unique
        basename match in shared subtree), MEDIUM (single match
        elsewhere), or LOW (multiple candidates, similarity-ranked).
    kind:
        Comma-separated filter restricting reference kinds. Choices:
        ``md_inline``, ``md_reference``, ``html_attr``, ``backtick``,
        ``anchor``. Empty (default) means all kinds.
    ignore:
        Comma-separated globs of source files to skip (e.g.
        ``CHANGELOG.md,docs/legacy/*.md``). Suppresses historical
        documents that intentionally mention deleted files.
    ignore_target:
        Comma-separated globs of missing-target paths to suppress
        (e.g. ``AGENTS.md,docs/old/*``).
    check_absolute_routes:
        If True, also check absolute-path URLs without file extensions
        (``href="/setup"``). Off by default — those are usually
        static-site router paths, not file references.
    no_anchors:
        If True, skip markdown anchor validation entirely. By default,
        ``[deploy](docs/cd.md#cloudflare)`` is flagged when the file
        exists but ``#cloudflare`` doesn't.
    diff:
        Branch-diff filter. Only report findings new in the current
        branch since merge-base with this ref (sourced from changed
        files OR targeting deleted files). Pass ``"auto"`` to let roam
        pick the base (origin/main → main → master → HEAD~1), or any
        valid git ref / SHA. Empty (default) disables the filter.
        Makes ``roam_stale_refs`` practical as a per-PR check.
    sort_by:
        ``priority`` (default — importance × recency × ref count),
        ``ref-count`` (most-referenced first), or ``alpha`` (target
        path alphabetical).
    fix:
        ``preview`` to print a unified diff of HIGH-confidence
        rewrites; ``apply`` to write them to disk. Empty (default)
        disables. Only edits lines with a single unambiguous stale ref.
    by_file:
        If True, group findings by source file instead of by missing
        target — useful when fixing one document at a time.
    enrich_with_llm:
        If True AND ``ROAM_AI_ENABLED=1`` is set in the environment AND
        the MCP client supports sampling, ask the calling agent's LLM
        to suggest semantic matches for findings the deterministic
        providers (git-history / basename / symbol-graph) couldn't
        resolve. LLM-sourced hints are tagged ``confidence=MEDIUM``
        with ``source=llm-sampling`` and never auto-fixed. Off by
        default (preserves the "no source code leaves your machine"
        stance unless the user opts in).
    root:
        Working directory (project root).

    Returns: targets list with per-target source locations, confidence-
    tagged rename hints, and a verdict / counts summary including
    ``scan_seconds`` and (when --diff is active) ``diff_base``. When
    enrichment fired, ``summary.llm_hints_added`` reports the count.
    """
    args = ["stale-refs", "--limit", str(limit), "--sort-by", sort_by]
    if not rename_hint:
        args.append("--no-rename-hint")
    if kind:
        for k in (k.strip() for k in kind.split(",") if k.strip()):
            args.extend(["--kind", k])
    if ignore:
        for pat in (p.strip() for p in ignore.split(",") if p.strip()):
            args.extend(["--ignore", pat])
    if ignore_target:
        for pat in (p.strip() for p in ignore_target.split(",") if p.strip()):
            args.extend(["--ignore-target", pat])
    if check_absolute_routes:
        args.append("--check-absolute-routes")
    if no_anchors:
        args.append("--no-anchors")
    if diff:
        # ``"auto"`` is our convention for "use the bare ``--diff`` flag";
        # any other value is passed through as the base ref / SHA.
        args.extend(["--diff", "" if diff == "auto" else diff])
    if fix:
        args.extend(["--fix", fix])
    if by_file:
        args.append("--by-file")
    if enrich_with_llm:
        # The CLI flag exposes the candidate path sample the enricher needs.
        args.append("--with-candidates")
    envelope = _run_roam(args, root)
    if enrich_with_llm:
        envelope = await _enrich_stale_refs_with_llm_hints(envelope, ctx)
    return envelope


@_tool(
    name="roam_suggest_refactoring",
    description="Rank proactive refactoring candidates using complexity/coupling/churn/smells.",
)
def roam_suggest_refactoring(limit: int = 20, min_score: int = 45, root: str = ".") -> dict:
    """Suggest high-ROI refactoring candidates.

    WHEN TO USE: proactive refactoring triage. Ranks symbols by a weighted
    blend of complexity, coupling, churn, smell density, coverage gaps,
    and structural debt.

    Parameters
    ----------
    limit:
        Maximum number of recommendations (default 20).
    min_score:
        Minimum recommendation score threshold (0-100, default 45).
    root:
        Working directory (project root).

    Returns: ranked recommendations with score, effort bucket (S/M/L),
    suggested action, reasons, and signal breakdown.
    """
    args = ["suggest-refactoring", "--limit", str(limit), "--min-score", str(min_score)]
    return _run_roam(args, root)


@_tool(
    name="roam_plan_refactor",
    description="Build an ordered refactor plan for one symbol using risk/test/simulation context.",
)
def roam_plan_refactor(
    symbol: str, operation: str = "auto", target_file: str = "", max_steps: int = 7, root: str = "."
) -> dict:
    """Build a compound refactoring plan for a symbol.

    WHEN TO USE: before making structural edits to a high-impact symbol.
    Composes callers/callees, blast radius, test gaps, layer risks, and
    simulation previews into an executable plan.

    Parameters
    ----------
    symbol:
        Target symbol name or qualified name.
    operation:
        "auto", "extract", or "move" (default auto).
    target_file:
        Optional explicit target file path for simulation preview.
    max_steps:
        Maximum number of plan steps returned (default 7).
    root:
        Working directory (project root).

    Returns: ordered plan steps, selected strategy preview, risk factors,
    and verification commands.
    """
    args = ["plan-refactor", symbol, "--operation", operation, "--max-steps", str(max_steps)]
    if target_file:
        args.extend(["--target-file", target_file])
    return _run_roam(args, root)


# ===================================================================
# Backend analysis -- framework-specific issue detection
# ===================================================================


@_tool(
    name="roam_n1",
    description="Detect N+1 I/O patterns in ORM code (Laravel/Django/Rails/SQLAlchemy/JPA).",
)
def roam_n1(confidence: str = "medium", verbose: bool = False, root: str = ".") -> dict:
    """Detect implicit N+1 I/O patterns in ORM code.

    WHEN TO USE: to find hidden N+1 query problems -- computed properties
    on data classes that trigger lazy-loaded queries during serialization.
    Supports Laravel/Eloquent, Django, Rails, SQLAlchemy, JPA/Hibernate.

    Parameters
    ----------
    confidence:
        Filter: "low", "medium", "high" (default medium).
    verbose:
        Include call chain traces from property to I/O.
    root:
        Working directory (project root).

    Returns: findings with model, property, I/O operation, collection
    contexts, eager-loading status, severity, suggested fix.
    """
    args = ["n1"]
    if confidence != "medium":
        args.extend(["--confidence", confidence])
    if verbose:
        args.append("--verbose")
    return _run_roam(args, root)


@_tool(
    name="roam_auth_gaps",
    description=(
        "Find endpoints lacking auth / authorization checks ranked by "
        "confidence. Triggers: 'which routes are unprotected?', 'show me "
        "auth gaps', 'audit handler protection'. Pair with roam_taint "
        "for taint-source reachability over the unprotected surfaces."
    ),
)
def roam_auth_gaps(
    routes_only: bool = False,
    controllers_only: bool = False,
    min_confidence: str = "medium",
    root: str = ".",
) -> dict:
    """Find endpoints missing authentication or authorization.

    WHEN TO USE: security audit -- detects routes outside auth middleware,
    controllers without authorize() checks. Supports Laravel, Django,
    Rails, Express.

    Parameters
    ----------
    routes_only:
        Only check route definitions.
    controllers_only:
        Only check controller authorization.
    min_confidence:
        Minimum: "low", "medium", "high" (default medium).
    root:
        Working directory (project root).

    Returns: findings with endpoint, location, missing protection type,
    severity (CRITICAL/HIGH/MEDIUM), suggested fix. Summary by severity.
    """
    args = ["auth-gaps"]
    if routes_only:
        args.append("--routes-only")
    if controllers_only:
        args.append("--controllers-only")
    if min_confidence != "medium":
        args.extend(["--min-confidence", min_confidence])
    return _run_roam(args, root)


@_tool(
    name="roam_over_fetch",
    description="Models serializing too many fields (data over-exposure risk).",
)
def roam_over_fetch(threshold: int = 10, confidence: str = "medium", root: str = ".") -> dict:
    """Detect models serializing too many fields (data over-exposure).

    WHEN TO USE: to find API responses leaking too many fields. Detects
    large $fillable without $hidden, direct controller returns bypassing
    Resources, poor exposed-to-hidden ratio.

    Parameters
    ----------
    threshold:
        Minimum exposed field count to flag (default 10).
    confidence:
        Filter: "low", "medium", "high" (default medium).
    root:
        Working directory (project root).

    Returns: findings with model, exposed/hidden counts, ratio,
    serialization method, severity, suggested fix.
    """
    args = ["over-fetch", "--threshold", str(threshold)]
    if confidence != "medium":
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@_tool(
    name="roam_missing_index",
    description=(
        "Detect queries hitting non-indexed columns flagged as slow-query "
        "risks. Triggers: 'find slow queries', 'audit database indexes', "
        "'where are the N+1 candidates?'. Pair with roam_n1 for "
        "per-property iteration patterns."
    ),
)
def roam_missing_index(table: str = "", confidence: str = "medium", root: str = ".") -> dict:
    """Find queries on non-indexed columns (potential slow queries).

    WHEN TO USE: to detect queries that will table-scan instead of using
    indexes. Cross-references WHERE/ORDER BY/foreign keys against
    migration-defined indexes. Supports Laravel, Django, Rails, Alembic.

    Parameters
    ----------
    table:
        Only check queries against this table.
    confidence:
        Filter: "low", "medium", "high" (default medium).
    root:
        Working directory (project root).

    Returns: findings with table, column, query type, existing indexes,
    severity, suggested index DDL.
    """
    args = ["missing-index"]
    if table:
        args.extend(["--table", table])
    if confidence != "medium":
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@_tool(
    name="roam_orphan_routes",
    description=(
        "Find backend routes lacking a frontend consumer — the dead-endpoint "
        "surface. Triggers: 'which routes can we delete?', 'find unused "
        "endpoints', 'audit API surface coverage'. Pair with roam_dead_code "
        "for symbol-level dead-export detection."
    ),
)
def roam_orphan_routes(limit: int = 50, confidence: str = "medium", root: str = ".") -> dict:
    """Detect backend routes with no frontend consumer (dead endpoints).

    WHEN TO USE: to find API endpoints defined but never called. Parses
    route files, searches frontend for API call references.

    Parameters
    ----------
    limit:
        Maximum findings (default 50).
    confidence:
        Filter: "low", "medium", "high" (default medium).
    root:
        Working directory (project root).

    Returns: findings with route path, HTTP method, controller, location,
    confidence, suggested action. Summary with safe removal candidates.
    """
    args = ["orphan-routes", "-n", str(limit)]
    if confidence != "medium":
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@_tool(
    name="roam_migration_safety",
    description=(
        "Detect non-idempotent database migrations unsafe to re-run. "
        "Triggers: 'audit migration safety', 'find non-idempotent migrations', "
        "'which DDL would break on replay?'. Pair with roam_tx_boundaries "
        "for transaction-correctness analysis."
    ),
)
def roam_migration_safety(limit: int = 50, include_archive: bool = False, root: str = ".") -> dict:
    """Detect non-idempotent database migrations (unsafe for re-run).

    WHEN TO USE: to find migrations that will fail or corrupt data if run
    twice. Detects missing hasTable/hasColumn guards, raw SQL without
    IF NOT EXISTS.

    Parameters
    ----------
    limit:
        Maximum findings (default 50).
    include_archive:
        Check old migrations too (>6 months).
    root:
        Working directory (project root).

    Returns: findings with migration file, operation type, missing guard,
    severity, risk explanation, suggested fix with guard code.
    """
    args = ["migration-safety", "-n", str(limit)]
    if include_archive:
        args.append("--include-archive")
    return _run_roam(args, root)


@_tool(
    name="roam_api_drift",
    description=(
        "Detect field drift between Laravel/PHP models and TypeScript interfaces. "
        "Triggers: 'where do API contracts diverge?', 'find drift between "
        "PHP $fillable fields and TypeScript types', 'audit frontend API types'. "
        "Pair with roam_endpoints for full route inventory."
    ),
)
def roam_api_drift(model: str = "", confidence: str = "all", root: str = ".") -> dict:
    """Detect field drift between backend models and frontend interfaces.

    WHEN TO USE: to find drift between PHP $fillable/$appends and
    TypeScript interface properties. Detects missing fields, extra fields,
    and likely naming mismatches. Auto-converts snake_case/camelCase.

    Parameters
    ----------
    model:
        Only check this model. Empty = check all.
    confidence:
        Filter: "all", "low", "medium", "high" (default all).
    root:
        Working directory (project root).

    Returns: findings with model, interface, drift type, field,
    confidence, and suggested fix context.
    """
    args = ["api-drift"]
    if model:
        args.extend(["--model", model])
    if confidence != "all":
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@_tool(name="roam_simulate_departure", description="Simulate knowledge loss if a developer leaves the team.")  # W459
def roam_simulate_departure(developer: str, root: str = ".") -> dict:
    """Simulate knowledge loss if a developer leaves the team.

    WHEN TO USE: to assess risk before a team member departs. Shows files,
    symbols, and modules that would become orphaned or under-owned. Combines
    git blame ownership, CODEOWNERS, PageRank importance, and cluster impact.

    Parameters
    ----------
    developer:
        Developer name or email (as it appears in git blame).
    root:
        Working directory (project root).

    Returns: verdict, risk-categorized files (critical/high/medium),
    key symbols at risk with PageRank, affected module count,
    and actionable recommendations.
    """
    args = ["simulate-departure", developer]
    return _run_roam(args, root)


@_tool(name="roam_ai_ratio", description="Estimate AI-generated code percentage from git commit heuristics.")  # W459
def roam_ai_ratio(since: int = 90, root: str = ".") -> dict:
    """Estimate AI-generated code percentage from git commit heuristics.

    WHEN TO USE: Call this to understand how much of the codebase was
    likely written or co-authored by AI tools. Uses Gini coefficient,
    burst-addition detection, co-author tags, comment density anomalies,
    and temporal patterns.

    Parameters
    ----------
    since:
        Analyze commits from last N days (default: 90).
    root:
        Working directory (project root).

    Returns: AI ratio (0-1), confidence level, per-signal scores,
    top AI-likely files, and trend direction.
    """
    args = ["ai-ratio", "--since", str(since)]
    return _run_roam(args, root)


@_tool(
    name="roam_syntax_check",
    description="Tree-sitter syntax validation. Finds ERROR/MISSING AST nodes. No index needed.",
    output_schema=_make_schema(
        {
            "total_files": {"type": "integer"},
            "total_errors": {"type": "integer"},
            "clean": {"type": "boolean"},
        },
        files={"type": "array", "items": {"type": "object"}},
    ),
)
def roam_syntax_check(paths: str = "", changed: bool = False, root: str = ".") -> dict:
    """Check files for tree-sitter syntax errors (ERROR/MISSING AST nodes).

    WHEN TO USE: in multi-agent workflows to validate that a Worker agent
    did not corrupt file syntax. Works WITHOUT a roam index -- parses
    files directly with tree-sitter.

    WHEN NOT TO USE: for full code quality analysis use roam_health instead.

    Parameters
    ----------
    paths:
        Space-separated file paths to check.
        Empty with changed=True = check git-changed files.
    changed:
        If True, check only git-changed files (unstaged + staged + untracked).
    root:
        Working directory (project root).

    Returns: per-file error list with line, column, node_type, and text.
    Exit code 5 if any syntax errors found, 0 if clean.
    """
    args = ["syntax-check"]
    if changed:
        args.append("--changed")
    if paths:
        args.extend(paths.split())
    return _run_roam(args, root)


@_tool(
    name="roam_doctor",
    description="Setup diagnostics: Python version, tree-sitter, git, index existence, freshness, SQLite.",
    output_schema=_make_schema(
        {
            "total": {"type": "integer"},
            "passed": {"type": "integer"},
            "failed": {"type": "integer"},
            "all_passed": {"type": "boolean"},
        },
        checks={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "detail": {"type": "string"},
                },
            },
        },
        failed_checks={"type": "array", "items": {"type": "object"}},
    ),
)
def roam_doctor(root: str = ".") -> dict:
    """Diagnose environment setup: Python, dependencies, index state.

    WHEN TO USE: when troubleshooting setup issues, onboarding a new
    developer, or when an agent reports that roam commands are failing.
    Checks Python version, tree-sitter, tree-sitter-language-pack, git,
    networkx, index existence, index freshness, and SQLite connectivity.

    Returns: per-check PASS/FAIL results and a summary verdict.
    Exit code 1 if any check fails, 0 if all pass.
    """
    return _run_roam(["doctor"], root)


@_tool(
    name="roam_codeowners",
    description="CODEOWNERS coverage, ownership distribution, unowned files, drift detection.",
)
def roam_codeowners(unowned: bool = False, owner: str = "", root: str = ".") -> dict:
    """Analyze CODEOWNERS coverage, ownership distribution, and unowned files.

    WHEN TO USE: to understand code ownership from CODEOWNERS file --
    who owns what, coverage gaps, unowned critical files, drift between
    declared and actual contributors.

    Parameters
    ----------
    unowned:
        If True, show only unowned files sorted by importance (PageRank).
    owner:
        Filter to show files owned by a specific owner (e.g. "@backend-team").
    root:
        Working directory (project root).

    Returns: coverage percentage, owner distribution with key areas,
    unowned files ranked by PageRank, ownership drift warnings.
    """
    args = ["codeowners"]
    if unowned:
        args.append("--unowned")
    if owner:
        args.extend(["--owner", owner])
    return _run_roam(args, root)


@_tool(
    name="roam_drift",
    description="Ownership drift detection: declared CODEOWNERS vs actual time-decayed contributors.",
)
def roam_drift(threshold: float = 0.5, root: str = ".") -> dict:
    """Detect ownership drift using time-decayed blame scoring.

    WHEN TO USE: to find files where the declared CODEOWNERS no longer
    match who actually maintains the code, weighted by recency.

    Parameters
    ----------
    threshold:
        Drift threshold 0-1 (default 0.5). Higher = only severe drift.
    root:
        Working directory (project root).

    Returns: files with drift, drift scores, actual top contributors,
    recommendations for CODEOWNERS updates.
    """
    args = ["drift", "--threshold", str(threshold)]
    return _run_roam(args, root)


@_tool(
    name="roam_dev_profile",
    description="Developer behavioral profiling: commit time patterns, change scatter (Gini), burst detection.",
)
def roam_dev_profile(author: str = "", days: int = 90, limit: int = 20, root: str = ".") -> dict:
    """Profile developer commit behavior for PR risk scoring.

    WHEN TO USE: to assess whether a PR author's coding patterns indicate
    elevated risk (late-night commits, broad unfocused changes, burst coding).
    Useful before approving large PRs or during risk triage.

    Parameters
    ----------
    author:
        Developer email or substring to filter (empty = all authors).
    days:
        Lookback window in days (default 90).
    limit:
        Max authors to profile (default 20).
    root:
        Working directory (project root).

    Returns: per-author behavioral profiles with risk scores, hour/day
    distributions, Gini scatter coefficient, burst scores, session patterns,
    and top directories. Higher risk_score = more anomalous patterns.
    """
    args = ["dev-profile"]
    if author:
        args.append(author)
    args.extend(["--days", str(days), "--limit", str(limit)])
    return _run_roam(args, root)


@_tool(
    name="roam_partition",
    description="Multi-agent work partitioning: split codebase into independent work zones.",
)
def roam_partition(n_agents: int = 4, output_format: str = "text", root: str = ".") -> dict:
    """Partition codebase into independent work zones for parallel agents.

    WHEN TO USE: when splitting work across multiple AI agents or developers
    who need to work in parallel without conflicts.

    Parameters
    ----------
    n_agents:
        Number of agents/partitions (default 4).
    output_format:
        Output format: text, json, or claude-teams.
    root:
        Working directory (project root).

    Returns: partitions with file lists, conflict scores, coupling boundaries,
    and suggested ownership assignments.
    """
    args = ["partition", "--n-agents", str(n_agents)]
    if output_format != "text":
        args.extend(["--format", output_format])
    return _run_roam(args, root)


@_tool(
    name="roam_spectral",
    description="Spectral bisection: Fiedler vector partition tree and modularity gap.",
)
def roam_spectral(depth: int = 3, compare: bool = False, gap_only: bool = False, k: int = 0, root: str = ".") -> dict:
    """Spectral bisection using the Fiedler vector as an alternative to Louvain.

    Parameters
    ----------
    depth:
        Maximum recursion depth for bisection (default 3).
    compare:
        If True, also run Louvain and report Adjusted Rand Index.
    gap_only:
        If True, only return the spectral gap metric.
    k:
        Number of communities to detect (0 = auto-detect from gap).
    root:
        Project root directory.

    Returns: spectral gap, verdict, partition tree, optional Louvain comparison.
    """
    args = ["spectral", "--depth", str(depth)]
    if compare:
        args.append("--compare")
    if gap_only:
        args.append("--gap-only")
    if k > 0:
        args.extend(["--k", str(k)])
    return _run_roam(args, root)


@_tool(
    name="roam_affected",
    description="Monorepo impact analysis: find all affected packages/modules from changes.",
)
def roam_affected(base: str = "HEAD~1", depth: int = 3, changed: str = "", root: str = ".") -> dict:
    """Analyze which packages/modules are affected by recent changes.

    WHEN TO USE: in monorepos or large codebases, to determine what needs
    rebuilding, retesting, or redeploying after changes.

    Parameters
    ----------
    base:
        Git base ref for comparison (default HEAD~1).
    depth:
        Maximum transitive dependency depth (default 3).
    changed:
        Comma-separated list of changed files (overrides git diff).
    root:
        Working directory (project root).

    Returns: affected files/modules with DIRECT/TRANSITIVE classification,
    impact scores, and rebuild recommendations.
    """
    args = ["affected", "--base", base, "--depth", str(depth)]
    if changed:
        args.extend(["--changed", changed])
    return _run_roam(args, root)


@_tool(
    name="roam_semantic_diff",
    description="Structural change summary: what symbols were added/removed/modified.",
)
def roam_semantic_diff(base: str = "HEAD~1", root: str = ".") -> dict:
    """Produce a semantic diff showing structural changes between commits.

    WHEN TO USE: when you need a high-level summary of what changed structurally
    (added/removed/modified functions, classes, imports) rather than line-level diffs.

    Parameters
    ----------
    base:
        Git base ref for comparison (default HEAD~1).
    root:
        Working directory (project root).

    Returns: added/removed/modified symbols, import changes, and a
    human-readable structural change summary.
    """
    args = ["semantic-diff", "--base", base]
    return _run_roam(args, root)


@_tool(
    name="roam_trends",
    description="Historical metric tracking: record and query health metric trends over time.",
)
def roam_trends(record: bool = False, days: int = 30, metric: str = "", root: str = ".") -> dict:
    """Track and query health metric trends over time.

    WHEN TO USE: to see how codebase health metrics have changed over time,
    or to record a new snapshot of current metrics.

    Parameters
    ----------
    record:
        If True, record current metrics as a new snapshot.
    days:
        Number of days of history to show (default 30).
    metric:
        Filter to a specific metric name.
    root:
        Working directory (project root).

    Returns: metric snapshots with timestamps, sparkline trends, and
    improvement/regression indicators.
    """
    args = ["trends"]
    if record:
        args.append("--record")
    if days != 30:
        args.extend(["--days", str(days)])
    if metric:
        args.extend(["--metric", metric])
    return _run_roam(args, root)


@_tool(
    name="roam_reset",
    description="Delete index DB and rebuild from scratch. Requires force=True. Recovery for corrupted indexes.",
    output_schema=_make_schema(
        {"removed": {"type": "boolean"}, "db_path": {"type": "string"}},
    ),
    # W365: deletes .roam/index.db. The roam_capability registry already
    # flags this as destructive=True; without this kwarg, _TOOL_METADATA
    # silently disagreed with the capability registry AND under-stated the
    # blast radius to MCP clients that route on destructiveHint.
    # idempotent stays True (default): per the MCP spec, idempotent =
    # "repeating with same args adds no further effect" — once the DB is
    # gone, a second reset is a no-op delete.
    destructive=True,
    read_only=False,
)
def roam_reset(force: bool = False, root: str = ".") -> dict:
    """Delete the roam index DB and rebuild from scratch.

    WHEN TO USE: when the index is corrupted, out of sync, or producing
    wrong results. Equivalent to `rm .roam/index.db && roam init`.
    Use roam_clean first for lighter cleanup (removes orphaned files only).

    Parameters
    ----------
    force:
        Must be True to confirm the destructive reset (required safety guard).
    root:
        Working directory (project root).

    Returns: verdict with removed/db_path fields.
    Exit code 2 if force=False (aborted), 0 on success.
    """
    args = ["reset"]
    if force:
        args.append("--force")
    if root != ".":
        args.extend(["--root", root])
    return _run_roam(args, root)


@_tool(
    name="roam_clean",
    description="Remove orphaned index entries (files deleted from disk) without full rebuild.",
    output_schema=_make_schema(
        {
            "files_removed": {"type": "integer"},
            "symbols_removed": {"type": "integer"},
            "edges_removed": {"type": "integer"},
        },
        orphaned_paths={"type": "array", "items": {"type": "string"}},
    ),
    # W365: removes file/symbol/edge rows from the index DB. The capability
    # registry already flags this as destructive=True; this kwarg keeps the
    # _TOOL_METADATA / ToolAnnotations on-the-wire view in lockstep so MCP
    # clients routing on destructiveHint see the right blast radius.
    # idempotent stays True (default): repeating roam_clean has no further
    # effect once the orphans are gone (MCP-spec idempotent semantics).
    destructive=True,
    read_only=False,
)
def roam_clean(root: str = ".") -> dict:
    """Remove stale/orphaned entries from the index without a full rebuild.

    WHEN TO USE: after deleting or moving files outside of git tracking.
    Faster than roam_reset -- only removes file records no longer on disk
    plus any dangling symbol edges, then optionally compacts the DB.

    Parameters
    ----------
    root:
        Working directory (project root).

    Returns: counts of files/symbols/edges removed.
    """
    return _run_roam(["clean"], root)


# ---------------------------------------------------------------------------
# roam_catalog: machine-readable list of all registered tools
# ---------------------------------------------------------------------------


@_tool(
    name="roam_catalog",
    description=(
        "Return the full machine-readable list of every roam MCP tool currently "
        "registered, including title, description, and capability flags "
        "(core / read_only / destructive). Use this once at session start "
        "to discover what's available without enumerating tools."
    ),
    output_schema=_make_schema(
        {
            "tool_count": {"type": "integer"},
            "core_count": {"type": "integer"},
        },
        tools={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "when_to_use": {"type": "string"},
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "core": {"type": "boolean"},
                    "read_only": {"type": "boolean"},
                    "destructive": {"type": "boolean"},
                    "version": {"type": "string"},
                },
            },
        },
        preset={"type": "string"},
    ),
)
def roam_catalog(root: str = ".") -> dict:
    """List every registered MCP tool with capability metadata.

    WHEN TO USE: at the start of a long session, before the agent picks
    which tools to call. Replaces enumerating ``list_tools`` and parsing
    each one — the catalog is one round-trip.

    Returns
    -------
    {
      "summary": {"verdict": "...", "tool_count": N, "core_count": M},
      "tools": [{name, title, description, core, read_only, destructive}, ...],
      "preset": "max" | "core" | ...
    }
    """
    tools = sorted(_TOOL_METADATA.values(), key=lambda t: (not t["core"], t["name"]))
    core_count = sum(1 for t in tools if t["core"])
    return {
        "summary": {
            "verdict": f"{len(tools)} tools registered ({core_count} core)",
            "tool_count": len(tools),
            "core_count": core_count,
        },
        "tools": tools,
        "preset": _ACTIVE_PRESET,
    }


# ---------------------------------------------------------------------------
# five MCP wrappers for previously CLI-only agent signals
# ---------------------------------------------------------------------------


@_tool(
    name="roam_alerts",
    description="Active health alerts: thresholds breached on tangle, complexity, churn, or coverage.",
    # W1312: redundant `output_schema=_ENVELOPE_SCHEMA` dropped — FastMCP
    # falls back to the envelope schema by default, so the explicit kwarg
    # is byte-equivalent to omission.
)
def roam_alerts(root: str = ".") -> dict:
    """Active health alerts. Call this to know what to act on RIGHT NOW.

    Reads the configured thresholds from .roam/config.json and returns
    every metric currently breaching a threshold (tangle ratio,
    complexity, churn, coverage drop).

    >>> roam alerts
    """
    return _run_roam(["alerts"], root)


@_tool(
    name="roam_session_metrics",
    description=(
        "Local-only telemetry: per-tool invocation counts grouped by "
        "outcome (success / rate_limited / error). Helps answer "
        '"which tools are agents actually using?" and "which '
        'tools are dead weight?". Never phones home — counters '
        "live in the MCP server process and reset on restart."
    ),
)
def roam_session_metrics(root: str = ".") -> dict:
    """Return per-tool invocation counts since the MCP server started.

    WHEN TO USE: at end-of-session or during dogfood to inspect which
    tools were exercised and how. Pairs with the architect's-correction
    plan from value-capture: tools at <0.1% over 30 days become
    deprecation candidates.

    Returns
    -------
    dict
        ``invocations``: ``{tool_name: {outcome: count}}`` (only tools
        called appear); ``concurrency``: snapshot of the
        backpressure state (max_concurrent, in_flight, busy_responses_total).
    """
    from roam.mcp_extras.concurrency import metrics as concurrency_metrics
    from roam.mcp_extras.concurrency import tool_invocation_summary
    from roam.output.formatter import json_envelope, to_json

    invocations = tool_invocation_summary()
    concurrency = concurrency_metrics()

    total_calls = sum(sum(v.values()) for v in invocations.values())
    distinct_tools = len(invocations)
    # ``command_error_count`` counts tools that raised an EXCEPTION (the
    # ``"error"`` outcome bucket from ``record_tool_outcome``). This is
    # DISTINCT from ``partial_success_count`` below — a tool can return
    # ``summary.partial_success: true`` (e.g. one of four compound
    # subcommands failed) and still record ``outcome="success"`` because
    # the wrapper didn't raise. The previous ``error_count`` field
    # conflated the two and made compound failures invisible to agents
    # reading the session report.
    command_error_count = sum(v.get("error", 0) for v in invocations.values())
    rate_limited_count = sum(v.get("rate_limited", 0) for v in invocations.values())
    partial_success_count = _session_partial_success_count

    envelope = json_envelope(
        "session-metrics",
        summary={
            "verdict": (
                f"{distinct_tools} distinct tool(s) exercised, "
                f"{total_calls} total call(s), {command_error_count} exception(s), "
                f"{partial_success_count} partial-success envelope(s), "
                f"{rate_limited_count} rate-limited"
            ),
            "distinct_tools": distinct_tools,
            "total_calls": total_calls,
            # NOTE (Fix E): ``error_count`` was renamed to
            # ``command_error_count`` to disambiguate from the new
            # ``partial_success_count`` field. Both are preserved here
            # to give agents a backward-compatible read path while we
            # roll the new name out across the dogfood corpus.
            "command_error_count": command_error_count,
            "partial_success_count": partial_success_count,
            # Legacy alias — agents reading the old field still work.
            "error_count": command_error_count,
            "rate_limited_count": rate_limited_count,
        },
        invocations=invocations,
        concurrency=concurrency,
    )
    # MCP wrappers return parsed dicts directly; serialise-then-parse
    # keeps the shape consistent with _run_roam outputs.
    import json as _json

    return _json.loads(to_json(envelope))


@_tool(
    name="roam_timeline",
    description="Chronological commits that touched the file owning a symbol — author, date, lines added/removed.",
    # Wave B4 (W767): specialised schema. 7 summary fields + commits[] +
    # authors{} now strict-typed; ``required`` narrowed to ``verdict`` +
    # ``commit_count`` (only fields emitted on EVERY branch, including
    # the symbol-not-found path).
    output_schema=_SCHEMA_TIMELINE,
)
def roam_timeline(symbol: str, limit: int = 20, root: str = ".") -> dict:
    """Show commit history for the file containing a symbol.

    WHEN TO USE: before refactoring a hub function — see who's been
    active and how often the file changes.

    >>> roam timeline ensure_index --limit 10
    """
    args = ["timeline", symbol, "--limit", str(limit)]
    return _run_roam(args, root)


@_tool(
    name="roam_test_impact",
    description="Tests transitively reachable from changed symbols — sharper scope than affected_tests.",
    # Wave B4 (W767): specialised schema. The high-leverage agent-input
    # envelope (which tests to run after a change) now strict-typed:
    # ``tests[]`` items require ``file`` + ``reach_count`` (ranked-by-
    # reach contract). ``required`` covers ``verdict`` + ``count``
    # because both are emitted on EVERY branch.
    output_schema=_SCHEMA_TEST_IMPACT,
)
def roam_test_impact(commit_range: str = "", max_hops: int = 5, root: str = ".") -> dict:
    """Tests reachable from changed symbols within N hops.

    WHEN TO USE: after a commit (or when staging) — pick which tests
    to run. Walks reverse call graph from each changed symbol.

    >>> roam test-impact HEAD~3
    """
    args = ["test-impact"]
    if commit_range:
        args.append(commit_range)
    args.extend(["--max-hops", str(max_hops)])
    return _run_roam(args, root)


@_tool(
    name="roam_disambiguate",
    description="List every symbol matching a name with file/line/kind/signature/PageRank — pick the right overload.",
    # W1312: redundant `output_schema=_ENVELOPE_SCHEMA` dropped — generic
    # `summary{verdict, count}` + `matches[]` envelope, no
    # command-specific shape worth declaring.
)
def roam_disambiguate(symbol: str, limit: int = 20, root: str = ".") -> dict:
    """List every symbol matching a name with disambiguators.

    WHEN TO USE: when search returns multiple matches and you need to
    pick the right one. Saves an agent from picking the wrong overload.

    Fix D: legacy alias ``name`` is still accepted.

    >>> roam disambiguate handle_login
    """
    args = ["disambiguate", symbol, "--limit", str(limit)]
    return _run_roam(args, root)


@_tool(
    name="roam_why_fail",
    description="Triage a failing test/symbol: recently-changed symbols transitively reachable from it.",
    # W1312: redundant `output_schema=_ENVELOPE_SCHEMA` dropped — envelope
    # is `summary{verdict, suspect_count, max_hops, days}` + `suspects[]`,
    # generic enough that the default envelope schema covers it.
)
def roam_why_fail(symbol: str, days: int = 14, max_hops: int = 5, limit: int = 10, root: str = ".") -> dict:
    """Triage a failing test by surfacing recently-changed reachable symbols.

    WHEN TO USE: a test just started failing — what changed?
    Combines BFS reach with git recency to rank suspects.

    Parameters
    ----------
    symbol:
        Failing test symbol or file. W430/Fix-D canonical; legacy
        ``target=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning.

    >>> roam why-fail tests/test_login.py --days 7
    """
    args = ["why-fail", symbol, "--days", str(days), "--max-hops", str(max_hops), "--limit", str(limit)]
    return _run_roam(args, root)


@_tool(
    name="roam_evidence_doctor",
    description=(
        "Diagnose a ChangeEvidence packet's health: schema validity, "
        "closed-enum conformance, content_hash integrity, completeness "
        "banner tier (STRONG / PARTIAL / INSUFFICIENT), declared "
        "redactions, and actionable next steps for partial / missing "
        "evidence questions. Read-only."
    ),
)
def roam_evidence_doctor(packet_path: str, root: str = ".") -> dict:
    """Diagnose an evidence packet's health.

    WHEN TO USE: a buyer / auditor (or a CI gate) received a
    ``ChangeEvidence`` JSON packet and wants to know whether it's
    trustworthy / complete / well-formed BEFORE running it through a
    heavyweight downstream check. Lightweight, read-only, never mutates
    the packet.

    Verdict ladder:

    * ``PASS`` — schema valid, content_hash matches, banner is STRONG.
    * ``WARN`` — schema valid, content_hash matches, but banner is
      PARTIAL or INSUFFICIENT (one or more questions are partial /
      missing).
    * ``FAIL`` — schema invalid (closed-enum violation, malformed JSON)
      OR content_hash recompute disagrees with the stamped value.

    Parameters
    ----------
    packet_path:
        Filesystem path to a ``ChangeEvidence`` JSON file. The packet is
        loaded as raw JSON (no schema-version coupling), so older
        packets that pre-date W210 still parse.
    """
    args = ["evidence-doctor", packet_path]
    return _run_roam(args, root)


# W464 + W465: roam_evidence_oscal — OSCAL v1.2 emission (Control Mapping or AR).
@_tool(
    name="roam_evidence_oscal",
    description=(
        "Emit an OSCAL v1.2 document. Default kind='control-mapping' "
        "compiles the roam control map (maps roam evidence to EU AI "
        "Act, ISO/IEC 42001, NIST AI RMF, NIST AI 600-1, NIST SP "
        "800-218A, SOC 2, internal AI-change policy). kind="
        "'assessment-results' compiles a per-run AR document from a "
        "ChangeEvidence packet (requires evidence_path); AR mandates "
        "an Assessment Plan reference — pass import_ap_ref for an "
        "external AP or omit it to inline a synthesized stub AP. "
        "Supports evidence for the listed frameworks — does not "
        "certify compliance. Two roam-specific concepts "
        "(authority_refs, redactions) surface as OSCAL ``prop`` "
        "extensions under the ``urn:roam:oscal:v1`` namespace."
    ),
)
def roam_evidence_oscal(
    kind: str = "control-mapping",
    output_path: str | None = None,
    control_map: str | None = None,
    evidence_path: str | None = None,
    import_ap_ref: str | None = None,
    title: str | None = None,
    root: str = ".",
) -> dict:
    """Emit an OSCAL v1.2 document (Control Mapping or Assessment Results).

    WHEN TO USE: an external GRC / compliance tool (compliance-
    trestle, complyctl, FedRAMP automation) wants to consume a
    portable, OSCAL-conformant document from roam. Control Mapping
    is the repo-static crosswalk (one per repo); Assessment Results
    is the per-run findings document (one per code-change scope).

    Parameters
    ----------
    kind:
        ``"control-mapping"`` (default) or ``"assessment-results"``.
    output_path:
        Optional path to write the OSCAL JSON to disk. When omitted,
        the document is returned inline in the JSON envelope's
        ``oscal_document`` payload field.
    control_map:
        Optional override for the source ``control-mapping.yaml``
        path (control-mapping kind only). Defaults to the wheel-
        bundled ``roam.templates.audit_report`` resource (W554);
        a project-root ``templates/audit-report/control-mapping.yaml``
        under ``root`` is honoured as a hand-edited override.
    evidence_path:
        Required when ``kind='assessment-results'``. Path to a
        ChangeEvidence JSON packet (canonical form as emitted by
        ``roam pr-replay --evidence``).
    import_ap_ref:
        Optional reference (path / URI) to an external Assessment
        Plan (AR kind only). When omitted, a stub AP is synthesized
        inline (FedRAMP continuous-assessment pattern).
    title:
        Optional document-level title override. Must comply with the
        W184 wording lint ("maps to" / "supports evidence for"; no
        "certifies" / "compliant" / "guarantees").
    root:
        Repo root (default current directory).

    Returns
    -------
    dict
        Envelope summary fields vary by kind:
        * control-mapping: ``{verdict, control_count, framework_count,
          document_uuid, output_path}``.
        * assessment-results: ``{verdict, result_count, finding_count,
          observation_count, document_uuid, output_path,
          import_ap_ref}``.
        ``oscal_document`` carries the full OSCAL JSON shape.
    """
    args = ["evidence-oscal", "--kind", kind]
    if output_path:
        args += ["--output", output_path]
    if control_map:
        args += ["--control-map", control_map]
    if evidence_path:
        args += ["--evidence", evidence_path]
    if import_ap_ref:
        args += ["--import-ap-ref", import_ap_ref]
    if title:
        args += ["--title", title]
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# W299: exploration & search cluster (Wave29 MCP wrapper backfill, sub-wave 1)
# ---------------------------------------------------------------------------
# 9 read-only navigation wrappers that an agent reaches for first: index-aware
# grep, through-history pickaxe, literal-string audit, fan-in/out, module
# overview, unified metrics, and the three findings-registry subcommands.
# All 9 require a built index, so they're auto-wired into the W296 cold-start
# guard by virtue of NOT appearing in ``_NO_INDEX_NEEDED``. Descriptions use
# imperative voice per CLAUDE.md LAW 2 ("Run X" not "This command does X").
# ---------------------------------------------------------------------------


# W299: roam_grep
@_tool(
    name="roam_grep",
    description=(
        "Run index-aware grep across the codebase. Returns matches with "
        "their enclosing symbol, reachability badge, PageRank, clone-class, "
        "and bridge annotations. Supports multi-pattern, source-only / "
        "test-only filters, reachable-from / unreachable filters, "
        "co-occurrence across patterns, and rank-by importance. Request "
        "bounded context packets or whole enclosing symbols to replace the "
        "usual grep-then-read loop."
    ),
)
def roam_grep(
    pattern: str = "",
    patterns: str = "",
    globs: str = "",
    fixed: bool = False,
    case_insensitive: bool = False,
    word_boundary: bool = False,
    count: int = 50,
    source_only: bool = False,
    test_only: bool = False,
    exclude: str = "",
    reachable_from: str = "",
    unreachable: bool = False,
    co_occur: bool = False,
    missing_pattern: str = "",
    rank_by: str = "line",
    group_by: str = "none",
    context_lines: int = 0,
    whole_symbol: bool = False,
    max_packets: int = 8,
    max_packet_lines: int = 120,
    with_blame: bool = False,
    with_heat: bool = False,
    no_clones: bool = False,
    no_bridges: bool = False,
    root: str = ".",
) -> dict:
    """Run index-aware grep across the codebase.

    WHEN TO USE: when raw text search is needed but the agent also wants
    each hit annotated with its enclosing symbol, reachability, clone
    class, and PageRank. More than a wrapper around ripgrep -- adds the
    structural signal an LLM agent uses to triage hits.

    Parameters
    ----------
    pattern:
        Single positional pattern. Use ``patterns`` for multi-pattern
        alternation.
    patterns:
        Comma-separated list of patterns (becomes repeated ``-e`` on the
        CLI). Treated as alternation across patterns.
    globs:
        Comma-separated glob filter (e.g. ``"py,md"``). Shorthand ``ts``
        / ``.ts`` is normalised to ``*.ts`` by the CLI.
    fixed:
        Literal mode (no regex).
    case_insensitive:
        Case-insensitive search.
    word_boundary:
        Match whole words only.
    count:
        Max results to show (default 50).
    source_only:
        Exclude docs, configs, and non-source files.
    test_only:
        Only search in test files.
    exclude:
        Comma-separated exclusion globs.
    reachable_from:
        Keep only hits reachable from a named entry symbol.
    unreachable:
        Keep only hits in unreachable / orphan code.
    co_occur:
        Keep hits whose enclosing symbol matches every pattern.
    missing_pattern:
        Drop hits whose enclosing symbol also matches this pattern.
    rank_by:
        ``line`` (default) or ``importance`` (sort by enclosing-symbol
        PageRank desc).
    group_by:
        ``none`` (default) or ``symbol`` (collapse hits inside the same
        symbol).
    context_lines:
        Attach this many source lines around each match (0-20).
    whole_symbol:
        Attach each match's complete indexed enclosing symbol.
    max_packets:
        Maximum unique context packets to return (default 8).
    max_packet_lines:
        Maximum rendered lines per context packet (default 120).
    with_blame:
        Annotate hits with last-modified author + date.
    with_heat:
        Annotate hits with churn / commit count.
    no_clones:
        Skip clone-class annotation.
    no_bridges:
        Skip bridge annotation.

    Returns: ``{summary: {verdict, total_hits, ...}, hits: [...]}``.
    """
    args: list[str] = ["grep"]
    if pattern:
        args.append(pattern)
    for p in (patterns or "").split(","):
        p = p.strip()
        if p:
            args.extend(["-e", p])
    for g in (globs or "").split(","):
        g = g.strip()
        if g:
            args.extend(["-g", g])
    if fixed:
        args.append("-F")
    if case_insensitive:
        args.append("-i")
    if word_boundary:
        args.append("-w")
    if count != 50:
        args.extend(["-n", str(count)])
    if source_only:
        args.append("-s")
    if test_only:
        args.append("-t")
    if exclude:
        args.extend(["--exclude", exclude])
    if reachable_from:
        args.extend(["--reachable-from", reachable_from])
    if unreachable:
        args.append("--unreachable")
    if co_occur:
        args.append("--co-occur")
    if missing_pattern:
        args.extend(["--missing-pattern", missing_pattern])
    if rank_by != "line":
        args.extend(["--rank-by", rank_by])
    if group_by != "none":
        args.extend(["--group-by", group_by])
    if context_lines:
        args.extend(["--context", str(context_lines)])
    if whole_symbol:
        args.append("--whole-symbol")
    if max_packets != 8:
        args.extend(["--max-packets", str(max_packets)])
    if max_packet_lines != 120:
        args.extend(["--max-packet-lines", str(max_packet_lines)])
    if with_blame:
        args.append("--blame")
    if with_heat:
        args.append("--heat")
    if no_clones:
        args.append("--no-clones")
    if no_bridges:
        args.append("--no-bridges")
    return _run_roam(args, root)


# W299: roam_history_grep
@_tool(
    name="roam_history_grep",
    description=(
        "Run git pickaxe (``-S`` / ``-G``) through commit history. Returns "
        "commits that introduced or removed the literal string, with "
        "author, date, short SHA, and summary per commit."
    ),
)
def roam_history_grep(
    pattern: str = "",
    patterns: str = "",
    fixed: bool = True,
    case_insensitive: bool = False,
    since: str = "",
    until: str = "",
    limit: int = 20,
    polarity: bool = False,
    paths: str = "",
    root: str = ".",
) -> dict:
    """Search through-history with git pickaxe.

    WHEN TO USE: postmortems, provenance investigations, auditing renames
    or deletions that no longer leave a trace in HEAD. Answers "when did
    this string first appear?" or "which commit removed it?".

    Parameters
    ----------
    pattern:
        Single positional pattern. Use ``patterns`` for multi-pattern.
    patterns:
        Comma-separated additional patterns.
    fixed:
        Literal mode (default True -- use ``-S``). Set False to use
        regex pickaxe (``-G``).
    case_insensitive:
        Case-insensitive search.
    since:
        Only commits after this date (YYYY-MM-DD or relative, e.g.
        ``"2 weeks ago"``).
    until:
        Only commits before this date.
    limit:
        Max commits per pattern (default 20).
    polarity:
        Annotate each commit as introduced / removed / modified (slower).
    paths:
        Comma-separated path filter (e.g. ``"src/,docs/"``).

    Returns: ``{summary: {...}, per_pattern: {pattern: [commit_rows]}}``.
    """
    args: list[str] = ["history-grep"]
    if pattern:
        args.append(pattern)
    for p in (patterns or "").split(","):
        p = p.strip()
        if p:
            args.extend(["-e", p])
    # CLI flag is ``-F/--fixed-string`` with default True; only emit when
    # the caller wants regex pickaxe (``fixed=False``). The CLI option is
    # a boolean flag (no value), so to disable it we'd need ``--no-...``
    # which the CLI doesn't expose. Per LAW 11 (user intent > inference)
    # we honor fixed=True as the documented default and skip the flag.
    if case_insensitive:
        args.append("-i")
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if limit != 20:
        args.extend(["-n", str(limit)])
    if polarity:
        args.append("--polarity")
    for path in (paths or "").split(","):
        path = path.strip()
        if path:
            args.extend(["-p", path])
    return _run_roam(args, root)


# W299: roam_refs_text
@_tool(
    name="roam_refs_text",
    description=(
        "Audit literal strings across the project and emit a per-string "
        "verdict: SAFE-TO-REMOVE / REVIEW / LOAD-BEARING. Groups every "
        "reference by surface (code, test, docs, config, generated, "
        "vendored) and annotates reachability for code hits."
    ),
)
def roam_refs_text(
    strings: str = "",
    reachable_from: str = "",
    globs: str = "",
    fixed: bool = True,
    case_insensitive: bool = False,
    with_clones: bool = True,
    with_bridges: bool = True,
    per_match_detail: bool = False,
    root: str = ".",
) -> dict:
    """Audit literal strings across the project with safety verdict.

    WHEN TO USE: before removing a config key, error message, route, or
    identifier. Different shape from ``roam_grep`` -- grep prints lines
    so you can eyeball them; refs-text answers the question "is this
    string still load-bearing?".

    Verdict ladder:

    * ``SAFE-TO-REMOVE`` -- only doc / test / dead-code references.
    * ``REVIEW`` -- referenced in one or two reachable code symbols.
    * ``LOAD-BEARING`` -- referenced in many reachable code symbols, or
      in symbols with non-trivial PageRank.

    Parameters
    ----------
    strings:
        Comma-separated list of strings to audit (e.g.
        ``"DATABASE_URL,/api/v1/users"``).
    reachable_from:
        Treat reachability as "reachable from <entry>". When omitted,
        dead = no inbound edges.
    globs:
        Comma-separated glob filter (e.g. ``"py,md"``).
    fixed:
        Literal mode (default True). Set False for regex matching.
    case_insensitive:
        Case-insensitive search.
    with_clones:
        Annotate code hits with clone-class siblings (default True).
    with_bridges:
        Annotate config / template hits with cross-language bridge
        links (default True).
    per_match_detail:
        Include every match in JSON output (default: only summary +
        per-surface counts).

    Returns: ``{summary: {verdict, total_targets, ...}, per_string: {...}}``.
    """
    args: list[str] = ["refs-text"]
    targets = [s.strip() for s in (strings or "").split(",") if s.strip()]
    args.extend(targets)
    if reachable_from:
        args.extend(["--reachable-from", reachable_from])
    for g in (globs or "").split(","):
        g = g.strip()
        if g:
            args.extend(["-g", g])
    # CLI's --fixed-string defaults to True; only need to skip when
    # caller wants regex. Same LAW 11 note as roam_history_grep.
    if case_insensitive:
        args.append("-i")
    if not with_clones:
        args.append("--no-clones")
    if not with_bridges:
        args.append("--no-bridges")
    if per_match_detail:
        args.append("--per-match-detail")
    return _run_roam(args, root)


# W299: roam_fan
@_tool(
    name="roam_fan",
    description=(
        "Show fan-in / fan-out: the most-connected symbols or files. "
        "Flags hub / spreader / HIGH-RISK structural hotspots based on "
        "cross-file import / call edges. Different from coupling "
        "(co-change frequency) -- this measures structural connectivity."
    ),
)
def roam_fan(
    mode: str = "symbol",
    count: int = 20,
    no_framework: bool = False,
    include_tooling: bool = False,
    persist: bool = False,
    root: str = ".",
) -> dict:
    """Show most-connected symbols or files by structural fan-in / fan-out.

    WHEN TO USE: find architectural hubs (many callers) and spreaders
    (many callees). High-risk symbols are both -- they aggregate AND
    distribute. Useful before refactoring or carving a module boundary.

    Parameters
    ----------
    mode:
        ``symbol`` (default) or ``file``.
    count:
        Number of items to show (default 20).
    no_framework:
        Filter out framework / boilerplate symbols.
    include_tooling:
        Include CI scripts, dev tooling, build, and generated files.
        Excluded by default -- high fan-in there is expected and
        dominates the headline.
    persist:
        Persist cross-file architectural fan findings (HIGH-RISK / hub
        / spreader) to the ``.roam/index.db`` findings registry.

    Returns: ``{summary: {...}, top_symbols: [...] | top_files: [...]}``.
    """
    args: list[str] = ["fan", mode, "-n", str(count)]
    if no_framework:
        args.append("--no-framework")
    if include_tooling:
        args.append("--include-tooling")
    if persist:
        args.append("--persist")
    return _run_roam(args, root)


# W299: roam_module
@_tool(
    name="roam_module",
    description=(
        "Show directory contents: exported symbols, signatures, external "
        "imports / importers, internal cohesion percentage, and API "
        "surface ratio. Different from ``roam_describe`` (project-wide) "
        "-- this analyses a single directory."
    ),
)
def roam_module(path: str, root: str = ".") -> dict:
    """Show a directory's exports, dependencies, and cohesion.

    WHEN TO USE: scoping a refactor to one directory, or auditing whether
    a module's public surface is reasonable. Returns the directory's
    cohesion (internal edge ratio), API surface percentage, and external
    coupling per importer / importee.

    Parameters
    ----------
    path:
        Directory path relative to the repo root (e.g. ``"src/roam/db"``).
        Pass ``"."`` for root-level files only.

    Returns: ``{summary: {...}, files, symbols, deps_in, deps_out, ...}``.
    """
    return _run_roam(["module", path], root)


# W299: roam_metrics
@_tool(
    name="roam_metrics",
    description=(
        "Show unified per-file or per-symbol metrics: cognitive "
        "complexity, fan-in / fan-out, SNA centrality vector "
        "(PageRank / betweenness / closeness / eigenvector / clustering "
        "coefficient), composite debt score, churn, test coverage, and "
        "comprehension difficulty in a single view."
    ),
)
def roam_metrics(symbol: str, root: str = ".") -> dict:
    """Show unified metrics for a file or symbol.

    WHEN TO USE: deciding whether a symbol is risky to change, or
    triaging a hotspot. Consolidates the signals from complexity, fan,
    centrality, churn, coverage, and dead-code-risk into one structured
    output. Different from ``roam_health`` (codebase-wide score) --
    this drills into one symbol.

    Parameters
    ----------
    symbol:
        File path (e.g. ``"src/app.py"``) OR symbol name (e.g.
        ``"create_user"``). W430/Fix-D canonical; legacy ``target=``
        callers are accepted via ``_PARAM_ALIASES`` with a deprecation
        warning.

    Returns: ``{summary: {...}, metrics: {complexity, fan_in, fan_out,
    centrality, churn, coverage, dead_code_risk, ...}}``.
    """
    return _run_roam(["metrics", symbol], root)


# W299: roam_findings_list
@_tool(
    name="roam_findings_list",
    description=(
        "List rows from the central findings registry, optionally "
        "filtered by detector or subject. Cross-detector view -- every "
        "migrated detector (clones, dead, complexity, smells, n1, "
        "missing-index, ...) emits here behind one schema."
    ),
)
def roam_findings_list(
    detector: str = "",
    subject_kind: str = "",
    subject_id: int = 0,
    limit: int = 100,
    root: str = ".",
) -> dict:
    """List findings from the central registry.

    WHEN TO USE: the canonical "what's wrong with this repo" surface.
    Cross-detector dedup, suppression management, and the SARIF-emit
    substrate all flow through this registry. Returns rows with
    ``finding_id_str`` you can pass to ``roam_findings_show``.

    Parameters
    ----------
    detector:
        Filter by ``source_detector`` (exact match, e.g. ``"clones"`` /
        ``"dead"`` / ``"complexity"`` / ``"smells"``).
    subject_kind:
        Filter by ``subject_kind`` (e.g. ``symbol`` / ``file`` /
        ``edge`` / ``commit``).
    subject_id:
        Filter by ``subject_id`` (numeric). Typically combined with
        ``subject_kind``.
    limit:
        Cap rows returned (default 100).

    Returns: ``{summary: {verdict, total_findings, detectors}, findings: [...]}``.
    """
    args: list[str] = ["findings", "list", "--limit", str(limit)]
    if detector:
        args.extend(["--detector", detector])
    if subject_kind:
        args.extend(["--subject-kind", subject_kind])
    if subject_id:
        args.extend(["--subject-id", str(subject_id)])
    return _run_roam(args, root)


# W299: roam_findings_show
@_tool(
    name="roam_findings_show",
    description=(
        "Show full detail for a single finding by its stable "
        "``finding_id_str``. Returns the detector version, subject, "
        "confidence tier, claim, evidence JSON, and any suppressions."
    ),
)
def roam_findings_show(finding_id_str: str, root: str = ".") -> dict:
    """Show full detail for a single finding.

    WHEN TO USE: after ``roam_findings_list`` returns a row of interest,
    fetch its full evidence payload. The ``finding_id_str`` is the
    stable cross-run identifier (e.g. ``"clones:sym:abcd"``).

    Parameters
    ----------
    finding_id_str:
        Stable finding identifier from ``roam_findings_list``.

    Returns: ``{summary: {...}, finding: {detector, subject, claim,
    evidence_json, suppressions, ...}}``.
    """
    return _run_roam(["findings", "show", finding_id_str], root)


# W299: roam_findings_count
@_tool(
    name="roam_findings_count",
    description=(
        "Show per-detector finding counts. Useful for spotting which "
        "detectors have migrated to the central registry vs which are "
        "still only emitting to their detector-specific tables."
    ),
)
def roam_findings_count(root: str = ".") -> dict:
    """Show per-detector finding counts.

    WHEN TO USE: triage which detectors have signal on the current
    repo. The tally maps ``source_detector`` -> row count so an agent
    can pick the highest-yield detector to drill into.

    Returns: ``{summary: {total_findings, detector_count}, counts: {detector: n}}``.
    """
    return _run_roam(["findings", "count"], root)


# ---------------------------------------------------------------------------
# W300: architecture cluster (Wave29 MCP wrapper backfill, sub-wave 2)
# ---------------------------------------------------------------------------
# 10 read-only structural-analysis wrappers that all hit the graph layer:
# Louvain clusters, topological layers, file / function temporal coupling,
# commit-range graph delta, graph-wide invariants, entry-point catalog,
# architectural-pattern detector, min-cut domain boundaries, and the
# cross-language bridge report. All 10 require a built index, so they're
# auto-wired into the W296 cold-start guard by virtue of NOT appearing in
# ``_NO_INDEX_NEEDED``. Descriptions use imperative voice per CLAUDE.md
# LAW 2 ("Run X" not "This command does X").
# ---------------------------------------------------------------------------


# W300: roam_clusters
@_tool(
    name="roam_clusters",
    description=(
        "Show Louvain code clusters and directory mismatches. Returns "
        "per-cluster size, cohesion, conductance, modularity Q, mega-cluster "
        "sub-group breakdowns, and inter-cluster coupling. Different from "
        "``roam_layers`` (dependency-layer violations) -- this groups by "
        "community detection, not by topological depth."
    ),
)
def roam_clusters(
    min_size: int = 3,
    mermaid: bool = False,
    weak: bool = False,
    strong: bool = False,
    root: str = ".",
) -> dict:
    """Show Louvain code clusters and directory mismatches.

    WHEN TO USE: discover natural module boundaries the graph finds on
    its own, then compare against the directory structure. Mismatches
    indicate hidden coupling that the layout doesn't reflect. Pair with
    ``roam_cut`` to find the thinnest seam to split along.

    Parameters
    ----------
    min_size:
        Hide clusters smaller than this (default 3).
    mermaid:
        Emit a Mermaid LR diagram of clusters + inter-cluster edges.
    weak:
        Rank clusters by lowest intra-density (split candidates).
    strong:
        Rank clusters by highest intra-density (well-formed modules).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, clusters, mismatches, modularity_q, ...},
    clusters: [...], mismatches: [...]}``.
    """
    args: list[str] = ["clusters", "--min-size", str(min_size)]
    if mermaid:
        args.append("--mermaid")
    if weak:
        args.append("--weak")
    if strong:
        args.append("--strong")
    return _run_roam(args, root)


# roam_cycles
@_tool(
    name="roam_cycles",
    description=(
        "Show import/call cycles (Tarjan strongly-connected components) of the "
        "symbol graph. Returns per-cycle size, member files/symbols, and an "
        "`actionable` flag (spans >=2 distinct non-test files). The focused "
        "counterpart to the cycles section of ``roam_health``; sibling of "
        "``roam_clusters`` / ``roam_layers``."
    ),
)
def roam_cycles(
    min_size: int = 2,
    limit: int = 20,
    actionable_only: bool = False,
    root: str = ".",
) -> dict:
    """List import/call cycles (SCCs) of the symbol graph.

    Parameters
    ----------
    min_size:
        Minimum SCC size to report (default 2).
    limit:
        Max cycles to list (default 20).
    actionable_only:
        Only cycles spanning >=2 distinct non-test files.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, cycle_count, actionable_count, ...},
    cycles: [...]}``.
    """
    args: list[str] = ["cycles", "--min-size", str(min_size), "--limit", str(limit)]
    if actionable_only:
        args.append("--actionable-only")
    return _run_roam(args, root)


# W300: roam_layers
@_tool(
    name="roam_layers",
    description=(
        "Show topological dependency layers and violations. Returns each "
        "layer's symbol count, directory breakdown, and any back-edges that "
        "violate the topological order. Different from ``roam_clusters`` "
        "(community detection) -- this measures dependency depth."
    ),
)
def roam_layers(mermaid: bool = False, root: str = ".") -> dict:
    """Show topological dependency layers and violations.

    WHEN TO USE: confirm the codebase has a clean layered architecture
    or surface back-edges (e.g. domain calling controller) before
    refactoring. Pair with ``roam_guard`` for per-symbol layer-violation
    risk scoring.

    Parameters
    ----------
    mermaid:
        Emit a Mermaid LR diagram of layers + violations.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_layers, violations, ...},
    layers: [...], violations: [...]}``.
    """
    args: list[str] = ["layers"]
    if mermaid:
        args.append("--mermaid")
    return _run_roam(args, root)


# W300: roam_coupling
@_tool(
    name="roam_coupling",
    description=(
        "Use for: 'what files change together?' / 'find hidden coupling "
        "not visible in imports' / 'which sibling file should I also "
        "update?'. Pick over reading git log manually — surfaces "
        "co-change partners the call graph misses. Use roam_fan for "
        "structural connectivity, roam_dark_matter for the latent variant."
    ),
)
def roam_coupling(
    count: int = 0,
    staged: bool = False,
    against: str = "",
    min_strength: float = 0.3,
    min_cochanges: int = 2,
    root: str = ".",
) -> dict:
    """Show file pairs that change together over git history.

    WHEN TO USE: spot hidden coupling that isn't visible in the call
    graph (e.g. files that always change together because they encode
    the same domain concept). Use ``staged`` / ``against`` to surface
    missing co-change partners for a working-set diff.

    Parameters
    ----------
    count:
        Number of pairs to show. ``0`` (default) lets the CLI auto-scale
        by project size (20 / 50 / 100 for small / mid / large repos).
    staged:
        Check coupling for staged changes only.
    against:
        Check coupling for a commit range (e.g. ``"HEAD~3..HEAD"``).
    min_strength:
        Minimum coupling strength for against mode (default 0.3).
    min_cochanges:
        Minimum co-change count for against mode (default 2).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, pairs: [...]}``.
    """
    args: list[str] = ["coupling"]
    if count:
        args.extend(["-n", str(count)])
    if staged:
        args.append("--staged")
    if against:
        args.extend(["--against", against])
    if min_strength != 0.3:
        args.extend(["--min-strength", str(min_strength)])
    if min_cochanges != 2:
        args.extend(["--min-cochanges", str(min_cochanges)])
    return _run_roam(args, root)


@_tool(
    name="roam_full_coupling",
    description=(
        "Composite coupling report for ONE file in a single envelope: "
        "top-N temporal coupling pairs touching the file + structural "
        "imports/importers + top-N file symbols. Use instead of chaining "
        "roam_coupling + roam_deps + roam_file_info."
    ),
)
def roam_full_coupling(path: str, top_n: int = 5, root: str = ".") -> dict:
    """Composite coupling envelope for one file.

    Bundles three existing tools — ``roam_coupling`` (temporal),
    ``roam_deps`` (structural imports/importers), and ``roam_file_info``
    (top symbols) — into a single response keyed on ``path``. Callers
    that pass ``file_path=`` continue to work via the W347 alias.
    """
    coupling_full = roam_coupling(root=root)
    pairs = coupling_full.get("pairs", []) if isinstance(coupling_full, dict) else []
    if not isinstance(pairs, list):
        pairs = []
    file_pairs = [p for p in pairs if isinstance(p, dict) and (p.get("file_a") == path or p.get("file_b") == path)][
        :top_n
    ]

    deps = roam_deps(path, root=root)

    info = file_info(path, root=root)
    syms = info.get("symbols", []) if isinstance(info, dict) else []
    if not isinstance(syms, list):
        syms = []
    top_symbols = syms[:top_n]

    return {
        "command": "roam_full_coupling",
        "file": path,
        "coupling": {"pairs": file_pairs, "all_pairs_total": len(pairs)},
        "deps": deps,
        "top_symbols": top_symbols,
        "summary": {
            "verdict": (f"{len(file_pairs)} coupled pairs, {len(top_symbols)} top symbols for {path}"),
        },
    }


# W300: roam_fn_coupling
@_tool(
    name="roam_fn_coupling",
    description=(
        "Show function-level temporal coupling: symbol pairs that change "
        "together across commits. Different from ``roam_coupling`` "
        "(file-level pairs) -- this drills into co-changing symbols "
        "inside and across files, with optional structural-edge filtering."
    ),
)
def roam_fn_coupling(
    min_count: int = 3,
    limit: int = 20,
    include_connected: bool = False,
    include_tests: bool = False,
    max_files_per_commit: int = 0,
    max_symbols_per_file: int = 0,
    since: str = "",
    root: str = ".",
) -> dict:
    """Show symbol pairs that change together over git history.

    WHEN TO USE: locate co-changing symbol pairs that lack a direct
    call edge -- the strongest hidden-coupling signal at the function
    level. Pass ``include_connected=True`` to compare against directly
    connected pairs.

    Parameters
    ----------
    min_count:
        Minimum co-change count to report (default 3).
    limit:
        Maximum pairs to show (default 20).
    include_connected:
        Also show pairs that have a direct edge.
    include_tests:
        Include test files in the co-change matrix. Off by default --
        test fixtures co-change with src files by design.
    max_files_per_commit:
        Skip commits touching more than N files (treated as
        merges/reformats). ``0`` lets the CLI use its default.
    max_symbols_per_file:
        Per commit, only the top-N PageRank symbols of each changed
        file contribute pairs. ``0`` lets the CLI use its default.
    since:
        Only consider commits since this ref (sha or tag).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, pairs: [...]}``.
    """
    args: list[str] = [
        "fn-coupling",
        "--min-count",
        str(min_count),
        "-n",
        str(limit),
    ]
    if include_connected:
        args.append("--include-connected")
    if include_tests:
        args.append("--include-tests")
    if max_files_per_commit:
        args.extend(["--max-files-per-commit", str(max_files_per_commit)])
    if max_symbols_per_file:
        args.extend(["--max-symbols-per-file", str(max_symbols_per_file)])
    if since:
        args.extend(["--since", since])
    return _run_roam(args, root)


# W300: roam_graph_diff
@_tool(
    name="roam_graph_diff",
    description=(
        "Show the structural graph delta between two snapshots. Surfaces "
        "new / removed symbols, edge churn, degree shifts, new cycles, "
        "layer migrations, and likely renames. Reads persisted snapshots "
        "from ``.roam/snapshots/`` -- capture one with ``--save-snapshot``."
    ),
)
def roam_graph_diff(
    base: str = "",
    head: str = "",
    top: int = 20,
    save_snapshot: str = "",
    root: str = ".",
) -> dict:
    """Show the structural graph delta between two snapshots.

    WHEN TO USE: before / after refactor verification. Save a snapshot
    with ``save_snapshot="pre-refactor"``, make changes, then call
    again with ``base="pre-refactor"`` to see what moved in the graph.
    Emits a clean ``state: no_baseline_snapshot`` envelope when nothing
    is on disk.

    Parameters
    ----------
    base:
        Baseline snapshot label. Defaults to newest snapshot on disk.
    head:
        Head snapshot label. Defaults to the current DB graph (live).
    top:
        Cap list outputs to N rows (default 20; 0 = unlimited).
    save_snapshot:
        Persist the current DB graph to
        ``.roam/snapshots/<label>.json`` and exit.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, added, removed, ...}``.
    """
    args: list[str] = ["graph-diff"]
    if base:
        args.extend(["--base", base])
    if head:
        args.extend(["--head", head])
    if top != 20:
        args.extend(["--top", str(top)])
    if save_snapshot:
        args.extend(["--save-snapshot", save_snapshot])
    return _run_roam(args, root)


# W300: roam_graph_stats
@_tool(
    name="roam_graph_stats",
    description=(
        "Report graph-level invariants: density, connected components, "
        "average in/out degree, top in-degree symbols, and approximate "
        "diameter. One overview number for 'how dense, connected, and "
        "cyclic is this codebase'."
    ),
)
def roam_graph_stats(scope: str = "symbol", root: str = ".") -> dict:
    """Report density, connected components, and degree statistics.

    WHEN TO USE: scoping a new repo or comparing the global shape
    against a known baseline. Pair with ``roam_health`` for a
    score-driven view and ``roam_clusters`` / ``roam_layers`` for
    structure-driven views.

    Parameters
    ----------
    scope:
        ``symbol`` (default) or ``file`` -- which dependency graph to
        measure.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, nodes, edges, density, ...}}``.
    """
    args: list[str] = ["graph-stats"]
    if scope.lower() != "symbol":
        args.extend(["--scope", scope])
    return _run_roam(args, root)


# W300: roam_entry_points
@_tool(
    name="roam_entry_points",
    description=(
        "Catalog every entry point into the codebase: HTTP routes, CLI "
        "commands, scheduled jobs, event handlers, message consumers, "
        "main functions, and exports. Reports per-entry reachability "
        "coverage -- what fraction of symbols each entry transitively "
        "reaches through the call graph."
    ),
)
def roam_entry_points(
    protocol: str = "",
    limit: int = 50,
    root: str = ".",
) -> dict:
    """Show entry-point catalog with protocol classification.

    WHEN TO USE: triage public surface area before a security review,
    measure reachability per protocol, or scope a refactor's external
    impact. Different from ``roam_coverage_gaps`` (unprotected entry
    points lacking gate guards) -- this catalogs every entry point.

    Parameters
    ----------
    protocol:
        Filter to one protocol: ``HTTP`` / ``CLI`` / ``Event`` /
        ``Scheduled`` / ``Message`` / ``Main`` / ``Export``. Empty
        (default) shows every protocol.
    limit:
        Maximum entry points to display (default 50).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, entry_points: [...]}``.
    """
    args: list[str] = ["entry-points", "--limit", str(limit)]
    if protocol:
        args.extend(["--protocol", protocol])
    return _run_roam(args, root)


# W300: roam_patterns
@_tool(
    name="roam_patterns",
    description=(
        "Detect positive architectural patterns: Singleton, Factory, "
        "Observer, Repository, Middleware, Strategy, and Decorator. "
        "Different from ``roam_smells`` (negative anti-patterns) -- "
        "this discovers intentional design patterns."
    ),
)
def roam_patterns(
    pattern: str = "",
    strict_factory: bool = False,
    root: str = ".",
) -> dict:
    """Detect common architectural patterns in the codebase.

    WHEN TO USE: confirm a design pattern claim ("we use the
    repository pattern") or scope a refactor that touches one
    pattern type. Returns per-pattern instances with the symbols and
    files involved.

    Parameters
    ----------
    pattern:
        Filter to one pattern type (e.g. ``singleton`` / ``factory`` /
        ``observer`` / ``repository`` / ``middleware`` / ``strategy`` /
        ``decorator``). Empty (default) shows every pattern.
    strict_factory:
        Drop builder-helper functions (``build_X`` / ``make_X``
        returning POJOs). Default keeps them tagged with
        ``subtype='builder_helper'``.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, patterns: {...}}``.
    """
    args: list[str] = ["patterns"]
    if pattern:
        args.extend(["--pattern", pattern])
    if strict_factory:
        args.append("--strict-factory")
    return _run_roam(args, root)


# W300: roam_cut
@_tool(
    name="roam_cut",
    description=(
        "Find fragile domain boundaries via minimum-cut analysis. "
        "Computes the thinnest edge cuts between architectural clusters "
        "and the highest-impact 'leak edges' whose removal would best "
        "improve domain isolation. Different from ``roam_split`` "
        "(decomposes a single file) -- this finds boundaries between "
        "clusters."
    ),
)
def roam_cut(
    between: str = "",
    leak_edges: bool = False,
    top: int = 10,
    root: str = ".",
) -> dict:
    """Show minimum-cut domain boundaries between clusters.

    WHEN TO USE: planning to split a mega-cluster into two modules.
    Pair with ``roam_clusters`` to find candidate cluster pairs and
    ``roam_dark_matter`` to confirm the cut doesn't sever co-changing
    symbols.

    Parameters
    ----------
    between:
        Comma-separated pair of cluster names to analyse the boundary
        between (e.g. ``"auth,payments"``). Empty (default) ranks
        every boundary.
    leak_edges:
        Focus on leak-edge analysis -- which single edges, if removed,
        most improve domain isolation.
    top:
        Show top N boundaries (default 10).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, boundaries: [...] | leak_edges: [...]}``.
    """
    args: list[str] = ["cut", "--top", str(top)]
    if between:
        pair = [p.strip() for p in between.split(",") if p.strip()]
        if len(pair) == 2:
            args.extend(["--between", pair[0], pair[1]])
    if leak_edges:
        args.append("--leak-edges")
    return _run_roam(args, root)


# W300: roam_x_lang
@_tool(
    name="roam_x_lang",
    description=(
        "Show cross-language symbol bridges: Protobuf .proto -> "
        "generated Go/Java/Python stubs, Salesforce Apex -> Aura/LWC/"
        "Visualforce, REST API frontend -> backend route, template "
        "variable -> source, and env-var read -> .env definition. "
        "Call this tool to list every registered bridge type."
    ),
)
def roam_x_lang(scope: str = "", root: str = ".") -> dict:
    """Show cross-language symbol bridges detected in the project.

    WHEN TO USE: audit cross-language coupling before a schema change
    (e.g. renaming a .proto field invalidates Go/Java/Python stubs).
    On large repos pass ``scope`` to restrict analysis to a subtree.

    Parameters
    ----------
    scope:
        Restrict analysis to files whose path starts with this prefix
        (e.g. ``"src/"``). Empty (default) scans the whole repo.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, bridges, links, ...}, bridges: [...], links: [...]}``.
    """
    args: list[str] = ["x-lang"]
    if scope:
        args.extend(["--scope", scope])
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# W301: health / quality cluster (Wave29 MCP wrapper backfill, sub-wave 3)
# ---------------------------------------------------------------------------
# 10 read-only detector-style wrappers that an agent reaches for when
# triaging code quality, runtime / security hotspots, ownership and review
# congestion, retrieval-quality, and doc<->code drift. Several of these
# detectors persist into the central findings registry behind their
# ``--persist`` flag; the wrappers default to NOT persisting so an MCP
# call stays read-only. All 10 require a built index, so they're
# auto-wired into the W296 cold-start guard by virtue of NOT appearing
# in ``_NO_INDEX_NEEDED``. Descriptions use imperative voice per
# CLAUDE.md LAW 2 ("Run X" not "This command does X"). Per-fact
# vocabulary follows LAW 4 concrete-noun terminals (smells, hotspots,
# directories, violations, orphans, tasks, symbols, findings).
#
# W332 canonical: ``eval-retrieve --tasks`` and ``fitness --baseline``
# are sidecar JSONL/JSON files the tool READS — those collapse onto
# ``input_path``. ``smells --file`` and ``owner <path>`` filter to a
# path already INDEXED in the repo, so they keep the ``path`` /
# ``file_path`` convention used elsewhere in the wrapper surface
# (e.g. ``roam_module(path=...)``, ``roam_file_info(path=...)``).
# ---------------------------------------------------------------------------


# W301: roam_smells
@_tool(
    name="roam_smells",
    description=(
        "Run 24 deterministic code-smell detectors over the indexed "
        "codebase: brain methods, god classes, deep nesting, shotgun "
        "surgery, feature envy, long parameter lists, large classes, "
        "dead params, low cohesion, message chains, data clumps, type "
        "switches, cross-layer clones, parallel hierarchies, and more. "
        "Different from ``roam_vibe_check`` (AI-rot pattern "
        "regex) and ``roam_patterns`` (positive design patterns) -- "
        "this surfaces negative structural anti-patterns from DB "
        "queries."
    ),
)
def roam_smells(
    path: str = "",
    min_severity: str = "",
    include_tooling: bool = False,
    root: str = ".",
) -> dict:
    """Detect code smells: brain methods, god classes, deep nesting.

    WHEN TO USE: scoping a refactor or auditing a single file's
    structural anti-patterns. Pair with ``roam_findings_list
    --detector smells`` to read previously-persisted hits without
    re-running the detectors.

    Parameters
    ----------
    path:
        Filter smells to one file path in the indexed repo (e.g.
        ``"src/roam/cli.py"``). Empty (default) scans every indexed
        file. Mirrors the ``--file`` CLI flag.
    min_severity:
        Minimum severity to include: ``critical`` / ``warning`` /
        ``info``. Empty (default) includes every severity.
    include_tooling:
        Include CI scripts, build scripts, dev tooling, and generated
        files in the smell count. Off by default because high
        complexity in one-shot scripts and codegen output is expected
        and uninteresting.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_smells, ...}, smells: [...]}``.
    """
    args: list[str] = ["smells"]
    if path:
        args.extend(["--file", path])
    if min_severity:
        args.extend(["--min-severity", min_severity])
    if include_tooling:
        args.append("--include-tooling")
    return _run_roam(args, root)


# W301: roam_hotspots
@_tool(
    name="roam_hotspots",
    description=(
        "Show runtime hotspots: symbols ranked by static analysis vs "
        "real production traces (requires ``roam ingest-trace`` to "
        "have populated ``runtime_stats``). Each row is tagged "
        "UPGRADE (runtime-critical but statically safe), CONFIRMED "
        "(both agree), or DOWNGRADE (statically risky but low "
        "traffic). Different from ``roam_why_slow`` (top-N by latency "
        "alone) -- this classifies static vs runtime mismatch."
    ),
)
def roam_hotspots(
    runtime: bool = False,
    discrepancy: bool = False,
    security: bool = False,
    danger: bool = False,
    root: str = ".",
) -> dict:
    """Show runtime hotspots: static vs runtime classification.

    WHEN TO USE: triage which symbols carry real production load
    after ingesting OTel / Jaeger / Zipkin traces. Pair with
    ``roam_why_slow`` for the latency-only ranking, and
    ``roam_metrics`` to drill into one hotspot symbol.

    Parameters
    ----------
    runtime:
        Sort by runtime metrics rather than the default
        composite score.
    discrepancy:
        Only show symbols where static and runtime rankings
        disagree (UPGRADE / DOWNGRADE rows).
    security:
        Detect security hotspots: dangerous-API regex sinks
        with entry-point reachability scoring. Switches the
        detector mode.
    danger:
        Files in top quartile of churn x complexity x max-fan-in
        (the "danger zone" cross-cut).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, hotspots: [...]}``.
    """
    args: list[str] = ["hotspots"]
    if runtime:
        args.append("--runtime")
    if discrepancy:
        args.append("--discrepancy")
    if security:
        args.append("--security")
    if danger:
        args.append("--danger")
    return _run_roam(args, root)


# W301: roam_bus_factor
@_tool(
    name="roam_bus_factor",
    description=(
        "Score knowledge-concentration risk per directory: Shannon "
        "entropy over unique authors, primary-author share, last "
        "activity, and a staleness factor. Flags CRITICAL / HIGH / "
        "MEDIUM / LOW per module. Different from ``roam_owner`` "
        "(per-file blame) and ``roam_congestion`` (too-many-authors "
        "merge-conflict risk) -- this measures knowledge-loss risk."
    ),
)
def roam_bus_factor(
    limit: int = 20,
    stale_months: int = 6,
    brain_methods: bool = False,
    force_team_mode: bool = False,
    root: str = ".",
) -> dict:
    """Score knowledge-loss risk per directory.

    WHEN TO USE: spot directories where a single departure would
    leave the team without a knowledgeable maintainer. Single-author
    repos auto-collapse to STALE-only output; pass
    ``force_team_mode=True`` to opt back into the full
    distributed-team rubric.

    Parameters
    ----------
    limit:
        Number of directories to show (default 20).
    stale_months:
        Months of inactivity before flagging stale knowledge
        (default 6).
    brain_methods:
        Show disproportionately complex functions per directory
        as an extra column.
    force_team_mode:
        Override single-author auto-detection. When one author owns
        >80% of commits, the default switches to STALE-only output;
        this flag restores the full team rubric.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_directories, ...},
    directories: [...]}``.
    """
    args: list[str] = [
        "bus-factor",
        "--limit",
        str(limit),
        "--stale-months",
        str(stale_months),
    ]
    if brain_methods:
        args.append("--brain-methods")
    if force_team_mode:
        args.append("--force-team-mode")
    return _run_roam(args, root)


# W301: roam_fitness
@_tool(
    name="roam_fitness",
    description=(
        "Run architectural fitness functions from "
        "``.roam/fitness.yaml``: dependency constraints, layer "
        "enforcement, metric thresholds, naming conventions, and "
        "trend regression guards. Different from ``roam_preflight`` "
        "(compound 6-signal pre-edit gate) -- this is the dedicated "
        "fitness surface with per-rule output, baseline / delta mode, "
        "and trend regression guards."
    ),
)
def roam_fitness(
    rule: str = "",
    explain: bool = False,
    input_path: str = "",
    root: str = ".",
) -> dict:
    """Run fitness rules from ``.roam/fitness.yaml``.

    WHEN TO USE: gate a PR against architectural rules
    (dependency / layer / metric / naming / trend) declared in
    ``.roam/fitness.yaml``. Pair with ``roam_preflight`` for the
    pre-edit composite verdict.

    Parameters
    ----------
    rule:
        Run only rules whose ``name`` matches this filter substring.
        Empty (default) runs every declared rule.
    explain:
        Show full reason text for each rule violation.
    input_path:
        Path to a baseline JSON file. When set, the runner compares
        current violations against the baseline and exits non-zero
        only for NEW violations. Sidecar file the tool reads (W332
        canonical ``input_path``).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, passed, failed, ...}, rules: [...],
    violations: [...]}``.
    """
    args: list[str] = ["fitness"]
    if rule:
        args.extend(["--rule", rule])
    if explain:
        args.append("--explain")
    if input_path:
        args.extend(["--baseline", input_path])
    return _run_roam(args, root)


# W301: roam_orphan_imports
@_tool(
    name="roam_orphan_imports",
    description=(
        "List imports that don't resolve to any indexed module or "
        "installed package -- catches typo'd local imports, missing "
        "packages, and dangling relative imports. Covers Python "
        "(default), JavaScript / TypeScript, and Go. Different from "
        "``roam_dead_code`` (unused symbols) -- this targets "
        "import-statement orphans."
    ),
)
def roam_orphan_imports(lang: str = "all", root: str = ".") -> dict:
    """List orphan imports across the indexed languages.

    WHEN TO USE: lint pass before a release, or scoping a refactor
    that renamed a module. Pair with ``roam_findings_list
    --detector orphan-imports`` to read previously-persisted hits.

    Parameters
    ----------
    lang:
        Restrict to a single language scan: ``all`` (default) /
        ``python`` / ``javascript`` / ``go``.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_orphans, ...}, orphans: [...]}``.
    """
    args: list[str] = ["orphan-imports"]
    if lang and lang.lower() != "all":
        args.extend(["--lang", lang])
    return _run_roam(args, root)


# W301: roam_eval_retrieve
@_tool(
    name="roam_eval_retrieve",
    description=(
        "Run the retrieval eval harness over a labeled task set. "
        "Reports recall@K, mean reciprocal rank, and per-task "
        "diagnostics. Supports a weight sweep and CodeRAG-Bench / "
        "BEIR emit formats for public leaderboard submission."
    ),
)
def roam_eval_retrieve(
    input_path: str = "",
    rerank: str = "fast",
    sweep: bool = False,
    min_recall_at_20: float = 0.0,
    quick: bool = False,
    root: str = ".",
) -> dict:
    """Measure retrieval recall against a labeled task set.

    WHEN TO USE: regression-test ``roam retrieve`` ranking weights
    after a pipeline change, or generate a portable run file for an
    external leaderboard.

    Parameters
    ----------
    input_path:
        JSONL file of ``(task, expected_files)`` pairs. Empty
        (default) picks up ``bench/retrieve/roam_self.jsonl``. W332
        canonical ``input_path`` -- the file the harness reads.
    rerank:
        Forwarded to ``roam retrieve``: ``fast`` (default) or
        ``off``.
    sweep:
        Run the harness across a small grid of weight vectors and
        report the best-scoring vector.
    min_recall_at_20:
        CI gate: exit 5 if the mean recall@20 is below this
        threshold. ``0.0`` (default) disables the gate.
    quick:
        Run the first 5 tasks only for fast local iteration.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, mean_recall_at_20, ...},
    per_task: [...]}``.
    """
    args: list[str] = ["eval-retrieve", "--rerank", rerank]
    if input_path:
        args.extend(["--tasks", input_path])
    if sweep:
        args.append("--sweep")
    if min_recall_at_20 > 0.0:
        args.extend(["--min-recall-at-20", str(min_recall_at_20)])
    if quick:
        args.append("--quick")
    return _run_roam(args, root)


# W301: roam_why_slow
@_tool(
    name="roam_why_slow",
    description=(
        "Rank runtime hotspots by cost = log10(call_count + 1) * "
        "p99_latency_ms. Reads ``runtime_stats`` populated by ``roam "
        "ingest-trace``. Optionally restricts to symbols in changed "
        "files vs a base ref. Different from ``roam_hotspots`` "
        "(static-vs-runtime classification) -- this is the pure "
        "latency-weighted ranking."
    ),
)
def roam_why_slow(
    top: int = 20,
    changed: bool = False,
    base: str = "HEAD~1",
    min_calls: int = 0,
    root: str = ".",
) -> dict:
    """Rank runtime symbols by cost weight.

    WHEN TO USE: gate a PR with ``changed=True`` to ask "is this PR
    slowing down a hot path?" Pair with ``roam_hotspots`` for the
    static-vs-runtime classification.

    Parameters
    ----------
    top:
        Limit to top N hotspots (default 20).
    changed:
        Filter to symbols in changed files (vs ``base`` ref).
    base:
        Base ref for ``changed=True`` (default ``HEAD~1``).
    min_calls:
        Filter out symbols below this ``call_count``.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, hotspots: [...]}``.
    """
    args: list[str] = ["why-slow", "--top", str(top)]
    if changed:
        args.append("--changed")
    if base and base != "HEAD~1":
        args.extend(["--base", base])
    if min_calls:
        args.extend(["--min-calls", str(min_calls)])
    return _run_roam(args, root)


# W301: roam_doc_staleness
@_tool(
    name="roam_doc_staleness",
    description=(
        "Run a semantic docstring-drift audit: flag documented "
        "parameters, returns, or raises that no longer match code. "
        "Pass ``include_prose_drift`` to include optional blame-only "
        "summary drift. Different "
        "from ``roam_docs_coverage`` (missing docs ranked by PageRank) "
        "and ``roam_stale_refs`` (dangling doc links) -- this audits "
        "concrete claims in existing docs."
    ),
)
def roam_doc_staleness(
    limit: int = 20,
    days: int = 90,
    include_prose_drift: bool = False,
    root: str = ".",
) -> dict:
    """Run AST-backed semantic docstring-drift checks.

    WHEN TO USE: audit existing comments before a release or
    refactor pass. Pair with ``roam_docs_coverage`` for the inverse
    (missing-doc) view.

    Parameters
    ----------
    limit:
        Maximum number of stale symbols to display (default 20).
    days:
        Staleness threshold in days -- body changed N+ days after
        docstring (default 90).
    include_prose_drift:
        Also flag pure-prose docstrings on commit-drift alone. Off
        by default because summary docstrings stay accurate even
        when the body is refactored.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_stale, ...}, symbols: [...]}``.
    """
    args: list[str] = [
        "doc-staleness",
        "--limit",
        str(limit),
        "--days",
        str(days),
    ]
    if include_prose_drift:
        args.append("--include-prose-drift")
    return _run_roam(args, root)


# W301: roam_owner
@_tool(
    name="roam_owner",
    description=(
        "Show code ownership computed from git blame: per-author "
        "line counts, percentages, last-active dates, and a "
        "fragmentation index. Works on a file or a directory "
        "prefix. Different from ``roam_codeowners`` (which reads the "
        "CODEOWNERS file) -- this measures actual ownership."
    ),
)
def roam_owner(path: str, root: str = ".") -> dict:
    """Show actual code ownership from git blame.

    WHEN TO USE: find the right reviewer for a file or scope, or
    spot fragmentation hotspots where no single author dominates.
    Pair with ``roam_bus_factor`` for the directory-level
    knowledge-concentration risk.

    Parameters
    ----------
    path:
        File path (e.g. ``"src/roam/cli.py"``) or directory prefix
        (e.g. ``"src/roam/db/"``) in the indexed repo.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, authors: [...]}``.
    """
    return _run_roam(["owner", path], root)


# W301: roam_congestion
@_tool(
    name="roam_congestion",
    description=(
        "Detect developer congestion: files with too many concurrent "
        "authors within a sliding time window. Combines author count, "
        "churn intensity, and complexity into a congestion score that "
        "predicts merge conflicts and coordination failures. Different "
        "from ``roam_bus_factor`` (knowledge-loss risk) and "
        "``roam_owner`` (per-file blame breakdown) -- this measures "
        "too-many-cooks contention."
    ),
)
def roam_congestion(
    window: int = 90,
    min_authors: int = 3,
    limit: int = 30,
    root: str = ".",
) -> dict:
    """Detect files with too many concurrent authors.

    WHEN TO USE: predict merge-conflict / review-bottleneck risk
    before scheduling a refactor that touches the same files
    multiple teams are already editing.

    Parameters
    ----------
    window:
        Time window in days for recent activity (default 90).
    min_authors:
        Minimum distinct recent authors to flag a file as congested
        (default 3).
    limit:
        Maximum files to display (default 30).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_congested, ...}, files: [...]}``.
    """
    args: list[str] = [
        "congestion",
        "--window",
        str(window),
        "--min-authors",
        str(min_authors),
        "--limit",
        str(limit),
    ]
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# W302: refactoring cluster (Wave29 MCP wrapper backfill, sub-wave 4)
# ---------------------------------------------------------------------------
# 9 read-only "what's safe to change" wrappers that an agent reaches for
# when scoping a refactor: deletion gates, refactor-zone classification,
# stale feature-flag detection, file-decomposition suggestions, directory
# API skeletons, and the three test-surface commands (map / pyramid /
# scaffold). All 9 require a built index so they auto-inherit the W296
# cold-start guard (none appear in ``_NO_INDEX_NEEDED``). Descriptions
# use imperative voice per CLAUDE.md LAW 2; ``agent_contract.facts`` and
# verdict strings anchor on LAW 4 concrete-noun terminals (callers,
# zones, deletions, flags, files, symbols, tests).
#
# W332 canonical: ``flag-dead --config`` is a sidecar file the tool READS
# (newline-delimited known-stale flag names), so the wrapper exposes it
# as ``input_path``. ``test-scaffold --framework`` is a string enum
# (pytest / unittest / jest / ...) -- a value, not a path -- so it stays
# ``framework``. ``safe-zones --depth`` is an int control knob, not a
# file, so it stays ``depth``. ``sketch <directory>``, ``split <path>``,
# ``owner <path>``, and ``test-map <name>`` filter to a path/name already
# INDEXED in the repo, so they keep the ``path`` / ``directory`` /
# ``name`` convention used elsewhere in the wrapper surface
# (e.g. ``roam_module(path=...)``, ``roam_owner(path=...)``,
# ``roam_safe_delete(name=...)``).
#
# Pattern-1 audit (JSON-parse-on-empty): all 9 underlying CLI commands
# emit a non-empty JSON envelope on every path -- including
# symbol-not-found / file-not-found / no-deletions-detected / too-few-
# symbols cases -- so MCP callers parsing the JSON output never crash on
# empty stdout. ``test-scaffold`` and ``safe-delete`` still raise
# ``SystemExit(1)`` when the symbol is not found AFTER emitting the
# envelope; the W325 chokepoint try-parse passthrough preserves that
# envelope through the MCP runner.
# ---------------------------------------------------------------------------


# W302: roam_safe_delete
@_tool(
    name="roam_safe_delete",
    description=(
        "Fuse dead-code, blast-radius, and test-coverage signals into a "
        "single deletion verdict: SAFE / REVIEW / UNSAFE. Reports direct "
        "callers (non-test), transitive dependents, affected files, and "
        "a public-API bump that flips SAFE -> REVIEW for exported "
        "symbols whose name matches a common public-API prefix. "
        "Different from ``roam_dead_code`` (all unreferenced symbols) "
        "and ``roam_impact`` (transitive blast radius) -- this is the "
        "single go/no-go gate."
    ),
)
def roam_safe_delete(symbol: str, root: str = ".") -> dict:
    """Decide whether a symbol can be safely deleted.

    WHEN TO USE: agent is about to delete a symbol and wants a single
    verdict + reason before touching the file. Pair with
    ``roam_impact`` for the full blast radius if the verdict is
    REVIEW / UNSAFE.

    Parameters
    ----------
    symbol:
        Symbol name (e.g. ``"handleSave"`` or ``"MyClass.method"``).
        W332/Fix-D canonical -- legacy ``name=`` callers are accepted
        via the ``_PARAM_ALIASES`` machinery with a deprecation warning.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict: SAFE|REVIEW|UNSAFE, direct_callers,
    affected_files}, callers: [...]}``.
    """
    return _run_roam(["safe-delete", symbol], root)


# W302: roam_safe_zones
@_tool(
    name="roam_safe_zones",
    description=(
        "Classify the refactor containment zone around a symbol or "
        "file: ISOLATED (no external connections), CONTAINED (<=5 "
        "boundary symbols), or EXPOSED (>5). Reports strictly-internal "
        "vs boundary symbols and external caller / callee counts per "
        "boundary. Different from ``roam_impact`` (unbounded reverse "
        "blast radius) and ``roam_closure`` (exact locations needing "
        "modification) -- this maps the bounded zone where it is safe "
        "to refactor freely."
    ),
)
def roam_safe_zones(symbol: str, depth: int = 5, root: str = ".") -> dict:
    """Map the safe-to-refactor containment zone around a symbol or file.

    WHEN TO USE: scoping a refactor and want to know which symbols are
    strictly internal (safe to change freely) vs boundary (maintain
    contracts). Pair with ``roam_preflight`` for the pre-edit composite
    gate on a single seed.

    Parameters
    ----------
    symbol:
        Symbol name, ``file:symbol``, or file path to anchor the zone.
        W332/Fix-D canonical for the symbol-shaped argument -- legacy
        ``target=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning. The CLI argument is positional and accepts
        either a symbol or a file path.
    depth:
        Max BFS depth for forward and backward propagation (default 5
        -- mirrors the CLI's ``--depth`` default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, zone, internal_symbols,
    boundary_symbols, affected_files}, internal: [...], boundary: [...]}``.
    """
    return _run_roam(["safe-zones", symbol, "--depth", str(depth)], root)


# W302: roam_delete_check
@_tool(
    name="roam_delete_check",
    description=(
        "Gate the diff (working / staged / PR / HEAD) on surviving "
        "references to deleted symbols and files. Per-deletion verdict: "
        "SAFE (no surviving references), LIKELY-SAFE (survivors only in "
        "tests / docs / unreachable code), or BREAK-RISK (survivors in "
        "reachable code). Different from ``roam_critique`` (PR-wide "
        "diff review) -- this targets the deletion surface specifically "
        "with CI-gate semantics (overall BREAK-RISK trips the gate)."
    ),
)
def roam_delete_check(
    source: str = "working",
    base_ref: str = "main",
    commit_range: str = "",
    reachable_from: str = "",
    ci: bool = False,
    count: int = 20,
    include_line_deletions: bool = False,
    root: str = ".",
) -> dict:
    """Gate a diff on surviving references to deleted symbols / files.

    WHEN TO USE: before merging a PR that removes symbols or files,
    confirm no caller survives in reachable code. Pair with
    ``roam_critique`` for the broader PR-diff review.

    Parameters
    ----------
    source:
        Which diff to gate: ``working`` (default) / ``staged`` / ``pr``
        / ``head``. ``pr`` uses ``base_ref...HEAD``; ``head`` uses the
        last commit.
    base_ref:
        Base branch when ``source="pr"`` (default ``"main"``).
    commit_range:
        Arbitrary git range (e.g. ``"HEAD~3..HEAD"``). Empty (default)
        uses the ``source`` selector.
    reachable_from:
        Anchor reachability classification at this entry symbol.
        Empty (default) uses the orphan-set fallback.
    ci:
        Surface BREAK-RISK as a CI-failing verdict (the underlying CLI
        exit 5 collapses into the JSON envelope when called via MCP).
    count:
        Max deletions to report in detail (default 20).
    include_line_deletions:
        Also gate on raw deleted lines (slow). Off by default --
        symbols-only mode is the precise path.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, overall: SAFE|LIKELY-SAFE|BREAK-RISK,
    break_risk, likely_safe, safe}, deletions: [...]}``.
    """
    args: list[str] = [
        "delete-check",
        "--source",
        source,
        "--base-ref",
        base_ref,
        "-n",
        str(count),
    ]
    if commit_range:
        args.extend(["--commit-range", commit_range])
    if reachable_from:
        args.extend(["--reachable-from", reachable_from])
    if ci:
        args.append("--ci")
    if include_line_deletions:
        args.append("--include-line-deletions")
    return _run_roam(args, root)


# W302: roam_flag_dead
@_tool(
    name="roam_flag_dead",
    description=(
        "Detect potentially stale feature-flag code: flags referenced "
        "only once, flags always checked with the same boolean default, "
        "and flags clustered in a single file. Recognises LaunchDarkly, "
        "Unleash, Split, generic ``feature_flag(...)`` calls, and "
        "``FEATURE_*`` env-var patterns. Different from "
        "``roam_dead_code`` (graph-unreachable symbols) -- this targets "
        "code that is alive in the graph but gated behind flags that "
        "may never fire."
    ),
)
def roam_flag_dead(
    input_path: str = "",
    include_tests: bool = False,
    root: str = ".",
) -> dict:
    """Detect stale feature-flag code (conditionally-dead code).

    WHEN TO USE: clean-up pass to find flags that have lived past
    their useful life. Pair with ``roam_dead_code`` to find symbols
    that became unreachable once the flag was disabled.

    Parameters
    ----------
    input_path:
        Path to a sidecar file listing known-stale flag names, one per
        line. Flags in the list get a guaranteed ``stale`` verdict.
        Empty (default) relies on the heuristics alone. Sidecar file
        the tool reads (W332 canonical ``input_path``).
    include_tests:
        Include test files, fixtures, docs, and examples in the scan.
        Off by default because flag references in fixtures are
        intentional and noisy.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_flags, stale, likely_stale,
    suspect, ok}, flags: [...]}``.
    """
    args: list[str] = ["flag-dead"]
    if input_path:
        args.extend(["--config", input_path])
    if include_tests:
        args.append("--include-tests")
    return _run_roam(args, root)


# W302: roam_sketch
@_tool(
    name="roam_sketch",
    description=(
        "Render a compact structural skeleton of a directory: every "
        "file's exported symbols with kind, signature, line range, and "
        "first-line docstring. Different from ``roam_understand`` "
        "(broader project overview) and ``roam_file_info`` (one-file "
        "skeleton) -- this is the directory-level API surface in a "
        "single view, with optional ``full=True`` to include private "
        "symbols."
    ),
)
def roam_sketch(directory: str, full: bool = False, root: str = ".") -> dict:
    """Render a compact directory API skeleton.

    WHEN TO USE: first read on an unfamiliar directory before touching
    a file inside it. Pair with ``roam_module`` for the import graph
    of the same directory.

    Parameters
    ----------
    directory:
        Directory path relative to the repo root (e.g.
        ``"src/roam/db"``).
    full:
        Include private symbols (default False -- only exported
        symbols, matching the CLI's default).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, file_count, symbol_count}, files:
    {<path>: [{name, kind, signature, line_start, line_end,
    docstring}]}}``.
    """
    args: list[str] = ["sketch", directory]
    if full:
        args.append("--full")
    return _run_roam(args, root)


# W302: roam_split
@_tool(
    name="roam_split",
    description=(
        "Analyse a file's internal call / reference graph and propose "
        "natural decomposition groups via Louvain community detection. "
        "Reports per-group isolation %, internal vs cross-group edges, "
        "and ranked extraction candidates (groups with >=3 symbols and "
        ">=50% isolation). Different from ``roam_clusters`` "
        "(repo-wide module partitioning) -- this analyses ONE file's "
        "internal seams."
    ),
)
def roam_split(path: str, min_group: int = 2, root: str = ".") -> dict:
    """Suggest how to decompose a single file into smaller modules.

    WHEN TO USE: the file is too big and you want to know whether it
    contains 2-3 cohesive sub-modules that could split out cleanly.
    Pair with ``roam_safe_zones`` once you have a candidate group to
    verify the extraction surface.

    Parameters
    ----------
    path:
        File path (e.g. ``"src/roam/cli.py"``) in the indexed repo.
    min_group:
        Minimum symbols per group (default 2 -- mirrors the CLI's
        ``--min-group`` default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, groups, total_symbols, extractable},
    groups: [...], suggestions: [...]}``.
    """
    return _run_roam(["split", path, "--min-group", str(min_group)], root)


# W302: roam_test_map
@_tool(
    name="roam_test_map",
    description=(
        "Map a symbol or file to its current test coverage: direct "
        "test edges (test file calls the symbol), file-level importers "
        "(test file imports the symbol's module), and convention-based "
        "matches (Salesforce ``<Name>Test`` / ``<Name>_Test`` "
        "classes). Different from ``roam_test_gaps`` (untested symbols "
        "in changed files) and ``roam_affected_tests`` (forward trace "
        "from changes to affected tests) -- this is the lookup for "
        "what currently exercises a given symbol."
    ),
)
def roam_test_map(symbol: str, root: str = ".") -> dict:
    """Look up the tests that currently cover a symbol or file.

    WHEN TO USE: deciding whether to delete or refactor a symbol --
    confirm what would lose coverage. Pair with ``roam_test_scaffold``
    when no coverage exists.

    Parameters
    ----------
    symbol:
        Symbol name (e.g. ``"handleSave"``) or file path (e.g.
        ``"src/roam/cli.py"``). Path-shaped inputs are dispatched as a
        file lookup; everything else is treated as a symbol name.
        W332/Fix-D canonical for the symbol-shaped argument -- legacy
        ``name=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, direct_tests, test_importers, ...},
    direct_tests: [...], test_importers: [...]}``.
    """
    return _run_roam(["test-map", symbol], root)


# W302: roam_test_pyramid
@_tool(
    name="roam_test_pyramid",
    description=(
        "Count indexed test files by kind (unit / integration / e2e / "
        "smoke / unknown) using path and name conventions, and flag "
        "inverted pyramids (when ``e2e + integration > unit``). "
        "Different from ``roam_test_gaps`` (missing coverage) -- this "
        "measures the shape of the existing test suite for slow-CI "
        "risk."
    ),
)
def roam_test_pyramid(root: str = ".") -> dict:
    """Count tests by kind, flag inverted pyramids.

    WHEN TO USE: triage CI duration -- a pyramid with more
    integration + e2e tests than unit tests pays for slow runs. Pair
    with ``roam_why_slow`` for the runtime-cost view on the hottest
    tests.

    Parameters
    ----------
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total, unit, integration, e2e,
    smoke, unknown}, counts: {...}, samples: {...}}``.
    """
    return _run_roam(["test-pyramid"], root)


# W302: roam_test_scaffold
@_tool(
    name="roam_test_scaffold",
    description=(
        "Generate a test-file skeleton for a source file or symbol "
        "(functions, classes, methods) with the right imports and "
        "per-symbol stub blocks. Supports pytest / unittest (Python), "
        "jest / mocha / vitest (JS/TS), Go testing, JUnit4 / JUnit5 "
        "(Java), and RSpec / Minitest (Ruby). Dry-run by default; pair "
        "with ``roam_test_map`` first to confirm no existing coverage. "
        "Skips symbols that already have tests in the target file."
    ),
)
def roam_test_scaffold(
    symbol: str,
    write: bool = False,
    framework: str = "",
    root: str = ".",
) -> dict:
    """Scaffold a test file from indexed source symbols.

    WHEN TO USE: ``roam_test_map`` reported "no tests found" for a
    symbol or file, and you want a fresh stub to fill in.

    Parameters
    ----------
    symbol:
        Source file path (e.g. ``"src/roam/cli.py"``) or symbol name
        (e.g. ``"MyClass"``). Path-shaped inputs scaffold every
        testable symbol in the file; symbol-shaped inputs scaffold one
        target plus its methods (for classes). W332/Fix-D canonical
        for the symbol-shaped argument -- legacy ``name=`` callers are
        accepted via ``_PARAM_ALIASES`` with a deprecation warning.
    write:
        Write the scaffold to disk. Off by default -- dry-run returns
        the scaffold text in the envelope without touching the
        filesystem.
    framework:
        Override the test framework (e.g. ``"pytest"``, ``"unittest"``,
        ``"jest"``, ``"junit5"``, ``"rspec"``). Empty (default) picks
        the language default.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, scaffolded, skipped, total_symbols,
    language, framework, written}, scaffold: "<text>",
    symbols: [...]}``.
    """
    args: list[str] = ["test-scaffold", symbol]
    if write:
        args.append("--write")
    if framework:
        args.extend(["--framework", framework])
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# W303: test surface cluster (Wave29 MCP wrapper backfill, sub-wave 5)
# ---------------------------------------------------------------------------
# 5 read-only wrappers that pair test coverage with the world-model
# classifiers an agent consults to reason about retry safety, transaction
# correctness, and parameter-to-sink dataflow. The W303 plan listed 8
# wrappers but W302 already shipped the three test-surface commands
# (``test-map`` / ``test-pyramid`` / ``test-scaffold``); the remaining 5
# land here: ``coverage-gaps`` (gate-coverage analysis) and the four
# world-model classifiers ``side-effects`` / ``idempotency`` /
# ``causal-graph`` / ``tx-boundaries``. All 5 require a built index so
# they auto-inherit the W296 cold-start guard (none appear in
# ``_NO_INDEX_NEEDED``). Descriptions use imperative voice per CLAUDE.md
# LAW 2; verdict strings anchor on LAW 4 concrete-noun terminals
# (symbols, gates, entries, classifications, edges, boundaries, files).
#
# W332 canonical: ``coverage-gaps --config`` is a sidecar
# ``.roam-gates.yml`` file the tool READS, so the wrapper exposes it
# as ``input_path``. ``side-effects``, ``idempotency``, ``causal-graph``,
# and ``tx-boundaries`` take an optional positional ``[SYMBOL]`` filter,
# so they use the W332/Fix-D canonical ``symbol`` argument with empty
# default (mirrors CLI behaviour: empty -> scan-all). ``--kind`` /
# ``--classification`` filters are string enums (a value, not a path),
# so they keep their natural names. ``--top`` / ``--max-depth`` are int
# control knobs, not files. ``coverage-gaps --import-report`` (repeated
# trace-ingestion path) is intentionally omitted from the wrapper
# surface; agents should run the CLI directly for trace ingestion.
#
# Pattern-1 audit (JSON-parse-on-empty): all 5 underlying CLI commands
# emit a non-empty JSON envelope on every path -- including no-symbols-
# matched / no-effects-classified / no-boundaries-found cases -- so MCP
# callers parsing the JSON output never crash on empty stdout.
# ---------------------------------------------------------------------------


# W303: roam_coverage_gaps
@_tool(
    name="roam_coverage_gaps",
    description=(
        "Find unprotected entry points: top-level exported functions / "
        "methods that have no call-graph path to a required gate symbol "
        "(auth / permission / validation). Supports exact gate names, "
        "regex patterns, framework presets (python / javascript / go / "
        "java-maven / rust), and a ``.roam-gates.yml`` sidecar config. "
        "Different from ``roam_auth_gaps`` (PHP/Laravel source analysis) "
        "and ``roam_test_gaps`` (untested symbols in changed files) -- "
        "this walks the call graph to verify every entry reaches a "
        "required gate."
    ),
)
def roam_coverage_gaps(
    gate: str = "",
    gate_pattern: str = "",
    scope: str = "",
    entry_pattern: str = "",
    max_depth: int = 8,
    preset: str = "",
    auto_detect: bool = False,
    input_path: str = "",
    root: str = ".",
) -> dict:
    """Find entry points with no path to a required gate symbol.

    WHEN TO USE: audit which exported handlers / routes / controllers
    skip a required auth or permission check. Pair with
    ``roam_auth_gaps`` for PHP/Laravel-specific source analysis and
    ``roam_entry_points`` to list the entry surface itself.

    Parameters
    ----------
    gate:
        Comma-separated gate symbol names (e.g.
        ``"requireAuth,validateToken"``). Empty (default) relies on
        ``gate_pattern`` / ``preset`` / ``auto_detect`` instead.
    gate_pattern:
        Regex matching gate symbols by name (e.g.
        ``"auth|permission|guard"``). Empty (default) skips
        regex matching.
    scope:
        File scope glob (e.g. ``"app/routes/**"``). Empty (default)
        scans every indexed entry. Mirrors the ``--scope`` CLI flag.
    entry_pattern:
        Regex filter on entry-point names (e.g.
        ``"handler|controller"``). Empty (default) keeps every entry.
    max_depth:
        Maximum BFS depth from entry to gate (default 8 -- mirrors the
        CLI default per LAW 11).
    preset:
        Built-in gate preset name (``python`` / ``javascript`` / ``go``
        / ``java-maven`` / ``rust``). Empty (default) skips presets.
    auto_detect:
        Auto-detect the framework preset from project files. Off by
        default -- explicit ``preset`` / ``gate`` wins.
    input_path:
        Path to a ``.roam-gates.yml`` config file the tool reads.
        Empty (default) skips the sidecar. Sidecar file the tool reads
        (W332 canonical ``input_path``).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_entries, uncovered, ...},
    entries: [...]}``.
    """
    args: list[str] = ["coverage-gaps", "--max-depth", str(max_depth)]
    if gate:
        args.extend(["--gate", gate])
    if gate_pattern:
        args.extend(["--gate-pattern", gate_pattern])
    if scope:
        args.extend(["--scope", scope])
    if entry_pattern:
        args.extend(["--entry-pattern", entry_pattern])
    if preset:
        args.extend(["--preset", preset])
    if auto_detect:
        args.append("--auto-detect")
    if input_path:
        args.extend(["--config", input_path])
    return _run_roam(args, root)


# W303: roam_side_effects
@_tool(
    name="roam_side_effects",
    description=(
        "Classify symbols by side-effect bucket: ``none`` (pure), "
        "``io_read`` (disk / network / DB read), ``io_write`` (disk / "
        "network / DB write), ``mutation`` (global / module state "
        "mutation), ``process`` (subprocess / thread / async), or "
        "``unknown``. Coarse five-bucket taxonomy designed for agent "
        "decisions. Different from ``roam_effects`` (finer 11-kind "
        "taxonomy + transitive propagation) -- this is the agent's "
        "go/no-go classifier for ``can I retry this safely?``."
    ),
)
def roam_side_effects(
    symbol: str = "",
    kind: str = "",
    top: int = 50,
    root: str = ".",
) -> dict:
    """Classify symbols by side effects (none / io / mutation / process).

    WHEN TO USE: deciding whether to retry / replay a symbol, or
    auditing the side-effect surface of a module. Pair with
    ``roam_idempotency`` for the retry-safety verdict that composes on
    top of this classification.

    Parameters
    ----------
    symbol:
        Optional symbol name to filter to one classification (e.g.
        ``"handleSave"``). Empty (default) scans every indexed symbol.
        W332/Fix-D canonical for the symbol-shaped argument.
    kind:
        Filter to one side-effect kind: ``none`` / ``io_read`` /
        ``io_write`` / ``mutation`` / ``process`` / ``unknown``. Empty
        (default) returns every kind.
    top:
        Maximum classifications to surface (default 50 -- mirrors the
        CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_classified, ...},
    classifications: [...]}``.
    """
    args: list[str] = ["side-effects"]
    if symbol:
        args.append(symbol)
    if kind:
        args.extend(["--kind", kind])
    args.extend(["--top", str(top)])
    return _run_roam(args, root)


# W303: roam_idempotency
@_tool(
    name="roam_idempotency",
    description=(
        "Classify symbols by retry safety: ``idempotent`` (pure, "
        "read-only I/O, write-with-check patterns like "
        "``mkdir(exist_ok=True)`` / ``INSERT OR IGNORE`` / ``UPSERT`` "
        "/ ``if not exists: create``), ``non_idempotent`` (naive "
        "writes, mutations, appends), or ``unknown`` (process spawn / "
        "unreadable body). Composes on top of ``roam_side_effects``. "
        "Different from ``roam_tx_boundaries`` (transaction "
        "correctness) -- this answers ``is it safe to retry?``."
    ),
)
def roam_idempotency(
    symbol: str = "",
    kind: str = "",
    top: int = 50,
    root: str = ".",
) -> dict:
    """Classify symbols by idempotency (safe-to-retry).

    WHEN TO USE: deciding whether an agent's failed step can be
    re-run without doubling its effect. Pair with ``roam_side_effects``
    for the underlying effect bucket and ``roam_tx_boundaries`` for
    transaction-scope reasoning.

    Parameters
    ----------
    symbol:
        Optional symbol name to filter to one classification (e.g.
        ``"createUser"``). Empty (default) scans every indexed symbol.
        W332/Fix-D canonical for the symbol-shaped argument.
    kind:
        Filter to one idempotency kind: ``idempotent`` /
        ``non_idempotent`` / ``unknown``. Empty (default) returns every
        kind.
    top:
        Maximum classifications to surface (default 50 -- mirrors the
        CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_classified, ...},
    classifications: [...]}``.
    """
    args: list[str] = ["idempotency"]
    if symbol:
        args.append(symbol)
    if kind:
        args.extend(["--kind", kind])
    args.extend(["--top", str(top)])
    return _run_roam(args, root)


# W303: roam_causal_graph
@_tool(
    name="roam_causal_graph",
    description=(
        "Build per-symbol causal graphs: edges from inputs "
        "(parameters / globals / env reads) to sinks "
        "(side-effecting calls / return / raise / mutation). Six "
        "causal kinds: ``param_to_effect``, ``param_to_return``, "
        "``global_to_effect``, ``global_to_mutation``, "
        "``env_to_effect``, ``param_to_raise``. Heuristic line-level "
        "text scan -- false negatives expected. Different from "
        "``roam_taint`` (cross-symbol taint propagation) -- this is "
        "intra-symbol dataflow only."
    ),
)
def roam_causal_graph(
    symbol: str = "",
    kind: str = "",
    top: int = 20,
    root: str = ".",
) -> dict:
    """Build per-symbol causal graphs (input -> sink dataflow).

    WHEN TO USE: tracing how a function parameter or env read flows
    into a side-effecting call before changing the signature. Pair
    with ``roam_taint`` for cross-symbol propagation and
    ``roam_side_effects`` for the sink classification.

    Parameters
    ----------
    symbol:
        Optional symbol name to filter to one graph (e.g.
        ``"handleSave"``). Empty (default) scans every indexed symbol.
        W332/Fix-D canonical for the symbol-shaped argument.
    kind:
        Filter to one causal kind: ``param_to_effect`` /
        ``param_to_return`` / ``global_to_effect`` /
        ``global_to_mutation`` / ``env_to_effect`` / ``param_to_raise``.
        Empty (default) returns every kind.
    top:
        Maximum graphs to surface (default 20 -- mirrors the CLI
        default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_graphs, total_edges, ...},
    graphs: [...]}``.
    """
    args: list[str] = ["causal-graph"]
    if symbol:
        args.append(symbol)
    if kind:
        args.extend(["--kind", kind])
    args.extend(["--top", str(top)])
    return _run_roam(args, root)


# W303: roam_tx_boundaries
@_tool(
    name="roam_tx_boundaries",
    description=(
        "Classify functions by transactional safety: "
        "``transactional`` (begin matched by commit/rollback, all "
        "mutations inside scope), ``partial_transactional`` (mutations "
        "both inside AND outside scope), ``unsafe_mutation`` (mutations "
        "OUTSIDE any transaction wrapper -- latent bug), "
        "``unmatched_begin`` (begin without commit/rollback -- leak), "
        "``unmatched_commit``, ``non_transactional``, or ``unknown``. "
        "Composes on top of ``roam_side_effects``. Different from "
        "``roam_idempotency`` (retry safety) -- this gates transaction "
        "correctness."
    ),
)
def roam_tx_boundaries(
    symbol: str = "",
    classification: str = "",
    top: int = 30,
    root: str = ".",
) -> dict:
    """Classify functions by transaction-scope correctness.

    WHEN TO USE: auditing a service for missing begin/commit pairs or
    mutations leaking outside transactions. Pair with
    ``roam_side_effects`` for the underlying mutation classification.

    Parameters
    ----------
    symbol:
        Optional symbol name to filter to one boundary (e.g.
        ``"transferFunds"``). Empty (default) scans every indexed
        symbol. W332/Fix-D canonical for the symbol-shaped argument.
    classification:
        Filter to one classification: ``transactional`` /
        ``partial_transactional`` / ``unsafe_mutation`` /
        ``unmatched_begin`` / ``unmatched_commit`` /
        ``non_transactional`` / ``unknown``. Empty (default) returns
        every classification.
    top:
        Maximum boundaries to surface (default 30 -- mirrors the CLI
        default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total_classified, ...},
    boundaries: [...]}``.
    """
    args: list[str] = ["tx-boundaries"]
    if symbol:
        args.append(symbol)
    if classification:
        args.extend(["--classification", classification])
    args.extend(["--top", str(top)])
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# W304: agent-OS daily flow cluster (Wave29 MCP wrapper backfill, sub-wave 6)
# ---------------------------------------------------------------------------
# 10 read-only wrappers covering the "what an agent runs to plan / brief
# itself" surface. Composite recipes that compose other commands
# (``brief`` / ``next`` / ``plan`` / ``agent-plan`` / ``agent-context`` /
# ``agent-score`` / ``guard`` / ``adversarial`` / ``migration-plan`` /
# ``recommend``). All 10 require a built index so they auto-inherit the
# W296 cold-start guard (none appear in ``_NO_INDEX_NEEDED``).
# Descriptions use imperative voice per CLAUDE.md LAW 2; verdict strings
# anchor on LAW 4 concrete-noun terminals (commands, symbols, agents,
# tasks, steps, runs, challenges, recommendations).
#
# W332 canonical: ``migration-plan --target`` is a sidecar YAML file the
# tool READS, so the wrapper exposes it as ``input_path``. ``recommend
# <symbol>`` / ``guard <name>`` / ``plan <target>`` take a symbol-shaped
# positional, so they use the W332/Fix-D canonical ``symbol`` argument.
# ``agent_id`` / ``n_agents`` are int control knobs (not files), so they
# keep their natural names. ``adversarial --range`` is a git-range
# string (a value, not a path), so it stays ``commit_range`` to match
# the pre-existing ``roam_delete_check(commit_range=...)`` convention.
# Choice options (``--task`` / ``--format`` / ``--severity`` /
# ``--max-risk``) are string enums (a value, not a path), so they keep
# their natural names.
#
# Pattern-1 audit (JSON-parse-on-empty): all 10 underlying CLI commands
# emit a non-empty JSON envelope on every path -- including no-runs /
# no-changes-detected / no-symbol-found / no-target-moves cases -- so
# MCP callers parsing the JSON output never crash on empty stdout.
# ``plan``, ``agent-context``, and ``recommend`` still raise
# ``SystemExit(1)`` on no-target / agent-not-found / symbol-not-found
# AFTER emitting the envelope; the W325 chokepoint try-parse passthrough
# preserves that envelope through the MCP runner.
# ---------------------------------------------------------------------------


# W304: roam_brief
@_tool(
    name="roam_brief",
    description=(
        "Compose a one-page agent briefing covering five sections: "
        "``next`` (what ``roam next`` would recommend), ``highlights`` "
        "(stack / top danger zones / top mined laws from "
        "``roam agents-md``), ``pr_bundle`` (current PR-bundle status on "
        "the active branch), ``mode`` (active agent mode and its "
        "allow-list size), and ``runs`` (the N most-recent runs from "
        "the ledger). Designed as the FIRST command an agent runs when "
        "joining a roam-indexed repo. Different from ``roam_next`` "
        "(single-command router) -- this is the verdict-first session "
        "kickoff packet."
    ),
)
def roam_brief(
    no_next: bool = False,
    no_pr_bundle: bool = False,
    no_highlights: bool = False,
    no_runs: bool = False,
    no_mode: bool = False,
    top_runs: int = 3,
    root: str = ".",
) -> dict:
    """Compose a one-page session kickoff briefing.

    WHEN TO USE: agent joining a fresh session and wants the most
    decision-relevant state in one call. Pair with ``roam_next`` for
    just the router recommendation or ``roam_workflow`` for curated
    multi-step recipes.

    Parameters
    ----------
    no_next:
        Skip the next-command recommendation block. Off by default.
    no_pr_bundle:
        Skip the PR-bundle status block (useful when not in PR mode).
        Off by default.
    no_highlights:
        Skip the stack / danger / laws highlights block. Off by default.
    no_runs:
        Skip the recent-runs block. Off by default.
    no_mode:
        Skip the active-mode block. Off by default.
    top_runs:
        How many recent closed runs to include (default 3 -- mirrors
        the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, next: {...}, highlights: {...},
    pr_bundle: {...}, mode: {...}, runs: {...}}``.
    """
    args: list[str] = ["brief", "--top-runs", str(top_runs)]
    if no_next:
        args.append("--no-next")
    if no_pr_bundle:
        args.append("--no-pr-bundle")
    if no_highlights:
        args.append("--no-highlights")
    if no_runs:
        args.append("--no-runs")
    if no_mode:
        args.append("--no-mode")
    return _run_roam(args, root)


# W304: roam_next
@_tool(
    name="roam_next",
    description=(
        "Suggest the next ``roam`` command based on cheap repo-state "
        "signals: index presence, staleness, working-tree dirtiness, "
        "recent envelope, and recent memory. Emits one imperative "
        "recommendation in <200ms. Different from ``roam_brief`` "
        "(multi-section session kickoff) and ``roam_workflow`` "
        "(curated multi-step recipes) -- this is the single-command "
        "router."
    ),
)
def roam_next(root: str = ".") -> dict:
    """Recommend the next command based on current repo state.

    WHEN TO USE: agent is unsure what to run next and wants a
    bounded router that picks one command + reason. Pair with
    ``roam_brief`` for the multi-section session packet.

    Parameters
    ----------
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, command, reason, state,
    partial_success}, next_steps: [...], state: {...}}``.
    """
    return _run_roam(["next"], root)


# W304: roam_recommend
@_tool(
    name="roam_recommend",
    description=(
        "Surface symbols related to a given symbol via three signal "
        "sources combined: call-graph neighbours (1-hop in + out), "
        "git co-change (other symbols whose files changed in the same "
        "commits), and persisted clone siblings (when ``roam clones "
        "--persist`` was run). Each candidate gets a score that's the "
        "normalised sum of the three contributions. Different from "
        "``roam_impact`` (transitive blast radius) and "
        "``roam_neighbours`` (graph-only 1-hop neighbours) -- this "
        "fuses co-change + clones into the ranking."
    ),
)
def roam_recommend(
    symbol: str,
    limit: int = 10,
    root: str = ".",
) -> dict:
    """Recommend related symbols using call-graph + co-change + clones.

    WHEN TO USE: agent is about to touch a symbol and wants to know
    which other symbols to read first. Pair with ``roam_preflight``
    for the pre-edit composite gate and ``roam_context`` for the
    read-order packet.

    Parameters
    ----------
    symbol:
        Symbol name (e.g. ``"handleSave"``) or qualified name (e.g.
        ``"module.handleSave"``). W332/Fix-D canonical for the
        symbol-shaped argument -- legacy ``name=`` callers are accepted
        via ``_PARAM_ALIASES`` with a deprecation warning.
    limit:
        Top N recommendations to surface (default 10 -- mirrors the
        CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, count}, recommendations: [...]}``.
    """
    return _run_roam(["recommend", symbol, "--limit", str(limit)], root)


# W304: roam_plan
@_tool(
    name="roam_plan",
    description=(
        "Generate a structured execution plan for modifying code: "
        "read-order (call-graph BFS), invariants (mined contracts), "
        "blast-radius preview, and per-task heuristics. Five task "
        "types: ``refactor`` / ``debug`` / ``extend`` / ``review`` / "
        "``understand``. Different from ``roam_plan_refactor`` "
        "(refactoring-specific simulation) and ``roam_preflight`` "
        "(blast-radius gate) -- this is the general-purpose work plan "
        "for any task type."
    ),
)
def roam_plan(
    symbol: str = "",
    task: str = "refactor",
    path: str = "",
    staged: bool = False,
    depth: int = 2,
    root: str = ".",
) -> dict:
    """Generate a structured execution plan for a coding task.

    WHEN TO USE: agent is about to start a task and wants read-order,
    invariants, and per-task heuristics in one envelope. Pair with
    ``roam_preflight`` before edits and ``roam_context`` for the
    single-symbol read-order packet.

    Parameters
    ----------
    symbol:
        Symbol name or file path to plan for (e.g. ``"handleSave"`` or
        ``"src/api.py"``). Empty (default) falls back to ``path`` or
        ``staged``. W332/Fix-D canonical for the symbol-shaped argument
        -- legacy ``name=`` / ``target=`` callers are accepted via
        ``_PARAM_ALIASES`` with a deprecation warning.
    task:
        Task type: ``refactor`` (default) / ``debug`` / ``extend`` /
        ``review`` / ``understand``. Mirrors the CLI default per LAW 11.
    path:
        Explicit file path to plan for (e.g. ``"src/api.py"``). Empty
        (default) uses ``symbol`` or ``staged`` instead. W347/Fix-D
        canonical for filesystem paths -- legacy ``file_path=`` /
        ``filename=`` / ``filepath=`` / ``file=`` callers are accepted
        via ``_PARAM_ALIASES`` with a deprecation warning.
    staged:
        Plan for staged changes (mirrors ``--staged`` flag). Off by
        default -- explicit ``symbol`` / ``path`` wins.
    depth:
        Call-graph depth for the read-order section (default 2 --
        mirrors the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, task, ...}, read_order: [...],
    invariants: [...], blast_radius: {...}}``.
    """
    args: list[str] = ["plan", "--task", task, "--depth", str(depth)]
    if symbol:
        args.append(symbol)
    if path:
        args.extend(["--path", path])
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


# W304: roam_agent_plan
@_tool(
    name="roam_agent_plan",
    description=(
        "Decompose partitions into dependency-ordered multi-agent "
        "tasks: per-task write scope, read-only dependencies, "
        "interface contracts, phase schedule, and merge sequencing. "
        "Supports ``plain`` / ``json`` / ``claude-teams`` output "
        "formats. Different from ``roam_partition`` (raw analytical "
        "manifest) and ``roam_orchestrate`` (operational dispatch) -- "
        "this is the dependency-ordered phase schedule."
    ),
)
def roam_agent_plan(
    agents: int,
    output_format: str = "plain",
    root: str = ".",
) -> dict:
    """Decompose partitions into a dependency-ordered multi-agent plan.

    WHEN TO USE: planning a parallel multi-agent run and want the
    phase + handoff schedule. Pair with ``roam_agent_context`` for
    a per-worker focused slice.

    Parameters
    ----------
    agents:
        Number of agents / tasks to generate (1 or more, no default
        in the CLI -- mirrors the required CLI flag per LAW 11).
    output_format:
        Output format: ``plain`` (default) / ``json`` / ``claude-teams``.
        Mirrors the CLI default per LAW 11.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, n_agents, tasks, handoffs,
    conflict_probability}, tasks: [...], handoffs: [...]}``.
    """
    return _run_roam(
        ["agent-plan", "--agents", str(agents), "--format", output_format],
        root,
    )


# W304: roam_agent_context
@_tool(
    name="roam_agent_context",
    description=(
        "Extract a single agent's partition from the full agent plan: "
        "write scope, read-only dependencies, interface contracts, "
        "coordination instructions, and key symbols. Different from "
        "``roam_agent_plan`` (full multi-agent view) and "
        "``roam_orchestrate`` (operational dispatch with merge order) "
        "-- this is the focused per-worker packet for one agent."
    ),
)
def roam_agent_context(
    agent_id: int,
    agents: int = 0,
    root: str = ".",
) -> dict:
    """Extract one agent's partition slice from the multi-agent plan.

    WHEN TO USE: dispatching a sub-agent and want its focused context
    blob (write scope + interface contracts + coordination notes).
    Pair with ``roam_agent_plan`` for the full multi-agent view.

    Parameters
    ----------
    agent_id:
        Worker ID (1-based). Required by the CLI per LAW 11.
    agents:
        Total number of agents used for partitioning. 0 (default) lets
        the CLI pick ``max(agent_id, 2)`` -- mirrors the CLI default
        per LAW 11.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, agent_id, ...}, agent: {...},
    write_files: [...], read_only_dependencies: [...],
    interface_contracts: [...], key_symbols: [...]}``.
    """
    args: list[str] = ["agent-context", "--agent-id", str(agent_id)]
    if agents > 0:
        args.extend(["--agents", str(agents)])
    return _run_roam(args, root)


# W304: roam_agent_score
@_tool(
    name="roam_agent_score",
    description=(
        "Aggregate runs from the local ledger and score each agent on "
        "a 0..100 composite (run completion, gate adherence, "
        "preflight compliance, blast accuracy, replay survival). "
        "Empty state (no runs / no matching runs) returns a clean "
        'envelope with ``state: "no_data"`` -- never empty stdout, '
        "never a crash. Different from ``roam_runs_verify`` (HMAC "
        "tamper-detection) -- this is the per-agent quality score "
        "across runs."
    ),
)
def roam_agent_score(
    agent: str = "",
    since: str = "",
    top: int = 0,
    root: str = ".",
) -> dict:
    """Score each agent on a 0..100 composite across recent runs.

    WHEN TO USE: triage which agent is producing the highest-quality
    runs. Pair with ``roam_runs_verify`` for ledger tamper-detection
    on a specific run.

    Parameters
    ----------
    agent:
        Filter to runs by this agent name. Empty (default) scores all
        agents. Mirrors the CLI default per LAW 11.
    since:
        Filter to runs started at >= ``SINCE`` (ISO-8601, e.g.
        ``"2026-05-01T00:00:00Z"``). Empty (default) keeps every run.
    top:
        Cap agents reported to N highest scores. 0 (default) returns
        every agent -- mirrors the CLI default per LAW 11.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, agents_scored, state,
    partial_success}, agents: [...]}``.
    """
    args: list[str] = ["agent-score"]
    if agent:
        args.extend(["--agent", agent])
    if since:
        args.extend(["--since", since])
    if top > 0:
        args.extend(["--top", str(top)])
    return _run_roam(args, root)


# W304: roam_guard
@_tool(
    name="roam_guard",
    description=(
        "Check breaking-change risk for a symbol before editing: "
        "0..100 risk score with component breakdown (blast radius, "
        "complexity, centrality, test gap, layer analysis) plus "
        "caller / callee lists and covering tests -- all within a "
        "~2K-token budget. Different from ``roam_preflight`` "
        "(file / staged / coupling / convention / fitness composite) "
        "-- this is the per-symbol quantified risk score for "
        "sub-agent dispatch."
    ),
)
def roam_guard(symbol: str, root: str = ".") -> dict:
    """Check breaking-change risk for a symbol before editing.

    WHEN TO USE: sub-agent is about to edit a single symbol and
    wants a bounded ~2K-token risk packet. Pair with
    ``roam_preflight`` for the composite gate on files / staged
    changes.

    Parameters
    ----------
    symbol:
        Symbol name (e.g. ``"handleSave"``). W332/Fix-D canonical for
        the symbol-shaped argument -- legacy ``name=`` callers are
        accepted via ``_PARAM_ALIASES`` with a deprecation warning.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, risk_score, ...},
    components: {...}, callers: [...], callees: [...], tests: [...]}``.
    """
    return _run_roam(["guard", symbol], root)


# W304: roam_adversarial
@_tool(
    name="roam_adversarial",
    description=(
        "Frame architectural issues in changed files as challenges "
        "the developer must defend: CRITICAL (new cyclic dependencies), "
        "HIGH (layer violations, high-confidence anti-patterns), "
        "WARNING (cross-cluster coupling, high fan-out), INFO "
        "(orphaned symbols). Composes cycles + clusters + layers + "
        "catalog + dead + complexity. Different from ``roam_diff`` "
        "(blast-radius facts) -- this is the architecture-review "
        "framing for code-review agents."
    ),
)
async def roam_adversarial(
    staged: bool = False,
    commit_range: str = "",
    severity: str = "low",
    fail_on_critical: bool = False,
    output_format: str = "text",
    root: str = ".",
    summarize: bool | None = None,
    compress_mode: str = "off",
    ctx: _Context | None = None,
) -> dict:
    """Generate adversarial architecture challenges on changed files.

    WHEN TO USE: code-review agent reviewing a diff and wants
    architectural challenges framed as defendable choices. Pair with
    ``roam_critique`` for the broader PR-diff review and
    ``roam_preflight`` for the pre-edit composite gate.

    Parameters
    ----------
    staged:
        Review staged changes only. Off by default -- working-tree
        wins. Mirrors the CLI default per LAW 11.
    commit_range:
        Git commit range (e.g. ``"main..HEAD"``). Empty (default)
        uses the working-tree / staged selector.
    severity:
        Minimum severity to surface: ``low`` (default, shows all) /
        ``medium`` / ``high`` / ``critical``. Mirrors the CLI default
        per LAW 11.
    fail_on_critical:
        Surface a CI-failing verdict when critical challenges land.
        Off by default.
    output_format:
        Output format: ``text`` (default) / ``markdown``. Mirrors the
        CLI ``--format`` default per LAW 11.
    root:
        Repo root (default current directory).
    summarize:
        If True and the client supports MCP sampling, force the
        ``compress_mode`` round-trip on; False forces it off. ``None``
        (default) defers to the ``ROAM_AI_ENABLED`` gate inside the
        sampling layer. Only consulted when ``compress_mode`` != ``off``.
    compress_mode:
        How to fold the challenges through MCP sampling. Closed enum:
        ``off`` (default -- return the deterministic envelope, zero
        behavior change), ``digest`` (compress the full envelope into a
        triage briefing under ``briefing``), ``defend`` (collapse the
        challenges into one "Dungeon Master" defend-this-change brief
        under ``defend_briefing``). Set ``compress_mode='defend'`` to
        pressure-test the structural choices; set ``'digest'`` to triage
        a large challenge set. Inherits the ``ROAM_AI_ENABLED`` opt-in
        gate -- absent the env var the deterministic envelope is returned.

    Returns: ``{summary: {verdict, challenges, critical, high,
    warning, info, changed_files}, challenges: [...]}``.
    """
    args: list[str] = ["adversarial", "--severity", severity, "--format", output_format]
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    if fail_on_critical:
        args.append("--fail-on-critical")
    result = _run_roam(args, root)

    if compress_mode == "off":
        return result
    if compress_mode not in {"digest", "defend"}:
        # Pattern-1 variant D: an unknown enum value is loud, not a
        # silent no-op. Preserve the deterministic envelope + verdict and
        # stamp the invalid sentinel so a typo surfaces.
        if isinstance(result, dict):
            summary = dict(result.get("summary") or {})
            summary["compress_mode_invalid"] = True
            result = dict(result)
            result["summary"] = summary
        return result

    from roam.mcp_extras import adversarial_compress as _adv

    task = _mcp_session.session_hint(ctx) if _mcp_session is not None else ""
    return await _adv.compress_adversarial(
        ctx,
        result,
        mode=compress_mode,
        task=task,
        summarize=summarize,
    )


# W304: roam_migration_plan
@_tool(
    name="roam_migration_plan",
    description=(
        "Generate an ordered migration plan with risk + blast-radius "
        "per step from a target-architecture YAML spec or inline "
        "``--move SYMBOL=path/to/new/file`` directives. Each step is "
        "annotated with caller count and a derived risk score so "
        "agents can decide where to stop or insert tests. Stops at "
        "the first step exceeding ``max_risk``. Different from "
        "``roam_simulate`` (counterfactual single-move analysis) -- "
        "this is the ordered multi-step plan with a risk gate."
    ),
)
def roam_migration_plan(
    input_path: str = "",
    moves: tuple[str, ...] = (),
    max_risk: str = "high",
    root: str = ".",
) -> dict:
    """Generate an ordered migration plan with risk + blast-radius steps.

    WHEN TO USE: planning a multi-symbol move / rename / restructure
    and want a risk-ordered step list. Pair with ``roam_simulate``
    for counterfactual single-move analysis.

    Parameters
    ----------
    input_path:
        Path to a target-architecture spec (YAML) the tool reads.
        Empty (default) requires ``moves`` instead. W332 canonical
        ``input_path`` for the sidecar config file.
    moves:
        Inline move directives, each shaped ``"SYMBOL=path/to/new.py"``.
        Repeatable -- pass a tuple of strings (e.g.
        ``("foo=src/a.py", "bar=src/b.py")``). Empty (default) requires
        ``input_path`` instead.
    max_risk:
        Stop the plan once the next step exceeds this risk threshold:
        ``low`` / ``medium`` / ``high`` (default). Mirrors the CLI
        default per LAW 11.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, step_count, skipped_count, max_risk,
    ...}, steps: [...], skipped: [...]}``.
    """
    args: list[str] = ["migration-plan", "--max-risk", max_risk]
    if input_path:
        args.extend(["--target", input_path])
    for move in moves:
        args.extend(["--move", move])
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# W305: reports & audit cluster (Wave29 MCP wrapper backfill, sub-wave 7)
# ---------------------------------------------------------------------------
# 11 read-only wrappers covering the "top-level reports + boolean oracles"
# surface. Composite report recipes (``audit`` / ``report`` / ``risk`` /
# ``stats``) plus two cross-index diff tools (``compare`` /
# ``evidence-diff``) plus 5 oracle subcommands (``symbol-exists`` /
# ``route-exists`` / ``is-test-only`` / ``is-reachable-from-entry`` /
# ``is-clone-of``). All 11 require a built index so they auto-inherit
# the W296 cold-start guard (none appear in ``_NO_INDEX_NEEDED``).
# Descriptions use imperative voice per CLAUDE.md LAW 2; verdict strings
# anchor on LAW 4 concrete-noun terminals (symbols, files, presets,
# sections, packets, callers).
#
# W332 canonicals: ``compare BASELINE TARGET`` takes two index-db file
# paths; the wrapper exposes them as ``baseline_path`` / ``target_path``
# (file-shaped, not symbol-shaped, so they keep semantically distinct
# names rather than collapsing to ``input_path``). ``evidence-diff
# OLD_PATH NEW_PATH`` similarly takes two evidence-packet file paths --
# exposed as ``old_path`` / ``new_path`` to preserve the OLD vs NEW
# semantic distinction. ``oracle symbol-exists NAME`` /
# ``is-test-only`` / ``is-reachable-from-entry`` / ``is-clone-of`` all
# take a symbol-shaped positional, so they use the W332/Fix-D canonical
# ``symbol`` argument. ``oracle route-exists PATH`` takes a URL-route
# string (a value, not a file path), so it stays ``route_path`` to
# avoid colliding with the W332/Fix-D ``path`` canonical for filesystem
# paths.
#
# ``oracle batch`` is intentionally NOT wrapped: it reads a JSONL
# stream of queries from a file path the agent would have to prepare
# on disk; the per-subcommand wrappers below are the MCP-idiomatic
# surface.
#
# Pattern-1 audit (JSON-parse-on-empty): all 11 underlying CLI commands
# emit a non-empty JSON envelope on every path -- including no-records
# / no-preset / no-symbol-found / packet-mismatch cases. ``oracle``
# returns a boolean verdict envelope even when the symbol is unknown.
# ``compare`` and ``evidence-diff`` raise ``SystemExit`` on missing-
# input AFTER emitting the envelope; the W325 chokepoint try-parse
# passthrough preserves that envelope through the MCP runner.
# ---------------------------------------------------------------------------


# W305: roam_audit
@_tool(
    name="roam_audit",
    description=(
        "Run a one-shot codebase architecture audit: bundles health, "
        "debt, dead-code, risk, test-pyramid, coverage, and API-surface "
        "signals into a single envelope. Designed as the structured "
        "artifact a written audit report attaches. Different from "
        "``roam_health`` (single 0-100 score) and ``roam_report`` "
        "(preset-driven Markdown report) -- this is the verdict-first "
        "audit packet for governance and onboarding."
    ),
)
def roam_audit(brief: bool = False, root: str = ".") -> dict:
    """Run the one-shot codebase architecture audit.

    WHEN TO USE: agent needs a single-envelope rollup of the audit-
    shaped signals across health / debt / dead / risk / pyramid /
    coverage / surface for an audit report or onboarding packet. Pair
    with ``roam_health`` for the single-number snapshot or
    ``roam_report`` for a curated Markdown render.

    Parameters
    ----------
    brief:
        Drop per-section detail and keep only the top-level summary
        scores. Off by default -- the full envelope wins per LAW 11.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, sections: [...],
    scores: {...}}``.
    """
    args: list[str] = ["audit"]
    if brief:
        args.append("--brief")
    return _run_roam(args, root)


# W305: roam_report
@_tool(
    name="roam_report",
    description=(
        "Run a compound report preset (built-ins: ``first-contact``, "
        "``security``, ``pre-pr``, ``refactor``, ``guardian``) that "
        "orchestrates multiple analysis commands into one rendered "
        "report. Different from ``roam_audit`` (single fixed bundle) "
        "-- this is the preset-driven multi-command roll-up with "
        "optional Markdown output and strict exit-code gating."
    ),
)
def roam_report(
    preset: str = "",
    list_presets: bool = False,
    strict: bool = False,
    markdown: bool = False,
    config_path: str = "",
    root: str = ".",
) -> dict:
    """Run a compound report preset over the codebase.

    WHEN TO USE: agent wants a curated multi-command report (pre-PR,
    security, refactor, ...) rendered in one shot. Pair with
    ``roam_audit`` for the fixed structured audit envelope.

    Parameters
    ----------
    preset:
        Preset slug (``first-contact`` / ``security`` / ``pre-pr`` /
        ``refactor`` / ``guardian``). Empty (default) runs no preset
        -- useful with ``list_presets=True``.
    list_presets:
        List available presets and exit. Off by default.
    strict:
        Exit non-zero if any section fails. Off by default -- mirrors
        the CLI default per LAW 11.
    markdown:
        Emit GitHub-compatible Markdown instead of plain text. Off by
        default.
    config_path:
        Path to a custom presets JSON config the tool reads. Empty
        (default) uses built-in presets only.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, preset, sections, ...},
    sections: [...]}``.
    """
    args: list[str] = ["report"]
    if list_presets:
        args.append("--list")
    if strict:
        args.append("--strict")
    if markdown:
        args.append("--md")
    if config_path:
        args.extend(["--config", config_path])
    if preset:
        args.append(preset)
    return _run_roam(args, root)


# W305: roam_risk
@_tool(
    name="roam_risk",
    description=(
        "Rank symbols by domain-weighted risk: combines static risk "
        "(fan-in + fan-out + betweenness) with domain criticality "
        "weights so financial / auth / data-integrity symbols rank "
        "higher than UI symbols. Different from ``roam_fan`` (raw "
        "fan-in/out degree) and ``roam_hotspots`` (runtime hotspot "
        "classification) -- this is the semantic-domain-weighted risk "
        "heatmap."
    ),
)
def roam_risk(
    top: int = 0,
    domain: str = "",
    explain: bool = False,
    include_tests: bool = False,
    show_suppressed: bool = False,
    root: str = ".",
) -> dict:
    """Rank symbols by domain-weighted structural risk.

    WHEN TO USE: agent wants risk-ranked symbols with semantic-domain
    weighting for pre-PR or audit scope. Pair with ``roam_fan`` for
    raw degree counts and ``roam_hotspots`` for runtime hotspots.

    Parameters
    ----------
    top:
        Number of symbols to surface. ``0`` (default) lets the CLI
        pick its default per LAW 11.
    domain:
        Comma-separated high-weight domain keywords (e.g.
        ``"payment,tax,ledger"``). Empty (default) uses heuristic
        domain detection.
    explain:
        Show the full callee-chain reasoning per symbol. Off by
        default.
    include_tests:
        Include test-file symbols in the ranking. Off by default --
        test fixtures co-change with src files by design and dominate
        the headline. Mirrors the CLI default per LAW 11.
    show_suppressed:
        List the rows that were filtered out instead of just counting
        them. Off by default.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, top_risk_symbols, ...},
    risks: [...]}``.
    """
    args: list[str] = ["risk"]
    if top > 0:
        args.extend(["-n", str(top)])
    if domain:
        args.extend(["--domain", domain])
    if explain:
        args.append("--explain")
    if include_tests:
        args.append("--include-tests")
    if show_suppressed:
        args.append("--show-suppressed")
    return _run_roam(args, root)


# W305: roam_stats
@_tool(
    name="roam_stats",
    description=(
        "Aggregate high-level statistics: language / role / kind "
        "counts plus a recent-commit activity counter over a "
        "configurable window. Different from ``roam_metrics`` "
        "(per-symbol static-metric report) and ``roam_graph_stats`` "
        "(graph-wide topology stats) -- this is the language-and-role "
        "inventory snapshot."
    ),
)
def roam_stats(days: int = 30, root: str = ".") -> dict:
    """Aggregate language / role / kind counts and recent activity.

    WHEN TO USE: agent wants a one-call inventory of language
    coverage, role distribution, and recent commit activity. Pair
    with ``roam_graph_stats`` for graph-topology counts and
    ``roam_metrics`` for per-symbol static metrics.

    Parameters
    ----------
    days:
        Window (in days) for the recent-commit activity counter
        (default 30 -- mirrors the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, language_count, role_count, ...},
    languages: {...}, roles: {...}, recent_activity: {...}}``.
    """
    return _run_roam(["stats", "--days", str(days)], root)


# W305: roam_compare
@_tool(
    name="roam_compare",
    description=(
        "Diff two roam indices structurally: reports symbols "
        "added/removed/moved, per-file complexity deltas above a "
        "threshold, language counts, and a one-line health verdict "
        "(improved / regressed / sideways). Different from "
        "``roam_graph_diff`` (commit-range graph delta from one "
        "index) -- this is the cross-index structural delta for "
        "release-vs-release comparisons."
    ),
)
def roam_compare(
    baseline_path: str,
    target_path: str,
    top: int = 15,
    threshold: int = 5,
    root: str = ".",
) -> dict:
    """Diff two roam ``.roam/index.db`` files structurally.

    WHEN TO USE: agent has a previous-release index and a current
    index and wants the structural delta + health verdict. Pair with
    ``roam_graph_diff`` for the single-index commit-range delta.

    Parameters
    ----------
    baseline_path:
        Path to the baseline ``.roam/index.db`` SQLite file
        (typically a previous-release index).
    target_path:
        Path to the target ``.roam/index.db`` SQLite file (typically
        the current-release index).
    top:
        Show only the top N changes per category (default 15 --
        mirrors the CLI default per LAW 11).
    threshold:
        Ignore per-file complexity deltas smaller than this magnitude
        (default 5 -- mirrors the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, symbols_added, symbols_removed,
    symbols_moved, ...}, changes: {...}}``.
    """
    args: list[str] = [
        "compare",
        baseline_path,
        target_path,
        "--top",
        str(top),
        "--threshold",
        str(threshold),
    ]
    return _run_roam(args, root)


# W305: roam_evidence_diff
@_tool(
    name="roam_evidence_diff",
    description=(
        "Diff two ``ChangeEvidence`` packets: shows hash drift, "
        "schema drift, added/removed refs, missing evidence, and "
        "changed verdicts. Useful for reviewing PR re-runs, comparing "
        "replay windows, or auditing whether a fresh evidence packet "
        "has improved or regressed against a stored baseline. "
        "Different from ``roam_compare`` (two-index structural delta) "
        "-- this is the two-packet evidence delta."
    ),
)
def roam_evidence_diff(
    old_path: str,
    new_path: str,
    root: str = ".",
) -> dict:
    """Diff two ``ChangeEvidence`` packets on disk.

    WHEN TO USE: agent wants to compare an older evidence packet
    against a fresh re-run -- hash drift, missing evidence axes,
    changed verdicts. Pair with ``roam_compare`` for the structural
    cross-index delta.

    Parameters
    ----------
    old_path:
        Path to the OLD ``ChangeEvidence`` JSON packet on disk.
    new_path:
        Path to the NEW ``ChangeEvidence`` JSON packet on disk.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, hash_drift, schema_drift, ...},
    deltas: {added_refs, removed_refs, missing_evidence, ...}}``.
    """
    return _run_roam(["evidence-diff", old_path, new_path], root)


# W305: roam_oracle_symbol_exists
@_tool(
    name="roam_oracle_symbol_exists",
    description="Answer the boolean oracle question: does a symbol with this name exist in the index? Returns a yes/no verdict envelope with the matched symbol's file + kind when found. Different from ``roam_search_symbol`` (top-N ranked hits) -- this is the cheap boolean lookup for agent precondition checks.",
    output_schema=_SCHEMA_ORACLE,
)
def roam_oracle_symbol_exists(symbol: str, root: str = ".") -> dict:
    """Answer whether a symbol with this name exists in the index.

    WHEN TO USE: agent has a candidate symbol name and wants a cheap
    yes/no precondition check before running a heavier command. Pair
    with ``roam_search_symbol`` for ranked-hit search.

    Parameters
    ----------
    symbol:
        Symbol name to look up (e.g. ``"handleSave"``). W332/Fix-D
        canonical for the symbol-shaped positional argument -- legacy
        ``name=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, exists, ...}, matches: [...]}``.
    """
    return _run_roam(["oracle", "symbol-exists", symbol], root)


# W305: roam_oracle_route_exists
@_tool(
    name="roam_oracle_route_exists",
    description="Answer the boolean oracle question: does a route handler match this URL path? Returns a yes/no verdict envelope with the matched handler's file + kind when found. Different from ``roam_endpoints`` (full endpoint enumeration) -- this is the cheap boolean lookup for one route precondition check.",
    output_schema=_SCHEMA_ORACLE,
)
def roam_oracle_route_exists(route_path: str, root: str = ".") -> dict:
    """Answer whether a route handler matches this URL path.

    WHEN TO USE: agent has a candidate URL route and wants a cheap
    yes/no precondition check before tracing handlers. Pair with
    ``roam_endpoints`` for full endpoint enumeration.

    Parameters
    ----------
    route_path:
        URL route path to look up (e.g. ``"/api/users/:id"``). This
        is a route-string VALUE, not a filesystem path, so it stays
        ``route_path`` to avoid colliding with the W332/Fix-D
        ``path`` canonical for file paths.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, exists, ...}, matches: [...]}``.
    """
    return _run_roam(["oracle", "route-exists", route_path], root)


# W305: roam_oracle_is_test_only
@_tool(
    name="roam_oracle_is_test_only",
    description="Answer the boolean oracle question: are ALL callers of this symbol in test files? Useful for sniffing test fixtures and dead-but-test-only helpers. Different from ``roam_dead_code`` (broad dead-symbol detection) -- this is the cheap boolean lookup for one symbol's test-only status.",
    output_schema=_SCHEMA_ORACLE,
)
def roam_oracle_is_test_only(symbol: str, root: str = ".") -> dict:
    """Answer whether all callers of a symbol live in test files.

    WHEN TO USE: agent wants to know if a symbol is safe to delete or
    repurpose because nothing but tests references it. Pair with
    ``roam_dead_code`` for the wider dead-symbol sweep.

    Parameters
    ----------
    symbol:
        Symbol name to look up (e.g. ``"_test_helper"``). W332/Fix-D
        canonical for the symbol-shaped positional argument -- legacy
        ``name=`` callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, test_only, caller_count, ...},
    callers: [...]}``.
    """
    return _run_roam(["oracle", "is-test-only", symbol], root)


# W305: roam_oracle_is_reachable_from_entry
@_tool(
    name="roam_oracle_is_reachable_from_entry",
    description="Answer the boolean oracle question: is the symbol reachable from any entry point via the call graph (BFS up to ``max_hops`` depth)? Useful for sniffing orphans and production-vs-tooling code. Different from ``roam_dead_code`` (broad dead-symbol detection) and ``roam_entry_points`` (entry-point enumeration) -- this is the cheap boolean lookup for one symbol's reachability.",
    output_schema=_SCHEMA_ORACLE,
)
def roam_oracle_is_reachable_from_entry(
    symbol: str,
    max_hops: int = 10,
    root: str = ".",
) -> dict:
    """Answer whether a symbol is reachable from any entry point.

    WHEN TO USE: agent wants to confirm a symbol is on a real call
    chain from main / handler / route before declaring it live. Pair
    with ``roam_entry_points`` for the enumeration and
    ``roam_dead_code`` for the broader sweep.

    Parameters
    ----------
    symbol:
        Symbol name to look up (e.g. ``"process_payment"``).
        W332/Fix-D canonical for the symbol-shaped positional
        argument -- legacy ``name=`` callers are accepted via
        ``_PARAM_ALIASES`` with a deprecation warning.
    max_hops:
        BFS depth cap (default 10 -- mirrors the CLI default per
        LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, reachable, entry_count, ...},
    entry_points: [...]}``.
    """
    return _run_roam(
        ["oracle", "is-reachable-from-entry", symbol, "--max-hops", str(max_hops)],
        root,
    )


# W305: roam_oracle_is_clone_of
@_tool(
    name="roam_oracle_is_clone_of",
    description="Answer the boolean oracle question: does this symbol have persisted clone siblings in the ``clone_pairs`` table? Returns a yes/no verdict envelope with the matched clone class size. Different from ``roam_clones`` (full clone-pair enumeration) -- this is the cheap boolean lookup for one symbol's clone status.",
    output_schema=_SCHEMA_ORACLE,
)
def roam_oracle_is_clone_of(symbol: str, root: str = ".") -> dict:
    """Answer whether a symbol has persisted clone siblings.

    WHEN TO USE: agent is about to edit a symbol and wants a cheap
    yes/no on whether clone-class siblings exist that should also be
    updated. Pair with ``roam_clones`` for the full enumeration.

    Parameters
    ----------
    symbol:
        Symbol name to look up (e.g. ``"validate_input"``).
        W332/Fix-D canonical for the symbol-shaped positional
        argument -- legacy ``name=`` callers are accepted via
        ``_PARAM_ALIASES`` with a deprecation warning.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, has_clones, class_size, ...},
    siblings: [...]}``.
    """
    return _run_roam(["oracle", "is-clone-of", symbol], root)


# ---------------------------------------------------------------------------
# W306: getting-started, refactoring, workflow & compliance cluster
# (Wave29 MCP wrapper backfill, sub-wave 8)
# ---------------------------------------------------------------------------
# 13 read-only wrappers covering the heterogeneous tail of the wrapper
# backfill: 4 getting-started / overview tools (``describe`` / ``map`` /
# ``minimap`` / ``workflow``), 1 ADR discoverer (``adrs``), 2 prose-to-code
# bridges (``intent`` / ``invariants``), 1 metric trender
# (``architecture-drift``), 1 dogfood-corpus triage (``dogfood-aggregate``),
# 1 compliance readiness check (``article-12-check``), 1 git-history
# replay (``postmortem``), 1 pre-PR rollup (``pr-prep``), and 1 symbol
# triage explainer (``why``).
#
# The cluster is heterogeneous on purpose: W305 was reports & oracles,
# W304 was architecture & evidence, W303 was indices & metrics. W306
# absorbs the remaining commands that did not fit a single shared
# theme. All 13 are read-only at the wrapped surface: ``describe`` and
# ``minimap`` have CLI flags (``--write`` / ``--update`` / ``--init-notes``)
# that mutate disk, but the WRAPPERS DO NOT EXPOSE THOSE FLAGS so the
# MCP surface stays read-only by construction. ``pr-bundle`` / ``fleet``
# (the genuinely state-mutating commands) defer to W307.
#
# All 13 wrappers require a built index so they auto-inherit the W296
# cold-start guard (none appear in ``_NO_INDEX_NEEDED``):
# * ``dogfood-aggregate`` declares ``requires_index=False`` at the
#   capability level (it scans ``internal/dogfood/evals/``), but a
#   cold-start path still benefits from the explicit "no index built"
#   envelope because the dogfood corpus is shipped private to the
#   roam-code repo and absent in installed environments.
# * ``article-12-check`` declares ``requires_index=False`` at the
#   capability level (the 6 checks read filesystem state under .roam/
#   and docs/), but the underlying CLI still calls ``ensure_index()``
#   for consistency, so the wrapper stays gated.
#
# W332 canonicals applied:
# * ``intent`` / ``invariants`` / ``why`` take symbol-shaped arguments
#   -- exposed as ``symbol`` (single) or ``symbols`` (variadic) per
#   W332/Fix-D. ``why`` takes ``names`` as ``nargs=-1`` so the wrapper
#   exposes ``symbols: tuple[str, ...]``.
# * ``intent --doc`` and ``map --seed`` both take filesystem paths --
#   exposed as ``path`` per W332/Fix-D (the alias machinery rewrites
#   ``file=`` and other legacy names to ``path=``).
# * ``postmortem COMMIT_RANGE`` / ``pr-prep [COMMIT_RANGE]`` take git
#   commit range strings (values, not paths) -- exposed as
#   ``commit_range`` which is semantically distinct.
# * ``workflow RECIPE_NAME`` takes a recipe slug (a value) -- exposed
#   as ``recipe_name``.
# * ``adrs`` / ``architecture-drift`` / ``article-12-check`` /
#   ``describe`` / ``minimap`` take no symbol/path positional args.
#
# Pattern-1 audit (JSON-parse-on-empty): all 13 underlying CLI
# commands emit a non-empty JSON envelope on every path, including:
# no-ADRs-found (``adrs``), no-doc-files (``intent``), unknown-recipe
# (``workflow``), insufficient-snapshots (``architecture-drift``),
# no-commits-matched (``postmortem``), empty diff (``pr-prep``). The
# Pattern-1 guard tests in tests/test_json_contracts.py and
# tests/test_pattern1_envelope_emission.py pin this discipline.
# ---------------------------------------------------------------------------


# W306: roam_adrs
@_tool(
    name="roam_adrs",
    description=(
        "Discover Architecture Decision Records (ADRs) and link them "
        "to code modules. Scans well-known ADR directories "
        "(``docs/adr/`` / ``architecture/decisions/`` / ...) for "
        "markdown files matching ADR naming patterns, parses each "
        "ADR's title / status / date / file refs, then cross-"
        "references mentioned files against the symbol index. "
        "Different from ``roam_doc_staleness`` (inline docstring "
        "drift) -- this is the prose-decision-document discoverer."
    ),
)
def roam_adrs(filter_status: str = "", limit: int = 50, root: str = ".") -> dict:
    """Discover ADRs and link them to indexed code modules.

    WHEN TO USE: agent needs to know whether the repo has any prose
    architecture decisions on file, how many are live vs deprecated,
    and which ADRs mention live code files. Pair with
    ``roam_doc_staleness`` for inline docstring drift and
    ``roam_intent`` for general doc-to-code linkage.

    Parameters
    ----------
    filter_status:
        Filter ADRs by status (``accepted`` / ``proposed`` /
        ``deprecated`` / ``superseded`` / ``rejected`` / ``draft`` /
        ``amended``). Empty (default) returns all ADRs.
    limit:
        Maximum number of ADRs to surface (default 50 -- mirrors the
        CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, adr_count, linked_count,
    status_counts, ...}, adrs: [...]}``.
    """
    args: list[str] = ["adrs", "--limit", str(limit)]
    if filter_status:
        args.extend(["--status", filter_status])
    return _run_roam(args, root)


# W306: roam_architecture_drift
@_tool(
    name="roam_architecture_drift",
    description=(
        "Compute per-week growth rates for symbols / edges / cycles "
        "across a sliding window of persisted ``.roam/snapshots/`` "
        "and classify overall direction as ``improving`` / "
        "``degrading`` / ``stable``. Different from ``roam_graph_diff`` "
        "(point-in-time delta between two commits) and ``roam_trends`` "
        "(metric-level time series) -- this is the snapshot-based "
        "architectural-trajectory report."
    ),
)
def roam_architecture_drift(
    window: str = "30d",
    top: int = 10,
    root: str = ".",
) -> dict:
    """Trend report over the sliding-window snapshot series.

    WHEN TO USE: agent wants a verdict-first "is the architecture
    improving or degrading" answer over the last N days. Pair with
    ``roam_graph_diff`` for one-shot deltas and ``roam_trends`` for
    metric-level series.

    Parameters
    ----------
    window:
        Time window: ``30d`` / ``4w`` / ``6m`` / ``1y``. Bare integer =
        days. Default ``30d`` -- mirrors the CLI default per LAW 11.
    top:
        Cap the ``biggest_movers`` list to this many rows (default
        10 -- mirrors the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, directional, window_days,
    snapshots_analyzed, ...}, metrics: {...}, biggest_movers: [...],
    pair_diffs: [...]}``.
    """
    return _run_roam(
        ["architecture-drift", "--window", window, "--top", str(top)],
        root,
    )


# W306: roam_article_12_check
@_tool(
    name="roam_article_12_check",
    description=(
        "Run a 6-item EU AI Act Article 12 readiness checklist over "
        "the indexed repo: audit-trail directory, audit-trail "
        "records, retention policy doc, technical docs, attestation "
        "surface, high-risk classification heuristic. Emits a "
        "structured envelope mapping each item to its Article (12, "
        "18, 19) or Annex (III). Different from ``roam_audit_trail_"
        "conformance_check`` (per-record chain integrity) -- this is "
        "the repo-level governance-readiness assessment. Per the "
        "agentic-assurance guardrails: 'maps to' / 'supports evidence "
        "for', never 'certifies' / 'makes compliant'."
    ),
)
def roam_article_12_check(root: str = ".") -> dict:
    """Run the EU AI Act Article 12 readiness checklist.

    WHEN TO USE: agent wants a scoping/readiness report for buyers
    whose product MAY fall under Annex III. Pair with
    ``roam_audit_trail_conformance_check`` for chain integrity.

    Parameters
    ----------
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, passed, total,
    governance_compliance_score, ...}, items: [...]}``.
    """
    return _run_roam(["article-12-check"], root)


# W306: roam_describe
@_tool(
    name="roam_describe",
    description=(
        "Auto-generate a project description for AI coding agents: "
        "multi-section Markdown report covering overview, "
        "directories, entry points, key abstractions, architecture, "
        "and testing. Different from ``roam_understand`` (compact "
        "codebase overview) -- this is the comprehensive prose "
        "description for CLAUDE.md / AGENTS.md / .cursor/rules. The "
        "wrapper emits to stdout; on-disk writes are deferred to the "
        "CLI (``roam describe --write``) so the MCP surface stays "
        "read-only."
    ),
)
def roam_describe(agent_prompt: bool = False, root: str = ".") -> dict:
    """Generate a project description for AI coding agents.

    WHEN TO USE: agent wants a long-form prose description of the
    indexed repo for CLAUDE.md / AGENTS.md / .cursor/rules. Pair
    with ``roam_understand`` for the compact overview and
    ``roam_minimap`` for the sentinel-block one-pager.

    Parameters
    ----------
    agent_prompt:
        Emit a compact agent-oriented prompt (under 500 tokens)
        instead of the full multi-section report. Off by default --
        the full description wins per LAW 11.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, mode, ...}, ...sections}``.
    """
    args: list[str] = ["describe"]
    if agent_prompt:
        args.append("--agent-prompt")
    return _run_roam(args, root)


# W306: roam_dogfood_aggregate
@_tool(
    name="roam_dogfood_aggregate",
    description=(
        "Triage view over the dogfood eval corpus: totals, "
        "per-command findings count, by-status / by-severity / "
        "by-type breakdowns. Reads ``internal/dogfood/evals/`` (or "
        "an override path). Useful for agents auditing roam-code "
        "itself; mostly a no-op on consumer repos that have no "
        "dogfood corpus."
    ),
)
def roam_dogfood_aggregate(
    path: str = "",
    show_all: bool = False,
    severity: str = "",
    finding_type: str = "",
    since: str = "",
    top: int = 10,
    limit: int = 50,
    root: str = ".",
) -> dict:
    """Aggregate the dogfood eval corpus into a triage view.

    WHEN TO USE: agent is auditing roam-code itself and wants a
    backlog/triage rollup of the 212-eval corpus. Mostly a no-op on
    consumer repos.

    Parameters
    ----------
    path:
        Directory of evals (W332 canonical for filesystem paths).
        Empty (default) uses ``<project>/internal/dogfood/evals/``.
    show_all:
        Include findings of every status (default: open only --
        mirrors the CLI default per LAW 11).
    severity:
        Single severity letter (``H`` / ``M`` / ``L``); empty
        (default) returns all severities.
    finding_type:
        Substring match on finding type (e.g. ``wrong`` / ``missing``
        / ``signal`` / ``noise``); empty (default) returns all types.
    since:
        Only include evals with frontmatter date >= this
        ``YYYY-MM-DD`` value. Empty (default) returns all dates.
    top:
        Show this many top commands by findings count (default 10 --
        mirrors the CLI default per LAW 11).
    limit:
        Cap the number of findings emitted in text mode (default
        50 -- mirrors the CLI default per LAW 11; ``0`` for no cap).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, total, ...}, totals: {...},
    per_command: [...], findings: [...]}``.
    """
    args: list[str] = ["dogfood-aggregate"]
    if path:
        args.extend(["--path", path])
    if show_all:
        args.append("--all")
    if severity:
        args.extend(["--severity", severity])
    if finding_type:
        args.extend(["--type", finding_type])
    if since:
        args.extend(["--since", since])
    args.extend(["--top", str(top), "--limit", str(limit)])
    return _run_roam(args, root)


# W306: roam_intent
@_tool(
    name="roam_intent",
    description=(
        "Link documentation to code: find which docs mention which "
        "symbols, and detect doc-to-code drift (references to "
        "non-existent symbols). Different from ``roam_docs_coverage`` "
        "(PageRank-ranked missing-docstring hotlist) and "
        "``roam_doc_staleness`` (stale docstring content) -- this is "
        "the prose-doc-to-symbol linker plus drift detector."
    ),
)
def roam_intent(
    symbol: str = "",
    path: str = "",
    drift: bool = False,
    undocumented: bool = False,
    top: int = 20,
    root: str = ".",
) -> dict:
    """Link prose documentation to indexed code symbols.

    WHEN TO USE: agent wants to know which docs describe which
    symbols, find dead references in docs, or surface important
    symbols missing from docs. Pair with ``roam_docs_coverage`` for
    the missing-docstring hotlist and ``roam_stale_refs`` for
    dangling file references.

    Parameters
    ----------
    symbol:
        Find docs mentioning this symbol. W332/Fix-D canonical for
        the symbol-shaped argument -- legacy ``name=`` callers are
        accepted via ``_PARAM_ALIASES`` with a deprecation warning.
        Empty (default) returns the full doc-to-code mapping.
    path:
        Find code referenced by this doc file. W332/Fix-D canonical
        for filesystem paths -- legacy ``file=`` callers are
        accepted via ``_PARAM_ALIASES`` with a deprecation warning.
        Empty (default) returns the full doc-to-code mapping.
    drift:
        Show references to non-existent symbols (doc-code drift).
        Off by default -- mirrors the CLI default per LAW 11.
    undocumented:
        Show important symbols not mentioned in any doc. Off by
        default -- mirrors the CLI default per LAW 11.
    top:
        Max items to show (default 20 -- mirrors the CLI default
        per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, links: [...], drift: [...],
    undocumented: [...]}``.
    """
    args: list[str] = ["intent", "--top", str(top)]
    if symbol:
        args.extend(["--symbol", symbol])
    if path:
        args.extend(["--doc", path])
    if drift:
        args.append("--drift")
    if undocumented:
        args.append("--undocumented")
    return _run_roam(args, root)


# W306: roam_invariants
@_tool(
    name="roam_invariants",
    description=(
        "Discover implicit contracts for a symbol or the public API "
        "surface: signature shape, parameter count and ordering, "
        "usage spread across files, dependency set. Different from "
        "``roam_check_rules`` (explicit governance rules) -- this is "
        "the AUTO-discovered implicit-contract surface so agents "
        "know what must stay stable when modifying a symbol."
    ),
)
def roam_invariants(
    symbol: str = "",
    public_api: bool = False,
    breaking_risk: bool = False,
    top: int = 20,
    root: str = ".",
) -> dict:
    """Discover implicit contracts for symbols.

    WHEN TO USE: agent is about to change a symbol's signature and
    wants the implicit-contract surface (signature shape, usage
    spread, callers) it must preserve. Pair with
    ``roam_check_rules`` for explicit rules and ``roam_breaking_changes``
    for API-break detection.

    Parameters
    ----------
    symbol:
        Target symbol or file path. W332/Fix-D canonical for the
        symbol-shaped argument -- legacy ``target=`` / ``name=``
        callers are accepted via ``_PARAM_ALIASES`` with a
        deprecation warning. Empty (default) requires ``public_api=True``.
    public_api:
        Analyze all public/exported symbols instead of one target.
        Off by default -- mirrors the CLI default per LAW 11.
    breaking_risk:
        Rank results by breaking risk. Off by default -- mirrors
        the CLI default per LAW 11.
    top:
        Max symbols to show (default 20 -- mirrors the CLI default
        per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, symbol_count, ...},
    invariants: [...]}``.
    """
    args: list[str] = ["invariants", "--top", str(top)]
    if public_api:
        args.append("--public-api")
    if breaking_risk:
        args.append("--breaking-risk")
    if symbol:
        args.append(symbol)
    return _run_roam(args, root)


# W306: roam_map
@_tool(
    name="roam_map",
    description=(
        "Show project skeleton: directory tree, entry points, top "
        "symbols by PageRank, language counts. Different from "
        "``roam_describe`` (prose description) and ``roam_minimap`` "
        "(sentinel-block one-pager for CLAUDE.md) -- this is the "
        "structured skeleton with directories, entry points, and "
        "ranked symbols for agent onboarding."
    ),
)
def roam_map(
    count: int = 20,
    full: bool = False,
    budget: int = 0,
    path: str = "",
    depth: int = 2,
    root: str = ".",
) -> dict:
    """Show the project skeleton for agent onboarding.

    WHEN TO USE: agent has just been pointed at a fresh repo and
    wants the skeleton (entry points + top symbols + directory
    layout). Pair with ``roam_describe`` for prose and
    ``roam_minimap`` for the CLAUDE.md-injectable one-pager.

    Parameters
    ----------
    count:
        Number of top symbols to show (default 20 -- mirrors the
        CLI default per LAW 11).
    full:
        Show all results without truncation. Off by default --
        mirrors the CLI default per LAW 11.
    budget:
        Approximate token limit for output. ``0`` (default) lets
        the CLI pick its default per LAW 11.
    path:
        Restrict the top-symbols list to symbols reachable from
        this seed file. W332/Fix-D canonical for filesystem paths --
        legacy ``file=`` callers are accepted via ``_PARAM_ALIASES``
        with a deprecation warning. Empty (default) returns all
        ranked symbols.
    depth:
        BFS hop limit when ``path`` is given (default 2 -- mirrors
        the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, files, symbols, edges, ...},
    directories: [...], entry_points: [...], top_symbols: [...]}``.
    """
    args: list[str] = ["map", "-n", str(count)]
    if full:
        args.append("--full")
    if budget > 0:
        args.extend(["--budget", str(budget)])
    if path:
        args.extend(["--seed", path, "--depth", str(depth)])
    return _run_roam(args, root)


# W306: roam_minimap
@_tool(
    name="roam_minimap",
    description=(
        "Generate a compact ~20-line codebase minimap for CLAUDE.md "
        "injection: tech stack, annotated directory tree, key "
        "symbols by PageRank, high-fan-in symbols to avoid, "
        "hotspots, detected conventions. Different from "
        "``roam_describe`` (long-form prose) and ``roam_map`` "
        "(structured skeleton) -- this is the sentinel-block "
        "one-pager. The wrapper emits to stdout; on-disk updates "
        "are deferred to the CLI (``roam minimap --update`` / "
        "``--init-notes``) so the MCP surface stays read-only."
    ),
)
def roam_minimap(root: str = ".") -> dict:
    """Generate a compact codebase minimap for agent context.

    WHEN TO USE: agent wants a sentinel-block one-pager (tech stack
    + tree + key symbols + hotspots) to seed CLAUDE.md / AGENTS.md.
    Pair with ``roam_describe`` for long-form and ``roam_map`` for
    the structured skeleton.

    Parameters
    ----------
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, minimap: "..."}``.
    """
    return _run_roam(["minimap"], root)


# W306: roam_postmortem
@_tool(
    name="roam_postmortem",
    description=(
        "Replay current detectors against past commits: walks a git "
        "commit range, runs ``roam critique`` against each commit's "
        "diff, and reports which findings would have surfaced "
        "pre-merge. Useful for retrospective replay -- 'would "
        "today's detector set have caught the incidents already in "
        "history?' Different from ``roam_pr_replay`` (one PR replay) "
        "-- this is the range-replay over historical commits."
    ),
)
def roam_postmortem(
    commit_range: str,
    limit: int = 100,
    show: int = 10,
    root: str = ".",
) -> dict:
    """Replay critique against a historical commit range.

    WHEN TO USE: agent wants to know whether the current detector
    set would have caught past incidents. Pair with
    ``roam_pr_replay`` for single-PR replay and ``roam_postmortem``
    in CI for pre-purchase signal.

    Parameters
    ----------
    commit_range:
        Git commit range (e.g. ``"HEAD~30..HEAD"`` or
        ``"v12.30..v12.39"`` or ``"main..feature/new-thing"``). This
        is a git-range VALUE, not a filesystem path, so it stays
        ``commit_range`` to avoid colliding with the W332/Fix-D
        ``path`` canonical for file paths.
    limit:
        Cap the number of commits walked (default 100 -- mirrors
        the CLI default per LAW 11).
    show:
        Top-N hits to display in text mode (default 10 -- mirrors
        the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, commits_scanned,
    commits_with_findings, ...}, per_commit: [...]}``.
    """
    return _run_roam(
        [
            "postmortem",
            commit_range,
            "--limit",
            str(limit),
            "--show",
            str(show),
        ],
        root,
    )


# W306: roam_pr_prep
@_tool(
    name="roam_pr_prep",
    description=(
        "One-shot pre-PR fitness check: bundles ``diff`` blast "
        "radius + ``critique`` + ``pr-risk`` into a single envelope "
        "with a ``ready_to_open`` verdict. Different from "
        "``roam_pr_risk`` (composite risk score alone) and "
        "``roam_critique`` (clones-not-edited + blast-radius alone) "
        "-- this is the three-section pre-PR rollup with the "
        "go/no-go verdict."
    ),
)
def roam_pr_prep(
    commit_range: str = "",
    high_callers: int = 10,
    root: str = ".",
) -> dict:
    """Run the one-shot pre-PR fitness rollup.

    WHEN TO USE: agent has finished editing and wants a single
    go/no-go verdict before opening the PR. Pair with
    ``roam_pr_risk`` for the standalone risk score and
    ``roam_critique`` for the standalone clones-not-edited check.

    Parameters
    ----------
    commit_range:
        Git commit range (e.g. ``"main..HEAD"`` or ``"HEAD~3"``).
        Empty (default) inspects uncommitted changes. This is a
        git-range VALUE, not a filesystem path, so it stays
        ``commit_range`` to avoid colliding with the W332/Fix-D
        ``path`` canonical for file paths.
    high_callers:
        Direct-caller threshold passed to ``critique`` (default
        10 -- mirrors the CLI default per LAW 11).
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ready_to_open, high_severity,
    pr_risk_score, ...}, diff: {...}, critique: {...}, pr_risk: {...}}``.
    """
    args: list[str] = ["pr-prep", "--high-callers", str(high_callers)]
    if commit_range:
        args.append(commit_range)
    return _run_roam(args, root)


# W306: roam_why
@_tool(
    name="roam_why",
    description=(
        "Explain why a symbol matters: role classification "
        "(Hub/Bridge/Leaf), transitive reach, critical-path "
        "membership, cluster cohesion, and a one-line verdict. "
        "Accepts multiple symbol names for batch triage. Different "
        "from ``roam_fan`` (raw connectivity ranking) and "
        "``roam_preflight`` (blast-radius gate before edit) -- this "
        "is the per-symbol role explainer for triage and onboarding."
    ),
)
def roam_why(symbols: tuple[str, ...], root: str = ".") -> dict:
    """Explain why each named symbol matters.

    WHEN TO USE: agent wants the role / reach / criticality verdict
    for one or more symbols to decide where to focus. Pair with
    ``roam_fan`` for raw connectivity and ``roam_preflight`` for
    the pre-edit blast-radius gate.

    Parameters
    ----------
    symbols:
        Symbol names to explain (e.g. ``("parseAmount",
        "formatNumber")``). Repeatable; the wrapper accepts a
        tuple. Plural form ``symbols`` (not the W332/Fix-D ``symbol``
        canonical) because the underlying CLI takes a variadic
        positional ``NAMES...`` -- the wrapper exposes the list
        shape explicitly so callers do not have to space-join
        symbol names into one string.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, symbol_count, ...},
    symbols: [...]}``.
    """
    args: list[str] = ["why"]
    args.extend(symbols)
    return _run_roam(args, root)


# W306: roam_workflow
@_tool(
    name="roam_workflow",
    description=(
        "Inspect a workflow recipe DAG, list available recipes, or "
        "suggest what to run next given a prior command. Useful as "
        "an agent navigation aid: 'I just ran roam impact -- what "
        "should I run next?' Different from the heavyweight "
        "analytical recipes -- this is the metadata-only recipe "
        "browser."
    ),
)
def roam_workflow(
    recipe_name: str = "",
    list_recipes: bool = False,
    query: str = "",
    next_after: str = "",
    root: str = ".",
) -> dict:
    """Inspect a workflow recipe or suggest the next command.

    WHEN TO USE: agent wants to discover recipes
    (``list_recipes=True``), inspect a specific recipe DAG (set
    ``recipe_name``), or get a 'what next?' hint (set
    ``next_after`` to the previously-run command). Pair with
    ``roam_describe`` for the prose overview.

    Parameters
    ----------
    recipe_name:
        Recipe slug to inspect (e.g. ``"first-contact"``). Empty
        (default) lists available recipes -- equivalent to
        ``list_recipes=True``.
    list_recipes:
        List available workflow recipes and exit. Off by default --
        mirrors the CLI default per LAW 11.
    query:
        Render ``{symbol}`` / ``{task}`` placeholders using this
        query string without running commands. Empty (default)
        skips placeholder rendering.
    next_after:
        Given the previously-run command name, suggest what to run
        next (e.g. ``"impact"`` -> suggestions for the impact
        follow-up). Empty (default) skips the suggestion path.
    root:
        Repo root (default current directory).

    Returns: ``{summary: {verdict, ...}, recipes: [...] |
    suggestions: [...]}``.
    """
    args: list[str] = ["workflow"]
    if list_recipes:
        args.append("--list")
    if query:
        args.extend(["--query", query])
    if next_after:
        args.extend(["--next", next_after])
    if recipe_name:
        args.append(recipe_name)
    return _run_roam(args, root)


# W307: roam_compile — compile a freeform task into a structured envelope
@_tool(
    name="roam_compile",
    description=(
        "Compile a freeform coding task into a structured envelope an "
        "AI agent can consume. Returns the ArtifactSelector verdict "
        "(facts / lean / full envelope) plus the deterministic plan. "
        "Empirically validated on Opus 4.8 (2026-05-28): FactsEnvelope "
        "delivers 99% of vanilla quality at 54% of vanilla cost. "
        "Different from roam_plan (symbol-centric execution plan) -- "
        "this is the freeform-task compiler."
    ),
)
def roam_compile(
    task: str,
    artifact: str = "auto",
    model_tier: str = "auto",
    brief: bool = False,
    explain: bool = False,
    route: bool = False,
    profile: str = "",
    root: str = ".",
) -> dict:
    """Compile a freeform task into a deterministic agent envelope.

    WHEN TO USE: agent receives a freeform task string and wants the
    cheapest deterministic envelope before any model exploration. Pair
    with the resulting envelope as the first user message for the
    downstream agent.

    Parameters
    ----------
    task:
        Freeform task description (e.g. "Find files coupled to
        src/roam/cli.py" or "Refactor the auth module").
    artifact:
        "auto" (default), "facts", "lean", "full", or "contract".
        "auto" uses the ArtifactSelector policy.
    model_tier:
        "auto" (default), "weak", or "capable". Capable models prefer
        "facts"; weak models prefer "full".
    brief:
        Emit the W22 brief envelope (~125-160 chars: procedure +
        classifier_confidence + first-command hint only). For agents that
        want the smallest possible routing hint.
    explain:
        Dump the classifier decision tree — which regexes matched, which
        procedures were rejected, tiebreak rules. For debugging
        surprising routing.
    route:
        Emit the full route_for_plan decision (model + envelope +
        contract_id). The production-grade output (ALL-LEVERS routing).
    profile:
        Calibration profile name (default `claude-2026-05`). Use
        `gpt-5-2026` for cross-model exploration (placeholder).
    root:
        Repo root (passed to `roam compile`). Defaults to cwd.

    W33c (2026-05-30): exposed --brief / --explain / --route / --profile
    that previously existed only on the CLI. Without them, MCP clients
    couldn't access the brief envelope or routing decision.

    Returns: ``{summary: {verdict, procedure, artifact_type, ...},
    artifact: {schema, plan}, agent_contract: {facts, ...}}``.
    """
    args = ["compile", task]
    # Mode flags are mutually exclusive with each other but compose with the
    # base artifact / model_tier args. CLI handles precedence.
    if explain:
        args.append("--explain")
    elif brief:
        args.append("--brief")
    elif route:
        args.append("--route")
        if profile:
            args.extend(["--profile", profile])
    else:
        args.extend(["--artifact", artifact, "--model-tier", model_tier])
    result = _run_roam(args, root)
    # W34e (E9): brief mode is meant to return a tiny shape — the CLI
    # already does that, but the MCP wrapper wraps it in an envelope.
    # Flatten the brief output so MCP callers get the 3-key dict directly.
    if brief and isinstance(result, dict):
        # CLI brief output (JSON) is itself a flat dict — unwrap it from
        # any outer envelope structure.
        for candidate in (result.get("artifact"), result.get("envelope_data"), result):
            if isinstance(candidate, dict) and "procedure" in candidate:
                return candidate
    return result


# ---------------------------------------------------------------------------
# Roam Guard — adoption surface (Wave 10)
# ---------------------------------------------------------------------------
#
# These wrappers expose the 8-command Roam Guard family on the MCP surface so
# agents discover it without reading the CLI inventory. Pair: `guard_pr`
# (writes log + optionally posts a GitHub Check) is the headline. The other
# six are read-only inspectors. Wrappers mirror their CLI flags 1:1.


@_tool(
    name="roam_guard_pr",
    description=(
        "Aggregate Roam Guard PR check: auto-collect bundle, compose "
        "AgentChangeProofBundle v1, render verdict (pass/pass_with_warnings/"
        "needs_review/blocked), optionally POST a GitHub Check Run. The "
        "headline tool — drop this into a CI step to gate any PR."
    ),
    read_only=False,
    idempotent=False,
)
def roam_guard_pr(
    bundle: str = "",
    mode: str = "safe_edit",
    policy_profile: str = "default",
    strict: bool = False,
    fmt: str = "text",
    output: str = "",
    skip_collect: bool = False,
    init_if_missing: bool = False,
    init_intent: str = "",
    ci: bool = False,
    rules: str = "",
    dry_run: bool = False,
    root: str = ".",
) -> dict:
    """Run the full Roam Guard pipeline against the active pr-bundle.

    WHEN TO USE: in CI on every PR, OR locally after an edit to ask
    "would this PR be blocked?". Pair `dry_run=True` for a read-only
    probe (no log append, no output file, no GH POST).

    Parameters
    ----------
    bundle:
        Explicit bundle path; empty = auto-discover from `.roam/pr-bundles/`.
    mode, policy_profile:
        Substrate inputs for the verdict engine.
    strict:
        Non-zero exit when verdict is `blocked` (CI gate).
    fmt:
        `text` (default), `markdown`, or `json`.
    ci:
        Preset: enables `strict + init_if_missing + fmt=markdown`.
    dry_run:
        Compute the verdict but don't write log / output / GH check.
    """
    args = ["guard-pr", "--mode", mode, "--policy-profile", policy_profile, "--format", fmt]
    if bundle:
        args.extend(["--bundle", bundle])
    if strict:
        args.append("--strict")
    if output:
        args.extend(["--output", output])
    if skip_collect:
        args.append("--skip-collect")
    if init_if_missing:
        args.append("--init-if-missing")
    if init_intent:
        args.extend(["--init-intent", init_intent])
    if ci:
        args.append("--ci")
    if rules:
        args.extend(["--rules", rules])
    if dry_run:
        args.append("--dry-run")
    return _run_roam(args, root)


@_tool(
    name="roam_guard_doctor",
    description=(
        "Roam Guard preflight: 8 health checks (.roam dir, bundles, "
        "rule pack, command graph, git, GitHub token, verdict log, yaml "
        "lib). Run once before adopting Roam Guard in CI."
    ),
)
def roam_guard_doctor(root: str = ".") -> dict:
    """Diagnose Roam Guard adoption readiness."""
    return _run_roam(["guard-doctor"], root)


@_tool(
    name="roam_guard_rules",
    description=(
        "Inspect or validate a Roam Guard rule pack. Subcommands: "
        "`show` (default) renders the pack, `validate` checks schema, "
        "`test` matches a path against the pack."
    ),
)
def roam_guard_rules(
    subcommand: str = "show",
    rules: str = "",
    path: str = "",
    from_bundle: bool = False,
    bundle: str = "",
    root: str = ".",
) -> dict:
    """Inspect rule packs.

    WHEN TO USE: before shipping a custom rule pack to CI, run
    `subcommand='validate'`. Use `subcommand='test', path=...` to dry-fire
    the pack against a single file path, or `subcommand='test',
    from_bundle=True` to dry-fire against every file in the active bundle.
    """
    args = ["guard-rules", subcommand]
    if rules:
        args.extend(["--rules", rules])
    if subcommand == "test":
        if from_bundle:
            args.append("--from-bundle")
            if bundle:
                args.extend(["--bundle", bundle])
        elif path:
            args.append(path)
    elif path:
        args.append(path)
    return _run_roam(args, root)


@_tool(
    name="roam_guard_history",
    description=(
        "List past Roam Guard verdicts on this repo (reads "
        "`.roam/verdict-log.jsonl` fast-path when present, falls back to "
        "scanning `.roam/pr-bundles/`). Supports `--verdict` and "
        "`--limit` filters."
    ),
)
def roam_guard_history(
    limit: int = 10,
    verdict: str = "",
    source: str = "auto",
    branch: str = "",
    root: str = ".",
) -> dict:
    """Show recent verdicts."""
    args = ["guard-history", "--limit", str(limit), "--source", source]
    if verdict:
        args.extend(["--verdict", verdict])
    if branch:
        args.extend(["--branch", branch])
    return _run_roam(args, root)


@_tool(
    name="roam_guard_diff",
    description=(
        "Verdict diff between two bundle snapshots (or the two most-recent "
        "verdict-log entries via `from_log=True`). Returns the verdict "
        "delta + reasons added/resolved + file/check counts. Answers "
        "'did my last commit help?'"
    ),
)
def roam_guard_diff(
    bundle_a: str = "",
    bundle_b: str = "",
    from_log: bool = False,
    branch: str = "",
    by_file: bool = False,
    root: str = ".",
) -> dict:
    """Compare two bundle snapshots.

    Pass `by_file=True` to also receive per-file annotations (status +
    reasons that name each file) — useful for answering "which files
    caused the verdict to move?"
    """
    args = ["guard-diff"]
    if from_log:
        args.append("--from-log")
        if branch:
            args.extend(["--branch", branch])
    else:
        if bundle_a:
            args.append(bundle_a)
        if bundle_b:
            args.append(bundle_b)
    if by_file:
        args.append("--by-file")
    return _run_roam(args, root)


@_tool(
    name="roam_proof_bundle",
    description=(
        "Compose AgentChangeProofBundle v1 from the active pr-bundle. "
        "Returns the structured verdict envelope an agent can attach to "
        "a PR. Supports markdown / json / sarif output formats."
    ),
)
def roam_proof_bundle(
    bundle: str = "",
    mode: str = "safe_edit",
    policy_profile: str = "default",
    strict: bool = False,
    fmt: str = "json",
    validate: bool = False,
    root: str = ".",
) -> dict:
    """Compose a v1 proof bundle without writing log or GH check."""
    args = ["proof-bundle", "--mode", mode, "--policy-profile", policy_profile, "--format", fmt]
    if bundle:
        args.extend(["--bundle", bundle])
    if strict:
        args.append("--strict")
    if validate:
        args.append("--validate")
    return _run_roam(args, root)


@_tool(
    name="roam_verification_contract",
    description=(
        "Compute the minimal `{required, skipped}` verification set for "
        "the current changed_files × risk × mode × policy. Surfaces what "
        "an agent MUST run before its PR can pass."
    ),
)
def roam_verification_contract(
    bundle: str = "",
    mode: str = "safe_edit",
    policy_profile: str = "default",
    rules: str = "",
    root: str = ".",
) -> dict:
    """Return the verification contract for the active bundle."""
    args = ["verification-contract", "--mode", mode, "--policy-profile", policy_profile]
    if bundle:
        args.extend(["--bundle", bundle])
    if rules:
        args.extend(["--rules", rules])
    return _run_roam(args, root)


@_tool(
    name="roam_guard_clean",
    description=(
        "Prune the verdict log at `.roam/verdict-log.jsonl` to its last "
        "N entries (default 500). Atomic rewrite — concurrent appenders "
        "never see a partial file. Pair `dry_run=True` for a probe."
    ),
    read_only=False,
    idempotent=True,
)
def roam_guard_clean(
    keep: int = 500,
    dry_run: bool = False,
    root: str = ".",
) -> dict:
    """Prune the verdict log to its last N entries."""
    args = ["guard-clean", "--keep", str(keep)]
    if dry_run:
        args.append("--dry-run")
    return _run_roam(args, root)


@_tool(
    name="roam_verdict",
    description=(
        "Compute a closed-enum verdict (pass / pass_with_warnings / "
        "needs_review / blocked) from the active pr-bundle. Pure judgment "
        "layer — no rendering, no log, no GH POST."
    ),
)
def roam_verdict(
    bundle: str = "",
    mode: str = "safe_edit",
    policy_profile: str = "default",
    root: str = ".",
) -> dict:
    """Return the closed-enum verdict for the active bundle."""
    args = ["verdict", "--mode", mode, "--policy-profile", policy_profile]
    if bundle:
        args.extend(["--bundle", bundle])
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="transport protocol (default: stdio)",
)
@click.option("--host", default="127.0.0.1", help="host for network transports")
@click.option("--port", type=int, default=8000, help="port for network transports")
@click.option("--no-auto-index", is_flag=True, help="skip automatic index freshness check")
@click.option("--list-tools", is_flag=True, help="list registered tools and exit")
@click.option("--list-tools-json", is_flag=True, help="list registered tools with metadata as JSON and exit")
@click.option(
    "--compat-profile",
    type=click.Choice(["all", "claude", "codex", "gemini", "copilot", "vscode", "cursor"]),
    default=None,
    help="emit client compatibility profile JSON and exit",
)
@click.option(
    "--card",
    is_flag=True,
    help=(
        "Print the MCP Server Card (the .well-known/mcp-server-card.json "
        "shape per spec 2025-11-25). Useful for piping into registry "
        "submissions: ``roam mcp --card | jq .``."
    ),
)
def mcp_cmd(transport, host, port, no_auto_index, list_tools, list_tools_json, compat_profile, card):
    """Start the roam MCP server.

    \b
    usage:
      roam mcp                    # stdio (for Claude Code, Cursor, etc.)
      roam mcp --transport sse    # SSE on localhost:8000
      roam mcp --transport streamable-http  # Streamable HTTP on localhost:8000
      roam mcp --list-tools       # show registered tools
      roam mcp --list-tools-json  # JSON metadata for conformance checks
      roam mcp --compat-profile all  # client compatibility matrix (JSON)

    \b
    environment:
      ROAM_MCP_PRESET=core        # tool preset (core/review/refactor/debug/architecture/full)
      ROAM_MCP_LITE=0             # legacy: same as ROAM_MCP_PRESET=full

    \b
    integration:
      claude mcp add roam-code -- roam mcp

    \b
    requires:
      pip install roam-code[mcp]
    """
    if compat_profile:
        payload = _compat_profile_payload(compat_profile, ".")
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if card:
        # MCP Server Card per blog.modelcontextprotocol.io/posts/2026-mcp-roadmap.
        # First-mover discovery for the MCP ecosystem registries
        # (PulseMCP, mcp.so, Smithery, etc.). v12.12.2: the card is
        # bundled inside the installed package (src/roam/mcp-server-card.json)
        # so post-PyPI ``roam mcp --card`` works without a source
        # checkout. The docs/site/.well-known/ copy stays canonical for
        # the hosted /well-known URL — they are kept in sync via the
        # release process.
        #
        # W624 / W642: resolve via ``importlib.resources`` only (mirrors
        # W554/W570/W577/W610/W664/W668 discipline). The package-data entry
        # ``"roam" = ["mcp-server-card.json"]`` in pyproject.toml + the
        # W664 / W610 drift-guards guarantee the file ships in every
        # install shape (pip wheel, editable, source checkout). No
        # filesystem-walk fallback — those silently mask packaging drift.
        from importlib.resources import as_file, files

        try:
            bundled_resource = files("roam") / "mcp-server-card.json"
            with as_file(bundled_resource) as bundled_path:
                if bundled_path.is_file():
                    click.echo(bundled_path.read_text(encoding="utf-8").rstrip())
                    return
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            click.echo(
                "error: mcp-server-card.json not reachable via importlib.resources "
                f"({exc!r}). Check pyproject.toml ships "
                '"roam" = ["mcp-server-card.json"] (W610 drift-guard).',
                err=True,
            )
            raise SystemExit(1)
        click.echo(
            "error: mcp-server-card.json resolved but is not a regular file. "
            "Check the wheel for a corrupted package-data entry.",
            err=True,
        )
        raise SystemExit(1)

    if mcp is None:
        click.echo(f"error: {_fastmcp_unavailable_message()}", err=True)
        raise SystemExit(1)

    if list_tools_json:

        async def _collect_tools():
            return await mcp.list_tools()

        tools = asyncio.run(_collect_tools())
        payload_tools = []
        for tool in sorted(tools, key=lambda t: t.name):
            ann = tool.annotations.model_dump(exclude_none=True) if tool.annotations else {}
            execution = tool.execution.model_dump(exclude_none=True) if tool.execution else {}
            meta = dict(tool.meta or {})
            # Include the inputSchema (parameters) so this output is a
            # complete proxy for the MCP ``tools/list`` response.
            # Conformance checkers and registry validators expect
            # inputSchema to be present ( ).
            try:
                input_schema = tool.parameters
            except AttributeError:
                input_schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
            payload_tools.append(
                {
                    "name": tool.name,
                    "title": tool.title,
                    "description": tool.description,
                    "annotations": ann,
                    "task_support": execution.get("taskSupport") or meta.get("taskSupport"),
                    "inputSchema": input_schema,
                }
            )
        payload = {
            "server": "roam-code",
            "preset": _ACTIVE_PRESET,
            "tool_count": len(payload_tools),
            "tools": payload_tools,
        }
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if list_tools:
        click.echo(f"{len(_REGISTERED_TOOLS)} tools registered (preset: {_ACTIVE_PRESET}):\n")
        for t in sorted(_REGISTERED_TOOLS):
            click.echo(f"  {t}")
        click.echo(f"\navailable presets: {', '.join(sorted(_PRESETS.keys()))}")
        return

    if not no_auto_index:
        sys.stderr.write("checking index freshness...\n")
        err = _ensure_fresh_index(".")
        if err:
            sys.stderr.write(f"warning: {err['error']}\n")
        else:
            sys.stderr.write("index is fresh.\n")

    # Register protocol-level completion handler (FTS5-backed symbol /
    # path / command lookup). Best-effort -- silently no-ops on
    # FastMCP versions that don't expose the hook.
    if _mcp_completions is not None:
        try:
            installed = _mcp_completions.install_completion_handler(mcp)
            if installed:
                sys.stderr.write("completion handler registered.\n")
        except Exception as exc:
            sys.stderr.write(f"completion handler not available: {exc}\n")

    # Optional file watcher for auto-reindex + resource invalidation.
    # Disabled by default; opt in with ROAM_MCP_WATCH=1 to keep the
    # default footprint minimal for users who don't need reactive updates.
    watch_handle = None
    if _mcp_watcher is not None and os.environ.get("ROAM_MCP_WATCH", "0").lower() in ("1", "true", "yes"):
        try:
            watch_handle = _mcp_watcher.start_watcher(mcp)
            if watch_handle is not None:
                sys.stderr.write("file watcher started (ROAM_MCP_WATCH=1).\n")
        except Exception as exc:
            sys.stderr.write(f"watcher not available: {exc}\n")

    try:
        if transport == "stdio":
            mcp.run()
        elif transport == "sse":
            mcp.run(transport="sse", host=host, port=port)
        else:
            try:
                mcp.run(transport="streamable-http", host=host, port=port)
            except TypeError:
                # Older FastMCP versions may use "http" alias.
                mcp.run(transport="http", host=host, port=port)
    finally:
        if watch_handle is not None:
            try:
                watch_handle.stop()
            except Exception as exc:  # noqa: BLE001 — shutdown cleanup must never mask the real exit
                # Watcher teardown on server shutdown — a failure here must
                # not mask the real exit path. Surface under ROAM_VERBOSE so
                # a watcher that fails to release its observer thread is
                # visible rather than silently leaking on every shutdown.
                log_swallowed("mcp_server:watch_handle_stop", exc)


# ---------------------------------------------------------------------------
# MCP Prompts — show as slash commands in Claude Code, VS Code, etc.
# ---------------------------------------------------------------------------

if mcp is not None:
    try:

        @mcp.prompt(name="roam-onboard", description="Get started with a new codebase")
        def prompt_onboard() -> str:
            return (
                "I'm new to this codebase. Please run roam_explore to get an overview, "
                "then summarize the architecture, key entry points, and tech stack. "
                "Suggest 3 files I should read first to understand the codebase."
            )

        @mcp.prompt(name="roam-review", description="Review pending code changes")
        def prompt_review() -> str:
            return (
                "Review my pending code changes. Run roam_review_change to check for "
                "breaking changes, risk score, and structural impact. Then run "
                "roam_affected_tests to identify what tests I should run. "
                "Summarize findings with actionable items."
            )

        @mcp.prompt(name="roam-debug", description="Debug a failing symbol or test")
        def prompt_debug(symbol: str = "") -> str:
            target = f" for `{symbol}`" if symbol else ""
            return (
                f"Help me debug an issue{target}. Run roam_diagnose_issue "
                f"{'with target=' + repr(symbol) + ' ' if symbol else ''}"
                "to find root cause suspects, then check the call chain and "
                "side effects. Suggest the most likely cause and a fix."
            )

        @mcp.prompt(name="roam-refactor", description="Plan a safe refactoring")
        def prompt_refactor(symbol: str = "") -> str:
            target = f" `{symbol}`" if symbol else " the target symbol"
            return (
                f"Help me safely refactor{target}. Run roam_prepare_change to check "
                "blast radius, affected tests, and side effects. Then suggest a "
                "step-by-step refactoring plan that minimizes risk."
            )

        @mcp.prompt(name="roam-health-check", description="Full codebase health assessment")
        def prompt_health_check() -> str:
            return (
                "Run a comprehensive health check on this codebase. Use roam_health "
                "for the overall score, roam_dead_code for unused code, and "
                "roam_complexity_report for complexity hotspots. Prioritize the "
                "top 3 issues I should fix first and explain why."
            )
    except (TypeError, AttributeError):
        # Older FastMCP versions may not support @mcp.prompt() — define as
        # plain functions so they're still importable for testing.
        def prompt_onboard() -> str:  # type: ignore[no-redef]
            return (
                "I'm new to this codebase. Please run roam_explore to get an overview, "
                "then summarize the architecture, key entry points, and tech stack. "
                "Suggest 3 files I should read first to understand the codebase."
            )

        def prompt_review() -> str:  # type: ignore[no-redef]
            return (
                "Review my pending code changes. Run roam_review_change to check for "
                "breaking changes, risk score, and structural impact. Then run "
                "roam_affected_tests to identify what tests I should run. "
                "Summarize findings with actionable items."
            )

        def prompt_debug(symbol: str = "") -> str:  # type: ignore[no-redef]
            target = f" for `{symbol}`" if symbol else ""
            return (
                f"Help me debug an issue{target}. Run roam_diagnose_issue "
                f"{'with target=' + repr(symbol) + ' ' if symbol else ''}"
                "to find root cause suspects, then check the call chain and "
                "side effects. Suggest the most likely cause and a fix."
            )

        def prompt_refactor(symbol: str = "") -> str:  # type: ignore[no-redef]
            target = f" `{symbol}`" if symbol else " the target symbol"
            return (
                f"Help me safely refactor{target}. Run roam_prepare_change to check "
                "blast radius, affected tests, and side effects. Then suggest a "
                "step-by-step refactoring plan that minimizes risk."
            )

        def prompt_health_check() -> str:  # type: ignore[no-redef]
            return (
                "Run a comprehensive health check on this codebase. Use roam_health "
                "for the overall score, roam_dead_code for unused code, and "
                "roam_complexity_report for complexity hotspots. Prioritize the "
                "top 3 issues I should fix first and explain why."
            )
else:
    # FastMCP not available — define plain functions for importability.
    def prompt_onboard() -> str:
        return (
            "I'm new to this codebase. Please run roam_explore to get an overview, "
            "then summarize the architecture, key entry points, and tech stack. "
            "Suggest 3 files I should read first to understand the codebase."
        )

    def prompt_review() -> str:
        return (
            "Review my pending code changes. Run roam_review_change to check for "
            "breaking changes, risk score, and structural impact. Then run "
            "roam_affected_tests to identify what tests I should run. "
            "Summarize findings with actionable items."
        )

    def prompt_debug(symbol: str = "") -> str:
        target = f" for `{symbol}`" if symbol else ""
        return (
            f"Help me debug an issue{target}. Run roam_diagnose_issue "
            f"{'with target=' + repr(symbol) + ' ' if symbol else ''}"
            "to find root cause suspects, then check the call chain and "
            "side effects. Suggest the most likely cause and a fix."
        )

    def prompt_refactor(symbol: str = "") -> str:
        target = f" `{symbol}`" if symbol else " the target symbol"
        return (
            f"Help me safely refactor{target}. Run roam_prepare_change to check "
            "blast radius, affected tests, and side effects. Then suggest a "
            "step-by-step refactoring plan that minimizes risk."
        )

    def prompt_health_check() -> str:
        return (
            "Run a comprehensive health check on this codebase. Use roam_health "
            "for the overall score, roam_dead_code for unused code, and "
            "roam_complexity_report for complexity hotspots. Prioritize the "
            "top 3 issues I should fix first and explain why."
        )


# ---------------------------------------------------------------------------
# ROADMAP A1 / W74 + W99 + W105 + W108: derived-view finalization
# ---------------------------------------------------------------------------
# Collapse the legacy split-brain dicts into derived views of
# ``_TOOL_METADATA``. Runs once, at module-load time, after every ``@_tool``
# decorator above has populated ``_TOOL_METADATA``. The legacy names stay
# importable (tests + ``_tool_annotations`` downstream may still read them),
# but their content now flows from kwargs on the ``@_tool`` decorator — a
# single source of truth. Adding a flagged tool requires only the matching
# kwarg on its decorator; no separate set to keep in sync.

_DESTRUCTIVE_TOOLS = frozenset(name for name, meta in _TOOL_METADATA.items() if meta.get("destructive", False))

# ROADMAP A1 / W108: derive _NON_READ_ONLY_TOOLS from _TOOL_METADATA
_NON_READ_ONLY_TOOLS = frozenset(name for name, meta in _TOOL_METADATA.items() if not meta.get("read_only", True))
# ROADMAP A1 / W113: derive _NON_IDEMPOTENT_TOOLS from _TOOL_METADATA.
# Independent axis from read_only (in current data they coincide — destructive
# tools are all also non-idempotent — but the semantic distinction matters
# for future tools, e.g. a read-only tool that's non-idempotent because it
# returns a UUID). The decorator's ``idempotent=...`` kwarg is the source
# of truth; this derived view is what ``_tool_annotations`` and downstream
# consumers read.
_NON_IDEMPOTENT_TOOLS = frozenset(name for name, meta in _TOOL_METADATA.items() if not meta.get("idempotent", True))

# W99 + W107: ``_TASK_REQUIRED_TOOLS`` derived from ``task_mode == "required"``.
# Same finalization pattern as ``_DESTRUCTIVE_TOOLS`` above. Reads the canonical
# 3-way enum rather than the legacy ``task_required`` bool, but the boolean
# field is still populated in ``_TOOL_METADATA`` for downstream consumers.
_TASK_REQUIRED_TOOLS = frozenset(name for name, meta in _TOOL_METADATA.items() if meta.get("task_mode") == "required")

# W105 + W107: ``_TASK_OPTIONAL_TOOLS`` derived from ``task_mode == "optional"``.
# Same finalization pattern as the two above. Pre-W107 the if/elif chain inside
# ``_tool`` could have silently preferred ``"required"`` if a tool appeared in
# both flags; the enum makes that impossible by construction (the decorator
# raises ValueError if both legacy bools are True). Disjointness is also pinned
# in ``tests/test_task_optional_tools_derived.py``.
_TASK_OPTIONAL_TOOLS = frozenset(name for name, meta in _TOOL_METADATA.items() if meta.get("task_mode") == "optional")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if mcp is None:
        raise SystemExit(_fastmcp_unavailable_message())
    mcp.run()
