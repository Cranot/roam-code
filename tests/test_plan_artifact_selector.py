"""Unit tests for roam.plan.compiler ArtifactSelector policy.

The policy was empirically locked 2026-05-28 from a 670-cell Sonnet 4.6
benchmark + 19-cell Opus 4.8 spike. See the final evaluation notes.
"""

from __future__ import annotations

from roam.plan.compiler import (
    _ARTIFACT_POLICY,
    compile_for_artifact,
    compile_plan,
    select_artifact,
)


def test_artifact_policy_has_all_known_procedures():
    """The policy table must cover every procedure the classifier emits."""
    procedures = {
        "structural_dead",
        "structural_coupling",
        "structural_complexity",
        "structural_cycle",
        "structural_callers",
        "structural_blast",
        "structural_query",
        "synthesis_query",
        "trace_query",
        "freeform_explore",
    }
    assert procedures.issubset(_ARTIFACT_POLICY.keys()), f"Policy missing: {procedures - _ARTIFACT_POLICY.keys()}"


def test_artifact_policy_only_valid_envelope_types():
    """Policy values must be one of facts/lean/full/contract."""
    valid = {"facts", "lean", "full", "contract"}
    for proc, art in _ARTIFACT_POLICY.items():
        assert art in valid, f"{proc} → {art!r} not in {valid}"


def test_structural_complexity_routes_to_contract():
    """R9 empirical lock: structural_complexity → contract.

    facts+answer_contract envelope scored 95.0 mean quality on
    structural_complexity vs vanilla's 86.1 (n=4 vs n=7, Sonnet 4.6,
    full re-judge 2026-05-29). This is the only per-procedure
    clean-win for facts-contract. Other procedures are mixed; keep
    other policies stable until more Opus data lands.
    """
    plan = compile_plan(
        "Among modules in src/roam/commands/, identify one cmd_*.py "
        "file that is likely a god-component (high cognitive complexity "
        "or large line count)."
    )
    assert plan.procedure == "structural_complexity"
    assert select_artifact(plan) == "contract"


def test_facts_contract_envelope_has_answer_contract():
    """facts_contract envelope must include answer_contract template."""
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    env = plan.to_facts_contract_envelope()
    assert env["schema"] == "roam-plan-v0-facts-contract"
    plan_obj = env["plan"]
    assert "answer_contract" in plan_obj
    assert isinstance(plan_obj["answer_contract"], list)
    assert len(plan_obj["answer_contract"]) >= 4  # at least 4 contract bullets
    # Must include named_paths and forbidden_paths like facts envelope
    assert "named_paths" in plan_obj
    assert "forbidden_paths" in plan_obj


def test_facts_contract_is_procedure_specialized():
    """R10: each procedure should get a contract tailored to its answer shape."""
    coupling = compile_plan("Find files coupled to src/roam/cli.py").to_facts_contract_envelope()
    trace = compile_plan("Trace the login flow from CLI to database").to_facts_contract_envelope()

    coupling_contract = coupling["plan"]["answer_contract"]
    trace_contract = trace["plan"]["answer_contract"]

    # Coupling and trace must NOT share the same generic contract
    assert coupling_contract != trace_contract
    # Coupling contract should mention 'pairs' or 'strength' or 'coupled'
    coupling_text = " ".join(coupling_contract).lower()
    assert any(k in coupling_text for k in ("pair", "strength", "coupl"))
    # Trace contract should mention chain/step/hop
    trace_text = " ".join(trace_contract).lower()
    assert any(k in trace_text for k in ("chain", "step", "hop", "walk"))


def test_facts_contract_includes_roam_starter_when_applicable():
    """R10: structural procedures should get a copy-pasteable starter command."""
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    env = plan.to_facts_contract_envelope()
    plan_obj = env["plan"]
    # structural_coupling should have a starter
    assert "roam_starter" in plan_obj
    assert "coupling" in plan_obj["roam_starter"]


def test_compile_plan_cache_returns_same_object():
    """R10: identical (task, cwd) compiles return the same PlanV0 instance.

    Cache makes repeated compiles for the same task literally free
    (~300ns) instead of paying the regex + path-extract cost each time.
    Useful for production agent workflows that hit the same task pattern
    many times.
    """
    from roam.plan.compiler import clear_plan_cache
    from roam.plan.compiler import compile_plan as cp

    clear_plan_cache()
    task = "Find files coupled to src/roam/cli.py"
    p1 = cp(task)
    p2 = cp(task)
    # Same object identity = cache hit
    assert p1 is p2


def test_clear_plan_cache_invalidates():
    """R10: clear_plan_cache() returns fresh compilation."""
    from roam.plan.compiler import clear_plan_cache
    from roam.plan.compiler import compile_plan as cp

    task = "Find files coupled to src/roam/cli.py"
    p1 = cp(task)
    clear_plan_cache()
    p2 = cp(task)
    # Different instances (fresh compile)
    assert p1 is not p2
    # But same content
    assert p1.procedure == p2.procedure
    assert p1.likely_files == p2.likely_files


def test_facts_contract_omits_starter_for_freeform_post_v04_revert():
    """v0.4.1 (2026-05-29 evening): v0.4 added a `roam ask` starter for
    freeform_explore. Phase B graded it as a clean regression — score/$
    18.8 (worst of all variants, beneath vanilla 22.4). Forcing the
    dispatcher made agents do extra round-trips without resolution.
    Reverted: freeform_explore has no starter again. The R9 minimal
    envelope is the production winner; surface envelope-shape lever is
    exhausted. See the envelope-regression notes."""
    plan = compile_plan("investigate why authentication is slow")
    env = plan.to_facts_contract_envelope()
    plan_obj = env["plan"]
    assert "roam_starter" not in plan_obj


def test_select_artifact_freeform_routes_to_facts():
    """R7 calibration: freeform_explore policy is "facts".

    R10.1 supersession: freeform_explore is a fall-through procedure with
    inherently low classifier confidence, so the confidence gate now
    routes it to "full" instead of "facts". The policy table still maps
    freeform_explore → facts (preserved for high-confidence overrides),
    but select_artifact applies the gate. See test_low_confidence_falls_back_to_full.
    """
    from roam.plan.compiler import _ARTIFACT_POLICY

    plan = compile_plan("investigate why authentication fails")
    assert plan.procedure == "freeform_explore"
    # Policy intent preserved
    assert _ARTIFACT_POLICY["freeform_explore"] == "facts"
    # W51 (per-procedure thresholds): freeform threshold is now 0.30,
    # so typical conf=0.35 fall-through tasks land on the specialized
    # "facts" policy rather than the generic "full" fallback.
    assert select_artifact(plan) == "facts"


def test_select_artifact_trace_routes_to_lean():
    """R7: trace_query wins with LEAN envelope."""
    plan = compile_plan("Trace the login flow from CLI through to the database")
    assert plan.procedure == "trace_query"
    assert select_artifact(plan) == "lean"


def test_select_artifact_structural_routes_to_full():
    """R7: structural_* wins with full envelope on Sonnet 4.6."""
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    assert plan.procedure == "structural_coupling"
    assert select_artifact(plan) == "full"


def test_compile_for_artifact_returns_correct_schema():
    """compile_for_artifact returns (envelope, label) tuple.

    W33: when probe fires for a structural task with a named
    path, the selector now prefers `l1_probe` over `full` (it contains the
    precomputed answer, not just metadata). Either label is acceptable; the
    schema reflects which one was picked.
    """
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    env, label = compile_for_artifact(plan)
    assert label in ("full", "l1_probe")  # depends on whether index probe fires
    if label == "l1_probe":
        assert env["schema"] == "roam-plan-v0-l1-probe"
    else:
        assert env["schema"] == "roam-plan-v0"


def test_facts_envelope_has_minimum_fields():
    """Facts envelope must include task, named_paths, forbidden_paths, repo_head."""
    plan = compile_plan("Refactor _normalize_aliases in src/roam/mcp_server.py")
    env = plan.to_facts_envelope()
    assert env["schema"] == "roam-plan-v0-facts"
    plan_obj = env["plan"]
    assert "task" in plan_obj
    assert "named_paths" in plan_obj
    assert "forbidden_paths" in plan_obj
    # named_paths should include the explicitly-mentioned file
    assert any("mcp_server.py" in p for p in plan_obj["named_paths"])


def test_facts_envelope_drops_routing_hint():
    """Facts envelope MUST NOT include recommended_first_command.

    The empirical finding: removing the routing hint is what gives
    facts its edge on capable models (Opus 4.8 quality 84.8 vs
    plan-v01 71.0 with the hint).
    """
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    env = plan.to_facts_envelope()
    plan_obj = env["plan"]
    assert "recommended_first_command" not in plan_obj
    assert "procedure" not in plan_obj


def test_lean_envelope_includes_routing_hint():
    """Lean envelope DOES include recommended_first_command.

    This is the v01 lean variant — for synthesis/trace where the
    routing hint helps but the verbose plan distracts.
    """
    plan = compile_plan("Trace the login flow from CLI to database")
    env = plan.to_lean_envelope()
    plan_obj = env["plan"]
    assert "recommended_first_command" in plan_obj
    assert "procedure" in plan_obj
    assert "forbidden_paths" in plan_obj


def test_classifier_confidence_high_on_clean_match():
    """R10.1: clean single-subtype structural match → confidence ≥ 0.85."""
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    assert plan.procedure == "structural_coupling"
    # Single subtype match + explicit path → high confidence
    assert plan.classifier_confidence >= 0.85


def test_classifier_confidence_low_on_freeform_fallthrough():
    """R10.1: pure freeform_explore (regex fall-through) → confidence ≤ 0.5."""
    plan = compile_plan("investigate why authentication is slow")
    assert plan.procedure == "freeform_explore"
    assert plan.classifier_confidence <= 0.5


def test_low_confidence_falls_back_to_full():
    """R10.1 + W51: low-confidence classifications still skip the
    specialized contract — but the threshold is now per-procedure.

    W51 lowered freeform_explore's threshold to 0.30 (was 0.60) so
    freeform tasks no longer trip the gate. Other procedures
    (structural_complexity = 0.60, trace_query = 0.70, stack_trace_fix =
    0.85) keep the original behavior.

    Demonstrated on structural_complexity at a thin/ambiguous match.
    """
    from roam.plan.compiler import (
        _ARTIFACT_POLICY,
        _PER_PROCEDURE_CONF_THRESHOLD,
    )

    # Thin "complexity" match → structural_complexity with ambiguous
    # compound score (≥2 subtypes hit) → confidence 0.55 or below.
    plan = compile_plan("Find complexity and dead code and cycles in this repo")
    assert plan.procedure.startswith("structural_")
    threshold = _PER_PROCEDURE_CONF_THRESHOLD.get(plan.procedure, 0.60)
    if plan.classifier_confidence < threshold:
        # Gate trips → "full" fallback
        assert select_artifact(plan) == "full"
    else:
        # High enough confidence → policy applies
        assert select_artifact(plan) == _ARTIFACT_POLICY.get(plan.procedure, "full")


def test_high_confidence_keeps_specialized_policy():
    """R10.1: high-confidence structural_complexity still routes to contract."""
    plan = compile_plan(
        "Among modules in src/roam/commands/, identify one cmd_*.py "
        "file that is likely a god-component (high cognitive complexity)."
    )
    assert plan.procedure == "structural_complexity"
    assert plan.classifier_confidence >= 0.60
    assert select_artifact(plan) == "contract"


def test_full_envelope_includes_everything():
    """Full envelope has all 7 v0.1 fields."""
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    env = plan.to_envelope()
    plan_obj = env["plan"]
    for field in (
        "task",
        "procedure",
        "likely_files",
        "required_checks",
        "forbidden_paths",
        "plan_quality",
        "recommended_first_command",
    ):
        assert field in plan_obj, f"Full envelope missing {field}"


def test_facts_contract_surfaces_parallel_tools_for_coupling():
    """v0.4 (2026-05-29): structural_coupling envelope MUST surface a
    structured `recommended_parallel_tools` list. The biggest documented
    win (-84% tokens via roam_coupling+roam_deps PARALLEL) was previously
    only mentioned in English inside `recommended_first_command`. Agents
    didn't latch on. Typed-list form makes it parseable."""
    plan = compile_plan("Find files coupled to src/roam/cli.py")
    assert plan.procedure == "structural_coupling"
    env = plan.to_facts_contract_envelope()
    plan_obj = env["plan"]
    assert plan_obj["recommended_parallel_tools"] == ["roam_coupling", "roam_deps"]


def test_batch_search_starter_fires_on_multi_symbol_task():
    """v0.4: when the task names 3+ symbols (via backticks or paths),
    `roam_starter` is overridden to `roam batch-search` — one call
    instead of N sequential single-symbol lookups (-69 to -79% tokens
    per global CLAUDE.md). Defends a documented but previously
    un-surfaced win."""
    plan = compile_plan("Compare implementations of `parseToken`, `validateToken`, and `signToken`")
    env = plan.to_facts_contract_envelope()
    starter = env["plan"]["roam_starter"]
    assert starter.startswith("roam --json batch-search")
    assert "parseToken" in starter
    assert "validateToken" in starter
    assert "signToken" in starter
