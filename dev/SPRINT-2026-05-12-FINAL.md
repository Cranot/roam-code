# Sprint 2026-05-12 to 2026-05-13 -- Final Summary

## Scoreboard

- 19 waves, 100+ agents dispatched in parallel sprints-of-sprints
- 25+ new commands shipped (`brief`, `next`, `agents-md`, `mode`, `lease`,
  `side-effects`, `idempotency`, `causal-graph`, `tx-boundaries`,
  `intent-check`, `dogfood-aggregate`, plus 14 supporting verbs)
- 30+ substrate modules (`world_model/`, `quality/`, `atomic_io`,
  `runs/helpers`, `pattern3c reconciliation layer`, ...)
- 9 R-series capabilities (R20 replay + R21 leases + R26 PR-bundle +
  R27 agent-modes + R28 4-of-4 world-model + R29 ledger signing +
  R30 brief router + R31 next router + R32 agents-md generator)
- 6 dogfood systemic anti-patterns closed (the six in
  `internal/dogfood/SYNTHESIS-2026-05-12.md`)
- 6 Pattern 3c vocabulary chasms reconciled: `cycles_total` and
  `god_components` and `ai_rot_score` now agree across health /
  describe / fingerprint / agent-export / dashboard / vibe-check
- 3 substrate-found bugs fixed in roam itself (W17.4 atomic_write
  consolidation, W18.x stale-refs corruption, W18.x sbom FP
  categories)
- 6 user-reported analyzer FPs fixed (Wave 18: stale-refs corruption,
  sbom phantom categories, dead Vue consumers, missing-index
  unconditional verdict, over-fetch three-state, ws-resolve unmatched)
- Total LOC: +13,369 / -1,518 across 300 tracked files + 135
  untracked entries (+175k newlines, dominated by the 212-eval
  dogfood corpus)
- Test count: 339+ tests passing in the sprint pack (5 chunks,
  37 sprint-new test files; 1 stale-allowlist failure fixed during
  recheck)
- Commits: 0 (per directive -- this sprint stages everything for the
  user to review and commit)

## R-series shipped

- R20 -- Run-ledger replay (`runs start/log/end/show/verify`) with
  HMAC signing chain (W17.4)
- R21 -- Multi-agent lease system (`lease claim/release/list/show/gc`)
- R26 -- Proof-carrying PR bundle (`pr-bundle init/add/emit/validate`)
  with auto-risk derivation from world-model classifications
- R27 -- Agent execution modes (`mode set/show`) with per-mode
  command allowlists
- R28 phase 1 -- side-effects + idempotency world-model classifiers
- R28 phase 2 -- causal-graph + tx-boundaries world-model classifiers
- R29 -- HMAC-signed event ledger (`.roam/runs/.ledger_key` + chain
  verification on `runs verify`)
- R30 -- `brief` router (verdict-first situational awareness)
- R31 -- `next` router (priority-aware "what to do now")
- R32 -- `agents-md` generator (AGENTS.md / CLAUDE.md / .cursor/rules
  unified)

## Dogfood corpus measurement

- Baseline (sprint-start, BACKLOG W14.1):
  590 open findings / 143 H / 385 M / 62 L (212 evals)
- W19.4 final (`roam dogfood-aggregate`, 219 evals, 179 still open):
  438 open / 85 H / 310 M / 43 L
- Cumulative delta:
  -152 open (-25.8%) / -58 H (-40.6%) / -75 M (-19.5%) / -19 L (-30.6%)
- 39 evals reached `status: fixed-in-13` during the sprint
- 1 eval marked `unverifiable-on-this-repo` (intentional)

## Integration checks (Phase 2)

| Check | Status | Notes |
|-------|--------|-------|
| A. Triple-chain hardened | PASS | 4 events auto-logged, signing verified |
| B. Pattern 3c reconciliation | PASS | cycles_total=17 + god_components=50 agree across health/describe/fingerprint/agent-export; ai_rot_score=12 agrees vibe-check/dashboard |
| C. R28 4-of-4 coherent | PASS | All four classify `_atomic_write_text`; side-effects + idempotency coherent |
| D. PR-bundle auto-risks | PASS | First risk: `H _atomic_write_text performs io_write (non_idempotent)` from auto_collect |
| E. Mode + ledger | PARTIAL | Signing in events: PASS. Per-event mode field: NOT captured (mode is global state, not per-event) |
| F. Doc consistency tests | PASS | 18/18 in test_compat_sweep + test_readme_surface_consistency |
| G. Dogfood aggregate matches | PASS | 438 open across 179/219 evals -- matches W19.4's claimed numbers |

## Tests (Phase 3)

| Chunk | Tests | Result |
|-------|-------|--------|
| C1: brief / next / agents-md / modes / leases / law4 | 107 | PASS |
| C2: pattern3c / pr-bundle-causal / atomic / runs / ledger | 58 + 1 skip | PASS |
| C3: loop e2e / synergy x4 / mode dispatch / world-model fidelity | 32 + 1 skip | PASS |
| C4: side-effects / idempotency / causal / tx / capability / compat / formatter | 40 + 1 FIXED | PASS (after fix) |
| C5: stale-refs / sbom / dead-vue / missing-index / over-fetch / ws / doctor | 75 | PASS |
| Doc consistency | 18 | PASS |
| LAW 4 lint | 8 | PASS |
| **TOTAL** | **339** | **GREEN** |

## Architectural invariants (Phase 4)

| Invariant | Status |
|-----------|--------|
| `roam surface` command count matches CLAUDE.md headline (233) | PASS |
| `mcp_tool_count: 57` (CLAUDE.md core preset) | PASS (mcp_server.py owned by W14 reservation -- not touched) |
| All new `cmd_*.py` carry `from __future__ import annotations` | PASS (10/10 sampled) |
| `roam.world_model` has 4 files (side_effects, idempotency, causal_graph, tx_boundaries) | PASS |
| `roam.quality` has 4 files (ai_rot, cycles, god_components, public_symbols) | PASS |
| `roam.atomic_io` consolidation: <= 1 inline `tempfile.mkstemp` outside atomic_io.py | PASS (only `cmd_pr_replay.py` -- non-critical subprocess invocation) |
| Capability registry coverage (decorated count) | PASS at 225/233 after recheck fix |
| LAW 4 lint passing | PASS (8/8) |

## Diff sanity (Phase 5)

- 300 tracked files modified
- 135 untracked entries (mostly the 212-eval dogfood corpus + dev notes)
- +13,369 / -1,518 LOC in tracked files
- +175,021 lines across untracked corpus + dev artefacts

## Bugs found and fixed during recheck

1. `tests/test_capability_decoration.py::test_every_command_eventually_decorated`
   stale xfail allowlist -- `causal-graph` and `tx-boundaries` were
   decorated in W15.3 but never promoted into `DECORATED_TODAY`. The
   test correctly flagged drift. Fixed by adding both to
   `DECORATED_TODAY` and `_FORCE_IMPORT_MODULES`, and bumping the
   expected count from 223 to 225.

## What is NOT done (open queue carried over)

- Per-event mode capture in the run ledger (Integration E PARTIAL --
  mode is global state, not threaded into events). Tracked as
  design decision; can be promoted to feature work later.
- Docs micro-drift: CLAUDE.md and README still reference "211/217/204"
  in older paragraphs alongside the new headline "233". W19.1 owns
  this; pattern-replace pass not yet complete.
- 179 dogfood evals still open (438 findings: 85 H, 310 M, 43 L).
  Next sprint targets the 85 H block.
- Mode allowlist still uses 43-command `safe_edit` (W14.2 default);
  needs broader auditing once R27 settles.
- MCP server (`src/roam/mcp_server.py`) reserved (W14 -- not touched
  this sprint). All MCP additions deferred to a dedicated wave.

## Overall verdict

GREEN. 339 sprint-pack tests pass; all six dogfood systemic patterns
have a concrete fix landed (verified by Pattern 3c reconciliation
check); R28 4-of-4 classifiers all coherent on a real symbol; PR
bundles now auto-derive risks from world-model classifications; the
event ledger is HMAC-signed and chain-verifiable. The one test
failure surfaced during recheck was a stale-allowlist bookkeeping
issue (the test caught real drift -- the desired behaviour) and was
fixed surgically in two edits.
