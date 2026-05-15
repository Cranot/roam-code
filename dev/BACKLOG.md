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

Current build-order memo:
[`dev/BUILD-PRIORITIES-2026-05-13.md`](BUILD-PRIORITIES-2026-05-13.md).
Use it for "what should we build next?" decisions. `ROADMAP.md` remains the
full index; this backlog remains sprint state/history.

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
