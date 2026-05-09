# Dogfood triage — 2026-05-10

20 `roam` passes against the repo. High-impact findings captured here for
human review. Auto-fixable items handled in the same session (stale-refs
12 edits across 7 files).

## Verdict-line summary

| # | Pass | Verdict |
|---|---|---|
| 01 | doctor | 16/17 pass (cloud-sync warning is intentional state) |
| 02 | db-check | OK (0 high, 0 medium, 0 errors) |
| 03 | health | 81/100 — 33 critical, focus: god_components |
| 04 | debt | moderate — 21 cycles, 235 god components, 1703 hotspots |
| 05 | complexity | avg 3.9, 458 critical, 528 high — worst: `_scan_buffer_for_diagnostics` (178) |
| 06 | dead | 456 dead exports — 87 safe to delete, 321 review, 48 intentional |
| 07 | clones | 116 clusters (446 functions, 76% avg similarity) |
| 08 | math/algo | **30 algorithmic improvements (22 high, 8 medium)** |
| 09 | n1 | clean — no implicit N+1 patterns |
| 10 | missing-index | clean |
| 11 | hotspots | clean |
| 12 | coupling | 100 coupled pairs (top: pyproject+CHANGELOG, expected) |
| 13 | fan | top fan-in `cli_runner` (test fixture, expected) |
| 14 | fitness | 1 of 3 fitness rules fail (50 violations) |
| 15 | layers | moderate (79% in Layer 0) — 0 violations |
| 16 | stale-refs | 184 stale (9 auto-fixable, applied this session) |
| 17 | doc-staleness | 484 stale docs (>90 days) |
| 18 | orphan-imports | 167 orphan imports across 706 files |
| 19 | coverage-gaps | needs --gate flag |
| 20 | critique HEAD~1 | clean (no findings on the last diff) |

## High-impact, queued for human review

### A. Math/algo — 5 N+1 patterns (high impact)

These are real algorithmic issues in our own code. Each is a "per-item
query in loop" pattern that would batch nicely with `WHERE IN (...)` or
`executemany()`. Fixing requires careful test verification.

| Location | Pattern | Impact |
|---|---|---|
| `src/roam/commands/cmd_n1.py:742` `analyze_n1` | per-item query in loop | 98.2 |
| `src/roam/commands/cmd_ai_readiness.py:309` `_score_test_signal` | per-item query in loop | 97.8 |
| `src/roam/critique/checks.py:169` `find_changed_symbols` | per-item query in loop | 97.6 |
| `src/roam/workspace/aggregator.py:209` `cross_repo_trace` | per-item query in loop | 96.8 |
| `src/roam/commands/cmd_understand.py:73` `_detect_frameworks` | per-item query in loop | 96.5 |

The irony of `cmd_n1.py` itself having an N+1 pattern is worth flagging.
Suggest a focused session to fix these five with test coverage that
demonstrates the batch behavior. Rough fix template:

```python
# Before
for item in items:
    row = conn.execute("SELECT … WHERE id = ?", (item.id,)).fetchone()

# After
ids = [item.id for item in items]
rows = list(batched_in(conn, "SELECT … WHERE id IN", ids))
```

### B. Quadratic string building (high impact)

`src/roam/index/gitignore.py:21` `_compile_pattern` does `str += in loop`,
O(n²) for n pattern segments. Fix is the canonical `''.join(...)` swap.

### C. complexity-178 god function

`_scan_buffer_for_diagnostics` (LSP scanner) at complexity 178. This
is the single highest-complexity function in the repo. Splitting it
will mean 4-6 new helper functions. Defer to a focused refactor session.

### D. 87 dead exports "safe to delete"

`roam dead --safe-only` would list them. Auto-deletion is risky for
public-API breakage; some "dead" exports may be consumed by external
agents via MCP without showing up in the local call graph. Recommend
manual review of the list — most are likely safe, but a few may be
real public surface that lacks an internal caller.

### E. 116 clone clusters (76% avg similarity)

Many of these will be intentional (template patterns across command
files). A focused cleanup pass would group them by file pair and
collapse the genuinely-redundant ones. Expect ~30-50 real
consolidations after filtering out templates.

### F. doc-staleness — 484 docs > 90 days behind code

Probably mostly OK (many docs are intentionally stable). Bulk action
not warranted; pick the top-10 by traffic and revisit.

### G. fitness — 50 violations on 1 failing rule

`roam fitness --explain` to see the failing rule + violations. May be
a real architecture-drift signal; needs investigation before fixing
since some violations may be deliberate exceptions.

## Auto-fixed this session

- **stale-refs**: 12 path-rename edits across 7 files (CHANGELOG.md
  references to old `templates/site/` and `docs/site/` paths now point
  at the current `templates/distribution/landing-page/` location).
  High-confidence renames per the stale-refs detector.

## Not findings — verified clean

- N+1 query patterns in graph data access: clean (the 5 above are CLI
  command files, not graph access)
- Missing DB indexes: clean
- Runtime hotspots: clean (no traces ingested locally; would require
  `roam ingest-trace` first)
- Layer violations: 0 (architecture clean)
- DB integrity: clean (orphan symbols, broken edges, FTS, all OK)
- All 211 commands import cleanly

## Recommended next session focus

1. Fix the 5 N+1 patterns (B above) — clear wins, well-isolated
2. Fix the quadratic-string-building in gitignore.py (also B) —
   one-liner swap
3. Triage the 87 "safe to delete" dead exports manually
4. Decide whether the complexity-178 split is worth the time vs.
   leaving it as-is (LSP buffer scanners legitimately have high
   complexity from the multi-state parsing)

The dogfood pass itself was the demonstration: roam's own detectors
flagged real, actionable issues in roam's own codebase. That's the
case to make to buyers — a reviewer that finds problems on its own
makers' code is harder for skeptics to dismiss.
