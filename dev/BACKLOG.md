# Backlog — current sprint queue

Forward-looking only. **What to build / research / test next.**
Read at session start to know what's on deck.

## Closures since 13.0 ship (W58-W102 autonomous extension)

The ~60-wave autonomous extension that ran after the 13.0 PyPI release
closed most of the Tier-A items that BACKLOG/ROADMAP flagged as open.
Strike-throughs are scattered through the doc; consolidated index here
for fast lookup:

| Item | Shipped in | Notes |
|---|---|---|
| ~~PHP/Laravel taint rule pack (Rank 12)~~ | W78 + W79 | 5 YAML / 101 entries; W79 also fixed engine BFS query bug (entire `roam taint` had been silently returning 0 findings since v12) |
| ~~A1 `_DESTRUCTIVE_TOOLS` split-brain collapse~~ | W98 | derived `frozenset` view via `@_tool(destructive=True)` kwarg — POC for the remaining 6 dicts |
| A1 `_DEPRECATED_COMMANDS` collapse | W100 (in flight) | parallel pattern to W98 |
| ~~A3 Detector registry + `@detector` decorator~~ | W84 + W85 | 11→34 detectors decorated; `roam math --list-detectors / --only / --exclude` |
| ~~A4 Finding Registry substrate~~ | W89 + W93 + W95 + W99 | `db/findings.py` (254 LOC) + `cmd_findings.py` (398 LOC, list/show/count); 3 detectors migrated (clones 584, dead 537, complexity in flight via W102) |
| ~~A6 Per-component VERSION stamps~~ | W81 | ABCs + 3 schema columns + USER_VERSION→15 |
| ~~A7 MCP tool versioning~~ | W83 | `_TOOL_METADATA` hoisted above fastmcp gate (CLI-only envs now work) |
| ~~A8 Indexer step-completion manifest~~ | W82 | + `roam doctor` advisory + USER_VERSION→14 |
| ~~B3 `roam n1` bulk-fetch~~ | W86 | 2 new bulks; ~200 SQL + ~500 disk reads saved per invocation |
| ~~B3.5 candidate-filter N+1 close~~ | W91 | gap-models filter 100 queries → 1 |
| ~~B5 git-history skip on unchanged HEAD~~ | already shipped (sentinels W87) | 94% warm-run speedup confirmed |
| ~~B6 DB pragma tuning~~ | already shipped (sentinels W92) | |
| ~~B8 FTS5 `docstring` column~~ | already shipped (sentinels W94) | + USER_VERSION→17 to hash `_FTS5_SCHEMA_COLUMNS` (W97) |
| ~~C9 Trust page~~ | W90 | `trust.html` 434 lines; honest SOC 2 Q1 2027 / ISO 42001 Q3 2027 stance |
| ~~C11 Pricing FAQ block~~ | W88 | 7 `<details>` items on `pricing.html`, +96 LOC |

W74 (BACKLOG/CHANGELOG reconciliation) drafted the close-out audit;
W78-W102 executed it. The "Tier-A items NOT yet in flight" section
below is now mostly STALE -- most of its remaining concerns are
closed. The "Strategic still-queued" R-series items remain accurate
(R21 lease was shipped at 13.0 ship; everything else still needs
design work).

## Closures since W102 (W103-W140)

The session immediately following the W102 doc-consolidation wave
closed 15 findings-registry migrations, 2 more A1 dict collapses,
4 production bug fixes, the long-queued W123/Wave28.3
`register_framework_profile`, and a batch of doc/audit sweeps.
Strike-throughs on the originating lines are kept; this is the
fast-lookup index. Items prefixed `(ADD)` were not pre-queued in
BACKLOG.md — they emerged this session and are recorded here as
completed-but-not-pre-queued for the audit trail.

| Item | Shipped in | Notes |
|---|---|---|
| ~~A1 `_NON_READ_ONLY_TOOLS` collapse~~ | W108 | derived view from `@_tool(read_only=False)` kwarg; mirrors W98 `_DESTRUCTIVE_TOOLS` pattern |
| (ADD) A1 `_NON_IDEMPOTENT_TOOLS` collapse | W113 | derived view from `@_tool(idempotent=False)` kwarg; 4 of 8 A1 dicts now collapsed |
| (ADD) Findings registry — `smells` detector | W109 | migrated to `db/findings.py` substrate |
| (ADD) Findings registry — `n1` detector | W110 | migrated to substrate |
| (ADD) Findings registry — `missing-index` detector | W111 | migrated to substrate |
| (ADD) Findings registry — `over-fetch` detector | W114 | migrated to substrate |
| (ADD) Findings registry — `bus-factor` detector | W115 | migrated to substrate |
| (ADD) Findings registry — `auth-gaps` detector | W116 | migrated to substrate |
| (ADD) Findings registry — `vulns` detector | W117 | migrated to substrate |
| (ADD) Findings registry — `invariants/laws` detector | W119 | migrated to substrate |
| (ADD) Findings registry — `hotspots` detector | W120 | migrated to substrate |
| (ADD) Findings registry — `taint` detector | W122 | migrated to substrate |
| (ADD) Findings registry — `vibe-check` detector | W125 | migrated to substrate |
| (ADD) Findings registry — `orphan-imports` detector | W132 | migrated to substrate |
| (ADD) Findings registry — `conventions` detector | W133 | migrated to substrate |
| (ADD) Findings registry — `pr-risk` detector | W134 | migrated to substrate |
| (ADD) Findings registry — `duplicates` detector | W136 | migrated to substrate; surfaced duplicates-vs-clones overlap audit (also W136) |
| (ADD) `batch_search` / `batch_get` per-query try/except | W103 | real bug surfaced by W101 test investigation — one failing query no longer kills the batch |
| (ADD) `cmd_stale_refs.py` `--attest` mkdir crash fix | W126 | W112 HIGH-severity — output dir wasn't being created |
| (ADD) `cmd_surface.py` `mcp_tool_count` read source fix | W138 | was reading wrong source; envelope now includes `mcp_tool_count_by_preset` |
| (ADD) Lease midnight-UTC clock race fix | W135 | W112 medium — date rollover no longer races lease acquisition |
| ~~W123 / Wave28.3 `register_framework_profile`~~ | W123 | long-queued plugin substrate primitive shipped — frameworks can now register profile metadata |
| (ADD) `llms-install.md` sweep | W124 | counts + surface refresh |
| (ADD) `architecture.html` findings registry section | W130 | landing-page docs reflect the substrate |
| (ADD) `CLAUDE.md` Pattern 4 + findings section refresh | W139 + post-W138 | dev/build_readme_counts.py touched for the auto-count generator |
| (ADD) apologetic-comment audit in `tests/` | W112 | swept the test corpus |
| (ADD) MCP tool count audit | W128 | reconciled the count across surfaces |
| (ADD) duplicates-vs-clones overlap audit | W136 | overlap mapped; both kept but clarified |
| (ADD) OneDrive/Dropbox WAL race init warning | W127 | substrate already shipped earlier; only the init warning was the remaining gap |

## Closures since W140 (W141-W232 — evidence-compiler thesis + producer wiring + redaction hardening)

The W141-W232 stretch executed the evidence-compiler pivot
(`dev/ARCHITECTURE-EVIDENCE-COMPILER-2026-05-13.md` + W170 strategic
reframe), wired real-pipeline producers into the shared evidence layer,
closed the W184 control-mapping wording-drift, and pinned 5 producer
gaps + 4 redaction leak paths via tests. Strike-throughs on originating
lines are preserved; this is the fast-lookup index. Wave W245 audit
context: the redaction leaks + producer gaps are **pinned** (tests
catch regressions) but not all are **sealed** — see "Pending after W232"
below for the seal-it-now queue.

| Item | Shipped in | Notes |
|---|---|---|
| ~~CLAUDE.md evidence-compiler thesis + crosswalk~~ | W170 / W187 | source-of-truth reframe codified in `CLAUDE.md` "Evidence compiler thesis" section + crosswalk to `dev/ARCHITECTURE-EVIDENCE-COMPILER-2026-05-13.md` |
| ~~README + landing-page reframe proposals~~ | W171 / W178 | reframe proposals authored; W200 committed Option B |
| ~~HANDOVER section additions~~ | W173 / W141 | handover notes added for the evidence-compiler pivot |
| ~~McpDecisionReceipt dataclass~~ | W183 | dataclass landed in `src/roam/evidence/` substrate |
| ~~Control mapping v1 schema~~ | W184 | control map schema shipped; 3 wording-drift entries later caught by W203 lint |
| ~~Evidence limitations section~~ | W185 | architecture memo now carries explicit limitations block |
| ~~pr-bundle actor block producer~~ | W189 | pr-bundle emits actor block into evidence layer |
| ~~Collector ref materialization (actor/authority/environment)~~ | W190 | mega collector materializes actor/authority/environment refs |
| ~~PR Replay render Actors/Authorities/Environment sections~~ | W191 | renderer surfaces the three new ref sections |
| ~~MCP decision receipt emitter~~ | W196 | emitter wired into MCP server boundary |
| ~~Vocabulary drift cleanup (author↔actor)~~ | W198 | normalised on `actor` across producers/consumers |
| ~~Mega collector extension (5 paths)~~ | W199 | rules / audit-trail / vuln-reach / test-impact / cga / mcp-receipts collector paths |
| ~~Option B reframe committed~~ | W200 | landing-page + README reframe landed on Option B |
| ~~Milestone integration note~~ | W202 | integration note threaded through architecture memo |
| ~~CI wording-guard lint~~ | W203 | lint caught 3 W184 drift entries ("certifies"/"makes compliant") — blocks new occurrences |
| ~~ChangeEvidence schema extensions~~ | W210 | 9 new optional fields + `assurance_floor` + `evidence_completeness` |
| ~~ActorRef trust_tier + AuthorityRef source + ApprovalRecord + non-goals docstrings~~ | W211 | dataclass surface extended + non-goals carved into docstrings |
| ~~MCP threat model doc~~ | W214 | threat model captured for the MCP boundary |
| ~~Team MCP authority-product positioning~~ | W215 | positioning written for the eventual authority-product surface |
| ~~Canonical demo narrative~~ | W216 | demo narrative authored against the evidence-compiler thesis |
| ~~Hostile-input Markdown tests~~ | W217 | 3 real bugs caught by hostile-input fuzz of the Markdown renderer |
| ~~Schema migration golden tests~~ | W218 | golden tests pin migration determinism + back-compat |
| ~~Producer/collector contract tests~~ | W219 | **5 producer gaps pinned** — context_files / approvals CLI / mode-always-emit / pr-risk findings[] / tool_id direction (still open per W224 — see pendings) |
| ~~Executable 8-question audit~~ | W220 | the canonical 8-question audit now runs as an executable check |
| ~~pr-replay producer wiring~~ | W223 | producer coverage moved 3/8 → 6/8 against the 8-question audit |
| ~~`roam evidence-diff` CLI command~~ | W225 | new command surfaces evidence-packet diffs |
| ~~Export profiles~~ | W226 | profile-driven exporter selection landed |
| ~~False-positive feedback loop module~~ | W228 | feedback module wired in for detector tuning |
| ~~Pipeline re-validation (synth ceiling 8/8 proven)~~ | W230 | synth ceiling proven; 8/8 against the executable audit when all producers feed |
| ~~Redaction snapshot tests~~ | W232 | **4 leak paths pinned** by snapshot tests — pr-bundle verdict/human_actor; critique findings keys; vuln-reach inliner; CGA absolute-path (still open per W236+ — see pendings) |

---

## Pre-dispatch dedup check

Before dispatching agents to fix findings from a new dogfood report,
run `python dev/dogfood_dedup_check.py --from-md <report.md>` (or
`--commands ...`). It greps `internal/dogfood/evals/<cmd>/*.md` for existing
`status: fixed-in-*` markers and surfaces likely-already-fixed findings.
Surfaced by W36.8's "50% already-fixed" finding in the 2026-05-13 batch.

Full demand index (~155 items, all tiers, source citations):
[`dev/ROADMAP.md`](ROADMAP.md). Pull items from there as they get
queued. When you ship one, delete the line; don't archive.

Current strategy/build-order memo:
[`dev/ROAM-STRATEGY-2026-05-15.md`](ROAM-STRATEGY-2026-05-15.md).
Use it for product framing, launch readiness, and "what should we build next?"
decisions. `ROADMAP.md` remains the full index; this backlog remains sprint
state/history.

Current architecture evidence-compiler memo:
[`dev/ARCHITECTURE-EVIDENCE-COMPILER-2026-05-13.md`](ARCHITECTURE-EVIDENCE-COMPILER-2026-05-13.md).
Use it when deciding whether new architecture work strengthens the shared
evidence spine or creates another parallel surface.

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
| ~~`roam surface --json` reports `mcp_tool_count: 0` when fastmcp absent~~ → W138 fixed the read source in `cmd_surface.py` and added `mcp_tool_count_by_preset` to the envelope. (Silent-fallback framing for the `fastmcp not installed` case may still want a follow-up audit.) | UX consistency | shipped W138 |
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
| W12.2 | R22 sweep — shipped pre-13.0 (helper at `src/roam/output/confidence.py`; W10.5 pilot + W12.2 sweep landed; 13.0 release contains the consumer migrations) |
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

**Wave 15 shipped (measurement + synergy + R28 phase 2 + final)** — W15.3/W15.4 land in 13.0 (CHANGELOG `### New commands` lists `roam causal-graph` + `roam tx-boundaries`; source at `src/roam/commands/cmd_causal_graph.py` + `cmd_tx_boundaries.py`). W15.1/W15.2/W15.5 audit trail in `dev/SPRINT-2026-05-12-FINAL.md`:

| Agent | Scope |
|---|---|
| W15.1 | Re-run the 212-eval dogfood corpus against current state; mark `status: fixed-in-13` where envelopes are now clean. **The "did we actually improve roam?" measurement.** |
| W15.2 | Wave-14 follow-ups — (a) auto_collect → summary (Pattern 3); (b) impact auto_log; (c) preflight writes to responses/ when bundle exists OR run active; (d) runs end --with-pr-bundle-emit; (e) promote agents_md private helpers to public API |
| W15.3 | R28 phase 2: causal graph — directional cause/effect edges; `roam causal-graph <symbol>` |
| W15.4 | R28 phase 2: transaction boundaries — `roam tx-boundaries` detects begin/commit/rollback regions. R28 complete after this. |
| W15.5 | Final integrative recheck — verify substrate composes; ensure dogfood re-run is actionable; GREEN/AMBER/RED + sprint-of-sprints summary |

---

## Pending after W140 (queue for next session)

Surfaced during the W103-W140 sweep but not closed in-session. Most need
user signoff or are larger-scope substrate work.

| Item | Where | Effort |
|---|---|---|
| **W107 Mode taxonomy tightening** — demote `findings` + `x-lang` from `safe_edit` to `read_only`. Pure DB queries, no edit semantics. Needs user signoff (W104 agent placed them in `safe_edit`; W106 flagged this as opinionated). | `src/roam/modes/` mode definitions | 15 min + signoff |
| **W137 Test naming unification** — 12+ new `test_findings_X.py` (current convention) vs 4 old `test_X_findings_emit.py`. Rename the old four to match the new convention so the corpus is grep-discoverable. | `tests/test_*_findings_emit.py` | 30 min |
| ~~**W140 auto-count drift literal fix**~~ — shipped; `tests/test_auto_count_script.py::test_script_exits_nonzero_on_drift` now derives the current count from `roam.surface_counts` instead of hard-coding the old `"233 commands and 149 MCP tools"` literal. Current truth: **236 commands**. | `tests/test_auto_count_script.py` | shipped |
| **W112 medium #1 — PHP indexer external-builtins materialization gap** | `src/roam/languages/php_lang.py` + indexer pipeline | substrate-level, big scope |
| **W112 medium #2 — `test_cli_contract.py` `_XFAIL_NO_TRACEBACK_JSON` allowlist audit** — small; confirm the empty allowlist is intentional and not stale-from-deletion. | `tests/test_cli_contract.py` | 15 min |
| **W112 medium #3 — `test_loop_e2e.py:626` skip** — W14.2 mode-enforcement substrate-vs-dispatch gap; the skip annotation explains the substrate is wired but dispatch doesn't enforce. Either close the dispatch gap or update the skip rationale. | `tests/test_loop_e2e.py` + `src/roam/modes/` dispatch | 1-2h |
| **Mode-enforcement PR-C flip** — long-queued user signoff. Empirically validated; the substrate is ready for the flip. | mode dispatch layer | signoff + flip + smoke |

---

## Pending after W232 (queue for W245+ session)

W141-W232 wired the evidence-compiler substrate, pinned 5 producer
gaps (W219) + 4 redaction leak paths (W232) via tests, and proved an
8/8 synth ceiling (W230). What remains is the actual sealing of those
pinned regressions plus producer coverage closure. Priority tags
follow `dev/BUILD-PRIORITIES-2026-05-13.md`.

### P0 — Critical correctness work

| Item | Where | Effort |
|---|---|---|
| **W236+ Seal 4 W232 redaction leak paths** — leaks are PINNED (snapshot tests catch regressions) but not SEALED. (1) pr-bundle `verdict` + `human_actor` fields; (2) critique findings keys; (3) vuln-reach inliner; (4) CGA absolute-path leak. Partially addressed by W240/W241 if they land — W245 audit pass should confirm which paths actually closed. | `cmd_pr_bundle*` / `cmd_critique` / `vuln_reach` / `attest/cga.py` + `tests/test_redaction_snapshots*` | 4-6h (4 surgical seals + snapshot updates) |
| ~~**W224 producer gap fixes**~~ — superseded by W240 / W242 / W261 / W266 / W267 / W268 (W876 triage). The pinned gaps are now sealed at the producer layer, not just the test layer. | pr-bundle producer / approvals CLI / mode dispatch / pr-risk producer / collector tool_id wiring | shipped via W240/W242/W261/W266/W267/W268 |

### P1 — Coverage closure

| Item | Where | Effort |
|---|---|---|
| ~~**W246 — Q3 context_refs producer for pr-replay**~~ — shipped W246; pipeline coverage moved 6/8 → 7/8. | pr-replay producer + collector wiring | shipped W246 |
| ~~**W247 (suggested) — Q8 approvals/accepted_risks harvest**~~ — half-shipped W247a (GitHub PR review parser/normalizer with offline fixtures; pure parser + closed `GITHUB_REVIEW_STATES` enum + review-body prohibition asserted by test). W247b (pr-replay integration) queued separately under "Pending after W287". | new harvester module + collector wiring | half-shipped W247a |

### P2 — Polish + housekeeping

| Item | Where | Effort |
|---|---|---|
| **W107 Mode taxonomy tightening** — demote `findings` + `x-lang` from `safe_edit` to `read_only` (carried over from "Pending after W140"; still awaits user signoff). | `src/roam/modes/` mode definitions | 15 min + signoff |
| ~~**Wave29.\*** (long-queued) — MCP wrapper coverage 73-command backfill~~ — half-shipped (38 wrappers added; 38 remaining). W299 added 9 exploration-cluster wrappers (75→67); W300 added 10 architecture-cluster wrappers (67→57); W301 added 10 health-cluster wrappers (57→47); W302 added 9 refactoring-cluster wrappers (47→38). Remaining 38 are organised by cluster in `dev/WAVE29-MCP-WRAPPER-PLAN-2026-05-15.md` (refreshed by W353). Sub-waves W303-W307 queued. | `src/roam/mcp_server.py` + `tests/test_mcp_wrapper_coverage.py` | half-shipped W299-W302; W303-W307 queued |
| ~~**Wave30.1** — Surface doc-hygiene CI gap~~ — shipped W250 (doc-hygiene CI extension + `.githooks/pre-commit`). | `.github/workflows/` doc-hygiene job + scripts | shipped W250 |
| ~~**Cross-platform CI env-ref tests**~~ — shipped W251 (env-ref tests wired into Windows/macOS/Linux matrix). | `.github/workflows/roam-ci.yml` + new test file | shipped W251 |
| ~~**Producer coverage matrix doc**~~ — shipped W252 (`dev/PRODUCER-COVERAGE-MATRIX-2026-05-14.md`). | new doc | shipped W252 |

### Out of scope until customer pulls

Locked behind the "build only when a customer asks" gate from
`dev/BUILD-PRIORITIES-2026-05-13.md`. Captured here so future waves
don't accidentally promote them.

- **Team MCP Gateway** — multi-tenant MCP boundary; build only when customer asks
- **Roam Cloud** — paid memory layer; build only when customer asks
- **Self-Hosted packaging** — enterprise self-host install path; build only when customer asks

---

## Shipped W246-W260 (post-W245 wave — pipeline coverage + hardening + observability)

The wave between W245's BACKLOG refresh and W260 closed the Q3
context_refs producer gap (lifting real-pipeline coverage 6/8 → 7/8
against the executable 8-question audit), sealed two cross-cutting
test pins (W232 redaction xfail-strict, critique-contract drift), and
landed the four P2 housekeeping items from the W232 pendings list
(doc-hygiene CI, cross-platform env-ref matrix, producer coverage
matrix, pipeline re-validation v3). Strike-throughs on originating
lines are preserved above; this is the fast-lookup index.

| Item | Shipped in | Notes |
|---|---|---|
| ~~Q3 context_refs producer for pr-replay~~ | W246 | producer + collector wiring; pipeline coverage 6/8 → 7/8 against the executable 8-question audit |
| ~~Q7 synth fixture enrichment~~ | W258 | synth audit threshold lifted 6 → 7; ceiling stays 8/8 |
| ~~Honest-banner thresholds in pr-replay~~ | W259 | STRONG / PARTIAL / INSUFFICIENT bands wired into the renderer |
| ~~pr-replay synth-bundle actor + scrub parity~~ | W260 | closed the sharp finding flagged in `dev/PRODUCER-COVERAGE-MATRIX-2026-05-14.md` |
| ~~Layer-2 collector secret scrub~~ | W249 | W232 xfail-strict markers removed; redaction snapshot tests now pass |
| ~~Critique-contract drift-guard test~~ | W256 | drift-guard pins the critique envelope shape |
| ~~`ws` command classified `safe_edit`~~ | W248 | mode taxonomy `UNCLASSIFIED_CEILING` 153 → 152 |
| ~~Doc-hygiene CI gate extension + `.githooks/pre-commit`~~ | W250 | shipped per "Wave30.1" pending; doc-hygiene now gates locally + in CI |
| ~~Cross-platform CI env-ref tests~~ | W251 | env-ref tests wired into the Windows/macOS/Linux matrix |
| ~~Producer coverage matrix~~ | W252 | `dev/PRODUCER-COVERAGE-MATRIX-2026-05-14.md` shipped; companion to W219's contract tests |
| ~~Pipeline re-validation v3 (7/8 complete real-world)~~ | W254 | real-pipeline coverage proven 7/8 (collector ceiling stays 8/8) |
| ~~W219 gap-pin verification~~ | W255 | 4/5 clean flips against the W219 contract tests; mode pin sealed by W257 |
| ~~pr-bundle.mode contract test refresh~~ | W257 | mode contract drift-guard refreshed; closed the 5th W219 gap-pin |

---

## Shipped W261-W266 (post-W260 wave — Q8 no-silent-gaps + env-axis fan-out + docs)

The wave between W260 and W266 closed the Q8 producer-availability gap
via the option-b redaction route (extending `REDACTION_REASONS` 8→9
with `producer_not_available` so partial states surface explicitly
instead of as silent zeros), fanned the environment-refs axis from 1
producer to N via the shared `env_refs.py` helper (closing the W252
environment-axis gap), and consolidated CHANGELOG + HANDOVER + BACKLOG
around the new framing "lead with no silent gaps, not the 7-complete
number — number unchanged, *shape* changed." Strike-throughs on
originating lines above are preserved; this is the fast-lookup index.

### Pipeline coverage

| Item | Shipped in | Notes |
|---|---|---|
| ~~pr-replay synth-bundle actor + scrub parity~~ | W260 | extracted `actor_helpers.py` (`resolve_actor_block()` + `resolve_actor_kind()`); cmd_pr_bundle wrappers became thin delegators; 37/37 test_pr_bundle.py preserved. Resolution priority: CLI flag > env var > git config > active run-ledger agent. W249 scrub wired at producer side. Closes W252 sharp finding (pr-replay bypassing cmd_pr_bundle.py). |
| ~~Q8 producer option-b (producer_not_available)~~ | W261 | extended `REDACTION_REASONS` 8→9 with `producer_not_available`. Limitation emitter at `cmd_pr_replay.py:1212-1242`. Renderer dedicated Q8 bullet at `:1797-1827`. Asymmetric `EXPECTED_PARTIAL_COUNT_TODAY = 1` added. **Smoke: complete=7 partial=1 missing=0 (no silent gaps).** Forward-compatible: silently suppresses when real producer arrives. |

### Cross-axis closures

| Item | Shipped in | Notes |
|---|---|---|
| ~~Shared `_build_environment_refs` helper~~ | W266 | new module `src/roam/evidence/env_refs.py` exporting `build_environment_refs()`. Delegate-not-move strategy: collector's existing function stays as-is (30+ call sites + v0/v1 content-hash contract); new module delegates CI detection via `_detect_ci_env_id` so `_CI_PROVIDER_ENV_VARS` stays single source. pr-bundle now stamps `environment_refs[]` on every emit path. Added to `_PR_BUNDLE_KNOWN_PAYLOAD`. **Closes W252 environment-axis gap (1 producer → N).** |

### Docs / observability

| Item | Shipped in | Notes |
|---|---|---|
| ~~CHANGELOG + HANDOVER consolidation W246-W257~~ | W262 | CHANGELOG +64, HANDOVER +124, new section 11. |
| ~~BACKLOG refresh covering W246-W259~~ | W264 | +45 lines, 4 strikethroughs. |
| ~~CHANGELOG + HANDOVER refresh W256-W264 + no-silent-gaps milestone~~ | W265 | CHANGELOG +68 to 5732, HANDOVER +144 to 964, new section 12 with 8 subsections. Framing: "lead with no silent gaps, not the 7-complete number — number unchanged, *shape* changed." |

### Hardening / verification

| Item | Shipped in | Notes |
|---|---|---|
| ~~Critique-contract reliability investigation~~ | W263 | 5/5 stable; sealed by W241's deliberate `'check' in _FINDING_SAFE_KEYS` entry. No regression — confirmed reliability. |

---

## Pending after W260 (queue for W261+ session)

Pinned from the post-W245 wave. Producer-coverage progress raises the
remaining 1/8 gap (Q8 approvals / accepted_risks) to the front of the
P1 queue.

### P1 — Coverage closure (carry-over)

| Item | Where | Effort |
|---|---|---|
| ~~**W261 — Q8 producer option-b redaction route**~~ — shipped W261; `REDACTION_REASONS` extended 8→9 with `producer_not_available`; dedicated Q8 limitation emitter + renderer bullet; asymmetric `EXPECTED_PARTIAL_COUNT_TODAY = 1`. **Smoke: complete=7 partial=1 missing=0 (no silent gaps).** | pr-replay producer + collector wiring | shipped W261 |
| ~~**W247 — Q8 approvals/accepted_risks harvest (promoted P1)**~~ — half-shipped W247a (parser/normalizer). W247b (pr-replay integration) queued separately under "Pending after W287"; lifts Q8 partial → complete (collector ceiling already 8/8 per W230). W261 sealed the no-silent-gaps shape; the W247b consumer wiring will make the surviving partial complete. | new harvester module + collector wiring | half-shipped W247a; W247b 2-3h |
| ~~**W265+ placeholder**~~ — shipped W264 + W265 (BACKLOG/CHANGELOG/HANDOVER refresh W246-W264). Real approvals producer work continues under promoted W247 above. | BACKLOG + CHANGELOG + HANDOVER | shipped W264-W265 |
| ~~**W267 — Policy adapters for constitution/permit/lease**~~ — shipped; `cmd_pr_replay` gathers constitution / permit / lease policy decisions and forwards them through `extra_policy_decisions`, closing the W252 policy axis. | pr-replay gatherer set + new adapters | shipped W267 |
| ~~**W268 — pr-bundle permits/leases real producer fields**~~ — shipped; `pr-bundle emit` now always emits `permits[]` / `leases[]`, reads disk-backed rows when present, and lets the collector mint permit / lease `AuthorityRef`s. | `cmd_pr_bundle*` + producer wiring | shipped W268 |
| ~~**W269 — BACKLOG refresh W260-W266**~~ — shipped; strike-through of completed W260-W266 entries + "Shipped W261-W266" subsection appended. | `dev/BACKLOG.md` | shipped W269 |
| ~~**W270 — CHANGELOG + HANDOVER refresh W266-W268**~~ — shipped; CHANGELOG `[Unreleased]` extended with W266 + W268 entries + three-closures milestone; HANDOVER section 13 added (7 subsections). | `CHANGELOG.md` + `dev/HANDOVER-2026-05-13.md` | shipped W270 |
| ~~**W273 — CHANGELOG + HANDOVER + BACKLOG consolidation W267 + W272**~~ — shipped; appended W267 + W272 entries plus the "W252 matrix closed" milestone callout, HANDOVER section 14, and the "Shipped W267-W272" BACKLOG subsection. | `CHANGELOG.md` + `dev/HANDOVER-2026-05-13.md` + `dev/BACKLOG.md` | shipped W273 |

---

## Shipped W267-W272 (post-W266 wave — W252 matrix closure cycle complete)

The wave between W266 and W272 completed the W252 producer-coverage
matrix closure cycle. W267 closed the policy axis (2 → 6
decisions from 4 sources) via three new `cmd_pr_replay` gatherers
projecting constitution / permit / lease decisions into
`extra_policy_decisions` on the canonical collector. W272 sealed
the synth-bundle parity gap so the W260 + W266 + W268 producer
fixes flow through the replay path, not only the direct pr-bundle
path — Strategy A (direct import with docstring annotation) proved
sufficient because the two `_load_*_from_disk` helpers were
already module-level in `cmd_pr_bundle.py`. End-state: real-world
`roam pr-replay HEAD~5..HEAD` on roam-code now carries 3 actor_refs /
3 authority_refs / 3 environment_refs / 6 policy_decisions / 492
context_refs / 11 artifacts; Q-coverage 7+1+0 (W261 no-silent-gaps
shape). Strike-throughs on originating lines above are preserved;
this is the fast-lookup index.

### Axis closures

| Item | Shipped in | Notes |
|---|---|---|
| ~~Policy adapters in pr-replay (constitution / permit / lease)~~ | W267 | 3 new gatherers in `cmd_pr_replay.py` (~lines 995 / 1056 / 1117) wiring option (b): collector's `collect_change_evidence` gained `extra_policy_decisions` kwarg. Stable concat order rules → audit-trail → extras; 31/31 golden hashes pass. Smoke on roam-code: 1 audit-trail + 3 constitution gates + 2 leases + 0 permits = 6 `policy_decisions` (up from 1 pre-W267). 5 new tests + 127/127 focused + 71/71 broader. **Closes W252 policy axis (2 producers → 6 decisions from 4 sources).** |
| ~~pr-replay synth-bundle full parity for permits / leases / env_refs~~ | W272 | Strategy A — direct import of `_load_permits_from_disk` / `_load_leases_from_disk` from `cmd_pr_bundle.py` with docstring annotation; no new module. W272 stamping block at `cmd_pr_replay.py:1378-1466` (right after W260 actor block); post-collector env_refs merge at `:1577-1601`. Dedup decision (c): stamp permits/leases on envelope (collector reads top-level) + merge W266-built env_refs tuple onto `packet.environment_refs` after collector returns (collector rebuilds env_refs from raw inputs — naive stamping would miss `workspace`); dedup by `(env_kind, env_id)`. Smoke on roam-code: 3 authority_refs (mode + 2 leases) up from 1; 3 environment_refs (branch_range + local_run + workspace) up from 1. 4 new tests + 146/146 focused + 72/72 broader. **Closes the W260 + W266 + W268 producer-parity gap on the replay path.** |

### W252 matrix closure inventory

The four-wave arc that completes the W252 closure cycle:

| Axis | Wave | Pre-wave | Post-wave |
|---|---|---|---|
| Environment | W266 | 1 producer (pr-replay only) | N producers via shared helper + pr-bundle wiring |
| Authority | W268 | 1 ref kind (mode) | 5 ref kinds (mode + permit + lease + policy_rule + approval) |
| Policy | W267 | 2 producers (rules + audit-trail-verify) | 6 decisions from 4 sources |
| Synth-bundle parity | W272 | producer fixes did not propagate to replay path | producer fixes flow through both direct and replay paths |

### Docs / observability

| Item | Shipped in | Notes |
|---|---|---|
| ~~CHANGELOG + HANDOVER refresh W266-W268 + three-closures milestone~~ | W270 | CHANGELOG `[Unreleased]` extended with W266 + W268 entries; HANDOVER section 13 added (7 subsections). Framing: "three closures in three waves — Pattern-2 always-emit + delegate-not-move discipline." |

---

## Pending after W272 (queue for W273+ session)

The W252 matrix closure cycle is now complete. The remaining P1
work pins to Q8 approvals (the last open producer-side gap on the
executable 8-question audit).

### P1 — Coverage closure (carry-over)

| Item | Where | Effort |
|---|---|---|
| ~~**W247 — Q8 approvals / accepted_risks harvest (promoted P1)**~~ — half-shipped W247a (parser/normalizer; first half of W247). W247b (pr-replay integration) queued separately under "Pending after W287"; lifts Q8 partial → complete (collector ceiling already 8/8 per W230). The W272 synth-bundle parity ensures the W247b wiring flows through both direct and replay paths automatically. | new harvester module + collector wiring | half-shipped W247a; W247b 2-3h |

---

## Shipped W279-W287 + W247a (evidence pipeline hardening batch)

The wave that followed the W252 matrix closure cycle moved one
rung sideways: instead of closing another producer axis, it
hardened the evidence pipeline itself. Nine waves landed —
schema integrity (W279 + W279b), packet size budget (W280),
trust-tier surface + corroboration (W281 + W285), provenance
vocab (W282), generated limitations (W284), canonical demo
bracket repair (W286), producer-site version stamping (W287),
and the first half of the real approvals producer (W247a).
End-state proof: `roam evidence doctor` on this repo flipped
**WARN → PASS** via real run-ledger corroboration (W285),
not classifier softening. Strike-throughs on originating
lines above are preserved; this is the fast-lookup index.

### Schema integrity

| Item | Shipped in | Notes |
|---|---|---|
| ~~Typed `PolicyDecision` dataclass~~ | W279 | New `src/roam/evidence/policy.py` mirrors W211's `ApprovalRecord` pattern. Promotes `policy_decisions` from tuple-of-mapping to frozen dataclass (`rule_id` / `decision` / `verdict` / `evaluated_at` / `extra`). 31/31 golden content_hashes preserved. |
| ~~PolicyDecision Mapping subclass + narrowed legacy-preserve catch~~ | W279b | User-mandated integrity follow-up. `PolicyDecision` subclasses `collections.abc.Mapping` so W226's `apply_profile()` `_redact_mapping_tuple()` works unchanged. The legacy-preserve `ValueError` catch at `change_evidence.py:320-358` was narrowed to ONLY preserve rows where `rule_id` OR `decision` is missing — drift detection now correctly fires on `{"rule_id":"r", "decision":"approved"}`. |

### Size budget

| Item | Shipped in | Notes |
|---|---|---|
| ~~Packet size budget enforcement~~ | W280 | `PACKET_SIZE_BUDGET_BYTES = 262144` (256 KiB) on canonical JSON. `_apply_size_budget()` called BEFORE `with_content_hash()` — content_hash IS the hash of the post-truncation packet. Frozen deterministic 5-step truncation order: `artifacts.content_inline` → `context_refs.content_inline` → `policy_decisions.extra` → `findings.evidence` → `actor_refs.extra`. Redactions never dropped; `"size_limit"` reason appended dedup-safe. `roam evidence doctor` surfaces `packet_size: {bytes, budget_bytes, budget_state}`; `oversized_after_truncation` → WARN, not FAIL. Real packets on roam-code at ~96 KB (~37% of budget). |

### Trust surface

| Item | Shipped in | Notes |
|---|---|---|
| ~~Trust-tier surface in `roam evidence doctor`~~ | W281 | New `_classify_trust_tiers(packet)` helper + extended `_validate_closed_enums` checks `actor_refs[i].trust_tier` against `ACTOR_TRUST_TIERS`. Pattern-2 always-emit 5-key `trust_tiers` dict + `trust_warnings[]` array. Verdict ladder: FAIL on enum violations / hash mismatch; WARN on PARTIAL/INSUFFICIENT banner OR STRONG with any self_reported/unknown actor; PASS requires STRONG + zero trust warnings. |
| ~~Pseudo-actor corroboration classifier~~ | W285 | `classify_actor_trust_tier()` gained `corroborated_tool_ids` + `corroborated_actor_ids` (frozensets, exact-equality). New `_collect_corroborated_ids()` at `collector.py:1150-1357` reads HMAC-verified run-ledger events (mirrors `cmd_runs._verify_one_run`; only `result["state"]=="ok"` contributes, whole-run granularity) + parseable MCP receipts. Side-effect: closes the W197 receipt-mirrored-ActorRef bypass. Live-repo flip: 3 unknown-tier pseudo-actors (`<unknown>`, `roam_init`, `roam_reindex`) promoted to `local_env` via real evidence → doctor WARN → PASS. Negative proof: `tempfile.mkdtemp()` with no `.roam/` keeps `roam_init` at `unknown` — no name-based shortcut. `_RUN_LEDGER_TOOL_FIELDS` constant captures non-uniform event-field naming for future-proofing. |
| ~~Canonical demo evidence fixture repaired~~ | W286 | Replaced `self_reported_agent` (claude-code 1.2.3) with `local_env` (`example-trusted-agent`) corroborated by HMAC-signed run-ledger. Cross-references updated: top-level agent_id, human_actor, approvals[0].approver, authority_refs[1].granted_by. Content hash recomputed via `.audit-tmp/w286-rehash.py` mirroring `ChangeEvidence.compute_content_hash` discipline. Test `test_doctor_passes_on_canonical_packet` PASS expectation preserved. Bracket holds: canonical PASS vs insufficient WARN, both on real evidence. |

### Generated outputs

| Item | Shipped in | Notes |
|---|---|---|
| ~~Limitations generated from packet structure~~ | W284 | `_derive_limitations(evidence)` at `cmd_pr_replay.py:2295` projects three packet sources in frozen deterministic order: Q-gaps (Q1→Q8) → redactions (tuple order) → trust-tier warnings (actor_refs order) → non-cert footer always appended. Renderer `_render_evidence_limitations()` at `:2136` rewritten end-to-end. New `_Q_GAP_LABELS` + `_REDACTION_EXPLANATIONS` lookup tables. Sentinel `_No evidence limitations detected._` when no source contributes. Replaces prior hand-written boilerplate; limitations no longer drift from packet. |

### Producer wiring

| Item | Shipped in | Notes |
|---|---|---|
| ~~Provenance vocabulary (vocab + helper; wiring deferred)~~ | W282 | New `PROVENANCE_SOURCES` frozenset (10 values: ci_env_var / git_config / run_ledger / cli_flag / env_var / producer_envelope / audit_trail / mcp_receipt / inferred / unknown) + `provenance_label()` pure helper with detail-compact form. Cross-vocab leakage validation. CLAUDE.md vocab table 11 → 12 rows. Producer-side wiring **deliberately deferred** to W290+ per user direction so the vocabulary lands clean before call-site churn. |
| ~~`roam_version` stamped at producer site~~ | W287 | Sharp correction of the W280 drive-by — the prior `"1.0.0"` was `schema_version` (different field). `roam_version` defaults to `None` and is omitted via W210 omit-when-default. New `_resolve_roam_version()` at `change_evidence.py:783` (deferred import, fallback `"unknown"`). Wired at PRODUCER site in `collect_change_evidence()` at `collector.py:2667`, NOT dataclass field default — preserves omit-when-None for default `ChangeEvidence` (critical for backward compat). Real version: `"13.0"` (matches pyproject + `roam.__version__`). |

### Real approvals (first half)

| Item | Shipped in | Notes |
|---|---|---|
| ~~GitHub PR review parser/normalizer~~ | W247a | New `src/roam/evidence/github_reviews.py` (~360 lines). Three public functions: `parse_github_reviews()` (pure), `load_reviews_from_fixture()` (pure), `harvest_reviews_from_gh_cli()` (deliberate opt-in subprocess). New `GITHUB_REVIEW_STATES` closed enum (5 values). APPROVED reviews land in `approvals[]` only when `commit_id == head_commit_sha`; CHANGES_REQUESTED → `PolicyDecision(decision="deny")`; COMMENTED / DISMISSED / PENDING filtered with warnings. Review bodies NEVER stored (asserted by test). Fixture-first design keeps tests offline. First half of W247; W247b (pr-replay integration) queued separately. |

---

## Pending after W287 (queue for W289+ session)

The evidence pipeline hardening batch sealed schema integrity,
size budget, trust-tier surface, and the canonical demo bracket.
Remaining work is the consumer side of W247 + a few follow-ons.

### P1 — Coverage closure (carry-over)

| Item | Where | Effort |
|---|---|---|
| ~~**W247b — pr-replay integration of the W247a parser (promoted P1)**~~ — shipped W247b; wires `parse_github_reviews()` / `load_reviews_from_fixture()` into `cmd_pr_replay`'s collector path so APPROVED rows land in `approvals[]` and CHANGES_REQUESTED rows land in `policy_decisions[]`. Lifted Q8 partial → complete (collector ceiling already 8/8 per W230). | `cmd_pr_replay.py` + collector wiring | shipped W247b |
| ~~**W198 cmd_permit --persist work (promoted P1)**~~ — shipped W198; `roam permit issue --persist` now writes `.roam/permits/<permit_id>.json` so the W292 / W294 harvester finds real permit rows. Closes the W186 audit's verdict-facade gap; pre-W198 the directory was always empty (`cmd_permit` was strictly a verdict facade), post-W198 it carries one document per issued permit. Verdict-facade path preserved as a dry-run when `--persist` is absent (byte-stable with the pre-W198 contract). | `cmd_permit.py` + `src/roam/permits/store.py` + `.roam/permits/` write path + tests | shipped W198 (95 permit-persist + 105 broader tests pass) |

### P2 — Investigation + follow-on

| Item | Where | Effort |
|---|---|---|
| ~~**W285-followup — 3 pre-existing test failures investigation**~~ — shipped W285-followup; hostile-markdown was a real W285 regression (actor_id/tier bypassed `_escape_cell_code`); 3 failures triaged and fixed. | tests | shipped W285-followup |
| ~~**W287 follow-on (W288) — INLINE_CONTENT_SOFT_LIMIT decision**~~ — shipped W288 + W288-followup. W288 kept the constant as advisory and clarified the two-tier discipline (8 KiB advisory vs 256 KiB enforced); W288-followup added a `warnings.warn` at `EvidenceArtifact.__post_init__` whenever `content_inline > INLINE_CONTENT_SOFT_LIMIT_BYTES`, complementing W280's enforced packet-level budget. | `src/roam/evidence/artifact.py` | shipped W288 + W288-followup |

### Future

| Item | Where | Effort |
|---|---|---|
| ~~**W290+ — provenance wiring** (Future)~~ — shipped across the W290 / W292 / W293 / W294 trilogy-closure arc. W290 stamped `actor_refs`, W292 stamped `authority_refs` with a deterministic precedence ladder, W293 stamped `policy_decisions` + `approvals` at 9 ingestion sites + Pattern-2 fallback, W294 stabilized `AuthorityRef.source` population and wired writer-side run-ledger event fields. 31/31 golden content_hashes byte-identical across the arc. | envelope-emitting `cmd_*.py` producers + `collector.py` + `auto_log()` helper | shipped W290 / W292 / W293 / W294 |

---

## Shipped W292-W294 + W288-followup (provenance trilogy + authority stabilization + inline warning)

The wave that followed the W279-W287 evidence-pipeline-hardening
batch closed the provenance trilogy across every evidence
dimension and stabilized the authority axis. Three discipline
threads landed: (a) `extra["provenance"]` stamping at ingestion
sites for `authority_refs` (W292) + `policy_decisions` /
`approvals` (W293), mirroring the W290 actor pattern across the
remaining two evidence axes; (b) authority-axis stabilization
via distinct `AuthorityRef.source` population + writer-side
run-ledger event fields so the W292 harvester finds real
corroboration (W294); (c) per-artifact advisory warning
complementing W280's enforced packet-level budget
(W288-followup). 31/31 golden content_hashes byte-identical
across the entire arc. Strike-throughs on originating lines
above are preserved; this is the fast-lookup index.

### Provenance trilogy

| Item | Shipped in | Notes |
|---|---|---|
| ~~authority_refs provenance stamping with deterministic precedence ladder~~ | W292 | Mirrors W290 actor_refs pattern. New `_resolve_authority_provenance()` at `collector.py:1063` + `_collect_corroborated_authorities_from_runs` at `:967` + rewritten `_build_authority_refs` at `:1153`. Frozen ladder: `run_ledger` > `audit_trail` > `mcp_receipt` > `producer_envelope(permit)` > `producer_envelope(mode)` > `producer_envelope(rule)` > `producer_envelope(approval)` > `producer_envelope(lease)` > generic `producer_envelope` > `inferred` > `unknown`. `AuthorityRef.source` (W211 category) and `extra["provenance"]` (W282 channel) preserved as INDEPENDENTLY load-bearing fields. 14 new tests + 31/31 goldens. |
| ~~policy_decisions + approvals provenance stamping at 9 ingestion sites~~ | W293 | Producer-side stamping at gatherers / parsers / CLI commands — never at dataclass defaults. Sites: `cmd_pr_replay.py:1061-1086` constitution → `producer_envelope(constitution)`; `:1120-1153` permit → `producer_envelope(permit)`; `:1198-1224` lease → `producer_envelope(lease)`; `:1267-1280` approval flattener → `producer_envelope(github_review)`; `github_reviews.py:370-380` PolicyDecision builder → `producer_envelope(github_review)`; `collector.py:2459-2467,2497,2502` audit-trail → `audit_trail`; `collector.py:1973-1976,2018` rules → `producer_envelope(rule)`; `cmd_pr_bundle.py:2399-2410` add-approval CLI → `cli_flag`; `collector.py:3361-3389` Pattern-2 fallback → `unknown` (only when no upstream signal; existing values preserved idempotently). PolicyDecision wire-format: `to_dict()` flattens `extra` to top-level keys, `from_dict()` re-nests. W247a body-prohibition guardrail extended. Smoke on roam-code: 6 policy_decisions all carry explicit provenance (zero `unknown` fired). 15 new tests + 1 regression assertion + 486 broader + 31/31 goldens byte-identical. |

### Authority stabilization

| Item | Shipped in | Notes |
|---|---|---|
| ~~AuthorityRef.source population + auto_log writer-side run-ledger fields~~ | W294 | Closes both W292 follow-ups in one wave. (a) `AuthorityRef.source` populated DISTINCTLY per category at `_build_authority_refs` via new `source=` kwarg on `_add` helper: `mode` → `"mode"`; `permit` → `"permit"` + `extra["permit_id"]` when real; `policy_rule` → `"rule_config"`; `approval` → `"human_approval"`; `lease` intentionally retains `"inferred_fallback"` (AUTHORITY_SOURCES has no `lease` literal — deliberate vocab decision, documented inline; see HANDOVER §16.5). (b) `auto_log()` in `src/roam/runs/helpers.py` gained optional `extra_event_fields` kwarg with closed whitelist `_AUTHORITY_EVENT_FIELDS = {"mode", "active_mode", "mode_to", "mode_from", "permit_id", "lease_id", "approval_id", "rule_id"}`. Writer-side wiring: `cmd_mode.py:362-386` emits `mode_to` + `mode_from` on non-noop switch (pre-switch capture before `set_active_mode`); `cmd_lease.py:329-340` (claim) + `:430-441` (release) emit `lease_id`; `cmd_pr_bundle.py:2392-2403` (add-approval) emits `approval_id` when active run exists. 15 new tests + 1 updated assertion (W292's source flipped from `"inferred_fallback"` to `"mode"`) + 31/31 goldens byte-identical + 305 broader pass. |

### Inline warning

| Item | Shipped in | Notes |
|---|---|---|
| ~~EvidenceArtifact advisory warning when content_inline > 8 KiB~~ | W288-followup | Companion to W280's enforced packet-level budget. Two-tier discipline: 8 KiB per-artifact advisory + 256 KiB packet-level enforced. `EvidenceArtifact.__post_init__` fires `warnings.warn` whenever `len(content_inline) > INLINE_CONTENT_SOFT_LIMIT_BYTES` (8 KiB). The limit stays purely advisory — no reject, no truncate, no redaction stamp. Pressure signal upstream of W280's deterministic 5-step truncation order (`artifacts.content_inline` → `context_refs.content_inline` → `policy_decisions.extra` → `findings.evidence` → `actor_refs.extra`). Focused tests green. |

---

## Shipped W198 (permit-persist closure)

The wave that closed the W186 audit's verdict-facade gap on
`roam permit`. Pre-W198 the command was strictly a verdict facade —
no disk write — and the W292 / W294 harvester therefore never found
real permit rows on this workspace (the smoke result in
SESSION-SNAPSHOT-2026-05-14 still showed `mode + 2 leases`, with the
permit axis riding the `inferred_fallback` marker). W198 ships the
writer side of the permit substrate.

### Permit-persist writer

| Item | Shipped in | Notes |
|---|---|---|
| ~~`roam permit issue --persist` writer + on-disk store~~ | W198 | New `roam permit issue` subcommand on top of the existing verdict-facade command. With `--persist`, writes one JSON document per issued permit to `.roam/permits/<permit_id>.json` via `src/roam/permits/store.py`. Without `--persist`, the command remains a dry-run — byte-stable with the pre-W198 verdict-facade contract, so existing hook / pre-commit gates that consume only the verdict are unaffected. Closes the W292 / W294 facade-vs-real corroboration loop: the W292 harvester now reads real permit rows from disk, and `AuthorityRef(authority_kind="permit", …)` entries carry a real `extra["permit_id"]` with `provenance="producer_envelope(permit)"` instead of the historical inferred-fallback marker. The W268 helper `_load_permits_from_disk` (which already returned `[]` from a missing directory per Pattern-2 always-emit) now sees real rows when permits have been issued. 95 permit-persist-focused tests + 105 broader pass. |

---

## Shipped W296-W368 (Pattern-1 family + wrapper backfill + research planning)

The wave that followed the W198 permit-persist closure executed
three parallel threads: (a) the Pattern-1 family A/B/C/D
canonization in `CLAUDE.md` plus the two production fixes it
surfaced (W324 silent-success + W336 Pattern-2 rounding); (b) the
Wave29 MCP wrapper backfill that moved missing-wrapper count
75 → 67 → 57 → 47 → 38 across four consecutive sub-waves; (c)
five sonnet+web research planning artifacts that seed the next
sprint. All entries here are `(ADD)` style — emerged this session,
not pre-queued. See HANDOVER §18 for the full session arc.

### Pattern-1 family canonization (A/B/C/D)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) MCP cold-start guard for index-gated tools~~ | W296 | Sealed Pattern-1 Variant A. Returns `state: "index_missing"` + `next_command: "roam init"` before any heavy import or DB connection. |
| ~~(ADD) Pattern-1 family audit (sonnet+web research)~~ | W315 | `dev/MCP-PATTERN-1-FAMILY-AUDIT-2026-05-15.md`. Named Variants A/B/C; surfaced open Variant B gaps. Drove W325, W328, W334. |
| ~~(ADD) Try-parse passthrough chokepoint sealing Variant B~~ | W325 | `_run_roam_inprocess` / `_run_roam_subprocess` now passes structured failure envelopes through instead of collapsing on non-zero exit. Sealed Variant B for `doctor` / `stale-refs` / `test_scaffold`. |
| ~~(ADD) pytest-fixtures structured envelope on no-result~~ | W327 | CLI-side Variant C fix. |
| ~~(ADD) Pattern-1 family canonical spec in CLAUDE.md~~ | W328 | A/B/C codified with 5 invariants and external citations (FastMCP / Anthropic / MCP-spec). |
| ~~(ADD) Pattern-1 Variant D added (silent success on degraded resolution)~~ | W334 | Codified after W324 surfaced the gap. Distinct from Variant B (fix lives at the resolver boundary, not the wrapper chokepoint). |

### MCP wrapper backfill (Wave29 sub-waves)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Wave29 planning doc + W298-polish (math/triage skip-allowlist + decorator audit)~~ | W298 | Plan covers 8 sub-waves (W299-W306) + deferred W307. |
| ~~(ADD) Exploration-cluster wrappers (9 added)~~ | W299 | Missing count 75 → 67. |
| ~~(ADD) Architecture-cluster wrappers (10 added)~~ | W300 | Missing count 67 → 57. |
| ~~(ADD) Health-cluster wrappers (10 added)~~ | W301 | Missing count 57 → 47. |
| ~~(ADD) Refactoring-cluster wrappers (9 added)~~ | W302 | Missing count 47 → 38. |
| ~~(ADD) `input_path` parameter alias normalization + 1355-case AST lint~~ | W332 | `file` / `path` / `paths` / `target_path` canonicalize to `input_path` in `_PARAM_ALIASES`. |
| ~~(ADD) WAVE29 plan refresh against post-W302 reality~~ | W353 | `dev/WAVE29-MCP-WRAPPER-PLAN-2026-05-15.md` updated; 38 remaining commands organised by cluster. |

### Pattern 3 + Pattern 6 vocab discipline

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Pattern 3 + Pattern 6 audit (sonnet+web research)~~ | W329 | `dev/PATTERN-3-AND-6-AUDIT-2026-05-15.md`. Enumerated 9+ MCP parameter-name mismatches + 8 high-volume commands. Drove W330, W331, W331b, W332. |
| ~~(ADD) CLAUDE.md Pattern 3 + 6 expanded (3a/3b + 6a/6b/6c)~~ | W330 | Variants codified with external citations. |
| ~~(ADD) Definition fields wired into 6 high-signal commands~~ | W331 | `<metric>_definition` fields emit `caller_metric_definition: "raw_edge_rows"` (etc.) so cross-command vocabulary drift no longer silently mismatches. |
| ~~(ADD) Definition fields wired into 3 remaining gaps + article-12 wording lint~~ | W331b | Closes the W331 follow-up. |
| ~~(ADD) caller_metric drift-guard extended + cmd_invariants stamp~~ | W335 | W332 lint extended to cmd_invariants. |
| ~~(ADD) `CALLER_METRIC_RAW` extracted to canonical constant (7 sites)~~ | W342 | `cmd_impact` / `cmd_preflight` / `cmd_understand` / `cmd_describe` / `cmd_minimap` / `cmd_for_refactor` / `cmd_invariants` re-use the constant; drift-guard rejects hand-rolled literals. |

### Silent-failure surfacings + redaction polish

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `roam_annotate_symbol` silent-success fix~~ | W324 | Surfaced Pattern-1 Variant D. Now emits `state: "no_match"` instead of `verdict: "completed"` when resolver degrades to no-match. |
| ~~(ADD) `cmd_impact` weighted_impact rounding + silent-fallback fix~~ | W336 | Two Pattern-2 violations sealed. Rounding-before-threshold-comparison fixed; `state: "no_callers_found"` replaces silent `verdict: "low impact"` on zero-edge traversal. |
| ~~(ADD) `_redact_secrets` extracted to `src/roam/security/redact.py`~~ | W364 | Single source of truth for redactor regex set + allowlist; evidence collector / MCP receipts / pr-bundle emit paths now share it. |

### Research planning (sonnet+web sweeps, queued sub-waves)

| Memo | Wave | Drives |
|---|---|---|
| ~~(ADD) `dev/MCP-PATTERN-1-FAMILY-AUDIT-2026-05-15.md`~~ | W315 | W325, W328, W334 |
| ~~(ADD) `dev/PATTERN-3-AND-6-AUDIT-2026-05-15.md`~~ | W329 | W330, W331, W331b, W332 |
| ~~(ADD) `dev/MCP-STATE-MUTATING-PATTERNS-2026-05-15.md`~~ | W340 | W363-W366 (queued) |
| ~~(ADD) `dev/STANDARDS-CURRENCY-AUDIT-2026-05-15.md`~~ | W341 | W358-W360 (queued); SLSA v1.2 Source Track + OSCAL v1.2 Control Mapping refresh |
| ~~(ADD) `dev/DETECTOR-COMPETITIVE-AUDIT-2026-05-15.md`~~ | W368 | W370-W374 (queued); AHEAD on 5 categories, PARITY on 5, BEHIND on 6 |

### Doc cross-reference sweep

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) W198 permit-persist doc cross-reference refresh~~ | W345 | Three HANDOVER sections + BACKLOG `Permits/Leases` row + W292/W294 historical notes refreshed in place. Pre-W198 references preserved as historical snapshots. |

### Pending after W368 (queue for W375+ session)

| Item | Where | Effort |
|---|---|---|
| ~~**Wave29 sub-waves W303-W307** — 38 remaining wrappers; organised by cluster in the refreshed plan doc.~~ | `src/roam/mcp_server.py` | W303 landed (38→33); W304 in flight; W305/W306 queued |
| ~~**W363-W366** — MCP state-mutating tool hardening from W340 audit.~~ | `src/roam/mcp_server.py` + new tests | W364 shared redactor landed (prereq); W363/W365/W366 still queued |
| ~~**W370-W374** — detector gap closures from W368 audit (reachable-vuln parity, smells empty-catch + primitive-obsession, taint OWASP Top-10 expansion).~~ | `src/roam/security/` + `src/roam/catalog/` | W370 (empty-catch, 469 findings) + W370b (duplicate-conditionals, 149 findings) + W371 (vibe-check modular-mirage + boilerplate-inflation, 163+499 findings, informational/score-preserving) landed; W370c + W372-W374 still queued |
| **W358-W360** — standards-currency refresh from W341 audit (SLSA v1.2 Source Track + OSCAL v1.2 Control Mapping wording tweaks). | `templates/audit-report/` + control-map YAML | 2-3h |
| **W367** — TEAM-MCP-AUTHORITY-PRODUCT facade refresh (queued). | `dev/TEAM-MCP-AUTHORITY-PRODUCT-2026-05-14.md` | 1h |

---

## Shipped W303-W393 (pitch refresh + Pattern-1 round 3 + detector closures + permit red-team)

The wave that followed the W375 consolidation executed four
threads in parallel: (a) top-of-funnel pitch refresh on every
customer-facing surface (W385 → W390 → W393); (b) a third
CLI-side Pattern-1 family fix at `cmd_owner` (W362); (c)
detector stub fills against the W368 BEHIND list (W370 + W370b);
(d) red-team hardening of the W198 permit substrate (W349 +
6 queued drive-by gaps). Wave29 wrapper backfill kept moving
(W303 closed the test-surface cluster, 38 → 33). One new
sonnet+web research artifact (W385 ecosystem positioning).
Strike-throughs on originating lines are preserved; this is the
fast-lookup index.

### Pitch refresh

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Hero copy refresh on `README.md` + landing `index.html` + `docs/index.html`~~ | W390 | "Pre-change gates + post-change evidence" — dual framing that names both halves of the agentic-assurance loop. Lede text only; no structural HTML change. |
| ~~(ADD) Pitch refresh swept across 11 secondary surfaces~~ | W393 | `pricing.html` / `press.html` / `trust.html` / `governance.html` + four `services-reports/` deliverables + three `audit-report/` templates. Same framing as W390; same no-structural-HTML-change discipline. |

### Pattern-1 family Round 3

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `cmd_owner` Pattern-1 exit-0+envelope fix~~ | W362 | Third CLI-side "exit 0 + structured envelope" Pattern-1 fix after W327 (`pytest-fixtures`) and W324 (`roam_annotate_symbol`). Pre-W362 `roam owner <symbol>` on a no-owner symbol exited 0 with empty stdout → MCP wrapper crashed in `json.loads("")`. Post-W362 always emits `state: "no_owner_data"` + `next_command: "roam blame"`. Pattern-1 Variant C contract preserved. |

### Detector strengthening (W368 BEHIND list)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) smells `empty-catch` detector~~ | W370 | First W368 BEHIND-list smells stub promoted to real detector. 469 findings on roam-code itself; emits through canonical `_emit_smells_findings` path so registry tier-mapping + version-stamp discipline inherit automatically. |
| ~~(ADD) smells `duplicate-conditionals` detector~~ | W370b | Second W368 BEHIND-list smells stub promoted. 149 findings on roam-code with long-tail distribution. Same emit path as W370. |

### Permit red-team

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Permit-persist red-team test surface~~ | W349 | 19 permit-persist tests exercising W198 writer edge cases (corrupt JSON, partial write, racing writer, schema drift, expired-permit reads, missing parent directory). Six drive-by gaps surfaced and queued as W377-W382 (see queue below). |

### MCP wrapper backfill continuation

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Test-surface cluster wrappers (5 added)~~ | W303 | Missing count 38 → 33. Fifth consecutive Wave29 sub-wave. W304 sub-wave in flight against the next cluster. |

### Structural infrastructure

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `_redact_secrets` extracted to `src/roam/security/redact.py`~~ | W364 | Single source of truth for redactor regex set + allowlist; evidence collector / MCP receipts / pr-bundle emit paths now share it. Load-bearing for W363 state-mutating wrapper hardening (queued). No behaviour change at consumer sites; pinned by W232 redaction snapshot tests + focused `tests/test_security_redact.py` contract test. (Originally listed in the W375 batch — duplicate suppressed; the W398 batch confirms it as the prerequisite that now unblocks W363/W365/W366.) |

### Research planning

| Memo | Wave | Drives |
|---|---|---|
| ~~(ADD) `dev/ECOSYSTEM-POSITIONING-2026-05-15.md`~~ | W385 | 7 adjacent tools surveyed; 5 COMPLEMENTARY / 2 COMPETITIVE / 0 SUBSTITUTE on agentic-assurance. Confirms "local evidence compiler" thesis (no surveyed tool emits portable evidence packets). Feeds W390 + W393 pitch refresh and W397 auto-count Codex-headline template update. |

### Pending after W393 (queue for W398+ session) — closed by W418

Most W398-queue entries have shipped (see "Shipped W303-W418"
below). Remaining items roll forward into the W418 queue.

| Item | Where | Effort |
|---|---|---|
| ~~**W304 / W305 / W306** — remaining Wave29 sub-waves against the 33 remaining MCP wrappers.~~ | `src/roam/mcp_server.py` | W304 + W305 landed (33→23→16); W306 queued |
| **W363 / W365 / W366** — MCP state-mutating sub-waves from W340 audit (W364 shared redactor prereq has shipped). | `src/roam/mcp_server.py` + new tests | 4-6h |
| ~~**W370c** — remaining smells stubs from W368 BEHIND list (after W370 + W370b).~~ | `src/roam/catalog/smells.py` | W371 vibe-check additions landed alongside (informational); W370c remaining stubs still queued |
| **W377-W382** — six permit-persist drive-by gaps surfaced by W349 red-teaming. | `src/roam/permits/store.py` + tests | 2-4h (W377-batch in flight in W418) |
| ~~**W396 / W397** — finish pitch-refresh mirror into `src/roam/mcp-server-card.json` (W396 in flight) + auto-count Codex-headline template update (W397 queued).~~ | `src/roam/mcp-server-card.json` + `dev/build_readme_counts.py` markers | W396 landed; W397 superseded by W399/W411/W417 count-drift sync waves |
| **W358-W360** — standards-currency refresh (carry-over from W341 audit). | `templates/audit-report/` + control-map YAML | 2-3h |
| ~~**W367** — TEAM-MCP-AUTHORITY-PRODUCT facade refresh (queued).~~ | `dev/TEAM-MCP-AUTHORITY-PRODUCT-2026-05-14.md` | W367 landed |

---

## Shipped W303-W418 (MCP wrapper backfill near-complete + detector strengthening Round 2 + perf research + llm-smells design)

The wave that followed the W398 consolidation ran ~15 mini-waves
in parallel across five threads: (a) Wave29 wrapper backfill from
38 → 16 missing via three consecutive sub-waves (W303 / W304 /
W305 — 26 wrappers added in total); (b) detector strengthening
Round 2 against the W368 BEHIND list and the vibe-check AI-rot
family — three new finding sources for ~1,280 new rows on
roam-code itself (W370 + W370b + W371); (c) pitch-refresh trilogy
completed across 15 surfaces (W390 + W393 + W396 mirror); (d)
structural cleanups closed five doc / version / config drift gaps
(W319 + W345 + W346 + W348 + W352 + W364 + W367 + W403 + W412);
(e) two new sonnet+web research artifacts (W395 perf benchmarking
+ W402-research llm-smells catalog). Strike-throughs on
originating lines above are preserved; this is the fast-lookup
index.

### MCP wrapper backfill (Wave29 cont'd)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Test-surface cluster wrappers (5 added)~~ | W303 | Missing count 38 → 33. Fifth consecutive Wave29 sub-wave. |
| ~~(ADD) Agent-OS daily-flow cluster wrappers (10 added)~~ | W304 | Missing count 33 → 23. Sixth Wave29 sub-wave. Targets runs / mode / lease / permit / memory / brief / next / agent-score surface — the daily-flow heart of the substrate. |
| ~~(ADD) Reports / audit cluster wrappers (11 added)~~ | W305 | Missing count 23 → 16. Seventh Wave29 sub-wave. Targets reports / audit-trail / pr-bundle / evidence-doctor / replay surface. Some Python wrappers route multiple canonical commands (consistent with the advisory audit's counting model). |

Cumulative arc W299 → W305: **75 → 67 → 57 → 47 → 38 → 33 → 23 → 16**.

### Detector strengthening Round 2 (~1,280 new findings)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) smells `empty-catch` detector~~ | W370 | First W368 BEHIND-list smells stub promoted; 469 findings on roam-code itself; emits through canonical `_emit_smells_findings` path. |
| ~~(ADD) smells `duplicate-conditionals` detector~~ | W370b | Second W368 BEHIND-list smells stub; 149 findings; long-tail distribution. |
| ~~(ADD) vibe-check `modular-mirage` + `boilerplate-inflation`~~ | W371 | Two informational AI-rot patterns; 163 + 499 findings; **score-preserving** (heuristic tier; do not move health score). Drive-by note pointed at arXiv:2512.18020 as out-of-scope-for-vibe-check; that note drove the W402-research llm-smells catalog. |

Total new findings: **469 + 149 + 163 + 499 = 1,280** on
roam-code itself, all through the canonical findings-registry
path.

### Pitch refresh trilogy completion

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `src/roam/mcp-server-card.json` pitch-refresh mirror + hash-pin update~~ | W396 | Closes the W390 (hero) → W393 (11 secondary) → W396 (discovery card) trilogy. 15 surfaces now aligned on the "pre-change gates + post-change evidence" framing. Hash-pin in `tests/test_mcp_server_card_hash.py` updated in step. |

### Pattern-1 family Round 3 + structural cleanups

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `cmd_owner` Pattern-1 exit-0+envelope fix~~ | W362 | Third CLI-side "exit 0 + structured envelope" Pattern-1 fix after W327 + W324 (carry-strikethrough from §"Pattern-1 family Round 3" above; surfaced again in W418 batch because the Round 3 arc culminates here). |
| ~~(ADD) `test_json_contracts.py` module-scope fixture (~28x speedup)~~ | W346 | Slowest test module in the broader sweep promoted from per-test indexing to module-scope. Runtime ~6 min → ~13s on this file. |
| ~~(ADD) `_redact_secrets` extracted to `src/roam/security/redact.py`~~ | W364 | Single source of truth for redactor; load-bearing for W363/W365/W366 (carry-mention; first surfaced in W375/W398 batches). |
| ~~(ADD) Plugin-count convention drift closed~~ | W319 | Single canonical citation across 3 sites. Closes a Pattern-3-style cross-site drift. |
| ~~(ADD) W198 doc cross-reference sweep~~ | W345 | Three HANDOVER sections + BACKLOG Permits/Leases row + W292/W294 historical notes refreshed (carry-mention; first surfaced in W375 batch). |
| ~~(ADD) W288 INLINE_CONTENT_SOFT_LIMIT advisory wording polish~~ | W348 | Wording tightened so consumers don't conflate the 8 KiB advisory with the 256 KiB enforced packet budget. |
| ~~(ADD) Python 3.10+ minimum documented across source tree~~ | W352 | Sweep aligned source-file comments + contributor docs with the pyproject requirement; no functional change. Companion to W412. |
| ~~(ADD) TEAM-MCP-AUTHORITY-PRODUCT facade refresh~~ | W367 | Memo updated against post-W198 reality (real permits exist; W292/W294 corroboration harvester reads them). Documentation refresh only. |
| ~~(ADD) `asyncio` configuration cleanup in pyproject pytest config~~ | W403 | Stale `asyncio_mode` entry removed; per-test warning gone. |
| ~~(ADD) Stale 3.9-compat rationale comments removed~~ | W412 | Companion to W352; removed last `from __future__ import annotations` rationale notes that referenced 3.9 compat. |

### Permit red-team (carry from W398 batch)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Permit-persist red-team test surface~~ | W349 | 19 permit-persist tests + 6 drive-by gaps queued as W377-W382 (W377-batch in flight in W418). |

### Research planning

| Memo | Wave | Drives |
|---|---|---|
| ~~(ADD) `dev/ECOSYSTEM-POSITIONING-2026-05-15.md`~~ | W385 | 7 adjacent tools surveyed (carry-mention; first surfaced in W398 batch). |
| ~~(ADD) `dev/PERFORMANCE-BENCHMARKING-2026-05-15.md`~~ | W395 | 7 indexing tools surveyed. Roam positioned MEDIUM: 10-40x slower than ctags per-file (different category) but **5-20x faster than CodeQL** with comparable depth on typical 50K-LOC repo. Defensible claim: "15-30s fresh + under 5s incremental." Drives 5 optimization sub-waves W404 + W405-W408. |
| ~~(ADD) `dev/LLM-SMELLS-PATTERN-CATALOG-2026-05-15.md`~~ | W402-research | 14 patterns catalogued (8 CHEAP + 3 MODERATE + 3 EXPENSIVE deferred). v1 surface = 11 patterns. Primary source: Mahmoudi et al. arXiv:2512.18020 (200 systems; 86.06% precision). **First production-grade multi-provider linter** for LLM API anti-patterns when shipped. v1 estimate: 6-8h. Drives W415 implementation (in flight). |

### Pending after W418 (queue for next session) — partially closed by W436

Most W418-queue items closed in the W436 batch (see "Shipped
W420-W436" section below). Remaining + new items roll forward.

| Item | Where | Effort |
|---|---|---|
| **W306 / W307** — final Wave29 sub-waves against the remaining ~3-4 MCP wrappers (W305 closed reports/audit; W306 next). | `src/roam/mcp_server.py` | 1-2h |
| ~~**W405-W408** — four perf optimization sub-waves from W395 memo (shallow git default; Louvain cache expansion; parallel parse via `ProcessPoolExecutor`; phase-timing in `roam doctor`).~~ | partial | W408 phase-timing landed in W436 batch; W405-W407 still queued though **W407 reclassified to VALIDATE** by W395-followup (Louvain cache already implemented) |
| **W404** — `ROAM_PARALLEL_INDEX` default-on (lowest risk of the W395 sub-waves; in flight in this batch). | `src/roam/index/indexer.py` | 1h |
| **W414** — test fixture audit (in flight in this batch). | `tests/conftest.py` + test files | 2-4h |
| **W415** — llm-smells v1 implementation (in flight in this batch; consumes W402-research catalog). | new `src/roam/catalog/llm_smells.py` + MCP wrapper + tests | 6-8h (v1 shipped; v1.1 also shipped via W415b) |
| **W411** — count-drift backstop wave; companion to W399 / W417 (running in parallel with W418). | `README.md` + `CLAUDE.md` + `src/roam/mcp-server-card.json` + `templates/.../mcp-server-card.json` + `llms-install.md` | 1-2h |
| **W413** — small structural cleanup carry-over. | TBD | 1-2h |
| ~~**W370c**~~ — remaining smells stubs from W368 BEHIND list (after W370 + W370b + W371) — **shipped W635 batch** (catalog reached ZERO placeholder stubs; W601-W605 queued for new smell kinds). | `src/roam/catalog/smells.py` | shipped |
| ~~**W377-W382** — six permit-persist drive-by gaps surfaced by W349 red-teaming (W377-batch in flight in this batch).~~ | shipped W436 batch | W377-batch closed all six; 31/31 golden hashes byte-identical, 163 tests pass |
| **W363 / W365 / W366** — MCP state-mutating sub-waves from W340 audit (W364 shared redactor prereq has shipped). | `src/roam/mcp_server.py` + new tests | 4-6h |
| **W420+** — follow-ups beyond this batch; not yet enumerated. | TBD | TBD |
| ~~**W358-W360** — standards-currency refresh (carry-over from W341 audit).~~ | partial | W360-research memo landed in W436 batch (`dev/CROSSWALK-ADDITIONS-2026-05-15.md`); implementation queued as W428 |

---

## Shipped W420-W436 (permit unification + Pattern-3b extension + llm-smells v1.1 + phase-timing reality check)

The wave that followed the W418 consolidation ran nine threads in
parallel across four families: (a) **Pattern-1 family + Pattern-3b
normalization** tightening (W347 / W383 / W421-bail); (b) **perf
optimization shifted from theory to real data** — W408 phase
timings exposed `effects_taint` at 48% of indexer wallclock,
invalidating the W395-followup PageRank-first ranking; (c)
**llm-smells v1 → v1.1** with 5 new CHEAP detectors (W415b); (d)
**standards crosswalk research** feeding three implementation
queues (W373/W374/W428). Strike-throughs on originating lines above
are preserved; this is the fast-lookup index.

### Permit-persist + permit reader unification

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Permit-persist red-team gap closures~~ | W377-batch (W377-W382) | Six gaps surfaced by W349; touched `cmd_pr_bundle._load_permits_from_disk` + `evidence/collector._build_authority_refs`. **31/31 golden hashes byte-identical**; 163 focused tests pass. No `ChangeEvidence.content_hash` movement. |
| ~~(ADD) Unified permit reader behind canonical store helper~~ | W383 | Both `pr-bundle` and `pr-replay` now delegate to `roam.permits.store.load_permits_from_disk`. **163/163 focused tests pass; 31/31 golden hashes remain byte-identical.** Two drive-bys captured as W421/W422. |
| ~~(BAIL) Constitution + lease gatherer delegation audit~~ | W421 | Investigation **bailed** — both gatherers already delegate to canonical readers. 119/119 baseline tests pass. Two drive-bys captured as W425/W426. |

### Pattern-3b extension

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `file_path` → `path` parameter-alias normalization~~ | W347 | Pattern-3b coverage extended. Prefix-pattern cluster (`queries`/`prefix`/`patterns`) **deliberately bailed** — boundary normalization shape doesn't fit a clean one-name canonical without consumer-side churn. **2733 + 140 + 31 focused tests pass.** Three drive-bys queued as W430/W431/W432. |

### llm-smells v1.1

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) llm-smells v1.1.0 — 5 new CHEAP detectors~~ | W415b | `missing_timeout`, `missing_max_retries`, `no_system_message`, `no_retry_backoff`, `call_in_loop`. Total v1.1 detector count = 10 (v1 had 5). All regex-based; no dataflow-engine extension required. 36/36 focused tests pass. Package version bumped 1.0.0 → 1.1.0. Three drive-bys captured as W415c/W415d/W427. |

### Phase-timing instrumentation (the headline real-data finding)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Per-phase timing in `roam doctor`~~ | W408 | Indexer phases (discover / parse / extract / resolve / effects / taint / graph / metrics) emit per-phase wall-clock. **CRITICAL real-data finding: `effects_taint` = 48% of indexer wallclock (67.6s of 139.6s on roam-code itself).** **Invalidates the W395-followup PageRank-first perf ranking** — new ordering: W433 (effects_taint optimization) > W423 (PageRank warm-start) > W424 (SQLite pragma). 134/134 focused tests pass. Three drive-bys queued as W433/W434/W435. |

### Research planning

| Memo | Wave | Drives |
|---|---|---|
| ~~(ADD) `dev/OWASP-TAINT-RULE-PACK-RESEARCH-2026-05-15.md`~~ | W372-research | OWASP 2026 taint rule pack. 3 first-ship rules identified: **W373** (python-ssti), **W374** (java-sqli), **W375** (java-deserialization). All three use existing AST edge types; no dataflow-engine extension required. 3-5h per rule. |
| ~~(ADD) `dev/PERF-PHASES-4-7-RESEARCH-2026-05-15.md`~~ | W395-followup | Phase 4-7 perf research. Top 3: W423 (PageRank warm-start, 2-5s), W424 (SQLite `synchronous=NORMAL`, 1-3s), W407 reclassified to VALIDATE (Louvain cache already implemented). **Superseded mid-window by W408 real-data finding** — new ordering pushes W433 ahead. |
| ~~(ADD) `dev/CROSSWALK-ADDITIONS-2026-05-15.md`~~ | W360-research | Standards crosswalk additions. 5 YAML entries proposed (NIST AI 600-1 + SP 800-218A). CAISI held until H2 2026 (still concept-paper). Implementation queued as **W428**. |

### Pending after W436 (queue for next session) — partially closed by W466

Most W436-queue items closed in the W466 batch (see "Shipped
W437-W466" section below). Remaining + new items roll forward.

| Item | Where | Effort |
|---|---|---|
| **W306 / W307** — final Wave29 sub-waves against the remaining ~3-4 MCP wrappers. | `src/roam/mcp_server.py` | 1-2h |
| ~~**W370c**~~ — remaining smells stubs from W368 BEHIND list — **shipped W635 batch** (catalog reached ZERO placeholder stubs; W601-W605 queued for new smell kinds). | `src/roam/catalog/smells.py` | shipped |
| **W363 / W365 / W366** — MCP state-mutating sub-waves from W340 audit (W364 shared redactor prereq has shipped). | `src/roam/mcp_server.py` + new tests | 4-6h |
| ~~**W373 / W374 / W375** — OWASP taint rule pack v1 implementation (consumes W372-research).~~ | partial | W373 (python-ssti) + W374 (java-sqli) shipped in W466 batch; W375 (java-deserialization) still queued |
| **W433** — `effects_taint` optimization. **Top P0 perf wave** per W408 finding (48% of indexer wallclock). Scoping memo at `dev/EFFECTS-TAINT-PERF-RESEARCH-2026-05-15.md` (W433-research, shipped W466 batch). | `src/roam/index/effects.py` + `src/roam/security/taint*` | 4-8h |
| **W434 / W435** — W408 drive-bys (carry-over follow-ups; not yet enumerated). | TBD | TBD |
| **W423** — PageRank warm-start (from W395-followup; demoted behind W433). | `src/roam/graph/pagerank.py` | 2-4h |
| **W424** — SQLite `synchronous=NORMAL` (from W395-followup; demoted behind W433). | `src/roam/db/connection.py` | 1-2h |
| **W407** — Louvain cache VALIDATE (W395-followup reclassified — verify existing implementation). | `src/roam/graph/clusters.py` | 1h |
| **W428** — standards-crosswalk YAML additions (consumes W360-research). | `templates/audit-report/control-map.yml` | 2-3h |
| **W422** — W383 drive-by carry-over (deprecate standalone permit wrapper). | shipped W466 batch (W429 bundle) | done |
| **W425 / W426** — W421 bail drive-bys (informational follow-ups). | shipped W466 batch (W429 bundle) | done |
| **W430 / W431 / W432** — W347 prefix-pattern cluster drive-bys. | partial | W432 (oracle wrapper dedup) shipped W466 batch; W430/W431 still queued |
| **W415c / W415d / W427** — llm-smells v1.1 drive-bys (additional patterns + MCP wrapper polish). | `src/roam/catalog/llm_smells.py` | 2-4h |
| **W411 / W413 / W414** — count-drift backstop + structural cleanup carry-overs. | partial | W411 substantially closed by W449 auto-gen README MCP table; W413/W414 still queued |
| ~~**W404 / W405 / W406** — remaining W395 perf sub-waves (`ROAM_PARALLEL_INDEX` default-on; shallow git default; `ProcessPoolExecutor` parallel parse).~~ | partial | W405 (shallow git default) shipped W466 batch; W404/W406 still queued |

---

## Shipped W437-W466 (taint rule pack v1 + standards crosswalk research + shallow git default + auto-generated MCP table + qualified-name flag)

The wave that followed the W436 consolidation ran twelve threads in
parallel across five families: (a) **standards crosswalk research
trilogy** (W358 SLSA v1.2 Source Track + W359 OSCAL v1.2 Control
Mapping — both with surprise findings that compress implementation
to ≤2 waves); (b) **taint rule pack v1** (W373 python-ssti + W374
java-sqli + W454 per-rule `qualified_only` flag); (c) **perf —
shallow git default on first index** (W405); (d) **documentation
count drift sealed** (W443 README coverage + W449 auto-generated
MCP tool table — 73-tool drift surface closed); (e) **dedup +
small-cleanup bundle** (W432 oracle wrapper dedup + W429 bundle of
W422/W425/W426). Strike-throughs on originating lines above are
preserved; this is the fast-lookup index.

### Standards crosswalk research

| Memo | Wave | Drives |
|---|---|---|
| ~~(ADD) `dev/SLSA-V12-POSITIONING-2026-05-15.md`~~ | W358-research | SLSA v1.2 Source Track positioning. **roam de-facto covers SRC-L2 today.** SURPRISE: SRC-L3 lift is **one wave** — `cosign_sign_statement()` at `attest/cga.py:495-594` already implemented. Implementation queued as **W451**. |
| ~~(ADD) `dev/OSCAL-V12-CONTROL-MAPPING-2026-05-15.md`~~ | W359-research | OSCAL v1.2 Control Mapping decision. SURPRISE: OSCAL v1.2 added a **7th model** (Control Mapping) — zero-prerequisite first emission for per-run evidence. AR for per-run evidence. Implementation queued as **W464 / W465**. |
| ~~(ADD) `dev/EFFECTS-TAINT-PERF-RESEARCH-2026-05-15.md`~~ | W433-research | `effects_taint` optimization scoping. Three candidates ranked: **(C) double-parse I/O elimination 15-30s zero risk**; (B) function-summary memoization 35→5s; (A) file-signature cache warm-reindex 0s. **Surprise discovery: roam has TWO independent taint engines** — `analysis/taint.py` (Phase 5 indexer-side) vs `security/taint_engine.py` (the `roam taint` command). Implementation queued as **W440 / W441 / W442** (rank order C → B → A). |

### Taint rule pack v1

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) `python-ssti` taint rule~~ | W373 | T-X01, CWE-94 (Server-Side Template Injection). Engine **already** supports qualified-name matching. **7 new + 45 + 39 existing tests pass.** Drive-bys W452/W453/W454 queued (W454 also landed this batch). |
| ~~(ADD) `java-sqli` taint rule~~ | W374 | CWE-89. Same recall-limited precision profile as java-fileupload (engine lacks Java qualified-name resolution today). **7 new + 44 + 31 existing tests pass.** Drive-bys W455/W456/W457 queued — **W455 captures the engine-side Java qualified-name resolution** fix. |
| ~~(ADD) Per-rule `qualified_only` flag for taint engine~~ | W454 | Rules can opt in to qualified-name-only matching. **java-sqli opts in.** **29 + 60 focused tests pass.** Drive-bys W461/W462/W463 queued. |

### Perf — shallow git default on first index

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) 365-day shallow git history default on first index~~ | W405 | `_DEFAULT_SINCE` in `git_stats.py`; `--full-history` opt-out + `ROAM_GIT_SINCE` env var via `cmd_init.py`. The `_first_index()` gate preserves existing deep indexes. **30 + 31 + 115 focused tests pass.** Drive-bys W437/W438/W439 queued. Lowest-risk W395 perf sub-wave. |

### Documentation count drift sealed

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) README coverage for 4 untracked CLI commands~~ | W443 | `evidence-diff`, `evidence-doctor`, `llm-smells`, `findings` added to README. `test_readme_covers_all_canonical_cli_commands` now passes. Drive-bys W449/W450 queued (W449 landed this batch). |
| ~~(ADD) Auto-generated README MCP tool table~~ | W449 | New `surface_counts.mcp_tool_descriptions()` helper drives a generated table. **74 missing tools added**; **core preset count corrected (25 → 57)** to match `roam surface --json`. Closes the 73-tool drift surface. **4/4 + 16/16 + 8/8 + 31/31 test suites pass.** Drive-bys W458/W459/W460 queued. |

### Dedup + small-cleanup bundle

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) Oracle wrapper dedup (5 wrappers)~~ | W432 | All 5 oracle wrappers (`symbol_exists`, `route_exists`, `is_test_only`, `is_reachable_from_entry`, `is_clone_of`) had been duplicated by W306; this wave removed the duplicates. Decorations **228 → 223** unique, matching the CLAUDE.md headline (`mcp tools registered: 223`). Added a new AST duplicate-name CI lint via `surface_counts.mcp_tool_decorations()` helper. Drive-bys W443/W444/W445 queued (W443 landed this batch). |
| ~~(ADD) Small-cleanup bundle (W422 + W425 + W426)~~ | W429 | Three drive-bys from the W383 + W421 batch: W422 deprecates the standalone permit wrapper; W425 adds `lease warnings_out`; W426 surfaces a constitution-unparseable warning. **204/204 tests pass; 31/31 hash stability byte-identical.** Drive-bys W446/W447/W448 queued. |

### Consolidation (docs-only)

| Item | Shipped in | Notes |
|---|---|---|
| ~~(ADD) W436-CONSOLIDATE — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W347-W436 batch~~ | W436 (W436-CONSOLIDATE) | Docs-only; hash-stability mandate held trivially (no source changes). |
| ~~(ADD) W466 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W437-W466 batch~~ | W466 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W466 (queue for next session) — partially closed by W491

Items closed in the W491 batch are struck through inline; remaining + new
roll forward to "Pending after W491" below.

| Item | Where | Effort |
|---|---|---|
| **W306 / W307** — final Wave29 sub-waves against the remaining ~3-4 MCP wrappers (carry-over). | `src/roam/mcp_server.py` | 1-2h |
| ~~**W370c**~~ — remaining smells stubs from W368 BEHIND list — **shipped W635 batch** (catalog reached ZERO placeholder stubs; W601-W605 queued for new smell kinds). | `src/roam/catalog/smells.py` | shipped |
| **W363 / W365 / W366** — MCP state-mutating sub-waves from W340 audit (carry-over). | `src/roam/mcp_server.py` + new tests | 4-6h |
| **W375** — OWASP taint rule pack v1 java-deserialization (last rule in W373/W374/W375 trio). | `src/roam/security/taint_rules/` | 3-5h |
| ~~**W440** / W441 / W442~~ — `effects_taint` optimization sub-waves per W433-research. **W440 shipped W491 batch** — Phase 2 → Phase 5 source-cache handoff, 91.0s → 84.7s = 7% reduction (modest vs 15-30s predicted). W441 + W485 still queued. | `src/roam/index/effects.py` + `src/roam/security/taint*` | partial |
| **W433** — `effects_taint` umbrella wave. | (parent of W440-W442) | (above) |
| ~~**W451** — SLSA SRC-L3 lift (consumes W358-research).~~ Shipped W491 batch — new `src/roam/attest/vsa.py` (369 lines) + `pr-bundle emit --slsa-l3 --sign --keyless`; `cosign_sign_statement` was already predicate-agnostic; 23+144 tests pass. | done | done |
| **W464 / W465** — OSCAL v1.2 Control Mapping emission (consumes W359-research). Zero-prereq first emission for per-run evidence. | `templates/audit-report/` + new emitter | 4-8h |
| **W437 / W438 / W439** — W405 shallow-git drive-bys. | TBD | TBD |
| **W443 / W444 / W445** — W432 oracle dedup drive-bys (W443 landed in W466 batch — README coverage; W444/W445 still queued). | TBD | TBD |
| **W446** / ~~**W447 / W448**~~ — W429 small-cleanup bundle drive-bys. **W447 + W448 shipped W591 batch** — pr-replay info marker on missing leases dir under `migration` / `autonomous_pr`; `roam.leases.store.read_lease(warnings_out=...)` kwarg. **137 + 31 tests pass.** W446 still queued. | TBD | partial |
| **W449 / W450 / W458 / W459 / W460** — W443/W449 MCP-table auto-gen drive-bys (W449 landed in W466 batch). | TBD | TBD |
| **W452 / W453** — W373 python-ssti drive-bys. | TBD | TBD |
| **W455 / W456 / W457** — W374 java-sqli drive-bys; **W455 is the engine Java qualified-name resolution** captured during W374 rollout. **Now more urgent post-W467** because the bare-name no-op surface ties directly to the engine's Java resolution gap. | `src/roam/security/taint_engine.py` | 4-8h |
| **W461 / W462 / W463** — W454 qualified_only flag drive-bys. **W467 closed the precision bug in W491 batch**; these are the wider rollout. | TBD | TBD |
| **W434 / W435** — W408 drive-bys (carry-over). | TBD | TBD |
| **W423** — PageRank warm-start (carry-over; demoted behind W433). | `src/roam/graph/pagerank.py` | 2-4h |
| **W424** — SQLite `synchronous=NORMAL` (carry-over; demoted behind W433). | `src/roam/db/connection.py` | 1-2h |
| **W407** — Louvain cache VALIDATE (carry-over). | `src/roam/graph/clusters.py` | 1h |
| **W428** — standards-crosswalk YAML additions (carry-over, consumes W360-research). | `templates/audit-report/control-map.yml` | 2-3h |
| ~~**W430**~~ / W431 — W347 prefix-pattern cluster drive-bys. **W430 shipped W491 batch** — `target` → `symbol` rename on 9 MCP wrappers, `_PRE_W332_EXEMPT` 14 → 5; 3014 tests pass. W431 still queued. | `src/roam/mcp_server.py` `_PARAM_ALIASES` | partial |
| **W415c / W415d / W427** — llm-smells v1.1 drive-bys (carry-over). | `src/roam/catalog/llm_smells.py` | 2-4h |
| **W413 / W414** — structural cleanup carry-overs (W411 substantially closed by W449). | various | 2-4h |
| **W404 / W406** — remaining W395 perf sub-waves (`ROAM_PARALLEL_INDEX` default-on; `ProcessPoolExecutor` parallel parse). W405 landed W466 batch. | `src/roam/index/` | 4-8h |

## Shipped W467-W491 (SLSA SRC-L3 pipeline + Pattern-3b consolidation + taint precision + perf ground-truth)

| Item | Shipped in | Notes |
|---|---|---|
| ~~SLSA SRC-L3 wire-up~~ | W451 | new `src/roam/attest/vsa.py` (369 lines) + `pr-bundle emit --slsa-l3 --sign --keyless`. `cosign_sign_statement` already predicate-agnostic, so no engine change required. **23 + 144 tests pass.** Closes the W358-research "one wave away" prediction. |
| ~~`target` → `symbol` MCP wrapper rename~~ | W430 | 9 wrappers (prepare_change / trace / affected_tests / annotate_symbol / get_annotations / generate_plan / get_invariants / why_fail / metrics). `_PRE_W332_EXEMPT` 14 → 5. Legacy `target` still resolves via alias with `summary.alias_warnings`. **3014 tests pass.** Saturation point of the Pattern-3b normalization thread. |
| ~~W454 `qualified_only` precision bug fix~~ | W467 | Compound A+C root cause: bare names matched via exact `qualified_name = ?` (Python top-level) AND suffix `LIKE '%.{name}'` (Java wrappers). Fix: bare names are no-ops under `qualified_only=true`. java-sqli YAML scrubbed. **125 + 31 tests pass.** Security-claim integrity now real. |
| ~~Detector FP-rate measurement methodology~~ | W470-research | Memo at `dev/DETECTOR-FP-RATE-METHODOLOGY-2026-05-15.md`. 3 first-to-measure detectors: smells (3047), vibe-check (831), taint. **SURPRISE: OWASP Benchmark is community-rejected** — task-specific real-codebase corpora preferred (Mahmoudi-class study design over synthetic Juliet-style suites). |
| ~~CI auto-trigger SLSA SRC-L3 VSA emit~~ | W471 | New template `src/roam/templates/ci/slsa-src-l3.yml` + `--with-slsa-l3` flag on `cmd_ci_setup`. **Closes Gap A from W358-research** — the CI-side half of the SRC-L3 evidence pipeline. **15 + 31 + 23 tests pass.** |
| ~~Phase 2 → Phase 5 source-cache handoff~~ | W440 | `effects_taint` 91.0s → 84.7s = **7% reduction**. **Below the 15-30s prediction in W433-research** (real-data gains < scoping-memo estimates; savings landed on cache-warm runs not the headline cold path). **216 tests pass.** Honest reframe of the perf trajectory. |
| ~~`roam cga emit --also-vsa` flag~~ | W472 | 110-line `_emit_vsa_sibling` helper threads `--sign --keyless` from parent `cga emit`. **3 new + 43 + 26 + 43 + 31 tests pass.** Third surface in the SLSA SRC-L3 evidence pipeline (after `pr-bundle emit --slsa-l3` and CI auto-trigger). |
| ~~Taint YAML qualified-name hygiene audit + lint~~ | W479 | Audited 22 remaining taint YAMLs — **ZERO offending rules**. Added load-time `warnings.warn` lint + 7-test hygiene guard. Drive-by-fixed an NTFS case-collision bug — **closes W468 + W477**. |
| ~~W468 — NTFS case-collision regression~~ | W479 (drive-by) | Closed via the W479 audit drive-by. |
| ~~W477 — NTFS case-collision regression follow-on~~ | W479 (drive-by) | Closed via the W479 audit drive-by. |
| ~~(ADD) W491 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W430-W491 batch~~ | W491 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W491 (queue for next session)

| Item | Where | Effort |
|---|---|---|
| **W306 / W307** — final Wave29 sub-waves against the remaining ~3-4 MCP wrappers (carry-over). | `src/roam/mcp_server.py` | 1-2h |
| ~~**W370c**~~ — remaining smells stubs from W368 BEHIND list — **shipped W635 batch** (catalog reached ZERO placeholder stubs; W601-W605 queued for new smell kinds). | `src/roam/catalog/smells.py` | shipped |
| **W363 / W365 / W366** — MCP state-mutating sub-waves from W340 audit (carry-over). | `src/roam/mcp_server.py` + new tests | 4-6h |
| ~~**W375** — OWASP taint rule pack v1 java-deserialization~~ | shipped W515 batch | Closes the W372-research first-ship trio. New `src/roam/security/taint_rules/java_deserialization.yaml` (T-X04 / CWE-502 / A08:2021); 15 sources / 12 sinks / 13 sanitizers; `qualified_only:true`. |
| ~~**W441**~~ / W442 / **W485** — remaining `effects_taint` optimization sub-waves. **W441 BAILED with HIGH-IMPACT find** — surfaced the W493 `kind='calls'` vs `kind='call'` typo (real wallclock 0.06s when fed correct data; W433-research's 35s prediction was on stale code). **W485 verdict: MEASUREMENT DRIFT not regression** — 17k → 23.6k symbol corpus growth (+39% / +76% / 7x); effects_taint scaled 67.6s → 87.4s (48% → 50.5% dominance). W442 still queued. | `src/roam/index/effects.py` + `src/roam/security/taint*` | partial |
| **W433** — `effects_taint` umbrella wave (parent of W440-W442 + W485). | (parent) | (above) |
| **W464 / W465** — OSCAL v1.2 Control Mapping emission (consumes W359-research). Now decoupled from SLSA — SRC-L3 pipeline is live, so OSCAL CM emission is the next standards surface. | `templates/audit-report/` + new emitter | 4-8h |
| **W437 / W438 / W439** — W405 shallow-git drive-bys. | TBD | TBD |
| **W444 / W445** — W432 oracle dedup drive-bys (W443 landed W466 batch). | TBD | TBD |
| **W446** / ~~**W447 / W448**~~ — W429 small-cleanup bundle drive-bys. **W447 + W448 shipped W591 batch** — pr-replay info marker on missing leases dir under `migration` / `autonomous_pr`; `roam.leases.store.read_lease(warnings_out=...)` kwarg. **137 + 31 tests pass.** W446 still queued. | TBD | partial |
| **W450 / W458 / W459 / W460** — W443/W449 MCP-table auto-gen drive-bys (W449 landed W466 batch). | TBD | TBD |
| **W452 / W453** — W373 python-ssti drive-bys. | TBD | TBD |
| **W455 / W456 / W457** — W374 java-sqli drive-bys; **W455 is the engine Java qualified-name resolution**, now more urgent post-W467 because the bare-name no-op surface ties directly to the engine's Java resolution gap. | `src/roam/security/taint_engine.py` | 4-8h |
| **W461 / W462 / W463** — W454 qualified_only flag drive-bys. **W467 closed the precision bug**; these are the wider rollout. | TBD | TBD |
| **W434 / W435** — W408 drive-bys (carry-over). | TBD | TBD |
| **W423** — PageRank warm-start (carry-over; demoted behind W433). | `src/roam/graph/pagerank.py` | 2-4h |
| **W424** — SQLite `synchronous=NORMAL` (carry-over; demoted behind W433). | `src/roam/db/connection.py` | 1-2h |
| **W407** — Louvain cache VALIDATE (carry-over). | `src/roam/graph/clusters.py` | 1h |
| ~~**W428** — standards-crosswalk YAML additions (consumes W360-research)~~ | shipped W515 batch | 5 entries shipped: AI600_VALUE_CHAIN_PROVENANCE, AI600_STOP_BUILD_AUTHORITY, SSDF218A_CODE_PROVENANCE, SSDF218A_CODE_REVIEW_AI_OUTPUT, SSDF218A_DEVELOPER_AUTHORIZATION. CAISI held to H2 2026. **W506 in flight** to add the missing SLSA entries. |
| **W431** — W347 prefix-pattern cluster drive-by remainder (W430 + W432 closed the cluster; W431 is the last). | `src/roam/mcp_server.py` `_PARAM_ALIASES` | 1-2h |
| **W415c / W415d / W427** — llm-smells v1.1 drive-bys (carry-over). | `src/roam/catalog/llm_smells.py` | 2-4h |
| **W413 / W414** — structural cleanup carry-overs. | various | 2-4h |
| **W404 / W406** — remaining W395 perf sub-waves (`ROAM_PARALLEL_INDEX` default-on; `ProcessPoolExecutor` parallel parse). | `src/roam/index/` | 4-8h |
| **W481** — wire SLSA SRC-L3 emit through `pr-replay` so the evidence packet carries the VSA artifact reference (W451 added pr-bundle path; pr-replay parity still queued). | `src/roam/commands/cmd_pr_replay.py` + collector | 2-4h |
| ~~**W482** — *(originally `target → symbol` description sweep; superseded.)*~~ | re-scoped + shipped W515 batch as `roam doctor` ci-setup advisory check | Advisory-check inside `roam doctor` compares local `.github/workflows/roam.yml` against canonical CI template. **Real-world signal**: roam-code's own roam.yml has drifted (26 vs 28 lines). 9 new tests + 137/137 focused pass. Follow-on cleanup queued as **W511**. |
| **W483** — detector FP-rate corpus selection per W470-research (pick the first real-repo corpus for smells / vibe-check / taint measurement). | new corpus picker + `dev/` memo | 4-6h |
| **W484** — taint YAML lint elevation: today the bare-name `qualified_only` mismatch emits `warnings.warn`; promote to a hard error once W455's engine Java qualified-name resolution lands. | `src/roam/security/taint_engine.py` | 1-2h (after W455) |
| ~~**W486** — *(originally cga emit --also-vsa parity test; superseded.)*~~ | re-scoped + shipped W515 batch as shared `emit_vsa` helper | New `src/roam/attest/emit_vsa.py` (339 lines); `cmd_pr_bundle` + `cmd_cga` collapse to 9-line + 24-line delegations. 143/143 tests pass. The parity-test scope landed in W498 (next row). |
| **W487** — CI template integration test exercising `--with-slsa-l3` end-to-end against a fixture repo. | `tests/test_ci_setup_slsa_l3.py` (new) | 2-3h |
| ~~**W488** — *(originally taint hygiene-guard `path → paths` extension; superseded.)*~~ | re-scoped + shipped W515 batch as audit of remaining `test_taint_*.py` | Swept for stale bare-name assertions; **CLEAN** — W479 caught the only offender. 128+31 tests pass. |
| **W489** — perf ground-truth memo update: rewrite W433-research estimates against W440 real-data deltas (warm-path 7% vs 15-30s predicted). | `dev/EFFECTS-TAINT-PERF-RESEARCH-2026-05-15.md` | 1h |
| **W490** — README + landing-page SLSA SRC-L3 callout (W451 shipped substrate; user-facing copy still pending). | README + landing-page | 1-2h |

## Shipped W492-W515 (TWO long-latent silent no-ops sealed + taint trio close + SLSA polish + crosswalk + closed-enum lints + advisory check)

| Item | Shipped in | Notes |
|---|---|---|
| ~~W375 — OWASP taint rule pack v1 java-deserialization~~ | W375 | **Closes the W372-research first-ship trio.** T-X04 / CWE-502 / A08:2021; 15 sources / 12 sinks / 13 sanitizers; `qualified_only:true`. New `src/roam/security/taint_rules/java_deserialization.yaml`. |
| ~~W441 — `effects_taint` Phase 2-5 investigation~~ | W441 (BAIL with HIGH-IMPACT find) | Bailed after surfacing the W493 critical-correctness typo. Real wallclock when fed correct data: 0.06s — W433-research's 35s prediction was on stale (no-op) code. Bail was the right move; carries the "investigate-first bails" discipline. |
| ~~W485 — `effects_taint` cold-path verdict~~ | W485 (MEASUREMENT DRIFT, not regression) | W408 baseline ran on 17k-symbol corpus; current roam-code is **23.6k symbols / 29.9k edges / 3.8k files (+39% / +76% / 7x)**. Effects_taint scaled 67.6s → 87.4s; relative dominance held 48% → 50.5%. Honest reframe of the perf trajectory. |
| ~~W486 — shared `emit_vsa` helper~~ | W486 | New `src/roam/attest/emit_vsa.py` (339 lines). `cmd_pr_bundle` + `cmd_cga` VSA emit paths collapse to 9-line + 24-line delegations. **143/143 tests pass.** "Delegate, not move" pattern. |
| ~~W498 — end-to-end VSA parity test~~ | W498 | `TestVsaCliParity` in `tests/test_attest_vsa.py:661+` exercises `pr-bundle emit --slsa-l3` against `cga emit --also-vsa` for byte-identical VSA predicates. **Found real drift**: pr-bundle drops `commit_sha` when `--no-auto-collect`; cga path falls back to `git rev-parse HEAD`. Spawned **W509** fix (in flight). |
| ~~W428 — NIST AI 600-1 + SP 800-218A control-map entries~~ | W428 | 5 entries: `AI600_VALUE_CHAIN_PROVENANCE`, `AI600_STOP_BUILD_AUTHORITY`, `SSDF218A_CODE_PROVENANCE`, `SSDF218A_CODE_REVIEW_AI_OUTPUT`, `SSDF218A_DEVELOPER_AUTHORIZATION`. CAISI held to H2 2026. **W506** in flight to add SLSA entries (claim-integrity follow-on). |
| ~~W493 — CRITICAL CORRECTNESS: taint `kind='calls'` typo~~ | W493 | `propagate_taint` queried `kind='calls'` but writers emit `kind='call'`. **DFS has been a NO-OP since inception.** Three read-side sites fixed (`taint.py:491`, `cmd_dead.py:1565`, `dataflow.py:329`); 4 stale tests that asserted the no-op behavior flipped to assert the contract. **31/31 hash byte-identical. 292 tests pass.** W441's **607-finding projection** now real on production roam-code. |
| ~~W499 — CRITICAL CLAIM-INTEGRITY: critique impact-gate typo~~ | W499 | `critique/checks.py:399` matched 0/14,949 caller edges pre-fix — impact gate was a COMPLETE NO-OP. Post-fix surfaces **5 high-severity findings** on production roam-code. PRs touching `open_db` / `json_envelope` / `to_json` / `invoke_cli` / `path` now correctly exit-5 in `--ci` mode. |
| ~~W505-bundle — 3 closed-enum lints (W502/W503/W504)~~ | W505-bundle | W502 `source_framework`, W503 `pass_condition`, W504 `surface`. **19 + 31 tests pass.** Same shape as W332 / W282 / W211 vocabulary-freeze discipline. |
| ~~W482 — `roam doctor` ci-setup advisory check~~ | W482 (re-scoped) | Compares local `.github/workflows/roam.yml` against canonical CI template. Chose advisory over standalone command (lower friction). **Real-world signal**: roam-code's own roam.yml has drifted (26 vs 28 lines). 9 new tests + 137/137 focused pass. Original W482 scope (description sweep) re-queued. |
| ~~W488 — sweep of remaining `test_taint_*.py`~~ | W488 (re-scoped) | Audited for stale bare-name assertions — **CLEAN** (W479 caught the only offender). 128+31 tests pass. Original W488 scope (`path → paths` hygiene-guard extension) re-queued. |
| ~~(ADD) W516 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W375-W515 batch~~ | W516 (this wave) | Docs-only; hash-stability mandate held trivially. |

## Shipped W506-W533 (W493 BUG FAMILY STRUCTURALLY CLOSED + 3 more silent no-ops sealed + OSCAL pipeline end-to-end + OWASP labels integrity + SLSA SRC-L3 commit_sha parity)

| Item | Shipped in | Notes |
|---|---|---|
| ~~W512 — STRUCTURAL CLOSE of the W493/W499/W511/W524 edge-kind bug family~~ | W512 | **The structural answer to the W493 discipline gap.** New `src/roam/db/edge_kinds.py` closed-enum module with canonical helpers; **12 read-sites migrated** off inline `kind IN (...)` literal-string tuples; **16-test drift-guard lint** added — future inline `kind IN` queries against the edges table fail the lint. **365 tests pass.** Closes the queued W510 item from §24. |
| ~~W511 — `side_effects.py:497` edge-kind union (FOURTH silent no-op in W493 family)~~ | W511 | `effects_propagation` was matching **13 / 14,949 edges** pre-fix (0.087% coverage); post-fix matches **14,949 / 14,949 edges** (100%). Side-effect classification on roam-code had been computed against a near-empty subset of the call graph since the edge-kind canonicals diverged. |
| ~~W524-bundle — Phantom edge-kind hunt + 3 broken sites fixed~~ | W524-bundle | **`cmd_hover.py` was missing 7,534 import edges** (the largest single edge-kind no-op in the family by 3 orders of magnitude — hover output had been blind to imports since launch); `cmd_risk.py` +13 references; defensive plumbing in `cmd_patterns.py`. **202 tests pass.** |
| ~~W531 — SARIF `severity=error` silently downgraded to `"note"` since launch~~ | W531 (W533-bundle) | **CRITICAL OUTPUT INTEGRITY.** GitHub Code Scanning + Microsoft Defender for DevOps + every SARIF-ingesting consumer were NOT flagging roam taint findings as errors since the SARIF feature launched. Fix restores `level="error"` end-to-end. |
| ~~W530 — A05 → A03 mislabel on `java_sqli` + `python_ssti`~~ | W530 (W533-bundle) | Both rules were stamped `A05:2021` (Security Misconfiguration) when they should have been `A03:2021` (Injection). Caught by the W533-bundle audit pass. |
| ~~W532 — owasp_top10 coverage 3/22 → 22/22 rules~~ | W532 (W533-bundle) | Only 3 of 22 taint rules carried owasp_top10 stamps pre-fix; **all 22** now correctly carry the annotation. |
| ~~W492 + W453 — owasp_top10 wired end-to-end (TaintRule → findings.evidence_json → SARIF tags[])~~ | W492 + W453 | Pipeline that makes the W533-bundle visible to every downstream consumer. **207 tests pass.** |
| ~~W465 — OSCAL v1.2 Assessment Results emission~~ | W465 | `roam evidence-oscal --kind assessment-results` now emits v1.2 Assessment Results JSON; a stub Assessment Plan is auto-synthesized when no upstream plan exists (FedRAMP continuous-assessment pattern). **81 tests pass.** With in-flight W464, `roam evidence-oscal` covers both OSCAL v1.2 models end-to-end. |
| ~~W518 — Framework-vocab allowlist consolidation~~ | W518 | New `src/roam/evidence/control_mapping_vocab.py`: 9 framework slugs + 9 titles + 3 pass-conditions + 7 surfaces, with drift-guard test. Same shape as W332 / W282 / W211 / W505-bundle vocabulary-freeze discipline. |
| ~~W506 — SLSA SRC-L2/L3 control-mapping entries + iso_42001 → iso_iec_42001 rename~~ | W506 | 3 new entries in `templates/audit-report/control-map.yml` alongside W428's NIST AI 600-1 + SP 800-218A additions; rename propagated in lockstep across **5 files** so the W518 drift-guard stays green. |
| ~~W509 — `pr-bundle emit` commit_sha fallback via `git rev-parse HEAD`~~ | W509 | Sealed the W498-surfaced drift. Restores SRC-L3 commit-anchored provenance parity with the cga path. |
| ~~W521 — `pr-bundle init` producer-side commit_sha stamping~~ | W521 | Bundles created on no-collect paths carry commit_sha from the moment of creation. **W509 fallback becomes belt-and-suspenders.** **127 tests pass.** |
| ~~(ADD) W549 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W506-W533 batch~~ | W549 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W533 (W520-W570 — SHIPPING BUG fixed + Pattern 1 variant D + canonical severity + ChangeEvidence round-trip + OSCAL persistence + package-data drift-guard)

The W578 consolidation pass folds in ten shipped items from the
W520-W570 stretch. Strike-throughs preserved on originating pending
lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W520 — cga sibling commit_sha fallback~~ | W520 | `emit_cga_vsa_sibling` falls back to `git rev-parse HEAD`; **belt-and-suspenders complement to W509**. Completes the SLSA SRC-L3 commit_sha three-path chain (producer W521 + collector W509 + cga sibling W520). All three paths now carry commit_sha through the no-collect path. |
| ~~W534 — `ChangeEvidence.from_canonical_json(text, *, strict=False)`~~ | W534 | Closed-enum validation. **31 golden fixtures round-trip BYTE-IDENTICAL** with `content_hash` preserved. Forgiving-projection by default; `strict=True` raises on unknown enum values. **Number reassigned** from the §25.8 "AST lint promotion" scope — that scope rolls forward as W565 (see new pending list below). |
| ~~W535 — `roam ci-setup --with-oscal` persistent artifacts~~ | W535 | Materializes `.roam/oscal/control-mapping.json` + `.roam/oscal/stub-assessment-plan.json` with deterministic UUIDv5 + SHA-256-seeded timestamps. **21 + 15 + 16 + 31 tests pass.** **Number reassigned** from the §25.8 `--kind assessment-plan` standalone-emitter scope — that scope rolls forward as W566 (see new pending list below). |
| ~~W554 — SHIPPING BUG: control-mapping.yaml MOVED into `src/roam/templates/audit_report/` + pyproject package-data~~ | W554 | **Customer-facing shipping bug.** `pip install roam-code` users could not previously run `roam ci-setup --with-oscal` or `roam evidence-oscal` against their own projects because the control-mapping YAML was not in the wheel. Lookup migrated to `importlib.resources`. **Verified end-to-end via fresh tmp venv wheel install.** **109 tests pass.** |
| ~~W557 — Version skew fix: server.json + mcp-server-card.json 12.50→13.0~~ | W557 | Via `dev/build_readme_counts.py --apply` (auto-derived path; manual edits would have re-drifted). **60 tests pass.** |
| ~~W561 — Pattern 1 variant D `dropped_enum_rows` + `partial_success` disclosure on AR envelope~~ | W561 | New `from_canonical_json_with_drops()` classmethod returns `(evidence, dropped_rows)`. **LAW-4 anchored on `rows` terminal.** **107 + 176 tests pass.** Direct application of dogfood synthesis "silent success on degraded resolution" guard. |
| ~~W547 + W548 (bundled) — Canonical `src/roam/output/_severity.py` module~~ | W547 + W548 | `SEVERITY_LEVELS` / `SEVERITY_ALIASES` / `normalize_severity` / `to_sarif_level` / `validate_severity` + AST drift-guard. **89 tests pass.** Closes Pattern 3a severity-vocabulary divergence across SARIF emitters. **Numbers reassigned** from the §25.8 OWASP-mislabel-audit (W547) + doc-consistency-extension (W548) scopes — those scopes roll forward as W567 + W568 (see new pending list below). |
| ~~W559 — Wired `ChangeEvidence.from_canonical_json` into cmd_evidence_oscal AR path with `--strict` flag~~ | W559 | Hybrid `Mapping|ChangeEvidence` signature. **W465 golden fixture stays byte-identical.** **116 tests pass.** |
| ~~W563 — Card-hash test normalizes auto-derived fields before hashing~~ | W563 | Hybrid A+B: count/version bumps invisible to hash; R17 tampering guard preserved for every other field. **3 + 10 + 5 + 31 tests pass.** Made the W557 bump land invisibly. |
| ~~W570 — `tests/test_package_data_wheel_drift.py` drift-guard~~ | W570 | Pins `roam.templates.audit_report` + `roam.templates.ci` package-data entries in `pyproject.toml`. **4 + 24 + 15 + 31 tests pass.** Structural answer to the W554 "feature works in src but broken in pip install" failure mode. |
| ~~(ADD) W578 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W520-W570 batch~~ | W578 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W570 (W540-W591 — Pattern-3a severity-rank STRUCTURAL CLOSE + fragile-path sweep + leasing parity + git-helper consolidation + Pattern 1 variant D CLI-boundary close)

The W600 consolidation pass folds in nine shipped items from the
W540-W591 stretch. Strike-throughs preserved on originating
pending lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W564 — MASSIVE Pattern-3a severity-rank consolidation~~ | W564 | 10 sites migrated to canonical `severity_rank()` helper. **460 tests pass.** **31/31 hash-stability byte-identical held.** **14 confidence-rank tables flagged as the next Pattern-3a target (W596 queued).** Continues the structural Pattern-3a close-out chain: W512 (edge-kinds) + W518 (control-mapping vocab) + W547 (severity vocab) + W564 (severity-rank) + W565+W566 (severity helpers) — every cluster surfaced in the dogfood corpus now flows through canonical modules with AST drift-guards. |
| ~~W565 + W566 (bundled) — Severity helpers in `_severity.py`~~ | W565 + W566 | New `severity_to_confidence_level()` + `severity_breakdown()` helpers; **5 call-sites migrated.** **248 tests pass.** **Numbers reassigned** from the old §25.8 AST-lint promotion (W565) + `--kind assessment-plan` emitter (W566) scopes — those scopes roll forward as **W596 / W597** below. Pair with W547/W548 to give every consumer one canonical entry-point per severity-derived computation. |
| ~~W540 — Consolidated `_git_fingerprint` + `_git_commit_sha` helpers~~ | W540 | `pr-bundle init` now shells out to `git rev-parse HEAD` ONCE per invocation instead of TWICE. **105 + 31 tests pass.** **Number reassigned** from old framework-vocab-extension scope — that scope rolls forward as **W598** below. Closes the subprocess-discipline gap surfaced by the W521 producer-side commit_sha stamping work. |
| ~~W447 + W448 (bundled) — Leasing-system Pattern-2 always-emit discipline complete~~ | W447 + W448 | **W447** added the `pr-replay` info marker on missing leases dir under `migration` / `autonomous_pr` modes (explicit `state: "leases_not_initialized"`); **W448** added the `roam.leases.store.read_lease(warnings_out=...)` kwarg (pairs with W425's `list_leases(warnings_out=...)` to give every lease read path the same always-emit surface). **137 + 31 tests pass.** |
| ~~W587 — 10 fragile-path test sites migrated to `tests/_helpers/repo_root.py`~~ | W587 | Closes the worktree-vs-main-tree visibility gotcha (W567) for the 10 highest-noise sites; **37 → 27 fragile-path sites remaining (W594 queued).** Surfaced a real bug as a side effect: `_wrap_with_alias_normalization` param-ordering breaks `test_surface_consistency` — **W595 in flight** to fix. |
| ~~W573 — Pattern 1 variant D family CLOSED at the CLI boundary (NO-OP investigation)~~ | W573 | Confirmed only 1 production call site for `ChangeEvidence.from_canonical_json*` exists (the one W561 already migrated to surface `dropped_enum_rows` + `partial_success`). Variant D class — "silent success on degraded resolution" — fully sealed at the CLI boundary. **No further migration work needed; class structurally closed.** Investigate-first discipline saved fabricating work. |
| ~~W569 — Doc sweep: 9 stale `templates/audit-report/` path refs swept~~ | W569 | Across 8 src/dev files + 1 test docstring + 1 fixture-regen command. **111 tests pass.** Closes the long-tail doc-drift class surfaced by the W554 move (control-mapping.yaml moved into `src/roam/templates/audit_report/`). |
| ~~W591-bundle — Small cleanups~~ | W591-bundle | W584 / W497 / W500 bailed as already-done (investigate-first discipline); W501 audit comments added to 4 test files. **81 tests pass.** |
| ~~(ADD) W600 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W540-W591 batch~~ | W600 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W591 (W370c-W610 — Pattern-3a STRUCTURAL CLOSE across BOTH rank axes + smell catalog ZERO stubs + fragile-path sweep continues + wheel-bundling discipline complete + python-version drift-guard parsing seal)

The W635 consolidation pass folds in sixteen shipped items from
the W370c-W610 stretch. Strike-throughs preserved on originating
pending lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W596 — MASSIVE Pattern-3a confidence-rank consolidation~~ | W596 | **15 sites migrated** to canonical `src/roam/output/confidence.py::confidence_level_rank()`. **561 tests pass.** **31/31 hash-stability byte-identical held.** Closes the Pattern-3a vocabulary cluster across BOTH rank axes — the pair to W564 severity-rank: every Pattern-3a vocabulary surface surfaced in the dogfood corpus now flows through canonical modules with AST drift-guards through 6 modules + 6 AST lint suites. **Third rank axis (risk) flagged as W631 follow-up.** |
| ~~W370c — Smell detector catalog reached ZERO placeholder stubs~~ | W370c | Scoped the W368 BEHIND-list smell stubs + shipped 2 detectors: `refused-bequest` (2 findings) + `primitive-obsession` (144 findings). Catalog now has ZERO placeholder detectors. 5 W370c-followup waves (W601-W605) queued for new smell kinds (first 2 in flight). |
| ~~W594 — 18 fragile-path test sites migrated~~ | W594 | 47 → 29 remaining (W588 inventory pass corrected the W587 estimate upward from 27 to 47). W608 priority pair (W512+W547 drift-guard templates) included. **230 tests pass.** |
| ~~W588 — AST drift-guard for fragile-path `Path(__file__).parents[N]`~~ | W588 | Fail-loud with `_PRE_W594_PENDING` allowlist (47 entries). Companion to W587 + W594 sweep. Pairs with W606 canonical-positional collision lint to give the fragile-path harness gotcha end-to-end lint coverage. |
| ~~W606 — AST lint for canonical-positional collision~~ | W606 | 4 new tests catching the pre-W595 crash class at PR time. Closes the latent breakage class that the W587 fragile-path sweep surfaced (`_wrap_with_alias_normalization` param-ordering — W595 sealed the source bug; W606 ensures regression catches at lint time). |
| ~~W577 — Wheel-built CI smoke job added to `roam-ci.yml`~~ | W577 | 3 steps: build wheel + install fresh venv + run drift-guard from `/tmp`. Pairs with W570 + W610 to close the "feature works in src but broken on wheel install" surface end-to-end. |
| ~~W610 — Wheel drift-guard extended to 3 more package-data surfaces~~ | W610 | `taint_rules` + `languages.extractors` + `mcp-server-card`. **3 new test classes + 6 new tests.** Closes 5 package-data surfaces end-to-end + closes prior 2 silent-empty bugs in the wheel (12.12.1 taint rules + 12.12.2 Jenkinsfile). |
| ~~W515 — Drift-guard parses python-version from live workflow~~ | W515 | False-positive class on CI version bumps sealed: the lint no longer flags routine version-string bumps as drift. **139 tests pass.** |
| ~~W564 — MASSIVE Pattern-3a severity-rank consolidation (W591 carry)~~ | W564 | 10 sites migrated, 460+31 tests. Listed here as the structural pair to W596 in the BOTH-rank-axes structural close-out chain. |
| ~~W565 + W566 (bundled) — Severity helpers (W591 carry)~~ | W565 + W566 | `severity_to_confidence_level()` + `severity_breakdown()` helpers, 5 call-sites migrated, 248 tests. |
| ~~W447 + W448 (bundled) — Leasing parity (W591 carry)~~ | W447 + W448 | `pr-replay` info marker + `read_lease(warnings_out=...)` kwarg. 137+31 tests. |
| ~~W587 — 10 fragile-path test sites migrated (W591 carry)~~ | W587 | First 10 highest-noise sites; pairs with W594 + W588 + W606. |
| ~~W573 — Pattern 1 variant D CLI boundary CLOSED (W591 carry)~~ | W573 | NO-OP investigation: only 1 production call site for `ChangeEvidence.from_canonical_json*` exists. Variant D class fully sealed at the CLI boundary. |
| ~~W569 — Doc sweep: 9 stale `templates/audit-report/` path refs (W591 carry)~~ | W569 | 8 src/dev files + 1 test docstring. 111 tests. |
| ~~W591-bundle — Small cleanups (W591 carry)~~ | W591-bundle | W584/W497/W500 bailed as already-done; W501 audit comments added to 4 test files. 81 tests. |
| ~~(ADD) W635 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W370c-W610 batch~~ | W635 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W635 (W601/W602/W603/W604/W605/W607/W624/W631/W636/W639/W640/W648 — Pattern-3a GENUINELY CLOSED across ALL THREE rank axes + smell catalog reached 20 detectors + _wrap_with_alias_normalization refactor+dedup chain + cross-detector empty-corpus smoke)

The W657 consolidation pass folds in nine shipped items from the
W607-W648 stretch. Strike-throughs preserved on originating pending
lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W631 — Third Pattern-3a axis CANONICALIZED~~ | W631 | New `src/roam/output/risk.py::risk_rank()` helper; 2 sites migrated (`cmd_migration_plan` + `cmd_path_coverage`). **Pattern-3a STRUCTURALLY CLOSED ACROSS ALL THREE AXES** (severity W547+W564 + confidence W596 + risk W631). **131 tests pass.** |
| ~~W648 — AST audit for slipped rank tables~~ | W648 | **Result: ZERO slipped.** Pattern-3a is GENUINELY structurally closed — the audit confirms the structural-close claim is not just-in-name. **47/47 + 31/31 tests pass.** |
| ~~W640 — `cmd_alerts._LEVEL_ORDER` folded into `severity_rank()`~~ | W640 | Sort key now uses `-severity_rank(lowercase)` instead of a private rank table. Drift-guard regex broadened `/sever/ → /sever|level_order/`. **121 tests pass.** One of the W648 audit's findings, sealed in the same wave. |
| ~~W601 — `switch-statement` smell detector~~ | W601 | **7 findings on roam-code itself, surfaced a REAL refactor candidate**: `_create_extractor` 23-arm switch (W646 in flight to refactor). |
| ~~W602 — `temporal-coupling` smell detector~~ | W602 | **10 findings; top coupling is cli ↔ `_run_roam_inprocess` at 34 commits.** W601+W602 bundle: 17 → 19 detectors, 165 tests pass. |
| ~~W603 — `magic-numbers` smell detector~~ | W603 | Continues W370c. |
| ~~W604 — `boolean-parameter` smell detector~~ | W604 | Continues W370c. |
| ~~W605 — `comment-density` smell detector~~ | W605 | TODO/FIXME/XXX/HACK. **20th detector ships; roam-code CLEAN (max rate 0.49%).** **173 tests pass.** **Closes W370c 5-smell expansion (W601/W602/W603/W604/W605 all shipped).** LAW-4 anchor sets bumped 92→93 / 109→110. |
| ~~W607 — Decomposed `_wrap_with_alias_normalization` into 3 helpers~~ | W607 | `_collect_alias_candidates`, `_build_merged_signature`, `_build_merged_annotations`. **130 → 50 lines.** **7 unit tests + 2960 focused tests pass.** |
| ~~W636 — Sync/async wrapper closure duplication collapsed~~ | W636 | New shared `_prepare_kwargs` helper + branched closure pattern. **33 → 28 lines** and duplicate-body anti-pattern eliminated. **40 tests pass.** |
| ~~W639 — Cross-detector empty-corpus smoke test~~ | W639 | Guards **54 detectors** (20 smells + 34 algo + 2 floor counts; 56+115+17+31 = 219 tests) against silent import errors after concurrent merges. Catches the W601/W602-style concurrent-merge import-error regression class at PR time. |
| ~~W624 — `mcp --card` handler migrated to `importlib.resources`~~ | W624 | `mcp_server.py:14593-14624` now resolves via `importlib.resources.files("roam") / "mcp-server-card.json"` with `as_file()`. **10 + 31 + 140 tests pass.** |
| ~~(ADD) W657 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W607-W648 batch~~ | W657 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W648 (W642-W685 — P0 user-flagged regression batch + bare-except discipline end-to-end + wheel-bundling COMPLETE + smell-suppression substrate + CRITICAL latent bug surfaced in suppressions.json)

The W698 consolidation pass folds in seventeen shipped items
from the W642-W685 stretch. Strike-throughs preserved on
originating pending lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W670 P0.1 — `roam_plan` `file_path` alias regression~~ | W670 | Moved `_wrap_with_alias_normalization` BEFORE the preset filter so every tool gets its alias contract, not just those that survive the filter. **User-flagged P0.** |
| ~~W671 P0.2 — `roam_catalog` cold-start auto-handle exemption~~ | W671 | New `_INLINE_RESPONSE_TOOLS` frozenset exempts `roam_catalog` from `_wrap_with_handle_off`. Pattern: tools with bounded cold-start output bypass the handle pattern. **User-flagged P0.** |
| ~~W672 P0.3 — 8 files synced to live `238/231/224` counts~~ | W672 | Auto-derived via `dev/build_readme_counts.py --apply`. README + CLAUDE.md + llms-install.md + both mcp-server-card.json copies + server.json + landing-page HTML. **User-flagged P0.** |
| ~~W682 P0.3-followup — README CLI table: added `evidence-oscal` row~~ | W682 | Closes the W672 gap audit. **User-flagged P0.** |
| ~~P0.3 canonical demo — 5-minute moat arc lives at `templates/distribution/landing-page/docs/canonical-demo.html`~~ | session 2026-05-16 | The CTO/CISO/dev-tools-lead screen-recording asset: install → `roam health --sarif` → `roam preflight ensure_index` → `git diff \| roam critique` → `pr-bundle init`+`emit`+`runs end`+`audit-trail-verify`. Each step paragraph names which moat property it proves (credential-free / zero-egress / SARIF / structural critique / tamper-evident `ChangeEvidence`). Cross-linked from `compare.html` (categories foot), `README.md` ("What's next"), and the docs index (now "10 guides", canonical demo as card 1). Demo target is `ensure_index` (204 refs, 608-symbol blast radius — real on roam-code itself). All 5 commands verified to run locally. **Closes `dev/ROAM-STRATEGY-2026-05-15.md` §"Quiet-Proof Definition Of Done" item 3.** |
| ~~W653 — REAL BUG: `run_all_detectors` bare-except now classifies~~ | W653 | Pre-fix: `NameError`/`ImportError`/`AttributeError`/`TypeError` swallowed as per-detector failures. Post-fix: those propagate as `RuntimeError` (structural — re-raise); `sqlite3.Error` keeps swallow+log. |
| ~~W662 — AST drift-guard banning bare-except in detector modules~~ | W662 | 9 sites grandfathered (subsequently narrowed to 4 via W665 + W677). **10/10 tests pass.** |
| ~~W661 — Fail-loud discipline applied to `catalog/detectors` production loop~~ | W661 | 8 new tests. Pairs with W653 source fix + W662 drift-guard for end-to-end coverage. |
| ~~W665 — 3 bare-except sites narrowed (allowlist 9 → 6)~~ | W665 | Continues W662 drift-guard scoping. |
| ~~W677 — 2 more bare-except sites narrowed (allowlist 6 → 4)~~ | W677 | Continues W665 scoping. |
| ~~W664 — `__init__.py` package-data drift-guard~~ | W664 | **CAUGHT A LIVE W643-class bug on first run**: `roam.languages.extractors` missing `__init__.py`. Pairs with W570/W610 (data files) for complete wheel-bundling coverage. |
| ~~W668 — `as_file()` callers audit + 4 fixes + drift-guard~~ | W668 | Pattern sealed: every `importlib.resources.files(...)` call that needs a filesystem path goes through `as_file()`. |
| ~~W642 — Removed triple-parent fallback from `mcp --card` handler~~ | W642 | **-19 LOC.** W624 (prior batch) migrated the resolution to `importlib.resources` so the `parents[3]` fallback was dead code. |
| ~~W658 — `.roam/smells.suppress.yml` smell-suppression substrate~~ | W658 | 225-line module + 17 tests. First-class suppression surface for the smell catalog; per-detector + per-path-glob with deterministic match order. |
| ~~W676 — Suppression-parser audit (BAILED, surfaced CRITICAL latent bug)~~ | W676 | **Found 4 parsers (not 3) with incompatible schemas reading `.roam/suppressions.json`** — two readers consume the same file with different shape contracts, so suppressions silently apply to one detector and not the other. **W691 queued** to seal. |
| ~~W646 — Refactored `_create_extractor` from 105 → 17 lines via `_LANGUAGE_EXTRACTORS` dispatch dict~~ | W646 | **Eat-our-own-dogfood**: W601 flagged the 23-arm switch as a REAL refactor candidate; W646 cleared the finding. First time the smell catalog caught a true positive on roam-code AND the same week's refactor wave sealed it. |
| ~~W683 — `.gitattributes` extended (13 → 49 lines)~~ | W683 | `eol=lf` + 26 binary rules. Closes the CRLF-on-Windows wheel-smoke failure class. |
| ~~W685 — README CLI table header pinned to `"(all 231)"`~~ | W685 | Smart 231-canonical choice. Pairs with W672/W682. |
| ~~(ADD) W698 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W642-W685 batch~~ | W698 (this wave) | Docs-only; hash-stability mandate held trivially. **51/51 doc-consistency + schema-migration tests pass.** |

### Closures since W698 (W691/W642/W685/W647/W650/W693/W697/W695/W702/W649/W707/W705/W720/W708/W692/W722/W689 — CRITICAL silent call-edge mis-attribution fixed (95% reduction repo-wide) + suppression family phased close + comment-density 14→21 languages + symbol-centric temporal-coupling rollup + hygiene wave)

The W733 consolidation pass folds in seventeen shipped items from
the W691-W722 stretch. Strike-throughs preserved on originating
pending lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W708 — CRITICAL: Python call-edge mis-attribution fixed~~ | W708 | `indexer.py:551` + `indexer.py:1192` omitted `line_end` from `all_symbol_rows`, collapsing per-call edge resolution to per-symbol. **`_format_count` non-import edges 78→0; repo-wide 2715→147 (95% reduction).** Affects every detector reading edges (taint, side_effects, critique, dead, smells, vibe-check, ai-rot). Validation in flight (W709). |
| ~~W691 — `.roam/suppressions.json` schema unified~~ | W691 | Schema unification between `finding_suppress` + sarif readers. Closes the W676-found CRITICAL latent bug (two readers with incompatible shape contracts → silent suppress-one-detector-not-the-other). |
| ~~W692 Phase A — Suppression discriminated-union dataclass~~ | W692 | Shipped at `src/roam/policy/suppression_v2.py`. Closed-schema typed surface replacing the prior shape-divergent dict-based parsers. |
| ~~W722 Phase B-a — `load_smells_suppressions_typed()` companion~~ | W722 | `KindSymbolSuppression` internal type bridging the legacy smell-suppression substrate (W658) into the W692 dataclass surface. W723 Phase B-b in flight; W724 Phase C queued. |
| ~~W693 — Cross-loader compat test for 5 suppression substrates~~ | W693 | Pins shape parity across the family — any future schema drift fails the compat test before it ships. |
| ~~W647 — Symbol-centric temporal-coupling rollup~~ | W647 | **10 pair findings → 5 cluster findings** on roam-code; `cmd_health.health` clustered. **Surfaced the false positive that drove the W708 critical fix** — the apparent coupling between two symbols was a mis-attributed call edge. |
| ~~W705 — Unified `_CommentSyntax` record~~ | W705 | Comment-density smell coverage **14 → 21 languages**. Closed-schema language record replaces prior per-language ad-hoc constants. |
| ~~W720 — Comment-density extended to `hcl` + `apex`~~ | W720 | Continues the W705 unification. |
| ~~W650 — Comment-density extended to `/* */` block comments~~ | W650 | Coverage now includes C-family + CSS block comments alongside the existing line-comment detection. |
| ~~W689 — `.editorconfig` added (23 lines)~~ | W689 | Mirrors `.gitattributes` EOL/charset/binary rules. Pairs with W683 `.gitattributes` extension to give editors a single source of truth on line-endings + charset. |
| ~~W685 — README CLI table header pin `"(all 231)"` with auto-count~~ | W685 | New `test_readme_cli_command_count_matches_source`. Fails the build if the README header drifts from the live source count. |
| ~~W695 — `--card` CLI smoke test (2 tests)~~ | W695 | Pins the `mcp --card` handler against silent regression — W624 + W642 migrations now have an end-to-end CLI test. |
| ~~W697 — README CLI test extras-gate~~ | W697 | Auto-allowlist from `cli._DEPRECATED_COMMANDS` so newly-deprecated commands flow through the gate automatically. |
| ~~W702 — `_DEPRECATED_COMMANDS` AST-literal contract test~~ | W702 | Pins the deprecation-list shape — any drift fails at lint time. |
| ~~W649 — `cmd_alerts` UPPER → lowercase canonicalisation~~ | W649 | Per W547 closed-severity-vocab contract. Pairs with W640 (`cmd_alerts._LEVEL_ORDER` fold) to give the alerts surface its full canonical-vocab discipline. |
| ~~W642 — Removed triple-parent fallback from `mcp --card` handler~~ | W642 | **-19 LOC.** Continuation of the W624 importlib.resources migration; `parents[3]` fallback was dead code. |
| ~~W707 — `_serialize_suppressions` dead-code cleanup + regression test~~ | W707 | Serializer was unreachable after the W691 schema unification; regression test pins the call-site count at zero. |
| ~~(ADD) W733 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W691-W722 batch~~ | W733 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W722 (W596/W606/W607/W624/W631/W636/W639/W640/W642/W643/W646/W647/W648/W649/W650/W660/W661/W662/W664/W665/W668/W670/W671/W672/W677/W678/W679/W682/W683/W685/W689/W690/W695/W697/W699/W702/W705/W707/W720/W722/W736/W737/W738/W740/W742/W746 — TWO systemic edge-attribution correctness fixes + suppression Phase C-1 + Pattern-3a third-axis CLOSED + bare-except discipline structurally CLOSED + wheel-bundling thread CLOSED + MCP wrapper P0 batch + smell catalog reached 20 detectors + hygiene drive-bys)

The W755 consolidation pass folds in ~20 shipped items from the
W691-W742 stretch. Strike-throughs preserved on originating pending
lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W708 — CRITICAL: Python call-edge mis-attribution fixed~~ | W708 (prior batch, recap) | `_store_symbols` + `_merge_existing_symbols` in `src/roam/index/indexer.py:551,1192` omitting `line_end` → resolver `le > 0` always false → syms[0] fallback. **Repo-wide 95% mis-attribution reduction.** |
| ~~W742 — CRITICAL: Phantom import-edge mis-attribution to first symbol~~ | W742 | `_closest_symbol` in `src/roam/index/relations.py:488-496,848-898` extended with optional `kind` parameter; returns None for `kind=='import'`. **18 phantom import edges on `_format_count` → 0 + 6 transitive side-effects sealed.** New invariant test in `tests/test_relations.py:167-225`. Pairs with W708 to close the edge-attribution family end-to-end across BOTH call-edge AND import-edge resolution. |
| ~~W722 / W723 / W736 / W737 / W738 — Suppression family migrated through Phase C-1~~ | W722 / W723 / W736 / W737 / W738 | Phase B-a smells typed companion → Phase B-b `finding_suppress` + sarif → Phase C-1a sarif `_load_suppressions` migrated → Phase C-1b `cmd_smells.load_smells_suppressions` migrated → Phase C-1c BAILED on `cmd_triage` (three malformed-input divergences), MIGRATED `suppression.save_suppression` internal dedup. New `tests/test_w738_suppression_wire_format.py` 8/8 pass. |
| ~~W596 — Confidence-level rank canonical helper~~ | W596 | New `src/roam/output/confidence.py`; **15 sites migrated.** Pattern-3a second canonical axis CLOSED. |
| ~~W631 — Risk rank canonical helper~~ | W631 | New `src/roam/output/risk.py`; **4-tier closed enum** + moderate→medium alias. Pattern-3a third axis CLOSED. |
| ~~W640 — `cmd_alerts._LEVEL_ORDER` folded into `severity_rank()`~~ | W640 | Drift-guard regex broadened to enforce the canonicalisation. |
| ~~W648 — AST audit confirmed ZERO slipped rank tables~~ | W648 | Pattern-3a structurally closed for real, not just-in-name. |
| ~~W649 — `cmd_alerts` UPPER → lowercase canonicalisation~~ | W649 | Per W547 closed-severity-vocab contract. |
| ~~W660 — `_find_workspace_root` bare-except narrowed~~ | W660 | Continues W662 drift-guard scoping. |
| ~~W661 — `catalog/detectors.py` production loop fail-loud~~ | W661 | Classifies structural errors as `RuntimeError`. |
| ~~W662 — AST drift-guard banning bare `except Exception: continue/pass`~~ | W662 | Initial `_GUARDED_DIRS` was 4 dirs. |
| ~~W665 — 3 specific bare-except sites narrowed~~ | W665 | Continues W662 scoping. |
| ~~W677 — `formatter.py:420,905` narrowed~~ | W677 | Continues W665 scoping. |
| ~~W678 — `taint_engine.py:133` narrowed~~ | W678 | Held back by parser flux; W662 stabilisation unblocked it. |
| ~~W679 — `detectors.py:4165` narrowed~~ | W679 | Closed-set `sqlite3.Error` / `KeyError` / `TypeError`; allowlist 3 → 2. |
| ~~W707 — REAL BUG: `_serialize_suppressions` dead-code on `first` flag~~ | W707 | Removed; regression test pins call-site count at zero. |
| ~~W740 — `_load_project_config` bare-except narrowed~~ | W740 | Allowlist 4 → 3. |
| ~~W746 — Extended W662 `_GUARDED_DIRS` 4 → 12 to substrate modules~~ | W746 | Constitution / modes / runs / leases / memory / pr-bundles / laws / agents_md now covered. Allowlist structurally pinned at 3 sites total. |
| ~~W624 — Migrated `mcp_server.py:14569` `mcp --card` handler to `importlib.resources`~~ | W624 | Continues W554 / W535 / W610 discipline thread. |
| ~~W642 — Removed triple-parent fallback from `mcp --card` handler~~ | W642 | -19 LOC. Dead code after W624. |
| ~~W643 — Grepped remaining `Path(__file__).parent` resource loads~~ | W643 | Audit confirms importlib.resources migration complete across `src/`. |
| ~~W664 — `__init__.py` package-data drift-guard CAUGHT A LIVE W643-class bug~~ | W664 | `roam.languages.extractors` was missing its `__init__.py` — wheel was shipping without the package. |
| ~~W668 — Audited `as_file()` callers for path-captured-outside-`with` anti-pattern~~ | W668 | 4 sites fixed; drift-guard pins the pattern. |
| ~~W606 — AST lint for canonical-positional collision~~ | W606 | 4 new tests catching the pre-W595 crash class at PR time. |
| ~~W607 — Refactored `_wrap_with_alias_normalization` into 3 helpers~~ | W607 | 130 → 50 lines + 7 unit tests. |
| ~~W636 — Collapsed `_sync` vs `_async` wrapper closure duplication~~ | W636 | 33 → 28 lines via shared `_prepare_kwargs` helper. |
| ~~W670 P0.1 — `roam_plan` `file_path` alias regression fixed~~ | W670 | `_wrap_with_alias_normalization` moved BEFORE preset filter. User-flagged P0. |
| ~~W671 P0.2 — `roam_catalog` cold-start auto-handle exemption~~ | W671 | New `_INLINE_RESPONSE_TOOLS` frozenset. User-flagged P0. |
| ~~W672 P0.3 — `scripts/sync_surface_counts --write` synced to live 238/231/224~~ | W672 | User-flagged P0. |
| ~~W695 — `--card` CLI smoke test~~ | W695 | 2 tests pinning the `mcp --card` handler. |
| ~~W601-W605 / W639 / W646 / W647 / W650 / W699 / W705 / W720 — Smell catalog reached 20 detectors~~ | (all W657 batch + W699 + W736-batch drive-bys, recap) | Switch-statement (7 findings) / temporal-coupling (10) / magic-numbers (495) / boolean-parameter (0) / comment-density; cross-detector empty-corpus smoke; `_create_extractor` 105→17 refactor (W646 dogfood seal); symbol-centric rollup (W647 surfaced W708); block-comment extension (W650); `_format_count` refactor (W699 — cluster finding led to W708 + W742); unified `_CommentSyntax` 21 languages (W705); hcl + apex (W720). |
| ~~W682 — README CLI table evidence-oscal row~~ | W682 | Closes W672 gap audit. |
| ~~W683 — `.gitattributes` 13 → 49 lines~~ | W683 | `* text=auto eol=lf` + 26 binary extensions. |
| ~~W685 — README CLI table header-count assertion~~ | W685 | `test_readme_cli_command_count_matches_source` fails build on drift. |
| ~~W689 — `.editorconfig` mirroring `.gitattributes`~~ | W689 | Editors get a single source of truth on line-endings + charset. |
| ~~W690 — Dev-doc note for pytest on Windows~~ | W690 | Captures the pytest-xdist + Windows quirk surfaced during W708 + W742 validation. |
| ~~W697 — Extra-gate to `test_readme_covers_all_canonical_cli_commands`~~ | W697 | Auto-allowlist from `cli._DEPRECATED_COMMANDS`. |
| ~~W702 — `_DEPRECATED_COMMANDS` AST-literal contract test~~ | W702 | Pins the deprecation-list shape. |
| ~~(ADD) W755 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W722-W742 batch~~ | W755 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W836 (W848 / W849 / W850 / W852 / W853 / W855 / W856 / W857 / W859 — smell catalog 20 → 24 detectors + research-memo arc)

The W865 consolidation pass folds in ~12 shipped items from the
W836→W864 stretch. Strike-throughs preserved on originating pending
lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W853 — `speculative-generality` (YAGNI) detector~~ | W853 | Lives inside `src/roam/catalog/smells.py`. Flags symbols whose only callers are test files. Confidence tier `structural`. Wired into `ALL_DETECTORS`. |
| ~~W857 — `parallel-hierarchy` (Fowler) detector~~ | W857 | Own module `src/roam/catalog/parallel_hierarchy.py`; re-imported into smells.py. Mirrored-subclass-hierarchy smell. **16/16 tests pass.** |
| ~~W855 — Rename-invariant clones (DECKARD-style) library~~ | W855 | Own module `src/roam/catalog/clones_rename_invariant.py`. Characteristic-vector clones that survive identifier renames. **6,070 Type-2 pairs surfaced on roam-code itself.** **6/6 tests pass.** Library-layer only this batch — no CLI surface yet (deliberately split). |
| ~~W852 — `type-switch` (OCP / Fowler) detector~~ | W852 | Own module `src/roam/catalog/type_switch.py`; re-imported into smells.py. Detects chained `isinstance` / `type(...) ==` / `match-case` dispatch on ≥3 concrete classes. All tests pass. |
| ~~W856 — `cross-layer-clone` detector (#1 real-world DRY debt)~~ | W856 | Own module `src/roam/catalog/clones_cross_layer.py`; re-imported into smells.py. Jaccard over callee-NAME multisets across controller/service/repository layers. **23/23 tests pass.** 0 findings on roam-code (correct — it's a CLI library, not a layered web app). Realises the W849 cross-layer DRY thesis. |
| ~~W848 — `dev/PRINCIPLE-ENFORCEMENT-ROADMAP-2026-05-15.md`~~ | W848 | Fowler 22-smell coverage map + top-3 recommendations. Drove the W852-W857 selection. |
| ~~W849 — `dev/DRY-BEYOND-LITERAL-CLONES-2026-05-15.md`~~ | W849 | Identifies cross-layer duplication as the #1 real-world DRY debt class. Drove the W856 algorithm. |
| ~~W850 — `dev/ROAM-DETECTOR-INVENTORY-2026-05-15.md`~~ | W850 | 94 distinct detectors catalogued. First single source of truth for the full detector roster. |
| ~~W859 — W848 correction banner~~ | W859 | W848 draft claimed `empty-catch` was a stub; W370 had already shipped a real detector. Banner added before any downstream reader was misled. |
| ~~(ADD) W865 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W836-W864 batch~~ | W865 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W864 (queue for next session)

| Item | Where | Effort |
|---|---|---|
| ~~**W862** — `smells.py` docstring/count drift-guard~~ | shipped W862 | `tests/test_smells_detector_count_drift.py` (173 lines, 3 tests) AST-parses both the module docstring and `run_all_detectors()` docstring; asserts both stay in lockstep with `len(ALL_DETECTORS)`. Inline drift fixes: `smells.py:2893` "remaining 19 detectors" → "remaining detectors"; `cmd_smells.py:72` "The 15 detectors" → "The detectors". |
| **W863** — `ALL_DETECTORS` ordered ad-hoc, not alphabetical-by-smell-id. If a future SARIF emitter ever depends on stable run-to-run ordering (golden-fixture hashing), becomes a real bug. Standardize + add AST drift-guard. | `src/roam/catalog/smells.py` + `tests/` | 1-2h |
| ~~**W864** — `_loc()` helper duplicated 3 ways~~ | shipped W864 | `src/roam/catalog/_shared.py` (~50 lines) created. 4 `_loc()` definitions → 1 canonical; 2 `_find_workspace_root()` definitions → 1 canonical. Touched: smells.py + clones_cross_layer.py + type_switch.py + detectors.py. **332 focused tests green.** |
| **W861** — Worktree-isolation files surface on main (W783 follow-up). Files created by `isolation: worktree` agents appear in main as untracked rather than staying in the worktree. Not a bug per se but worth documenting in the agent dispatcher: future planning should treat "worktree isolation" as effectively "work in main + auto-stage" for additive changes. | dev/ docs + dispatcher | 1-2h |
| **W855 follow-on — CLI surface + findings persistence for rename-invariant clones.** Library landed this batch; promote to `roam clones --rename-invariant`? and persist into the W95 / W136 findings-registry substrate. | `src/roam/commands/cmd_clones.py` + `src/roam/db/findings.py` | 4-6h |
| **W748 follow-on — Smell catalog 24 → 29 candidate wave.** Wirfs-Brock candidates: string-typing / shotgun-surgery v2 / feature-envy v2 / data-clump v2 / divergent-change. Should adopt the W864 shared helper. **Unblocked by W864 (shipped).** | `src/roam/catalog/smells.py` + per-smell modules | 4-8h per detector |

### Closures since W939 (W871-bulk / W895 / W896 / W897 / W914-pass-3 / W937 / W938 / W940-RESEARCH / W941 / W870 — GATE 1 of the registry-parity milestone CLOSED)

**MILESTONE consolidation, not just a batch.** W869's research memo
catalogued the registry-parity bug class as having 10 instances across
roam-code. **Instance #1 (smell-detector P0 surface) is now structurally
CLOSED.** Pre-state: TWO hand-rolled parallel data tables in lockstep —
`ALL_DETECTORS = [...]` in `smells.py` + `_SMELL_KIND_TO_CONFIDENCE = {...}`
in `cmd_smells.py` — held together by W862 + W867 drift-guard lints. W941
converted BOTH to DERIVED VIEWS off the `@detector`-decorated registry.
~78 lines of parallel-maintained data eliminated. 24 detectors + 1 rollup
= 25 confidence entries, all canonically registered, all derivable.
Detector output bytes unchanged. 283/283 focused tests pass. The
registry-parity bug class is now structurally impossible for this
registry surface.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W940-RESEARCH — Registry-parity next-wave sequencing memo~~ | W940-RESEARCH | Ranked 10 candidate waves; recommended W895 / W896 / W897 design closures + W871-bulk **FIRST** (the decorator migration is nonlinear — eliminates a debt class permanently vs patching individual instances). |
| ~~W895 — `@detector` `rollup_kinds={"cluster": tier}` kwarg~~ | W895 (design closure inline) | Replaces W871-POC `register_rollup_kind` orphan-API. Single source of truth: rollup `smell_id` declared inline on the parent detector. Captured as W943 follow-up. |
| ~~W896 — `all_detectors()` returns sorted-by-smell_id~~ | W896 (design closure inline) | SARIF-stable, grep-friendly, decoration-order-independent. |
| ~~W897 — `freeze_registry()` called at `run_all_detectors` entry~~ | W897 (design closure inline) | Validator runs once per invocation, not per `@detector` call. Decouples import order from finalisation semantics. Resolves W871 POC open question. |
| ~~W871-bulk — 22 remaining detectors migrated to `@detector` decorator~~ | W871-bulk | `registry.py` extended with W895 / W896 / W897 implementations inline. `temporal-coupling` cluster rollup upgraded to `rollup_kinds={"cluster": CONFIDENCE_STRUCTURAL}` per W895. **W862 + W867 parity lints flipped SUBSET → EQUAL** (the registry IS the source of truth, not a subset of the hand-rolled table). **283/283 focused tests pass.** |
| ~~W941 — THE GATE 1 CLOSURE: `ALL_DETECTORS` + `_SMELL_KIND_TO_CONFIDENCE` converted to DERIVED VIEWS~~ | W941 | `smells.py:ALL_DETECTORS = [d.fn for d in registry.all_detectors()]`; `cmd_smells.py:_SMELL_KIND_TO_CONFIDENCE = {d.smell_id: d.confidence_tier for d in registry.all_detectors()} \| {rollup_id: tier for ...}` per W895 rollup_kinds. **~78 lines of parallel-maintained data eliminated.** Detector output bytes unchanged (hash-stability mandate held). 283/283 focused tests pass. **W869-catalogued registry-parity bug class is now structurally impossible for this registry surface.** |
| ~~W870 — Per-detector version-stamp parity lint (permissive)~~ | W870 | AST lint asserts every `@detector`-registered detector either has a per-id `<DETECTOR>_VERSION` module constant OR inherits the composite `SMELLS_DETECTOR_VERSION` fallback. 7/24 per-id stamps; 17/24 share composite. Captured as W944 for strict-mode toggle. **3/3 lint tests pass.** Final P0 piece of W869's hybrid Archetype B+E recommendation. |
| ~~W938 — `cmd_bus_factor._repo_summary_finding_id` folded onto W935's `make_finding_id` canonical~~ | W938 | 4th-cousin site (no `*raw_parts`, only `prefix` + `subject`). One-line return; **hash-stable across 5 sample inputs**. `import hashlib` removed. **43 focused tests pass.** W935 finding-id-builder family is now fully consolidated across all 7 sites. |
| ~~W914-pass-3 — Third stale-pending re-triage~~ | W914-pass-3 | 3 confirmed closures (W221 user-blocked + sub-scope absorbed into W196/W199/W202/W203; W354 absorbed into W454 `qualified_only`; W367 duplicate-pending already #513), 2 BLOCKED (W350 / W351), 11 STILL VALID. **Combined with W876 + W914: 22 stale-pending tasks flipped across 3 passes.** |
| ~~W221 — Audit Trail (R29) status snapshot~~ | closed via W914-pass-3 triage | User-blocked + sub-scope absorbed into W196 / W199 / W202 / W203 milestone. |
| ~~W354 — Verify `qualified_only` flag wiring~~ | closed via W914-pass-3 triage | Verification work absorbed into W454 `qualified_only` flag. |
| ~~W367 — Duplicate-pending~~ | closed via W914-pass-3 triage | Already completed as #513 / prior session. |
| ~~W937 — Closed not-applicable~~ | closed via W937 sweep | Source sweep confirmed no `β†’` corruption remains; W929 fix was the only instance. |
| ~~(ADD) W949 — CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT refresh for W940 → W941 milestone batch~~ | W949 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W948 (queue for next session — W949-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| ~~**W942 — Pivot W862 count-drift lint to registry source**~~ — shipped W965-CONSOLIDATE. All 5 call-sites flipped to `len(list(all_detectors()))` from `roam.catalog.registry`. 179 focused tests pass. | `tests/test_smells_detector_count_drift.py` | 30 min |
| **W943 — Decide `register_rollup_kind` orphan-API status** (W941 follow-up). W895 added `rollup_kinds=` kwarg on `@detector`; the standalone helper is now functionally redundant. Decide: deprecate-and-remove vs leave-as-explicit-API. Captured as DBD-1 in the W940 memo. | `src/roam/catalog/registry.py` | 30 min decision + 1h migration |
| **W944 — W870 strict-mode toggle for per-detector versioning** (W870 follow-up). Once per-id `<DETECTOR>_VERSION` stamps cover the full 24-detector roster (currently 7/24), flip the W870 lint from permissive (composite fallback OK) to strict (per-id required). | `src/roam/catalog/registry.py` + per-detector module constants | 2-4h |
| ~~**W945 — Refresh `registry.py` docstring "two SOURCE-OF-TRUTH" comment**~~ — shipped W965-CONSOLIDATE. Lines ~11-16 + ~68-72 flipped to past-tense; "The source-of-truth collections..." replaces present-tense framing. | `src/roam/catalog/registry.py` docstring | 30 min |
| ~~**W946 — Refresh `smells.py:19` parallel_hierarchy wording**~~ — shipped W965-CONSOLIDATE. Module docstring lines 12-20 refreshed; notes `ALL_DETECTORS` is now a derived view + `@detector` registration. | `src/roam/catalog/smells.py:12-20` | 15 min |
| ~~**W947 — Simplify `test_decorator_registry_parity` self-referential assertions**~~ — shipped W965-CONSOLIDATE as **regression-guard pin** instead of simplification: 11-line "W947 note (KEPT as regression guard, do not delete)" block added at the top of the file. Closes the W941 follow-up — the lint stays as the guard against silent un-deriving. | `tests/test_decorator_registry_parity.py` | 1h |
| ~~**W948 — Move tier rationale inline to `@detector` calls**~~ — shipped W1015-CONSOLIDATE. Per-detector confidence-tier rationale now lives inline at the decorator call sites alongside the W895 `rollup_kinds=` kwarg. | smells.py + sibling catalog modules | shipped |
| **W931 — Add `mypy` to `.venv` typecheck extras** (W939 carry-forward). | `pyproject.toml [project.optional-dependencies]` | 30 min |
| **W932 — Audit `detectors._finding` callers for non-dict `evidence=`** (W939 carry-forward). | `src/roam/catalog/detectors.py` callers | 1h |
| ~~**W933 — Tighten `cmd_alerts._parse_alerts_yaml` + `_resolved_thresholds` return types**~~ — shipped W965-CONSOLIDATE. `_parse_alerts_yaml` → `dict[str, dict[str, Any]]`; `_resolved_thresholds` picked **Option B (loose-but-honest)** because `slot.update(rule)` precludes TypedDict without runtime validation. 46/46 focused tests pass. | `src/roam/commands/cmd_alerts.py` | 1h |
| **W934 — `test_findings_*` parametrization opportunity** (W939 carry-forward). | `tests/test_findings_*.py` | 1-2h |
| **W936 — Migrate `query_cost` string-literal defaults to `QUERY_COST_*`** (W939 carry-forward). | grep-then-migrate | 1h |
| **W350 / W351 — BLOCKED stale-pending rows** (W914-pass-3 finding). Both depend on user signoff on the mode-taxonomy direction; carried forward until that resolves. | external decision | TBD |
| **W903 — W686 path-length recurrence operational note** (carried forward). Tooling-side, not addressable from inside roam. | tooling / harness config | TBD (external) |
| **W906 — Overly-defensive lazy-import comments** in `mcp_server.py` + `oscal.py` (W902 forward-looking, carried forward). | `src/roam/mcp_server.py` + `src/roam/evidence/oscal.py` | 30 min |
| ~~**W918 — `_resolved_thresholds` silent fallback for unknown metrics**~~ — shipped W965-CONSOLIDATE. New `warnings_out: list[str] \| None` param accumulates per-unknown-metric warnings; envelope flips `summary.partial_success=True`; new `agent_contract.facts` entry surfaces the fallback. Backward compat preserved. 52 focused tests pass. | `src/roam/commands/cmd_alerts.py` | 1-2h |
| **W921 — Audit other "duplicated from python_lang" claims** (W904 carry-forward). | grep-then-audit | 1-2h |
| **W887 — `python_idioms._enclosing_symbol` name collision** (W877 drive-by, carried forward). | `src/roam/python_idioms/` + audit | 1h |
| **W888 — `smells._enclosing_symbol` defensive-migration audit** (W877 drive-by, carried forward). | docs / discipline note | 30 min |
| ~~**W890 — `is_test_file` None-guard**~~ — closed not-applicable W1015-CONSOLIDATE. Audit verified the W873-era canonical (`changed_files.is_test_file`) already None-guards its `path` argument; no work needed. Closes W886 drive-by-2 carry-forward. | `src/roam/commands/changed_files.py` | n/a |
| **W898 — Long-term catalog/`_shared.is_test_path` delegate to canonical** (carried forward; **now unblocked by W871-bulk shipping**). | `src/roam/catalog/_shared.py` | 1-2h |
| **W899 — Tighten the Apex `Test.cls` regex** (carried forward). | `src/roam/catalog/_shared.py` | 30 min |
| **W900 — Per-language adapter table** (carried forward; deferred behind W898). | `src/roam/catalog/_shared.py` + 3 sister layers | 4-6h |
| **W872 — Layer-classification heuristic audit across clone detectors** (carried forward). | `src/roam/catalog/clones_cross_layer.py` + sibling detectors | 2-4h |
| **W875 — Consolidate `_finding` / `_make_finding` constructors** — **PARTIALLY SHIPPED** (W923 catalog-layer canonical + W935 id-builders canonical + W938 finished the W935 cluster). Remaining sister-helpers in non-catalog modules still pending. | `src/roam/catalog/_shared.py` + 2 migration sites | 1-2h |
| **W863 — `ALL_DETECTORS` alphabetical ordering + drift-guard** — **PARTIALLY SHIPPED via W896 sorted-by-smell_id retrieval**; the AST drift-guard for the underlying decoration order is now optional. Decide carry vs close. | `src/roam/catalog/smells.py` + `tests/` | 1-2h |
| **W855 follow-on — CLI surface + findings persistence for rename-invariant clones** (carried forward). | `src/roam/commands/cmd_clones.py` + `src/roam/db/findings.py` | 4-6h |
| **W748 follow-on — Smell catalog 24 → 29 candidate wave** (carried forward). **Unblocked by W864 + W871-bulk (both shipped).** New detectors should register via `@detector` decorator only — no parallel-table updates required (W941 made that structurally impossible). | `src/roam/catalog/smells.py` + per-smell modules | 4-8h per detector |
| **W869 registry-parity follow-on — Instances #2-#10** (W940 sequencing memo). Gate 1 closed for the smell-detector registry; nine more instances remain (MCP tool registry, mode-allowlists, `_DEPRECATED_COMMANDS`, `subject_kind`, etc.). Each is its own milestone-scope effort. **W525 ran the Instance #2 (MCP tool registry) proving ground in W965-CONSOLIDATE and STOPPED AT INVENTORY — see W357 / W950 / W951 / W952 / W953 below.** | per-instance | per-instance |

### Closures since W949 (W918 / W924 / W933 / W942 / W945 / W946 / W947 / W954 / W955 / W956 — Gate-1 cleanup + source-tightening trio + W525 STOP-AT-INVENTORY)

**Two arcs in parallel.** The W949-CONSOLIDATE batch closed Gate 1 of
the W869 registry-parity milestone but left a cleanup queue of seven
follow-throughs and three W939-carry-forward source-tightening
pendings. W965-CONSOLIDATE closed both queues in parallel: (1) Gate-1
cleanup (W942 count-drift lint pivot + W945 / W946 wording flips +
W947 regression-guard pin + W955 / W956 inline tightens), and (2)
three independent source landings — **W918** Pattern 2 silent-fallback
fix in `_resolved_thresholds`, **W924** `detector_version` stamp on
`detectors._finding`, and **W933** return-type tightening on
`cmd_alerts._parse_alerts_yaml` + `_resolved_thresholds`. **W525 —
the W869 Instance #2 proving ground (MCP tool registry)** ran the
inventory pass, surfaced real structural gaps the W869 template
would have silently papered over (`category="core"=0` hits;
`mcp_preset=("core",)` is mostly boilerplate at 228/230 tools;
hand-rolled `_CORE_TOOLS=57` matches neither), and **STOPPED at
inventory** rather than mechanically deriving. ~10 closures + 15
drive-by captures since W949.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W942 — Count-drift lint pivoted to registry source~~ | W942 | All 5 call-sites updated to `len(list(all_detectors()))` from `roam.catalog.registry`. **179 focused tests pass.** Closes DBD-3 from the W940 sequencing memo. |
| ~~W945 — `registry.py` docstring + comments past-tense~~ | W945 | Lines ~11-16 ("two SOURCE-OF-TRUTH dicts" framing) + ~68-72 ("The source-of-truth collections...") flipped to past-tense. |
| ~~W946 — `smells.py` module docstring refreshed~~ | W946 | Lines 12-20: `ALL_DETECTORS` is now a derived view; registration happens via `@detector` decorator + `detector(...)(fn)` calls. |
| ~~W947 — Regression-guard note pinned in `test_decorator_registry_parity.py`~~ | W947 | 11-line "W947 note (KEPT as regression guard, do not delete)" block at top of file. Lint stays — guard against silent un-deriving. |
| ~~W955 — Inline tighten of pre-W941 transition wording~~ | W955 | `test_decorator_registry_parity.py:9` flipped "belt-and-braces during the transition window" → past-tense ("…transition window from W871 → W941"). |
| ~~W956 — `freeze_registry` invariant numbering re-ordered to match execution order~~ | W956 | Docstring 1/2/3 was inconsistent with code order; re-numbered: duplicates first (cheapest), then anchored ids, then canonical tier. |
| ~~W918 — Pattern 2 silent-fallback fix in `_resolved_thresholds`~~ | W918 | New `warnings_out` param + envelope `partial_success=True` + new `agent_contract.facts` entry on unknown user-supplied metrics. Backward compat preserved. **52 focused tests pass.** |
| ~~W924 — `detector_version` stamp on `detectors._finding`~~ | W924 | Stamps via the pre-existing canonical `roam.catalog.versions.detector_version(task_id)`. Most task_ids → `DEFAULT_VERSION='1.0.0'`; nested-lookup carries `1.1.0` override. **Hash-stable** — `make_finding_id` hashes only `*raw_parts`. **219 focused tests green.** |
| ~~W933 — `cmd_alerts` return-type tightening~~ | W933 | `_parse_alerts_yaml` → `dict[str, dict[str, Any]]`; `_resolved_thresholds` picked **Option B (loose-but-honest)** because `slot.update(rule)` precludes TypedDict without runtime validation. **46/46 focused tests pass.** |
| ~~W525 — W869 Instance #2 proving ground: STOP AT INVENTORY~~ | W525 (decision) | Three real structural gaps surfaced: (a) `@roam_capability(category="core")` returns 0 hits — `category` enum doesn't include `"core"`; (b) `mcp_preset=("core",)` is mostly boilerplate (228 of 230 tools); (c) hand-rolled `_CORE_TOOLS=57` matches neither. **Decision: STOP at inventory** until W357 strategic call lands. |
| ~~W954 — Regression-guard test landed~~ | W954 | `tests/test_w954_core_tools_capability_drift.py` (3 tests, 191 lines, all pass). Snapshot: `_CORE_TOOLS=57`, capability registry=230 (one retired since W949), `mcp_preset="core"` boilerplate=228, `category="core"=0`. Floors at ~10% headroom. |
| ~~(ADD) W965 — CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT refresh for W941 cleanup + source-tightening trio + W525 inventory~~ | W965 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W965 (W934 / W958 / W961 / W962 / W963 / W964 / W966 / W967 / W968 / W969 / W971 / W975 / W976 — cmd_alerts Pattern-2 family FULLY CLOSED + W923 test-layer consolidation + W966 audit pass)

**The cmd_alerts.py Pattern-2 family is now FULLY CLOSED end-to-end.**
W918 (W965-CONSOLIDATE) opened the chapter; W977-CONSOLIDATE closes it.
**Two trifectas:** W962 / W963 / W964 added `_VALID_OPS` frozenset +
parse-time + check-time op validation + `_coerce_bool` helper for
`delta_alerts`. W967 / W968 / W969 found 2 REAL latent bugs (tiny YAML
parser silently disabled `delta_alerts` for no-PyYAML users; `level:
"fatal"` would KeyError downstream) + landed the drift-guard test
pinning `_VALID_OPS == AlertThreshold.op Literal` via
`typing.get_type_hints`. 87 focused tests pass. **This is the SECOND
consecutive Pattern-2 family fully closed in this session** — the
first was W826 / W834 / W836 (silent SAFE on empty corpus across
taint / health / doctor). cmd_alerts.py is the new exemplar.
**W923 test-layer consolidation**: W934 delegated 24
`test_<detector>_findings_visible_via_cmd_findings_count` tests to a
shared `tests/_findings_helpers.py` via Strategy C; doctor's
exact-count + critique's tolerant exit-code preserved; 24/24 + ~190
sibling tests pass; net -46 lines. **W966 audit pass**: W971 confirmed
the codebase was already W966-compliant (13 HONEST / 0 LYING / 2
VALIDATED across 15 boundary sites); W933 `_resolved_thresholds` is
the EXEMPLAR. W975 / W976 added lock-comments at `json_envelope` +
`_compat_profile_payload` documenting the discipline at the call site.
~10 closures + drive-by captures since W965.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W962 — `_parse_alerts_yaml` op-vocabulary validation at parse time~~ | W962 | New `_VALID_OPS` frozenset (`>` / `>=` / `<` / `<=` / `==`); parse-time validation rejects unknown comparators via `warnings_out` accumulator + `partial_success=true`. Warning text follows LAW 2 + LAW 4. **15 new tests.** |
| ~~W963 — `_check_thresholds` unknown-comparator silent skip closed~~ | W963 | Check-time validation folds through the same `_VALID_OPS` frozenset; unknown op at check time surfaces via `partial_success` instead of silently skipping. Different code path from W962. |
| ~~W964 — `delta_alerts` bool coercion silent disable closed~~ | W964 | New `_coerce_bool` helper rejects non-bool YAML values via `warnings_out` + `partial_success=true` instead of silently `bool(...)`-coercing to enabled. |
| ~~W967 — REAL BUG: tiny YAML parser silently disabled `delta_alerts` for users without PyYAML~~ | W967 | New `_coerce_scalar` helper + scalar-vs-section detection. The fallback parser was treating `delta_alerts: true` at root as a section header rather than a top-level scalar. **0 fixtures exercised this path pre-fix — confirmed latent.** |
| ~~W968 — Drift-guard test pinning `_VALID_OPS == AlertThreshold.op Literal`~~ | W968 | Test consumes `typing.get_type_hints(AlertThreshold)` to extract the `op` field's `Literal[...]` members and assert equality with `_VALID_OPS`. |
| ~~W969 — REAL BUG: `level: "fatal"` would KeyError downstream~~ | W969 | Added `_CANONICAL_LEVELS` frozenset + `_coerce_level` helper at 3 sites + counts initializer fold. Pre-fix: user-supplied `level: "fatal"` would parse cleanly but downstream `_LEVEL_ORDER` lookup would `KeyError`. **0 fixtures exercised this path pre-fix — confirmed latent.** |
| ~~W934 — 24 `test_<detector>_findings_visible_via_cmd_findings_count` tests delegated to `tests/_findings_helpers.py`~~ | W934 | Strategy C: shared helper + retained per-detector tests for fixture independence. **Doctor's exact-count + critique's tolerant exit-code preserved.** 24/24 + ~190 sibling tests pass. **Net -46 lines** (-114 of actual code, +68 lines of shared helper scaffolding). |
| ~~W958 — `_load_alerts_config` return-type tightened to `dict[str, dict[str, Any]]`~~ | W958 | Companion to W933 — return type now matches `_parse_alerts_yaml` for consistency. |
| ~~W961 — CLAUDE.md MCP tool naming convention section added~~ | W961 | New sub-section at CLAUDE.md lines 822-832 documenting the uniform `roam_<underscored>` ↔ `<dashed>` convention + 4-entry alias allowlist for genuine renames. Closes the W953 / W954 audit's documentation gap. |
| ~~W966 — CLAUDE.md "Don't TypedDict a boundary you don't validate" discipline rule~~ | W966 | New sub-section at CLAUDE.md lines 156-170 (companion to W907 "Verify the cycle before hedging"). Codifies the W933 Option B decision rationale. |
| ~~W971 — Audit pass: codebase already W966-compliant~~ | W971 | 15 TypedDict / boundary sites surveyed; **13 HONEST**, **0 LYING**, **2 VALIDATED**. The discipline existed *before* W966 codified it; W933 `_resolved_thresholds` is the EXEMPLAR. |
| ~~W975 — Lock-comment added at `json_envelope`~~ | W975 | Documents the W966 discipline at the call site per W971's audit recommendations. |
| ~~W976 — Lock-comment added at `_compat_profile_payload`~~ | W976 | Same shape as W975 — documents the W966 discipline inline. |
| ~~(ADD) W977 — CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT refresh for cmd_alerts Pattern-2 family FULL CLOSURE + W923 test consolidation + W966 audit pass~~ | W977 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Closures since W977 (W982 / W978 / W983-RESEARCH / W987 / W988 / W989 / W990 / W991 / W994 / W995 / W936 / W970 / W999 — Pattern-2 playbook propagation + SQL ESCAPE discipline + smells_suppress YAML hardening)

**Headline — the playbook propagation wave.** W983-RESEARCH
synthesised W977's cmd_alerts.py Pattern-2 close into a reusable
7-pattern playbook + 3 candidate modules. W1001-CONSOLIDATE
propagated it to all three: W987 sealed `cmd_smells.py` via full
playbook apply; W988 was correctly closed-as-not-applicable (premise
didn't match `cmd_conventions.py` — agent refused to fabricate work);
W989 sealed `cmd_pr_risk.py` via a DIFFERENT real Pattern-2 gap than
W983's framing (silent floor in `_normalise_pr_risk_level`, not the
framed `slot.update` shape). **The methodological lesson: premise
verification is the first step of every playbook application.** W999
amended the W983 case-study memo to codify this rule. **W990 / W991
SQL ESCAPE sweep** fixed 15 LIKE escapes (8 W990 + 6 parallel
drive-bys + 1 matmul fallback); 3 HIGH-risk false-positive sites
sealed in the idiom matchers. **W994 / W995 smells_suppress YAML
hardening** sealed two REAL latent bugs (unparseable `expires`
silent-default + malformed-entry silent-drops; both now surface via
`warnings_out`). **W982** completed the `fan_symbol → fan-symbol`
rename (9 source + ~32 test sites). **W978** fixed the pre-existing
bus_factor stale-kind test failure via fixture monkeypatch. ~15
closures since W977.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W983-RESEARCH — Pattern-2 case-study memo + reusable playbook~~ | W983-RESEARCH | `dev/CMD-ALERTS-PATTERN-2-CASE-STUDY-2026-05-15.md` (374 lines, 7 reusable patterns, 3 candidate modules — cmd_smells / cmd_conventions / cmd_pr_risk). |
| ~~W987 — `cmd_smells.py` Pattern-2 playbook FULLY APPLIED~~ | W987 | Closed-set `--kind` validation against canonical `kind_to_confidence()` (lazy-derive from registry, not `Literal[...]` — smart 1-anchor design); `warnings_out` plumbed from suppression loader → CLI → envelope; unknown `--kind` arguments surface via W918 envelope shape. **185 tests pass.** |
| ~~W988 — CORRECTLY CLOSED AS NOT-APPLICABLE~~ | W988 (discipline win) | Agent verified W983 premise didn't match `cmd_conventions.py` (no user-supplied boundary) and STOPPED instead of fabricating work. **71 baseline tests still pass** (no source bytes moved). |
| ~~W989 — `cmd_pr_risk.py` sealed via a DIFFERENT real Pattern-2 gap than W983's framing~~ | W989 | `_normalise_pr_risk_level` silently floored unknown input to `"low"` per W718 CI-safety contract; now warns + PRESERVES the floor (CI-safety contract held). **NO TypedDict added** per W966 (internal dict, not user boundary). **51 tests pass.** Methodologically the most important close — proves the framework gates work with discipline. |
| ~~W990 — SQL LIKE wildcard audit on `detectors.py`~~ | W990 | **10 accidental wildcard sites + 2 already-correctly-escaped**; 3 HIGH-risk in the idiom matchers. |
| ~~W991 — 15 SQL LIKE escapes (8 W990 + 6 parallel drive-bys + 1 matmul fallback)~~ | W991 | Canonical pattern: `LIKE ? ESCAPE '\\'` + parameter pre-escape via `replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')`. **109 focused tests pass**; smoke confirms `finXinXsortedXarray` excluded. |
| ~~W994 — REAL BUG: `smells_suppress._is_expired` silent-default on unparseable `expires`~~ | W994 | Pre-fix: typo `2026-13-99` silently treated as "not expired" → suppression stays active forever. Fix: parse at LOAD time (raises `ValueError` via new `EXPIRES_FMT` constant) + re-validate at match time. 8 tests. |
| ~~W995 — Malformed-entry drops now surface~~ | W995 | Pre-fix: parser had `# silently skipped` comment in malformed-entry handling. Fix: partitions input into `valid` + `dropped` lists; indexed warnings + rollup accumulate into `warnings_out`. 7 tests. |
| ~~W982 — `fan_symbol → fan-symbol` rename completed~~ | W982 | 9 cmd_fan.py + ~32 test sites; SQL `LIKE 'fan_%'` pattern fixed (sister to W979's kebab-case canonicalisation). **Strategy A persisted-hash break documented** — forward-only migration by design. **27 focused tests pass.** |
| ~~W978 — `test_bus_factor_stale_kind_emitted` failure FIXED via fixture monkeypatch~~ | W978 | Root cause: W405 shallow-history dropped a 2-year-old commit the fixture depended on. Fix monkeypatches `cutoff_days`. **18/18 tests pass.** Three sharp drive-bys captured (W984 / W985 / W986). |
| ~~W936 — 37 `query_cost` string literals migrated to `QUERY_COST_*` constants~~ | W936 | Pairs with W915 constant introduction (W939-CONSOLIDATE) — consumer sites reference canonical constants. Closes W939 carry-forward. |
| ~~W970 — CLAUDE.md W966 sub-section gained 7-line positive counter-example~~ | W970 | `_DEFAULT_THRESHOLDS` (in `cmd_alerts.py`) named as canonical "when TypedDict IS appropriate" exemplar — closed shape, no `slot.update()` mutation, W919's `AlertThreshold` accurately captures it. The W966 rule now ships paired with its inverse exemplar. |
| ~~W999 — W983 case-study memo amendment: "Premise verification is the first step of every playbook application"~~ | W999 | Codifies the W988 + W989 outcomes: (a) W988 closed correctly as not-applicable; (b) W989 found a DIFFERENT real Pattern-2 gap than the playbook's framing; (c) therefore mechanical application produces fabricated work — discipline either seals the nominated gap, finds a different real gap, or stops cleanly. **Single most important methodological output of the batch.** |
| ~~(ADD) W1001 — CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT refresh for Pattern-2 playbook propagation + SQL ESCAPE + smells_suppress YAML hardening~~ | W1001 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W1000 (queue for next session — W1001-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W984 — Autouse conftest for the bus_factor stale-kind fixture monkeypatch** (W978 follow-up). Promoting to autouse covers sibling tests that may regress when the W405 shallow-history default shifts. | `tests/conftest.py` or `tests/test_findings_bus_factor.py` | 30 min |
| **W985 — INFO log when `W405 shallow-history` drops a commit** (W978 follow-up). Drop is currently silent at the `git_history` ingestion layer; an INFO log naming the dropped SHA + reason would help "why is this test failing" investigations. | `src/roam/index/git_stats.py` | 30 min |
| **W986 — CLAUDE.md "first hypothesis" checklist for test-failure triage** (W978 follow-up). When a `test_*_stale_*` or `test_*_history_*` test fails, first hypothesis to check: "did W405 truncate the fixture's expected commit?". | `CLAUDE.md` Quality-discipline section | 30 min |
| **W980 / W981 — W974 UX papercuts** (carry-forward). W980: `AlertThreshold.level` `Literal[...]` tightening produces generic TypedDict error message; a `LITERAL_LEVEL_VALUES` error in `_coerce_level` would be more actionable. W981: the `_coerce_level` error message format doesn't follow LAW 4 concrete-noun-terminal vocabulary. | `src/roam/commands/cmd_alerts.py` | 30 min each |
| **W992 — AST lint asserting every `LIKE ?` in `detectors.py` is paired with `ESCAPE '\\'`** (W991 drift-guard). Prevents the next reviewer from re-introducing an unescaped wildcard pattern. | `tests/test_w992_sql_escape_drift.py` | 1h |
| **W993 — End-to-end smoke test asserting `find_in_sorted_array` does NOT match `finXinXsortedXarray`** (W991 drift-guard). The exact false-positive case W991 sealed; pin it as a regression guard. | `tests/test_w993_finXsortedXarray_smoke.py` | 1h |
| ~~**W996 — W987 follow-up: click-vocab divergence doc**~~ — shipped W1015-CONSOLIDATE as a documentation-grade audit naming 7 commands where `--kind` vs `--type` vs `--metric` vocabulary diverges at the CLI boundary; same Pattern-3b parameter-name canonicalisation shape as the `_PARAM_ALIASES` table for MCP. Feeds W1004 (7-cmd audit pending). | `src/roam/commands/cmd_smells.py` | shipped (docs) |
| **W997 / W998 — `expires` ↔ `expires_on` field-name divergence in smells_suppress YAML** (W994 follow-up). W994 standardised on `expires`; sister suppression substrates carry `expires_on` or `expiry`. Pattern-3b parameter-name canonicalisation gap. | `src/roam/policy/suppression_v2.py` + sibling parsers | 1-2h |
| ~~**W1000 — REAL: `strip_list_payloads` drops `warnings_out` without `--detail`**~~ — shipped W1015-CONSOLIDATE via new `_ALWAYS_PRESERVED_LIST_FIELDS` allow-set in `src/roam/output/formatter.py` + companion lint test asserting the allow-set covers every field touched by the Pattern-2 envelope shape. Disclosure-hygiene class identified; W1006 captures expansion candidates. | `src/roam/output/formatter.py` | shipped |
| **W972 — `_load_alerts_config` non-dict YAML root silent fallback** (W918 Pattern 2 family follow-up, W977 carry-forward). A YAML file whose root is a list rather than a dict silently falls through to defaults rather than surfacing via `partial_success`. Same shape as W918 / W963 / W964. | `src/roam/commands/cmd_alerts.py` | 1h |
| **W973 — `_make_alert` level validation defense** (W969 follow-up, W977 carry-forward). Latent risk: `_make_alert` doesn't re-validate `level` against `_CANONICAL_LEVELS` even though `_coerce_level` does at construction time. Defense-in-depth tightening. | `src/roam/commands/cmd_alerts.py` | 30 min |
| **W974 — Tighten `AlertThreshold.level` to `Literal[...]` (now safe per W969)** (W977 carry-forward). Pre-W969 the field was deliberately `str` because there was no runtime validation; post-W969 `_coerce_level` validates at construction time, so the TypedDict `Literal` tightening is now safe. | `src/roam/commands/cmd_alerts.py` | 30 min |
| **W979 — `dark_matter` ↔ `dark-matter` + `fan_symbol` Pattern-3a divergence** (W977 carry-forward; **fan-symbol leg closed by W982**). One detector_id slug (`dark_matter`) still uses underscores. Pattern-3a metric-name canonicalisation gap. | `src/roam/db/findings.py` + per-detector emitters | 30 min |
| **W357 (strategic, long-horizon) — Pick the MCP registry derivation source** (W977 carry-forward). | `src/roam/mcp_server.py` + `src/roam/plugins/capability.py` | TBD (strategic) |
| **W950 — STRATEGIC: `category=` vs `mcp_preset=` path for MCP registry derivation** (W977 carry-forward). | strategic | TBD |
| **W951 — `mcp_preset=("core",)` default is dead metadata** (W977 carry-forward). | `src/roam/mcp_server.py` decorator default | 1-2h decision + 4-6h migration |
| **W952 — 24 MCP-only tools have no `@roam_capability` anchor** (W977 carry-forward). | per-tool audit | 4-6h |
| **W953 — 4 naming-drift cases between CLI + MCP wrappers** (W977 carry-forward). | per-case docs | 2h |
| **W957 — W862 lint "Fix:" hint forward-compat nit** (W977 carry-forward). | `tests/test_smells_detector_count_drift.py` hint string | 15 min |
| **W959 — `_check_thresholds` `Alert` TypedDict bundle** (W977 carry-forward, W933 follow-up). | `src/roam/commands/cmd_alerts.py` | 1-2h |

### Closures since W1000 (W706 / W948 / W996 / W1000 / W1002 / W1003 / W494 / W886 / W890 / W1009 / W1011 / W1015 / W1016 — W1015-CONSOLIDATE)

The W1015 consolidation pass folds in ~11 status changes from the
W1001 → W1015 stretch. **Pattern-2 propagation arc continued** with
three more loader surfaces sealed (W706 cmd_ignore_findings + W1009
per-finding-suppressions + W1011 cmd_alerts section-level audit
confirmation). **A new disclosure-hygiene class identified** and
sealed by W1000 (`strip_list_payloads` `warnings_out` preservation
via the new `_ALWAYS_PRESERVED_LIST_FIELDS` allow-set). **A shared
YAML loader hardening memo shipped** (W1016-RESEARCH) recommending a
roll-our-own 2-phase migration plan. Test discipline hardened via
W1002 / W1003 (relative test-date offsets + xfail-strict pin
comment) + W494 (order-sensitivity verified clean). Catalog
`_shared.py` direct coverage landed via W1015 (new
`tests/test_catalog_shared.py`, 24 tests). Strike-throughs preserved
on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W706 — `cmd_ignore_findings` Pattern-2 close~~ | W706 (W1015-CONSOLIDATE) | YAML-loader unknown-key path plumbs `warnings_out` + flips `partial_success=true` + surfaces `agent_contract.facts` entry; matches W918 envelope shape. |
| ~~W948 — Move tier rationale inline to `@detector` calls~~ | W948 (W1015-CONSOLIDATE) | Per-detector confidence-tier rationale now lives inline at decorator call sites alongside the W895 `rollup_kinds=` kwarg. |
| ~~W996 — Click-vocab divergence doc~~ | W996 (W1015-CONSOLIDATE) | Documentation-grade audit naming 7 commands where `--kind` vs `--type` vs `--metric` vocabulary diverges. Pattern-3b shape. Feeds W1004 pending. |
| ~~W1000 — `strip_list_payloads` `warnings_out` preservation~~ | W1000 (W1015-CONSOLIDATE) | New `_ALWAYS_PRESERVED_LIST_FIELDS` allow-set + companion lint test. Disclosure-hygiene class identified; W1006 captures expansion candidates. |
| ~~W1002 — Test-date relative offsets~~ | W1002 (W1015-CONSOLIDATE) | Hard-coded absolute dates flipped to relative offsets; defeats autouse `freeze_time` fixture interaction. |
| ~~W1003 — `xfail-strict` pin comment for W1002~~ | W1003 (W1015-CONSOLIDATE) | Inline rationale comment so the next reader doesn't flip back to absolute dates. |
| ~~W494 — `test_inter_unused_return` order-sensitivity verified clean~~ | W494 (W1015-CONSOLIDATE) | Audit found taint inter-procedural unused-return analysis is deterministic; no fix needed. |
| ~~W886 — `is_test_path` None-guard verified already-guarded~~ | W886 (W1015-CONSOLIDATE) | Audit confirmed W873-era canonical (`changed_files.is_test_file`) already None-guards `path`. |
| ~~W890 — `is_test_file` None-guard~~ | W890 (W1015-CONSOLIDATE) | Same audit as W886; closed not-applicable. |
| ~~W1009 — Per-finding-suppressions Pattern-2 close~~ | W1009 (W1015-CONSOLIDATE) | Sister of W994 / W995. Malformed entries partition into valid + dropped + indexed warnings + rollup; flips `partial_success=true`. |
| ~~W1011 — cmd_alerts section-level Pattern-2 audit confirmation~~ | W1011 (W1015-CONSOLIDATE) | Audit-only close confirming every silent-fallback surface is post-W918 / W962 / W963 / W964 / W967 / W968 / W969 compliant. |
| ~~W1015 — `tests/test_catalog_shared.py` (24 tests)~~ | W1015 (this wave) | Direct coverage for W864 `_loc` + W873 `is_test_path` + W877 `_enclosing_symbol` + W923 `make_smell_finding` canonical helpers. |
| ~~W1016-RESEARCH — YAML loader hardening memo~~ | W1016 (W1015-CONSOLIDATE) | `dev/YAML-LOADER-HARDENING-2026-05-15.md` — verdict: roll our own, 2-phase plan (W1018 Phase 1 / W1019 Phase 2), ~125 LOC net removed at 5 of 7 callsites. |

### Pending after W1015 (queue for next session — W1015-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W1018 — Phase 1 of W1016 YAML helper.** Extract canonical `_parse_yaml_with_warnings` + closed-set comparator validator from cmd_alerts; **highest-leverage follow-up of the batch** — unblocks the 5-callsite migration in W1019. | `src/roam/policy/_yaml_helper.py` (new) | 2-3h |
| **W1019 — Phase 2 of W1016 YAML helper migrations** (5 of 7 callsites). Blocked behind W1018. | 5 callsite migrations | 4-6h |
| **W1004 — 7-command click-vocab audit** (W996 follow-up). Audit the `--kind` vs `--type` vs `--metric` boundary vocabulary across the 7 surfaced commands; recommend a canonical normalisation. | per-command audit | 2-3h |
| **W1005 — 3-tier vs 5-tier severity Pattern 3a divergence** (W1011 audit follow-up). Some surfaces report `low/medium/high`, others `info/low/medium/high/critical`. Same Pattern-3a shape as W596 / W631. | per-command audit | 1-2h |
| **W1006 — Formatter sibling preserved-fields expansion** (W1000 follow-up). `_ALWAYS_PRESERVED_LIST_FIELDS` likely needs to expand to cover sibling fields (e.g. `dropped_entries`, `notes`); audit the disclosure-hygiene class end-to-end. | `src/roam/output/formatter.py` | 1-2h |
| **W1007 — `agent_contract:[]` empty-list mistake** (W1011 audit drive-by). Some envelopes emit `agent_contract:[]` instead of `agent_contract:{}`; breaks consumer schema. | per-emitter sweep | 1h |
| **W1008 — `list_counts` envelope-root surfacing.** Pattern-2 envelopes should surface counts at envelope root (`warnings_count`, `dropped_count`) rather than only in nested structures; helps agents check disclosure without parsing full lists. | envelope shape design + emitters | 2-3h |
| **W1010 — DEFERRED behind W1018.** Captured as deferred pending the W1018 shared helper landing; revisit after Phase 1. | TBD (deferred) | TBD |
| **W1012 — Test-date triage** (W1002 follow-up). Sweep remaining hard-coded test dates for the same autouse-fixture interaction W1002 sealed. | tests/ sweep | 1-2h |
| **W1013 — `changed_files` None-guard sibling sweep, leg 1** (W886 / W890 follow-up). Sweep the remaining helpers in `changed_files` for missing None-guards on `path` arguments. | `src/roam/commands/changed_files.py` | 30 min |
| **W1014 — `changed_files` None-guard sibling sweep, leg 2** (W886 / W890 follow-up). Sister to W1013; pair the two if landed together. | `src/roam/commands/changed_files.py` | 30 min |
| **W1017 — Typed `WarningsOut` wrapper plumb** (W918 / W933 family follow-up). The `warnings_out: list[str] | None` accumulator pattern would benefit from a typed wrapper class instead of the bare-list-with-side-effects shape. | new module + 7 callsite migrations | 2-4h |
| **W1020 — Fixture-scope audit** (W1002 follow-up). Audit the test fixtures for hidden autouse-scope interactions like the one W1002 sealed. | `tests/conftest.py` + per-test scope audit | 2-3h |
| **W1021 — `camel_split` location verify** (W901 memo drift). Memo claim about `_camel_split` canonical location may differ from current source; W929 moved the canonical to `tfidf.py` but the W901 `__all__` export note may be stale. Verify and refresh. | docs / memo update | 30 min |
| **W1022 — `_shared.py` polish: type annotations.** Small drive-by from W1015; tighten type annotations on the catalog `_shared.py` helpers. | `src/roam/catalog/_shared.py` | 30 min |
| **W1023 — `_shared.py` polish: docstring tightening.** Small drive-by from W1015. | `src/roam/catalog/_shared.py` | 30 min |
| **W1024 — `_shared.py` polish: internal helper consolidation.** Small drive-by from W1015 — one or two internal helpers can fold further. | `src/roam/catalog/_shared.py` | 30 min |
| ~~**W1025 — Alerts section-level Pattern-2 sibling**~~ — shipped W1042-CONSOLIDATE. cmd_alerts thresholds-section envelope now plumbs `warnings_out`; final cmd_alerts.py Pattern-2 sibling sealed end-to-end. | `src/roam/commands/cmd_alerts.py` | shipped |

### Closures since W1015 (W1017 / W1018 / W1019a/c/d/e / W1025 / W1026 / W1029 / W1031 / W1032 / W1033 / W1034 / W1035 / W1037 / W1039 / W1040 / W1042 — W1042-CONSOLIDATE)

The W1042 consolidation pass folds in ~18 status changes from the
W1015 → W1042 stretch. **Shared YAML helper arc fully landed (Phase 1
+ 4/5 Phase 2 migrations + 3 contract-gap extensions + 1 type alias)**;
**Pattern-2 propagation continued** with 4 more loader surfaces sealed
(W1017 typed per-finding-suppressions + W1025 cmd_alerts thresholds-
section + W1032 deeper load_suppressions close + W1042 sarif typed
loader), bringing the running Pattern-2 loader-site total to **~33-34
sealed end-to-end**; **W1029 + W1034 cargo-cult `or ""` cleanup** (11
sites + 3 helpers None-guarded + 1 sibling); **W1033 + W1037 catalog/*
`__all__` discipline** across 6 modules; **W1039-RESEARCH Pattern-2
evolution memo — STAY verdict**; **W1026 W1016-RESEARCH back-fill** with
W1018 tiebreaks observed during Phase 1 implementation. Strike-throughs
preserved on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1017 — Typed `WarningsOut` wrapper plumb~~ | W1017 (W1042-CONSOLIDATE) | `load_per_finding_suppressions_typed` `warnings_out` plumb; sister to W1009 typed surface. Full Pattern-2 envelope shape now flows through the typed loader. |
| ~~W1018 — Phase 1 of W1016 YAML helper~~ | W1018 (W1042-CONSOLIDATE) | `load_yaml_with_warnings` shipped + 23 tests. Deliberately-unused on landing — abstraction iterated against real migrations rather than designed in isolation. |
| ~~W1019a / W1019c / W1019d / W1019e — Phase 2 migrations~~ | W1019a/c/d/e (W1042-CONSOLIDATE) | 4 of 5 planned Phase 2 callsites migrated (`cmd_ignore_findings` / `smells_suppress` / `per_finding_suppressions` / `rules/loader`). **W1019b in flight** post-W1040 re-dispatch. |
| ~~W1025 — Alerts thresholds-section Pattern-2 sibling~~ | W1025 (W1042-CONSOLIDATE) | Final cmd_alerts.py Pattern-2 sibling captured in the W1015 audit. Trivial extension once W1018 landed. |
| ~~W1026 — W1016-RESEARCH memo back-fill with W1018 tiebreaks~~ | W1026 (W1042-CONSOLIDATE) | Retroactive memo annotation folding W1018 / W1035 / W1040 / W1031 / W1043 evolution into "Phase 1 implementation notes" section. |
| ~~W1029 — Cargo-cult `or ""` sweep (10 sites + 3 helpers)~~ | W1029 (W1042-CONSOLIDATE) | 10 defensive-coercion sites cleaned + 3 helpers tightened with explicit None-guards. Same shape as W907 false-hedge anti-pattern audit. |
| ~~W1031 — Typed overload for `load_yaml_with_warnings`~~ | W1031 (W1042-CONSOLIDATE) | Phase 2 surface: typed-overload return path for callers consuming the helper through TypedDict-fronted loaders. Companion to W933 / W919 / W966 discipline. |
| ~~W1032 — `load_suppressions` + `load_suppressions_typed` deeper Pattern-2~~ | W1032 (W1042-CONSOLIDATE) | Both loaders migrated through `load_yaml_with_warnings`; covers the previously-untreated "valid YAML, semantically-incoherent payload" surface. |
| ~~W1033 — `_shared.py` `__all__` declaration~~ | W1033 (W1042-CONSOLIDATE) | Explicit `__all__` per W901 cross-module private-name-import discipline. Closes a sub-30-min polish item from W1015. |
| ~~W1034 — `causal_graph.py:713` cargo-cult cleanup~~ | W1034 (W1042-CONSOLIDATE) | Drive-by from W1029 — one more `or ""` site the sweep missed. **W1013/W1014 + W1029 + W1034 = 14 cargo-cult removals across 9 files.** |
| ~~W1035 — `load_yaml_with_warnings` `parse_error_label` kwarg~~ | W1035 (W1042-CONSOLIDATE) | Phase 2 contract-gap extension: JSON-parse-error wording for callers needing non-YAML error labelling. |
| ~~W1037 — 5 sibling catalog modules `__all__`~~ | W1037 (W1042-CONSOLIDATE) | Uniform discipline across `smells.py` + `detectors.py` + `parallel_hierarchy.py` + `clones_cross_layer.py` + `type_switch.py`. **6 catalog modules with `__all__` total.** |
| ~~W1039-RESEARCH — Python 3.13+ Pattern-2 evolution memo~~ | W1039 (W1042-CONSOLIDATE) | Verdict: **STAY** the course. W918 envelope shape is structurally cheaper than TypedDict-fronted alternative; W933 / W966 discipline rule applies. W1043 is the maximum-tightening that stays inside the discipline. |
| ~~W1040 — `load_yaml_with_warnings` `force_tiny_parser` kwarg~~ | W1040 (W1042-CONSOLIDATE) | Phase 2 contract-gap extension: PyYAML's strict-timestamp parser was inverting W994's `EXPIRES_FMT` discipline on smells_suppress migration. Kwarg lets callers force the tiny parser even when PyYAML is installed. |
| ~~W1042 — `sarif._load_suppressions_typed` `warnings_out` plumb~~ | W1042 (W1042-CONSOLIDATE) | Final typed-loader sibling closed. Running Pattern-2 loader-site total **~33-34 sealed**. |
| ~~W1043 — `WarningsOut` type alias~~ | W1043 (W1042-CONSOLIDATE) | `TypeAlias = list[str]` at canonical boundary. Finalises W1017 typed wrapper plumb as readability + LSP hint quality only (no behavior change). |

### Closures since W1042 (v13.1 ship + W1047 — W1047-CONSOLIDATE)

The post-W1042 stretch culminated in the **v13.1 RELEASE to PyPI**
(commit `9f0be35d`, tag force-moved to `484e34fa`, PyPI confirmed live)
and the **W1047 `publish.yml` SBOM-upload fix** that produced the first
fully-green end-to-end publish run in the workflow's history (workflow
run `25932785927`, including the post-publish smoke step). Both v13.0
and v13.1 GitHub Releases were backfilled with their CycloneDX SBOMs as
part of the same fix. PyPI wheel content is unchanged across the
force-move — the only delta between `9f0be35d` and `484e34fa` is the
`publish.yml` workflow file.

| Item | Shipped in | Notes |
|---|---|---|
| ~~RELEASE-v13.1 — Ship roam-code v13.1 to PyPI~~ | v13.1 (2026-05-15) | Trusted Publishing OIDC; tag at `9f0be35d` initially, force-moved to `484e34fa` for the W1047 CI fix. PyPI confirms `roam-code==13.1` live. CHANGELOG release headline already established by W836-CONSOLIDATE (Pattern-2 propagation + shared YAML helper + 3 flagship silent-fallback seals). |
| ~~W1047 — `publish.yml` SBOM-upload step fixed~~ | W1047 (W1047-CONSOLIDATE) | `gh release upload` now passes `--repo` explicitly + creates the GitHub Release idempotently via `gh release create ... \|\| true` before upload. Workflow run `25932785927` is the first **fully-green publish run end-to-end** including smoke. Both v13.0 + v13.1 Releases backfilled with CycloneDX SBOMs. |

### Closures since W1198 (W1203 / W1205-audit / W1205-impl / W1206-audit / W1206-impl-skip / W1206-audit-unclear / W1208 / W1212 — CONSOLIDATE-10)

The CONSOLIDATE-10 pass folds in ~15 completions from the W1199 →
W1212 stretch. **Three milestones + one reclassification discipline**:
(a) **SARIF SHIP family grew to 24 emitters** — W1203 `cmd_test_impact`
(23rd, ~333 LOC = 160 prod + 173 test; per-test reach_count ranker
with file-level anchor; reuses global `--sarif` flag via
`_SARIF_CONSUMERS`; 11 new SARIF tests + 59 pre-existing pass) +
W1208 `cmd_n1` (24th; W110 N+1 detector SARIF wrapper with per-query
findings; hash-stable additive wrapper); (b) **Pattern-3b propagation
arc — 11 waves shipped, 58% gap closed** — `_KNOWN_MISSING` 96 → 82
across W1205-impl + W1206-impl-skip; 114 commands closed across W1180
→ W1212; (c) **Reclassification discipline — W1212 + W1213** —
`cmd_coverage_gaps` REVISED from W1199 SHIP (CONSOLIDATE-9) to W1212
SKIP-DISCLOSURE (REPORT-not-detector); `cmd_duplicates` discovered as
SHIP via BAIL-and-capture from W1206-impl-skip premise failure
(captured as W1213). First formal cross-session reclassification in
the propagation arc. **5 SHIPPED + 3 AUDIT-VERDICTs + 7 CAPTURED
(W1199 superseded; W1200-W1202, W1204, W1207, W1209-W1211, W1213
remain)**. Strike-throughs preserved on originating pending lines;
fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1203 — `cmd_test_impact` 23rd SARIF SHIP~~ | W1203 (CONSOLIDATE-10) | ~333 LOC total (160 prod + 173 test). Per-test reach_count ranker with file-level anchor. Reuses global `--sarif` flag via `_SARIF_CONSUMERS`. 11 new SARIF tests + 59 pre-existing pass. Hash-stable additive wrapper. Supersedes W1203 entry in "Pending after W1198". |
| ~~W1205-audit — Wave 10: 10 Bucket B SKIP-DISCLOSURE verdicts~~ | W1205-audit (CONSOLIDATE-10) | **VERDICT SKIP-DISCLOSURE x10.** Sites: `cmd_batch_search` + `cmd_file` + `cmd_symbol` + `cmd_relate` + `cmd_refs_text` + `cmd_history_grep` + `cmd_recipes` + `cmd_sketch` + `cmd_pr_analyze` + `cmd_pr_replay`. |
| ~~W1205-impl — Wave 10 docstring landings~~ | W1205-impl (CONSOLIDATE-10) | 10 Bucket B docstrings shipped. `_KNOWN_MISSING` 96 → 86. |
| ~~W1206-audit — Wave 11 mixed batch~~ | W1206-audit (CONSOLIDATE-10) | 6 SKIP + 4 unclear + 2 SHIP. SHIP captured as W1207 (`cmd_llm_smells`) + W1208 (`cmd_n1`, shipped this window). 4 unclear → W1206-audit-unclear deeper audit. |
| ~~W1206-impl-skip — Wave 11 SKIP landings (5 of 6)~~ | W1206-impl-skip (CONSOLIDATE-10) | 5 of 6 SKIP-DISCLOSURE docstrings: `cmd_affected` + `cmd_closure` + `cmd_compare` + `cmd_conventions` + `cmd_causal_graph`. The 6th (`cmd_duplicates`) **bailed mid-impl** — premise check surfaced per-location findings; captured as W1213 SHIP (BAIL-and-capture pattern). `_KNOWN_MISSING` 88 → 82. |
| ~~W1206-audit-unclear — 4-command deeper audit~~ | W1206-audit-unclear (CONSOLIDATE-10) | 3 SHIP captured (`cmd_fan` W1209 / `cmd_hotspots` W1210 / `cmd_dark_matter` W1211; ~1-2d each); 1 REVISED SKIP (`cmd_coverage_gaps` is REPORT-not-detector → W1212 supersedes W1199). First formal cross-session reclassification. |
| ~~W1208 — `cmd_n1` 24th SARIF SHIP~~ | W1208 (CONSOLIDATE-10) | W110 N+1 detector SARIF wrapper with per-query findings. Hash-stable additive wrapper. |
| ~~W1212 — `cmd_coverage_gaps` REVISED SKIP-DISCLOSURE~~ | W1212 (CONSOLIDATE-10) | **Supersedes W1199 SHIP from CONSOLIDATE-9.** REPORT command — wrap_findings is envelope-level; no FindingRecord. ~10 LOC docstring. First formal cross-session classification revision in the Pattern-3b propagation arc. |

### Pending after W1212 (queue for next session — CONSOLIDATE-10)

The CONSOLIDATE-10 pass folds in ~15 completions from the W1199 →
W1212 stretch. 5 SHIPPED + 3 AUDIT-VERDICTs + 7 CAPTURED. **Three
milestones + one discipline**: SARIF SHIP family at 24 emitters
(W1203 + W1208); Pattern-3b propagation arc 11 waves shipped + 58%
gap closed (`_KNOWN_MISSING` 96 → 82; 114 commands closed across
W1180 → W1212); reclassification discipline applied (W1199 SHIP →
W1212 SKIP; cmd_duplicates SKIP → W1213 SHIP capture). All
carry-forwards from "Pending after W1198" remain in queue except
where superseded.

| Item | Where | Effort |
|---|---|---|
| **W1200 — `cmd_orphan_routes` SARIF SHIP impl** (carry-forward from W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper per W1192/W1195/W1203 scaffold. | `src/roam/commands/cmd_orphan_routes.py` | 1-2d |
| **W1201 — `cmd_pytest_fixtures` SARIF SHIP impl** (carry-forward from W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_pytest_fixtures.py` | 1-2d |
| **W1202 — `cmd_test_gaps` SARIF SHIP impl** (carry-forward from W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_test_gaps.py` | 1-2d |
| **W1204 — `cmd_verify_imports` SARIF SHIP impl** (carry-forward from W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_verify_imports.py` | 1-2d |
| **W1207 — `cmd_llm_smells` SARIF SHIP impl** (captured this window via W1206-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_llm_smells.py` | 1-2d |
| **W1209 — `cmd_fan` SARIF SHIP impl** (captured this window via W1206-audit-unclear). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_fan.py` | 1-2d |
| **W1210 — `cmd_hotspots` SARIF SHIP impl** (captured this window via W1206-audit-unclear). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_hotspots.py` | 1-2d |
| **W1211 — `cmd_dark_matter` SARIF SHIP impl** (captured this window via W1206-audit-unclear). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_dark_matter.py` | 1-2d |
| **W1213 — `cmd_duplicates` SARIF SHIP impl** (captured this window via W1206-impl-skip BAIL-and-capture). Already emits per-location duplicate-pair findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_duplicates.py` | 1-2d |
| ~~W1199 — `cmd_coverage_gaps` SARIF SHIP impl~~ | **SUPERSEDED by W1212 SKIP-DISCLOSURE** (CONSOLIDATE-10) — REPORT-not-detector per W1206-audit-unclear. |
| **W1214+ — Pattern-3b propagation arc, remaining ~74-82 unaudited cmd_*.py files.** Per W1175-RESEARCH breakdown: ~50 Bucket A likely-SKIP-aggregate + ~10 Bucket B/E leftover + ~8 Bucket F likely-SHIP (per W1207/W1209/W1210/W1211/W1213 capture roster above) + ~10-13 unclear. ~10 commands per wave for SKIP; ~1-2 per wave for SHIP. Total estimated: ~7-10 more sessions to close `_KNOWN_MISSING`. | per-command audit + impl | ~7-10 sessions |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since CONSOLIDATE-15 (W1255 / W1255-IMPL / W1272 / W1273 — CONSOLIDATE-16)

The CONSOLIDATE-16 pass folds in 7+ completions from the W1255 →
W1278 stretch — the follow-through batch after the
Pattern-2c 30/30 terminal landed at CONSOLIDATE-15. **The MAJOR
load-bearing milestone**: the **W1255 architectural decision** is
recorded AND shipped within the same consolidation window —
Cranot picked Option (a) "Keep top-level + add siblings"; the
W1255-IMPL ship lands `src/roam/evidence/config_hashes.py` (84 LOC,
NEW) + `ledger.py` stamping at `start_run` (+18 LOC) + `CLAUDE.md`
doc (+17 LOC); 11 new tests + 101 in-scope tests pass; hash-stability
preserved; **vsa.py already CONSUMES `constitution_hash` +
`rules_config_hash` at lines 281-296 so producer wire-up immediately
benefits VSA attestation with zero further code change**; W1253
unblocked. Together with the **W1272 Pattern-2c unresolved-path
Convention-c standardization** across 8 commands (`cmd_impact` +
6 helper-callers + `cmd_preflight` already-compliant pin; 78+105+27+51
tests pass; zero regressions; exit-code-0-on-unresolved uniform
across all 8), the **W1273 test_validate_plan dogfood-brittleness
fix** (3 tests hardened; 27/27 pass; capture-to-fix arc spans
CONSOLIDATE-15 → CONSOLIDATE-16), and 4 drive-by captures
(W1275 + W1276 + W1277 + W1278). The follow-through batch lands
cleanly without the multi-arc-terminal volume of CONSOLIDATE-14 /
CONSOLIDATE-15 — the CONSOLIDATE-N captures → CONSOLIDATE-N+1 ships
cadence applied end-to-end across W1255 (decision captured
CONSOLIDATE-14) + W1272 (audit captured CONSOLIDATE-15) + W1273
(audit captured CONSOLIDATE-15). **4 SHIPPED + 4 CAPTURED + 1
RECLASSIFICATION + 1 UNBLOCK.**

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1255 — `.roam-rules.yml` (root) + `.roam/constitution.yml` + `.roam/control-map.yml` canonical paths (Option (a) decision)~~ | W1255 (CONSOLIDATE-16) | Cranot picked Option (a) "Keep top-level + add siblings". |
| ~~W1255-IMPL — `config_hashes.py` substrate + ledger.py stamping + CLAUDE.md doc~~ | W1255-IMPL (CONSOLIDATE-16) | `src/roam/evidence/config_hashes.py` (84 LOC, NEW) + `ledger.py` stamping at `start_run` (+18 LOC) + `CLAUDE.md` doc (+17 LOC). 11 new tests + 101 in-scope tests pass. Hash-stability preserved. vsa.py already CONSUMES the new fields at lines 281-296 → VSA attestation immediate benefit zero further code. W1253 unblocked. |
| ~~W1272 — Pattern-2c unresolved-path Convention-c standardization (8 commands)~~ | W1272 (CONSOLIDATE-16) | `cmd_impact` + 6 helper-callers + `cmd_preflight` already-compliant pin. 78+105+27+51 tests pass. Zero regressions. Exit code 0 on unresolved uniform across all 8. Post-Pattern-2c-disclosure-terminal shape-uniform sub-arc. |
| ~~W1273 — `test_validate_plan` dogfood-brittleness fix~~ | W1273 (CONSOLIDATE-16) | 3 tests hardened (cold-start-guard bypass + `_vp_blast_radius` stubbing). 27/27 tests pass. Capture-to-fix arc spans CONSOLIDATE-15 → CONSOLIDATE-16. |
| W1276 RECLASSIFIED → W1272-expected-failing | RECLASSIFIED at CONSOLIDATE-16 | `test_impact_auto_logs_not_found_path` needs update for W1272's new exit-code-0-on-unresolved contract. In flight as W1276-fix. |
| W1253 UNBLOCKED | UNBLOCKED at CONSOLIDATE-16 | W1255-IMPL landing was the only remaining blocker. Next-session dispatch picks up. |

### Pending after CONSOLIDATE-16 (queue for next session)

The CONSOLIDATE-16 pass closes the **W1255 architectural-decision-and-implementation
arc within a single consolidation window**, ships the **W1272
Pattern-2c unresolved-path Convention-c standardization** across 8
commands, and lands the **W1273 test_validate_plan dogfood-brittleness
fix**. **4 SHIPPED + 4 CAPTURED + 1 RECLASSIFICATION + 1 UNBLOCK.**
The post-Pattern-2c-disclosure-terminal shape-uniform sub-arc is
8/8 covered with W1278 capturing the remaining 3 `symbol_not_found`
callers (`cmd_test_scaffold` / `cmd_plan_refactor` / `cmd_guard`)
for next session. **In flight at consolidation time: W1253 +
W1276-fix** — neither touches consolidation docs.

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **W1275 — harden 3 remaining dogfood-brittle tests in `test_validate_plan.py`** (partial W1273 follow-up; the W1273 3-test fix landed cleanly but 3 more dogfood-brittle assertions surfaced). | `tests/test_validate_plan.py` | ~30 min |
| **W1276-fix — `test_impact_auto_logs_not_found_path` test-needs-update** (in flight; reclassified as W1272-expected-failing for the new exit-code-0-on-unresolved contract). | `tests/test_cmd_impact_auto_logs.py` | ~15 min |
| **W1277 — restore replay-narration provenance for unresolved-path attempts** (`auto_log` removed from `cmd_impact` during W1272 standardization; potential signal-loss risk on replay-narration surface). | `src/roam/commands/cmd_impact.py` + replay narration substrate | 1-2h |
| **W1278 — audit 3 remaining `symbol_not_found` callers** (`cmd_test_scaffold` / `cmd_plan_refactor` / `cmd_guard`) for Convention-c alignment. W1272 touched 8 of 11 known callers; remaining 3 want audit before bulk-migration. | per-cmd audit | 1-2h |
| **W1253 — `pr-bundle emit` packet-stale architectural decision** (UNBLOCKED by W1255-IMPL; was blocked on the canonical-paths decision). Decide whether the no-upstream-packet case should emit a synthetic stub packet or stay silent. | architectural decision + 1d impl | 1-2h decision + 1d impl |
| **W1251 — 45-site state-vocab bulk migration** (carry-forward from CONSOLIDATE-14 → CONSOLIDATE-15 → CONSOLIDATE-16; **heavy**). Post-W1257 audit landing — consumer-side adoption of the W1235 `_STATE_FAMILY_ALIASES` registry. | per-cmd edit | ~1-2 sessions |

### Closures since CONSOLIDATE-16 (W1284-W1308 — CONSOLIDATE-17)

The CONSOLIDATE-17 pass folds in ~25 completions from the post-v13.2-release
stretch (W1284 → W1308). Four themes carry the batch: (a) **init / cold-start
UX fixes** — W1288 health-banner removal, W1289 mcp-status Pattern-1A
envelope, W1290 surface-count survives `[mcp]` extras gap, W1291 init
self-recommend regression. (b) **SARIF advisory-warning plumb** — W1084 +
W1113 + W1114 + W1115 land `warnings_out` into the 4 SARIF emitters held
on carry-forward since CONSOLIDATE-13, plus W1236 chore drop of orphan
breaking + conventions emitters. (c) **CGA edge-bundle + post-merge CI
hardening** — W1285 deterministic edge_bundle_digest tiebreaker, W1284-G3
SFC synthetic-component anchor for module-scope imports, W1286 clones
language-allowlist perf, W1287 non-hermetic test detector, W1297-W1302
drift-guards + CGA dirty-tree fixes, W1303-W1305 doc-hygiene + ruff I001
+ W792 mirror sync. (d) **MCP card v13.2 sync + CI infrastructure** —
W1306 server.json + changelog.html catchup, W1307 card-hash digest bump,
W1308 LF-normalize + SEP-1649 mirror, W1088 SHA-pin third-party CI
actions, W1089 publish.yml retry-backoff replaces sleep-45. The batch is
the **immediate post-v13.2-release hardening** — first 25 W#s after the
release merge land cleanly without re-opening flagship arcs.
**~20 SHIPPED + ~5 CAPTURED** (the W1297 follow-up trio + W1292 docs +
W1287 detector are the only non-fix entries).

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1084 — `cmd_fitness` SARIF helper `warnings_out` plumb~~ | W1084 (CONSOLIDATE-17) | Shipped via 96d31bd0 alongside W1113/W1114/W1115 — single SARIF advisory-plumb commit covers the 4 emitter carry-forward. |
| ~~W1088 — CI SHA-pin credential-bearing third-party actions~~ | W1088 (CONSOLIDATE-17) | publish.yml + release pipeline locked to immutable SHAs on credential-touching actions. |
| ~~W1089 — publish.yml retry-backoff replaces `sleep 45` smoke-job~~ | W1089 (CONSOLIDATE-17) | Replaces the sleep-45 race that intermittently failed v13.0/v13.1 publish; smoke job now retries with backoff. |
| ~~W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb~~ | W1113 (CONSOLIDATE-17) | Shipped via 96d31bd0; see W1084 note. |
| ~~W1114 — `cmd_rules` SARIF helper `warnings_out` plumb~~ | W1114 (CONSOLIDATE-17) | Shipped via 96d31bd0; see W1084 note. |
| ~~W1115 — `cmd_health` SARIF helper `warnings_out` plumb~~ | W1115 (CONSOLIDATE-17) | Shipped via 96d31bd0; see W1084 note. |
| ~~W1236 — chore: drop orphan SARIF breaking + conventions emitters~~ | W1236 (CONSOLIDATE-17) | f42132a5 — cleanup of two orphan SARIF emitters that no longer had registry consumers post-W1232. |
| ~~W1284-G3 — relations SFC synthetic-component anchor for module-scope imports~~ | W1284-G3 (CONSOLIDATE-17) | a6bcef41 — Vue SFC `<script setup>` module-scope imports now anchor to a synthetic `__sfc_module__` component so cross-file refs resolve cleanly. |
| ~~W1285 — CGA edge_bundle_digest sort-stability via id tiebreaker~~ | W1285 (CONSOLIDATE-17) | 42ccd163 — `edge_bundle_digest` was sort-unstable when source+target+kind tuples tied; appended `id` as tiebreaker to make digest deterministic. |
| ~~W1286 — clones perf: language allowlist on candidate fetch~~ | W1286 (CONSOLIDATE-17) | 95b54051 — clones candidate fetch now filters by language allowlist upstream of similarity scoring; cuts candidate set ~3-5× on multi-language repos. |
| ~~W1287 — non-hermetic test detector~~ | W1287 (CONSOLIDATE-17) | 06058749 — new test-hermeticity detector flags tests reading/writing outside `tmp_path` / mutating `os.environ` without cleanup / using real network. Joins the smell-detector family. |
| ~~W1288 — `cmd_init` drop misleading "Health: N/100" banner~~ | W1288 (CONSOLIDATE-17) | 15f91e59 — cold-start banner removed; the score was computed on a 0-symbol corpus and always showed 100/100, misleading first-run UX. |
| ~~W1289 — `mcp-status` canonical Pattern-1A envelope on import fail~~ | W1289 (CONSOLIDATE-17) | 5b09b494 — when fastmcp is uninstalled, `roam mcp-status` now emits the canonical Pattern-1A "missing prerequisite" envelope (status=`index_not_built`-analog, `next_command: pip install roam-code[mcp]`) instead of a raw ImportError. |
| ~~W1290 — surface AST-derived mcp_tool_count survives `[mcp]` extras gap~~ | W1290 (CONSOLIDATE-17) | 19636ae0 — `roam surface --json` AST-derives the MCP tool count from `@_tool` decorators directly rather than importing the live registry; survives the cold-install case where fastmcp is absent. |
| ~~W1291 — `cmd_init` regression: must not self-recommend (cold-start advisory)~~ | W1291 (CONSOLIDATE-17) | 44e7e6fb — regression test pinning that `roam init` advisory output never recommends running `roam init` (self-recommendation = silent advisory-loop). |
| ~~W1292 — plugin docs: close Gap 3 — 3-hook copy-fork template~~ | W1292 (CONSOLIDATE-17) | f7a24c67 — `dev/example-plugin/` gained a 3-hook template (detect / extract / bridge) so plugin authors can copy-fork without piecing it together from the registry source. |
| ~~W1297 (follow-up) — ruff format 6 drift-guard test files~~ | W1297-followup (CONSOLIDATE-17) | fdf92605 — ruff format pass on 6 drift-guard test files that the v13.2 merge left unformatted. |
| ~~W1298-W1302 — 6 CI failures on main: drift-guards + CGA dirty-tree~~ | W1298-W1302 (CONSOLIDATE-17) | 723a6eab — multi-fix landing for 6 CI failures on main: drift-guard test xfail re-pins + CGA `dirty_tree=true` propagation through pr-bundle integration tests. |
| ~~W1303-W1305 — doc-hygiene drift + ruff I001 + W792 mirror sync~~ | W1303-W1305 (CONSOLIDATE-17) | 3e88eee0 — 3-fix landing: stale wording in 2 docs, ruff I001 import-order on touched files, and the W792 server-card mirror that drifted between `.well-known/` variants. |
| ~~W1306 — server.json + changelog.html v13.2 catchup~~ | W1306 (CONSOLIDATE-17) | 541c20c6 — landing-page server.json and changelog.html were still pinned to v13.1 after the v13.2 release tag; catchup pass. |
| ~~W1307 — bump card hash pin to v13.2 digest~~ | W1307 (CONSOLIDATE-17) | 67024d98 — `_EXPECTED_CARD_SHA256` test pin advanced to v13.2 card digest after W1306 + W1308 mutated the canonical card files. |
| ~~W1308 — LF-normalize MCP card files + sync SEP-1649 variant~~ | W1308 (CONSOLIDATE-17) | f0ea8fe5 — 3 MCP card files (`mcp-server-card.json` + `.well-known/` mirror + SEP-1649 variant) now LF-normalized + content-synced; fixes the CRLF-drift footgun caught on Windows checkouts (related W562). |

### Closures since CONSOLIDATE-17 (W1275-W1312-arc + Wave-B1 + sarif-disclosure — CONSOLIDATE-18)

The CONSOLIDATE-18 pass folds in ~15 completions from the short tight
session that follows the CONSOLIDATE-17 post-v13.2 hardening tail.
Five themes carry the batch: (a) **Pattern-2c carry-forward closures**
— W1275 (3 dogfood-brittle assertions in `test_validate_plan.py`
hardened, 27 tests pass), W1276-fix no-op verified (already landed),
W1277 (`auto_log` provenance restored on `cmd_impact` unresolved-attempt
path for replay-narration), W1278a (cmd_test_scaffold Convention-c
migration, 1 cmd, 22 tests pass), W1309 (`cmd_test_scaffold` Pattern-1D
file-substring `resolution: "file_substring"` enum disclosure, 31
tests pass). (b) **SARIF dashboard-filtering trio** — W1060
runtime-notifications activation tests on cmd_health + cmd_complexity
(NEW test file, 12 tests pass; cmd_doctor BAIL — no SARIF emit path
exists); W1061 `ruleConfigurationOverrides[]` on cmd_smells
(OASIS 2.1.0 § 3.51 compliant, default-off, 38 tests pass) +
W1061-followup extends ruleConfigurationOverrides + new
`notificationConfigurationOverrides` to cmd_check_rules + cmd_taint +
cmd_vulns (11 tests pass); W1062 `result.properties.tags[]` on taint
+ vulns + audit-trail-conformance (21 tests pass) + W1062-followup
secrets_to_sarif tag wiring (60 tests pass). (c) **MCP outputSchema
roadmap kickoff** — W767 inventory at
`dev/MCP-OUTPUTSCHEMA-INVENTORY-2026-05-16.md` (57 core tools
catalogued, 5-wave Wave B roadmap, drive-bys W1311 + W1312); plus the
**MCP-OUTPUTSCHEMA-EVOLUTION** research memo at
`dev/MCP-OUTPUTSCHEMA-EVOLUTION-2026-05-16.md` (Claude Code #25081
status shifted; 3-wave roadmap); Wave B1 lands `_SCHEMA_IMPACT` +
`_SCHEMA_PREFLIGHT` specialized outputSchemas on roam_impact +
roam_preflight (18 tests pass; 1 inventory drift caught); W1311
normalizes 5 oracle multi-line `@_tool(` decorators (-37 LOC, 131
tests pass); W1312 drops 3 redundant `output_schema=_ENVELOPE_SCHEMA`
declarations + queues 2 for Wave B (142 tests pass). (d) **Pattern-1D
file-substring disclosure** — W1309 closes the disclosure gap on
file-substring fallback path in `cmd_test_scaffold` (the Pattern-1D
audit's remaining file-resolver-tier case). (e) **Pattern-3a severity
widening** — W1005 cmd_smells `--min-severity` 3-tier→7-tier W547
canonical widening (236 tests pass); W1005-followup-A cmd_llm_smells
parallel widening (2 tests pass); plus the **W1007** strip_list_payloads
`agent_contract:[]` preservation fix (89 tests pass) closing a
W1006-spec gap. Plus the **SARIF disclosure fix** for cmd_boundary +
cmd_compatibility + cmd_test_hermeticity docstrings (103 tests pass,
closes a CI-blocking gap caught at consolidation time). The batch is
the **fast follow-through** after CONSOLIDATE-17's
post-release-hardening tail — every dispatch lands cleanly with zero
flagship arcs re-opened. **~15 SHIPPED + ~6 CAPTURED + 0
RECLASSIFICATION + 0 UNBLOCK.**

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1005 — `cmd_smells --min-severity` 3-tier → W547 7-tier canonical widening~~ | W1005 (CONSOLIDATE-18) | Pattern-3a — the `--min-severity` flag was a 3-tier {low/medium/high} closed enum that diverged from the W547 canonical 7-tier `_severity` module. Widened to the full 7-tier vocabulary (`info` / `low` / `medium` / `high` / `critical` / etc.) via the canonical severity_rank lookup. 236 tests pass. Carries the cmd_llm_smells parallel widening as W1005-followup-A. |
| ~~W1005-followup-A — `cmd_llm_smells --min-severity` parallel 7-tier widening~~ | W1005-followup-A (CONSOLIDATE-18) | Follow-up parallel widening on cmd_llm_smells to keep the Pattern-3a vocabulary uniform across both LLM-smells and code-smells surfaces. 2 tests pass. |
| ~~W1007 — `strip_list_payloads` preserves `agent_contract:[]`~~ | W1007 (CONSOLIDATE-18) | Closes the W1006 Pattern-2 disclosure-list audit finding: `strip_list_payloads` was silently dropping `agent_contract:[]` when `--detail` was off. Added `agent_contract` to `_ALWAYS_PRESERVED_LIST_FIELDS`. 89 tests pass. Byte-stable additive on the preservation path. |
| ~~W1060 — SARIF runtime-notifications activation tests on cmd_health + cmd_complexity~~ | W1060 (CONSOLIDATE-18) | NEW test file pinning that `to_sarif(emit_runtime_notifications=True)` actually plumbs through to a `notifications[]` array in the SARIF output for cmd_health + cmd_complexity. 12 tests pass. **cmd_doctor BAIL** — `cmd_doctor` does not emit SARIF (no `--sarif` flag on its CLI surface); the W1046 plumb does not apply there. |
| ~~W1061 — SARIF `ruleConfigurationOverrides[]` on cmd_smells~~ | W1061 (CONSOLIDATE-18) | OASIS SARIF 2.1.0 § 3.51 compliant. Default-off; opt-in via the `--with-overrides` flag or programmatic kwarg. Emits the `overrides[]` array on each `result` whenever the runtime severity/confidence tier differs from the rule's default. 38 tests pass. The dashboard-filtering trio (W1060 + W1061 + W1062) collectively land the OASIS-spec advisory-warning + tag + override plumb that GitHub Code Scanning and other SARIF consumers use for run-level filtering. |
| ~~W1061-followup — extend ruleConfigurationOverrides + new `notificationConfigurationOverrides` to cmd_check_rules + cmd_taint + cmd_vulns~~ | W1061-followup (CONSOLIDATE-18) | Fan-out of W1061's ruleConfigurationOverrides pattern to 3 more SARIF emitters, plus the sibling `notificationConfigurationOverrides[]` array (OASIS 2.1.0 § 3.52). 11 tests pass. |
| ~~W1062 — SARIF `result.properties.tags[]` on taint + vulns + audit-trail-conformance~~ | W1062 (CONSOLIDATE-18) | The dashboard-filtering tag plumb: every result on these 3 emitters now carries a `properties.tags[]` array (e.g. `["security/taint", "owasp/a03"]` for taint, `["security/sca"]` for vulns). 21 tests pass. The 3 emitters were the top tag-shaped consumers per the W1046 audit. |
| ~~W1062-followup — `secrets_to_sarif` tag wiring~~ | W1062-followup (CONSOLIDATE-18) | Extends W1062 tag plumb to the secrets-finding SARIF emitter. 60 tests pass. |
| ~~W1087 — SARIF tag-coverage lint (substitute for long-tail tag wiring)~~ | W1087 (W1062-followup-4) | NEW `tests/test_sarif_tag_coverage.py` (6 tests pass). Two-part contract: (a) PIN — the 13 WIRED emitters (W1062-followup-4 canonical 12 + audit-trail-conformance) MUST call `_derive_finding_tags()` in body; (b) ALLOWLIST drift guard — every `*_to_sarif` / `_*_to_sarif` function in `src/roam/output/sarif.py` + cmd_*.py local emitters (cmd_vulns / cmd_audit_trail_conformance / cmd_supply_chain / cmd_boundary / cmd_check_rules) MUST be in `_WIRED` OR `_TAG_COVERAGE_EXEMPT`. AST-scan shape mirrors `tests/test_w365_tool_metadata_annotations_parity.py`. Implements the W1062-followup-4 recommendation to lint rather than wire the long tail (compound aggregators + thin advisories + invocation-scoped signals). |
| ~~W767 — outputSchema inventory + Wave B roadmap~~ | W767 (CONSOLIDATE-18) | `dev/MCP-OUTPUTSCHEMA-INVENTORY-2026-05-16.md` — full catalogue of all 57 core-preset MCP tools' outputSchema status. 5-wave Wave B roadmap drafted; W1311 + W1312 captured as drive-bys (decorator-normalization + redundant-declaration cleanup). |
| ~~MCP-OUTPUTSCHEMA-EVOLUTION research memo~~ | research-memo (CONSOLIDATE-18) | `dev/MCP-OUTPUTSCHEMA-EVOLUTION-2026-05-16.md` — protocol-evolution memo. Claude Code #25081 status shifted (was BLOCKED on outputSchema spec; now ungated). 3-wave roadmap drafted for the post-#25081 propagation. |
| ~~Wave B1 — roam_impact + roam_preflight specialized outputSchema~~ | Wave-B1 (CONSOLIDATE-18) | First Wave B ship from the W767 roadmap: `_SCHEMA_IMPACT` + `_SCHEMA_PREFLIGHT` specialized outputSchemas wired on roam_impact + roam_preflight MCP wrappers. 18 tests pass. **1 inventory drift caught** during the ship (W767 inventory updated in-flight to reflect the corrected entry). |
| ~~W1275 — harden 3 remaining dogfood-brittle tests in `test_validate_plan.py`~~ | W1275 (CONSOLIDATE-18) | Carry-forward from CONSOLIDATE-16 → -17. The W1273 fix landed 3 brittle assertions cleanly at CONSOLIDATE-16; 3 more surfaced under the post-W1255-IMPL contract. All 3 hardened (cold-start-guard bypass + helper-stub patterns from W1273 reused). 27 tests pass. |
| ~~W1276-fix — `test_impact_auto_logs_not_found_path` no-op verified~~ | W1276-fix (CONSOLIDATE-18) | Carry-forward from CONSOLIDATE-16 → -17. Investigation revealed this was a NO-OP — the fix had already landed during the W1272 Pattern-2c batch (exit-code-0 contract takes the test from xfail to pass without further change). Structural verification only; no code touched. |
| ~~W1277 — restore `auto_log` provenance on `cmd_impact` unresolved-attempt path~~ | W1277 (CONSOLIDATE-18) | Carry-forward from CONSOLIDATE-16 → -17. The W1272 Pattern-2c standardization removed `auto_log` from the `cmd_impact` unresolved branch (the signal-loss risk captured at CONSOLIDATE-16). Restored as an explicit attempt-stamped log entry so `roam replay` still narrates the unresolved-attempt event. 1 cmd + 1 test rename. 8 tests pass. |
| ~~W1278a — `cmd_test_scaffold` Convention-c migration~~ | W1278a (CONSOLIDATE-18) | Carry-forward from W1278-audit at CONSOLIDATE-17 (W1278b + W1278c marked ALIGNED — no migration needed there). `cmd_test_scaffold` was the one of 3 remaining `symbol_not_found` callers that DID want the migration. 1 cmd / ~22 tests pass. Pattern-2c roster now closed at 31/31 effective sites (30 from CONSOLIDATE-15 + 1 from W1278a). |
| ~~W1309 — `cmd_test_scaffold` Pattern-1D file-substring `resolution: "file_substring"` enum disclosure~~ | W1309 (CONSOLIDATE-18) | Drive-by from W1278a. Pattern-1D file-substring fallback path was emitting a success verdict without disclosing the degraded-resolution tier. Added `resolution: "file_substring"` to the enum + `partial_success: true` + degraded verdict. 1 cmd + 4 file edits. 31 tests pass. |
| ~~W1311 — normalize 5 oracle multi-line `@_tool(` decorators~~ | W1311 (CONSOLIDATE-18) | W767 drive-by — 5 oracle MCP wrappers had multi-line `@_tool(...)` decorators that the inventory script couldn't AST-walk cleanly. Normalized to single-line decorators where the kwargs fit. -37 LOC. 131 tests pass. |
| ~~W1312 — drop 3 redundant `output_schema=_ENVELOPE_SCHEMA` + queue 2 for Wave B~~ | W1312 (CONSOLIDATE-18) | W767 drive-by — 5 wrappers declared `output_schema=_ENVELOPE_SCHEMA` which is the @_tool default. 3 DROP (clearly redundant); 2 QUEUE for Wave B (the specialized outputSchema slot will land in a subsequent Wave-B sub-ship). 142 tests pass. |
| ~~SARIF disclosure fix — `cmd_boundary` + `cmd_compatibility` + `cmd_test_hermeticity` docstrings~~ | sarif-disclosure (CONSOLIDATE-18) | Discovered at consolidation time — the 3 docstrings lacked the `--sarif` flag disclosure that the drift-guard test requires. Closed a CI-blocking gap. 103 tests pass. |

## Sealed batch — W1103 → W1175 (2026-05-17, CONSOLIDATE-22)

The CONSOLIDATE-22 pass consolidates the post-CONSOLIDATE-21 wave-arc
spanning the regex-toggle CLI surface (W421 on `history-grep` +
`refs-text`), the taint qualified_only lint wired end-to-end (W489-A
+ W489-A-followup hoists the helper to `src/roam/security/taint_rules_lint.py`
and wires `cmd_cga` alongside `cmd_taint`), the capability-axis
invariant lint (W365 + W365-followup-2 fix two REAL BUGS on
`roam_reset` + `roam_clean` falsely flagged destructive=False + add 3
logical-entailment invariants), the `structured_unknown_filter`
multi-value variant closure (W1083-followup-3 adds the multi-value
sibling + migrates `cmd_math` + `cmd_smells`; **family fully closed**
across single + multi + CLI dispatcher), symmetric envelope emission
(W1100 partial_success on malformed `agent_contract:[]` + W1101
`list_counts: {}` always-emit + W1102 `preserved_list_truncations`
symmetric emission — **symmetric-emission family complete**), and 2
test-rot diagnoses (W844-drive-by-2 sweep + W1084 test refresh).
Strike-throughs on the originating pending rows are preserved below;
this section is the fast-lookup index for the arc-closure
consolidation.

### Closures since CONSOLIDATE-21 (W1103-arc — CONSOLIDATE-22)

The CONSOLIDATE-22 pass folds in the post-CONSOLIDATE-21 wave-arc
that ran in the same session-iteration as the CONSOLIDATE-21 close.
Seven themes carry the batch: (a) **Regex toggle on CLI grep
surfaces** — W421 exposes `-E/--regexp` on `roam history-grep` +
`roam refs-text` (+25 LOC, 53 tests pass). (b) **Taint qualified_only
lint wired into envelope** — W489-A wires the qualified_only lint
into `roam taint --rules-dir` envelope via Option A catch_warnings
capture (+234/-44 LOC + 195 LOC tests, 62 pass); W489-A-followup
hoists the helper to shared `src/roam/security/taint_rules_lint.py`
+ wires `cmd_cga` (+95 shared, -65 cmd_taint, +50 cmd_cga; 107 tests
pass). **W489 family fully closed.** (c) **Capability-axis invariant
lint + 2 real bugs** — W365 wires `_TOOL_METADATA` ↔ ToolAnnotations
parity lint + 3rd-surface capability registry cross-check (2854 tests
pass); **fixed 2 REAL BUGS** (roam_reset + roam_clean falsely flagged
destructive=False at the capability-decorator layer);
W365-followup-2 adds 3 new logical-entailment invariants to the
capability registry (destructive→NOT ai_safe;
deprecated↔maturity; task_required→mcp_expose) — entailment surface
exhausted. (d) **`structured_unknown_filter` multi-value variant
closure** — W1083-followup-3 adds the `structured_unknown_filter_many`
sibling + `cmd_math` + `cmd_smells` migration (+302 helper / +113-53
cmd_math / +193-42 cmd_smells / +336 tests; 366 broader tests pass).
**`structured_unknown_filter` family FULLY CLOSED** (single +
multi + CLI dispatcher). (e) **Symmetric envelope emission family
complete** — W1100 emits `partial_success: true` on malformed
`agent_contract:[]` (+30 LOC; 6 new + 142 broader pass); W1101 emits
symmetric `list_counts: {}` always-emit (+4 effective LOC; 369 pass);
W1102 emits symmetric `preserved_list_truncations` (+10 LOC; 381 pass).
**Symmetric-emission family complete.** (f) **Test-rot diagnoses** —
W844-drive-by-2 sweeps 3 stale README-headline references (186 tests
pass); W1084 diagnoses `test_test_scaffold_unknown_symbol_passes_through`
failure as test rot from the W1278a Pattern-2c migration; test
refreshed; 39/39 pass sequential + parallel. (g) **Pruning +
research** — W507 prunes the dead `'self-hosted'` enum value (0
consumers; 91 tests pass); W1117-followup-4 closes the final 2
placeholder normalizations on `cmd_clones` — **W1117 family fully
closed** (~32 normalizations across 5-wave arc); W1083-RESEARCH drafts
the multi-value helper design memo at
`dev/W1083-RESEARCH-multi-value-2026-05-17.md`. Plus 3 BAIL/SHIPPED
discoveries surfaced during the audit: **W414b** (all 4 target files
already migrated under W414/W346); **W851** (already resolved by
W1296/W1297); **W844** (already shipped in prior wave; drive-by
surfaced README test rot fixed separately). Zero flagship arcs
re-opened. **~17 SHIPPED + 3 BAIL/SHIPPED + 1 RESEARCH MEMO + 2 REAL
BUGS FIXED + 1 FAMILY-CLOSED (structured_unknown_filter) + 1
FAMILY-COMPLETE (symmetric emission) + 1 FAMILY-FULLY-CLOSED
(W1117 placeholders).**

### BACKLOG-drift discipline (5 stale-pending hits this session)

This session surfaced 5 instances of the BACKLOG-drift pattern that
CONSOLIDATE-21 first codified (W1007 / W1008 / W844 / W1100 finding):
**W844** (drive-by surface; already shipped at CONSOLIDATE-20),
**W1007** (already shipped at CONSOLIDATE-18 via `strip_list_payloads`
`agent_contract:[]` preservation), **W1008** (already shipped at
CONSOLIDATE-19 via `list_counts` top-level surfacing), **W851**
(already resolved by W1296/W1297; BAIL/SHIPPED), **W414b** (all 4
target files already migrated under W414/W346; BAIL). All 5 doc-pinned
SHIPPED-PRE-CONSOLIDATE-22 below with retro note. The discipline rule
codified at CONSOLIDATE-21 holds: **before dispatching from a BACKLOG
`[pending]` flag alone, run a fast `grep -n "<W#>" src/roam/` + a
recent-CHANGELOG scan to verify the code state matches the doc state.**

| Item | Shipped in | Notes |
|---|---|---|
| ~~W421 — `-E/--regexp` flag on `roam history-grep` + `roam refs-text`~~ | W421 (CONSOLIDATE-22) | Regex toggle exposed on 2 CLI grep surfaces. +25 LOC; 53 tests pass. |
| ~~W489-A — qualified_only lint wired into `roam taint --rules-dir` envelope (Option A: catch_warnings capture)~~ | W489-A (CONSOLIDATE-22) | The qualified_only lint surfaced as a warning during rule loading; Option A captures it via `catch_warnings` and surfaces it in the envelope. +234/-44 LOC + 195 LOC tests, 62 pass. Shipped pack clean. |
| ~~W489-A-followup — taint_rules_lint helper hoist + cmd_cga wiring~~ | W489-A-followup (CONSOLIDATE-22) | Helper hoisted to shared `src/roam/security/taint_rules_lint.py`; cmd_cga wired alongside cmd_taint. +95 shared, -65 cmd_taint, +50 cmd_cga; 107 tests pass. **W489 family fully closed.** |
| ~~W365 — `_TOOL_METADATA` ↔ ToolAnnotations parity lint + 3rd-surface capability-registry cross-check~~ | W365 (CONSOLIDATE-22) | CI lint cross-check across 3 surfaces (`_TOOL_METADATA` + ToolAnnotations + capability registry). 2854 adjacent tests pass. **Fixed 2 REAL BUGS**: `roam_reset` + `roam_clean` falsely flagged destructive=False at the capability-decorator layer. |
| ~~W365-followup — capability decorator side_effect fix on `roam_reset` + `roam_clean`~~ | W365-followup (CONSOLIDATE-22) | Mechanical follow-up to the 2 REAL BUGS surfaced by W365. +9/-10 LOC + 85 LOC test; 42 pass. |
| ~~W365-followup-2 — 3 new logical-entailment invariants on the capability registry~~ | W365-followup-2 (CONSOLIDATE-22) | Adds (destructive → NOT ai_safe) + (deprecated ↔ maturity) + (task_required → mcp_expose) invariants. +217 LOC test; 46 pass. **Entailment surface exhausted.** |
| ~~W1083-followup-3 — multi-value `structured_unknown_filter_many` sibling + cmd_math + cmd_smells migration~~ | W1083-followup-3 (CONSOLIDATE-22) | Multi-value variant of the structured_unknown_filter helper. +302 helper / +113-53 cmd_math / +193-42 cmd_smells / +336 tests; 366 broader tests pass. **`structured_unknown_filter` family FULLY CLOSED** (single + multi + CLI dispatcher). |
| ~~W1084 — diagnose `test_test_scaffold_unknown_symbol_passes_through` failure~~ | W1084 (CONSOLIDATE-22) | Diagnosis (a) test rot from W1278a Pattern-2c migration; test refreshed; 39/39 pass sequential + parallel. W-number-collision target separate from the W1084 cmd_ai_readiness / cmd_fitness arc at CONSOLIDATE-20. |
| ~~W507 — prune dead `'self-hosted'` enum value~~ | W507 (CONSOLIDATE-22) | 0 consumers; safe to drop. 91 tests pass. |
| ~~W1117-followup-4 — final 2 placeholder normalizations on cmd_clones~~ | W1117-followup-4 (CONSOLIDATE-22) | **W1117 family fully closed** — ~32 normalizations across 5-wave arc (W1117 + W1117-followup-2 + W1117-followup-3 + W1117-followup-4 + the root). |
| ~~W1100 — `partial_success: true` on malformed `agent_contract:[]`~~ | W1100 (CONSOLIDATE-22) | +30 LOC; 6 new + 142 broader pass; W1102 candidate found NONE. Pairs with the W1100 CONSOLIDATE-21 `schema_violations[]` envelope-root ship — this ship plumbs the `partial_success: true` flag on the same path. |
| ~~W1101 — symmetric `list_counts: {}` always-emit~~ | W1101 (CONSOLIDATE-22) | +4 effective LOC; 369 pass; W1102 + W1103 captured. Pairs with the W1101 CONSOLIDATE-21 ship — this ship verifies the symmetry on the `list_counts: {}` always-emit case. |
| ~~W1102 — symmetric `preserved_list_truncations` emission~~ | W1102 (CONSOLIDATE-22) | +10 LOC; 381 pass; **symmetric-emission family complete.** Closes the W1102 carry-forward from CONSOLIDATE-21. |
| ~~W1083-RESEARCH — multi-value helper design memo~~ | research-memo (CONSOLIDATE-22) | `dev/W1083-RESEARCH-multi-value-2026-05-17.md` — drafts the multi-value helper design rationale for W1083-followup-3 + the broader `structured_unknown_filter` family closure. |
| ~~W414b BAIL/SHIPPED-PRE-CONSOLIDATE-22~~ | W414b (CONSOLIDATE-22 retro) | All 4 target files already migrated under W414/W346; BAIL with rationale. Doc-pinned SHIPPED-PRE-CONSOLIDATE-22 for the BACKLOG-drift discipline finding. |
| ~~W851 BAIL/SHIPPED-PRE-CONSOLIDATE-22~~ | W851 (CONSOLIDATE-22 retro) | Already resolved by W1296/W1297; BAIL/SHIPPED. Doc-pinned SHIPPED-PRE-CONSOLIDATE-22 for the BACKLOG-drift discipline finding. |
| ~~W844 SHIPPED-PRE-CONSOLIDATE-22~~ | W844 (CONSOLIDATE-22 retro) | Already shipped in prior wave (drive-by surfaced README test rot fixed separately — see W844-drive-by-2). Doc-pinned for the BACKLOG-drift discipline finding. |
| ~~W844-drive-by-2 — 3 stale README-headline references swept across tests/docs~~ | W844-drive-by-2 (CONSOLIDATE-22) | Headline drift sweep continuation — 3 stale references across tests + docs. 186 tests pass. |
| ~~W1007 SHIPPED-PRE-CONSOLIDATE-22~~ | W1007 (CONSOLIDATE-22 retro) | Already shipped at CONSOLIDATE-18 via `strip_list_payloads` `agent_contract:[]` preservation; previously doc-pinned at CONSOLIDATE-21. Re-doc-pinned for the BACKLOG-drift discipline finding (5 stale-pending hits this session). |
| ~~W1008 SHIPPED-PRE-CONSOLIDATE-22~~ | W1008 (CONSOLIDATE-22 retro) | Already shipped at CONSOLIDATE-19 via `list_counts` top-level surfacing in `strip_list_payloads`; previously doc-pinned at CONSOLIDATE-21. Re-doc-pinned for the BACKLOG-drift discipline finding (5 stale-pending hits this session). |

### Pending after CONSOLIDATE-22 (queue for next session)

The CONSOLIDATE-22 pass closes the **7-theme batch** — the
post-CONSOLIDATE-21 wave-arc lands with regex CLI toggle (W421),
taint qualified_only lint family closure (W489-A + W489-A-followup),
capability-axis invariant lint + 2 real bugs fixed (W365 +
W365-followup + W365-followup-2 entailment surface exhausted),
`structured_unknown_filter` family FULLY CLOSED (W1083-followup-3
multi-value sibling), symmetric-emission family complete (W1100 +
W1101 + W1102), 2 test-rot diagnoses (W844-drive-by-2 + W1084), and 3
BAIL/SHIPPED-PRE-CONSOLIDATE-22 hits (W414b + W851 + W844) plus
W1117-followup-4 (placeholder-family fully closed) and W507
(dead-enum prune) and the W1083-RESEARCH memo. **~17 SHIPPED + 3
BAIL/SHIPPED + 1 RESEARCH MEMO + 2 REAL BUGS FIXED.** Pattern-3
family terminal milestones: structured_unknown_filter FULLY CLOSED;
symmetric-emission COMPLETE; W1117 placeholders FULLY CLOSED;
W489 qualified_only FULLY CLOSED; entailment surface EXHAUSTED.
**5 stale-pending hits this session** — discipline rule re-affirmed.

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **W1103 — `schema_violations` top-level placement design call** (carry-forward from CONSOLIDATE-21; the `schema_violations[]` array landed at envelope-root but the `summary.schema_violations_count` sibling field surfaces the count duplicate at `summary.partial_success_count` — design Q on which slot wins). | architectural decision | 30 min decision |
| **W1083-followup-4 candidate** — NONE found this session. The `structured_unknown_filter` family is FULLY CLOSED across single + multi + CLI dispatcher; no additional callsites surface as candidates. Captured as NULL-CANDIDATE for next session audit. | — | — |
| **Working-tree drift note** — commits remain banned per user directive; ~150+ files dirty across `src/` and `templates/`. Carry-forward as session-state note; no action item. | working tree | session-state |
| Plus all CONSOLIDATE-21 carry-forwards (W1083-followup-2 cli.py:848 difflib n alignment + W851 investigation re-triage + Wave C2+ + W363 re-scope + W846 + W1253 + W1083 Phase 3 ergonomic + W1054-W1056 + W1044 + W1251 + cmd_boundary + cmd_compatibility W-number collision) unchanged. | various | various |

## Sealed batch — W1067 → W1102 (2026-05-17)

The CONSOLIDATE-21 pass consolidates the long W1067 → W1102 wave-arc
spanning Pattern-1D helper Phase 2/3 propagation, the W1142 cap-hit
disclosure family, Pattern 3a severity widening on cmd_smells +
cmd_adversarial, placeholder normalization sweep (W1117-followup-2/-3
across 22 commands), symmetric envelope emission (W1100 schema_violations
+ W1101 list_counts), OSCAL authority_refs projection (W350 drive-by
closes evidence-question Q2 coverage), W414d git_repo + python_project
module-scope BAIL-BOTH, and W844-drive-by README hero test rot fix plus
the W844-drive-by-2 sweep of 3 stale headline references. Strike-throughs
on the originating pending rows are preserved below; this section is
the fast-lookup index for the arc-closure consolidation.

### Closures since CONSOLIDATE-20 (W1067-W1102 arc — CONSOLIDATE-21)

The CONSOLIDATE-21 pass folds in the W1067 → W1102 wave-arc that ran
across multiple session-iterations between CONSOLIDATE-19 and -21.
Seven themes carry the batch: (a) **Pattern-1D helper Phase 2/3
propagation** — 7 callsites migrated across cmd_search / cmd_endpoints /
cmd_test_scaffold / cmd_workflow / cmd_explain_command (5 adopt
`structured_unknown_filter`; 2 adopt `to_summary_payload`); the
W1083-followup pass added cmd_workflow + cmd_explain_command on top
of the original Phase 2 batch, and W1083-followup's cmd_math:250
KEEP-knobs decision pins the Phase 3 opt-out site. (b) **W1142 cap-hit
disclosure family closure** — 7 commands sealed across two follow-up
ships: W1142-followup-A wires cmd_clones + cmd_debt + cmd_recommend +
cmd_test_impact; W1142-followup-B wires cmd_supply_chain +
cmd_agent_score + cmd_runs; cmd_search_semantic BAILED (the candidate
8th site does not carry a cap-shaped truncation path). Canonical
em-dash text uniform across all 7. (c) **Pattern 3a severity widening
on cmd_smells + cmd_adversarial** — W1005 + W1005-followup-B widen the
two remaining 3-tier severity surfaces to the W547 7-token canonical
(info / low / medium / high / critical / blocker / unknown). **Pattern
3a severity family fully closed** — the W1005 arc that opened at
CONSOLIDATE-18 with cmd_smells primary + cmd_llm_smells followup-A now
sweeps cmd_adversarial as the third (and final) high-signal site.
(d) **Placeholder normalization sweep** — W1117-followup-2 normalizes
11 commands' square-bracket `[VALUE]` placeholder convention to the
canonical angle-bracket `<value>` style; W1117-followup-3 sweeps 7 more;
the W1117 root carry-forward sweeps the final 4 sites. 22 commands
end-to-end. (e) **Symmetric envelope emission** — W1100 emits
`schema_violations[]` at envelope-root on malformed `agent_contract:[]`
(closes the Pattern-2 partial-success disclosure gap that the W1100
original ship at CONSOLIDATE-19's metavar-alignment work surfaced as a
sibling); W1101 emits `list_counts: {}` (empty dict, not omitted) on
zero-truncation paths so callers always have the same envelope shape;
W1102 (preserved_list_truncations symmetry) carries forward as
in-flight. (f) **OSCAL authority_refs projection (W350 drive-by)** —
the evidence-doctor + pr-replay path now projects `authority_refs[]`
as OSCAL Assessment Results EXAMINE observations; closes
evidence-question Q2 ("what authority existed?") for the OSCAL
projection axis. (g) **Permit-vs-lease asymmetry documented in
CLAUDE.md (W1071)** — the W1067 NOT-A-BUG verdict gets a CLAUDE.md
sub-section codifying why permits load expired entries (audit-
completeness) and leases filter them (live conflict-resolution).
Plus 2 drive-bys: **W844-drive-by** (README hero test rot — the
`pytest tests/test_basic.py` example was rotted by the W405 shallow
git default; refreshed) and **W844-drive-by-2** (3 stale headline
references swept across README + landing-page + docs). And **W414d**
(git_repo + python_project module-scope BAIL-BOTH — the two probes
were named in the W414c followup audit but are structurally inapplicable
at module scope; BAIL-BOTH captured with rationale). Zero flagship
arcs re-opened. **~30 SHIPPED + ~5 CAPTURED + 1 BAIL (cmd_search_semantic)
+ 2 DRIVE-BY + 1 NOT-A-BUG (W1067).**

### BACKLOG-drift discipline (W1007 / W1008 / W844 / W1100 finding)

Recurring pattern surfaced in the consolidation: BACKLOG `[pending]`
flags drift from on-disk shipped state. **W1007** + **W1008** were
re-marked SHIPPED at CONSOLIDATE-18 / -19 yet still surfaced as
`[pending]` rows in later pending-blocks; **W844** had already shipped
per CONSOLIDATE-20 yet the drive-bys (README hero test rot, 3 stale
headline references) were not captured in the pending block; **W1100**
ship at CONSOLIDATE-19 had a sibling pending row that didn't get
struck through. **Discipline rule going forward**: before dispatching
from a BACKLOG `[pending]` flag alone, run a fast `grep -n "<W#>"
src/roam/` + a recent-CHANGELOG scan to verify the code state matches
the doc state. The "Re-run before declaring a fix" pattern from
CLAUDE.md (W978 / W851 / W1005) is the structurally identical
discipline applied to test-failure triage.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1067 — Permit-expiry investigation (NOT-A-BUG, audit-completeness design)~~ | W1067 (W1079-CONSOLIDATE, doc-pinned CONSOLIDATE-21) | Re-pinned at consolidation time; permit-vs-lease asymmetry now documented in CLAUDE.md "Permit-vs-lease expiry-filtering asymmetry (W1067)" sub-section. |
| ~~W1068-W1083 — Pattern-1D Phase 2 + 3 helper migrations (5 commands)~~ | W1068-W1083 (CONSOLIDATE-21) | 5 callsites adopt `structured_unknown_filter` across cmd_search + cmd_endpoints + cmd_test_scaffold + cmd_workflow + cmd_explain_command. |
| ~~W1083-followup — Phase 3 `to_summary_payload` adopters (2 commands) + cmd_math:250 KEEP-knobs~~ | W1083-followup (CONSOLIDATE-21) | 2 callsites (cmd_workflow + cmd_explain_command) adopt `to_summary_payload`; cmd_math:250 KEEP-knobs decision pins the Phase 3 opt-out site. |
| ~~W1100 — `schema_violations[]` envelope-root on malformed `agent_contract:[]`~~ | W1100 (CONSOLIDATE-21) | Pattern-2 partial-success disclosure gap closure. Malformed `agent_contract:[]` now surfaces a `schema_violations[]` array at envelope-root + `partial_success: true`. |
| ~~W1101 — Symmetric `list_counts: {}` emission on zero-truncation paths~~ | W1101 (CONSOLIDATE-21) | `list_counts` was previously omitted on no-truncation paths; now always-emitted as `{}` for envelope-shape symmetry. |
| ~~W1117-followup-2 — Placeholder normalization sweep (11 commands)~~ | W1117-followup-2 (CONSOLIDATE-21) | 11 commands normalized from `[VALUE]` square-bracket to `<value>` angle-bracket placeholder convention. |
| ~~W1117-followup-3 — Placeholder normalization sweep (7 commands)~~ | W1117-followup-3 (CONSOLIDATE-21) | 7 more commands swept (the second-tier follow-up to W1117-followup-2). |
| ~~W1117 (root) — Final 4 placeholder normalizations~~ | W1117 (CONSOLIDATE-21) | The carry-forward root sweep closes the final 4 sites; 22 commands end-to-end across W1117 + W1117-followup-2 + W1117-followup-3. |
| ~~W1142-followup-A — Cap-hit disclosure on 4 commands (cmd_clones + cmd_debt + cmd_recommend + cmd_test_impact)~~ | W1142-followup-A (CONSOLIDATE-21) | Cap-hit disclosure wired with canonical em-dash text on 4 commands. |
| ~~W1142-followup-B — Cap-hit disclosure on 3 commands (cmd_supply_chain + cmd_agent_score + cmd_runs)~~ | W1142-followup-B (CONSOLIDATE-21) | 3 more commands wired (the second-tier follow-up to W1142-followup-A). Total: 7 commands sealed. **cmd_search_semantic BAILED** (candidate 8th site has no cap-shaped truncation path). |
| ~~W350 + drive-by — OSCAL projection of authority_refs as EXAMINE observations~~ | W350 (CONSOLIDATE-21) | Drive-by from evidence-doctor + pr-replay path. `authority_refs[]` now projects as OSCAL Assessment Results EXAMINE observations. **Closes evidence-question Q2** for the OSCAL projection axis. |
| ~~W414d — git_repo + python_project module-scope BAIL-BOTH~~ | W414d (CONSOLIDATE-21) | Both probes named in the W414c followup audit are structurally inapplicable at module scope; BAIL-BOTH captured with rationale. |
| ~~W844-drive-by — README hero test rot fix~~ | W844-drive-by (CONSOLIDATE-21) | The `pytest tests/test_basic.py` README hero example was rotted by the W405 shallow-git default; refreshed. |
| ~~W844-drive-by-2 — 3 stale headline references swept (README + landing-page + docs)~~ | W844-drive-by-2 (CONSOLIDATE-21) | Headline drift sweep across 3 surfaces. |
| ~~W1005 — `cmd_smells` Pattern 3a severity widening (W547 7-token canonical)~~ | W1005 (CONSOLIDATE-21, re-shipped on widened scope) | The W1005 widening at CONSOLIDATE-18 covered the `--min-severity` flag surface; this re-ship covers the remaining severity-tier emission paths. |
| ~~W1005-followup-B — `cmd_adversarial` Pattern 3a severity widening (W547 7-token canonical)~~ | W1005-followup-B (CONSOLIDATE-21) | **Pattern 3a severity family fully closed.** The W1005 arc that opened at CONSOLIDATE-18 with cmd_smells primary + cmd_llm_smells followup-A now sweeps cmd_adversarial as the third (and final) high-signal site. |
| ~~W1007 (BACKLOG-drift correction)~~ | W1007 (CONSOLIDATE-18, doc-pinned CONSOLIDATE-21) | Already shipped at CONSOLIDATE-18 via `strip_list_payloads` `agent_contract:[]` preservation; re-marked here to seal the BACKLOG-drift instance. |
| ~~W1008 (BACKLOG-drift correction)~~ | W1008 (CONSOLIDATE-19, doc-pinned CONSOLIDATE-21) | Already shipped at CONSOLIDATE-19 via `list_counts` top-level surfacing in `strip_list_payloads`; re-marked here to seal the BACKLOG-drift instance. |
| ~~W844 (BACKLOG-drift correction)~~ | W844 (CONSOLIDATE-20, doc-pinned CONSOLIDATE-21) | Already shipped at CONSOLIDATE-20 via `_EXPECTED_CARD_SHA256` auto-rotate; re-marked here to seal the BACKLOG-drift instance. |

### Pending after CONSOLIDATE-21 (queue for next session)

The CONSOLIDATE-21 pass closes the **7-theme batch** — the long
W1067 → W1102 wave-arc lands cleanly with Pattern-1D Phase 2/3
propagation across 7 callsites, the W1142 cap-hit disclosure family
closure across 7 commands, Pattern 3a severity widening on cmd_smells
+ cmd_adversarial (family TERMINAL), the W1117 placeholder
normalization sweep across 22 commands, symmetric envelope emission
on W1100 + W1101 (W1102 in-flight), the W350 OSCAL authority_refs
projection (Q2 coverage closure), and the W1071 permit-vs-lease
asymmetry CLAUDE.md sub-section. **~30 SHIPPED + ~5 CAPTURED + 1
BAIL + 2 DRIVE-BY + 1 NOT-A-BUG.** Pattern 3a severity family
TERMINAL. **BACKLOG-drift discipline codified** (W1007 / W1008 / W844
/ W1100 finding — see sub-section above).

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **W1102 — `preserved_list_truncations` symmetry emission** (carry-forward from this batch; W1101 wired the `list_counts: {}` case but the sibling `preserved_list_truncations` envelope-root field still asymmetric — emit `{}` on zero-truncation paths for shape uniformity). | `src/roam/output/formatter.py` + per-emitter audit | 1-2h |
| **W1103 — `schema_violations` top-level placement design call** (carry-forward from W1100 ship; the `schema_violations[]` array landed at envelope-root but the `summary.schema_violations_count` sibling field surfaces the count duplicate at `summary.partial_success_count` — design Q on which slot wins). | architectural decision | 30 min decision |
| **W1083-followup-2 — `cli.py:848` difflib `n` parameter alignment** (carry-forward; the `to_summary_payload` Phase 3 work uses `difflib.get_close_matches(... n=3)` but `cli.py:848` still passes the default `n=6`. Mechanical alignment.). | `src/roam/cli.py` | 15 min |
| **W851 investigation — `test_w596_confidence_level_rank_round_trip` cross-worker warnings leak** (carry-forward from CONSOLIDATE-20 BAIL; in-flight per W986 discipline rule). | `tests/test_w596_*.py` + xdist worker isolation audit | 2-3h |

### Closures since CONSOLIDATE-19 (W1086-arc + Wave-B-TERMINAL + W478 + Pattern-1A-family — CONSOLIDATE-20)

The CONSOLIDATE-20 pass folds in ~20 completions from the longer
batch that follows the CONSOLIDATE-19 Wave-B TERMINAL dispatch.
Four themes carry the batch: (a) **SARIF dashboard family TERMINAL
at 12 wired emitters + W1087 lint substitute** — W1062-followup-3
(clones + smells + over_fetch; 11 tests pass) and W1062-followup-4
(n1 + missing_index + orphan_imports; 12 tests pass) close the
high-signal SARIF tag-wiring fan-out at 12 emitters; W1087's
tag-coverage lint then closes the long-tail by catalogue (13 WIRED
+ 26 EXEMPT = 39 emitters pinned, 6 tests pass) — the
W1062-followup wave + W1087 lint substitute together TERMINAL the
SARIF dashboard family per the W1062-followup-4 substitute-rather-
than-wire recommendation; **1 NO-OP captured during the fan-out**
(`pr_risk_to_sarif` per W1147/W1148 deliberate omission).
(b) **MCP outputSchema 13-tool Wave B TERMINAL carry-forward
documentation + Wave C1 implementation kickoff** — Wave C1 lands
the first compat-profile env-vars (`ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA`
+ `ROAM_MCP_COMPAT_STRICT`; 7 tests + 188 broader tests pass) plus
a drive-by sidecar hoist (audit-metadata `_meta` block escaped the
fastmcp gate); the MCP-COMPAT-PROFILE-ROADMAP research memo drafts
the broader Wave-C compat-profile-emit + `roam mcp doctor` probe
surface that Wave C1 implements at the env-var tier.
(c) **Pattern-2 + Pattern-1A empty-state arc — 8 detectors + 2
hard-cap commands sealed** — W805-followup-bundle migrates the 5
remaining detectors from the W805 follow-up roster
(cmd_vibe_check + cmd_fingerprint + cmd_fan + cmd_dark_matter +
cmd_conventions) on top of W805's original 3, taking detector
empty-state Pattern-2 coverage to 8/8 effective sites; **2 real
Pattern-1A hard-cap disclosure fixes shipped** (W1085 cmd_fingerprint
+ W1086 cmd_cut — mirror commands, mirror fix templates), plus 1
real probe-breaking fix (W1084 cmd_ai_readiness
denominator-clamp). 25 + 19 + 4 + 10 tests pass cumulatively.
(d) **3 research memos drafted** — `dev/MCP-OUTPUTSCHEMA-EVOLUTION-2026-05-16.md`
(carry-forward from -18), the new
`dev/MCP-COMPAT-PROFILE-ROADMAP-2026-05-17.md` (Wave-C planning
memo), and the carry-forward
`dev/DETECTOR-FP-RATE-METHODOLOGY-2026-05-16.md` (12 sources cited).
Plus eight stand-alone polish items: **W365** wires the
`_TOOL_METADATA` ↔ ToolAnnotations CI lint cross-check (10 tests
pass; finding: **ToolAnnotations FULLY wired today** — W363
materially less critical than the -18 audit feared); **W459**
normalizes 17 MCP wrappers to the `description=` kwarg (2895 tests
pass); **W478** closes 4 SQLite fd-leak paths in the `_make_db()`
test helpers (135 tests pass); **W844** wires the auto-rotate
`_EXPECTED_CARD_SHA256` in `dev/build_readme_counts.py` (10 tests
pass; drive-by closes the W1308 manual-sync gap); **W847** + **W759**
land the cmd_preflight UPPER-case-scope clarification and the
4-site envelope-slot UPPER-case sweep (W762 cmd_preflight allowlist
now empty; 13 tests pass); **W986** codifies the CLAUDE.md "First
hypothesis" test-failure-triage discipline rule (W978 + W851 + W1005
incidents cited); **W462** pins the landing-page tool-count
drift-guard test (11 integers asserted; 1 pass); **W1088** the
cmd_preflight `_SEVERITY_ORDER` lookup-miss belt-and-suspenders fix
(64 tests pass); **W1038** lands the `extract_typed` YAML-loader
helper + `validator` kwarg follow-up (4 callsites migrated; 11 +
447 tests pass; clarification: cmd_alerts:961 `== 0` clause is NOT
dead code). Plus **W851 BAIL** —
`test_w596_confidence_level_rank_round_trip` is pre-existing and
not reproducible in isolation (likely a cross-worker
`warnings.resetwarnings()` leak under xdist; captured for re-triage
not re-dispatched). Zero flagship arcs re-opened. **~17 SHIPPED + ~6
CAPTURED + 1 NO-OP + 1 BAIL + 0 RECLASSIFICATION + 1 UNBLOCK (W365
unblocks the W363 audit follow-up).** Wave B TERMINAL + SARIF
dashboard family TERMINAL.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1062-followup-3 — SARIF tag wiring on clones + smells + over_fetch~~ | W1062-followup-3 (CONSOLIDATE-20) | Third fan-out of W1062's tag plumb to 3 more SARIF emitters (`clones_to_sarif` + `smells_to_sarif` + `over_fetch_to_sarif`). 11 tests pass. `pr_risk_to_sarif` found N/A during the fan-out (deliberate W1147/W1148 omission — pr-risk is invocation-scoped and does not carry tag-shaped consumer use cases). |
| ~~W1062-followup-4 — SARIF tag wiring on n1 + missing_index + orphan_imports~~ | W1062-followup-4 (CONSOLIDATE-20) | Fourth fan-out — 3 more SARIF emitters (`n1_to_sarif` + `missing_index_to_sarif` + `orphan_imports_to_sarif`). 12 tests pass. The high-signal SARIF tag-wiring trio + sextet (W1062 + W1062-followup + W1062-followup-2 + W1062-followup-3 + W1062-followup-4) now lands across 12 emitters end-to-end. **W1087 captured** as the substitute-rather-than-wire ship for the long tail (compound aggregators + thin advisories + invocation-scoped signals). |
| ~~W1087 — SARIF tag-coverage lint (substitute for long-tail wiring)~~ | W1087 (CONSOLIDATE-20) | NEW `tests/test_sarif_tag_coverage.py` (6 tests pass). Two-part contract: (a) PIN — the 13 WIRED emitters (W1062-followup-4 canonical 12 + audit-trail-conformance) MUST call `_derive_finding_tags()` in body; (b) ALLOWLIST drift guard — every `*_to_sarif` / `_*_to_sarif` function in `src/roam/output/sarif.py` + cmd_*.py local emitters MUST be in `_WIRED` OR `_TAG_COVERAGE_EXEMPT`. 13 WIRED + 26 EXEMPT = 39 emitters catalogued. AST-scan shape mirrors `tests/test_w365_tool_metadata_annotations_parity.py`. Closes the SARIF dashboard family per the W1062-followup-4 substitute-recommendation. SARIF dashboard family TERMINAL. |
| ~~Wave C1 — MCP compat env-vars (`ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA` + `ROAM_MCP_COMPAT_STRICT`)~~ | Wave-C1 (CONSOLIDATE-20) | First Wave C ship from the MCP-COMPAT-PROFILE-ROADMAP memo: two env-vars that let MCP clients opt into outputSchema-stripping or strict-validation modes for cross-client compatibility shaping. 7 focused tests + 188 broader MCP tests pass. **Drive-by sidecar hoist** during the ship — audit-metadata `_meta` block had escaped the fastmcp gate, hoisted to the canonical wrapper path. Pairs with the MCP-COMPAT-PROFILE-ROADMAP planning memo that drafts the Wave-C compat-profile-emit + `roam mcp doctor` probe surface. |
| ~~MCP-COMPAT-PROFILE-ROADMAP research memo~~ | research-memo (CONSOLIDATE-20) | `dev/MCP-COMPAT-PROFILE-ROADMAP-2026-05-17.md` — Wave-C planning memo. Drafts the compat-profile-emit + `roam mcp doctor` probe surface for client-side capability negotiation. Pairs with the MCP-OUTPUTSCHEMA-EVOLUTION (CONSOLIDATE-18) + DETECTOR-FP-RATE-METHODOLOGY (CONSOLIDATE-19) research-memo cluster. |
| ~~W805-followup-bundle — Pattern-2 detector empty-state migration (5 detectors)~~ | W805-followup-bundle (CONSOLIDATE-20) | Carry-forward from W805 at CONSOLIDATE-19 (the 5 captured A/B/C/D/E follow-ups). Bundled migration across cmd_vibe_check + cmd_fingerprint + cmd_fan + cmd_dark_matter + cmd_conventions — each now emits explicit `partial_success: true` on empty-state branches. 25 tests pass. Pattern-2 detector empty-state coverage now 8/8 effective sites (3 from W805 + 5 from this bundle). |
| ~~W1085 — `cmd_fingerprint` Pattern-1A hard-cap disclosure~~ | W1085 (CONSOLIDATE-20) | Mirror fix to the W1085 (CONSOLIDATE-17) `cmd_fitness` SARIF advisory-plumb arc, but on a DIFFERENT W-number-collision target — the Pattern-1A hard-cap disclosure on `cmd_fingerprint`. Empty-state hard-cap was emitting a success verdict without disclosing the cap; added explicit hard-cap disclosure + `partial_success: true` + degraded verdict. 19 tests pass. Captured **W1086** drive-by during the ship. |
| ~~W1086 — `cmd_cut` Pattern-1A hard-cap disclosure~~ | W1086 (CONSOLIDATE-20) | Drive-by from W1085 — `cmd_cut` had the same Pattern-1A hard-cap shape (empty-state success verdict without disclosing the cap). Mirror fix to W1085 — explicit hard-cap disclosure + `partial_success: true` + degraded verdict. 4 tests pass. |
| ~~W1084 — `cmd_ai_readiness` denominator-clamp probe-breaking fix~~ | W1084 (CONSOLIDATE-20) | Mirror to the W1084 (CONSOLIDATE-17) `cmd_fitness` SARIF advisory-plumb arc, but on a DIFFERENT W-number-collision target — a denominator-clamp probe-breaking fix on `cmd_ai_readiness`. Probe path divided by zero when the denominator collapsed to 0; clamped to a minimum-1 floor with explicit insufficient-data disclosure. 10 tests pass. |
| ~~W365 — CI lint cross-check `_TOOL_METADATA` ↔ ToolAnnotations~~ | W365 (CONSOLIDATE-20) | NEW `tests/test_w365_tool_metadata_annotations_parity.py` (10 tests pass). AST-walks `_TOOL_METADATA` + ToolAnnotations together; any wrapper that declares one MUST declare the other (or be in an allowlist). **Finding: ToolAnnotations FULLY wired today** — the W363 follow-up the -18 audit feared is materially less critical than estimated; the lint pins the parity rather than the wider rollout. |
| ~~W459 — normalize 17 MCP wrappers to `description=` kwarg~~ | W459 (CONSOLIDATE-20) | Carry-forward from W449/W458/W459/W460 batch (W466). 17 MCP wrappers had positional-arg `description` calls that diverged from the canonical `description=` kwarg style. Normalized for AST-walk consistency. 2895 tests pass. |
| ~~W478 — 4 SQLite fd-leak fixes in `_make_db()` test helpers~~ | W478 (CONSOLIDATE-20) | 4 test-helper `_make_db()` callsites left SQLite file descriptors leaking on exception paths. Wrapped each in a `try/finally` + explicit `conn.close()`. 135 tests pass. |
| ~~W844 — auto-rotate `_EXPECTED_CARD_SHA256` in `dev/build_readme_counts.py`~~ | W844 (CONSOLIDATE-20) | The `_EXPECTED_CARD_SHA256` constant in `dev/build_readme_counts.py` required manual updates whenever the MCP server card changed. Wired the auto-rotate path so the constant updates in lockstep with the card hash. 10 tests pass. **Drive-by closes the W1308 manual-sync gap** (the LF-normalization + SEP-1649 mirror sync was carrying an implicit manual step). |
| ~~W847 — `cmd_preflight` UPPER-case scope clarification~~ | W847 (CONSOLIDATE-20) | The W759 envelope-slot UPPER-case sweep had an unclear scope on `cmd_preflight` — clarified that only 4 of the original 30 sites required migration (86% scope reduction). Sets up the W759 ship cleanly. |
| ~~W759 — 4 envelope-slot UPPER-case sites cleaned~~ | W759 (CONSOLIDATE-20) | Carry-forward from the W759 envelope-slot UPPER-case sweep (post-W847 scope reduction). 4 sites migrated; W762 cmd_preflight allowlist now empty. 13 tests pass. Captured **W1088** drive-by during the ship (the lookup-miss belt-and-suspenders fix). |
| ~~W986 — CLAUDE.md "First hypothesis" test-failure-triage discipline rule~~ | W986 (CONSOLIDATE-20) | Carry-forward from W978 follow-up. Codifies the "first hypothesis to check when `test_*_stale_*` / `test_*_history_*` fails: did W405 truncate the fixture's expected commit?" rule. W978 + W851 + W1005 incidents cited as concrete worked examples. Mirrors the "Verify the cycle before hedging" + "Never N/A without running it" discipline-rule pattern. |
| ~~W462 — landing-page tool-count drift-guard test~~ | W462 (CONSOLIDATE-20) | Carry-forward from W461/W462/W463 batch (W454 qualified_only drive-by). NEW drift-guard test pinning the 11 tool-count integers across the landing-page surface (handles the cross-page drift between header / body / footer / docs / about copies). 1 pass. |
| ~~W1088 — `cmd_preflight` `_SEVERITY_ORDER` lookup-miss fix~~ | W1088 (CONSOLIDATE-20) | Drive-by from W759. Lookup-miss on `_SEVERITY_ORDER` was failing silently when callers passed UPPER-case tier names against the lower-case canonical map. Belt-and-suspenders: lower-case canonical + UPPER-case aliases + `.lower()` at lookup. 64 tests pass. Mirrors W1086 cmd_cut Pattern-1A hard-cap disclosure but on a DIFFERENT W-number-collision target — Cranot user-decision item carries forward. |
| ~~W1038 — `extract_typed` YAML-loader helper + `validator` kwarg follow-up~~ | W1038 (CONSOLIDATE-20) | Carry-forward from W1019d drive-by (deferred at -18 + -19). NEW `extract_typed` helper in `src/roam/yaml_loader.py`; 4 callsites migrated; 11 + 447 tests pass. `validator` kwarg added in follow-up; 1 site migrated. **Drive-by clarification: cmd_alerts:961 `== 0` clause is NOT dead code** (the W918 warnings_out path threads through it on the empty-thresholds edge). |
| ~~W851 BAIL — `test_w596_confidence_level_rank_round_trip` pre-existing, not reproducible~~ | W851-BAIL (CONSOLIDATE-20) | Investigated the W596 confidence_level rank round-trip test failure. **Not reproducible in isolation** — likely a cross-worker `warnings.resetwarnings()` leak under xdist (one worker resets warnings filters mid-run, breaking the `pytest.warns()` contract on the next worker). Captured for re-triage at next session; W986 "First hypothesis" rule already codifies the test-failure-triage discipline that surfaced the BAIL. |

### Pending after CONSOLIDATE-20 (queue for next session)

The CONSOLIDATE-20 pass closes the **4-theme batch** — ~20
completions covering the SARIF dashboard family TERMINAL (12 wired
emitters + W1087 lint substitute for the long tail; 39 emitters
catalogued end-to-end), the MCP outputSchema 13-tool Wave B
TERMINAL carry-forward documentation + Wave C1 implementation
kickoff (env-vars + sidecar hoist drive-by + the
MCP-COMPAT-PROFILE-ROADMAP planning memo), the Pattern-2 +
Pattern-1A empty-state arc closure (8 detectors + 2 hard-cap
commands sealed across W805-followup-bundle + W1085 + W1086 + the
W1084 denominator-clamp probe-breaking fix), and 3 research memos
(MCP outputSchema evolution + Wave-C compat-profile roadmap + FP-
rate methodology). Plus 8 stand-alone polish items (W365 + W459 +
W478 + W844 + W847 + W759 + W986 + W462 + W1088 + W1038) + 1 BAIL
(W851). Zero flagship arcs re-opened. **~17 SHIPPED + ~6 CAPTURED
+ 1 NO-OP + 1 BAIL + 0 RECLASSIFICATION + 1 UNBLOCK.** Wave B
TERMINAL + SARIF dashboard family TERMINAL — the two terminal
arcs that opened with Wave B1 (CONSOLIDATE-18) and W1062 + W1062-
followup (CONSOLIDATE-18) close cleanly at CONSOLIDATE-20.

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **Wave C2+ — full compat-profile emit + `roam mcp doctor` probe surface** (carry-forward from Wave C1 ship + MCP-COMPAT-PROFILE-ROADMAP memo — Wave C1 lands the env-var tier; Wave C2+ lands the `roam mcp doctor` probe surface that consumes the emitted profile for client-side capability negotiation). | new MCP cluster | ~1-2 sessions |
| **W851 BAIL re-triage — `test_w596_confidence_level_rank_round_trip` cross-worker warnings leak** (carry-forward from CONSOLIDATE-20 BAIL; pre-existing failure, not reproducible in isolation; likely a cross-worker `warnings.resetwarnings()` leak under xdist). | `tests/test_w596_*.py` + xdist worker isolation audit | 2-3h |
| **W363 audit follow-up (now less critical post-W365)** — W365 found ToolAnnotations FULLY wired today, so the W363 state-mutating tool hardening from the W340 audit is materially less critical than the -18 audit feared. Re-scope at next session. | `src/roam/mcp_server.py` + new tests | re-scope |
| **W846 — Claude Code tooling desync (W844 follow-up)** — carry-forward from W844 ship; the `dev/build_readme_counts.py` auto-rotate landed but the Claude Code tooling desync surface (cross-session card-hash sync) carries forward as a separate item. | session-level decision + tooling audit | 1-2h |
| **W1253 — `pr-bundle emit` packet-stale architectural decision** (carry-forward from CONSOLIDATE-16 → -17 → -18 → -19 → -20; UNBLOCKED by W1255-IMPL). | architectural decision + 1d impl | 1-2h decision + 1d impl |
| **W1054 / W1055 / W1056 — Release-pipeline hardening P1 bundle** (carry-forward from W1049-RESEARCH; PEP 740 wheel attestations + concurrency group + publish.yml split + SBOM→wheel SHA binding; **user-decision-gated** before next dispatch). | `.github/workflows/publish.yml` + pyproject | 1-2 sessions |
| **W1083 — `structured_unknown_filter` Phase 3 ergonomic `to_summary_payload()` fragment method** (carry-forward from W1081 drive-by; **deferred** at -18, -19, and -20 — Phase 2 finished at W1082, Phase 3 is ergonomic-only). | `src/roam/commands/structured_unknown_filter.py` | ~30 min |
| **W1044 — internal `ExceptionGroup` at hairy-command boundary** (carry-forward from W1039 deferred; **marginal** — only fires on multi-error paths in compound commands). | `src/roam/commands/compound_dispatch.py` | 1-2h |
| **cmd_boundary + cmd_compatibility W-number collision (USER DECISION)** — carry-forward from CONSOLIDATE-18 → -19; the W805 real-bug fix on cmd_boundary lands without picking a canonical W#. Also surfaces fresh in CONSOLIDATE-20 across W1085/W1086/W1088 — the W-number collisions between the Pattern-1A hard-cap fixes (cmd_fingerprint + cmd_cut + cmd_preflight) and the older W1085-W1088 (cmd_fitness + cmd_doctor + CI hardening) family are deliberate session-local renames pending Cranot canonical W# assignment before next release. | session-level decision | quick |
| **W1251 — 45-site state-vocab bulk migration** (carry-forward from CONSOLIDATE-14 → -15 → -16 → -17 → -18 → -19 → -20; **heavy**). Consumer-side adoption of the W1235 `_STATE_FAMILY_ALIASES` registry. | per-cmd edit | ~1-2 sessions |

### Closures since CONSOLIDATE-18 (Wave-B + W794 + W1028 + W805 — CONSOLIDATE-19)

The CONSOLIDATE-19 pass folds in ~18 completions from the longer
batch that follows the CONSOLIDATE-18 fast-follow-through dispatch.
Three themes carry the batch: (a) **Wave B TERMINAL — 13 tools
specialized across 5 sub-ships** — Wave B2 (`_SCHEMA_HEALTH` +
`_SCHEMA_UNDERSTAND` on roam_health + roam_understand; 25 tests pass),
Wave B3 (bundled `_SCHEMA_ORACLE` across 6 oracle wrappers; 37 tests
pass), Wave B4 (`_SCHEMA_TIMELINE` + `_SCHEMA_TEST_IMPACT`; 7 tests
pass), Wave B5-partial (`_SCHEMA_AUDIT_TRAIL_VERIFY` +
`_SCHEMA_DIAGNOSE`; 5 tests pass), and Wave B5b TERMINAL
(`_SCHEMA_FETCH_HANDLE` + `_SCHEMA_VALIDATE_PLAN` +
`_SCHEMA_AUDIT_TRAIL_CONFORMANCE`; 39 tests pass). The W767 roadmap's
5-wave Wave B propagation now lands on 13 MCP tools end-to-end —
~113 envelope-validation tests pass cumulatively across the ship.
(b) **MCP server card SEP-2127 readiness (W794)** — `icons[]` field
wired across all 4 .well-known path variants (`mcp-server-card.json`
+ `.well-known/mcp-server-card` + SEP-1649 mirror + SEP-2127 mirror);
22 tests pass. Carries the W792 multi-path-variant work to a clean
SEP-2127-ready posture. (c) **Pattern-2 empty-state audit arc
closure** — W805 audited the 3 remaining detector empty-state
branches missing `partial_success`: cmd_test_hermeticity +
cmd_llm_smells + cmd_boundary all migrated (13 tests pass); 5
followups captured (W805-followup-A/B/C/D/E). **1 real bug surfaced
during the audit** — cmd_boundary had SQL execution outside the
`with open_db` block (resource-leak-on-error); fixed inline. Plus
two stand-alone polish items: **W1061-followup-2** extracts the
`runtime_filter_disclosure()` shared helper from 4 SARIF callers
(-36 LOC consolidation, 17 tests pass) and the
**DETECTOR-FP-RATE-METHODOLOGY** research memo at
`dev/DETECTOR-FP-RATE-METHODOLOGY-2026-05-16.md` (674 words, 12
sources cited, methodology for measuring detector false-positive
rates). Also **W1008** surfaces `list_counts` top-level in
`strip_list_payloads` (234 tests pass) — carry-forward from the
CONSOLIDATE-17 → -18 disclosure-list watch-list. Zero flagship arcs
re-opened. **~10 SHIPPED + ~8 CAPTURED + 1 REAL BUG + 0
RECLASSIFICATION + 0 UNBLOCK.** Wave B TERMINAL.

| Item | Shipped in | Notes |
|---|---|---|
| ~~Wave B2 — `_SCHEMA_HEALTH` + `_SCHEMA_UNDERSTAND` MCP outputSchema specialization~~ | Wave-B2 (CONSOLIDATE-19) | Second Wave B ship from the W767 roadmap: specialized outputSchemas on roam_health + roam_understand wrappers. 25 tests pass. Continues the Wave B propagation across the catalogued 57 core-preset MCP tools. |
| ~~Wave B3 — bundled `_SCHEMA_ORACLE` across 6 oracle wrappers~~ | Wave-B3 (CONSOLIDATE-19) | Third Wave B ship. The 6 oracle MCP wrappers share a single bundled `_SCHEMA_ORACLE` outputSchema (rather than 6 separate per-oracle slot schemas) since the oracle envelope shape is uniform across the family. 37 tests pass. Pairs with the W1311 oracle-decorator normalization from CONSOLIDATE-18. |
| ~~W1008 — surface `list_counts` top-level in `strip_list_payloads`~~ | W1008 (CONSOLIDATE-19) | Carry-forward from W1000 drive-by at CONSOLIDATE-17. The `strip_list_payloads` helper was discarding the per-field `list_counts` dict when `--detail` was off; now surfaces it top-level (sized 1 dict, not the per-field list contents) so callers can still see how many items were stripped. 234 tests pass. |
| ~~Wave B4 — `_SCHEMA_TIMELINE` + `_SCHEMA_TEST_IMPACT` outputSchema specialization~~ | Wave-B4 (CONSOLIDATE-19) | Fourth Wave B ship. Specialized outputSchemas on roam_timeline + roam_test_impact wrappers. 7 tests pass. |
| ~~Wave B5-partial — `_SCHEMA_AUDIT_TRAIL_VERIFY` + `_SCHEMA_DIAGNOSE` outputSchema specialization~~ | Wave-B5-partial (CONSOLIDATE-19) | Fifth Wave B sub-ship (partial — the remaining 3 wrappers in Wave B5 ship as Wave B5b below). Specialized outputSchemas on roam_audit_trail_verify + roam_diagnose wrappers. 5 tests pass. |
| ~~W1061-followup-2 — extract `runtime_filter_disclosure()` shared helper from 4 SARIF callers~~ | W1061-followup-2 (CONSOLIDATE-19) | Consolidation pass on the W1061 + W1061-followup ruleConfigurationOverrides / notificationConfigurationOverrides plumb. The 4 callers (cmd_smells + cmd_check_rules + cmd_taint + cmd_vulns) had near-identical disclosure-shaping code; extracted to `runtime_filter_disclosure()` shared helper. -36 LOC consolidation. 17 tests pass. |
| ~~DETECTOR-FP-RATE-METHODOLOGY research memo~~ | research-memo (CONSOLIDATE-19) | `dev/DETECTOR-FP-RATE-METHODOLOGY-2026-05-16.md` — 674 words, 12 sources cited. Methodology for measuring detector false-positive rates beyond the W470 + W480 + W797 BigCloneBench audit work. Drafts the framework for FP-rate benchmarks on the 94-detector inventory (W850). |
| ~~Wave B5b TERMINAL — `_SCHEMA_FETCH_HANDLE` + `_SCHEMA_VALIDATE_PLAN` + `_SCHEMA_AUDIT_TRAIL_CONFORMANCE` outputSchema specialization~~ | Wave-B5b (CONSOLIDATE-19) | **Wave B TERMINAL.** Sixth and final Wave B sub-ship: specialized outputSchemas on the last 3 wrappers in the W767 roadmap (fetch_handle + validate_plan + audit_trail_conformance). 39 tests pass. The 5-wave Wave B propagation (B1 + B2 + B3 + B4 + B5-partial + B5b) collectively lands specialized outputSchemas on 13 MCP tools end-to-end — the W767 roadmap is now closed. Parallel to the W1255 architectural-decision-and-implementation arc that CONSOLIDATE-16 carried. |
| ~~W794 — MCP server card `icons[]` field wired across 4 .well-known path variants~~ | W794 (CONSOLIDATE-19) | W765-RESEARCH wave #3 (SEP-2127 readiness). The `icons[]` field landed across `mcp-server-card.json` + `.well-known/mcp-server-card` + SEP-1649 mirror + SEP-2127 mirror (4 paths). 22 tests pass. Carries W792 (3 .well-known path variants) + W793 (display_name → title rename) to a clean SEP-2127-ready posture. W795 (`_meta` privacy posture stanza) remains BLOCKED on SEP-2127 merge. |
| ~~W1028 — `_ALWAYS_PRESERVED_LIST_FIELDS` expansion audit (4 DEFER + drift-guard test)~~ | W1028 (CONSOLIDATE-19) | Carry-forward from W1006 drive-by at CONSOLIDATE-17 → -18. The 4-field watch-list audited; 4 candidates marked DEFER (envelope-shape contracts inherit from existing preservation rules); drift-guard test added pinning the current `_ALWAYS_PRESERVED_LIST_FIELDS` set. 162 tests pass. |
| ~~W805 — Pattern-2 empty-state audit (3 detectors migrated + 5 followups captured + 1 real bug)~~ | W805 (CONSOLIDATE-19) | Carry-forward from W802 drive-by. Audited the 3 remaining detector empty-state branches missing `partial_success` from the W802/W804/W813/W814/W817/W818 sweep. cmd_test_hermeticity + cmd_llm_smells + cmd_boundary all migrated. 13 tests pass. **1 real bug found** — cmd_boundary had a SQL block outside `with open_db` (resource-leak-on-error path); fixed inline. 5 follow-ups captured (W805-followup-A/B/C/D/E) for the surface-level disclosure consistency sweep across the remaining detector empty-state branches. |

### Pending after CONSOLIDATE-19 (queue for next session)

The CONSOLIDATE-19 pass closes the **3-theme batch** — ~18
completions covering Wave B TERMINAL (13 MCP tools specialized
across 5 sub-ships ending at Wave B5b — the W767 outputSchema
roadmap closes), the MCP server card SEP-2127 readiness ship (W794
icons[] across 4 .well-known paths), and the Pattern-2 empty-state
audit arc closure (W805 — 3 detectors migrated, 5 followups
captured, 1 real bug fixed in cmd_boundary). Plus two stand-alone
polish items (W1061-followup-2 SARIF helper consolidation + the
DETECTOR-FP-RATE-METHODOLOGY research memo) and the W1008
carry-forward disclosure-list polish. Zero flagship arcs re-opened.
**~10 SHIPPED + ~8 CAPTURED + 1 REAL BUG + 0 RECLASSIFICATION + 0
UNBLOCK.** Wave B TERMINAL — the W767 5-wave outputSchema roadmap
that kicked off at CONSOLIDATE-18 closes cleanly at CONSOLIDATE-19.

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **W805-followup-A/B/C/D/E — Pattern-2 empty-state disclosure-consistency sweep** (carry-forward from W805; 5 surface-level disclosure-consistency candidates across the remaining detector empty-state branches, captured at consolidation time but may already be partially folded by the bundled wave running in parallel — re-triage at next session). | per-cmd edit across the remaining detector empty-state branches | ~30-60 min per followup |
| **Wave C — compatibility profile + `roam mcp doctor` probe** (next major MCP roadmap milestone after Wave B TERMINAL — drafts the compatibility-profile-emit + the `roam mcp doctor` probe surface that consumes it for client-side capability negotiation). | new MCP cluster | ~1-2 sessions |
| **W1054 / W1055 / W1056 — Release-pipeline hardening P1 bundle** (carry-forward from W1049-RESEARCH; PEP 740 wheel attestations + concurrency group + publish.yml split + SBOM→wheel SHA binding; **user-decision-gated** before next dispatch). | `.github/workflows/publish.yml` + pyproject | 1-2 sessions |
| **W1083 — `structured_unknown_filter` Phase 3 ergonomic `to_summary_payload()` fragment method** (carry-forward from W1081 drive-by; **deferred** at -18 and -19 — Phase 2 finished at W1082, Phase 3 is ergonomic-only). | `src/roam/commands/structured_unknown_filter.py` | ~30 min |
| **W1044 — internal `ExceptionGroup` at hairy-command boundary** (carry-forward from W1039 deferred; **marginal** — only fires on multi-error paths in compound commands). | `src/roam/commands/compound_dispatch.py` | 1-2h |
| **W1038 — `_extract_typed` helper for "load → check type → warn-or-default" pattern** (carry-forward from W1019d drive-by; ~6 callsites would benefit). | `src/roam/yaml_loader.py` | 1-2h |
| **cmd_boundary + cmd_compatibility W-number collision (USER DECISION)** — carry-forward from CONSOLIDATE-18; the W805 real-bug fix on cmd_boundary lands without picking a canonical W#. Cranot to decide canonical W# assignment before next release. | session-level decision | quick |
| **W1253 — `pr-bundle emit` packet-stale architectural decision** (carry-forward from CONSOLIDATE-16 → -17 → -18 → -19; UNBLOCKED by W1255-IMPL). | architectural decision + 1d impl | 1-2h decision + 1d impl |
| **W1251 — 45-site state-vocab bulk migration** (carry-forward from CONSOLIDATE-14 → -15 → -16 → -17 → -18 → -19; **heavy**). Consumer-side adoption of the W1235 `_STATE_FAMILY_ALIASES` registry. | per-cmd edit | ~1-2 sessions |

### Pending after CONSOLIDATE-18 (queue for next session)

The CONSOLIDATE-18 pass closes the **5-theme fast-follow-through
batch** — ~15 completions covering Pattern-2c carry-forward closures
(W1275 / W1276-fix / W1277 / W1278a / W1309), the SARIF
dashboard-filtering trio (W1060 + W1061 + W1062 + 2 followups), the
MCP outputSchema roadmap kickoff (W767 inventory + Wave B1 first ship
+ W1311 + W1312 drive-bys + the EVOLUTION research memo), the
Pattern-1D file-substring disclosure ship (W1309), and the Pattern-3a
severity widening (W1005 + W1005-followup-A + W1007). Zero flagship
arcs re-opened. **~15 SHIPPED + ~6 CAPTURED + 0 RECLASSIFICATION + 0
UNBLOCK.** The Pattern-2c roster carry-forward chain
CONSOLIDATE-16 → -17 → -18 is now closed — no W1278/W1275/W1277
items carry into CONSOLIDATE-19.

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **W1253 — `pr-bundle emit` packet-stale architectural decision** (carry-forward from CONSOLIDATE-16 → -17 → -18; UNBLOCKED by W1255-IMPL). | architectural decision + 1d impl | 1-2h decision + 1d impl |
| ~~W1277 — restore `auto_log` provenance on `cmd_impact` unresolved-attempt path~~ — SHIPPED CONSOLIDATE-18. | `src/roam/commands/cmd_impact.py` + replay narration substrate | DONE |
| **W1083 — `structured_unknown_filter` Phase 3 ergonomic `to_summary_payload()` fragment method** (carry-forward from W1081 drive-by; Phase 2 finished at W1082). | `src/roam/commands/structured_unknown_filter.py` | ~30 min |
| **W1054 / W1055 / W1056 — Release-pipeline hardening P1 bundle** (carry-forward from W1049-RESEARCH; PEP 740 wheel attestations + concurrency group + publish.yml split + SBOM→wheel SHA binding). | `.github/workflows/publish.yml` + pyproject | 1-2 sessions |
| **W1028 — `_ALWAYS_PRESERVED_LIST_FIELDS` 4-field watch-list** (carry-forward from CONSOLIDATE-17 → -18; W1007 closed 1 of 4 via `agent_contract` — 3 candidate fields remain on the watch-list). | `src/roam/output/formatter.py` | ~30 min audit + ~30 min add |
| **W1038 — `_extract_typed` helper for "load → check type → warn-or-default" pattern** (carry-forward from W1019d drive-by; ~6 callsites would benefit). | `src/roam/yaml_loader.py` | 1-2h |
| **W1044 — internal `ExceptionGroup` at hairy-command boundary** (carry-forward from W1039 deferred; marginal — only fires on multi-error paths in compound commands). | `src/roam/commands/compound_dispatch.py` | 1-2h |
| **W1308+followups — MCP card LF / SEP-1649 mirror long-tail** (carry-forward from CONSOLIDATE-17 — W1308 LF-normalized the 3 cards; follow-ups for additional `.well-known` mirror paths surface as upstream SEP-2127 lands). | landing-page `.well-known/` mirrors | per-mirror |
| **cmd_boundary + cmd_compatibility W-number collision (USER DECISION)** — both new commands are tracked as in-flight under the same W-number range; Cranot to decide canonical W# assignment before next release. The SARIF disclosure fix shipped under "sarif-disclosure" tag rather than picking a W#. | session-level decision | quick |
| **W1251 — 45-site state-vocab bulk migration** (carry-forward from CONSOLIDATE-14 → -15 → -16 → -17 → -18; **heavy**). Consumer-side adoption of the W1235 `_STATE_FAMILY_ALIASES` registry. | per-cmd edit | ~1-2 sessions |

### Pending after CONSOLIDATE-17 (queue for next session)

The CONSOLIDATE-17 pass ships the **post-v13.2-release hardening
batch** — ~25 completions covering init UX fixes, SARIF advisory plumb,
CGA edge-bundle stability, MCP card v13.2 sync, CI hardening, and the
W1287 non-hermetic test detector. Zero flagship arcs re-opened.
**~20 SHIPPED + ~5 CAPTURED + 0 RECLASSIFICATION + 0 UNBLOCK.**

**Captured for next session (W1275 / W1276-fix / W1277 / W1278 carry-forward from CONSOLIDATE-16):**

| Item | Where | Effort |
|---|---|---|
| **W1275 — harden 3 remaining dogfood-brittle tests in `test_validate_plan.py`** (carry-forward from CONSOLIDATE-16; partial W1273 follow-up). | `tests/test_validate_plan.py` | ~30 min |
| **W1276-fix — `test_impact_auto_logs_not_found_path` test-needs-update** (carry-forward from CONSOLIDATE-16; W1272-expected-failing). | `tests/test_cmd_impact_auto_logs.py` | ~15 min |
| **W1277 — restore replay-narration provenance for unresolved-path attempts** (carry-forward from CONSOLIDATE-16; `auto_log` removed from `cmd_impact` during W1272 standardization). | `src/roam/commands/cmd_impact.py` + replay narration substrate | 1-2h |
| **W1278 — audit 3 remaining `symbol_not_found` callers** (carry-forward from CONSOLIDATE-16; W1272 touched 8 of 11 known callers). **W1278b + W1278c marked ALIGNED, no migration** per the W1278 audit just completed — the remaining 3 callers already emit Convention-c-compatible shapes; no bulk-migration needed. | `cmd_test_scaffold` / `cmd_plan_refactor` / `cmd_guard` audit | DONE (ALIGNED) |
| **W1253 — `pr-bundle emit` packet-stale architectural decision** (carry-forward from CONSOLIDATE-16; UNBLOCKED by W1255-IMPL). | architectural decision + 1d impl | 1-2h decision + 1d impl |
| **W1251 — 45-site state-vocab bulk migration** (carry-forward from CONSOLIDATE-14 → -15 → -16 → -17; **heavy**). Consumer-side adoption of the W1235 `_STATE_FAMILY_ALIASES` registry. | per-cmd edit | ~1-2 sessions |

### Closures since CONSOLIDATE-14 (W1245-batch-1 / W1245-batch-2 / W1245-batch-3 / W1245-batch-4 / W1250 / W1256 / W1262 / W1265 / W1266 / W1267-audit / W1268-audit / W1269 / W1270 / W1271-audit / W1274 — CONSOLIDATE-15)

The CONSOLIDATE-15 pass folds in ~20 completions from the W1245 →
W1274 stretch — the largest cumulative batch since the W1175-RESEARCH
mid-points. **The MAJOR load-bearing milestone**: **Pattern-2c
propagation arc COMPLETE at 30/30 sites.** Together with the SARIF
SHIP/SKIP-disclosure 196 → 0 arc that reached terminal at
CONSOLIDATE-14, both flagship propagation arcs of the autonomous-loop
era are now structurally complete inside two consecutive consolidation
passes. **Six themes**: (a) **Pattern-2c bulk completion** — W1245
batches 1-4 ship 20 cmd_*.py adoptions (3 + 5 + 5 + 7) plus 2 BAIL
on W1267-audit false positives (`cmd_hotspots` / `cmd_smells` — no
real `find_symbol` callsite); combined with the Wave-1 quartet from
CONSOLIDATE-14 (W1242/W1243/W1244/W1248) and the W324 cmd_annotate
origin template, the arc closes at 30/30 real Pattern-2c sites
disclosure-covered. (b) **Pattern-2c family extensions** — W1250
helper docstring expansion (W324 template precedent + W1241
substrate-first sequencing + collision pattern documented); W1270
helper reserved-key warning surface (Pattern-2 silent-drop fix at
substrate; first real-world use in W1245-batch-4 `cmd_safe_zones`);
W1268-audit surfaced 5-way unresolved-path divergence captured as
**W1272**; W1271-audit surfaced `test_validate_plan` dogfood-
brittleness captured as **W1273**; W1273-fix shipped as **W1274**
(`test_visualize` stale-assertion fix); W1265 docstring at
`vsa.py:133` (W1264 follow-up). (c) **Evidence/W210 consumer-side
wire-up** — W1262 closes the W1254 in-flight dispatch from
CONSOLIDATE-14 (`roam doctor` and `roam diff` surface "stale
evidence" banner consuming W1234 producer); W1266 hoists
`evidence_completeness_compat` helpers (-180 LOC duplicate + 205 LOC
shared module — drive-by from W1262 wiring). The agentic-assurance
substrate now spans all three axes structurally complete: producer
(W1234) + consumer (W1262) + attestation (W37x CGA + W377 permit
collector). (d) **Per-kind version stamps** — W1256 `cmd_vibe_check`
per-pattern version stamps (10 AI-rot patterns); W1269 `cmd_smells`
per-kind version stamps (7 patterns wired, closing 7 of the W870
17-detector composite-fallback gap). (e) **Audit closures** —
W1267-audit corrected the W1233-audit roster from 34 sites to 30
real true-positives. (f) **CONSOLIDATE pause** — natural stopping
point after W1245-batch-4; **zero in-flight dispatches** at
consolidation time (first empty in-flight queue at consolidation
time in 8+ consolidations). **20 SHIPPED + 4 CAPTURED + 3 AUDIT
CLOSURES**. Strike-throughs preserved on originating pending lines;
fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1245-batch-1 — Pattern-2c Wave 2 batch-1 (5 sites)~~ | W1245-batch-1 (CONSOLIDATE-15) | 3 SHIP (`cmd_dead` + `cmd_safe_delete` + `cmd_closure`) + 2 BAIL (`cmd_hotspots` / `cmd_smells` — W1233-audit false positives, no real `find_symbol` callsite; surfaced for W1267-audit). |
| ~~W1245-batch-2 — Pattern-2c Wave 2 batch-2 (5 sites)~~ | W1245-batch-2 (CONSOLIDATE-15) | 5 SHIP (`cmd_symbol` + `cmd_hover` + `cmd_pytest_fixtures` + `cmd_plan` + `cmd_context`). |
| ~~W1245-batch-3 — Pattern-2c Wave 2 batch-3 (5 sites)~~ | W1245-batch-3 (CONSOLIDATE-15) | 5 SHIP (`cmd_relate` + `cmd_why` + `cmd_visualize` + `cmd_invariants` + `cmd_testmap`). |
| ~~W1245-batch-4 — Pattern-2c Wave 2 batch-4 (7 sites)~~ | W1245-batch-4 (CONSOLIDATE-15) | 7 SHIP (`cmd_affected_tests` + `cmd_guard` + `cmd_metrics` + `cmd_plan_refactor` + `cmd_pr_bundle` + `cmd_safe_zones` + `cmd_test_scaffold`). **Largest single Pattern-2c dispatch** of the arc. First real-world use of W1270 reserved-key warning in `cmd_safe_zones`. Completes the 30/30 terminal. |
| ~~W1250 — `resolution_disclosure()` helper docstring expansion~~ | W1250 (CONSOLIDATE-15) | W324 cmd_annotate template precedent + W1241 substrate-first sequencing + collision-pattern documented. |
| ~~W1270 — helper reserved-key warning surface~~ | W1270 (CONSOLIDATE-15) | Pattern-2 silent-drop fix at substrate. Helper now warns when a downstream caller tries to override a reserved envelope key. First real-world use in W1245-batch-4 `cmd_safe_zones`. |
| ~~W1268-audit — 5-way unresolved-path divergence audit~~ | W1268-audit (CONSOLIDATE-15) → W1272 captured | Pattern-2c consumer family hand-rolled 5 different unresolved-path shapes. Captured as W1272 (bundled standardization, 10 cmd / ~150 LOC). |
| ~~W1271-audit — `test_validate_plan` dogfood-brittleness~~ | W1271-audit (CONSOLIDATE-15) → W1273 captured | Assertion couples to unstable transient hash. Captured as W1273. |
| ~~W1273-fix → W1274~~ | W1274 (CONSOLIDATE-15) | `test_visualize` stale-assertion fix (~10-20 LOC). Full audit → capture → fix arc within consolidation window. |
| ~~W1265 — vsa.py:133 docstring~~ | W1265 (CONSOLIDATE-15) | W1264 follow-up; surfaced during W1262 stale-banner wiring. Documents the load-bearing role of the `vsa.py` helper in the W1262 consumer path. |
| ~~W1262 — evidence_stale consumer wire-up~~ | W1262 (CONSOLIDATE-15) | Closes the W1254 in-flight dispatch from CONSOLIDATE-14. `roam doctor` and `roam diff` surface "stale evidence" banner consuming the W1234 evidence_stale producer's `evidence_stale: true` field. |
| ~~W1266 — `evidence_completeness_compat` shared module~~ | W1266 (CONSOLIDATE-15) | Drive-by from W1262 stale-banner wiring. -180 LOC duplicate helpers + 205 LOC shared module across `cmd_doctor` / `cmd_diff` / `cmd_critique` / 3 sibling sites. Substrate-first sequencing applied to evidence completeness checks. |
| ~~W1256 — `cmd_vibe_check` per-pattern version stamps~~ | W1256 (CONSOLIDATE-15) | 10 AI-rot patterns each carry `pattern_version`. Agents can detect pattern-signal-shape changes without forcing the whole detector version to bump. Byte-stable additive. |
| ~~W1269 — `cmd_smells` per-kind version stamps~~ | W1269 (CONSOLIDATE-15) | 7 patterns wired; closes 7 of the 17 W870-vintage composite-fallback gap. Byte-stable additive. |
| ~~W1267-audit — Pattern-2c roster correction~~ | W1267-audit (CONSOLIDATE-15) | W1233-audit's 38-site original roster → 34 → 30 real true-positives. Surfaced 2 W1233-audit false positives (`cmd_hotspots` / `cmd_smells` — rule-engine path, not symbol-resolver path; no degraded resolution to disclose). |

### Pending after CONSOLIDATE-15 (queue for next session)

The CONSOLIDATE-15 pass closes the **Pattern-2c 30/30 propagation
arc TERMINAL**; ships the Pattern-2c family extensions (W1250 +
W1270); lands the W1262 evidence_stale consumer + W1266 shared
completeness module; lands the W1256 + W1269 per-kind version
stamps. **20 SHIPPED + 4 CAPTURED + 3 AUDIT CLOSURES.** Both
flagship propagation arcs of the autonomous-loop era are now
structurally complete (Pattern-3b 196 → 0 at CONSOLIDATE-14;
Pattern-2c 30/30 at CONSOLIDATE-15). The agentic-assurance substrate
spans all three axes structurally complete (producer + consumer +
attestation). **Zero in-flight dispatches at consolidation time.**

**Captured for next session:**

| Item | Where | Effort |
|---|---|---|
| **W1272 — Pattern-2c unresolved-path bundled standardization** (captured this session from W1268-audit). 10 cmd_*.py hand-rolled 5 different unresolved-path shapes; standardize at the helper level. | per-cmd edit | ~150 LOC bundled |
| **W1273 — `test_validate_plan` dogfood-brittleness** (captured this session from W1271-audit; partial fix shipped via W1274 on `test_visualize`; the `test_validate_plan` proper case remains). | `tests/test_validate_plan.py` | ~10-20 LOC |
| **W1255 — `pr-bundle emit` packet-stale architectural decision** (BAIL+capture carry-forward from CONSOLIDATE-14 via W1253). **Hash-stamp canonical paths.** Decide whether the no-upstream-packet case should emit a synthetic stub packet or stay silent. | architectural decision | 1-2h decision + 1d impl |
| **W1251 — 45-site state-vocab bulk migration** (carry-forward from CONSOLIDATE-14; **heavy**). Post-W1257 audit landing — consumer-side adoption of the W1235 `_STATE_FAMILY_ALIASES` registry. | per-cmd edit | ~1-2 sessions |

**Outstanding from older rosters** (carry-forwards from CONSOLIDATE-9
through CONSOLIDATE-14 that the post-CONSOLIDATE-14 batch did not
re-touch):

| Item | Where | Effort |
|---|---|---|
| **W1256 — vibe-check per-pattern confidence-tier tuning** (carry-forward from CONSOLIDATE-14 — DISTINCT from the W1256 version-stamp ship here; the confidence-tier tuning ticket using the same number for FP-rate-driven tier adjustments remains captured). | `src/roam/commands/cmd_llm_smells.py` | 1-2d |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since CONSOLIDATE-13 (W1232 / W1234 / W1235 / W1239 / W1240 / W1242 / W1243 / W1244 / W1247 / W1248 / W1249 / W1252 / W1258 / W1259 / Wave-16 — CONSOLIDATE-14)

The CONSOLIDATE-14 pass folds in ~15 completions from the W1225 →
W1259 stretch (the W1226-W1248 sub-stretch already recorded by
CONSOLIDATE-13 stays strike-through-locked; CONSOLIDATE-14 adds the
post-W1248 completions). **Six themes**: (a) **Pattern-2c family
enablement — Wave 1 quartet shipped** — W1242 `cmd_impact` + W1243
`cmd_preflight` + W1244 `cmd_diagnose` + W1248 `cmd_trace` adopt the
W1241 `resolution_disclosure()` helper at the `find_symbol()` /
`find_symbol_id()` call sites; envelopes carry `resolution` (closed-
enum: symbol / file / fuzzy / unresolved) + `partial_success` flag.
(b) **Pattern-2c substrate refactor — W1249** — `find_symbol`
tier-stamping hoisted into the substrate helper, eliminating ~100 LOC
of duplicate `_detect_resolution_tier` helpers across the four Wave-1
flagships; **~3× LOC simplification per Wave-2 consumer** unlocking
W1245-batch-1. (c) **Wave 16 SKIP-disclosure — `_KNOWN_MISSING` 17 →
0; the 196 → 0 propagation arc is now terminal** — 17 docstrings
landed across the Bucket B long-tail (`cmd_debt` + `cmd_entry_points`
+ `cmd_guard` + `cmd_map` + `cmd_metrics` + `cmd_path_coverage` +
`cmd_patterns` + `cmd_plan_refactor` + `cmd_pytest_fixtures` +
`cmd_risk` + `cmd_safe_delete` + `cmd_safe_zones` +
`cmd_simulate_departure` + `cmd_suggest_refactoring` + `cmd_testmap`
+ `cmd_why_slow` + `cmd_ws`); 196 commands disclosure-covered = 179
SKIP-disclosure + 17 SARIF SHIP across CONSOLIDATEs 4 → 14. (d)
**Evidence/W210 wire-up** — W1234 `evidence_stale` producer (W210
packet-layer Pattern-2 variant-2f); W1254 consumer in flight; W1253
BAIL surfaced W1255 architectural prerequisite (no upstream packet to
mark stale → captured). (e) **Substrate registries** — W1235
`_STATE_FAMILY_ALIASES` registry (state-vocab normalization for
Pattern-2g closed-vocab sites) + W1247 module-local SARIF convention
doc-pass + W1252 findings-registry decision doc-pass + W1258+W1259
detector-count refresh (16 → 26 detectors + `emit_finding(conn,
record)` API name canonicalized in CLAUDE.md). (f) **SARIF rule rename
W1232** — `flag-constant-default` → `flag-suspect` per W1226 SHIP
scope-discipline follow-up. Plus drift-guard hygiene follow-ups (W1239
+ W1240 clean) + the **773-LOC research memo**
(`dev/DETECTOR-FP-RATE-BENCHMARKS-2026-05-16.md`) covering false-
positive rates across the 26 emitting detectors (companion to the
884-LOC PATTERN-2-EVOLUTION memo shipped CONSOLIDATE-13). **4 SHIPPED
(W1242 + W1243 + W1244 + W1248) + 1 SUBSTRATE-REFACTOR (W1249) + 1
SUBSTRATE (W1235) + 1 WAVE-CLOSURE (Wave 16) + 1 PRODUCER (W1234) + 1
BAIL+CAPTURE (W1253 → W1255) + 1 RULE-RENAME (W1232) + 2 DRIFT-GUARD
FOLLOW-UPS (W1239/W1240) + 4 DOC-DRIFT (W1247/W1252/W1258/W1259) + 1
RESEARCH MEMO**. Strike-throughs preserved on originating pending
lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1242 — `cmd_impact` Pattern-2c adoption~~ | W1242 (CONSOLIDATE-14) | First Wave-1 consumer of W1241 `resolution_disclosure()`. `find_symbol()` callsite stamped with `resolution` (closed-enum) + `partial_success` on non-`symbol` resolution. Byte-stable for `symbol`-tier (exact-match) envelopes. |
| ~~W1243 — `cmd_preflight` Pattern-2c adoption~~ | W1243 (CONSOLIDATE-14) | Second Wave-1 consumer. Gate-before-edit pathway — the highest-leverage Pattern-2c surface for agent-call sequencing (preflight is the canonical pre-edit gate). |
| ~~W1244 — `cmd_diagnose` Pattern-2c adoption~~ | W1244 (CONSOLIDATE-14) | Third Wave-1 consumer. Root-cause ranking pathway. |
| ~~W1248 — `cmd_trace.find_symbol_id` Pattern-2c adoption~~ | W1248 (CONSOLIDATE-14) | Wave-3 promoted to Wave-1 (originally W1246-audit captured for Wave 3, but the `find_symbol_id` shape mapped cleanly onto the helper's `find_symbol` tier-detection scaffold). Closes the four highest-traffic Pattern-2c sites in a single Wave-1 quartet. |
| ~~W1249 — Pattern-2c substrate refactor~~ | W1249 (CONSOLIDATE-14) | `find_symbol` tier-stamping hoisted into `resolution_disclosure()` helper at `src/roam/output/formatter.py:1263`. Eliminated ~100 LOC of duplicate `_detect_resolution_tier` helpers across cmd_impact / cmd_preflight / cmd_diagnose / cmd_trace. **~3× LOC simplification per Wave-2 consumer**. Substrate-first sequencing precedent (W1018 YAML loader + W1241 helper). |
| ~~Wave 16 SKIP-disclosure — 17 docstrings, `_KNOWN_MISSING` 17 → 0~~ | Wave-16-impl (CONSOLIDATE-14) | 17 SKIP-disclosure docstrings landed across the Bucket B long-tail (`cmd_debt` + `cmd_entry_points` + `cmd_guard` + `cmd_map` + `cmd_metrics` + `cmd_path_coverage` + `cmd_patterns` + `cmd_plan_refactor` + `cmd_pytest_fixtures` + `cmd_risk` + `cmd_safe_delete` + `cmd_safe_zones` + `cmd_simulate_departure` + `cmd_suggest_refactoring` + `cmd_testmap` + `cmd_why_slow` + `cmd_ws`). **The 196 → 0 propagation arc is now terminal — 196 commands audited, 196 commands disclosure-covered** across CONSOLIDATEs 4 → 14. 0 BAILs. 0 reclassifications. |
| ~~W1234 — `evidence_stale` producer~~ | W1234 (CONSOLIDATE-14) | W210 packet-layer Pattern-2 variant-2f producer. `evidence_stale` field populated upstream when the evidence-compiler detects time-skew between `context_read_at` / `edits_started_at` / `edits_completed_at` and the current commit. Consumer-side (W1254) in flight at consolidation time. |
| ~~W1253 BAIL surfacing W1255 capture~~ | W1253-BAIL (CONSOLIDATE-14) | `pr-bundle emit` path cannot mark a packet stale before any packet exists. Architectural prerequisite captured as W1255 — the BAIL-and-capture discipline applied to packet-layer Pattern-2 work. |
| ~~W1235 — `_STATE_FAMILY_ALIASES` substrate registry~~ | W1235 (CONSOLIDATE-14) | State-vocab normalization for closed-vocab Pattern-2g sites. Substrate-first sequencing — substrate ships unused; consumer-side audit (W1257) in flight. 45-site bulk migration (W1251) captured for next session. |
| ~~W1232 — `flag-constant-default` → `flag-suspect` rule rename~~ | W1232 (CONSOLIDATE-14) | W1226 SHIP scope-discipline follow-up. `cmd_flag_dead` `flag-*` namespace now expresses claims (`flag-staleness` / `flag-single-reference` / `flag-suspect`) rather than mixing claims with antecedent conditions. Hash-stable via the SARIF wrapper's `rule_id` field. |
| ~~W1239 — drift-guard hygiene follow-up A~~ | W1239 (CONSOLIDATE-14) | Stale audit assertion cleaned up. From W1231-audit. |
| ~~W1240 — drift-guard hygiene follow-up B~~ | W1240 (CONSOLIDATE-14) | BACKLOG.md table-of-pendings cleaned to match the post-CONSOLIDATE-13 ground truth. From W1231-audit. |
| ~~W1247 — CLAUDE.md module-local SARIF convention doc-pass~~ | W1247 (CONSOLIDATE-14) | Added note that `_to_sarif()` helpers live in the cmd module per SHIP emitter — not centralized — per W1236-audit BENIGN verdict. |
| ~~W1252 — CLAUDE.md findings-registry decision doc-pass~~ | W1252 (CONSOLIDATE-14) | `emit_finding(conn, record)` canonicalized as the API name (supersedes older `findings_store.persist(...)` snake_case spelling). |
| ~~W1258+W1259 — CLAUDE.md detector-count refresh~~ | W1258+W1259 (CONSOLIDATE-14) | "16 detectors persist findings" (W146 vintage) → "26 detectors persist findings as of 2026-05-16". 10 newly-emitting detectors enumerated (predominantly aggregator / consumer commands). |
| Detector FP-rate research memo | `dev/DETECTOR-FP-RATE-BENCHMARKS-2026-05-16.md` (773 LOC, CONSOLIDATE-14) | False-positive rate benchmarks across the 26 emitting detectors. Reference for next per-detector confidence-tier tuning pass (W1256 captured for next session). Companion to `dev/PATTERN-2-EVOLUTION-2026-05-16.md` (884 LOC, CONSOLIDATE-13). |

### Pending after CONSOLIDATE-14 (queue for next session)

The CONSOLIDATE-14 pass closes the **196 → 0 propagation arc terminal**;
ships the Pattern-2c Wave-1 quartet + substrate refactor; lands the
W1234 evidence_stale producer + W1235 state-vocab substrate; and clears
two drift-guard follow-ups + four CLAUDE.md doc-drift items. **4
SHIPPED + 1 SUBSTRATE-REFACTOR + 1 SUBSTRATE + 1 WAVE-CLOSURE + 1
PRODUCER + 1 BAIL+CAPTURE + 1 RULE-RENAME + 2 DRIFT-GUARD FOLLOW-UPS
+ 4 DOC-DRIFT + 1 RESEARCH MEMO.** Pattern-2c remains the dominant
in-flight arc; the SARIF SHIP/SKIP-DISCLOSURE arc has reached its
terminal state (the 196 → 0 propagation arc is structurally complete).

**Pattern-2c Wave 2/3 (in flight + captured):**

| Item | Where | Effort |
|---|---|---|
| **W1245-batch-1 — Pattern-2c Wave 2 first 5 sites** (in flight). `cmd_hotspots` / `cmd_smells` / `cmd_dead` / `cmd_safe_delete` / `cmd_closure` — substrate-helper consumers per W1249's ~3× LOC simplification. | per-command edit | dispatched |
| **W1245-batch-2+ — Pattern-2c Wave 2/3 remaining ~29 sites** (captured). Per W1233-audit enumeration; substrate-helper consumers post-W1249 refactor. Estimated 2-3 sessions for full sweep. | per-command edit | ~2-3 sessions |

**Evidence/W210 wire-up (in flight + captured):**

| Item | Where | Effort |
|---|---|---|
| **W1254 — `evidence_stale` consumer** (in flight). Report renderer + projection layers consume the `evidence_stale` field W1234 populates; surface a "stale evidence" banner. | `src/roam/evidence/*` consumers | dispatched |
| **W1255 — `pr-bundle emit` packet-stale architectural decision** (BAIL+capture from W1253). Decide whether the no-upstream-packet case should emit a synthetic stub packet or stay silent. | architectural decision | 1-2h decision + 1d impl |

**State-vocab Pattern-2g (in flight + captured):**

| Item | Where | Effort |
|---|---|---|
| **W1257 — state-vocab adoption audit (~45 sites)** (in flight). Consumer-side of W1235 `_STATE_FAMILY_ALIASES` registry; same pattern as W1233-audit for Pattern-2c. | per-command audit | dispatched |
| **W1251 — 45-site state-vocab bulk migration** (captured). Post-W1257 audit landing. | per-command edit | ~1-2 sessions |

**Helper docstring + per-pattern + vibe-check (captured):**

| Item | Where | Effort |
|---|---|---|
| **W1250 — `resolution_disclosure()` helper docstring expansion** (captured). Document the W324 cmd_annotate template precedent + W1241 substrate-first sequencing in the helper docstring. | `src/roam/output/formatter.py` | 30 min |
| **W1256 — vibe-check per-pattern confidence-tier tuning** (captured from the 773-LOC FP-rate benchmark memo). Per-pattern confidence-tier adjustment for the 8 vibe-check patterns based on FP-rate evidence. | `src/roam/commands/cmd_llm_smells.py` | 1-2d |

**Outstanding from older rosters** (carry-forwards from CONSOLIDATE-9
through CONSOLIDATE-13 that the post-CONSOLIDATE-13 batch did not
re-touch):

| Item | Where | Effort |
|---|---|---|
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward; **W1226 landed the SARIF SHIP wrapper** but the original W1113 ticket is the helper plumb — still open). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (SUPERSEDED by W1258+W1259 CONSOLIDATE-14 — count refreshed to 26). | — | CLOSED |

### Closures since W1224 (W1226 / W1227 / W1229 / W1230 / W1231 / W1233 / W1236 / W1237 / W1238 / W1241 / W1246 — CONSOLIDATE-13)

The CONSOLIDATE-13 pass folds in ~13 completions from the W1225 →
W1248 stretch. **Four themes**: (a) **SARIF SHIP family grew from
34 to 37 emitters in a single post-CONSOLIDATE-12 window** — W1226
`cmd_flag_dead` (35th, three closed-enum rules under `flag-*`
namespace — `flag-staleness` / `flag-single-reference` /
`flag-constant-default`; staleness-banded per-result `level` with a
**warning ceiling**, heuristic detector never escalates to error) +
W1227 `cmd_orphan_routes` (36th, per-route dead-endpoint projection;
single closed-enum rule `orphan-route`, confidence-banded per-result
`level` — high + medium → warning, low → note; warning ceiling,
heuristic detector never escalates to error; `used` bucket filtered
upstream so SARIF consumers never see non-actionable rows) + W1229
`cmd_verify_imports` (37th, **first SHIP emitter that escalates to
error** — two closed-enum rules: `invalid-import` (warning) for
unresolved with FTS5 fuzzy-match candidates, `hallucination-import`
(error) for unresolved with no candidates; verify-imports is the
canonical "hallucination firewall" detector for LLM-era code and the
only verify-imports rule that escalates to error per W1229 scope
discipline); (b) **Pattern-2 variant-D family enablement** — W1241
landed the canonical `resolution_disclosure()` helper at
`src/roam/output/formatter.py:1263` + `_RESOLUTION_KINDS` frozen
closed-enum (`symbol` / `file` / `fuzzy` / `unresolved`) +
drift-guard test (`tests/test_resolution_disclosure.py`). Helper
substrate is now live for the W1242/W1243/W1244 Wave-1 adoption sweep
across `cmd_impact` / `cmd_preflight` / `cmd_diagnose` (in flight at
consolidation time); (c) **SKIP-disclosure pin-list 20 → 17 via
SHIP-promotes** — each of W1226 + W1227 + W1229 decremented
`_KNOWN_MISSING` in-batch via the
`tests/test_known_missing_pin_is_current` inverse-drift guard; no
new docstring waves this batch (long-tail of the propagation arc);
the arc is now **~91% complete from the original 196-file gap (179
commands closed; 196 → 17)**; (d) **drift-guard remediation pass** —
W1237 (`cmd_risk` edge-kind vocabulary canonicalized to
`roam.db.edge_kinds`) + W1238 (`catalog/detectors.py`
framework-detector plugin loop migrated from bare `except Exception`
to `log.warning(...) + continue` per W531 fail-loud discipline;
previously-grandfathered `_PRE_W662_PENDING` entries in
`catalog/detectors.py` dropped to zero). Plus a **884-LOC research
memo** (`dev/PATTERN-2-EVOLUTION-2026-05-16.md`) cataloguing the
**seven-variant Pattern-2 family taxonomy** (2a compound-recipe / 2b
empty-corpus / 2c resolution-state — W1241 helper / 2d producer-gap /
2e shared-substrate / 2f packet-layer / 2g closed-vocabulary-unknown)
with seven open gaps surfaced. **3 SHIPPED + 1 SUBSTRATE + 5 AUDITS +
2 DRIFT-GUARD FIXES + 1 RESEARCH MEMO**. Strike-throughs preserved on
originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1226 — `cmd_flag_dead` 35th SARIF SHIP~~ | W1226 (CONSOLIDATE-13) | Per-flag staleness projection. **Three closed-enum rules** under `flag-*` namespace: `flag-staleness` / `flag-single-reference` / `flag-constant-default`. Staleness-banded per-result `level` with a **warning ceiling** — heuristic detector, never escalates to error. Hash-stable additive wrapper. Pin-list 20 → 19. |
| ~~W1227 — `cmd_orphan_routes` 36th SARIF SHIP~~ | W1227 (CONSOLIDATE-13) | Per-route dead-endpoint projection. Single closed-enum rule `orphan-route` with confidence-banded per-result `level`: high + medium → warning, low → note; **warning ceiling** — heuristic detector, never escalates to error. The `used` bucket (has a frontend consumer) is filtered upstream so SARIF consumers never see non-actionable rows. Hash-stable additive wrapper. Pin-list 19 → 18. |
| ~~W1229 — `cmd_verify_imports` 37th SARIF SHIP~~ | W1229 (CONSOLIDATE-13) | **First SHIP emitter that escalates to error.** Two closed-enum rules: `invalid-import` (warning) for unresolved with FTS5 fuzzy-match candidates, `hallucination-import` (error) for unresolved with no candidates. verify-imports is the canonical "hallucination firewall" detector for LLM-era code and the only verify-imports rule that escalates to error per the W1229 scope discipline. `resolved` rows are filtered upstream so SARIF consumers never see non-actionable rows. Hash-stable additive wrapper. Pin-list 18 → 17. |
| ~~W1241 — `resolution_disclosure()` helper substrate~~ | W1241 (CONSOLIDATE-13) | Canonical Pattern-2 variant-D helper at `src/roam/output/formatter.py:1263`. Frozen closed-enum `_RESOLUTION_KINDS` (`symbol` / `file` / `fuzzy` / `unresolved`). Drift-guard test at `tests/test_resolution_disclosure.py`. Implements the W324 cmd_annotate template at substrate level. `partial_success` flag is True for any non-`symbol` resolution. Enables W1242/W1243/W1244 Wave-1 adoption sweep. |
| ~~W1230 — `cmd_test_gaps` SKIP re-verification~~ | W1230-audit (CONSOLIDATE-13) | Carry-forward audit from W1202 (CONSOLIDATE-9) re-opened by Wave 14b docstring landing. Re-verified: REPORT-not-detector pattern (no per-location FindingRecord persistence). W1202 close confirmed; SKIP-disclosure docstring stays in place. |
| ~~W1231 — drift-guard triage (5 failures)~~ | W1231-audit (CONSOLIDATE-13) | 5 drift-guard failures triaged: W1237 (`cmd_risk` edge-kind canonicalize — FIXED), W1238 (`catalog/detectors.py` bare-except migration — FIXED), W1239 + W1240 (drift-guard hygiene follow-ups — captured to Pending). |
| ~~W1233 — Pattern-2c audit on 38 sites~~ | W1233-audit (CONSOLIDATE-13) | Wave 1 = `cmd_impact` + `cmd_preflight` + `cmd_diagnose` (W1242 + W1243 + W1244 — in flight). Wave 2 = next 10 sites (W1245). Wave 3 = remaining (W1246 + W1248). Adoption sequencing parallels the SARIF SHIP / SKIP-DISCLOSURE arc. |
| ~~W1236 — SARIF helper convention sweep~~ | W1236-audit (CONSOLIDATE-13) | **VERDICT BENIGN** — module-local SARIF convention consistent across all 37 emitters; no canonicalization needed at substrate level. W1247 captured the doc-pass to ensure CLAUDE.md reflects the module-local SARIF convention. |
| ~~W1237 — `cmd_risk` edge-kind canonicalize~~ | W1237 (CONSOLIDATE-13) | Edge-kind vocabulary canonicalized to `roam.db.edge_kinds` registry. Closes a quiet drift class where edge-kind literals could diverge across cmd_*.py callsites. |
| ~~W1238 — `catalog/detectors.py` bare-except migration~~ | W1238 (CONSOLIDATE-13) | Framework-detector plugin loop migrated from bare `except Exception: continue\|pass` to `log.warning(...) + continue` per W531 fail-loud discipline. Plugin-isolation perimeter rationale preserved in inline comments. **Previously-grandfathered `_PRE_W662_PENDING` entries in `catalog/detectors.py` dropped to zero** (stale-pin hygiene applied alongside the migration). |
| ~~W1246 — `cmd_trace.find_symbol_id` Pattern-2c audit~~ | W1246-audit (CONSOLIDATE-13) | **VERDICT NON-COMPLIANT** — the `find_symbol_id` callsite in `cmd_trace` does not currently emit `resolution` disclosure on degraded resolution. Captured as W1248 for Wave-3 adoption sweep. |
| Pattern-2 family research memo | `dev/PATTERN-2-EVOLUTION-2026-05-16.md` (884 LOC, CONSOLIDATE-13) | Catalogues the **seven-variant Pattern-2 family taxonomy** (2a compound-recipe / 2b empty-corpus / 2c resolution-state — W1241 helper / 2d producer-gap — W261 redaction reason / 2e shared-substrate — W1018 YAML loader / 2f packet-layer — W210 evidence_stale / 2g closed-vocabulary-unknown — W1077/W1080 structured_unknown). Surfaces 7 open gaps + counts adoption per variant. Companion to `dev/PATTERN-2-EVOLUTION-2026-05-15.md` (language-feature survey). |

### Pending after CONSOLIDATE-13 (queue for next session)

The CONSOLIDATE-13 pass folds in ~13 completions from the W1225 →
W1248 stretch. SARIF SHIP family at 37 emitters (W1226 + W1227 +
W1229); SKIP-disclosure pin-list 20 → 17 via SHIP-promotes; Pattern-2
variant-D substrate live with Wave 1 in flight; drift-guard hygiene
restored on `cmd_risk` + `catalog/detectors.py`. **3 SHIPPED + 1
SUBSTRATE + 5 AUDITS + 2 DRIFT-GUARD FIXES + 1 RESEARCH MEMO.**

**Wave 1 Pattern-2c adoption — SHIPPED CONSOLIDATE-14 (W1242 + W1243 + W1244):**

| Item | Where | Effort |
|---|---|---|
| ~~W1242 — `cmd_impact` Pattern-2c adoption~~ | ~~`src/roam/commands/cmd_impact.py`~~ | ~~SHIPPED CONSOLIDATE-14~~ |
| ~~W1243 — `cmd_preflight` Pattern-2c adoption~~ | ~~`src/roam/commands/cmd_preflight.py`~~ | ~~SHIPPED CONSOLIDATE-14~~ |
| ~~W1244 — `cmd_diagnose` Pattern-2c adoption~~ | ~~`src/roam/commands/cmd_diagnose.py`~~ | ~~SHIPPED CONSOLIDATE-14~~ |

**Wave 2/3 Pattern-2c adoption — partially shipped CONSOLIDATE-14 (W1248 shipped; W1245 in flight):**

| Item | Where | Effort |
|---|---|---|
| **W1245 — Pattern-2c Wave 2 adoption sweep** (10 sites from W1233 audit; **W1245-batch-1 in flight CONSOLIDATE-14** — 5 sites: `cmd_hotspots` / `cmd_smells` / `cmd_dead` / `cmd_safe_delete` / `cmd_closure`; W1245-batch-2+ captured for next session at ~29 remaining sites). | per-command audit + impl | 1-2d total |
| ~~W1246/W1248 — `cmd_trace.find_symbol_id` Pattern-2c adoption~~ | ~~`src/roam/commands/cmd_trace.py`~~ | ~~SHIPPED CONSOLIDATE-14 — promoted from Wave-3 capture to Wave-1 quartet at landing~~ |
| ~~W1247 — module-local SARIF convention doc-pass~~ | ~~`CLAUDE.md`~~ | ~~SHIPPED CONSOLIDATE-14~~ |

**Pattern-2 G2/G3 (research memo gaps — SHIPPED CONSOLIDATE-14):**

| Item | Where | Effort |
|---|---|---|
| ~~W1234 — Pattern-2 G2 gap (evidence_stale producer)~~ | ~~W210 packet-layer~~ | ~~SHIPPED CONSOLIDATE-14 (W1234 producer; W1254 consumer in flight)~~ |
| ~~W1235 — Pattern-2 G3 gap (state-vocab substrate)~~ | ~~`_STATE_FAMILY_ALIASES` substrate~~ | ~~SHIPPED CONSOLIDATE-14 (substrate registry; W1257 consumer audit in flight)~~ |

**Drift-guard hygiene follow-ups — SHIPPED CONSOLIDATE-14 (W1239 / W1240):**

| Item | Where | Effort |
|---|---|---|
| ~~W1239 — drift-guard hygiene follow-up A~~ | ~~drift-guard substrate~~ | ~~SHIPPED CONSOLIDATE-14~~ |
| ~~W1240 — drift-guard hygiene follow-up B~~ | ~~BACKLOG.md hygiene~~ | ~~SHIPPED CONSOLIDATE-14~~ |

**Flag-dead rule rename — SHIPPED CONSOLIDATE-14 (W1232):**

| Item | Where | Effort |
|---|---|---|
| ~~W1232 — `cmd_flag_dead` rule rename~~ | ~~`src/roam/commands/cmd_flag_dead.py` (`flag-constant-default` → `flag-suspect`)~~ | ~~SHIPPED CONSOLIDATE-14~~ |

**Wave 15 SHIP candidates — RESOLVED as Wave 16 SKIP-DISCLOSURE CONSOLIDATE-14:**

The W1175-RESEARCH "Bucket A likely-SHIP subset" of the 17 pin-list
entries was re-classified as SKIP-DISCLOSURE on Wave 16 audit —
each is a REPORT-not-detector pattern (no per-location FindingRecord
persistence). `cmd_pytest_fixtures` + `cmd_debt` + `cmd_risk` +
`cmd_guard` + `cmd_safe_delete` + `cmd_safe_zones` + `cmd_path_coverage`
all landed SKIP-DISCLOSURE docstrings in Wave 16. **The 196 → 0 arc
is terminal; no further SHIP candidates remain in the original
W1175 long-tail roster.**

| Item | Where | Effort |
|---|---|---|
| ~~W1201 — `cmd_pytest_fixtures` SARIF SHIP impl~~ | ~~`src/roam/commands/cmd_pytest_fixtures.py`~~ | ~~RESOLVED as Wave 16 SKIP-DISCLOSURE CONSOLIDATE-14~~ |
| ~~W1225+ — `cmd_debt` / `cmd_risk` / `cmd_guard` / `cmd_safe_delete` / `cmd_safe_zones` / `cmd_path_coverage` SHIP audit~~ | ~~per-command audit~~ | ~~RESOLVED as Wave 16 SKIP-DISCLOSURE CONSOLIDATE-14 (all 7 closed as REPORT-not-detector)~~ |

**Wave 15/16 SKIP-DISCLOSURE — SHIPPED CONSOLIDATE-14 (Wave 16: 17 docstrings; `_KNOWN_MISSING` 17 → 0):**

| Item | Where | Effort |
|---|---|---|
| ~~Pattern-3b propagation arc, remaining 17 Bucket B SKIP candidates~~ | ~~`cmd_debt` + `cmd_entry_points` + `cmd_guard` + `cmd_map` + `cmd_metrics` + `cmd_path_coverage` + `cmd_patterns` + `cmd_plan_refactor` + `cmd_pytest_fixtures` + `cmd_risk` + `cmd_safe_delete` + `cmd_safe_zones` + `cmd_simulate_departure` + `cmd_suggest_refactoring` + `cmd_testmap` + `cmd_why_slow` + `cmd_ws`~~ | ~~SHIPPED CONSOLIDATE-14 — 196 → 0 propagation arc terminal~~ |

**Outstanding from older rosters** (carry-forwards from CONSOLIDATE-9
through CONSOLIDATE-12 that the post-CONSOLIDATE-12 batch did not
re-touch):

| Item | Where | Effort |
|---|---|---|
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward; **W1226 landed the SARIF SHIP wrapper** but the original W1113 ticket is the helper plumb — still open). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1222 (W1207 / W1209 / W1210 / W1211 / W1213 / W1216 / W1224-impl-14a / W1224-impl-14b — CONSOLIDATE-12)

The CONSOLIDATE-12 pass folds in ~13 completions from the W1223 →
W1224 stretch. **Two milestones**: (a) **SARIF SHIP family grew from
28 to 34 emitters in a single window** — the CONSOLIDATE-11
deferred-SHIP roster closed to **zero outstanding**: W1207
`cmd_llm_smells` (29th, 10 closed-enum rules under `llm-smells/`
namespace — first SHIP emitter with double-digit closed-enum rule
count; severity-banded `level`) + W1209 `cmd_fan` (30th, per-symbol
fan-in/fan-out projection) + W1210 `cmd_hotspots` (31st, runtime-mode
only; `--security`/`--danger` sub-modes emit raw findings outside
the closed-enum `hotspots/*` rule catalogue — first SHIP emitter
with mode-conditional rule-catalogue scoping) + W1211 `cmd_dark_matter`
(32nd, per-pair hidden-coupling projection; single closed-enum rule
with confidence-tier-banded severity) + W1213 `cmd_duplicates` (33rd,
BAIL-and-capture promotion landing — per-cluster semantic-duplicate
projection; similarity-banded severity) + W1216 `cmd_laws` (34th,
per-rule mined-invariant projection from the W119 substrate);
(b) **Pattern-3b propagation arc — 14+ waves shipped, `_KNOWN_MISSING`
64 → 20** — Wave 14a (W1224-impl, 15 SKIP-eligible docstrings) + Wave
14b (W1224-impl, 22 SKIP-eligible docstrings) landed cleanly with 0
BAILs across both sub-waves; **largest single-wave docstring landing
of the arc to date (37 docstrings in one window)** vs prior maximum
of 12 (W1187-impl Wave 4) and 11 (W1188-impl Wave 5 / W1191-impl Wave
7). 176 commands closed across W1180 → W1224 (90% of the original
196-file gap). **6 SHIPPED + 2 IMPL-WAVES**. Strike-throughs preserved
on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1207 — `cmd_llm_smells` 29th SARIF SHIP~~ | W1207 (CONSOLIDATE-12) | **First SHIP emitter with double-digit closed-enum rule count** — 10 rules under `llm-smells/` namespace; severity-banded per-result `level`. Per-occurrence LLM-API anti-pattern projection. Hash-stable additive wrapper. Originally captured CONSOLIDATE-10 (W1206-audit); deferred through CONSOLIDATE-11; landed this batch. |
| ~~W1209 — `cmd_fan` 30th SARIF SHIP~~ | W1209 (CONSOLIDATE-12) | Per-symbol fan-in/fan-out projection via `fan_to_sarif`. Hash-stable additive wrapper. Originally captured CONSOLIDATE-10 (W1206-audit-unclear); deferred through CONSOLIDATE-11; landed this batch. |
| ~~W1210 — `cmd_hotspots` 31st SARIF SHIP~~ | W1210 (CONSOLIDATE-12) | **First SHIP emitter with mode-conditional rule-catalogue scoping** — runtime mode SARIF-projected; `--security`/`--danger` static-analysis modes emit raw findings outside the closed-enum `hotspots/*` rule catalogue (per W1210 scope discipline). Hash-stable additive wrapper. Originally captured CONSOLIDATE-10 (W1206-audit-unclear); deferred through CONSOLIDATE-11; landed this batch. |
| ~~W1211 — `cmd_dark_matter` 32nd SARIF SHIP~~ | W1211 (CONSOLIDATE-12) | Per-pair hidden-coupling projection. Single closed-enum rule `dark-matter/hidden-coupling` with confidence-tier-banded severity (high/med/low by coupling-strength quantile). Hash-stable additive wrapper. Originally captured CONSOLIDATE-10 (W1206-audit-unclear); deferred through CONSOLIDATE-11; landed this batch. |
| ~~W1213 — `cmd_duplicates` 33rd SARIF SHIP~~ | W1213 (CONSOLIDATE-12) | **BAIL-and-capture promotion landing — discovered CONSOLIDATE-10, captured as SHIP, deferred through CONSOLIDATE-11, landed CONSOLIDATE-12.** Demonstrates the full BAIL-and-capture → SHIP arc end-to-end (two sessions from discovery to landing). Per-cluster semantic-duplicate projection; single closed-enum rule `duplicates/cluster` with similarity-banded severity. Hash-stable additive wrapper. |
| ~~W1216 — `cmd_laws` 34th SARIF SHIP~~ | W1216 (CONSOLIDATE-12) | Per-rule mined-invariant projection from the W119 mined-laws substrate via `laws_to_sarif`. Hash-stable additive wrapper. Drive-by capture from CONSOLIDATE-11 Wave 13 / W1215-audit work; deferred one session; landed this batch. |
| ~~W1224-impl Wave 14a — 15 SKIP-eligible docstring landings~~ | W1224-impl-14a (CONSOLIDATE-12) | Invocation-scoped-aggregate + state-mutating + validator slice. Sites: cut / dev_profile / doc_staleness / docs_coverage / drift / effects / eval_retrieve / evidence_diff / evidence_doctor / fitness / fn_coupling / graph_stats / idempotency / index / index_bundle. **0 BAILs**. `_KNOWN_MISSING` 64 → 49. |
| ~~W1224-impl Wave 14b — 22 SKIP-eligible docstring landings~~ | W1224-impl-14b (CONSOLIDATE-12) | Aggregate + composer + state-mutating + validator slice. Sites: ingest_trace / invariants / mutate / owner / pr_diff / pr_prep / side_effects / split / stats / suggest_reviewers / surface / syntax_check / telemetry / test_gaps / test_pyramid / tx_boundaries / version / vuln_map / vuln_reach / workflow / xlang / index_stats. **0 BAILs**. **Largest single-wave batch of the arc to date — 22 docstrings.** `_KNOWN_MISSING` 49 → 20. |

### Pending after CONSOLIDATE-12 (queue for next session)

The CONSOLIDATE-12 pass folds in ~13 completions from the W1223 →
W1224 stretch. SARIF SHIP family at 34 emitters (W1207 + W1209 +
W1210 + W1211 + W1213 + W1216 — 6 emitters in a single window, new
arc maximum); Pattern-3b propagation arc 14+ waves shipped +
`_KNOWN_MISSING` 64 → 20 (Wave 14a + Wave 14b — 37 docstrings in a
single window, new arc maximum). **CONSOLIDATE-11 deferred-SHIP
roster closed to ZERO outstanding** — first time in the arc that the
post-roster deferred queue has fully drained in one session.

**Wave 15 SHIP candidates** (sourced from the 20 surviving
`_KNOWN_MISSING` pin-list entries; per W1175-RESEARCH the Bucket A
likely-SHIP subset is ~10 commands):

| Item | Where | Effort |
|---|---|---|
| **W1200 — `cmd_orphan_routes` SARIF SHIP impl** (carry-forward from W1198-audit CONSOLIDATE-9). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper per W1192/W1195/W1208/W1215/W1217/W1218/W1219 scaffold. | `src/roam/commands/cmd_orphan_routes.py` | 1-2d |
| **W1201 — `cmd_pytest_fixtures` SARIF SHIP impl** (carry-forward from W1198-audit CONSOLIDATE-9). Already emits per-location findings. | `src/roam/commands/cmd_pytest_fixtures.py` | 1-2d |
| **W1204 — `cmd_verify_imports` SARIF SHIP impl** (carry-forward from W1198-audit CONSOLIDATE-9). Already emits per-location findings. | `src/roam/commands/cmd_verify_imports.py` | 1-2d |
| **W1225+ — `cmd_debt` / `cmd_risk` / `cmd_guard` / `cmd_safe_delete` / `cmd_safe_zones` / `cmd_path_coverage` SHIP audit** — Bucket A likely-SHIP subset of the 20 surviving pin-list entries. Each needs a Wave 14-style audit-first dispatch to verify per-location findings + SARIF emitter scaffold. | per-command audit + impl | 1-2d each (estimated 6-12d total) |

**Wave 15 SKIP-DISCLOSURE candidates** (the remaining ~10 Bucket B
leftover from the 20 surviving pin-list entries):

| Item | Where | Effort |
|---|---|---|
| **W1226+ — Pattern-3b propagation arc, remaining ~10 Bucket B SKIP candidates** — `cmd_map` + `cmd_metrics` + `cmd_patterns` + `cmd_plan_refactor` + `cmd_simulate_departure` + `cmd_suggest_refactoring` + `cmd_testmap` + `cmd_why_slow` + `cmd_ws` + `cmd_entry_points` + `cmd_flag_dead`. Per W1175-RESEARCH all Bucket B aggregate / composer / state-mutating — same SKIP-DISCLOSURE docstring pattern as Waves 14a/14b. Estimated: ~1 wave (10 commands) to fully close `_KNOWN_MISSING` to **zero**. | per-command audit + docstring | ~1 wave |

**Outstanding from older rosters** (carry-forwards from CONSOLIDATE-9
+ CONSOLIDATE-10 that the propagation arc did not re-touch this
window):

| Item | Where | Effort |
|---|---|---|
| **W1202 — `cmd_test_gaps` SARIF SHIP impl** (carry-forward from W1198-audit CONSOLIDATE-9; **Wave 14b landed the SKIP-disclosure docstring** but the SHIP-eligibility verdict for this command is itself the open question — needs re-audit to decide SHIP-promotion-vs-SKIP-confirm). | `src/roam/commands/cmd_test_gaps.py` | 1-2d (audit + SHIP) OR 0 (SKIP confirm) |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward; **Wave 14a landed the SKIP-disclosure docstring** but the original W1112 ticket is the helper plumb, not the disclosure docstring — still open). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1212 (W1208 / W1212 / W1215 / W1217 / W1218 / W1219 / W1220 / W1221-audit / W1221-impl / W1222 — CONSOLIDATE-11)

The CONSOLIDATE-11 pass folds in ~10 completions from the W1213 →
W1222 stretch. **Two milestones**: (a) **SARIF SHIP family grew
from 24 to 28 emitters** — W1208 `cmd_n1` (24th, W110 N+1 detector
with 3 closed-enum rules — high/med/low; 89+30 tests pass) + W1217
`cmd_missing_index` (25th, 3 closed-enum rules; 20 tests pass) +
W1218 `cmd_orphan_imports` (26th, 3 confidence tiers —
`internal_typo=error` / `missing_package=warning` /
`missing_local=warning`) + W1219 `cmd_over_fetch` (27th, single
closed-enum rule at warning; dual-shape endpoint+model handling) +
W1215 `cmd_bus_factor` (28th, directory-anchor pattern with 3 rules
— concentration / stale-ownership / solo-summary; hash-stable
sha256 verified); (b) **Pattern-3b propagation arc — 12+ waves
shipped, `_KNOWN_MISSING` 96 → 64** — Wave 13 (W1221-audit +
W1221-impl) landed 10 SKIP-eligible docstrings with 0 BAILs +
W1212 reclassification + W1220 SKIP + W1222 inline (stale-pin
removal). ~22 commands closed across propagation + SHIP-promote
in this batch. Strike-throughs preserved on originating pending
lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1208 — `cmd_n1` 24th SARIF SHIP~~ | W1208 (CONSOLIDATE-11) | W110 N+1 detector SARIF wrapper. **3 closed-enum rules** (high/med/low severity). 89 new SARIF tests + 30 pre-existing pass. Hash-stable additive wrapper. Originally captured CONSOLIDATE-10; landed this batch. |
| ~~W1212 — `cmd_coverage_gaps` REVISED SKIP-DISCLOSURE~~ | W1212 (CONSOLIDATE-11) | SKIP docstring landed (REPORT-not-detector pattern, supersedes W1199 SHIP from CONSOLIDATE-9). ~10 LOC. Originally captured CONSOLIDATE-10. |
| ~~W1215 — `cmd_bus_factor` 28th SARIF SHIP~~ | W1215 (CONSOLIDATE-11) | 3 closed-enum rules — concentration / stale-ownership / solo-summary. **Directory-anchor pattern** (new shape — finding location anchors on directory rather than file/symbol). Hash-stable additive wrapper (sha256 verified pre/post). |
| ~~W1217 — `cmd_missing_index` 25th SARIF SHIP~~ | W1217 (CONSOLIDATE-11) | 3 closed-enum rules. 20 tests pass. Hash-stable additive wrapper. |
| ~~W1218 — `cmd_orphan_imports` 26th SARIF SHIP~~ | W1218 (CONSOLIDATE-11) | **3 confidence tiers in SARIF rules**: `internal_typo=error` / `missing_package=warning` / `missing_local=warning`. First SHIP emitter where rule severity diverges by detection-confidence tier (related to W1195 confidence-tier pattern but expressed in `level` rather than `properties`). Hash-stable additive wrapper. |
| ~~W1219 — `cmd_over_fetch` 27th SARIF SHIP~~ | W1219 (CONSOLIDATE-11) | Single closed-enum rule at warning level. **Dual-shape endpoint+model handling** — same wrapper emits SARIF results for both endpoint-level over-fetch findings and model-level over-fetch findings. Hash-stable additive wrapper. |
| ~~W1220 — `cmd_capabilities` SKIP-DISCLOSURE~~ | W1220 (CONSOLIDATE-11) | Capability-registry manifest emitter; no per-location FindingRecord. SKIP docstring shipped. |
| ~~W1221-audit — Wave 13: 10 SKIP-eligible docstring verdicts~~ | W1221-audit (CONSOLIDATE-11) | **VERDICT SKIP-DISCLOSURE x10, 0 BAILs.** Sites: `cmd_changelog` + `cmd_db_check` + `cmd_intent_check` + `cmd_metrics_push` + `cmd_recommend` + `cmd_report` + `cmd_retrieve` + `cmd_schema` + `cmd_search_semantic` + `cmd_simulate`. |
| ~~W1221-impl — Wave 13 docstring landings~~ | W1221-impl (CONSOLIDATE-11) | 10 SKIP-eligible docstrings landed cleanly. 0 BAILs (premise checks uniform — no per-location persistence). |
| ~~W1222 — `cmd_over_fetch` stale-pin removal from `_KNOWN_MISSING`~~ | W1222 (CONSOLIDATE-11) | Inline follow-up of W1219 — over_fetch was still pinned in the disclosure-coverage `_KNOWN_MISSING` list despite the SARIF wrapper having landed. Removed in-batch. |
| ~~W1213 — `cmd_duplicates` SHIP captured~~ | W1213 (CONSOLIDATE-11) | Captured cleanly via BAIL-and-capture from W1206-impl-skip premise failure. **Still pending impl** — appears in "Pending after W1222" SHIP candidate roster. |

### Pending after W1222 (queue for next session — CONSOLIDATE-11)

The CONSOLIDATE-11 pass folds in ~10 completions from the
W1213 → W1222 stretch. SARIF SHIP family at 28 emitters
(W1208 + W1217 + W1218 + W1219 + W1215); Pattern-3b propagation
arc 12+ waves shipped + `_KNOWN_MISSING` 96 → 64. **6 SHIP
candidates remain pending** (W1207 / W1209 / W1210 / W1211 /
W1213 / W1216 — ~6-12d total effort). All carry-forwards from
"Pending after W1212" remain in queue except where superseded.

| Item | Where | Effort |
|---|---|---|
| **W1207 — `cmd_llm_smells` SARIF SHIP impl** (carry-forward from W1206-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper per W1192/W1195/W1208/W1215/W1217/W1218/W1219 scaffold. | `src/roam/commands/cmd_llm_smells.py` | 1-2d |
| **W1209 — `cmd_fan` SARIF SHIP impl** (carry-forward from W1206-audit-unclear). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_fan.py` | 1-2d |
| **W1210 — `cmd_hotspots` SARIF SHIP impl** (carry-forward from W1206-audit-unclear). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_hotspots.py` | 1-2d |
| **W1211 — `cmd_dark_matter` SARIF SHIP impl** (carry-forward from W1206-audit-unclear). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_dark_matter.py` | 1-2d |
| **W1213 — `cmd_duplicates` SARIF SHIP impl** (carry-forward from W1206-impl-skip BAIL-and-capture). Already emits per-location duplicate-pair findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_duplicates.py` | 1-2d |
| **W1216 — `cmd_laws` SARIF SHIP impl** (captured this batch; emerged during Wave 13 / W1215-audit work). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_laws.py` | 1-2d |
| **W1200 — `cmd_orphan_routes` SARIF SHIP impl** (carry-forward from W1198-audit). | `src/roam/commands/cmd_orphan_routes.py` | 1-2d |
| **W1201 — `cmd_pytest_fixtures` SARIF SHIP impl** (carry-forward from W1198-audit). | `src/roam/commands/cmd_pytest_fixtures.py` | 1-2d |
| **W1202 — `cmd_test_gaps` SARIF SHIP impl** (carry-forward from W1198-audit). | `src/roam/commands/cmd_test_gaps.py` | 1-2d |
| **W1204 — `cmd_verify_imports` SARIF SHIP impl** (carry-forward from W1198-audit). | `src/roam/commands/cmd_verify_imports.py` | 1-2d |
| **W1223+ — Pattern-3b propagation arc, remaining ~64 unaudited cmd_*.py files.** ~10 commands per wave for SKIP; ~1-2 per wave for SHIP. Total estimated: ~6-8 more sessions to close `_KNOWN_MISSING`. | per-command audit + impl | ~6-8 sessions |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1185 (W1186 / W1187-impl / W1188-audit / W1188-impl / W1189-audit / W1189-impl / W1190 / W1191-audit / W1191-impl / W1192-audit / W1192-impl-ship / W1192-impl-skip / W1193 / W1194-audit / W1194-impl-skip / W1195-audit / W1195-ship / W1195-skip / W1196 / W1197-audit / W1197-impl-skip / W1198-audit — CONSOLIDATE-9)

The CONSOLIDATE-9 pass folds in ~25 completions from the
W1186 → W1198 stretch (contiguous with CONSOLIDATE-8's W1186 →
W1189 window — the W1186 entry intentionally appears in both
indexes for fast cross-lookup). **Three milestones**:
(a) **SARIF SHIP family grew to 22 emitters** — W1192
`cmd_delete_check` (21st, ~165 LOC, BREAK-RISK gate-blocking with
PRIMARY + SECONDARY SARIF locations — first SHIP emitter with
multi-location pattern) + W1195 `cmd_auth_gaps` (22nd, ~180 LOC,
3-tier confidence emitter — `static_analysis` / `structural` /
`heuristic` reusing single-source-of-truth confidence mapping —
first SHIP emitter with explicit 3-tier confidence in SARIF
output); (b) **Pattern-3b propagation arc — 9 waves shipped,
51% gap closed** — `_KNOWN_MISSING` 196 → 96 across W1180 +
W1181 + W1182 + W1185 + W1187 + W1188 + W1189 + W1190 + W1191 +
W1194 + W1195 + W1197 + W1198 (100 commands closed); per-wave
throughput 10-12 docstrings; audit-and-emit asymmetric pattern;
cryptographic hash-stability where required; (c) **Capture
discipline preserved — 6 SHIP candidates captured cleanly
(W1199-W1204)** — `cmd_coverage_gaps` + `cmd_orphan_routes` +
`cmd_pytest_fixtures` + `cmd_test_gaps` + `cmd_test_impact` +
`cmd_verify_imports`; ~7-10d total effort estimated. All 6
emit per-location findings in their JSON envelope today; the
remaining work is `emit_finding()` integration + a SARIF
wrapper per the W1192/W1195 scaffold. Hash-stability invariant
held across all 22 emitters. 131/131 SARIF tests pass throughout.
**~17 SHIPPED + 7 AUDIT-VERDICTs + 1 CAPTURED**.
Strike-throughs preserved on originating pending lines; fast-lookup
index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1186 — `_rule_entry()` `extras` parameter polish~~ | W1186 (CONSOLIDATE-8 / restated in CONSOLIDATE-9) | `extras` parameter added to `_rule_entry()` (mirrors `_result_entry()` extras pattern). 22 LOC. Closes a usability gap noticed during W1179a/b adoption. 131/131 SARIF tests pass. |
| ~~W1187-impl — Wave 4: 12 Bucket B exploration docstrings~~ | W1187-impl (CONSOLIDATE-8 / CONSOLIDATE-9) | `_KNOWN_MISSING` 161 → 149. |
| ~~W1188-audit — Wave 5: 11 Bucket B continuation verdict~~ | W1188-audit (CONSOLIDATE-8 / CONSOLIDATE-9) | **VERDICT SKIP-DISCLOSURE x11.** |
| ~~W1188-impl — Wave 5 docstring landings~~ | W1188-impl (CONSOLIDATE-8 / CONSOLIDATE-9) | `_KNOWN_MISSING` 149 → 138. |
| ~~W1189-audit — Wave 6 batch identification~~ | W1189-audit (CONSOLIDATE-8 / CONSOLIDATE-9) | 10 commands queued: `cmd_help_search` + `cmd_timeline` + `cmd_trends` + `cmd_alerts` + `cmd_weather` + `cmd_ai_ratio` + `cmd_ai_readiness` + `cmd_dogfood` + `cmd_postmortem` + `cmd_dogfood_aggregate`. |
| ~~W1189-impl — Wave 6 docstring landings~~ | W1189-impl (CONSOLIDATE-9) | 10 Bucket B/aggregate commands documented. `_KNOWN_MISSING` 137 → 127. |
| ~~W1190 — `cmd_triage` Bucket D reclassification~~ | W1190 (CONSOLIDATE-9) | Drive-by surfaced during W1189-audit. Re-classified for SKIP-DISCLOSURE rationale. |
| ~~W1191-audit — Wave 7 audit + cmd_delete_check stale-pin~~ | W1191-audit (CONSOLIDATE-9) | **VERDICT SKIP-DISCLOSURE x10 + 1 stale-pin removal**. The `cmd_delete_check` stale `_KNOWN_MISSING` pin was identified as a SHIP-eligible emitter (then 21st via W1192). |
| ~~W1191-impl — Wave 7 docstring landings + stale-pin removal~~ | W1191-impl (CONSOLIDATE-9) | 10 docstring landings + `cmd_delete_check` stale-pin removal. `_KNOWN_MISSING` 125 → 114. |
| ~~W1192-audit — `cmd_delete_check` SHIP + `cmd_migration_safety` SKIP~~ | W1192-audit (CONSOLIDATE-9) | **2 verdicts**: SHIP (21st SARIF emitter) + SKIP (validator-not-detector). |
| ~~W1192-impl-ship — `cmd_delete_check` 21st SARIF SHIP~~ | W1192-impl-ship (CONSOLIDATE-9) | ~165 LOC. BREAK-RISK gate-blocking. **First SHIP emitter with PRIMARY + SECONDARY SARIF locations**: deletion candidate is PRIMARY; surviving refs (code/test/docs/config) are SECONDARY. Hash-stable. 131/131 SARIF tests pass. |
| ~~W1192-impl-skip — `cmd_migration_safety` SKIP-DISCLOSURE docstring~~ | W1192-impl-skip (CONSOLIDATE-9) | ~9 LOC. Validator-not-detector — checks migration-script structure, not file:line findings. |
| ~~W1193 — `action.yml` drift re-audit~~ | W1193 (CONSOLIDATE-9) | No actionable drift — re-confirmed previous audit. |
| ~~W1194-audit — Wave 8: 10 Bucket B/C/E mixed verdicts~~ | W1194-audit (CONSOLIDATE-9) | **VERDICT SKIP-DISCLOSURE x10.** Sibling pattern across exploration + codegen + environment buckets. |
| ~~W1194-impl-skip — Wave 8 docstring landings~~ | W1194-impl-skip (CONSOLIDATE-9) | 10 Bucket B/C/E commands documented. `_KNOWN_MISSING` 113 → 103. |
| ~~W1195-audit — `cmd_auth_gaps` SHIP + `cmd_audit_trail_verify` SKIP~~ | W1195-audit (CONSOLIDATE-9) | **2 verdicts**: SHIP (22nd SARIF emitter; 3-tier confidence) + SKIP (verifier-not-detector). |
| ~~W1195-ship — `cmd_auth_gaps` 22nd SARIF SHIP~~ | W1195-ship (CONSOLIDATE-9) | ~180 LOC. **First SHIP emitter with explicit 3-tier confidence in SARIF output**: `static_analysis` / `structural` / `heuristic` flow from single-source-of-truth confidence map into SARIF `properties.confidence` field. Hash-stable. 131/131 SARIF tests pass. |
| ~~W1195-skip — `cmd_audit_trail_verify` SKIP-DISCLOSURE docstring~~ | W1195-skip (CONSOLIDATE-9) | ~10 LOC. Verifier-not-detector — checks HMAC chain integrity, not file:line findings. |
| ~~W1196 — `breaking_to_sarif()` dormant code investigation~~ | W1196 (CONSOLIDATE-9) | CAPTURED. Investigation only — no code change. Captured for future close-out wave. |
| ~~W1197-audit — Wave 9 mixed verdicts~~ | W1197-audit (CONSOLIDATE-9) | 4 SKIP + 6 SHIP candidates (deferred as W1199-W1204) + 2 UNCLEAR → SKIP. |
| ~~W1197-impl-skip — Wave 9 docstring landings~~ | W1197-impl-skip (CONSOLIDATE-9) | 4 SKIP-DISCLOSURE + 2 UNCLEAR-resolved-to-SKIP docstrings. `_KNOWN_MISSING` 100 → 96. |
| ~~W1198-audit — 6 SHIP candidates + 2 UNCLEAR → SKIP~~ | W1198-audit (CONSOLIDATE-9) | SHIP candidates captured as W1199-W1204 (~7-10d total). 2 UNCLEAR resolved to SKIP. |

### Pending after W1198 (queue for next session — CONSOLIDATE-9)

The CONSOLIDATE-9 pass folds in ~25 completions from the W1186 →
W1198 stretch. 17 SHIPPED + 7 AUDIT-VERDICTs + 1 CAPTURED. **Three
milestones**: SARIF SHIP family at 22 emitters; Pattern-3b
propagation arc 9 waves shipped + 51% gap closed (`_KNOWN_MISSING`
196 → 96); capture discipline preserved with 6 SHIP candidates
(W1199-W1204) deferred cleanly with effort estimates. All
carry-forwards from "Pending after W1189" remain in queue except
where superseded.

| Item | Where | Effort |
|---|---|---|
| **W1199 — `cmd_coverage_gaps` SARIF SHIP impl** (deferred via W1198-audit). Already emits per-location findings in JSON envelope; needs `emit_finding()` integration + SARIF wrapper per W1192/W1195 scaffold. | `src/roam/commands/cmd_coverage_gaps.py` | 1-2d |
| **W1200 — `cmd_orphan_routes` SARIF SHIP impl** (deferred via W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_orphan_routes.py` | 1-2d |
| **W1201 — `cmd_pytest_fixtures` SARIF SHIP impl** (deferred via W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_pytest_fixtures.py` | 1-2d |
| **W1202 — `cmd_test_gaps` SARIF SHIP impl** (deferred via W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_test_gaps.py` | 1-2d |
| **W1203 — `cmd_test_impact` SARIF SHIP impl** (deferred via W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_test_impact.py` | 1-2d |
| **W1204 — `cmd_verify_imports` SARIF SHIP impl** (deferred via W1198-audit). Already emits per-location findings; needs `emit_finding()` + SARIF wrapper. | `src/roam/commands/cmd_verify_imports.py` | 1-2d |
| **W1205+ — Pattern-3b propagation arc, remaining ~90 unaudited cmd_*.py files.** Per W1175-RESEARCH breakdown: ~50 Bucket A likely-SKIP-aggregate + ~13 Bucket B likely-SKIP-environment leftover + ~14-20 Bucket F likely-SHIP + ~10-13 unclear. ~10 commands per wave for SKIP; ~1-2 per wave for SHIP. Total estimated: ~9-12 more sessions to close `_KNOWN_MISSING`. | per-command audit + impl | ~9-12 sessions |
| **W1171 — `cmd_smells` SARIF SHIP impl** (CARRY-FORWARD now superseded by W1165 SHIP in Section 52; check status before re-dispatching). | `src/roam/commands/cmd_smells.py` | check status |
| **W1172 — `cmd_clones` SARIF SHIP impl (dual-location)** (CARRY-FORWARD now superseded by W1160 SHIP in Section 52; check status before re-dispatching). | `src/roam/commands/cmd_clones.py` | check status |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward from "Pending after W1189"). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1185 (W1179a / W1179b / W1186 / W1182-impl / W1187-audit / W1187-impl / W1188-audit / W1188-impl / W1189-audit — CONSOLIDATE-8)

The CONSOLIDATE-8 pass folds in ~10 completions from the W1186 →
W1189 stretch. **Three pillars**: (a) the **SARIF substrate adoption
STRUCTURALLY COMPLETE** — all 19 `*_to_sarif` helpers across the
codebase now use the `_rule_entry()` + `_result_entry()` factories
from `src/roam/output/sarif.py`. W1179a + W1179b shipped 16 emitter
substrate adoptions with hash-stability **cryptographically verified**
via sha256 matches on pre/post SARIF outputs; W1186 polished the
substrate via the `extras` parameter on `_rule_entry()`; (b) the
**Pattern-3b propagation arc extending 3 more waves** — Wave 3
(W1182-impl, 12 Bucket C codegen docstrings) + Wave 4 (W1187-impl,
12 Bucket B exploration/aggregate docstrings) + Wave 5 (W1188-impl,
11 Bucket B continuation docstrings); `_KNOWN_MISSING` 174 → 138
(35 commands closed; 56 total across W1180 → W1188; 29% of the
original gap closed); (c) the **concurrent-merge discipline
battle-tested** across 5+ parallel waves on shared substrate files
via the harness Edit guard's file-read-before-write contract.
**6 SHIPPED + 3 AUDIT-VERDICTs + 1 substrate polish**.
Strike-throughs preserved on originating pending lines; fast-lookup
index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1179a — SARIF substrate adoption wave A (8 emitters)~~ | W1179a (CONSOLIDATE-8) | 8 emitter substrate adoption: every site now uses `_rule_entry()` + `_result_entry()` factories from `src/roam/output/sarif.py`. Hash-stability **cryptographically verified** via sha256 matches on pre/post SARIF outputs (strongest invariant class). ~LOC-neutral overall (honest discipline per W1080). 131/131 SARIF tests pass. |
| ~~W1179b — SARIF substrate adoption wave B (8 emitters)~~ | W1179b (CONSOLIDATE-8) | 8 more emitter substrate adoption. PARTIAL extraction from W1177-audit now STRUCTURALLY COMPLETE across all 19 `*_to_sarif` helpers. Hash-stability sha256-verified. 131/131 SARIF tests pass. |
| ~~W1186 — `_rule_entry()` `extras` parameter polish~~ | W1186 (CONSOLIDATE-8) | `extras` parameter added to `_rule_entry()` (mirrors `_result_entry()` extras pattern). 1 inline refactor (taint emitter). Closes a usability gap noticed during W1179a/b adoption when sites needed to pass rule-level extras. 131/131 SARIF tests pass. |
| ~~W1182-impl — Wave 3: 12 Bucket C codegen SARIF-skip docstrings~~ | W1182-impl (CONSOLIDATE-8) | 12 codegen commands documented: cmd_attest / cmd_capsule / cmd_agent_export / cmd_agents_md / cmd_graph_export / cmd_cga / cmd_sbom / cmd_skill_generate / cmd_pr_comment_render / cmd_audit_trail_export / cmd_evidence_oscal / cmd_fingerprint. `_KNOWN_MISSING` 173 → 161. Drive-by: cmd_lsp anchor newline fix. |
| ~~W1187-audit — Wave 4: 12 Bucket B exploration/aggregate commands~~ | W1187-audit (CONSOLIDATE-8) | **VERDICT SKIP-DISCLOSURE x12.** Sibling pattern to W1182-audit (codegen-not-analysis) and W1148 (aggregate-not-located-finding). |
| ~~W1187-impl — Wave 4 docstring landings~~ | W1187-impl (CONSOLIDATE-8) | 12 Bucket B exploration/aggregate commands documented. `_KNOWN_MISSING` 161 → 149. |
| ~~W1188-audit — Wave 5: 11 Bucket B continuation commands~~ | W1188-audit (CONSOLIDATE-8) | **VERDICT SKIP-DISCLOSURE x11.** Sibling-confirms the W1187 Bucket B pattern. |
| ~~W1188-impl — Wave 5 docstring landings~~ | W1188-impl (CONSOLIDATE-8) | 11 Bucket B continuation commands documented. `_KNOWN_MISSING` 149 → 138. |
| ~~W1189-audit — Wave 6 batch identified (10 commands)~~ | W1189-audit (CONSOLIDATE-8) | `cmd_help_search` + `cmd_timeline` + `cmd_trends` + `cmd_alerts` + `cmd_weather` + `cmd_ai_ratio` + `cmd_ai_readiness` + `cmd_dogfood` + `cmd_postmortem` + `cmd_dogfood_aggregate`. Queued for W1190+ impl. |

### Pending after W1189 (queue for next session — CONSOLIDATE-8)

The CONSOLIDATE-8 pass folds in ~10 completions from the W1186 →
W1189 stretch. 6 SHIPPED + 3 AUDIT-VERDICTs + 1 substrate polish.
**Three pillars**: SARIF substrate adoption STRUCTURALLY COMPLETE
across all 19 emitters; Pattern-3b propagation arc extending 3 more
waves (`_KNOWN_MISSING` 174 → 138; 56 commands closed across
W1180 → W1188); concurrent-merge discipline battle-tested across
5+ parallel waves. All carry-forwards from "Pending after W1185"
remain in queue except where superseded.

| Item | Where | Effort |
|---|---|---|
| **W1190+ — Wave 6 impl: 10 Bucket B / aggregate commands** (identified via W1189-audit). `cmd_help_search` + `cmd_timeline` + `cmd_trends` + `cmd_alerts` + `cmd_weather` + `cmd_ai_ratio` + `cmd_ai_readiness` + `cmd_dogfood` + `cmd_postmortem` + `cmd_dogfood_aggregate`. Expected yield: ~50-80 LOC docstrings; `_KNOWN_MISSING` 138 → 128. | 10 `cmd_*.py` sites | 2-3h |
| **W1191+ Wave 7+ candidates — remaining 138 unaudited cmd_*.py files.** Per W1175-RESEARCH the remaining 138 break down as: ~65 Bucket A likely-SKIP-aggregate + ~13 Bucket B likely-SKIP-environment leftover + ~14-20 Bucket F likely-SHIP + ~10-15 unclear. ~10 commands per wave for SKIP; ~1-2 per wave for SHIP. Total estimated: ~12-15 sessions to close `_KNOWN_MISSING`. | per-command audit + impl | ~12-15 sessions |
| **W1171 — `cmd_smells` SARIF SHIP impl** (carry-forward from "Pending after W1185"). | `src/roam/commands/cmd_smells.py` | 2-3h |
| **W1172 — `cmd_clones` SARIF SHIP impl (dual-location)** (carry-forward from "Pending after W1185"). | `src/roam/commands/cmd_clones.py` | 2-3h |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward from "Pending after W1185"). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1176 (W1151 / W1156 / W1162 / W1175-RESEARCH / W1177-audit / W1178 / W1180 / W1181-audit / W1181-impl / W1182-audit / W1185-audit / W1185-impl / W1164-VERDICT / W1176 — CONSOLIDATE-7)

The CONSOLIDATE-7 pass folds in ~14 completions from the W1176 → W1185
stretch. **Three major systemic shifts**: (a) the **SARIF helper
substrate launched** via W1178 — `_rule_entry()` + `_result_entry()`
factories in `sarif.py` (~80 LOC) + 3 adopters (`cmd_dead` +
`cmd_critique` + `cmd_partition`, ~50 LOC subtractive). 131/131 SARIF
tests pass. W1179a + W1179b refactoring 17 more emitters in parallel;
(b) the **Pattern-3b propagation arc launched** via W1175-RESEARCH
(684-line memo with bucket inventory + asymmetric propagation strategy)
+ Wave 1 (W1180, 10 bootstrap commands, +95 LOC) + Wave 2 (W1181-audit
+ W1181-impl, 10 local-state commands) + W1185 outliers (W1185-audit
+ W1185-impl, +15 LOC) — `_KNOWN_MISSING` 196 → 174 (33 done, 162 to
go); (c) **vocabulary canonicalization disciplines sealed** via
W1151 (`_to_level()` cargo-cult `.upper()` removal across 7 sites in
`sarif.py`) + W1156 (`REFERENCE_REMOVAL_VERDICTS` substrate fully
operational, carry-forward) + W1162 (cmd_flag_dead likely_stale
canonical + dual-form display preservation, mirrors W1156) + W1176
(cmd_pr_analyze NO_CHANGES → NOCHANGES, realises W1164 verdict).
**8 SHIPPED + 2 RESEARCH memos + 3 AUDIT-VERDICTs + 1 PARTIAL
audit-extraction**. Strike-throughs preserved on originating pending
lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1175-RESEARCH — SARIF-disclosure propagation strategy memo (196-file gap)~~ | W1175-RESEARCH (CONSOLIDATE-7) | 684-line memo at `dev/SARIF-DISCLOSURE-196-PROPAGATION-PLAN-2026-05-16.md`. Bucket inventory: ~135 likely-SKIP + ~14-20 likely-SHIP + ~17 unclear. Asymmetric propagation pattern: bulk audit-and-emit for SKIP; 1:1 audit-then-impl for SHIP. Total estimated 30-50 batches. |
| ~~W1177-audit — SARIF helper-substrate extraction audit~~ | W1177-audit (CONSOLIDATE-7) | **VERDICT PARTIAL EXTRACTION.** 5-phase pipeline surveyed across 20 SARIF emitters; 3 patterns identified (Fixed-rule / Dynamic-rule / Complex-multi-rule). `_rule_entry()` + `_result_entry()` helpers viable; ~500 LOC subtractive ceiling. |
| ~~W1178 — SARIF helper substrate + 3 adopters~~ | W1178 (CONSOLIDATE-7) | `_rule_entry()` + `_result_entry()` factories in `src/roam/output/sarif.py` (~80 LOC helpers) + 3 adopters: `cmd_dead` + `cmd_critique` + `cmd_partition` (~50 LOC subtractive). 131/131 SARIF tests pass. Hash-stable. |
| ~~W1180 — Wave 1: 10 bootstrap commands SARIF-skip docstrings~~ | W1180 (CONSOLIDATE-7) | +95 LOC across 10 bootstrap commands (one-time human-driven setup / config / version sibling pattern). Drive-by: pruned 10 stale `_KNOWN_MISSING` pins. `_KNOWN_MISSING` 196 → 186 (W1180 contribution). |
| ~~W1181-audit — Wave 2: 10 Bucket D local-state commands~~ | W1181-audit (CONSOLIDATE-7) | **VERDICT SKIP-DISCLOSURE x10.** Substrate-state nouns: mode / runs / lease / memory / permits / annotations / suppress / replay / agent-score / agents-md. None are file:line findings emitters. `cmd_lsp` + `cmd_rules_validate` flagged as outliers → W1185. |
| ~~W1181-impl — Wave 2 docstring landings~~ | W1181-impl (CONSOLIDATE-7) | 10 Bucket D commands documented (substrate-state nouns). Concurrent merge with W1180/W1185-impl absorbed cleanly via the Edit guard. `_KNOWN_MISSING` -10 (W1181 contribution). |
| ~~W1182-audit — Wave 3: 12 Bucket C codegen commands~~ | W1182-audit (CONSOLIDATE-7) | 12 codegen commands identified for Wave 3 impl: cmd_attest / cmd_capsule / cmd_agent_export / cmd_agents_md / cmd_graph_export / cmd_cga / cmd_sbom / cmd_skill_generate / cmd_pr_comment_render / cmd_audit_trail_export / cmd_evidence_oscal / cmd_fingerprint. Codegen-artifact-not-analysis rationale (sibling pattern to W1174). |
| ~~W1185-audit — Outlier commands (cmd_lsp + cmd_rules_validate)~~ | W1185-audit (CONSOLIDATE-7) | **VERDICT SKIP x2.** `cmd_lsp` SKIP (editor protocol, not CI/findings); `cmd_rules_validate` SKIP (validator-not-detector — rules check existing rule definitions, do not analyze code). |
| ~~W1185-impl — Outlier docstring landings~~ | W1185-impl (CONSOLIDATE-7) | +15 LOC docstrings (LSP editor-protocol + validator-vs-code-analyzer rationales). `_KNOWN_MISSING` -2 (W1185 contribution). |
| ~~W1162 — `cmd_flag_dead` likely-stale canonicalization~~ | W1162 (CONSOLIDATE-7) | Canonical `likely_stale` + display `"likely-stale"` preserved via `_STALENESS_DISPLAY` map. Mirrors W1156 dual-form normalization pattern. (Confirmation entry — landed in CONSOLIDATE-6; cross-linked here for visibility.) |
| ~~W1164 / W1176 — `cmd_pr_analyze` NOCHANGES rename~~ | W1164-VERDICT + W1176 (CONSOLIDATE-7) | 3 LOC. `NO_CHANGES` → `NOCHANGES` sibling-aligned bare UPPERCASE. 154/155 tests pass. (Confirmation entry — landed in CONSOLIDATE-6; cross-linked here for visibility.) |
| ~~W1156 — `REFERENCE_REMOVAL_VERDICTS` substrate operational~~ | W1156 (CONSOLIDATE-7) | ~100 LOC substrate fully operational this batch. Dual-form normalization (canonical underscore + display hyphen). Carry-forward from W1156-CONSOLIDATE. |
| ~~W1151 — `_to_level()` cargo-cult `.upper()` removal~~ | W1151 (CONSOLIDATE-7) | 7 sites in `sarif.py`. Hash-stable. The canonical `_to_level()` output was already uppercase at the call site. |

### Pending after W1185 (queue for next session — CONSOLIDATE-7)

The CONSOLIDATE-7 pass folds in ~14 completions from the W1176 → W1185
stretch. 8 SHIPPED + 2 RESEARCH memos + 3 AUDIT-VERDICTs + 1 PARTIAL
audit-extraction. **Three major systemic shifts**: SARIF helper
substrate launched; Pattern-3b propagation arc launched
(`_KNOWN_MISSING` 196 → 174); vocabulary canonicalization disciplines
sealed. **W1179a + W1179b + W1182-impl in flight**; close-out captured
at the next CONSOLIDATE checkpoint. All other carry-forwards from
"Pending after W1176" remain in queue except where superseded.

| Item | Where | Effort |
|---|---|---|
| **W1179a — SARIF helper-adoption refactor wave A (~8 emitters).** Fixed-rule + Dynamic-rule pattern adopters from W1177-audit. In flight as this CONSOLIDATE runs; close-out captured at the next CONSOLIDATE checkpoint. Expected yield: ~130-150 LOC subtractive. | `src/roam/output/sarif.py` + 8 `cmd_*.py` sites | 4-6h |
| **W1179b — SARIF helper-adoption refactor wave B (~9 emitters).** Complex-multi-rule + remaining Dynamic-rule pattern adopters from W1177-audit. In flight as this CONSOLIDATE runs; close-out captured at the next CONSOLIDATE checkpoint. Expected yield: ~150-200 LOC subtractive (lower per-site yield on complex emitters). | `src/roam/output/sarif.py` + 9 `cmd_*.py` sites | 4-6h |
| **W1182-impl — Wave 3: 12 Bucket C codegen commands SARIF-skip docstrings.** Identified via W1182-audit. Sibling pattern to W1174 (cmd_test_scaffold). Expected yield: ~70-100 LOC docstrings; `_KNOWN_MISSING` -12. | 12 codegen `cmd_*.py` sites | 2-3h |
| **W1186+ Wave 4 candidates — Bucket A (likely-SKIP-aggregate, ~85 commands) + Bucket B (likely-SKIP-environment, ~25 commands) + Bucket F (likely-SHIP-file-located, ~14-20 commands) bulk audit-and-emit.** Per W1175-RESEARCH, ~10 commands per wave for SKIP; ~1-2 commands per wave for SHIP. Total estimated effort: ~15-25 sessions across the remaining 162 unaudited cmd_*.py. | per-command audit + impl | 30+ sessions |
| **W1171 — `cmd_smells` SARIF SHIP impl** (carry-forward from "Pending after W1176"). | `src/roam/commands/cmd_smells.py` | 2-3h |
| **W1172 — `cmd_clones` SARIF SHIP impl (dual-location)** (carry-forward from "Pending after W1176"). | `src/roam/commands/cmd_clones.py` | 2-3h |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward from "Pending after W1176"). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward from "Pending after W1176"). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward from "Pending after W1176"). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward from "Pending after W1176"). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward from "Pending after W1176"). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward; W1162 likely-stale canonicalization landed but `warnings_out` plumb remains open). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1157 (W1159 / W1160 / W1162 / W1165 / W1167 / W1168 / W1169 / W1173 / W1174 / W1176 / W1158-VERDICT / W1164-VERDICT / W1170-VERDICT — CONSOLIDATE-6)

The CONSOLIDATE-6 pass folds in ~13 completions from the W1156 → W1176
stretch. **Three structural inflections**: (a) the SARIF SHIP family
expanded from 17 commands (post-W1146) to **20 commands** via W1159
(`cmd_partition`) + W1160 (`cmd_affected_tests`) + W1165 (`cmd_impact`),
with W1171 (`cmd_smells` SHIP, ~250 LOC) + W1172 (`cmd_clones` SHIP,
~300 LOC) in flight; (b) the W1169 SARIF-disclosure-coverage CI lint
discovered **196 unaudited `cmd_*.py`** vs the W1166-RESEARCH 4-8
estimate, with a `_KNOWN_MISSING` frozenset pinning the gap;
(c) two vocabulary canonicalization sweeps (W1162 likely-stale +
W1176 NO_CHANGES → NOCHANGES) extended the W1156 dual-form normalization
pattern. **~1000+ LOC of impl** (W1165 +413, W1169 +403 test, W1159
+189, W1160 +147, W1162 +13, W1167 +10, W1168 +28, W1173 +8, W1174 +8,
W1176 +3) — most prolific batch this session. 8 SHIPPED + 2 RESEARCH
memos + 3 AUDIT-VERDICTs. Strike-throughs preserved on originating
pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1158 — SARIF source-drift audit~~ | W1158-VERDICT (CONSOLIDATE-6) | **VERDICT HEALTHY DRIFT.** 4 SARIF sources surveyed: action.yml 7-cmd subset ⊂ cli.py 18-cmd `_SARIF_CONSUMERS`; sarif.py 14 emitters + 4 external; 3 cli-only. No bug. Realised via W1167 + W1168. |
| ~~W1167 — action.yml `_SUPPORTED_SARIF` subset intent comment~~ | W1167 (CONSOLIDATE-6) | +10 LOC `action.yml` comment block documenting the deliberate 7-command subset of cli.py 18-command `_SARIF_CONSUMERS`. Anchors the subset rationale at the YAML source. Hash-stable (comment only). |
| ~~W1168 — cli.py ⊃ action.yml SARIF subset CI lint~~ | W1168 (CONSOLIDATE-6) | +28 LOC new `test_action_yml_supported_sarif_subset_of_cli_consumers` in `tests/test_sarif_consumer_list.py`. Pins the subset relationship — blocks any future drift where action.yml gains a SARIF entry that cli.py doesn't recognize. |
| ~~W1165 — `cmd_impact` SARIF SHIP~~ | W1165 (CONSOLIDATE-6) | ~413 LOC across 6 files. 4 finding families: `affected-file` (importance→severity), `direct-dependent`, `sf-convention-test`, `indirect-ref`. `_SARIF_CONSUMERS` 15→16. |
| ~~W1166-RESEARCH — SARIF-disclosure-pattern maturity memo~~ | W1166-RESEARCH (CONSOLIDATE-6) | 555-line memo at `dev/SARIF-DISCLOSURE-PATTERN-MATURITY-2026-05-16.md`. 14 audits surveyed; ZERO contested verdicts. Anti-recommendation: do NOT extract shared docstring constant. Top recommendation: ship the CI lint (DONE this batch via W1169). |
| ~~W1169 — SARIF-disclosure coverage CI lint~~ | W1169 (CONSOLIDATE-6) | +403 LOC new `tests/test_sarif_disclosure_coverage.py`. **Key discovery**: 196 unaudited `cmd_*.py` (vs W1166-RESEARCH 4-8 estimate). `_KNOWN_MISSING` frozenset pins the gap; W1175-RESEARCH plans propagation. |
| ~~W1170-bundle — SARIF audience-disclosure quartet~~ | W1170-VERDICT (CONSOLIDATE-6) | **VERDICT 2x SHIP + 2x SKIP-DISCLOSURE.** `cmd_smells` SHIP (W1171 in flight, ~250 LOC) + `cmd_clones` SHIP (W1172 in flight, ~300 LOC, dual-location); `cmd_vibe_check` SKIP-DISCLOSURE (W1173 docstring) + `cmd_test_scaffold` SKIP-DISCLOSURE (W1174 docstring). |
| ~~W1159 — `cmd_partition` SARIF SHIP~~ | W1159 (CONSOLIDATE-6) | ~189 LOC. PRIMARY + up-to-10 SECONDARY locations. `conflict_risk` severity scaling. `_SARIF_CONSUMERS` 17→18. Concurrent-merge with W1160 surfaced via the Edit guard and resolved cleanly. |
| ~~W1160 — `cmd_affected_tests` SARIF SHIP~~ | W1160 (CONSOLIDATE-6) | ~147 LOC. 3 closed-enum rules: `direct`=error / `transitive`=warning / `colocated`=note. `_SARIF_CONSUMERS` 16→17. |
| ~~W1173 — `cmd_vibe_check` SARIF-skip docstring~~ | W1173 (CONSOLIDATE-6) | +8 LOC. Names `roam findings list --detector vibe-check` as the per-finding path. SKIP-DISCLOSURE docstring sibling to W1148 / W1144 / W1154-impl. |
| ~~W1174 — `cmd_test_scaffold` SARIF-skip docstring~~ | W1174 (CONSOLIDATE-6) | +8 LOC. Codegen-artifact-not-analysis rationale; distinct from W1148's invocation-scoped template. |
| ~~W1164 — `cmd_pr_analyze` NO_CHANGES naming audit~~ | W1164-VERDICT (CONSOLIDATE-6) | **VERDICT RENAME to `NOCHANGES`** (option a sibling-aligned bare UPPERCASE). 3 sites, no hard-coded test assertions, 5 LOC effort. Realised via W1176. |
| ~~W1162 — `cmd_flag_dead` likely-stale canonicalization~~ | W1162 (CONSOLIDATE-6) | +13/-5 LOC. Canonical `likely_stale` applied; display `"likely-stale"` preserved via new `_STALENESS_DISPLAY` map. Extends W1156 dual-form normalization pattern. |
| ~~W1176 — `cmd_pr_analyze` NO_CHANGES → NOCHANGES rename~~ | W1176 (CONSOLIDATE-6) | 3 LOC. Realises W1164 audit verdict. 154/155 tests pass. |

### Closures since W1149 (W1103 / W1104 / W1154-impl / W1156 / W1154-VERDICT / W1134-VERDICT — W1156-CONSOLIDATE)

The W1156 consolidation pass folds in ~12 completions from the W1149 →
W1156 stretch. **Two structural verdicts**: (a) the SARIF-disclosure
pattern now spans **9 aggregate-style commands** (W1144 + W1145 + W1148
+ W1152 + W1154-impl x6), formally documenting "invocation-scoped
aggregates have no SARIF locations[]" as a stable design rule; (b)
reference-removal verdicts elevated to a closed-enum frozenset
(`REFERENCE_REMOVAL_VERDICTS`) via W1156 — drift guard pinned in
`tests/test_evidence_v0.py` + dual-form normalization preserves CLI
display. `publish.yml` hardened via W1103 (`persist-credentials:false`
on build + smoke checkout) + W1104 (3-site robust wheel-glob assertion).
**~180 LOC of impl** — 5 SHIPPED + 2 AUDIT-VERDICTs + 3 drive-by
W-tasks captured (W1155 audit pending / W1157 audit pending / W1158
NOT NEEDED). Strike-throughs preserved on originating pending lines;
fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1103 — `publish.yml` `persist-credentials:false` on build + smoke checkout~~ | W1103 (W1156-CONSOLIDATE) | +6 LOC `publish.yml`. Build + smoke jobs got `persist-credentials: false` on `actions/checkout`; publish job correctly untouched (Trusted Publishing OIDC needs the credential). Supply-chain hardening — removes leaked-credentials attack surface on the non-publish steps. |
| ~~W1104 — `publish.yml` robust 3-site wheel-glob assertion~~ | W1104 (W1156-CONSOLIDATE) | +36/-3 LOC `publish.yml`. 3 `dist/*.whl` sites (PEP 639 verify + v2-commands verify + SBOM `pip install`) converted to robust single-wheel assertion pattern (`shopt -s nullglob` + length-1 array check + quoted variable). Above the 6-10 LOC estimate because the pattern is repeated at 3 sites. |
| ~~W1154 — SARIF-disclosure audit (6 third-tier commands)~~ | W1154-VERDICT (W1156-CONSOLIDATE) | **VERDICT SKIP-DISCLOSURE x6** for cmd_orchestrate / cmd_diagnose / cmd_oracle / cmd_plan / cmd_brief / cmd_next. None in the SARIF `action.yml` allowlist. Aggregate-style commands. Verdicts realised at the per-site source via W1154-impl docstrings. |
| ~~W1154-impl — 6 SARIF-skip docstring landings~~ | W1154-impl (W1156-CONSOLIDATE) | +36 LOC + 6 blank separators across 6 files. SARIF-skip docstrings mirror the W1144 / W1148 / W1152 pattern. Module-level docstrings anchor the rationale at the per-site source. Hash-stable. **SARIF-disclosure pattern now spans 9 commands.** |
| ~~W1134 — reference-removal verdict vocabulary audit~~ | W1134-VERDICT (W1156-CONSOLIDATE) | **VERDICT LOCAL-CLOSED-ENUM.** Reference-removal verdicts are orthogonal to `POLICY_DECISIONS` — overloading would have conflated "is this code-string safe to delete?" with "did the policy gate pass?". Recommendation: dedicated `REFERENCE_REMOVAL_VERDICTS` frozenset. Verdict realised by W1156 impl. |
| ~~W1156 — `REFERENCE_REMOVAL_VERDICTS` closed-enum substrate~~ | W1156 (W1156-CONSOLIDATE) | ~100 LOC full substrate. New `REFERENCE_REMOVAL_VERDICTS` frozenset in `src/roam/evidence/_vocabulary.py` (6 members: `safe_to_remove` / `review` / `load_bearing` / `safe` / `likely_safe` / `break_risk`) + drift guard test in `tests/test_evidence_v0.py` + `_validate_verdict` helpers in `cmd_refs_text` + `cmd_delete_check`. Dual-form normalization (display `"SAFE-TO-REMOVE"` round-trips to canonical `"safe_to_remove"`). 56 + 34 tests pass. |

### Closures since W1133 (W1136 / W1141 / W1144 / W1145 / W1148 / W1100 / W1099-narrow / W1139-RESEARCH / W1085-VERDICT / W1146-VERDICT / W1147-VERDICT — W1149-CONSOLIDATE)

The W1149 consolidation pass folds in 11 completions + 4 captures from the
W1136 → W1149 stretch. **Three structural outcomes**: (a) the **W332
Pattern-3b CLI-boundary thread is functionally closed at v13.x** via the
W1141 4th-mirror drift guard; (b) the SARIF audience-disclosure trilogy
(W1144 + W1145 + W1148) propagated the deliberate-skip rationale docstring
across cmd_doctor + cmd_audit + cmd_pr_risk; (c) the W1100 + W1099-narrow
CLI sweep landed the biggest user-facing CLI surface change in 30+
sections without any breaking rename. **~410 LOC of impl + 361 LOC
research memo** — 10 SHIPPED + 3 AUDIT-VERDICTs + 6 drive-by W-tasks
captured (W1140 / W1141-followup / W1142 / W1143 / W1146-impl /
W1149-audit-pending). Strike-throughs preserved on originating pending
lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1136 — Option-dest input_path discipline CI lint~~ | W1136 (W1149-CONSOLIDATE) | New `tests/test_w1136_click_option_input_path_dest_lint.py` (339 LOC). 6 canonical + 2 legacy carve-out sites. 4 lint tests + 1 sanity test pass. Extends the W1111 + W1121-target AST drift-block pattern to the option-dest axis. |
| ~~W1141 — `_PARAM_ALIASES` 4th-mirror drift guard~~ | W1141 (W1149-CONSOLIDATE) | +36 LOC drift guard in `tests/test_mcp_param_names.py`. `_PARAM_ALIASES` table now quadruple-pinned across `mcp_server.py` + W1111 + W1121-target + W1141 mirrors. **W332 Pattern-3b CLI-boundary thread FUNCTIONALLY CLOSED at v13.x.** |
| ~~W1144 — `cmd_doctor` SARIF skip rationale docstring~~ | W1144 (W1149-CONSOLIDATE) | +6 LOC on `cmd_doctor.py:1-12` documenting deliberate text+JSON-only design. Environment-scoped diagnostics, no file:line. Realises the W1085 SKIP verdict at the per-site source. |
| ~~W1145 — `cmd_audit` SARIF flow docstring~~ | W1145 (W1149-CONSOLIDATE) | +9 LOC on `cmd_audit.py` documenting composed-subcommand SARIF flow + no top-level --sarif flag (each subcommand emits its own SARIF if relevant). |
| ~~W1148 — `cmd_pr_risk` SARIF skip rationale docstring~~ | W1148 (W1149-CONSOLIDATE) | +14/-4 LOC on `cmd_pr_risk.py` documenting SARIF-skip rationale (invocation-scoped aggregates with `subject_kind="commit"`; action.yml allowlist already excludes pr-risk by design). Realises the W1147 SKIP verdict at the per-site source. |
| ~~W1085 — `cmd_doctor` SARIF audit~~ | W1085-VERDICT (W1149-CONSOLIDATE) | **VERDICT SKIP-SARIF.** Environment-scoped diagnostics (Python version, index status, watcher status, OneDrive/Dropbox detection); no file:line. Verdict landed via W1144 docstring. |
| ~~W1146 — `cmd_critique` SARIF audit~~ | W1146-VERDICT (W1149-CONSOLIDATE) | **VERDICT SHIP-SARIF.** File-located findings: clones-not-edited findings have file:line; impact entries have file paths; intent is diff-wide. Impl dispatched as W1146-impl (in flight as this CONSOLIDATE runs). |
| ~~W1147 — `cmd_pr_risk` SARIF audit~~ | W1147-VERDICT (W1149-CONSOLIDATE) | **VERDICT SKIP-SARIF.** Invocation-scoped aggregates (`subject_kind="commit"`). action.yml allowlist already excludes pr-risk by design. Verdict landed via W1148 docstring. |
| ~~W1100 — CLI symbol-cluster metavar alignment~~ | W1100 (W1149-CONSOLIDATE) | 14 sites across 11 files got `metavar="SYMBOL"` (or SYMBOL_OR_PATH / [SYMBOL] context-aware variants on cmd_test_scaffold + cmd_testmap) + docstring identifier-tone refresh. ~28 LOC. Hash-stable. cmd_explain_command + cmd_plugins correctly NOT touched (DOMAIN-DISTINCT per W1108/W1120 from Section 49). Strategy D from W1102-RESEARCH (Section 48). |
| ~~W1099-narrow — `--file` → `--path` harmonization (6 CLI-only commands)~~ | W1099-narrow (W1149-CONSOLIDATE) | --file → --path harmonization across 6 CLI-only commands + mcp_server.py + 2 test files. Click required=True + alias limitation surfaced (cmd_triage manual UsageError adaptation). 508/509 tests pass. cmd_pr_bundle deferred to W1141-followup. ~80 LOC. |
| ~~W1139-RESEARCH — Pattern-3b CLI-boundary completeness memo~~ | W1139-RESEARCH (W1149-CONSOLIDATE) | 361-line memo at `dev/PATTERN-3B-CLI-BOUNDARY-COMPLETENESS-2026-05-15.md`. **Coverage matrix**: 6 axes SHIPPED + 2 PARTIAL + 0 GAP. Key finding: W332 functionally closeable in 15 min via W1141 (DONE this batch). Companion to W1102-RESEARCH (Section 48). |

### Pending after W1176 (queue for next session — CONSOLIDATE-6)

The CONSOLIDATE-6 pass folds in ~13 completions from the W1156 → W1176
stretch. 8 SHIPPED + 2 RESEARCH memos + 3 AUDIT-VERDICTs. **Three
structural inflections**: SARIF SHIP family 17 → 20 commands;
W1169 CI lint surfaces 196 unaudited `cmd_*.py`; W1162 + W1176
vocabulary canonicalization sweeps. **W1171 + W1172 + W1175-RESEARCH
in flight**; close-out captured at the next CONSOLIDATE checkpoint.
All other carry-forwards from "Pending after W1157" remain in queue
except where superseded (W1155 superseded mid-window by the W1170-bundle
+ W1173/W1174 docstring landings + the W1159/W1160/W1165 SHIPs;
W1157 superseded by W1162 likely-stale canonicalization).

| Item | Where | Effort |
|---|---|---|
| **W1171 — `cmd_smells` SARIF SHIP impl.** SHIP verdict from W1170-bundle audit. In flight as this CONSOLIDATE runs (~250 LOC); close-out captured at the next CONSOLIDATE checkpoint. | `src/roam/commands/cmd_smells.py` | 2-3h |
| **W1172 — `cmd_clones` SARIF SHIP impl (dual-location).** SHIP verdict from W1170-bundle audit. In flight as this CONSOLIDATE runs (~300 LOC, dual-location pattern: clone-class members + clone-class anchor); close-out captured at the next CONSOLIDATE checkpoint. | `src/roam/commands/cmd_clones.py` | 2-3h |
| **W1175-RESEARCH — Propagation strategy memo for the 196-file SARIF-disclosure coverage gap.** W1169 surfaced 196 unaudited `cmd_*.py` vs W1166-RESEARCH 4-8 estimate. Memo plans the propagation strategy (batched audits vs single-source-of-truth docstring constant vs deferred per-command sweeps). In flight as this CONSOLIDATE runs; close-out captured at the next CONSOLIDATE checkpoint. | `dev/SARIF-DISCLOSURE-PROPAGATION-STRATEGY-2026-05-16.md` | 3-4h |
| **W1177-W1180 candidates — drive-by captures from W1169 + W1170 discoveries.** Captured during the CONSOLIDATE-6 dispatch window. Per-command audit + impl follow-ups; close-out captured at the next CONSOLIDATE checkpoint. | TBD | TBD |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carry-forward from "Pending after W1157"). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward from "Pending after W1157"). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward from "Pending after W1157"). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward from "Pending after W1157"). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward from "Pending after W1157"). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward; W1162 likely-stale canonicalization landed but `warnings_out` plumb remains open). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Pending after W1157 (queue for next session — W1156-CONSOLIDATE)

The W1156 consolidation pass folds in ~12 completions from the W1149 →
W1156 stretch. 5 SHIPPED + 2 AUDIT-VERDICTs + 3 drive-by W-tasks captured
(W1155 / W1157 / W1158-not-needed). **Two structural verdicts**: the
SARIF-disclosure pattern formalized across **9 commands** (W1144 + W1145
+ W1148 + W1152 + W1154-impl x6) and the `REFERENCE_REMOVAL_VERDICTS`
closed-enum substrate (W1156). All other carry-forwards from "Pending
after W1149" remain in queue except where superseded (W1146-impl
realised by W1152 mid-window per the W1149-W1156 ship arc;
W1149-audit-pending close-out captured at this CONSOLIDATE).

| Item | Where | Effort |
|---|---|---|
| **W1155 — Third-tier SARIF-disclosure audit (5 commands).** Audit dispatched but not yet returned a verdict as of this CONSOLIDATE's window. Targets: cmd_fleet / cmd_partition / cmd_affected_tests / cmd_impact / cmd_context. On a likely-SKIP outcome the SARIF-disclosure pattern reaches 14 commands. | per-command audit | 2-3h |
| **W1157 — `cmd_flag_dead` hyphenation drift drive-by.** Audit dispatched; verdict not yet returned. Likely a per-site help-text follow-up. | `src/roam/commands/cmd_flag_dead.py` | 1h |
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carried forward from "Pending after W1149"). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep** (carry-forward from "Pending after W1149"). | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path`** (carry-forward from "Pending after W1149"). | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family** (carry-forward from "Pending after W1149"). | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED)** (carry-forward from "Pending after W1149"). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward). | per-command CLI surface | DEFERRED to v14.0 |

### Pending after W1149 (queue for next session — W1149-CONSOLIDATE)

The W1149 consolidation pass folds in 11 completions + 4 captures from
the W1136 → W1149 stretch. 10 SHIPPED + 3 AUDIT-VERDICTs + 6 drive-by
W-tasks captured (W1140 / W1141-followup / W1142 / W1143 / W1146-impl /
W1149-audit-pending). **Three structural outcomes**: the W332 Pattern-3b
thread functional closure, the SARIF audience-disclosure trilogy
propagation, and the W1100 + W1099-narrow CLI sweep (14 metavar-aligned
commands + 6 --file→--path canonicalizations). All other carry-forwards
from "Pending after W1133" remain in queue except where superseded
(W1130 still open; W1117 still open; W1112-W1115 still open; W1098
DEFERRED to v14.0 unchanged; W1121 superseded by W1121-target +
W1136 — the option-dest axis is now SHIPPED via W1136 and the path
axis is harmonized via W1099-narrow).

| Item | Where | Effort |
|---|---|---|
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift** (carried forward from "Pending after W1133"). | `CLAUDE.md` (docstring section) | 30 min |
| **W1140 — Slug dash-vs-underscore migration drive-by from W1100 sweep.** Several command slugs mix `dash-case` vs `snake_case` at the CLI registry layer vs the module name layer. Documentation-grade follow-up. | per-site audit | 1-2h |
| **W1141-followup — `cmd_pr_bundle --file → --path` deferred from W1099-narrow.** Click's `required=True` + alias limitation on `pr-bundle init`'s `--file` flag needed manual `UsageError` adaptation; deferred for sequencing. | `src/roam/commands/cmd_pr_bundle.py` | 1-2h |
| **W1142 — `--limit` / `--top` Pattern-3b silent-fail family.** 39+ sites use either `--limit` or `--top` for the same concept (limit the number of results returned). Drive-by from W1139-RESEARCH coverage matrix work. Pattern-3b vocabulary-mismatch family expansion. | per-command CLI surface | 3-4h |
| **W1143 — Path-axis option-dest lint (DEFERRED).** Drive-by from W1136: the option-dest discipline could extend to the path axis (`--path` / `--paths` / `--file` clusters). Deferred (lower priority than W1142 — the path axis has already been harmonized at the axiomatic level via W1099-narrow + W1141). | `tests/test_w1143_click_option_path_dest_lint.py` (new file) | DEFERRED |
| **W1146-impl — `cmd_critique` SARIF emit_runtime_notifications impl.** SHIP verdict from W1146 audit. In flight as this CONSOLIDATE runs; close-out captured at the next CONSOLIDATE checkpoint. | `src/roam/commands/cmd_critique.py` | 2-3h |
| **W1149-audit-pending — TBD audit verdict.** The W1149 audit dispatched but not yet reported as of this CONSOLIDATE's window. Close-out captured at the next CONSOLIDATE checkpoint. | TBD | TBD |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carry-forward from "Pending after W1133"). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carry-forward from "Pending after W1133"). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carry-forward from "Pending after W1133"). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carry-forward from "Pending after W1133"). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carry-forward from "Pending after W1133"). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (carry-forward from "Pending after W1133" — partially superseded; the option-dest axis is now SHIPPED via W1136 + the path axis is harmonized via W1099-narrow). | `tests/test_w1121_click_argument_<axis>_lint.py` (remaining files) | 1-2h |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward from "Pending after W1133"). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward from "Pending after W1133"). | 3 per-command sites | 1-2h |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133)** (carry-forward from "Pending after W1133"). | per-command CLI surface | DEFERRED to v14.0 |

### Closures since W1126 (W1121-target / W1129 / W1125 / W1131 / W1132 / W1118-bundle / W1101-BAIL — W1133-CONSOLIDATE)

The W1133 consolidation pass folds in 14 completions from the W1097 → W1133
stretch. **Two structural closures**: (a) the `cmd_runs` placeholder-vocabulary
cluster (W1097 + W1105 + W1116 + W1125 = 8 sites swept end-to-end), (b) the
W1118-bundle reclassification (12 W1111 grandfathered sites classified into
10 SYMBOL-CONCEPT + 2 DOMAIN-DISTINCT permanent carve-outs). 7 SHIPPED + 7
audits/classifications + 1 BAIL (W1101 inherited from Section 48 batch).
**~80 LOC of impl + 253 LOC of new test coverage** — net documentation-heavy
batch; load-bearing structural output is the two cluster closures + the v14.0
rename cluster expansion finding (W1133: ~21 files versus the W1004 audit's
6-file estimate). 4 new drive-by W-tasks captured (W1130-W1133).
Strike-throughs preserved on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1121-target — AST CI lint blocking new `@click.argument('target')` drift~~ | W1121-target (W1133-CONSOLIDATE) | New `tests/test_w1121_click_argument_target_lint.py` (253 LOC). 15 sites classified into 4 categories: 13 SYMBOL, 1 GIT_REF (`bisect` start/good/bad), 1 FILE_PATH. Companion to W1111; extends the AST drift-block pattern to a second vocabulary axis. Covers the W1099 input_path-cluster gap end-to-end for the `target` axis. |
| ~~W1118 — W1111 grandfather reclassification A (cmd_closure.py:191)~~ | W1118 (W1133-CONSOLIDATE) | VERDICT SYMBOL-CONCEPT. `find_symbol` resolution; joins the v14.0 rename cluster. |
| ~~W1119 — W1111 grandfather reclassification B (cmd_testmap.py:232)~~ | W1119 (W1133-CONSOLIDATE) | VERDICT SYMBOL-CONCEPT. `find_symbol` resolution; joins the cluster. |
| ~~W1120 — W1111 grandfather reclassification C (cmd_plugins.py:213)~~ | W1120 (W1133-CONSOLIDATE) | VERDICT DOMAIN-DISTINCT — plugin name, not a symbol. Permanent grandfather carve-out. W1129 applied carve-out comment at the per-site source. |
| ~~W1106 — W1111 grandfather classification (cmd_impact.py:264)~~ | W1106 (W1133-CONSOLIDATE) | VERDICT SYMBOL-CONCEPT. Joins the cluster. |
| ~~W1107 — W1111 grandfather classification (cmd_oracle.py x4)~~ | W1107 (W1133-CONSOLIDATE) | VERDICT SYMBOL-CONCEPT (x4 sites): `symbol_exists` / `is_test_only` / `is_reachable` / `is_clone_of`. Joins the cluster. |
| ~~W1108 — W1111 grandfather classification (cmd_explain_command.py:150)~~ | W1108 (W1133-CONSOLIDATE) | VERDICT DOMAIN-DISTINCT — CLI command name, not a symbol. Permanent grandfather carve-out. W1129 applied carve-out comment at the per-site source. |
| ~~W1109 — W1111 grandfather classification (cmd_diagnose.py:201)~~ | W1109 (W1133-CONSOLIDATE) | VERDICT SYMBOL-CONCEPT. `find_symbol_with_alternatives` resolution; optional/default argument shape preserved at the call. Joins the cluster. |
| ~~W1125 — Placeholder unify drive-by (`--action X` → `<action>`)~~ | W1125 (W1133-CONSOLIDATE) | `cmd_runs.py:6` placeholder unified. 1-line. Hash-stable (help-text only). **`cmd_runs` placeholder cluster STRUCTURALLY CLOSED end-to-end** across W1097 + W1105 + W1116 + W1125. |
| ~~W1129 — W1108 + W1120 carve-out comments + W1111 lint disambiguation~~ | W1129 (W1133-CONSOLIDATE) | +15 LOC across 3 files. Anchors the DOMAIN-DISTINCT carve-out rationale at the per-site source so future readers see the rationale at the call site. Hash-stable (comments only). |
| ~~W1131 — findings.py vocabulary cross-link cleanup capstone~~ | W1131 (W1133-CONSOLIDATE) | +54 LOC across `src/roam/db/findings.py`: `source_version` cross-link comment + `evidence_json` size-GUIDANCE flag + `suppressions_json` docstring + module-level docstring refresh. Closes the 4-cleanup cluster (W1122 + W1123 + W1127 + W1128 follow-ups) over the W1126-batch + W1133-batch span. 66/66 findings tests pass. Hash-stable (comments + docstring only). |
| ~~W1132 — W1111 lint comment update (test-only)~~ | W1132 (W1133-CONSOLIDATE) | `tests/test_w1111_click_argument_name_lint.py` comment rewording (~0 LOC). Moves cmd_impact / cmd_oracle / cmd_diagnose annotations from "pending classification" to SYMBOL-CONCEPT confirmed. Reflects the W1118-bundle classification verdicts at the W1111 lint's grandfather metadata. |
| ~~W1133 — v14.0 rename cluster expansion (INFORMATIONAL)~~ | W1133 (W1133-CONSOLIDATE) | Captures the v14.0 hard-rename candidate cluster expansion to ~21 files: 8 sites on `@click.argument("name")` (10 of 12 W1111 grandfather set are SYMBOL-CONCEPT after Section 49 reclassification; 2 DOMAIN-DISTINCT carve-outs excluded) + 13 sites on `@click.argument("target")` (W1121-target SYMBOL classification set; 1 GIT_REF and 1 FILE_PATH carve-outs excluded). USER DECISION W1098 should reference W1133 for the full v14.0 scope at v14.0 planning. |

### Pending after W1133 (queue for next session — W1133-CONSOLIDATE)

The W1133 consolidation pass folds in 14 completions from the W1097 →
W1133 stretch. 7 SHIPPED + 7 audits/classifications + 4 drive-by
W-tasks captured (W1130-W1133). **Two structural closures**: the
`cmd_runs` placeholder cluster (W1097 + W1105 + W1116 + W1125) and
the W1118-bundle reclassification (10 SYMBOL-CONCEPT + 2 DOMAIN-DISTINCT).
**v14.0 rename cluster expanded from 6 to ~21 files** (W1133
informational); USER DECISION W1098 references W1133 at v14.0 planning.

| Item | Where | Effort |
|---|---|---|
| **W1130 — CLAUDE.md 16-vs-20 detector-count drift.** Drive-by from W1131 cleanup: CLAUDE.md "16 detectors persist findings" docstring drifts from the actual codebase count (20+ sites persist findings). Documentation-grade follow-up. | `CLAUDE.md` (docstring section) | 30 min |
| **W1098 — Click-argument rename (DEFERRED to v14.0 per W1102-RESEARCH + W1133).** No action needed today — the W1111 + W1121-target AST CI lints lock the current grandfather surface (~21 files). Re-evaluate at v14.0 planning; reference W1133 for the full scope (8 `@click.argument("name")` + 13 `@click.argument("target")` SYMBOL-CONCEPT sites). | per-command CLI surface | DEFERRED to v14.0 |
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb** (carried forward from "Pending after W1126"). | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb** (carried forward from "Pending after W1126"). | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb** (carried forward from "Pending after W1126"). | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb** (carried forward from "Pending after W1126"). | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep** (carried forward from "Pending after W1126" — narrower scope now that the `cmd_runs` placeholder cluster is structurally closed; remaining `[VALUE]` sites are in sibling commands). | per-command help text | 30 min |
| **W1121 — sibling AST lints for `file` / `pattern` axes** (partial carry-forward; `target` axis SHIPPED via W1121-target). Apply the W1111 + W1121-target shape to the remaining `file` / `pattern` axes — closes the W1099 input_path-cluster gap end-to-end. | `tests/test_w1121_click_argument_<axis>_lint.py` (2 new files) | 2-3h |
| **W1123 — Vocabulary cross-link follow-up A** (partial carry-forward; **closed by W1131** capstone — strikethrough recorded in the closures table above). | per-site audit | shipped via W1131 |
| **W1124 — Vocabulary cross-link follow-up B** (carry-forward from "Pending after W1126"). | per-site audit | 1h |
| **W1126 — INVERTED `memory` plural-flag harmonize** (carry-forward from "Pending after W1126" — the actual W1101 fix; 3 outlier sites). | 3 per-command sites | 1-2h |
| **W1127 — Severity-vocabulary alphabet gap** (partial carry-forward; **closed by W1131** capstone — strikethrough recorded in the closures table above). | `src/roam/output/_severity.py` | shipped via W1131 |
| **W1128 — `source_detector` enum capture** (partial carry-forward; **closed by W1131** capstone — strikethrough recorded in the closures table above). | `src/roam/db/findings.py` | shipped via W1131 |

### Closures since W1096 (W1086 / W1060-take2 / W1097 / W1105 / W1111 / W1094 / W1116 / W1122 / W1101-BAIL — W1126-CONSOLIDATE)

The W1126 consolidation pass folds in 9 dispatches from the W1086 → W1126
stretch. **Architectural ship**: `to_sarif` gained a `warnings_out: list[str]`
parameter + a new closed-enum descriptor `producer.advisory-warning` on the
SARIF tool driver (was missing in the W1046 landing). 8 of 9 outcomes
SHIPPED; 1 BAIL (W1101 — premise inverted; the W1004 audit had misread the
dominant convention so the sweep would have been backwards). **~250 LOC of
impl** (W1060-take2 + W1086 dominant) + ~120 LOC of new test coverage +
1 research memo (677 LOC) — the W1102-RESEARCH closed the W1098
USER-DECISION as "no v14.0 rename needed; ship the W1111 AST CI lint
instead". The premise-verification-first discipline continues to
outperform force-through; 17 new drive-by W-tasks captured (W1112-W1128).
Strike-throughs preserved on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1086 — `warnings_out` accumulator landing for `cmd_complexity`~~ | W1086 (W1126-CONSOLIDATE) | +84/-29 LOC at `src/roam/commands/cmd_complexity.py`: `warnings: list[str] = []` accumulator threaded through 4 envelope sites via the hash-stable omit-when-empty idiom. New 6-test file (+39 LOC) passes. Unblocked W1060-take2 (which had bailed in W1060-narrowed for lack of this prereq). |
| ~~W1060-take2 — `cmd_complexity` SARIF emit_runtime_notifications~~ | W1060-take2 (W1126-CONSOLIDATE) | **ARCHITECTURAL.** `src/roam/output/sarif.py::to_sarif(warnings_out=...)` parameter added (was missing in the W1046 landing) + new closed-enum descriptor `producer.advisory-warning` on the SARIF tool driver. `complexity_to_sarif(warnings=...)` signature wired. 15 SARIF tests pass. Hash-stability programmatically asserted for the empty-warnings path. Unblocks W1112-W1115 (4 sibling SARIF helpers). |
| ~~W1097 — `cmd_runs.py:944` placeholder unify (`NAME` → `<name>`)~~ | W1097 (W1126-CONSOLIDATE) | 1-line change. 4/4 focused tests pass. Hash-stable (help-text only). |
| ~~W1105 — `cmd_runs.py` 7-site `--agent NAME` → `<name>` sweep~~ | W1105 (W1126-CONSOLIDATE) | 7-site sweep across `cmd_runs.py`. 14/14 focused tests pass. Hash-stable (help-text only). |
| ~~W1111 — AST CI lint blocking new `@click.argument('name')` drift~~ | W1111 (W1126-CONSOLIDATE) | New `tests/test_w1111_click_argument_name_lint.py` (199 LOC; 50 LOC executable). 12-file grandfather set. Negative path verified — a 13th site fails the AST scan. W1102-RESEARCH deliverable. **Closes W1098 USER-DECISION**: defer hard rename to v14.0; lock current drift surface today. |
| ~~W1094 — Severity-vocabulary docstring cross-link~~ | W1094 (W1126-CONSOLIDATE) | +17 LOC across `src/roam/evidence/_vocabulary.py` + `src/roam/output/_severity.py`. Closes the W1005 BAIL drive-by (5-tier evidence vs 4-tier output is layered by design — now stated explicitly in the docstring). 55 W210 drift guards pass. Hash-stable (comments only). |
| ~~W1116 — `cmd_runs.py:7` placeholder unify (`--run-id ID` → `<id>`)~~ | W1116 (W1126-CONSOLIDATE) | 1-line change. 29/29 focused tests pass. Hash-stable (help-text only). |
| ~~W1122 — `db/findings.py:101-107` reverse-pointer comment block to evidence SUBJECT_KINDS~~ | W1122 (W1126-CONSOLIDATE) | +7-line comment block. 92/92 tests pass. Hash-stable (comments only). Closes a drive-by from the W1094 sweep. |
| ~~W1101 — `memory` → `memories` plural sweep — BAILED (premise inverted)~~ | W1101 BAIL (W1126-CONSOLIDATE) | The W1004 audit had read the codebase wrong: ~26 sites use singular-flag/plural-var (dominant) vs ~3 sites with plural-flag. `cmd_memory` FOLLOWS the dominant convention; the proposed sweep would have inverted it. New **W1126** captured for the 3 actual outliers (inverted task). |
| ~~W1102-RESEARCH — Click-argument rename strategy memo~~ | W1102-RESEARCH (W1126-CONSOLIDATE) | 677-line memo at `dev/CLICK-ARGUMENT-RENAME-STRATEGY-2026-05-15.md`. **KEY FINDING**: the MCP boundary is already sealed via `_PARAM_ALIASES` (W430) — CLI-side `@click.argument("name")` drift does NOT silent-fail through MCP. **Recommendation**: ship the W1111 lint (DONE), defer hard rename until v14.0 ships for unrelated reason. **W1098 downgraded from BLOCKER to FOLLOW-UP**. |

### Closures since W1079 (W1041 / W1048 / W1060-narrowed / W1007 / W1008 / W1087 / W1091 / W1004 / W1005 / W1020 / W1096 — W1096-CONSOLIDATE)

The W1096 consolidation pass folds in ~11 dispatches from the W1041 → W1096
stretch. **Premise-verification-first iteration**: 4 of 11 outcomes were
BAIL/NO-OP (W1041 already alphabetical, W1008 already converged via W706+W1057,
W1005 already W547/W564-compliant, W1020 already optimised with scope="module"
overrides where viable); 1 BAIL with prereq capture (W1060-narrowed → W1084/
W1085/W1086 prereqs); 2 VALIDATED-then-sealed (W1007 → W1091 fix; W1008 → W1093
drive-by); 3 impl-shipped (W1087/W1091/W1096); 1 SWEPT-CLEAN (W1048). **Only
3 commits-worth of impl actually landed (~63 LOC + ~39 test LOC)** — the
iteration's load-bearing methodological output is that **the BAIL-and-CAPTURE
pattern is faster and more accurate than force-through**. The bail discipline
(W1019b/W1019e/W1080 precedent + W988+W989 "premise verification is the first
step" methodology from W1001-CONSOLIDATE) generated 9 follow-up W-tasks
(W1084-W1097) instead of forcing-through cargo-cult code. All 11 dispatches
used `general-purpose` or `Explore` subagents per the W1072 directive —
`claude` subagent worktree-MAX_PATH blocker still active on Windows.
Strike-throughs preserved on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1041 — `clones_cross_layer.py` `__all__` divergence~~ | W1041 NO-OP (W1096-CONSOLIDATE) | Verified already alphabetical (matches W855/W856/W857/W858 sibling files). 101 focused tests pass. Drive-by W1090 captured (3 ordering conventions across 9 catalog files). |
| ~~W1048 — Node 20 deprecation in `publish.yml` + sister workflows~~ | W1048 SWEPT-CLEAN (W1096-CONSOLIDATE) | All `actions/*` references already on current majors. Pure-Python repo — no `setup-node` versions to bump. Drive-bys: W1087 (shipped), W1088, W1089. |
| ~~W1060-narrowed — `cmd_complexity` `emit_runtime_notifications` plumb~~ | W1060 BAILED (W1096-CONSOLIDATE) | Verified `cmd_complexity` has zero `warnings_out` accumulators — proposed plumb would have been cargo-cult. Prereqs captured: W1084 (`cmd_health` re-dispatch), W1085 (`cmd_doctor` SARIF), W1086 (`warnings_out` prereq). |
| ~~W1007 — `agent_contract:[]` empty-list mistake~~ | W1007 VALIDATED → W1091 (W1096-CONSOLIDATE) | Confirmed `cmd_runs.py:1050-1058` emitted empty `next_commands` on state=unsigned + key_missing. Tier-1 fix shipped via W1091. Tier-2 design Q captured as W1092 (auto-derive omit-when-empty). |
| ~~W1008 — envelope-root `list_counts` sweep~~ | W1008 BAILED (W1096-CONSOLIDATE) | `list_counts` only exists as dead local var in `formatter.strip_list_payloads`; already converged via W706+W1057. Drive-by W1093 (dead-code cleanup, deferred). |
| ~~W1087 — CI hardening: timeout-minutes + concurrency groups~~ | W1087 (W1096-CONSOLIDATE) | +17 LOC across 3 workflows. `architecture-guardian.yml` + `roam-ci.yml` got concurrency groups with `cancel-in-progress: true`; 9 jobs across architecture-guardian / cga-attestation / roam-ci got `timeout-minutes`. `publish.yml` deliberately left untouched (never cancel publishes). Drive-bys: W1095 (publish.yml timeouts), W1096 (roam.yml template — sealed inline). |
| ~~W1091 — `roam runs verify --all` LAW 4 fix on unsigned + key_missing~~ | W1091 (W1096-CONSOLIDATE) | +6 LOC at `src/roam/commands/cmd_runs.py:1050-1058` + 1 new test (+39 LOC) at `tests/test_ledger_signing.py`. Both branches now populate imperative `next_command` per LAW 4. **Hash-stable for tampered + ok paths** (only adds bytes to previously-empty fields). Drive-by W1097 (placeholder unify). |
| ~~W1004 — 7-cmd click-vocab audit~~ | W1004 VALIDATED → W1098 USER DECISION (W1096-CONSOLIDATE) | 6 commands (disambiguate/guard/safe_delete/symbol/test_scaffold/uses) diverge on `@click.argument("name")` vs canonical MCP `"symbol"`. Click has NO argument-alias support — migration captured as **W1098 (USER DECISION pending)**. W1099 (path cluster), W1100 (help-text), W1101 (memory plural) captured as siblings. |
| ~~W1005 — 3-tier vs 5-tier severity Pattern-3a divergence~~ | W1005 BAILED (W1096-CONSOLIDATE) | Codebase already compliant with W547 + W564 discipline. `CLAIM_SEVERITIES` (5-tier evidence) vs canonical (4-tier output) is layered by design — not a Pattern-3a divergence. Drive-by W1094 (docstring reconciliation). |
| ~~W1020 — fixture-scope audit~~ | W1020 NO-OP (W1096-CONSOLIDATE) | Already optimised. 8 test files use `scope="module"` override (~642s wall-clock savings); 6 findings test files cannot apply override (DB mutations require per-test isolation). |
| ~~W1096 — `roam.yml` dormant template hardened~~ | W1096 (W1096-CONSOLIDATE) | +1 line (`timeout-minutes: 20`) + 3-line teaching comment. Confirmed workflow_dispatch-only template. Mirrors W1087 discipline at the user-facing template surface. |

### Closures since W1047 (W1010 / W1027 / W1043-sweep / W1059 / W1065 / W1066 / W1067 / W1068 / W1069 / W1070 / W1071 / W1072 / W1073 / W1074 / W1075 / W1076 / W1077 / W1078 / W1079 — W1079-CONSOLIDATE)

The W1079 consolidation pass folds in ~17 status changes from the
W1047 → W1079 stretch. **Pattern-1D disclosure arc** went from 1
command (W1063 `cmd_findings --detector` at W1042-CONSOLIDATE) to **9
commands** with explicit unknown-value envelopes + difflib closest-match
suggestions. **Helper hoist Phase 1** shipped unused (W1077; Phase 2
W1080 in flight). **Pattern-2 propagation** completed via W1010 final
close on `cmd_flag_dead._load_known_stale` (plain-text loader, not YAML).
**W1043** `WarningsOut` type alias swept 21 callsites across 8 files.
**Operational blocker W1072** captured the `claude` subagent
worktree-MAX_PATH structural issue on Windows. Strike-throughs
preserved on originating pending lines; fast-lookup index here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W1010 — `cmd_flag_dead._load_known_stale` Pattern-2 close~~ | W1010 (W1079-CONSOLIDATE) | Plain-text loader (NOT YAML — does not flow through `load_yaml_with_warnings`). `warnings_out` plumb + `partial_success=true` on malformed lines + `agent_contract.facts` surface. Completes the W706 fan-out end-to-end. |
| ~~W1027 — `_no_pyyaml` monkeypatch → conftest.py fixture~~ | W1027 (W1079-CONSOLIDATE) | 6 test files migrated; -50 LOC net. Closes the duplicated-test-scaffolding class W934 had been chipping away at. |
| ~~W1043 — `WarningsOut` type alias swept across 21 callsites~~ | W1043-sweep (W1079-CONSOLIDATE) | 21 sites across 8 files; types-only no runtime delta. Closes the W706-fan-out arc end-to-end through W918 → W994/W995/W1009/W1011/W1017/W1025/W1032/W1042. |
| ~~W1059 — 10 hardcoded `expires_at` future-dates → relative offsets~~ | W1059 (W1079-CONSOLIDATE) | 2 files; same shape as W1002 / W1012 autouse-fixture interaction discipline. |
| ~~W1065 — 3 more files triaged, 0 conversions~~ | W1065 (W1079-CONSOLIDATE) | All 3 were valid B-variant fixtures (date is INPUT under test); documented as deliberate carve-out. |
| ~~W1066 — `cmd_findings` + `cmd_smells` difflib closest-match augmentation~~ | W1066 (W1079-CONSOLIDATE) | Adds difflib `next_command` carrying closest match to the existing post-W1063 rejection envelope. |
| ~~W1067 — Permit-expiry investigation closed NOT-A-BUG~~ | W1067 (W1079-CONSOLIDATE) | Audit-completeness design per W377 marker — permits intentionally remain in audit trail post-expiry. Permit module docstring updated. |
| ~~W1068 — `cmd_search --kind` unknown-value disclosure + LAW 4 `kinds` anchor~~ | W1068 (W1079-CONSOLIDATE) | Pattern-1D envelope + LAW 4 anchor set extended (`kinds` terminal) in both `formatter.py` + `test_law4_lint.py`. |
| ~~W1069 — `cmd_endpoints --framework` unknown-substring disclosure~~ | W1069 (W1079-CONSOLIDATE) | Framework validation tightened from substring-match to exact-match against closed registry. Closes a silent-fallback class. |
| ~~W1070 — `cmd_test_scaffold --framework` unknown-value disclosure~~ | W1070 (W1079-CONSOLIDATE) | Sister of W1069; same Pattern-1D envelope on the test-scaffold path. |
| ~~W1071 — Permit-vs-lease asymmetry documented~~ | W1071 (W1079-CONSOLIDATE) | Sibling capture from W1067. Module-docstring updates + CLAUDE.md sub-section codifying the asymmetry. |
| ~~W1072 — `claude` subagent worktree-MAX_PATH on Windows (operational)~~ | W1072 (W1079-CONSOLIDATE) | Operational finding documented; use `general-purpose` subagent instead. Structural issue with agent platform's default-worktree behavior — not addressable from inside roam. |
| ~~W1074 — `cmd_workflow` + `cmd_explain_command` UsageError difflib augmentation~~ | W1074 (W1079-CONSOLIDATE) | UsageError shape (not envelope shape). LAW 2 imperative voice: "Did you mean `roam <closest>`?". |
| ~~W1075 — `cmd_endpoints --method` Pattern-1D disclosure~~ | W1075 (W1079-CONSOLIDATE) | Method validation aligned with W1069 framework path; closed HTTP-verb set. |
| ~~W1076 — CLAUDE.md is intentionally untracked~~ | W1076 (W1079-CONSOLIDATE) | Commit `89a338d9` removed CLAUDE.md from public repo. Documented inline at top of CLAUDE.md (local-only by design; not shipped to PyPI/GitHub/landing-page). |
| ~~W1077 — `structured_unknown_filter` helper shipped Phase 1 UNUSED~~ | W1077 (W1079-CONSOLIDATE) | New module `src/roam/output/structured_unknowns.py` (128 LOC) + 15 tests. Mirrors W1018 landing pattern; Phase 2 W1080 in flight. |
| ~~W1078 — `cmd_complete --kind` audit closed not-applicable~~ | W1078 (W1079-CONSOLIDATE) | Uses `click.Choice(...)` — pre-handler rejection makes Pattern-1D template inapplicable. Documented as deliberate carve-out. |
| ~~W1079 — `cmd_oracle` unknown-oracle name closest-match~~ | W1079 (W1079-CONSOLIDATE) | Per-line shape on the `--oracles` repeatable flag. Closes the final unknown-value site in the disclosure arc. |
| ~~W1063 — `cmd_findings --detector` unknown disclosure (recap)~~ | W1063 (W1042-CONSOLIDATE, recapped here) | The seed of the W1066-W1079 Pattern-1D arc. Strikethrough was already in the W1042 closures index; restated here for arc continuity. |
| ~~W1049-RESEARCH — Release-pipeline hardening memo~~ | W1049 (W1079-CONSOLIDATE) | 3 P1 recommendations: PEP 740 attestations + workflow split + SBOM-wheel SHA binding. Queued as W1054 / W1055 / W1056 (user-decision-pending). |

### Pending after W1126 (queue for next session — W1126-CONSOLIDATE)

The W1126 consolidation pass folds in 9 dispatches from the W1086 → W1126
stretch. 8 SHIPPED + 1 BAIL (W1101 premise-inverted) + 1 research memo +
17 drive-by W-tasks captured (W1112-W1128). **Architectural ship**:
`to_sarif(warnings_out=...)` parameter + new closed-enum descriptor
`producer.advisory-warning` unlocks 4 sibling SARIF helpers as the
W1112-W1115 follow-up queue. W1102-RESEARCH closed W1098 as
no-action-needed-beyond-W1111.

| Item | Where | Effort |
|---|---|---|
| **W1112 — `cmd_fitness` SARIF helper `warnings_out` plumb.** Sibling unblocked by W1060-take2. The `to_sarif(warnings_out=...)` parameter is now live; landing the fitness-side plumb is a straight callsite migration. | `src/roam/commands/cmd_fitness.py` | 1-2h |
| **W1113 — `cmd_flag_dead` SARIF helper `warnings_out` plumb.** Sibling to W1112 — same migration shape. | `src/roam/commands/cmd_flag_dead.py` | 1-2h |
| **W1114 — `cmd_rules` SARIF helper `warnings_out` plumb.** Sibling to W1112. | `src/roam/commands/cmd_rules.py` | 1-2h |
| **W1115 — `cmd_health` SARIF helper `warnings_out` plumb.** Sibling to W1112; previously bailed in W1060-narrowed as cargo-cult before the W1086 prereq landed. | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1117 — `cmd_runs` square-bracket placeholder convention sweep.** Drive-by from W1097/W1105/W1116: 3 sites still carry `[VALUE]` square-bracket style vs the canonical `<value>` angle-bracket style. Documentation-grade follow-up. | `src/roam/commands/cmd_runs.py` | 30 min |
| **W1118 — W1111 grandfather reclassification A.** Drive-by from W1111 lint sweep: 1 of the 12 grandfathered sites may be reclassifiable as a non-symbol identifier (e.g. a path or a pattern); audit per-site to narrow the lint's scope. | `tests/test_w1111_click_argument_name_lint.py` | 30 min |
| **W1119 — W1111 grandfather reclassification B.** Sibling to W1118. | `tests/test_w1111_click_argument_name_lint.py` | 30 min |
| **W1120 — W1111 grandfather reclassification C.** Sibling to W1118. | `tests/test_w1111_click_argument_name_lint.py` | 30 min |
| **W1121 — Sibling AST lints for `target` / `file` / `pattern` arguments.** W1111 lint shape is generalisable: the same `@click.argument(<canonical-key>)` discipline should apply to `target` / `file` / `pattern` clusters. Captures the W1099 input_path-cluster gap end-to-end. | `tests/test_w1121_click_argument_<axis>_lint.py` (3 new files) | 3-4h |
| **W1123 — Vocabulary cross-link follow-up A.** Drive-by from W1122: similar reverse-pointer comments may benefit 1-2 other registry sites that map to evidence SUBJECT_KINDS. Documentation-grade. | per-site audit | 1h |
| **W1124 — Vocabulary cross-link follow-up B.** Drive-by from W1122. | per-site audit | 1h |
| **W1125 — Placeholder unify drive-by (`--action X`).** Drive-by from the W1097/W1105/W1116 sweep: at least one site uses `--action X` style placeholder still. Narrow help-text cleanup. | per-command help text | 30 min |
| **W1126 — INVERTED `memory` plural-flag harmonize.** The actual W1101 fix: 3 outlier sites use plural-flag where the dominant codebase convention is singular-flag/plural-var. Bring the 3 outliers in line with the dominant convention (NOT the other way around). Captured after W1101 BAIL surfaced the inverted premise. | 3 per-command sites | 1-2h |
| **W1127 — Severity-vocabulary alphabet gap.** Drive-by from W1094 cross-link work: the canonical severity alphabet has a small gap between evidence (5-tier) and output (4-tier) that may benefit a 5th output tier (or a documented evidence-only tier) — DESIGN Q. | `src/roam/output/_severity.py` | DESIGN Q |
| **W1128 — `source_detector` enum capture.** Drive-by from W1122 cross-link work: `source_detector` field on findings is currently free-string; should be a closed enum mirroring the registry detector list. | `src/roam/db/findings.py` | 1-2h |
| **W1098 — Click-argument rename (DOWNGRADED from BLOCKER to FOLLOW-UP per W1102-RESEARCH).** No action needed today — the W1111 AST CI lint locks the current 12-site grandfather surface; defer the hard rename until v14.0 ships for unrelated reason. Re-evaluate at v14.0 planning. | per-command CLI surface | DEFERRED to v14.0 |

### Pending after W1096 (queue for next session — W1096-CONSOLIDATE)

The W1096 consolidation pass folds in ~11 dispatches from the W1041 →
W1096 stretch. 3 impl-shipped (W1087/W1091/W1096), 6 audits / NO-OPs /
VALIDATED (W1041/W1004/W1005/W1007/W1008/W1020), 1 bail-with-prereq
(W1060-narrowed), 1 SWEPT-CLEAN (W1048). The methodological output is
that **BAIL-and-CAPTURE produces sharper follow-up W-tasks than
force-through cargo-cult code** — 9 follow-up captures (W1084-W1097)
in lieu of fabricated implementation.

| Item | Where | Effort |
|---|---|---|
| **W1098 — USER DECISION: 6-cmd click-argument vocabulary migration.** Click has NO argument-alias support, so the 6 commands (disambiguate / guard / safe_delete / symbol / test_scaffold / uses) on `@click.argument("name")` can EITHER (a) accept silent divergence from the canonical MCP `"symbol"` vocabulary, (b) migrate to `@click.option("--symbol")` (breaking CLI compatibility), or (c) wrap with a positional-to-option shim. Migration captured pending user decision. | per-command CLI surface | USER DECISION |
| **W1099 — `input_path` cluster Pattern-3b normalization.** Sibling capture from W1004 audit: `rules_path` / `rules_file` / `statement_path` / `envelope_path` are 4 parameter names referring to similar concepts with ZERO current normalization in `_PARAM_ALIASES`. Same shape as the W332 silent-fail class (`roam_audit_trail_verify` accepts only one of the 4 names). | `src/roam/mcp_server.py:_PARAM_ALIASES` + boundary normalization | 2-3h |
| **W1100 — Help-text vocabulary sweep across the 6 W1004 commands.** The 6 commands' `--help` strings use a mix of "symbol", "name", and "identifier" — even if W1098 keeps `@click.argument("name")` as-is, help-text consistency is a separate axis from argument-name and is independently fixable. | per-command help text | 1-2h |
| **W1101 — `memory` plural sweep (drive-by from W1004 audit).** A handful of envelope fields use `memory` (singular) where the canonical plural `memories` would anchor LAW 4 correctly. Surface-level concrete-noun terminal hygiene. | per-envelope audit | 1h |
| **W1095 — `publish.yml` timeouts (drive-by from W1087).** Deliberately skipped in W1087 (never cancel publishes); separate user-decision on whether `publish.yml` should have per-job timeout-minutes (not concurrency) for runaway-prevention. | `.github/workflows/publish.yml` | USER DECISION |
| **W1086 — `warnings_out` prereq for the W1060 family.** Several commands (cmd_complexity, cmd_health, cmd_doctor) lack a `warnings_out` accumulator; landing one is the prereq for the W1060-class runtime-notifications plumb. Captures the BAIL-and-CAPTURE rationale from W1060-narrowed. | per-command audit | 2-3h |
| **W1084 — `cmd_health` re-dispatch under the W1086 prereq.** Once W1086 lands, re-dispatch the cmd_health emit_runtime_notifications plumb. Was bailed in W1060-narrowed as cargo-cult. | `src/roam/commands/cmd_health.py` | 1-2h |
| **W1085 — `cmd_doctor` SARIF surface follow-up.** Sibling to W1084 — `cmd_doctor` SARIF emission needs the same `warnings_out` accumulator before the W1060-class plumb is non-cargo-cult. | `src/roam/commands/cmd_doctor.py` | 1-2h |
| **W1090 — 3 alphabetical-ordering conventions across 9 catalog files (drive-by from W1041 NO-OP).** Drive-by capture: catalog `__all__` ordering varies across 9 sibling files. Narrow style-rule documentation candidate — not a runtime issue. | `src/roam/catalog/*.py` | 1h |
| **W1092 — Auto-derive `omit-when-empty` across envelopes (DESIGN Q, from W1007 Tier-2).** Tier-2 follow-up to W1091: rather than per-site population, design an auto-derive that omits empty `next_commands` / `agent_contract.facts` / `agent_contract.next_commands` from envelopes uniformly. | `src/roam/output/formatter.py:json_envelope` | DESIGN Q |
| **W1093 — Dead-code cleanup: `list_counts` local in `formatter.strip_list_payloads` (drive-by from W1008 BAIL).** Already-converged via W706+W1057 but the dead local is still present. Deferred — `formatter.py` is modified in the working tree. | `src/roam/output/formatter.py` | 30 min |
| **W1094 — Docstring reconciliation: `CLAIM_SEVERITIES` 5-tier vs canonical 4-tier output (drive-by from W1005 BAIL).** Not a Pattern-3a divergence (layered by design) but the docstring at the evidence-vocabulary site doesn't explicitly say so — next reader may re-flag. | `src/roam/evidence/_vocabulary.py` docstring | 30 min |
| **W1097 — Placeholder unify (drive-by from W1091).** Pre-fix the unsigned + key_missing branches had different placeholder styles for the next_command; unifying with the rest of the envelope-population sites is a documentation-grade follow-up. | `src/roam/commands/cmd_runs.py` | 30 min |

### Pending after W1079 (queue for next session — W1079-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W1080 — Phase 2 of W1077 helper migration (in flight).** First 3 callsites — `cmd_findings` + `cmd_search` + `cmd_endpoints --framework` — to migrate to `structured_unknown_filter`. Expected net -90 to -120 LOC once landed. | 3 callsite migrations | 2-3h |
| **W1081 — Drive-by captures from W1080 yet-to-land** (placeholder pending the W1080 re-dispatch landing). | TBD | TBD |
| **W1054 — PEP 740 attestation signing** (W1049-RESEARCH P1.1, user-decision-pending). Sign wheel + sdist with provenance attestation post-OIDC-mint; attach to GitHub Release alongside SBOM. | `.github/workflows/publish.yml` | 3-4h |
| **W1055 — `publish.yml` workflow split** (W1049-RESEARCH P1.2, user-decision-pending). Separate build / publish / smoke into independent workflows so smoke-step failures don't block publish from finishing. | `.github/workflows/*.yml` | 2-3h |
| **W1056 — SBOM-wheel SHA binding** (W1049-RESEARCH P1.3, user-decision-pending). CycloneDX SBOM should carry the wheel's content SHA so the SBOM cannot be silently swapped post-publish. | SBOM-generation step in `publish.yml` | 1-2h |
| **W1006-shipped** (carry-from-W1047). | `src/roam/output/formatter.py` + sibling sites | 1-2h |
| **W1007** (carry-from-W1047). | per-command audit | 1h |
| **W1008** (carry-from-W1047). | per-envelope audit | 1-2h |
| **W1012** (carry-from-W1047). | `tests/` audit | 2h |
| **W1020** (carry-from-W1047). | `tests/` audit | 1-2h |
| **W1036** (carry-from-W1047). | per-loader audit + migration | 2-3h |
| **W1038** (carry-from-W1047). | new helper or `_yaml_helper.py` extension | 1h |
| **W1041** (carry-from-W1047). | `src/roam/catalog/clones_cross_layer.py` | 15 min |
| **W1019b** (carry-from-W1042 / W1047). Phase 2 leg b of the W1016 YAML helper migration; re-dispatch post-W1040. | callsite migration | 1-2h |
| **W1048** (carry-from-W1047). Node 20 → Node 22 in `.github/workflows/*.yml`. | workflows sweep | 1-2h |
| **W1004** (carry-from-W1015 / W1042 / W1047). | per-command audit | 2-3h |
| **W1005** (carry-from-W1015 / W1042 / W1047). | per-command audit | 1-2h |

### Pending after W1047 (queue for next session — W1047-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W1006-shipped — formatter sibling preserved-fields expansion sweep** (carry-from-W1015; partially landed via W1000 disclosure-hygiene allow-set; remaining sweep candidates from the W1006 capture still queued). | `src/roam/output/formatter.py` + sibling sites | 1-2h |
| **W1007 — `agent_contract:[]` empty-list mistake**. Empty-array `agent_contract` field surfaced in 1+ commands; should be omitted or populated per LAW 4. Carry-from-W1015. | per-command audit | 1h |
| **W1008 — envelope-root `list_counts` sweep**. Carry-from-W1015. Several envelopes carry `list_counts` at the root that would read better as `summary.list_counts`. | per-envelope audit | 1-2h |
| **W1010 — DEFERRED behind W1018**. Pre-W1018; revisit post-helper landing. Now ready to re-evaluate (W1018 + W1019a/c/d/e shipped). | TBD | TBD |
| **W1012 — test-date triage**. Several test fixtures carry hard-coded dates that need re-baselining against the 2026-05-15 session date. Carry-from-W1015. | `tests/` audit | 2h |
| **W1020 — fixture-scope audit** (W978 follow-up). Some `tests/test_findings_*.py` fixtures may want `scope="module"` vs `scope="function"` for indexing performance. Carry-from-W1015. | `tests/` audit | 1-2h |
| **W1027 — drive-by capture from W1019 batch** (gap between W1026 and W1029 IDs in the closures index; placeholder pending re-confirmation against in-flight worktrees). | TBD | TBD |
| **W1028 — drive-by capture from W1019 batch** (same as W1027 — placeholder pending re-confirmation). | TBD | TBD |
| **W1036 — 4 sibling `_parse_simple_yaml` loaders still bespoke** (carry-from-W1042; W1018 / W1019 follow-up). Each is a candidate for migration to `load_yaml_with_warnings`. | per-loader audit + migration | 2-3h |
| **W1038 — `_extract_typed` helper hoist** (carry-from-W1042; W1031 follow-up). Typed-overload adapter at each callsite; hoist once 2+ callsites stabilise. | new helper or `_yaml_helper.py` extension | 1h |
| **W1041 — `clones_cross_layer.py` `__all__` divergence** (carry-from-W1042; W1037 follow-up; **in flight**). W1037 sweep used alphabetical ordering convention; W1033 baseline used declaration-order. Resolve. | `src/roam/catalog/clones_cross_layer.py` | 15 min |
| **W1043 — `WarningsOut` TypeAlias** (in flight). Shipped as `TypeAlias = list[str]` at canonical boundary per Section 44; capture here for completeness of the W1042+ queue audit trail. | `src/roam/policy/_yaml_helper.py` | shipped |
| **W1044 — DEFERRED behind W1019b re-dispatch** (carry-from-W1042). | TBD (deferred) | TBD |
| **W1045 — drive-by capture from v13.1 release prep batch** (in flight; placeholder pending re-confirmation). | TBD | TBD |
| **W1046 — drive-by capture from v13.1 release prep batch** (placeholder pending re-confirmation). | TBD | TBD |
| **W1048 — Node 20 deprecation in `publish.yml` + sister workflows**. GitHub's runner is deprecating Node 20 in favour of Node 22; surfaced during the W1047 workflow audit. Sweep `actions/*@v3` consumers and bump to v4 where applicable. | `.github/workflows/*.yml` | 1-2h |
| **W1004 — 7-cmd click-vocab audit** (carry-from-W1015 / W1042 / W1047). | per-command audit | 2-3h |
| **W1005 — 3-tier vs 5-tier severity Pattern 3a divergence** (carry-from-W1015 / W1042 / W1047). | per-command audit | 1-2h |

### Pending after W1042 (queue for next session — W1042-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W1019b — Phase 2 of W1016 YAML helper migration, leg b** (in flight). 5th of 5 planned callsites; re-dispatch after the W1040 `force_tiny_parser` extension landed. When this lands, W1016 2-phase plan is 5/5 complete at the ~125 LOC net-removed budget. | callsite migration | 1-2h |
| **W1036 — 4 sibling `_parse_simple_yaml` loaders still bespoke** (W1018 / W1019 follow-up). Each is a candidate for migration to `load_yaml_with_warnings` once the helper has a `force_tiny_parser` path proven through one more callsite. | per-loader audit + migration | 2-3h |
| **W1038 — `_extract_typed` helper hoist** (W1031 follow-up). The typed-overload path carries a `_extract_typed` adapter at each callsite; once 2+ callsites stabilise, hoist to shared utility. | new helper or `_yaml_helper.py` extension | 1h |
| **W1041 — `clones_cross_layer.py` `__all__` divergence** (W1037 follow-up). W1037 sweep used alphabetical ordering convention; W1033 baseline used declaration-order. Resolve. | `src/roam/catalog/clones_cross_layer.py` | 15 min |
| **W1044 — DEFERRED behind W1019b re-dispatch.** Captured pending the W1019b re-dispatch landing post-W1040. | TBD (deferred) | TBD |
| **W1004 — 7-cmd click-vocab audit** (W996 follow-up, carry-from-W1015 / W1042). | per-command audit | 2-3h |
| **W1005 — 3-tier vs 5-tier severity Pattern 3a divergence** (W1011 audit follow-up, carry-from-W1015 / W1042). | per-command audit | 1-2h |

### Pending after W976 (queue from W977-CONSOLIDATE — see Pending after W1000 above for the latest)

| Item | Where | Effort |
|---|---|---|
| ~~**W972 — `_load_alerts_config` non-dict YAML root silent fallback**~~ — superseded; re-listed in "Pending after W1000" above. | `src/roam/commands/cmd_alerts.py` | 1h |
| ~~**W973 — `_make_alert` level validation defense**~~ — superseded; re-listed in "Pending after W1000" above. | `src/roam/commands/cmd_alerts.py` | 30 min |
| ~~**W974 — Tighten `AlertThreshold.level` to `Literal[...]` (now safe per W969).**~~ — superseded; re-listed in "Pending after W1000" above. | `src/roam/commands/cmd_alerts.py` | 30 min |
| ~~**W978 — Pre-existing `test_bus_factor_stale_kind_emitted` failure**~~ — shipped W1001-CONSOLIDATE via fixture monkeypatch. 18/18 tests pass. | `tests/test_findings_bus_factor.py` | 1-2h triage |
| ~~**W979 — `dark_matter` ↔ `dark-matter` + `fan_symbol` Pattern-3a divergence**~~ — fan-symbol leg shipped W1001-CONSOLIDATE via W982; dark_matter leg re-listed in "Pending after W1000" above. | `src/roam/db/findings.py` + per-detector emitters | 1-2h |
| **W357 (strategic, long-horizon) — Pick the MCP registry derivation source.** (Carry-from-W965) W525 inventory pass surfaced three candidates (`@roam_capability(category=...)`, `mcp_preset=(...)`, hand-rolled `_CORE_TOOLS`) — each has structural gaps. Strategic decision required before any derivation pass. | `src/roam/mcp_server.py` + `src/roam/plugins/capability.py` | TBD (strategic) |
| **W950 — STRATEGIC: `category=` vs `mcp_preset=` path for MCP registry derivation** (Carry-from-W965). Sub-question of W357; feeds the W869 Instance #2 wave once it unblocks. | strategic | TBD |
| **W951 — `mcp_preset=("core",)` default is dead metadata** (Carry-from-W965). 228 of 230 tools carry the default via copy-paste, not curation; a derivation pass that trusts it would over-include. Decide: strip the default, or curate it deliberately. | `src/roam/mcp_server.py` decorator default | 1-2h decision + 4-6h migration |
| **W952 — 24 MCP-only tools have no `@roam_capability` anchor** (Carry-from-W965). Gap class to close before any `category=`-based derivation pass. | per-tool audit | 4-6h |
| **W953 — 4 naming-drift cases between CLI + MCP wrappers** (Carry-from-W965). Tools where the MCP wrapper name does not derive from the CLI command name via the canonical kebab-→-snake transform. Documentation-grade audit. | per-case docs | 2h |
| **W957 — W862 lint "Fix:" hint forward-compat nit** (Carry-from-W965). Post-W942 pivot, the lint's "Fix: update the docstring" hint references the registry rather than ALL_DETECTORS; nit-pick wording polish. | `tests/test_smells_detector_count_drift.py` hint string | 15 min |
| **W959 — `_check_thresholds` `Alert` TypedDict bundle** (Carry-from-W965, W933 follow-up). Companion to W933 — the per-finding `Alert` dict shape would benefit from a TypedDict analogous to `AlertThreshold`. Per W966 discipline: must validate at the boundary OR keep loose. | `src/roam/commands/cmd_alerts.py` | 1-2h |

### Closures since W922 (W866 / W915 / W916 / W917 / W919 / W920 / W923 / W925 / W927 / W928 / W929 / W930 / W935 — canonical-source consolidation arc)

The W939 consolidation pass folds in ~14 status changes from the
W922→W938 stretch. **Two systemic canonical-source hoists** landed in
detail: the catalog-layer `_finding` constructor family (W923 — 4
callers migrated to `make_smell_finding` in `_shared.py`; 0 remaining
clone pairs per W855 rename-invariant detector) and the finding-id-
builder family (W935 — 6 sites collapsed to one-line returns calling
`make_finding_id` in `roam.db.findings`; 6/6 outputs hash-byte-identical).
The W922 drive-by queue closed almost end-to-end (W915 / W916 / W917 /
W919 / W929). Strike-throughs preserved on originating pending lines;
the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W923 — REAL clone target: `make_smell_finding(...)` hoisted to `_shared.py`~~ | W923 | 4 catalog-layer callers migrated. `smells.py` + `type_switch.py` via direct import alias; `parallel_hierarchy.py` + `clones_cross_layer.py` kept detector-specific arg-adapter wrappers but route dict construction through canonical. **Optional kwargs OMITTED FROM DICT when None** — preserves the 8-key shape every finding-registry test asserts. W855 rename-invariant detector reports **0 remaining catalog-layer `_finding` clone pairs**. **237 focused tests pass.** |
| ~~W935 — `make_finding_id(prefix, subject, *raw_parts)` hoisted to `roam.db.findings`~~ | W935 | 6 sites (`cmd_audit_trail_conformance` / `cmd_bus_factor` / `cmd_dead` / `cmd_doctor` / `cmd_orphan_imports` / `cmd_smells`) reduced their `_XXX_finding_id` bodies to one-line returns. **ALL 6 outputs hash-byte-identical before/after** — persisted finding rows stay valid. 5 dangling `import hashlib` lines removed inline. **77/78 focused tests pass** (1 pre-existing unrelated). |
| ~~W925 — `detectors._finding` fully annotated~~ | W925 | Matches `smells.make_smell_finding` style: `sqlite3.Row` for `sym`, `Mapping[str, Any]` / `Iterable[str]` / `int | None` for kwargs, `-> dict` return. **230 focused tests pass.** |
| ~~W866 — Dispatch-table refactor on 3 type-switch sites W852 flagged on roam-code's OWN code~~ | W866 | `smells.py:1793` + `smells.py:1812` (magic-numbers walker; 4-arm chain → `_AST_HANDLERS` dispatch by `type(child)`) and `registry_dispatch.py:170` (3-arm dispatch on `type(value)` against `ast.Dict` / `List` / `Tuple`). **Dogfood-OCP win.** 216 focused tests pass. |
| ~~W915 — `QUERY_COST_LOW/MEDIUM/HIGH` constants added to detectors.py~~ | W915 | `_QUERY_COSTS` derives from them; keys no longer live as bare string literals. |
| ~~W916 — CLAUDE.md confidence-tier vocabulary section cites `src/roam/db/findings.py`~~ | W916 | Adds 4 `CONFIDENCE_*` constant names + "extend canonical first, never hardcode at consumer site" discipline rule. |
| ~~W917 — `_SMELL_CONFIDENCE_TIERS` (3-of-4 subset, no runtime) added to `test_smells_confidence_mapping_parity.py`~~ | W917 | `test_all_confidence_values_are_canonical` now uses smells-specific allowlist. |
| ~~W919 — `AlertThreshold` TypedDict landed in `cmd_alerts.py`~~ | W919 | `op` as `Literal[5 comparators]`, `value` as `float \| int`, `level` as `str` (deliberately not `Literal` — `_resolved_thresholds` normalizes UPPER-case at load time). `_DEFAULT_THRESHOLDS` typed as `dict[str, AlertThreshold]`. **49/49 focused tests pass.** |
| ~~W929 — `_RE_CAMEL_SPLIT` + `_RE_UPPER_SPLIT` canonical at `tfidf.py`~~ | W929 | Via option (C) — option (A) had circular import. `index_embeddings._camel_split()` is now a thin wrapper consuming the canonical pre-compiled regexes. Captured as operational pattern: when canonical-hoist hits a cycle, owner-flip is the safe alternative. |
| ~~W920 — Behavioral-fingerprint sweep for differently-named twins (Explore)~~ | W920 | 5 unmigrated twins surfaced beyond literal-grep reach: `relations.py:343 _is_test_path` (W873 left as cycle-break — W902 method says verify); `pytest_fixtures.py:112 _is_test_function`; `rerank.py:376` inline (4+ call sites); `cmd_adversarial.py:347` inline; `cmd_next.py:379+518` inline (later DECLASSIFIED as non-clones — shape-checks, not parsers). |
| ~~W927 — `rerank.py:376-396` inline 21-line OR-chain extracted~~ | W927 | Module-level `_is_test_path()` + 4 named pattern tuples. **Did NOT delegate to `is_test_file`** — would broaden behavior (rerank tuned WITHOUT `conftest.py` / `_test.java` / etc). 26-case truth table at `.audit-tmp/verify_rerank_helper.py` confirms 0 diffs. **139 retrieve tests pass.** |
| ~~W928 — `relations.py:343` cycle verdict NO~~ | W928 | AST transitive scan: `changed_files` imports only `file_roles` + `test_conventions` + `git_utils`, never reaches `relations`. W873-era "to avoid roam.commands import cycle" comment was cargo-cult false per W902. BUT delegation would broaden behavior. Kept local def, REPLACED misleading comment with W928's verification record + "deliberately narrower; broadening requires reindex audit" rationale. **31 index tests pass.** |
| ~~W930 — Closed not-applicable~~ | W930 | `cmd_next.py` inline `startswith("roam ")` usages are shape-checks, not parsers (W920 misclassified). |
| ~~(ADD) W939 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W923-W937 batch~~ | W939 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W938 (queue for next session — W939-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W931 — Add `mypy` to `.venv` typecheck extras.** Discovered during W919 / W925 type-annotation validation; convenience pending. | `pyproject.toml [project.optional-dependencies]` | 30 min |
| **W932 — Audit `detectors._finding` callers for non-dict `evidence=`** (W925 follow-up). Type annotation says `Mapping[str, Any] \| None`; callers should be audited for stray non-dict shapes. | `src/roam/catalog/detectors.py` callers | 1h |
| **W933 — Tighten `cmd_alerts._parse_alerts_yaml` + `_resolved_thresholds` return types** (W919 follow-up). The TypedDict landed; the two YAML-loader return types should now narrow from `dict[str, dict]` to `dict[str, AlertThreshold]`. | `src/roam/commands/cmd_alerts.py` | 1h |
| **W934 — `test_findings_*` parametrization opportunity** (W923 cluster). The 4 catalog-layer migration sites have near-identical test scaffolding; parametrize for drift-guard discipline. | `tests/test_findings_*.py` | 1-2h |
| **W936 — Migrate `query_cost` string-literal defaults to `QUERY_COST_*`** (W915 follow-up). Consumer sites that take a `query_cost` kwarg with a default string literal should now reference the new constants. | grep-then-migrate | 1h |
| **W937 — Sweep mis-encoded Unicode arrows in docstrings** (W929 drive-by). Captured while editing tfidf.py; some docstrings have UTF-8-mangled arrows from prior edits. | grep `→` / mangled variants | 30 min |
| **W938 — Fold `cmd_bus_factor._repo_summary_finding_id`** (W935 4th cousin). Has the same shape as the 6 sites already migrated but takes only `prefix` + `subject`, no `raw_parts`. Migration is mechanical once W935 is reviewed. | `src/roam/commands/cmd_bus_factor.py` | 30 min |
| **W903 — W686 path-length recurrence operational note.** Recurring across batches; tooling-side investigation not addressable from inside roam. Carried forward. | tooling / harness config | TBD (external) |
| **W906 — Overly-defensive lazy-import comments** in `mcp_server.py` + `oscal.py` (W902 forward-looking, carried forward). Both surface real optional-dep handling but wording reads as cargo-cult; polish in a future docs-only wave. | `src/roam/mcp_server.py` + `src/roam/evidence/oscal.py` | 30 min |
| **W918 — `_resolved_thresholds` silent fallback for unknown metrics** (W922 carry-forward). Returns a default threshold on unknown metric names. Should raise OR surface a `partial_success=True` envelope. | `src/roam/commands/cmd_alerts.py` | 1-2h |
| **W921 — Audit other "duplicated from python_lang" claims** (W904 follow-up, carried forward). Sweep the codebase for other "duplicated from python_lang" / "mirrors python_lang" hedges and verify each one is factually true. | grep-then-audit | 1-2h |
| **W887 — `python_idioms._enclosing_symbol` name collision** (W877 drive-by, carried forward). Third site under `src/roam/python_idioms/` that wasn't part of the W877 hoist — name collision rather than clone; audit candidate. | `src/roam/python_idioms/` + audit | 1h |
| **W888 — `smells._enclosing_symbol` defensive-migration audit** (W877 drive-by, carried forward). Confirmed correct; capture as a discipline note for the next sibling-helper hoist. | docs / discipline note | 30 min |
| ~~**W890 — `is_test_file` None-guard**~~ — closed not-applicable W1015-CONSOLIDATE. Audit verified the W873-era canonical (`changed_files.is_test_file`) already None-guards its `path` argument; no work needed. Closes W886 drive-by-2 carry-forward. | `src/roam/commands/changed_files.py` | n/a |
| **W895 — `rollup_id` auto-infer design call** (W871 follow-up, carried forward). Auto-infer from parent `smell_id` + suffix vs require explicit kwarg on `@detector`. | design + 1 module | 30 min |
| **W896 — Stable iteration ordering design call** (W871 follow-up, carried forward). Alphabetical-by-smell-id vs declaration-order frozenset. SARIF-emitter golden-fixture stability depends on the answer. | design + `registry.py` | 30 min |
| **W897 — `parent_id` finalisation semantics** (W871 follow-up, carried forward). Rollup pattern needs an explicit binding contract. | design + `registry.py` | 30 min |
| **W898 — Long-term catalog/`_shared.is_test_path` delegate to canonical** (sister to W885, carried forward). Defer behind W871 bulk migration so the registry POC has a stable surface. | `src/roam/catalog/_shared.py` | 1-2h |
| **W899 — Tighten the Apex `Test.cls` regex** (W893 follow-up, carried forward). Greedy `.*` works but is non-obvious; explicit `(?:_)?Test` alternation would document intent. Cosmetic. | `src/roam/catalog/_shared.py` | 30 min |
| **W900 — Per-language adapter table** (W889+W891+W893 cross-cut, carried forward). Suffix-tuple + camelCase-pattern-tuple unification across 4 layers. Deferred behind W898. | `src/roam/catalog/_shared.py` + 3 sister layers | 4-6h |
| **W871 bulk migration — remaining 22 detectors** to the `@detector` registry per W869 hybrid Archetype B+E. Blocked behind W895 / W896 / W897 design decisions (carried forward). **Highest-leverage follow-up after the design calls close.** | `src/roam/catalog/smells.py` + `src/roam/catalog/registry.py` | 4-8h |
| **W870 — Per-detector `*_DETECTOR_VERSION` sparse-stamp lint** (W867 finding, carried forward). W871 decorator registry should subsume the surface; defer until bulk migration lands. | `src/roam/catalog/registry.py` + `tests/` | 1-2h |
| **W872 — Layer-classification heuristic audit across clone detectors** (W864 finding, carried forward). | `src/roam/catalog/clones_cross_layer.py` + sibling detectors | 2-4h |
| **W875 — Consolidate `_finding` / `_make_finding` constructors** (W886 finding) — **PARTIALLY SHIPPED via W923 (catalog-layer canonical landed) + W935 (id-builders canonical landed)**. Remaining sister-helpers in non-catalog modules pending. | `src/roam/catalog/_shared.py` + 2 migration sites | 1-2h |
| **W863 — `ALL_DETECTORS` alphabetical ordering + drift-guard** (carried forward). | `src/roam/catalog/smells.py` + `tests/` | 1-2h |
| **W855 follow-on — CLI surface + findings persistence for rename-invariant clones** (carried forward). | `src/roam/commands/cmd_clones.py` + `src/roam/db/findings.py` | 4-6h |
| **W748 follow-on — Smell catalog 24 → 29 candidate wave** (carried forward). **Unblocked by W864 (shipped).** | `src/roam/catalog/smells.py` + per-smell modules | 4-8h per detector |

### Closures since W908 (W909-RESEARCH / W910 / W911 / W912 / W913 / W877 / W878 / W879 / W880 / W894 / W901 / W904 / W905 / W907 / W914 — registry-parity remediation + cargo-cult cycle audit)

The W922 consolidation pass folds in ~16 status changes from the
W908→W921 stretch. **Arc 1 (registry-parity remediation)** graduated
three W909-RESEARCH candidates to HIGH-RISK fixes (W910 cmd_alerts
thresholds + W911 `_CONFIDENCE_BASES` derive-from-canonical + W912
detector-metadata coverage lint + W913 backfill). **Arc 2 (W908 hoist
arc carry-through)** landed the W877 / W878 / W879 / W880 / W901
hoists in detail — W878 in particular sealed a QUADRUPLE-mirror that
W874 missed via literal-string grep. **Arc 3 (W902 cargo-cult follow-
through)** sealed W904 + W905 + W907 + landed the CLAUDE.md "Verify
the cycle before hedging" anti-pattern rule. **W914 second stale-
pending re-triage** flipped 8 more easy-win closures + 1 supersession,
bringing the W876+W914 combined total to 19 stale-pending tasks
flipped across the W886 → W908 → W922 arc. Strike-throughs preserved
on originating pending lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W909-RESEARCH — 14+ more registry-parity drift candidates~~ | W909 | Surfaced beyond W869's 10. Top three graduated to HIGH-RISK fixes this batch (W910 + W911 + W912/W913). Remaining captured as W915-W921 pendings. |
| ~~W910 — HIGH-RISK: `cmd_alerts._DEFAULT_THRESHOLDS` missing `bottlenecks` + `dead_exports`~~ | W910 | Backfilled `bottlenecks` (>5, WARNING) + `dead_exports` (>20, INFO). New 3-test parity lint at `tests/test_w910_alerts_threshold_parity.py` pinning `_DEFAULT_THRESHOLDS / _TREND_LABELS / _WORSE_WHEN_*` in lockstep. **46/46 focused tests green.** Closes a Pattern 2 silent-fallback at the threshold layer. |
| ~~W911 — HIGH-RISK: `_CONFIDENCE_BASES` derive-from-canonical~~ | W911 | `src/roam/catalog/detectors.py:_CONFIDENCE_BASES` now derives from `roam.db.findings.CONFIDENCE_*` canonical constants. Frozenset shape preserved; zero outside callers needed updates. New parity test `tests/test_w911_confidence_tier_parity.py` (3 tests). **162 focused tests pass.** Closes a Pattern 3a vocabulary-divergence path between the detector catalog + findings registry. |
| ~~W912 — HIGH-RISK: detector-metadata coverage lint~~ | W912 | `tests/test_w912_detector_metadata_coverage.py` (3 tests) asserts every `_QUERY_COSTS` task_id has a matching `_DETECTOR_METADATA` row. Pre-fix gap: 11 detectors silently fell back to default precision/impact. |
| ~~W913 — Detector-metadata backfill~~ | W913 | Backfilled 11 missing `_DETECTOR_METADATA` rows in `src/roam/catalog/detectors.py:4001-4020` with deliberate per-detector precision/impact picks. xfail removed from W912 lint. Parity now 34 task_ids ↔ 34 metadata rows. **17/17 focused tests pass.** |
| ~~W877 — `_enclosing_symbol` hoist landed in detail~~ | W877 (W922 detail) | Defensive variant chosen as canonical (preserves `try/except OperationalError` contract); type_switch's permissive variant replaced. **253 focused tests pass.** |
| ~~W878 — `_bare_command_name` QUADRUPLE-mirror SEALED~~ | W878 | W874 missed `modes/policy._normalise_command` as a 4th twin because grep was literal-string. All four sites consolidated into new module `src/roam/commands/_command_utils.bare_command_name` (42 LOC). **-47 +33 across 3 patched files; 158 focused tests pass.** Captured methodological gap as W920 (literal-string clone-detection misses semantically-equivalent rename-variants; W855 detector could replace literal sweep). |
| ~~W879 — `_camel_split` hoist landed in detail~~ | W879 (W922 detail) | `retrieve/seeds.py` → `search/index_embeddings.py` canonical (12 lines removed). Companion W901 added `__all__ = ["_camel_split"]`. |
| ~~W880 — `_parse_iso` hoist landed in detail~~ | W880 (W922 detail) | `evidence/change_evidence.py` → `evidence/approval.py` canonical (12 lines removed). The "duplicated here to avoid import cycle" docstring was VERIFIED FACTUALLY WRONG and deleted. |
| ~~W894 — Temporal-coupling confidence-tier mismatch fixed inline~~ | W894 (W922 detail) | Hand-rolled side was right (W602+W647 intentional split: parent = `heuristic` for git-cochange frequency; rollup = `structural` for graph aggregation). Decorator side aligned. **W867 lint extended with a value-parity test** so the same drift can't recur. |
| ~~W901 — `__all__ = ["_camel_split"]` export declaration~~ | W901 | Declares the underscored name as intentionally exported across the search/retrieve package boundary post-W879. |
| ~~W904 — `django_post.py` docstring corrected: alleged duplication NEVER EXISTED~~ | W904 | Triple-false claim (no cycle + no duplication + confused readers). python_lang.py is Django-agnostic; the "duplicated from python_lang" hedge was pattern-matched cargo-cult. |
| ~~W905 — `cmd_oracle.py:83` lazy import PROMOTED to module-level~~ | W905 | Companion try/except masking the impossible `ImportError` REMOVED. No cycle exists; the defensive try/except was dead code. **316 focused tests pass.** |
| ~~W907 — CLAUDE.md "Verify the cycle before hedging" sub-section landed~~ | W907 | Added between "Never N/A without running it" and "Adding-a-command checklist" in Quality-discipline section. Codifies the cargo-cult anti-pattern that W904 + W905 + W880 collectively exposed (3+ false-hedge replications). |
| ~~W914 — Second stale-pending re-triage (8 closures + 1 supersession)~~ | W914 | W336 (#439) / W362 (#465) / W370 (#474) / W370b (#493) / W371 (#475) / W383 (#545) / W399 (#507) — all duplicate-pending shadows of already-completed waves. W356 superseded as obsolete process directive. **Combined with W876, the two passes have flipped 19 stale-pending tasks total.** |
| ~~(ADD) W922 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W909-W921 batch~~ | W922 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W921 (queue for next session — W922-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W915 — `_QUERY_COSTS` closed-enum-as-string-literals.** Same Pattern 3a shape as W911; the keys live as bare string literals rather than canonical-constant references. Trivial hoist behind a `_QUERY_COST_KEYS` frozenset. | `src/roam/catalog/detectors.py` | 1-2h |
| **W916 — CLAUDE.md cite `findings.py` as confidence canonical.** Post-W911 the canonical site for confidence-tier strings is `roam.db.findings.CONFIDENCE_*`. CLAUDE.md's "Confidence-tier vocabulary" sub-section should cite the module + constant names explicitly. | `CLAUDE.md` | 30 min |
| **W917 — `test_smells_confidence_mapping_parity` hardcoded-string set should derive from findings.** W867 lint currently hardcodes its allowed-tier set; should derive from `roam.db.findings.CONFIDENCE_*` for the same reason W911 flipped `_CONFIDENCE_BASES`. | `tests/test_smells_confidence_mapping_parity.py` | 1h |
| **W918 — `_resolved_thresholds` silent fallback for unknown metrics.** Returns a default threshold on unknown metric names. Should raise OR surface a `partial_success=True` envelope so agents calling `roam alerts <new-metric>` don't get a silent default-pass. | `src/roam/commands/cmd_alerts.py` | 1-2h |
| **W919 — TypedDict for cmd_alerts rule shape.** The rule dict carries `threshold` / `direction` / `severity` / `trend_label` / `worse_when_*` ad-hoc; a TypedDict would surface drift at write time (the W910 backfill would have failed at type-check time on a TypedDict-annotated `_DEFAULT_THRESHOLDS`). | `src/roam/commands/cmd_alerts.py` | 1-2h |
| **W920 — Differently-named-twin audit via the W855 behavioral-fingerprint detector.** W878 surfaced a 4th literal-named twin that grep missed. The W855 rename-invariant clone detector (already shipped) could replace literal-string sweeps for this class of audit. Worth proving on one more case before generalising. | `src/roam/catalog/clones_rename_invariant.py` + audit | 2-4h |
| **W921 — Audit other "duplicated from python_lang" claims** (W904 follow-up). Sweep the codebase for other "duplicated from python_lang" / "mirrors python_lang" hedges and verify each one is factually true. The W904 finding suggests this is a recurring template. | grep-then-audit | 1-2h |
| **W903 — W686 path-length recurrence operational note.** Recurring across batches; tooling-side investigation not addressable from inside roam. Carried forward. | tooling / harness config | TBD (external) |
| **W906 — Overly-defensive lazy-import comments** in `mcp_server.py` + `oscal.py` (W902 forward-looking, carried forward). Both surface real optional-dep handling but wording reads as cargo-cult; polish in a future docs-only wave. | `src/roam/mcp_server.py` + `src/roam/evidence/oscal.py` | 30 min |
| **W887 — `python_idioms._enclosing_symbol` name collision** (W877 drive-by, carried forward). Third site under `src/roam/python_idioms/` that wasn't part of the W877 hoist — name collision rather than clone; audit candidate. | `src/roam/python_idioms/` + audit | 1h |
| **W888 — `smells._enclosing_symbol` defensive-migration audit** (W877 drive-by, carried forward). Confirmed correct; capture as a discipline note for the next sibling-helper hoist. | docs / discipline note | 30 min |
| ~~**W890 — `is_test_file` None-guard**~~ — closed not-applicable W1015-CONSOLIDATE. Audit verified the W873-era canonical (`changed_files.is_test_file`) already None-guards its `path` argument; no work needed. Closes W886 drive-by-2 carry-forward. | `src/roam/commands/changed_files.py` | n/a |
| **W895 — `rollup_id` auto-infer design call** (W871 follow-up, carried forward). Auto-infer from parent `smell_id` + suffix vs require explicit kwarg on `@detector`. | design + 1 module | 30 min |
| **W896 — Stable iteration ordering design call** (W871 follow-up, carried forward). Alphabetical-by-smell-id vs declaration-order frozenset. SARIF-emitter golden-fixture stability depends on the answer. | design + `registry.py` | 30 min |
| **W897 — `parent_id` finalisation semantics** (W871 follow-up, carried forward). Rollup pattern needs an explicit binding contract. | design + `registry.py` | 30 min |
| **W898 — Long-term catalog/`_shared.is_test_path` delegate to canonical** (sister to W885, carried forward). Defer behind W871 bulk migration so the registry POC has a stable surface. | `src/roam/catalog/_shared.py` | 1-2h |
| **W899 — Tighten the Apex `Test.cls` regex** (W893 follow-up, carried forward). Greedy `.*` works but is non-obvious; explicit `(?:_)?Test` alternation would document intent. Cosmetic. | `src/roam/catalog/_shared.py` | 30 min |
| **W900 — Per-language adapter table** (W889+W891+W893 cross-cut, carried forward). Suffix-tuple + camelCase-pattern-tuple unification across 4 layers. Deferred behind W898. | `src/roam/catalog/_shared.py` + 3 sister layers | 4-6h |
| **W871 bulk migration — remaining 22 detectors** to the `@detector` registry per W869 hybrid Archetype B+E. Blocked behind W895 / W896 / W897 design decisions (carried forward). **Highest-leverage follow-up after the design calls close.** | `src/roam/catalog/smells.py` + `src/roam/catalog/registry.py` | 4-8h |
| **W870 — Per-detector `*_DETECTOR_VERSION` sparse-stamp lint** (W867 finding, carried forward). W871 decorator registry should subsume the surface; defer until bulk migration lands. | `src/roam/catalog/registry.py` + `tests/` | 1-2h |
| **W872 — Layer-classification heuristic audit across clone detectors** (W864 finding, carried forward). | `src/roam/catalog/clones_cross_layer.py` + sibling detectors | 2-4h |
| **W875 — Consolidate `_finding` / `_make_finding` constructors** (W886 finding, carried forward). W686 path-length blocked the dispatch this batch (W903-class operational). | `src/roam/catalog/_shared.py` + 2 migration sites | 1-2h |
| **W863 — `ALL_DETECTORS` alphabetical ordering + drift-guard** (carried forward). | `src/roam/catalog/smells.py` + `tests/` | 1-2h |
| **W855 follow-on — CLI surface + findings persistence for rename-invariant clones** (carried forward). | `src/roam/commands/cmd_clones.py` + `src/roam/db/findings.py` | 4-6h |
| **W748 follow-on — Smell catalog 24 → 29 candidate wave** (carried forward). **Unblocked by W864 (shipped).** | `src/roam/catalog/smells.py` + per-smell modules | 4-8h per detector |

### Closures since W886 (W871 / W877 / W879 / W880 / W881-W884 / W889 / W891 / W893 / W894 / W901 / W902 — cross-layer hoist + decorator POC + cross-language test-path fix)

The W908 consolidation pass folds in ~15 status changes from the
W886→W907 stretch. The W886 pendings list closed in two arcs (cross-
layer hoist + cross-language test-path) plus a decorator-driven
registry POC. Strike-throughs preserved on originating pending lines;
the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W877 — `_enclosing_symbol` hoist to `_shared.py`~~ | W877 | Defensive variant chosen as canonical (preserves `try/except OperationalError` contract; W888 audit). `type_switch.py` local def + Mirror admission docstring removed. **253 focused tests pass.** |
| ~~W879 — `_camel_split` hoist~~ | W879 | `retrieve/seeds.py` → `search/index_embeddings.py` canonical (12 lines removed). Companion **W901** added `__all__ = ["_camel_split"]` to declare the underscored name as an intentionally exported cross-package boundary. |
| ~~W880 — `_parse_iso` hoist~~ | W880 | `evidence/change_evidence.py` → `evidence/approval.py` canonical (12 lines removed). The OLD "duplicated here to avoid import cycle" docstring was VERIFIED FACTUALLY WRONG (no cycle exists; W902 finding). |
| ~~W881 — Delegate `_is_test_path` in `cmd_over_fetch.py`~~ | W881 | Routes through canonical `roam.commands.changed_files.is_test_file`. |
| ~~W882 — Delegate `_is_test_path` in `metrics_history.py`~~ | W882 | Routes through canonical. |
| ~~W883 — Delegate `_is_test_path` in `rules/builtin.py`~~ | W883 | Routes through canonical. |
| ~~W884 — Delegate `_is_test_path` in `rules/dataflow.py`~~ | W884 | Routes through canonical. **Bundle total**: 9 call-sites across the 4 W881-W884 sites. **477 focused tests pass.** |
| ~~W885 — Architectural decision (extend `changed_files` vs invent `roam._common`)~~ | W881-W884 | **RESOLVED EMPIRICALLY**: the canonical `changed_files.is_test_file` already covered every pattern the 4 sites needed — no extension required. Catalog/`_shared` stays narrow; no transverse `roam._common` namespace needed. |
| ~~W889 — Cross-language test-path false-positive fix~~ | W889 | 3 new case-sensitive regex patterns added to catalog `is_test_path` covering Java/Kotlin/C#/Swift/PHP/Scala/Apex camelCase `Test`/`Tests` basenames. W886 xfail-strict pinning test inverted into **11-case positive + 8-case negative parametrize**. **276 tests pass.** Closes W886 drive-by-1. |
| ~~W891 — `_TEST_FILE_SUFFIXES` extended with `_test.exs` (Elixir) + `_test.dart` (Dart)~~ | W891 | Canonical parity. **334 tests pass.** |
| ~~W893 — Apex `*_Test.cls` regex coverage~~ | W893 | **VERIFIED FALSE POSITIVE** — canonical `^.*Test\.cls$` already matches `*_Test.cls` via greedy `.*` consuming the underscore. Pinned with a new 4-layer parity test asserting canonical + catalog + changed_files + DEFAULT_TEST_PATTERNS all match. |
| ~~W871 — P0 `@detector` decorator POC~~ | W871 | `src/roam/catalog/registry.py` (176 lines) exposes `@detector(smell_id, confidence_tier=...)` + `register_rollup_kind(...)` + construction-time validation against W867 vocabulary. **2 detectors migrated**: `speculative-generality` (W853) + `temporal-coupling` parent (W602) + `temporal-coupling-cluster` rollup (W647). **212 focused tests pass.** Validates the W869 hybrid Archetype B+E recommendation on a narrow surface before the bulk 22-detector migration. |
| ~~W894 — Confidence-tier mismatch surfaced + sealed by W871~~ | W894 | HAND-ROLLED was correct (W602+W647 intentional split: parent = `heuristic` for git-cochange frequency, rollup = `structural` for graph aggregation). Decorator side aligned. **W867 lint extended with a value-parity test** so the same drift can't recur silently. |
| ~~W902 — "Duplicated here to avoid X" docstring audit~~ | W902 | 6 matches: 1 real-now-resolved (W880); 3 FALSE HEDGES captured as W904 + W905 + W906 pendings (loader.py / django_post.py / cmd_oracle.py); 2 forward-looking kept (mcp_server.py / oscal.py). Meta-observation: the cargo-cult anti-pattern was replicated 3+ times — captured as W907. |
| ~~W107 — Mode taxonomy (was user-blocked)~~ | W876 follow-on | Confirmed previously-shipped; user-blocked gating note was stale. |
| ~~(ADD) W908 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W887-W907 batch~~ | W908 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W907 (queue for next session — W908-CONSOLIDATE)

| Item | Where | Effort |
|---|---|---|
| **W871 bulk migration — remaining 22 detectors** to the `@detector` registry per W869 hybrid Archetype B+E. POC landed (W871 above); blocked behind W895 / W896 / W897 design decisions. **Highest-leverage follow-up after the design calls close.** | `src/roam/catalog/smells.py` + `src/roam/catalog/registry.py` | 4-8h |
| **W895 — `rollup_id` auto-infer design call** (W871 follow-up). Decide: auto-infer from parent `smell_id` + suffix (e.g. `temporal-coupling` → `temporal-coupling-cluster`) vs require explicit kwarg on `@detector`. | design + 1 module | 30 min |
| **W896 — Stable iteration ordering design call** (W871 follow-up). Decide: alphabetical-by-smell-id vs declaration-order frozenset for the registry's iteration contract. SARIF-emitter golden-fixture stability depends on the answer. | design + `registry.py` | 30 min |
| **W897 — `parent_id` finalisation semantics** (W871 follow-up). Rollup pattern needs an explicit binding contract: cluster ids point to parent id via `register_rollup_kind(rollup_id, parent_id)`; finalise the resolution order vs declaration order. | design + `registry.py` | 30 min |
| **W887 — `python_idioms._enclosing_symbol` name collision** (W877 drive-by). A third site exists under `src/roam/python_idioms/` that wasn't part of the W877 hoist — name collision rather than clone; audit candidate. | `src/roam/python_idioms/` + audit | 1h |
| **W888 — `smells._enclosing_symbol` defensive-migration audit** (W877 drive-by, sister to W877). Audit confirmed the defensive variant is correct; capture as a discipline note for the next sibling-helper hoist. | docs / discipline note | 30 min |
| ~~**W890 — `is_test_file` None-guard**~~ — closed not-applicable W1015-CONSOLIDATE. Audit verified the W873-era canonical (`changed_files.is_test_file`) already None-guards its `path` argument; no work needed. Closes W886 drive-by-2 carry-forward. | `src/roam/commands/changed_files.py` | n/a |
| **W898 — Long-term catalog/`_shared.is_test_path` delegate to canonical** (sister to W885). Today `_shared.is_test_path` carries its own regex tuples; could delegate to `changed_files.is_test_file` instead. Defer behind W871 bulk migration so the registry POC has a stable surface. | `src/roam/catalog/_shared.py` | 1-2h |
| **W899 — Tighten the Apex `Test.cls` regex** (W893 follow-up). Greedy `.*` works but is non-obvious; an explicit `(?:_)?Test` alternation would document intent. Cosmetic. | `src/roam/catalog/_shared.py` | 30 min |
| **W900 — Per-language adapter table** (W889+W891+W893 cross-cut). Today suffix-tuple + camelCase-pattern-tuple live separately across 4 layers; a single per-language adapter table would centralise the discipline. Deferred behind W898. | `src/roam/catalog/_shared.py` + 3 sister layers | 4-6h |
| **W903 — CRITICAL operational: claude subagent type creating worktrees by default → W686 path-length blocking.** ~3 dispatches this batch failed because agent worktrees branched off old SHAs with paths exceeding Windows MAX_PATH despite the prior session's `git config --global core.longpaths true` fix. Tooling-side investigation; not addressable from inside roam. | tooling / harness config | TBD (external) |
| **W904 — `django_post._DJANGO_*` constants duplicated from `python_lang`** (W902 finding). Trivial hoist — same shape as W879/W880, no architectural decision required. | `src/roam/languages/python_lang.py` + migration | 30 min |
| **W905 — `cmd_oracle.py:83` lazy-import false-hedge claim** (W902 finding). Comment claims import-cycle avoidance; no cycle exists. Cosmetic cleanup. | `src/roam/commands/cmd_oracle.py` | 15 min |
| **W906 — Overly-defensive lazy-import comments** in `mcp_server.py` + `oscal.py` (W902 forward-looking). Both surface real optional-dep handling but wording reads as cargo-cult; polish in a future docs-only wave. | `src/roam/mcp_server.py` + `src/roam/evidence/oscal.py` | 30 min |
| **W907 — CLAUDE.md note on the false-cycle hedge cargo-cult anti-pattern** (W902 meta-observation). Pattern replicated 3+ times across unrelated modules; one-paragraph note in CLAUDE.md's LLM-discipline section would deter the next replication. | `CLAUDE.md` | 15 min |
| **W870 — Per-detector `*_DETECTOR_VERSION` sparse-stamp lint** (W867 finding, carried forward). Some detectors stamp `<NAME>_DETECTOR_VERSION` per the Adding-a-command checklist; others don't. Worth a parity lint. **W871 decorator registry should subsume this** — defer until the bulk migration lands. | `src/roam/catalog/registry.py` + `tests/` | 1-2h |
| **W872 — Layer-classification heuristic audit across clone detectors** (W864 finding, carried forward). Controller/service/repository inference heuristics may have similar drift to the now-folded `_find_workspace_root`. | `src/roam/catalog/clones_cross_layer.py` + sibling detectors | 2-4h |
| **W875 — Consolidate `_finding` / `_make_finding` constructors** (W886 finding, carried forward). Two near-identical constructors for the same payload shape; candidate for `_shared.py` extension. **W686 path-length blocked the dispatch this batch** — W903-class operational. | `src/roam/catalog/_shared.py` + 2 migration sites | 1-2h |
| **W878 — `_bare_command_name` triple-mirror** (W874 finding, carried forward) across `cmd_next.py` + `constitution/loader.py` + `modes/policy.py`. Three identical-shape helpers. **Now unblocked** by W885 resolution — pattern follows the W881-W884 delegation discipline. | per-site migration | 1-2h |

### Closures since W865 (W862 / W864 / W867 / W869 / W873 / W874 / W876 — helper-hoist arc + registry-parity research)

The W886 consolidation pass folds in ~19 status changes from the
W865→W885 stretch. Strike-throughs preserved on originating pending
lines; the fast-lookup index lives here.

| Item | Shipped in | Notes |
|---|---|---|
| ~~W862 — `smells.py` docstring/count drift-guard~~ | W862 | `tests/test_smells_detector_count_drift.py` (173 lines, 3 tests). AST lint guarding `ALL_DETECTORS` count against both docstrings. |
| ~~W864 — `_loc()` + `_find_workspace_root()` helper-hoist~~ | W864 | `src/roam/catalog/_shared.py` (~50 lines) created. 4+2 duplicates folded into 1+1 canonical. **332 focused tests green.** |
| ~~W867 — `_SMELL_KIND_TO_CONFIDENCE` parity drift-guard~~ | W867 | `tests/test_smells_confidence_mapping_parity.py` (208 lines, 3 tests). Reference set computed as `ALL_DETECTORS ∪ AST-derived _finding("<id>",...) first-args` to handle the W647 rollup pattern (one detector emits two smell_ids). |
| ~~W869 — registry-parity-pattern research memo~~ | W869 | `dev/REGISTRY-PARITY-PATTERN-2026-05-15.md` (~600 lines). Synthesises the bug-class across 10+ session-observed drift instances. 8 industry references. Recommendation: hybrid Archetype B+E (decorator-driven `@detector(smell_id, confidence_tier)` + construction-time validation + parity-test backstop). P0 = smell-detector registry (W871 captured); P1 = MCP tool registry; P2 = mode-allowlists / `_DEPRECATED_COMMANDS` / `subject_kind`. |
| ~~W873 — `is_test_path()` extension to `_shared.py`~~ | W873 | ~70 lines + 4 pattern tuples covering Python / Go / JS-TS / Java-Kotlin / Ruby / Apex. Folded 2 catalog-layer duplicates: `detectors._is_test_path` (37 call-sites + `_INCLUDE_TESTS_OVERRIDE` semantics preserved) + `type_switch._file_is_test` (1 call-site). 6 sites left alone (already delegated OR canonical at their own layer OR deliberate import-cycle break). **17/17 new + 216/216 sibling tests pass.** |
| ~~W874 — "Mirror smells.py" docstring anti-pattern sweep~~ | W874 | Audited ~75 "Mirror" mentions; only 4-5 are real code clones (captured as W877-W880). Rest is legitimate comparative-narrative. |
| ~~W876 — Stale-pending triage cleanup~~ | W876 | 20 candidates audited; 11 actual stale-pending rows flipped (see Stale-pending flips section below). W107 left pending (user-blocked). |
| ~~W125 — Wave30.1 doc-hygiene CI gap (stale pending)~~ | W876 / shipped via W250 | Duplicate-pending row caught by triage. Wave-number-vs-task-ID collision documented. |
| ~~W221 — Manual integration checkpoint~~ | W876 | Flagged user-blocked; not closable from agent loop. |
| ~~W224 — Producer-gap-pin~~ | W876 | Superseded by W240 / W242 / W261 / W266 / W267 / W268 producer sealing waves. |
| ~~W298-polish — Wave29 polish~~ | W876 | Already shipped; pending row flipped. |
| ~~W319 — Plugin-count convention drift~~ | W876 | Already shipped as #444; pending row flipped. |
| ~~W335 — `caller_metric` drift-guard extended + `cmd_invariants` stamp~~ | W876 | Already shipped; pending row flipped. |
| ~~W342 — `CALLER_METRIC_RAW` canonical constant extraction~~ | W876 | Already shipped; pending row flipped. |
| ~~W345 — W198 permit-persist doc cross-reference refresh~~ | W876 | Already shipped; pending row flipped. |
| ~~W346 — `test_json_contracts.py` module-scope fixture~~ | W876 | Already shipped (~28x speedup); pending row flipped. |
| ~~W348 — `INLINE_CONTENT_SOFT_LIMIT` advisory wording polish~~ | W876 | Already shipped; pending row flipped. |
| ~~W349 — Permit-persist red-team test surface~~ | W876 | Already shipped (19 tests; W377-W382 drive-bys closed by W436 batch); pending row flipped. |
| ~~W352 — Python 3.10+ minimum documented~~ | W876 | Already shipped (companion to W412); pending row flipped. |
| ~~W353 — Wave29 plan refresh~~ | W876 | Already shipped (`dev/WAVE29-MCP-WRAPPER-PLAN-2026-05-15.md` updated); pending row flipped. |
| ~~(ADD) W886 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W862-W885 batch~~ | W886 (this wave) | Docs-only; hash-stability mandate held trivially. |

### Pending after W885 (queue for next session — superseded by Pending after W907 above)

| Item | Where | Effort |
|---|---|---|
| **W870 — Per-detector `*_DETECTOR_VERSION` sparse-stamp lint** (W867 finding). Carried forward to W908 pendings — W871 decorator registry subsumes the surface; defer until bulk migration lands. | `src/roam/catalog/smells.py` + `tests/` | 1-2h |
| ~~**W871 — P0 `@detector` decorator implementation**~~ | shipped W871 (W908 batch) | POC landed in `src/roam/catalog/registry.py` (176 lines); 2 detectors migrated; 212 tests pass. Bulk 22-detector migration carried forward to W908 pendings behind W895/W896/W897 design calls. |
| **W872 — Layer-classification heuristic audit across clone detectors** (W864 finding). Carried forward unchanged. | `src/roam/catalog/clones_cross_layer.py` + sibling detectors | 2-4h |
| **W875 — Consolidate `_finding` / `_make_finding` constructors** across `smells.py` + `detectors.py`. Carried forward — W686 path-length blocked the dispatch this batch (W903-class operational). | `src/roam/catalog/_shared.py` + 2 migration sites | 1-2h |
| ~~**W877 — Hoist `_enclosing_symbol` from `type_switch.py` to `_shared.py`**~~ | shipped W877 (W908 batch) | Defensive variant chosen as canonical. **253 focused tests pass.** |
| **W878 — `_bare_command_name` triple-mirror** (W874 finding) across `cmd_next.py` + `constitution/loader.py` + `modes/policy.py`. **Now unblocked** by W885 resolution — pattern follows the W881-W884 delegation discipline. Carried forward to W908 pendings. | per-site migration | 1-2h |
| ~~**W879 — Hoist `_camel_split` from `retrieve/seeds.py`**~~ | shipped W879 (W908 batch) | `retrieve/seeds.py` → `search/index_embeddings.py` canonical. Companion W901 added `__all__ = ["_camel_split"]`. |
| ~~**W880 — `_parse_iso` duplication**~~ | shipped W880 (W908 batch) | `evidence/change_evidence.py` → `evidence/approval.py` canonical. The "duplicated here to avoid import cycle" docstring was VERIFIED FACTUALLY WRONG (W902 finding). |
| ~~**W881-W884 — Delegate `_is_test_path` in 4 remaining sites**~~ | shipped W881-W884 (W908 batch) | 4 cross-layer delegations to canonical `changed_files.is_test_file` across `cmd_over_fetch.py` + `metrics_history.py` + `rules/builtin.py` + `rules/dataflow.py` (9 call-sites). **477 focused tests pass.** |
| ~~**W885 — Architectural decision** (extend `changed_files` vs invent `roam._common`)~~ | RESOLVED EMPIRICALLY by W881-W884 (W908 batch) | The canonical `changed_files.is_test_file` already covered every pattern the 4 sites needed. Catalog/`_shared` stays narrow; no transverse `roam._common` namespace needed. |
| ~~**W107** — Mode taxonomy tightening~~ | shipped (confirmed by W876 follow-on stale-pending audit) | Previously-shipped; user-blocked gating note was stale. |

### Pending after W742 (queue for next session)

| Item | Where | Effort |
|---|---|---|
| **W632** — drive-bys on the W596 + W606 + W610 trio (confidence-helper migration tail; wheel-smoke CI extension). | various | 4-8h |
| **W703** — extend the W693 cross-loader compat test to the n1 / taint / vibe-check / conventions suppression loaders (Phase B-b → Phase B-c tail). | per-detector loader audit | 2-4h |
| **W713** — README + landing-page callouts for the W708 + W742 edge-attribution family closure. The marketing copy must avoid claiming "fixed every detector" — the validation pass is the load-bearing claim. | README.md + templates/distribution/landing-page/ | 2-3h |
| **W724 Phase C-2 — Suppression family closure capstone.** Deprecate the legacy parsers; drift-guard pins the single canonical loader after the Phase B / Phase C-1 migrations have landed and stabilised. Blocked behind W703. | `src/roam/policy/suppression_v2.py` + drift-guard | 2-4h |
| **W741** — `cmd_triage` malformed-input divergences surfaced by W738 BAIL — design the closed-schema migration path for the user-facing triage surface that preserves the legacy lenient behavior on the three divergent inputs (or breaks them with a clear error envelope per Pattern 2). | `src/roam/commands/cmd_triage.py` + `src/roam/policy/suppression_v2.py` | 4-6h |
| **W745** — README + landing-page rebadge: "Pattern-3a STRUCTURALLY CLOSED across all three rank axes (severity + confidence + risk)" — the third-axis close (W631) is sellable as a structural quality milestone. | README.md + landing-page | 1-2h |
| **W748** — Smell catalog 20 → 25 follow-up wave: candidate kinds (string-typing / shotgun-surgery / feature-envy / data-clump / divergent-change) per the Wirfs-Brock smell catalog. **Blocked behind W703 + W724** to keep the suppression substrate stable during the catalog expansion. | `src/roam/catalog/smells.py` | 4-8h per detector |
| **W602 follow-on** — top temporal-coupling finding `cli ↔ _run_roam_inprocess` at 34 commits (W647 clustered it into the `cmd_health.health` group but the underlying refactor is still queued). | refactor across the `cli.py` + `_run_roam_inprocess` boundary | 4-8h |
| **W612-W613** — finish the fragile-path sweep (29 remaining sites in the `_PRE_W594_PENDING` allowlist — carry from W635 batch). | `tests/_helpers/repo_root.py` consumers | 2-4h |
| **W709 — W708 + W742 joint validation pass** (HIGHEST PRIORITY; broad). Every detector that reads edges needs a re-pin against the post-W708 + post-W742 edge counts. Plus a fresh dogfood pass on roam-code itself to surface any formerly-buried real positives that the corrupted edges were masking. | taint / side_effects / critique / dead / smells / vibe-check / ai-rot test corpora + dogfood | **HIGHEST PRIORITY** — 4-8h. |
| **W749-W754 (drive-bys on this batch)** — additional bare-except sweeps (allowlist 3 → 0); wider `importlib.resources` migration to remaining file-handle sites; smell-suppression substrate adoption per detector; W647 rollup adoption by other pair-detectors (clones, dark-matter); W695 `--card` smoke template extended to `mcp --tools-table` + `mcp --doctor` handlers; W689 `.editorconfig` extended to dev/ + docs/ subdirectories; W685 auto-count extended to llms-install.md + landing-page HTML rows; comment-density extension to remaining languages (yaml/toml/dockerfile/makefile). | various | 8-16h |
| **W752 / W753 / W754** — additional drive-bys queued for the next batch (TBD; placeholder numbers reserved by W755 for the next CONSOLIDATE checkpoint to populate). | TBD | TBD |

### Pending after W722 (queue for next session — superseded by Pending after W742 above)

| Item | Where | Effort |
|---|---|---|
| ~~**W709 — W708 validation pass**~~ | shipped partially through the W742 invariant test + still queued as the broad joint W708 + W742 pass per "Pending after W742" above. | partial |
| ~~**W723 Phase B-b — Suppression typed-loader continuation**~~ | shipped W755 batch (W723 + W736 + W737 + W738 phased through Phase C-1). | shipped |
| ~~**W724 Phase C — Suppression family closure capstone**~~ | partial — Phase C-1 shipped W755 batch (W736 + W737 + W738); Phase C-2 capstone still queued per "Pending after W742" above (now blocked behind W703 + W741). | partial |
| **W602 follow-on** — top temporal-coupling finding `cli ↔ _run_roam_inprocess` at 34 commits. | refactor across the `cli.py` + `_run_roam_inprocess` boundary | 4-8h (carry-forward) |
| **W612-W613** — finish the fragile-path sweep (29 remaining sites — carry from W635 batch). | `tests/_helpers/repo_root.py` consumers | 2-4h (carry-forward) |
| ~~**W699-W732 (drive-bys on this batch)**~~ — most shipped W755 batch: W699 (`_format_count` refactor cluster-finding seal), W702 (`_DEPRECATED_COMMANDS` AST contract), W705 (unified `_CommentSyntax`), W707 (dead-code REAL BUG seal), W720 (hcl + apex extension), W722 (Phase B-a smells typed), and the suppression Phase C-1 trio (W736 + W737 + W738). **Remaining (W749-W754)** carry forward as the drive-by row in "Pending after W742". | various | mostly shipped |

### Pending after W685 (queue for next session — superseded by Pending after W722 above)

| Item | Where | Effort |
|---|---|---|
| ~~**W691 — Suppression-parser consolidation (CRITICAL latent bug seal)**~~ | `.roam/suppressions.json` readers + closed-schema migration + drift-guard | **shipped W733 batch** — W691 unified the schema between `finding_suppress` + sarif readers. Pairs with W692 Phase A (dataclass) + W722 Phase B-a (typed loader) for the phased close. |
| ~~**W602 follow-on**~~ — top temporal-coupling finding `cli ↔ _run_roam_inprocess` at 34 commits is still queued for a refactor (W646 sealed the W601 finding; W602 is the next real-positive follow-on). **W647 clustered the finding** into the `cmd_health.health` group; the underlying refactor is still queued (carries forward as the W602 follow-on row in "Pending after W722"). | refactor across the `cli.py` + `_run_roam_inprocess` boundary | partial (rollup shipped W733; refactor still queued) |
| **W612-W613** — finish the fragile-path sweep (29 remaining sites in the `_PRE_W594_PENDING` allowlist — carry from W635 batch). | `tests/_helpers/repo_root.py` consumers | 2-4h |
| ~~**W686-W697 (drive-bys on this batch)**~~ — most shipped W733 batch: W689 `.editorconfig`, W685 README header auto-count, W695 `--card` smoke, W697 extras-gate, W693 cross-loader compat, W647 rollup, W649 alerts lowercase, W650 block-comments, W691 schema unify, W692 dataclass, W693 compat, W695 smoke, W697 gate, W702 AST contract, W705 unified `_CommentSyntax`, W707 dead-code, W708 CRITICAL fix, W720 hcl/apex, W722 Phase B-a. **Remaining (W699-W732)** carry forward as the drive-by row in "Pending after W722". | various | mostly shipped |

### Pending after W648 (queue for next session — superseded by Pending after W685 above)

| Item | Where | Effort |
|---|---|---|
| ~~**W642-W656**~~ — drive-bys on this batch: additional Pattern-3a audit passes against the `risk_rank()` consumers (parity with W594 fragile-path follow-on), new smell-kind candidates (post W370c the catalog can absorb more without stub debt), wider `importlib.resources` migration to remaining file-handle sites in `mcp_server.py`, additional cross-detector smoke matrices for plugin-introduced detectors. **Most shipped W698 batch**: W642 (triple-parent fallback removed), W646 (`_create_extractor` refactor), W653 (run_all_detectors classify), W658 (smell-suppression substrate), W661 (catalog/detectors fail-loud), W662 (bare-except AST drift-guard), W664 (`__init__.py` drift-guard), W665 (bare-except narrow), W668 (as_file audit), W676 (suppression-parser audit BAILED with critical find). | various | mostly shipped |
| ~~**W646**~~ — refactor `_create_extractor` 23-arm switch surfaced by W601 — **shipped W698 batch**. 105 → 17 lines via `_LANGUAGE_EXTRACTORS` dispatch dict. | `src/roam/languages/` | shipped |
| **W612-W613** — finish the fragile-path sweep (29 remaining sites in the `_PRE_W594_PENDING` allowlist — carry from W635 batch). | `tests/_helpers/repo_root.py` consumers | 2-4h |

### Pending after W610 (queue for next session — superseded by Pending after W648 above)

| Item | Where | Effort |
|---|---|---|
| ~~**W601-W605**~~ — new smell kinds — **all 5 shipped W657 batch** (W601 switch-statement / W602 temporal-coupling / W603 magic-numbers / W604 boolean-parameter / W605 comment-density). Smell catalog reached 20 detectors (was 15 at session start). | `src/roam/catalog/smells.py` | shipped |
| **W612-W613** — finish the fragile-path sweep (29 remaining sites in the `_PRE_W594_PENDING` allowlist). | `tests/_helpers/repo_root.py` consumers | 2-4h |
| **W614-W630** — drive-bys on this batch (confidence-helper migration tail, wheel-smoke CI extensions, AST drift-guard refinements, smell-detector follow-on patterns). **W624 shipped W657 batch** (importlib.resources migration of mcp --card handler). | various | 4-8h |
| ~~**W631**~~ — third rank axis — **shipped W657 batch**. New `src/roam/output/risk.py::risk_rank()` helper; 2 sites migrated. Pattern-3a STRUCTURALLY CLOSED ACROSS ALL THREE AXES. 131 tests pass. | new canonical module + AST drift-guard | shipped |
| **W632-W634** — drive-bys on the W596 + W606 + W610 trio. | various | 4-8h |

### Pending after W591 (queue for next session — superseded by Pending after W610 above)

| Item | Where | Effort |
|---|---|---|
| ~~**W594**~~ — finish the fragile-path sweep — **W594 shipped W635 batch** (18 of 47 sites migrated; 29 remain — track under W612-W613). | `tests/_helpers/repo_root.py` consumers | shipped (partial) |
| ~~**W595**~~ — fix the `_wrap_with_alias_normalization` param-ordering bug surfaced by W587 — **shipped (sealed)**. **W606** added the AST lint catching the pre-W595 crash class at PR time. | `src/roam/mcp_server.py` + `tests/test_surface_consistency.py` | shipped |
| ~~**W596**~~ — next Pattern-3a target: 15 confidence-rank tables (corrected upward from the 14 estimate) consolidated into canonical helpers — **shipped W635 batch**. 561 tests pass. | new canonical module + AST drift-guard | shipped |
| **W597** — `roam evidence-oscal --kind assessment-plan` standalone emitter. **Number reassigned from old W566 scope** (W566 reused for severity helpers). | new emitter | 4-6h |
| **W598** — extend W518 framework-vocab module to remaining bypass sites. **Number reassigned from old W540 scope** (W540 reused for git-helper consolidation). | `src/roam/evidence/control_mapping_vocab.py` consumers | 2-3h |
| **W585 / W586 / W589 / W590 / W592 / W593 / W599** — drive-bys on the W591 batch (severity-helper migration tail, leasing parity follow-ons, git-helper sites outside `pr-bundle`, additional fragile-path sites picked up by W594). **W588 shipped W635 batch** (AST drift-guard for fragile-path pattern). | various | 4-8h |

### Pending after W570 (queue for next session)

| Item | Where | Effort |
|---|---|---|
| **W550-W553** — drive-bys on the W534 `from_canonical_json` + W559 `--strict` AR path: extend the typed-surface contract to non-AR collector paths (pr-bundle, cga emit, audit-trail). | `src/roam/evidence/` + collector wiring | 4-8h |
| **W555 / W556** — W554 follow-on: audit the rest of `src/roam/templates/` subdirectories for similar pyproject-package-data gaps. **The W570 lint catches new gaps**, but older subdirectories may already be drifted. | `pyproject.toml` + audit pass | 2-4h |
| **W558** — extend the W557 auto-derive path to remaining scattered version-string sites (`docs/`, landing-page HTML). | `dev/build_readme_counts.py` + targets | 2-3h |
| **W560** — wire the W559 `--strict` flag into the rest of the evidence-oscal kinds (Control Mapping + Component Definition when they ship). | `src/roam/commands/cmd_evidence_oscal.py` | 2-3h |
| **W562** — extend the W561 `dropped_enum_rows` disclosure to other AR-shape consumers (Mode envelope, run envelope) so the Pattern 1 variant D guard runs end-to-end across the evidence stack. | `src/roam/evidence/` + envelope shapers | 2-4h |
| ~~**W564-W569**~~ — drive-bys on the W547/W548 severity vocab. **W564 + W565 + W566 + W569 shipped W591 batch** — W564 is the MASSIVE severity-rank consolidation (10 sites, 460 tests, Pattern-3a structural close); W565 + W566 are `severity_to_confidence_level()` + `severity_breakdown()` helpers in `_severity.py` (5 call-sites, 248 tests; **numbers reassigned from old AST-lint / assessment-plan scopes — those scopes roll forward as W596 / W597 below**); W569 is the 9-stale-path doc sweep across 8 src/dev files + 1 test docstring + 1 fixture-regen command (111 tests). **W567 + W568 still queued** (see below). | `src/roam/output/_severity.py` consumers | mostly shipped |
| **W571-W577** — drive-bys on the W570 drift-guard: extend pinning to the rest of the `MANIFEST.in` / package-data ecosystem (taint YAML rule files, plugin discovery glob, etc.). **W573 shipped W591 batch** as a NO-OP investigation: only 1 production call site for `ChangeEvidence.from_canonical_json*` exists (the one W561 already migrated) — Pattern 1 variant D family is fully sealed at the CLI boundary. | `tests/test_package_data_wheel_drift.py` extension | partial |
| **W567** — W533-bundle follow-on: audit non-taint detectors for similar OWASP-mislabel claim-integrity issues (smells, vibe-check, n1, missing-index — any detector that stamps owasp_top10 or CWE). **Number reassigned from old W547 scope** (W547 reused for canonical severity). | rule-file audit pass | 2-4h |
| **W568** — `tests/test_doc_consistency.py` extension for the W518 framework-vocab drift-guard so the docs and the code share a single source of truth on framework names. **Number reassigned from old W548 scope** (W548 bundled into canonical severity with W547). | `tests/test_doc_consistency.py` | 1-2h |
| Carry-forwards from §25.8 / Pending after W533: **W536** (OSCAL Component Definition emitter), **W537** (W512 structural-close README + landing-page callout), **W538** (`roam doctor` advisory check for W531 SARIF severity round-trip), **W539** (taint findings-row hash pin downstream of W511 + W524), ~~**W540**~~ (old scope: extend W518 framework-vocab module to remaining bypass sites; **W540 number was reassigned in W591 batch to seal the `_git_fingerprint` + `_git_commit_sha` consolidation — `pr-bundle init` halved subprocess calls, 105 tests**; the original framework-vocab extension scope rolls forward as **W598**), **W541** (extend W509 + W521 + W520 belt-and-suspenders to other audit-trail fields), **W542** (README + landing-page SLSA SRC-L3 callout — now extra sellable because W520 closes the chain), **W543** (W465 OSCAL AR integration test against fixture audit_trail), **W544** (W506 iso_42001 rename audit), **W545** (W492 + W453 owasp_top10 plumbing extension to cmd_critique exit-message), **W546** (W512 follow-on `kind=` literal audit outside `kind IN (...)`). | various | per §25.8 estimates |

### Pending after W515 (carry-forward — supersedes per §25.8)

| Item | Where | Effort |
|---|---|---|
| ~~**W509**~~ shipped W533 batch — pr-bundle emit commit_sha fallback via `git rev-parse HEAD`. Restores SRC-L3 commit-anchored provenance parity with cga path. | `src/roam/commands/cmd_pr_bundle.py` + `src/roam/attest/emit_vsa.py` | shipped |
| ~~**W506**~~ shipped W533 batch — 3 SLSA SRC-L2/L3 control-mapping entries + iso_42001 → iso_iec_42001 rename lockstep across 5 files. | `templates/audit-report/control-map.yml` | shipped |
| ~~**W510**~~ closed by **W512** — see W512 row in Shipped section. Highest-priority queued item from §24 NOW SEALED. | n/a | shipped via W512 |
| **W511-doctor** — `roam doctor` follow-on for the W482-surfaced `.github/workflows/roam.yml` drift (26 vs 28 lines). **Note: W511 number was reassigned in W533 batch to seal `side_effects.py:497` edge-kind union (FOURTH silent no-op in W493 family). This row is the original §24 carry-over; track under new ID W538 instead.** | `.github/workflows/roam.yml` | 30 min |
| **W512-callout** — README + landing-page callout for the W493/W499/W511/W524 critical fixes (production-grade taint + critique + effects + hover now real on roam-code itself). **Note: W512 number was reassigned to the STRUCTURAL CLOSE wave; this user-facing copy is now tracked as W537.** | README + landing-page | 1-2h |
| **W513** — taint findings-row hash pin (downstream of W493 + W511 + W524 fixes; `chain_length` distribution and import-edge counts are now real data so the findings-row goldens want a fresh pin). | `tests/test_findings_taint.py` (new pin) | 1-2h |
| **W514** — CAISI control-map entries (held to H2 2026 by W428; tracker only). | `templates/audit-report/control-map.yml` | TBD |
| ~~**W492**~~ shipped W533 batch — owasp_top10 loaded into TaintRule/TaintFinding + persisted to findings.evidence_json. 207 tests. | (rolled into W533-bundle) | shipped |
| ~~**W494**~~ — closed audit-only W1015-CONSOLIDATE (`test_inter_unused_return` order-sensitivity audit found taint inter-procedural unused-return analysis deterministic across input order; no fix needed). **W495 / W496** still queued — investigation drive-bys from W493 fix (test-corpus extension for chain-length fan-out on JS / Go bridges respectively). | `tests/test_taint_*.py` per bridge | 1-2h each |
| ~~**W500** / **W501**~~ — W499 critique-gate follow-on coverage waves. **W500 bailed in W591-bundle as already-done** (subsumed by W512 structural close per the original note). **W501 closed in W591-bundle** — audit comments added to 4 test files documenting the no-rerun rationale. **W497 / W584 also bailed as already-done** in the same bundle (investigate-first discipline saved fabricating work). | `src/roam/commands/cmd_critique.py` + sibling checks | shipped (bail / audit) |
| **W507** / **W508** — closed-enum lint extensions surfaced by W505-bundle (additional vocabularies surfaced during the AST pass). | `tests/test_*_enum_drift.py` (new) | 1-2h |
| **W306 / W307** — final Wave29 sub-waves against the remaining ~3-4 MCP wrappers (carry-over). | `src/roam/mcp_server.py` | 1-2h |
| ~~**W370c**~~ — remaining smells stubs from W368 BEHIND list — **shipped W635 batch** (catalog reached ZERO placeholder stubs; W601-W605 queued for new smell kinds). | `src/roam/catalog/smells.py` | shipped |
| **W363 / W365 / W366** — MCP state-mutating sub-waves from W340 audit (carry-over). | `src/roam/mcp_server.py` + new tests | 4-6h |
| **W442** — remaining `effects_taint` function-summary memoization slice (W440 landed cache handoff; W441 bailed-with-find; W485 reframed). | `src/roam/index/effects.py` + `src/roam/security/taint*` | 4-8h |
| **W433** — `effects_taint` umbrella wave (parent of W440-W442 + W485). | (parent) | (above) |
| ~~**W464 / W465**~~ W465 shipped W533 batch — OSCAL v1.2 Assessment Results emission via `roam evidence-oscal --kind assessment-results` (stub Assessment Plan auto-synthesized per FedRAMP continuous-assessment pattern). 81 tests. W464 still in flight for Control Mapping kind. | `templates/audit-report/` + new emitter | W465 shipped; W464 in flight |
| **W437 / W438 / W439** — W405 shallow-git drive-bys. | TBD | TBD |
| **W444 / W445** — W432 oracle dedup drive-bys. | TBD | TBD |
| ~~**W447 / W448**~~ — shipped W591 batch. **W446** still queued. — W429 small-cleanup bundle drive-bys. | TBD | partial |
| **W450 / W458 / W459 / W460** — W443/W449 MCP-table auto-gen drive-bys. | TBD | TBD |
| ~~**W452 / W453**~~ W453 shipped W533 batch — owasp_top10 plumbed to SARIF tags[]. W452 still queued (python-ssti drive-by). | TBD | W453 shipped; W452 TBD |
| **W455 / W456 / W457** — W374 java-sqli drive-bys; W455 = engine Java qualified-name resolution. | `src/roam/security/taint_engine.py` | 4-8h |
| **W461 / W462 / W463** — W454 qualified_only flag drive-bys (wider rollout). | TBD | TBD |
| **W434 / W435** — W408 drive-bys (carry-over). | TBD | TBD |
| **W423** — PageRank warm-start (carry-over; demoted behind W433). | `src/roam/graph/pagerank.py` | 2-4h |
| **W424** — SQLite `synchronous=NORMAL` (carry-over; demoted behind W433). | `src/roam/db/connection.py` | 1-2h |
| **W407** — Louvain cache VALIDATE (carry-over). | `src/roam/graph/clusters.py` | 1h |
| **W431** — W347 prefix-pattern cluster drive-by remainder. | `src/roam/mcp_server.py` `_PARAM_ALIASES` | 1-2h |
| **W415c / W415d / W427** — llm-smells v1.1 drive-bys (carry-over). | `src/roam/catalog/llm_smells.py` | 2-4h |
| **W413 / W414** — structural cleanup carry-overs. | various | 2-4h |
| **W404 / W406** — remaining W395 perf sub-waves. | `src/roam/index/` | 4-8h |
| **W481** — wire SLSA SRC-L3 emit through `pr-replay` (W451 added pr-bundle path; pr-replay parity still queued). | `src/roam/commands/cmd_pr_replay.py` + collector | 2-4h |
| **W482-original** — `target → symbol` MCP-tool-description sweep to drop the remaining 5 `_PRE_W332_EXEMPT` entries (W482 number re-scoped to advisory-check; this is the original scope re-queued). | `src/roam/mcp_server.py` | 1-2h |
| **W483** — detector FP-rate corpus selection per W470-research. | new corpus picker + `dev/` memo | 4-6h |
| **W484** — taint YAML lint elevation (promote `warnings.warn` to hard error once W455 lands). | `src/roam/security/taint_engine.py` | 1-2h (after W455) |
| **W487** — CI template integration test exercising `--with-slsa-l3` end-to-end against a fixture repo. | `tests/test_ci_setup_slsa_l3.py` (new) | 2-3h |
| **W488-original** — taint hygiene-guard extension to cover the `path → paths` cluster surfaced during W430's exempt-list audit (W488 number re-scoped to test-corpus sweep; this is the original scope re-queued). | `src/roam/security/taint_engine.py` lint | 1-2h |
| **W489** — perf ground-truth memo update against W440 + W485 real-data deltas. | `dev/EFFECTS-TAINT-PERF-RESEARCH-2026-05-15.md` | 1h |
| **W490** — README + landing-page SLSA SRC-L3 callout. | README + landing-page | 1-2h |

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

Phase-0 monetisation freebies (per STATUS-2026-05-10 user directive) — `permit`/`postmortem`/`article-12-check` already exist (confirmed by W4.3 capability registry). Refinement and packaging is the next step. The 2026-05-13 external research pass is captured in `dev/MONETIZATION-OPPORTUNITIES-2026-05-13.md`; the deeper synergy map is `dev/MONETIZATION-SYNERGY-MAP-2026-05-13.md`. Together they add Agent Governance Evidence Pack, Premium Rules/Policy Packs, Team MCP Gateway, Security Reachability Triage, Agent Vendor Benchmark, Framework Intelligence Packs, Team Index Cache, Codebase Due Diligence, AI Adoption Readiness Audit, Migration/Refactor Assurance, Agent Observability Bridge, Governance Evidence Exporters, and Post-Incident Replay as monetisable extensions of already-built primitives.

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

- ~~`cmd_mcp` async/fast freshness check — 38s startup is currently in skip-tier (Rank 9, est 3h, 1 H finding closed)~~ — shipped in W13.3 (see `src/roam/commands/cmd_mcp.py:1-132`: `--skip-freshness` flag + fast check + fallback). Sprint table line 154 confirms "cmd_mcp 38s→<2s".
- ~~PHP/Laravel taint rule pack (Rank 12, 4-6h, 1 H — highest project-value for real-world PHP apps)~~ → shipped in W78 (5 YAML files, 101 rule entries under `src/roam/security/taint_rules/php_*.yaml`); **W79 also fixed the engine BFS query bug** (`taint_engine.py:283,333` — `kind IN ('calls','references')` missed singular `'call'`/`'reference'` edge kinds, so the entire `roam taint` subsystem had been silently returning 0 findings since v12).
- Vue/Vitest detection for `test-pyramid`/`endpoints`/`n1` (Rank 15, 4h, 3 H)
- ~~Vue SFC import resolution for `orphan-imports`/`verify-imports` (Rank 17, 3h, 2 H)~~ — DONE in W6.3; regression sentinels live in `tests/test_vue_sfc_imports.py` (5 tests, 13 with `test_dead_vue_consumers.py`)
- ~~Alias consolidation — deprecate 7 redundant aliases (`digest`/`math`/`refs`/`snapshot`/`trend`/`onboard`/`churn`) currently in `_INTENTIONALLY_UNCATEGORISED` allowlist (Rank 18, 1h)~~ — shipped in W3.3 + W5.4; 7 aliases moved to `_DEPRECATED_COMMANDS` in `src/roam/cli.py`; deprecation warning emitted via stderr + `summary.deprecation_warning`; contract pinned by `tests/test_alias_deprecation.py` (5 tests). CHANGELOG `[Unreleased] ### Deprecated` documents user-facing surface.
- Sparse spectral / scale algorithms for `duplicates`/`x_lang`/`spectral` "graph too large" bailouts (Rank 19, 4h, 3 H) — PARTIAL: `duplicates` has `--sample` + `--max-pairs` flags; `x_lang` has `--scope` recommendation envelope; `spectral` still emits `state: graph_too_large_for_spectral_dense` and tells callers to use `clusters`/`partition` instead (no sparse Lanczos — see `cmd_spectral.py:105-113`). Sprint table line 79 ("W5.3 shipped") claims this is done but the spectral half is not.
- ~~Extend trend tracking beyond `dead_exports` (Rank 20, 3h)~~ — shipped; `cmd_trends.py:103-105` tracks `_QUALITY_METRICS = [cycles, god_components, bottlenecks, dead_exports, layer_violations]` + `_GROWTH_METRICS = [files, symbols, edges]` + `_COMPOSITE_METRICS = [health_score]` (9 metrics total, all flow through `_analyze_trends` with anomaly + Mann-Kendall + forecasting).
- Detector tuning: `SfxmlExtractor._TAG_TO_KIND` maps `customobject` not `object` (R10.5 from STATUS-2026-05-10)
- Detector tuning: `roam math` false-positive on `format_table` (R11.B — recognize cell-formatting nested loops as O(rows×cols), not nested-iteration anti-pattern)

**A1 full Capability Registry consolidation** — PARTIAL → PROGRESSING. The command-side
auto-decoration shipped via W10.4 (218/226 = 96.5%; allowlist 190→8) and is
production-quality. The FULL collapse of the 8 split-brain dicts
in `mcp_server.py` is now in-flight: ~~`_DESTRUCTIVE_TOOLS`~~ → shipped W98 (derived `frozenset` view, built from `@_tool(destructive=True)` decorator kwarg — see `src/roam/mcp_server.py:301-307`); ~~`_DEPRECATED_COMMANDS`~~ → W100 in flight (parallel pattern); ~~`_NON_READ_ONLY_TOOLS`~~ → shipped W108 (derived view from `@_tool(read_only=False)`); ~~`_NON_IDEMPOTENT_TOOLS`~~ → shipped W113 (derived view from `@_tool(idempotent=False)`); 4 remaining: `_CORE_TOOLS`/`_REGISTERED_TOOLS`/`_TASK_REQUIRED_TOOLS`/`_TASK_OPTIONAL_TOOLS`/`_TOOL_METADATA`. Foundational for R13.

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
- "AI PR review tool" framing — locked to "local codebase intelligence layer"
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
