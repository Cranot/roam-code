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
from pathlib import Path

import click
from click.testing import CliRunner as _CliRunner

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

# ---------------------------------------------------------------------------
# Tool presets — named sets of tools exposed to agents.
# Default: "core" (16 tools + meta-tool).
# Override: ROAM_MCP_PRESET=review|refactor|debug|architecture|full
# Legacy: ROAM_MCP_LITE=0 maps to "full" preset.
# ---------------------------------------------------------------------------

_CORE_TOOLS = {
    # compound operations (4) — each replaces 2-4 individual calls
    "roam_explore", "roam_prepare_change", "roam_review_change", "roam_diagnose_issue",
    # batch operations (2) — replace 10-50 sequential calls with one
    "roam_batch_search", "roam_batch_get",
    # comprehension (5)
    "roam_understand", "roam_search_symbol", "roam_context", "roam_file_info", "roam_deps",
    # daily workflow (7)
    "roam_preflight", "roam_diff", "roam_pr_risk", "roam_affected_tests", "roam_impact", "roam_uses",
    "roam_syntax_check",
    # code quality (5)
    "roam_health", "roam_dead_code", "roam_complexity_report", "roam_diagnose", "roam_trace",
}

_PRESETS: dict[str, set[str]] = {
    "core": _CORE_TOOLS.copy(),
    "review": _CORE_TOOLS | {
        "roam_breaking_changes", "roam_pr_diff", "roam_effects",
        "roam_adversarial_review", "roam_budget_check", "roam_attest",
        "roam_rules_check", "roam_weather", "roam_debt", "roam_symbol",
        "roam_algo", "roam_secrets", "roam_docs_coverage",
    },
    "refactor": _CORE_TOOLS | {
        "roam_simulate", "roam_closure", "roam_mutate", "roam_generate_plan",
        "roam_suggest_refactoring", "roam_plan_refactor",
        "roam_get_invariants", "roam_cut_analysis", "roam_fingerprint",
        "roam_relate", "roam_symbol", "roam_visualize",
    },
    "debug": _CORE_TOOLS | {
        "roam_effects", "roam_path_coverage", "roam_bisect_blame",
        "roam_forecast", "roam_vuln_map", "roam_vuln_reach",
        "roam_ingest_trace", "roam_runtime_hotspots", "roam_relate",
        "roam_symbol", "roam_algo",
    },
    "architecture": _CORE_TOOLS | {
        "roam_visualize", "roam_tour", "roam_dark_matter", "roam_repo_map",
        "roam_simulate", "roam_fingerprint", "roam_orchestrate",
        "roam_capsule_export", "roam_cut_analysis", "roam_forecast",
        "roam_algo", "roam_symbol", "roam_relate", "roam_agent_export",
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
            "Pre-indexes symbols, call graphs, dependencies, architecture, "
            "and git history into a local SQLite DB. "
            "One tool call replaces 5-10 Glob/Grep/Read calls. "
            "Most tools are read-only; side-effect tools are explicitly marked."
        ),
    )
else:
    mcp = None


_REGISTERED_TOOLS: list[str] = []

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
_TASK_REQUIRED_TOOLS = {
    "roam_init",
    "roam_reindex",
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
    "roam_simulate",
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
        "instruction_precedence": [".github/copilot-instructions.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md"],
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
        "instruction_precedence": [".cursor/rules/roam.mdc", ".cursorrules", "AGENTS.md", "CLAUDE.md"],
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
    payload.update({
        "server": "roam-code",
        "compat_version": "2026-02-22",
        "detected_instruction_files": existing,
        "profile": profile,
    })
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

_SCHEMA_EXPLORE = _make_schema(
    {"sections": {"type": "array", "items": {"type": "string"}}},
    understand={"type": "object", "description": "Full codebase briefing"},
    context={"type": "object", "description": "Symbol context (when symbol provided)"},
)

_SCHEMA_PREPARE_CHANGE = _make_schema(
    {"sections": {"type": "array"}, "target": {"type": "string"}},
    preflight={"type": "object", "description": "Safety check: blast radius, tests, fitness"},
    context={"type": "object", "description": "Files and line ranges to read"},
    effects={"type": "object", "description": "Side effects of the target symbol"},
)

_SCHEMA_REVIEW_CHANGE = _make_schema(
    {"sections": {"type": "array"}},
    pr_risk={"type": "object", "description": "Risk score and per-file breakdown"},
    breaking_changes={"type": "object", "description": "Removed/changed API signatures"},
    pr_diff={"type": "object", "description": "Structural graph delta"},
)

_SCHEMA_DIAGNOSE_ISSUE = _make_schema(
    {"sections": {"type": "array"}, "target": {"type": "string"}},
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
    {"health_score": {"type": "number"}, "total_files": {"type": "integer"},
     "total_symbols": {"type": "integer"}},
    issues={"type": "array"},
    bottlenecks={"type": "array"},
)

_SCHEMA_SEARCH = _make_schema(
    {"total_matches": {"type": "integer"}, "query": {"type": "string"}},
    results={
        "type": "array",
        "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "kind": {"type": "string"},
            "file_path": {"type": "string"}, "line_start": {"type": "integer"},
        }},
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
    {"source": {"type": "string"}, "target": {"type": "string"},
     "hop_count": {"type": "integer"}},
    path={"type": "array"},
)

_SCHEMA_BATCH_SEARCH = _make_schema(
    {"queries_executed": {"type": "integer"}, "total_matches": {"type": "integer"}},
    results={
        "type": "object",
        "description": "Map of query -> list of matching symbols",
        "additionalProperties": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "kind": {"type": "string"},
                "file_path": {"type": "string"}, "line_start": {"type": "integer"},
                "pagerank": {"type": "number"},
            }},
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


def _tool(name: str, description: str = "", output_schema: dict | None = None):
    """Register an MCP tool if it belongs to the active preset.

    Automatically sets ``deferLoading`` in MCP tool annotations:
    - Core tools and the meta-tool: ``deferLoading`` is absent (always loaded)
    - All other tools: ``deferLoading=True`` (loaded on-demand via Tool Search)

    This enables Claude Code's Tool Search feature to achieve ~85% context
    reduction by only loading non-core tool descriptions when needed.
    """
    def decorator(fn):
        if mcp is None:
            return fn
        # Meta-tool is always registered; others filtered by preset
        if name != _META_TOOL:
            if _ACTIVE_TOOLS and name not in _ACTIVE_TOOLS:
                return fn
        _REGISTERED_TOOLS.append(name)
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
    ("no .roam",            "INDEX_NOT_FOUND",  "run `roam init` to create the codebase index."),
    ("not found in index",  "INDEX_NOT_FOUND",  "run `roam init` to create the codebase index."),
    ("index is stale",      "INDEX_STALE",      "run `roam index` to refresh."),
    ("out of date",         "INDEX_STALE",      "run `roam index` to refresh."),
    ("not a git repository","NOT_GIT_REPO",     "some commands require git history. run: git init."),
    ("database is locked",  "DB_LOCKED",        "another roam process is running. wait or delete .roam/index.lock."),
    ("permission denied",   "PERMISSION_DENIED","check file permissions."),
    ("cannot open index",   "INDEX_NOT_FOUND",  "run `roam init` to create the codebase index."),
    ("symbol not found",    "NO_RESULTS",       "try a different search term or check spelling."),
    ("no matches",          "NO_RESULTS",       "try a different search term or check spelling."),
    ("no results",          "NO_RESULTS",       "try a different search term or check spelling."),
]


_RETRYABLE_CODES = {"DB_LOCKED", "INDEX_STALE"}


def _classify_error(stderr: str, exit_code: int) -> tuple[str, str, bool]:
    """Classify error and return (error_code, hint, retryable).

    Checks standardized exit codes first (more reliable than text matching),
    then falls back to text pattern matching for legacy/subprocess output.
    The *retryable* flag indicates whether the agent should retry the call
    (True for DB_LOCKED, INDEX_STALE; False for everything else).
    """
    from roam.exit_codes import (
        EXIT_USAGE, EXIT_INDEX_MISSING, EXIT_INDEX_STALE,
        EXIT_GATE_FAILURE, EXIT_PARTIAL,
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


def _structured_error(error_dict: dict) -> dict:
    """Wrap error dict with MCP-compliant structured error fields (#116, #117)."""
    error_dict["isError"] = True
    code = error_dict.get("error_code", "UNKNOWN")
    error_dict["retryable"] = code in _RETRYABLE_CODES
    error_dict["suggested_action"] = error_dict.get("hint", "check the error message")
    return error_dict


def _ensure_fresh_index(root: str = ".") -> dict | None:
    """Run incremental index to ensure freshness. Returns None on success."""
    result = _run_roam(["index"], root)
    if "error" in result:
        return {"error": f"index update failed: {result['error']}"}
    return None


def _run_roam(args: list[str], root: str = ".") -> dict:
    """Run a roam CLI command with ``--json`` and return parsed output.

    Uses in-process Click invocation (fast, no subprocess overhead) when
    *root* is ``"."``.  Falls back to subprocess for non-local roots.
    """
    if root != ".":
        return _run_roam_subprocess(args, root)
    return _run_roam_inprocess(args)


def _run_roam_inprocess(args: list[str]) -> dict:
    """Run a roam CLI command in-process via Click CliRunner (no subprocess)."""
    from roam.cli import cli as _cli

    runner = _CliRunner()
    cmd_args = ["--json"] + args
    try:
        result = runner.invoke(_cli, cmd_args, catch_exceptions=True)
    except Exception as exc:
        return _structured_error({
            "error": str(exc),
            "error_code": "UNKNOWN",
            "hint": "an unexpected error occurred.",
        })

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
            return _structured_error({
                "error": f"Failed to parse JSON output: {exc}",
                "error_code": "COMMAND_FAILED",
                "hint": "command produced invalid JSON output.",
            })

    # Error path — classify and return structured error
    error_text = output
    if result.exception:
        error_text = error_text or str(result.exception)

    error_code, hint, _retryable = _classify_error(error_text, result.exit_code)
    return _structured_error({
        "error": error_text or "command failed",
        "error_code": error_code,
        "hint": hint,
        "exit_code": result.exit_code,
        "command": "roam --json " + " ".join(args),
    })


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
        return _structured_error({
            "error": stderr or "command failed",
            "error_code": error_code,
            "hint": hint,
            "exit_code": result.returncode,
            "command": " ".join(cmd),
        })
    except subprocess.TimeoutExpired:
        return _structured_error({
            "error": "Command timed out after 60s",
            "error_code": "COMMAND_FAILED",
            "hint": "the command took too long. try a smaller scope or check system load.",
        })
    except json.JSONDecodeError as exc:
        return _structured_error({
            "error": f"Failed to parse JSON output: {exc}",
            "error_code": "COMMAND_FAILED",
            "hint": "command produced invalid JSON output.",
        })
    except Exception as exc:
        return _structured_error({
            "error": str(exc),
            "error_code": "UNKNOWN",
            "hint": "an unexpected error occurred.",
        })


async def _run_roam_async(args: list[str], root: str = ".") -> dict:
    """Run a roam CLI command in a worker thread from async tool handlers."""
    return await asyncio.to_thread(_run_roam, args, root)


async def _ctx_report_progress(
    ctx: _Context | None, progress: float, total: float | None = None, message: str | None = None,
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


def _compound_envelope(
    command: str,
    sub_results: list[tuple[str, dict]],
    **meta,
) -> dict:
    """Build a compound operation response from multiple sub-command results."""
    errors: list[dict] = []
    sections: dict = {}

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

    result: dict = {
        "command": command,
        "summary": {
            "verdict": " | ".join(verdicts) if verdicts else "compound operation completed",
            "sections": list(sections.keys()),
            "errors": len(errors),
            **meta,
        },
    }
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


def _append_context_personalization_args(args: list[str], session_hint: str = "",
                                         recent_symbols: str = "") -> list[str]:
    """Append optional context personalization flags to a roam CLI arg list."""
    if session_hint:
        args.extend(["--session-hint", session_hint])
    if recent_symbols:
        for raw in str(recent_symbols).split(","):
            sym = raw.strip()
            if sym:
                args.extend(["--recent-symbol", sym])
    return args


@_tool(name="roam_explore",
       description="Codebase exploration bundle: understand overview + optional symbol deep-dive in one call.",
       output_schema=_SCHEMA_EXPLORE)
def explore(symbol: str = "", budget: int = 0, session_hint: str = "",
            recent_symbols: str = "", root: str = ".") -> dict:
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
    recent_symbols:
        Comma-separated recently discussed symbols for rank biasing.

    Returns: codebase overview (tech stack, architecture, health) and
    optionally focused context for the given symbol.
    """
    budget_args = ["--budget", str(budget)] if budget else []
    overview = _run_roam(budget_args + ["understand"], root)

    if not symbol:
        result = _compound_envelope("explore", [("understand", overview)])
        return _apply_budget(result, budget)

    ctx_args = budget_args + ["context", symbol, "--task", "understand"]
    _append_context_personalization_args(
        ctx_args,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
    )
    ctx = _run_roam(ctx_args, root)
    result = _compound_envelope("explore", [
        ("understand", overview),
        ("context", ctx),
    ], target=symbol)
    return _apply_budget(result, budget)


@_tool(name="roam_prepare_change",
       description="Pre-change bundle: preflight + context + effects in one call. Call BEFORE modifying code.",
       output_schema=_SCHEMA_PREPARE_CHANGE)
def prepare_change(target: str, staged: bool = False, budget: int = 0,
                   session_hint: str = "", recent_symbols: str = "",
                   root: str = ".") -> dict:
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

    result = _compound_envelope("prepare-change", [
        ("preflight", preflight_data),
        ("context", ctx_data),
        ("effects", effects_data),
    ], target=target)
    return _apply_budget(result, budget)


@_tool(name="roam_review_change",
       description="Change review bundle: pr-risk + breaking changes + structural diff in one call.",
       output_schema=_SCHEMA_REVIEW_CHANGE)
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

    result = _compound_envelope("review-change", [
        ("pr_risk", risk_data),
        ("breaking_changes", breaking_data),
        ("pr_diff", diff_data),
    ])
    return _apply_budget(result, budget)


@_tool(name="roam_diagnose_issue",
       description="Debug bundle: root cause suspects + side effects in one call.",
       output_schema=_SCHEMA_DIAGNOSE_ISSUE)
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

    result = _compound_envelope("diagnose-issue", [
        ("diagnose", diag_data),
        ("effects", effects_data),
    ], target=symbol)
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
    from roam.db.queries import CALLERS_OF, CALLEES_OF, METRICS_FOR_SYMBOL
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


@_tool(name="roam_batch_search",
       description="Search up to 10 patterns in one call. Replaces 10 sequential roam_search_symbol calls.",
       output_schema=_SCHEMA_BATCH_SEARCH)
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
    from roam.output.formatter import json_envelope, to_json

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
    verdict = (
        f"{total_matches} matches across {len(results)} queries"
        if results
        else "no matches found"
    )
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


@_tool(name="roam_batch_get",
       description="Get details for up to 50 symbols in one call. Replaces 50 sequential roam_symbol calls.",
       output_schema=_SCHEMA_BATCH_GET)
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


@_tool(name="roam_expand_toolset",
       description="List available tool presets or show contents of a preset. "
                   "Presets: core (16), review (27), refactor (26), debug (27), architecture (29), full (all).")
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
                f"To switch to '{preset}' preset, restart the MCP server with: "
                f"ROAM_MCP_PRESET={preset} roam mcp"
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


@_tool(name="roam_init",
       description="Initialize roam and build the first index. Task-mode for non-blocking setup.",
       output_schema=_SCHEMA_INIT)
async def roam_init(root: str = ".", yes: bool = True, ctx: _Context | None = None) -> dict:
    """Initialize roam for a repo and create the first index.

    WHEN TO USE: first run in a repository without a `.roam/index.db`.
    This is task-enabled for non-blocking setup in MCP clients.
    """
    args = ["init"]
    if yes:
        args.append("--yes")
    if root != ".":
        args.extend(["--root", root])

    await _ctx_info(ctx, "Starting roam initialization.")
    await _ctx_report_progress(ctx, 5, total=100, message="initializing")
    result = await _run_roam_async(args, root)
    await _ctx_report_progress(ctx, 100, total=100, message="completed")
    return result


@_tool(name="roam_reindex",
       description="Incremental or force reindex. Task-mode + elicited confirmation for force runs.",
       output_schema=_SCHEMA_REINDEX)
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
            return _structured_error({
                "error": "force reindex requires confirmation but elicitation is unavailable.",
                "error_code": "ELICITATION_REQUIRED",
                "hint": "rerun with confirm_force=true or use a client with elicitation support.",
                "command": "roam_reindex",
            })
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
    await _ctx_report_progress(ctx, 5, total=100, message="indexing")
    result = await _run_roam_async(args, root)
    await _ctx_report_progress(ctx, 100, total=100, message="completed")
    if force and "error" not in result:
        result["force"] = True
    return result


@_tool(name="roam_understand",
       description="Full codebase briefing: stack, architecture, health, hotspots. Call FIRST in a new repo.",
       output_schema=_SCHEMA_UNDERSTAND)
def understand(root: str = ".") -> dict:
    """Get a full codebase briefing in a single call.

    WHEN TO USE: Call this FIRST when you start working with a new or
    unfamiliar repository. Do NOT use Glob/Grep/Read to explore the
    codebase manually -- this tool gives you everything in one shot.

    Returns: tech stack, architecture overview (layers, clusters, entry
    points, key abstractions), health score, hotspots, naming conventions,
    design patterns, and a suggested file reading order.

    Output is ~2,000-4,000 tokens of structured JSON. After calling this,
    use `search_symbol` or `context` to drill into specific areas.
    """
    return _run_roam(["understand"], root)


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


@_tool(name="roam_health",
       description="Codebase health score (0-100) with issue breakdown, cycles, bottlenecks.",
       output_schema=_SCHEMA_HEALTH)
def health(root: str = ".") -> dict:
    """Get the codebase health score (0-100) with issue breakdown.

    WHEN TO USE: Call this to assess overall code quality before deciding
    where to focus refactoring effort, or to check whether recent changes
    degraded health. Do NOT call this if you already called `understand`
    (which includes health data) or `preflight` (which includes it per-symbol).

    Returns: composite health score, cycle count, god-component count,
    bottleneck symbols, dead-export count, layer violations, per-file
    health scores, and tangle ratio.
    """
    return _run_roam(["health"], root)


@_tool(name="roam_preflight",
       description="Pre-change safety check: blast radius, tests, complexity, fitness. Call BEFORE modifying code.",
       output_schema=_SCHEMA_PREFLIGHT)
def preflight(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Pre-change safety check. Call this BEFORE modifying any symbol or file.

    WHEN TO USE: Always call this before making code changes. It replaces
    5-6 separate tool calls by combining blast radius, affected tests,
    complexity, coupling, convention checks, and fitness violations into
    one response. Do NOT call `context`, `impact`, `affected_tests`, or
    `complexity_report` separately if preflight covers your need.

    Parameters
    ----------
    target:
        Symbol name or file path to check. If empty, checks all
        currently changed (unstaged) files.
    staged:
        If True, check staged (git add-ed) changes instead.

    Returns: risk level, blast radius (affected symbols and files),
    test files to run, complexity metrics, coupling data, and any
    fitness rule violations.
    """
    args = ["preflight"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_search_symbol",
       description="Find symbols by name substring. Returns kind, file, line, PageRank importance.",
       output_schema=_SCHEMA_SEARCH)
def search_symbol(query: str, root: str = ".") -> dict:
    """Find symbols by name (case-insensitive substring match).

    WHEN TO USE: Call this when you know part of a symbol name and need
    the exact qualified name, file location, or kind. Use this before
    calling `context` or `impact` to get the correct symbol identifier.
    Do NOT use Grep to search for function definitions -- this is faster
    and returns structured data with PageRank importance.

    Parameters
    ----------
    query:
        Name substring to search for (e.g., "auth", "User", "handle_request").

    Returns: matching symbols with kind (function/class/method), file path,
    line number, signature, export status, and PageRank importance score.
    """
    return _run_roam(["search", query], root)


@_tool(name="roam_context",
       description="Minimal files + line ranges needed to work with a symbol.",
       output_schema=_SCHEMA_CONTEXT)
def context(symbol: str, task: str = "", session_hint: str = "",
            recent_symbols: str = "", root: str = ".") -> dict:
    """Get the minimal context needed to work with a specific symbol.

    WHEN TO USE: Call this when you need to understand or modify a
    specific function, class, or method. Returns the exact files and
    line ranges to read -- much more targeted than `understand`.
    For pre-change safety checks, prefer `preflight` instead (it
    includes context data plus blast radius and tests).

    Parameters
    ----------
    symbol:
        Qualified or short name of the symbol to inspect.
    task:
        Optional hint: "refactor", "debug", "extend", "review", or
        "understand". Tailors output (e.g., adds complexity details
        for refactor, test coverage for debug).
    session_hint:
        Optional conversation hint used to personalize files-to-read rank.
    recent_symbols:
        Comma-separated recently discussed symbols for rank biasing.

    Returns: symbol definition, direct callers and callees, file location
    with line ranges, related tests, graph metrics (PageRank, fan-in/out,
    betweenness), and complexity metrics.
    """
    args = ["context", symbol]
    if task:
        args.extend(["--task", task])
    _append_context_personalization_args(
        args,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
    )
    return _run_roam(args, root)


@_tool(name="roam_trace",
       description="Shortest dependency path between two symbols with hop details.",
       output_schema=_SCHEMA_TRACE)
def trace(source: str, target: str, root: str = ".") -> dict:
    """Find the shortest dependency path between two symbols.

    WHEN TO USE: Call this when you need to understand HOW a change in
    one symbol could affect another. Shows each hop along the path with
    symbol names, edge types, and locations.

    Parameters
    ----------
    source:
        Starting symbol name.
    target:
        Destination symbol name.

    Returns: path hops (symbol name, kind, location, edge type), total
    hop count, coupling classification (strong/moderate/weak), and any
    hub nodes encountered.
    """
    return _run_roam(["trace", source, target], root)


@_tool(name="roam_impact",
       description="Blast radius: all symbols and files affected by changing a symbol.",
       output_schema=_SCHEMA_IMPACT)
def impact(symbol: str, root: str = ".") -> dict:
    """Show the blast radius of changing a symbol.

    WHEN TO USE: Call this when you need to know everything that would
    break if a symbol's signature or behavior changed. For pre-change
    checks, prefer `preflight` which includes impact data plus tests
    and fitness checks.

    Parameters
    ----------
    symbol:
        Symbol to analyze.

    Returns: affected symbols grouped by hop distance, affected files,
    total affected count, and severity assessment.
    """
    return _run_roam(["impact", symbol], root)


@_tool(name="roam_file_info",
       description="File skeleton: all symbols with signatures, kinds, line ranges.")
def file_info(path: str, root: str = ".") -> dict:
    """Show a file skeleton: every symbol definition with its signature.

    WHEN TO USE: Call this when you need to understand what a file
    contains without reading the full source. Returns a structured
    outline that is more useful than Read for getting an overview.

    Parameters
    ----------
    path:
        File path relative to the project root.

    Returns: all symbols in the file (functions, classes, methods) with
    kind, line range, signature, export status, and parent relationships.
    Also includes per-kind counts and the file's detected language.
    """
    return _run_roam(["file", path], root)


# ===================================================================
# Tier 2 tools -- change-risk and deeper analysis
# ===================================================================


@_tool(name="roam_pr_risk",
       description="Risk score (0-100) for pending changes with per-file breakdown.",
       output_schema=_SCHEMA_PR_RISK)
def pr_risk(staged: bool = False, root: str = ".") -> dict:
    """Compute a risk score (0-100) for pending changes.

    WHEN TO USE: Call this before committing or creating a PR to assess
    risk. Analyzes the current diff and produces a risk rating (LOW /
    MODERATE / HIGH / CRITICAL) with specific risk factors.

    Parameters
    ----------
    staged:
        If True, analyze staged changes instead of working-tree diff.

    Returns: risk score, risk level, per-file breakdown (symbols changed,
    blast radius, churn), suggested reviewers, coupling surprises, and
    any new dead exports created.
    """
    args = ["pr-risk"]
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_suggest_reviewers")
def suggest_reviewers(top: int = 3, exclude: str = "",
                      changed: bool = True, root: str = ".") -> dict:
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


@_tool(name="roam_breaking_changes",
       description="Detect breaking API changes between git refs: removed exports, changed signatures.")
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


@_tool(name="roam_affected_tests",
       description="Test files that exercise changed code, with hop distance.")
def affected_tests(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Find test files that exercise the changed code.

    WHEN TO USE: Call this to know which tests to run after making
    changes. Walks reverse dependency edges from changed code to find
    test files. For a full pre-change check, prefer `preflight` which
    includes affected tests plus blast radius and fitness checks.

    Parameters
    ----------
    target:
        Symbol name or file path. If empty, uses all currently changed files.
    staged:
        If True, start from staged changes.

    Returns: test files with the symbols that link them to the change
    and the hop distance.
    """
    args = ["affected-tests"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_test_gaps")
def test_gaps(changed: bool = True, severity: str = "medium",
              files: str = "", root: str = ".") -> dict:
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


@_tool(name="roam_algo",
       description="Detect suboptimal algorithms with better alternatives and complexity analysis.")
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


@_tool(name="roam_dark_matter",
       description="File pairs that co-change without structural links (hidden coupling).")
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
    args = ["dark-matter", "--explain",
            "--min-npmi", str(min_npmi),
            "--min-cochanges", str(min_cochanges)]
    return _run_roam(args, root)


@_tool(name="roam_dead_code",
       description="Unreferenced exported symbols (dead code candidates).")
def dead_code(root: str = ".") -> dict:
    """List unreferenced exported symbols (dead code candidates).

    WHEN TO USE: Call this to find code that can be safely removed.
    Finds exported symbols with zero incoming edges, filtering out
    known entry points and framework lifecycle hooks.

    Returns: each dead symbol with kind, location, file, and a safety
    verdict indicating confidence level.
    """
    return _run_roam(["dead"], root)


@_tool(name="roam_duplicates")
def duplicates_tool(threshold: float = 0.75, min_lines: int = 5,
                    scope: str = "", root: str = ".") -> dict:
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
    args = ["duplicates", "--threshold", str(threshold),
            "--min-lines", str(min_lines)]
    if scope:
        args.extend(["--scope", scope])
    return _run_roam(args, root)


@_tool(name="roam_vibe_check",
       description="AI rot score (0-100): 8-pattern taxonomy of AI code anti-patterns.")
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


@_tool(name="roam_supply_chain",
       description="Dependency risk dashboard: pin coverage, risk scoring, supply-chain health.")
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


@_tool(name="roam_dashboard",
       description="Unified single-screen codebase status: health, hotspots, bus factor, dead code, AI rot.")
def dashboard_tool(root: str = ".") -> dict:
    """One-call codebase status combining health, hotspots, risks, and AI rot.

    WHEN TO USE: Call this for a quick unified overview instead of running
    health, hotspot, bus-factor, dead, and vibe-check separately.
    Returns health score, top hotspots, risk areas, and approximate AI rot.
    """
    return _run_roam(["dashboard"], root)


@_tool(name="roam_ai_readiness",
       description="AI readiness score (0-100): how effectively AI agents can work on this codebase.")
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




@_tool(name="roam_check_rules",
       description="Run 10 built-in structural rules: cycles, fan-out, complexity, tests, god classes, layer violations.")
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

@_tool(name="roam_complexity_report",
       description="Functions ranked by cognitive complexity above threshold.")
def complexity_report(threshold: int = 15, root: str = ".") -> dict:
    """Rank functions by cognitive complexity.

    WHEN TO USE: Call this to find the most complex functions that
    should be refactored. Only symbols at or above the threshold are
    included. For checking a single symbol, prefer `context` or
    `preflight` which include complexity data.

    Parameters
    ----------
    threshold:
        Minimum cognitive-complexity score to include (default 15).

    Returns: symbols ranked by complexity with score, nesting depth,
    parameter count, line count, severity label, and file location.
    """
    return _run_roam(["complexity", "--threshold", str(threshold)], root)


@_tool(name="roam_repo_map",
       description="Compact project skeleton with key symbols per file, by PageRank.")
def repo_map(budget: int = 0, root: str = ".") -> dict:
    """Show a compact project skeleton with key symbols.

    WHEN TO USE: Call this for a spatial overview of the repository
    structure -- files grouped by directory, annotated with their most
    important symbols (by PageRank). Lighter than `understand`, useful
    when you just need the file layout.

    Parameters
    ----------
    budget:
        Approximate token budget for the output. 0 means no limit.

    Returns: files grouped by directory with top symbols per file,
    annotated with kind and importance.
    """
    args = ["map"]
    if budget > 0:
        args.extend(["--budget", str(budget)])
    return _run_roam(args, root)


@_tool(name="roam_tour",
       description="Codebase onboarding guide: reading order, entry points, architecture roles.")
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


@_tool(name="roam_agent_export",
       description="Generate AI agent context file (CLAUDE.md/AGENTS.md/.cursorrules) from index.")
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


@_tool(name="roam_visualize",
       description="Generate Mermaid/DOT architecture diagram with smart filtering.")
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
    args = ["visualize", "--format", format, "--depth", str(depth),
            "--limit", str(limit), "--direction", direction]
    if focus:
        args.extend(["--focus", focus])
    if no_clusters:
        args.append("--no-clusters")
    if file_level:
        args.append("--file-level")
    return _run_roam(args, root)


@_tool(name="roam_diagnose",
       description="Root cause analysis: upstream/downstream suspects ranked by composite risk.",
       output_schema=_SCHEMA_DIAGNOSE)
def diagnose(symbol: str, depth: int = 2, root: str = ".") -> dict:
    """Root cause analysis for a failing symbol.

    WHEN TO USE: Call this when debugging a bug or test failure and you
    need to find the likely root cause. Ranks upstream callers and
    downstream callees by a composite risk score combining git churn,
    cognitive complexity, file health, and co-change entropy. Much
    faster than manually tracing call chains.

    Parameters
    ----------
    symbol:
        The symbol suspected of being involved in the bug.
    depth:
        How many hops upstream/downstream to analyze (default 2).

    Returns: target symbol metrics, upstream suspects ranked by risk,
    downstream suspects ranked by risk, co-change partners, recent
    git commits, and a verdict naming the top suspect.
    """
    args = ["diagnose", symbol, "--depth", str(depth)]
    return _run_roam(args, root)


@_tool(name="roam_relate",
       description="How symbols connect: shared deps, call chains, conflicts, cohesion score.")
def relate(symbols: list[str], files: list[str] | None = None,
           depth: int = 3, root: str = ".") -> dict:
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


@_tool(name="roam_annotate_symbol",
       description="Add persistent annotation to a symbol/file for future agent sessions.")
def annotate_symbol(
    target: str, content: str,
    tag: str = "", author: str = "", expires: str = "",
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


@_tool(name="roam_get_annotations",
       description="Read annotations for symbols, files, or project. Filter by tag/date.")
def get_annotations(
    target: str = "", tag: str = "", since: str = "",
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


@_tool(name="roam_endpoints",
       description="List all REST/GraphQL/gRPC endpoints with handlers, methods, and locations.")
def endpoints_tool(framework: str = "", method: str = "", group_by: str = "framework",
                   root: str = ".") -> dict:
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
            return json.dumps({
                "languages": data.get("languages", {}),
                "files": data.get("files", {}),
                "frameworks": data.get("frameworks", []),
            }, indent=2)
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


@_tool(name="roam_ws_understand",
       description="Multi-repo workspace overview: per-repo stats, cross-repo connections.")
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


@_tool(name="roam_ws_context",
       description="Cross-repo augmented context for a symbol spanning multiple repos.")
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


@_tool(name="roam_pr_diff",
       description="Structural graph delta of code changes: metric deltas, layer violations.")
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


@_tool(name="roam_effects",
       description="Side effects of functions: DB writes, network, filesystem (direct + transitive).")
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


@_tool(name="roam_budget_check",
       description="Check changes against architectural budgets (cycles, health floor, complexity).")
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


@_tool(name="roam_attest",
       description="Proof-carrying PR attestation: evidence bundle + merge verdict.")
def attest(commit_range: str = "", staged: bool = False, output_format: str = "json",
           sign: bool = False, root: str = ".") -> dict:
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


@_tool(name="roam_capsule_export",
       description="Sanitized structural graph export without code bodies (privacy-safe).")
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


@_tool(name="roam_path_coverage",
       description="Critical call paths with zero test protection, ranked by risk.")
def path_coverage(from_pattern: str = "", to_pattern: str = "",
                  max_depth: int = 8, root: str = ".") -> dict:
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


@_tool(name="roam_forecast",
       description="Predict when metrics will exceed thresholds (Theil-Sen regression).")
def forecast(symbol: str = "", horizon: int = 30,
             alert_only: bool = False, root: str = ".") -> dict:
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


@_tool(name="roam_generate_plan",
       description="Structured execution plan for code modification: read order, invariants, tests.")
def generate_plan(target: str = "", task: str = "refactor",
                  file_path: str = "", staged: bool = False,
                  depth: int = 2, root: str = ".") -> dict:
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


@_tool(name="roam_adversarial_review",
       description="Adversarial architecture review: challenges about cycles, anti-patterns, coupling.")
def adversarial_review(staged: bool = False, commit_range: str = "",
                       severity: str = "low", root: str = ".") -> dict:
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


@_tool(name="roam_cut_analysis",
       description="Minimum cut analysis: fragile domain boundaries, highest-impact leak edges.")
def cut_analysis(between_a: str = "", between_b: str = "",
                 leak_edges: bool = False, top_n: int = 10,
                 root: str = ".") -> dict:
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


@_tool(name="roam_get_invariants",
       description="Implicit contracts for symbols: signature stability, usage spread, breaking risk.")
def get_invariants(target: str = "", public_api: bool = False,
                   breaking_risk: bool = False, top_n: int = 20,
                   root: str = ".") -> dict:
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


@_tool(name="roam_bisect_blame",
       description="Find snapshots that caused architectural degradation, ranked by impact.")
def bisect_blame(metric: str = "health_score", threshold: float = 0,
                 direction: str = "degraded", top_n: int = 10,
                 root: str = ".") -> dict:
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


@_tool(name="roam_simulate",
       description="Predict metric deltas from move/extract/merge/delete operations.")
def simulate(operation: str, symbol: str = "", target_file: str = "",
             file_a: str = "", file_b: str = "", root: str = ".") -> dict:
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
    return _run_roam(args, root)


@_tool(name="roam_closure",
       description="Minimal set of changes needed for rename/delete/modify (exact files + lines).")
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


@_tool(name="roam_doc_intent",
       description="Link documentation to code: find drift, dead refs, undocumented symbols.")
def doc_intent(symbol: str = "", doc: str = "",
               drift: bool = False, undocumented: bool = False,
               top_n: int = 20, root: str = ".") -> dict:
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


@_tool(name="roam_fingerprint",
       description="Topology fingerprint for cross-repo comparison or structural drift tracking.")
def fingerprint(compact: bool = False, export_path: str = "",
                compare_path: str = "", root: str = ".") -> dict:
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


@_tool(name="roam_rules_check",
       description="Evaluate custom governance rules from .roam/rules/ YAML files.")
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


@_tool(name="roam_orchestrate",
       description="Partition codebase for parallel multi-agent work with exclusive write zones.")
def orchestrate(n_agents: int, files: list[str] | None = None,
                staged: bool = False, root: str = ".") -> dict:
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
    return _run_roam(args, root)


@_tool(name="roam_mutate",
       description="Agentic editing: move/rename/add-call/extract symbols with auto-import rewrite.")
def mutate(operation: str, symbol: str = "", target_file: str = "",
           new_name: str = "", from_symbol: str = "", to_symbol: str = "",
           args: str = "", lines: str = "", apply: bool = False,
           root: str = ".") -> dict:
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
    return _run_roam(cmd_args, root)


@_tool(name="roam_vuln_map",
       description="Ingest vulnerability scanner reports (npm/pip/trivy/osv), match to symbols.")
def vuln_map(npm_audit: str = "", pip_audit: str = "", trivy: str = "",
             osv: str = "", generic: str = "", root: str = ".") -> dict:
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


@_tool(name="roam_vuln_reach",
       description="Vulnerability reachability through call graph: paths, hops, blast radius.")
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


@_tool(name="roam_secrets",
       description="Scan for hardcoded secrets, API keys, tokens, passwords (24 patterns).")
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


@_tool(name="roam_ingest_trace",
       description="Ingest runtime traces (OTel/Jaeger/Zipkin), match spans to symbols.")
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


@_tool(name="roam_runtime_hotspots",
       description="Runtime hotspots where static and runtime rankings disagree (UPGRADE/DOWNGRADE).")
def runtime_hotspots(runtime_sort: bool = False, discrepancy: bool = False,
                     root: str = ".") -> dict:
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


@_tool(name="roam_search_semantic",
       description="Find symbols by natural language query (hybrid BM25 + vector + framework packs).")
def search_semantic(query: str, top: int = 10, threshold: float = 0.05,
                    root: str = ".") -> dict:
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
    args = ["search-semantic", query, "--top", str(top),
            "--threshold", str(threshold)]
    return _run_roam(args, root)


# ===================================================================
# Daily workflow tools
# ===================================================================


@_tool(name="roam_diff",
       description="Blast radius of uncommitted/committed changes: affected symbols, files, tests.",
       output_schema=_SCHEMA_DIFF)
def roam_diff(commit_range: str = "", staged: bool = False, root: str = ".") -> dict:
    """Blast radius of uncommitted or committed changes.

    WHEN TO USE: call after making code changes to see what's affected
    BEFORE committing. Shows affected symbols, files, tests, coupling
    warnings, and fitness violations.

    WHEN NOT TO USE: for pre-PR analysis use roam_pr_risk instead.

    Parameters
    ----------
    commit_range:
        Git range like ``HEAD~3..HEAD`` or ``main..feature``.
        Empty = uncommitted working tree changes.
    staged:
        If True, analyze git-staged changes only.
    root:
        Working directory (project root).

    Returns: changed files, affected symbols, blast radius metrics,
    per-file breakdown.
    """
    args = ["diff"]
    if commit_range:
        args.append(commit_range)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@_tool(name="roam_symbol",
       description="Symbol definition, callers, callees, PageRank, fan-in/out metrics.")
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


@_tool(name="roam_deps",
       description="File-level imports and importers (what depends on this file).")
def roam_deps(path: str, full: bool = False, root: str = ".") -> dict:
    """File-level import/imported-by relationships.

    WHEN TO USE: to understand a file's dependencies -- what it imports
    and what imports it. File-level granularity. Use for module boundary
    analysis and refactoring impact.

    Parameters
    ----------
    path:
        File path relative to project root.
    full:
        Show all dependencies without truncation.
    root:
        Working directory (project root).

    Returns: file path, imports list (paths, symbol counts), importers
    list (files that import this one).
    """
    args = ["deps", path]
    if full:
        args.append("--full")
    return _run_roam(args, root)


@_tool(name="roam_uses",
       description="All consumers of a symbol: callers, importers, inheritors by edge type.")
def roam_uses(name: str, full: bool = False, root: str = ".") -> dict:
    """All consumers of a symbol: callers, importers, inheritors.

    WHEN TO USE: to find ALL places using a symbol, grouped by edge type
    (calls, imports, inheritance, trait usage). Broader than roam_impact.
    Use for planning API changes.

    Parameters
    ----------
    name:
        Symbol name. Supports partial matching.
    full:
        Show all consumers without truncation.
    root:
        Working directory (project root).

    Returns: symbol name, total_consumers, total_files, consumers
    grouped by edge kind with name, kind, and location.
    """
    args = ["uses", name]
    if full:
        args.append("--full")
    return _run_roam(args, root)


# ===================================================================
# Health tools
# ===================================================================


@_tool(name="roam_weather",
       description="Churn x complexity hotspot ranking: highest-leverage refactoring targets.")
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


@_tool(name="roam_debt",
       description="Prioritized tech debt with SQALE remediation cost estimates.")
def roam_debt(limit: int = 20, by_kind: bool = False, threshold: float = 0.0,
              roi: bool = False,
              root: str = ".") -> dict:
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


@_tool(name="roam_docs_coverage",
       description="Doc coverage + stale-doc drift with PageRank-ranked missing docs.")
def roam_docs_coverage(limit: int = 20, days: int = 90, threshold: int = 0,
                       root: str = ".") -> dict:
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


@_tool(name="roam_suggest_refactoring",
       description="Rank proactive refactoring candidates using complexity/coupling/churn/smells.")
def roam_suggest_refactoring(limit: int = 20, min_score: int = 45,
                             root: str = ".") -> dict:
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


@_tool(name="roam_plan_refactor",
       description="Build an ordered refactor plan for one symbol using risk/test/simulation context.")
def roam_plan_refactor(symbol: str, operation: str = "auto", target_file: str = "",
                       max_steps: int = 7, root: str = ".") -> dict:
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


@_tool(name="roam_n1",
       description="Detect N+1 I/O patterns in ORM code (Laravel/Django/Rails/SQLAlchemy/JPA).")
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


@_tool(name="roam_auth_gaps",
       description="Endpoints missing authentication or authorization checks.")
def roam_auth_gaps(routes_only: bool = False, controllers_only: bool = False,
                   min_confidence: str = "medium", root: str = ".") -> dict:
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


@_tool(name="roam_over_fetch",
       description="Models serializing too many fields (data over-exposure risk).")
def roam_over_fetch(threshold: int = 10, confidence: str = "medium",
                    root: str = ".") -> dict:
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


@_tool(name="roam_missing_index",
       description="Queries on non-indexed columns (slow query risk).")
def roam_missing_index(table: str = "", confidence: str = "medium",
                       root: str = ".") -> dict:
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


@_tool(name="roam_orphan_routes",
       description="Backend routes with no frontend consumer (dead endpoints).")
def roam_orphan_routes(limit: int = 50, confidence: str = "medium",
                       root: str = ".") -> dict:
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


@_tool(name="roam_migration_safety",
       description="Non-idempotent database migrations (unsafe for re-run).")
def roam_migration_safety(limit: int = 50, include_archive: bool = False,
                          root: str = ".") -> dict:
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


@_tool(name="roam_api_drift",
       description="Mismatches between backend models and frontend interfaces.")
def roam_api_drift(model: str = "", confidence: str = "medium",
                   root: str = ".") -> dict:
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


@_tool(name="roam_syntax_check",
       description="Tree-sitter syntax validation. Finds ERROR/MISSING AST nodes. No index needed.",
       output_schema=_make_schema(
           {"total_files": {"type": "integer"}, "total_errors": {"type": "integer"},
            "clean": {"type": "boolean"}},
           files={"type": "array", "items": {"type": "object"}},
       ))
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


@_tool(name="roam_doctor",
       description="Setup diagnostics: Python version, tree-sitter, git, index existence, freshness, SQLite.",
       output_schema=_make_schema(
           {"total": {"type": "integer"}, "passed": {"type": "integer"},
            "failed": {"type": "integer"}, "all_passed": {"type": "boolean"}},
           checks={"type": "array", "items": {
               "type": "object",
               "properties": {
                   "name": {"type": "string"},
                   "passed": {"type": "boolean"},
                   "detail": {"type": "string"},
               },
           }},
           failed_checks={"type": "array", "items": {"type": "object"}},
       ))
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


@_tool(name="roam_codeowners",
       description="CODEOWNERS coverage, ownership distribution, unowned files, drift detection.")
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


@_tool(name="roam_drift",
       description="Ownership drift detection: declared CODEOWNERS vs actual time-decayed contributors.")
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


@_tool(name="roam_dev_profile",
       description="Developer behavioral profiling: commit time patterns, change scatter (Gini), burst detection.")
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


@_tool(name="roam_partition",
       description="Multi-agent work partitioning: split codebase into independent work zones.")
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




@_tool(name="roam_spectral",
       description="Spectral bisection: Fiedler vector partition tree and modularity gap.")
def roam_spectral(depth: int = 3, compare: bool = False, gap_only: bool = False,
                  k: int = 0, root: str = ".") -> dict:
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

@_tool(name="roam_affected",
       description="Monorepo impact analysis: find all affected packages/modules from changes.")
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


@_tool(name="roam_semantic_diff",
       description="Structural change summary: what symbols were added/removed/modified.")
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


@_tool(name="roam_trends",
       description="Historical metric tracking: record and query health metric trends over time.")
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


@_tool(name="roam_reset",
       description="Delete index DB and rebuild from scratch. Requires force=True. Recovery for corrupted indexes.",
       output_schema=_make_schema(
           {"removed": {"type": "boolean"}, "db_path": {"type": "string"}},
       ))
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


@_tool(name="roam_clean",
       description="Remove orphaned index entries (files deleted from disk) without full rebuild.",
       output_schema=_make_schema(
           {"files_removed": {"type": "integer"}, "symbols_removed": {"type": "integer"},
            "edges_removed": {"type": "integer"}},
           orphaned_paths={"type": "array", "items": {"type": "string"}},
       ))
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
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.option('--transport', type=click.Choice(['stdio', 'sse', 'streamable-http']), default='stdio',
              help='transport protocol (default: stdio)')
@click.option('--host', default='127.0.0.1', help='host for network transports')
@click.option('--port', type=int, default=8000, help='port for network transports')
@click.option('--no-auto-index', is_flag=True, help='skip automatic index freshness check')
@click.option('--list-tools', is_flag=True, help='list registered tools and exit')
@click.option('--list-tools-json', is_flag=True,
              help='list registered tools with metadata as JSON and exit')
@click.option('--compat-profile',
              type=click.Choice(['all', 'claude', 'codex', 'gemini', 'copilot', 'vscode', 'cursor']),
              default=None,
              help='emit client compatibility profile JSON and exit')
def mcp_cmd(transport, host, port, no_auto_index, list_tools, list_tools_json, compat_profile):
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

    if mcp is None:
        click.echo(
            "error: fastmcp is required for the MCP server.\n"
            "install it with:  pip install roam-code[mcp]",
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
            payload_tools.append({
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "annotations": ann,
                "task_support": execution.get("taskSupport") or meta.get("taskSupport"),
            })
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
            "Install it with:  pip install roam-code[mcp]"
        )
    mcp.run()
