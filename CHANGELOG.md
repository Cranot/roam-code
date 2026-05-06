# Changelog

All notable changes to [roam-code](https://github.com/Cranot/roam-code) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [12.42] - 2026-05-06

### CI fix — landscape.json self-row version stamp

Hotfix after 12.41. The 12.41 release bumped pyproject + MCP cards
+ competitor_site_data but missed `docs/site/data/landscape.json`'s
self-row, which `tests/test_doc_consistency.py::test_landscape_json_self_row_version_matches`
guards (major.minor must match pyproject). Bumped 12.40 → 12.42 in
that file. No behavior change.

## [12.41] - 2026-05-06

### CI fix — README surface consistency for Phase 0 commands

Hotfix release after 12.40. The README's command listing did not yet
include `permit`, `postmortem`, and `article-12-check`, which broke
`tests/test_readme_surface_consistency.py::test_readme_covers_all_canonical_cli_commands`
on all 5 Python versions in the matrix. Added one-line entries for
each of the three new commands in the canonical command table. No
behavior change; documentation-only fix to restore CI green.

## [12.40] - 2026-05-06

### Pivot to monetization — Phase 0 free-OSS funnel artifacts + commercial landing page

After 8 CI iterations restoring the matrix to green (12.31 → 12.39),
this release pivots from polish to monetization-aligned shipping.
Lands the Phase 0 commands from `build_priorities.md` plus a
starter landing-page implementation for the `roam.review` umbrella.

### New commands (3 — redacted)

- **`roam permit`** — structural-permission verdict facade for AI
  agents. Returns `{verdict, reason, allowed_actions, blocked_actions}`
  over staged changes (`--staged`), an arbitrary diff (`--input`), or
  a target symbol (`--symbol`). Wraps `roam critique` + `roam preflight`.
  Exit codes: 0=ALLOW, 5=BLOCK, 6=REVIEW. Drops into Cursor rules,
  Claude Code permission hooks, pre-commit, GitHub Actions branch
  protection. **Engine reused by the Roam Review GitHub App at PR
  time.**
- **`roam postmortem <commit-range>`** — replays current detectors
  against past commits. Walks `HEAD~30..HEAD` (or any range), runs
  `roam critique` against each commit's diff, reports findings that
  would have surfaced pre-merge. The single highest-conversion buyer-
  meeting demo per the v2 plan: *"if it retroactively catches my Q1
  incidents, redacted."*
- **`roam article-12-check`** — EU AI Act Article 12 readiness
  assessment. 6-item checklist (audit-trail dir, trail records,
  retention policy, technical docs, attestation surface, high-risk
  classification heuristic) → 1-page Markdown report (or PDF with
  `--pdf out.pdf` if reportlab installed). Captures Article-12-curious
  leads before they pick another vendor.

### Commercial landing page (starter)

New directory `templates/distribution/landing-page/` with:

- `index.html` — hero + 3 product cards + buyer-pain band citing
  PocketOS / Amazon Treadwell / DORA 2025 + audit upsell + trust
  strip + FAQ + footer
- `landing.css` — single 6KB stylesheet, IBM Plex Mono + Space
  Grotesk fonts (matches docs/site visual language)
- `README.md` — domain priority list (`roam.review` recommended
  primary, with `roamreview.com` / `roam.cloud` / `roamaudit.com`
  as backups), 3 deploy paths (Cloudflare Pages / Vercel / GitHub
  Pages), content TODOs before going live

`templates/distribution/landing-page-spec.md` updated to reflect
the new domain recommendation.

### Surface counts

- 187 → **190 commands** (+permit, +postmortem, +article-12-check)
- README + llms-install + landscape.json updated

### Tests

- `tests/test_pivot_phase0_commands.py` — 7 tests covering happy-
  path + verdict-decision-tree for `permit`, no-commits-found path
  for `postmortem`, JSON envelope shape + markdown render + file-
  output for `article-12-check`. All pass on Python 3.9-3.13.

## [12.39] - 2026-05-06

### Polish — exhaustive bugbear sweep + B904 cleanup

After the 12.38 PyYAML pin landed CI green for the first time in 8
iterations, ran an exhaustive bug-hunt sweep across 8 categories
(fallback parsers, hardcoded counts, B033/B023, future-annotations,
test-side YAML deps, module-level state survival, lazy-import
asserts, B904 missing-`from`-clauses).

### Findings + fixes

- **B904 missing `from err` / `from None` in re-raises** — 4 sites
  caught by `ruff check --select B904`. All `raise SystemExit(1)`
  after explicit error-handling — added `from None` to mark intent
  explicit:
  - `cmd_check_rules.py:433` (profile-resolution failure)
  - `cmd_coverage_gaps.py:290` (config-load failure)
  - `cmd_trends.py:1645` (date-format error)
  - `cmd_report.py:105` — added `from exc` (preserves chain since
    the original parse error is informative)

### Findings without fixes (low risk, future-watch)

- **Hardcoded counts in tests** — 5 spots verified live-state-matching
  as of v12.39: `BUILTIN_RULES==10`, `RECIPES==24`, `CATALOG==32`,
  `_CORE_TOOLS==49`, `_PRESETS={7 keys}`. Will drift on next
  feature add; release-checklist already has the grep sweep
  (added in 12.33).
- **3 narrow YAML fallback parsers** — `cmd_alerts._parse_alerts_yaml`
  (alerts.yaml schema), `cmd_budget` + `cmd_check_rules` both use
  the now-PyYAML-equivalent `_parse_simple_yaml` from 12.36/37.
  Verified `_parse_simple_yaml` correctly handles their schemas
  (`rules: [- id: ..., threshold: ...]`).
- **Module-level caches** — `_FILE_LINES_CACHE`,
  `_IN_MEMORY_CALL_CACHE`, `_FRAMEWORK_PACK_CACHE`,
  `_ACTIVE_FRAMEWORK_PROFILE`, `_INCLUDE_TESTS_OVERRIDE` all
  properly cleared/restored in `run_detectors`. `_GRAPH_CACHE`
  cleared via conftest autouse fixture.

## [12.38] - 2026-05-06

### Clean fix — PyYAML pinned in `[dev]` extras (kills the recurring Python 3.9 CI red)

12.31 → 12.37 was seven consecutive bugfix releases — every one
caused by some divergence between PyYAML and the in-tree
`_parse_simple_yaml` / `_emit_simple_yaml` fallback on Python 3.9.
Root cause: `fastmcp` (which transitively pulls in PyYAML) is
gated on `python_version >= '3.10'` in `[dev]` extras, so Python 3.9
CI ran without PyYAML. Every test asserting PyYAML-equivalent
behaviour surfaced a missing capability in the fallback. Each fix
narrowed the gap; the gap kept reappearing.

This release **pins PyYAML in `[dev]`** so the test matrix has a
consistent reference parser on every Python version. The fallback
parser/emitter stays in tree (production users without PyYAML still
get a working roam, just with the documented-shape coverage we
built across 12.33-12.37).

### Why not make PyYAML a hard dep?

Considered. PyYAML is one of the most-installed Python packages
globally so the cost would be small. But:

* Production users on minimal installs benefit from optionality.
* The fallback is now well-tested across the seven-iteration sweep
  — parse + emit fully round-tripped for the documented rules.yml
  shape.
* The bug class only surfaces in tests (which assert PyYAML's
  exact behaviour). Production callers tolerate the small
  divergences (e.g. fallback emitter doesn't quote some scalars
  PyYAML would).

So: hard dep stays a future option; for now the test-matrix fix is
sufficient.

### What this changes

- `pyproject.toml` `[dev]` extras: added `pyyaml>=6.0`.
- No source-code changes. The fallback parser/emitter from 12.33-37
  remains.
- CI matrix on Python 3.9 now installs PyYAML and behaves
  identically to 3.10-3.13.

## [12.37] - 2026-05-06

### Bugfix release — `roam rules-validate --fix` write-back works without PyYAML (Python 3.9 CI red, seventh iteration)

12.36 fixed the parser. 12.37 fixes the round-trip: `roam rules-validate
--fix` rewrites `severity: block` → `severity: BLOCK` in memory, then
calls `yaml.safe_dump` to write back. On Python 3.9 (no PyYAML) the
import raised, the write-back was skipped, and the file stayed at
`block` — failing `test_cli_fix_mode_writes_back_to_file` which
asserts `'BLOCK' in file_contents`.

### Bugfix

- **`roam.rules.engine._emit_simple_yaml`** — minimal YAML emitter
  for the `rules:` shape, matching `yaml.safe_dump(doc,
  sort_keys=False)` output for the documented `{"rules": [{...}]}`
  structure. Quotes scalars that contain YAML-special chars; emits
  `null`/`true`/`false` for None/bool; preserves insertion order.
- **`cmd_rules_validate.py`** — write-back fallback now uses
  `_emit_simple_yaml` when `import yaml` raises ImportError, so
  the round-trip works on every Python version.

### Sweep status

- 12.31 → CI red (3 stale assertions)
- 12.32 → CI red (third stale assertion)
- 12.33 → CI red (Python 3.9 fallback parser missing list-of-dicts)
- 12.34 → CI red (Python 3.9 fallback parser too permissive)
- 12.35 → CI red (Python 3.9 bracket check too aggressive)
- 12.36 → CI red (Python 3.9 `--fix` write-back needed PyYAML)
- 12.37 → expected green

The pattern's been: every test that touches the parse OR emit YAML
path on Python 3.9 surfaces a different missing capability in the
fallback. Now we have parse + emit fully round-trippable without
PyYAML for the documented rules.yml shape.

## [12.36] - 2026-05-06

### Bugfix release — bracket-balance check ignores quoted strings (Python 3.9 CI red, sixth iteration)

12.35's bracket-balance malformed-YAML check counted brackets inside
quoted strings. Community rule files (e.g.
`rules/community/dataflow/DF-005-php-cross-fn-sqli.yaml`) have
legitimate `sources: ["$_GET[", "$_POST[", "$_REQUEST["]` shapes —
each string contains a `[` with no matching `]`, but PyYAML happily
parses them. The fallback's naive `s.count("[")` flagged this as
malformed and `test_rules_community_pack.py::test_community_pack_has_1000_plus_valid_rules`
went red on Python 3.9 (PyYAML missing).

### Bugfix

- **`_parse_simple_yaml` strips quoted substrings before bracket
  counting.** Uses `re.sub(r'"[^"]*"|\'[^\']*\'', '', s)` to remove
  `"..."` and `'...'` content first, then counts brackets on the
  remainder. PyYAML-equivalent behaviour: malformed shapes still
  raise; community rule files parse cleanly.

### Sweep status

- 12.31 → CI red (3 stale assertions)
- 12.32 → CI red (third stale assertion)
- 12.33 → CI red (Python 3.9 fallback parser missing list-of-dicts)
- 12.34 → CI red (Python 3.9 fallback parser too permissive)
- 12.35 → CI red (Python 3.9 bracket check too aggressive — quoted strings)
- 12.36 → expected green

Each fix surfaces another edge case in the same `_parse_simple_yaml`
path — the cost of having a fallback parser that diverges from PyYAML
on shapes the test suite exercises. Long-term: the right move is to
either ship PyYAML as a hard dependency for the rules-engine subsystem
or to import a tiny vendored YAML parser. For now: targeted shape-by-
shape fixes, validated by CI matrix on Python 3.9.

### Also lands

- **Empty-rules edge case fix** carried from 12.35 dev — when input
  is `rules:\n` with no items, `_parse_simple_yaml` now returns
  `{"rules": None}` (matching PyYAML) instead of `{"rules": {}}` so
  the loader doesn't surface a spurious "must be a list, got dict"
  warning. Internal `_collapse_empty` walks the parse tree and
  replaces empty placeholder dicts with `None`.

## [12.35] - 2026-05-06

### Bugfix release — `_parse_simple_yaml` malformed-input + top-level-list (Python 3.9 CI red)

Fifth iteration on the same CI matrix. 12.34's list-of-dicts fix made
the fallback parser TOO permissive — `tests/test_pr_analyze_edge_cases.py::test_load_rules_yaml_handles_non_yaml_file`
expects malformed YAML to surface a warning, but the fallback parsed
`"this is not: valid: yaml: at all: ["` as a non-failing dict and no
warning was emitted. Same for `test_load_rules_yaml_top_level_not_dict`
where PyYAML returns a list and the loader warns "must be a mapping",
but the fallback returned `{}` silently.

### Bugfixes (`_parse_simple_yaml`)

- **Bracket-balance check** — unbalanced `[`/`]`/`{`/`}` on a single
  line now raises `ValueError`; the caller's `except Exception`
  wrapper surfaces it as a warning. Mirrors PyYAML's error behaviour.
- **Top-level-list detection** — when the first non-comment line
  starts with `- `, return a Python list (matching PyYAML). The
  downstream loader then triggers its existing
  `not isinstance(data, dict)` warning instead of silently treating
  the file as empty.

### Sweep done while waiting on CI

While CI 12.34 was running, swept all 25 `yaml.safe_load` call sites
across 7 files — every one already has an `except ImportError`
fallback. So the parser bug surfaced in just one place
(`cmd_pr_analyze._parse_rules_data`) but the fix lands in the shared
`roam.rules.engine._parse_simple_yaml` so all callers benefit.

## [12.34] - 2026-05-06

### Bugfix release — `_parse_simple_yaml` list-of-dicts (Python 3.9 CI red)

12.33 fixed three test files but missed a fourth red on Python 3.9:
`tests/test_pr_analyze.py::test_load_rules_yaml_simple`. The test's
fixture YAML is a list-of-dicts (`rules: [- id: ...]`) — the canonical
shape of `.roam/rules.yml`. Without PyYAML, the fallback parser at
`roam.rules.engine._parse_simple_yaml` only handled flat key-value
shapes and inline lists, so the result on 3.9 was an empty
single-dict and the assertion `len(rules) == 1` failed.

### Bugfix

- **`_parse_simple_yaml` now handles `key:\n  - dict-item` shape.**
  Frame stack tracks `parent_dict + parent_key` per push so that when
  a `- ` item arrives under an empty placeholder dict, the parser can
  promote that placeholder into a list at the recorded location and
  push a fresh dict for the item. Returns the same shape PyYAML would
  for the documented rules.yml format.
- 12.34 carries the v12.33 surface (54 detectors, 32 catalog tasks)
  plus the bugbear lint sweep — no functional changes beyond the
  parser fix.

### How this slipped through (running tally)

- 12.30: stale `_CORE_TOOLS` count in `test_defer_loading` (caught locally).
- 12.31: shipped without re-running the broader CI matrix.
- 12.32: stale `_CORE_TOOLS == 41` in `test_mcp_server.py` (two assertions).
  CI red on all 5 Python versions.
- 12.33: stale `tool_count == 41` in `test_inspect_core_preset` (third
  assertion in same file). CI red on all 5 Python versions.
- 12.34: list-of-dicts YAML fallback bug. CI red ONLY on Python 3.9
  (every other version has PyYAML pulled in by transitive deps).

The pattern: each fix covered the reported failure but didn't sweep
for siblings. Added a release-checklist note in 12.33 about the
triple-grep for `_CORE_TOOLS`. Adding now: also `grep "yaml.safe_load"
src/` to spot every fallback path that needs `_parse_simple_yaml`
coverage of advanced YAML shapes.

## [12.33] - 2026-05-06

### Bugfix release — third stale assertion + bugbear lint sweep

12.32 fixed two stale `_CORE_TOOLS == ...` assertions but missed a
third one in the same file. CI on 12.32 stayed red. Fixed here, plus
a bugbear-lint sweep (B033 duplicate set items, B023 closure-over-
loop-variable) that surfaced four real micro-bugs.

### Bugfixes

- **`tests/test_mcp_server.py::test_inspect_core_preset`** — hardcoded
  `assert result["tool_count"] == 41` updated to 49 (matches the
  v12.28 Agent Review v2 surface). Same drift class as the two we
  caught in 12.32; this was a third copy of the assertion in a
  different test class. Now the file has zero stale tool-count
  references.
- **B033 duplicate set items**:
  - `_FUNCTION_NODE_TYPES` in `roam.graph.clone_detect` — six
    languages share `function_definition`/`method_declaration`/
    `function_declaration` node-type names; the set contained each
    duplicate. Set semantics were unaffected (sets de-dup) but
    the apparent intent was a per-language map that's been wrong
    all along. Cleaned + comments document the sharing.
  - `_MODEL_PARENTS` in `cmd_n1` — bare `"Model"` listed twice
    (once for Laravel, once for Django). Collapsed.
  - 17 other smaller duplicates across `cmd_describe`, complexity
    indexer, foxpro extractor, retrieve seeds, tfidf search,
    auto-fixed by ruff.
- **B023 loop-variable closure**:
  - `cmd_orphan_routes._is_self_reference` — defined inside
    `for route in all_routes:` and closed over `controller_name`
    and `route_file_prefixes`. Fine in practice (called within
    same iteration) but a latent bug if anyone refactors. Bound
    as default args.
  - `resolve._score` — closes over `signals` from the enclosing
    `for table in (...):` loop. Same fix.
  - `hcl_lang._add` (closure inside `for ln, line in enumerate(...)`)
    — captures `ln` and `current_block`. Bound as default args.

### How this slipped through

The `_CORE_TOOLS` count appears in **three** assertions across two
test files. 12.30 updated one. 12.32 caught the second on CI red.
12.33 caught the third on CI red. Lesson: a `grep -rn "tool_count"`
sweep at every surface bump would have caught all three at once.
Added that as a release-checklist note in `dev/redacted`.

## [12.32] - 2026-05-06

### Bugfix release — CI green-bar restore + Z-phase polish

12.31 went out with two stale tests (drift from the redacted
core-tools list landed in 12.27/12.28) plus a Python-3.9 environment
gap. Both fixed here.

### Bugfixes

- **`tests/test_mcp_server.py::test_core_tools_set_has_expected_members`** —
  expected-set contained the v12.19 list but missed the eight v12.27/28
  Agent Review v2 tools (`roam_pr_analyze`, `roam_pr_comment_render`,
  `roam_rules_validate`, `roam_audit_trail_export`,
  `roam_audit_trail_verify`, `roam_audit_trail_conformance_check`,
  `roam_dogfood`, `roam_metrics_push`). Brought in line.
- **`tests/test_mcp_server.py::test_core_tools_count`** — bumped
  `assert len(_CORE_TOOLS) == 41` to `== 49`.
- **`tests/test_finding_suppress.py::test_annotate_ignore_findings_glob`**
  fails on Python 3.9 because PyYAML is not a project dependency and
  `_load_ignore_findings_file` only had a JSON fallback (the test
  fixture is YAML). Added `_parse_simple_ignore_findings_yaml` —
  a 30-line minimal parser for the documented `rules: [...]` shape
  so `.roamignore-findings` works without PyYAML installed.

### New detectors (3)

- **`useeffect-missing-deps`** (Z1, JS/TS) — React `useEffect(() => {})`
  without dependency array runs on every render. Conservative: only
  fires when no useEffect-with-deps appears in the same body.
- **`dangerous-eval`** (Z2, language-agnostic) — `eval` / `exec` /
  `new Function()` / `setTimeout(string)` in production source.
  Suppresses test / migration / script paths and `ast.literal_eval`.
- **`unremoved-event-listener`** (Z5, JS/TS) — `addEventListener` in
  a component lifecycle (useEffect / componentDidMount / etc.) without
  paired `removeEventListener` or useEffect cleanup function.

### Smarter outputs

- **`roam math --task TYPO`** (Z7) now suggests close matches via
  edit distance instead of running 54 detectors silently.
- **`roam math` zero-state verdict** (Z3) is now informative:
  detector count, profile note, two suggested next commands.
- **`roam math --json summary.top_tasks_by_count`** (Z13) — compact
  ranked list of task_id + count for dashboards/agents.
- **`roam debt` verdict** (Z14) appends top-1 hotspot path inline
  so the one-liner tells you WHERE to look first.
- **`pr-comment-render`** (Z4) wraps long rule-violation lists
  (≥12) in a collapsible `<details>` block so the comment doesn't
  dominate the PR thread on noisy diffs.

### Catalog

- 32 catalog tasks total (was 29). 54 detectors registered (was 51).
- All Z-phase tasks added with rank-1 fix tip and one rank-10
  detected-way for downstream catalog consumers.

## [12.31] - 2026-05-06

### Major release — 90-phase polish + smarter pass

This release lands a multi-session polish run touching almost every
detector and command in the codebase. Headline gains: **2.7× faster
`roam math`** (5.5s → 2.07s on roam-code itself), **3.2× faster
`roam --help`** (1.24s → 0.39s warm), **2.3× faster `roam health`**
(3.3s → 1.45s warm), **+6 new algorithm detectors**, **+3 framework
profiles**, **69% of findings now carry structured `matched_patterns`
explainability blocks**, and a **40-entry regression-FP corpus** so
the wins can't quietly come back.

### New detectors (6)

- **`async-blocking-sleep`** — Python `time.sleep()` / `requests.*` /
  `subprocess.run()` inside `async def`. Blocks the event loop;
  fix is `await asyncio.sleep` / `httpx.AsyncClient` / asyncpg.
- **`broad-except-swallow`** — Python `except Exception:` without a
  re-raise. Catches `KeyboardInterrupt`, `MemoryError`, `SystemExit`
  silently. Suppressed for functions named `safe_*` / `_try_*` /
  `with_default_*` / `silent_*` (recovery wrappers are intentional).
- **`spread-accumulator`** — JS/TS `acc = [...acc, x]` and
  `.reduce((a, x) => [...a, x])` patterns are O(n²); fix is `.push()`.
- **`defer-in-loop`** — Go `defer` inside `for`/`range` accumulates
  deferred calls until the FUNCTION returns, not per iteration.
  Common fd-exhaustion bug.
- **`chained-collection-walk`** — JS/TS `.filter().find()` /
  `.map().find()` / `.filter().length` are 2-pass when 1-pass
  equivalents (`.find(x => predA(x) && predB(x))`, `.some()`) exist.
- **`serial-await-loop`** — JS/TS `for (... of ...) { await fn(x) }`
  serial pattern. Each iteration waits for the previous; fix is
  `await Promise.all(items.map(fn))`. Caught a real case in roam's
  own `.github/scripts/pr-comment.js`.

### New framework profiles (3) + auto-detection

`roam math --framework FRAMEWORK` now bundles five profiles. New ones:

- **`django`** — recognises `queryset.iterator/values_list/values/
  annotate/prefetch_related/select_related/only/defer/exists/count`
  as not-IO when the receiver is `queryset`/`qs`/`manager`/`objects`/`cache`.
- **`rails`** — ActiveRecord `includes/joins/preload/eager_load/pluck/
  find_each/in_batches/scope` plus `Rails.cache.*`.
- **`nestjs`** — TypeORM `createQueryBuilder/leftJoinAndSelect/etc.`,
  `CacheManager.*`, `ConfigService.get`.

Auto-detection (`autodetect_framework_profile`) sniffs:
`requirements.txt` / `pyproject.toml` for django, `Gemfile` for rails,
`package.json @nestjs/core` for nestjs (alongside the existing vue3 +
laravel cases). The `(auto)` tag in the verdict line surfaces when a
profile was auto-selected so it isn't invisible.

### Performance

- **`_FILE_LINES_CACHE` + `_IN_MEMORY_CALL_CACHE` + `_FRAMEWORK_PACK_CACHE`**
  in detectors.py. Combined: 4989 file reads + 12,226 cache classifier
  calls per `roam math` run collapsed to one read per (path, mtime)
  and one classification per (call, framework_id). Cache reset at
  `run_detectors` entry so test isolation holds. **5.5s → 2.07s.**
- **`_short_help_via_ast` disk cache** in cli.py keyed by file mtime.
  126 cmd_*.py AST parses per `roam --help` collapse to one cache
  read. **1.24s → 0.39s warm.**
- **`algebraic_connectivity` disk cache** in `graph/cycles.py` keyed
  by graph fingerprint (node+edge count + sorted edge sample). The
  spectral solve dominates `roam health`; warm runs skip it entirely.
  **3.3s → 1.45s.**
- **CLI plugin discovery** short-circuits when the requested command
  is in the built-in `_COMMANDS` map — saves the 100ms `entry_points()`
  scan for the 99% case of users with no third-party plugins. The
  `_entry_points_for_group()` lookup is also process-cached.

### Smarter outputs / explainability

- **`evidence.matched_patterns`** on every detector finding (math,
  over-fetch, auth-gaps). Lists the named sub-patterns that fired
  (e.g. `["high-confidence I/O leaves (3)", "framework pack: django",
  "DEV-only gate (confidence demoted)"]`). 69% of findings carry
  this block now (was 0%).
- **`roam math` text output** surfaces `matched_patterns` on a
  one-line `Matched: ...` row per finding.
- **`pr-comment-render`** renders `matched_patterns` as `_matched: ..._`
  italic line under each concern in the markdown surface; plain
  renderer mirrors it. `pr-analyze`'s critique concern now also
  attaches top-3 finding pointers (check + title) as
  `matched_patterns`, replacing the opaque "see `pr_prep.critique`"
  evidence line.
- **`roam math` VERDICT** appends "; mostly: io-in-loop" hint when
  >=5 findings cluster on one detector.
- **`roam math --since BASELINE.json`** flag — show only NEW findings
  vs a baseline snapshot. Workflow: `roam --json math > .roam/baseline.json`
  then `roam math --since .roam/baseline.json` shows only regressions.
- **`roam math --include-tests`** flag — opt-in scan of test files
  (default still excludes them).
- **`roam math --json` summary** carries `framework`,
  `framework_autodetected`, `framework_unknown` for CI/dashboard
  consumers.
- **`roam over-fetch`** — when `fillable_count >= 50` and no API
  Resource exists, the suggestion now leads with a concrete artefact
  scaffold (`app/Http/Resources/<Model>Resource.php` skeleton + the
  `Resource::collection(Model::query()->paginate())` controller call).
- **`roam auth-gaps`** — top-by-controller rollup when ≥10 findings
  cluster on a few controllers. Triage is radically faster on the
  ~115-finding redacted case.
- **`roam debt --json`** — every result carries a `roi_band` field
  (high/medium/low) using percentile-adaptive cutoffs (top 10% / next
  25% / rest). CI dashboards can filter on band without re-deriving.
- **`roam diff` text** — top-3 affected symbols by PageRank surfaced
  inline. Tells reviewers "central abstraction" vs "leaf module" at
  a glance.
- **`roam health`** — verdict says "all flagged as utility / non-actionable"
  when `actionable_count == 0` and critical issues exist (was misleading).
- **SARIF output** includes `matched_patterns` as a SARIF `properties`
  field for GitHub Code Scanning.

### Smarter classification (FP fixes)

- **`_BATCH_ITERATION_PATTERNS`** in detectors.py recognises chunked
  iteration (`for chunk in _chunked(ids):`, `for batch in _batched()`,
  `WHERE IN ({ph})` interpolation) and skips N+1 flagging on those
  bodies. Caught roam's own `_symbol_context` self-FP.
- **`busy-wait` detector** — sleeps ≥ 1 second are operator-paced
  polling, not busy-wait. Function-name suppression list expanded
  with `_loop`, `watch_*`, `watcher`. Eliminated `_run_watch_loop` FP.
- **Walker fix in `complexity._extract_math_signals`** — nested
  function bodies (arrow-function default params, callbacks, lambdas)
  now reset `loop_depth` to 0 at the boundary. Eliminated FPs where
  arrow defaults like `(item) => item.name` were flagged as I/O in loop.

### Cleaner

- **9 unused private helpers removed** across 8 files
  (`_is_query_source_path`, `_row_signals`, `_to_test_function_name`,
  `_read_body_lines`, `_files_for_commit`, `_find_callers`,
  `_infer_ts_model_name`, `_search_with_git_grep_regex`,
  `_decision_entries`, `_parse_table_after_any_heading`).
- Pre-compiled depth-guard / memo-collection / batch-iteration regexes
  in detectors.py (no per-call recompile, hits cache hot).
- `cmd_pr_analyze.py` split — three pure-helper modules extracted
  to `roam.commands.pr_analyze.*`: `cache.py` / `audit_trail.py` /
  `rules.py`. All imports re-exported for back-compat. Coordinator
  shrunk from 2340 → 2098 lines.

### Tests + corpus

- **40-entry regression-FP corpus** under `tests/regression_fp_fixtures/`.
  JSON-based; one entry per FP pattern; harness at
  `tests/test_regression_fp_corpus.py` parametrises one test per
  entry. Adding a new fixture is a one-file edit (no Python).
- **9 new corpus helpers** mapping onto detector internals:
  `in_memory_call`, `depth_guard_regex`, `dev_only_block`, `call_awaited`,
  `extract_arg_after`, `try_catch_idempotency`, `ancestor_constructor_auth`,
  `body_shaping`, `batch_iteration`.
- **+15 unit tests** for the new detectors (`detect_async_blocking_sleep`,
  `detect_broad_except_swallow`, `detect_serial_await_loop`,
  `_has_batch_iteration`).
- **2 N+1 self-bugs fixed** while dogfooding: `_evaluate_gate_rules`
  in `cmd_coverage_gaps.py` (one query per test file → batched
  `WHERE IN`); `_symbol_context` correctly recognised as batch.

### Schema

- **Envelope `schema_version` 1.0.0 → 1.1.0** signals additive
  enhancements: `matched_patterns`, `framework`/`framework_autodetected`/
  `framework_unknown`, `roi_band`, `context_lines`. Pre-1.1 consumers
  continue to work; new consumers can opt in to the richer fields.

## [12.30] - 2026-05-06

### Detector quality round 3 — redacted v12.28 audit follow-ups (E1-E5)

A second dogfood pass of `roam math` / `weather` / `auth-gaps` /
`migration-safety` / `over-fetch` against the redacted Vue 3 + Laravel
multi-tenant codebase surfaced five fresh false-positive classes that
the 12.28/12.29 rounds didn't catch. All five are fixed here, each with
regression-corpus fixtures so they can't quietly come back. Web search
confirmed the patterns we're recognising are the canonical Laravel +
TypeScript idioms (parent-controller `$this->middleware('auth')` is the
pre-Laravel-11 base-class auth pattern; PostgreSQL SQLSTATE `42P07` /
MySQL `1050` are the standard "table already exists" idempotency codes
in stancl/tenancy multi-tenant migrations).

#### `roam weather` / hotspot ranking (E1)

- **Skip non-source files in churn x complexity ranking.** Legacy text
  dumps (`docs/legacy/reports/extracted/*.txt` from FoxPro extraction),
  build/generated artefacts, and `data` / `docs` files were ranking
  highest in `roam weather` simply because they had high churn. Now the
  shared `TOP_CHURN_FILES` query filters on
  `COALESCE(file_role, 'source') = 'source'` — same filter
  `cmd_hotspots.py` already applied for security hotspots. Source files
  with no role classification are still kept (the COALESCE preserves
  the conservative default).

#### `roam math` / I/O-in-loop walker (E3)

- **Nested function bodies establish a fresh loop scope.** A walker bug
  in `complexity._extract_math_signals` recursed through arrow-function
  default parameters (and any nested function/lambda/closure) while
  inheriting the enclosing function's `loop_depth`. So a default arg
  like `(item: T) => item.name || item.id` had its property access
  recorded as "I/O in loop" whenever the outer function contained a loop
  elsewhere. The `_walk` recursion now resets depth to 0 and clears
  `loop_vars` at every nested-function boundary (matching how
  `_walk_complexity` already handles callback depth).

#### `roam auth-gaps` / base-class inheritance (E2)

- **Walk the `extends` chain when looking for `$this->middleware('auth')`.**
  The detector previously regex-scanned only the IMMEDIATE controller
  class. Every `EmployeeController extends DynamicResourceController`
  pattern (where the parent class wires `$this->middleware('auth')` once
  in its constructor) was generating ~115 false positives on redacted.
  New `_build_class_source_map` indexes every controller-file class once
  per `auth-gaps` invocation; `_ancestor_has_constructor_auth` walks up
  to 3 ancestors looking for the auth-middleware registration.

#### `roam migration-safety` / Schema::create messaging (E4)

- **Anchor table-name extraction after the `create(` token.** The chained
  form `Schema::connection('payroll')->create('payroll_entity_report_presets', ...)`
  was parsed correctly by `_RE_SCHEMA_CREATE` but the table-name
  extractor grabbed the FIRST quoted string (`'payroll'` — the
  connection name), so the warning message said the wrong table.
  `_extract_arg(line, after_token="create(")` now starts the search
  after the `create(` literal. Same fix applied to `Schema::drop` /
  `Schema::dropIfExists` chains.
- **Recognise try/catch idempotency idioms.** Multi-tenant migrations
  often wrap `Schema::create(...)` in `try { ... } catch { if
  ($e->getMessage() contains 'already exists') ... }` instead of
  `if (!Schema::hasTable(...))`. The new `_has_try_catch_idempotency`
  helper recognises the `'already exists'` branch plus PostgreSQL
  SQLSTATE `42P07` and MySQL error code `1050`.

#### `roam over-fetch` / config-shaping wrappers (E5)

- **Body-level shape signals demote raw-return findings.** Controllers
  whose `index()` looks like
  `return $this->inheritModelFields(Employee::query()->paginate());`
  (or `paginate()->through(fn $x => …)`, `makeHidden`, `makeVisible`,
  `only`, `except`, DTO assembly via `\w+Dto::fromXxx`, `parent::index()`
  delegation) are now treated as shape-protected: the bytes that hit
  the wire are filtered, so the raw-fields warning would just create
  noise. New `_BODY_SHAPING_PATTERNS` list captures all 11 idioms.

#### Tests

- 14 new entries in `tests/regression_fp_fixtures/second_repo_2026_05_06_round2.json`
  drive 4 new corpus helpers (`extract_arg_after`, `try_catch_idempotency`,
  `ancestor_constructor_auth`, `body_shaping`). 34 corpus entries total
  now form the regression tripwire net.

### Deferred

- **E6 (multi-tenant per-schema index detection)** — the user's audit
  marked this "no action" on their side; without seeing the actual
  per-office migration files we can't tell whether the indexes live in
  a non-standard migration path, in raw `CREATE INDEX` SQL, or are
  applied via an artisan command outside the migration corpus. Will
  revisit once we have a concrete failing fixture.

## [12.29] - 2026-05-06

### Detector quality round 2 — deferred items D1-D7

The 12.28 round shipped 14 FP fixes plus a suppression mechanism. Customer
feedback flagged seven gaps the rushed round didn't cover; this release
ships them as a coherent batch.

#### Math / IO / N+1 detector

- **D1 — 5-line context snippet on every finding.** Each detector finding
  now carries `evidence.context_lines` (5 lines centred on the matched
  AST node), so reviewers see the surrounding code without an extra git
  fetch. Wired through `over-fetch`, `missing-index`, `auth-gaps` too.
- **D2 — `await` heuristic refines cache-vs-IO.** Without full type
  resolution, the cheap proxy is "did the call get awaited?". When a
  cache-allowlisted name (`getQueryData`, `cache.read`) appears with a
  preceding `await` in the snippet, escalate to medium I/O instead of
  silencing as cache. Catches "I overloaded a cache name with a real
  fetch" without forcing project annotations.
- **D3 — `--framework FRAMEWORK` flag (math).** Bundled profiles
  `vue3-tanstack` and `laravel-multitenant` layer extra cache
  allowlists on top of the safe defaults. `roam math --list-frameworks`
  enumerates available profiles. Unknown names tolerated (defaults
  apply, surfaced in `meta.framework_unknown`).

#### Tests / regression discipline

- **D4 — Regression-FP fixture corpus.** New `tests/regression_fp_fixtures/`
  directory holds JSON fixtures keyed by detector helper. Adding a new
  fixture is a one-file edit (no Python). Currently covers 19 patterns
  drawn from the 2026-05-06 redacted FP batch — each is a tripwire
  that fails by name if the fix regresses.

#### PR comment renderer

- **D6 — 5-line context surfaced in markdown.** `pr-comment-render`
  now renders any concern or rule-violation that carries a
  `context_lines` block as a fenced code snippet. Plain renderer shows
  it indented. Each `_check_rules` violation now carries a 5-line
  window from the diff so the GitHub App comment shows reviewers the
  matched line in context.

#### Suppression workflow

- **D7 — `roam suppress --from-finding PATH_OR_-`.** Batch ingest from
  a `roam --json math` envelope (or stdin). Adds `--filter key=value`
  for narrowing intake by `task_id`/etc., and `--dry-run` to preview
  without writing. Findings without a `finding_id` are skipped and
  surfaced in JSON output for over-suppression auditing.

#### Refactor

- **D5 — `cmd_pr_analyze.py` split.** Three pure-helper modules
  extracted into `roam.commands.pr_analyze.*`:
  `cache.py` (envelope cache), `audit_trail.py` (Article 12 JSONL
  emit), `rules.py` (pattern matchers + diff parser). All previous
  imports remain valid via re-exports — no test or external caller
  needs to change. cmd_pr_analyze.py shrunk from 2340 → 2098 lines.

## [12.28] - 2026-05-06

### Detector quality round (M1-M14) — false-positive fixes

User feedback after running `roam math` / `over-fetch` / `missing-index` /
`auth-gaps` on a multi-tenant Laravel + Vue 3 codebase surfaced systematic
FP patterns. This release ships fixes for all of them.

#### Math (`roam math` / `algo`)

- **M1 — findings now point at the exact AST node, not the enclosing
  function declaration.** User: "highest single-leverage fix on its own —
  cuts triage time in half." Sort/IO/regex-in-loop detectors all walk the
  snippet to find the actual match line.
- **M2 — bounded-recursion FP killed.** Depth-guard regex now recognises
  the `if (depth > limit) return` early-return form (was only matching
  `if (depth < limit)` continue form). Plus new `Set/Map/WeakSet`
  parameter detection — functions that carry their own memoisation
  collection no longer flagged as O(2^n). Real-world FP eliminated:
  `deepEqual` flagged on redacted with `if (depth > 10) return false`
  on the next line.
- **M3 — cache-vs-IO distinction expanded.** In-memory call allowlist
  now covers Apollo (`client.readQuery`, `cache.modify`), SWR (`mutate`),
  TanStack Query lifecycle methods (`invalidateQueries`, `removeQueries`,
  `cancelQueries`), and native collection ops (`Map.has`, `Set.delete`,
  `WeakMap.set`) when the receiver hint matches. Real-world FP
  eliminated: `queryClient.getQueryData` inside a TanStack factory no
  longer flagged as N+1 round trips.
- **M4 — DEV-only block recognition.** Detectors recognise
  `if (import.meta.env.DEV)`, `if (process.env.NODE_ENV !== 'production')`,
  `if (__DEV__)`, `if (DEBUG)`, `console.assert(...)`. IO-in-loop
  findings inside DEV gates demoted two confidence tiers (production-
  stripped code shouldn't gate releases).
- **M5 — sort-then-subscript with full iteration → demoted.** When the
  sort result is also iterated/returned (display-order pattern), drop
  confidence from "high" to "medium" (or "medium" to "low") and add a
  note explaining the subscript may be incidental.
- **M6 — every finding now carries a `to_suppress` evidence block.** No
  more reverse-engineering the heuristic: each emitted finding tells
  you exactly what would have made it not fire.
- **M8 — confidence calibration floor.** Categories where the FP-fix is
  heuristic-only (`branching-recursion`, `sort-to-select`) cap at
  "medium" unless there's strong runtime signal. Real-world calibration
  on redacted showed "high confidence" for these was 0/1 true positive.

#### Missing-index (`roam missing-index`)

- **M9 — `$table` property is now the source of truth, not class-name
  derivation.** New cross-file `_build_model_table_overrides` pass
  walks every model file and indexes `protected $table = '...'`
  declarations. `_class_to_table` consults this BEFORE applying snake_
  case-plural derivation. Real-world FP eliminated: 6/6 high-confidence
  findings on `advances` / `payments` / `reminders` (which were actually
  `payroll_advances` / etc. via `$table` override).
- **M13 — multi-tenant per-schema migration pattern recognised.**
  `Schema::connection('payroll')->create("{$schema}.payroll_advances",
  ...)` is now matched by the table regex (was only matching
  `Schema::create(...)` directly). Plus a normalise-prefix helper
  strips `{$schema}.` / `$schema.` from captured table names so the
  index map keys on the bare table name.

#### Auth-gaps (`roam auth-gaps`)

- **M10 — non-auth route guards (throttle / signed / verified / can /
  scope) are recognised as intentional.** Routes with `->middleware
  ('throttle:60,1')` etc. but no `auth:*` are now flagged at "low"
  confidence with the explanation "looks like an intentional public-
  but-protected endpoint" instead of "missing auth".
- **M11 — tenant-scoped controllers recognised as authorization-
  equivalent.** When a controller method scopes its query to the
  current tenant (`officeScoped()`, `multiTenant()`, `Resource::for()`,
  `forTenant()`, `forUser()`, `belongsToCurrentUser()`, `currentTeam()`),
  the route auth + tenant scope counts as the authorization layer.
  CRUD methods downgraded from "medium" to "low"; read methods skipped
  entirely. Real-world FP eliminated: ~115 controller methods on
  redacted flagged for missing `$this->authorize()` despite being
  protected by route Sanctum + officeScoped queries.

#### Over-fetch (`roam over-fetch`)

- **M12 — direct-return scan is now method-body-scoped, not file-
  level.** Previously: any controller importing `LedgerAccount` could
  get flagged for over-fetching it just because `return $aadeService
  ->getDocs()` matched the generic `return $var;` pattern. Now: only
  flag direct returns when the model is *actually used* in the same
  method body (`Model::find/all/...`, `new Model(`, etc.).

### Suppression mechanism (M7)

Three layered paths for marking a finding as a known FP:

- **Inline annotation** — `# roam: ignore-math[branching-recursion]`
  on the symbol line (or sym-line) suppresses just that one finding.
  Bare `# roam: ignore-math` (no `[task-id]`) suppresses every math
  task on that line. `[*]` covers all task-ids. Supported across
  `math`, `over-fetch`, `missing-index`, `auth-gaps`.
- **`.roamignore-findings`** — repo-level YAML/JSON file with `rules:`
  blocks matching by `task_id` + `path_glob`. Use for project-wide
  carve-outs.
- **`.roam/suppressions.json` via `roam suppress` command** — record a
  one-off audit-trail-friendly suppression with reason. Each finding
  now carries a deterministic `finding_id` (sha256 of task_id +
  location + symbol_name, 16 chars) so the suppression survives reindex.

Suppressed findings stay in the JSON envelope under
`finding["suppressed"] = {source, reason}` instead of being silently
dropped — consumers can detect over-suppression. Text output filters
them by default. Verdict line (M14) now reflects "N unsuppressed
candidates surfaced; M suppressed via …" when any suppression fires.

### Added — new command

- **`roam suppress <finding-id> --reason "…"`** — companion to the
  inline / file paths above. `--list` to view all, `--remove` to drop
  one. JSON envelope mode for scripts.

### Surface counts

- CLI commands: 186 → **187** (+1: `suppress`)
- MCP tools: **136** (unchanged)
- Core MCP preset: **49** (unchanged)

### Tests

- 198 detector tests pass (+72 new across `test_math_fp_fixes.py` (22),
  `test_finding_suppress.py` (20), `test_laravel_fp_fixes.py` (16),
  plus existing math (83) / missing-index (33) / auth-gaps (19) /
  over-fetch (5) regression files).
- 405 v2 + adjacent tests still pass; no behavioural regressions.

## [12.27] - 2026-05-06

### Added — round-5 polish + dogfood

15 small-to-medium improvements driven by the round-5 task capture.
No new top-level commands; all flags + helpers + content additions.

- **`roam pr-analyze --diff-from-pr URL`** — fetch a GitHub PR diff via
  `gh pr diff` (delegates auth to gh CLI). Lets you analyse a PR
  without cloning. Smoke-tested against fastapi#15482.
- **`roam pr-analyze --watch SECONDS`** — poll the diff source every N
  seconds; re-run when it changes. Local dogfood mode for refactor
  sessions. Ctrl-C exits cleanly.
- **`roam pr-analyze --batch --stream-jsonl`** — emit each per-file row
  as a JSONL line as soon as it completes. Long batches feel responsive;
  closing line carries the summary so consumers detect end-of-stream.
- **`roam pr-analyze --audit-trail` auto-runs conformance check** — the
  Article 12 score is now attached to the envelope (under
  `audit_trail.conformance`) on every audit-trail emission. Surfaced
  in text output as `conformance: NN/100`. Advisory; never blocks.
- **`roam audit-trail-export --top-actors N`** — procurement-friendly
  hot list ranking actors by BLOCK count first, total count as tiebreaker.
  Markdown / CSV / JSON variants.
- **`roam rules-validate --fix`** — auto-coerce safe schema mistakes
  (severity casing → uppercase; trim whitespace on glob fields). Skips
  real typos so they're still flagged. Writes back to the file.
- **`roam metrics-push` last-pr block now includes conformance score** —
  when `--include-pr-analysis` is set AND the saved baseline carries an
  `audit_trail.conformance` block, fold it into the payload. Cloud Lite
  Growth-tier dashboards can show compliance posture alongside trends
  without a separate API call.
- **Kotlin starter rule pack** at `templates/rules/kotlin/.roam-rules.yml`
  (12 rules: no-runBlocking-in-suspend, no-GlobalScope, no-System.exit-
  in-libs, no-Runtime.exec, hallucinated-import detection, layer
  violations, deprecated-annotation).
- **Rust starter rule pack** at `templates/rules/rust/.roam-rules.yml`
  (12 rules: no-unwrap-in-prod, no-mem::transmute, no-process::exit,
  no-eprintln-in-prod, hallucinated-crate-import, layer violations).

### Fixed

- **`_compute_drift` per-rule breakdown** — drift output now distinguishes
  "rule fired this PR for the first time" from "existing rule's
  violation count changed". The PR comment surfaces both as separate
  sentences instead of a generic delta.

### Internal

- Cognitive complexity reductions (continued from 12.26.1):
  - `_build_payload` cc=49 → split into 3 helpers (already in 12.26.1)
  - `_load_rules_yaml` cc=71 → extracted `_warn_or_raise` +
    `_parse_rules_data` + `_coerce_rule` (already in 12.26.1)
  - `_emit_batch` cc=48 → split into `_run_batch_serial` +
    `_run_batch_parallel`
  - `_build_rationale` cc=39 → split into per-concern collectors +
    `_compose_next_steps` + `_extract_suggested_reviewers`
- New `tests/test_pr_analyze_helpers.py` — 17 unit tests for the
  small helpers extracted across rounds 3-5: `_serve_from_cache`,
  `_apply_drift`, `_emit_audit_trail`, `_run_batch_serial`,
  `_run_batch_parallel`, `_process_single_diff`,
  `_run_conformance_check_inline`, `_compute_drift` per-rule breakdown.
- **PyPI Trusted Publishing workflow hardened**: triggers on tag push
  (`v*`) AND release creation, includes `skip-existing: true` for
  idempotency, fails build if wheel doesn't contain ≥8 v2 command files.
  Manual twine uploads no longer collide with the workflow.
- README v2 quickstart subsection — `git diff | roam pr-analyze` and
  `roam dogfood` now appear in the main Quick Start section, not just
  the dedicated Roam Agent Review section.
- Templates index — README now points users at `templates/rules/` (6
  starter packs) and `templates/audit-report/` for customer-facing
  artifacts.

### Surface counts

- CLI commands: **186** (unchanged)
- MCP tools: **136** (unchanged)
- Core MCP preset: **49** (unchanged)
- Rule packs shipped: 4 → **6** (+Kotlin, Rust)

### Tests

- 405 v2 + adjacent tests pass (was 386 in 12.26.1). +19 new tests
  across 4 files.

## [12.26.1] - 2026-05-06

### Added

- **`audit-trail-conformance-check --sarif`** — emit SARIF 2.1.0
  envelope with failed checks as findings (drops into GitHub Code
  Scanning UI). Pair with global `--sarif` flag (consistent with
  `roam health --sarif`, `roam dead --sarif`, etc.).
- **`templates/rules/go/.roam-rules.yml`** — 12-rule starter pack for Go
  (no-unsafe, no-cgo, no-md5/sha1, no-panic, no-init-funcs,
  hallucinated-import detection, layer violations).
- **`templates/rules/java/.roam-rules.yml`** — 12-rule starter pack for
  Java (no-Runtime.exec, no-System.exit-in-libs, no-ObjectInputStream,
  no-printStackTrace, no-raw-types, no-Thread.stop, hallucinated-import
  detection, controller-from-jdbc layer violation).
- **`.github/workflows/dogfood.yml`** — self-CI workflow for roam-code
  itself: runs `roam dogfood` on every PR + push, posts a sticky
  PR comment via `roam pr-comment-render`, uploads the audit trail as
  a workflow artifact. Eat our own cooking publicly.

### Fixed

- **`_save_baseline` now stamps `_meta.timestamp`** at save time. Without
  this, `pr-comment-render --from-baseline` couldn't compute baseline
  age (the "saved X days ago" line silently never fired). Caught by
  the new end-to-end integration test.

### Internal

- Cognitive complexity reductions (continued from 12.26):
  - `_build_payload` cc=49 → split into `_extract_metrics` +
    `_extract_hotspots` + `_build_last_pr_block` helpers
  - `pr_analyze` (the command coordinator) cc=38 → extracted
    `_serve_from_cache` + `_apply_drift` + `_emit_audit_trail`
- New `tests/test_v2_integration.py` — 3 end-to-end tests exercise the
  whole v2 pipeline (audit → pr-analyze → audit-trail-verify →
  audit-trail-export → audit-trail-conformance-check → pr-comment-render
  → metrics-push → dogfood). Catches schema drift across the chain.
- Cache stress-test on a 30-real-commit batch: **54.7× warm-cache
  speedup** (60s cold → 1.1s warm) sequentially; **2.55× cold speedup**
  with `--parallel 4`.
- Real-OSS validation: ran pr-analyze on 3 small human-written PRs
  (fastapi#15482, requests#7401, httpx#3773); all SAFE with
  AI-likelihood 13-23. Confirms scorer doesn't false-positive on
  legitimate human work.

## [12.26] - 2026-05-06

### Added — Roam Agent Review + Cloud Lite engines (redacted)

8 new commands ship the Roam Agent Review and Roam Cloud Lite product
engines plus the EU AI Act Article 12 audit-trail toolkit.

- **`roam pr-analyze`** — agent-aware PR risk verdict (INTENTIONAL / SAFE /
  REVIEW / BLOCK). Aggregates `pr-prep` (diff + critique + pr-risk) with
  **9-signal AI-likelihood scoring**: add/remove ratio, comment density,
  test coverage, function-size variance, generic naming, orphan imports,
  **placeholder density** (TODO/FIXME/NotImplementedError stubs),
  **LLM-phrase density** ("we use this approach because…"),
  **suspicious imports** (numbered modules / mass typing imports /
  helper.helper). Language-aware weights for 7 languages.
  `.roam/rules.yml` enforcement (4 pattern types: `import_from`,
  `function_call`, `class_inherit`, `decorator_use`).
  Reviewer suggestions (`--with-reviewers`), drift detection vs a saved
  baseline with auto-escalation, CI gate (`--gate` exits 5 on BLOCK).
  Flags: `--explain`, `--quiet`, `--rules-strict`, `--audit-trail`,
  `--save-baseline`, `--baseline`, `--batch DIR`, `--parallel N`,
  `--progress`, `--cache`, `--cache-dir`. The CLI engine behind Roam
  Agent Review.
- **`roam pr-comment-render`** — render a markdown PR comment from a
  `pr-analyze --json` envelope. GitHub / GitLab / plain styles. Before-after
  drift rendering (`(45 → 50, +5)`), regression / improvement banners,
  reviewer block, plain-English signal explanations, previous-verdict link
  on drift, baseline-age banner on `--from-baseline`.
- **`roam metrics-push`** — push metrics-only summary (no source code) from
  `roam audit` to a Roam Cloud Lite endpoint. Allow-listed payload schema
  (`roam-metrics-v1`), SHA-256 path-hashing under `--anonymize`, `--dry-run`
  default-safe inspection, `--timeout SECONDS` for slow networks.
  `--include-pr-analysis` folds `.roam/last-pr-analysis.json` summary
  (verdict, blast, ai, primary language) plus computed `age_days` + `stale`
  fields into the payload. The CLI engine behind Roam Cloud Lite.
- **`roam audit-trail-verify`** — verify SHA-256 chain integrity of an EU AI
  Act Article 12 audit trail. Detects tampered records by line number;
  `--gate` exits 5 on broken chain.
- **`roam audit-trail-export`** — export the audit trail as markdown / JSON /
  CSV with `--since`, `--until`, `--verdict` filters. `--aggregate` emits
  procurement-ready summary tables bucketed by actor / repo / verdict /
  month, plus a top-snapshot block (`top_actor`, `top_repo`, `top_month`,
  `top_verdict`). `--finalize` appends a closing `AuditIntegritySummary`
  record (chain head + event count + algorithm, per the canonical
  forensic-format pattern).
- **`roam audit-trail-conformance-check`** — score the audit trail against
  an EU AI Act Article 12 6-check checklist: chain integrity, timestamp
  completeness, actor attribution, reproducibility metadata, verdict +
  rationale present, retention (≥ `--retention-days`, default 180).
  `--gate` exits 5 on score < 100.
- **`roam rules-validate`** — lint a `.roam/rules.yml` for typos, schema
  mistakes, unknown patterns, duplicate rule IDs, unbalanced glob brackets.
  `--against DIFF` dry-runs the rules. `--strict` treats warnings as
  failures. `--gate` exits 5 on errors. `--explain` prints a pattern
  reference with matchers + glob examples + use cases.
- **`roam dogfood`** — one-shot v2 stack runner: `audit` + `pr-analyze`
  (uncommitted) + audit-trail emission + `audit-trail-conformance-check`
  in a single envelope. The "show me everything" first-touch demo.

### Added — pr-analyze hardening

- **Audit-trail safety**: `pr-analyze --audit-trail` now pre-verifies the
  existing chain BEFORE appending. A broken chain auto-escalates the
  verdict to BLOCK + appends a reason + fires `--gate`. Prevents compound
  corruption of compliance records.
- **`--rules-strict`** — the rules loader now returns `(rules, warnings)`.
  Default tolerant mode surfaces warnings (missing file, malformed YAML,
  type-coerced fields) into the envelope under `rules_warnings`. `--rules-strict`
  raises `ValueError` and fires `--gate` on any malformed input.
- **Type-coerced rule loading**: `severity: 42` (number, not string) and
  `forbidden_target_glob: <non-string>` are now caught with structured
  warnings instead of silently miscomparing later.
- **Sequence numbers**: every `pr-analyze --audit-trail` record carries a
  monotonic `sequence_number`. Gaps signal partial-write corruption that
  hash chains alone can't detect.
- **Cache** (`--cache` + `--cache-dir`): pr-analyze envelopes are keyed by
  `sha256(diff + rules + threshold + language + version)`. Repeats short-
  circuit before pr-prep runs. Dogfooded on a 5-real-commit batch:
  **24.5× speedup** (12.2s cold → 0.5s warm). Cache works through batch
  mode; hit-rate surfaced in batch summary.
- **Batch parallelism**: `pr-analyze --batch DIR --parallel N` runs files
  through a `ProcessPoolExecutor`. `--progress` emits per-file stderr
  lines so long batches don't feel hung. Oversubscription warning fires
  when N > cpu_count.

### Added — MCP tool surface

- 8 new MCP tools registered in `mcp_server.py`:
  `roam_pr_analyze`, `roam_pr_comment_render`, `roam_metrics_push`,
  `roam_audit_trail_verify`, `roam_audit_trail_export`,
  `roam_audit_trail_conformance_check`, `roam_rules_validate`,
  `roam_dogfood`.
- Total MCP tool count: 128 → 136. Core preset: 41 → 49.

### Added — distribution surface

- `templates/examples/.roam-rules.yml` — example rule pack showing all 4
  pattern types and BLOCK / WARN severities.
- **`templates/rules/python/.roam-rules.yml`** — 14-rule starter pack for
  Python: dangerous APIs (eval/exec/pickle.loads/os.system/yaml.load),
  hallucinated imports, layer violations, stale-code markers, dangerous
  base classes. Validated clean by `rules-validate`.
- **`templates/rules/typescript/.roam-rules.yml`** — 14-rule starter pack
  for TS/JS: eval/Function/document.write/innerHTML, layer violations,
  hallucinated imports, deprecated decorators. Validated clean.
- `templates/rules/README.md` — index of starter packs + customisation
  guidance.
- `src/roam/templates/ci/agent-review.yml` — drop-in GitHub Actions
  workflow that runs `roam pr-analyze` on every PR, posts a sticky markdown
  comment via `roam pr-comment-render`, verifies audit-trail integrity, and
  fails the check on BLOCK verdict.
- README.md gains dedicated "Roam Agent Review" + "Roam Cloud Lite" sections
  explaining the v2 paid layers on top of the OSS CLI.

### Added — shared helpers

- `roam.commands.git_helpers` — centralised `git_actor`, `git_origin_url`,
  `git_head_sha`, `git_branch`, `git_metadata`, `detect_roam_version`,
  `utc_timestamp`. Replaces 4-way duplicated git invocation code across
  `cmd_pr_analyze` and `cmd_metrics_push`. UTC timestamp formatting is
  now Python-version-stable.
- `roam.commands.audit_trail_helpers` — `DEFAULT_AUDIT_TRAIL_PATH`,
  `AUDIT_TRAIL_SCHEMA`, `INTEGRITY_SUMMARY_SCHEMA`, `load_records`,
  `next_sequence_number`. Eliminates 3-way `_load_records` duplication
  between `cmd_audit_trail_export` and `cmd_audit_trail_conformance`.

### Changed

- License switched from MIT to **Apache 2.0** (landed in 12.23). All
  references updated across LICENSE, pyproject, README, MCP server cards,
  docs/site, and SUBMISSION.md.

### Internal — cognitive complexity reductions

Self-dogfood with `roam complexity` surfaced 2 CRITICAL functions
(cc ≥ 99) and 3 HIGH functions in v2 modules. All five refactored:

- `_compute_ai_likelihood` (cc=110 → <28) — split into 9 per-signal helpers
  + `_parse_diff_into_buckets` + `_bucket_score`.
- `_render_github_markdown` (cc=101 → <28) — split into 8 per-section
  helpers (header, scores, drift banner, concerns, reviewers, rule
  violations, next steps, top signals, footer).
- `_load_rules_yaml` (cc=71 → <23) — strict-vs-tolerant branching
  collapsed into `_warn_or_raise`; YAML parsing into `_parse_rules_data`;
  per-rule type-coercion into `_coerce_rule`.
- `_emit_batch` (cc=48 → 26) — parallel-vs-serial paths split into
  `_run_batch_serial` / `_run_batch_parallel`.
- `_build_rationale` (cc=39 → <23) — concern collectors + next-steps
  composer + reviewer extractor pulled into small helpers.

### Surface counts

- CLI commands: 178 → **186** (+8: `pr-analyze`, `pr-comment-render`,
  `metrics-push`, `audit-trail-verify`, `audit-trail-export`,
  `audit-trail-conformance-check`, `rules-validate`, `dogfood`)
- MCP tools: 128 → **136**
- Core MCP preset: 41 → **49**

### Tests

- +280 new tests across 11 new test files: `test_pr_analyze.py` (~50),
  `test_pr_analyze_edge_cases.py` (33), `test_pr_analyze_v2_signals.py`
  (19), `test_pr_analyze_cache.py` (10), `test_pr_comment_render.py` (37),
  `test_metrics_push.py` (~30), `test_audit_trail_verify.py` (12),
  `test_audit_trail_aggregate.py` (15), `test_audit_trail_conformance.py`
  (22), `test_audit_trail_sequence.py` (7), `test_rules_validate.py` (25),
  `test_git_helpers.py` (14), `test_v2_edge_cases.py` (19),
  `test_dogfood.py` (7).
- 383 tests pass in the targeted v2 sweep (2 skipped).

## [12.25] - 2026-05-05

CI fix: backport ``QueryCursor`` for tree-sitter < 0.24 (Python 3.9
lane). The 12.24 narrowing got past the install layer; the next
breakage was an unconditional ``from tree_sitter import QueryCursor``
in ``roam/languages/query_engine.py``. ``QueryCursor`` was added to
the Python bindings in tree-sitter 0.24, but Python 3.9 pins to
tree-sitter 0.23.x (newer versions require ≥ 3.10).

This was also a real runtime bug — any Python 3.9 user installing
``roam-code`` from PyPI would have hit ``ImportError`` the first
time the indexer hit a YAML-extractor language.

Fix: ``try: from tree_sitter import QueryCursor; except ImportError:``
falls back to a thin shim that delegates ``.matches()`` and
``.captures()`` to the underlying ``Query`` object — the old
tree-sitter 0.23 API exposes the same methods on ``Query`` directly.

## [12.24] - 2026-05-05

CI fix: narrow the fastmcp dev-dep marker so Python 3.9 stops failing
to install. fastmcp >= 2.0 requires Python >= 3.10, which means the
unconditional ``"fastmcp>=2.0"`` shipped in 12.23 broke the 3.9 lane:

```
ERROR: Could not find a version that satisfies the requirement
fastmcp>=2.0; extra == "dev" (from roam-code[dev])
```

Marker is now ``"fastmcp>=2.0; python_version >= '3.10'"`` so 3.9
skips the install entirely. The MCP-runtime test already guards on
``_HAS_FASTMCP`` (12.23) so 3.9 simply skips that single assertion.

## [12.23] - 2026-05-05

CI bring-up: surface fastmcp dependency for the MCP-runtime tests.

After 12.22 fixed the indexer-order bug, CI exposed the next layer of
the saga: ``test_pass93_mcp_wrappers_registered`` asserted
``"roam_why_fail" in _TOOL_METADATA`` but CI installed only the
``[dev]`` extras (no ``fastmcp``). Without ``fastmcp`` the
``@_tool(...)`` decorator becomes a no-op and ``_TOOL_METADATA`` stays
empty — the test had been masked by the earlier blockers since 12.17.

Fix:

1. Add ``fastmcp>=2.0`` to the ``[dev]`` extra so CI exercises the
   actual MCP registration path.
2. Defense-in-depth: ``test_pass93_mcp_wrappers_registered`` is now
   ``@pytest.mark.skipif(not _HAS_FASTMCP, ...)`` so it stops gating
   environments that intentionally skip the optional extra.

## [12.22] - 2026-05-05

Indexer pipeline ordering fix + two CI test-isolation fixes.

### Indexer ordering — late-edge resolvers now run BEFORE graph metrics

Pass 69 cached ``build_symbol_graph(conn)`` keyed on ``id(conn)``. The
indexer pipeline ran graph metrics first, then the django-post,
pytest-fixture, and registry-dispatch resolvers — which add edges to
the DB AFTER the graph was already cached. When a follow-up command
opened a new readonly connection that happened to be assigned the same
``id()`` (Python reuses freed addresses), the cache returned the stale
graph from before those late edges existed.

The user-visible symptom: ``roam impact <fixture>`` showed "no
dependents" when there were transitively-depending tests, because the
``pytest_fixture_dep`` edges weren't in the cached graph the impact
command read.

Fix:

1. Reorder the indexer to run all late-edge resolvers BEFORE
   ``_compute_graph_metrics``. The graph metrics now reflect every
   edge, not a stale subset.
2. Clear the graph cache at the end of ``Indexer().run()`` so any
   subsequent reader builds fresh — belt-and-suspenders against future
   late-resolver additions.

### CI test isolation

- ``test_pass31_test_pyramid_runs`` ran against the project cwd. In
  CI, when sequential tests left the cwd in an unexpected state, the
  command produced empty stdout. Switched the test to use a fresh
  ``tmp_path`` + ``monkeypatch.chdir`` + indexed mini-project so it's
  independent of suite ordering.
- ``test_impact_picks_up_fixture_edges`` was failing at 3.9 / 3.10 /
  3.12 / 3.13. Same root cause as the indexer ordering above —
  pytest_fixture_dep edges were missing from the impact graph because
  the indexer cached metrics before adding them. Fixed by the indexer
  reorder.

## [12.21] - 2026-05-05

Ten quality + reliability passes (rounds 111-120). Three real CI bugs
fixed (CI has been red since 12.17), three more cognitive-complexity
splits, a new audit-report template, and a latent graph-cache leak
fix from Pass 69.

### redactedcmd_impact JSON contract

CI failure at 3.9 + 3.12. When ``roam impact`` finds the symbol in
the index but NOT in the dependency graph, the path emitted plain
text on stdout, breaking ``--json`` consumers. Wrapped in a proper
envelope (``summary.in_graph: False``) with the same hint surfaced
in the ``tip`` field.

### redactedhealth --gate exit code

CI failure at 3.13. The test asserted ``health_min: 100`` is
unreachably high but a tiny fixture project scores exactly 100, and
the comparison is ``score >= h_min`` so 100 ≥ 100 passes. Switched
the test to ``health_min: 999`` to make the threshold genuinely
unreachable.

### redactedMCP sampling test

CI failure at 3.11. Pass 98 added the ``ROAM_AI_ENABLED`` opt-in
gate; the existing test never set the env var, so sampling
returned None on CI. Updated the success-path test to set
``ROAM_AI_ENABLED=1`` and added a default-OFF assertion test.

### redacted_compute_reachability split

cc 150 (deepest nesting in repo at depth 8) → ~10. Decomposed
into ``_node_match_keys``, ``_matches_dep``,
``_trace_entry_reach``, ``_build_norm_lookup``, ``_record_match``.
Orchestrator stays under 10 LOC of branching.

### redactedpoll_loop split

cc 154 with 17 params at ``cmd_watch.py:457``. Pulled per-event
helpers (``_need_force``, ``_scan_disk_changes``,
``_label_webhook_events``, ``_refresh_tracked_after_reindex``,
``_run_guardian_step``) keeping the public signature stable so
callers and tests are unaffected.

### redactedtests for 5 untested commands

Added behavioural tests for ``py-modern`` (had 0 references),
``graph-stats``, ``mcp-status``, ``pre-commit``, ``exit-codes``
(each had 1 registration-only reference). 9 new tests.

### redactedROAM_QUERY_TIMEOUT_S coverage

Pass 58 shipped an opt-in SQLite progress handler. Zero test
coverage existed. Added 4 tests exercising no-env / invalid /
zero / and a tiny-budget interrupt that should fire OperationalError.

### redactedformat_table budget threading (cmd_context)

20 ``format_table()`` calls across 5 files lacked ``budget=``.
Added ``_table_budget(data)`` helper and threaded the global
``--budget`` through cmd_context's ``data`` dict. Wired into the
two highest-volume call sites (callers + callees lists).

### redactedaudit-report Markdown template

P1.2 strategic blocker per build_priorities.md. Built a 9-section,
185-line template at ``docs/audit_report_template.md`` with
placeholders for every ``roam audit --json`` field. Bridges the
gap between the engine (Pass 97 ``roam audit``) and the deliverable
artifact paying customers see.

### redacted_build_agent_descriptors split + graph-cache fix

Top remaining complexity offender: ``_build_agent_descriptors``
cc=161 in ``graph/partition.py``. Decomposed into 6 small helpers
(``_node_partition_index``, ``_fetch_node_metadata``,
``_file_majority_owners``, ``_read_only_files_for``,
``_boundary_contracts``, ``_cluster_label_for``).

Also fixed a latent state-leak bug from Pass 69's graph-builder
memoization: the cache was keyed on ``id(conn)`` and Python reuses
``id`` values across short-lived objects, so partition tests
running after orchestrate tests in the same process saw a stale
graph from a closed connection. Added an ``autouse`` fixture in
``conftest.py`` that calls ``clear_graph_cache()`` between tests.

Surface counts unchanged: 178 CLI commands, 128 MCP tools, 41 core.

## [12.20] - 2026-05-05

Ten quality-focused passes (rounds 101-110). No new commands; this
round is pure cleanup and hardening based on what `roam debt`,
`roam health`, and `roam complexity` reported about the codebase
itself.

### redacted`QueryEngine._extract_symbols_from_pattern` cc 198 → ~10

Single most-complex function in the codebase. Decomposed into four
small helpers (``_find_name_node``, ``_decode_capture``,
``_resolve_kotlin_class_kind``, ``_build_symbol_from_def``) leaving
the orchestrator at ~10 cognitive complexity. All 194 extractor
tests pass.

### redacted`_render_single_text` cc 189 → smaller orchestrator

Pulled the per-symbol header rendering (async badge, idiom badge,
paren-aware decorators block) out of ``cmd_context._render_single_text``
into ``_render_async_badge`` / ``_render_idiom_badge`` /
``_render_decorators_block``. The paren-aware split now correctly
handles `parametrize("a,b", [...])` decorators that previously got
mangled by naive comma-splitting.

### redacteddelete 4 truly-dead exports

`roam dead` aggregated 78 SAFE entries but most are decorator-
registered MCP tools (false positives the analyzer can't see
through). Of the 16 non-decorator candidates, 4 had only self-
references and were genuinely dead: removed
``write_site_payload`` (competitor_site_data),
``detect_string_format_old`` (python_idioms — disabled by
``return findings`` on first iteration),
``structured_click_exception`` (output/errors).

### redactedbreak the cli ↔ cmd_doctor cycle

`roam health` flagged exactly one actionable cycle: cmd_doctor
imported `_COMMANDS` from cli, while cli's command registry
referenced cmd_doctor. Static graph saw it as a 2-edge cycle.
Replaced ``from roam.cli import _COMMANDS`` with
``importlib.import_module("roam.cli")`` so the only edge is
runtime-only — cycle eliminated, doctor still validates every
registered command.

### redactedhealth 80 → 88 via utility-path classifier fix

The god-component classifier was labeling architectural hubs
(``cli`` Click root, ``_run_roam`` MCP dispatch, ``build_symbol_graph``)
as actionable when they're SUPPOSED to have high fan-in. Added
``graph/`` ``mcp_extras/`` ``languages/`` to ``_UTILITY_PATH_PATTERNS``
and ``cli.py`` ``mcp_server.py`` ``file_roles.py`` to
``_UTILITY_FILE_PATTERNS``. Health score jumped 80 → 88 (+8 pts).

### redacted`_analyze_dataflow_dead` cc 160 → ~10

Top of the danger-zone list (cmd_dead.py: 3362 churn × cc=24.6
× fan-in=8 = score 1.68). The 200-line ``_analyze_dataflow_dead``
mega-function split into ``_table_exists``, ``_read_caller_line``,
``_is_return_captured``, ``_detect_unused_returns``,
``_parse_param_names``, ``_detect_dead_param_chains``,
``_detect_side_effect_only``. Orchestrator stays under 10. All 48
dead-code tests pass.

### redactedobservability hook extended

Pass 92 covered cmd_metrics + cmd_describe (20 sites). Pass 107
adds cmd_understand (4 sites), metrics_history (9 sites), and the
remaining nested patterns. ``ROAM_VERBOSE=1`` now surfaces 31
swallow points; remaining ~40 are in less-touched commands and
will land incrementally.

### redactedsecond `--json` bypass sweep

Probed every command with an unknown-symbol input. Caught one new
bypass: ``roam test-map UnknownXYZ`` printed plain text "Not
found: ..." instead of a JSON envelope. Fixed.

### redactedTODO/FIXME audit (no real debt)

22 markers in source; all 22 are intentional —
``cmd_test_scaffold.py`` writes "TODO" strings as scaffold output
(17 sites) and ``cmd_vibe_check.py`` detects TODO patterns in user
code (5 sites). No actual debt. Decision logged here.

### redactedorphan-imports false-positive sweep

`orphan-imports` was flagging ``roam.telemetry`` (Pass 42) and
``roam.observability`` (Pass 92) as ``internal_typo`` because the
indexed file table was older than these modules.
``_indexed_python_modules`` now also walks ``src/`` directly so
modules added between index runs aren't false-flagged. 30 false
internal-typo entries eliminated; total orphan count 164 → 143.

## [12.19] - 2026-05-05

Ten quality-focused passes (rounds 91-100). Net new surface:
1 CLI command (`audit` — Priority 1 strategic blocker), 5 MCP
wrappers (`roam_alerts`, `roam_timeline`, `roam_test_impact`,
`roam_disambiguate`, `roam_why_fail`), cross-language
`orphan-imports` (JS/TS/Go), auto-generated complete-reference
appendix in the docs site, MCP error-storm rate-limiter,
agent-export `--brief` mode, observability hook for swallowed
exceptions, and registry-dispatch detection in `roam impact`.

### redacted`--json` empty-state sweep

Same class of bug as the 12.18.1 safe-zones hotfix. Fixed three
real bypasses uncovered by JSON-parse probes:
``cmd_complexity`` (3 sites: empty data, no matches, no bumpy
roads), ``cmd_coverage_gaps`` (missing-filter usage error),
and ``cmd_config`` where a flag-default mismatch made
``roam --json config`` silently produce empty output.

### redactedsilent `except: pass` observability hook

84 ``except Exception: pass`` blocks across 40 files masked
real failures (missing schema columns, optional dependencies,
sqlite errors). Added ``roam.observability.log_swallowed``
which is a no-op unless ``ROAM_VERBOSE=1`` (or
``ROAM_OBSERVABILITY=1``) is set. Applied to the heaviest
offenders: ``cmd_metrics`` (12 sites) and ``cmd_describe`` (8
sites). Rate-limited to 5 reports per scope per process.

### redactedfive MCP wrappers

Wired up agent-actionable signals that were CLI-only:
``roam_alerts``, ``roam_timeline``, ``roam_test_impact``,
``roam_disambiguate``, ``roam_why_fail``. All five added to
the core preset (35 → 41 core tools).

### redactedN+1 SQL batching

Replaced per-symbol ``conn.execute`` loops in
``cmd_adversarial`` (orphaned-symbols + high-fan-out checks)
with a single ``batched_in()`` query. On a 14k-symbol repo,
``roam adversarial`` previously made thousands of round-trips;
now one batch per check. Same pattern for ``cmd_affected``
(start-symbol collection).

### redactedauto-regenerated command reference

Hand-curated workflow sections in
``docs/site/command-reference.html`` now have a complete
auto-generated appendix listing every command + short help line
organised by category, between
``<!-- BEGIN auto-reference -->`` markers. Regenerate with
``python dev/build_command_reference.py``. Coverage went from
73 to 185 commands documented.

### redactedcross-language `orphan-imports`

Pass 44 was Python-only. Extended to JS/TS (path-rewrite
resolution + bare-specifier detection) and Go (stdlib +
hostname-shaped import path heuristic). New ``--lang`` flag
(``all`` / ``python`` / ``javascript`` / ``go``).

### redacted`roam audit`

Build-priorities P1: revenue-blocker meta-command. Chains
``health → debt → dead → test-pyramid → api → stats →
hotspots --danger`` into one envelope with a
top-level summary (verdict, health_score, debt_total,
danger_zone_count, api_surface, etc.). Pass ``--brief`` to drop
per-section detail.

### redactedAI-on-client-code default OFF

Sampling/LLM hook in ``mcp_extras/sampling.py`` now requires
``ROAM_AI_ENABLED=1`` (or ``=true``) to dispatch payloads to
the client's LLM. Without the env var, the hook returns
``None`` and callers fall back to the raw envelope. GDPR / EU
AI Act credibility blocker for the first paid audit.

### redacted`roam impact` dispatch-via-registry

Dogfood #189 — the call graph misses consumers that route
through string-lookup tables (cli ``_COMMANDS``, ask recipes,
plugin entry points). New ``indirect_refs`` field in the
``impact`` envelope scans source files for string literals
matching the symbol's name/qname. Surfaces ``43 sites`` for
``health`` that the static graph misses.

### redactedagent-export `--brief`

``roam --json agent-export`` previously emitted ~6 KB of
nested JSON (directory layout, key files, hotspots, layers,
clusters). New ``--brief`` flag drops the verbose payload and
keeps only the top-level summary — 6197 → 608 bytes (10×
reduction). Useful for CI / fleet workflows that just need
project metadata.

## [12.18.1] - 2026-05-05

Hotfix for a CI failure spotted in the 12.18 release run. ``roam
safe-zones --json <missing-symbol>`` printed a plain-text "Target
symbol(s) not found in the dependency graph." line when the
target wasn't in the graph, which broke ``json.loads`` consumers.
The empty-result branch now emits a proper envelope with
``summary.verdict``, ``internal_size=0``, and ``boundary_size=0``.

The bug pre-dated this batch — it surfaced because CI runs in
3.12 / 3.13 environments where the test fixture happened to seed
a name that wasn't in the test-project graph. Local Python 3.11
runs didn't trip it.

## [12.18] - 2026-05-05

Ten more deep passes (rounds 81-90), shipped as a focused
follow-up to 12.17. Net new surface: 5 CLI commands
(`disambiguate`, `pre-commit`, `mcp-status`, `test-impact`,
`recipes`), 1 new flag (`map --seed/--depth`), 1 new env-var
override family (`ROAM_RERANK_*`), MCP error-storm rate-limiter
that drops verbose envelope on repeated failures, and a
recheck-driven shipping pipeline that caught residual stale
counts left over from the 12.17 ship.

### redacted`roam disambiguate <name>`

Lists every symbol matching the name with file/line/kind/
signature/docstring snippet + PageRank tiebreaker. Saves
agents from picking the wrong overload when names collide.

### redacted`roam pre-commit`

Generates a git pre-commit hook that runs `git diff --cached |
roam critique` on staged changes. Idempotent installer
(``--install``); preview-only by default (``--print``).
``ROAM_PRECOMMIT_SKIP=1`` to bypass.

### redacted`roam mcp-status`

Companion to `roam doctor` for the MCP transport: preset,
registered tool count, backpressure limits (max_concurrent,
in_flight, busy_responses_total), result-cache size, watcher
state.

### redacted`roam test-impact <range>`

Sharper than `affected-tests`. Walks BFS over the reverse call
graph from each changed symbol; ranks tests by the number of
changed symbols that reach them.

### redactedrerank weights via env vars

`ROAM_RERANK_ALPHA` / `BETA` / `GAMMA` / `DELTA` / `EPSILON` /
`ZETA` override `[retrieve]` config without touching
config.toml. Useful for quick weight-tuning loops.

### redacted`roam fitness --explain`

Confirmed already shipped. Verified the existing flag covers
the per-violation rule citation requirement.

### redactedMCP error storm rate-limit

When the same `error_code` fires ≥ 3× in a row, the MCP error
envelope drops the verbose fields (`hint`, `suggested_action`,
`doc_link`, `severity`) and replaces them with a tight
`{error_code, repeat_count, trimmed: True}` shape. Reduces
token bloat in agent retry loops. Counter resets when a
different error_code fires.

### redacted`roam recipes`

Sugar over `roam ask --list` for discoverability. Lists every
recipe with intent + example queries + commands. JSON envelope
includes the full recipe metadata.

### redacted`roam why --json` audit

Verified that the existing `why --json` payload already returns
structured per-symbol fields (`role`, `fan_in`, `fan_out`,
`pagerank`, `reach`, `cluster`). No work needed — the
explanation is already structured.

### redacted`roam map --seed --depth`

Restricts the project map's top-symbols list to symbols
reachable from a seed file within N hops. For monorepo
navigation where the full map is overwhelming.

## [12.17] - 2026-05-05

Sixty deep passes (rounds 21-80), shipped together. Net new
surface: 18 CLI commands (`plugins`, `test-pyramid`, `index-stats`,
`telemetry`, `orphan-imports`, `changelog`, `graph-export`,
`help-search`, `timeline`, `pr-prep`, `stats`, `why-fail`,
`graph-stats`, `recommend`, `api`, `exit-codes`, `version`, plus
the `oracle batch` subcommand), 1 MCP tool (`roam_catalog`), 2
doctor checks, 11 new `ask` recipes, many new flags (`--explain`,
`--danger`, `--env`, `--batch`, `--quality`, `--scope`,
`--check`, `--quick`, `--hops`, `--mode`, `--since-tag`,
`--focus`, `--inline`, `--by-file`, `--weights`, `--recent`,
`--dry-run`, `--next`), 2 opt-in indexing
structured error `doc_link` + `severity` field, ask-classifier
auto-routing for unknown commands, opt-in local telemetry,
richer `roam_catalog` metadata (when_to_use + examples), graceful
Ctrl-C handling, MCP `roam_health` payload trimming when noisy,
graph-builder memoization, and a deprecation registry hook.

### redacted`roam why-fail <test>`

Triage helper: traces from a failing test (or symbol) back to
recently-changed symbols it transitively reaches. Sorted by
recency × hop distance × PageRank.

### redacted`roam graph-stats`

Graph-level invariants: density, weak components, non-trivial
cycles, average degree, top-inbound symbols. Single overview
number for "how dense / connected is this codebase".

### redacted`roam recommend <symbol>`

Surfaces related symbols using three signals — call-graph
neighbours, git co-change, persisted clone siblings —
combined with normalised contribution scoring.

### redacted`roam diff --since-tag`

Auto-fills the commit range with `<last-tag>..HEAD` via
``git describe --tags --abbrev=0``.

### redacted`roam tour --focus <module>`

Constrains the tour (top symbols, reading order, entry points)
to files under the given path prefix.

### redactedtaint risk score

`roam taint` summary now includes a 0-100 ``risk_score``
weighting errors 5×, warnings 1×, and discounting sanitized
findings.

### redacted`roam context --inline`

Concatenates the recommended files into one paste-ready block
with line numbers — for chat agents that prefer one big string
over multi-file output.

### redacted`roam clones --by-file`

Aggregates clone pairs into (file, file) coupling. Shows which
file pairs are most clone-coupled.

### redactedgraph-builder memoization

`build_symbol_graph` and `build_file_graph` cache by
``id(conn)`` so compound commands like ``pr-prep`` (which
internally call multiple subcommands) don't rebuild the graph
multiple times.

### redacted`roam api`

Lists the public API surface (exported public symbols + their
signatures). Useful for changelog generation and breaking-
change detection.

### redactederror envelope `severity`

MCP error envelopes now include a ``severity`` field
(`info | warning | error | fatal`) per error code. Lets agents
branch on severity without parsing the message.

### redacted`roam search --recent`

Boost results in files modified within N days. Useful when
retracing very recent changes.

### redacted`roam config --weights`

Surfaces the active rerank weights (alpha/beta/gamma/delta/
epsilon/zeta) merged with defaults. Replaces grepping the
source.

### redacted`roam diagnose --batch`

Run diagnose on N symbols from a newline-separated list (file
or stdin). Mirrors the oracle batch pattern.

### redactedMCP `roam_health` payload trimming

When the issue count is ≥ 50, the MCP envelope drops the verbose
issue list and keeps the score, category counts, and
breakdown. Set ``ROAM_MCP_HEALTH_FULL=1`` for the unfiltered
shape.

### redacted`roam reset --dry-run`

Preview the destructive reset (DB path + size) without deleting.
No --force required for the preview.

### redacted`roam exit-codes`

Lists every roam exit code with its meaning. Replaces grepping
the docs or source.

### redacted`roam workflow --next`

Given a previously-run command name, suggest what to run next
(e.g. after `preflight`: `context`, `impact`, `diff`).

### redacteddeprecation registry

Adds the ``_DEPRECATED_COMMANDS`` map in ``cli.py``. When a
deprecated command is invoked, the LazyGroup resolver prints a
"use X instead" note on stderr without breaking the call.

### redacted`roam version --check`

Prints the installed version and (with ``--check``) queries
PyPI for the latest version. Offline-friendly: falls back
silently when PyPI is unreachable.

### redacted`roam timeline <symbol>`

Chronological commit history for the file owning a symbol:
SHA, date, author, lines added/removed, subject. Joins
``symbols`` × ``git_file_changes`` × ``git_commits`` with a
GROUP BY commit_id to dedupe duplicate change rows.

### redacted`roam pr-prep`

One-shot pre-PR fitness check that bundles ``diff`` +
``critique`` + ``pr-risk`` into a single envelope with a
top-level ``ready_to_open`` boolean. Replaces calling four
commands sequentially before opening a PR.

### redacted`roam eval-retrieve --quick`

Runs the first 5 tasks of the bench harness for fast local
iteration. The full 30-task bench takes too long for tight
weight-tuning loops.

### redacted`roam config --check`

Validates ``.roam/config.json`` against the known-keys schema.
Flags unknown keys (typo guard) and type mismatches. Lists the
canonical key set with one-line descriptions when no issues are
found.

### redactedricher `roam_catalog` metadata

Tool catalog now includes ``when_to_use`` (extracted from each
docstring's "WHEN TO USE:" line) and up to three doctest-style
``>>> roam ...`` examples per tool. Lets agents pick the right
tool without fetching each individual description.

### redacted`roam impact --hops N`

Bound the BFS at N hops instead of full transitive descendants.
``--hops 1`` mirrors ``roam uses``; ``--hops 3`` shows callers
of callers of callers. Lets agents scope a refactor to a
controlled radius.

### redacted`ROAM_QUERY_TIMEOUT_S` query timeout

Opt-in SQLite progress handler that interrupts long queries
past N seconds. Prevents hangs on huge codebases. Default
behaviour unchanged when env var is absent.

### redacted`roam search --mode regex|exact|substring`

Three matching modes. Default is ``substring`` (LIKE %p%, the
existing behaviour). ``regex`` registers a Python ``re``-backed
SQLite REGEXP function. ``exact`` matches name = pattern only.

### redacted`roam stats`

Aggregate metrics over the index: file count, symbol count,
total lines, recent commit activity (last N days), broken down
by language / file role / symbol kind. Useful as the first
thing an agent runs after ``roam init``.

### redacted`roam test-pyramid`

Counts test files by sub-kind (unit / integration / e2e / smoke /
unknown) using ``classify_test_kind`` from Pass 23. Verdict flags
inverted pyramids (``e2e+integration > unit``) and unstructured
test layouts (``unknown >= 4× classified``).

### redactedworking-tree drift in `index_status`

Adds a ``dirty_files`` field to the staleness envelope. Even when
``HEAD`` matches the indexed commit, an outstanding working-tree edit
makes the symbol/edge data stale; we count modified files via
``git status --porcelain`` and surface a refresh hint.

### redacted`roam_catalog` MCP tool

Machine-readable list of every registered MCP tool with capability
flags (``core`` / ``read_only`` / ``destructive``). Replaces having to
enumerate ``list_tools`` and parse each one — the catalog is one
round-trip and is part of the core preset.

### redacted`roam health --explain`

The 0-100 health score is a weighted geometric mean of five factors;
``--explain`` shows each factor's "loss" in points so the user can
see which dimension is dragging the score down. Surfaced in both
text mode (sorted breakdown table) and JSON envelope
(``score_breakdown`` array).

### redacteddoctor adds plugin + table checks

``roam doctor`` now runs 13 checks (was 11). New entries: plugin
discovery error count via ``get_plugin_errors()``, and required-table
presence (``files``, ``symbols``, ``edges``, ``git_commits``,
``file_stats``) — surfaces a half-migrated DB before a downstream
"no such table" error.

### redacted`roam config --env`

Walks ``src/roam/`` for ``ROAM_*`` references and prints a sorted,
deduped inventory of every env var the codebase reads, with the
file/line of the first read and whether it's currently set.
Replaces grepping the source manually.

### redacted`roam hotspots --danger`

Files in the top quartile of churn × file complexity × max
fan-in. Score is the geometric mean of the metric ratios so a
moderate-everywhere file ranks above one that's extreme in only
one dimension.

### redacted`roam index-stats`

Surface the ``.roam/index.db`` size, table row counts, and SQLite
fragmentation (``freelist_count / page_count``). Verdict suggests
``VACUUM`` above 25% fragmentation and ``roam reset`` when both
fragmented and oversized (default 200 MB threshold, override via
``ROAM_INDEX_SIZE_WARN_MB``).

### redacted`roam critique --batch <dir>`

Reviews every ``*.diff`` and ``*.patch`` in the directory in a single
pass. Handy for reviewing a stack of PRs or a series of
``git format-patch`` output. Per-diff verdict + aggregate gate fail
when any diff has a high-severity finding.

### redactedgraceful Ctrl-C

``python -m roam`` now catches ``KeyboardInterrupt`` at the top level
and exits with the conventional 130 instead of dumping a traceback.
The indexer also catches the interrupt to release its lock cleanly,
so a rerun resumes from the last committed checkpoint instead of
stumbling on a stale ``.roam/index.lock``.

### redactedauto-route unknown commands

When ``roam <unknown>`` doesn't have a close edit-distance neighbour in
``_COMMANDS``, the LazyGroup's resolver now consults the ``ask``
TF-IDF classifier. If a recipe matches with confidence ≥ 0.5, the
``UsageError`` suggests ``roam ask "<input>"`` so a natural-language
attempt ("trace login flow through middleware") still leads
somewhere useful in one turn.

### redactedopt-in local telemetry

``ROAM_TELEMETRY_LOCAL=1`` enables a tiny SQLite ring buffer
(`.roam/telemetry.db`, 500-row cap, prune-on-write) that records
``(command, duration_ms, exit_code, ts)`` for every CLI invocation.
Surface via ``roam telemetry`` (slowest + recent calls). Strictly
local — no network. No-op when env var is absent so the hot path
stays unaffected.

### redacted`roam oracle batch`

The five boolean oracles (``symbol-exists``, ``route-exists``,
``is-test-only``, ``is-reachable-from-entry``, ``is-clone-of``)
now accept a JSONL stream via ``roam oracle batch [--input -]``.
Each line is one ``{oracle, args}`` object; output is a single
JSON envelope with all results. Useful for fleet-style pre-flight
checks (50 symbols at once instead of 50 round-trips).

### redacted`roam orphan-imports`

Quick Python-only lint that flags imports the indexer couldn't
resolve. Distinguishes ``internal_typo`` (top-level package
indexed but submodule missing — e.g. ``roam.cmds.foo`` instead
of ``roam.commands.cmd_foo``) from ``missing_package`` (genuinely
absent). JS/TS/Go versions deferred — per-language scaffolding
overhead is too much for one pass.

### redacted`roam docs-coverage --quality`

Buckets every public symbol's docstring into ``ABSENT / SHALLOW
/ RICH``. Heuristic: a docstring is ``RICH`` when its length ≥ 80
chars AND it mentions params/returns or has an example block;
``SHALLOW`` otherwise. Surfaces in both text and JSON output, with
sample symbols per bucket so the user can see the gap concretely.

### redacted`roam search --explain` shows PageRank

The ``--explain`` flag already showed BM25 + matched fields +
highlights + term counts. Pass 46 adds the per-result PageRank to
the explanation so users can see when ordering is structural-rerank-
driven vs. lexical.

### redacted`roam retrieve --scope <dir>`

Restrict candidates to files under a given path prefix —
useful for monorepos and large codebases where the user knows
the relevant subtree. Post-filter on the ranked candidate list,
so no rerun of the heavy retrieval pipeline.

### redacted`roam changelog --suggest`

Read commits since the last tag, classify them via Conventional
Commits prefixes (feat / fix / perf / refactor / docs / test / chore /
build / ci), emit a draft ``## [Unreleased]`` markdown section grouped
by bucket. ``--since <ref>`` overrides the tag autodetect.

### redacted`roam graph-export`

Write the symbol or file dependency graph as ``GraphML / DOT /
JSONL`` for plugging into external graph tooling (Gephi, Cytoscape,
igraph, or custom analyses). ``--scope file`` switches from the
symbol-level graph to the file-level graph.

### redacted`roam help-search <query>`

Fuzzy match across every command's name + short docstring.
Replaces grepping ``--help-all`` output of 158 commands. Score
weights name matches above docstring matches and rewards shorter
matching names.

### redactedMCP-level result caching

The MCP server already had per-cell caching for a handful of hot paths
(`understand`, `tour`); Pass 21 promotes ~30 read-only commands into a
shared, index-mtime-keyed result cache. Cache hit drops the round-trip
from 153ms to 1ms (153× speedup) without changing tool semantics.
Auto-invalidates on reindex (mtime bump on `.roam/index.db`).

### redacted`roam ask` recipe expansion (13 → 24)

Eleven new TF-IDF-classifiable recipes covering common agent
workflows: `trace-bug`, `who-owns`, `what-changed`, `audit-security`,
`explore-impact`, `find-similar`, `why-this-exists`, `check-pr`,
`explore-tests`, `dependency-update`, `visualize-architecture`. Each
maps to an existing roam command pipeline so the dispatcher stays a
thin classifier-and-route — no new analysis logic.

### redactedtest sub-classification

`file_roles.py` now exports ``classify_test_kind(path)`` returning
``unit | integration | e2e | smoke | unknown``. Path-pattern first
(``e2e/``, ``integration/``, ``cypress/``, ``playwright/``), then
filename-pattern fallback (``*_e2e.py``, ``*_smoke.py``). Lays the
groundwork for "test pyramid" reports (Pass 31+) without changing
the existing ``is_test`` boolean contract.

### redactederror envelope `doc_link` field

The MCP error path already emitted ``error_code``, ``hint``, and
``retryable``. Pass 28 fills the fourth field of the structured-
error contract: every classified ``error_code`` now carries a
stable ``doc_link`` pointing at an anchor in the public
troubleshooting page. Agents get one URL to fetch when self-
serving an error, instead of grep-the-docs-and-pray.

### redactedopt-in parallel source prefetch

``ROAM_PARALLEL_INDEX=1`` enables a thread-pool source prefetcher
in the indexer. Disk reads run in parallel up to ``min(32,
cpu_count*2)`` workers ahead of the (still-serial) parse + DB
write loop. The serial section is unchanged, so this is safe
under concurrency and a no-op without the env var.

I/O-dominated indexes (cold cache, OneDrive-mirrored repos,
network drives) see the biggest wins; CPU-bound indexes see no
regression because the cache is consumed in-order.

### redacted`roam plugins`

The plugin discovery system has shipped since v11 (entry points
+ ``ROAM_PLUGIN_MODULES``) but had no introspection surface.
``roam plugins`` lists discovered commands, detectors, language
extractors, extensions, grammar aliases, and any discovery
errors. JSON envelope mirrors the same fields. With no plugins
registered, prints the activation hint instead.

### Decisions logged (no shipped change)

- Pass 24 (``--markdown`` global flag) — deferred. Rendering layer
  would touch every command. Adding the flag without a working
  renderer is dead code; revisit when there's a concrete agent
  surface that benefits from it.
- Pass 25 (``roam impact-commit <hash>``) — already covered by
  ``roam diff <commit-range>`` (e.g. ``roam diff HEAD~1``).
- Pass 26 (compound ``roam_explore`` MCP tool) — already shipped.
- Pass 27 (stale-command audit) — all 162 CLI command names appear
  in at least one test. No cleanup needed.

## [12.14] - 2026-05-05

Ten more research passes building on v12.13's speed wins. Three
land as concrete features; the rest were research-decided
(existing surface adequate or out of scope).

### Did-you-mean for command typos (Pass 14)

``LazyGroup.resolve_command`` now catches Click's "No such command"
and surfaces the closest names by edit distance. Previous behaviour:

```
$ roam contxt
Usage: python -m roam [OPTIONS] COMMAND [ARGS]...
```

— bare error, no recovery hint. Now:

```
$ roam contxt
Error: No such command: 'contxt'. Did you mean `roam context`, `roam agent-context`?
```

Up to 3 suggestions at edit-distance ≤ 0.6, picked from the live
``_COMMANDS`` table so plugin commands also surface.

### Auto-refine on low-confidence retrieve (Pass 13)

When ``roam retrieve`` confidence drops below 0.40, the verdict now
appends a ``REFINE:`` block with 2-3 alternative queries:

1. **Drop NL filler** — ``"trace the login flow"`` → ``"login flow"``,
   removing the words that diluted the lexical signal.
2. **Anchor on top result's file** — adds ``--seed-files <path>``
   pointing at the highest-scoring candidate.
3. **Pivot to ``roam search``** — when the query contains an
   identifier-shaped token, exact-name lookup may beat structural
   retrieval.

Surfaced in both text mode (``REFINE:`` block) and JSON
(``summary.refinements``), so MCP clients can branch on it.

### ``--help-all`` global option (Pass 19)

``roam --help`` shows priority categories + 66 names from "More
Commands" without descriptions. Agents mapping the surface want
every command's one-liner. ``roam --help-all`` renders all 162
invokable names with their AST-extracted short-help, sub-second.
The flat list is alphabetical, deterministic, and pipeable.

### Smaller fixes

- ``roam dead`` empty-state now leads with ``VERDICT: no dead exports
  — every exported symbol has at least one consumer`` instead of just
  the bare section header.

### Research findings (decided not to ship)

- **Pass 11 (indexing speed)** — incremental index is ~2.8s warm.
  ``compute_file_stats`` and friends already early-exit on no-change.
  Further wins would require a daemon mode.
- **Pass 12 (symbol disambiguation)** — ``pick_best`` already uses a
  6-level tiebreak (edge count → PageRank → cc → churn → path
  priority → id). Live tests confirm canonical paths win
  consistently.
- **Pass 15 (cold-start of common commands)** — ``cmd_search``
  subprocess at 320ms is mostly Python interpreter (~90ms) + Click
  parse + execute. Hot path already tight; further wins need a
  daemon or in-process MCP path (already free for MCP clients).
- **Pass 16 (empty / edge-case repos)** — most commands handle empty
  repos correctly; one cosmetic dead-empty fix landed.
- **Pass 17 (mermaid quality)** — ``visualize`` output is
  well-structured (color-coded by kind, named clusters).
- **Pass 18 (schema export)** — ``roam schema`` already validates
  envelopes. Per-command schema introspection is a bigger feature.
- **Pass 20 (cross-command consistency)** — verdict-first compliance
  surveyed across 33 commands; previously-flagged outliers all
  resolved in v12.12.8 polish round.

## [12.13] - 2026-05-05

Ten dedicated research passes plus three check phases. Drops the
third version segment going forward — there's no reason for a patch
suffix on these incremental releases. Future versions: 12.14, 12.15,
not 12.13.x.

### Speed wins

| Operation | v12.12.9 | v12.13 | Speedup |
|---|---|---|---|
| ``roam --help`` | 3845 ms | **790 ms** | **4.9×** |
| ``roam uses`` | 700 ms | **347 ms** | **2.0×** |

**``--help`` cold path.** The previous ``format_help()`` called
``self.get_command()`` on every command in the priority categories,
which triggered ``importlib.import_module()`` for each cmd_*.py.
Around 20 module imports added 3.5 seconds to render the help
banner. v12.13 extracts the short-help via Python ``ast`` from the
source file's first docstring without importing — same output, no
cmd module loads.

**``roam uses`` warm path.** ``_test_text_consumers`` was reading
~590 test files (4.0 seconds of ``io.open`` calls) on every
``uses`` invocation against a Python repo. The fallback exists for
JS/Vitest where the symbol resolver leaves gaps; on Python / Go /
Rust the edges table already has every reference, so the scan was
a 4-second-per-call no-op. Now gated on whether the target's
language is in the JS family (``javascript``, ``typescript``,
``tsx``, ``jsx``, ``vue``, ``svelte``).

### Smarter retrieval

- **Programming-abbreviation expansion** in the seed tokenizer.
  ``db connect`` / ``ctx propagation`` / ``fn signature`` /
  ``auth flow`` / ``find error`` now seed both the abbr and its
  expansion (``db``↔``database``, ``ctx``↔``context``,
  ``auth``↔``authentication``, …) so the FTS layer hits whichever
  spelling the codebase uses. Only fires for short queries (≤4
  words) where shorthand is most likely; long queries already
  carry enough seed tokens. Curated 36-pair table.
- **Adaptive budget defaults** — ``--budget`` now scales with
  ``--k`` (200 tokens per result, floor 1500, ceiling 2× the
  configured default). ``--k 5`` budgets at 1500 tokens (saves
  tokens), ``--k 50`` at 8000 (more room). The standard ``--k 20``
  path stays at the configured 4000 default for backwards compat.
- **PageRank-ranked affected-files** in ``roam impact``. Was
  alphabetical (``benchmarks/`` and ``bench-repos/`` ahead of
  ``src/roam/cli.py``); now sorted by max-dependent PageRank so
  the high-impact files surface first.

### Newcomer-friendly tour

``roam tour`` "Key Symbols" list now appends a one-line docstring
summary for each top symbol. Pure-PageRank ranking surfaces
plumbing functions (``open_db``, ``json_envelope``,
``find_project_root``) at the top because every command imports
them — without context, a newcomer doesn't know what these are.
The docstring excerpt orients them:

```
fn  open_db                        src/roam/db/connection.py:354
    Context manager for database access. Creates schema if needed
fn  json_envelope                  src/roam/output/formatter.py:346
    Wrap command output in a self-describing envelope.
```

### Bench-neutral, performance-positive

The 10-pass round preserves the bench position from v12.12.9:
recall@5=0.708, recall@10=0.778, recall@20=0.878 across the
30-task self-bench. Speed gains are pure addition.

### Research findings (not landed)

Some passes researched-and-decided rather than shipped:

- **Pass 5 (N+1 detection)** — existing detector catalog already
  covers the SOTA static-analysis space. Runtime profilers like
  ``nplusone`` are complementary, not replacement.
- **Pass 6 (clone detection)** — current AST-hash-bag + Jaccard
  approach is SOTA-comparable. Neural alternatives (CCDetect,
  ASTNN) need training data and don't pay back the integration cost.
- **Pass 8 (anomaly detection)** — Modified Z-Score (MAD-based) +
  Theil-Sen + Mann-Kendall + Western Electric + CUSUM cover the
  statistical anomaly-detection space without sklearn as a hard dep.
- **Pass 10 (semantic retrieve)** — graceful zeta redistribution
  regressed bench (-1.9 pp recall@5). Reverted; semantic stays
  inert until the ``[semantic]`` extras are installed and
  embeddings are populated. Keeping the wheel under 5 MB matters.

## [12.12.9] - 2026-05-05

Three smarter / more dynamic moves layered on the v12.12.8 polish:
recency-aware retrieve, calibrated confidence numbers, and a
broken empty-state guard.

### Recency-aware retrieve (adapts daily without retuning)

Files modified within the last 14 days now get a small boost in the
``roam retrieve`` reranker. Hypothesis: when a developer asks "where
is X?" they're usually asking about something they're actively
working on. Magnitude up to +0.05 for files edited *today*, decaying
linearly to zero at 14 days. Suppressed when the query is shaped
like a historical question ("old auth handler", "deprecated routes",
"legacy code") because recent edits are anti-signal there.

Implementation: ``_recency_boost`` in ``retrieve/rerank.py``. One
batched ``MAX(git_commits.timestamp)`` query per call — no
per-candidate fan-out. Bench-tuned at 0.05 to be ``recall@5
+0.8 pp`` neutral-to-positive against the 30-task self-bench (the
synthetic bench labels treat all expected files as equal regardless
of mtime, so a stronger recency lift slightly rearranges co-equal
answers and shows as bench-neutral; the magnitude is real-world-
positive without disturbing bench-equal-treatment).

The boost adapts daily — yesterday's hot file becomes today's stale
one without any retuning or feedback loop.

### Calibrated confidence numbers in retrieve

The previous binary low/ok confidence label is now a continuous
score in ``[0.0, 1.0]`` exposed in the verdict and JSON summary.
Three signals combine: score gap (top vs runners-up, gap ≥ 0.30 →
unique winner), score floor (top < 0.30 with bunched tail → noise),
and **squared** token coverage. The squared coverage penalises
partial-coverage queries harder than linear — *"trace the login
flow"* (2/3 tokens covered, "login" missing) had been crossing
"ok" because linear coverage gave 0.67; squared drops to 0.45 and
the verdict carries the lower number.

Output sample:

```
VERDICT: 5 spans (... 10 seeds) (confidence 0.82)   ← real impl query
VERDICT: 5 spans (... 10 seeds) (confidence 0.71)   ← junk query
```

JSON summary now exposes ``confidence: 0.82`` alongside the
existing ``low_confidence`` boolean.

### `roam coverage-gaps` empty state

The "no flag passed" case used to print ``"Provide --gate <names>
or --gate-pattern <regex>"`` and exit. Now leads with ``VERDICT:
missing required filter — pass --gate or --gate-pattern``, lists
the two flags with their formats, and shows two example
invocations. Same shape every other empty-state command in the
surface uses.

## [12.12.8] - 2026-05-04

Phases 2 + 3 + 4 in one release: rough-edge polish, smarter verdicts,
and cross-command synergy.

### Phase 2 — verdict-first compliance

Several commands skipped the surface-wide ``VERDICT: ...`` opening line
that every other command leads with, leaving agents to count
``[FAIL]`` markers or scroll past raw section headers to find the
bottom line. Now consistent across:

- ``roam layers`` — opens with the architecture-shape verdict
  (``Flat (80% in Layer 0) — 14 layers, 0 violation(s)``) and exposes
  ``shape`` + ``verdict`` in JSON ``summary``.
- ``roam dead`` — opens with the safe-vs-review breakdown
  (``424 dead export(s) — 78 safe to delete, 302 review, 44 intentional``).
- ``roam adrs`` empty-state — was ``"No Architecture Decision Records
  found."``, now leads with ``VERDICT: No Architecture Decision Records
  found``.
- ``roam api-drift`` and ``roam orphan-routes`` empty-state messages
  similarly prefix ``VERDICT:``.
- ``roam preflight`` not-found case — was just ``"No symbols found
  for: X"``; now ``VERDICT: target not found — `X` is not in the
  index`` plus a ``Try `roam search X` …`` follow-up hint.
- ``roam why`` JSON envelope ``summary`` gained ``verdict``.
- ``roam search ""`` (empty pattern) now errors with ``EMPTY_INPUT``
  instead of returning the first 50 random symbols. Tests pinned the
  empty pattern to "matches everything", which was never the
  intent.

### Phase 3 — smarter verdicts that name the driver

Plain count summaries don't tell a user *what to fix first*. Three
high-traffic verdicts now name the dominant signal so the next
action is one read away:

- ``roam pr-risk`` — verdict appends ``(driver: hotspot_score)`` /
  ``(driver: test_coverage_low)`` / ``(driver: bus_factor)`` etc. The
  largest single risk factor maps directly to a fix.
- ``roam health`` — verdict appends ``focus: bottlenecks`` /
  ``focus: god_components`` / ``focus: cycles`` / ``focus:
  layer_violations`` based on which CRITICAL category dominates.

### Phase 4 — every command points at its natural follow-up

The ``next_steps.suggest_next_steps`` registry covered ``health``,
``context``, ``hotspots``, ``diagnose``, and ``dead``. Five more
commands now generate follow-up commands at the bottom of every
text run, so an agent finishing one ``roam`` call sees the next
``roam`` call to make:

- ``roam preflight`` — HIGH/CRITICAL → ``roam impact`` + ``roam diagnose`` + ``roam affected-tests``;
  LOW → ``roam diff`` after editing.
- ``roam impact`` — large blast → ``roam closure`` for the minimum
  coordinated change set; ``roam affected-tests`` for tests; ``roam
  preflight`` for the one-shot risk verdict.
- ``roam pr-risk`` — HIGH → ``roam diff --staged``; driver
  ``test_coverage_low`` → ``roam test-gaps --changed``; driver
  ``hotspot_score`` → ``roam hotspots``; otherwise → ``roam
  suggest-reviewers``.
- ``roam critique`` — HIGH severity → ``roam preflight`` on each
  finding; bench_hint set → run the named bench; otherwise → ``roam
  diff`` to confirm the structural delta.
- ``roam retrieve`` — low_confidence → "refine with ``--seed-files``"
  / ``roam search``; high-confidence → ``roam context`` on top
  result, ``roam preflight`` if planning to modify.

Each suggestion is scoped to the bare symbol name (the ``(file:line)``
suffix the resolver appends to ``label`` is now stripped before the
template fills) so the follow-up command is copy-pasteable.

## [12.12.7] - 2026-05-04

Phase-1.5: speed up agent search vs grep.

### Findings (measured on this 15K-symbol repo)

| Tool | Latency | Result quality |
|---|---|---|
| `grep` (POSIX) multi-shape | 200–2000 ms | raw text, false positives in comments / strings |
| `roam search` subprocess | 350 ms | symbols by name + PageRank rank |
| `roam uses` subprocess (warm) | 700 ms | direct dependents grouped by edge type, no false positives |
| `roam_uses` MCP tool (warm) | <100 ms | same as CLI but in-process |

ripgrep (Claude Code's `Grep` tool) is ~50–200 ms — 2–5× faster than
roam's CLI in raw wall-time. The win for `roam refs` / `roam_uses`
isn't speed — it's that the result is **already correct**. Multi-shape
grep needs follow-up filtering to drop comment / string-literal false
positives; the agent then has to read each match to learn the
structure. Going through the indexed call/import/inherit graph
returns one structured envelope with kind / file / line per consumer.

### Changes

- **CLI alias `roam refs`** for `roam uses`. Agents reaching for "find
  references to X" hit a grep-familiar name first; the same indexed
  lookup answers.
- **`roam_uses` MCP tool description rewritten** to explicitly steer
  agents away from multi-shape grep:
  > Use this *instead of* a multi-shape grep
  > (``"->X|\\.X\\b|'X'|\\"X\\""``) to find references — graph-precise,
  > no string-literal / comment false positives, and the result is
  > already structured by edge type.
- **Skill markdown adds a "Find every reference to X" section** that
  shows the multi-shape grep pattern an agent reaches for, why it
  produces noise, and the `roam refs` answer with measured latency.
- **README** points at `roam refs` from the `uses` row in the command
  reference table.

The recommendation is documentation-led, not a speed optimisation —
roam's CLI startup overhead (~250 ms python-process spawn) is the
floor, and shrinking it past the MCP-warm path isn't justified
relative to the 5–10 grep cycles a single `roam refs` call replaces.
For agents in MCP-enabled clients (Claude Code, Cursor, Codex CLI)
the latency gap closes entirely; the recommendation tells agents
in any client to prefer `roam refs` for reference-finding because
the *fewer iterations* dominate the latency comparison.

## [12.12.6] - 2026-05-04

Phase-1 deep-dogfood release. Live-fired roam against this very repo
to find edge cases that didn't show up in unit tests. Five real
correctness wins, all bench-positive.

### `roam retrieve` ranks implementations above tests

For implementation-style queries ("where is X", "find X", "how does X
work") the reranker now applies a -0.18 penalty to test files. The
test files weren't wrong — they had legitimate fan-in / PageRank from
every test importing the conftest fixtures — but for "where is X" the
user wants implementation, not the test. On the dogfood query
*"where is the patch verifier with clones-not-edited check"*:

```
Before:  #1 test_verify_patch_match (test)
         #2 critique_patch (MCP wrapper)
         #3 TestCheckClonesNotEdited (test class)
         #4 check_clones_not_edited       ← actual answer at #4
         #5 _patch_stub_backend (test)

After:   #1 critique_patch
         #2 check_clones_not_edited       ← lifted to #2
         #3-4 tests demoted
```

Bench (recall@K on `roam_self.jsonl`):

| Metric | v12.11 baseline | v12.12.6 | Δ |
|---|---|---|---|
| recall@5 | 0.664 | **0.700** | +3.6 pp |
| recall@10 | 0.758 | **0.786** | +2.8 pp |
| recall@20 | 0.900 | 0.878 | -2.2 pp |

The penalty was empirically tuned at -0.18 — stronger penalties (-0.25)
gave bigger top-5 gains but regressed recall@20 more. The bench
expects test files as co-answers for some "where is X" queries
(e.g. ``test_personalized_pagerank.py`` is listed alongside
``pagerank.py``); -0.18 keeps those in top-20 while still pushing
high-PR test fixtures below same-token implementations at top-5/10.

### Implementation queries down-weight structural, up-weight lexical

Same query family had a deeper issue. *"where is the symbol resolver"*
ranked ``_resolve_file`` (PR=0.99, fts=0.65) at #1 above the actual
``find_symbol`` (PR=0.16, fts=0.88) — PR was dominating because the
6× PR ratio overwhelmed the 1.35× fts ratio. For "where is X" queries
v12.12.6 now down-weights ``alpha`` (PR) by 30% and up-weights
``lexical_baseline`` by 20% within a single call. Navigation /
planning queries still use the structural-strong default.

### Tokenizer learns programming-domain shorthand

Two seed-token gaps caused several "where is X" queries to return
generic noise:

- **Programming shorthand.** ``n+1`` / ``i18n`` / ``2fa`` / ``a11y``
  fell through every regex. *"find n+1 query detection"* tokenised
  to ``["query", "detection"]`` and missed ``cmd_n1.py`` entirely.
  Now ``n+1`` emits ``n1`` as a token (matching the file name);
  the actual ``detect_django_n1`` ranks #1 instead of generic
  ``QueryEngine`` properties.
- **4-letter domain nouns.** The lowercase-noun fallback's ≥5-char
  floor dropped ``dead`` / ``code`` / ``file`` / ``role`` / ``path``
  / ``node`` / ``edge`` / ``view`` / ``task`` / ``flow`` etc.
  *"where is dead code detection"* tokenised to ``["detection"]``
  and the actual ``cmd_dead.py`` was nowhere in top-10. A curated
  allow-list of 50 programming-domain 4-letter words now restores
  these.

### `roam ask` extracts identifiers with leading underscore

The recipe-runner regex used ``\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+`` which
fails the word-boundary check before a leading ``_``. *"is it safe to
delete _resolve_file"* extracted no symbol, then passed the full
query string to ``roam uses`` as the symbol name — which produced
``"symbol not found: 'is it safe to delete _resolve_file'"``.
Regex now allows the leading underscore. The full safe-delete
recipe runs end-to-end on the dogfood query.

### `roam fitness` prepends a verdict line

Every other command in the surface starts its text output with
``VERDICT: …``; ``fitness`` skipped straight to the rule list and
left the user counting ``[FAIL]`` markers to know if the gate
passed. Now opens with e.g. ``VERDICT: 2 of 3 fitness rule(s) fail
(51 violation(s))`` so the bottom line is on the first line.

## [12.12.5] - 2026-05-04

A correctness sweep on the agent-orientation commands and a clarity
sweep on the `doctor` output. The big finding: the prior tour and
understand commands were ranking pytest fixtures at the top of "key
abstractions" / "key symbols", and a newcomer's reading order
started inside `tests/conftest.py`. The graph said it was correct
(those fixtures genuinely have huge fan-in) but it's exactly the
wrong shape for orientation.

### `roam tour` and `roam understand` orient in source code

Both commands now exclude symbols whose file is classified as
``test`` from the Key Symbols / Key Abstractions list, and the
reading-order / entry-points lists drop tests, dev scripts, generated
code, configs, examples, benchmarks, build output, CI, and docs —
keeping only ``source`` files (and a small extension of "where else
might a real entry point live"). On roam itself the change pulls
``cli_runner`` / ``indexed_project`` / ``project_factory`` /
``conftest.py`` out of the top-10 list and surfaces ``cli`` /
``open_db`` / ``json_envelope`` / ``ensure_index`` /
``LanguageExtractor`` instead.

### Generic-named property false positives

Tour also drops kind=property/field/attribute symbols whose name is
in a small list of generic names (``path``, ``name``, ``value``,
``key``, ``id``, …). These are name-collision artifacts in the
symbol resolver: every ``obj.path`` reference in unrelated code
resolves to the first class with a ``path`` property, inflating
that one symbol's in-degree to hundreds. ``WebhookBridge.path``
was the live example — fan-in 490 against a 3-line property because
the resolver couldn't tell which ``.path`` reference belonged to
which class.

### File-role classifier learns benchmark-shaped directories

``benchmarks/``, ``benchmark/``, ``bench/``, and ``bench-repos/``
now classify as ``examples`` (was ``source``). Without this fix the
new tour / understand filters wouldn't help on roam itself —
``benchmarks/agent-eval/prompts.py`` would still surface as the
"start file". Four new tests in ``test_file_roles.py`` cover the
new patterns.

### `roam doctor` clarity

- `tree-sitter` and `tree-sitter-language-pack` versions resolve
  through ``importlib.metadata`` instead of the dunder attribute.
  The dunder isn't set on those packages so the previous output
  was always "unknown" even on a healthy install.
- The MCP-tool-registry line now names the active preset and the
  full-preset ceiling: ``36 MCP tools registered (core preset; 122
  in full preset)`` instead of just ``36 MCP tools registered``.
  Without the preset context, users assumed something was broken
  when the docs claimed 122 tools.

### Tests

- ``test_tour_cmd.py::TestTourFiltering`` — synthetic project with
  one fat ``conftest.py`` and many tests; asserts no test/conftest
  symbol shows up in Key Symbols and the reading-order start file
  is in source.
- ``test_file_roles.py`` — four new cases for ``benchmarks/`` /
  ``benchmark/`` / ``bench/`` / ``bench-repos/``.

## [12.12.4] - 2026-05-04

Fix the MCP server card's tool count and add a guard test so it
can't drift again.

The card reports the server's capabilities to MCP-discovery surfaces
(PulseMCP, mcp.so, Smithery, …). Its ``capabilities.tools.total``
field had been at ``120`` (with ``presets.core: 33``) for several
releases while the live MCP server registered ``122`` tools and the
core preset had grown to ``35``. The card description correctly
quoted ``"122 MCP tools"`` in plain text but the structured number
clients actually parse was stale.

Updated both copies (``src/roam/mcp-server-card.json`` and the
canonical ``docs/site/.well-known/mcp-server-card.json``). Added
``test_card_tool_count_matches_live_count`` which compares the
card's tool count against ``surface_counts.collect_surface_counts``
so a future MCP-tool addition that forgets the card update fails
CI rather than ships silently.

## [12.12.3] - 2026-05-04

Documentation and MCP-surface polish caught while auditing the
v12.12.2 release.

### MCP wrappers in step with the CLI

- ``roam_taint`` now exposes ``rules_pack`` (the v12.3 CLI flag had
  never been added to the MCP wrapper). The accepted values mirror
  the CLI Choice list — sqli, xss, ssrf, path-traversal,
  command-injection, deserialization, open-redirect, urllib,
  socketio, fileupload.
- ``roam_critique`` accepts an ``intent`` string. When supplied, the
  intent-vs-semantic-diff check fires (a "rename" intent that
  produces non-rename changes flags as misalignment). Previously
  only available via the CLI's ``--intent``.
- ``roam_retrieve`` accepts ``dry_run`` so an agent can preview the
  search plan (candidate ids, scores, locations) without paying the
  span-content token cost. The docstring also points at the
  ``summary.low_confidence`` boolean exposed in v12.12.

### Doc-count drift

Stale CLI / MCP counts swept across user-facing surfaces:

- ``README.md`` — the architecture diagram, "what's where" table,
  and the historical bullet about command-and-MCP parity now all
  show 155 canonical CLI commands and 122 MCP tools.
- ``docs/site/index.html`` — landing-page surface line was 150 / 116.
- ``docs/site/landscape.html`` — competitor stat tiles were 153 / 112.
- ``docs/site/architecture.html`` — interface-block label was 139.
- ``docs/site/command-reference.html`` — the taint row was still
  describing the v12.0 5-pack starter list; updated to the 10-pack
  v12.3+ surface and switched the example to ``--rules-pack``.

### One stale xfail removed

``test_json_contracts.py`` had ``dead`` in the FRAGILE_COMMANDS set
(envelope was missing ``verdict`` on the minimal fixture). v12.x
added the verdict field; ``--runxfail`` confirms all four
parametrized tests pass on the minimal fixture. Removed from the
fragile set; the suite now reports 24 xfailed (was 28).

## [12.12.2] - 2026-05-04

Polish on top of v12.12.1's packaging hotfix. Two more files were
broken post-install plus a quietly-skipping consistency test.

### Bundled in the wheel

- `src/roam/mcp-server-card.json` — the MCP discovery card. Previously
  `roam mcp --card` walked up to `docs/site/.well-known/` which only
  exists in the source checkout, so PyPI users got "server card file
  not found". v12.12.2 ships the card alongside the package; the
  docs-site copy stays canonical for the hosted `/.well-known/` URL
  and a guard test asserts byte-for-byte equality so they can't drift.
- `src/roam/templates/ci/Jenkinsfile` — `roam ci-setup --platform jenkins`
  was raising "Template file not found" because the package-data glob
  only covered `*.yaml` / `*.yml`. The glob is now `*` for that dir.

### Consistency

- `test_landscape_json_self_row_version_matches` was silently skipping
  because `_landscape_json_version()` searched a 500-character window
  around `"cli_commands"` for `"version_evaluated"` — when the row
  grew past that window, the test returned None and pytest skipped.
  Switched to JSON-parse-and-find-by-name. The test runs again.
- `landscape.json` self-row had been at `12.10.1` since the v12.11
  release commit forgot to bump it (the test was skipping, so CI
  didn't catch it). Synced to `12.12.2`.

### cmd_ask uses the shared confidence helper

`cmd_ask` invented the low-confidence verdict pattern that v12.12
extracted into `roam.output.confidence`, but the original code
declared its own `_CONFIDENCE_THRESHOLD = 0.15` and inlined the score
comparison. Refactored to import `DEFAULT_CONFIDENCE_THRESHOLD` and
`is_low_confidence` from the shared module so threshold tweaks land
in one place across `cmd_ask`, `cmd_retrieve`, and any future
ranked-output command.

## [12.12.1] - 2026-05-04

Hotfix: bundle YAML data files in the wheel.

PyPI installs of `roam-code` from at least v8.x through v12.12 silently
shipped zero taint rules — the wheel didn't include
`roam/security/taint_rules/*.yaml` because no `package-data` entry
declared them. `roam taint` post-install on a clean venv reported
"No rules in /.../security/taint_rules" with no actionable hint.
Editable installs (``pip install -e .``) and source checkouts worked
because the YAMLs were on disk; the bug only bit binary-wheel users.

`pyproject.toml` now declares `[tool.setuptools.package-data]` for:

- `roam.security.taint_rules` — 14 rule packs (sqli, xss, ssrf,
  path-traversal, command-injection, deserialization, open-redirect,
  urllib, socketio, fileupload, plus js-prototype-pollution,
  js-insecure-jwt-decode, js-localstorage-secrets, js-api-error-leak,
  vue-template-injection).
- `roam.languages.extractors` — Kotlin YAML extractor.
- `roam.templates.ci` — Azure / Bitbucket / GitLab CI templates.

Verified via clean-venv install of the rebuilt wheel: `load_rules`
returns 14 rules including `python-deserialization`.

## [12.12] - 2026-05-04

A focused close-out of the v12.3 dogfood report's five remaining open
items. No new commands; this release tightens precision in the
`retrieve` expansion path, restores signal in `diagnose`'s churn
column, makes `critique`'s bench-hint discoverable from JSON / MCP
clients, ships the missing taint pack, and centralises the
low-confidence verdict pattern so it grows uniformly across commands.

### Bug fixes — close v12.3 dogfood backlog

- **`roam retrieve` hub-neighbour leak (#8)** — the v12.3 fix gated
  hub *seeds* but expansion still leaked when a non-hub seed
  legitimately imported a utility hub (e.g. `cmd_critique.py` →
  `output/formatter.py`). v12.12 applies the hub-degree filter
  symmetrically to neighbours, so utility imports are dropped from
  the expanded candidate set without disturbing legitimate
  cross-module expansion.
- **`roam diagnose` Commits column always 0 (#11)** — the column
  reads `file_stats.commit_count`, which is populated by
  `compute_file_stats` during a full re-index but lags behind
  incremental runs. v12.12 falls back to a direct count over
  `git_file_changes` whenever the cached stat is 0, so freshly-
  modified files report real churn again and the risk-score's
  churn dimension stops silently zeroing out.
- **`roam taint --rules-pack deserialization` filtered to zero rules
  (#18)** — the pack was advertised in the `Choice` list but no
  rule_id contained "deserialization". v12.12 ships
  `python_deserialization.yaml` covering pickle / yaml.load / marshal
  / shelve / dill sources → safe-loaders sanitisers. A new test
  asserts every advertised pack matches at least one rule.
- **`roam critique` bench hint missing from JSON envelope (#15)** —
  `_bench_relevance_hint` shipped in v12.10 but text-only. MCP
  clients couldn't see it. v12.12 emits `bench_hint` in both the
  top-level envelope and `summary` so agents can branch on it.

### New surfaces

- **`.roam-critique.yml`** — project-local override for the
  `_bench_relevance_hint` rule list. Format::

      bench_hints:
        - paths: ["src/foo/", "src/bar/"]
          hint: "pytest tests/test_foo.py"

  Overrides are searched before the built-in rules so projects can
  shadow defaults. Closes the second half of dogfood #15
  ("generalises to other projects via a `.roam-critique.yml`").
- **`roam.output.confidence`** — shared low-confidence verdict
  helper (`verdict_prefix`, `format_no_match`, `is_low_confidence`).
  Lifts the pattern from `cmd_ask` so `roam retrieve` and future
  ranked-output commands surface one consistent shape. `cmd_retrieve`
  now uses it; the JSON summary additionally exposes
  `low_confidence: bool` so MCP clients don't have to parse the
  verdict string. Closes dogfood #7's follow-up
  ("lift this pattern into a shared helper").

### Tests

- `test_retrieve.py::TestHubNeighbourFilter` — two tests
  (hub-neighbour rejection, low-degree neighbour preserved).
- `test_retrieve.py::TestSharedConfidenceHelper` — pure helpers +
  JSON `low_confidence` field exposure.
- `test_critique.py::TestBenchHint` — six tests covering default
  rules, override loading, override-takes-precedence, missing
  YAML, and JSON-envelope inclusion.
- `test_taint.py::TestTaintCLI::test_every_advertised_pack_has_at_least_one_rule`
  + `test_deserialization_pack_loads` + `test_rules_pack_choice_advertised_in_help`.
- `test_commands_workflow.py::TestDiagnose::test_commits_falls_back_to_git_file_changes`.

### Pre-existing failures cleared during the close-out

The dogfood-cleanup test sweep surfaced four unrelated failures
that had been silently red on `main`. Each turned out to be a
small, localized issue rather than a deep regression, so they're
fixed alongside the v12.12 close-out:

- **`roam map --budget N`** ignored the value because the global
  parser moved `--budget` to the group context, leaving the
  command-local parameter `None`. `cmd_map` now consults the
  global value as a fallback. (`test_v6_features.py::TestV6MapBudget`.)
- **`roam fingerprint --compact`** had the same parser-collision
  shape; `cmd_fingerprint` now reads `ctx.obj["compact"]` when its
  own option is `False`. (`test_fingerprint.py::TestCLIFingerprint::test_cli_fingerprint_compact`.)
- **Kotlin inheritance fixture** declared `LoggingPrinter.log` as
  `function` with `scope: LoggingPrinter` — internally inconsistent.
  Methods inside class bodies are `kind: method`. Fixed the fixture.
  (`test_language_corpus.py::TestKotlinCorpus::test_inheritance`.)
- **Version-format consistency** — the cross-file regex required
  three segments and was failing on `12.11` (the prior release
  switched to two-segment versions). Relaxed the check to accept
  both shapes; consistency across `pyproject.toml` / `server.json` /
  `mcp-server-card.json` is now restored.

## [12.11] - 2026-05-04

A precision and agent-UX release built on six rounds of dogfood
feedback. Headline work: round-trip false-positive suppression across
the entire analyzer surface, a cross-tool framework-alias filter that
single-handedly fixes five inflated-PageRank reports, MCP capacity
backpressure that replaces silent connection drops with structured
`RATE_LIMITED` responses, and a tri-state oracle envelope so agents
can distinguish "we proved no" from "we can't tell."

### New modules

- `roam.output.framework_filter` — shared registry of Vue / React /
  Angular type aliases and lifecycle hooks. Consumed by `fan`,
  `health`, `tour`, `understand`, and `visualize` so `computed<T>`
  and friends stop dominating PageRank rankings.
- `roam.output.project_shape` — one detector returning team_size,
  test_runner, build_tool, polyglot, frontend / backend flags.
  Powers bus-factor's single-author mode, describe's runner-aware
  test command, and preflight's vitest detection.
- `roam.output.errors` — canonical error code taxonomy
  (`EMPTY_INPUT` / `INVALID_DIFF` / `UNKNOWN_RECIPE` /
  `RATE_LIMITED` / …) with `structured_usage_error()` helper and
  `parse_code()` round-trip validator. Applied to every high-traffic
  CLI `UsageError` site so agents can branch programmatically.
- `roam.mcp_extras.concurrency` — bounded-semaphore backpressure on
  every MCP tool. Default 8 in flight (env
  `ROAM_MCP_MAX_CONCURRENT`) plus per-tool overrides
  (`ROAM_MCP_LIMITS=JSON`). Over-capacity returns a structured
  `RATE_LIMITED` envelope with retry hint instead of dropping the
  connection.

### Precision (false-positive suppression)

- `dead` / `uses` split production vs test consumers; tested-but-
  unused surface lands as `REVIEW` with explicit reason. Decay
  distribution (fresh / stale / decayed / fossilized) ships in the
  default summary.
- `dead` recognises a scaffolding heuristic (CB-NNN behaviour IDs,
  legacy file references, "see legacy/spec" citations) and tags
  `INTENTIONAL_SCAFFOLDING`. `--reachable-only` intersects with the
  is-reachable oracle for the really-really-dead set.
- `dead --by-directory` adds file count, dead-export density, and
  scaffolding column. Barrel-export importers
  (`index.ts` / `__init__.py`) are split from real consumers in the
  reason text.
- `health` filters local-only and test-involved cycles from scoring,
  tangle, and gates. Severity breakdown by category in default
  output.
- `vibe-check` — `Promise.catch(() => fallback)` no longer counts as
  an empty handler. `pr-risk` deletion-only changes get a reductive
  rubric. `complexity` gains `--no-framework` / `--no-imports`.
- `fan` splits intra-file vs inter-file fan-out; reserves
  `hub` / `spreader` for symbols whose consumers span ≥3 files.
- `coupling` detects locale-pair (`src/locales/<lang>.ts`) and
  doc-hub patterns and labels them `EXPECTED` instead of `HIDDEN`.
  Default `-n` auto-scales by file count (20 / 50 / 100).
- `conventions` applies per-language rules (SQL = snake_case for
  tables / views, JS = camelCase, etc.) instead of imposing the
  codebase-wide dominant style.
- `fn-coupling` caps symbols-per-file via PageRank and excludes
  tests by default — drops 2.2M pairs to thousands. New `--since
  <ref>` baseline mode.
- `risk` excludes tests by default (`--include-tests` opt-in),
  surfaces a `--show-suppressed` inspector, and surfaces a
  `suppressions` envelope field for honest filter accounting.
- `fitness` cycle metrics consume the actionable-cycle filter
  (filtering local + test SCCs); preflight scopes rule failures
  to the target's surface (`rules_failing_on_target` vs
  `rules_failing_on_siblings`) and uses "currently fail" rather
  than misleading "would fail" wording.
- `patterns` factory detection splits `true_factory` vs
  `builder_helper` into separate sections in default text output;
  `--strict-factory` drops helpers entirely.
- `hotspots` tags each entry with `kind` (code | doc | config |
  sql | other) and prefers code in the headline rankings.
- `doc-staleness` switched to semantic mismatch (phantom params,
  return clause without return annotation). Pure-prose drift is
  gated behind `--include-prose-drift`.
- `safe-delete` no longer flips `SAFE` → `REVIEW` purely on
  `use*` / `get*` naming when every signal is zero.

### Agent UX

- Tri-state oracle envelope: `value: bool | null` plus
  `reason_class` (`definitive_yes` / `definitive_no` /
  `indeterminate_workspace` / `indeterminate_no_data` /
  `unreachable_dead` / `unreachable_scaffolding` / …) and
  `confidence`. `route-exists` returns `indeterminate_workspace`
  with sibling-backend candidates when `roam ws resolve` would
  help; `is-reachable` distinguishes `unreachable_dead` from
  `unreachable_scaffolding`.
- New `roam_oracle_batch` MCP tool: multiple oracle queries in
  one round-trip with full tri-state envelopes per result.
  `roam_oracle_test_only` alias added so the shorter name agents
  sometimes guess no longer 404s.
- Bundle aggregator surfaces `partial_success` +
  `failed_subcommands` at the top of every compound envelope;
  `prepare-change` recipe scorer picks `refactor-orchestrator`
  vs `safe-delete-check` by signal vector instead of always
  defaulting to delete.
- `find_symbol_with_alternatives` returns ranked `did_you_mean`
  for ambiguous queries; `pick_best` uses PageRank + cognitive
  complexity + churn fallbacks when incoming-edge counts tie at
  zero.
- Vue / Svelte SFC component name indexing — `roam why
  MyDataManagementModal` resolves the component by filename.
- Dynamic JS / TS imports (`import('@/foo').then(m => m.bar)` and
  `await import('./mod')` member access) now produce consumer
  references; relative `../src/...` imports match by suffix.
- `roam describe` / `preflight` read `package.json` `scripts.test`
  for the test command instead of hardcoding `pytest`.
- Stale-index warning is a top-level `index_status` envelope
  field and prints **before** the verdict in text mode for
  `diagnose` and `health`.
- MCP startup hint promotes `roam_expand_toolset` and
  `roam_batch_get` so agents discover tool-scoping and batched
  paths up front. `roam_impact` description recasts it as the
  FIRST safety check.
- `roam test-map` reconciles "no direct tests" with "test files
  importing the same module" so the verdict can't contradict
  its own data.

### Configuration

- `.roam/alerts.yaml` — configurable health / cycles / god-component
  thresholds plus delta-vs-baseline mode that emits regression
  warnings when a snapshot exists.
- `ROAM_MCP_MAX_CONCURRENT` / `ROAM_MCP_LIMITS` env vars tune the
  backpressure caps.

### New flags / commands

- `dead --reachable-only` / `--include-noisy-dataflow` (alias of
  the experimental `--dataflow`).
- `clusters --weak` / `--strong` — split / merge candidates ranked
  by intra-cluster density.
- `fn-coupling --since <ref>` / `--include-tests` /
  `--max-files-per-commit` / `--max-symbols-per-file`.
- `retrieve --dry-run` — return the search plan without paying
  the token cost.
- `risk --include-tests` / `--show-suppressed`.
- `complexity --no-framework` / `--no-imports`.
- `bus-factor --force-team-mode`.
- `patterns --strict-factory`.
- `health --compact` accepted *after* the subcommand.
- `roam trend` / `roam digest` / `roam snapshot` — aliases of
  the consolidated `roam trends`.

### Doctor

- New checks: every CLI command in the registry imports cleanly,
  the MCP tool registry registers without errors, and the MCP
  backpressure module loaded with a positive limit. Catches the
  "documented but missing" / "renamed without alias" class of
  bug at doctor time rather than at agent call-time.

### Security rules

- Five new YAML taint rules for Vue / TS codebases:
  `js-prototype-pollution`, `js-localstorage-secrets`,
  `vue-template-injection` (v-html), `js-insecure-jwt-decode`,
  `js-api-error-leak`.
- `roam taint --json` only ships the OpenVEX vocabulary lists
  when there are findings to attach them to (cuts ~2 KB of
  metadata noise per empty run).
- `roam_taint_classify` short-circuits when the static engine
  returns zero findings — no wasted LLM sampling tokens.

### Surface

- 155 CLI commands · 122 MCP tools · 27 languages · 100% local · zero API keys.

## [12.10.1] - 2026-05-04

A patch release for the `12.10.0` workflow-synergy release.

### Fixed

- Restored Python 3.9 import compatibility in taint analysis by avoiding a
  runtime PEP 604 union inside the `TaintOrigin` type alias.
- Applied Ruff formatting to the release files so the CI format gate matches
  the repository style.

## [12.10.0] - 2026-05-03

A workflow-synergy and maintainability release. Headline work:
semantic retrieval is now truthfully diagnosable, recipe workflows
advertise gates and follow-up commands, existing fitness debt can be
baselined without hiding new regressions, and several high-complexity
indexing/analysis hotspots were split into focused helpers.

### Added — workflow intelligence

- **`roam workflow`** — no-run recipe inspector for the `ask` recipe
  DAG, review lenses, gates, rendered command arguments, and follow-up
  commands.
- **Recipe gates** — every `ask` recipe now carries phase,
  perspectives, gates, and follow-ups; the metadata is exposed through
  `ask`, `workflow`, MCP compound envelopes, report presets, and JSON
  tests.

### Added — semantic activation diagnostics

- **`roam config --semantic-status`** — reports dense embedding
  coverage, ONNX dependency readiness, configured model/tokenizer
  readiness, and concrete next actions.
- **`semantic_coverage`** — retrieve JSON summaries now report how
  many indexed symbols have dense vectors, making `zeta` weighting
  observable instead of silent.

### Added — local operability and fitness baselines

- **`roam config --use-local-cache`** — persists a deterministic
  per-project user-cache DB path for developer machines and
  cloud-synced workspaces.
- **`roam fitness --write-baseline` / `--baseline PATH`** — captures
  existing fitness violations and exits non-zero only for new
  violations when comparing against a baseline.

### Fixed

- Empty ONNX model/tokenizer settings no longer appear ready because
  `Path("")` resolves to the current directory.
- Windows/cloud-sync index locks are reused safely when stale lock
  files can be overwritten but not deleted.
- The `architecture-debt` recipe follow-up now points at the existing
  `roam health --json` command instead of a nonexistent cycles
  command.

### Refactored

- Index orchestration and file processing were split into focused
  pipeline phases.
- Taint tracking and math-signal extraction now use explicit state
  objects and small recording helpers.
- Fitness metric checks, baseline comparison, and output rendering are
  separated so the new baseline feature does not add command-level
  complexity.

### Surface

- 155 CLI commands · 120 MCP tools · 27 languages · 100% local · zero API keys.

## [12.9.0] - 2026-05-02

A precision and graph-completeness release. Headline work: a
registry-dispatch resolver that closes a long-standing gap in
``roam impact``, three new Flask detectors, four new taint rule
packs, intraprocedural taint propagation, an ``ask`` recipe for
pytest fixtures, and a +11pp jump in roam's own type coverage.

### Added — graph completeness

- **Registry-dispatch edges** — new post-indexing pass synthesises
  ``dispatch`` edges from string-keyed dispatch dicts
  (``_COMMANDS = {"name": ("module.path", "fn_name")}``) and from
  list-of-tuples-with-function-references registries
  (``_DETECTORS = [("slug", "way", detect_fn), ...]``). ``roam impact
  preflight`` now reports a small blast radius instead of
  "no dependents — safe to change". Same for every Python idiom
  detector that lives in the registry.

### Added — Flask framework detectors

- **``py-flask-routes``** — info-level inventory of
  ``@app.route`` / ``@blueprint.route`` decorators. Surfaces the URL
  surface so agents can discover routes without grep.
- **``py-flask-debug-true``** — high-severity catch for
  ``app.run(debug=True)``. The Werkzeug debugger leaks a Python REPL
  to anyone who can reach the host.
- **``py-flask-secret-key-literal``** — high-severity catch for a
  literal SECRET_KEY in source. ``os.environ`` reads are skipped by
  construction.

Total Python idiom detector count: 22.

### Added — CodeQL-style taint rule packs

- **``python-urllib-open-redirect``** — request.* sources flowing into
  flask.redirect / HttpResponseRedirect / httpx.get / urllib.urlopen.
- **``python-socketio-remote-input``** — sio.on / @socketio.event
  payloads as remote sources, sinks are SQL/shell/file family.
- **``java-fileupload-path-traversal``** — FileItem.getName /
  Part.getSubmittedFileName flowing into Files.write / Paths.get.

Mirror the dataflow models CodeQL 2.24 (Jan 2026) added.

### Added — taint engine

- **Intraprocedural co-call detection** — second BFS pass flags
  functions that *call both* a source and a sink, even when no
  forward edge connects them. Catches the
  ``y = source(); sink(y)`` shape that pure forward BFS misses.
  Mirrors Semgrep's Feb 2026 assignment-propagation improvement.

### Added — agent ergonomics

- **``ask`` recipe ``fixture-impact``** — natural-language queries
  like "what depends on cli_runner" or "blast radius of
  indexed_project" route to ``roam pytest-fixtures --reverse``.
  Recipe count up to 13.
- **``roam drift --by-team``** — per-owner ownership-realisation
  table (% of each team's declared files where the declared owner is
  also the de-facto top contributor). Closes the CodeScene
  team-autonomy parity gap.

### Fixed — detector precision

- **``py-except-pass`` narrows on legitimate cases** — no longer
  fires on ``except OSError: pass``, ``except ImportError: pass``,
  or tuples of OS / parse / optional-import errors. The remaining
  list focuses on ``except:`` / ``except Exception:`` / custom error
  classes where silent ``pass`` genuinely deserves attention.
- **Three N+1 query fixes in roam itself**: compute_partition_manifest
  pre-loads file_stats once, _analyze_dataflow_dead batches caller
  scans, build_aibom_block batches ``git show`` calls.

### Fixed — type-coverage detector bug

- **py-types decorator-anchor** — the detector was reading the FIRST
  ``(...)`` in the stored signature, which on ``@_tool(name="...")``
  decorated functions captured the decorator args, not the def's
  params. 121 mcp_server.py functions reported as 0% typed despite
  full annotations. Anchored on ``def NAME(`` for both param and
  return scan.

### Refactored

- Eight ``test_*_file_skeleton`` per-language test classes collapsed
  into one parametrised case table — adding a new language is now
  one row, not a fresh class.

### Improved

- **Type coverage**: 48% → 59% on roam's own 1118 public functions.
  Bug fix above contributed ~6pp; targeted annotations on
  db/connection.py, commands/resolve.py, catalog/smells.py,
  catalog/detectors.py, commands/context_helpers.py,
  search/index_embeddings.py, and commands/cmd_ws.py contributed
  the rest.
- **``roam --sarif taint``** wires through cleanly; was silently
  swallowed in v12.8.
- **``F841`` (unused-variable) re-enabled in ruff** — future dead
  vars fail CI rather than rotting silently.

### Surface

- 154 CLI commands · 120 MCP tools · 27 languages · 100% local · zero API keys.

## [12.8.0] - 2026-05-02

A documentation, positioning, and trust-scaffold release plus two new
commands (``pytest-fixtures`` and ``hover``), tighter ORM detector
precision, full SARIF coverage, and a documentation-drift CI check.

### Added — ``roam hover``

- **``roam hover SYMBOL``** — single-line architectural summary
  bounded at ~200 tokens: kind, qualified name, file:line,
  blast-radius bucket, top caller, top callee. Designed for IDE
  hover plugins and chat-inline references where ``roam context`` is
  too verbose.
- **``roam_hover``** MCP tool wraps it for agents.
- **``ask`` recipe** ``fixture-impact`` — natural-language queries
  like "what depends on cli_runner" route to
  ``roam pytest-fixtures --reverse``.

### Added — SARIF for taint analysis

- **``roam --sarif taint``** emits SARIF 2.1.0 with one rule per
  taint rule_id (e.g. ``python-sqli``, ``js-xss``) and a SARIF
  code-flow describing each source-to-sink path. Sanitized findings
  are downgraded to ``note`` level so they don't fail a CI gate but
  still surface for OpenVEX-style remediation tracking.

### Added — pytest fixture dependency edges

- **``roam pytest-fixtures [SYMBOL]``** — inventory pytest fixture
  chains. With no SYMBOL, prints the project-wide fixture count and
  the top fixtures by dependent count (a blast-radius proxy). With a
  fixture or ``test_*`` function, walks the implicit fixture-parameter
  dependency graph to show what each test transitively requires.
  Resolves through ``conftest.py`` chains the way pytest itself does.
- **``--unused`` flag** — list fixtures with no dependents (orphaned
  test infrastructure left behind by refactors).
- **``--reverse`` flag** — walks the inverse edges: "if I rename
  fixture X, what tests break?". Output is capped at 30 lines for
  hot fixtures used by hundreds of tests; ``--json`` returns the
  full list.
- **Scope and autouse parsing** — fixture chain output annotates each
  node with ``[scope=session, autouse]`` badges so agents can reason
  about test isolation. The root fixture's own scope/autouse appears
  in the verdict line.
- **Resolved file:line displayed** — when a fixture name is ambiguous
  across files (e.g. multiple ``cli_runner`` definitions), the output
  prints which one the resolver picked.
- **``edges.kind = 'pytest_fixture_dep'``** — new edge type. A pytest
  fixture's parameters are themselves fixtures, but that relationship
  is invisible to call-graph or import analysis. Indexing now derives
  it as a post-processing step. Edges flow through the existing graph
  builder, so ``roam impact`` and ``roam preflight`` automatically
  include transitively-affected tests in their blast radius.
- **``roam_pytest_fixtures``** MCP tool, surfaced under the
  ``refactor`` and ``debug`` presets.

### Added — per-detector precision audit

- **``tests/fixtures/detector_eval/``** — small labelled corpus per
  Python detector with true-positive and true-negative cases plus an
  ``expected.json`` ground truth.
- **``tests/test_detector_precision.py``** — runner that indexes each
  corpus, executes the detector, and asserts precision and recall
  against per-detector floors so they cannot regress in CI.
- **Initial baselines** — recall 1.0 across the board; precision 1.0
  for ``py-django-n1``, ``py-sqlalchemy-lazy``, and
  ``py-fastapi-depends`` after the next two fixes. Numbers published
  on ``docs/site/language-precision.md``.

### Fixed — false-positive classes in ORM detectors

- **``py-django-n1``** no longer fires on
  ``Model.objects.select_related(...).all()`` followed by a for-loop.
  The detector now scans the queryset chain back from the ``.all()``
  call for ``select_related(`` or ``prefetch_related(`` and skips
  when found.
- **``py-sqlalchemy-lazy``** no longer fires on queries that already
  eager-load via ``joinedload``, ``selectinload``, ``contains_eager``,
  or ``subqueryload`` in their ``.options(...)`` chain.
- **``py-django-n1`` Django context check** — the detector now
  requires a Django ORM hint somewhere in the file (``.objects.``,
  ``from django``, ``import django``) before firing. Previously a
  custom collection class with ``.all()`` could trip the all-then-for
  branch.

### Fixed — minor

- **``roam tour`` starting-file language** — the verdict line now
  reports the language of the actual starting file rather than the
  project's dominant language (so a YAML-heavy repo that starts at a
  Python file no longer gets labelled ``(yaml)``).
- **Symbol resolution canonical-path bias** — when the same name is
  defined in both ``src/`` (canonical library) and ``dev/`` /
  ``scripts/`` / ``tests/`` (helper scripts), call resolution now
  prefers the ``src/`` definition. Previously a dev/ helper script
  with its own ``open_db`` could shadow the canonical
  ``src/roam/db/connection.py:open_db`` and pull every call edge in
  the codebase. Same path bias added to ``find_symbol`` for
  command-time disambiguation.
- **SBOM pyproject parser** — strip TOML comments line-by-line before
  the quote-extraction regex. An apostrophe in an English comment
  (``# when these aren't installed``) was opening a fake quoted
  string and emitting a phantom ``t installed.`` dependency.

### Cleaned — dead variables

- Swept five unused locals out of ``cmd_search``, ``cmd_ws``,
  ``progress``, and several test helpers. Mostly leftovers from prior
  refactors. ``cmd_taint`` reads ``--sarif`` but never emits SARIF —
  tracked as a separate follow-up.

### Improved — preflight ergonomics

- **Suggested-tests cap** — ``roam preflight`` now caps the
  ``Suggested tests:`` line at 15 test files with a ``# (+N more)``
  suffix. Previously, preflight'ing a hot pytest fixture (one used by
  hundreds of tests) dumped every test file in the repo into the
  suggestion line.

### Added — documentation consistency CI check

- **`tests/test_doc_consistency.py`** — cross-surface consistency
  check. Asserts version + CLI command count + MCP tool count agree
  across `pyproject.toml`, `server.json`,
  `docs/site/.well-known/mcp-server-card.json`, `README.md`,
  `llms-install.md`, and `docs/site/data/landscape.json`. Optional
  surfaces (project-local files, missing fields) skip cleanly. Caught
  a real drift on first run: the docs-site landscape entry was
  reporting an older version and command count than the published
  package.

### Added — internal-link audit in doc-consistency check

- The doc-consistency test now verifies every ``docs/site/*.{md,html}``
  link in README and CHANGELOG resolves to an existing file. Catches
  silent rot when a docs page is renamed or removed.

### Added — public docs pages

- **`docs/site/benchmarks.md`** — Accuracy & Benchmarks page.
  Self-bench: recall@5 / @10 / @20 = 0.656 / 0.769 / 0.900.
  Cross-repo synthetic, detector E2E and scale findings, and an
  explicit "what's not yet measured" section. Links the
  CodeRAG-Bench-portable JSONL the harness already emits.
- **`docs/site/comparisons.md`** — concise "roam vs X" pages
  (Cursor, Sourcegraph/Cody, CKB/CodeMCP, Aider repo map, CodeQL,
  Semgrep, SonarQube, CodeScene, Codebase-Memory, Claude Context).
  Complement-not-replace positioning. Each section names what each
  tool wins and when to use both.
- **`docs/site/language-precision.md`** — per-language precision
  matrix for the Tier 1 languages: what's solid, what's heuristic,
  what's not extracted, and known false-positive / false-negative
  classes per detector. Replaces the "27 languages" headline number
  with information a reader can act on.

### Added — SARIF output for Python detectors

- **`roam --sarif py-types`** emits SARIF 2.1.0 with rule
  `py-types/coverage` (one result per file with missing annotations).
- **`roam --sarif py-modern`** emits SARIF with rules
  `py-modern/legacy-typing` and `py-modern/dot-format`.
- Both integrate with GitHub Code Scanning. Note that ``--sarif`` is
  a global Roam flag (placed before the subcommand), matching the
  existing convention used by ``--sarif health`` and ``--sarif debt``.

### Improved — README hero

- **New tagline**: "Architectural sight for AI coding agents — before
  they edit." Adopted across README, `server.json`, and
  `mcp-server-card.json`.
- **5-verb framing**: README now leads with the 5 high-leverage
  commands (`understand`, `retrieve`, `context`, `preflight`,
  `critique`) followed by a one-line note that the other 147 are
  advanced surface for specialised workflows.
- **First-run section**: a 4-line agent workflow at the top of the
  README so a reader can copy and run.

### Improved — agent ergonomics

- **`roam py-types` empty state** diagnoses why it's empty (no
  Python files indexed vs. all symbols filtered as tests) and points
  at the appropriate next step.

### Verification

- 240+ focused tests pass on the affected paths.
- Bench preserved: recall@5 0.656, recall@10 0.769, recall@20 0.900.
- All CI jobs green on the release commit.

## [12.7.1] - 2026-05-02

### Performance

- **Cache `_file_text` + `_strip_strings_and_comments`** in detector
  pipeline. With 19 detectors each calling these per file, the
  uncached path did N×19 disk reads + N×many strip passes. Now
  per-process LRU caches (4096-entry cap). On a 17k-file repo,
  ``roam math`` warm-cache improved from 0.43s → 0.37s.

## [12.7.0] - 2026-05-02

A 10-round push past v12.6: 7 new idiom detectors (now 19 total),
Pydantic/dataclass field display in ``roam context``, framework-aware
N+1 detection, async-call-graph propagation.

### Added

- **Model-class field display** in ``roam context``. When the symbol
  is a Pydantic / dataclass / attrs / TypedDict / NamedTuple class,
  shows the ``Fields (N):`` block with each field's name and default.
  Agents working with data models see the schema without scanning
  source.
- **7 new idiom detectors** (catalog now 19):
  - ``py-sync-calls-async`` — graph-based: sync function calls async
    function via ``edges.kind='call'`` table. Stronger evidence than
    regex-based ``py-async-not-awaited``.
  - ``py-django-n1`` — Django ORM N+1: ``.objects.filter()`` /
    ``.get()`` inside loop, ``.all()`` then iterate.
  - ``py-sqlalchemy-lazy`` — SQLAlchemy ``.all()`` then attribute
    access (lazy-load N+1).
  - ``py-fastapi-depends`` — inventories FastAPI ``Depends(X)``
    chains. Info-level (not anti-pattern) so agents discover the
    dependency graph.
  - ``py-lambda-in-loop`` — late-binding closure (lambda captures
    loop variable by reference).
  - ``py-except-pass`` — ``except X: pass`` silently swallows.
  - ``py-broad-except`` — ``except Exception:`` catches too much.

### Verification

- Bench held: recall@5 0.656, recall@10 0.769, recall@20 0.900.
- Lint + format clean.
- 19 detectors registered, no regressions on prior detectors.

## [12.6.0] - 2026-05-02

A 10-round push past v12.5: ``roam py-modern`` for modern-Python
adoption signal, ``roam py-types --ci`` CI gate mode, MCP wrappers,
12th idiom detector (lock-without-with), comprehensive end-to-end
detector test fixture.

### Added

- **`roam py-modern`** — modern-Python adoption signal: walrus
  operator (PEP 572), match statements (PEP 634), PEP 604
  (``X | None``), PEP 585 (``dict[…]``), PEP 695 type aliases,
  f-strings vs ``.format()``. Reports ``type-modernisation %``
  and ``f-string adoption %`` to gauge migration progress.
  ``--detail`` for per-file breakdown. Counterpart to
  ``roam py-types`` which scores annotation coverage.
- **`roam py-types --ci --min-coverage N`** CI gate mode. Exits 5
  (mirrors ``EXIT_GATE_FAILURE``) when coverage falls below the
  threshold. Use in CI to enforce a typing floor.
- **12th idiom detector**: ``py-lock-without-with`` —
  ``threading.Lock.acquire()`` outside ``with``-block (lock leak
  on exception path).
- **Two new MCP tools**: ``roam_py_types`` and ``roam_py_modern``.
  Both registered in the ``core`` preset (now 35 tools, was 33).

### Improved

- **Detector portability**: ``_file_text`` resolves project-relative
  paths against the DB's parent directory instead of cwd. Detectors
  now work correctly when invoked from outside the project root
  (caught by the new E2E test fixture). ``_project_root_for_conn``
  helper added.
- **Comprehensive E2E detector tests**:
  ``tests/test_python_idioms_e2e.py`` — single fixture project with
  one example of every anti-pattern + 11 OK counter-examples.
  Each detector verified to find the right line ±2.

### Verification

- 540 focused tests pass.
- 12 E2E detector tests pass.
- All 7 CI jobs verified green.
- Bench held: recall@5 0.656, recall@10 0.769, recall@20 0.900.
- 35 MCP tools (was 33), 152 CLI commands (was 151).

## [12.5.0] - 2026-05-02

A Python-pivot iteration release. v12.4 added the substrate
(``is_async`` + decorators on symbols, generated-dir exclusion, 4
idiom detectors). v12.5 doubles the idiom catalog, adds a new
``roam py-types`` command, and ships agent-facing badges for
Pydantic / dataclass / pytest fixture / parametrize.

### Added

- **`roam py-types`** — type-annotation health command for Python
  projects. Reports % of public functions fully typed, ``Any`` usage,
  legacy ``typing.Optional/Dict/List`` (PEP 585/604 modernisation
  candidates), per-file worst offenders. Default-excludes test files
  (``--include-tests`` opts in).
- **7 new Python idiom detectors** in `roam math`:
  - `py-sync-in-async` — ``requests.get`` / ``time.sleep`` /
    ``subprocess.run`` / ``urllib.request.urlopen`` / ``socket.recv``
    inside ``async def``. Real production bug class.
  - `py-open-without-with` — ``open(...)`` outside ``with`` block —
    file resource leak. Surfaced 3 real leaks in roam-code itself
    (fixed in this release).
  - `py-star-import` — ``from X import *`` namespace pollution.
  - `py-dict-keys-iter` — ``for k in d.keys():`` redundant.
  - `py-async-not-awaited` — call to known async function without
    ``await``. Returns a coroutine that never runs.
  - `py-async-with-missing` — ``aiofiles.open(...)`` /
    ``httpx.AsyncClient()`` not entered with ``async with``.
  - `py-type-eq` — ``type(x) == X`` should be ``isinstance(x, X)``.
- **Pydantic/dataclass/attrs/msgspec/Enum/TypedDict/NamedTuple model
  badge** in ``roam context``. ``[pydantic model]`` / ``[dataclass
  model]`` / ``[enum model]`` etc. surfaces above the signature so
  agents reading context immediately know the class shape. Found
  14 pydantic + 31 dataclass + 1 enum in deep-research.
- **`@pytest.fixture` / `@pytest.mark.parametrize` /
  `@pytest.mark.asyncio` badge** in ``roam context``. ``[pytest
  fixture]`` / ``[parametrize]`` / ``[async test]``.
- **`roam search --fixtures-only`** flag — shortcut for
  ``--decorator pytest.fixture``.
- **Async-aware retrieve boost** — when query mentions
  async/await/coroutine/asyncio/aiohttp/httpx, boost
  ``is_async=True`` candidates in rerank.
- **`has_decorator()` / `fixture_kind()` / `is_model_class()`** —
  new helpers in ``catalog/python_idioms`` for decorator-aware
  symbol introspection.
- **`_strip_strings_and_comments()`** — length-preserving stripper
  used by all idiom detectors so they don't false-match in
  docstrings/comments. Caught 5 false positives on roam-code itself.

### Improved

- **`roam search` filters now in SQL.** v12.4 ``--async`` /
  ``--decorator`` flags Python-post-filtered after a LIMIT 50 query,
  so rare-shape symbols got stripped before the filter ran. Now
  pushed into the WHERE clause; rare-shape returns work correctly
  on bare patterns.
- **Decorator capture on classes** (was: only functions). Fixes
  ``roam context`` showing ``@dataclass`` etc. badges on classes.
- **Decorator display in ``roam context`` is paren-aware.** Multi-arg
  decorators like ``@pytest.mark.parametrize("a,b,c", [...])`` no
  longer break across the comma inside arguments.

### Fixed (real bugs surfaced by the new detectors on roam-code itself)

- **3 `open()` resource leaks** in
  ``cmd_agent_export.py:626``, ``tests/test_agent_export.py:387``,
  ``tests/test_fingerprint.py:213`` — converted to ``with`` blocks.

### Verification

- 71 Python pivot tests pass (was 29).
- 646 focused tests pass (was 541).
- Bench held: recall@5 0.656, recall@10 0.769, recall@20 0.900.
- All 11 idiom detectors verified on synthetic + scaled to a 17k-file
  codebase (supernode: 167 open-leaks, 4 sync-in-async, 146 bare-except).
- roam-code itself: 0 findings across all 11 detectors (post-fix).

## [12.4.0] - 2026-05-02

A Python-pivot release. Three super-passes of dogfooding on real
Python codebases (deep-research, roam-agent-eval, supernode) surfaced
gaps that the language-agnostic surface couldn't catch. v12.4 adds
Python-specific structural signals and idiom detection without
adding new commands — existing commands give better Python answers.

### Added

- **`is_async` on symbols.** New schema column populated by the
  Python extractor when a function/method uses ``async def``.
  Surfaced in:
  - `roam context` shows `[async coroutine]` above the signature
    so agents reading context know coroutine semantics without
    scanning source.
  - `roam search --async` filters to async functions/methods only.
  - Signature display uses ``async def ...`` instead of ``def ...``.
- **`decorators` on symbols.** Comma-joined list of decorators on the
  symbol (``@property``, ``@pytest.fixture``, ``@app.route``, etc.).
  Surfaced in:
  - `roam context` displays decorators above the signature.
  - `roam search --decorator <substring>` filters by decorator —
    e.g. ``--decorator pytest.fixture`` finds all fixtures,
    ``--decorator app.route`` finds Flask/FastAPI routes.
- **Python-specific anti-pattern detectors** in `roam math`:
  - `py-mutable-default-arg` — ``def foo(x=[])`` and similar.
    Classic Python footgun where the list is shared across calls.
  - `py-bare-except` — ``except:`` with no exception type. Catches
    SystemExit/KeyboardInterrupt; PEP 8 explicitly discourages.
  - `py-none-eq` — ``x == None`` should be ``x is None`` (faster,
    idiomatic, robust against ``__eq__`` overrides).
  - `py-logger-fstring` — ``logger.info(f"x={x}")`` builds the
    f-string even when the level discards the message. Use
    ``logger.info("x=%s", x)`` for lazy evaluation.

### Improved

- **Generated/example/vendor/workspaces directories now excluded
  from headline metrics by default.** Extends the v12.3 tooling
  exclusion (`/dev/`, `/.github/`, `/benchmarks/`) to a richer set
  surfaced from Python-pivot dogfood: `/examples/`, `/workspaces/`
  (agent-generated benchmark artifacts), `/vendor/`, `/third_party/`,
  `/_generated/`, `/_build/`, `/node_modules/`, `/dist/`, `/build/`,
  `/docs/`. Hint set centralised in
  `roam.output.file_role_hints` so all headline commands stay in
  sync. Smells, fan, dead, complexity all benefit. Pass
  `--include-tooling` (where available) to opt back in.
- **`output.file_role_hints.header_indicates_generated()`** — new
  helper detects machine-generated files by header marker
  (``// Code generated by``, ``@generated``, etc.) in addition to
  path-based detection. Available for downstream code; not yet
  consumed by core commands but lays the substrate for v12.5.
- **`roam complexity --include-tooling`** flag added. Default now
  excludes the same path set as smells/fan/dead.

### Verification

- 541 focused tests pass (+ 29 new in `tests/test_python_pivot.py`).
- Bench preserved: recall@5 0.672, recall@10 0.786, recall@20 0.900.
- Reindexed roam-code itself: 31 async symbols, 724 decorated
  symbols detected.

## [12.3.1] - 2026-05-02

A polish patch from 10 more rounds of dogfooding. No surface changes,
five papercut bugs fixed.

### Fixed

- **`roam dead --json`** was missing `summary.verdict`, the only
  command in the surface area without it. Agents calling
  `roam_dead_code` over MCP now get a verdict line consistent with
  every other tool.
- **`roam retrieve` UPPERCASE / mixed-case identifiers** previously
  extracted zero tokens (e.g. `PERSONALIZED_PAGERANK` and
  `Personalized_Pagerank` both returned no candidates). Now resolve
  to the same FTS terms as `personalized_pagerank` via a new
  `_UPPER_SNAKE_RE` and a lowercase-fallback re-snake pass.
- **`roam retrieve` low-confidence false-positive** on real matches
  with a high-scoring top-1 (e.g. `where is email sending` →
  `send_welcome` at 0.900). Added a top-1-vs-2nd gap override: when
  the gap is ≥ 0.30 the structural reranker has a unique winner and
  the token-coverage check is skipped. Distinguishes real big-gap
  matches from "one common word matches everything" failure modes.
- **MCP server card** at `docs/site/.well-known/mcp-server-card.json`
  was hardcoded to `"version": "12.2.0"`. Bumped to track the
  package.
- **`roam mcp --list-tools-json`** was missing `inputSchema` per
  tool, breaking conformance for registry validators that expect a
  drop-in proxy of the MCP `tools/list` response. Now includes
  `tool.parameters` from FastMCP.

### Verification

- Bench held: recall@5 0.672, recall@10 0.786, recall@20 0.900.
- 375 focused tests pass; full suite green on CI across 5 Python
  versions + no-optional-deps lane.
- 12 pre-commit re-checks: all 10 prior fixes from v12.3.0 still
  working, JSON envelopes consistent, tooling exclusion clean,
  tokenization matrix 5/5, confidence calibration 5/5.

## [12.3.0] - 2026-05-01

A retrieve-quality + dogfood-correctness release. Recall@20 on the
30-task self-bench moved from **0.486 → 0.900** across the day's
iterations (+41.4 pp). Sixteen agent-facing bugs surfaced by deep
dogfooding were fixed across two sprints. No new commands; existing
commands give better answers.

### Fixed — agent-facing correctness

- **`roam health` score formula** — was reporting 2/100 on
  structurally healthy codebases (tangle 0.5%, prop cost 0.0%) because
  the geometric-mean factors penalised absolute counts of god
  components / bottlenecks without normalising for codebase size *or*
  discounting expected utilities. Now actionable items only,
  normalised per 1k symbols. Same fix applied to
  `metrics_history._compute_health_score` so `roam init`'s summary
  agrees with `roam health`.
- **`roam oracle is-test-only <name>`** — returned False (with reason
  "orphan") for canonical test methods because pytest invokes them by
  reflection (zero callers in the call graph). Now checks the symbol's
  own `file_role` first — anything in `file_role='test'` is test-only
  by definition, falling back to caller-graph analysis for production
  helpers used only from tests.
- **`roam oracle is-reachable-from-entry <name>`** — returned False
  on every input because (a) the SQL queried `edges.kind IN ('calls',
  'references')` (plural) but the schema uses `'call'`/`'reference'`
  (singular), and (b) the entry-point definition relied on a non-
  existent `is_entry` column / `file_role='entry'` value. Now uses
  the same import-graph definition as `cmd_understand`, plus a
  named-entry fallback so common entry symbols (`cli`, `main`, `run`,
  `app`, `serve`) are recognized regardless of import shape.
- **`roam smells` headline number included CI/dev tooling.** Top-N
  critical smells were dominated by `.github/scripts/`, `benchmarks/`,
  `dev/` files that aren't source code. Now respects `file_role` and
  path hints (`/dev/`, `/benchmarks/`, `/.github/`). Default output
  gains a truncated `Where` column. `--include-tooling` opts back in.
- **`roam fan` and `roam dead`** — same tooling-exclusion default.
  Top hub symbols and top dead exports no longer dominated by
  `pr-comment.js` and `roam-bench.py`.
- **`roam preflight` Risk driver line** — names the row driving the
  overall verdict (`Risk driver: complexity (cc=17, HIGH)`). Saves
  the agent the deduction step.
- **`roam weather` Score column** — was rounding all values to `1`
  via `.0f`. Now `.2f` to expose the geometric-mean discrimination.
- **`roam search` PR display** — was rounding all niche/test symbols
  to `0.0001`. Now scientific notation for `<0.001` so `1.07e-04`
  stays distinct from `5.68e-05`.
- **`roam rules` empty state** — silently said "no rules directory"
  on a project that ships 2489 community rules at `rules/community/`.
  Now mentions the count and how to opt in.
- **`roam taint --rules-pack`** — flag was claimed in MEMORY/external
  docs but didn't exist on the CLI. Added as a Choice (sqli, xss,
  ssrf, path-traversal, command-injection, deserialization).
- **`roam entry-points` HTTP false-positive** — the name-based
  classifier matched `_view$|_handler$`, mis-tagging
  `SqlExtractor._extract_create_view` and `error_handler` as HTTP
  routes. Tightened to `_endpoint$|_controller$`; the decorator-pattern
  arm catches the genuine routes.

### Improved — retrieve quality

Eight changes against the 30-task self-bench. **recall@5 0.289 →
0.672 (+38.3 pp), recall@10 0.358 → 0.786 (+42.8 pp), recall@20
0.486 → 0.900 (+41.4 pp).** Cross-repo regression test on a
synthetic Python microservice returns 1.0 / 1.0 / 1.0 — the lift
isn't an artifact of self-bench.

- **Domain-noun supplement.** `extract_tokens()` now always includes
  lowercase domain nouns alongside identifier-shaped tokens. The old
  all-or-nothing fallback discarded words like "language" /
  "extractor" whenever any PascalCase/snake/dotted token was found.
- **File-level dedup in budget.** Top-K window now picks the
  best-of-file before filling with deferred symbols. A 20-symbol
  window no longer collapses to 5 unique files.
- **File-edge neighbour expansion.** Pulls symbols from files
  imported-by/importing the strongest first-stage hits so
  structurally-related files (registry, tests, command companions)
  enter the candidate pool. Hub-aware (degree > 20 disqualifies a
  seed) and bounded to 80 expansion symbols.
- **Path-token boost.** Files whose path contains a query token get a
  boost (max 0.15). Prefix-tolerant on both sides with a 4-char floor
  so plurals/derivations match.
- **cmd-companion boost.** `commands/cmd_FOO.py` lifts when the
  engine module `FOO/` is a strong candidate. Magnitude scales with
  the companion's normalised fts_score so weak matches don't drag
  unrelated cmd files into top-K.
- **Rule-YAML demotion** for "where is X" queries. Excludes
  `rules/community/*.yaml` from top-K when the query is implementation-
  shaped (`where`, `how`, `find`, `locate`) and doesn't mention
  rule/yaml/lint/policy.
- **Low-confidence verdict.** When the top-3 candidate paths cover
  ≤1 of ≥2 query tokens (or scores are bunched at the noise floor),
  the verdict is prefixed `low confidence —`. Catches the "trace the
  login flow" failure mode where every candidate matches one common
  word but no real answer exists.

### Added — small additions

- **Index-staleness hint.** New
  `commands/resolve.index_staleness_hint()` helper compares the
  latest indexed commit hash against `git rev-parse HEAD`. When they
  differ, `health`, `diagnose`, and `weather` print a `NOTE: index
  latest commit X != HEAD Y — git-derived metrics may be stale. Run
  roam index --force.` line. Suppressed via
  `ROAM_NO_STALENESS_HINT=1` for CI.
- **Bench-relevance hint** in `roam critique`. When the diff touches
  a structurally-significant path (retrieve/, graph/, languages/,
  security/taint/, critique/, oracle/health), the verifier suggests
  the relevant pytest target + `roam eval-retrieve` invocation.
  Hot-path table is path-prefix keyed; projects without these paths
  get no hint.

### Verification

- Full test suite: 6216 passed, 12 skipped, 0 failed.
- 26 new tests across `tests/test_fallback_contracts.py`,
  `tests/test_extractor_grammar_drift.py`,
  `tests/test_retrieve_cross_repo.py`.
- New CI lane: `test-no-optional-deps` actually exercises the
  ImportError fallback paths that previously slipped through.
- 5 Python versions (3.9–3.13), Linux + cross-grammar-version drift.

## [12.1.0] - 2026-05-01

### Added
- **`roam oracle <name>`** — boolean-oracle command group with 5 subcommands giving 1-token yes/no answers to agents: `symbol-exists`, `route-exists`, `is-test-only`, `is-reachable-from-entry`, `is-clone-of`. MCP tools: `roam_oracle_*`. Direct counter to CKB v9.2's `symbolExists` pattern.
- **`roam_taint_classify` (MCP only)** — LLM-augmented taint classification. Runs `roam taint` then asks the agent's own model (via MCP sampling) to label each reachable finding as IDOR / AUTHZ / SQLI / XSS / CMD_INJECTION / PATH_TRAVERSAL / SSRF / etc. with confidence + reasoning. Counter to Semgrep Multimodal — same LLM-reasoning narrative without a hosted API key. Sequential for v12.1; concurrency-bounded gather lands in v12.2.
- **`roam index-export <bundle.tar.gz>`** + **`roam index-import <bundle.tar.gz>`** — portable, integrity-checked roam index bundles. Manifest carries SHA-256 of the bundled `index.db`; import verifies before extracting. Optional cosign signing (`--sign --key ...` or `--sign --keyless`). Counter to Cursor's "92% similar codebase = reuse teammate's index" without a vendor cloud.
- **`roam eval-retrieve --emit-format coderag|beir`** — bench-portable JSONL emit for public retrieval-leaderboard submission. CodeRAG-Bench-compatible `ctxs` array + BEIR-style trec_eval run files. Pair with `--emit-out <path>` and `--emit-k N`.
- **Django bridge** — full implicit-relationship resolution: admin → model (via `@admin.register` / `admin.site.register`), serializer/form/filterset → model (via `Meta.model`), `@receiver(sender=Model)`, `path()`/`re_path()`/`include()` URL trees, DRF `router.register()`, `@app.task`/`@shared_task` tagging. Companion `index/django_post.py` resolves transitive Django model inheritance + custom field metadata after the per-file extraction phase. New schema columns: `symbols.framework_type`, `field_type`, `field_metadata`; `edges.call_function`. Ported from `upstream fork/roam-code` — credit upstream fork author.
- **`roam.git_utils.worktree_git_env(cwd)`** — sets `GIT_INDEX_FILE` per worktree so parallel agents in sibling worktrees don't contend on `.git/index.lock`. Wired into `discovery.py`, `git_stats.py`, `changed_files.py`, `cmd_index_bundle.py`. Ported from `upstream fork/roam-code-sf` — credit upstream fork author.

### Fixed
- **BLOCKER (taint engine)**: source-as-sanitizer false-clean OpenVEX claim. When a rule listed the same name as both source and sanitizer (or via LIKE-suffix overlap), every reachable path was emitted as `not_affected/inline_mitigations_already_exist`. Fix: drop overlap before BFS.
- **HIGH (rerank)**: `rerank.py:_pagerank_scores` IN-clause violated CLAUDE.md `batched_in()` rule when `--k > 80` (1000+ placeholders > `SQLITE_MAX_VARIABLE_NUMBER=999`).
- **`oracle_is_clone_of`** queried wrong columns (`name_a`/`name_b` vs schema's `qname_a`/`qname_b`) — would always false-negative. Now uses `qname_a/b` + suffix LIKE match.
- **`cmd_index_bundle`** treated `cosign_available()` (which returns `(bool, str)`) as a bare bool — the "binary missing" message never fired. Now unpacks the tuple correctly in both export and import flows.
- **`cmd_index_bundle._verify_bundle`** now catches `tarfile.ReadError` / `CompressionError` / `EOFError` and surfaces a clean `ValueError("bundle is corrupted...")` instead of an uncaught traceback.
- **`oracle_is_reachable_from_entry`** clamps `max_hops` to `[1, 1000]` to avoid confusing "unreachable within -5 hops" messages.

### Changed
- Surface counts: **150 CLI commands** (147 → 150: +3 for `oracle`, `index-export`, `index-import`), **112 MCP tools** (106 → 112: +6 for 5 oracles + `roam_taint_classify`), **33 core preset** (27 → 33: all 6 added to core).
- Test coverage: **80 new tests** across `test_oracle.py`, `test_git_utils.py`, `test_eval_retrieve.py`, `test_index_bundle.py`, `test_taint_classifier.py`, `test_bridge_django.py`.

## [12.0.0] - 2026-05-01

### Added
- **`roam retrieve "<task>"`** — graph-aware FTS5 + structural reranker (personalised PageRank + clone-canonical signal + lexical baseline) + token-budget cap. Returns ranked spans with justification tags. MCP tool: `roam_retrieve`.
- **`roam critique`** — graph-grounded patch verifier. `git diff | roam critique` gets findings ranked by severity. The killer signal is **clones-not-edited**. Exits 5 on high severity (CI-gateable). MCP tool: `roam_critique`.
- **`roam fleet plan`** — Louvain + dark-matter co-change + PageRank multi-agent partitioner. Emits `roam-fleet/v1` manifest with raw / Composio / GitHub Copilot CLI adapters. MCP tool: `roam_fleet_plan`.
- **`roam ask`** — 12-recipe TF-IDF intent classifier dispatching `preflight/retrieve/critique/fleet/diagnose/trace/...` in-process.
- **`roam taint`** — graph-reach BFS + 5 starter rule packs (sqli, xss, path-traversal, command-injection, deserialization). OpenVEX-correct status + justification strings (no `code_not_reachable`).
- **`roam cga emit`** — in-toto v1 statement (predicate type `roam-code.dev/CodeGraph/v1`) with Merkle root + edge-bundle digest + optional taint reachability claims. Cosign keyless or offline signing.
- **`roam eval-retrieve`** — recall@K JSONL harness + weight-sweep mode rotating α/β/γ/δ/ε vectors.
- New CI workflow `.github/workflows/cga-attestation.yml` running real cosign offline-key + keyless OIDC + tamper-detection sanity check.
- Bench infrastructure (`bench/retrieve/roam_self.jsonl`, 30 hand-curated tasks; recall@20 = 0.503).

### Changed
- Surface counts: 147 CLI commands, 106 MCP tools, 27 core preset.
- Tests: 6073+ across 240+ files.

## [11.1.3] - 2026-02-27

### Fixed
- PyPI: 11.1.2 package was missing `SqlExtractor` (published before SQL was added). This release is the first PyPI version with SQL DDL support.

## [11.1.2] - 2026-02-27

### Added
- **SQL DDL promoted to Tier 1** with dedicated `SqlExtractor` — full support for tables, columns, views, functions, triggers, schemas, types (enums), sequences, ALTER TABLE ADD COLUMN. Foreign keys produce graph edges; views and triggers reference their source tables (27 languages total)
- **Scala promoted to Tier 1** with dedicated `ScalaExtractor` — full support for classes, traits, objects, case classes, sealed hierarchies, val/var properties, type aliases, imports, and inheritance
- `server.json` for official MCP Registry submission (`registry.modelcontextprotocol.io`)
- MCP registry submission guide prepared for 9 directories

### Fixed
- CI: lazy `import yaml` in `extractor_schema.py` (PyYAML is optional)
- CI: `TYPE_CHECKING` guard for networkx import in `cmd_visualize.py`
- CI: skip language corpus tests when yaml/QueryCursor unavailable
- Registry docstring no longer mentions Scala as Tier 2

## [11.1.1] - 2026-02-27

### Fixed
- `roam algo`: list-prepend detector SQL missing `calls_in_loops` columns, causing false positives (5 -> 3 findings)
- `roam intent --undocumented`: wrong DB table reference
- `roam rules --ci`: use `EXIT_GATE_FAILURE=5` instead of exit code 1
- `roam fan`: incorrect verdict labels
- `roam coupling`: missing VERDICT line
- `roam visualize`: lazy-load import fix
- `cmd_report.py`: stale `snapshot`/`trend` command references updated to `trends`
- `cmd_missing_index.py`: `re.compile` hoisted from loop to module level
- CODEOWNERS `@`-prefix: strip at comparison point in suggest-reviewers, not in shared parser
- Surface count consistency across README, cli.py, CLAUDE.md (139 canonical, 137 cmd files)

### Removed
- `cmd_trend.py`, `cmd_snapshot.py`, `cmd_digest.py`, `cmd_onboard.py` -- consolidated into `cmd_trends.py` and `cmd_understand.py`
- 15 unused variables across 12 source files (ruff F841 sweep)
- Dead loop in `cmd_partition.py`, unused `hashlib` import in `cmd_sbom.py`
- Dead helpers `_find_section_line_range()` and `_parse_roam_trails()` in `competitor_site_data.py`

### Added
- `codeowners_helpers.py` -- shared CODEOWNERS parsing extracted from `cmd_codeowners.py`
- `graph/stats.py` -- shared graph statistics helper
- ~30 new test files (~700+ tests): alerts, auth-gaps, bus-factor, conventions, coverage-gaps, entry-points, hotspots, init, migration-safety, missing-index, n1, patterns, report, risk, sketch, split, testmap, tour, uses, why, xlang, and more
- All command docstrings updated with cross-references to related commands
- Token budget added to ~15 commands that were missing it

## [11.1.0] - 2026-02-25

### Added
- **7 new commands:** `roam adrs`, `roam ci-setup`, `roam congestion`, `roam coverage-gaps`, `roam doc-staleness`, `roam flag-dead`, `roam over-fetch`
- **YAML-based language extractors:** declarative tree-sitter query definitions in `src/roam/languages/extractors/*.yaml`
- **Kotlin promoted to Tier 1** via YAML extractor architecture with context-aware kind resolution
- **CI workflow templates:** `roam ci-setup` generates GitHub Actions, GitLab CI, and Azure Pipelines configs
- **Community rules expanded** to 2480+ YAML rules across architecture, correctness, dataflow, performance, security, and style categories
- **Architecture Guardian:** `roam watch --guardian` with CI workflow for automated architecture drift detection
- Dev tooling: `dev/command_audit.py`, `dev/repo_hygiene.py`, `dev/env_doctor.py`, `dev/todo_guard.py`
- Integration tutorials for Claude Code, Cursor, Gemini CLI, Codex, and Amp
- OSS benchmark harness with reproducible evaluation framework
- Search v2 ONNX semantic backend for improved recall

## [11.0.0] - 2026-02-25

### Added
- **MCP v2 Overhaul:**
  - In-process MCP execution via CliRunner -- eliminates subprocess overhead (#1)
  - 4 compound MCP operations: `roam_explore`, `roam_prepare_change`, `roam_review_change`, `roam_diagnose_issue` -- each replaces 2-4 tool calls (#2)
  - 6 MCP tool presets: core (20 tools), review, refactor, debug, architecture, full (65 tools) via `ROAM_MCP_PRESET` env var (#3)
  - Structured return schemas (`output_schema`) on all 65 MCP tools (#4)
  - `roam_expand_toolset` meta-tool for dynamic mid-session preset switching (#6)
- **Performance Foundations:**
  - SQLite FTS5/BM25 search replacing TF-IDF -- symbol search is now ~1000x faster (#14)
  - O(changed) incremental edge rebuild via `source_file_id` provenance tracking (#13)
  - 7 new database indexes, UPSERT pattern, batch size optimization (#15)
  - `PRAGMA mmap_size=268435456` (256MB memory-mapped I/O) (#11)
  - Size guard on `propagation_cost()` for graphs >500 nodes (#12)
- **MCP Protocol Compliance (Epic 14):**
  - Structured error responses with `isError`, `retryable`, and `suggested_action` fields (#116)
  - `structuredContent` alongside text on MCP tool failures (#117)
  - 5 MCP Prompts: `/roam-onboard`, `/roam-review`, `/roam-debug`, `/roam-refactor`, `/roam-health-check` (#118)
  - Response metadata in `_meta`: `response_tokens`, `latency_ms`, `cacheable`, `cache_ttl_s` (#119)
- **Code Smell Detection:**
  - `roam smells` — 15 deterministic detectors (brain methods, god classes, feature envy, shotgun surgery, data clumps, etc.) with per-file health scores (#120)
- **Quality Gates and Setup:**
  - `roam health --gate` — quality gate checks from `.roam-gates.yml` with exit code 5 on failure (#122)
  - `roam mcp-setup <platform>` — config snippets for claude-code, cursor, windsurf, vscode, gemini-cli, codex-cli (#130)
- **Security and Verification:**
  - `roam verify-imports [--file F]` — import hallucination firewall: validate imports against indexed symbol table with FTS5 fuzzy suggestions (#125)
  - `roam vulns [--import-file F] [--reachable-only]` — vulnerability scanning CLI: auto-detect npm/pip/trivy/osv formats, reachability filtering, SARIF output (#131)
  - `roam secrets` upgraded: test/doc suppression, env-var detection, Shannon entropy detector, per-finding remediation suggestions (#133)
- **Analytics and Scoring:**
  - `roam metrics <file|symbol>` — unified vital signs: complexity, fan-in/out, PageRank, churn, test coverage, dead code risk (#137)
  - `roam debt --roi` — refactoring ROI estimate (developer-hours saved per quarter/year) with confidence band based on complexity, churn, and coupling signals (#144)
  - Composite difficulty scoring for partitions: weighted complexity + coupling + churn + size with Easy/Medium/Hard/Critical labels (#128)
  - Quality rule profiles with inheritance: default, strict-security, ai-code-review, legacy-maintenance, minimal — `--profile` flag on `roam check-rules` (#138)
- **Documentation Intelligence:**
  - `roam docs-coverage` — exported-symbol docs coverage report with stale-doc drift detection and PageRank-ranked missing-doc hotlist, plus threshold gate support (`--threshold`) (#143)
- MCP resources expanded from 2 to 10: architecture, hotspots, tech-stack, dead-code, recent-changes, dependencies, test-coverage, complexity (#129)
- **CI/Runtime Ergonomics:**
  - Standardized exit codes for CI integration (0=success, 3=index missing, 5=gate failure) (#19)
  - GitHub Action: composite action with SARIF upload, sticky PR comments, quality gates, SQLite caching (#20)
  - Progress indicators during `roam init` / `roam index` with `--quiet` flag (#30)
  - `defer_loading` annotations on non-core MCP tools for Claude Code Tool Search compatibility (#66)
- **Ownership and Reviewer Intelligence:**
  - `roam codeowners` (#38)
  - `roam drift` (#39)
  - `roam simulate-departure` (#40)
  - `roam suggest-reviewers` (#41)
- **Change-risk and Structural Review:**
  - `roam api-changes` (#42)
  - `roam test-gaps` (#43)
  - `roam secrets` (#44)
  - `roam semantic-diff` (#77)
- **Agent Quality and Governance Suite:**
  - `roam vibe-check` (#57)
  - `roam ai-readiness` (#84)
  - `roam verify` (#85)
  - `roam ai-ratio` (#86)
  - `roam duplicates` (#87)
- **Dashboard and Trend Visibility:**
  - `roam dashboard` (#80)
  - `roam trends` (#81)
  - `--mermaid` architecture output support (#82)
  - `roam onboard` (#83)
- **Multi-agent Workflows:**
  - `roam partition` (#88)
  - `roam affected` (#89)
  - `roam syntax-check` (#92)
- **Output Determinism and Context Ranking:**
  - Deterministic output ordering for cache-friendly prompts (#90)
  - PageRank-weighted budget truncation metadata (#91)
  - Conversation-aware ranking personalization (#94)
- **Agent Context Export and MCP Compatibility:**
  - Agent context export bundles (`AGENTS.md` + provider overlays) (#65, #68, #97)
  - Streamable HTTP transport baseline (`roam mcp --transport streamable-http`) (#98)
  - Expanded MCP annotations + task-support metadata (#99)
  - MCP client conformance/profile suite (#100)
- **Algorithm Detection Upgrades:**
  - Precision profiles (`balanced`/`strict`/`aggressive`) (#101)
  - Runtime-aware impact scoring + evidence paths + framework-aware N+1 packs (#102)
  - `roam algo --sarif` with stable fingerprints, codeFlows, and fixes payloads (#103)
- **CI Quality-gate Hardening:**
  - Idempotent sticky PR comment updater with duplicate cleanup (#23)
  - Trend-aware fitness gates (#74)
  - `--changed-only` incremental CI mode (#75)
  - SARIF guardrails + configurable category + truncation warnings (#105)
- **Documentation/Release Hygiene:**
  - CONTRIBUTING.md with issue/PR templates (#28)
  - README competitive positioning table (#76)
  - Command/matrix count reconciliation helpers and tests (#108)
  - README command/MCP inventory overhaul to match source reality (#106)
  - Product landing page at `docs/site/index.html` with competitive comparison, feature showcase, and install instructions
  - Competitive research page at `docs/site/landscape.html` with fairness-recalibrated scores
- PyPI discoverability: keywords, Documentation URL, and expanded classifiers in `pyproject.toml` (#111)
- Pre-commit integration: `.pre-commit-hooks.yaml` with 5 hooks (`roam-secrets`, `roam-syntax-check`, `roam-verify`, `roam-health`, `roam-vibe-check`) (#21)
- Fuzzy symbol-not-found suggestions via FTS5/BM25 search in `roam symbol`, `roam impact`, `roam context`, `roam diagnose` (#51)
- Actionable remediation hints in all major error messages — "index missing", "symbol not found", "database error" now include next steps (#50)
- **Agent Error Recovery and Diagnostics:**
  - `roam doctor` — setup diagnostics: Python version, tree-sitter, git, index freshness, SQLite, networkx checks (#48)
  - `roam reset` — destructive index rebuild with `--force` safety flag (#52)
  - `roam clean` — lightweight orphaned-file cleanup without full rebuild (#52)
  - Next-step suggestions in `roam health`, `roam context`, `roam hotspots`, `roam diagnose`, `roam dead` output (#45)
  - `roam endpoints` — multi-framework API endpoint scanner (Flask, FastAPI, Django, Express, Go, Spring, Laravel, GraphQL, gRPC) (#113)
- **Progressive Disclosure and Batch Operations:**
  - Universal progressive disclosure: `--detail` flag for full output, compact summary by default. Applied to `health`, `hotspots`, `dead`, `deps`, `layers`, `clusters` (#10)
  - Batch MCP operations: `roam_batch_search` (10 queries) and `roam_batch_get` (50 symbols) in single MCP call with shared DB connection (#7)
- **Developer Workflow Tools:**
  - Git hook auto-indexing: `roam hooks install/uninstall/status` with append-mode markers for post-merge/post-checkout/post-rewrite (#61)
  - Install verification: `roam --check` eager flag for quick first-run validation (#115)
  - `roam dev-profile` — developer behavioral profiling: commit time patterns, Gini scatter, burst detection, session analysis, risk scoring (#78)
  - `roam watch` — poll-based file watcher with debouncing for always-on agent sessions, plus authenticated webhook daemon mode (`POST /roam/reindex`, `GET /health`) for warm refresh workflows (#60, #95)
- **Search and Analysis:**
  - `roam search-semantic` now uses hybrid retrieval: BM25 lexical ranking + TF-IDF vector ranking fused with Reciprocal Rank Fusion for stronger semantic recall (#54)
  - Pre-indexed framework/library packs now enrich semantic retrieval for common stacks (Django, Flask, FastAPI, React, Express, SQLAlchemy, pytest, stdlib) to improve cold-start recall (#96)
  - `roam search --explain` — BM25 score breakdown with field match highlights for search result transparency (#55)
  - `roam supply-chain` — dependency risk dashboard: 7 package formats, pin coverage scoring, maintenance signals (#79)
  - `roam spectral` — Fiedler vector bisection for module decomposition, spectral gap metric, `--compare` vs Louvain (#73)
- **Structural Governance:**
  - `roam check-rules` — structural rule engine with 10 built-in rules and `.roam-rules.yml` config (#93)
  - Bottom-up context propagation through call graph for `roam context` ranking (#72)

### Changed
- All MCP tool descriptions shortened to <60 tokens each for agent efficiency (#5)
- MCP token overhead reduced from ~36K to <3K tokens (core preset) -- 92% reduction
- `--budget N` Phase 2: extended to all list-producing commands (13 more commands), completing universal budget support across the full CLI (#9)
- MCP core preset expanded from 21 to 23 tools (added `roam_batch_search`, `roam_batch_get`)
- CI workflows consolidated: removed redundant `ci.yml`, enhanced `roam-ci.yml` with lint job, converted `roam.yml` to `workflow_dispatch`-only template (#110)
- Competitive landscape scoring rebalanced: equal weights (0.5/0.5), self-assessed labels, roam arch score 90→78, SonarQube 62→72, CodeQL 60→74
- roam-code category in competitive data changed from standalone `"roam"` to `"mcp_server"`
- Confidence system removed from competitive landscape page
- Consolidated duplicated EXTENSION_MAP and schema definitions to single sources of truth (#17)

### Fixed
- Command-count drift removed from docs and launch copy by adopting canonical-vs-alias counting (`algo` + legacy `math`) (#108)
- README command tables and MCP inventory now match code (121 canonical CLI commands + 1 alias, 93 MCP tools) (#106)
- Bare `except:` audit confirmed — codebase already clean, no broad exception swallowing (#18)
- Cycle detection in health scoring now uses Tarjan SCC (O(V+E)) instead of 2-cycle self-join (#16)

## [10.0.1] - 2026-02-21

### Added
- MCP lite mode (16 core tools) as the default; full mode via `ROAM_MCP_LITE=0`
- MCP tool namespacing with `roam_` prefix across all 61 tools
- `roam mcp` command with `--transport` and `--list-tools` flags
- 13 additional MCP tools with structured error handling

### Fixed
- Community issues #7 and #9 addressed
- YAML fallback parser indentation handling corrected
- `--json` flag position in CI workflow examples fixed
- CI dev dependencies (pytest-xdist) properly installed

## [10.0.0] - 2026-02-20

### Added
- **30+ new commands** bringing total to 94 (from 56 in v9.1):
  - Architecture: `simulate`, `fingerprint`, `orchestrate`, `cut`, `adversarial`, `plan`
  - Debugging: `invariants`, `bisect`, `intent`, `closure`
  - Governance: `rules`, `attest`, `pr-diff`, `budget`, `capsule`, `forecast`, `path-coverage`
  - Analysis: `dark-matter`, `effects`, `annotate`, `annotations`, `relate`
  - Backend quality: `n1`, `auth-gaps`, `over-fetch`, `migration-safety` (and 3 more)
- Cross-language bridges: Salesforce (Apex/Aura/LWC), Protobuf, REST API, Jinja2/Django templates, env var config
- Semantic search via TF-IDF with cosine similarity (`roam search --semantic`)
- JSON envelope schema versioning and validation on all command output
- `--sarif` global CLI flag for SARIF 2.1.0 output (health, debt, complexity)
- `--include-excluded` flag for inspecting normally-excluded files
- Algorithm catalog tips integrated into analysis output
- Ruby Tier 1 language support (26 languages total)
- `roam fingerprint` for topology fingerprinting and comparison
- `roam orchestrate` for multi-agent work partitioning (Louvain-based)
- `roam mutate` for code transforms (move, rename, add-call, extract)
- Vulnerability mapping (`roam vuln-map`) and trace ingestion (`roam ingest-trace`)
- Property-based and indexing integration tests

### Fixed
- ON DELETE CASCADE/SET NULL added to foreign key constraints
- `fitness` command outputs proper JSON when no rules are configured
- Schema-prefixed `$` table names stripped in `missing-index` detection
- Pluralization edge cases and `$hidden` symbol messaging
- Algorithm findings accuracy: auth-gaps brace tracking, over-fetch, migration-safety
- Loop-invariant false positive rate reduced

### Changed
- Lint cleanup and algorithm optimizations across codebase
- pytest-xdist enabled for parallel test execution (~2x speedup)

## [9.1.0] - 2026-02-18

### Added
- `roam minimap` -- compact annotated codebase snapshot for CLAUDE.md generation
- YAML language support (Tier 1)
- HCL/Terraform language support (Tier 1)
- `roam describe --write` for agent-generated project instructions
- `.roamignore` support for excluding files from indexing

### Fixed
- Network drive path detection with automatic SQLite journal mode adaptation
- Indexer stall on binary formats (SCX files) with cloud-sync hardening

## [9.0.0] - 2026-02-18

### Added
- Universal algorithm catalog -- 23 tasks with ranked solution approaches (`roam math`)
- Algorithm anti-pattern detectors that query DB signals to find suboptimal code
- Command decomposition: large CLI modules split into focused `cmd_*.py` files
- `roam n1` -- implicit N+1 I/O pattern detection
- 6 backend quality analysis commands

## [8.2.0] - 2026-02-14

### Added
- Python extractor: `with`, `except`, `raise` statement extraction

### Fixed
- Dead export count discrepancy between `roam understand` and `roam dead --summary`
- Alerts health score mismatch with `roam health` (replaced simple penalty formula with weighted geometric mean)
- `roam patterns` self-detection of its own detector functions
- Middleware false positives from `%Handler` and `%Filter` patterns

### Changed
- Smarter health scoring: `dev/`, `tests/`, `scripts/`, `benchmark/` classified as expected utilities
- File role classifier: `dev/` directory assigned `ROLE_SCRIPTS`

### Removed
- 5 unused functions: `condense_cycles`, `layer_balance`, `find_path`, `build_reverse_adj`, `get_symbol_blame` (~200 lines)

## [8.1.1] - 2026-02-14

### Added
- Python extractor: instance attribute extraction from `__init__` methods (Pyan-inspired)
- Python extractor: assignment type annotation references for class fields and module variables
- Python extractor: forward reference support for string annotations (`Optional["Config"]`)

## [8.1.0] - 2026-02-14

### Added
- Python extractor: decorator references (`@decorator` and `@module.decorator(args)`)
- Python extractor: type annotation references for parameters, returns, and generics

### Fixed
- `roam complexity` crash on databases missing v7.4 columns (defensive `_safe_metric()` accessor)
- 95% fewer dead code false positives (test files excluded, ABC overrides and CLI functions marked intentional)
- Smarter health scoring with expanded utility path detection (`output/`, `db/`, `common/`, `internal/`)

## [8.0.1] - 2026-02-14

### Changed
- Extracted `graph_helpers.py` with shared BFS/adjacency code from 4 command files
- Split `cmd_context.py` into focused modules (1,622 to 1,022 lines)
- Added Python 3.9 to CI matrix, `Makefile`, and dev tooling

## [8.0.0] - 2026-02-14

### Added
- Statistical anomaly detection: Modified Z-Score, Theil-Sen regression, Mann-Kendall trend, CUSUM change-point detection
- Smart file role classifier (source, test, config, docs, build, generated, etc.)
- Dead code aging with git blame temporal decay scoring
- Cross-language bridge framework (abstract `LanguageBridge` with auto-discovery)
- C# Tier 1 language support (attributes, nullable types, using directives, constructors)
- `roam visualize` command for Mermaid/DOT architecture diagrams
- SCX/SCT binary form support for Visual FoxPro
- Agent-agnostic `roam describe` with auto-detection
- Gate presets for `coverage-gaps` (Python, JavaScript, Go, Java, Rust) with `.roam-gates.yml`
- Pluggable test convention adapters (Python, Go, JavaScript, Java, Ruby, Apex)
- `roam trend --analyze` with anomaly detection, forecasting, and `--fail-on-anomaly` flag
- 1,656 tests (up from 669 in v7.5.0)

### Changed
- Version sourced from single location (`pyproject.toml` via `importlib.metadata`)
- License format updated to SPDX string

### Fixed
- Cloud-synced path auto-detection with SQLite journal mode adaptation
- Indexer stall on binary SCX formats

## [7.5.0] - 2026-02-13

### Changed
- 12 research-backed math improvements across core analysis algorithms
- Percentile-based betweenness severity scoring (scales across codebase sizes)
- Three-factor trace quality scoring (directness + coupling + scaled hub penalty)

## [7.4.0] - 2026-02-12

### Added
- Multi-repo workspace support (`roam ws init`, `roam ws add`, `roam ws query`)
- Visual FoxPro (VFP) Tier 1 language support with regex-only extractor
- Cross-repo API edge detection (REST routes and HTTP client calls)
- Smart encoding detection for multi-codepage files (11 Windows codepages)
- Case-insensitive reference resolution fallback for VFP

## [7.2.0] - 2026-02-12

### Added
- Cognitive load index (0-100) per file combining complexity, dependencies, entropy, and size
- `roam tour` -- auto-generated onboarding guide with PageRank-ranked symbols
- `roam diagnose` -- root cause ranking with composite risk scoring
- Verdict-first output pattern across key commands (VERDICT line + JSON `verdict` field)
- Trend-based fitness rules (`type: trend` in `.roam/fitness.yaml`)
- MCP tools for `tour` and `diagnose`
- PR risk structural profile (cluster spread + layer spread)
- PyPI Trusted Publishing workflow

## [7.1.0] - 2026-02-12

### Added
- `batched_in()` / `batched_count()` helpers preventing >999 parameter SQL crashes
- Salesforce cross-language edges (LWC anonymous classes, `@salesforce/*` imports, Apex generics)
- Flow XML `actionCalls` to Apex class edge resolution
- Custom report presets via `roam report --config <path>`

### Fixed
- 41 unbatched IN-clause sites across 15 command modules and 2 graph modules
- Flow XML cross-block regex spanning across `</actionCalls>` boundaries

## [7.0.0] - 2026-02-12

### Added
- Composite health scoring (0-100) replacing old cycles-only formula
- SARIF 2.1.0 output for GitHub Code Scanning
- MCP server with 12 tools and 2 resources via FastMCP
- `roam init` -- guided project onboarding with CI workflow generation
- `roam digest` -- metric comparison against snapshots with delta arrows
- `roam describe --agent-prompt` -- compact agent-oriented summary under 500 tokens
- Per-file health score (1-10) with 7-factor CodeScene-inspired composite
- Co-change entropy (Shannon) for shotgun surgery detection
- Tangle ratio metric (Structure101 concept)
- `--compact` flag for token-efficient output across all commands
- `--gate EXPR` for CI quality gates (`roam health --gate score>=70`)
- Categorized `--help` with 7 command categories
- GitHub Action (`action.yml`) for CI integration

### Fixed
- `elif`/`else`/`case` chains inflating cognitive complexity ~3x vs SonarSource spec

## [6.0.0] - 2026-02-12

### Added
- 15 new commands: `complexity`, `conventions`, `debt`, `fitness`, `preflight`, `affected-tests`, `entry-points`, `safe-zones`, `patterns`, `bus-factor`, `breaking`, `alerts`, `fn-coupling`, `doc-staleness`, `complexity --bumpy-road`
- Cognitive complexity analysis (SonarSource-compatible, tree-sitter based)
- Architectural fitness functions via `.roam/fitness.yaml`
- `roam context --task` with mode-specific output (refactor/debug/extend/review/understand)
- `roam map --budget N` token-budget-aware repo map
- `roam diff --tests --coupling --fitness` enhanced diff analysis
- `symbol_metrics` table for per-function complexity data

## [5.0.0] - 2026-02-10

### Added
- `roam understand` -- single-call codebase comprehension for AI agents
- `roam coverage-gaps` -- unprotected entry point detection via BFS reachability
- `roam snapshot` / `roam trend` -- health metric history with sparklines and CI assertions
- `roam report` -- compound preset runner (first-contact, security, pre-pr, refactor)
- Salesforce support: Apex, Aura, Visualforce, SF Metadata XML extractors
- Hypergraph co-change analysis with surprise scoring
- JSON envelope contract across all `--json` output
- `roam dead --summary --by-kind --clusters` grouped analysis modes
- `roam context` batch mode for multiple symbols

## [4.0.0] - 2026-02-10

### Added
- Location-aware health scoring, callee-chain risk, multi-path trace
- `roam why` -- symbol role classification with reach and verdict
- PHP Tier 1 language support
- `--json` global flag on all commands

### Changed
- Precision refinements: utility-aware bottlenecks, zone-override risk, hub-aware trace

## [3.7.0] - 2026-02-10

### Added
- `roam describe`, `roam test-map`, `roam sketch` commands
- Method call extraction
- `.svelte` file support

## [3.0.0] - 2026-02-09

### Added
- Vue SFC parsing with template consumption analysis
- `roam diff` for blast radius of uncommitted changes
- Cross-file resolution with exported preference and Go same-directory matching
- Dead code transitive consumption check

### Fixed
- Vue import resolution and preprocessing off-by-one errors
- Windows NUL device crash
- Symbol disambiguation accuracy

## [1.0.0] - 2026-02-09

### Added
- Initial release: instant codebase comprehension for AI coding agents
- Tree-sitter AST parsing for Python, JavaScript, TypeScript, Go, Java, Rust, C
- Symbol extraction with qualified names and visibility
- Reference resolution and call graph construction
- Dependency graph with NetworkX (PageRank, cycles, layers, clusters)
- Core commands: `search`, `get`, `callers`, `callees`, `uses`, `map`, `layers`, `clusters`, `health`, `dead`, `hotspot`, `risk`, `owner`, `coupling`, `trace`, `grep`, `deps`
- SQLite local index (`.roam/index.db`)
- Incremental indexing via mtime + hash change detection
- Git integration: churn, blame, co-change analysis

[Unreleased]: https://github.com/Cranot/roam-code/compare/v11.1.3...HEAD
[11.1.3]: https://github.com/Cranot/roam-code/compare/v11.1.2...v11.1.3
[11.1.2]: https://github.com/Cranot/roam-code/compare/v11.1.1...v11.1.2
[11.1.1]: https://github.com/Cranot/roam-code/compare/v11.1.0...v11.1.1
[11.1.0]: https://github.com/Cranot/roam-code/compare/v11.0.0...v11.1.0
[11.0.0]: https://github.com/Cranot/roam-code/compare/v10.0.1...v11.0.0
[10.0.1]: https://github.com/Cranot/roam-code/compare/v10.0.0...v10.0.1
[10.0.0]: https://github.com/Cranot/roam-code/compare/v9.1.0...v10.0.0
[9.1.0]: https://github.com/Cranot/roam-code/compare/v9.0.0...v9.1.0
[9.0.0]: https://github.com/Cranot/roam-code/compare/v8.2.0...v9.0.0
[8.2.0]: https://github.com/Cranot/roam-code/compare/v8.1.1...v8.2.0
[8.1.1]: https://github.com/Cranot/roam-code/compare/v8.1.0...v8.1.1
[8.1.0]: https://github.com/Cranot/roam-code/compare/v8.0.1...v8.1.0
[8.0.1]: https://github.com/Cranot/roam-code/compare/v8.0.0...v8.0.1
[8.0.0]: https://github.com/Cranot/roam-code/compare/v7.5.0...v8.0.0
[7.5.0]: https://github.com/Cranot/roam-code/compare/v7.4.0...v7.5.0
[7.4.0]: https://github.com/Cranot/roam-code/compare/v7.2.0...v7.4.0
[7.2.0]: https://github.com/Cranot/roam-code/compare/v7.1.0...v7.2.0
[7.1.0]: https://github.com/Cranot/roam-code/compare/v7.0.0...v7.1.0
[7.0.0]: https://github.com/Cranot/roam-code/compare/v6.0.0...v7.0.0
[6.0.0]: https://github.com/Cranot/roam-code/compare/v5.0.0...v6.0.0
[5.0.0]: https://github.com/Cranot/roam-code/compare/v4.0.0...v5.0.0
[4.0.0]: https://github.com/Cranot/roam-code/compare/v3.7.0...v4.0.0
[3.7.0]: https://github.com/Cranot/roam-code/compare/v3.0.0...v3.7.0
[3.0.0]: https://github.com/Cranot/roam-code/compare/v1.0.0...v3.0.0
[1.0.0]: https://github.com/Cranot/roam-code/releases/tag/v1.0.0
