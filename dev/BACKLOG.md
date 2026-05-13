# Backlog — current sprint queue

Forward-looking only. **What to build / research / test next.**
Read at session start to know what's on deck.

## Pre-dispatch dedup check

Before dispatching agents to fix findings from a new dogfood report,
run `python dev/dogfood_dedup_check.py --from-md <report.md>` (or
`--commands ...`). It greps `internal/dogfood/evals/<cmd>/*.md` for existing
`status: fixed-in-*` markers and surfaces likely-already-fixed findings.
Surfaced by W36.8's "50% already-fixed" finding in the 2026-05-13 batch.

Full demand index (~155 items, all tiers, source citations):
[`dev/ROADMAP.md`](ROADMAP.md). Pull items from there as they get
queued. When you ship one, delete the line; don't archive.

**Status as of 2026-05-12 (mid-session, dogfood sprint in flight)**:
Currently running a 19-agent parallel pass closing dogfood-corpus
findings + ROADMAP S-tier items. ~61 files dirty (uncommitted per
user directive). Working tree is composed entirely of in-flight or
just-shipped sprint work — see "Sprint 2026-05-12 — done + in flight"
section below.

Prior baseline: HEAD = `26b0320` (R10/R11, 2026-05-10). All sprint
work is unstaged; user directive is no commits.

⚠ **DO NOT** run `pytest tests/` (the full suite, parallel or
sequential) on the current hardware unsupervised. Targeted-file sweeps
ran fine (624 tests passed before commit `26b0320`); the full surface
is the suspect. Monitor RAM if attempting on the new PC; consider
`-n 2` capped parallelism instead of `-n auto`.

---

## Sprint 2026-05-12 — done + in flight

Dogfood-corpus sprint closing 6 systemic patterns + ROADMAP S-tier
items via parallel-agent waves. **NO COMMITS** until user direction
changes. Working tree state confirmed clean of pre-sprint dirt.

**Shipped this session (tests pass, working tree only):**

| Bundle | What | Tests |
|---|---|---|
| Fix A | JSON-parse class — `cmd_diff` empty-tree envelope, `cmd_pr_analyze` failure propagation, mcp_server JSONDecodeError defenses, `file_info` empty-input handling | 14 new + 60 regression |
| Fix B | Compound registry lookup — `_COMPOUND_REGISTRY` dict + import-time validation; fixes `vuln→vulns`, `complexity-report→complexity`; corrects `for_refactor` inverted `partial_success` flag | 7 new |
| Fix C | `caller_metric_definition` rollout — 12 commands labeled (10 expected + 2 bonus offenders from invariant scanner), new `docs/concepts/caller-metrics.md` | 15 new |
| Fix D | MCP parameter aliases — `_PARAM_ALIASES` + `_normalize_aliases` + sig-merging wrapper; 9 tools renamed (`name→symbol`, `target→symbol`, `file→path`, `pattern→query`); 9 rejected with reasons | 26 new + 140 regression |
| Fix E | Empty-state framing — `audit_trail_verify`, `audit_trail_conformance`, `missing_index`, `vulns` now emit explicit `state: "uninitialized"/"no_trail"/"no_scan"/"no_migrations"` instead of silent 0/6 / BROKEN; `session_metrics` exposes `partial_success_count` + `command_error_count` rename; `validate_plan` UNKNOWN_KIND carries `expected_fields` + `supported_kinds` | 7 new |
| Fix G | Conventions detector consolidation — new `conventions_helper.py` + `DEFAULT_EXCLUDE_PREFIXES`; `describe`/`understand`/`minimap`/`preflight`/`conventions` all delegate; outliers on roam-code dropped 9014→37 default / 47 with `--include-excluded` | 17 new + 240 regression |
| Task 2 | `StaleDbDirError` in `db/connection.py` (`get_db_path` wrapped with `_safe_mkdir`, sources tracked: `ROAM_DB_DIR env` / `.roam/config.json db_dir` / `<project default>`); `_first_error_message` cache preserved in trimmed envelopes; `STALE_DB_DIR` error_code surface | 16 new + 92 regression |
| Task 3 | `cmd_dogfood_aggregate.py` (NEW) — Click command parsing eval frontmatter + findings tables; `--status`/`--all`/`--severity`/`--since`/`--top`/`--limit` flags; default = open backlog view; registered in "Daily Workflow" category | 9 new |
| W3.1 | 4 surgical CLI fixes — `cmd_init` no unsolicited workflow write (default `--with-ci=none`); `cmd_describe` Stack-leak removed; **NEW** `cmd_batch_search` (symbol-only default, `--include-paths` opt-in); **NEW** `cmd_complete` (literal prefix via separate helper, FTS5 camelCase tokenizer found) | 9 new + 157 regression |
| W3.2 | Indexer rename edge-loss — `indexer.py:1618` dropped `and modified` clause (ROADMAP S2 line ref was stale — actual line was 1618, not 1409); `USER_VERSION` discipline test with schema-hash snapshot (current value is **12**, ROADMAP S16 was stale claiming `1`) | 5 new + 261 regression |
| W3.3 | `cmd_doctor` 18th check `_check_index_manifest_history` via `manifest.manifest_diff`; surface-consistency test already existed — extended with `_TOOL_METADATA` keys⊆declared-tools check | 8 new + 76 regression |
| W3.4 | `cmd_impact` bounded — `--depth 3`, `--max-callers 100`, `--timeout 30s` (cross-platform deadline check every 1000 nodes); `cmd_trace` `--max-hops 6` + `--exhaustive` opt-in; both Tier-3 skip-tier commands now usable | 6 new + 119 regression |
| W3.5 | CGA security regression tests (Fix S4 fail-closed + Fix S5 dirty-hash binding) — **the actual fixes were already shipped in commit `26b0320`** (ROADMAP entries S4/S5 are stale); added 11 contract-pinning tests | 11 new + 105 regression |

**Wave 4 shipped:** W4.1 doctor-breadcrumb (5 sites), W4.2 unique-signal
discovery surfaces, W4.3 capability decoration (10 commands), W4.4 bus-factor
exclude paths (252 noise dirs filtered on roam-code itself).

**Wave 5 shipped:** W5.1 detector tuning + stale-test cleanup, W5.2 Vue/Vitest
detection (test-pyramid now non-zero on Vue projects), W5.4 alias deprecation
(7 aliases moved to `_DEPRECATED_COMMANDS` with stderr+envelope warning),
W5.5 R19 memory substrate (`roam memory add/list/relevant`).
W5.3 still running (sparse/bounded for duplicates/x_lang/spectral).
Fix F shipped (response-volume auto-handle for 8 commands + `fetch_handle` v2 + batch/complete MCP parity).

**Wave 6 shipped:** W6.1 surface-count refresh (212→217; 145→149 MCP tools), W6.2 test-conventions consolidation (Fix-G mirror), W6.3 Vue SFC import resolution (real bug was SQL `WHERE language IN (...)` excluding `'vue'/'svelte'`; parser-level `_preprocess_vue` was already correct), W6.4 `roam next` agent router, W6.5 R20 ledger substrate (`roam runs start/log/end/list/show`).

Also: W5.3 shipped (sparse/bounded for duplicates/x_lang/spectral with --scope/--sample/--max-pairs flags).

**Wave 7 shipped:** W7.1 cleanup bundle (MCP wrappers for duplicates; `test_help` updated; numpy importorskip; ROADMAP S2/S16 corrected, S4/S5 marked SHIPPED in `df4a091`); W7.2 README/llms-install.md/mcp-server-card.json refreshed (22 new rows + 12 preset counts); W7.3 sprint-wide recheck — GREEN, 1 surgical bug fixed (`formatter._stringify_risk_item` missed `observation` field), 7 non-blocking concerns flagged; W7.4 R20 phase 2 auto-log integration (7 gate commands + helper); W7.5 capability registry +18 decorations (xfail allowlist 207→189). Total test-file count was actually 40 (not 24 as earlier BACKLOG sections claimed).

**Wave 8 shipped (strategic — R-series differentiators):**

| Agent | Scope |
|---|---|
| W8.1 | R18 graph-aware policy DSL — 4 clauses (`reachable_from`/`imports_from`/`clones_with`/`tested_by`) operational in `src/roam/policy/graph_clauses.py`; bounded execution (`--depth 3 / --max-nodes 100`); evidence dict has `status` field. 14/14 tests. **The moat.** |
| W8.2 | R26 proof-carrying PR bundle — `roam pr-bundle init/set/add/emit/validate` (12 subcommands); branch-scoped storage; KILLER FEATURE `--auto-collect` walks `.roam/responses/*.json` and folds prior commands into the bundle. 13/13 tests. **Roam Review MVP differentiator.** |
| W8.3 | R27 invariant/law mining — `roam laws mine/check/list/explain`; naming + import-layering + testing strategies (errors + co_change stubbed for v1); `Law.rule` dict shape-compatible with R18 clause dispatcher. **9 high-confidence laws mined on roam-code itself in 0.36s.** 13/13 tests. |
| W8.4 | R20 phase 3 — `roam replay <run_id>` (text/json/dry-run/execute modes; `--execute` refuses without `--dry-run` or `--no-dry-run`) + `roam agent-score` (transparent composite formula: 70% completion + 20% clean signal + 10% breadth). 11/11 tests. |
| W8.5 | R23 graph versioning — `roam graph-diff` + `roam architecture-drift`; hybrid in-degree shift threshold (`abs >=2 AND relative >=25%`); two-tier move detection (name+kind → HIGH, name-only → MEDIUM); cycle-dominated direction signal. 17/17 tests. Snapshot round-trip captures 18K symbols/19K edges in seconds. |

---

## Latent bugs surfaced by Wave 8 (queue for next session)

| Bug | Impact | Effort |
|---|---|---|
| `output/formatter.py:555` — `json_envelope(agent_contract=...)` kwarg is silently OVERWRITTEN by the auto-derived block from `summary.next_commands`. W8.4 AND W8.5 independently hit this. W7.3 also flagged that `cmd_architecture_drift.py` + `cmd_graph_diff.py` are likely losing their `facts` arrays this way (though W8.5 worked around it via `next_steps=` kwarg). Fix: MERGE agent_contract kwarg with auto-derived, instead of overwrite. | High — class of latent bugs across many commands | 1h surgical fix + test |
| `tests/test_capability_decoration.py::test_every_command_eventually_decorated` flake on FIRST run of the full 38-file sweep, passes on re-run. Likely module-global `_CAPABILITIES` state leak from another test importing mcp_server. | Test flake / order-dependency | 1h: add session-scoped fixture resetting `_CAPABILITIES` |
| `.dev` IRI leftovers in `cmd_stale_refs.py` (2116, 2121, 2140, 2670 — both producer AND verifier), `cmd_sbom.py:323` (`https://roam-code.dev/spdx/...`), `mcp-server-card.json:94,95`, `mcp_server.py:5243` description, `cmd_cga.py:4,45,157` docstrings. Only the CGA path was migrated to `.com` with `_LEGACY_PREDICATE_TYPES`. Risk: dead domain resolution. | Cosmetic + provenance integrity | 2h: mirror CGA migration for StaleRefs + SPDX |
| `roam surface --json` reports `mcp_tool_count: 0` when fastmcp absent (silent fallback anti-pattern Pattern 2). Should be `null` + `mcp_state: "fastmcp_not_installed"`. | UX consistency | 30min |
| `dogfood-aggregate` envelope missing `partial_success` + `state` fields (W7.3 found this — others emit them consistently). | Parity | 15min |
| `roam-code` installed via uv tool is stale (missing all sprint commands). Daily shell `roam` invocations don't see anything from this sprint until `pip install -e .` from local repo. | Deployment, not code | user action |
| LazyGroup `--agent` flag fix (W6.5) also fixed silent `roam memory add --agent X` breakage — verify other global-positional combos still work for the new W7+W8 commands. | Coverage | covered by recheck |

---

**Wave 9 shipped (final integrative recheck + cross-cutting fixes):**

| Agent | Scope | Result |
|---|---|---|
| W9.1 | Final integrative recheck — 22 smoke invocations PASS, 5 cross-Wave-8 integrations PASS, 592 tests PASS, 1 surgical fix to `test_capability_decoration.py` (added 4 W8 commands to DECORATED_TODAY=32). **Verdict: GREEN.** | ✅ |
| W9.2 | Cross-cutting bug fixes: (1) `json_envelope(agent_contract=)` merge fix at `formatter.py:540-576` — explicit kwarg now preserved + auto-derived next_commands fills gaps; (2) `test_capability_decoration` session-scoped fixture; (3) `.dev` IRI cleanup across StaleRefs+SPDX+CGA docstrings+both mcp-server-card.json files (with `_LEGACY_STALE_REFS_PREDICATE_TYPES` back-compat); (4) `dogfood-aggregate` envelope now emits `state` + `partial_success`. | ✅ 80 targeted + 329 sweep tests pass |
| W9.3 | Final docs refresh — README + CLAUDE.md + llms-install.md + mcp-server-card.json refreshed to 217→223 commands; `dev/build_command_reference.py` ran, regenerated landing-page HTML. | ✅ 23 doc-consistency tests pass |

---

**Wave 10 shipped (capstone + substrate):**

| Agent | Result |
|---|---|
| W10.1 | R24 Agent Constitution shipped. 16/16 tests. Smoke ran real subprocesses + aggregated correctly. Wired into `roam next` + R20 auto-log. |
| W10.2 | CLI `.roam/responses/` writing closes W9.1 pr-bundle gap. 7/7 tests. End-to-end `envelopes_scanned: 3, commands_run: 3` verified. |
| W10.3 | R28 side-effects + idempotency. 12/12 tests + 50 regression. 12,117 symbols classified in <5s on roam-code. |
| W10.4 | Capability auto-decoration: 181 commands decorated, allowlist 190 → 8 (all by-design). 218/226 decorated = 96.5%. |
| W10.5 | R22 Confidence pilot — 5 commands migrated, helper at `output/confidence.py`. 25 tests + 188 regression. Migration recipe documented. |

**Wave 11 shipped (final recheck — sign-off):**

| Phase | Result |
|---|---|
| Smoke (14 invocations) | ALL PASS |
| Cross-Wave-10 integrations (A-F) | F/6 PASS (1 noted caveat on `health` auto-log — by-design, not bug) |
| Triple-chain (R24+R20+W10.2) end-to-end | PASS — pr-bundle auto-collect `envelopes_scanned: 3, commands_run: 3` |
| Capability registry | 218/226 = 96.5% (matches W10.4 claim) |
| Tests | 254 Wave 6-10 + 438 regression + 173 R22 consumer = 865 PASS |
| Surgical fixes | 3 (test_v7_features::test_help_has_categories rewritten; README + CLAUDE.md headline counts 223 → 226) |
| **VERDICT** | **🟢 GREEN — ready for next sprint** |

---

**Wave 12 shipped (polish + integration):**

| Agent | Result |
|---|---|
| W12.1 | pr-bundle ↔ R28 — severity matrix (H/M/L) + smart dedup + legacy-backfill; 35 tests. Flagged 2 R28 classifier-fidelity gaps. |
| W12.2 | R22 sweep — in flight (8 cmd files) |
| W12.3 | helpers.py auto_log policy doc (3 tiers + 4 exclusion categories per LAW 7); 6 LAW 4 fact-anchoring fixes; 14 new tests. Found `_derive_agent_contract` is the dominant LAW 4 leak (30+ commands). |
| W12.4 | mcp_tool_count resolved as 57 (CLAUDE.md was claiming 58); README:325 → 226; llms-install ×2 lines refreshed. Flagged README missing 3 W10 commands. |
| W12.5 | Facade now delegates to JavaScriptConvention adapter for JS/TS/Vue; adapter extended with smoke/sanity recognition. Pattern 4 closed for test detection. 47+61+452 tests pass. |

---

**Wave 13 shipped (strategic + cleanup + recheck):**

| Agent | Result |
|---|---|
| W13.1 | R15 `roam agents-md` — 9-section AGENTS.md in 1.2s; 10 tests pass |
| W13.2 | R16 modes — read_only/safe_edit/migration/autonomous_pr cumulative 33/43/51/67 cmds; 30 tests pass |
| W13.3 | cmd_mcp 38s→<2s — root cause was `_ensure_fresh_index` (full reindex on every boot); 11 tests pass |
| W13.4 | LAW 4 humanizer (`critical: 5` → `5 critical findings`); R28 Path.* classifier; README rows constitution/side-effects/idempotency; 13 new tests |
| W13.5 | Final recheck — AMBER from stale-binary false alarm (commands ARE registered, surface=229); 1 surgical R28 classifier fix landed |

---

**Wave 14 shipped (synergy + composition + 100% capability):**

| Agent | Result |
|---|---|
| W14.1 | E2E loop holds — 10/11 PASS (1 designed-skip); perf 0.91s full loop; flagged 3 architectural concerns (auto_collect Pattern 3, impact no auto-log, preflight gating). Dogfood baseline = 143/385/62 (UNCHANGED — substrate didn't feed back yet). |
| W14.2 | 4 synergies wired — next consults mode; pr-bundle respects mode (`mode_restricted` state); agents-md has Current mode section; runs start records mode in meta. 10 new + 75 regression tests. |
| W14.3 | README +3 rows; .gitignore scratch block; doctor stale-install advisory check. 3 new + 84 regression tests. |
| W14.4 | 100% in-scope coverage — all 3 remaining commands already had decorators in source; gap was pure test bookkeeping (DECORATED_TODAY 218→221). Allowlist 0. |
| W14.5 | `roam brief` shipped — 9 tests; 27-line text output; ~450ms in-process; composes 5 sub-systems via Python APIs (zero subprocess). |

---

**Wave 15 in flight (measurement + synergy + R28 phase 2 + final):**

| Agent | Scope |
|---|---|
| W15.1 | Re-run the 212-eval dogfood corpus against current state; mark `status: fixed-in-13` where envelopes are now clean. **The "did we actually improve roam?" measurement.** |
| W15.2 | Wave-14 follow-ups — (a) auto_collect → summary (Pattern 3); (b) impact auto_log; (c) preflight writes to responses/ when bundle exists OR run active; (d) runs end --with-pr-bundle-emit; (e) promote agents_md private helpers to public API |
| W15.3 | R28 phase 2: causal graph — directional cause/effect edges; `roam causal-graph <symbol>` |
| W15.4 | R28 phase 2: transaction boundaries — `roam tx-boundaries` detects begin/commit/rollback regions. R28 complete after this. |
| W15.5 | Final integrative recheck — verify substrate composes; ensure dogfood re-run is actionable; GREEN/AMBER/RED + sprint-of-sprints summary |

---

## Follow-ups surfaced by Wave 6 (queue for next session)

Items the sprint discovered but didn't fully close. Ordered by impact-per-effort.

| Item | Where | Effort |
|---|---|---|
| MCP wrappers for W5.3's new CLI flags — wire `--scope`/`--sample`/`--max-pairs` into `roam_duplicates`/`roam_x_lang`/`roam_spectral` MCP signatures | `src/roam/mcp_server.py` | 30 min |
| `test_smoke.py::test_help` fails after W5.4 removed `onboard`/`churn` from "Getting Started"/"Codebase Health" — categories may now be empty and dropped from `--help`. Either restore categories with other entries or update the test | `src/roam/cli.py` + `tests/test_smoke.py` | 30 min |
| README full-command table missing rows for 9 new commands: `batch-search`, `complete`, `db-check`, `dogfood-aggregate`, `explain-command`, `memory`, `next`, `runs`, `surface` | `README.md` | 1h (consider re-running `dev/build_command_reference.py` if it's an autogenerator) |
| README MCP tool table missing 12 new tools: `roam_api`, `roam_ask`, `roam_changelog`, `roam_conventions`, `roam_fetch_handle`, `roam_for_bug_fix`, `roam_for_new_feature`, `roam_for_refactor`, `roam_for_security_review`, `roam_session_metrics`, `roam_validate_plan`, `roam_verify_imports` | `README.md` | 30 min |
| `llms-install.md` has stale `211 commands` | `llms-install.md` | 5 min |
| `templates/distribution/landing-page/.well-known/mcp-server-card.json` has stale tool total (137 vs 149) | the JSON file | 5 min |
| ~~Count-drift across README/CLAUDE/llms-install/mcp-server-card per wave~~ → ELIMINATED 2026-05-13 via `dev/build_readme_counts.py` (Wave 21). Run `python dev/build_readme_counts.py --apply` after any command-adding wave; `--check` gates CI. Markers: `<!-- BEGIN auto-count:NAME --> ... <!-- END auto-count:NAME -->`. JSON updates target specific keys without reformatting. | `dev/build_readme_counts.py`, `tests/test_auto_count_script.py` | shipped |
| Count-drift coverage split (W23.1 investigation, 2026-05-13): `scripts/sync_surface_counts.py` and `dev/build_readme_counts.py` are **intentional cousins, not duplicates**. `build_readme_counts.py` owns README/CLAUDE/llms-install (marker-protected Markdown blocks) + both `mcp-server-card.json` copies (byte-identical writer). `sync_surface_counts.py` owns landing-page HTML / llms.txt / `server.json` / `skills/roam/SKILL.md` / `competitor_site_data.py` / `docs/ci-integration.md` (free-form prose, regex substitution). The mcp-card entries in `sync_surface_counts.py` are intentionally no-op (`repl=None`). Both scripts gate CI in the `doc-hygiene` job. Cross-references added to each script docstring. Do not consolidate without first inserting markers into the 9+ landing-page HTML files (~50 LOC of test/CI churn) — deferred. | `scripts/sync_surface_counts.py`, `dev/build_readme_counts.py` | informational |
| R20 phase 2 — `roam.runs.helpers.auto_log(envelope, action, target)` helper + wire into `cmd_preflight`/`cmd_diff`/`cmd_critique`/`cmd_pr_prep`/`cmd_pr_analyze`/`cmd_attest`/`cmd_verify` | new helper + ~7 cmd files | 2-3h |
| R20 phase 3 — CGA signing chain for `events.jsonl` (HMAC-rolling-hash; one event's signature = prev sig + new JSON line) | `src/roam/runs/ledger.py` + `src/roam/attest/cga.py` | 4-6h |
| `.gitignore` decisions for `.roam/memory.jsonl` and `.roam/runs/` — currently NOT gitignored. Strategic question: commit-by-default (memory portable across machines) or local-by-default (privacy)? | `.gitignore` template in `cmd_init.py` | strategic |
| `roam replay <run_id>` + `roam agent-score` — both now unblocked by R20 substrate | new cmd files | 4h |
| Auto-log integration: detect `ROAM_RUN_ID` env var and auto-log every roam invocation | `src/roam/cli.py` middleware | 2h |
| Capability Registry full migration — W4.3 decorated 10 commands; remaining 207 + the 8 split-brain dicts collapse | many files | 1-2 days incremental |
| dev-deps reconciliation: venv missing `numpy`/`scipy`/`pytest-xdist`/`pytest-timeout`. W5.5 installed `pytest+xdist` via `uv pip install`. `pip install -e ".[dev]"` next session | venv | 5 min |
| ROADMAP doc cleanup — S2 line ref (1409→1618), S16 USER_VERSION (1→12), S4+S5 already shipped in `26b0320` | `dev/ROADMAP.md` | 15 min |
| `test_v12_2.py::TestGraphBackendDispatch::test_pagerank_dispatches` — `ModuleNotFoundError: numpy`. Either install numpy or xfail/skip with `pytest.importorskip` | the test file | 5 min |
| `cmd_mcp` async/fast freshness check — 38s startup currently in skip-tier (SYNTHESIS Rank 9) | `cmd_mcp.py` startup path | 3h |
| Vue SFC adapter for the bridges layer — `bridge_template.py` covers Jinja/Django/ERB but not Vue; W6.3 fixed orphan/verify-imports via the simpler SQL-filter route. A proper bridge would help cross-component refactor analysis | new `bridge_vue.py` | 4h |
| Naming-aliases facade vs adapter parity (W6.2 surface) — `JavaScriptConvention.classify_kind` is stricter than the module-level facade. Decide if one should subsume the other | `test_conventions.py` | 1h |

---

## Strategic still-queued (NOT mechanical — needs design)

- R15 `roam agents-md` — generate AGENTS.md from indexed codebase + conventions + danger zones
- R16 Agent modes — read_only/safe_edit/migration/autonomous_pr; pairs with `roam permit`
- R18 Graph-aware policy DSL — `roam rules` clauses `reachable_from`/`imports_from`/`clones_with`/`tested_by`. **The moat.**
- R21 Multi-Agent Lease System — stateful claim/release over graph-partition substrate
- R22 Confidence/Uncertainty contract — every list-of-findings tool returns `{value, confidence, reason}` triples; big mechanical sweep
- R23 Graph Versioning — `roam graph-diff main..HEAD`, `roam architecture-drift`
- R24 Agent Constitution — `.roam/constitution.yml` unifying AGENTS.md + policy + memory + required checks
- R26 Proof-carrying PR bundle — `{intent, context_read, affected_symbols, risks, tests_required, tests_run, known_non_goals, roam_verdict}`. **Roam Review MVP differentiator.**
- R27 Invariant/law mining — `roam laws mine` discovers unwritten rules; `git diff | roam laws check` enforces. Self-installing constitution.
- R28 World Model expansion — side-effect ledger, causal graph, transaction boundary detector, idempotency detector

Phase-0 monetisation freebies (per STATUS-2026-05-10 user directive) — `permit`/`postmortem`/`article-12-check` already exist (confirmed by W4.3 capability registry). Refinement and packaging is the next step. The 2026-05-13 external research pass is captured in `dev/MONETIZATION-OPPORTUNITIES-2026-05-13.md`; it adds Agent Governance Evidence Pack, Premium Rules/Policy Packs, Team MCP Gateway, Security Reachability Triage, Agent Vendor Benchmark, Framework Intelligence Packs, and Team Index Cache as monetisable extensions of already-built primitives.

---

## Stale-doc cleanup discovered mid-sprint

Small ROADMAP / test-suite rot found during the sprint. None blocking,
all worth fixing on next pass:

| Item | Location | Fix shape |
|---|---|---|
| `USER_VERSION` already at `12`, not `1` | ROADMAP S16 reference | update text |
| Indexer rename-edge-loss is line `1618`, not `1409` | ROADMAP S2 reference | update text |
| CGA S4 (fail-closed verify) was already shipped in `26b0320` | ROADMAP S4 | mark done / remove from queue |
| CGA S5 (dirty-hash binding) was already shipped in `26b0320` | ROADMAP S5 | mark done / remove from queue |
| `tests/test_v12_2.py::test_predicate_type_constant` asserts dropped `.dev` IRI literal (migration to `.com` already happened, `_LEGACY_PREDICATE_TYPES` covers back-compat) | test file | update or delete; `test_predicate_type_now_uses_owned_domain` covers post-migration |
| Orphaned test-body fragment in `tests/test_cga.py:797-814` (copy-paste leftover at bottom of `test_clean_tree_emits_with_none_dirty_hash`) | test file | delete dead lines |
| `monkeypatch.setattr(cga_mod.subprocess.run, ...)` is a foot-gun if cosign mocks ever need git shellouts — first-cut fix landed in `tests/test_cga_fail_closed.py` but the older pattern in `test_cga.py::TestCosignWiring` has same risk | test pattern | revisit when next cosign mock is needed |
| `tests/test_v7_features.py::TestInit::test_init_creates_files` and `test_init_skips_existing` enshrined the BUGGY init default; W3.1 rewrote both — confirm next session that the rewrites match the new contract | test file | already updated by W3.1 |

---

## Tier-A items NOT yet in flight (mechanical, parallelisable later)

From the sweep that found ~10 NEW high-impact items + the SYNTHESIS
Rank 8-20 leftovers. Listed by impact-per-hour:

- `cmd_mcp` async/fast freshness check — 38s startup is currently in skip-tier (Rank 9, est 3h, 1 H finding closed)
- PHP/Laravel taint rule pack (Rank 12, 4-6h, 1 H — highest project-value for real-world PHP apps)
- Vue/Vitest detection for `test-pyramid`/`endpoints`/`n1` (Rank 15, 4h, 3 H)
- Vue SFC import resolution for `orphan-imports`/`verify-imports` (Rank 17, 3h, 2 H)
- Alias consolidation — deprecate 7 redundant aliases (`digest`/`math`/`refs`/`snapshot`/`trend`/`onboard`/`churn`) currently in `_INTENTIONALLY_UNCATEGORISED` allowlist (Rank 18, 1h)
- Sparse spectral / scale algorithms for `duplicates`/`x_lang`/`spectral` "graph too large" bailouts (Rank 19, 4h, 3 H)
- Extend trend tracking beyond `dead_exports` (Rank 20, 3h)
- Detector tuning: `SfxmlExtractor._TAG_TO_KIND` maps `customobject` not `object` (R10.5 from STATUS-2026-05-10)
- Detector tuning: `roam math` false-positive on `format_table` (R11.B — recognize cell-formatting nested loops as O(rows×cols), not nested-iteration anti-pattern)

**A1 full Capability Registry consolidation** — W4.3 decorates 10
high-frequency commands; the FULL collapse of the 8 split-brain dicts
in `mcp_server.py` (`_CORE_TOOLS`/`_REGISTERED_TOOLS`/`_NON_READ_ONLY_TOOLS`/`_TASK_REQUIRED_TOOLS`/`_TASK_OPTIONAL_TOOLS`/`_DESTRUCTIVE_TOOLS`/`_TOOL_METADATA`/`_DEPRECATED_COMMANDS`) into a single derived-view source remains queued. Foundational for R13.

---

---

## R12 verification — DEFERRED until new PC is set up

Belt-and-suspenders quality gate before pivoting to monetisation.
Status: 1 of 7 phases queued in TaskCreate (V1 lint), the other 6
are documented in `STATUS-2026-05-10.md` waiting for a safe host.

| Phase | What |
|---|---|
| V1 | `ruff check` + `ruff format --check` across src/ + tests/ |
| V2 | Full pytest parallel ⚠ (suspected crash-trigger) |
| V3 | Full pytest sequential ⚠ |
| V4 | Schema-migration idempotency smoke |
| V5 | Dogfood: `roam health` / `doctor` / `math` / `critique` / `index --force` |
| V6 | `/verify` cross-family pass on `26b0320` |
| V7 | Final sign-off report |

After R12 returns green: pivot to Phase 0 monetisation freebies
(`roam permit`, `roam postmortem`, `roam ai-governance-check` —
parallelisable) per the user's stated direction.

---

## R13–R17 — agent-OS positioning rounds (2026-05-11)

External strategic input from a ChatGPT positioning audit (full
capture: `dev/agent-os-positioning-2026-05-11.md`). Five rounds queued
in dependency order:

| Round | What | Risk |
|---|---|---|
| **R13** | Agent-OS metadata pass — add `phase`, `recommended_next_tools`, `avoid_when`, `confidence_fields` to `_TOOL_METADATA` for every @_tool. Surface in `roam_catalog`. | LOW — pure substrate |
| **R14** | Hero-copy A/B ("Agents should not edit blind. Roam is their map.") + capability-coverage reframe ("145 capabilities across 9 categories") + rename playful commands' aliases (vibe-check → intent-check, weather → churn, dark-matter → hidden-complexity) | LOW — website only |
| **R15** | `roam agents-md` + `roam next` (agent router) + prompt snippets product surface. Free-OSS, viral. | MED — new commands |
| **R16** | Agent modes (read_only/safe_edit/migration/autonomous_pr) + `roam intent-check` + `roam agent-score`. Pairs with `roam permit`. | MED |
| **R17** | Reposition Roam Cloud as "governance for agent-written code" — Cloud dashboard cards for which-agents-changed-what, blast-radius distribution, ignored-warning trail. | MED — copy + dashboard |

R13 is the highest-leverage low-risk item. R14 + R15 are
parallelisable with R13. R16/R17 are mid-term, paired with the
monetisation Phase-0 work.

---

## R18–R25 — agent-OS architecture rounds (2026-05-11, round 3)

ChatGPT architectural round (full capture:
`dev/agent-os-architecture-2026-05-11.md`). These are larger, more
strategic builds than R13-R17.

| Round | What | Strategic note |
|---|---|---|
| **R18** | Graph-aware policy DSL — `roam rules` clauses `reachable_from`, `imports_from`, `clones_with`, `tested_by`. Pairs with `roam permit`. | **The moat.** Path-aware policy is commodified; graph-reachability is something we can do today because we have the graph substrate. |
| **R19** | Repo-local agent memory — `.roam/memory.jsonl` + `roam memory add/list/relevant`. Distinct from LLM/Cursor/Claude memory. | Makes Roam *portable across agent vendors*. Strategic moat. |
| **R20** | Agent Run Ledger — per-agent-run event stream signed via existing CGA chain. Powers `roam replay`, `roam agent-score`, `roam audit-trail`. | The UX layer on top of the Phase-4 Audit Trail product. |
| **R21** | Multi-Agent Lease System — stateful claim/release over the existing graph-partition substrate. | Pairs with `roam orchestrate` / `roam fleet`. |
| **R22** | Confidence/Uncertainty contract — every list-of-findings tool returns `{value, confidence, reason}` triples. | Mechanical sweep. |
| **R23** | Graph Versioning — `roam graph-diff main..HEAD`, `roam architecture-drift`. | Pairs with `roam trends`. Marketing: *"not just what changed in code, but what changed in the system structure."* |
| **R24** | Agent Constitution (`.roam/constitution.yml`) — unifies AGENTS.md + policy rules + memory + required checks. | Capstone primitive — the single declarative file an agent reads. |
| **R25+** | Pluggable Analyzer protocol (`roam-plugin-*` for nextjs/laravel/prisma/django/…). | Multi-quarter direction. Bridge architecture is the substrate. |

**Top-5 priorities (per ChatGPT, validated against our codebase)**:
R15 Decision Engine, R18 Policy Engine, R3-context-pack extension,
R20 Run Ledger, R19 Repo Memory. These five compound into the
killer loop: *task → context → plan → permit → edit → critique →
record → memory*.

---

## R26–R28 — control plane rounds (2026-05-11, round 4)

ChatGPT round 4 (50 ideas, full capture:
`dev/agent-os-control-plane-2026-05-11.md`, raw paste preserved at
`dev/chatgpt-paste-2026-05-11.md`). Round 4 *formalises* rounds
1-3 under the thesis:

> Roam is a local control plane for autonomous coding agents.
> *"Roam helps agents earn the right to change code."*

Most of round 4's 50 ideas **sharpen** R13-R25 rather than replace
them — see the in-repo capture for the integration table. Three
new rounds added for the genuinely category-defining ideas:

| Round | What | Strategic note |
|---|---|---|
| **R26** | **Proof-carrying PR bundle** — every PR ships `{intent, context_read, affected_symbols, risks, tests_required, tests_run, known_non_goals, roam_verdict}`. Review can BLOCK on missing proof. | **THE Roam Review differentiator** vs CodeRabbit/Greptile/Qodo. Phase-2 MVP priority. |
| **R27** | **Invariant/law mining** — `roam laws mine` discovers repo's unwritten rules from existing code + tests + git history; `git diff \| roam laws check` enforces. | Self-installing constitution. Pairs with R18 + R24. |
| **R28** | **World Model expansion** — side-effect ledger (#42), causal graph (#41), transaction boundary detector (#43), idempotency detector (#44). | One sprint of structural-graph work; unlocks 4 commands. |

Round-4 also adds new hero-copy candidates worth A/B testing:

- **"Roam helps agents earn the right to change code."** ← sharpest
- "Roam is a local control plane for autonomous coding agents."
- (Round 1, still strong) "Agents should not edit blind. Roam is their map."

The 4 category-defining ideas across all 50:
proof-carrying PRs (R26), agent attention audit (R20 + R16),
invariant mining (R27), codebase immune memory (R19 + #50).
Each is *uncopyable* without our graph substrate + MCP session
tracking.

---

## Next pickup — pick from ROADMAP

When this queue clears (it has), pull from `ROADMAP.md` in this order:

1. **Tier ★★★★ — pick one focus area at a time**:
   - **A** = architecture substrate (Capability Registry adoption, migration
     sequence numbers, finding registry, split health(), version stamps,
     MCP versioning, step-completion manifest)
   - **B** = perf heavy hitters (fused AST walker, cache controller reads
     in `_find_eager_loads`, bulk-fetch n1 helpers, ProcessPoolExecutor,
     skip-git-when-HEAD-unchanged)
   - **C** = GTM (Marketplace listing, Starter caps, Founding Customer
     lock, /enterprise pull, annual toggle)
   - **D** = site/copy/CTA pass (hero CTAs on /pricing + /compare, kill
     default-AI-prose lede, fix docs subnav)
   - **E** = agent/MCP DX (`roam_ask` MCP tool, SKILL.md rewrite,
     compact contract block, soft-enforce destructive tools)
   - **F** = security tier-2 (predicate IRI, /security cleanup, vuln
     telemetry endpoint)
   - **G** = DX onboarding (R1 stop unsolicited CI write, R2 install
     ordering, R3 short --help, R7 OneDrive auto-protect, R10 compact
     welcome banner)
2. **Tier ★★★** strategic moves once ★★★★ winds down

`ROADMAP.md` has a "Sequencing recommendation" section near the bottom
that lays out 7 sprints in dependency order — use it for scope decisions.

---

## ANTI-PRIORITIES (do not revisit unless evidence flips)

- Auto-deploy from `git push` — user explicitly chose manual wrangler
- "AI PR review tool" framing — locked to "structural intelligence layer"
- Per-dev pricing as launch tier — flat $99/$299/$799/$1,499 per pricing v3
- README hero rewrite — already correct
- Mailto → Stripe migration today — gated to specific event per pricing v3
- Page restructure to 7 sections — homepage stays at 13, restraint via spacing
- Per-session version bumps — accumulate under `[Unreleased]`
- "agent senses" being killed from copy — locked positioning keeps it
- Auto-deletion of dead exports — manual triage required (public-API risk)
- Building Pro+ tier ($45/dev/mo) pre-emptively — wait for 5+ Business asks
- IDE plugin as standalone product — wait for first Pro+ Audit Trail customer

---

## User-action items (cannot be fixed from code)

- **CI test (3.13) bypass** — workflow is correctly configured at
  `.github/workflows/roam-ci.yml:37` (matrix includes 3.13). The
  "expected but not running" status is a stale required-check name in
  GitHub's branch-protection settings. Fix in repo Settings → Branches
  → Protection rules: re-pin the required check or remove the stale
  expectation. Only the repo owner can do this.
