# Feature Synergy Reiterations - 2026-05-03

Goal: review the current feature surface deeply, improve places where existing
features can reinforce each other, extract actionable items across three review
cycles, then execute the consolidated backlog cleanly.

## Iteration 1 - Retrieval Signal Integrity

Review lenses:

- Schema contract: retrieve must read the same embedding table the indexer writes.
- API observability: ranking output should explain when semantic signal contributes.
- Failure posture: optional semantic dependencies must degrade to deterministic zero.
- Testability: zeta behavior needs a small unit test and an integrated rerank test.

Implemented:

- `roam.retrieve.semantic.semantic_score` now reads canonical JSON `vector` and
  `dims` columns from `symbol_embeddings`, matching `roam search-semantic`.
- Malformed, missing, or dimension-mismatched vectors are skipped safely.
- Semantic contribution now appears in rerank justifications as `semantic`.
- Added focused tests for canonical embedding reads and semantic-only rerank lift.

Actionable follow-ups:

- Done: add semantic coverage diagnostics showing how many symbols have dense
  embeddings before users tune `zeta`.
- Done: resolve local dogfood indexing by redirecting the project DB to a
  writable local directory and making stale lock handling tolerate delete-denied
  cloud folders.
- Done: add an index-time warning when retrieve semantic weighting is enabled
  but the embedding table is empty.

## Iteration 2 - Workflow Composition Metadata

Review lenses:

- Agent UX: `ask` should not just dispatch commands; it should expose how to
  interpret and continue a workflow.
- Product composition: recipes should make advanced commands discoverable through
  phases and review lenses instead of a flat command catalog.
- Structured API: MCP and CLI consumers need machine-readable metadata.
- Routing quality: classifier corpus should benefit from metadata terms such as
  security, ownership, blast radius, and test impact.

Implemented:

- Added `phase`, `perspectives`, and `followups` to every `Recipe`.
- Included workflow metadata in classifier token bags.
- Exposed metadata in `roam ask --list`, low-confidence candidates, `--explain`,
  normal text output, and JSON envelopes.
- Added tests locking recipe metadata and JSON output shape.

Actionable follow-ups:

- Done: template follow-up placeholders with the parsed `{symbol}` or `{task}`
  so the `NEXT` section is copy-ready for humans and agents.
- Done: promote recipe metadata into MCP compound tools and report presets so
  they share the same workflow vocabulary.
- Done: add a `roam workflow <recipe>` command that returns the recipe DAG,
  review lenses, rendered command arguments, and next commands without running
  the steps.

## Iteration 3 - Drift, Maintenance, And Adoption

Review lenses:

- Documentation drift: public counts and recipe descriptions should match the
  code surface.
- CI guardrails: count drift and recipe metadata regressions should be caught by
  tests, not manual review.
- Adoption path: the first screen and docs should keep the 5-verb mental model,
  while `ask` becomes the bridge to the larger surface.
- Operating risk: local dogfood commands must be able to update their own index.

Implemented:

- Updated stale README command counts from 152/148 to 154/149 where applicable.
- Updated the README `roam ask` entry from 12 recipes to the current 13-recipe
  registry and documented workflow metadata.
- Removed stale 5/12/22 recipe wording from ask package docs and tests.
- Captured this three-cycle review as a durable report.

Actionable follow-ups:

- Done: add docs drift tests for README-visible counts, including the five-verb
  "other N specialised commands" phrasing and recipe registry count.
- Done: lock public recipe JSON keys in tests: `name`, `intent`, `phase`,
  `perspectives`, `followups`, `examples`, and `commands`.
- Done: refresh the self-index after redirecting the local DB path.

## Iteration 4 - Execution Re-Review

Review lenses:

- Local operability: dogfood commands must work in the actual OneDrive-backed
  workspace, not only in clean temp projects.
- Planner continuity: next actions should be concrete commands, not templates
  agents must mentally substitute.
- Surface reuse: workflow metadata should appear in `ask`, MCP compounds, and
  reports with the same schema.
- Regression prevention: every drift fixed in docs should have a test that
  would fail if it drifted again.

Implemented:

- Added robust index-lock claiming/release for Windows/cloud-sync folders that
  allow overwrites but deny deletes.
- Redirected this workspace's active DB to
  `C:\Users\Dimitris\.codex\memories\roam-code-index` and rebuilt the index.
- Added `semantic_coverage` diagnostics to retrieve JSON summaries.
- Added index-time semantic activation advice when `zeta` is enabled but dense
  embedding coverage is zero.
- Added shared ask workflow metadata helpers and attached workflow metadata to
  MCP compound envelopes and report preset listings/runs.
- Rendered ask follow-up placeholders with parsed symbols/tasks.
- Added targeted drift tests for README command/recipe counts.
- Added `roam workflow` as a no-run workflow inspector and updated public
  command counts from 154 to 155.

Remaining actionable items:

- Consider moving the local DB override from `.codex\memories` to a normal user
  cache path when running outside this sandbox.

## Iteration 5 - Cleanup Re-Review

Review lenses:

- Command maintainability: orchestration commands should read as branch routing,
  not as deeply nested render logic.
- Configuration safety: `roam config --set-db-dir` should reject unusable paths
  before persisting them.
- Schema drift: workflow metadata added to envelopes should also be present in
  advertised MCP schemas and report listings.
- Dogfood quality bar: new surfaces should reduce local preflight risk, even
  while broader repository fitness debt remains.

Implemented:

- Refactored `roam ask` emission/ranking paths into focused helpers, dropping
  command cognitive complexity from critical to medium.
- Refactored `roam config` DB-dir, semantic, exclude, remove-exclude, and show
  branches into focused helpers, dropping command cognitive complexity from 23
  to 9 and nesting depth from 7 to 2.
- Added DB override validation that probes create/write/delete capability before
  `.roam/config.json` is written.
- Persisted validated DB overrides as absolute paths so relative input does not
  depend on the caller's future working directory.
- Added targeted tests for successful, rejected, and relative DB-dir
  persistence.
- Verified `roam workflow` remains low-complexity and keeps workflow metadata
  available without executing recipe commands.

Remaining actionable items:

- Move the local DB override from `.codex\memories` to a normal user cache path
  for non-sandbox developer machines.
- Tackle the existing repository-wide fitness baseline separately: existing
  cycle debt and a large set of historical max-function-complexity violations
  remain outside the focused surfaces cleaned here.

## Iteration 6 - Final Standing Items

Review lenses:

- Local-first ergonomics: developers should have a sanctioned cache-backed DB
  location instead of copying this sandbox's `.codex\memories` override.
- Activation truthfulness: semantic features should report whether dense
  vectors can actually contribute, not just whether options are configured.
- Workflow safety: recipes should advertise their pass/fail gates alongside
  phases, review lenses, and follow-up commands.
- Debt containment: existing repository-wide fitness debt should be baselined
  so new changes can be judged by regression delta.
- Refactor hygiene: high-risk indexing and analysis functions should be split
  into behavior-preserving helpers while focused tests remain green.

Implemented:

- Added `roam config --use-local-cache`, which persists a deterministic
  user-cache DB path for the current project.
- Added `roam config --semantic-status`, reporting dense embedding coverage,
  ONNX dependency/config readiness, and concrete next actions.
- Fixed empty ONNX model/tokenizer paths so they no longer appear ready because
  `Path("")` resolves to the current directory.
- Added recipe gates to `ask`, `workflow`, MCP compound schemas, and tests.
- Added `roam fitness --write-baseline` and `--baseline PATH`, with stable
  violation keys and full current-violation capture for baseline mode.
- Refactored the top focused hotspots:
  `_do_run` from 327 complexity / nesting 7 to 16 / 2,
  `_process_files` from 176 / 6 to 7 / 3,
  `_track_variable_taint` from 215 / 8 to 1 / 1,
  `_extract_math_signals` from 206 / 5 to 8 / 2, and
  `_is_bounded_loop` from 172 / 9 to 1 / 0.
- Refactored the new fitness baseline code after review:
  `_check_metric_rule` from 62 / 3 to 4 / 1 and `fitness` from 25 / 7 to
  10 / 3.

Remaining truth:

- Dense semantic retrieval is wired and diagnosable, but this workspace still
  needs real ONNX model/tokenizer paths and generated embeddings before the
  semantic signal becomes active.
- Repository-wide fitness still has historical debt: the cycle rule currently
  reports 18 cycles plus a large set of max-function-complexity violations.
  Baseline mode now keeps that debt from hiding new regressions.

## Consolidated Priority List

1. Configure a real local ONNX model/tokenizer and build embeddings so
   semantic retrieval contributes non-zero signal.
2. Run a dedicated repo-fitness cleanup pass for the existing cycles and the
   next top complexity reducers (`query_engine`, context rendering, partition,
   dead-code analysis, watch polling, SBOM reachability).
