"""Proof-oriented episode ledger for compiler savings measurement.

The hook boundary writes append-only JSONL because it must stay tiny,
dependency-free, and fail-open.  Analysis materializes those events plus
``compile-runs.jsonl`` into a local SQLite database so joins are idempotent,
deduplicated, and queryable.

This module deliberately separates:

* **measurement admissibility** — are prompt, compile, and terminal records
  joined with enough coverage to support analysis?
* **policy admissibility** — do we also know the execution-health context
  needed to turn repeated failures into routing or quarantine policy?

Frequency alone never promotes a savings claim.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from roam.atomic_io import capture_file_generation, conditional_install_file
from roam.compile_telemetry import bounded_number, sanitize_compile_telemetry_row
from roam.procedure_mining import build_procedure_atlas, normalized_episode_tokens
from roam.security.bounded_json import loads_bounded, strict_json_object_pairs
from roam.security.owner_only import (
    delete_file_if_matches_descriptor,
    delete_file_if_matches_identity,
    ensure_owner_only_file_descriptor,
    ensure_owner_only_path,
    file_descriptor_identity,
    file_descriptor_is_owner_only,
    open_new_owner_only_file,
    path_is_owner_only,
    pinned_owner_only_directory,
)

EPISODE_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 4
MIN_ADMISSIBLE_EPISODES = 30
MIN_COVERAGE_PCT = 95.0
TERMINAL_GRACE_SECONDS = 600
MIN_LIVE_HOOK_VERSION = 6

EVENT_LOG_NAME = "episodes.jsonl"
TRANSCRIPT_EVENT_LOG_NAME = "transcript-episodes.jsonl"
COMPILE_LOG_NAME = "compile-runs.jsonl"
LEDGER_DB_NAME = "episodes.sqlite"
MAX_EVENT_LOG_BYTES = 16 * 1024 * 1024
MAX_COMPILE_LOG_BYTES = 32 * 1024 * 1024
MAX_TRANSCRIPT_EVENT_LOG_BYTES = 32 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024
MAX_JSONL_ROWS = 60_000
MAX_JSONL_PARSE_SECONDS = 30.0
MAX_LEDGER_TEMPFILES = 512
MAX_LEDGER_DB_BYTES = 64 * 1024 * 1024
MAX_LEDGER_SIDECAR_BYTES = 16 * 1024 * 1024
MAX_LEDGER_LOCK_BYTES = 1024
LEDGER_TEMP_ORPHAN_GRACE_SECONDS = 5 * 60
LEDGER_MATERIALIZATION_LOCK_TIMEOUT_SECONDS = 120.0
LEDGER_MATERIALIZATION_LOCK_RETRY_SECONDS = 0.025
VALIDATED_HEALTH_STATES = frozenset({"verification_passed", "verification_failed"})

_LEDGER_MATERIALIZATION_THREAD_LOCK = threading.Lock()

SAVINGS_AGGREGATE_SCHEMA = "roam.savings.aggregate"
SAVINGS_AGGREGATE_SCHEMA_VERSION = 1
SAVINGS_COVERAGE_DEFINITIONS = {
    "terminal_coverage_definition": (
        "eligible prompt starts with a terminal stop event / prompt starts older than the grace window"
    ),
    "episode_join_coverage_definition": (
        "eligible prospective prompt starts with a terminal event and either the expected compile "
        "record or an explicit compile_expected=false skip / eligible prospective prompt starts"
    ),
    "compile_identity_coverage_definition": (
        "unambiguously post-prompt production compile rows with an opaque local outcome join key / "
        "production compile rows since the start of the coarse UTC hour containing the first prompt; "
        "identified rows in the overlapping hour bucket do not promote the numerator"
    ),
    "health_context_coverage_definition": (
        "eligible edited episodes with explicit verification_passed or verification_failed state / "
        "eligible edited episodes"
    ),
    "hook_version_coverage_definition": (
        f"eligible prospective prompt starts emitted by hook version >= {MIN_LIVE_HOOK_VERSION} / "
        "eligible prospective prompt starts"
    ),
    "repeat_identity_coverage_definition": (
        "terminal compile-expected prospective episodes joined to a keyed repo-local task fingerprint / "
        "terminal compile-expected prospective episodes"
    ),
}

_AGGREGATE_SUMMARY_VERDICTS = {
    "not_initialized": "Savings ledger not initialized",
    "insufficient_evidence": "Savings claims withheld because aggregate evidence is insufficient",
    "measurement_ready": "Aggregate episode joins are measurement-ready",
    "policy_ready": "Aggregate episode evidence is policy-ready",
    "unknown": "Savings aggregate unavailable because the source state is unknown",
}
_AGGREGATE_COVERAGE_COUNT_FIELDS = (
    "prompt_starts",
    "prospective_prompt_starts",
    "historical_prompt_starts",
    "eligible_prompt_starts",
    "terminal_outcomes",
    "compile_expected_episodes",
    "compile_joined_episodes",
    "fully_joined_episodes",
    "health_context_episodes",
    "health_expected_episodes",
    "current_hook_episodes",
    "repeat_identity_episodes",
    "repeat_expected_episodes",
)
_AGGREGATE_COVERAGE_PERCENT_FIELDS = (
    "terminal_coverage_pct",
    "episode_join_coverage_pct",
    "health_context_coverage_pct",
    "hook_version_coverage_pct",
    "compile_identity_coverage_pct",
    "repeat_identity_coverage_pct",
)
_AGGREGATE_DECLARATION_STATES = (
    "declared_native",
    "declared_partial",
    "unclaimed",
    "unknown",
)
_AGGREGATE_ASSIGNMENT_STATES = ("control", "exposed", "shadow", "unknown")

EVENT_FIELD_DEFINITIONS: dict[str, str] = {
    "event_id": "stable unique event identifier used for idempotent ingestion",
    "episode_id": "join key spanning prompt compilation and terminal outcome",
    "event_type": (
        "closed event kind: prompt_submitted, stop_decision, stop_continuation, "
        "intervention_assignment, or intervention_observation"
    ),
    "ts": "UTC event timestamp in ISO-8601 form",
    "session_id": "host session identifier; local-only join metadata",
    "turn_seq": "monotonic prompt sequence within the host session",
    "terminal": "whether this event closes the episode",
    "outcome": "closed terminal or intermediate outcome classification",
    "duration_ms": "elapsed milliseconds from prompt submission to this event",
    "changed_files": "count of changed tracked and untracked files at stop time",
    "diff_sha256": "content hash of the local tracked diff plus untracked path identities",
    "health_state": "closed execution-health state; only explicit verification pass/fail is validated",
    "evidence_source": "live_hook for prospective evidence or transcript_backfill for historical discovery",
    "hook_version": "live hook body version that produced the event",
    "intervention_id": "closed repeated-transition identifier for prospective intervention events",
    "intervention_version": "versioned intervention behavior assigned to the episode",
    "eligibility_rule_version": "version of the pre-intervention eligibility rule",
    "assignment": "control, exposed, or shadow assignment made before intervention delivery",
    "assignment_cluster": "session-level cluster identifier used to prevent cross-arm contamination",
    "eligible_transition": "whether the pre-registered transition eligibility rule passed",
    "delivered": "whether the assigned intervention was actually surfaced",
    "adopted": "whether the agent used the surfaced intervention",
    "downstream_transition_count": "eligible repeated transitions observed after assignment",
}

_INTERVENTION_ASSIGNMENTS = frozenset({"control", "exposed", "shadow"})
_INTERVENTION_EVENT_TYPES = frozenset({"intervention_assignment", "intervention_observation"})
_EVENT_TYPES = frozenset(
    {
        "prompt_submitted",
        "stop_decision",
        "stop_continuation",
        "transcript_terminal",
        *_INTERVENTION_EVENT_TYPES,
    }
)
_EVENT_OUTCOMES = frozenset(
    {
        "pending",
        "unknown",
        "no_edit",
        "verified_clean",
        "verify_unavailable",
        "verification_blocked",
        "verification_failed_without_findings",
        "second_opinion_blocked",
        "continued_after_block",
        "policy_evidence_unavailable",
        "policy_tampering",
        "verification_race",
        "verification_snapshot_unavailable",
        "historical_acted_verified_proxy",
        "historical_acted_verification_failed_proxy",
        "historical_acted_unverified",
        "historical_no_edit_tool_error",
        "historical_no_edit",
        "intervention_measurement",
    }
)
_HEALTH_STATES = frozenset(
    {
        "unknown",
        "verification_passed",
        "verification_failed",
        "verification_unavailable",
        "continuation_unverified",
        "not_applicable",
        "proxy_verification_passed",
        "proxy_verification_failed",
        "proxy_unverified",
        "proxy_tool_error",
    }
)
_EVIDENCE_SOURCES = frozenset({"live_hook", "transcript_backfill"})
_TRANSCRIPT_SOURCES = frozenset({"claude", "codex"})
_PROJECT_IDENTITY_BASES = frozenset({"git_root", "workspace", "missing"})
_INTENT_ARCHETYPES = frozenset(
    {
        "debug",
        "implement",
        "refactor",
        "review",
        "verify",
        "performance",
        "security",
        "deploy",
        "research",
        "document",
        "plan",
        "data",
        "ui",
        "git",
        "other",
    }
)
_TOOL_FAMILIES = frozenset({"edit", "shell", "read", "search", "roam", "web", "agent", "other"})
_PHASES = frozenset(
    {
        "verify",
        "format",
        "review",
        "publish",
        "deploy",
        "setup",
        "search",
        "inspect",
        "orient",
        "shell",
        "edit",
        "intelligence",
        "research",
        "delegate",
        "other",
    }
)
_COMMAND_CLASSES = frozenset(
    {
        "verify",
        "build",
        "format",
        "review",
        "vcs_write",
        "deploy",
        "dependency",
        "search",
        "inspect",
        "orient",
        "git",
        "other",
    }
)
_FAILURE_CLASSES = frozenset(
    {
        "invalid_invocation",
        "command_unavailable",
        "dependency_unavailable",
        "path_unavailable",
        "permission_or_auth",
        "timeout",
        "network",
        "resource_exhausted",
        "state_conflict",
        "syntax_or_compile",
        "test_failure",
        "unknown",
    }
)
_FRICTION_FIELDS = frozenset(
    {
        "orientation_calls",
        "search_calls",
        "inspection_calls",
        "slice_calls",
        "output_postprocess_calls",
        "structured_output_postprocess_calls",
        "help_calls",
        "exact_shell_replays",
        "adjacent_shell_replays",
        "failed_action_retries",
        "verification_retries",
        "post_edit_context_calls",
        "search_inspect_cycles",
        "phase_switches",
    }
)
_REGISTERED_INTERVENTIONS = frozenset({"repeated_code_slicing"})
_REGISTERED_INTERVENTION_VERSIONS = frozenset({"grep-packets-v1"})
_REGISTERED_ELIGIBILITY_RULES = frozenset({"slice-transition-v1"})
_EPISODE_ID_RE = re.compile(r"^(?:ep_|hist_)[0-9a-f]{24}$|^orphan_[0-9a-f]{20}$")
_EVENT_ID_RE = re.compile(r"^evt_[0-9a-f]{20,24}(?:_(?:start|terminal))?$")
_HEX_16_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX_24_RE = re.compile(r"^[0-9a-f]{24}$")
_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SHELL_EXECUTABLES = frozenset(
    {
        "ansible",
        "awk",
        "bash",
        "black",
        "cargo",
        "cat",
        "cmd",
        "cmake",
        "cut",
        "dir",
        "docker",
        "dotnet",
        "eslint",
        "findstr",
        "get-childitem",
        "get-content",
        "get-location",
        "git",
        "go",
        "gofmt",
        "gradle",
        "head",
        "helm",
        "journalctl",
        "jq",
        "kubectl",
        "less",
        "ls",
        "make",
        "more",
        "mvn",
        "mypy",
        "ninja",
        "nl",
        "node",
        "npm",
        "npx",
        "pnpm",
        "poetry",
        "powershell",
        "prettier",
        "pwsh",
        "py",
        "pyright",
        "pytest",
        "python",
        "python3",
        "rg",
        "roam",
        "rsync",
        "ruff",
        "rustfmt",
        "scp",
        "sed",
        "select-object",
        "sh",
        "ssh",
        "systemctl",
        "tail",
        "terraform",
        "tox",
        "tree",
        "tsc",
        "uv",
        "vitest",
        "yarn",
    }
)
_SAFE_SHELL_WORDS = frozenset(
    {
        "add",
        "branch",
        "build",
        "check",
        "checkout",
        "commit",
        "context",
        "diff",
        "fetch",
        "format",
        "grep",
        "health",
        "init",
        "install",
        "lint",
        "log",
        "merge",
        "pull",
        "push",
        "rebase",
        "rev-parse",
        "run",
        "show",
        "status",
        "tag",
        "test",
        "typecheck",
        "unittest",
        "verify",
    }
)
_SAFE_SHELL_FLAGS = frozenset(
    {
        "-a",
        "-c",
        "-f",
        "-h",
        "-m",
        "-n",
        "-q",
        "-r",
        "-v",
        "--all",
        "--auto",
        "--check",
        "--diff-only",
        "--dry-run",
        "--exclude",
        "--files",
        "--format",
        "--help",
        "--json",
        "--max-count",
        "--no-cache",
        "--quiet",
        "--root",
        "--short",
        "--verbose",
        "--version",
    }
)
_SHELL_PLACEHOLDERS = frozenset(
    {
        "<ARG>",
        "<CODE>",
        "<EXEC>",
        "<MODULE>",
        "<N>",
        "<PATH>",
        "<REDIR>",
        "<SECRET>",
        "<SUBCOMMAND>",
        "<URL>",
        "<ENV>=<VALUE>",
        "--<FLAG>",
        "-<FLAG>",
        "--<FLAG>=<ARG>",
    }
)

# Only fields consumed by savings analysis may enter the derived SQLite cache.
# This blocks legacy or malformed source rows from smuggling prompt text or
# other unknown transcript fields into ``payload_json``.
_EPISODE_PAYLOAD_FIELDS = frozenset(
    {
        "compile_expected",
        "transcript_source",
        "project_id",
        "project_identity_basis",
        "intent_archetypes",
        "intent_simhash64",
        "prompt_hmac_sha256",
        "prompt_tokens_bucket",
        "trajectory_fingerprint",
        "trajectory_template",
        "phase_sequence_template",
        "command_sequence_fingerprint",
        "command_sequence_template",
        "shell_templates",
        "shell_ngrams",
        "tool_ngrams",
        "phase_ngrams",
        "friction",
        "shell_template_outcomes",
        "phase_outcomes",
        "command_class_outcomes",
        "tool_calls",
        "tool_errors",
        "tool_result_bytes_bucket",
        "assistant_messages",
        "time_to_first_tool_ms",
        "time_to_first_edit_ms",
        "edit_actions",
        "verification_attempts",
        "verification_failures",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_creation_tokens",
        "reasoning_output_tokens",
        "correction_after",
        "intervention_id",
        "intervention_version",
        "eligibility_rule_version",
        "assignment",
        "assignment_cluster",
        "eligible_transition",
        "delivered",
        "adopted",
        "downstream_transition_count",
    }
)


def _bounded_event_int(value: Any, *, maximum: int = 10**12) -> int | None:
    number = bounded_number(value, minimum=0.0, maximum=float(maximum), integer=True)
    return int(number) if number is not None else None


def _closed_value(value: Any, allowed: frozenset[str]) -> str | None:
    raw = value.strip().lower() if isinstance(value, str) else ""
    return raw if raw in allowed else None


def _session_join_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return "sid_" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def _sanitize_closed_sequence(value: Any, allowed: frozenset[str], *, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 320 or any(char in value for char in "\r\n\t"):
        return None
    has_more = value == "<MORE>" or value.endswith("><MORE>")
    core = "" if value == "<MORE>" else value[: -len("><MORE>")] if has_more else value
    segments = core.split(">") if core else []
    rendered: list[str] = []
    for segment in segments:
        match = re.fullmatch(r"([a-z_]+)(?:\*([1-9][0-9]{0,5}))?", segment)
        if match is None or match.group(1) not in allowed:
            return None
        count = int(match.group(2) or "1")
        rendered.append(f"{match.group(1)}*{count}" if count > 1 else match.group(1))
        if len(rendered) > limit:
            return None
    if has_more:
        rendered.append("<MORE>")
    if not rendered or len(rendered) > limit:
        return None
    return ">".join(rendered)


def _sanitize_shell_template(value: Any) -> str | None:
    """Canonicalize an already-redacted shell shape through a closed grammar."""

    if not isinstance(value, str) or not value or len(value) > 320 or any(char in value for char in "\r\n\t"):
        return None
    tokens = value.split()
    if not tokens or len(tokens) > 96:
        return None
    rendered: list[str] = []
    command_start = True
    unknown_executable = False
    for token in tokens:
        if token in {"&&", "||", "|", ";"}:
            if command_start:
                return None
            rendered.append(token)
            command_start = True
            continue
        if command_start:
            executable = token.lower().removesuffix(".exe")
            if executable not in _SAFE_SHELL_EXECUTABLES:
                unknown_executable = True
                rendered.append("<EXEC>")
            else:
                rendered.append(executable)
            command_start = False
            continue
        if token in _SHELL_PLACEHOLDERS:
            rendered.append(token)
        elif token in _SAFE_SHELL_FLAGS:
            rendered.append(token)
        elif token.lower() in _SAFE_SHELL_WORDS:
            rendered.append(token.lower())
        elif "=" in token:
            flag, assigned = token.split("=", 1)
            if flag in _SAFE_SHELL_FLAGS and assigned in _SHELL_PLACEHOLDERS:
                rendered.append(f"{flag}={assigned}")
            else:
                rendered.append("--<FLAG>=<ARG>" if flag.startswith("-") else "<ARG>")
        else:
            rendered.append("<ARG>")
    if command_start or unknown_executable:
        return None
    return " ".join(rendered)


def _sanitize_shell_sequence(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    rendered: list[str] = []
    segments = value.split(" => ")
    for index, segment in enumerate(segments):
        if segment == "<MORE>" and index == len(segments) - 1:
            rendered.append(segment)
            continue
        match = re.fullmatch(r"(.+?)(?: ×([1-9][0-9]{0,5}))?", segment)
        if match is None:
            return None
        template = _sanitize_shell_template(match.group(1))
        if template is None:
            return None
        count = int(match.group(2) or "1")
        rendered.append(f"{template} ×{count}" if count > 1 else template)
        if len(rendered) > limit:
            return None
    return " => ".join(rendered)


def _sanitize_count_map(
    value: Any,
    *,
    key_sanitizer,
    limit: int = 128,
) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, raw_count in list(value.items())[:limit]:
        safe_key = key_sanitizer(key)
        count = _bounded_event_int(raw_count, maximum=10**9)
        if safe_key and count is not None and count > 0:
            out[safe_key] = min(10**9, out.get(safe_key, 0) + count)
    return out


def _sanitize_outcome_map(value: Any, *, key_sanitizer) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, raw_record in list(value.items())[:128]:
        safe_key = key_sanitizer(key)
        if not safe_key or not isinstance(raw_record, dict):
            continue
        record: dict[str, Any] = {}
        for field in ("attempts", "failures", "no_results", "retries_after_failure", "result_bytes_bucket"):
            number = _bounded_event_int(raw_record.get(field), maximum=10**12)
            if number is not None:
                record[field] = number
        failures = _sanitize_count_map(
            raw_record.get("failure_classes"),
            key_sanitizer=lambda item: _closed_value(item, _FAILURE_CLASSES),
            limit=len(_FAILURE_CLASSES),
        )
        if failures:
            record["failure_classes"] = failures
        if record:
            out[safe_key] = record
    return out


def _sanitize_episode_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Project one source row onto the closed, non-text episode schema."""

    payload: dict[str, Any] = {}
    for field in ("compile_expected", "correction_after", "eligible_transition", "delivered", "adopted"):
        if isinstance(row.get(field), bool):
            payload[field] = row[field]
    scalar_categories = {
        "transcript_source": _TRANSCRIPT_SOURCES,
        "project_identity_basis": _PROJECT_IDENTITY_BASES,
        "assignment": _INTERVENTION_ASSIGNMENTS,
        "intervention_id": _REGISTERED_INTERVENTIONS,
        "intervention_version": _REGISTERED_INTERVENTION_VERSIONS,
        "eligibility_rule_version": _REGISTERED_ELIGIBILITY_RULES,
    }
    for field, allowed in scalar_categories.items():
        safe = _closed_value(row.get(field), allowed)
        if safe is not None:
            payload[field] = safe
    if isinstance(row.get("intent_archetypes"), list):
        intents = [
            safe
            for item in row["intent_archetypes"][:4]
            if (safe := _closed_value(item, _INTENT_ARCHETYPES)) is not None
        ]
        if intents:
            payload["intent_archetypes"] = list(dict.fromkeys(intents))
    regex_fields = {
        "project_id": re.compile(r"^proj_[0-9a-f]{20}$"),
        "intent_simhash64": _HEX_16_RE,
        "prompt_hmac_sha256": _HEX_32_RE,
        "trajectory_fingerprint": _HEX_24_RE,
        "command_sequence_fingerprint": _HEX_24_RE,
    }
    for field, pattern in regex_fields.items():
        value = str(row.get(field) or "").strip().lower()
        if pattern.fullmatch(value):
            payload[field] = value
    if row.get("assignment_cluster"):
        payload["assignment_cluster"] = _session_join_id(row.get("assignment_cluster"))
    trajectory = _sanitize_closed_sequence(row.get("trajectory_template"), _TOOL_FAMILIES, limit=21)
    if trajectory:
        payload["trajectory_template"] = trajectory
    phases = _sanitize_closed_sequence(row.get("phase_sequence_template"), _PHASES, limit=21)
    if phases:
        payload["phase_sequence_template"] = phases
    commands = _sanitize_shell_sequence(row.get("command_sequence_template"), limit=13)
    if commands:
        payload["command_sequence_template"] = commands
    map_specs = {
        "shell_templates": lambda item: _sanitize_shell_template(item),
        "shell_ngrams": lambda item: _sanitize_shell_sequence(item, limit=4),
        "tool_ngrams": lambda item: _sanitize_ngram(item, _TOOL_FAMILIES, maximum=5),
        "phase_ngrams": lambda item: _sanitize_ngram(item, _PHASES, maximum=5),
    }
    for field, sanitizer in map_specs.items():
        safe_map = _sanitize_count_map(row.get(field), key_sanitizer=sanitizer)
        if safe_map:
            payload[field] = safe_map
    friction = _sanitize_count_map(
        row.get("friction"),
        key_sanitizer=lambda item: _closed_value(item, _FRICTION_FIELDS),
        limit=len(_FRICTION_FIELDS),
    )
    if friction:
        payload["friction"] = friction
    outcome_specs = {
        "shell_template_outcomes": lambda item: _sanitize_shell_template(item),
        "phase_outcomes": lambda item: _closed_value(item, _PHASES),
        "command_class_outcomes": lambda item: _closed_value(item, _COMMAND_CLASSES),
    }
    for field, sanitizer in outcome_specs.items():
        safe_map = _sanitize_outcome_map(row.get(field), key_sanitizer=sanitizer)
        if safe_map:
            payload[field] = safe_map
    numeric_fields = (
        "prompt_tokens_bucket",
        "tool_calls",
        "tool_errors",
        "tool_result_bytes_bucket",
        "assistant_messages",
        "time_to_first_tool_ms",
        "time_to_first_edit_ms",
        "edit_actions",
        "verification_attempts",
        "verification_failures",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_creation_tokens",
        "reasoning_output_tokens",
        "downstream_transition_count",
    )
    for field in numeric_fields:
        number = _bounded_event_int(row.get(field))
        if number is not None:
            payload[field] = number
    # Keep the field-name allowlist authoritative as well as the value grammar;
    # future edits must satisfy both boundaries before data can persist.
    return {field: payload[field] for field in _EPISODE_PAYLOAD_FIELDS if field in payload}


def _sanitize_ngram(value: Any, allowed: frozenset[str], *, maximum: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 320:
        return None
    parts = value.split(" => ")
    if not 2 <= len(parts) <= maximum:
        return None
    safe = [_closed_value(part, allowed) for part in parts]
    return " => ".join(safe) if all(safe) else None


def _parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _read_jsonl(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], int]:
    if not os.path.lexists(path):
        return [], 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SavingsLedgerSafetyError(f"cannot open {label}: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > max_bytes
            or not ensure_owner_only_file_descriptor(descriptor, path)
        ):
            raise SavingsLedgerSafetyError(f"{label} must be a bounded owner-only regular file: {path}")
        # Permission repair, where needed for a legacy single-link file, has
        # completed. Capture the exact post-repair object before reading so the
        # coherence check does not mistake our own ACL update for source churn.
        opened = os.fstat(descriptor)
        # Read exactly the open-time size without materializing a second full
        # payload or ``splitlines`` list. Any concurrent append or same-size
        # rewrite invalidates the whole buffered snapshot below.
        remaining = opened.st_size
        rows: list[dict[str, Any]] = []
        invalid = 0
        line_number = 0
        parse_deadline = time.monotonic() + MAX_JSONL_PARSE_SECONDS

        def _assert_parse_deadline() -> None:
            if time.monotonic() > parse_deadline:
                raise SavingsLedgerSafetyError(
                    f"{label} exceeds the {MAX_JSONL_PARSE_SECONDS:g}-second parse limit: {path}"
                )

        fh = os.fdopen(descriptor, "rb", buffering=64 * 1024)
        descriptor = -1
        with fh:
            while remaining:
                line_number += 1
                if line_number > MAX_JSONL_ROWS:
                    raise SavingsLedgerSafetyError(f"{label} exceeds the {MAX_JSONL_ROWS}-row limit: {path}")
                _assert_parse_deadline()
                raw_line = fh.readline(min(MAX_JSONL_LINE_BYTES + 1, remaining))
                _assert_parse_deadline()
                if not raw_line:
                    raise SavingsLedgerSafetyError(f"{label} changed while it was read: {path}")
                remaining -= len(raw_line)
                if len(raw_line) > MAX_JSONL_LINE_BYTES:
                    # Drain this one oversized logical line in bounded pieces.
                    while remaining and not raw_line.endswith(b"\n"):
                        raw_line = fh.readline(min(MAX_JSONL_LINE_BYTES + 1, remaining))
                        if not raw_line:
                            raise SavingsLedgerSafetyError(f"{label} changed while it was read: {path}")
                        remaining -= len(raw_line)
                        _assert_parse_deadline()
                    invalid += 1
                    continue
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    value = loads_bounded(raw_line, object_pairs_hook=strict_json_object_pairs)
                except (TypeError, ValueError):
                    _assert_parse_deadline()
                    invalid += 1
                    continue
                _assert_parse_deadline()
                if not isinstance(value, dict):
                    invalid += 1
                    continue
                value = dict(value)
                value["__source_line"] = line_number
                rows.append(value)
            after_descriptor = os.fstat(fh.fileno())
        try:
            after_path = os.lstat(path)
        except OSError as exc:
            raise SavingsLedgerSafetyError(f"{label} changed while it was read: {path}") from exc
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns")
        if os.name != "nt":
            stable_fields += ("st_ctime_ns",)
        if any(getattr(opened, field) != getattr(after_descriptor, field) for field in stable_fields) or any(
            getattr(after_descriptor, field) != getattr(after_path, field) for field in stable_fields
        ):
            raise SavingsLedgerSafetyError(f"{label} changed while it was read: {path}")
        _assert_parse_deadline()
        return rows, invalid
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _canonical_hash(prefix: str, value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return prefix + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


class SavingsLedgerSafetyError(ValueError):
    """The derived ledger could not be kept inside owner-only state."""


def _is_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validate_ledger_artifact(
    path: Path,
    *,
    parent_was_private: bool,
    max_bytes: int = MAX_LEDGER_DB_BYTES,
) -> None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SavingsLedgerSafetyError(f"cannot inspect savings ledger artifact: {path}") from exc
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or _is_reparse_point(value)
        or value.st_nlink != 1
        or value.st_size > max_bytes
    ):
        raise SavingsLedgerSafetyError(f"savings ledger artifact must be a bounded regular private file: {path}")
    if not path_is_owner_only(path):
        if not parent_was_private or not ensure_owner_only_path(path):
            raise SavingsLedgerSafetyError(f"savings ledger artifact is not owner-only: {path}")


def _prepare_ledger_directory(path: Path) -> Path:
    state = path.parent
    parent_was_private = path_is_owner_only(state) if os.path.lexists(state) else False
    for suffix, max_bytes in (
        ("", MAX_LEDGER_DB_BYTES),
        ("-journal", MAX_LEDGER_SIDECAR_BYTES),
        ("-wal", MAX_LEDGER_SIDECAR_BYTES),
        ("-shm", MAX_LEDGER_SIDECAR_BYTES),
        (".materialize.lock", MAX_LEDGER_LOCK_BYTES),
    ):
        _validate_ledger_artifact(
            Path(f"{path}{suffix}"),
            parent_was_private=parent_was_private,
            max_bytes=max_bytes,
        )
    try:
        from roam.transcript_backfill import _private_state_directory

        secured = _private_state_directory(state.parent, create=True)
    except (OSError, ValueError) as exc:
        raise SavingsLedgerSafetyError(f"savings ledger directory is unsafe: {state}") from exc
    if secured != state:
        raise SavingsLedgerSafetyError(f"savings ledger directory escaped project state: {state}")
    if not ensure_owner_only_path(state):
        raise SavingsLedgerSafetyError(f"savings ledger directory is not owner-only: {state}")
    return state


def _ledger_lock_path(path: Path) -> Path:
    return Path(f"{path}.materialize.lock")


def _open_ledger_materialization_lock(path: Path) -> int:
    """Open one private, single-link lock file without following links."""

    lock_path = _ledger_lock_path(path)
    try:
        descriptor = open_new_owner_only_file(lock_path)
    except FileExistsError:
        _validate_ledger_artifact(lock_path, parent_was_private=True)
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            before = os.lstat(lock_path)
            descriptor = os.open(lock_path, flags)
        except OSError as exc:
            raise SavingsLedgerSafetyError(f"cannot open savings ledger materialization lock: {lock_path}") from exc
        try:
            opened = os.fstat(descriptor)
            current = os.lstat(lock_path)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or not path_is_owner_only(lock_path)
            ):
                raise SavingsLedgerSafetyError(f"savings ledger materialization lock changed: {lock_path}")
        except BaseException:
            os.close(descriptor)
            raise
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise SavingsLedgerSafetyError(f"invalid savings ledger materialization lock: {lock_path}")
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
        elif opened.st_size != 1:
            raise SavingsLedgerSafetyError(f"invalid savings ledger materialization lock: {lock_path}")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if not ensure_owner_only_file_descriptor(descriptor, lock_path):
            raise SavingsLedgerSafetyError(f"savings ledger materialization lock is not private: {lock_path}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _try_lock_ledger_materialization(descriptor: int) -> bool:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock_ledger_materialization(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextlib.contextmanager
def _exclusive_ledger_materialization(path: Path):
    """Serialize cleanup and replacement across threads and processes."""

    acquired_thread_lock = _LEDGER_MATERIALIZATION_THREAD_LOCK.acquire(
        timeout=LEDGER_MATERIALIZATION_LOCK_TIMEOUT_SECONDS
    )
    if not acquired_thread_lock:
        raise TimeoutError("timed out acquiring savings ledger materialization thread lock")
    descriptor = -1
    locked = False
    try:
        descriptor = _open_ledger_materialization_lock(path)
        deadline = time.monotonic() + LEDGER_MATERIALIZATION_LOCK_TIMEOUT_SECONDS
        while not _try_lock_ledger_materialization(descriptor):
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out acquiring savings ledger materialization process lock")
            time.sleep(LEDGER_MATERIALIZATION_LOCK_RETRY_SECONDS)
        locked = True
        opened = os.fstat(descriptor)
        current = os.lstat(_ledger_lock_path(path))
        if (
            opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            or not ensure_owner_only_file_descriptor(descriptor, _ledger_lock_path(path))
        ):
            raise SavingsLedgerSafetyError("savings ledger materialization lock changed after acquisition")
        yield
    finally:
        if locked:
            with contextlib.suppress(OSError):
                _unlock_ledger_materialization(descriptor)
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        _LEDGER_MATERIALIZATION_THREAD_LOCK.release()


def _new_ledger_temp(path: Path) -> tuple[int, Path]:
    for _attempt in range(128):
        temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
        try:
            return open_new_owner_only_file(temporary, allow_delete_sharing=True), temporary
        except FileExistsError:
            continue
    raise SavingsLedgerSafetyError("could not allocate a private savings-ledger tempfile")


def _unlink_if_same_file(path: Path, descriptor: int) -> bool:
    if os.name != "nt":
        # POSIX exposes no portable unlink-by-open-file-identity operation.
        # A stat-then-unlink sequence can delete a replacement installed in
        # between, so failed cleanup deliberately leaves the private random
        # tempfile for the bounded orphan inventory to disclose.
        return False
    return delete_file_if_matches_descriptor(path, descriptor)


def _cleanup_orphaned_ledger_temps(path: Path) -> tuple[int, int]:
    """Remove bounded, identity-checked tempfiles left by interrupted rebuilds.

    Production callers hold the materialization lock for both this sweep and
    tempfile creation, so another current writer can never be mistaken for an
    orphan. The grace period is a second fail-safe for legacy writers that did
    not yet participate in that lock protocol.
    """

    pattern = re.compile(rf"^\.{re.escape(path.name)}\.[0-9a-f]{{16}}\.tmp$")
    candidates: list[Path] = []
    try:
        with os.scandir(path.parent) as entries:
            for entry in entries:
                if pattern.fullmatch(entry.name):
                    candidates.append(Path(entry.path))
                    if len(candidates) > MAX_LEDGER_TEMPFILES:
                        raise SavingsLedgerSafetyError(f"too many orphaned savings-ledger tempfiles in {path.parent}")
    except OSError as exc:
        raise SavingsLedgerSafetyError(f"cannot inspect savings ledger directory: {path.parent}") from exc

    removed = 0
    retained = 0
    for candidate in candidates:
        try:
            before = os.lstat(candidate)
        except OSError as exc:
            raise SavingsLedgerSafetyError(f"cannot inspect savings ledger tempfile: {candidate}") from exc
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse_point(before)
            or before.st_nlink != 1
            or not path_is_owner_only(candidate)
        ):
            raise SavingsLedgerSafetyError(
                f"orphaned savings ledger tempfile must be a regular owner-only file: {candidate}"
            )
        modified_ns = int(getattr(before, "st_mtime_ns", before.st_mtime * 1_000_000_000))
        age_ns = time.time_ns() - modified_ns
        if age_ns < int(LEDGER_TEMP_ORPHAN_GRACE_SECONDS * 1_000_000_000):
            continue
        if os.name != "nt":
            retained += 1
            continue
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(candidate, flags)
        except OSError as exc:
            raise SavingsLedgerSafetyError(f"cannot pin savings ledger tempfile: {candidate}") from exc
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
            ):
                raise SavingsLedgerSafetyError(f"orphaned savings ledger tempfile changed: {candidate}")
            # A CRT descriptor opened for inspection does not share DELETE,
            # so release it before opening the dedicated delete handle. The
            # second handle is still bound to the captured native file
            # identity; a pathname replacement is never removed.
            identity = file_descriptor_identity(descriptor)
            if identity is None:
                raise SavingsLedgerSafetyError(f"cannot identify savings ledger tempfile: {candidate}")
            os.close(descriptor)
            descriptor = -1
            if not delete_file_if_matches_identity(candidate, identity):
                raise SavingsLedgerSafetyError(f"orphaned savings ledger tempfile changed: {candidate}")
            removed += 1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return removed, retained


def _prior_ledger_ids(
    path: Path,
    current_event_ids: set[str],
    current_compile_ids: set[str],
) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != LEDGER_SCHEMA_VERSION:
                return set(), set()

            def existing_ids(table: str, column: str, current: set[str]) -> set[str]:
                found: set[str] = set()
                values = list(current)
                for start in range(0, len(values), 400):
                    batch = values[start : start + 400]
                    placeholders = ",".join("?" for _ in batch)
                    query = f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})"
                    found.update(str(row[0]) for row in conn.execute(query, batch))
                return found

            events = existing_ids("episode_events", "event_id", current_event_ids)
            compiles = existing_ids("compile_records", "record_id", current_compile_ids)
            return events, compiles
    except sqlite3.DatabaseError:
        # The ledger is a rebuildable projection. A crash-corrupted prior cache
        # contributes no idempotency baseline and is replaced from source logs.
        return set(), set()


@contextlib.contextmanager
def _open_ledger(path: Path):
    state = _prepare_ledger_directory(path)
    with pinned_owner_only_directory(state):
        _validate_ledger_artifact(path, parent_was_private=True)
        if not path.exists():
            raise SavingsLedgerSafetyError(f"savings ledger is not initialized: {path}")
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != LEDGER_SCHEMA_VERSION:
                raise SavingsLedgerSafetyError(
                    f"savings ledger schema mismatch: expected {LEDGER_SCHEMA_VERSION}, found {version}"
                )
            yield conn
        finally:
            conn.close()
            if not path_is_owner_only(path):
                raise SavingsLedgerSafetyError(f"savings ledger lost owner-only protection: {path}")


def _initialize_ledger(conn: sqlite3.Connection) -> None:
    # MEMORY journaling ensures SQLite never writes a rollback/WAL copy of
    # transcript-derived rows. The whole database is a fresh disposable
    # projection and is atomically installed only after quick_check succeeds.
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.executescript(
        """
        CREATE TABLE episode_events (
            event_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ts TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            turn_seq INTEGER,
            terminal INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER,
            changed_files INTEGER,
            diff_sha256 TEXT NOT NULL DEFAULT '',
            health_state TEXT NOT NULL DEFAULT 'unknown',
            evidence_source TEXT NOT NULL DEFAULT 'live_hook',
            hook_version INTEGER,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX idx_episode_events_episode ON episode_events(episode_id, ts);
        CREATE INDEX idx_episode_events_type ON episode_events(event_type, ts);

        CREATE TABLE compile_records (
            record_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL DEFAULT '',
            ts TEXT NOT NULL,
            task_fingerprint TEXT NOT NULL DEFAULT '',
            procedure TEXT NOT NULL DEFAULT '',
            classifier_conf REAL,
            art_label TEXT NOT NULL DEFAULT '',
            envelope_bytes INTEGER,
            compile_ms REAL,
            injection_advice TEXT NOT NULL DEFAULT '',
            cache_hit INTEGER NOT NULL DEFAULT 0,
            agent_mode TEXT NOT NULL DEFAULT 'unknown'
        );
        CREATE INDEX idx_compile_records_episode ON compile_records(episode_id, ts);
        CREATE INDEX idx_compile_records_task ON compile_records(task_fingerprint, ts);

        CREATE TABLE ledger_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(f"PRAGMA user_version={LEDGER_SCHEMA_VERSION}")


def _ingest_events(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> tuple[int, int, set[str]]:
    inserted = 0
    rejected = 0
    seen_event_ids: set[str] = set()
    for row in rows:
        episode_id = str(row.get("episode_id") or "").strip()
        event_type = _closed_value(row.get("event_type"), _EVENT_TYPES)
        parsed_ts = _parse_ts(row.get("ts"))
        if not _EPISODE_ID_RE.fullmatch(episode_id) or event_type is None or parsed_ts is None:
            rejected += 1
            continue
        ts = parsed_ts.isoformat().replace("+00:00", "Z")
        supplied_event_id = str(row.get("event_id") or "").strip().lower()
        event_id = supplied_event_id if _EVENT_ID_RE.fullmatch(supplied_event_id) else _canonical_hash("evt_", row)
        payload = _sanitize_episode_payload(row)
        session_id = _session_join_id(row.get("session_id"))
        outcome = _closed_value(row.get("outcome"), _EVENT_OUTCOMES) or "unknown"
        health_state = _closed_value(row.get("health_state"), _HEALTH_STATES) or "unknown"
        evidence_source = _closed_value(row.get("evidence_source"), _EVIDENCE_SOURCES) or "live_hook"
        diff_sha256 = str(row.get("diff_sha256") or "").strip().lower()
        if not _HEX_64_RE.fullmatch(diff_sha256):
            diff_sha256 = ""
        seen_event_ids.add(event_id)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO episode_events (
                event_id, episode_id, event_type, ts, session_id, turn_seq,
                terminal, outcome, duration_ms, changed_files, diff_sha256,
                health_state, evidence_source, hook_version, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                episode_id,
                event_type,
                ts,
                session_id,
                _bounded_event_int(row.get("turn_seq"), maximum=10**9),
                int(row.get("terminal") is True),
                outcome,
                _bounded_event_int(row.get("duration_ms"), maximum=10 * 365 * 24 * 60 * 60 * 1000),
                _bounded_event_int(row.get("changed_files"), maximum=10**7),
                diff_sha256,
                health_state,
                evidence_source,
                _bounded_event_int(row.get("hook_version"), maximum=10**6),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        inserted += int(cur.rowcount > 0)
    return inserted, rejected, seen_event_ids


def _ingest_compiles(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> tuple[int, int, set[str]]:
    inserted = 0
    rejected = 0
    seen_record_ids: set[str] = set()
    for row in rows:
        safe = sanitize_compile_telemetry_row(row)
        if safe is None:
            rejected += 1
            continue
        source_line = _int_or_none(row.get("__source_line"))
        record_id = _canonical_hash("cmp_", {"source_line": source_line, "payload": safe})
        seen_record_ids.add(record_id)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO compile_records (
                record_id, episode_id, ts, task_fingerprint, procedure,
                classifier_conf, art_label, envelope_bytes, compile_ms,
                injection_advice, cache_hit, agent_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                str(safe.get("episode_id") or ""),
                str(safe["ts"]),
                str(safe.get("task_fingerprint") or ""),
                str(safe.get("procedure") or "unknown"),
                _float_or_none(safe.get("classifier_conf")),
                str(safe.get("art_label") or "unknown"),
                _int_or_none(safe.get("envelope_bytes")),
                _float_or_none(safe.get("compile_ms")),
                str(safe.get("injection_advice") or "unknown"),
                int(safe.get("cache_hit") is True),
                str(safe.get("agent_mode") or "unknown"),
            ),
        )
        inserted += int(cur.rowcount > 0)
    return inserted, rejected, seen_record_ids


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        number = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if abs(number) <= 10**12 else None


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def materialize_ledger(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).resolve()
    roam_dir = root_path / ".roam"
    database = roam_dir / LEDGER_DB_NAME
    state = _prepare_ledger_directory(database)
    with pinned_owner_only_directory(state), _exclusive_ledger_materialization(database):
        orphan_temps_removed, orphan_temps_retained = _cleanup_orphaned_ledger_temps(database)
        live_event_rows, invalid_live_events = _read_jsonl(
            roam_dir / EVENT_LOG_NAME,
            label="live episode log",
            max_bytes=MAX_EVENT_LOG_BYTES,
        )
        transcript_event_rows, invalid_transcript_events = _read_jsonl(
            roam_dir / TRANSCRIPT_EVENT_LOG_NAME,
            label="transcript episode snapshot",
            max_bytes=MAX_TRANSCRIPT_EVENT_LOG_BYTES,
        )
        compile_rows, invalid_compiles = _read_jsonl(
            roam_dir / COMPILE_LOG_NAME,
            label="compile telemetry log",
            max_bytes=MAX_COMPILE_LOG_BYTES,
        )
        for row in live_event_rows:
            row.setdefault("evidence_source", "live_hook")
        for row in transcript_event_rows:
            row["evidence_source"] = "transcript_backfill"
        event_rows = [*live_event_rows, *transcript_event_rows]
        invalid_events = invalid_live_events + invalid_transcript_events
        # Stale sidecars from the former in-place ledger can contain historical
        # rows. A pathname unlink cannot be made identity-conditional on every
        # supported platform, so fail closed instead of deleting a raced
        # replacement. The new projection never creates these sidecars.
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(f"{database}{suffix}")
            if os.path.lexists(sidecar):
                raise SavingsLedgerSafetyError(f"stale savings ledger sidecar requires explicit removal: {sidecar}")

        descriptor, temporary = _new_ledger_temp(database)
        conn: sqlite3.Connection | None = None
        replaced = False
        source_generation = None
        prior_event_ids: set[str] = set()
        prior_compile_ids: set[str] = set()
        try:
            if not ensure_owner_only_file_descriptor(descriptor, temporary):
                raise SavingsLedgerSafetyError(f"savings ledger tempfile was not private at creation: {temporary}")
            conn = sqlite3.connect(temporary)
            conn.row_factory = sqlite3.Row
            _initialize_ledger(conn)
            event_written, event_rejected, current_event_ids = _ingest_events(conn, event_rows)
            compile_written, compile_rejected, current_compile_ids = _ingest_compiles(conn, compile_rows)
            conn.execute(
                "INSERT INTO ledger_meta(key, value) VALUES ('last_materialized_at', ?)",
                (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
            )
            conn.commit()
            integrity = str(conn.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                raise SavingsLedgerSafetyError(f"rebuilt savings ledger failed quick_check: {integrity}")
            totals = {
                "event_records": event_written,
                "compile_records": compile_written,
            }
            conn.close()
            conn = None
            os.fsync(descriptor)
            if os.fstat(descriptor).st_size > MAX_LEDGER_DB_BYTES:
                raise SavingsLedgerSafetyError(f"rebuilt savings ledger exceeds the {MAX_LEDGER_DB_BYTES}-byte limit")
            if not ensure_owner_only_file_descriptor(descriptor, temporary):
                raise SavingsLedgerSafetyError(f"savings ledger tempfile changed: {temporary}")
            source_generation = capture_file_generation(descriptor, max_bytes=MAX_LEDGER_DB_BYTES)
            os.close(descriptor)
            descriptor = -1

            def capture_prior_generation() -> None:
                nonlocal prior_event_ids, prior_compile_ids
                prior_event_ids, prior_compile_ids = _prior_ledger_ids(
                    database,
                    current_event_ids,
                    current_compile_ids,
                )
                _validate_ledger_artifact(database, parent_was_private=True)

            conditional_install_file(
                temporary,
                database,
                source_generation=source_generation,
                before_install=capture_prior_generation,
            )
            replaced = True
            open_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
            open_flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(database, open_flags)
            if file_descriptor_identity(descriptor) != source_generation.identity or not file_descriptor_is_owner_only(
                descriptor, database
            ):
                raise SavingsLedgerSafetyError(f"installed savings ledger changed: {database}")
            os.fsync(descriptor)
            if os.name != "nt":
                directory_fd = os.open(state, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            if capture_file_generation(
                descriptor, max_bytes=MAX_LEDGER_DB_BYTES
            ) != source_generation or not file_descriptor_is_owner_only(descriptor, database):
                raise SavingsLedgerSafetyError(f"installed savings ledger changed after durability sync: {database}")
        except BaseException:
            if conn is not None:
                conn.close()
            # Before installation, remove only the exact Windows tempfile
            # identity; POSIX retains it because there is no conditional
            # unlink-by-inode primitive. After installation, a later fsync or
            # validation failure must never delete the valid current ledger.
            if not replaced:
                if descriptor >= 0:
                    _unlink_if_same_file(temporary, descriptor)
                elif source_generation is not None:
                    delete_file_if_matches_identity(temporary, source_generation.identity)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        event_inserted = len(current_event_ids - prior_event_ids)
        compile_inserted = len(current_compile_ids - prior_compile_ids)
    return {
        "database": str(database),
        "event_rows_read": len(event_rows),
        "live_event_rows_read": len(live_event_rows),
        "transcript_event_rows_read": len(transcript_event_rows),
        "compile_rows_read": len(compile_rows),
        "event_rows_inserted": event_inserted,
        "compile_rows_inserted": compile_inserted,
        "invalid_event_rows": invalid_events + event_rejected,
        "invalid_compile_rows": invalid_compiles + compile_rejected,
        "orphan_temps_removed": orphan_temps_removed,
        "orphan_temps_retained": orphan_temps_retained,
        "ledger_db_byte_limit": MAX_LEDGER_DB_BYTES,
        **totals,
    }


def _known_answer_canaries() -> dict[str, Any]:
    """Run tiny deterministic fixtures through the classification assumptions."""
    fixtures = [
        {
            "name": "clean_no_edit_terminal",
            "events": [
                {"event_type": "prompt_submitted", "terminal": False},
                {"event_type": "stop_decision", "terminal": True, "outcome": "no_edit"},
            ],
            "expected": ("no_edit", True),
        },
        {
            "name": "blocked_then_completed",
            "events": [
                {"event_type": "prompt_submitted", "terminal": False},
                {"event_type": "stop_decision", "terminal": False, "outcome": "verification_blocked"},
                {"event_type": "stop_continuation", "terminal": True, "outcome": "continued_after_block"},
            ],
            "expected": ("continued_after_block", True),
        },
        {
            "name": "degraded_verify_terminal",
            "events": [
                {"event_type": "prompt_submitted", "terminal": False},
                {"event_type": "stop_decision", "terminal": True, "outcome": "verify_unavailable"},
            ],
            "expected": ("verify_unavailable", True),
        },
    ]
    failures: list[str] = []
    for fixture in fixtures:
        terminal = [event for event in fixture["events"] if event.get("terminal")]
        observed = (
            str(terminal[-1].get("outcome") or "") if terminal else "",
            bool(terminal),
        )
        if observed != fixture["expected"]:
            failures.append(fixture["name"])
    return {
        "state": "passed" if not failures else "failed",
        "passed": len(fixtures) - len(failures),
        "total": len(fixtures),
        "failures": failures,
    }


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def _episode_health_state(
    episode_events: list[dict[str, Any]],
    start: dict[str, Any],
    terminal: dict[str, Any] | None,
) -> str:
    states = [str(row.get("health_state") or "unknown") for row in episode_events]
    if "verification_failed" in states:
        return "verification_failed"
    if "verification_passed" in states:
        return "verification_passed"
    return str((terminal or start).get("health_state") or "unknown")


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = loads_bounded(
            row.get("payload_json") or "{}",
            object_pairs_hook=strict_json_object_pairs,
        )
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _episode_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    events = [dict(row) for row in conn.execute("SELECT * FROM episode_events ORDER BY ts, event_id")]
    compiles = [dict(row) for row in conn.execute("SELECT * FROM compile_records ORDER BY ts, record_id")]
    events_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    compiles_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        events_by_episode[row["episode_id"]].append(row)
    for row in compiles:
        if row["episode_id"]:
            compiles_by_episode[row["episode_id"]].append(row)

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for episode_id, episode_events in events_by_episode.items():
        starts = [row for row in episode_events if row["event_type"] == "prompt_submitted"]
        if not starts:
            continue
        start = starts[0]
        start_payload = _payload(start)
        start_ts = _parse_ts(start["ts"])
        terminals = [row for row in episode_events if row["terminal"]]
        terminal = terminals[-1] if terminals else None
        terminal_payload = _payload(terminal or {})
        age_s = (now - start_ts).total_seconds() if start_ts else None
        eligible = age_s is not None and age_s >= TERMINAL_GRACE_SECONDS
        compile_row = compiles_by_episode.get(episode_id, [None])[-1]
        out.append(
            {
                "episode_id": episode_id,
                "session_id": str(start.get("session_id") or ""),
                "started_at": start["ts"],
                "eligible": eligible,
                "terminal": terminal is not None,
                "outcome": str((terminal or {}).get("outcome") or ""),
                "duration_ms": (terminal or {}).get("duration_ms"),
                "changed_files": (terminal or {}).get("changed_files"),
                "health_state": _episode_health_state(episode_events, start, terminal),
                "evidence_source": str(start.get("evidence_source") or "live_hook"),
                "transcript_source": str(start_payload.get("transcript_source") or ""),
                "hook_version": start.get("hook_version"),
                "compile_expected": bool(start_payload.get("compile_expected", True)),
                "compile_joined": compile_row is not None,
                "project_id": str(start_payload.get("project_id") or ""),
                "project_identity_basis": str(start_payload.get("project_identity_basis") or ""),
                "intent_archetypes": (
                    start_payload.get("intent_archetypes")
                    if isinstance(start_payload.get("intent_archetypes"), list)
                    else []
                ),
                "intent_simhash64": str(start_payload.get("intent_simhash64") or ""),
                "prompt_hmac_sha256": str(start_payload.get("prompt_hmac_sha256") or ""),
                "prompt_tokens_bucket": (_int_or_none(start_payload.get("prompt_tokens_bucket")) or 0),
                "trajectory_fingerprint": str(terminal_payload.get("trajectory_fingerprint") or ""),
                "trajectory_template": str(terminal_payload.get("trajectory_template") or ""),
                "phase_sequence_template": str(terminal_payload.get("phase_sequence_template") or ""),
                "command_sequence_fingerprint": str(terminal_payload.get("command_sequence_fingerprint") or ""),
                "command_sequence_template": str(terminal_payload.get("command_sequence_template") or ""),
                "shell_templates": (
                    terminal_payload.get("shell_templates")
                    if isinstance(terminal_payload.get("shell_templates"), dict)
                    else {}
                ),
                "shell_ngrams": (
                    terminal_payload.get("shell_ngrams")
                    if isinstance(terminal_payload.get("shell_ngrams"), dict)
                    else {}
                ),
                "tool_ngrams": (
                    terminal_payload.get("tool_ngrams") if isinstance(terminal_payload.get("tool_ngrams"), dict) else {}
                ),
                "phase_ngrams": (
                    terminal_payload.get("phase_ngrams")
                    if isinstance(terminal_payload.get("phase_ngrams"), dict)
                    else {}
                ),
                "friction": (
                    terminal_payload.get("friction") if isinstance(terminal_payload.get("friction"), dict) else {}
                ),
                "shell_template_outcomes": (
                    terminal_payload.get("shell_template_outcomes")
                    if isinstance(terminal_payload.get("shell_template_outcomes"), dict)
                    else {}
                ),
                "phase_outcomes": (
                    terminal_payload.get("phase_outcomes")
                    if isinstance(terminal_payload.get("phase_outcomes"), dict)
                    else {}
                ),
                "command_class_outcomes": (
                    terminal_payload.get("command_class_outcomes")
                    if isinstance(terminal_payload.get("command_class_outcomes"), dict)
                    else {}
                ),
                "tool_calls": _int_or_none(terminal_payload.get("tool_calls")) or 0,
                "tool_errors": _int_or_none(terminal_payload.get("tool_errors")) or 0,
                "tool_result_bytes_bucket": (_int_or_none(terminal_payload.get("tool_result_bytes_bucket")) or 0),
                "assistant_messages": (_int_or_none(terminal_payload.get("assistant_messages")) or 0),
                "time_to_first_tool_ms": _int_or_none(terminal_payload.get("time_to_first_tool_ms")),
                "time_to_first_edit_ms": _int_or_none(terminal_payload.get("time_to_first_edit_ms")),
                "edit_actions": _int_or_none(terminal_payload.get("edit_actions")) or 0,
                "verification_attempts": (_int_or_none(terminal_payload.get("verification_attempts")) or 0),
                "verification_failures": (_int_or_none(terminal_payload.get("verification_failures")) or 0),
                "input_tokens": _int_or_none(terminal_payload.get("input_tokens")) or 0,
                "output_tokens": _int_or_none(terminal_payload.get("output_tokens")) or 0,
                "cached_input_tokens": (_int_or_none(terminal_payload.get("cached_input_tokens")) or 0),
                "cache_creation_tokens": (_int_or_none(terminal_payload.get("cache_creation_tokens")) or 0),
                "reasoning_output_tokens": (_int_or_none(terminal_payload.get("reasoning_output_tokens")) or 0),
                "correction_after": bool(terminal_payload.get("correction_after")),
                "task_fingerprint": str((compile_row or {}).get("task_fingerprint") or ""),
                "procedure": str((compile_row or {}).get("procedure") or ""),
                "art_label": str((compile_row or {}).get("art_label") or ""),
                "classifier_conf": (compile_row or {}).get("classifier_conf"),
            }
        )
    return out


def _intervention_evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summarize pre-assignment/post-observation joins without estimating effects."""
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT event_id, episode_id, event_type, ts, session_id, payload_json
            FROM episode_events
            WHERE event_type IN ('intervention_assignment', 'intervention_observation')
            ORDER BY ts, event_id
            """
        )
    ]
    terminal_times = {
        str(row[0]): _parse_ts(row[1])
        for row in conn.execute(
            """
            SELECT episode_id, MAX(ts)
            FROM episode_events
            WHERE terminal=1
            GROUP BY episode_id
            """
        )
    }
    assignments: dict[tuple[str, str, str], dict[str, Any]] = {}
    observations: dict[tuple[str, str, str], dict[str, Any]] = {}
    invalid_rows = 0
    duplicate_rows = 0
    for row in rows:
        payload = _payload(row)
        intervention_id = str(payload.get("intervention_id") or "").strip()
        version = str(payload.get("intervention_version") or "").strip()
        key = (str(row.get("episode_id") or ""), intervention_id, version)
        if not all(key):
            invalid_rows += 1
            continue
        if row["event_type"] == "intervention_assignment":
            assignment = str(payload.get("assignment") or "")
            rule_version = str(payload.get("eligibility_rule_version") or "")
            assignment_cluster = str(payload.get("assignment_cluster") or "")
            eligible_transition = payload.get("eligible_transition")
            if (
                assignment not in _INTERVENTION_ASSIGNMENTS
                or not rule_version
                or not assignment_cluster
                or assignment_cluster != str(row.get("session_id") or "")
                or eligible_transition is not True
            ):
                invalid_rows += 1
                continue
            if key in assignments:
                duplicate_rows += 1
            assignments[key] = {
                **payload,
                "session_id": str(row.get("session_id") or ""),
                "assigned_at": str(row.get("ts") or ""),
            }
            continue
        transition_count = _int_or_none(payload.get("downstream_transition_count"))
        if transition_count is None or transition_count < 0:
            invalid_rows += 1
            continue
        if key in observations:
            duplicate_rows += 1
        observations[key] = {
            **payload,
            "observed_at": str(row.get("ts") or ""),
        }

    grouped: dict[tuple[str, str], list[tuple[tuple[str, str, str], dict[str, Any]]]] = defaultdict(list)
    for key, assignment in assignments.items():
        grouped[(key[1], key[2])].append((key, assignment))

    experiments = []
    for (intervention_id, version), assigned_rows in grouped.items():
        arm_counts = Counter(str(assignment.get("assignment") or "") for _, assignment in assigned_rows)
        session_arms: dict[str, set[str]] = defaultdict(set)
        joined = 0
        measured = 0
        delivered = 0
        adopted = 0
        ordering_violations = 0
        for key, assignment in assigned_rows:
            session_arms[str(assignment.get("assignment_cluster") or "")].add(str(assignment.get("assignment") or ""))
            observation = observations.get(key)
            assigned_at = _parse_ts(assignment.get("assigned_at"))
            observed_at = _parse_ts((observation or {}).get("observed_at"))
            terminal_at = terminal_times.get(key[0])
            if observation is None or terminal_at is None:
                continue
            if assigned_at is None or observed_at is None or assigned_at >= observed_at or observed_at > terminal_at:
                ordering_violations += 1
                continue
            joined += 1
            measured += int(_int_or_none(observation.get("downstream_transition_count")) is not None)
            delivered += int(observation.get("delivered") is True)
            adopted += int(observation.get("adopted") is True)
        contaminated_sessions = sum(len(arms) > 1 for session, arms in session_arms.items() if session)
        control = arm_counts["control"]
        exposed = arm_counts["exposed"]
        join_coverage = _pct(joined, len(assigned_rows))
        if ordering_violations:
            readiness = "event_ordering_violation"
        elif control < 30 or exposed < 30:
            readiness = "insufficient_sample"
        elif (join_coverage or 0.0) < MIN_COVERAGE_PCT:
            readiness = "incomplete_observation_join"
        elif contaminated_sessions:
            readiness = "assignment_contamination"
        else:
            readiness = "ready_for_preregistered_effect_estimation"
        experiments.append(
            {
                "intervention_id": intervention_id,
                "intervention_version": version,
                "assigned_episodes": len(assigned_rows),
                "assignment_counts": dict(sorted(arm_counts.items())),
                "terminal_observation_joins": joined,
                "observation_join_coverage_pct": join_coverage,
                "transition_measurement_coverage_pct": _pct(measured, len(assigned_rows)),
                "delivery_rate_pct": _pct(delivered, exposed),
                "adoption_rate_pct": _pct(adopted, exposed),
                "contaminated_sessions": contaminated_sessions,
                "event_ordering_violations": ordering_violations,
                "promotion_readiness": readiness,
                "effectiveness_state": "unmeasured",
                "causal_savings_claimed": False,
            }
        )
    experiments.sort(
        key=lambda row: (
            -row["assigned_episodes"],
            row["intervention_id"],
            row["intervention_version"],
        )
    )
    joined_total = sum(row["terminal_observation_joins"] for row in experiments)
    assignment_total = sum(row["assigned_episodes"] for row in experiments)
    ordering_violations = sum(row["event_ordering_violations"] for row in experiments)
    return {
        "summary": {
            "state": "instrumented" if assignment_total else "not_instrumented",
            "assignment_events": assignment_total,
            "terminal_observation_joins": joined_total,
            "observation_join_coverage_pct": _pct(joined_total, assignment_total),
            "invalid_intervention_events": invalid_rows,
            "duplicate_intervention_events": duplicate_rows,
            "event_ordering_violations": ordering_violations,
            "causal_savings_claimed": False,
        },
        "experiments": experiments,
        "event_contract": {
            "required_pair": [
                "intervention_assignment",
                "intervention_observation",
            ],
            "assignment_must_precede_delivery": True,
            "assignment_unit": "session_id",
            "analysis_population": "intent_to_treat",
        },
    }


def _repeat_candidates(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        if episode["task_fingerprint"] and episode["terminal"]:
            grouped[episode["task_fingerprint"]].append(episode)
    candidates: list[dict[str, Any]] = []
    for task_fingerprint, rows in grouped.items():
        if len(rows) < 3:
            continue
        outcomes = Counter(row["outcome"] or "unknown" for row in rows)
        duration_values = [
            int(row["duration_ms"])
            for row in rows
            if isinstance(row.get("duration_ms"), (int, float)) and row["duration_ms"] >= 0
        ]
        health_known = sum(row["health_state"] in VALIDATED_HEALTH_STATES for row in rows)
        candidates.append(
            {
                "task_fingerprint": task_fingerprint,
                "procedure": rows[-1]["procedure"],
                "episodes": len(rows),
                "outcomes": dict(outcomes.most_common()),
                "observed_wall_ms": sum(duration_values),
                "health_context_pct": _pct(health_known, len(rows)),
                "evidence_status": (
                    "candidate" if health_known == len(rows) else "candidate_only_health_context_missing"
                ),
            }
        )
    candidates.sort(
        key=lambda row: (
            -row["observed_wall_ms"],
            -row["episodes"],
            row["task_fingerprint"],
        )
    )
    return candidates[:20]


def _interpret_historical_pattern(kind: str, pattern: str) -> dict[str, str]:
    lowered = pattern.lower()
    if "roam " in lowered and any(token in lowered for token in (" | python", " | jq", "head -c")):
        return {
            "pattern_family": "roam_projection_postprocessing",
            "candidate_disposition": "projection_gap",
            "priority": "high",
            "automation_hypothesis": (
                "Return the exact compact projection from Roam so agents stop parsing or truncating it in shell"
            ),
            "existing_surface": "roam fetch-handle --jq or a command-native --summary projection",
            "next_experiment": "Add one native projection and measure disappearance of the post-processing shell n-gram",
        }
    if "roam " in lowered and "--help" in lowered:
        return {
            "pattern_family": "command_discoverability",
            "candidate_disposition": "discoverability_gap",
            "priority": "medium",
            "automation_hypothesis": (
                "Compile the relevant command contract before execution so agents stop opening help repeatedly"
            ),
            "existing_surface": "roam explain-command <command>",
            "next_experiment": "Inject the selected command signature and compare follow-up --help calls",
        }
    if "rg " in lowered and "sed -n" in lowered:
        return {
            "pattern_family": "search_then_slice",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Collapse search plus manual line slicing into one ranked bounded-context result"
            ),
            "existing_surface": "roam grep <pattern> --group-by symbol or roam retrieve <task>",
            "next_experiment": "Route a held-out search cohort through one context-enriched call",
        }
    if lowered.count("sed -n") >= 2 or ("nl -ba" in lowered and "sed -n" in lowered):
        return {
            "pattern_family": "repeated_code_slicing",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Replace repeated line-window reads with one location-aware structural context call"
            ),
            "existing_surface": "roam at FILE:LINE or roam context --for-file PATH",
            "next_experiment": "Compare repeated slice count after compiling roam at/context into the first turn",
        }
    if "git diff" in lowered and any(token in lowered for token in ("sed -n", "rg ", "pytest", "roam verify")):
        return {
            "pattern_family": "diff_drilldown",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Bundle changed hunks, structural blast radius, and verification targets into one review envelope"
            ),
            "existing_surface": "roam diff --full and git diff | roam critique",
            "next_experiment": "Compile one diff-review envelope and measure follow-up git/sed/rg calls",
        }
    if "py_compile" in lowered and "pytest" in lowered:
        return {
            "pattern_family": "verification_ladder",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Turn syntax checking and focused test selection into one deterministic verification recipe"
            ),
            "existing_surface": "roam syntax-check plus roam test-impact",
            "next_experiment": "Emit one executable verification recipe and compare retries and failed checks",
        }
    if "git status" in lowered and kind != "shell_template":
        return {
            "pattern_family": "workspace_orientation",
            "candidate_disposition": "compiler_context_candidate",
            "priority": "medium",
            "automation_hypothesis": (
                "Include the minimal dirty-tree state in compiled context so orientation does not require a shell turn"
            ),
            "existing_surface": "roam brief plus compile envelope facts",
            "next_experiment": "Add a compact dirty-tree fact and measure follow-up git status calls",
        }
    if kind == "tool_ngram" and "edit" in lowered and "shell" in lowered:
        return {
            "pattern_family": "edit_shell_loop",
            "candidate_disposition": "measurement_refinement",
            "priority": "medium",
            "automation_hypothesis": (
                "Distinguish verification shells from exploration shells before proposing loop automation"
            ),
            "existing_surface": "prospective hook verification outcomes",
            "next_experiment": "Join shell command class to live edit outcomes before promoting a recipe",
        }
    if lowered.startswith("roam ") and all(token not in lowered for token in ("=>", "|", "&&", ";")):
        return {
            "pattern_family": "already_compiled_operation",
            "candidate_disposition": "already_boring",
            "priority": "low",
            "automation_hypothesis": "Observe adoption and outcome quality; no new wrapper is justified by frequency alone",
            "existing_surface": pattern,
            "next_experiment": "Compare success and fallback rates before changing the command",
        }
    return {
        "pattern_family": "unclassified_repetition",
        "candidate_disposition": "candidate_only",
        "priority": "medium" if kind in {"shell_ngram", "shell_sequence"} else "low",
        "automation_hypothesis": "Inspect the repeated structure and identify the invariant input/output contract",
        "existing_surface": "",
        "next_experiment": "Label a held-out sample and test one deterministic replacement",
    }


def _historical_candidates(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank repeated transcript patterns without promoting them to savings facts."""
    groups: dict[str, dict[str, list[tuple[dict[str, Any], int]]]] = {
        "shell_ngram": defaultdict(list),
        "shell_sequence": defaultdict(list),
        "tool_ngram": defaultdict(list),
        "shell_template": defaultdict(list),
        "tool_trajectory": defaultdict(list),
    }
    for episode in episodes:
        if not episode["terminal"]:
            continue
        for source_field, kind in (
            ("shell_ngrams", "shell_ngram"),
            ("tool_ngrams", "tool_ngram"),
            ("shell_templates", "shell_template"),
        ):
            for pattern, count in episode.get(source_field, {}).items():
                try:
                    occurrences = int(count)
                except (TypeError, ValueError):
                    continue
                if pattern and occurrences > 0:
                    groups[kind][str(pattern)].append((episode, occurrences))
        trajectory = str(episode.get("trajectory_template") or "")
        if trajectory:
            groups["tool_trajectory"][trajectory].append((episode, 1))
        command_sequence = str(episode.get("command_sequence_template") or "")
        if command_sequence:
            groups["shell_sequence"][command_sequence].append((episode, 1))

    ranked_by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind, patterns in groups.items():
        ranked: list[dict[str, Any]] = []
        for pattern, records in patterns.items():
            if len(records) < 3:
                continue
            if len(pattern) > 260 or pattern.count("<EXEC>") > 2:
                continue
            if kind == "shell_sequence" and "=>" not in pattern and "×" not in pattern:
                continue
            if kind == "tool_ngram":
                families = {part.strip() for part in pattern.split("=>")}
                if len(families) <= 1:
                    continue
            if kind == "tool_trajectory":
                families = {
                    re.sub(r"\*\d+$", "", part.strip())
                    for part in pattern.split(">")
                    if part.strip() and part.strip() != "<MORE>"
                }
                if len(families) <= 1:
                    continue
            rows = [episode for episode, _count in records]
            outcomes = Counter(row["outcome"] or "unknown" for row in rows)
            associated_tokens = sum(normalized_episode_tokens(row) for row in rows)
            ranked.append(
                {
                    "kind": kind,
                    "pattern": pattern,
                    "episodes": len(rows),
                    "occurrences": sum(count for _episode, count in records),
                    "associated_episode_wall_ms": sum(
                        int(row["duration_ms"])
                        for row in rows
                        if isinstance(row.get("duration_ms"), (int, float)) and row["duration_ms"] >= 0
                    ),
                    "associated_tool_calls": sum(int(row.get("tool_calls") or 0) for row in rows),
                    "associated_tokens": associated_tokens,
                    "correction_pct": _pct(
                        sum(bool(row.get("correction_after")) for row in rows),
                        len(rows),
                    ),
                    "outcomes": dict(outcomes.most_common()),
                    "evidence_status": "historical_candidate_only",
                    **_interpret_historical_pattern(kind, pattern),
                }
            )
        ranked.sort(
            key=lambda row: (
                -row["associated_episode_wall_ms"],
                -row["episodes"],
                -row["occurrences"],
                row["pattern"],
            )
        )
        ranked_by_kind[kind] = ranked

    priority = (
        "shell_ngram",
        "shell_sequence",
        "tool_ngram",
        "shell_template",
        "tool_trajectory",
    )
    interleaved: list[dict[str, Any]] = []
    for rank in range(12):
        for kind in priority:
            rows = ranked_by_kind.get(kind, [])
            if rank < len(rows):
                interleaved.append(rows[rank])
    return interleaved


def analyze_ledger(root: str | Path) -> dict[str, Any]:
    materialization = materialize_ledger(root)
    db_path = Path(materialization["database"])
    with _open_ledger(db_path) as conn:
        episodes = _episode_rows(conn)
        event_counts = dict(
            conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM episode_events GROUP BY event_type ORDER BY n DESC"
            ).fetchall()
        )
        first_start_row = conn.execute(
            """
            SELECT MIN(ts) FROM episode_events
            WHERE event_type='prompt_submitted' AND evidence_source='live_hook'
            """
        ).fetchone()
        first_start = first_start_row[0] if first_start_row else None
        compile_window = []
        first_start_time = _parse_ts(first_start)
        if first_start_time is not None:
            # Compile telemetry intentionally coarsens timestamps to the hour.
            # Comparing those buckets with an exact prompt timestamp would
            # exclude every compile from the overlapping hour and could make
            # identity coverage look better than it is. Include the complete
            # overlapping bucket; any pre-prompt rows in that bucket make the
            # gate conservatively harder to satisfy, never easier.
            compile_window_start = (
                first_start_time.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                .isoformat()
                .replace("+00:00", "Z")
            )
            compile_window = [
                dict(row)
                for row in conn.execute(
                    "SELECT episode_id, agent_mode, ts FROM compile_records WHERE ts >= ?",
                    (compile_window_start,),
                )
            ]
        intervention_evidence = _intervention_evidence(conn)

    canaries = _known_answer_canaries()
    prospective = [episode for episode in episodes if episode["evidence_source"] == "live_hook"]
    historical = [episode for episode in episodes if episode["evidence_source"] == "transcript_backfill"]
    eligible = [episode for episode in prospective if episode["eligible"]]
    terminal = [episode for episode in eligible if episode["terminal"]]
    compile_expected = [episode for episode in eligible if episode["compile_expected"]]
    compile_joined = [episode for episode in compile_expected if episode["compile_joined"]]
    fully_joined = [
        episode
        for episode in eligible
        if episode["terminal"] and (episode["compile_joined"] or not episode["compile_expected"])
    ]
    health_expected = [
        episode
        for episode in eligible
        if isinstance(episode.get("changed_files"), int) and episode["changed_files"] > 0
    ]
    health_known = [episode for episode in health_expected if episode["health_state"] in VALIDATED_HEALTH_STATES]
    current_hook = [
        episode
        for episode in eligible
        if isinstance(episode.get("hook_version"), int) and episode["hook_version"] >= MIN_LIVE_HOOK_VERSION
    ]
    repeat_expected = [episode for episode in compile_expected if episode["terminal"] and episode["compile_joined"]]
    repeat_identified = [episode for episode in repeat_expected if episode.get("task_fingerprint")]
    production_compiles = [
        row
        for row in compile_window
        if (row.get("agent_mode") or "unknown")
        not in {"bench", "corpus", "trace", "envelope_diff", "compile_cache_build", "test"}
    ]
    identified_compiles = [
        row
        for row in production_compiles
        if row.get("episode_id")
        and first_start_time is not None
        and (row_time := _parse_ts(row.get("ts"))) is not None
        and row_time > first_start_time
    ]

    coverage = {
        "prompt_starts": len(episodes),
        "prospective_prompt_starts": len(prospective),
        "historical_prompt_starts": len(historical),
        "eligible_prompt_starts": len(eligible),
        "terminal_outcomes": len(terminal),
        "compile_expected_episodes": len(compile_expected),
        "compile_joined_episodes": len(compile_joined),
        "fully_joined_episodes": len(fully_joined),
        "health_context_episodes": len(health_known),
        "health_expected_episodes": len(health_expected),
        "current_hook_episodes": len(current_hook),
        "repeat_identity_episodes": len(repeat_identified),
        "repeat_expected_episodes": len(repeat_expected),
        "terminal_coverage_pct": _pct(len(terminal), len(eligible)),
        "episode_join_coverage_pct": _pct(len(fully_joined), len(eligible)),
        "health_context_coverage_pct": _pct(len(health_known), len(health_expected)),
        "hook_version_coverage_pct": _pct(len(current_hook), len(eligible)),
        "compile_identity_coverage_pct": _pct(len(identified_compiles), len(production_compiles)),
        "repeat_identity_coverage_pct": _pct(len(repeat_identified), len(repeat_expected)),
        **SAVINGS_COVERAGE_DEFINITIONS,
    }

    integrity_clean = materialization["invalid_event_rows"] == 0 and materialization["invalid_compile_rows"] == 0
    measurement_admissible = (
        canaries["state"] == "passed"
        and integrity_clean
        and len(eligible) >= MIN_ADMISSIBLE_EPISODES
        and (coverage["terminal_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and (coverage["episode_join_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and (coverage["compile_identity_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and (coverage["hook_version_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
    )
    policy_admissible = (
        measurement_admissible
        and bool(health_expected)
        and (coverage["health_context_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and bool(repeat_expected)
        and (coverage["repeat_identity_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
    )

    if not episodes:
        state = "not_initialized"
        verdict = "Savings ledger not initialized — install refreshed hooks and complete agent episodes"
    elif not measurement_admissible:
        state = "insufficient_evidence"
        if not integrity_clean:
            verdict = "Savings claims withheld — malformed telemetry rows failed the integrity gate"
        else:
            verdict = (
                f"Savings claims withheld — {len(fully_joined)}/{len(eligible)} eligible prospective "
                "episodes carry compile and terminal records"
            )
    elif not policy_admissible:
        state = "measurement_ready"
        if (coverage["repeat_identity_coverage_pct"] or 0) < MIN_COVERAGE_PCT:
            verdict = "Episode joins are measurement-ready; routing savings remain gated on keyed repeat identity"
        else:
            verdict = "Episode joins are measurement-ready; routing savings remain gated on execution-health context"
    else:
        state = "policy_ready"
        verdict = "Episode ledger is admissible for outcome-conditioned savings experiments"

    cleanup_degraded = materialization["orphan_temps_retained"] > 0
    if cleanup_degraded:
        verdict += (
            f"; {materialization['orphan_temps_retained']} private orphan tempfiles retained "
            "because identity-bound deletion is unavailable"
        )

    candidates = _repeat_candidates(eligible) if measurement_admissible else []
    historical_candidates = _historical_candidates(historical)
    procedure_atlas = build_procedure_atlas(historical)
    return {
        "summary": {
            "verdict": verdict,
            "state": state,
            "partial_success": state != "policy_ready" or cleanup_degraded,
            "measurement_admissible": measurement_admissible,
            "policy_admissible": policy_admissible,
            "integrity_clean": integrity_clean,
            "north_star": "durable successful outcomes per unit of constrained resource",
        },
        "coverage": coverage,
        "sensor_canaries": canaries,
        "event_distribution": event_counts,
        "outcome_distribution": dict(Counter(ep["outcome"] or "open" for ep in episodes).most_common()),
        "repeat_candidates": candidates,
        "historical_candidates": historical_candidates,
        "procedure_atlas": procedure_atlas,
        "intervention_evidence": intervention_evidence,
        "materialization": materialization,
        "thresholds": {
            "minimum_eligible_episodes": MIN_ADMISSIBLE_EPISODES,
            "minimum_coverage_pct": MIN_COVERAGE_PCT,
            "terminal_grace_seconds": TERMINAL_GRACE_SECONDS,
        },
    }


def _aggregate_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _aggregate_percentage(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 100.0:
        return None
    return number


def _aggregate_dict_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def aggregate_savings_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project a rich local analysis onto the browser-safe SavingsAggregate contract.

    The projection reconstructs every string from closed producer vocabulary and
    derives only counts from transcript-backed rows. It never copies titles,
    patterns, commands, prompts, responses, paths, identifiers, or per-episode
    records into the returned value.
    """
    source = result if isinstance(result, dict) else {}
    source_summary = source.get("summary") if isinstance(source.get("summary"), dict) else {}
    source_state = str(source_summary.get("state") or "")
    state = source_state if source_state in _AGGREGATE_SUMMARY_VERDICTS else "unknown"

    source_coverage = source.get("coverage") if isinstance(source.get("coverage"), dict) else {}
    coverage = {
        field: _aggregate_nonnegative_int(source_coverage.get(field)) for field in _AGGREGATE_COVERAGE_COUNT_FIELDS
    }
    coverage.update(
        {field: _aggregate_percentage(source_coverage.get(field)) for field in _AGGREGATE_COVERAGE_PERCENT_FIELDS}
    )
    coverage.update(SAVINGS_COVERAGE_DEFINITIONS)

    source_canaries = source.get("sensor_canaries") if isinstance(source.get("sensor_canaries"), dict) else {}
    canaries_passed = _aggregate_nonnegative_int(source_canaries.get("passed"))
    canaries_total = _aggregate_nonnegative_int(source_canaries.get("total"))
    canary_state = str(source_canaries.get("state") or "")
    if canary_state not in {"passed", "failed"}:
        canary_state = "unknown"
    if canaries_total and canaries_passed > canaries_total:
        canaries_passed = canaries_total
        canary_state = "failed"

    atlas = source.get("procedure_atlas") if isinstance(source.get("procedure_atlas"), dict) else {}
    opportunities = _aggregate_dict_rows(atlas.get("opportunities"))
    failure_signatures = _aggregate_dict_rows(atlas.get("failure_signatures"))
    recovery_targets = _aggregate_dict_rows(atlas.get("recovery_targets"))
    intervention_mappings = _aggregate_dict_rows(atlas.get("intervention_mappings"))

    declaration_states = {name: 0 for name in _AGGREGATE_DECLARATION_STATES}
    for row in intervention_mappings:
        raw_state = str(row.get("declaration_state") or "")
        declaration_state = raw_state if raw_state in declaration_states else "unknown"
        declaration_states[declaration_state] += 1

    intervention_evidence = (
        source.get("intervention_evidence") if isinstance(source.get("intervention_evidence"), dict) else {}
    )
    experiments = _aggregate_dict_rows(intervention_evidence.get("experiments"))
    assignment_states = {name: 0 for name in _AGGREGATE_ASSIGNMENT_STATES}
    for experiment in experiments:
        counts = experiment.get("assignment_counts")
        if not isinstance(counts, dict):
            continue
        for raw_state, raw_count in counts.items():
            count = _aggregate_nonnegative_int(raw_count)
            assignment_state = str(raw_state) if str(raw_state) in assignment_states else "unknown"
            assignment_states[assignment_state] += count

    return {
        "aggregate_schema": SAVINGS_AGGREGATE_SCHEMA,
        "aggregate_schema_version": SAVINGS_AGGREGATE_SCHEMA_VERSION,
        "summary": {
            "verdict": _AGGREGATE_SUMMARY_VERDICTS[state],
            "state": state,
            "partial_success": state != "policy_ready",
            "measurement_admissible": source_summary.get("measurement_admissible") is True,
            "policy_admissible": source_summary.get("policy_admissible") is True,
            "integrity_clean": source_summary.get("integrity_clean") is True,
            "north_star": "durable successful outcomes per unit of constrained resource",
            "causal_savings_claimed": False,
        },
        "coverage": coverage,
        "sensor_canaries": {
            "state": canary_state,
            "passed": canaries_passed,
            "total": canaries_total,
        },
        "opportunity_counts": {
            "repeated_live_candidates": len(_aggregate_dict_rows(source.get("repeat_candidates"))),
            "historical_pattern_candidates": len(_aggregate_dict_rows(source.get("historical_candidates"))),
            "ranked_work_opportunities": len(opportunities),
            "failure_signatures": len(failure_signatures),
            "recovery_targets": len(recovery_targets),
            "intervention_mappings": len(intervention_mappings),
        },
        "intervention_state": {
            "declaration_states": declaration_states,
            "assignments": sum(assignment_states.values()),
            "experiments": len(experiments),
            "assignment_states": assignment_states,
            "causal_savings_claimed": False,
        },
        "privacy": {
            "aggregate_only": True,
            "raw_transcripts_returned": False,
            "prompt_or_response_text_returned": False,
            "shell_command_text_returned": False,
            "source_or_path_text_returned": False,
            "per_episode_data_returned": False,
            "identifiers_returned": False,
        },
    }
