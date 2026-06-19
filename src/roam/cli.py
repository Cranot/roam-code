"""Click CLI entry point with lazy-loaded subcommands."""

from __future__ import annotations

import os
import sys

# Fix Unicode output on Windows consoles (cp1253, cp1252, etc.)
if sys.platform == "win32" and not os.environ.get("PYTHONIOENCODING"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import click

# Lazy-loading command group: imports command modules only when invoked.
# This avoids importing networkx (~500ms) on every CLI call.
# Total: 241 invokable command names (234 canonical commands + 7 alias names).
# If this changes, update README.md, CLAUDE.md, llms-install.md, and docs copy.
# Deprecated commands map to a structured record. When a user invokes a
# deprecated name we still resolve it (no breaking change) and print a
# note on stderr. Each entry is:
#   {"replacement": str, "reason": str, "removal_version": str}
# Backwards-compat: a bare-string value still works (treated as
# `{"replacement": <string>}`).
#
# Seven legacy aliases land here in v12.18: they continue to work but emit
# a deprecation note (stderr) and, in --json mode, a `summary.deprecation_warning`
# entry in the envelope. Removal is planned for a future major release;
# leave `removal_version` unset until a target is firm so we don't promise
# a version we haven't agreed to ship.
_DEPRECATED_COMMANDS: dict[str, dict] = {
    "digest": {"replacement": "trends", "reason": "alias for 'trends'"},
    "math": {"replacement": "algo", "reason": "alias for 'algo'"},
    "refs": {"replacement": "uses", "reason": "alias for 'uses'"},
    "snapshot": {"replacement": "trends", "reason": "alias for 'trends'"},
    "trend": {"replacement": "trends", "reason": "alias for 'trends'"},
    "onboard": {"replacement": "understand", "reason": "alias for 'understand'"},
    "churn": {"replacement": "weather", "reason": "alias for 'weather'"},
}

# Module-level cross-talk slot read by `roam.output.formatter.json_envelope`
# to inject `summary.deprecation_warning` when a deprecated alias was the
# invoked name. Reset on each cli() entry. Lives at module level (not on
# ctx.obj) so the formatter — which has no Click context — can read it.
_ACTIVE_DEPRECATION_NOTICE: str | None = None

# Canonical, alphabetically-sorted list of commands that honour the global
# `--sarif` flag. Surfaced in help text (avoids the "lists 7, supports 14"
# drift caught by W22.3) and enforced by tests/test_sarif_consumer_list.py
# (AST-scans cmd_*.py for `ctx.obj["sarif"]` consumers and asserts the set
# matches this tuple exactly). Adding a new SARIF consumer means adding it
# here AND in the consumer module — the test fails on either drift.
# Per CLAUDE.md Constraint 8: closed enumeration over free string composition.
_SARIF_CONSUMERS: tuple[str, ...] = (
    "affected-tests",
    "algo",
    "audit-trail-conformance-check",
    "auth-gaps",
    "bus-factor",
    "check-rules",
    "clones",
    "complexity",
    "critique",
    "dark-matter",
    "dead",
    "delete-check",
    "duplicates",
    "fan",
    "flag-dead",
    "health",
    "hotspots",
    "impact",
    "laws",
    "llm-smells",
    "missing-index",
    "n1",
    "orphan-imports",
    "orphan-routes",
    "over-fetch",
    "partition",
    "py-modern",
    "py-types",
    "rules",
    "secrets",
    "smells",
    "stale-refs",
    "supply-chain",
    "taint",
    "test-impact",
    "verify-imports",
    "vulns",
)


def _set_active_deprecation_notice(text: str | None) -> None:
    """Set the deprecation-notice string visible to the JSON envelope builder."""
    global _ACTIVE_DEPRECATION_NOTICE
    _ACTIVE_DEPRECATION_NOTICE = text


def _get_active_deprecation_notice() -> str | None:
    """Return the active deprecation notice (or None) for the current invocation."""
    return _ACTIVE_DEPRECATION_NOTICE


def _format_deprecation_notice(name: str, record: dict) -> str:
    """Build the canonical deprecation-warning string for *name*.

    Format matches the contract documented in CLAUDE.md/the W3.3 ticket:
        DEPRECATION: 'math' is an alias for 'algo' and will be removed
        in a future release. Use `roam algo` instead.
    """
    replacement = record.get("replacement") or ""
    msg = (
        f"DEPRECATION: '{name}' is an alias for '{replacement}' "
        f"and will be removed in a future release. "
        f"Use `roam {replacement}` instead."
    )
    if record.get("removal_version"):
        msg += f" Removal target: v{record['removal_version']}."
    return msg


def _deprecation_replacement(name: str) -> str | None:
    """Return the replacement command for a deprecated name, or None."""
    record = _DEPRECATED_COMMANDS.get(name)
    if record is None:
        return None
    if isinstance(record, str):
        return record
    return record.get("replacement")


def _deprecation_record(name: str) -> dict | None:
    """Return the full deprecation record for a name, normalized."""
    record = _DEPRECATED_COMMANDS.get(name)
    if record is None:
        return None
    if isinstance(record, str):
        return {"replacement": record, "reason": None, "removal_version": None}
    return {
        "replacement": record.get("replacement"),
        "reason": record.get("reason"),
        "removal_version": record.get("removal_version"),
    }


# Source-of-truth for compound-recipe linting. ``tests/test_compound_recipe_registry.py``
# (W1297) asserts every internal-command-name string literal referenced by a
# compound recipe (ask recipes, report PRESETS, ``_run_roam([...])`` /
# ``runner.invoke(cli, [...])`` / ``args = ["--json", ...]`` invocations,
# the mcp ``_COMPOUND_REGISTRY``, etc.) resolves against ``_COMMANDS.keys()
# | _DEPRECATED_COMMANDS.keys()`` — closes the ``vuln``/``vulns`` typo class
# named in CLAUDE.md Pattern 5. Closed enumeration per CLAUDE.md Constraint 8.
_COMMANDS = {
    "index": ("roam.commands.cmd_index", "index"),
    "map": ("roam.commands.cmd_map", "map_cmd"),
    "module": ("roam.commands.cmd_module", "module"),
    "file": ("roam.commands.cmd_file", "file_cmd"),
    "symbol": ("roam.commands.cmd_symbol", "symbol"),
    "trace": ("roam.commands.cmd_trace", "trace"),
    "deps": ("roam.commands.cmd_deps", "deps"),
    "health": ("roam.commands.cmd_health", "health"),
    "clusters": ("roam.commands.cmd_clusters", "clusters"),
    "layers": ("roam.commands.cmd_layers", "layers"),
    "cycles": ("roam.commands.cmd_cycles", "cycles"),
    "weather": ("roam.commands.cmd_weather", "weather"),
    "churn": ("roam.commands.cmd_weather", "weather"),
    "dead": ("roam.commands.cmd_dead", "dead"),
    "search": ("roam.commands.cmd_search", "search"),
    "grep": ("roam.commands.cmd_grep", "grep_cmd"),
    "uses": ("roam.commands.cmd_uses", "uses"),
    # — ``refs`` is a grep-familiar alias for ``uses``. Agents
    # reaching for "find references to X" hit this name first; the real
    # work happens in cmd_uses through the indexed call/import graph
    # (no string-literal / comment false positives).
    "refs": ("roam.commands.cmd_uses", "uses"),
    "impact": ("roam.commands.cmd_impact", "impact_cmd"),
    "dict-consistency": ("roam.commands.cmd_dict_consistency", "dict_consistency"),
    "owner": ("roam.commands.cmd_owner", "owner"),
    "coupling": ("roam.commands.cmd_coupling", "coupling"),
    "fan": ("roam.commands.cmd_fan", "fan"),
    "diff": ("roam.commands.cmd_diff", "diff_cmd"),
    "describe": ("roam.commands.cmd_describe", "describe"),
    "test-map": ("roam.commands.cmd_testmap", "test_map"),
    "sketch": ("roam.commands.cmd_sketch", "sketch"),
    "context": ("roam.commands.cmd_context", "context"),
    "safe-delete": ("roam.commands.cmd_safe_delete", "safe_delete"),
    "pr-risk": ("roam.commands.cmd_pr_risk", "pr_risk"),
    "split": ("roam.commands.cmd_split", "split"),
    "risk": ("roam.commands.cmd_risk", "risk"),
    "why": ("roam.commands.cmd_why", "why"),
    "auth-gaps": ("roam.commands.cmd_auth_gaps", "auth_gaps_cmd"),
    "coverage-gaps": ("roam.commands.cmd_coverage_gaps", "coverage_gaps"),
    "report": ("roam.commands.cmd_report", "report"),
    "understand": ("roam.commands.cmd_understand", "understand_cmd"),
    "onboard": ("roam.commands.cmd_understand", "understand_cmd"),
    "affected-tests": ("roam.commands.cmd_affected_tests", "affected_tests"),
    "complexity": ("roam.commands.cmd_complexity", "complexity"),
    "py-types": ("roam.commands.cmd_py_types", "py_types"),
    "py-modern": ("roam.commands.cmd_py_modern", "py_modern"),
    "pytest-fixtures": ("roam.commands.cmd_pytest_fixtures", "pytest_fixtures"),
    "hover": ("roam.commands.cmd_hover", "hover"),
    "at": ("roam.commands.cmd_at", "at"),
    "debt": ("roam.commands.cmd_debt", "debt"),
    "conventions": ("roam.commands.cmd_conventions", "conventions"),
    "bus-factor": ("roam.commands.cmd_bus_factor", "bus_factor"),
    "entry-points": ("roam.commands.cmd_entry_points", "entry_points"),
    "breaking": ("roam.commands.cmd_breaking", "breaking"),
    "safe-zones": ("roam.commands.cmd_safe_zones", "safe_zones"),
    "doc-staleness": ("roam.commands.cmd_doc_staleness", "doc_staleness"),
    "stale-refs": ("roam.commands.cmd_stale_refs", "stale_refs"),
    "lsp": ("roam.commands.cmd_lsp", "lsp"),
    "docs-coverage": ("roam.commands.cmd_docs_coverage", "docs_coverage"),
    "docs-index": ("roam.commands.cmd_docs_index", "docs_index"),
    "suggest-refactoring": ("roam.commands.cmd_suggest_refactoring", "suggest_refactoring"),
    "plan-refactor": ("roam.commands.cmd_plan_refactor", "plan_refactor"),
    "fn-coupling": ("roam.commands.cmd_fn_coupling", "fn_coupling"),
    "alerts": ("roam.commands.cmd_alerts", "alerts"),
    "fitness": ("roam.commands.cmd_fitness", "fitness"),
    "findings": ("roam.commands.cmd_findings", "findings"),
    "patterns": ("roam.commands.cmd_patterns", "patterns"),
    "preflight": ("roam.commands.cmd_preflight", "preflight"),
    "permit": ("roam.commands.cmd_permit", "permit_cmd"),
    "postmortem": ("roam.commands.cmd_postmortem", "postmortem_cmd"),
    "pr-replay": ("roam.commands.cmd_pr_replay", "pr_replay_cmd"),
    "article-12-check": ("roam.commands.cmd_article_12_check", "article_12_check_cmd"),
    "capabilities": ("roam.commands.cmd_capabilities", "capabilities_cmd"),
    "skill-generate": ("roam.commands.cmd_skill_generate", "skill_generate_cmd"),
    "compare": ("roam.commands.cmd_compare", "compare_cmd"),
    "compatibility": ("roam.commands.cmd_compatibility", "compatibility"),
    "migration-plan": ("roam.commands.cmd_migration_plan", "migration_plan_cmd"),
    "guard": ("roam.commands.cmd_guard", "guard"),
    "init": ("roam.commands.cmd_init", "init"),
    "config": ("roam.commands.cmd_config", "config"),
    "tour": ("roam.commands.cmd_tour", "tour"),
    "diagnose": ("roam.commands.cmd_diagnose", "diagnose"),
    "ws": ("roam.commands.cmd_ws", "ws"),
    "visualize": ("roam.commands.cmd_visualize", "visualize"),
    "x-lang": ("roam.commands.cmd_xlang", "xlang"),
    "algo": ("roam.commands.cmd_math", "math_cmd"),
    "math": ("roam.commands.cmd_math", "math_cmd"),
    "agent-opt": ("roam.commands.cmd_agent_opt", "agent_opt_cmd"),
    "observability-opt": ("roam.commands.cmd_observability_opt", "observability_opt_cmd"),
    "n1": ("roam.commands.cmd_n1", "n1_cmd"),
    "minimap": ("roam.commands.cmd_minimap", "minimap"),
    "migration-safety": ("roam.commands.cmd_migration_safety", "migration_safety_cmd"),
    "over-fetch": ("roam.commands.cmd_over_fetch", "over_fetch_cmd"),
    "missing-index": ("roam.commands.cmd_missing_index", "missing_index_cmd"),
    "orphan-routes": ("roam.commands.cmd_orphan_routes", "orphan_routes_cmd"),
    "api-drift": ("roam.commands.cmd_api_drift", "api_drift_cmd"),
    "annotate": ("roam.commands.cmd_annotate", "annotate"),
    "annotations": ("roam.commands.cmd_annotate", "annotations"),
    "dark-matter": ("roam.commands.cmd_dark_matter", "dark_matter"),
    "pr-diff": ("roam.commands.cmd_pr_diff", "pr_diff_cmd"),
    "budget": ("roam.commands.cmd_budget", "budget"),
    "effects": ("roam.commands.cmd_effects", "effects"),
    "side-effects": ("roam.commands.cmd_side_effects", "side_effects_cmd"),
    "idempotency": ("roam.commands.cmd_idempotency", "idempotency_cmd"),
    "causal-graph": ("roam.commands.cmd_causal_graph", "causal_graph_cmd"),
    "tx-boundaries": ("roam.commands.cmd_tx_boundaries", "tx_boundaries_cmd"),
    "attest": ("roam.commands.cmd_attest", "attest"),
    "capsule": ("roam.commands.cmd_capsule", "capsule"),
    "path-coverage": ("roam.commands.cmd_path_coverage", "path_coverage"),
    "plugins": ("roam.commands.cmd_plugins", "plugins_cmd"),
    "test-pyramid": ("roam.commands.cmd_test_pyramid", "test_pyramid"),
    "test-hermeticity": ("roam.commands.cmd_test_hermeticity", "test_hermeticity"),
    "index-stats": ("roam.commands.cmd_index_stats", "index_stats"),
    "telemetry": ("roam.commands.cmd_telemetry", "telemetry"),
    "orphan-imports": ("roam.commands.cmd_orphan_imports", "orphan_imports"),
    "boundary": ("roam.commands.cmd_boundary", "boundary"),
    "changelog": ("roam.commands.cmd_changelog", "changelog"),
    "graph-export": ("roam.commands.cmd_graph_export", "graph_export"),
    "graph-stats": ("roam.commands.cmd_graph_stats", "graph_stats"),
    "graph-diff": ("roam.commands.cmd_graph_diff", "graph_diff_cmd"),
    "architecture-drift": ("roam.commands.cmd_architecture_drift", "architecture_drift_cmd"),
    "help-search": ("roam.commands.cmd_help_search", "help_search"),
    "timeline": ("roam.commands.cmd_timeline", "timeline"),
    "pr-prep": ("roam.commands.cmd_pr_prep", "pr_prep"),
    "pr-analyze": ("roam.commands.cmd_pr_analyze", "pr_analyze"),
    "pr-bundle": ("roam.commands.cmd_pr_bundle", "pr_bundle_group"),
    "pr-comment-render": ("roam.commands.cmd_pr_comment_render", "pr_comment_render"),
    "metrics-push": ("roam.commands.cmd_metrics_push", "metrics_push"),
    "audit-trail-verify": ("roam.commands.cmd_audit_trail_verify", "audit_trail_verify"),
    "audit-trail-export": ("roam.commands.cmd_audit_trail_export", "audit_trail_export_cmd"),
    "audit-trail-conformance-check": (
        "roam.commands.cmd_audit_trail_conformance",
        "audit_trail_conformance_check_cmd",
    ),
    "rules-validate": ("roam.commands.cmd_rules_validate", "rules_validate_cmd"),
    "dogfood": ("roam.commands.cmd_dogfood", "dogfood"),
    "dogfood-aggregate": ("roam.commands.cmd_dogfood_aggregate", "dogfood_aggregate"),
    "suppress": ("roam.commands.cmd_suppress", "suppress"),
    "stats": ("roam.commands.cmd_stats", "stats"),
    "why-fail": ("roam.commands.cmd_why_fail", "why_fail"),
    "recommend": ("roam.commands.cmd_recommend", "recommend"),
    "api": ("roam.commands.cmd_api", "api"),
    "exit-codes": ("roam.commands.cmd_exit_codes", "exit_codes"),
    "version": ("roam.commands.cmd_version", "version"),
    "disambiguate": ("roam.commands.cmd_disambiguate", "disambiguate"),
    "audit": ("roam.commands.cmd_audit", "audit"),
    "pre-commit": ("roam.commands.cmd_pre_commit", "pre_commit"),
    "mcp-status": ("roam.commands.cmd_mcp_status", "mcp_status"),
    "test-impact": ("roam.commands.cmd_test_impact", "test_impact"),
    "recipes": ("roam.commands.cmd_recipes", "recipes"),
    "forecast": ("roam.commands.cmd_forecast", "forecast"),
    "plan": ("roam.commands.cmd_plan", "plan"),
    "compile": ("roam.commands.cmd_compile", "compile_"),
    "compile-stats": ("roam.commands.cmd_compile_stats", "compile_stats"),
    "compile-cache": ("roam.commands.cmd_compile_cache", "compile_cache"),
    "envelope-diff": ("roam.commands.cmd_envelope_diff", "envelope_diff"),
    "dispatch-trace": ("roam.commands.cmd_dispatch_trace", "dispatch_trace"),
    "magic-numbers": ("roam.commands.cmd_magic_numbers", "magic_numbers"),
    "compiler-health": ("roam.commands.cmd_compiler_health", "compiler_health"),
    "compiler-corpus": ("roam.commands.cmd_compiler_corpus", "compiler_corpus"),
    "adversarial": ("roam.commands.cmd_adversarial", "adversarial"),
    "cut": ("roam.commands.cmd_cut", "cut"),
    "invariants": ("roam.commands.cmd_invariants", "invariants"),
    "bisect": ("roam.commands.cmd_bisect", "bisect"),
    "intent": ("roam.commands.cmd_intent", "intent"),
    "simulate": ("roam.commands.cmd_simulate", "simulate"),
    "closure": ("roam.commands.cmd_closure", "closure"),
    "rules": ("roam.commands.cmd_rules", "rules"),
    "fingerprint": ("roam.commands.cmd_fingerprint", "fingerprint"),
    "spectral": ("roam.commands.cmd_spectral", "spectral"),
    "orchestrate": ("roam.commands.cmd_orchestrate", "orchestrate"),
    "mutate": ("roam.commands.cmd_mutate", "mutate"),
    "vuln-map": ("roam.commands.cmd_vuln_map", "vuln_map_cmd"),
    "vuln-reach": ("roam.commands.cmd_vuln_reach", "vuln_reach"),
    "ingest-trace": ("roam.commands.cmd_ingest_trace", "ingest_trace_cmd"),
    "hotspots": ("roam.commands.cmd_hotspots", "hotspots"),
    "why-slow": ("roam.commands.cmd_why_slow", "why_slow"),
    "schema": ("roam.commands.cmd_schema", "schema_cmd"),
    "search-semantic": ("roam.commands.cmd_search_semantic", "search_semantic"),
    "relate": ("roam.commands.cmd_relate", "relate"),
    "agent-export": ("roam.commands.cmd_agent_export", "agent_export_cmd"),
    "agent-plan": ("roam.commands.cmd_agent_plan", "agent_plan"),
    "agent-context": ("roam.commands.cmd_agent_context", "agent_context"),
    "agents-md": ("roam.commands.cmd_agents_md", "agents_md_cmd"),
    "syntax-check": ("roam.commands.cmd_syntax_check", "syntax_check"),
    "vibe-check": ("roam.commands.cmd_vibe_check", "vibe_check"),
    "llm-smells": ("roam.commands.cmd_llm_smells", "llm_smells"),
    "ai-readiness": ("roam.commands.cmd_ai_readiness", "ai_readiness"),
    "check-rules": ("roam.commands.cmd_check_rules", "check_rules_command"),
    "codeowners": ("roam.commands.cmd_codeowners", "codeowners"),
    "dashboard": ("roam.commands.cmd_dashboard", "dashboard"),
    "drift": ("roam.commands.cmd_drift", "drift"),
    "dev-profile": ("roam.commands.cmd_dev_profile", "dev_profile"),
    "secrets": ("roam.commands.cmd_secrets", "secrets"),
    "supply-chain": ("roam.commands.cmd_supply_chain", "supply_chain"),
    "simulate-departure": ("roam.commands.cmd_simulate_departure", "simulate_departure"),
    "suggest-reviewers": ("roam.commands.cmd_suggest_reviewers", "suggest_reviewers"),
    "verify": ("roam.commands.cmd_verify", "verify"),
    "verification-contract": ("roam.commands.cmd_verification_contract", "verification_contract"),
    "verdict": ("roam.commands.cmd_verdict", "verdict"),
    "proof-bundle": ("roam.commands.cmd_proof_bundle", "proof_bundle"),
    "guard-pr": ("roam.commands.cmd_guard_pr", "guard_pr"),
    "guard-history": ("roam.commands.cmd_guard_history", "guard_history"),
    "guard-doctor": ("roam.commands.cmd_guard_doctor", "guard_doctor"),
    "guard-rules": ("roam.commands.cmd_guard_rules", "guard_rules_group"),
    "guard-diff": ("roam.commands.cmd_guard_diff", "guard_diff"),
    "guard-init": ("roam.commands.cmd_guard_init", "guard_init"),
    "guard-clean": ("roam.commands.cmd_guard_clean", "guard_clean"),
    "bench-compile": ("roam.commands.cmd_bench", "bench_compile"),
    "api-changes": ("roam.commands.cmd_api_changes", "api_changes"),
    "test-gaps": ("roam.commands.cmd_test_gaps", "test_gaps"),
    "ai-ratio": ("roam.commands.cmd_ai_ratio", "ai_ratio"),
    "duplicates": ("roam.commands.cmd_duplicates", "duplicates"),
    "partition": ("roam.commands.cmd_partition", "partition"),
    "affected": ("roam.commands.cmd_affected", "affected"),
    "semantic-diff": ("roam.commands.cmd_semantic_diff", "semantic_diff"),
    "trends": ("roam.commands.cmd_trends", "trends"),
    # Aliases for the consolidated trends command. Older
    # docs and agent recipes still mention `roam trend` / `roam digest`;
    # we keep them as discoverable aliases instead of breaking the
    # documented surface.
    "trend": ("roam.commands.cmd_trends", "trends"),
    "digest": ("roam.commands.cmd_trends", "trends"),
    "snapshot": ("roam.commands.cmd_trends", "trends"),
    "endpoints": ("roam.commands.cmd_endpoints", "endpoints"),
    "watch": ("roam.commands.cmd_watch", "watch"),
    # ``cmd_mcp`` is a thin Click wrapper around ``mcp_server.mcp_cmd``
    # that swaps the synchronous full-reindex freshness check for a
    # fast mtime check (typically <100 ms). On large indexes the legacy
    # path spent ~36 s in ``_ensure_fresh_index`` which blew past Claude
    # Code's 30 s MCP connect timeout. The wrapper imports
    # ``roam.mcp_server`` lazily inside the function body so info-only
    # paths (``--help``, ``--card``, ``--list-tools``) stay cheap.
    "mcp": ("roam.commands.cmd_mcp", "mcp"),
    "doctor": ("roam.commands.cmd_doctor", "doctor"),
    "reset": ("roam.commands.cmd_reset", "reset"),
    "clean": ("roam.commands.cmd_clean", "clean"),
    "hooks": ("roam.commands.cmd_hooks", "hooks"),
    "smells": ("roam.commands.cmd_smells", "smells"),
    "mcp-setup": ("roam.commands.cmd_mcp_setup", "mcp_setup"),
    "verify-imports": ("roam.commands.cmd_verify_imports", "verify_imports_cmd"),
    "vulns": ("roam.commands.cmd_vulns", "vulns"),
    "metrics": ("roam.commands.cmd_metrics", "metrics"),
    "congestion": ("roam.commands.cmd_congestion", "congestion"),
    "adrs": ("roam.commands.cmd_adrs", "adrs"),
    "flag-dead": ("roam.commands.cmd_flag_dead", "flag_dead"),
    "test-scaffold": ("roam.commands.cmd_test_scaffold", "test_scaffold"),
    "sbom": ("roam.commands.cmd_sbom", "sbom"),
    "triage": ("roam.commands.cmd_triage", "triage"),
    "ci-setup": ("roam.commands.cmd_ci_setup", "ci_setup"),
    "clones": ("roam.commands.cmd_clones", "clones"),
    "retrieve": ("roam.commands.cmd_retrieve", "retrieve"),
    "critique": ("roam.commands.cmd_critique", "critique"),
    "fleet": ("roam.commands.cmd_fleet", "fleet"),
    "ask": ("roam.commands.cmd_ask", "ask"),
    "workflow": ("roam.commands.cmd_workflow", "workflow"),
    "taint": ("roam.commands.cmd_taint", "taint"),
    "cga": ("roam.commands.cmd_cga", "cga"),
    "eval-retrieve": ("roam.commands.cmd_eval_retrieve", "eval_retrieve"),
    "oracle": ("roam.commands.cmd_oracle", "oracle"),
    "index-export": ("roam.commands.cmd_index_bundle", "index_export"),
    "index-import": ("roam.commands.cmd_index_bundle", "index_import"),
    "refs-text": ("roam.commands.cmd_refs_text", "refs_text_cmd"),
    "delete-check": ("roam.commands.cmd_delete_check", "delete_check_cmd"),
    "history-grep": ("roam.commands.cmd_history_grep", "history_grep_cmd"),
    "surface": ("roam.commands.cmd_surface", "surface"),
    "commands": ("roam.commands.cmd_commands", "commands_cmd"),
    "explain-command": ("roam.commands.cmd_explain_command", "explain_command"),
    "db-check": ("roam.commands.cmd_db_check", "db_check"),
    "batch-search": ("roam.commands.cmd_batch_search", "batch_search_cmd"),
    "complete": ("roam.commands.cmd_complete", "complete"),
    "memory": ("roam.commands.cmd_memory", "memory_group"),
    "runs": ("roam.commands.cmd_runs", "runs_group"),
    "laws": ("roam.commands.cmd_laws", "laws_group"),
    "constitution": ("roam.commands.cmd_constitution", "constitution_group"),
    "next": ("roam.commands.cmd_next", "next_cmd"),
    "brief": ("roam.commands.cmd_brief", "brief_cmd"),
    "replay": ("roam.commands.cmd_replay", "replay_cmd"),
    "agent-score": ("roam.commands.cmd_agent_score", "agent_score_cmd"),
    "mode": ("roam.commands.cmd_mode", "mode_cmd"),
    "intent-check": ("roam.commands.cmd_intent_check", "intent_check_cmd"),
    "lease": ("roam.commands.cmd_lease", "lease_group"),
    "evidence-diff": ("roam.commands.cmd_evidence_diff", "evidence_diff"),
    "evidence-doctor": ("roam.commands.cmd_evidence_doctor", "evidence_doctor"),
    "evidence-oscal": ("roam.commands.cmd_evidence_oscal", "evidence_oscal"),
}

# Command categories for organized --help display
_CATEGORIES = {
    "Getting Started": [
        "ask",
        "workflow",
        "index",
        "index-export",
        "index-import",
        "watch",
        "init",
        "hooks",
        "reset",
        "clean",
        "config",
        "doctor",
        "understand",
        # `onboard` lives in _DEPRECATED_COMMANDS (use `understand`); kept
        # invokable, but removed from the categorised --help panel so it
        # no longer reads as a recommended starting verb.
        "dashboard",
        "tour",
        "describe",
        "minimap",
        "agent-export",
        "ws",
        "schema",
        "mcp",
        "mcp-setup",
        "mcp-status",
        "ci-setup",
        "adrs",
        "audit",
        "changelog",
        "exit-codes",
        "help-search",
        "plugins",
        "pre-commit",
        "recipes",
        "version",
        "index-stats",
        "stats",
        "telemetry",
        "surface",
        "commands",
        "explain-command",
        "db-check",
    ],
    "Daily Workflow": [
        "preflight",
        "permit",
        "postmortem",
        "pr-replay",
        "guard",
        # Roam Guard family (Wave 11-20): the PR-gating surface.
        "guard-pr",
        "guard-doctor",
        "guard-init",
        "guard-clean",
        "guard-diff",
        "guard-history",
        "guard-rules",
        "proof-bundle",
        "verdict",
        "verification-contract",
        # Wave 24: benchmark harness for compiler vs vanilla vs static.
        "bench-compile",
        "agent-plan",
        "agent-context",
        "pr-risk",
        "pr-prep",
        "pr-analyze",
        "pr-bundle",
        "pr-comment-render",
        "rules-validate",
        "metrics-push",
        "audit-trail-verify",
        "audit-trail-export",
        "audit-trail-conformance-check",
        "article-12-check",
        "capabilities",
        "skill-generate",
        "compare",
        "migration-plan",
        "dogfood",
        "dogfood-aggregate",
        "suppress",
        "pr-diff",
        "evidence-diff",
        "evidence-doctor",
        "evidence-oscal",
        "api-changes",
        "semantic-diff",
        "test-gaps",
        "affected",
        "attest",
        "adversarial",
        "verify",
        "verify-imports",
        "diff",
        "context",
        "hover",
        "retrieve",
        "critique",
        "fleet",
        "affected-tests",
        "test-impact",
        "diagnose",
        "why-fail",
        "recommend",
        "api",
        "disambiguate",
        "annotate",
        "annotations",
        "plan",
        "compile",
        "compile-stats",
        "compile-cache",
        "envelope-diff",
        "dispatch-trace",
        "syntax-check",
        "triage",
        "oracle",
        "memory",
        "runs",
        "laws",
        "constitution",
        "agents-md",
        "next",
        "brief",
        "replay",
        "agent-score",
        "mode",
        "intent-check",
        "lease",
    ],
    "Codebase Health": [
        "health",
        "smells",
        "magic-numbers",
        "compiler-health",
        "compiler-corpus",
        "vibe-check",
        "llm-smells",
        "ai-readiness",
        "check-rules",
        "dict-consistency",
        "ai-ratio",
        "trends",
        "weather",
        # `churn` lives in _DEPRECATED_COMMANDS (use `weather`); kept
        # invokable but removed from the categorised --help panel.
        "timeline",
        "debt",
        "complexity",
        "py-types",
        "py-modern",
        "pytest-fixtures",
        "test-hermeticity",
        "algo",
        "agent-opt",
        "observability-opt",
        "n1",
        "over-fetch",
        "missing-index",
        "alerts",
        "fitness",
        "forecast",
        "bisect",
        "ingest-trace",
        "hotspots",
        "why-slow",
        "eval-retrieve",
        "boundary",
    ],
    "Architecture": [
        "map",
        "graph-export",
        "graph-stats",
        "graph-diff",
        "architecture-drift",
        "layers",
        "clusters",
        "cycles",
        "spectral",
        "coupling",
        "dark-matter",
        "effects",
        "side-effects",
        "idempotency",
        "causal-graph",
        "tx-boundaries",
        "cut",
        "simulate",
        "orchestrate",
        "partition",
        "entry-points",
        "patterns",
        "safe-zones",
        "visualize",
        "x-lang",
        "fingerprint",
        "clones",
    ],
    "Exploration": [
        "search",
        "at",
        "search-semantic",
        "batch-search",
        "complete",
        "grep",
        "refs-text",
        "history-grep",
        "file",
        "symbol",
        "module",
        "trace",
        "deps",
        "uses",
        "fan",
        "impact",
        "relate",
        "endpoints",
        "metrics",
        "findings",
    ],
    "Reports & CI": [
        "report",
        "budget",
        "breaking",
        "coverage-gaps",
        "auth-gaps",
        "orphan-routes",
        "bus-factor",
        "simulate-departure",
        "suggest-reviewers",
        "dev-profile",
        "owner",
        "codeowners",
        "drift",
        "secrets",
        "supply-chain",
        "risk",
        "migration-safety",
        "api-drift",
        "path-coverage",
        "capsule",
        "rules",
        "vuln-map",
        "vuln-reach",
        "vulns",
        "sbom",
        "taint",
        "cga",
        "congestion",
        "compatibility",
    ],
    "Refactoring": [
        "dead",
        "orphan-imports",
        "flag-dead",
        "duplicates",
        "safe-delete",
        "delete-check",
        "split",
        "fn-coupling",
        "doc-staleness",
        "docs-coverage",
        "docs-index",
        "stale-refs",
        "lsp",
        "suggest-refactoring",
        "plan-refactor",
        "conventions",
        "sketch",
        "test-map",
        "test-pyramid",
        "why",
        "pr-risk",
        "invariants",
        "intent",
        "closure",
        "mutate",
        "test-scaffold",
    ],
}

_PLUGIN_COMMANDS_LOADED = False


def _ensure_plugin_commands_loaded() -> None:
    """Merge discovered plugin commands into the CLI command map once."""
    global _PLUGIN_COMMANDS_LOADED
    if _PLUGIN_COMMANDS_LOADED:
        return
    _PLUGIN_COMMANDS_LOADED = True

    try:
        from roam.plugins import get_plugin_commands

        for cmd_name, target in get_plugin_commands().items():
            if cmd_name in _COMMANDS:
                continue
            _COMMANDS[cmd_name] = target
    except Exception:  # noqa: BLE001 — plugin loading must never break core CLI behavior
        return


def _emit_deprecation_notice_for_args(args: list[str]) -> None:
    if not args:
        return

    cmd_name = args[0]
    record = _deprecation_record(cmd_name)
    if not record or not record.get("replacement"):
        return

    msg = _format_deprecation_notice(cmd_name, record)
    click.echo(msg, err=True)
    _set_active_deprecation_notice(msg)


def _is_unknown_command_error(exc: click.UsageError) -> bool:
    return "No such command" in str(exc)


def _bad_command_token(args: list[str]) -> str:
    bad = args[0] if args else ""
    return bad.strip("'\"")


def _close_command_matches(bad: str) -> list[str]:
    import difflib

    _ensure_plugin_commands_loaded()
    # W1083-followup-2: align to canonical n=2 (5 of 6 sites use n=2; cli.py was the outlier)
    return difflib.get_close_matches(bad, list(_COMMANDS.keys()), n=2, cutoff=0.6)


def _recipe_hint_for_bad_command(bad: str) -> str | None:
    if len(bad) < 6:
        return None
    try:
        from roam.ask.classifier import classify

        matches = classify(bad)
    except Exception:  # noqa: BLE001 — command suggestions must never break unknown-command errors
        return None

    if matches and matches[0][1] >= 0.5:
        recipe = matches[0][0]
        return f'`roam ask "{bad}"` (matches recipe: {recipe.name})'
    return None


def _unknown_command_usage_error(args: list[str], exc: click.UsageError) -> click.UsageError | None:
    if not _is_unknown_command_error(exc):
        return None

    bad = _bad_command_token(args)
    if not bad:
        return None

    close = _close_command_matches(bad)
    if close:
        suggestions = ", ".join(f"`roam {name}`" for name in close)
        return click.UsageError(f"No such command: '{bad}'. Did you mean {suggestions}?")

    recipe_hint = _recipe_hint_for_bad_command(bad)
    if recipe_hint:
        return click.UsageError(f"No such command: '{bad}'. Try {recipe_hint}.")

    return None


class LazyGroup(click.Group):
    """A Click group that lazy-loads command modules on first access."""

    _GLOBAL_FLAGS = {
        "--json",
        "--compact",
        "--agent",
        "--sarif",
        "--include-excluded",
        "--detail",
    }
    _GLOBAL_VALUE_OPTIONS = {"--budget"}
    _AMBIGUOUS_FLAG_VS_VALUE = {"--agent"}

    def parse_args(self, ctx, args):
        """Accept known global options before or after the subcommand.

        Click normally requires group options before the command
        (``roam --compact health``). Older docs and agent memories often use
        ``roam health --compact``; normalising that shape avoids a hard "No
        such option" while keeping command-specific parsing unchanged.
        """
        if args:
            args = self._normalise_global_option_position(ctx, list(args))
        return super().parse_args(ctx, args)

    def _normalise_global_option_position(self, ctx, args: list[str]) -> list[str]:
        if not args:
            return args

        cmd_index = self._find_command_index(args)
        if cmd_index is None or cmd_index >= len(args) - 1:
            return args

        before = args[:cmd_index]
        command = args[cmd_index]
        after = args[cmd_index + 1 :]
        moved, kept = self._split_post_command_options(ctx, command, after)

        if not moved:
            return args
        return before + moved + [command] + kept

    def _find_command_index(self, args: list[str]) -> int | None:
        idx = 0
        while idx < len(args):
            token = args[idx]
            if token == "--":
                return None
            if not token.startswith("-"):
                return idx
            idx += self._pre_command_option_span(args, idx)
        return None

    def _pre_command_option_span(self, args: list[str], idx: int) -> int:
        token = args[idx]
        if token in self._GLOBAL_VALUE_OPTIONS and idx + 1 < len(args):
            return 2
        return 1

    def _split_post_command_options(self, ctx, command: str, tokens: list[str]) -> tuple[list[str], list[str]]:
        moved: list[str] = []
        kept: list[str] = []
        idx = 0
        while idx < len(tokens):
            values, destination, consumed = self._classify_post_command_option(ctx, command, tokens, idx)
            if destination == "move":
                moved.extend(values)
            else:
                kept.extend(values)
            idx += consumed
        return moved, kept

    def _classify_post_command_option(
        self, ctx, command: str, tokens: list[str], idx: int
    ) -> tuple[list[str], str, int]:
        token = tokens[idx]
        if token in self._GLOBAL_FLAGS:
            destination = "keep" if self._subcommand_keeps_flag(ctx, command, tokens, idx) else "move"
            return [token], destination, 1
        if token in self._GLOBAL_VALUE_OPTIONS and idx + 1 < len(tokens):
            return [token, tokens[idx + 1]], "move", 2
        if self._is_global_value_assignment(token):
            return [token], "move", 1
        return [token], "keep", 1

    def _subcommand_keeps_flag(self, ctx, command: str, tokens: list[str], idx: int) -> bool:
        token = tokens[idx]
        return self._command_owns_option(ctx, command, token) or self._looks_like_subcommand_value_flag(tokens, idx)

    def _looks_like_subcommand_value_flag(self, tokens: list[str], idx: int) -> bool:
        """Return True for ambiguous flags used as subcommand value options."""
        token = tokens[idx]
        has_value = idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-")
        return token in self._AMBIGUOUS_FLAG_VS_VALUE and has_value

    def _is_global_value_assignment(self, token: str) -> bool:
        return any(token.startswith(f"{opt}=") for opt in self._GLOBAL_VALUE_OPTIONS)

    def _command_owns_option(self, ctx, command: str, option: str) -> bool:
        """Return True when a subcommand declares *option* itself."""
        try:
            cmd = self.get_command(ctx, command)
        except (AttributeError, ImportError, KeyError, TypeError):
            return False
        if cmd is None:
            return False
        for param in getattr(cmd, "params", ()):
            opts = tuple(getattr(param, "opts", ()) or ())
            secondary = tuple(getattr(param, "secondary_opts", ()) or ())
            if option in opts or option in secondary:
                return True
        return False

    def list_commands(self, ctx):
        _ensure_plugin_commands_loaded()
        return sorted(_COMMANDS.keys())

    def get_command(self, ctx, cmd_name):
        # built-ins resolve without paying the
        # ~100ms entry-point-discovery cost. Only when the requested
        # command isn't in the static map do we fall back to plugin
        # discovery. Saves 100ms per CLI invocation for the 99% case
        # of users with no third-party roam plugins installed.
        if cmd_name in _COMMANDS:
            module_path, attr_name = _COMMANDS[cmd_name]
        else:
            _ensure_plugin_commands_loaded()
            if cmd_name not in _COMMANDS:
                return None
            module_path, attr_name = _COMMANDS[cmd_name]
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, attr_name)

    def resolve_command(self, ctx, args):
        """Resolve a subcommand, with a did-you-mean hint on typos.

        v12.14 — Click's default ``"No such command: 'contxt'"`` ends
        the conversation; we can do better. When the requested name
        isn't in ``_COMMANDS`` we look for the closest existing names
        by edit distance and surface them in the UsageError so the
        agent can retry with the right command in one turn.

        also surface a deprecation note on stderr when the
        invoked command is in ``_DEPRECATED_COMMANDS`` so users know
        about a planned rename / replacement.
        """
        # Clear any leftover notice from a previous invocation in the same
        # Python process (matters for `CliRunner`-driven tests where many
        # commands run inside one interpreter).
        _set_active_deprecation_notice(None)
        _emit_deprecation_notice_for_args(args)
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as exc:
            hinted = _unknown_command_usage_error(args, exc)
            if hinted is not None:
                raise hinted from exc
            raise

    def invoke(self, ctx):
        """Override invoke to map unhandled exceptions to standardized exit codes.

        RoamError subclasses (IndexMissingError, GateFailureError, etc.) carry
        their own exit_code and are handled by Click's ClickException machinery.
        This override catches *unexpected* exceptions (KeyError, TypeError, etc.)
        and maps them to EXIT_ERROR (1) instead of letting Python print a traceback
        with exit code 1 (which is ambiguous).

        The mode-enforcement gate (W13.2) runs inside the group
        callback (`cli()` below) — not here — because that's when
        ``ctx.obj`` (and the ``--override-mode`` flag) is populated.
        """
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit:
            # click.Context.exit() raises this — propagate as-is
            raise
        except (click.Abort, click.ClickException, SystemExit):
            # Click-managed exceptions — propagate as-is
            raise
        except Exception as exc:
            from roam.exit_codes import EXIT_ERROR

            click.echo(f"Error: {exc}", err=True)
            ctx.exit(EXIT_ERROR)

    def format_help(self, ctx, formatter):
        """Short "Start here" panel — the 5 verbs + init/doctor/ask.

        The default ``roam --help`` was a 154-line flat dump that buried
        the 5-verb mental model (the buyable narrative) under 38
        "Getting Started" entries and a 73-name "More Commands" list.
        First impression on a new install was "this is a lot of
        commands" rather than "this is a clear 5-step workflow".

        This panel surfaces only what a new user needs to start. Power
        users use ``roam --help-all`` for the full categorised view, or
        ``roam <command> --help`` for any specific command.
        """
        _ensure_plugin_commands_loaded()
        self.format_usage(ctx, formatter)
        formatter.write("\n")
        if self.help:
            formatter.write(self.help + "\n\n")

        formatter.write("Start here — the 5 verbs cover ~80% of agent workflows:\n\n")
        starter = [
            ("roam init", "initialize this repo (one-time)"),
            ("roam understand", "what is this codebase? (briefing)"),
            ("roam context <symbol>", "files + lines to read before editing"),
            ("roam preflight <symbol>", "what breaks if I change this?"),
            ("git diff | roam critique", "review my patch before merge"),
            ('roam ask "<question>"', "free-form intent — 24 recipes"),
        ]
        for cmd, blurb in starter:
            formatter.write(f"  {cmd:30s} {blurb}\n")

        formatter.write("\nCommon next steps:\n\n")
        common = [
            ("roam doctor", "diagnose your install (20 checks)"),
            ("roam tour", "5-minute guided walkthrough"),
            ("roam mcp-setup <editor>", "wire roam into your AI agent"),
            ("roam --help-all", f"every command ({len(_COMMANDS)} total)"),
        ]
        for cmd, blurb in common:
            formatter.write(f"  {cmd:30s} {blurb}\n")

        # Global options — these are flags on the `roam` group itself, valid
        # before OR after the subcommand (see LazyGroup._normalise_global_option_position).
        # Surfaced here so `--detail`, `--json`, `--agent` etc. are discoverable
        # from the short help. The previous custom panel omitted them entirely;
        # W19.2 flagged `--detail` in particular as accepted-but-undocumented.
        formatter.write("\nGlobal options (work with any command):\n\n")
        global_opts = [
            ("--json", "output JSON envelope instead of text"),
            ("--compact", "compact output (TSV tables, minimal envelope)"),
            ("--agent", "agent mode (JSON + compact + 500-token budget)"),
            ("--detail", "show full detailed output instead of compact summary"),
            ("--sarif", f"SARIF 2.1.0 output (supported by: {', '.join(_SARIF_CONSUMERS)})"),
            ("--budget N", "max output tokens (0 = unlimited)"),
            ("--include-excluded", "include files normally excluded by .roamignore"),
            ("--override-mode", "bypass mode-based command blocking (logs to audit trail)"),
            ("--ci", "CI mode: stricter defaults (over-fetch --leaks-only, pr-bundle --strict + --strict-resolved)"),
            ("--help-all", "list every command (no categories)"),
        ]
        for flag, blurb in global_opts:
            formatter.write(f"  {flag:30s} {blurb}\n")

        formatter.write("\nDocs: https://roam-code.com/docs   ·   roam exit-codes for CI integration\n")
        # V6 — persist any newly-cached short-help entries (kept for any
        # call paths that still hit the AST extractor).
        _save_short_help_cache_if_dirty()


# ---------------------------------------------------------------------------
# Mode enforcement at dispatch (W13.2 follow-through)
# ---------------------------------------------------------------------------
#
# `roam.modes.policy.check_command_allowed()` (R16 substrate) was only
# consumed by `roam mode --check` and `roam intent-check`. Today it is
# wired into the LazyGroup.invoke() so that any command can be blocked
# when the active mode doesn't allow it.
#
# Two intentional constraints, both per the task spec:
#
#   1. Enforcement is OPT-IN via `ROAM_MODE_ENFORCEMENT=1`. Flipping it
#      on by default would break long-standing workflows where a repo
#      has a stale `.roam/active_mode = read_only` from a previous
#      session and the next `roam attest` call expects to run, not
#      exit 5. The opt-in keeps the substrate ready for agents that
#      WANT a hard gate while leaving humans/CI on the permissive path.
#
#   2. Meta-commands ALWAYS run, even with enforcement on. These are
#      the commands an agent needs to recover from a wrong-mode state:
#      `mode`, `intent-check`, `surface`, `doctor`, plus help/version
#      affordances. Without these the gate becomes a deadlock (you
#      can't switch mode without running `roam mode`, and `roam mode`
#      itself would be blocked).
#
# The gate is also fail-open: if `find_project_root()` raises, if the
# policy module is unimportable, or anything else trips, we let the
# command through and emit a stderr hint. Never block dispatch over a
# gate bug — that would be worse than the bug it's protecting against.

_MODE_ALWAYS_ALLOWED: frozenset[str] = frozenset(
    {
        # Meta / discovery
        "mode",
        "intent-check",
        "help",
        "help-search",
        "help-all",
        "surface",
        "doctor",
        "exit-codes",
        "version",
        "recipes",
        "explain-command",
        "db-check",
        "telemetry",
        "config",
        # Bootstrap: an agent stuck in the wrong mode still needs to
        # be able to wire the harness up. Keep these uncategorised
        # meta operations available regardless.
        "plugins",
        "mcp-status",
        # Index bootstrap: an agent that can't index can't do anything
        # else. A fresh repo has no `.roam/active_mode`, so the default
        # resolves to `safe_edit`, which does not list `init`/`index`
        # in its allow-set. Without these here, exporting
        # `ROAM_MODE_ENFORCEMENT=1` (e.g. in CI) creates a
        # chicken-and-egg deadlock: the user can't initialise the
        # index, and can't switch mode meaningfully until they have
        # one. Keep these always-on so the bootstrap path is reachable
        # from any mode in any repo state.
        "init",
        "index",
    }
)


def _resolve_invoked_command_name(ctx: click.Context) -> str | None:
    """Best-effort resolution of the bare subcommand for *ctx*.

    Returns ``None`` when no subcommand can be identified — the caller
    treats ``None`` as "let Click handle whatever it is", which is the
    right thing to do for ``roam`` with no args or ``roam --help``.
    """
    name = getattr(ctx, "invoked_subcommand", None)
    if name:
        return name
    # Fallback: peek at ctx.protected_args / ctx.args. These hold the
    # tokens Click hasn't consumed yet at the point invoke() runs.
    args = list(getattr(ctx, "protected_args", []) or []) + list(getattr(ctx, "args", []) or [])
    for tok in args:
        if tok and not tok.startswith("-"):
            return tok
    return None


def _mode_enforcement_enabled() -> bool:
    return os.environ.get("ROAM_MODE_ENFORCEMENT", "").strip() in {"1", "true", "yes", "on"}


def _canonical_mode_command(cmd_name: str) -> str:
    return _deprecation_replacement(cmd_name) or cmd_name


def _mode_gate_should_skip(cmd_name: str, canonical: str) -> bool:
    if canonical in _MODE_ALWAYS_ALLOWED or cmd_name in _MODE_ALWAYS_ALLOWED:
        return True
    return canonical not in _COMMANDS and cmd_name not in _COMMANDS


def _mode_gate_dependencies():
    try:
        from roam.db.connection import find_project_root
        from roam.modes import check_command_allowed

        return find_project_root, check_command_allowed
    except ImportError:
        return None


def _mode_gate_decision(canonical: str):
    dependencies = _mode_gate_dependencies()
    if dependencies is None:
        return None

    find_project_root, check_command_allowed = dependencies
    try:
        repo_root = find_project_root()
        allowed, reason = check_command_allowed(repo_root, canonical)
        return repo_root, allowed, reason
    except Exception:  # noqa: BLE001 — mode policy lookup is opt-in and must fail open
        return None


def _active_mode_name(repo_root) -> str:
    try:
        from roam.modes import resolve_mode

        return resolve_mode(repo_root).name
    except Exception:  # noqa: BLE001 — override warning should survive mode metadata failures
        return "<unknown>"


def _log_mode_override(canonical: str, active_name: str, repo_root) -> None:
    try:
        from roam.runs.helpers import auto_log

        auto_log(
            {
                "command": canonical,
                "summary": {
                    "verdict": f"override-mode used: active={active_name}",
                    "partial_success": True,
                },
            },
            action="mode-override",
            target=canonical,
            repo_root=repo_root,
        )
    except Exception:  # noqa: BLE001 — audit logging must never block the override
        pass


def _allow_mode_override(canonical: str, repo_root) -> None:
    active_name = _active_mode_name(repo_root)
    click.echo(
        f"WARNING: Mode enforcement overridden. Active mode: {active_name}. Command: {canonical}.",
        err=True,
    )
    _log_mode_override(canonical, active_name, repo_root)


def _block_mode_command(ctx: click.Context, reason: str) -> None:
    from roam.exit_codes import EXIT_GATE_FAILURE

    click.echo(f"BLOCKED: {reason}", err=True)
    click.echo(
        "Pass `--override-mode` to bypass for this one call, or `roam mode <name>` to switch modes.",
        err=True,
    )
    ctx.exit(EXIT_GATE_FAILURE)


def _enforce_mode_gate(ctx: click.Context) -> None:
    """Run the opt-in mode-enforcement gate before command dispatch."""
    if not _mode_enforcement_enabled():
        return

    cmd_name = _resolve_invoked_command_name(ctx)
    if not cmd_name:
        return

    canonical = _canonical_mode_command(cmd_name)
    if _mode_gate_should_skip(cmd_name, canonical):
        return

    decision = _mode_gate_decision(canonical)
    if decision is None:
        return
    repo_root, allowed, reason = decision

    if allowed:
        return

    obj = ctx.ensure_object(dict)
    if bool(obj.get("override_mode", False)):
        _allow_mode_override(canonical, repo_root)
        return

    _block_mode_command(ctx, reason)


# `_short_help_via_ast` is called 126x by `roam --help`,
# each call AST-parses the cmd_*.py file. ~640ms total. Disk cache keyed
# by source-file mtime collapses repeat invocations to a single dict lookup.
_SHORT_HELP_CACHE_PATH = os.path.expanduser("~/.roam-cli-cache/short-help.json")
_short_help_disk_cache: dict | None = None
_short_help_disk_cache_dirty = False


def _load_short_help_cache() -> dict:
    global _short_help_disk_cache
    if _short_help_disk_cache is not None:
        return _short_help_disk_cache
    try:
        import json as _json

        with open(_SHORT_HELP_CACHE_PATH, encoding="utf-8") as fh:
            _short_help_disk_cache = _json.load(fh)
    except (OSError, ValueError):
        _short_help_disk_cache = {}
    return _short_help_disk_cache


def _save_short_help_cache_if_dirty() -> None:
    """Persist the in-memory short-help cache to disk atomically.

    Two parallel ``roam --help`` invocations used to race on the naked
    ``open(path, "w")``: one writer's bytes could land mid-stream of the
    other's, producing a file that ``json.load`` rejected on next read
    (and silently nuked via the ``except (OSError, ValueError)`` in
    ``_load_short_help_cache``). The atomic temp-file + rename pattern
    closes the window — the last writer's payload wins cleanly, and no
    intermediate state is ever visible to a reader.
    """
    global _short_help_disk_cache_dirty
    if not _short_help_disk_cache_dirty or _short_help_disk_cache is None:
        return
    try:
        # W17.1 atomic_io consolidation — use shared helper (was local _atomic_write_json).
        from roam.atomic_io import atomic_write_json

        atomic_write_json(_SHORT_HELP_CACHE_PATH, _short_help_disk_cache, indent=None)
        _short_help_disk_cache_dirty = False
    except OSError:
        # Cache is best-effort: a write failure means the next CLI
        # invocation will re-parse the AST. Never fail the parent
        # command on a cache hiccup.
        pass


def _short_help_source_path(module_path: str) -> str:
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel = module_path.replace(".", os.sep) + ".py"
    return os.path.join(pkg_root, rel)


def _source_mtime(src_path: str) -> float:
    try:
        return os.path.getmtime(src_path)
    except OSError:
        return 0.0


def _cached_short_help(cache: dict, cache_key: str, mtime: float) -> str | None:
    cached = cache.get(cache_key)
    if cached and cached.get("mtime") == mtime:
        return cached.get("text") or None
    return None


def _parse_short_help_source(src_path: str):
    try:
        import ast as _ast

        with open(src_path, encoding="utf-8") as fh:
            return _ast.parse(fh.read(), filename=src_path)
    except (OSError, SyntaxError):
        return None


def _click_short_help_text(doc: str) -> str:
    first_para = doc.split("\n\n", 1)[0].strip()
    first_line = " ".join(first_para.split())
    if len(first_line) > 60:
        return first_line[:57] + "..."
    return first_line


def _target_function_docstring(tree, attr_name: str) -> str | None:
    import ast as _ast

    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == attr_name:
            return _ast.get_docstring(node) or ""
    return None


def _store_short_help(cache: dict, cache_key: str, mtime: float, text: str) -> None:
    global _short_help_disk_cache_dirty
    cache[cache_key] = {"mtime": mtime, "text": text}
    _short_help_disk_cache_dirty = True


def _short_help_via_ast(cmd_name: str) -> str | None:
    """Extract a Click short-help string from cmd_*.py without importing.

    Click's ``get_short_help_str()`` reads the docstring of the function
    decorated with ``@click.command``, truncates at the first sentence,
    and limits to 60 chars. We reproduce that via Python's ``ast`` —
    no Click load, no cmd module import, no cascade of heavy deps.

    Returns ``None`` when the cmd file or expected attribute is absent;
    the caller falls back to the live ``self.get_command()`` path.
    """
    target = _COMMANDS.get(cmd_name)
    if not target:
        return None

    module_path, attr_name = target
    src_path = _short_help_source_path(module_path)
    if not os.path.isfile(src_path):
        return None

    mtime = _source_mtime(src_path)
    cache = _load_short_help_cache()
    cache_key = f"{module_path}:{attr_name}"
    cached_text = _cached_short_help(cache, cache_key, mtime)
    if cached_text is not None:
        return cached_text

    tree = _parse_short_help_source(src_path)
    if tree is None:
        return None

    doc = _target_function_docstring(tree, attr_name)
    if doc is None:
        return None

    short_help = _click_short_help_text(doc)
    _store_short_help(cache, cache_key, mtime, short_help)
    return short_help


def _check_python_version(issues: list[str]) -> None:
    if sys.version_info < (3, 10):
        issues.append(f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.10")


def _check_importable(module_name: str, label: str, issues: list[str]) -> None:
    try:
        __import__(module_name)
    except ImportError:
        issues.append(f"{label} not installed")


def _check_git_available(issues: list[str]) -> None:
    import shutil

    if not shutil.which("git"):
        issues.append("git not found in PATH")


def _check_sqlite_available(issues: list[str]) -> None:
    import sqlite3

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:  # pragma: no cover
        issues.append(f"SQLite error: {exc}")


def _setup_check_issues() -> list[str]:
    issues: list[str] = []
    _check_python_version(issues)
    _check_importable("tree_sitter", "tree-sitter", issues)
    _check_importable("tree_sitter_language_pack", "tree-sitter-language-pack", issues)
    _check_git_available(issues)
    _check_sqlite_available(issues)
    return issues


def _emit_setup_check_result(ctx: click.Context, issues: list[str]) -> None:
    if issues:
        click.echo(f"roam-code setup incomplete: {'; '.join(issues)}")
        ctx.exit(1)

    click.echo("roam-code ready")
    ctx.exit(0)


def _run_check(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager callback for --check: run critical install checks and exit.

    Validates the five minimum requirements for roam-code to function:
      1. Python >= 3.10
      2. tree-sitter importable
      3. tree-sitter-language-pack importable
      4. git on PATH
      5. SQLite in-memory DB usable

    Exits 0 on success ("roam-code ready"), 1 on any failure.
    """
    if not value or ctx.resilient_parsing:
        return

    _emit_setup_check_result(ctx, _setup_check_issues())


def _check_gate(gate_expr: str, data: dict) -> bool:
    """Evaluate a gate expression like 'score>=70' against data.

    Returns True if the gate passes, False if it fails.
    Supports: key>=N, key<=N, key>N, key<N, key=N
    """
    import re

    m = re.match(r"^(\w+)\s*(>=|<=|>|<|=)\s*(\d+(?:\.\d+)?)$", gate_expr.strip())
    if not m:
        return True  # can't parse, pass by default
    key, op, val_str = m.groups()
    val = float(val_str)

    actual = data.get(key)
    if actual is None:
        return True  # key not found, pass

    actual = float(actual)
    if op == ">=":
        return actual >= val
    if op == "<=":
        return actual <= val
    if op == ">":
        return actual > val
    if op == "<":
        return actual < val
    if op == "=":
        return actual == val
    return True


def _run_help_all(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager callback for --help-all: print every command + short help.

    The default ``roam --help`` shows priority categories + a flat
    "More Commands" name list (66 names, no descriptions). Agents
    mapping the territory often want every command's one-liner;
    --help-all renders that without categorisation, sub-second
    because the same AST short-help extraction --help uses.
    """
    if not value or ctx.resilient_parsing:
        return
    _ensure_plugin_commands_loaded()
    click.echo("Usage: roam [OPTIONS] COMMAND [ARGS]...\n")
    click.echo(f"All {len(_COMMANDS)} invokable command names:\n")
    for cmd_name in sorted(_COMMANDS):
        help_text = _short_help_via_ast(cmd_name) or ""
        record = _deprecation_record(cmd_name)
        if record and record.get("replacement"):
            suffix = f"  (deprecated, use {record['replacement']})"
            click.echo(f"  {cmd_name:32s} {help_text}{suffix}")
        else:
            click.echo(f"  {cmd_name:32s} {help_text}")
    click.echo()
    # Global options block — mirrors the panel in `format_help` so `--help-all`
    # is self-contained (agents that pipe `--help-all` to `grep` find the
    # global flags without needing a second call). W19.2: `--detail` was
    # accepted but undocumented; documenting all global options here too.
    click.echo("Global options (work with any command):\n")
    for flag, blurb in (
        ("--json", "output JSON envelope instead of text"),
        ("--compact", "compact output (TSV tables, minimal envelope)"),
        ("--agent", "agent mode (JSON + compact + 500-token budget)"),
        ("--detail", "show full detailed output instead of compact summary"),
        ("--sarif", f"SARIF 2.1.0 output (supported by: {', '.join(_SARIF_CONSUMERS)})"),
        ("--budget N", "max output tokens (0 = unlimited)"),
        ("--include-excluded", "include files normally excluded by .roamignore"),
        ("--override-mode", "bypass mode-based command blocking (logs to audit trail)"),
    ):
        click.echo(f"  {flag:30s} {blurb}")
    click.echo()
    click.echo("Run `roam <command> --help` for details on any command.")
    ctx.exit(0)


def _apply_agent_mode(agent: bool, json_mode: bool, compact: bool, budget: int) -> tuple[bool, bool, int]:
    if not agent:
        return json_mode, compact, budget

    json_mode = True
    compact = True
    if budget <= 0:
        budget = 500
    return json_mode, compact, budget


def _populate_cli_context(
    ctx: click.Context,
    *,
    json_mode: bool,
    compact: bool,
    agent: bool,
    sarif_mode: bool,
    budget: int,
    include_excluded: bool,
    detail: bool,
    override_mode: bool,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
    ctx.obj["compact"] = compact
    ctx.obj["agent"] = agent
    ctx.obj["sarif"] = sarif_mode
    ctx.obj["budget"] = budget
    ctx.obj["include_excluded"] = include_excluded
    ctx.obj["detail"] = detail
    ctx.obj["override_mode"] = bool(override_mode)


def _structured_output_requested(json_mode: bool, sarif_mode: bool, agent: bool) -> bool:
    return json_mode or sarif_mode or agent


def _stderr_showwarning(message, category, filename, lineno, file=None, line=None) -> None:
    import json as _json

    try:
        line_json = _json.dumps(
            {
                "warning": str(message),
                "category": getattr(category, "__name__", str(category)),
                "filename": str(filename),
                "lineno": int(lineno),
            }
        )
        sys.__stderr__.write(line_json + "\n")
    except Exception:  # noqa: BLE001 — warning serialization must never crash the command
        try:
            sys.__stderr__.write(
                f'{{"warning": {str(message)!r}, "category": {getattr(category, "__name__", str(category))!r}}}\n'
            )
        except Exception:  # noqa: BLE001 — a warning handler must never crash the command
            pass


def _install_structured_warning_handler() -> None:
    import warnings as _warnings

    # W1078: under structured-output modes, keep warnings off stdout and
    # preserve pytest/user warning hooks by only replacing the stdlib default.
    default_showwarning = getattr(_warnings, "_showwarning_orig", None) or getattr(_warnings, "_showwarning_impl", None)
    if default_showwarning is not None and _warnings.showwarning is default_showwarning:
        _warnings.showwarning = _stderr_showwarning


def _ci_mode_enabled(ci_mode: bool) -> bool:
    if ci_mode:
        return True
    env_ci = (os.environ.get("ROAM_CI") or "").strip().lower()
    return env_ci in {"1", "true", "yes", "on"}


def _warn_mode_gate_skipped() -> None:
    try:
        click.echo("WARNING: mode-enforcement gate skipped (internal error)", err=True)
    except Exception:  # noqa: BLE001 — the gate must never block a command via its own bug
        pass


def _run_mode_gate_safely(ctx: click.Context) -> None:
    try:
        _enforce_mode_gate(ctx)
    except click.exceptions.Exit:
        raise
    except Exception:  # noqa: BLE001 — the mode gate must never block via its own bug
        _warn_mode_gate_skipped()


def _install_local_telemetry(ctx: click.Context) -> None:
    import time as _time

    from roam.telemetry import record as _telemetry_record

    start = _time.perf_counter()

    def _on_close():
        try:
            cmd_name = ctx.invoked_subcommand or "<root>"
            duration_ms = int((_time.perf_counter() - start) * 1000)
            _telemetry_record(cmd_name, duration_ms, exit_code=0)
        except Exception:  # noqa: BLE001 — telemetry must never break command teardown
            pass

    ctx.call_on_close(_on_close)


@click.group(cls=LazyGroup)
@click.version_option(package_name="roam-code")
@click.option(
    "--check",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_run_check,
    help="Quick setup verification: checks Python, tree-sitter, git, SQLite",
)
@click.option(
    "--help-all",
    "help_all",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_run_help_all,
    help="Print every command (no categories, no truncation) and exit.",
)
@click.option("--json", "json_mode", is_flag=True, help="Output in JSON format")
@click.option("--compact", is_flag=True, help="Compact output: TSV tables, minimal JSON envelope")
@click.option("--agent", is_flag=True, help="Agent mode: compact JSON with 500-token default budget")
@click.option(
    "--sarif",
    "sarif_mode",
    is_flag=True,
    help=f"Output in SARIF 2.1.0 format. Supported by: {', '.join(_SARIF_CONSUMERS)}.",
)
@click.option("--budget", type=int, default=0, help="Max output tokens (0=unlimited)")
@click.option(
    "--include-excluded",
    is_flag=True,
    help="Include files normally excluded by .roamignore / config / built-in patterns",
)
@click.option("--detail", is_flag=True, help="Show full detailed output instead of compact summary")
@click.option(
    "--override-mode",
    "override_mode",
    is_flag=True,
    default=False,
    help=(
        "Bypass mode-based command blocking for this invocation. "
        "Emits a stderr warning and logs an `override` event to the "
        "active run. Use sparingly — every override leaves a trail."
    ),
)
@click.option(
    "--ci",
    "ci_mode",
    is_flag=True,
    default=False,
    help=(
        "CI mode: stricter defaults across subcommands "
        "(over-fetch --leaks-only, pr-bundle --strict AND --strict-resolved, "
        "machine-friendly output). Per-command flags ALWAYS override these "
        "implications (LAW 11: explicit --no-strict / --no-strict-resolved wins). "
        "Also enabled by ROAM_CI=1 in the environment."
    ),
)
@click.pass_context
def cli(ctx, json_mode, compact, agent, sarif_mode, budget, include_excluded, detail, override_mode, ci_mode):
    """Roam: Codebase comprehension tool."""
    if agent and sarif_mode:
        raise click.UsageError("--agent cannot be combined with --sarif")

    json_mode, compact, budget = _apply_agent_mode(agent, json_mode, compact, budget)
    _populate_cli_context(
        ctx,
        json_mode=json_mode,
        compact=compact,
        agent=agent,
        sarif_mode=sarif_mode,
        budget=budget,
        include_excluded=include_excluded,
        detail=detail,
        override_mode=override_mode,
    )
    if _structured_output_requested(json_mode, sarif_mode, agent):
        _install_structured_warning_handler()
    ctx.obj["ci_mode"] = _ci_mode_enabled(ci_mode)
    _run_mode_gate_safely(ctx)
    _install_local_telemetry(ctx)
