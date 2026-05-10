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
import json
import os
import subprocess
import sys
import time as _time
from pathlib import Path

import click
from click.testing import CliRunner as _CliRunner

from roam.ask.workflow import workflow_metadata_for_recipe

try:
    from fastmcp import Context as _Context
    from fastmcp import FastMCP
except ImportError:
    _Context = None
    FastMCP = None

try:
    from mcp.types import ToolAnnotations as _ToolAnnotations
except ImportError:
    _ToolAnnotations = None

try:
    from fastmcp.server.tasks.config import TaskConfig as _TaskConfig
except Exception:
    _TaskConfig = None

# MCP-native enhancements (sampling, watcher, session, progress, completions).
# Each module is best-effort -- import failures degrade gracefully.
try:
    from roam.mcp_extras import completions as _mcp_completions
    from roam.mcp_extras import progress as _mcp_progress
    from roam.mcp_extras import sampling as _mcp_sampling
    from roam.mcp_extras import session as _mcp_session
    from roam.mcp_extras import watcher as _mcp_watcher
except Exception:
    _mcp_completions = None  # type: ignore[assignment]
    _mcp_progress = None  # type: ignore[assignment]
    _mcp_sampling = None  # type: ignore[assignment]
    _mcp_session = None  # type: ignore[assignment]
    _mcp_watcher = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Tool presets — named sets of tools exposed to agents.
# Default: "core" (23 tools + meta-tool = 24 total).
# Override: ROAM_MCP_PRESET=review|refactor|debug|architecture|full
# Legacy: ROAM_MCP_LITE=0 maps to "full" preset.
# ---------------------------------------------------------------------------

_CORE_TOOLS = {
    # compound operations (4) — each replaces 2-4 individual calls
    "roam_explore",
    "roam_prepare_change",
    "roam_review_change",
    "roam_diagnose_issue",
    # batch operations (2) — replace 10-50 sequential calls with one
    "roam_batch_search",
    "roam_batch_get",
    # comprehension (6)
    "roam_understand",
    "roam_search_symbol",
    "roam_complete",
    "roam_context",
    "roam_file_info",
    "roam_deps",
    # daily workflow (7)
    "roam_preflight",
    "roam_diff",
    "roam_pr_risk",
    "roam_affected_tests",
    "roam_impact",
    "roam_uses",
    "roam_syntax_check",
    # code quality (5)
    "roam_health",
    "roam_dead_code",
    "roam_complexity_report",
    "roam_diagnose",
    "roam_trace",
    # v12.6 — Python-pivot tools (2)
    "roam_py_types",
    "roam_py_modern",
    # v12 — retrieval / patch verification / agent fleet planning (3)
    "roam_retrieve",
    "roam_critique",
    "roam_fleet_plan",
    # v12.1 — boolean oracles (5) — 1-token answers for agent prompts
    "roam_oracle_symbol_exists",
    "roam_oracle_route_exists",
    "roam_oracle_is_test_only",
    "roam_oracle_is_reachable_from_entry",
    "roam_oracle_is_clone_of",
    # v12.1 — LLM-augmented taint classification (1)
    "roam_taint_classify",
    # v12.16 / machine-readable tool catalog (1)
    "roam_catalog",
    # v12.19 / agent-actionable wrappers for previously CLI-only signals (5)
    "roam_alerts",
    "roam_timeline",
    "roam_test_impact",
    "roam_disambiguate",
    "roam_why_fail",
    # v12.26 — Roam Agent Review + Cloud Lite engines (8)
    "roam_pr_analyze",
    "roam_pr_comment_render",
    "roam_metrics_push",
    "roam_audit_trail_verify",
    "roam_audit_trail_export",
    "roam_audit_trail_conformance_check",
    "roam_rules_validate",
    "roam_dogfood",
    # v12.51 — free-form intent dispatcher (replaces Grep+Read fallback)
    "roam_ask",
    # v12.51 — local-only tool-usage telemetry (introspection)
    "roam_session_metrics",
    # R8.E3 — pre-apply change-plan validator
    "roam_validate_plan",
    # R8.E4 — situation-keyed compound entry points
    "roam_for_new_feature",
    "roam_for_bug_fix",
    "roam_for_refactor",
    "roam_for_security_review",
    # R8.E8 — large-response handle retrieval
    "roam_fetch_handle",
}

_PRESETS: dict[str, set[str]] = {
    "core": _CORE_TOOLS.copy(),
    "review": _CORE_TOOLS
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
            "sequential `roam_uses` / `roam_search` calls. "
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

# Tools with side effects or non-idempotent behavior.
_NON_READ_ONLY_TOOLS = {
    "roam_annotate_symbol",
    "roam_ingest_trace",
    "roam_vuln_map",
    "roam_mutate",
    "roam_init",
    "roam_reindex",
}
_DESTRUCTIVE_TOOLS = {"roam_mutate"}
_NON_IDEMPOTENT_TOOLS = _NON_READ_ONLY_TOOLS.copy()

# Tools where task execution must be used (non-blocking by default).
# v12.2: promote `roam_health`, `roam_understand`, `roam_simulate` to
# required-task per MCP spec 2025-11-25 (SEP-1686). These all run >2s on
# a 14k-symbol repo — blocking the client is wrong UX. Tasks/get + cancel
# work end-to-end. roam was the first code-intel MCP server to ship this.
_TASK_REQUIRED_TOOLS = {
    "roam_init",
    "roam_reindex",
    "roam_health",
    "roam_understand",
    "roam_simulate",
}

# Long-running tools where task support is useful when FastMCP task extras exist.
_TASK_OPTIONAL_TOOLS = {
    "roam_orchestrate",
    "roam_mutate",
    "roam_vuln_map",
    "roam_ingest_trace",
    "roam_bisect_blame",
    "roam_forecast",
    "roam_path_coverage",
    "roam_search_semantic",
    "roam_closure",
    "roam_cut_analysis",
    "roam_generate_plan",
    "roam_adversarial_review",
}


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
    {"sections": {"type": "array"}, "target": {"type": "string"}},
    workflow=_WORKFLOW_SCHEMA,
    preflight={"type": "object", "description": "Safety check: blast radius, tests, fitness"},
    context={"type": "object", "description": "Files and line ranges to read"},
    effects={"type": "object", "description": "Side effects of the target symbol"},
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

_SCHEMA_UNDERSTAND = _make_schema(
    {"health_score": {"type": "number"}, "tech_stack": {"type": "array"}},
    architecture={"type": "object"},
    hotspots={"type": "array"},
)

_SCHEMA_HEALTH = _make_schema(
    {
        "health_score": {"type": "number"},
        "total_files": {"type": "integer"},
        "total_symbols": {"type": "integer"},
    },
    issues={"type": "array"},
    bottlenecks={"type": "array"},
)

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

_SCHEMA_PREFLIGHT = _make_schema(
    {"risk_level": {"type": "string"}, "target": {"type": "string"}},
    blast_radius={"type": "object"},
    affected_tests={"type": "array"},
    complexity={"type": "object"},
)

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

_SCHEMA_IMPACT = _make_schema(
    {"total_affected": {"type": "integer"}, "target": {"type": "string"}},
    affected_symbols={"type": "array"},
    affected_files={"type": "array"},
)

_SCHEMA_PR_RISK = _make_schema(
    {"risk_score": {"type": "number"}, "risk_level": {"type": "string"}},
    per_file={"type": "array"},
)

_SCHEMA_DIFF = _make_schema(
    {"changed_files": {"type": "integer"}},
    files={"type": "array"},
    affected_symbols={"type": "array"},
)

_SCHEMA_DIAGNOSE = _make_schema(
    {"target": {"type": "string"}, "top_suspect": {"type": "string"}},
    upstream_suspects={"type": "array"},
    downstream_suspects={"type": "array"},
)

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
    read_only = name not in _NON_READ_ONLY_TOOLS
    annotations = {
        "title": _tool_title(name),
        "readOnlyHint": read_only,
        "destructiveHint": name in _DESTRUCTIVE_TOOLS,
        "idempotentHint": name not in _NON_IDEMPOTENT_TOOLS,
        "openWorldHint": False,
    }
    # Non-core tools are lazily discoverable in clients that support this extension.
    if name not in _CORE_TOOLS and name != _META_TOOL:
        annotations["deferLoading"] = True
    return annotations


def _tool(
    name: str,
    description: str = "",
    output_schema: dict | None = None,
    *,
    version: str = "1.0.0",
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
    """

    def decorator(fn):
        if mcp is None:
            return fn
        # Meta-tool is always registered; others filtered by preset
        if name != _META_TOOL:
            if _ACTIVE_TOOLS and name not in _ACTIVE_TOOLS:
                return fn
        # Round 4 #14 / P: bound parallel tool invocations so the
        # FastMCP executor doesn't drop connections under burst load.
        # Over-capacity calls return a structured BUSY envelope with
        # a retry hint. Below capacity, overhead is one non-blocking
        # semaphore acquire (sub-microsecond).
        from roam.mcp_extras.concurrency import wrap_with_guard

        # R8.E8: post-process large returns into reference-based
        # handles so the agent's context budget isn't blown by a
        # single 50KB+ envelope. Wraps BEFORE the concurrency guard
        # so the guard's BUSY error envelope passes through unchanged
        # (errors are never handle-off'd).
        fn = _wrap_with_handle_off(name, fn)
        fn = wrap_with_guard(name, fn)
        _REGISTERED_TOOLS.append(name)
        # extract richer metadata from the tool's docstring so
        # ``roam_catalog`` consumers don't need to fetch each tool's
        # full description to pick the right one.
        when_to_use = ""
        examples: list[str] = []
        doc = fn.__doc__ or ""
        if doc:
            # ``WHEN TO USE:`` block — single line up to a blank line.
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("WHEN TO USE:"):
                    when_to_use = stripped.split(":", 1)[1].strip()
                    break
            # First three doctest-style example lines (``>>> roam ...``).
            for line in doc.splitlines():
                ls = line.strip()
                if ls.startswith(">>>") and "roam" in ls:
                    examples.append(ls[3:].strip())
                if len(examples) >= 3:
                    break
        _TOOL_METADATA[name] = {
            "name": name,
            "title": _tool_title(name),
            "description": description,
            "when_to_use": when_to_use,
            "examples": examples,
            "core": name in _CORE_TOOLS,
            "read_only": name not in _NON_READ_ONLY_TOOLS,
            "destructive": name in _DESTRUCTIVE_TOOLS,
            # Version stamp — agents can compare against a cached value
            # to detect schema drift without re-enumerating tools.
            # Bump when the input or output schema for ``name`` changes.
            "version": version,
        }
        kwargs: dict = {"name": name, "title": _tool_title(name)}
        if description:
            kwargs["description"] = description
        schema = output_schema if output_schema is not None else _ENVELOPE_SCHEMA
        kwargs["output_schema"] = schema
        kwargs["annotations"] = _tool_annotations(name)

        task_mode: str | None = None
        if name in _TASK_REQUIRED_TOOLS:
            task_mode = "required"
        elif name in _TASK_OPTIONAL_TOOLS:
            task_mode = "optional"
        if task_mode:
            # Metadata fallback for clients even when FastMCP task extras are absent.
            kwargs["meta"] = {"taskSupport": task_mode}
            if _TaskConfig is not None:
                kwargs["task"] = _TaskConfig(mode=task_mode)

        # Register with compatibility fallbacks:
        # 1) Full feature set
        # 2) Drop task support when tasks extras aren't installed
        # 3) Legacy FastMCP without output_schema/annotations/title/meta/task
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
            except (TypeError, ImportError) as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        return fn

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
    "EMPTY_INPUT": "error",       # missing required input
    "INVALID_DIFF": "error",      # malformed git diff in critique
    "RUN_FAILED": "error",        # subprocess returned non-zero
    "JSON_DECODE": "error",       # downstream produced non-JSON
    "ELICITATION_REQUIRED": "warning",  # awaits user response, retryable
    "FILE_NOT_FOUND": "error",    # specific file missing
    "DIRTY_TREE": "warning",      # uncommitted changes block emit
    "UNKNOWN": "error",
}


# error storm rate-limit. When the same error_code fires N
# times in a row, the verbose envelope (hint, suggested_action,
# doc_link, severity) is dropped on subsequent fires and replaced with
# a tight ``{error_code, repeat_count}`` shape. The full envelope
# returns the moment a different error_code fires (resets the counter).
_ERROR_STORM_THRESHOLD = 3
_ERROR_STORM_STATE: dict[str, int] = {"_last_code": 0, "_count": 0}


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
    import hashlib as _hashlib
    import json as _json

    if not isinstance(payload, dict):
        return payload
    # Don't handle-off errors — agent needs the full structured-error
    # envelope to decide whether to retry.
    if payload.get("isError"):
        return payload
    # Don't handle-off the fetch tool itself (would loop) or the meta-tool.
    if tool_name in {"roam_fetch_handle", _META_TOOL}:
        return payload
    # Don't double-handle: if the payload already IS a handle envelope
    # (e.g. an internal call composed from one), pass through.
    if payload.get("is_handle"):
        return payload

    try:
        threshold_kb = int(os.environ.get("ROAM_MCP_HANDLE_KB", "50"))
    except ValueError:
        threshold_kb = 50
    if threshold_kb <= 0:
        return payload
    threshold = threshold_kb * 1024

    try:
        blob = _json.dumps(payload, default=str)
    except (TypeError, ValueError):
        # Unserialisable — let the regular code path raise the error.
        return payload
    encoded = blob.encode("utf-8")
    size = len(encoded)
    if size < threshold:
        return payload

    sha = _hashlib.sha256(encoded).hexdigest()[:16]
    handle_dir = _handle_storage_dir()
    try:
        handle_dir.mkdir(parents=True, exist_ok=True)
        target = handle_dir / f"{sha}.json"
        # content-addressed → identical payload reuses the same file
        if not target.is_file():
            target.write_text(blob, encoding="utf-8")
    except OSError:
        # Read-only filesystem or permission issue — better to ship
        # the fat envelope than fail the call.
        return payload

    # Build a tiny preview so the agent has orientation without
    # fetching the full payload. Keep this VERY small.
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
                f"large response ({size:,} bytes) stored as handle {sha}; "
                "fetch the full payload via roam_fetch_handle"
            ),
            "byte_size": size,
            "handle": sha,
        },
    }


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


def _structured_error(error_dict: dict) -> dict:
    """Wrap error dict with MCP-compliant structured error fields (#116, #117).

    also fills the ``doc_link`` field so agents have a stable
    URL for self-service troubleshooting per error code.
    adds a ``severity`` field (info | warning | error | fatal)
    so agents can branch on severity without parsing the message.
    when the same ``error_code`` fires ≥
    ``_ERROR_STORM_THRESHOLD`` times in a row, drop the verbose fields
    on subsequent fires to save tokens in agent loops.
    """
    error_dict["isError"] = True
    code = error_dict.get("error_code", "UNKNOWN")
    error_dict["retryable"] = code in _RETRYABLE_CODES
    error_dict["suggested_action"] = error_dict.get("hint", "check the error message")
    error_dict.setdefault("doc_link", _DOC_LINKS.get(code, _DOC_LINKS["UNKNOWN"]))
    error_dict.setdefault("severity", _SEVERITY_MAP.get(code, "error"))

    if _ERROR_STORM_STATE.get("_last_code") == code:
        _ERROR_STORM_STATE["_count"] = int(_ERROR_STORM_STATE.get("_count", 0)) + 1
    else:
        _ERROR_STORM_STATE["_last_code"] = code
        _ERROR_STORM_STATE["_count"] = 1
    repeat = int(_ERROR_STORM_STATE["_count"])
    if repeat >= _ERROR_STORM_THRESHOLD:
        # R9 security recheck #3: keep ``retryable`` and ``doc_link`` in
        # the trimmed envelope. Agents that branch on ``retryable``
        # (e.g. retry on ``DB_LOCKED`` / ``INDEX_STALE``) used to stop
        # retrying after the third fire because the field went missing —
        # silent behaviour change. Same for ``doc_link``: dropping it
        # stripped the self-service URL from every recurring error.
        return {
            "isError": True,
            "error_code": code,
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
    return error_dict


def _reset_error_storm() -> None:
    """Test helper — reset the storm counter."""
    _ERROR_STORM_STATE["_last_code"] = 0
    _ERROR_STORM_STATE["_count"] = 0


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
    except Exception:
        pass
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
    except Exception:
        is_stale, reason = False, None
    _STALE_CHECK_CACHE["default"] = (now, is_stale, reason)
    return is_stale, reason


def _annotate_stale(result: dict, command: str) -> dict:
    """If the index is stale and *command* isn't a recovery tool,
    prepend a banner to the verdict and stamp ``_meta.stale_index``.
    """
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


def _run_roam_inprocess(args: list[str]) -> dict:
    """Run a roam CLI command in-process via Click CliRunner (no subprocess)."""
    from roam.cli import cli as _cli

    runner = _CliRunner()
    cmd_args = ["--json"] + args
    try:
        result = runner.invoke(_cli, cmd_args, catch_exceptions=True)
    except Exception as exc:
        return _structured_error(
            {
                "error": str(exc),
                "error_code": "UNKNOWN",
                "hint": "an unexpected error occurred.",
            }
        )

    output = result.output.strip() if result.output else ""

    # Gate failure (exit code 5) still produces valid JSON output — the
    # command completed but found issues.  Treat it like success for output
    # parsing, but annotate the result with gate_failure=True.
    from roam.exit_codes import EXIT_GATE_FAILURE

    _success_codes = {0, EXIT_GATE_FAILURE}

    # Successful JSON output — look for JSON object in output
    if result.exit_code in _success_codes and output:
        try:
            parsed = json.loads(output)
            if result.exit_code == EXIT_GATE_FAILURE:
                parsed["gate_failure"] = True
                parsed["exit_code"] = EXIT_GATE_FAILURE
            return parsed
        except json.JSONDecodeError as exc:
            return _structured_error(
                {
                    "error": f"Failed to parse JSON output: {exc}",
                    "error_code": "COMMAND_FAILED",
                    "hint": "command produced invalid JSON output.",
                }
            )

    # Error path — classify and return structured error
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
    from roam.exit_codes import EXIT_GATE_FAILURE

    _success_codes = {0, EXIT_GATE_FAILURE}

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
        if result.returncode in _success_codes and result.stdout.strip():
            parsed = json.loads(result.stdout)
            if result.returncode == EXIT_GATE_FAILURE:
                parsed["gate_failure"] = True
                parsed["exit_code"] = EXIT_GATE_FAILURE
            return parsed
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
    if exit_code in success_codes and stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return _structured_error(
                {
                    "error": f"Failed to parse JSON output: {exc}",
                    "error_code": "COMMAND_FAILED",
                    "hint": "command produced invalid JSON output.",
                }
            )
        if exit_code == EXIT_GATE_FAILURE:
            parsed["gate_failure"] = True
            parsed["exit_code"] = EXIT_GATE_FAILURE
        return parsed

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
    except Exception:
        pass


async def _ctx_info(ctx: _Context | None, message: str) -> None:
    """Best-effort MCP log message to the client."""
    if ctx is None or not hasattr(ctx, "info"):
        return
    try:
        await ctx.info(message)
    except Exception:
        pass


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
    except Exception:
        return None

    if getattr(response, "action", "") != "accept":
        return False
    parsed = _coerce_yes_no(getattr(response, "data", None))
    return parsed if parsed is not None else False


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
        if not data or "error" in data:
            err_msg = data.get("error", "empty result") if data else "empty result"
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
    partial_success = bool(failed_subcommands) and bool(sections)
    result: dict = {
        "command": command,
        "summary": {
            "verdict": " | ".join(verdicts) if verdicts else "compound operation completed",
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
    if partial_success:
        # Mention the failures explicitly in the verdict line so an LLM
        # reading just the summary doesn't miss them.
        prefix = f"PARTIAL ({len(failed_subcommands)} failed: {', '.join(failed_subcommands)}) — "
        result["summary"]["verdict"] = prefix + result["summary"]["verdict"]
    workflow = workflow_metadata_for_recipe(str(workflow_recipe)) if workflow_recipe else None
    if workflow:
        result["workflow"] = workflow
        result["summary"]["workflow_phase"] = workflow["phase"]
        result["summary"]["workflow_recipe"] = workflow["recipe"]
    result.update(sections)

    if errors:
        result["_errors"] = errors

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
    description="Pre-change bundle: preflight + context + effects in one call. Call BEFORE modifying code.",
    output_schema=_SCHEMA_PREPARE_CHANGE,
)
def prepare_change(
    target: str,
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
    target:
        Symbol name or file path to prepare for changing.
    staged:
        If True, check staged (git add-ed) changes instead.
    budget:
        Max output tokens (0 = unlimited). Truncates intelligently.
    session_hint:
        Optional conversation hint used to personalize context ranking.
    recent_symbols:
        Comma-separated recently discussed symbols for rank biasing.

    Returns: preflight safety data, context files to read, and side
    effects of the target. Each section includes its own verdict.
    """
    budget_args = ["--budget", str(budget)] if budget else []
    pf_args = budget_args + ["preflight"]
    if target:
        pf_args.append(target)
    if staged:
        pf_args.append("--staged")

    preflight_data = _run_roam(pf_args, root)

    ctx_data: dict = {}
    effects_data: dict = {}
    if target:
        ctx_args = budget_args + ["context", target, "--task", "refactor"]
        _append_context_personalization_args(
            ctx_args,
            session_hint=session_hint,
            recent_symbols=recent_symbols,
        )
        ctx_data = _run_roam(ctx_args, root)
        effects_data = _run_roam(budget_args + ["effects", target], root)

    result = _compound_envelope(
        "prepare-change",
        [
            ("preflight", preflight_data),
            ("context", ctx_data),
            ("effects", effects_data),
        ],
        target=target,
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
    description="Debug bundle: root cause suspects + side effects in one call.",
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

_MAX_BATCH_QUERIES = 10
_MAX_BATCH_SYMBOLS = 50

# FTS5 search SQL used by batch_search — same as resolve.fts_suggestions
_BATCH_FTS_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbol_fts sf "
    "JOIN symbols s ON sf.rowid = s.id "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE symbol_fts MATCH ? "
    "ORDER BY rank "
    "LIMIT ?"
)

# Fallback LIKE SQL when FTS5 is unavailable or returns nothing
_BATCH_LIKE_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbols s "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE s.name LIKE ? COLLATE NOCASE "
    "ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name "
    "LIMIT ?"
)


def _fts_query_for(q: str) -> str:
    """Build an FTS5 MATCH expression for a raw search query string."""
    tokens = q.replace("_", " ").replace(".", " ").split()
    if tokens:
        return " OR ".join(f'"{t}"*' for t in tokens)
    return f'"{q}"*'


def _batch_search_one(conn, q: str, limit: int) -> tuple[list, str | None]:
    """Search for one query in an open DB connection.

    Returns (rows, error_or_None).  Rows are plain dicts.
    Tries FTS5 first; falls back to LIKE match if FTS5 is unavailable.
    """
    rows: list = []
    try:
        fts_q = _fts_query_for(q)
        rows = conn.execute(_BATCH_FTS_SQL, (fts_q, limit)).fetchall()
    except Exception:
        rows = []

    if not rows:
        try:
            rows = conn.execute(_BATCH_LIKE_SQL, (f"%{q}%", limit)).fetchall()
        except Exception as exc:
            return [], str(exc)

    return [
        {
            "name": r["name"],
            "qualified_name": r["qualified_name"] or "",
            "kind": r["kind"],
            "file_path": r["file_path"],
            "line_start": r["line_start"],
            "pagerank": round(float(r["pagerank"] or 0), 4),
        }
        for r in rows
    ], None


def _batch_get_one(conn, sym: str) -> tuple[dict | None, str | None]:
    """Retrieve full details for a single symbol in an open DB connection.

    Returns (details_dict, error_or_None).
    Uses the same lookup chain as find_symbol(): qualified -> name -> fuzzy.
    """
    from roam.commands.resolve import find_symbol
    from roam.db.queries import CALLEES_OF, CALLERS_OF, METRICS_FOR_SYMBOL
    from roam.output.formatter import loc

    try:
        s = find_symbol(conn, sym)
    except Exception as exc:
        return None, str(exc)

    if s is None:
        return None, f"symbol not found: {sym!r}"

    try:
        metrics = conn.execute(METRICS_FOR_SYMBOL, (s["id"],)).fetchone()
        callers = conn.execute(CALLERS_OF, (s["id"],)).fetchall()
        callees = conn.execute(CALLEES_OF, (s["id"],)).fetchall()
    except Exception as exc:
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


@_tool(
    name="roam_batch_search",
    description="Search up to 10 patterns in one call. Replaces 10 sequential roam_search_symbol calls.",
    output_schema=_SCHEMA_BATCH_SEARCH,
)
def batch_search(queries: list, limit_per_query: int = 5, root: str = ".") -> dict:
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
    root:
        Project root directory (default ".").

    Returns: per-query result lists plus aggregate match count.
    Partial failures are collected in ``errors``; remaining queries still run.
    """
    from roam.commands.resolve import ensure_index
    from roam.db.connection import open_db

    ensure_index()

    queries_list: list[str] = [str(q) for q in (queries or [])][:_MAX_BATCH_QUERIES]
    limit = max(1, min(int(limit_per_query), 50))

    results: dict = {}
    errors: dict = {}

    if not queries_list:
        return {
            "command": "batch-search",
            "summary": {
                "verdict": "no queries provided",
                "queries_executed": 0,
                "total_matches": 0,
            },
            "results": {},
            "errors": {},
        }

    try:
        with open_db(readonly=True) as conn:
            for q in queries_list:
                rows, err = _batch_search_one(conn, q, limit)
                if err:
                    errors[q] = err
                else:
                    results[q] = rows
    except Exception as exc:
        # Index not available or other fatal DB error — return structured error
        return {
            "command": "batch-search",
            "summary": {
                "verdict": f"batch search failed: {exc}",
                "queries_executed": 0,
                "total_matches": 0,
            },
            "results": {},
            "errors": {"_fatal": str(exc)},
        }

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
    from roam.commands.resolve import ensure_index
    from roam.db.connection import open_db

    ensure_index()

    symbols_list: list[str] = [str(s) for s in (symbols or [])][:_MAX_BATCH_SYMBOLS]

    results: dict = {}
    errors: dict = {}

    if not symbols_list:
        return {
            "command": "batch-get",
            "summary": {
                "verdict": "no symbols provided",
                "symbols_requested": 0,
                "symbols_resolved": 0,
            },
            "results": {},
            "errors": {},
        }

    try:
        with open_db(readonly=True) as conn:
            for sym in symbols_list:
                details, err = _batch_get_one(conn, sym)
                if err or details is None:
                    errors[sym] = err or "not found"
                else:
                    results[sym] = details
    except Exception as exc:
        return {
            "command": "batch-get",
            "summary": {
                "verdict": f"batch get failed: {exc}",
                "symbols_requested": len(symbols_list),
                "symbols_resolved": 0,
            },
            "results": {},
            "errors": {"_fatal": str(exc)},
        }

    resolved = len(results)
    verdict = f"{resolved}/{len(symbols_list)} symbols resolved"
    if errors:
        verdict += f", {len(errors)} not found"

    payload: dict = {
        "command": "batch-get",
        "summary": {
            "verdict": verdict,
            "symbols_requested": len(symbols_list),
            "symbols_resolved": resolved,
        },
        "results": results,
    }
    if errors:
        payload["errors"] = errors

    return payload


# ===================================================================
# Tier 1 tools -- the most valuable for day-to-day AI agent work
# ===================================================================


@_tool(
    name="roam_expand_toolset",
    description="List available tool presets or show contents of a preset. "
    "Presets: core (16), review (27), refactor (26), debug (27), architecture (29), full (all).",
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
    if force and not confirm_force:
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
        if not approved:
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
    description="Full codebase briefing: stack, architecture, health, hotspots. Call FIRST in a new repo.",
    output_schema=_SCHEMA_UNDERSTAND,
)
async def understand(
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


@_tool(name="roam_onboard")
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
        "Free-form intent dispatcher: maps a natural-language question "
        '("is it safe to delete X", "where does login validate", '
        '"what just broke") to one of 24 pre-built recipes that compose '
        "preflight / retrieve / critique / fleet / diagnose / trace / "
        "trends / hotspots / debt / taint commands. Call this BEFORE "
        "falling back to Grep+Read — the recipe registry covers most "
        "common workflows in one tool call."
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
def preflight(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Pre-change safety check. Call this BEFORE modifying any symbol or file.

    Combines blast radius, affected tests, complexity, coupling, and
    fitness violations in one call. Replaces 5-6 separate tool calls.
    Do NOT call context, impact, affected_tests, or complexity_report
    separately if preflight covers your need."""
    args = ["preflight"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


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
    exact = [
        m for m in matches
        if isinstance(m, dict) and m.get("name") == symbol
    ]
    if exact:
        return True, exact[:5]
    qual = [
        m for m in matches
        if isinstance(m, dict) and m.get("qualified_name") == symbol
    ]
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


def _vp_validate_one(idx: int, op: dict, root: str = ".") -> dict:
    """Validate a single change-plan operation. See
    :func:`validate_plan` for the operation schema."""
    kind = (op.get("kind") or "").lower()
    blockers: list[dict] = []
    warnings: list[dict] = []
    advice: list[str] = []
    facts: dict = {}

    def _block(code: str, detail: str) -> None:
        blockers.append({"code": code, "detail": detail})

    def _warn(code: str, detail: str) -> None:
        warnings.append({"code": code, "detail": detail})

    if kind in {"rename", "move", "remove", "modify"}:
        symbol = op.get("symbol") or ""
        if not symbol:
            _block("MISSING_SYMBOL", f"{kind} requires 'symbol'")
        else:
            found, candidates = _vp_check_symbol_exists(symbol, root)
            facts["symbol_found"] = found
            if not found:
                _block(
                    "SYMBOL_NOT_FOUND",
                    f"symbol {symbol!r} not in index — did you mean: "
                    + ", ".join(c.get("name") or c.get("qualified_name") or "?" for c in candidates[:3])
                    if candidates else f"symbol {symbol!r} not in index",
                )
            else:
                blast = _vp_blast_radius(symbol, root)
                facts["blast_radius"] = blast
                if isinstance(blast, int):
                    if blast > 50:
                        _warn(
                            "HIGH_BLAST_RADIUS",
                            f"{symbol} has {blast} incoming callers — review impact before applying.",
                        )
                    elif blast > 10:
                        _warn(
                            "MEDIUM_BLAST_RADIUS",
                            f"{symbol} has {blast} incoming callers — proceed with care.",
                        )

    if kind == "rename":
        new_name = op.get("new_name") or ""
        if not new_name:
            _block("MISSING_NEW_NAME", "rename requires 'new_name'")
        else:
            new_found, _ = _vp_check_symbol_exists(new_name, root)
            facts["new_name_collision"] = new_found
            if new_found:
                _warn(
                    "NAME_COLLISION",
                    f"another symbol already uses {new_name!r} — rename may shadow it.",
                )
                advice.append("run `roam search <new_name>` to inspect the collision.")

    elif kind == "move":
        target_file = op.get("target_file") or ""
        if not target_file:
            _block("MISSING_TARGET_FILE", "move requires 'target_file'")
        else:
            # For move, target file may or may not exist — both are
            # valid (existing file means appending, new file means
            # creating). Just verify the parent dir is sane.
            ok, reason = _vp_check_target_file(target_file, must_exist=False, root=root)
            facts["target_file_ok"] = ok
            if not ok and "already exists" not in reason:
                _block("INVALID_TARGET_FILE", reason)

    elif kind == "remove":
        blast = facts.get("blast_radius")
        if isinstance(blast, int) and blast > 0:
            _block(
                "REMOVE_HAS_CALLERS",
                f"cannot remove {op.get('symbol')!r} — {blast} callers would break. "
                "Migrate or update them first.",
            )

    elif kind == "modify":
        # Modify is a soft op — the agent is just signalling intent.
        # We surface fitness/complexity from preflight as an advisory.
        symbol = op.get("symbol") or ""
        if symbol:
            pre = _run_roam(["preflight", symbol], root)
            if isinstance(pre, dict):
                summary = pre.get("summary") or {}
                if isinstance(summary, dict):
                    facts["preflight_verdict"] = summary.get("verdict")
                    fitness = summary.get("fitness_violations") or summary.get("violations")
                    if isinstance(fitness, list) and fitness:
                        _warn(
                            "FITNESS_VIOLATIONS",
                            f"{symbol} has {len(fitness)} fitness violation(s) — fix before adding new logic.",
                        )

    elif kind == "add":
        file_path = op.get("file") or ""
        if not file_path:
            _block("MISSING_FILE", "add requires 'file'")
        else:
            ok, reason = _vp_check_target_file(file_path, must_exist=False, root=root)
            facts["file_ok"] = ok
            if not ok:
                _block("INVALID_ADD_FILE", reason)

    else:
        _block("UNKNOWN_KIND", f"unsupported operation kind: {kind!r}")

    return {
        "index": idx,
        "kind": kind,
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "advice": advice,
        "facts": facts,
    }


@_tool(
    name="roam_validate_plan",
    description="Pre-apply validator for a multi-step change plan. Returns blockers, warnings, advice per operation.",
    version="1.0.0",
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
    import json as _json

    if not operations and plan_json:
        try:
            parsed = _json.loads(plan_json)
            if isinstance(parsed, list):
                operations = parsed
            elif isinstance(parsed, dict):
                ops = parsed.get("operations")
                if isinstance(ops, list):
                    operations = ops
        except _json.JSONDecodeError as e:
            return _structured_error(
                {
                    "error": f"plan_json is not valid JSON: {e}",
                    "error_code": "USAGE_ERROR",
                    "hint": "pass operations=[{...}] or plan_json='{\"operations\":[...]}'",
                    "command": "roam_validate_plan",
                }
            )

    if not operations or not isinstance(operations, list):
        return _structured_error(
            {
                "error": "no operations supplied",
                "error_code": "USAGE_ERROR",
                "hint": "pass operations=[{kind:'rename', symbol:'x', new_name:'y'}, ...]",
                "command": "roam_validate_plan",
            }
        )

    op_results: list[dict] = []
    total_blockers = 0
    total_warnings = 0
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            op_results.append(
                {
                    "index": i,
                    "kind": "unknown",
                    "ok": False,
                    "blockers": [
                        {"code": "MALFORMED_OP", "detail": f"operation {i} is not an object"}
                    ],
                    "warnings": [],
                    "advice": [],
                    "facts": {},
                }
            )
            total_blockers += 1
            continue
        result = _vp_validate_one(i, op, root)
        op_results.append(result)
        total_blockers += len(result.get("blockers", []))
        total_warnings += len(result.get("warnings", []))

    if total_blockers:
        verdict = "blocked"
    elif total_warnings:
        verdict = "needs-review"
    else:
        verdict = "ok"

    summary_text = (
        f"{verdict}: {len(op_results)} operation(s), "
        f"{total_blockers} blocker(s), {total_warnings} warning(s)"
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


@_tool(
    name="roam_fetch_handle",
    description="Fetch the full payload for a handle returned by a large MCP tool response.",
    version="1.0.0",
)
def fetch_handle(handle: str = "", root: str = ".", ctx: _Context | None = None) -> dict:
    """Retrieve a large MCP response previously written to disk under a
    content-addressed handle.

    WHEN TO USE: When a tool returned a small envelope with
    ``is_handle=true`` and a ``handle: "<sha16>"`` field, call this to
    fetch the full payload. The preview block in the original envelope
    tells you whether you actually need it — most agents can answer
    from the preview alone.

    Parameters
    ----------
    handle:
        The 16-char hex handle returned by an earlier tool call. The
        file must exist under ``.roam/responses/<handle>.json`` in the
        current project.

    Returns: the full original payload, or a structured error if the
    handle is unknown / the file was deleted.
    """
    import json as _json
    import re as _re

    if not handle or not _re.fullmatch(r"[0-9a-f]{16}", handle):
        return _structured_error(
            {
                "error": "handle must be a 16-char lowercase hex string",
                "error_code": "USAGE_ERROR",
                "hint": "pass the handle from a prior tool response — e.g. 'a1b2c3d4...' (16 chars).",
                "command": "roam_fetch_handle",
            }
        )
    target = _handle_storage_dir() / f"{handle}.json"
    if not target.is_file():
        return _structured_error(
            {
                "error": f"handle {handle!r} not found in {target.parent}",
                "error_code": "NO_RESULTS",
                "hint": (
                    "the response may have been cleaned up. Re-run the "
                    "original tool call to regenerate the handle."
                ),
                "command": "roam_fetch_handle",
            }
        )
    try:
        return _json.loads(target.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as e:
        return _structured_error(
            {
                "error": f"could not read handle file: {type(e).__name__}: {e}",
                "error_code": "PERMISSION_DENIED" if isinstance(e, OSError) else "COMMAND_FAILED",
                "hint": "check filesystem permissions on .roam/responses/, then retry.",
                "command": "roam_fetch_handle",
            }
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


def _safe_run(args: list[str], root: str) -> dict:
    """Wrapper around _run_roam that converts exceptions into structured
    error dicts so a partial failure in one sub-command doesn't poison
    the whole compound envelope."""
    try:
        out = _run_roam(args, root)
        if isinstance(out, dict):
            return out
        return {"error": f"unexpected return type: {type(out).__name__}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


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
    sections = [
        ("understand", _safe_run(["understand"], root)),
        ("complexity_report", _safe_run(["complexity", "--limit", "10"], root)),
    ]
    if area:
        sections.append(("search", _safe_run(["search", area], root)))
        # Only fetch context if search found a symbol — context for an
        # unmatched query is wasted tokens.
        search_res = sections[-1][1]
        matches = []
        if isinstance(search_res, dict):
            matches = search_res.get("matches") or search_res.get("results") or []
        if matches:
            top = matches[0] if isinstance(matches, list) and matches else None
            anchor = top.get("qualified_name") or top.get("name") if isinstance(top, dict) else None
            if anchor:
                sections.append(("context", _safe_run(["context", anchor], root)))
    return _compound_envelope(
        "for-new-feature",
        sections,
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
    sections = [
        ("diagnose", _safe_run(["diagnose", symbol], root)),
        ("affected_tests", _safe_run(["affected-tests", symbol], root)),
        # `roam diff` of the working tree shows what's recently been
        # touched in the area — context for whether this is a new
        # regression or a long-standing issue.
        ("diff", _safe_run(["diff"], root)),
        ("context", _safe_run(["context", symbol], root)),
    ]
    return _compound_envelope(
        "for-bug-fix",
        sections,
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
    sections = [
        ("preflight", _safe_run(["preflight", symbol], root)),
        ("impact", _safe_run(["impact", symbol], root)),
        ("complexity_report", _safe_run(["complexity-report", "--limit", "5"], root)),
        # Cap at top-20 clone clusters; --top is the right flag (clones
        # uses --top, not --limit; CLI surface drift caught here).
        ("clones", _safe_run(["clones", "--top", "20"], root)),
    ]
    return _compound_envelope(
        "for-refactor",
        sections,
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
    sections = [
        ("taint", _safe_run(["taint"], root)),
        ("vuln", _safe_run(["vuln", "list"], root)),
        # ``critique`` reads the working-tree diff (and is a no-op if
        # nothing's staged); it pairs naturally here because the agent
        # is often reviewing a PR's worth of changes.
        ("critique", _safe_run(["critique"], root)),
    ]
    adv_args = ["adversarial"]
    if symbol:
        adv_args.append(symbol)
    sections.append(("adversarial", _safe_run(adv_args, root)))
    return _compound_envelope(
        "for-security-review",
        sections,
        situation="security_review",
        target=symbol or "(full repo)",
    )


@_tool(
    name="roam_search_symbol",
    description="Find symbols by name substring. Returns kind, file, line, PageRank importance.",
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
def complete(prefix: str, kind: str = "symbol", limit: int = 30, root: str = ".") -> dict:
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
    payload = _mcp_completions.complete_prefix(prefix, kind=kind, limit=limit, root=root)
    total = sum(len(v) for v in payload.values())
    return {
        "command": "roam_complete",
        "summary": {
            "verdict": f"{total} completion{'s' if total != 1 else ''} for {prefix!r}",
            "prefix": prefix,
            "kind": kind,
        },
        **payload,
    }


@_tool(
    name="roam_context",
    description="Minimal files + line ranges needed to work with a symbol.",
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
    ``roam_search`` (which only returns names without budget /
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
    args = ["retrieve", task]
    if budget:
        args.extend(["--budget", str(budget)])
    if k:
        args.extend(["--k", str(k)])
    if rerank:
        args.extend(["--rerank", rerank])
    for raw in (seed_files or "").split(","):
        path = raw.strip()
        if path:
            args.extend(["--seed-files", path])
    if dry_run:
        args.append("--dry-run")
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
    description="Verify a patch against the indexed graph (clones-not-edited + blast radius). Pipe a diff in `diff_text`.",
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
# v12.1 — Boolean oracles (5)
# ---------------------------------------------------------------------------
# Each oracle returns a single yes/no fact about the indexed graph. Direct
# response to CKB v9.2's `symbolExists` / `analyzeOutgoingImpact` pattern —
# 1-token answers keep agent prompts tight. All five wrap pure-function
# implementations in `commands.cmd_oracle` so the same logic runs from CLI
# and from MCP.


@_tool(
    name="roam_oracle_symbol_exists",
    description="Boolean oracle: does any symbol with this name (or qualified name) exist in the indexed graph?",
)
def oracle_symbol_exists(name: str, root: str = ".") -> dict:
    """Yes/no: is there an indexed symbol matching this name?

    WHEN TO USE: cheapest possible existence check before generating
    references to a symbol that might have been renamed/removed. Returns
    ``{"value": bool, "reason": str}`` plus the standard envelope.

    Differs from ``roam_search_symbol`` (which lists matches with metadata)
    — this returns just a boolean for tight agent prompts. Matches name OR
    qualified_name OR ``%.<name>`` suffix on qualified_name.
    """
    return _run_roam(["oracle", "symbol-exists", name], root)


@_tool(
    name="roam_oracle_route_exists",
    description="Boolean oracle: does any HTTP route handler match this URL path?",
)
def oracle_route_exists(path: str, root: str = ".") -> dict:
    """Yes/no: is there a route handler for this URL path?

    WHEN TO USE: before generating a fetch/request to a backend endpoint
    — confirm the route actually exists. Reads ``cross_repo_edges``
    (populated by ``roam ws resolve``) when available; falls back to
    scanning route-handler-shaped symbols (``app.get/post/...``,
    ``Route::get/...``, ``@app.get(...)``).
    """
    return _run_roam(["oracle", "route-exists", path], root)


@_tool(
    name="roam_oracle_is_test_only",
    description="Boolean oracle: are ALL callers of this symbol in test files?",
)
def oracle_is_test_only(name: str, root: str = ".") -> dict:
    """Yes/no/indeterminate: do all callers of this symbol live in test files?

    WHEN TO USE: before deleting a symbol or marking it as dead. A
    ``True`` answer means the symbol exists only to support tests and
    likely targets *production* code that's been gone for a while; combine
    with ``roam_safe_delete`` for full evidence.

    Tri-state: orphan symbols (no callers at all) return ``value=null``
    with ``reason_class="indeterminate_no_data"`` rather than collapsing
    to ``False`` — there's no evidence either way.
    """
    return _run_roam(["oracle", "is-test-only", name], root)


@_tool(
    name="roam_oracle_test_only",
    description="Alias of roam_oracle_is_test_only — preserves the shorter name agents sometimes guess.",
)
def oracle_test_only_alias(name: str, root: str = ".") -> dict:
    """Alias of :func:`oracle_is_test_only`.

    Round 4 #15 reported agents calling ``roam_oracle_test_only``
    (without the ``is_`` prefix) and getting ``No such tool``. The alias
    keeps the canonical name discoverable while accepting the shorter
    form so a typo doesn't cost an MCP round-trip.
    """
    return _run_roam(["oracle", "is-test-only", name], root)


@_tool(
    name="roam_oracle_is_reachable_from_entry",
    description="Boolean oracle: can BFS reach this symbol from any entry point in the call graph?",
)
def oracle_is_reachable_from_entry(name: str, max_hops: int = 10, root: str = ".") -> dict:
    """Yes/no: is this symbol reachable from any entry-point symbol?

    WHEN TO USE: before treating a symbol as "live code" — confirm the
    static call graph actually has a path from an entry point (``main``,
    a Click command, an HTTP route, an event handler, anything tagged
    ``is_entry = 1`` or living in a file with ``file_role = 'entry'``).

    BFS over ``edges.kind IN ('calls', 'references')`` up to ``max_hops``
    deep. ``False`` from this oracle is the strongest signal a symbol is
    truly unreachable — combine with ``roam_safe_delete`` for the full
    evidence bundle.

    Parameters
    ----------
    max_hops: BFS depth cap (default 10). Increase for very deep graphs;
        decrease for quick sanity checks.
    """
    args = ["oracle", "is-reachable-from-entry", name]
    if max_hops != 10:
        args.extend(["--max-hops", str(max_hops)])
    return _run_roam(args, root)


@_tool(
    name="roam_oracle_is_clone_of",
    description="Boolean oracle: does this symbol participate in a persisted clone cluster?",
)
def oracle_is_clone_of(name: str, root: str = ".") -> dict:
    """Yes/no: does this symbol have persisted clone siblings?

    WHEN TO USE: before editing a symbol — if it's a clone, the same fix
    likely needs to land on its siblings. Reads ``clone_pairs`` (populated
    by ``roam clones --persist``); returns ``False`` with a hint when the
    table is empty.
    """
    return _run_roam(["oracle", "is-clone-of", name], root)


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
    with open_db(readonly=True, project_root=project_root_path) as conn:
        for item in items:
            if not isinstance(item, dict):
                results.append({"error": "item must be a dict", "input": item})
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
                    results.append({"error": f"unknown oracle '{oracle_name}'", "input": item})
                    continue
            except Exception as exc:
                results.append({"error": f"{oracle_name} crashed: {exc}", "input": item})
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
        "predicate type `roam-code.dev/CodeGraph/v1` (or `CodeGraph-"
        "AIBOM/v1` with --aibom). Merkle root over symbol fingerprints + "
        "edge-bundle digest. Optional cosign keyless or offline signing."
    ),
)
def cga_emit(
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
    statement_path: str,
    cosign_bundle: str = "",
    cosign_key: str = "",
    no_cosign: bool = False,
    root: str = ".",
) -> dict:
    """Verify a CGA statement file against the live indexed DB.

    WHEN TO USE: at audit / receipt time. Pair with the public key
    distributed alongside the codebase to verify both the Merkle digest
    AND the cosign identity in one call.
    """
    args = ["cga", "verify", statement_path]
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
    label_counts: dict[str, int] = {}
    for f in classified:
        cls = f.get("classification") or {}
        lbl = cls.get("label")
        if lbl:
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
    summary = dict(out.get("summary") or {})
    summary["classification_counts"] = label_counts
    summary["classified_count"] = sum(label_counts.values())
    out["summary"] = summary
    return out


@_tool(
    name="roam_trace",
    description="Shortest dependency path between two symbols with hop details.",
    output_schema=_SCHEMA_TRACE,
)
def trace(source: str, target: str, root: str = ".") -> dict:
    """Find the shortest dependency path between two symbols.

    Call this to understand HOW a change in one symbol could affect
    another. Shows path hops with symbol names, edge types, locations,
    and coupling strength."""
    return _run_roam(["trace", source, target], root)


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
    full source. More useful than Read for getting a file overview."""
    return _run_roam(["file", path], root)


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


@_tool(name="roam_pr_analyze")
def pr_analyze(
    diff_path: str = "",
    commit_range: str = "",
    staged: bool = False,
    rules_path: str = "",
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
        ``staged`` / unstaged ``git diff`` is used.
    commit_range:
        Git range (e.g. ``"main..HEAD"``).
    staged:
        Analyse staged changes.
    rules_path:
        Path to ``.roam/rules.yml`` (default: auto-detect).
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
    if rules_path:
        args.extend(["--rules", rules_path])
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


@_tool(name="roam_pr_comment_render")
def pr_comment_render(envelope_path: str, style: str = "github", include_links: bool = True, root: str = ".") -> dict:
    """Render a markdown PR comment from a pr-analyze JSON envelope.

    WHEN TO USE: After ``roam_pr_analyze``, render the verdict as a sticky
    GitHub / GitLab PR comment. Used by the Roam Agent Review GitHub App
    worker; useful locally to dogfood the comment shape before the bot
    posts it.

    Parameters
    ----------
    envelope_path:
        Path to a saved ``roam pr-analyze --json`` envelope on disk.
    style:
        ``github`` / ``gitlab`` / ``plain`` (default: ``github``).
    include_links:
        Append the small attribution + docs footer.

    Returns: ``{summary: {...}, markdown: "..."}`` — the rendered comment
    in the ``markdown`` field plus a small summary block.
    """
    args = ["pr-comment-render", "--input", envelope_path, "--style", style]
    if not include_links:
        args.append("--no-links")
    return _run_roam(args, root)


@_tool(name="roam_audit_trail_verify")
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


@_tool(name="roam_audit_trail_export")
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


@_tool(name="roam_metrics_push")
def metrics_push(
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


@_tool(name="roam_audit_trail_conformance_check")
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


@_tool(name="roam_dogfood")
def dogfood(
    audit: bool = True,
    pr_analyze_on: bool = True,
    audit_trail_on: bool = True,
    rules_file: str = "",
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
    rules_file:
        Pass-through to pr-analyze (default: auto-detect ``.roam/rules.yml``).

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
    if rules_file:
        args.extend(["--rules", rules_file])
    return _run_roam(args, root)


@_tool(name="roam_rules_validate")
def rules_validate(
    rules_path: str = ".roam/rules.yml",
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
    rules_path:
        Path to the rules YAML to validate (default: ``.roam/rules.yml``).
    against:
        Optional sample diff path; the rules will be dry-run against it
        and matching violations reported.
    strict:
        Treat warnings (missing severity, missing description, unknown
        keys) as failures.

    Returns: ``{summary: {verdict, errors_count, warnings_count, ...},
    errors: [...], warnings: [...], dry_run_violations: [...]}``.
    """
    args = ["rules-validate", rules_path]
    if against:
        args.extend(["--against", against])
    if strict:
        args.append("--strict")
    return _run_roam(args, root)


@_tool(name="roam_suggest_reviewers")
def suggest_reviewers(top: int = 3, exclude: str = "", changed: bool = True, root: str = ".") -> dict:
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


@_tool(name="roam_verify")
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


@_tool(name="roam_api_changes")
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


@_tool(
    name="roam_affected_tests",
    description="Test files that exercise changed code, with hop distance.",
)
def affected_tests(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Find test files that exercise changed code.

    Call this to know which tests to run after making changes. Walks
    reverse dependency edges from changed code to find test files. For
    a full pre-change check, prefer preflight (includes affected tests
    plus blast radius and fitness)."""
    args = ["affected-tests"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_test_gaps")
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


@_tool(name="roam_dead_code", description="Unreferenced exported symbols (dead code candidates).")
def dead_code(root: str = ".") -> dict:
    """List unreferenced exported symbols (dead code candidates).

    Call this to find code that can be safely removed. Finds symbols
    with zero incoming edges, filtering out known entry points and
    framework lifecycle hooks. Includes safety verdict per symbol."""
    return _run_roam(["dead"], root)


@_tool(name="roam_duplicates")
def duplicates_tool(threshold: float = 0.75, min_lines: int = 5, scope: str = "", root: str = ".") -> dict:
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

    Returns: duplicate clusters with similarity scores, shared patterns,
    and refactoring suggestions.
    """
    args = ["duplicates", "--threshold", str(threshold), "--min-lines", str(min_lines)]
    if scope:
        args.extend(["--scope", scope])
    return _run_roam(args, root)


@_tool(name="roam_clones")
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
def vibe_check(threshold: int = 0, root: str = ".") -> dict:
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
def ai_readiness(threshold: int = 0, root: str = ".") -> dict:
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
def visualize(
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
def relate(symbols: list[str], files: list[str] | None = None, depth: int = 3, root: str = ".") -> dict:
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
            args.extend(["--file", f])
    if depth != 3:
        args.extend(["--depth", str(depth)])
    return _run_roam(args, root)


# ===================================================================
# Tier 3 tools -- agentic memory
# ===================================================================


@_tool(
    name="roam_annotate_symbol",
    description="Add persistent annotation to a symbol/file for future agent sessions.",
)
def annotate_symbol(
    target: str,
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
    target:
        Symbol name or file path to annotate.
    content:
        The annotation text (e.g., "O(n^2) loop, see PR #42").
    tag:
        Category tag: security, performance, gotcha, review, wip.
    author:
        Who is annotating (agent name or user).
    expires:
        Optional expiry datetime (ISO 8601, e.g. "2025-12-31").

    Returns: confirmation with the resolved target and tag.
    """
    args = ["annotate", target, content]
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
    target: str = "",
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
    target:
        Symbol name or file path. If empty, returns all annotations.
    tag:
        Filter by tag (e.g., "security", "performance").
    since:
        Only annotations created after this datetime (ISO 8601).

    Returns: list of annotations with content, tag, author, and timestamps.
    """
    args = ["annotations"]
    if target:
        args.append(target)
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
        MCP resource so agents can subscribe to or poll it.
        """
        data = _run_roam(["health"])
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
def pr_diff(staged: bool = False, commit_range: str = "", root: str = ".") -> dict:
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
def effects(target: str = "", file: str = "", effect_type: str = "", root: str = ".") -> dict:
    """Show side effects of functions (DB writes, network, filesystem, etc.).

    WHEN TO USE: Call this to understand what a function actually DOES
    beyond its signature. Shows both direct effects (from the function
    body) and transitive effects (inherited from callees via the call
    graph). Useful for assessing change risk and understanding data flow.

    Parameters
    ----------
    target:
        Symbol name to inspect effects for.
    file:
        File path to show effects per function.
    effect_type:
        Filter by effect type (e.g. "writes_db", "network").

    Returns: classified effects (direct and transitive) for the symbol,
    file, or entire codebase.
    """
    args = ["effects"]
    if target:
        args.append(target)
    if file:
        args.extend(["--file", file])
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
)
def path_coverage(from_pattern: str = "", to_pattern: str = "", max_depth: int = 8, root: str = ".") -> dict:
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
)
def generate_plan(
    target: str = "",
    task: str = "refactor",
    file_path: str = "",
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
    target:
        Symbol name to plan for.
    task:
        Task type: refactor, debug, extend, review, understand.
    file_path:
        File to plan for (alternative to target).
    staged:
        Plan for staged changes.
    depth:
        Call graph depth for read order (default: 2).

    Returns: structured plan with 6 sections.
    """
    args = ["plan"]
    if target:
        args.append(target)
    if task != "refactor":
        args.extend(["--task", task])
    if file_path:
        args.extend(["--file", file_path])
    if staged:
        args.append("--staged")
    if depth != 2:
        args.extend(["--depth", str(depth)])
    return _run_roam(args, root)


@_tool(
    name="roam_adversarial_review",
    description="Adversarial architecture review: challenges about cycles, anti-patterns, coupling.",
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
    target: str = "",
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
    target:
        Symbol name or file path to analyze.
    public_api:
        Analyze all exported/public symbols.
    breaking_risk:
        Rank symbols by breaking risk (callers * file spread).
    top_n:
        Max symbols to show (default: 20).

    Returns: invariants per symbol with breaking risk scores.
    """
    args = ["invariants"]
    if target:
        args.append(target)
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
def fingerprint(compact: bool = False, export_path: str = "", compare_path: str = "", root: str = ".") -> dict:
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
            args.extend(["--files", f])
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
    name="roam_secrets",
    description="Scan for hardcoded secrets, API keys, tokens, passwords (24 patterns).",
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
    args = ["search-semantic", query, "--top", str(top), "--threshold", str(threshold)]
    return _run_roam(args, root)


# ===================================================================
# Daily workflow tools
# ===================================================================


@_tool(
    name="roam_diff",
    description="Blast radius of uncommitted/committed changes: affected symbols, files, tests.",
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
def roam_symbol(name: str, full: bool = False, root: str = ".") -> dict:
    """Symbol definition, callers, callees, and graph metrics.

    WHEN TO USE: when you need detailed info about a specific symbol --
    definition, who calls it, what it calls, PageRank, fan-in/out.
    More focused than roam_context (which adds files-to-read).

    Parameters
    ----------
    name:
        Symbol name. Supports ``file:symbol`` for disambiguation.
    full:
        Show all callers/callees without truncation.
    root:
        Working directory (project root).

    Returns: name, kind, signature, location, docstring, PageRank,
    in_degree, out_degree, callers list, callees list.
    """
    args = ["symbol", name]
    if full:
        args.append("--full")
    return _run_roam(args, root)


@_tool(name="roam_deps", description="File-level imports and importers (what depends on this file).")
def roam_deps(path: str, full: bool = False, root: str = ".") -> dict:
    """File-level import/imported-by relationships.

    Call this to understand a file's dependencies -- what it imports
    and what imports it. Use for module boundary analysis and
    refactoring impact."""
    args = ["deps", path]
    if full:
        args.append("--full")
    return _run_roam(args, root)


@_tool(
    name="roam_uses",
    description=(
        "All consumers of a symbol: callers, importers, inheritors by edge type. "
        'Use this *instead of* a multi-shape grep ("->X|\\.X\\b|\'X\'|\\"X\\"") '
        "to find references — graph-precise, no string-literal / comment "
        "false positives, and the result is already structured by edge type. "
        "For 3+ symbols call `roam_batch_get` (one round-trip) instead."
    ),
)
def roam_uses(name: str, full: bool = False, root: str = ".") -> dict:
    """All consumers of a symbol: callers, importers, inheritors.

    WHEN TO USE: this is the right tool for "find every reference to X"
    queries. Multi-shape regex grep — ``->X|\\.X\\b|'X'|"X"`` — is the
    standard way to do this with raw text tools, but it produces false
    positives in comments / docstrings / unrelated string literals,
    and the agent then has to filter those out. ``roam_uses`` resolves
    references through the indexed call/import/inherit graph: every
    result is a real symbol that depends on the target, grouped by
    edge type (calls, imports, inheritance, trait usage). Broader
    than ``roam_impact`` (which counts symbols only); use ``uses``
    for planning API changes or "what would break if I delete X".

    For verifying multiple symbols (a typical "is X really dead?"
    sweep), call ``roam_batch_get`` instead — one round-trip resolves
    up to 50 symbols with full caller/callee metadata."""
    args = ["uses", name]
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


@_tool(name="roam_debt", description="Prioritized tech debt with SQALE remediation cost estimates.")
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
    import os as _os

    if _os.environ.get("ROAM_AI_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
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

    user_prompt = _build_llm_enrich_prompt(
        [t["target"] for t in unresolved],
        list(repo_paths),
    )

    import time as _time

    started = _time.monotonic()
    try:
        result = await ctx.sample(
            user_prompt,
            system_prompt=_LLM_ENRICH_SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.1,
        )
    except Exception as exc:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        envelope = _set_llm_skip_reason(envelope, f"sampling raised: {type(exc).__name__}")
        envelope.setdefault("summary", {})["llm_latency_ms"] = elapsed_ms
        return envelope
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

    # Cardinal rule: never attach a hint pointing at a path the repo
    # doesn't actually contain. Build fast lookup sets — full paths AND
    # basenames — so suggestions like ``"intro.md"`` resolve to
    # ``docs/intro.md`` when that's the canonical location.
    valid_paths = set(repo_paths)
    basename_to_paths: dict[str, list[str]] = {}
    for p in repo_paths:
        basename_to_paths.setdefault(p.rsplit("/", 1)[-1], []).append(p)

    def _validate_candidate(c: str) -> str | None:
        """Return a repo-relative path the candidate resolves to, or None."""
        if not c or "://" in c or c.startswith("/"):
            return None
        if c in valid_paths:
            return c
        matches = basename_to_paths.get(c.rsplit("/", 1)[-1])
        if not matches:
            return None
        # Prefer the shortest matching path (usually canonical).
        return min(matches, key=len)

    added = 0
    per_target: dict[str, dict] = {}
    for tgt in unresolved:
        target_name = tgt["target"]
        candidates = suggestions.get(target_name)
        if candidates is None:
            per_target[target_name] = {
                "skip_reason": "target not present in LLM response",
                "candidates_returned": 0,
            }
            continue
        per_target_entry: dict = {
            "candidates_returned": len(candidates),
            "candidates_raw": list(candidates),
        }
        if not candidates:
            per_target_entry["skip_reason"] = "LLM returned empty candidate list"
            per_target[target_name] = per_target_entry
            continue

        accepted: list[str] = []
        rejection: list[dict] = []
        for raw_candidate in candidates:
            resolved = _validate_candidate(raw_candidate)
            if resolved is None:
                rejection.append({"candidate": raw_candidate, "reason": "not in repo"})
                continue
            accepted.append(resolved)
        per_target_entry["candidates_validated"] = accepted
        if rejection:
            per_target_entry["candidates_rejected"] = rejection

        if not accepted:
            per_target_entry["skip_reason"] = "all candidates failed validation"
            per_target[target_name] = per_target_entry
            continue

        chosen = accepted[0]
        tgt["hint"] = {
            "target": chosen,
            "confidence": "MEDIUM",
            "reason": "LLM-suggested semantic match",
            "source": "llm-sampling",
        }
        tgt["rename_hint"] = chosen
        # Surface the runners-up so callers (CI / agents / verdict UI)
        # can present alternatives without re-asking the LLM.
        if len(accepted) > 1:
            tgt["llm_alternates"] = accepted[1:]
        per_target_entry["chosen"] = chosen
        per_target[target_name] = per_target_entry
        added += 1

    summary["llm_hints_added"] = added
    summary["llm_per_target"] = per_target
    if added:
        # Re-derive ``by_confidence`` so the count reflects the new hints.
        new_by_confidence: dict[str, int] = {}
        for t in targets:
            c = (t.get("hint") or {}).get("confidence", "NONE")
            new_by_confidence[c] = new_by_confidence.get(c, 0) + 1
        summary["by_confidence"] = new_by_confidence
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


@_tool(name="roam_auth_gaps", description="Endpoints missing authentication or authorization checks.")
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


@_tool(name="roam_missing_index", description="Queries on non-indexed columns (slow query risk).")
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
    description="Backend routes with no frontend consumer (dead endpoints).",
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
    description="Non-idempotent database migrations (unsafe for re-run).",
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


@_tool(name="roam_api_drift", description="Mismatches between backend models and frontend interfaces.")
def roam_api_drift(model: str = "", confidence: str = "medium", root: str = ".") -> dict:
    """Detect mismatches between backend models and frontend interfaces.

    WHEN TO USE: to find drift between PHP $fillable/$appends and
    TypeScript interface properties. Detects missing fields, extra fields,
    type mismatches. Auto-converts snake_case/camelCase.

    Parameters
    ----------
    model:
        Only check this model. Empty = check all.
    confidence:
        Filter: "low", "medium", "high" (default medium).
    root:
        Working directory (project root).

    Returns: findings with model, interface, drift type, field,
    backend/frontend types, severity, suggested fix.
    """
    args = ["api-drift"]
    if model:
        args.extend(["--model", model])
    if confidence != "medium":
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@_tool(name="roam_simulate_departure")
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


@_tool(name="roam_ai_ratio")
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
    output_schema=_ENVELOPE_SCHEMA,
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
        '"which tools are agents actually using?" and "are 90 of '
        'the 137 tools dead weight?". Never phones home — counters '
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
    error_count = sum(v.get("error", 0) for v in invocations.values())
    rate_limited_count = sum(v.get("rate_limited", 0) for v in invocations.values())

    envelope = json_envelope(
        "session-metrics",
        summary={
            "verdict": (
                f"{distinct_tools} distinct tool(s) exercised, "
                f"{total_calls} total call(s), {error_count} error(s), "
                f"{rate_limited_count} rate-limited"
            ),
            "distinct_tools": distinct_tools,
            "total_calls": total_calls,
            "error_count": error_count,
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
    output_schema=_ENVELOPE_SCHEMA,
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
    output_schema=_ENVELOPE_SCHEMA,
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
    output_schema=_ENVELOPE_SCHEMA,
)
def roam_disambiguate(name: str, limit: int = 20, root: str = ".") -> dict:
    """List every symbol matching a name with disambiguators.

    WHEN TO USE: when search returns multiple matches and you need to
    pick the right one. Saves an agent from picking the wrong overload.

    >>> roam disambiguate handle_login
    """
    args = ["disambiguate", name, "--limit", str(limit)]
    return _run_roam(args, root)


@_tool(
    name="roam_why_fail",
    description="Triage a failing test/symbol: recently-changed symbols transitively reachable from it.",
    output_schema=_ENVELOPE_SCHEMA,
)
def roam_why_fail(target: str, days: int = 14, max_hops: int = 5, limit: int = 10, root: str = ".") -> dict:
    """Triage a failing test by surfacing recently-changed reachable symbols.

    WHEN TO USE: a test just started failing — what changed?
    Combines BFS reach with git recency to rank suspects.

    >>> roam why-fail tests/test_login.py --days 7
    """
    args = ["why-fail", target, "--days", str(days), "--max-hops", str(max_hops), "--limit", str(limit)]
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
        # now bundled inside the installed package (src/roam/mcp-server-card.json)
        # so post-PyPI ``roam mcp --card`` works without a source
        # checkout. The docs/site/.well-known/ copy stays canonical for
        # the hosted /well-known URL — they are kept in sync via the
        # release process.
        from pathlib import Path as _Path

        bundled = _Path(__file__).resolve().parent / "mcp-server-card.json"
        if bundled.is_file():
            click.echo(bundled.read_text(encoding="utf-8").rstrip())
            return

        # Source-checkout fallback so dev runs against an unbuilt tree
        # still find the docs-site copy.
        for candidate in (
            _Path(__file__).resolve().parents[1].parent.parent
            / "docs"
            / "site"
            / ".well-known"
            / "mcp-server-card.json",
            _Path(__file__).resolve().parent.parent.parent / "docs" / "site" / ".well-known" / "mcp-server-card.json",
        ):
            if candidate.is_file():
                click.echo(candidate.read_text(encoding="utf-8").rstrip())
                return
        click.echo("error: server card file not found", err=True)
        raise SystemExit(1)
        return

    if mcp is None:
        click.echo(
            "error: fastmcp is required for the MCP server.\n"
            "install it with:  pip install roam-code[mcp]\n"
            "if this looks unexpected, run `roam doctor` to diagnose your install.",
            err=True,
        )
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
            except Exception:
                pass


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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if mcp is None:
        raise SystemExit(
            "fastmcp is required for the MCP server.\n"
            "Install it with:  pip install roam-code[mcp]\n"
            "If this looks unexpected, run `roam doctor` to diagnose your install."
        )
    mcp.run()
