"""W1169 — CI lint: every cmd_*.py either ships SARIF OR carries the
canonical SKIP-disclosure docstring anchor.

Per W1166-RESEARCH §8 (SARIF-Disclosure Pattern Maturity memo), the
SARIF audience-disclosure framework has propagated to 14 command sites
(8 SHIP + 6 SKIP-DISCLOSURE) with a stable docstring template. This
lint closes the discipline end-to-end: any new cmd_*.py that doesn't
fit the closed enumeration in ``cli._SARIF_CONSUMERS`` MUST anchor its
SARIF-skip rationale via the canonical docstring substring.

Two enforcement axes:

(a) **SHIP path** — command appears in
    ``src/roam/cli.py::_SARIF_CONSUMERS`` (the 16-entry closed enum
    surfaced in --help text + enforced by
    ``tests/test_sarif_consumer_list.py``). Carries ``--sarif``.

(b) **SKIP-DISCLOSURE path** — module docstring carries the canonical
    anchor substring (see ``_CANONICAL_ANCHOR``). The post-W1166
    wording-convergence audit pinned every shipped SKIP docstring on
    the same prefix: "SARIF is deliberately NOT" followed by
    "emitted because *output-shape* — not per-location violations."

The W1166-RESEARCH §8 hypothesis is that the first run of this lint
surfaces 4-8 unaudited commands that have neither SARIF support nor a
disclosure docstring. Those captures land as follow-up W117x-impl
docstring-addition tickets, and the ``_KNOWN_MISSING`` frozenset below
pins the current gap so the lint passes on the current tree without
hiding regressions.

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Stays
runnable in environments where roam's optional dependencies aren't
installed (mirrors the discipline in
``tests/test_w1111_click_argument_name_lint.py``).

See:
- (internal memo) §8 (lint sketch)
- src/roam/cli.py:54 — ``_SARIF_CONSUMERS`` canonical 16-entry tuple
- src/roam/commands/cmd_doctor.py — canonical SKIP-disclosure docstring
"""

from __future__ import annotations

import ast
from pathlib import Path

from roam.cli import _COMMANDS, _SARIF_CONSUMERS

# ---------------------------------------------------------------------------
# The canonical anchor substring. Post-W1166 wording-convergence audit
# pinned every shipped SKIP-DISCLOSURE docstring on this exact prefix.
# Extending the anchor (e.g. supporting an alias) is a deliberate edit:
# update this constant AND the per-site docstrings that drift. The lint
# stays strict on a single anchor to prevent template-erosion.
# ---------------------------------------------------------------------------
_CANONICAL_ANCHOR = "SARIF is deliberately NOT"


_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# Known-missing pin — captures the W1166-RESEARCH §8 initial-gap commands
# that have neither SARIF support nor a disclosure docstring. Each entry
# is a follow-up W117x-impl docstring-addition ticket; drop it from this
# set when the docstring lands. The inverse-drift guard below fails the
# build if an entry here no longer needs the carve-out (either the
# command grew a disclosure docstring OR was added to _SARIF_CONSUMERS).
# ---------------------------------------------------------------------------
# W1169 first-sweep capture (2026-05-16). 174 cmd_*.py files carry
# neither SARIF support nor the canonical SKIP-disclosure anchor. Each
# is a follow-up W117x docstring-addition opportunity. Per W1166-RESEARCH
# §8 the initial-gap hypothesis of 4-8 commands turned out to be a
# significant under-estimate — the SARIF-disclosure framework has landed
# on 53 sites total (18 SHIP via ``_SARIF_CONSUMERS`` + 35 SKIP-DISCLOSURE)
# so the long tail of cmd_*.py files remains unaudited. Pinning the full
# current gap here turns the lint into a "no regression" guard: new
# cmd_*.py files inherit the discipline, and the pin shrinks as W117x
# docstrings land. W1180 (Wave 1 bootstrap bucket) removed 10 entries:
# init, ci-setup, hooks, pre-commit, clean, reset, config, mcp-setup,
# mcp-status, mcp. W1181-impl (Wave 2 Bucket D local-state bucket)
# anchored the canonical disclosure docstring on 10 additional
# substrate-state commands and removed them from this pin: mode,
# annotate, constitution, lease, memory, permit, replay, runs,
# suppress, watch. W1182-impl (Wave 3 Bucket C codegen bucket) anchored
# the canonical disclosure docstring on 12 additional codegen commands
# and removed them from this pin: attest, capsule, agent-export,
# agents-md, graph-export, cga, sbom, skill-generate, pr-comment-render,
# audit-trail-export, evidence-oscal, fingerprint. W1187-impl (Wave 4
# Bucket B exploration/aggregate bucket) anchored the canonical
# disclosure docstring on 12 additional exploration/aggregate commands
# and removed them from this pin: search, grep, dashboard, minimap,
# describe, understand, tour, visualize, clusters, layers, coupling,
# spectral. W1191-impl (Wave 7 Bucket B invocation-scoped aggregate /
# metadata-registry bucket) anchored the canonical disclosure docstring
# on 10 additional commands and removed them from this pin: api-changes,
# breaking, complete, disambiguate, explain-command, exit-codes,
# findings, graph-diff, intent, plugins. W1194-impl (Wave 8 mixed
# Buckets B/C/E) anchored the canonical disclosure docstring on 10
# additional commands and removed them from this pin: adrs (Bucket C),
# adversarial (Bucket B), agent-context (Bucket B), agent-plan
# (Bucket B), api (Bucket B), api-drift (Bucket B), architecture-drift
# (Bucket B), article-12-check (Bucket C), ask (Bucket E), audit
# (Bucket B Q4-composer). W1197-impl (Wave 9 clear-SKIP slice) anchored
# the canonical disclosure docstring on 4 additional commands and removed
# them from this pin: bisect (Bucket B time-series rankings), budget
# (Bucket B invocation-scoped gate), codeowners (Bucket F advisory
# attribution), congestion (Bucket B per-PR aggregate). W1205-impl
# (Wave 10 Bucket B SKIP slice) anchored the canonical disclosure
# docstring on 10 additional commands and removed them from this pin:
# batch-search (symbol-name match enumerations), file (file skeleton
# summaries), symbol (definition + callers/callees), relate (relation
# graph rankings), refs-text (verdict envelope), history-grep (git
# commit rows), recipes (ask-recipe enumerations), sketch (skeleton +
# API surface), pr-analyze (Q4 recipe-composer), pr-replay (buyer-
# facing report). W1206-impl (Wave 11 Bucket B clear-SKIP slice)
# anchored the canonical disclosure docstring on 5 additional commands
# and removed them from this pin: affected (blast-radius graph
# rankings), closure (change-closure envelopes), compare (structural
# delta summaries), conventions (convention-classification
# percentages), causal-graph (causal dependency rankings). The 6th
# Wave-11 audit candidate (duplicates) BAILed as a SHIP candidate —
# cmd_duplicates.py persists findings via ``emit_finding`` so the
# SARIF audience-fit decision flips to "ship"; captured as W1209
# follow-up. W1208-impl promoted cmd_n1.py to SHIP via the global
# ``--sarif`` flag (n1_to_sarif emits per-finding implicit-N+1
# projections; closed-enum rule catalog: high/medium/low-confidence)
# and removed it from this pin. W1221-impl (Wave 13 SKIP-eligible
# aggregate / metadata / non-detector slice) anchored the canonical
# disclosure docstring on 10 additional commands and removed them
# from this pin: changelog (invocation-scoped git-repo-metadata
# enumeration), db-check (validator-not-detector: index-integrity
# verification), intent-check (invocation-scoped user-intent
# classification aggregate), metrics-push (environment-scoped
# external-service report transmitter), recommend (invocation-scoped
# related-symbol suggestion enumeration), report (Q4 recipe-composer),
# retrieve (invocation-scoped knowledge-base ranked-spans retrieval),
# schema (validator-not-detector: envelope schema enumeration +
# validation), search-semantic (invocation-scoped semantic-vector
# retrieval rankings), simulate (invocation-scoped counterfactual
# scenario-planning envelopes). All 10 BAIL-checked clean (no
# ``emit_finding`` / ``findings_store.persist`` call site).
# W1224-impl (Wave 14a SKIP-eligible aggregate / state / validator
# slice) anchored the canonical disclosure docstring on 15 additional
# commands and removed them from this pin: cut (invocation-scoped
# graph-partition boundary enumeration), dev-profile (invocation-scoped
# per-author commit-behavior aggregate), doc-staleness (invocation-scoped
# docstring-vs-code drift ranking), docs-coverage (invocation-scoped
# coverage-percentage rollup), drift (invocation-scoped declared-vs-actual
# ownership-mismatch ranking), effects (invocation-scoped side-effect
# classification rollup), eval-retrieve (invocation-scoped retrieval
# benchmark aggregate), evidence-diff (validator-not-detector: packet
# delta vs source-coordinate findings), evidence-doctor
# (validator-not-detector: packet integrity vs source-coordinate
# findings), fitness (invocation-scoped policy-aggregate per-rule
# verdict), fn-coupling (invocation-scoped temporal co-change ranking),
# graph-stats (invocation-scoped graph-topology summary), idempotency
# (invocation-scoped per-symbol classification rollup), index
# (setup/state: index-build status), index-bundle (setup/state: bundle
# export status + on-disk artifact). All 15 BAIL-checked clean (no
# ``emit_finding`` / ``findings_store.persist`` call site).
# W1224-impl (Wave 14b SKIP-eligible aggregate / composer / state /
# validator slice) anchored the canonical disclosure docstring on 22
# additional commands and removed them from this pin: ingest-trace
# (state-mutating trace ingest), invariants (invocation-scoped
# implicit-contract enumeration), mutate (state-mutating code-
# transform), owner (invocation-scoped ownership attribution
# rankings), pr-diff (invocation-scoped graph-delta summary),
# pr-prep (Q4 recipe-composer), side-effects (invocation-scoped
# per-symbol classification rollup), split (invocation-scoped
# decomposition recommendation), stats (invocation-scoped index
# rollup), suggest-reviewers (invocation-scoped reviewer-attribution
# rankings), surface (capability-registry meta-enumeration),
# syntax-check (validator-not-detector: tree-sitter parse-failure
# status), telemetry (state-mutating ring-buffer surface), test-gaps
# (invocation-scoped coverage-gap aggregate), test-pyramid
# (invocation-scoped test-kind rollup), tx-boundaries (invocation-
# scoped per-symbol transaction classification), version (tool-
# identity metadata), vuln-map (state-mutating vuln-ingest +
# mapping), vuln-reach (invocation-scoped reachability aggregate),
# workflow (Q4 recipe-composer / inspector), xlang (invocation-scoped
# cross-language bridge enumeration), index-stats (setup/state:
# index-artifact metadata). All 22 BAIL-checked clean (no
# ``emit_finding`` / ``findings_store.persist`` call site).
# W1233-impl (Wave 16 final SKIP-eligible aggregate / validator /
# composer / simulator / catalog / infrastructure slice) anchored
# the canonical disclosure docstring on the final 17 commands and
# removed them from this pin, closing the SARIF disclosure
# coverage propagation arc to zero:
#   - REPORT variant (8): debt (hotspot-weighted technical-debt
#     rollup), map (project-skeleton summary), metrics (per-file /
#     per-symbol metric export), path-coverage (untested-path
#     absence-of-edge enumeration — parallel W1230 cmd_test_gaps
#     rationale), pytest-fixtures (fixture-relationship catalog),
#     risk (domain-weighted ranking aggregate), testmap (test-
#     coverage relationship rollup), why-slow (runtime-hotspot
#     aggregate);
#   - VALIDATOR variant (2): safe-delete (single-symbol verdict
#     SAFE/REVIEW/UNSAFE — parallel W1192 cmd_syntax_check pattern),
#     safe-zones (refactoring-boundary verdict for one input);
#   - COMPOSER variant (2): plan-refactor (wraps cmd_guard helpers
#     + blast-radius + risk-score into a single plan envelope),
#     suggest-refactoring (wraps run_all_detectors smells catalog
#     + complexity + fan-in into a ranked recommendation);
#   - SIMULATOR variant (1): simulate-departure (team-level
#     counterfactual knowledge-loss rollup — parallel W1221
#     cmd_simulate pattern);
#   - CATALOG variant (1): entry-points (protocol-classification
#     catalog with reachability coverage);
#   - INFORMATIONAL variant (1): patterns (design-pattern catalog,
#     not violations to remediate);
#   - SETUP/WORKFLOW variant (1): guard (pre-edit sub-agent
#     context bundle — parallel W1180 cmd_index_bundle pattern);
#   - INFRASTRUCTURE variant (1): ws (workspace-management
#     multi-repo grouping).
# All 17 BAIL-checked clean (no ``emit_finding`` / ``findings_store.persist``
# / ``FindingRecord(`` / ``findings.append(`` call site). After this
# sweep _KNOWN_MISSING is empty — the SARIF audience-fit decision is
# now disclosed at every cmd_*.py site (SHIP via _SARIF_CONSUMERS OR
# canonical anchor in module docstring).
_KNOWN_MISSING: frozenset[str] = frozenset(
    {
        # W1215: cmd_bus_factor.py removed from this pin — ships
        # SARIF via the bus_factor_to_sarif projection + global
        # --sarif flag.
        # W1224-impl: cmd_cut.py removed (invocation-scoped graph-partition
        # boundary enumeration; SKIP-disclosure docstring).
        # W1211: cmd_dark_matter.py removed from this pin — ships
        # SARIF via the dark_matter_to_sarif projection + global --sarif
        # flag (per-pair hidden-coupling projection, single closed-enum
        # rule dark-matter/hidden-coupling, confidence-tier-banded
        # severity).
        # W1233-impl: cmd_debt.py removed (Wave 16 REPORT; hotspot-
        # weighted technical-debt rollup).
        # W1224-impl: cmd_dev_profile.py / cmd_doc_staleness.py /
        # cmd_docs_coverage.py / cmd_drift.py removed (invocation-scoped
        # aggregates; SKIP-disclosure docstrings).
        # W1213: cmd_duplicates.py removed from this pin — ships SARIF
        # via the duplicates_to_sarif projection + global --sarif flag
        # (per-cluster semantic-duplicate projection, single closed-enum
        # rule duplicates/cluster, similarity-banded severity).
        # W1224-impl: cmd_effects.py removed (invocation-scoped side-effect
        # classification rollup; SKIP-disclosure docstring).
        # W1233-impl: cmd_entry_points.py removed (Wave 16 CATALOG;
        # protocol-classification catalog).
        # W1224-impl: cmd_eval_retrieve.py / cmd_evidence_diff.py /
        # cmd_evidence_doctor.py removed (eval-retrieve = invocation-scoped
        # benchmark aggregate; evidence-diff + evidence-doctor =
        # validator-not-detector). SKIP-disclosure docstrings.
        # W1209: cmd_fan.py removed from this pin — ships SARIF via
        # the fan_to_sarif projection + global --sarif flag.
        # W1224-impl: cmd_fitness.py removed (invocation-scoped
        # policy-aggregate per-rule verdict; SKIP-disclosure docstring).
        # W1226: cmd_flag_dead.py removed from this pin — ships SARIF
        # via the flag_dead_to_sarif projection + global --sarif flag
        # (per-flag staleness projection, three closed-enum rules under
        # the flag-* namespace: flag-staleness / flag-single-reference
        # / flag-suspect [W1232 renamed flag-constant-default to align
        # with the envelope's 4-value staleness vocabulary], staleness-
        # banded per-result level with a warning ceiling — heuristic
        # detector, never escalates to error).
        # W1224-impl: cmd_fn_coupling.py / cmd_graph_stats.py /
        # cmd_idempotency.py removed (invocation-scoped aggregates;
        # SKIP-disclosure docstrings).
        # W1233-impl: cmd_guard.py removed (Wave 16 SETUP/WORKFLOW;
        # pre-edit sub-agent context bundle).
        # W1210: cmd_hotspots.py removed from this pin — ships SARIF
        # via the hotspots_to_sarif projection + global --sarif flag
        # (runtime-mode only; --security / --danger emit raw findings
        # at file/line outside the closed-enum hotspots/* rule
        # catalogue).
        # W1224-impl: cmd_index.py / cmd_index_bundle.py /
        # cmd_index_stats.py / cmd_ingest_trace.py / cmd_invariants.py
        # removed (setup/state + state-mutating + aggregate;
        # SKIP-disclosure docstrings).
        # W1216: cmd_laws.py removed from this pin — ships SARIF via
        # the laws_to_sarif projection + global --sarif flag.
        # W1207: cmd_llm_smells.py removed from this pin — ships SARIF
        # via the llm_smells_to_sarif projection + global --sarif flag
        # (per-occurrence LLM-API anti-pattern projection, ten closed-
        # enum rules under the llm-smells/ namespace, severity-banded
        # per-result level).
        # W1233-impl: cmd_map.py / cmd_metrics.py removed (Wave 16 REPORT;
        # project-skeleton summary + per-file metric export).
        # W1224-impl: cmd_mutate.py / cmd_owner.py / cmd_pr_diff.py /
        # cmd_pr_prep.py removed (state-mutating + aggregates +
        # composer; SKIP-disclosure docstrings).
        # W1227: cmd_orphan_routes.py removed from this pin — ships SARIF
        # via the orphan_routes_to_sarif projection + global --sarif flag
        # (per-route dead-endpoint projection, single closed-enum rule
        # orphan-route, confidence-banded per-result level: high + medium
        # -> warning, low -> note; warning ceiling — heuristic detector,
        # never escalates to error). The ``used`` bucket (has a frontend
        # consumer) is filtered upstream so SARIF consumers never see
        # non-actionable rows.
        # W1233-impl: cmd_path_coverage.py removed (Wave 16 REPORT;
        # untested-path absence-of-edge enumeration — parallel W1230
        # cmd_test_gaps rationale).
        # W1233-impl: cmd_patterns.py removed (Wave 16 INFORMATIONAL;
        # design-pattern catalog — Factory / Singleton / Observer / etc.
        # — not violations to remediate).
        # W1233-impl: cmd_plan_refactor.py removed (Wave 16 COMPOSER;
        # wraps cmd_guard helpers + blast-radius + risk-score into a
        # single plan envelope).
        # W1233-impl: cmd_pytest_fixtures.py removed (Wave 16 REPORT;
        # fixture-relationship catalog via pytest_fixture_dep edges).
        # W1233-impl: cmd_risk.py removed (Wave 16 REPORT/RANKER;
        # domain-weighted risk-ranking aggregate).
        # W1233-impl: cmd_safe_delete.py / cmd_safe_zones.py removed
        # (Wave 16 VALIDATOR; single-input verdict — parallel W1192
        # cmd_syntax_check pattern).
        # W1224-impl: cmd_side_effects.py / cmd_split.py / cmd_stats.py /
        # cmd_suggest_reviewers.py / cmd_surface.py / cmd_syntax_check.py /
        # cmd_telemetry.py / cmd_test_gaps.py / cmd_test_pyramid.py /
        # cmd_tx_boundaries.py / cmd_version.py / cmd_vuln_map.py /
        # cmd_vuln_reach.py / cmd_workflow.py / cmd_xlang.py removed
        # (per-symbol classifications, aggregates, composers, validator,
        # state-mutating, meta-registry; SKIP-disclosure docstrings).
        # W1233-impl: cmd_simulate_departure.py removed (Wave 16 SIMULATOR;
        # team-level counterfactual knowledge-loss rollup — parallel
        # W1221 cmd_simulate pattern).
        # W1233-impl: cmd_suggest_refactoring.py removed (Wave 16
        # COMPOSER; wraps run_all_detectors smells catalog + complexity
        # + fan-in into a ranked recommendation).
        # W1233-impl: cmd_testmap.py removed (Wave 16 REPORT;
        # test-coverage relationship rollup).
        # W1229: cmd_verify_imports.py removed from this pin — ships SARIF
        # via the verify_imports_to_sarif projection + global --sarif flag
        # (per-import hallucination-firewall projection, two closed-enum
        # rules: invalid-import (warning) for unresolved with FTS5 fuzzy-
        # match candidates, hallucination-import (error) for unresolved
        # with no candidates. The error band is deliberate — verify-imports
        # is the canonical "hallucination firewall" detector for LLM-era
        # code and the only verify-imports rule that escalates to error;
        # ``resolved`` rows are filtered upstream so SARIF consumers
        # never see non-actionable rows).
        # W1233-impl: cmd_why_slow.py removed (Wave 16 REPORT;
        # runtime-hotspot aggregate — dedicated detector cmd_hotspots
        # ships SARIF per W1210).
        # W1233-impl: cmd_ws.py removed (Wave 16 INFRASTRUCTURE;
        # workspace-management multi-repo grouping).
    }
)


def _cmd_files() -> list[Path]:
    """Return every ``cmd_*.py`` under ``src/roam/commands/``."""
    return sorted(p for p in _COMMANDS_DIR.glob("cmd_*.py") if p.name != "__init__.py")


def _sarif_carrier_filenames() -> frozenset[str]:
    """Resolve ``_SARIF_CONSUMERS`` CLI names into the underlying
    ``cmd_*.py`` basenames via ``_COMMANDS``.

    Two CLI names break the naïve ``cmd_<stem>.py`` filename-derivation
    heuristic:
      - ``algo`` lives in ``cmd_math.py`` (alias-only registration).
      - ``audit-trail-conformance-check`` lives in
        ``cmd_audit_trail_conformance.py``.
    Resolving through ``_COMMANDS`` is robust to both — a SARIF consumer
    whose CLI name doesn't match its filename stem still gets credited.
    """
    carriers: set[str] = set()
    for cli_name in _SARIF_CONSUMERS:
        entry = _COMMANDS.get(cli_name)
        if entry is None:
            continue  # surfaced by tests/test_sarif_consumer_list.py
        module_path, _attr = entry
        mod_name = module_path.rsplit(".", 1)[-1]
        carriers.add(f"{mod_name}.py")
    return frozenset(carriers)


def _module_docstring(path: Path) -> str:
    """Return the module-level docstring text (empty string when absent)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ""
    return ast.get_docstring(tree, clean=False) or ""


def _find_missing_sites() -> list[str]:
    """Return basenames of cmd_*.py files that neither ship SARIF nor
    carry the canonical SKIP-disclosure anchor."""
    sarif_carriers = _sarif_carrier_filenames()
    missing: list[str] = []
    for path in _cmd_files():
        if path.name in sarif_carriers:
            continue
        if _CANONICAL_ANCHOR in _module_docstring(path):
            continue
        missing.append(path.name)
    return missing


# ---------------------------------------------------------------------------
# Sanity guard: discovery must surface a non-trivial population.
# A silent "zero cmd_*.py files found" would let the negative-path lint
# pass even if the glob regressed.
# ---------------------------------------------------------------------------


def test_discovery_finds_command_files():
    """Discovery must find a substantial cmd_*.py population — the
    repo ships 200+ command modules per CLAUDE.md."""
    files = _cmd_files()
    assert len(files) >= 100, (
        f"AST discovery found only {len(files)} cmd_*.py files under "
        f"{_COMMANDS_DIR}. Either the glob is broken or the commands "
        f"directory was reorganized — investigate before trusting this lint."
    )


# ---------------------------------------------------------------------------
# The W1169 lint — block new cmd_*.py files that elide SARIF disclosure.
# ---------------------------------------------------------------------------


def test_sarif_disclosure_coverage():
    """Every cmd_*.py either ships ``--sarif`` (member of
    ``cli._SARIF_CONSUMERS``) OR documents its SKIP rationale via the
    canonical docstring anchor.

    See (internal memo) §8 for the
    audit framework + W1148 for the docstring template.
    """
    missing = set(_find_missing_sites())
    unexpected = missing - _KNOWN_MISSING
    assert not unexpected, (
        f"cmd_*.py file(s) missing SARIF disclosure "
        f"(not in _SARIF_CONSUMERS, no canonical anchor "
        f"{_CANONICAL_ANCHOR!r} in module docstring): "
        f"{sorted(unexpected)}.\n"
        f"Resolve by EITHER:\n"
        f"  (a) Adding the command to _SARIF_CONSUMERS in src/roam/cli.py "
        f"and wiring ctx.obj['sarif'] consumption (SHIP path), OR\n"
        f"  (b) Anchoring the SKIP rationale via the W1148 docstring "
        f"template (SKIP-DISCLOSURE path).\n"
        f"See (internal memo) §8 for "
        f"the decision framework + cmd_doctor.py for a canonical SKIP "
        f"example."
    )


def test_known_missing_pin_is_current():
    """Inverse drift guard — pinned entries in ``_KNOWN_MISSING`` must
    still need the carve-out. A stale entry hides the fact that the
    command was either fixed (docstring added) or promoted (now in
    ``_SARIF_CONSUMERS``).
    """
    missing = set(_find_missing_sites())
    stale = _KNOWN_MISSING - missing
    assert not stale, (
        f"_KNOWN_MISSING contains cmd_*.py file(s) that no longer need "
        f"the carve-out: {sorted(stale)}.\n"
        f"Either they grew the canonical docstring anchor OR were added "
        f"to _SARIF_CONSUMERS — remove them from _KNOWN_MISSING in this "
        f"file so the lint stays accurate."
    )
