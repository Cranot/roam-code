"""agent-opt — the envelope-contract super-optimizer (super-optimizer family P1).

`roam math` detects when *code* solves a known task with a weak algorithm and
points at the stronger one. `roam agent-opt` does the same for roam's OWN
agent-facing surface: it scans MCP tool descriptions and `roam --json`
envelopes for violations of the agi-in-md LAWs + the 6 systemic anti-patterns
(AGENTS.md § Quality discipline) and emits "this is solving TASK_X with weak
shape Y -> use shape Z" findings. It is the substrate that protects the
envelope contract as new commands land, and it dogfoods the laws it enforces.

Why a SEPARATE registry from ``catalog/detectors.py``
-----------------------------------------------------
The math ``@detector`` / ``_DETECTOR_REGISTRY`` surface assumes the signature
``(conn) -> list[dict]`` and the hard contract "empty DB -> [] findings"
(pinned by ``tests/test_w639_detector_smoke.py``, which parametrises over every
registry entry and asserts ``fn(empty_db) == []``). agent-opt detectors read
MCP descriptions and harvested CLI envelopes — NOT the DB — so they return
non-empty findings on an empty corpus and would break that contract. They
therefore live in their own ``_AGENT_OPT_DETECTORS`` registry, but they reuse
the *canonical* closed-enum vocabularies (``confidence_basis``/``query_cost``)
so the "extend the enum before adding a tier" discipline still flows through
one place.

The TASK catalog, by contrast, IS shared: the three tasks live in
``src/roam/catalog/tasks.py`` tagged ``family="agent-opt"`` (single catalog,
many surfaces).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

# Reuse the CANONICAL closed-enum vocabularies so a typo fails fast against the
# same source of truth math uses. Extending a tier (e.g. a "probabilistic"
# basis) means editing ``roam.db.findings`` / ``roam.catalog.detectors`` — and
# it then flows here for free.
from roam.catalog.detectors import QUERY_COST_HIGH, QUERY_COST_LOW, QUERY_COST_MEDIUM
from roam.catalog.tasks import CATALOG, best_way
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
    FindingRecord,
    make_finding_id,
)

__all__ = [
    "FAMILY",
    "AGENT_OPT_DETECTOR_VERSION",
    "agent_opt_detector",
    "list_agent_opt_detectors",
    "list_agent_opt_tasks",
    "agent_opt_task_ids",
    "detect_declarative_tool_description",
    "detect_weak_verdict",
    "detect_missing_next_command",
    "detect_silent_degraded_state",
    "detect_large_envelope_no_handle",
    "detect_abstract_fact",
    "detect_parameter_name_drift",
    "iter_tool_descriptions",
    "harvest_command_envelopes",
    "known_command_names",
    "discover_tool_params",
    "run_agent_opt",
    "build_finding_records",
    "DEFAULT_RUNTIME_COMMANDS",
]

FAMILY = "agent-opt"
AGENT_OPT_DETECTOR_VERSION = "1.0.0"

_VALID_BASES = frozenset({CONFIDENCE_HEURISTIC, CONFIDENCE_STRUCTURAL, CONFIDENCE_STATIC_ANALYSIS, CONFIDENCE_RUNTIME})
_VALID_COSTS = frozenset({QUERY_COST_LOW, QUERY_COST_MEDIUM, QUERY_COST_HIGH})

# ---------------------------------------------------------------------------
# A3-style detector registry (family-local; see module docstring for why it is
# not the shared ``_DETECTOR_REGISTRY``).
# ---------------------------------------------------------------------------
_AGENT_OPT_DETECTORS: dict[str, dict[str, Any]] = {}


def agent_opt_detector(
    *,
    task_id: str,
    confidence_basis: str = CONFIDENCE_STRUCTURAL,
    query_cost: str = QUERY_COST_LOW,
    version: str = AGENT_OPT_DETECTOR_VERSION,
) -> Callable[[Callable[..., list[dict]]], Callable[..., list[dict]]]:
    """Register an agent-opt envelope detector with metadata.

    Validates ``confidence_basis`` / ``query_cost`` against the CANONICAL
    closed-enum sets (raises ``ValueError`` at import time on a typo), mirroring
    ``roam.catalog.detectors.detector`` — the difference is only the registry
    these land in (``_AGENT_OPT_DETECTORS``, not ``_DETECTOR_REGISTRY``).
    """
    if confidence_basis not in _VALID_BASES:
        raise ValueError(f"confidence_basis must be one of {sorted(_VALID_BASES)}, got {confidence_basis!r}")
    if query_cost not in _VALID_COSTS:
        raise ValueError(f"query_cost must be one of {sorted(_VALID_COSTS)}, got {query_cost!r}")
    if task_id not in CATALOG or CATALOG[task_id].get("family") != FAMILY:
        raise ValueError(f"task_id {task_id!r} is not a CATALOG task tagged family={FAMILY!r}")

    def wrap(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
        _AGENT_OPT_DETECTORS[fn.__name__] = {
            "name": fn.__name__,
            "task_id": task_id,
            "family": FAMILY,
            "languages": (),  # framework-free — operates over roam's own surface
            "confidence_basis": confidence_basis,
            "query_cost": query_cost,
            "version": version,
            "function": fn,
        }
        return fn

    return wrap


def list_agent_opt_detectors() -> list[dict[str, Any]]:
    """Registry entries (sans callable) for ``--list-detectors``."""
    return [{k: v for k, v in e.items() if k != "function"} for e in _AGENT_OPT_DETECTORS.values()]


def agent_opt_task_ids() -> list[str]:
    """Catalog task ids tagged ``family="agent-opt"`` (the surface selector)."""
    return [tid for tid, t in CATALOG.items() if t.get("family") == FAMILY]


def list_agent_opt_tasks() -> list[dict[str, Any]]:
    """Task rows for ``roam agent-opt --list-tasks`` (best-way included)."""
    rows: list[dict[str, Any]] = []
    detectors_by_task: dict[str, int] = {}
    for e in _AGENT_OPT_DETECTORS.values():
        detectors_by_task[e["task_id"]] = detectors_by_task.get(e["task_id"], 0) + 1
    for tid in agent_opt_task_ids():
        task = CATALOG[tid]
        best = best_way(tid)
        rows.append(
            {
                "task_id": tid,
                "name": task["name"],
                "category": task["category"],
                "kind": task["kind"],
                "family": FAMILY,
                "detector_count": detectors_by_task.get(tid, 0),
                "best_way": best["id"] if best else "",
                "best_name": best["name"] if best else "",
                "best_tip": best.get("tip", "") if best else "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _suggestion_for(task_id: str) -> tuple[str, str]:
    """Return ``(best_way_id, best_tip)`` — the rank-1 way to jump to."""
    best = best_way(task_id)
    if not best:
        return "", ""
    return best["id"], best.get("tip", "")


def _finding(
    task_id: str,
    detected_way: str,
    subject: str,
    subject_kind: str,
    confidence: str,
    confidence_basis: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    best_id, best_tip = _suggestion_for(task_id)
    return {
        "task_id": task_id,
        "detected_way": detected_way,
        "suggested_way": best_id,
        "subject": subject,
        "subject_kind": subject_kind,
        "confidence": confidence,  # CVSS-style high/medium/low (matches `roam math`)
        "confidence_basis": confidence_basis,  # heuristic/structural/... (the @detector axis)
        "reason": reason,
        "evidence": evidence,
        "suggestion": best_tip,
    }


# ---------------------------------------------------------------------------
# Task 1: tool-description-declarative (LAW 2/11)
# ---------------------------------------------------------------------------
# A description opens "declaratively" when its first token narrates what the
# tool IS or DOES in the third person rather than directing the agent. The
# rank-1 way is an imperative verb or an identity-noun phrase.
_DECLARATIVE_OPENERS = frozenset(
    {"this", "the", "a", "an", "it", "returns", "provides", "shows", "displays", "gives", "contains"}
)
_FIRST_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _first_word(text: str) -> str:
    m = _FIRST_WORD_RE.search(text or "")
    return m.group(0).lower() if m else ""


@agent_opt_detector(task_id="tool-description-declarative", confidence_basis=CONFIDENCE_HEURISTIC)
def detect_declarative_tool_description(tool_descriptions: dict[str, str]) -> list[dict[str, Any]]:
    """Flag MCP tool descriptions that open declaratively instead of imperatively.

    ``tool_descriptions`` maps tool-name -> description string (e.g. from
    ``iter_tool_descriptions()``). Heuristic-tier: a leading "Returns ..." can
    occasionally be the clearest framing, so this is ``--profile balanced`` /
    ``--confidence low`` signal, not a hard gate.
    """
    out: list[dict[str, Any]] = []
    for name, desc in sorted((tool_descriptions or {}).items()):
        if not desc or not desc.strip():
            continue
        opener = _first_word(desc)
        if opener in _DECLARATIVE_OPENERS:
            out.append(
                _finding(
                    task_id="tool-description-declarative",
                    detected_way="declarative-opening",
                    subject=name,
                    subject_kind="tool",
                    confidence="low",
                    confidence_basis=CONFIDENCE_HEURISTIC,
                    reason=f"{name} description opens with '{opener.capitalize()}' — describes rather than directs (LAW 2/11)",
                    evidence={"opener": opener, "head": desc.strip()[:80]},
                )
            )
    return out


# ---------------------------------------------------------------------------
# Task 2: weak-verdict (LAW 6 — verdict must work standalone)
# ---------------------------------------------------------------------------
_WEAK_VERDICT_PHRASES = frozenset(
    {"", "completed", "done", "ok", "okay", "finished", "see details", "n/a", "na", "no data", "success", "complete"}
)
_DIGIT_RE = re.compile(r"\d")


def _verdict_weakness(verdict: Any) -> str | None:
    """Return a short weakness label if ``verdict`` fails LAW 6, else None."""
    if verdict is None:
        return "missing"
    if not isinstance(verdict, str):
        return None  # non-string verdicts are a different (schema) problem
    norm = verdict.strip()
    if not norm:
        return "empty"
    low = norm.lower().rstrip(".")
    if low in _WEAK_VERDICT_PHRASES:
        return "boilerplate"
    if "see detail" in low or low.startswith("see "):
        return "deferral"
    if norm.endswith(":"):
        return "dangling-colon"
    if len(norm.split()) < 4 and not _DIGIT_RE.search(norm):
        return "too-terse"
    return None


@agent_opt_detector(task_id="weak-verdict", confidence_basis=CONFIDENCE_STRUCTURAL)
def detect_weak_verdict(envelopes: Iterable[tuple[str, dict]]) -> list[dict[str, Any]]:
    """Flag ``summary.verdict`` strings that don't stand alone (LAW 6).

    ``envelopes`` is an iterable of ``(label, envelope_dict)`` — e.g. from
    ``harvest_command_envelopes()``. An agent that consumes only the verdict
    (and not the full envelope) must still act correctly.
    """
    out: list[dict[str, Any]] = []
    for label, env in envelopes:
        if not isinstance(env, dict):
            continue
        verdict = (env.get("summary") or {}).get("verdict")
        weakness = _verdict_weakness(verdict)
        if weakness is not None:
            out.append(
                _finding(
                    task_id="weak-verdict",
                    detected_way="non-standalone-verdict",
                    subject=label,
                    subject_kind="command",
                    confidence="high",
                    confidence_basis=CONFIDENCE_STRUCTURAL,
                    reason=f"{label} verdict is {weakness} — fails LAW 6 (must work without any other field)",
                    evidence={"verdict": verdict, "weakness": weakness},
                )
            )
    return out


# ---------------------------------------------------------------------------
# Task 3: missing-next-command (CONSTRAINT 12)
# ---------------------------------------------------------------------------
def _envelope_has_findings(env: dict) -> bool:
    """Heuristic: does this envelope report something actionable?"""
    for key in ("findings", "alerts", "issues", "violations", "results", "items"):
        val = env.get(key)
        if isinstance(val, list) and val:
            return True
    summary = env.get("summary") or {}
    for key in ("count", "total", "total_findings", "unsuppressed_total"):
        val = summary.get(key)
        if isinstance(val, int) and val > 0:
            return True
    return False


def _next_commands(env: dict) -> list:
    ac = env.get("agent_contract")
    if isinstance(ac, dict) and isinstance(ac.get("next_commands"), list):
        return ac["next_commands"]
    if isinstance(env.get("next_commands"), list):
        return env["next_commands"]
    return []


@agent_opt_detector(task_id="missing-next-command", confidence_basis=CONFIDENCE_STRUCTURAL)
def detect_missing_next_command(
    envelopes: Iterable[tuple[str, dict]],
    known_commands: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Flag envelopes whose follow-ups aren't copy-paste ``roam <cmd>`` (CONSTRAINT 12).

    Two violation shapes:
    - findings present but ``next_commands`` empty (no direction);
    - a ``next_commands`` entry that isn't a runnable ``roam <subcommand>``
      resolving to a real command (``known_commands``).
    """
    known = known_commands if known_commands is not None else known_command_names()
    out: list[dict[str, Any]] = []
    for label, env in envelopes:
        if not isinstance(env, dict):
            continue
        ncs = _next_commands(env)
        if not ncs:
            if _envelope_has_findings(env):
                out.append(
                    _finding(
                        task_id="missing-next-command",
                        detected_way="no-next-command",
                        subject=label,
                        subject_kind="command",
                        confidence="medium",
                        confidence_basis=CONFIDENCE_STRUCTURAL,
                        reason=f"{label} reports findings but offers no next_commands — agent has diagnosis without direction (CONSTRAINT 12)",
                        evidence={"next_commands": []},
                    )
                )
            continue
        for nc in ncs:
            if not isinstance(nc, str) or not nc.strip().startswith("roam "):
                out.append(
                    _finding(
                        task_id="missing-next-command",
                        detected_way="no-next-command",
                        subject=label,
                        subject_kind="command",
                        confidence="medium",
                        confidence_basis=CONFIDENCE_STRUCTURAL,
                        reason=f"{label} next_command {nc!r} is not a runnable 'roam <cmd>' string (CONSTRAINT 12)",
                        evidence={"offending_next_command": nc},
                    )
                )
                continue
            # First non-flag token after `roam` must resolve to a real command.
            tokens = [t for t in nc.split()[1:] if not t.startswith("-")]
            sub = tokens[0] if tokens else ""
            if sub and known and sub not in known:
                out.append(
                    _finding(
                        task_id="missing-next-command",
                        detected_way="no-next-command",
                        subject=label,
                        subject_kind="command",
                        confidence="high",
                        confidence_basis=CONFIDENCE_STRUCTURAL,
                        reason=f"{label} next_command names 'roam {sub}' which is not a registered command (CONSTRAINT 12)",
                        evidence={"offending_next_command": nc, "unresolved_subcommand": sub},
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Task 4: silent-degraded-state (Pattern 2 — disclose, never silent SAFE)
# ---------------------------------------------------------------------------
@agent_opt_detector(task_id="silent-degraded-state", confidence_basis=CONFIDENCE_STRUCTURAL)
def detect_silent_degraded_state(envelopes: Iterable[tuple[str, dict]]) -> list[dict[str, Any]]:
    """Flag envelopes that carry a failure signal but don't set partial_success.

    A clean ABSENT state (``state: "not_initialized"`` etc.) is a CORRECT
    Pattern-2 disclosure and is deliberately NOT flagged — only genuine failure
    signals (error fields, non-zero failed counts, unflagged warnings) require
    ``summary.partial_success: true``.
    """
    out: list[dict[str, Any]] = []
    for label, env in envelopes:
        if not isinstance(env, dict):
            continue
        summary = env.get("summary") or {}
        if summary.get("partial_success") is True:
            continue  # correctly disclosed — no violation
        signals: list[str] = []
        for key in ("error", "error_code"):
            if summary.get(key) or env.get(key):
                signals.append(key)
        for key in ("detectors_failed", "subcommands_failed"):
            v = summary.get(key)
            if isinstance(v, int) and v > 0:
                signals.append(key)
        for key in ("failed_subcommands", "failed_detectors", "failed_subtasks"):
            v = summary.get(key)
            if isinstance(v, list) and v:
                signals.append(key)
        wc = summary.get("warnings_count")
        if isinstance(wc, int) and wc > 0:
            signals.append("warnings_count")
        for key in ("warnings_out", "warnings"):
            v = env.get(key) if isinstance(env.get(key), list) else summary.get(key)
            if isinstance(v, list) and v:
                signals.append(key)
        if not signals:
            continue
        out.append(
            _finding(
                task_id="silent-degraded-state",
                detected_way="silent-fallback",
                subject=label,
                subject_kind="command",
                confidence="high",
                confidence_basis=CONFIDENCE_STRUCTURAL,
                reason=f"{label} envelope carries failure signal(s) {sorted(set(signals))} but summary.partial_success is not true (Pattern 2 silent fallback)",
                evidence={"signals": sorted(set(signals)), "partial_success": summary.get("partial_success")},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Task 5: large-envelope-no-handle (Pattern 6 — >20K tokens must use a handle)
# ---------------------------------------------------------------------------
# ~20K tokens at the common 4-chars/token heuristic.
_LARGE_ENVELOPE_BYTE_THRESHOLD = 80_000
_HANDLE_KEYS = ("handle_id", "_handle", "handle", "details_handle", "output_file", "output_path")


@agent_opt_detector(task_id="large-envelope-no-handle", confidence_basis=CONFIDENCE_STRUCTURAL)
def detect_large_envelope_no_handle(envelopes: Iterable[tuple[str, dict]]) -> list[dict[str, Any]]:
    """Flag envelopes that inline a >~20K-token payload with no handle/output_file."""
    out: list[dict[str, Any]] = []
    for label, env in envelopes:
        if not isinstance(env, dict):
            continue
        try:
            size = len(json.dumps(env, default=str))
        except (TypeError, ValueError):
            continue
        if size <= _LARGE_ENVELOPE_BYTE_THRESHOLD:
            continue
        summary = env.get("summary") or {}
        if any(k in env for k in _HANDLE_KEYS) or any(k in summary for k in _HANDLE_KEYS):
            continue
        out.append(
            _finding(
                task_id="large-envelope-no-handle",
                detected_way="inline-large-payload",
                subject=label,
                subject_kind="command",
                confidence="medium",
                confidence_basis=CONFIDENCE_STRUCTURAL,
                reason=f"{label} envelope is {size} bytes (~{size // 4} tokens) with no handle_id/output_file — exceeds the ~20K-token inline cap (Pattern 6)",
                evidence={"bytes": size, "approx_tokens": size // 4},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Task 6: abstract-fact (LAW 4 — facts must anchor on concrete-noun terminals)
# ---------------------------------------------------------------------------
# The three frozensets + ``_fact_is_concrete_anchored`` below are a faithful
# PRODUCTION mirror of the LAW-4 CI lint (``tests/test_law4_lint.py``:
# _CONCRETE_NOUN_ANCHORS / _ANALYTICAL_VERBS / _MEASUREMENT_SUFFIXES +
# _is_concrete_anchored). The repo deliberately keeps the lint's anchor set
# decoupled from the formatter's (AGENTS.md § LAW 4), so agent-opt carries its
# own copy; EXACT parity is pinned by
# ``tests/test_agent_opt.py::test_abstract_fact_anchor_parity_with_law4`` so the
# two definitions of "weak fact" can never silently diverge (Pattern 3a).
_CONCRETE_NOUN_ANCHORS = frozenset(
    {
        "actions",
        "added",
        "affected",
        "agents",
        "alerts",
        "analysed",
        "analyzed",
        "annotations",
        "available",
        "branches",
        "budget",
        "bytes",
        "callees",
        "callers",
        "candidates",
        "capabilities",
        "challenges",
        "characters",
        "chars",
        "checked",
        "checks",
        "checks-failed",
        "checks-passed",
        "clusters",
        "commands",
        "commits",
        "confirmed",
        "cycles",
        "days",
        "dependencies",
        "diagnostics",
        "direct",
        "directories",
        "downgrades",
        "edges",
        "effects",
        "endpoints",
        "entries",
        "errors",
        "events",
        "exits",
        "failed",
        "fields",
        "files",
        "findings",
        "flags",
        "frameworks",
        "gaps",
        "heuristic",
        "hotspots",
        "hours",
        "imports",
        "issues",
        "items",
        "keys",
        "kinds",
        "languages",
        "layers",
        "leaks",
        "lines",
        "literals",
        "logged",
        "markers",
        "matches",
        "milliseconds",
        "minutes",
        "modules",
        "months",
        "movers",
        "moves",
        "nodes",
        "options",
        "owned",
        "owners",
        "packages",
        "passed",
        "paths",
        "patterns",
        "phantom",
        "presets",
        "queries",
        "reachable",
        "reached",
        "records",
        "removed",
        "risks",
        "routes",
        "rules",
        "scanned",
        "scenarios",
        "schemas",
        "scored",
        "seconds",
        "secrets",
        "seeds",
        "shifts",
        "skipped",
        "smells",
        "snapshots",
        "subcommands",
        "symbols",
        "tests",
        "tokens",
        "tools",
        "total",
        "trending",
        "types",
        "upgrades",
        "used",
        "users",
        "values",
        "violations",
        "vulnerabilities",
        "warnings",
        "weeks",
        "years",
    }
)
_ANALYTICAL_VERBS = frozenset(
    {
        "added",
        "blocked",
        "classified",
        "computed",
        "confirmed",
        "detected",
        "emitted",
        "failed",
        "flagged",
        "found",
        "introduced",
        "logged",
        "passed",
        "ran",
        "reached",
        "rejected",
        "removed",
        "rendered",
        "reported",
        "scanned",
        "scored",
        "skipped",
        "surfaced",
        "verified",
    }
)
_MEASUREMENT_SUFFIXES = frozenset(
    {
        "bytes",
        "cohesion",
        "count",
        "depth",
        "kb",
        "mb",
        "ms",
        "pct",
        "percent",
        "percentage",
        "rate",
        "ratio",
        "score",
        "size",
        "total",
    }
)
_KNOWN_ABSTRACT_FACTS = frozenset({"no data", "ok", "completed", "see details", "tbd", "n/a", "done"})


def _is_floatable(token: str) -> bool:
    """True if *token* parses as a float. Helper (not an inline ``except: pass``)
    so the loud-fallback lint (``test_loud_fallback_no_new_silent_except``) sees
    a handled outcome, while preserving exact ``float()`` acceptance semantics."""
    try:
        float(token)
    except (ValueError, TypeError):
        return False
    return True


def _fact_is_concrete_anchored(fact: Any) -> bool:
    """Faithful mirror of ``test_law4_lint._is_concrete_anchored`` (LAW 4)."""
    if not isinstance(fact, str):
        return False
    stripped = fact.strip()
    if not stripped:
        return False
    if stripped.lower() in _KNOWN_ABSTRACT_FACTS:
        return False
    lower = stripped.lower()
    for verb in _ANALYTICAL_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return True
    tokens = stripped.split()
    if not tokens:
        return False
    terminal = tokens[-1].lower().rstrip(",.;:!?)").lstrip("(")
    if terminal in _CONCRETE_NOUN_ANCHORS:
        return True
    # Anchor can sit before a trailing parenthetical — strip and recheck.
    stripped_paren = re.sub(r"\s*\([^)]*\)\s*$", "", stripped).strip()
    if stripped_paren != stripped:
        tail_tokens = stripped_paren.split()
        if tail_tokens:
            tail_terminal = tail_tokens[-1].lower().rstrip(",.;:!?)").lstrip("(")
            if tail_terminal in _CONCRETE_NOUN_ANCHORS:
                return True
    # Measurement form: "<label> <suffix> <numeric>" — e.g. "health score 75".
    if len(tokens) >= 2 and _is_floatable(tokens[-1]):
        penultimate = tokens[-2].lower().rstrip(",.;:!?)")
        if penultimate in _MEASUREMENT_SUFFIXES:
            return True
    # Long sentence with non-numeric lead — likely a verdict (self-anchors).
    first = tokens[0]
    if len(tokens) > 4 and not first[:1].isdigit() and first != "{X}":
        return True
    return False


def _fact_is_abstract(fact: Any) -> bool:
    """True iff *fact* FAILS LAW 4 (would be flagged by the lint)."""
    return not _fact_is_concrete_anchored(fact)


@agent_opt_detector(task_id="abstract-fact", confidence_basis=CONFIDENCE_STRUCTURAL)
def detect_abstract_fact(envelopes: Iterable[tuple[str, dict]]) -> list[dict[str, Any]]:
    """Flag ``agent_contract.facts`` whose terminal isn't concrete-noun-anchored (LAW 4)."""
    out: list[dict[str, Any]] = []
    for label, env in envelopes:
        if not isinstance(env, dict):
            continue
        ac = env.get("agent_contract")
        facts = ac.get("facts") if isinstance(ac, dict) else None
        for fact in facts or []:
            if _fact_is_abstract(fact):
                out.append(
                    _finding(
                        task_id="abstract-fact",
                        detected_way="abstract-fact",
                        subject=label,
                        subject_kind="command",
                        confidence="medium",
                        confidence_basis=CONFIDENCE_STRUCTURAL,
                        reason=f"{label} agent_contract fact {fact!r} is not concrete-noun-anchored (LAW 4)",
                        evidence={"fact": fact},
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Task 7: parameter-name-drift (Pattern 3b — cross-MCP param-name divergence)
# ---------------------------------------------------------------------------
def _legacy_param_map() -> dict[str, str]:
    """{legacy_declared_name -> canonical} derived from the _PARAM_ALIASES table."""
    from roam.mcp_server import _PARAM_ALIASES

    legacy: dict[str, str] = {}
    for _canon, alias_map in _PARAM_ALIASES.items():
        for declared, target in alias_map.items():
            if declared != target:
                legacy[declared] = target
    return legacy


def discover_tool_params() -> list[tuple[str, tuple[str, ...]]]:
    """AST-discover ``(tool_name, declared_param_names)`` for every @_tool wrapper.

    Mirrors ``tests/test_mcp_param_names.py::_discover_tool_wrappers`` — runtime
    signature introspection is unreliable after decoration, so this parses the
    server source instead.
    """
    import ast
    import inspect

    from roam import mcp_server

    src_path = inspect.getsourcefile(mcp_server)
    if not src_path:
        return []
    with open(src_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tool_name = None
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            dname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if dname != "_tool":
                continue
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        tool_name = kw.value.value
            break
        if tool_name is None:
            continue
        params = tuple(a.arg for a in node.args.args if a.arg not in ("self", "cls"))
        out.append((tool_name, params))
    return out


@agent_opt_detector(task_id="parameter-name-drift", confidence_basis=CONFIDENCE_STRUCTURAL)
def detect_parameter_name_drift(
    tool_params: Iterable[tuple[str, tuple[str, ...]]],
    legacy_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Flag MCP wrappers that declare a legacy alias as their parameter name.

    ``legacy_map`` defaults to the live ``_PARAM_ALIASES``-derived map. A
    canonical-named wrapper produces nothing — the existing lint keeps the
    surface clean, so this is the agent-facing regression guard.
    """
    lm = legacy_map if legacy_map is not None else _legacy_param_map()
    out: list[dict[str, Any]] = []
    for tool, params in tool_params:
        for p in params:
            canon = lm.get(p)
            if canon and canon != p:
                out.append(
                    _finding(
                        task_id="parameter-name-drift",
                        detected_way="legacy-param-name",
                        subject=f"{tool}.{p}",
                        subject_kind="tool",
                        # Advisory, not a break: existing callers are already
                        # normalized by the alias table (and some legacy names
                        # are grandfathered by the lint). `--confidence high`
                        # filters these out; the value is the canonical-name
                        # inventory, not a hard gate.
                        confidence="low",
                        confidence_basis=CONFIDENCE_STRUCTURAL,
                        reason=f"MCP tool {tool} declares non-canonical param '{p}'; canonical is '{canon}' — callers are normalized via _PARAM_ALIASES, but new wrappers should declare '{canon}' (Pattern 3b)",
                        evidence={"tool": tool, "param": p, "canonical": canon},
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Signal-source harvesters (reuse roam's OWN static surfaces, never re-run a
# detector — Pattern 3)
# ---------------------------------------------------------------------------
def iter_tool_descriptions(scope: str = "full") -> dict[str, str]:
    """Map MCP tool-name -> description from the canonical ``_TOOL_METADATA``.

    ``scope="core"`` restricts to the core preset; any other value (default
    ``"full"``) returns every registered tool. Importing ``roam.mcp_server`` is
    heavy, so callers do it lazily (only when a description-tier task runs).
    """
    from roam.mcp_server import _TOOL_METADATA  # lazy: 18k-line module

    out: dict[str, str] = {}
    want_core = str(scope).lower() == "core"
    for name, meta in _TOOL_METADATA.items():
        if want_core and not meta.get("core"):
            continue
        out[name] = meta.get("description") or ""
    return out


def known_command_names() -> set[str]:
    """Every registered CLI command name (for next_command resolution)."""
    from roam.cli import _COMMANDS

    names = set(_COMMANDS.keys())
    try:
        from roam.cli import _DEPRECATED_COMMANDS

        names |= set(_DEPRECATED_COMMANDS.keys())
    except ImportError:
        pass
    return names


# A small, read-only, no-required-arg corpus. Kept short so a default
# ``roam agent-opt`` run stays fast; mirrors the law4 lint's representative
# sweep (``tests/test_law4_lint.py``).
DEFAULT_RUNTIME_COMMANDS: list[tuple[str, list[str]]] = [
    ("health", ["health"]),
    ("understand", ["understand"]),
    ("dashboard", ["dashboard"]),
    ("alerts", ["alerts"]),
    ("conventions", ["conventions"]),
    ("brief", ["brief"]),
    ("agent-score", ["agent-score"]),
]


def _invoke_json(args: list[str]) -> dict | None:
    """Run ``roam --json <args>`` in-process and return the parsed envelope.

    Copied shape from ``tests/test_law4_lint.py::_invoke_json`` — output may be
    prefixed by status/progress lines, so locate the first ``{`` that parses.
    """
    from click.testing import CliRunner

    from roam.cli import cli

    result = CliRunner().invoke(cli, ["--json"] + args)
    if result.exit_code != 0:
        return None
    text = result.output or ""
    for start in range(len(text)):
        if text[start] != "{":
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def harvest_command_envelopes(
    commands: list[tuple[str, list[str]]] | None = None,
) -> tuple[list[tuple[str, dict]], list[str]]:
    """Harvest ``(label, envelope)`` pairs from a no-arg command corpus.

    Returns ``(envelopes, unavailable)`` where ``unavailable`` lists labels
    whose command did not return a parseable envelope (skipped, not failed —
    a command may simply not exist on this checkout).
    """
    corpus = commands if commands is not None else DEFAULT_RUNTIME_COMMANDS
    envelopes: list[tuple[str, dict]] = []
    unavailable: list[str] = []
    for label, args in corpus:
        env = _invoke_json(args)
        if env is None:
            unavailable.append(label)
            continue
        envelopes.append((label, env))
    return envelopes, unavailable


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
_DETECTOR_FN_BY_TASK = {
    "tool-description-declarative": "detect_declarative_tool_description",
    "weak-verdict": "detect_weak_verdict",
    "missing-next-command": "detect_missing_next_command",
    "silent-degraded-state": "detect_silent_degraded_state",
    "large-envelope-no-handle": "detect_large_envelope_no_handle",
    "abstract-fact": "detect_abstract_fact",
    "parameter-name-drift": "detect_parameter_name_drift",
}
# Tasks whose source is a harvested `roam --json` envelope corpus.
_ENVELOPE_TASKS = frozenset(
    {"weak-verdict", "missing-next-command", "silent-degraded-state", "large-envelope-no-handle", "abstract-fact"}
)
# Task whose source is the MCP wrapper parameter surface (AST-discovered).
_PARAM_TASKS = frozenset({"parameter-name-drift"})


def run_agent_opt(
    *,
    scope: str = "full",
    only: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    commands: list[tuple[str, list[str]]] | None = None,
    envelopes: list[tuple[str, dict]] | None = None,
    tool_descriptions: dict[str, str] | None = None,
    tool_params: list[tuple[str, tuple[str, ...]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the selected agent-opt detectors and return ``(findings, meta)``.

    Sources are harvested lazily: the description-tier task imports
    ``_TOOL_METADATA``; the envelope-tier tasks harvest the command corpus only
    if at least one is active. ``envelopes`` / ``tool_descriptions`` may be
    passed in directly (used by tests for deterministic input).

    ``meta`` carries ``partial_success`` (Pattern 2): True iff a detector
    raised OR no signal source could be harvested for an active envelope task.
    """
    active = set(agent_opt_task_ids())
    only_set = {t for t in (only or ()) if t}
    exclude_set = {t for t in (exclude or ()) if t} - only_set
    if only_set:
        active &= only_set
    active -= exclude_set

    only_unknown = sorted(only_set - set(agent_opt_task_ids())) if only_set else []
    exclude_unknown = sorted(exclude_set - set(agent_opt_task_ids())) if exclude_set else []

    findings: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    executed = 0
    sources: dict[str, Any] = {}
    partial = False

    # --- description-tier ---
    if "tool-description-declarative" in active:
        try:
            descs = tool_descriptions if tool_descriptions is not None else iter_tool_descriptions(scope)
            sources["tool_descriptions_scanned"] = len(descs)
            findings.extend(detect_declarative_tool_description(descs))
            executed += 1
        except Exception as exc:  # noqa: BLE001 — record + degrade, never silent
            failed.append({"detector": "detect_declarative_tool_description", "error": f"{type(exc).__name__}: {exc}"})
            partial = True

    # --- envelope-tier (harvest once, shared by both tasks) ---
    env_tasks = active & _ENVELOPE_TASKS
    if env_tasks:
        if envelopes is None:
            harvested, unavailable = harvest_command_envelopes(commands)
        else:
            harvested, unavailable = envelopes, []
        sources["envelopes_scanned"] = len(harvested)
        sources["commands_unavailable"] = unavailable
        if not harvested:
            # No signal source for an active task -> disclose, don't fake SAFE.
            partial = True
        if "weak-verdict" in env_tasks:
            try:
                findings.extend(detect_weak_verdict(harvested))
                executed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"detector": "detect_weak_verdict", "error": f"{type(exc).__name__}: {exc}"})
                partial = True
        if "missing-next-command" in env_tasks:
            try:
                findings.extend(detect_missing_next_command(harvested, known_command_names()))
                executed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"detector": "detect_missing_next_command", "error": f"{type(exc).__name__}: {exc}"})
                partial = True
        if "silent-degraded-state" in env_tasks:
            try:
                findings.extend(detect_silent_degraded_state(harvested))
                executed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"detector": "detect_silent_degraded_state", "error": f"{type(exc).__name__}: {exc}"})
                partial = True
        if "large-envelope-no-handle" in env_tasks:
            try:
                findings.extend(detect_large_envelope_no_handle(harvested))
                executed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"detector": "detect_large_envelope_no_handle", "error": f"{type(exc).__name__}: {exc}"})
                partial = True
        if "abstract-fact" in env_tasks:
            try:
                findings.extend(detect_abstract_fact(harvested))
                executed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"detector": "detect_abstract_fact", "error": f"{type(exc).__name__}: {exc}"})
                partial = True

    # --- param-tier (MCP wrapper parameter surface, AST-discovered) ---
    if active & _PARAM_TASKS:
        try:
            tps = tool_params if tool_params is not None else discover_tool_params()
            sources["tool_params_scanned"] = len(tps)
            findings.extend(detect_parameter_name_drift(tps))
            executed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"detector": "detect_parameter_name_drift", "error": f"{type(exc).__name__}: {exc}"})
            partial = True

    if failed:
        partial = True

    meta: dict[str, Any] = {
        "detectors_executed": executed,
        "detectors_failed": len(failed),
        "failed_detectors": failed,
        "active_tasks": sorted(active),
        "scope": scope,
        "sources": sources,
        "partial_success": partial,
    }
    if only_unknown:
        meta["only_unknown"] = only_unknown
    if exclude_unknown:
        meta["exclude_unknown"] = exclude_unknown
    return findings, meta


# ---------------------------------------------------------------------------
# A4 persistence — wired explicitly (per-family, not free reuse)
# ---------------------------------------------------------------------------
def build_finding_records(findings: list[dict[str, Any]]) -> list[FindingRecord]:
    """Map in-envelope findings onto canonical ``FindingRecord`` rows.

    ``source_detector`` is prefixed with the family (``agent-opt.<task>``) so
    the persisted name won't collide with future families. ``subject_id`` is
    NULL — the subject is a tool/command surface, not a resolved ``symbols.id``
    (FindingRecord explicitly allows a NULL subject_id).
    """
    records: list[FindingRecord] = []
    for f in findings:
        task_id = f["task_id"]
        subject = f.get("subject", "?")
        basis = f.get("confidence_basis", CONFIDENCE_STRUCTURAL)
        evidence = f.get("evidence", {})
        # Fold the evidence into the id digest. A single subject can carry
        # MULTIPLE distinct findings for the same (task, detected_way) — e.g.
        # `health` with two different non-executable next_commands — and
        # without the evidence discriminator they would share a finding_id and
        # collapse to one row under the ON CONFLICT upsert.
        evidence_repr = json.dumps(evidence, sort_keys=True)
        records.append(
            FindingRecord(
                finding_id_str=make_finding_id("agent-opt", subject, task_id, f.get("detected_way", ""), evidence_repr),
                subject_kind="symbol",
                subject_id=None,
                claim=f.get("reason", f"{task_id} violation on {subject}"),
                evidence_json=json.dumps(
                    {
                        "task_id": task_id,
                        "detected_way": f.get("detected_way"),
                        "recommended_way": f.get("suggested_way"),
                        "subject": subject,
                        "subject_kind": f.get("subject_kind"),
                        "suggestion": f.get("suggestion"),
                        "evidence": evidence,
                    },
                    sort_keys=True,
                ),
                confidence=basis,
                source_detector=f"agent-opt.{task_id}",
                source_version=AGENT_OPT_DETECTOR_VERSION,
            )
        )
    return records
