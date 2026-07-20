"""Canonical privacy projection for local compile telemetry.

Every reader applies the same whitelist as the writer. Legacy rows can still
contribute safe aggregate fields, but prompt text, raw prompt hashes, session
identifiers, and unknown fields never cross into derived stores or commands.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

COMPILE_TELEMETRY_SCHEMA_VERSION = 4
COMPILE_TELEMETRY_SAFE_MODES = frozenset(
    {
        "unknown",
        "other",
        "compile",
        "roam",
        "vanilla",
        "hook",
        "read_only",
        "safe_edit",
        "migration",
        "autonomous_pr",
        "bench",
        "corpus",
        "trace",
        "envelope_diff",
        "compile_cache_build",
        "compile_codex",
        "compile_claude",
        "compile_maestro",
        "test",
    }
)
# Every persisted identifier-like value is selected from one of these closed
# vocabularies.
COMPILE_TELEMETRY_SAFE_PROCEDURES = frozenset(
    {
        "unknown",
        "other",
        "cli_verb_why_slow",
        "compare_x_vs_y",
        "config_where",
        "describe_file",
        "entry_point_where",
        "file_history",
        "freeform_explore",
        "refactor_move",
        "repo_structure",
        "self_contained_task",
        "session_meta",
        "stack_trace_fix",
        "structural_blast",
        "structural_callers",
        "structural_complexity",
        "structural_coupling",
        "structural_cycle",
        "structural_dead",
        "structural_query",
        "symbol_defined_where",
        "synthesis_query",
        "top_n_ranking",
        "trace_query",
    }
)
COMPILE_TELEMETRY_SAFE_ART_LABELS = frozenset(
    {"unknown", "other", "facts", "lean", "full", "l1_probe", "contract", "fallback"}
)
COMPILE_TELEMETRY_SAFE_INJECTION_ADVICE = frozenset(
    {"unknown", "other", "inject", "skip_generation_task", "skip_edit_task"}
)
COMPILE_TELEMETRY_SAFE_PROBE_TIMING_KEYS = frozenset(
    {
        "inner_probe",
        "task_text",
        "backtick_fallback",
        "always_on",
        "l10_symbol_resolution",
        "other",
    }
)
COMPILE_TELEMETRY_SAFE_PREFETCH_KEYS = frozenset(
    {
        "algo_findings",
        "api_surface",
        "bug_site_slice",
        "callers",
        "cli_verb_remediation",
        "cli_verb_slow_diagnosis",
        "cli_verb_subcommand",
        "compare_x_vs_y_result",
        "compare_x_vs_y_unavailable",
        "complexity_metrics",
        "config_matches",
        "config_matches_unavailable",
        "convention_samples",
        "cycle_count",
        "cycles",
        "declared_entry_points",
        "decision_criterion",
        "deprecation_markers",
        "design_patterns",
        "entry_points",
        "entry_points_unavailable",
        "env_vars_used",
        "file_excerpt",
        "file_history_unavailable",
        "file_recent_commits",
        "file_skeleton",
        "file_summary",
        "find_by_desc",
        "full_file_body",
        "grep_results",
        "impact_top_files",
        "import_audit",
        "known_findings",
        "owners",
        "path_comparison",
        "prefetched_facts_injection_markers",
        "reachability",
        "recent_commits",
        "refactor_move",
        "repo_structure_result",
        "repo_structure_unavailable",
        "resolved_entity",
        "resolved_named_paths_from_module_name",
        "runtime_hotspots",
        "runtime_hotspots_unavailable",
        "scope_lock",
        "self_contained_notice",
        "semantic_matches",
        "session_brief",
        "session_brief_unavailable",
        "sibling_test_excerpt",
        "stack_frames",
        "structural_imported_by_top",
        "structural_imports",
        "subprocess_sites",
        "symbol_definitions",
        "symbol_definitions_unavailable",
        "symbol_history",
        "taint_summary",
        "target_symbol",
        "temporal_coupling_pairs",
        "test_impact",
        "todo_items",
        "top_n_ranking",
        "top_n_ranking_unavailable",
        "trace_spans",
        "unused_top_10",
        "world_model",
        "other",
    }
)
COMPILE_TELEMETRY_SAFE_CATEGORIES = frozenset().union(
    COMPILE_TELEMETRY_SAFE_MODES,
    COMPILE_TELEMETRY_SAFE_PROCEDURES,
    COMPILE_TELEMETRY_SAFE_ART_LABELS,
    COMPILE_TELEMETRY_SAFE_INJECTION_ADVICE,
    COMPILE_TELEMETRY_SAFE_PROBE_TIMING_KEYS,
    COMPILE_TELEMETRY_SAFE_PREFETCH_KEYS,
)
COMPILE_TELEMETRY_EPISODE_ID_RE = re.compile(r"^ep_[0-9a-f]{24}$")
COMPILE_TELEMETRY_TASK_FINGERPRINT_RE = re.compile(r"^tfp_[0-9a-f]{32}$")
COMPILE_TELEMETRY_TS_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<hour>\d{2}):\d{2}:\d{2}Z$")


def safe_telemetry_category(
    value: Any,
    *,
    allowed: frozenset[str] = COMPILE_TELEMETRY_SAFE_CATEGORIES,
    default: str = "unknown",
) -> str:
    if not isinstance(value, str):
        return default
    raw = value.strip().lower()
    return raw if raw in allowed else default


def _closed_telemetry_category(value: Any, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return safe_telemetry_category(value, allowed=allowed, default="other")


def bucket_telemetry_ts(value: Any) -> str | None:
    raw = str(value or "").strip()
    match = COMPILE_TELEMETRY_TS_RE.fullmatch(raw)
    if not match:
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return f"{match.group('date')}T{match.group('hour')}:00:00Z"


def bounded_number(
    value: Any,
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < minimum or number > maximum:
        return None
    return int(number) if integer else number


def sanitize_compile_telemetry_row(row: Any) -> dict[str, Any] | None:
    """Return the closed aggregate row schema or ``None`` for invalid input."""

    if not isinstance(row, dict):
        return None
    ts = bucket_telemetry_ts(row.get("ts"))
    if ts is None:
        return None

    keys = row.get("prefetched_keys")
    raw_keys = [key.strip().lower() for key in (keys if isinstance(keys, list) else []) if isinstance(key, str)][:128]
    safe_keys = sorted({key if key in COMPILE_TELEMETRY_SAFE_PREFETCH_KEYS else "other" for key in raw_keys})
    mode = _closed_telemetry_category(row.get("agent_mode"), COMPILE_TELEMETRY_SAFE_MODES)
    if mode not in COMPILE_TELEMETRY_SAFE_MODES:
        mode = "other"

    out: dict[str, Any] = {
        "schema_version": COMPILE_TELEMETRY_SCHEMA_VERSION,
        "ts": ts,
        "procedure": _closed_telemetry_category(row.get("procedure"), COMPILE_TELEMETRY_SAFE_PROCEDURES),
        "art_label": _closed_telemetry_category(row.get("art_label"), COMPILE_TELEMETRY_SAFE_ART_LABELS),
        "prefetched_keys": safe_keys,
        "prefetched_fact_count": len(safe_keys),
        "agent_mode": mode,
        "injection_advice": _closed_telemetry_category(
            row.get("injection_advice"), COMPILE_TELEMETRY_SAFE_INJECTION_ADVICE
        ),
        "cache_hit": row.get("cache_hit") is True,
    }
    episode_id = str(row.get("episode_id") or "").strip()
    if COMPILE_TELEMETRY_EPISODE_ID_RE.fullmatch(episode_id):
        out["episode_id"] = episode_id
    task_fingerprint = str(row.get("task_fingerprint") or "").strip()
    if COMPILE_TELEMETRY_TASK_FINGERPRINT_RE.fullmatch(task_fingerprint):
        out["task_fingerprint"] = task_fingerprint

    numeric_fields = {
        "classifier_conf": (0.0, 1.0, False),
        "envelope_bytes": (-1.0, 100 * 1024 * 1024, True),
        "compile_ms": (0.0, 24 * 60 * 60 * 1000, False),
        "model_calls_avoided_count": (0.0, 1000.0, True),
    }
    for name, (minimum, maximum, integer) in numeric_fields.items():
        number = bounded_number(
            row.get(name),
            minimum=minimum,
            maximum=maximum,
            integer=integer,
        )
        if number is not None:
            out[name] = number

    timings = row.get("probe_timings_ms")
    if isinstance(timings, dict):
        safe_timings: dict[str, float] = {}
        for key, value in timings.items():
            safe_key = key if isinstance(key, str) and key in COMPILE_TELEMETRY_SAFE_PROBE_TIMING_KEYS else "other"
            number = bounded_number(
                value,
                minimum=0.0,
                maximum=24 * 60 * 60 * 1000,
            )
            if number is not None:
                safe_timings[safe_key] = min(
                    24 * 60 * 60 * 1000,
                    safe_timings.get(safe_key, 0.0) + float(number),
                )
        if safe_timings:
            out["probe_timings_ms"] = safe_timings

    avoided_count = int(out.get("model_calls_avoided_count", 0))
    out["savings"] = {
        "model_calls_avoided_count": avoided_count,
        "prefetched_fact_count": len(safe_keys),
        "cache_reuse_count": int(out["cache_hit"]),
    }
    return out
