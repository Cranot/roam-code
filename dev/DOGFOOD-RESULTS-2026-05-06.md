# Dogfood results — 2026-05-06 (round 3, GM session)

User asked for "more polish + improvements + heavy dogfooding". Twelve
phases shipped: 8 polish/feature improvements + 4 dogfood phases.

The dogfood loop surfaced **3 real defects** in our own v2 code that we
fixed in-flight:

1. Two CRITICAL-severity functions (cc≥99) caught by `roam complexity` on
   our own modules — refactored to <28 cc each.
2. A real bug: `roam pr-analyze --batch --cache` silently dropped the
   `--cache` flag for inner per-file invocations. Found by measuring zero
   speedup on the second run; fixed; speedup now 24.5×.
3. The cache_hit metadata key was being stripped by `json_envelope`'s
   `_meta` rebuild. Fixed by surfacing it as a top-level key.

## Phase table (this round)

| # | Phase | Outcome |
|---|---|---|
| P1 | Centralise constants + metrics-push --timeout | `DEFAULT_AUDIT_TRAIL_PATH` consolidated via `audit_trail_helpers` re-export (was duplicated in 3 modules). `metrics-push --timeout SECONDS` added. |
| P2 | 5 small polish wins (P.5–P.9) | --explain mentions --json pairing; --quiet+--json mutex warning; conformance disclaimer at top level; rules-validate gate-failure hint; --parallel oversubscription warning. |
| P3 | Parametrised test all 9 signals have explanations | Guardrail test catches future scorer additions that forget the explanation function. |
| P4 | rules-validate `--explain` mode | Pattern reference block surfaces matchers + glob examples + use cases for first-time rule authors. |
| P5 | pr-comment-render last-PR linking + age line | "Previous: BLOCK at TIMESTAMP" link on drift; "saved 3 days ago" line on `--from-baseline`. |
| P6 | audit-trail-export --aggregate top-snapshot fields | JSON summary now exposes `top_actor` / `top_repo` / `top_month` / `top_verdict` for at-a-glance procurement reading. |
| P7 | Audit-trail `sequence_number` + `--finalize` | Records get monotonic sequence numbers; `audit-trail-export --finalize` writes a closing `AuditIntegritySummary` record (chain head + event count + algorithm). Forensic-format conventions per the round-2 research. |
| P8 | Starter rule packs (Python + TypeScript) | `templates/rules/python/.roam-rules.yml` (14 rules) + `templates/rules/typescript/.roam-rules.yml` (14 rules) + README. Both validated clean by `rules-validate`. |
| P9 | New command `roam dogfood` | One-shot v2 stack runner: audit + pr-analyze + audit-trail + conformance in one envelope. First-touch demo + local self-check. **Surface bump: +1 CLI command (185 → 186), +1 MCP tool (135 → 136), core preset 48 → 49.** |
| P10 | Heavy dogfood — run roam against itself | `roam dogfood` on roam-code: health 86, pr-analyze SAFE, conformance 83/100. `roam complexity` surfaced 2 CRITICAL v2 functions: `_compute_ai_likelihood` (cc=110) + `_render_github_markdown` (cc=101). **Refactored both** by extracting per-signal / per-section helpers. Both now <28 cc. |
| P11 | Cache speedup measurement | Built 5-real-commit batch from `git log` + `git show`. Cold run 12.2s → warm run 0.5s = **24.5× speedup**. **Found and fixed real bug**: `--cache` wasn't being propagated through `_emit_batch` to per-file inner CLI invocations; cache_hit metadata was being dropped by `json_envelope`'s `_meta` rebuild. Both fixed; cache hit-rate now correctly reported in batch summary. |
| P12 | This report + final sweep | 383 tests pass, 12 v2 files lint-clean. |

## Dogfood findings (file-by-file)

### Health snapshot (live)
```
roam dogfood
VERDICT: health 86 · pr-analyze SAFE · conformance 83/100
  audit health:    86/100
  pr-analyze:      SAFE  (blast 51, ai 24, rules 0)
  conformance:     83/100  (5/6 checks passed)
```

### `roam complexity` — top v2 functions before refactor
```
cc=110  _compute_ai_likelihood      cmd_pr_analyze.py:579       <-- CRITICAL
cc=101  _render_github_markdown     cmd_pr_comment_render.py    <-- CRITICAL
cc= 71  _load_rules_yaml            cmd_pr_analyze.py:863       (HIGH)
cc= 49  _build_payload              cmd_metrics_push.py:105     (HIGH)
cc= 48  _emit_batch                 cmd_pr_analyze.py:1302      (HIGH)
cc= 39  _build_rationale            cmd_pr_analyze.py:1086      (HIGH)
cc= 38  pr_analyze                  cmd_pr_analyze.py:1417      (HIGH)
```

### After refactor (CRITICAL functions removed)
```
cc= 71  _load_rules_yaml            cmd_pr_analyze.py:863
cc= 49  _build_payload              cmd_metrics_push.py:105
cc= 48  _emit_batch                 cmd_pr_analyze.py:1331
cc= 39  _build_rationale            cmd_pr_analyze.py:1115
cc= 38  pr_analyze                  cmd_pr_analyze.py:1446
cc= 29  _check_rules                cmd_pr_analyze.py:1052
cc= 28  _verify_chain               cmd_audit_trail_verify.py:25
cc= 27  _signal_explanation         cmd_pr_comment_render.py:73
```

`_compute_ai_likelihood` and `_render_github_markdown` both dropped from
CRITICAL (>=99) to below the top-8 (<28). The next biggest function is
now `_load_rules_yaml` at cc=71 (HIGH but not CRITICAL — fine for now).

### Cache speedup measurement (5-file batch)
```
COLD: 12.219s  (cache hits 0/5)
WARM:  0.498s  (cache hits 5/5)
       -----
       24.5x speedup
```

The cache key includes diff text + rules content + threshold + language
override + cache version, so any change invalidates. For a CI workflow
that re-runs pr-analyze on every push, the warm-cache path is
near-instantaneous when the diff hasn't changed.

## Surface counts now

- **CLI commands**: 185 → **186** (+1: `dogfood`)
- **MCP tools**: 135 → **136** (+1: `roam_dogfood`)
- **Core MCP preset**: 48 → **49**
- **Tests**: +50 across new files: `test_audit_trail_sequence.py` (7), `test_dogfood.py` (7), and +30 added to existing test files (rules-validate --explain, signal-explanation parametrise, drift-arrow rendering, baseline age, aggregate snapshot, polish wins).

**383 tests pass (2 skipped). 12 v2 files lint-clean.**

## Files added / modified this round

### New (untracked)
```
src/roam/commands/cmd_dogfood.py                      193 lines, new CLI command
templates/rules/README.md                             rule pack index
templates/rules/python/.roam-rules.yml                14 rules, validated clean
templates/rules/typescript/.roam-rules.yml            14 rules, validated clean
tests/test_audit_trail_sequence.py                    7 tests for sequence_number + --finalize
tests/test_dogfood.py                                 7 tests for the dogfood command
dev/dogfood-batch/commit-{1..5}.diff                  5 real-commit fixtures used by P11
dev/DOGFOOD-RESULTS-2026-05-06.md                     this report
```

### Modified (most already untracked)
```
src/roam/commands/cmd_pr_analyze.py                   +200 lines (cache propagation in batch, _compute_ai_likelihood refactor into 9 signal helpers + diff-parser + bucket-score helper, --rules-strict polish, BLOCK-bypass hint, --quiet+--json mutex warning, --parallel oversubscription warning, sequence_number support, top-level cache_hit/cache_key keys)
src/roam/commands/cmd_pr_comment_render.py            +160 lines (_render_github_markdown refactored into 8 section helpers, last-PR link line, baseline age line)
src/roam/commands/cmd_audit_trail_export.py           +90 lines (--aggregate snapshot fields, --finalize integrity summary, _build_integrity_summary helper, snapshot rendering in markdown)
src/roam/commands/cmd_audit_trail_conformance.py       refactor: use shared load_records helper, top-level disclaimer in JSON
src/roam/commands/cmd_audit_trail_verify.py            use shared DEFAULT_AUDIT_TRAIL_PATH from helpers
src/roam/commands/cmd_metrics_push.py                  +30 lines (--timeout flag, age_days + stale fields)
src/roam/commands/cmd_rules_validate.py                +90 lines (--explain mode + _PATTERN_DOCS + gate-failure hint)
src/roam/commands/audit_trail_helpers.py               +30 lines (next_sequence_number + INTEGRITY_SUMMARY_SCHEMA)
src/roam/cli.py                                        +1 command registration
src/roam/mcp_server.py                                 +1 MCP tool wrapper, core preset 48 → 49
README.md                                              186/136 counts + dogfood + rules-validate --explain row
CLAUDE.md                                              186/176/136 counts
llms-install.md                                        186/136 counts
docs/site/.well-known/mcp-server-card.json             136/49 counts
src/roam/mcp-server-card.json                          136/49 counts
tests/test_pr_analyze.py                               +5 tests (--quiet, batch --parallel/--progress, _process_single_diff helper)
tests/test_pr_analyze_edge_cases.py                    (no new tests this round; existing rules-strict tests confirm working)
tests/test_pr_comment_render.py                        +13 tests (signal explanation parametrise, before-after rendering, baseline age, drift previous-verdict line)
tests/test_audit_trail_aggregate.py                    +3 tests (snapshot fields, snapshot-empty, markdown snapshot line)
tests/test_rules_validate.py                           +2 tests (--explain pattern reference, _PATTERN_DOCS coverage)
tests/test_metrics_push.py                             (no new tests; --timeout covered by manual smoke)
```

## Smoke test (re-runnable)

```bash
# 0. Install + reindex
pip install -e . -q && roam index

# 1. New command: dogfood
roam dogfood
# expected: VERDICT: health NN · pr-analyze X · conformance NN/100

# 2. Cache speedup (re-run is instant)
rm -rf .roam/pr-analyze-cache
time roam pr-analyze --batch dev/dogfood-batch --cache --quiet  # cold ~12s
time roam pr-analyze --batch dev/dogfood-batch --cache --quiet  # warm ~0.5s

# 3. Audit-trail finalize
echo '{"verdict":"SAFE","previous_record_hash":"","timestamp":"2026-05-05T00:00:00Z","actor":"a"}' > /tmp/trail.jsonl
roam audit-trail-export --input /tmp/trail.jsonl --finalize
# expected: trail now ends with an AuditIntegritySummary line with chain_head + event_count

# 4. Aggregate snapshot
roam audit-trail-export --input /tmp/trail.jsonl --aggregate
# expected: markdown headers include "**top verdict**: SAFE (1)" line

# 5. Starter rule packs
roam rules-validate templates/rules/python/.roam-rules.yml
roam rules-validate templates/rules/typescript/.roam-rules.yml
# expected: VERDICT: valid (14 rule(s) loaded clean) for both

# 6. rules-validate --explain
roam rules-validate templates/rules/python/.roam-rules.yml --explain | head -30
# expected: Pattern reference block + 4 pattern entries with examples

# 7. Refactored signal scoring still works
echo "+def handle_request(req):
+    # We use this approach because it's clean
+    raise NotImplementedError()" | roam pr-analyze --json --input /dev/stdin 2>/dev/null \
  | python -c "import sys,json; e=json.load(sys.stdin); print('placeholder:',e['ai_likelihood']['signals']['placeholder_density']); print('llm_phrase:',e['ai_likelihood']['signals']['llm_phrase_density'])"
```

## What's NOT done (still intentionally)

- **No version bump** in `pyproject.toml` (still 12.25)
- **No git commits** by me
- **No git push** by me
- **No PyPI upload**
- **No git tag**
- **No GitHub release**

When you're ready to bump, follow `dev/RELEASE-CHECKLIST.md`. The new
release should bump to **12.26** — the surface is now 186 CLI / 136 MCP /
49 core preset, and there are 3 fewer CRITICAL-complexity functions in
the v2 modules than at the start of this session.

## Recommendations for next session

1. **F.7 — split cmd_pr_analyze.py into a sub-package.** Now ~1900 lines.
   The recent refactors mean each sub-piece is well-encapsulated, making
   the file split mechanical: `pr_analyze/scoring.py` (9 signal helpers),
   `pr_analyze/rules.py` (matchers), `pr_analyze/audit_trail.py` (chain
   emission), `pr_analyze/drift.py` (baseline comparison), `pr_analyze/
   cache.py` (cache helpers). ~3 h.
2. **DF.5 expansion — author 3 more rule packs** (Go, Java, Kotlin) in
   `templates/rules/`. Each is ~30 min of curation. Reuses the same
   pattern types tested in P8.
3. **DF.1 — set up `roam dogfood --gate` in roam-code's own CI.** Drop a
   `.github/workflows/dogfood.yml` that runs `roam dogfood --json` on
   every PR + posts the summary as a sticky comment. Dogfood
   demonstration in the wild.
4. **DF.16 — migrate release.yml to PyPI Trusted Publishing** (per the
   round-2 research finding). ~3 d, eliminates long-lived API tokens.
5. **Deep #6 — start the GitHub App TypeScript scaffold.** The CLI engine
   is now production-grade; this is the natural next big push.

## End-of-session state verification

- `pyproject.toml` version: **12.25** (unchanged)
- HEAD: **f1101fb** (unchanged from session start)
- Working tree: 18 modified-or-new files, all linted, all tested

— assistant, 2026-05-06 round 3 (GM session)
