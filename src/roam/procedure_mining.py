"""Mine private transcript telemetry for general agent-work opportunities.

The module ranks observed friction and repeated intent clusters.  Its counters
are deliberately named ``associated_*`` or ``addressable_*``: historical
telemetry can identify work that an intervention may remove, but it cannot
establish causal savings.
"""

from __future__ import annotations

import ast
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Callable


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _friction(episode: dict[str, Any], key: str) -> int:
    values = episode.get("friction")
    return _int(values.get(key)) if isinstance(values, dict) else 0


_STRUCTURED_PROJECTION_RE = re.compile(
    r"(?:^|(?:&&|\|\||;)\s*)roam\b[^;]*"
    r"(?:\|\s*(?:jq|python(?:3)?|head|tail|sed|awk|cut|select-object)\b|head\s+-c\b)",
    re.IGNORECASE,
)


def _structured_projection_calls(episode: dict[str, Any]) -> int:
    recorded = _friction(episode, "structured_output_postprocess_calls")
    if recorded:
        return recorded
    templates = episode.get("shell_templates")
    if not isinstance(templates, dict):
        return 0
    return sum(_int(count) for template, count in templates.items() if _STRUCTURED_PROJECTION_RE.search(str(template)))


_NO_EDIT_EXPECTED_ARCHETYPES = frozenset({"review", "research", "document", "plan"})


def _high_tool_no_edit_transition(episode: dict[str, Any]) -> int:
    """Count long no-edit work only when the observed intent is not passive."""
    if _int(episode.get("edit_actions")) or _int(episode.get("tool_calls")) < 8:
        return 0
    archetypes = _archetype_set(episode)
    if not archetypes or archetypes.issubset(_NO_EDIT_EXPECTED_ARCHETYPES):
        return 0
    return 1


def normalized_episode_tokens(episode: dict[str, Any]) -> int:
    """Normalize provider token fields without double-counting cached input."""
    input_tokens = _int(episode.get("input_tokens"))
    output_tokens = _int(episode.get("output_tokens"))
    cached_tokens = _int(episode.get("cached_input_tokens"))
    cache_creation_tokens = _int(episode.get("cache_creation_tokens"))
    reasoning_tokens = _int(episode.get("reasoning_output_tokens"))
    source = str(episode.get("transcript_source") or "")
    if source == "claude":
        normalized_input = input_tokens + cached_tokens + cache_creation_tokens
    elif source == "codex":
        normalized_input = input_tokens
    else:
        normalized_input = max(input_tokens, cached_tokens + cache_creation_tokens)
    return normalized_input + output_tokens + reasoning_tokens


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def _median(values: list[int]) -> int | None:
    return int(median(values)) if values else None


@dataclass(frozen=True)
class _Opportunity:
    key: str
    title: str
    signal_definition: str
    intervention_shape: str
    counter: Callable[[dict[str, Any]], int]
    leverage: float
    precision: str = "direct"


_OPPORTUNITIES = (
    _Opportunity(
        "repeated_code_slicing",
        "Repeated code-window reconstruction",
        "Line-window inspection calls beyond the first call in one episode",
        "Return one location-aware structural context envelope",
        lambda ep: max(0, _friction(ep, "slice_calls") - 1),
        1.0,
    ),
    _Opportunity(
        "search_inspect_thrash",
        "Search and inspection thrash",
        "Observed search→inspect→search or inspect→search→inspect cycles",
        "Combine ranked search hits with bounded structural context",
        lambda ep: _friction(ep, "search_inspect_cycles"),
        1.0,
    ),
    _Opportunity(
        "exact_shell_replay",
        "Repeated shell-template execution",
        "Exact sanitized shell-template executions beyond the first execution",
        "Cache, parameterize, or compile the repeated deterministic operation",
        lambda ep: _friction(ep, "exact_shell_replays"),
        0.9,
    ),
    _Opportunity(
        "output_postprocessing",
        "Structured-output post-processing",
        "Roam shell calls that pipe structured output into projection or truncation utilities",
        "Expose a native bounded projection at the producing tool boundary",
        _structured_projection_calls,
        1.0,
    ),
    _Opportunity(
        "orientation_tax",
        "Repeated workspace orientation",
        "Workspace-orientation calls beyond the first call in one episode",
        "Inject compact dirty-tree, branch, and project identity facts",
        lambda ep: max(0, _friction(ep, "orientation_calls") - 1),
        0.85,
    ),
    _Opportunity(
        "verification_retry",
        "Repeated verification selection",
        "Verification or build attempts beyond the first attempt in one episode",
        "Select and order the smallest executable verification ladder",
        lambda ep: _friction(ep, "verification_retries"),
        1.0,
    ),
    _Opportunity(
        "failed_action_retry",
        "Failed action replay",
        "Failed actions retried with the same shell template within three actions",
        "Diagnose the failure before retrying or repair the command contract",
        lambda ep: _friction(ep, "failed_action_retries"),
        0.95,
    ),
    _Opportunity(
        "post_edit_context_recovery",
        "Context reacquisition after editing",
        "Orientation, search, inspection, or intelligence calls after the first edit and before verification",
        "Carry forward the edit-local context and changed-symbol closure",
        lambda ep: _friction(ep, "post_edit_context_calls"),
        0.95,
    ),
    _Opportunity(
        "command_discoverability",
        "Repeated command-help lookup",
        "Observed shell invocations containing --help or -h",
        "Compile the selected command signature and one executable example",
        lambda ep: _friction(ep, "help_calls"),
        0.8,
    ),
    _Opportunity(
        "user_correction_loop",
        "User correction after an agent episode",
        "The next user turn begins with a correction marker",
        "Mine the preceding trajectory as a negative procedure example",
        lambda ep: int(bool(ep.get("correction_after"))),
        1.0,
        "proxy",
    ),
    _Opportunity(
        "tool_failure_recovery",
        "Tool failure and recovery work",
        "Tool results classified as failed in the episode",
        "Route known failure signatures to deterministic recovery procedures",
        lambda ep: _int(ep.get("tool_errors")),
        0.9,
    ),
    _Opportunity(
        "high_tool_no_edit",
        "Long exploration with no edit",
        "No-edit episodes containing at least eight tool calls after excluding "
        "unknown and passive-only intent archetypes",
        "Return a stopping verdict or a compressed research brief earlier",
        _high_tool_no_edit_transition,
        0.75,
        "conditional",
    ),
    _Opportunity(
        "large_result_handling",
        "Large tool-result handling",
        "Episodes receiving at least 64 KiB of bucketed tool-result content",
        "Summarize, page, or project large results before they enter context",
        lambda ep: int(_int(ep.get("tool_result_bytes_bucket")) >= 65_536),
        0.9,
        "conditional",
    ),
)


_PLATFORM_DISPLACEMENT_CLAIMS: tuple[dict[str, str], ...] = (
    {
        "capability": "global --select",
        "opportunity": "output_postprocessing",
        "support": "native",
        "contract": "Project JSON envelopes before token budgeting without jq or Python.",
    },
    {
        "capability": "automatic response handles",
        "opportunity": "large_result_handling",
        "support": "native",
        "contract": "Page or persist oversized command results before they enter agent context.",
    },
    {
        "capability": "compile-cache",
        "opportunity": "exact_shell_replay",
        "support": "partial",
        "contract": "Cache compiled agent procedures; arbitrary deterministic shell replay remains uncovered.",
    },
)

_TRANSITION_ELIGIBILITY: dict[str, str] = {
    "repeated_code_slicing": (
        "A live location or search hit exists and a later action reads source before the next edit or verification."
    ),
    "search_inspect_thrash": ("A search and inspection transition repeats before any edit or terminal verdict."),
    "exact_shell_replay": (
        "The same deterministic sanitized template repeats with equivalent inputs and no intervening state mutation."
    ),
    "output_postprocessing": ("A Roam JSON result is immediately projected or truncated by another process."),
    "orientation_tax": ("Workspace identity was already observed and no branch/cwd mutation occurred."),
    "verification_retry": ("A verification attempt repeats or widens after a result that can be classified."),
    "failed_action_retry": ("A failed action is replayed before a diagnosis or relevant state change."),
    "post_edit_context_recovery": (
        "An edit occurred and the agent re-reads facts available from the changed-symbol closure."
    ),
    "command_discoverability": ("A help lookup follows an intent that can be mapped to a registered capability."),
    "user_correction_loop": ("A correction follows an exposed intervention with joined intent and terminal evidence."),
    "tool_failure_recovery": ("A failure class has validated precision and a deterministic recovery precondition."),
    "high_tool_no_edit": (
        "A non-passive closed intent archetype is present; unknown, research-only, "
        "review-only, planning-only, and documentation-only tasks are excluded."
    ),
    "large_result_handling": (
        "A result crosses the size threshold before projection, pagination, or file persistence."
    ),
}


@lru_cache(maxsize=1)
def declared_displacement_claims() -> tuple[dict[str, str], ...]:
    """AST-read command displacement contracts without importing commands.

    Lazy command loading is performance-critical, so the Foundry must not
    import hundreds of command modules merely to learn coverage. The
    ``@roam_capability(displaces=(...))`` literals are parsed directly from
    source and combined with platform-wide claims such as ``--select``.
    """
    claims: list[dict[str, str]] = [dict(row) for row in _PLATFORM_DISPLACEMENT_CLAIMS]
    commands_dir = Path(__file__).resolve().parent / "commands"
    for path in sorted(commands_dir.glob("cmd_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if func_name != "roam_capability":
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            displaces_node = keywords.get("displaces")
            if displaces_node is None:
                continue
            try:
                displaced = ast.literal_eval(displaces_node)
            except (ValueError, TypeError):
                continue
            if isinstance(displaced, str):
                displaced = (displaced,)
            if not isinstance(displaced, (tuple, list)):
                continue
            capability = path.stem.removeprefix("cmd_").replace("_", "-")
            name_node = keywords.get("name")
            if name_node is not None:
                try:
                    explicit_name = ast.literal_eval(name_node)
                except (ValueError, TypeError):
                    explicit_name = None
                if isinstance(explicit_name, str) and explicit_name:
                    capability = explicit_name
            for opportunity in displaced:
                if isinstance(opportunity, str) and opportunity:
                    claims.append(
                        {
                            "capability": capability,
                            "opportunity": opportunity,
                            "support": "native",
                            "contract": (f"Capability `{capability}` declares this repeated-work family."),
                        }
                    )
    claims.sort(
        key=lambda row: (
            row["opportunity"],
            row["support"],
            row["capability"],
        )
    )
    return tuple(claims)


def _intervention_mapping_rows(
    opportunities: list[dict[str, Any]],
    claims: tuple[dict[str, str], ...] | None = None,
) -> list[dict[str, Any]]:
    """Map declared interventions without pretending they close the gap.

    Historical support determines research priority. Declarations only say
    which intervention should enter a prospective test; they do not discount
    priority until live episodes establish adoption and non-inferior outcomes.
    """
    coverage_claims = claims if claims is not None else declared_displacement_claims()
    claims_by_opportunity: dict[str, list[dict[str, str]]] = defaultdict(list)
    for claim in coverage_claims:
        claims_by_opportunity[str(claim.get("opportunity") or "")].append(dict(claim))
    rows: list[dict[str, Any]] = []
    for opportunity in opportunities:
        key = str(opportunity.get("opportunity") or "")
        matching = claims_by_opportunity.get(key, [])
        supports = {str(claim.get("support") or "") for claim in matching}
        if "native" in supports:
            declaration_state = "declared_native"
        elif "partial" in supports:
            declaration_state = "declared_partial"
        else:
            declaration_state = "unclaimed"
        opportunity_score = float(opportunity.get("opportunity_score") or 0.0)
        rows.append(
            {
                "opportunity": key,
                "title": opportunity.get("title"),
                "declaration_state": declaration_state,
                "effectiveness_state": "unmeasured",
                "research_priority_score": opportunity_score,
                "residual_gap_score": None,
                "opportunity_score": opportunity_score,
                "organic_episode_estimate": _int(opportunity.get("organic_episode_estimate")),
                "organic_addressable_actions": _int(opportunity.get("organic_addressable_actions")),
                "projects": _int(opportunity.get("projects")),
                "capability_claims": matching,
                "next_architecture_move": opportunity.get("intervention_shape"),
                "evidence_status": "declared_intervention_mapping_only",
                "causal_savings_claimed": False,
            }
        )
    rows.sort(
        key=lambda row: (
            -row["research_priority_score"],
            -row["organic_addressable_actions"],
            row["opportunity"],
        )
    )
    return rows


def _intervention_test_rows(
    opportunities: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build prospective tests around transitions, not command counts."""
    mapping_by_key = {str(row.get("opportunity") or ""): row for row in mappings}
    rows = []
    for opportunity in opportunities:
        key = str(opportunity.get("opportunity") or "")
        mapping = mapping_by_key.get(key, {})
        rows.append(
            {
                "transition": key,
                "eligibility_rule": _TRANSITION_ELIGIBILITY.get(
                    key,
                    "A preconditioned repeated transition is observed in a joined live episode.",
                ),
                "baseline_signal": opportunity.get("signal_definition"),
                "intervention_candidates": mapping.get("capability_claims") or [],
                "exposure_state": "not_instrumented",
                "effectiveness_state": "unmeasured",
                "primary_metric": ("eligible downstream transition count per joined episode"),
                "experimental_design": {
                    "assignment_unit": "session_id",
                    "analysis_unit": "eligible_episode",
                    "assignment_strategy": ("randomized clustered holdout or pre-registered stepped wedge"),
                    "analysis_population": "intent_to_treat",
                    "control_condition": (
                        "candidate capability remains available but is not surfaced as the assigned intervention"
                    ),
                    "exposed_condition": (
                        "the assigned intervention is explicitly surfaced before the eligible transition"
                    ),
                    "required_event_pair": [
                        "intervention_assignment",
                        "intervention_observation",
                    ],
                    "stratification_fields": [
                        "project_id",
                        "intent_archetype",
                        "agent_surface",
                        "baseline_task_burden",
                    ],
                    "contamination_guard": ("keep one assignment per session and intervention version"),
                },
                "resource_metrics": [
                    "tool_calls",
                    "normalized_tokens",
                    "episode_wall_ms",
                    "tool_result_bytes",
                ],
                "outcome_guards": [
                    "terminal_success_rate",
                    "verification_pass_rate",
                    "user_correction_rate",
                    "partial_success_rate",
                ],
                "minimum_promotion_gate": {
                    "minimum_exposed_episodes": 30,
                    "minimum_control_episodes": 30,
                    "join_coverage_pct": 95.0,
                    "power_analysis_required": True,
                    "minimum_relative_transition_reduction_pct": 10.0,
                    "transition_effect_confidence_level_pct": 95.0,
                    "transition_effect_interval_must_exclude_zero": True,
                    "terminal_success_non_inferiority_margin_pp": 2.0,
                    "verification_pass_non_inferiority_margin_pp": 2.0,
                    "user_correction_inferiority_margin_pp": 2.0,
                    "partial_success_inferiority_margin_pp": 2.0,
                    "sequential_testing_policy": ("fixed horizon or pre-registered alpha-spending rule"),
                    "require_transition_reduction": True,
                    "require_outcome_non_inferiority": True,
                },
                "causal_savings_claimed": False,
            }
        )
    rows.sort(
        key=lambda row: (
            -float(mapping_by_key.get(row["transition"], {}).get("research_priority_score", 0.0)),
            row["transition"],
        )
    )
    return rows


def _opportunity_score(row: dict[str, Any], max_episodes: int, max_actions: int) -> float:
    episode_scale = math.log1p(row["organic_episode_estimate"]) / max(1.0, math.log1p(max_episodes))
    action_scale = math.log1p(row["organic_addressable_actions"]) / max(1.0, math.log1p(max_actions))
    project_scale = min(1.0, row["projects"] / 12.0)
    burden_scale = min(1.0, (row["median_tool_calls"] or 0) / 20.0)
    correction_scale = min(1.0, (row["correction_pct"] or 0.0) / 20.0)
    score = 100.0 * (
        0.25 * episode_scale
        + 0.20 * action_scale
        + 0.20 * project_scale
        + 0.10 * burden_scale
        + 0.10 * correction_scale
        + 0.15 * float(row["implementation_leverage"])
    )
    return round(score, 1)


def _likely_automated_episode_ids(episodes: list[dict[str, Any]]) -> set[str]:
    """Identify repeated exact prompts that look like scripted/eval cohorts."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        fingerprint = str(episode.get("prompt_hmac_sha256") or "")
        if fingerprint:
            grouped[fingerprint].append(episode)
    automated: set[str] = set()
    for rows in grouped.values():
        sessions = {str(row.get("session_id") or "") for row in rows}
        sessions.discard("")
        if len(rows) >= 20 and len(sessions) >= 20:
            automated.update(str(row.get("episode_id") or "") for row in rows)
    automated.discard("")
    return automated


def _opportunity_rows(
    episodes: list[dict[str, Any]],
    likely_automated_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in _OPPORTUNITIES:
        selected: list[tuple[dict[str, Any], int]] = []
        for episode in episodes:
            count = spec.counter(episode)
            if count > 0:
                selected.append((episode, count))
        if not selected:
            continue
        members = [episode for episode, _count in selected]
        organic_selected = [
            (episode, count)
            for episode, count in selected
            if str(episode.get("episode_id") or "") not in likely_automated_ids
        ]
        organic_members = [episode for episode, _count in organic_selected]
        projects = {str(ep.get("project_id") or "") for ep in organic_members}
        projects.discard("")
        analysis_members = organic_members or members
        sources = Counter(str(ep.get("transcript_source") or "unknown") for ep in analysis_members)
        archetypes = Counter(
            str(archetype)
            for episode in analysis_members
            for archetype in (
                episode.get("intent_archetypes") if isinstance(episode.get("intent_archetypes"), list) else []
            )
        )
        tokens = [normalized_episode_tokens(ep) for ep in members]
        tools = [_int(ep.get("tool_calls")) for ep in members]
        organic_tokens = [normalized_episode_tokens(ep) for ep in organic_members]
        organic_tools = [_int(ep.get("tool_calls")) for ep in organic_members]
        analysis_tokens = [normalized_episode_tokens(ep) for ep in analysis_members]
        analysis_tools = [_int(ep.get("tool_calls")) for ep in analysis_members]
        wall = [_int(ep.get("duration_ms")) for ep in members if ep.get("duration_ms") is not None]
        rows.append(
            {
                "opportunity": spec.key,
                "title": spec.title,
                "signal_definition": spec.signal_definition,
                "intervention_shape": spec.intervention_shape,
                "precision": spec.precision,
                "episodes": len(members),
                "organic_episode_estimate": len(organic_members),
                "likely_automated_episodes": len(members) - len(organic_members),
                "automation_contamination_pct": _pct(
                    len(members) - len(organic_members),
                    len(members),
                ),
                "projects": len(projects),
                "addressable_actions": sum(count for _episode, count in selected),
                "organic_addressable_actions": sum(count for _episode, count in organic_selected),
                "associated_tool_calls": sum(tools),
                "associated_tokens": sum(tokens),
                "organic_associated_tool_calls": sum(organic_tools),
                "organic_associated_tokens": sum(organic_tokens),
                "associated_episode_wall_ms": sum(wall),
                "median_tool_calls": _median(analysis_tools),
                "median_tokens": _median(analysis_tokens),
                "median_episode_wall_ms": _median(wall),
                "correction_pct": _pct(
                    sum(bool(ep.get("correction_after")) for ep in analysis_members),
                    len(analysis_members),
                ),
                "tool_error_pct": _pct(
                    sum(_int(ep.get("tool_errors")) > 0 for ep in analysis_members),
                    len(analysis_members),
                ),
                "verified_edit_pct": _pct(
                    sum(str(ep.get("outcome") or "") == "historical_acted_verified_proxy" for ep in analysis_members),
                    len(analysis_members),
                ),
                "sources": dict(sources.most_common()),
                "top_intent_archetypes": dict(archetypes.most_common(6)),
                "implementation_leverage": spec.leverage,
                "evidence_status": "historical_opportunity_only",
            }
        )
    max_episodes = max((row["organic_episode_estimate"] for row in rows), default=1)
    max_actions = max((row["organic_addressable_actions"] for row in rows), default=1)
    for row in rows:
        row["opportunity_score"] = _opportunity_score(row, max_episodes, max_actions)
    rows.sort(
        key=lambda row: (
            -row["opportunity_score"],
            -row["projects"],
            -row["organic_addressable_actions"],
            row["opportunity"],
        )
    )
    return rows


def _behavior_signature(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trajectories = Counter(str(row.get("phase_sequence_template") or "") for row in rows)
    trajectories.pop("", None)
    shell_patterns = Counter(
        str(pattern)
        for row in rows
        for pattern in (row.get("shell_templates").keys() if isinstance(row.get("shell_templates"), dict) else [])
    )
    archetypes = Counter(
        str(archetype)
        for row in rows
        for archetype in (row.get("intent_archetypes") if isinstance(row.get("intent_archetypes"), list) else [])
    )
    modal_phase, modal_count = trajectories.most_common(1)[0] if trajectories else ("", 0)
    return {
        "modal_phase_sequence": modal_phase,
        "modal_phase_sequence_pct": _pct(modal_count, len(rows)),
        "top_shell_templates": [pattern for pattern, _count in shell_patterns.most_common(3)],
        "top_intent_archetypes": dict(archetypes.most_common(4)),
    }


def _exact_intent_clusters(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        fingerprint = str(episode.get("prompt_hmac_sha256") or "")
        if fingerprint:
            grouped[fingerprint].append(episode)
    clusters: list[dict[str, Any]] = []
    for fingerprint, rows in grouped.items():
        sessions = {str(row.get("session_id") or "") for row in rows}
        sessions.discard("")
        if len(rows) < 3 or len(sessions) < 2:
            continue
        projects = {str(row.get("project_id") or "") for row in rows}
        projects.discard("")
        outcomes = Counter(str(row.get("outcome") or "unknown") for row in rows)
        tools = [_int(row.get("tool_calls")) for row in rows]
        tokens = [normalized_episode_tokens(row) for row in rows]
        clusters.append(
            {
                "intent_fingerprint": fingerprint,
                "episodes": len(rows),
                "sessions": len(sessions),
                "projects": len(projects),
                "associated_tool_calls": sum(tools),
                "associated_tokens": sum(tokens),
                "median_tool_calls": _median(tools),
                "median_tokens": _median(tokens),
                "correction_pct": _pct(sum(bool(row.get("correction_after")) for row in rows), len(rows)),
                "automation_likelihood": ("high" if len(rows) >= 20 and len(sessions) >= 20 else "low"),
                "outcomes": dict(outcomes.most_common()),
                "evidence_status": "historical_repeated_intent_only",
                **_behavior_signature(rows),
            }
        )
    clusters.sort(
        key=lambda row: (
            -row["associated_tool_calls"],
            -row["episodes"],
            row["intent_fingerprint"],
        )
    )
    return clusters[:50]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def _archetype_set(episode: dict[str, Any]) -> set[str]:
    values = episode.get("intent_archetypes")
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if value and value != "other"}


def _near_intent_clusters(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster conservative paraphrases using keyed SimHash locality."""
    eligible = [
        episode
        for episode in episodes
        if str(episode.get("intent_simhash64") or "") not in {"", "0000000000000000"}
        and _int(episode.get("prompt_tokens_bucket")) >= 25
        and _archetype_set(episode)
    ]
    if len(eligible) < 3:
        return []
    hashes: list[int] = []
    filtered: list[dict[str, Any]] = []
    for episode in eligible:
        try:
            value = int(str(episode["intent_simhash64"]), 16)
        except (TypeError, ValueError):
            continue
        hashes.append(value)
        filtered.append(episode)
    union_find = _UnionFind(len(filtered))
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, value in enumerate(hashes):
        for band in range(4):
            buckets[(band, (value >> (band * 16)) & 0xFFFF)].append(index)
    compared: set[tuple[int, int]] = set()
    for members in buckets.values():
        # Very large LSH buckets represent generic language rather than a
        # useful procedure neighborhood. Exact-intent mining still covers them.
        if len(members) > 500:
            continue
        for offset, left in enumerate(members):
            left_archetypes = _archetype_set(filtered[left])
            for right in members[offset + 1 :]:
                pair = (left, right) if left < right else (right, left)
                if pair in compared:
                    continue
                compared.add(pair)
                if not left_archetypes.intersection(_archetype_set(filtered[right])):
                    continue
                if (hashes[left] ^ hashes[right]).bit_count() <= 4:
                    union_find.union(left, right)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, episode in enumerate(filtered):
        grouped[union_find.find(index)].append(episode)
    clusters: list[dict[str, Any]] = []
    for rows in grouped.values():
        exact_intents = {str(row.get("prompt_hmac_sha256") or "") for row in rows}
        exact_intents.discard("")
        sessions = {str(row.get("session_id") or "") for row in rows}
        sessions.discard("")
        if len(rows) < 3 or len(exact_intents) < 2 or len(sessions) < 2:
            continue
        projects = {str(row.get("project_id") or "") for row in rows}
        projects.discard("")
        tools = [_int(row.get("tool_calls")) for row in rows]
        tokens = [normalized_episode_tokens(row) for row in rows]
        outcomes = Counter(str(row.get("outcome") or "unknown") for row in rows)
        hashes_in_cluster = sorted(str(row.get("intent_simhash64") or "") for row in rows)
        clusters.append(
            {
                "intent_cluster_id": f"sim_{hashes_in_cluster[0]}",
                "episodes": len(rows),
                "distinct_exact_intents": len(exact_intents),
                "sessions": len(sessions),
                "projects": len(projects),
                "associated_tool_calls": sum(tools),
                "associated_tokens": sum(tokens),
                "median_tool_calls": _median(tools),
                "median_tokens": _median(tokens),
                "correction_pct": _pct(sum(bool(row.get("correction_after")) for row in rows), len(rows)),
                "automation_likelihood": ("high" if len(rows) >= 20 and len(sessions) >= 20 else "medium"),
                "outcomes": dict(outcomes.most_common()),
                "evidence_status": "historical_near_intent_only",
                **_behavior_signature(rows),
            }
        )
    clusters.sort(
        key=lambda row: (
            -row["associated_tool_calls"],
            -row["episodes"],
            row["intent_cluster_id"],
        )
    )
    return clusters[:50]


def _template_outcome_rankings(
    episodes: list[dict[str, Any]],
    likely_automated_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    aggregates: dict[str, Counter[str]] = defaultdict(Counter)
    episode_sets: dict[str, set[str]] = defaultdict(set)
    project_sets: dict[str, set[str]] = defaultdict(set)
    recovery_counts: Counter[str] = Counter()
    recovery_templates: dict[str, set[str]] = defaultdict(set)
    recovery_projects: dict[str, set[str]] = defaultdict(set)
    for episode in episodes:
        if str(episode.get("episode_id") or "") in likely_automated_ids:
            continue
        outcomes = episode.get("shell_template_outcomes")
        if not isinstance(outcomes, dict):
            continue
        for template, values in outcomes.items():
            if not template or not isinstance(values, dict):
                continue
            aggregate = aggregates[str(template)]
            for key in (
                "attempts",
                "failures",
                "no_results",
                "retries_after_failure",
                "result_bytes_bucket",
            ):
                aggregate[key] += _int(values.get(key))
            failure_classes = values.get("failure_classes")
            if isinstance(failure_classes, dict):
                for failure_class, count in failure_classes.items():
                    label = str(failure_class or "")
                    amount = _int(count)
                    if not label or amount <= 0:
                        continue
                    aggregate[f"failure_class:{label}"] += amount
                    recovery_counts[label] += amount
                    recovery_templates[label].add(str(template))
                    project_id = str(episode.get("project_id") or "")
                    if project_id:
                        recovery_projects[label].add(project_id)
            episode_sets[str(template)].add(str(episode.get("episode_id") or ""))
            project_id = str(episode.get("project_id") or "")
            if project_id:
                project_sets[str(template)].add(project_id)
    rows: list[dict[str, Any]] = []
    for template, counts in aggregates.items():
        attempts = counts["attempts"]
        if attempts < 3:
            continue
        failures = counts["failures"]
        no_results = counts["no_results"]
        result_bytes = counts["result_bytes_bucket"]
        projects = len(project_sets[template])
        rows.append(
            {
                "template": template,
                "episodes": len(episode_sets[template] - {""}),
                "projects": projects,
                "attempts": attempts,
                "failures": failures,
                "failure_rate_pct": _pct(failures, attempts),
                "no_results": no_results,
                "failure_rate_excluding_no_results_pct": _pct(
                    failures,
                    max(0, attempts - no_results),
                ),
                "retries_after_failure": counts["retries_after_failure"],
                "failure_classes": {
                    key.removeprefix("failure_class:"): value
                    for key, value in sorted(counts.items())
                    if key.startswith("failure_class:") and value > 0
                },
                "associated_bucketed_result_bytes": result_bytes,
                "failure_priority_score": round(
                    math.log1p(failures)
                    * (1.0 + min(2.0, projects / 10.0))
                    * (1.0 + min(1.0, counts["retries_after_failure"] / 20.0)),
                    2,
                ),
                "result_volume_score": round(
                    math.log1p(result_bytes) * (1.0 + min(2.0, projects / 10.0)),
                    2,
                ),
                "evidence_status": "historical_template_outcome_only",
            }
        )
    failure_signatures = [row for row in rows if row["failures"] > 0]
    failure_signatures.sort(
        key=lambda row: (
            -row["failure_priority_score"],
            -row["failures"],
            -row["projects"],
            row["template"],
        )
    )
    result_producers = [row for row in rows if row["associated_bucketed_result_bytes"] > 0]
    result_producers.sort(
        key=lambda row: (
            -row["result_volume_score"],
            -row["associated_bucketed_result_bytes"],
            -row["projects"],
            row["template"],
        )
    )
    recovery_targets = [
        {
            "failure_class": failure_class,
            "failures": failures,
            "templates": len(recovery_templates[failure_class]),
            "projects": len(recovery_projects[failure_class]),
            "evidence_status": "closed_failure_class_only",
            "raw_result_content_persisted": False,
            "classification_status": "heuristic_unvalidated",
            "routing_eligible": False,
        }
        for failure_class, failures in recovery_counts.items()
    ]
    recovery_targets.sort(
        key=lambda row: (
            -row["failures"],
            -row["projects"],
            -row["templates"],
            row["failure_class"],
        )
    )
    return {
        "failure_signatures": failure_signatures[:100],
        "large_result_producers": result_producers[:100],
        "recovery_targets": recovery_targets,
    }


def build_procedure_atlas(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return cross-project opportunity and repeated-intent evidence."""
    historical = [
        episode
        for episode in episodes
        if episode.get("evidence_source") == "transcript_backfill" and episode.get("terminal")
    ]
    projects = {str(episode.get("project_id") or "") for episode in historical}
    projects.discard("")
    sources = Counter(str(episode.get("transcript_source") or "unknown") for episode in historical)
    likely_automated_ids = _likely_automated_episode_ids(historical)
    template_outcomes = _template_outcome_rankings(
        historical,
        likely_automated_ids,
    )
    opportunities = _opportunity_rows(historical, likely_automated_ids)
    intervention_mappings = _intervention_mapping_rows(opportunities)
    intervention_tests = _intervention_test_rows(
        opportunities,
        intervention_mappings,
    )
    return {
        "summary": {
            "verdict": (
                f"{len(historical)} historical episodes across {len(projects)} project identities "
                "rank private procedure opportunities"
            ),
            "episodes": len(historical),
            "projects": len(projects),
            "sources": dict(sources.most_common()),
            "likely_automated_episodes": len(likely_automated_ids),
            "organic_episode_estimate": len(historical) - len(likely_automated_ids),
            "causal_savings_claimed": False,
            "declared_native_interventions": sum(
                row["declaration_state"] == "declared_native" for row in intervention_mappings
            ),
            "declared_partial_interventions": sum(
                row["declaration_state"] == "declared_partial" for row in intervention_mappings
            ),
            "unclaimed_intervention_families": sum(
                row["declaration_state"] == "unclaimed" for row in intervention_mappings
            ),
        },
        "opportunities": opportunities,
        "intervention_mappings": intervention_mappings,
        "intervention_tests": intervention_tests,
        "exact_intent_clusters": _exact_intent_clusters(historical),
        "near_intent_clusters": _near_intent_clusters(historical),
        **template_outcomes,
        "definitions": {
            "addressable_actions": (
                "directly counted historical actions matching the opportunity signal; not actions proven removable"
            ),
            "associated_tokens": (
                "provider-normalized input plus output/reasoning tokens in episodes "
                "containing the signal; not tokens attributable to the signal"
            ),
            "opportunity_score": (
                "non-causal prioritization score combining episode support, action support, "
                "cross-project support, episode burden, correction rate, and implementation leverage; "
                "support terms use the organic estimate"
            ),
            "likely_automated_episode": (
                "episode whose exact keyed intent repeats across at least 20 episodes "
                "and 20 sessions; a contamination proxy, not proof of automation"
            ),
            "near_intent_cluster": (
                "keyed 64-bit intent SimHashes within Hamming distance 4, sharing "
                "a closed intent archetype, spanning at least two exact intents and sessions"
            ),
            "failure_signature": (
                "sanitized shell-template attempts, failed results, and same-template "
                "retries aggregated from transcript tool results"
            ),
            "recovery_target": (
                "closed failure-reason labels classified transiently from tool results; "
                "raw result content is discarded; historical regex labels remain "
                "routing-ineligible until validated"
            ),
            "intervention_test": (
                "prospective transition-level test requiring explicit exposure, joined "
                "terminal outcomes, transition reduction, and outcome non-inferiority"
            ),
            "associated_bucketed_result_bytes": (
                "sum of 4 KiB-rounded result-size buckets; output content is not persisted"
            ),
            "declaration_state": (
                "declared_native or declared_partial when an intervention hypothesis "
                "exists, otherwise unclaimed; declarations do not establish coverage"
            ),
            "research_priority_score": ("historical opportunity score, intentionally undiscounted by declarations"),
            "residual_gap_score": (
                "null until prospective episodes establish intervention adoption and "
                "outcome non-inferiority; only then may residual priority be estimated"
            ),
        },
    }
