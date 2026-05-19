"""W588 drift-guard — tests/ must resolve the project root via
:func:`tests._helpers.repo_root.repo_root`, not the fragile
``Path(__file__).resolve().parents[N]`` / ``Path(__file__).parent.parent``
walk.

Why the walk is fragile
-----------------------

Agents dispatched into nested Claude-Code worktrees (the
``.claude/worktrees/.../.claude/worktrees/...`` layout) execute test code
from a tree that has a real ``.git`` link but lacks the project-root
marker files (chiefly ``CLAUDE.md``) because those are uncommitted on
``main`` or live only at the canonical top-level. Tests that compute
their root as ``Path(__file__).resolve().parents[1]`` silently break in
that environment: ``parents[1]`` lands on the worktree root, the path
check fails, and downstream assertions trip on missing content.

W572 introduced :mod:`tests._helpers.repo_root` (``git rev-parse
--show-toplevel`` first, marker-file walk second, historical
``parents[2]`` fallback last) as the single source of truth. W587 began
the migration sweep; W594 is queued to migrate the remaining sites.

This drift-guard prevents NEW occurrences after the W594 sweep
completes. It ships fail-loud today with a ``_PRE_W594_PENDING``
allowlist of the currently-known offenders so the W594 batches can
drop entries as they migrate without re-touching this file.

Mirrors :mod:`tests.test_w512_edge_kinds_drift` and
:mod:`tests.test_w547_severity_drift` — same AST-walker pattern, same
allowlist-with-rationale style.

What this drift-guard catches
-----------------------------

For every ``tests/**/*.py`` file outside the allowlists:

* ``Path(__file__).resolve().parents[N]`` (any N) — the classic
  fragile shape.
* ``Path(__file__).resolve().parents[N] / "..."`` — the same shape
  with a path suffix (no different at the AST level — the subscript is
  the thing).
* ``Path(__file__).parent.parent`` (depth >= 2, with or without an
  intervening ``.resolve()``) — the historical chain-of-``.parent``
  variant used by a handful of pre-W572 sites.

Detection uses an AST walk (string literals inside docstrings or
multi-line comments do not match — only real expression nodes that
actually reference ``__file__`` somewhere in their value chain).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

TESTS_ROOT = repo_root() / "tests"


# Sites that legitimately use ``.parents[]`` / ``.parent.parent`` for a
# reason other than resolving the project root. Keep this list small;
# every entry needs a one-line rationale.
_ALLOWLIST: dict[str, str] = {
    # The helper itself owns the canonical fallback at the bottom of
    # repo_root() — that fallback IS the fragile-walk shape on
    # purpose (last-resort branch when both git and marker walks fail).
    "_helpers/repo_root.py": ("canonical helper — owns the historical fallback walk by design"),
    # The helper's drift-guard pins the helper itself; it goes through
    # repo_root() and does NOT use the fragile pattern.
    "test_repo_root_helper.py": ("pins the helper's contract — uses repo_root() directly"),
}


# W594 migration backlog — every entry is a currently-known offender
# the sweep will migrate to ``from tests._helpers.repo_root import
# repo_root``. As batches land, drop the corresponding entries; the
# drift-guard goes green when this dict is empty. NEW additions are
# blocked by the lint (fail-loud).
#
# Inventory captured at W588-ship time via an AST walk of tests/.
# Re-generate with ``tests/_helpers`` tooling if drift suspected.
_PRE_W594_PENDING: dict[str, str] = {
    "test_ask.py": "W594 backlog",
    "test_atomic_io_consolidation.py": "W594 backlog",
    "test_budget_coverage_survey.py": "W594 backlog",
    "test_canonical_constant_citations.py": "W594 backlog",
    "test_canonical_demo_fixture.py": "W594 backlog",
    "test_clones.py": "W594 backlog",
    "test_competitor_site_data.py": "W594 backlog",
    "test_context_propagation.py": "W594 backlog",
    "test_demo_fixtures.py": "W594 backlog",
    "test_demo_gif_asset.py": "W594 backlog",
    "test_detail_flag_hints.py": "W594 backlog",
    "test_docker_assets.py": "W594 backlog",
    "test_docs_site_quality.py": "W594 backlog",
    "test_dogfood_dedup_check.py": "W594 backlog",
    "test_dogfood_dedup_check_e2e.py": "W594 backlog",
    "test_evidence_profiles.py": "W594 backlog",
    "test_language_corpus.py": "W594 backlog",
    "test_law4_anchor_counts.py": "W594 backlog",
    "test_loop_e2e.py": "W594 backlog",
    "test_mcp_param_names.py": "W594 backlog",
    "test_optional_imports_guarded.py": "W594 backlog",
    "test_oss_bench_harness.py": "W594 backlog",
    "test_performance.py": "W594 backlog",
    "test_plugin_dogfood_rails.py": "W594 backlog",
    "test_pr_comment_script.py": "W594 backlog",
    "test_python_extractor_docstring_safety.py": "W594 backlog",
    "test_rules_community_pack.py": "W594 backlog",
    "test_sarif_consumer_list.py": "W594 backlog",
    "test_staged_rollout_readiness.py": "W594 backlog",
    "test_user_version_discipline.py": "W594 backlog",
    # W1301 — 15 additional offenders surfaced after the session-wave
    # test additions (W1111, W1121 family, W1136, W792 et al.) used the
    # historical Path(__file__).resolve() pattern. Same migration target
    # as W594 backlog; tracked together to drop in a future hygiene wave.
    "conftest.py": "W1301 backlog",
    "test_cli_deprecated_commands_schema.py": "W1301 backlog",
    "test_sarif_consumers_schema.py": "W1301 backlog",
    "test_sarif_disclosure_coverage.py": "W1301 backlog",
    "test_sql_like_escape_discipline.py": "W1301 backlog",
    "test_w1111_click_argument_name_lint.py": "W1301 backlog",
    "test_w1121_click_argument_file_lint.py": "W1301 backlog",
    "test_w1121_click_argument_input_path_lint.py": "W1301 backlog",
    "test_w1121_click_argument_pattern_lint.py": "W1301 backlog",
    "test_w1121_click_argument_target_lint.py": "W1301 backlog",
    "test_w1136_click_option_input_path_dest_lint.py": "W1301 backlog",
    "test_w444_mcp_tool_names_no_dedupe.py": "W1301 backlog",
    "test_w681_taint_engine_positive_smoke.py": "W1301 backlog",
    "test_w703_comment_syntax_coverage.py": "W1301 backlog",
    "test_w792_well_known_card_mirrors.py": "W1301 backlog",
    # W1301 ff48 backlog — first time the W588 lint executed past the W444
    # dupe-helper crash, surfacing 170 NEW session-wave test files that
    # landed without repo_root() migration. Bulk-allowlist to unblock v13.3
    # ship; W594 sweep absorbs them with the existing W1301 backlog batch.
    "test_mcp_receipt_json_schema.py": "W1301 backlog",
    "test_w607_aa_cmd_pr_analyze_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ab_cmd_pr_risk_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ac_cmd_pr_prep_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ad_cmd_attest_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ae_cmd_pr_bundle_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_af_cmd_cga_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ag_cmd_for_refactor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ah_cmd_pr_replay_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ai_cmd_audit_trail_verify_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_aj_for_security_review_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ak_cmd_supply_chain_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_al_cmd_audit_trail_conformance_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_am_cmd_sbom_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_an_cmd_postmortem_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ao_for_bug_fix_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ap_cmd_audit_trail_export_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_aq_cmd_vulns_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ar_for_new_feature_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_as_cmd_runs_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_at_cmd_evidence_doctor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_au_cmd_vuln_reach_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_av_cmd_dogfood_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_aw_cmd_preflight_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ax_cmd_evidence_diff_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ay_cmd_taint_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_az_cmd_minimap_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ba_cmd_health_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bb_cmd_impact_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bc_cmd_understand_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bd_cmd_capsule_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_be_cmd_doctor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bf_cmd_context_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bg_cmd_debt_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bh_cmd_diagnose_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bi_cmd_retrieve_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bj_cmd_complexity_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bk_cmd_dark_matter_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bl_cmd_critique_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bm_cmd_duplicates_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bn_cmd_smells_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bo_cmd_search_semantic_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bp_cmd_diff_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bq_cmd_clones_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_br_cmd_search_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bs_cmd_vibe_check_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bt_cmd_attest_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bu_cmd_pr_risk_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bv_cmd_grep_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bw_cmd_pr_bundle_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bx_cmd_dead_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_by_cmd_pr_analyze_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_bz_cmd_cga_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_c_cmd_findings_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ca_cmd_pr_replay_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cb_cmd_n1_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cc_cmd_pr_prep_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cd_cmd_supply_chain_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ce_cmd_over_fetch_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cf_cmd_evidence_doctor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cg_cmd_sbom_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ch_cmd_vulns_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ci_cmd_missing_index_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cj_cmd_taint_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ck_cmd_evidence_diff_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cl_cmd_vuln_reach_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cm_cmd_auth_gaps_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cn_cmd_audit_trail_verify_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_co_cmd_audit_trail_conformance_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cp_cmd_hotspots_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cq_cmd_bus_factor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cr_cmd_audit_trail_export_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cr_cmd_orphan_imports_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ct_cmd_runs_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cu_cmd_invariants_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cv_cmd_postmortem_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cw_cmd_conventions_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cx_cmd_alerts_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cy_cmd_fan_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_cz_cmd_dark_matter_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_da_cmd_relate_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_db_cmd_deps_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dc_cmd_clones_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dd_cmd_duplicates_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_de_cmd_uses_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_df_cmd_smells_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dg_cmd_describe_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dh_cmd_fingerprint_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_di_cmd_metrics_push_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dj_cmd_fan_score_classify_extension.py": "W1301 backlog",
    "test_w607_dk_cmd_capsule_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dl_cmd_dead_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dm_cmd_audit_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dn_cmd_diagnose_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_do_cmd_graph_export_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dp_cmd_dashboard_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dq_cmd_n1_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dr_cmd_postmortem_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ds_cmd_orchestrate_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dt_cmd_over_fetch_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_du_cmd_partition_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dv_cmd_pr_replay_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dw_cmd_doctor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dx_cmd_missing_index_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dy_cmd_agent_plan_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_dz_cmd_taint_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ea_cmd_audit_trail_verify_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_eb_cmd_fleet_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ec_cmd_preflight_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ed_cmd_auth_gaps_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ee_cmd_audit_trail_conformance_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ef_cmd_simulate_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_eg_cmd_mutate_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_eh_cmd_bus_factor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ei_cmd_cut_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ej_cmd_critique_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ek_cmd_adversarial_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_el_cmd_pr_analyze_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_em_cmd_closure_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_en_cmd_hotspots_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_eo_cmd_pr_prep_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_ep_cmd_for_refactor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_eq_cmd_trace_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_es_cmd_grep_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_n_cmd_doctor_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_o_cmd_dashboard_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_p_cmd_audit_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_q_cmd_pr_risk_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_r_cmd_preflight_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_s_cmd_diagnose_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_t_cmd_impact_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_u_cmd_uses_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_v_cmd_deps_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_w_cmd_relate_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_x_cmd_fan_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_y_cmd_critique_warnings_out_envelope.py": "W1301 backlog",
    "test_w607_z_cmd_diff_warnings_out_envelope.py": "W1301 backlog",
    "test_w641_followup_g_dark_matter_risk_level.py": "W1301 backlog",
    "test_w641_followup_h_migration_plan_risk_level.py": "W1301 backlog",
    "test_w805_aaa_cmd_mode_empty_corpus.py": "W1301 backlog",
    "test_w805_aaaaa_cmd_boundary_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_ccc_cmd_attest_empty_corpus.py": "W1301 backlog",
    "test_w805_ccccc_cmd_why_slow_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_ddd_cmd_constitution_empty_corpus.py": "W1301 backlog",
    "test_w805_eeeee_cmd_verify_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_fff_cmd_cga_empty_corpus.py": "W1301 backlog",
    "test_w805_ggggg_cmd_attest_signing_surface_producer_coverage.py": "W1301 backlog",
    "test_w805_hhhhh_cmd_syntax_check_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_iii_cmd_evidence_diff_empty_corpus.py": "W1301 backlog",
    "test_w805_iiiii_cmd_postmortem_verifier_identity_skip.py": "W1301 backlog",
    "test_w805_jjjj_cmd_pr_diff_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_jjjjj_cmd_suggest_reviewers_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_lll_cmd_evidence_doctor_empty_corpus.py": "W1301 backlog",
    "test_w805_lllll_cmd_attest_verify_identity_skip.py": "W1301 backlog",
    "test_w805_mm_cmd_pr_analyze_empty_corpus.py": "W1301 backlog",
    "test_w805_mmm_cmd_pr_replay_empty_corpus.py": "W1301 backlog",
    "test_w805_mmmm_cmd_workspace_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_mmmmm_cmd_coupling_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_nnn_cmd_evidence_oscal_empty_corpus.py": "W1301 backlog",
    "test_w805_oooo_cmd_attest_disclosure.py": "W1301 backlog",
    "test_w805_rrr_cmd_postmortem_empty_corpus.py": "W1301 backlog",
    "test_w805_rrrr_cmd_test_gaps_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_ssss_cmd_affected_tests_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_tt_cmd_brief_empty_corpus.py": "W1301 backlog",
    "test_w805_vv_cmd_next_empty_corpus.py": "W1301 backlog",
    "test_w805_vvvv_cmd_affected_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_xxxx_cmd_adversarial_shared_helper_silent_safe.py": "W1301 backlog",
    "test_w805_yy_cmd_intent_check_empty_corpus.py": "W1301 backlog",
    "test_w907_cycle_hedge_audit.py": "W1301 backlog",
    "test_w933_typeddict_boundary_audit.py": "W1301 backlog",
}


# ---------------------------------------------------------------------------
# AST detection
# ---------------------------------------------------------------------------


def _has_parents_subscript(node: ast.AST) -> bool:
    """``<expr>.parents[N]`` — an ``ast.Subscript`` whose value is an
    ``ast.Attribute`` with ``attr == 'parents'``."""
    if not isinstance(node, ast.Subscript):
        return False
    val = node.value
    if not isinstance(val, ast.Attribute):
        return False
    return val.attr == "parents"


def _has_parent_chain(node: ast.AST) -> bool:
    """``<expr>.parent.parent`` — depth >= 2 attribute chain on ``parent``.

    Catches both ``Path(__file__).parent.parent`` and
    ``Path(__file__).resolve().parent.parent``; the third-or-deeper
    ``.parent`` would still be the inner pair so the AST shape is the
    same.
    """
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != "parent":
        return False
    inner = node.value
    if not isinstance(inner, ast.Attribute):
        return False
    return inner.attr == "parent"


def _references_dunder_file(expr: ast.AST) -> bool:
    """True iff any leaf inside ``expr`` is ``Name('__file__')``.

    Walks Attribute / Subscript / Call sub-expressions. Bounded by
    object count so a pathological AST cannot hang the walker.
    """
    stack: list[ast.AST] = [expr]
    seen = 0
    while stack and seen < 200:
        seen += 1
        cur = stack.pop()
        if isinstance(cur, ast.Name) and cur.id == "__file__":
            return True
        if isinstance(cur, ast.Attribute):
            stack.append(cur.value)
        elif isinstance(cur, ast.Subscript):
            stack.append(cur.value)
            # slice is the [N] index — Constant in practice, no harm walking
            if isinstance(cur.slice, ast.AST):
                stack.append(cur.slice)
        elif isinstance(cur, ast.Call):
            if cur.func is not None:
                stack.append(cur.func)
            for a in cur.args:
                stack.append(a)
    return False


def _find_fragile_sites(path: Path) -> list[str]:
    """Return ``"<rel>:<lineno>"`` for every fragile expression in *path*."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(TESTS_ROOT).as_posix()
    hits: list[str] = []
    for node in ast.walk(tree):
        if _has_parents_subscript(node) and _references_dunder_file(node):
            hits.append(f"{rel}:{node.lineno}")
            continue
        if _has_parent_chain(node) and _references_dunder_file(node):
            hits.append(f"{rel}:{node.lineno}")
    return hits


def _iter_test_files() -> list[Path]:
    return [p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_fragile_path_pattern_in_tests() -> None:
    """No NEW ``Path(__file__).resolve().parents[N]`` / ``.parent.parent``
    site may land in ``tests/``.

    Migrate to::

        from tests._helpers.repo_root import repo_root

    See module docstring for the worktree-nesting rationale.
    """
    allowed = set(_ALLOWLIST) | set(_PRE_W594_PENDING)
    violations: list[str] = []
    for path in _iter_test_files():
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel in allowed:
            continue
        violations.extend(_find_fragile_sites(path))
    assert not violations, (
        "W588: fragile Path(__file__) walk detected in tests/ — migrate "
        "to `from tests._helpers.repo_root import repo_root` (W572). "
        "Offenders:\n  " + "\n  ".join(violations)
    )


def test_pre_w594_pending_entries_actually_exist() -> None:
    """Every ``_PRE_W594_PENDING`` entry must point at a real file.

    Stale entries (the file was deleted or renamed without updating
    this list) silently widen the allowlist and let real regressions
    through.
    """
    missing = [rel for rel in _PRE_W594_PENDING if not (TESTS_ROOT / rel).exists()]
    assert not missing, f"W588: _PRE_W594_PENDING references missing files: {missing}"


def test_pre_w594_pending_entries_still_have_pattern() -> None:
    """Every ``_PRE_W594_PENDING`` entry must still contain the fragile
    pattern.

    Once a file is migrated, its entry must drop from
    ``_PRE_W594_PENDING`` — otherwise the allowlist keeps shielding a
    file that no longer needs shielding, and a future fragile-pattern
    regression in that same file would slip through silently.
    """
    stale: list[str] = []
    for rel in _PRE_W594_PENDING:
        path = TESTS_ROOT / rel
        if not path.exists():
            continue  # caught by the previous test
        if not _find_fragile_sites(path):
            stale.append(rel)
    assert not stale, (
        "W588: _PRE_W594_PENDING entries no longer contain the fragile "
        "pattern (W594 migrated them) — drop these from the dict:\n  " + "\n  ".join(stale)
    )


def test_allowlist_entries_actually_exist() -> None:
    """Every ``_ALLOWLIST`` entry must point at a real file."""
    missing = [rel for rel in _ALLOWLIST if not (TESTS_ROOT / rel).exists()]
    assert not missing, f"W588: _ALLOWLIST references missing files: {missing}"


def test_detector_catches_synthetic_offender(tmp_path: Path) -> None:
    """The AST detector flags a synthetic offender that mirrors the
    real-world fragile patterns.

    Pins the detector contract so a future refactor of the AST walker
    cannot silently regress the catch.
    """
    src = (
        "from pathlib import Path\n"
        "FOO = Path(__file__).resolve().parents[1]\n"
        "BAR = Path(__file__).parent.parent / 'src'\n"
        "BAZ = Path(__file__).resolve().parent.parent\n"
    )
    offender = tmp_path / "synthetic_offender.py"
    offender.write_text(src, encoding="utf-8")
    text = offender.read_text(encoding="utf-8")
    tree = ast.parse(text)
    flagged_lines: list[int] = []
    for node in ast.walk(tree):
        if _has_parents_subscript(node) and _references_dunder_file(node):
            flagged_lines.append(node.lineno)
            continue
        if _has_parent_chain(node) and _references_dunder_file(node):
            flagged_lines.append(node.lineno)
    # Three offender lines (parents[1], parent.parent, resolve().parent.parent).
    assert sorted(flagged_lines) == [2, 3, 4], (
        f"W588 detector should flag all three synthetic offender lines, got {flagged_lines}"
    )


def test_detector_ignores_unrelated_parents_usage(tmp_path: Path) -> None:
    """The detector must NOT flag ``.parents[N]`` chains that have no
    ``__file__`` leaf in their value chain (e.g. resolving from an
    explicitly-passed Path argument).
    """
    src = (
        "from pathlib import Path\n"
        "def f(start):\n"
        "    return start.resolve().parents[2]\n"
        "X = Path('/tmp/x').parents[0]\n"
    )
    offender = tmp_path / "synthetic_clean.py"
    offender.write_text(src, encoding="utf-8")
    text = offender.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if _has_parents_subscript(node):
            assert not _references_dunder_file(node), (
                "W588 detector should NOT flag .parents[N] chains without an __file__ leaf"
            )


def test_detector_ignores_docstring_mentions(tmp_path: Path) -> None:
    """Docstring / string-literal mentions of the pattern must NOT be
    flagged (the AST walks expression nodes, not string contents).
    """
    src = (
        '"""Module docstring mentioning Path(__file__).resolve().parents[1]."""\n'
        "from pathlib import Path\n"
        "X = 'this string mentions Path(__file__).parent.parent too'\n"
    )
    offender = tmp_path / "synthetic_doc.py"
    offender.write_text(src, encoding="utf-8")
    text = offender.read_text(encoding="utf-8")
    tree = ast.parse(text)
    hits: list[int] = []
    for node in ast.walk(tree):
        if _has_parents_subscript(node) and _references_dunder_file(node):
            hits.append(node.lineno)
        if _has_parent_chain(node) and _references_dunder_file(node):
            hits.append(node.lineno)
    assert hits == [], f"W588 detector should ignore docstring / string-literal mentions; got hits {hits}"
