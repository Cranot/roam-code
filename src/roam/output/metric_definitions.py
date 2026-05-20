"""Canonical metric-definition strings shared across roam commands.

W331 — Pattern 3a fix per CLAUDE.md ("Vocabulary mismatch across
commands"). Every command that reports a "callers" / "complexity" /
"blast radius" / "health score" / etc. number SHOULD also stamp a
``<metric>_definition`` sidecar in its JSON envelope. When two or more
commands share the same definition, the string lives here so the value
cannot drift independently.

Companion to ``docs/concepts/caller-metrics.md`` (canonical caller-
metric reference) and ``src/roam/quality/cycles.py`` /
``src/roam/quality/god_components.py`` (canonical cycle / god-component
definitions). The exports below cover metrics that span more than one
command.

Convention
----------
Field-name shape: ``<metric>_definition`` (snake_case, NO
``_metric_definition`` middleware — keep it short). String shape: a
single sentence, ~10-15 words, naming the actual computation. Match the
existing examples in ``cmd_uses.py`` (``caller_metric_definition``) and
``roam.quality.cycles.DEFINITION``.

Drift guard: ``tests/test_metric_definition_sidecars.py`` asserts every
constant is non-empty and that no two constants are byte-identical (i.e.
two different definitions must say different things).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Impact / blast-radius family — shared by cmd_impact and cmd_preflight.
# ---------------------------------------------------------------------------

BLAST_RADIUS_AFFECTED_SYMBOLS = "transitive reverse-BFS over edges; bounded by --depth + --max-callers + --timeout."

BLAST_RADIUS_AFFECTED_FILES = "distinct file_path values of every symbol reached by the reverse-BFS."

# W335/W342 Pattern-3a — the UNCAPPED blast-radius metric shared by
# cmd_impact's ``affected_symbols_total`` / ``affected_files_total`` and
# cmd_preflight's ``_check_blast_radius`` (``nx.descendants`` over the
# reverse graph). Both commands MUST report the same number for the same
# target; this string names the exact computation so the parity is
# provable and cannot drift. The capped display list (``--max-callers``)
# is a presentation cap only — the total is always this full transitive
# closure, matching preflight's gate so the two read identically when
# used together for change-safety.
BLAST_RADIUS_AFFECTED_TOTAL = (
    "full transitive reverse-reachability (nx.descendants) over call+ref edges from the target,"
    " excluding the target's own file; identical to preflight's blast-radius gate (uncapped)."
)

WEIGHTED_IMPACT_DEFINITION = (
    "sum of personalized PageRank (alpha=0.85) over the reverse-graph from the target, rounded to 6 decimals."
)

REACH_PCT_DEFINITION = "affected_symbols / total_symbols_in_graph * 100, rounded to 1 decimal."


# ---------------------------------------------------------------------------
# Complexity family — shared by cmd_complexity and cmd_preflight.
# ---------------------------------------------------------------------------

COGNITIVE_COMPLEXITY_DEFINITION = (
    "SonarSource-compatible cognitive complexity from symbol_metrics.cognitive_complexity."
)

CYCLOMATIC_COMPLEXITY_DEFINITION = "McCabe cyclomatic complexity (decision points + 1)."

NESTING_DEPTH_DEFINITION = "max nested block depth in the AST (if/for/while/try) from symbol_metrics.nesting_depth."


# ---------------------------------------------------------------------------
# Health-score family — primarily cmd_health, but the definition is the
# canonical reference for any command that surfaces health_score.
# ---------------------------------------------------------------------------

HEALTH_SCORE_DEFINITION = (
    "weighted geometric mean (0-100) of 5 sigmoid health factors: tangle_ratio,"
    " god_components, bottlenecks, layer_violations, file_health (+coverage if available)."
)

TANGLE_RATIO_DEFINITION = "fraction of symbols inside non-trivial SCCs; higher = more cyclic coupling."


# ---------------------------------------------------------------------------
# Dead-export family — cmd_dead.
# ---------------------------------------------------------------------------

DEAD_EXPORT_DEFINITION = "exported symbols (kind in function/class/method) with zero inbound edges in edges table."

DEAD_EXPORT_ACTION_DEFINITION = (
    "SAFE = no production consumers (graph proof); REVIEW = heuristic (API/barrel/test-only);"
    " INTENTIONAL = name/docstring scaffolding pattern."
)


# ---------------------------------------------------------------------------
# Invariants family — cmd_invariants.
# ---------------------------------------------------------------------------

INVARIANTS_DEFINITION = "implicit contracts discovered from signature, param_count, caller-count, file-spread, callees."

BREAKING_RISK_DEFINITION = "caller_count * max(file_spread, 1) — heuristic damage score, NOT a probability."


# ---------------------------------------------------------------------------
# Risk-level family — cmd_preflight.
# ---------------------------------------------------------------------------

PREFLIGHT_RISK_LEVEL_DEFINITION = (
    "max severity across 6 dimensions: blast, tests, complexity, coupling, conventions, fitness."
)


# ---------------------------------------------------------------------------
# Coverage-gap family — cmd_coverage_gaps.
# Two different metrics (gate-reachability vs preset rule violation)
# share the same envelope on different branches, so they get distinct
# definition fields and must be stamped accordingly.
# ---------------------------------------------------------------------------

COVERAGE_PCT_DEFINITION = (
    "fraction of exported entry points with a BFS path (max --max-depth hops) to any --gate symbol."
)

GATE_VIOLATION_DEFINITION = "preset/config file-level rule violations: missing test file or test_count < min_required."


# ---------------------------------------------------------------------------
# Caller-metric label family — shared by every command that emits a
# ``caller_metric_definition`` sidecar on its summary. The four canonical
# label values live in ``docs/concepts/caller-metrics.md``; the literal
# string ``"raw_edge_rows"`` is the most-stamped one (cmd_uses,
# cmd_context, cmd_invariants, cmd_guard, cmd_plan_refactor) so it gets
# extracted here per Pattern 3a (W342). Other label values
# (``direct_in_degree`` / ``distinct_caller_tuples`` /
# ``transitive_upstream_bfs``) are stamped at only one site today and
# stay inline until a second adopter shows up.
# ---------------------------------------------------------------------------

CALLER_METRIC_RAW = "raw_edge_rows"


# ---------------------------------------------------------------------------
# Compliance-score family — cmd_audit_trail_conformance + cmd_article_12_check.
# These two commands report DIFFERENT scores under similar names (chain-of-
# custody score vs repo-level readiness score); per CLAUDE.md Pattern 3a the
# sidecar definitions live here so the strings cannot drift independently.
# Wording follows the agentic-assurance guardrails: "maps to" / "supports
# evidence for", never "certifies" / "makes compliant".
# ---------------------------------------------------------------------------

CHAIN_COMPLIANCE_SCORE_DEFINITION = (
    "fraction of 6 per-record integrity checks passed (chain hash, timestamps, actor,"
    " reproducibility, verdict+rationale, retention); maps to EU AI Act Article 12 event logging."
)

ARTICLE_12_READINESS_DEFINITION = (
    "fraction of 6 repo-level artifact-existence checks passed (audit-trail dir, records,"
    " retention doc, technical docs, attestation surface, high-risk heuristic); supports"
    " evidence for EU AI Act Article 12 readiness — does NOT certify compliance."
)


# ---------------------------------------------------------------------------
# Drift guard — every constant in this module is exported so the
# sidecar-test can iterate them and assert non-empty / no-collision.
# ---------------------------------------------------------------------------

ALL_DEFINITIONS: dict[str, str] = {
    "BLAST_RADIUS_AFFECTED_SYMBOLS": BLAST_RADIUS_AFFECTED_SYMBOLS,
    "BLAST_RADIUS_AFFECTED_FILES": BLAST_RADIUS_AFFECTED_FILES,
    "BLAST_RADIUS_AFFECTED_TOTAL": BLAST_RADIUS_AFFECTED_TOTAL,
    "WEIGHTED_IMPACT_DEFINITION": WEIGHTED_IMPACT_DEFINITION,
    "REACH_PCT_DEFINITION": REACH_PCT_DEFINITION,
    "COGNITIVE_COMPLEXITY_DEFINITION": COGNITIVE_COMPLEXITY_DEFINITION,
    "CYCLOMATIC_COMPLEXITY_DEFINITION": CYCLOMATIC_COMPLEXITY_DEFINITION,
    "NESTING_DEPTH_DEFINITION": NESTING_DEPTH_DEFINITION,
    "HEALTH_SCORE_DEFINITION": HEALTH_SCORE_DEFINITION,
    "TANGLE_RATIO_DEFINITION": TANGLE_RATIO_DEFINITION,
    "DEAD_EXPORT_DEFINITION": DEAD_EXPORT_DEFINITION,
    "DEAD_EXPORT_ACTION_DEFINITION": DEAD_EXPORT_ACTION_DEFINITION,
    "INVARIANTS_DEFINITION": INVARIANTS_DEFINITION,
    "BREAKING_RISK_DEFINITION": BREAKING_RISK_DEFINITION,
    "PREFLIGHT_RISK_LEVEL_DEFINITION": PREFLIGHT_RISK_LEVEL_DEFINITION,
    "COVERAGE_PCT_DEFINITION": COVERAGE_PCT_DEFINITION,
    "GATE_VIOLATION_DEFINITION": GATE_VIOLATION_DEFINITION,
    "CHAIN_COMPLIANCE_SCORE_DEFINITION": CHAIN_COMPLIANCE_SCORE_DEFINITION,
    "ARTICLE_12_READINESS_DEFINITION": ARTICLE_12_READINESS_DEFINITION,
    "CALLER_METRIC_RAW": CALLER_METRIC_RAW,
}
