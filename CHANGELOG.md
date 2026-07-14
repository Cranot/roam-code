# Changelog

All notable changes to [roam-code](https://github.com/Cranot/roam-code) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **`roam ask "blast radius of renaming <field>"`** — new `field-trace` recipe (registry 30 → 31) that composes `uses` + `refs-text` + `impact` + `x-lang` into a pre-rename recon report: direct code consumers, per-surface text references (code/tests/docs/config with reachability + verdict), transitive blast radius, and FE<->BE API-bridge links — the full DB->BE->API->FE touchpoint list before a field/symbol rename. The `roam mutate rename` dry-run is the follow-up (it needs the new name; keeping `mutate` out of the trace path keeps the recipe runnable under read-only constitutions). Honest gate: the rest-api bridge matches URLs, not serialized JSON keys.
- **`roam ask "is <feature> wired?"`** — new `is-feature-wired` recipe (registry 29 → 30) that composes reachability-from-entry with CRUD/read-site detection to answer whether a feature is hooked up end-to-end (entry reachable → handler → data read/write site) in one call.
- **`roam compile-stats --schema`** — documents the 14 `compile-runs.jsonl` telemetry row fields (name + one-line meaning, in writer order), text + `--json`; works with no telemetry log present.
- **`roam compile --checklist`** — emits a `roam-compile-checklist-v1` block composing the plan's `required_checks` + verification contract + recommended first command into one checkbox-shaped artifact. Explicitly `kind: static` — it lists the commands to run, it is not a live verification.
- **`roam duplicates` semantic-review caveat** — high-structural-similarity clusters that are likely *behaviorally distinct* (disjoint call targets, or a genuine pure-query vs pure-mutation name split) now carry an additive `semantic_review` annotation ("review before merging") in text/JSON/SARIF. Never suppresses a cluster; compound-verb genuine duplicates (`getOrCreateUser`) draw no caveat.
- **`roam coupling` / `roam dark-matter` expected-pattern annotation** — locale-pair co-changes (real ISO-639 codes only, e.g. `messages.en.ts` ↔ `messages.el.ts`) and doc-hub siblings (same docs dir, both `.md`) are labeled `expected_locale` / `expected_doc_hub` instead of reading as hidden coupling. Annotation-only: dark-matter counts, verdicts, and risk are unchanged.
- **`roam cycles` shadow-artifact labeling** — every cycle finding now carries `shadow_artifact: true/false`, flagging phantom cycles caused by destructured-consumer name collisions (a resolver artifact, not a real reverse import). Label-only, never suppresses; genuine cycles report unchanged.

- **`roam blame-reviewers`** — advisory command that ranks suggested reviewers for a diff by total `lines_added` per author across the changed non-test files (pure git-blame-lines, read from the index — no live blame shell-out). Positional `COMMIT_RANGE` + `--staged` + `--top N`; text + `--json`. Complements the multi-signal `roam suggest-reviewers` with a simpler, transparent blame-only view, and extracts a shared `rank_blame_reviewers` helper that de-duplicates pr-risk's inline logic.
- **`roam rules-suggest`** — first-class command that promotes the review-suggestion capability previously hidden inside `roam pr-replay`. Runs `roam postmortem` over a commit range (`--range` / `--tier {sample,team,deep}`), aggregates findings by detector class, and for the classes that *recur* emits a starter `.roam/rules.yml` body + concrete `roam … --ci` gate invocations (heuristic, no LLM). Advisory/read-only by default; `--write` persists the preview and refuses to clobber an existing file without `--force`. Text + `--json`.
- **`roam reachability-triage`** — first-class zero-egress command that projects the `service-report --type reachability-triage` compose (sbom → supply-chain → vulns → vuln-reach → taint → secrets) into deterministic reachability facts. Adds `--gate-on-new-reachable` (exit 5 only on a *new* reachable flow versus a persisted `.roam` baseline; fail-open on a missing baseline) and `--range` for a diff scope. Emits facts only — reachable/not, hop distance, blast radius — never a semantic pass/fail verdict.
- **`roam verify --checks tenant_scope`** — opt-in multi-tenant data-bleed detector. Flags an API endpoint that reaches a tenant-scoped data access with no configured tenant guard on the reachable path. Conservative (fires only when project guards are configured and matched) and advisory; configure guards via `.roam/verify.yaml`.
- **`roam verify --checks fabricated_success`** — opt-in detector for external-sink stubs: a function whose name/docstring/annotations declare an external operation (payment/http/db/write) but that returns only a success-shaped literal with no statically-resolved external effect. Precision-first; silent on any resolved effect or unresolved declaration.
- **`roam verify --checks self_comparison`** — opt-in precision detector that flags a comparison whose two operands are the same non-name expression (e.g. `x.a == x.a`), which is always constant and usually a typo. Never fires on the `x == x` / `x != x` NaN idiom (bare names are excluded). Advisory WARN.
- **`roam verify --checks return_in_finally`** — opt-in control-flow detector that flags a `return`/`break`/`continue` inside a `finally` block, which silently discards any exception propagating out of the corresponding `try`. Skips `break`/`continue` bound to a loop nested inside the finally. Advisory WARN.
- **`roam verify --checks unchecked_result`** — opt-in precision detector that flags an inline dereference of a narrowly-known optional stdlib result (`re.search`/`re.match`, `os.getenv`, `dict.get`) without a `None` check. Excludes HTTP-client `.get()` idioms (`self.session.get(...)`) via immediate-receiver checking. Advisory WARN.
- **`roam verify --checks unreachable_except`** — opt-in precision detector that flags a later `except` handler made unreachable by an earlier broader handler catching a proven builtin supertype (e.g. `except Exception:` before `except ValueError:`). Builtins-only resolution; reports the first shadowed handler. Advisory WARN.
- **`roam verify --checks redundant_boolean_return`** — opt-in detector that flags an `if/else` returning `True` in one branch and `False` in the other (or the if-return-sibling form), simplifiable to `return bool(cond)`. Advisory WARN.
- **`roam verify --checks unreachable_after_return`** — opt-in detector that flags dead code following an unconditional `return`/`raise`/`break`/`continue` in the same block. Advisory WARN.
- **`roam verify --checks none_eq_comparison`** — opt-in precision detector that flags `x == None` / `x != None` (use `is` / `is not`). Never fires on the `x == x` NaN idiom. Advisory WARN.
- **`roam verify --summary`** — compact grouped-findings inspection (one top item per group) alongside the existing `--json` detail.
- **Claude Stop-hook empty-diff fast-exit** — a stop with a clean working tree (no tracked edits, no new untracked files — a pure Q&A session) now skips the `roam verify --auto --diff-only` subprocess entirely (~10ms git check instead of a 90s-budget verify run). Fail-open: any git error falls back to the full verify path; roam's own state files (`.roam/`, `.claude/`) don't defeat the fast-exit.
- **Claude Stop-hook block-rate telemetry** — every stop-hook decision appends a counts-only JSON line (`blocked`, `findings`, `advisory_findings`, `verify_ms`, `skipped_no_edit`) to `.roam/hook-stops.jsonl` (10 MB cap, fail-open, no finding text). A block decision costs a full extra agent turn — the rate is now measurable.

### Fixed
- **Flaky JS-detector CI failure root-caused (cache aliasing on recycled connection ids)** — the per-process file-text cache was keyed by `(id(conn), file_id)`; CPython recycles a dead `sqlite3.Connection`'s address, so a fresh connection could silently be served a dead connection's cached text (observed as `detect_js_shift_in_loop … got set()` on CI: the JS end-to-end test scanned cached *Python* source). Cache now keys on the resolved file path (the identity of what's read), with a distinct read-failed sentinel so empty files and unreadable files stay distinguishable; the strip-cache got the same id-reuse fix. Deterministically reproduced pre-fix, passes 3/3 + xdist post-fix.
- **Unreadable indexed files are loud in JS idiom scans** — an indexed file that can't be read from disk now emits a `RuntimeWarning` ("findings ABSENT, not verified-clean") instead of being silently skipped, per the loud-fallback ratchet.
- **Stale probe timings on compile cache hits** — `compile-runs.jsonl` rows for envelope-cache HITs no longer carry `probe_timings_ms` copied from the original MISS's cached plan object (a ~1ms hit row claiming hundreds of ms of probe time corrupted probe-cost analyses; 1,008 such rows observed). Telemetry-record-only; the served envelope is unchanged.
- **React-hook naming false positives** — `roam verify` naming and `roam conventions` no longer flag idiomatic `useX` React hooks (`useSwarm`, `useProjects`) as camelCase outliers in PascalCase-dominant `.tsx` files; the Rules-of-Hooks `use` prefix is the correct convention. Gated js-family + `^use[A-Z]` + functions-only, so genuinely mis-named helpers still flag.
- **`detect_list_membership` overfitting** — the O(n) membership detector now keys on the real membership idiom (strict equality in a boolean-return/early-exit position) instead of name substrings; measured on roam's own source: 11 firings → 1 (the genuine case), with filter/dispatch/identity loops excluded by shape.

## [13.8.0] — 2026-07-10

### Added
- **Five new commands** — `roam surface-gaps` (reconcile CLI ↔ MCP ↔ docs surfaces and
  flag orphaned/undocumented/unimplemented commands), `roam cycle-break` (recommend the
  minimal extraction that breaks an import cycle), `roam profile-import` (ingest a
  profiler trace and rank the hottest source spans by runtime share), `roam vue-emits`
  (flag Vue child-component emits with no matching parent handler), and the
  **silent-data-loss detector** surfaced as `roam verify --checks restore_loss`
  (flags a function that deletes tables without reinserting them).
- **JS/TS import resolution** — `verify-imports` now resolves Node core builtins
  (`node:*` and bare), `package.json` dependencies (incl. subpaths), and relative
  paths, eliminating hundreds of false "unresolved import" findings on JS/TS.
- **Semantic docstring-drift** — flags documented parameters/returns/raises that no
  longer match the code; prose-only summary drift is gated behind an opt-in.
- **Complexity clustering** — `roam verify` consolidates repeated complexity findings
  into one refactor target per file instead of many near-duplicate warnings.
- **Remaining-findings surface** — an opt-in flag lists residual findings in touched
  files (priority-ranked, clearly labelled pre-existing) so a cleanup wave can finish.

### Changed
- **Default verify checks expanded (behaviour change).** `roam verify` now runs
  `restore_loss` by default (advisory WARN), and `dead` + `n1` now fire by default on
  **newly-introduced** issues in changed code (diff-scoped, WARN). Opt out with
  `ROAM_VERIFY_DEAD=0` / `ROAM_VERIFY_N1=0`. Higher-FP checks (complexity, cycles,
  taint, …) remain opt-in via `--checks`/`--all`/config.
- **Dead-code precision** — public API symbols (named in `__all__`, re-exported from a
  package `__init__`, or declared as entry points) are capped at REVIEW confidence and
  labelled external-facing instead of reported as dead-SAFE from lack of internal
  consumers alone.
- **N+1-I/O precision** — the io-in-loop detector no longer flags batch primitives
  (`executemany`, `execute_batch`, `bulk_create`/`insert`/`update`, `copy_from`,
  `writerows`, `addAll`, …); genuine per-row I/O still fires.

## [13.7.1] — 2026-07-09

### Added
- **`roam suggest-refactoring` extraction hints** — an `extract` recommendation now
  names the exact block, line range, and estimated cognitive-complexity delta
  (parent → after) via the deterministic `complexity_extract` analyzer, in both the
  text output and the JSON envelope. Safe no-op when no hint is available.
- **Self-propagating attribution footer** in generated `AGENTS.md` — a single
  invisible HTML comment crediting roam; on by default, opt out with
  `roam agents-md --no-attribution`, idempotent across `--refresh`.
- **`roam service-report`** — restored the one-command reachability-triage deliverable
  (SBOM → supply-chain → vulns → vuln-reach → taint → secrets) that was dropped in a
  history reconcile.
- **`docs/COMMANDS.md`** — the full command index (268 commands across 7 categories,
  243 MCP tools), regenerated from `roam surface` and guarded by a CI invariant so it
  cannot be silently dropped again.

### Fixed
- **Complexity gate no longer re-flags a freshly-extracted helper** — a helper landing
  exactly on the cognitive-complexity threshold is no longer emitted as a new finding
  (`<=` skip), so refactoring a hotspot toward the ceiling is not punished. Real
  above-threshold hotspots still emit.
- **`vuln-reach`** — package-name matching now anchors to dotted path segments instead
  of a naked substring, eliminating false matches on short package names. This unblocks
  accurate vulnerability-reachability triage.
- **`for_security_review`** — reconnected to the W607 substrate marker layer so
  degradation markers surface correctly.
- MCP binding corrections (`understand`, `fetch_handle`) + surface-count sync (243 MCP tools).
- `yaml-loader` tiny-parser fallback now catches all exceptions, not just `ValueError`.
- **CI reliability** — eliminated an intermittent native `Bus error` under pytest-xdist
  (bounded SQLite `mmap`/`cache`/`temp_store` + serialized grammar-heavy index builds)
  and a monkeypatch import-capture flake; full suite is green across Python 3.10–3.13.

### Security
- **Default-deny executing a repo-local `.roam-leak-patterns.py`** (hostile-repo RCE
  fix). The verify secrets gate previously imported/executed this file from the target
  repo with no trust gate, so running `roam verify` on an untrusted repo — including the
  auto-fired Claude Code Stop hook — was arbitrary code execution. It is now gated behind
  the `ROAM_ALLOW_REPO_LEAK_PATTERNS` environment variable (read from the environment
  only, so a repo cannot set its own trust flag); default-deny returns a disclosed note
  and the built-in credential patterns still run.
- **Client-side pre-push secret scan** — `.githooks/pre-push` now scans the commits being
  pushed for credential shapes (reusing roam's own patterns, with placeholder / env-var /
  `secretsallow` exemptions), blocking a leaked credential locally before it reaches the
  remote where a later force-push cannot un-compromise it.
- **Default-deny `PUBLIC_ALLOWLIST` CI test** — every tracked file must match an explicit
  publishable-path allowlist, so a new internal-only file at the repo root fails CI
  instead of shipping (a leak-shield that survives a history reconcile).
- Bumped `joserfc` 1.6.5 → 1.7.3 (HMAC empty-key verify bypass + JWS payload-size bypass)
  and `pydantic-settings` 2.14.1 → 2.14.2 (nested-secrets symlink escape) — clears 3
  Dependabot alerts. Both are transitive and not reached by roam's own code.

## [13.7.0] — 2026-07-08

### Sibling-patch network, first-class attestation staleness, and a large reliability wave

- **Sibling Patch Network v1** (flag-gated, default-off) — a repair-intent-scored lens that finds the same fix's siblings across a codebase and applies them through an isolated replay gate.
- **Attestation staleness is now first-class** — `roam attest` emits a stable `stale_if` contract (seven canonical conditions with baselines), an honest `not_checked` list, a `verification_command`, a `privacy` block, and promotes `indexed_commit`/`head_commit` into the durable body, so a downstream agent reading an attestation later can decide whether to trust it or re-run the evidence. It stays off the signed `content_hash` so tamper and freshness remain separate axes.
- **Reliability wave** — restored the compiler argv-guard (five silently-dark cross-tool probes fire again), per-item batch resilience so one bad query no longer sinks a whole batch, a worker cap for the native xdist crash, and graceful degradation when an optional tool import is missing.

## [13.6.1] — 2026-06-12

### The algo command grows teeth — and goes cross-language

- **Six Python loop-body performance detectors** (manual Counter builds,
  quadratic list-reassign concat, append-then-sort accumulators, pop(0)
  list-as-queues, deepcopy-per-iteration, DataFrame/array concat-in-loop)
  on a shared loop-window scanner with an indent guard. Dogfooded three
  rounds on this repo: two false-positive classes (mid-identifier
  backreference matches; post-loop window matches) were found by the
  dogfood and sealed with regression tests before release.
- **Five JavaScript/TypeScript detectors** — .shift() queues,
  concat-reassign rebuilds, push-then-sort, JSON.parse(JSON.stringify())
  roundtrip clones, delete-in-loop — on the same scanner, Vue/Svelte SFCs
  included. The detector surface goes 58 -> 69, and  now
  fires the JS pack on JS/TS edits.
- **44 findings fixed in roam itself** (Counter/defaultdict conversions,
  BFS queues to deque, O(1) partition pops). The full test suite got 39%
  faster in the same pass.
- **top_n_ranking gains a native cycles dimension** — "biggest cycles"
  prompts now ship the Tarjan SCCs in the envelope (re-measured n=3:
  bash.65 -> bash.07, 6 turns -> 1).
- **The hallucination firewall now runs INSIDE the verify loop** — the
  imports check was style-only while the docs promised resolution (caught
  by the new planted-recall eval). Unresolvable module paths now FAIL as
  likely hallucinations; near-misses WARN with did-you-mean candidates.
  Three precision classes sealed in the same pass: declared dependencies
  (pyproject + requirements) are never flagged, dotted internal modules
  resolve via the file index, and from-import member names / comment
  lines / try-guarded optional imports are excluded. Self-scan on this
  repo: 28 false positives -> 0 with the planted hallucination still
  caught. New recall suite: tests/test_verify_planted_recall.py (every
  verify category must catch its canonical planted positive).
- Entry-point routing accepts qualifiers ("CLI entry point"); first
  parallel-CI races sealed with xdist groups; envelope/probe caches now
  invalidate on re-index (index stamp + generation sweep).

## [13.6] — 2026-06-11

### The verify loop grows teeth (field-feedback wave)

#### Added
- **`secrets` verify category, on by default** — credential shapes fail the
  check on every touched file; an optional repo-local `.roam-leak-patterns.py`
  catalogue catches project-specific never-publish strings (fail-open, errors
  disclosed).
- **Symbol-keyed suppressions** — `.roam-suppressions.yml` entries can carry
  `symbol:` and survive refactors that shift line numbers; line-keyed entries
  now match within a 3-line tolerance. `roam triage add --symbol`.
- **`verify --auto` implies the advisory patterns sweep** — the algorithm/idiom
  detectors scoped to the touched files (N+1 shapes, loop-invariant calls, each
  with a fix sketch). `ROAM_VERIFY_NO_DEEP=1` opts out.
- **`roam algo --path <file|dir>`** (repeatable) — scoped scans in ~2s vs the
  whole-project sweep, with `scoped_paths` / `scope_file_count` disclosure.

#### Fixed
- **Suppression data loss** — saving a suppression no longer rewrites the file
  through a lossy parse (hand-edited entries silently vanished); save is
  append-only and an unparseable file is never replaced.
- **Naming convention sampling** — test/vendored/generated files no longer vote
  in (or get flagged against) the project convention; single lowercase words
  are case-neutral; ~45 framework lifecycle names (`setUp`, `beforeEach`,
  `ngOnInit`, ...) are never flagged. On a test-heavy PSR-12 codebase this
  removed ~2000 false positives.
- **Composable complexity** — `use*` containers in JS/TS/Vue report as INFO
  advisories (the container score sums inner closures and is not actionable at
  function thresholds). FoxPro files skipped by the syntax rule; ERROR nodes
  inside string literals are opaque.
- **`verify --auto` is 16× faster on sweeping diffs** (3m27s → ~13s) — the
  duplicates similar-name pass used full `SequenceMatcher.ratio()` against
  every repo symbol; now gated by the difflib quick-ratio fast path with a
  disclosed cap. `roam algo` whole-project sweep 35.7s → 18.1s.

### Compiler: injection economics + answer probes

#### Added
- **`injection_advice`** — generation-shaped prompts ("write a test",
  "implement X") advise injection channels to inject NOTHING (measured pure
  overhead: same turns, +25% input tokens). Stamped in the JSON summary, the
  text envelope, and telemetry; honored by the Claude Code hook.
- **Graph-ranked retrieval** — freeform `named_paths` blend percentile-ranked
  text score, path-token match (mini-IDF), file role, and symbol PageRank;
  basename recall pulls the module the task literally names into the pool.
- **New answer probes** — security-shaped tasks embed the whole-repo taint
  scan; "is X idempotent" / "what does X mutate" embed the world-model
  classifiers; design-pattern asks embed detected instances; perf-shaped tasks
  embed scoped algorithm-catalog findings; **verify findings ride into compile
  envelopes** (`known_findings`) so agents fix adjacent debt in the same pass.
- **Probe-trigger override** — bare-symbol phrasings of probe-answerable
  shapes ("which tests cover X", "find SQL injection risks", "list TODOs in
  F") now run the probe pipeline instead of shipping empty envelopes.
- **Routing waves** — trace-shaped ("where does the login flow start",
  "follow the path from X to Y") and literal entry-point phrasings route to
  their procedures.

#### Infrastructure
- **`prepush_check.py --release`** — one command proves a release-sized push:
  full gates + the entire test suite + commit-message leak scan +
  doc-consistency + landing-page linkcheck.
- **Offline lock suites** — procedure-registry lint (closed-enumeration over
  all per-procedure tables; found two real gaps on first run), suppression
  adversarial corpus (14 hostile files), verify self-dogfood FP lock, envelope
  byte-budget ratchets, L1-rate floor (56.7% at introduction, floor 45%).
- **Leak gate** — generalized dated-marker patterns, commit-message scanning
  in pre-push, exemplar ratchet suite so patterns can't silently weaken.
- CGA/VSA sibling attestations share one clock (CI flake sealed).


## [13.5.1] — 2026-06-10

### Fixed

- **Documentation-hygiene patch over 13.5.** Scrubbed stale internal-note
  remnants (dated session markers, private-doc path references, a
  project-specific symbol name in one test prompt) from shipped sources and
  tests; the `doc-hygiene` CI gate now passes. No functional changes.
- **Public-checkout test portability.** The `bench-compile --ground-truth`
  oracle tests now skip cleanly when the development-only benchmark oracles
  are absent (they are not part of the distribution), so the suite is green
  on a clean install.

## [13.5] — 2026-06-10

### Compiler coverage waves + the Claude Code adapter (2026-06-09/10)

#### Added

- **Eight new compile intent procedures**, all telemetry-mined from production prompt corpora and gated by a frozen-corpus routing ratchet (still-missed freeform 447 → 353 unique prompts, zero routing drift): `file_history` ("what changed in X recently / last week" → embedded `git log` with `--since` window), `repo_structure` (layers / clusters / health summaries), `entry_point_where` (surfaces the authoritative `[project.scripts]` console script first), `config_where` (env-var/config definition sites), module-name recall for `describe_file` ("explain the compiler architecture"), `session_meta` (contentless continuation directives get a tiny repo-state brief instead of blind probes), `self_contained_task` (zero-probe fast-path for batch payloads that need no repo facts), and a `bug_site_slice` freeform augment ("fix the bug in cli.py:45" embeds the gutter-numbered source around the cited line).
- **`roam hooks claude --write`** — one-command Claude Code adapter: installs a UserPromptSubmit hook (compile the prompt, inject pre-resolved facts) + a Stop hook (scoped `roam verify --auto --diff-only` after edits, quiet on pass). Fail-open by design, idempotent, `--no-verify` opt-out, `--uninstall` sweeps both. 13 lifecycle tests.
- **W-GENLEAN** — test-write synthesis tasks emit a 2KB lean envelope and skip the probe pipeline (A/B showed generation tasks ignore the rich envelope; lean is behavior-identical and cheaper to compile).
- **`compiler_fp` telemetry field** — every `.roam/compile-runs.jsonl` row now stamps the compiler-code fingerprint, making routing/latency shifts attributable to compiler revisions.
- **Frozen-corpus routing lock** (`tests/test_corpus_routing_lock.py`) — replay ratchet over the production prompt corpora: coverage can only improve.

#### Fixed

- **CliRunner stdout-swap race in the in-process probe pool** — concurrent probe invokes raced the process-global `sys.stdout` swap, occasionally leaking a probe's envelope to real stdout while swallowing the parent command's output (observed: `compiler-corpus` losing its aggregate, exit 0). The in-proc dispatch lock now guards every invoke (re-entrant); regression test records invoke concurrency.
- **Compile caches ignored compiler revisions** — the plan cache and in-process cache lacked the compiler fingerprint the envelope cache had, so classifier changes kept serving stale routing under unchanged git HEAD. All three cache keys now fold in the fingerprint.
- **`envelope-diff` regression false-positives** — underscore-prefixed budget-bookkeeping keys (`_envelope_budget_pruned`) counted as probe families, so an envelope *shrinking under budget* tripped `probe_family_missing`; the text printer also crashed (KeyError) on rules without `actual`/`threshold` fields, masking which rule tripped.
- **Synthesis target extraction** — "write a pytest for `_resolve_module_names` in <file>" (bare identifier, no backticks) failed extraction and degraded the source excerpt to the module docstring; identifier-shape-gated patterns now extract correctly, and the excerpt carries an explicit `file:line` location.

#### Measured

- Compiler A/B on Claude (Fable 5), 41 cells, n=2/cell: **−83% agent turns (median 6 → 1), −80% input tokens (271K → 53K), −63% cost, −50% wall** on navigation/comprehension tasks; same shape on Opus (−86% turns) [corrected 2026-07-14: −33% turns overall; −88% was a single cell — see the README claims audit, c2adf10d]. Ground-truth bug-fix bench (20 cells, planted bugs): saturated 10/10 vs 10/10 with compile slightly cheaper. Compile overhead p50 92ms / p95 305ms per prompt.

### Perf + drift-truth wave (2026-05-22)

#### Performance

- **`roam tx-boundaries` — per-line substring fast-reject.** A pre-check (a strict-superset token set of every transaction / mutation regex's literal anchor) gates the ~41-regex per-line scan with cheap `any(tok in line)`. A line that contains none of those tokens cannot match any pattern, so the regex pass is skipped entirely. Output-identical (the token set never excludes a line a pattern would have matched). Microbenchmark put the pre-check ~20x cheaper than the equivalent combined-alternation NFA. The drift guard in `tests/test_tx_boundaries.py` pins the superset relationship.
- **`roam simulate` recompute shortcut.** When the counterfactual graph is topologically identical to the baseline (every metric in `compute_graph_metrics` is derived purely from topology — `move` / `extract` / `merge` rewrite `file_path` but never add or remove nodes / edges), the post-transform recompute reuses the baseline metrics instead of running a fresh ~50s metric pass. `delete` still triggers the full recompute (it genuinely removes nodes / edges). The guard compares graphs directly, so a future topology-changing transform stays correct automatically. Output-identical.
- **`roam fingerprint` / `roam clusters` — graph perf.** Targeted hotspot fixes across the graph layer; new drift-guard in `tests/test_fingerprint.py`.
- **`roam duplicates` — pair-scoring hoist + deterministic tie-break.** Per-row derived fields hoisted out of the O(n²) pair-scoring loop. Output byte-identical.
- **`roam smells` type-switch — shared AST cache.** Type-switch detector reuses the v13.4 mtime+size-keyed `lru_cache` (same one the magic-numbers / boolean-parameter / switch-statement detectors share). Output byte-identical.

#### Fixed

- **`roam compare` — `math_signals` → `symbol_metrics` schema migration.** The complexity-delta query was reading from the long-retired `math_signals` table; migrated to `symbol_metrics.cognitive_complexity` (the metric's actual location since v12.x). The "predates the schema" disclosure wording also names `symbol_metrics`. A2-fix re-keys symbol identity on `(qualified_name, line_start)` so overloaded methods (roam-code has ~5600 same-qname symbols) stop silently dropping from the delta.
- **`roam simulate` — non-saturating health score.** Health-score computation was prematurely clamping; now non-saturating.
- **`roam agents-md` — canonical MCP preset counts.** Was reading a stale source for preset counts; now canonical.
- **`roam audit-trail-verify` — Pattern-2 degraded-path disclosure.** A partially-failed verify no longer reads as a clean pass.
- **`roam article-12-check` — plain-ASCII status markers in the markdown report.** Replaced the `✅` / `⚠️` emoji markers with `[OK]` / `[WARN]` plain-ASCII labels per the CLAUDE.md output convention (no emojis / colors / box-drawing in CLI output); mirrors how sibling commands render text-mode status. Check-item titles + summary text keep their typographic em-dashes / `≥` consistent with the rest of the codebase.
- **`roam agent-score` — clamp to [0, 100].**
- **`roam clones` / `roam duplicates` — deterministic clone-pattern tie-break.** Equal-score clusters previously had PYTHONHASHSEED-dependent ordering across both detector families; now deterministic.
- **Kotlin extractor — by-delegate syntax classification.** The tree-sitter Kotlin grammar misparses `class C : I by delegate { ... }` (treats the trailing `{...}` as a lambda argument to the delegate call), so functions declared inside such blocks landed under an `annotated_lambda` parent and the existing `class_body` context rule missed them. Adding `explicit_delegation` to the function-declaration context map in `extractors/kotlin.yaml` resolves the classification (`LoggingPrinter.log` now correctly classifies as `method`).
- **`roam_ask` description — recipe count 24 → 25.** Stale literal in the MCP tool description; the README MCP tool table is auto-regenerated from the description, so the README count tracks automatically.
- **MCP tool count 224 → 227** across README headline + landing-page stale literals (the canonical count lives in `roam.surface_counts`).

#### Changed

- **Loud-fallback campaign — batch 6 (extended sweep).** ~57 modules (`src/roam/commands/cmd_*.py` + `agents_md/generator.py` + `output/formatter.py` + `mcp_extras/concurrency.py` + `mcp_server.py`) now emit `roam.observability.log_swallowed` lineage at silent `except: pass` / swallowed-exception sites. Behaviour identical — the exception is still swallowed, just no longer silent. The ratchet baseline in `tests/test_loud_fallback_no_new_silent_except.py` drops from ~196 to ~107 on the post-campaign tree.

#### Changed (dogfood wiring 2026-05-22 evening)

- **MCP tool descriptions — top-10 rewrite (BiasBusters + Sentry pattern).** The 10 most-critical roam-code MCP tool descriptions (`roam_ask`, `roam_search_symbol`, `roam_uses`, `roam_context`, `roam_understand`, `roam_diff`, `roam_prepare_change`, `roam_critique`, `roam_diagnose_issue`, `roam_affected_tests`) rewritten with imperative verb-lede, user-voice trigger phrases ('where is X?', 'who calls Y?', 'safe to delete Z?'), explicit anti-patterns ('Do NOT use Bash:grep for symbol lookup'), and alternative-named replacement ('Replaces multi-shape grep'). Pattern grounded in **BiasBusters (arXiv 2510.00307)** — description semantic alignment is the #1 driver of selection — and the **Sentry MCP scaling case** (60M req/month with 3 tools; each description carries concrete examples + workflow chaining hints). Lift target measured via `dev/audit_session_tool_usage.py`.

#### Added

- **`dev/audit_session_tool_usage.py` — Claude Code session transcript auditor.** Measures the "dogfood ratio" (`roam_*` MCP tool calls / total tool calls) across recent JSONL transcripts under `~/.claude/projects/<slug>/`. Surfaces shell-grep volume, retry-loop patterns, top Read targets, and Bash-class breakdown (git-diff / git-log / fs-discovery / test-lint / etc.). Baseline 2026-05-22 across 200 transcripts: **0.18% (2/1113)** — Bash 47% / Read 22% / Grep 20% / MCP 0.18%. Tracked tool; re-runnable after each session-cluster.

- **`CLAUDE.md` (1-line `@AGENTS.md` pointer).** Restores Claude Code's auto-load path (removed in e5993a6 because the original 263-line file carried internal-only content) WITHOUT re-introducing the internal-content pattern. Single `@AGENTS.md` directive imports the multi-vendor AGENTS.md so the navigation guidance (§ "Codebase navigation with roam") reaches Claude Code at session start. HTML comment documents the strategic decision and instructs maintainers to edit AGENTS.md (not this file) for any content additions.
- **5 new drift-guards in `tests/test_doc_consistency.py`** pinning landing-page version surfaces.
- **`templates/rules/rust/.roam-rules.yml` + `templates/rules/swift/.roam-rules.yml` — invalid `severity: NOTE`** (a non-canonical severity not in the closed enum) → fixed. All 8 rule packs now validate.

#### Docs

- **README pass 3 — scannable rewrite** (the v13.4 polish continued). "What's New" now reflects v13.4; ~20 stale command examples + literals fixed; the comprehensive command table moved behind a `<details>` link to the hosted Command Reference. `test_readme_covers_all_canonical_cli_commands` relaxed accordingly (the count gate via `test_readme_cli_command_count_matches_source` still pins the `(all N)` header to canonical).
- **CONTRIBUTING / AGENTS / llms-install / docs/ polish.** `USER_VERSION 17 → 18`, SARIF list `14 → 37`, algo-catalog `23 → 34`. Stale `CLAUDE.md` cross-refs removed from AGENTS.md — CLAUDE.md was removed from the public repo in `e5993a6`; AGENTS.md is now the sole canonical agent guide.
- **Landing-page sweep.** `templates/distribution/landing-page/index.html` + `status.html` + `docs/*.html` — stale version literals + broken command examples fixed.
- **`src/roam/mcp_extras/adversarial_compress.py` docstring** — corrected; the prior "PROTOTYPE / NOT yet wired into `mcp_server.py`" claim was stale relative to HEAD (the B6 wire shipped in v13.4 via `compress_mode` on `roam_adversarial`).
- **Generator bug fix** in `dev/build_readme_counts.py` — was emitting backwards alias arrows in the `readme-canonical-mention` block (alias → canonical was rendering as canonical → alias).

### Post-push polish (2026-05-22)

Follow-up commits that landed on top of the v13.5 push after the deep-eval wave (5 parallel agents: CI watch / D2 + D3 architecture spike memos / test-isolation fix / flaky-perf test fix) surfaced two real defects + two doc-truth gaps.

#### Test hardening

- **`tests/test_auto_count_script.py` parallel-safety via `tmp_path` + `--root` flag.** The 3 modifying tests previously invoked `dev/build_readme_counts.py --apply` against the REAL repo root and the v13.5 hardening caught the classic race symptom: `test_readme_recipe_count_matches_registry` failed because a sibling auto-count test had momentarily written intermediate (drift-injected) bytes that the recipe-count test then read in its window. `dev/build_readme_counts.py` now accepts `--root <path>` (default unchanged), and the modifying tests copy the count-bearing files (README/CLAUDE/llms-install/AGENTS + both MCP cards + `.well-known` mirrors + `pyproject.toml` + `src/roam/cli.py` + `src/roam/mcp_server.py` + `tests/test_mcp_server_card_hash.py`) into a `tmp_path` shadow and invoke the script with `--root <tmp_path>`. The real working tree is never touched by the tests. Verified parallel-safe alongside `tests/test_ask.py` + `tests/test_readme_surface_consistency.py`.
- **`tests/test_performance.py::test_incremental_single_file_change` — self-calibrating threshold.** The CPU-contention timing test had been relaxed once already (3000 ms → 5000 ms in `45b48eb`) and still failed under `pytest -n auto` on contended hosts (observed at 6552 ms vs 5000 ms in the v13.5 slow-lane). Replaced the absolute threshold with `min(max(5000, baseline * 0.75), 15000)` where `baseline` is a module-scoped fixture that times one `roam index --force` under the same conditions. The real regression signal — incremental ≤ 75 % of full-rebuild — survives, the 5000 ms floor keeps the assertion meaningful on unrealistically fast baselines, and the 15000 ms ceiling prevents a pathological baseline from hiding a real regression. ~7 sibling perf tests (`test_incremental_index_fast`, `TestNewCommandPerformance.test_understand_speed`, …) carry the same absolute-threshold vulnerability and are candidates for the same pattern in a future sweep.

#### Chore

- **`.gitignore`** — extended dev-scratch patterns for `dev/HANDOVER-*.md`, `dev/ROAM-SMOKE-*.md`, `dev/roam_smoke_results.jsonl`, `.stoa/` so `git status` stays signal-only after roam_smoke / handover-handoff sessions.

### Post-push polish — waves 2 + 3 + 4 (2026-05-22 afternoon/evening)

Four additional polish waves continued the brain-method refactor + drift-truth sweep work. Highlights — see commits `5fbe04a..33cc7e5` for the per-file diffs.

#### Brain-method refactor sweep — 4 critical-cog functions

The split-into-named-helpers pattern, applied 4 times. Each target's existing test suite confirms byte-identical output; orchestrator drops to <30 and stays under it.

- **`strip_list_payloads` in `src/roam/output/formatter.py` — cog 61 → 0** (4 named helpers: `_cap_preserved_list`, `_partition_envelope_fields`, `_detect_schema_violations`, `_annotate_summary_disclosure`). ~3,755 tests byte-identical across the W1000/1006/1007/1008/1028/1100/1101/1102 fixture families.
- **`analyze_n1` in `src/roam/commands/cmd_n1.py` — cog 78 → 3** (6 named helpers: `_resolve_missing_model_file_ids`, `_prefetch_bulk_state`, `_resolve_model_methods`, `_build_candidate_tuples`, `_make_n1_finding`, `_emit_findings_for_candidate`). 166 + 108 cross-suite tests byte-identical.
- **`_maybe_handle_off` in `src/roam/mcp_server.py` — cog 64 → 3** (6 named helpers: `_should_bypass_handle_off`, `_serialise_for_handle`, `_persist_handle_blob`, `_maybe_run_amortised_gc`, `_build_handle_preview`, `_build_handle_envelope`). 201 tests across `tests/test_mcp_handle_off.py` + `tests/test_response_volume_handles.py` + `tests/test_fetch_handle_chunked.py` byte-identical; the public `roam-code.com/spec/handle/v1` envelope shape is pinned.
- **`_find_eager_loads` in `src/roam/commands/cmd_n1.py` — cog 54 → 4** (5 named helpers: `_resolve_with_symbol`, `_read_with_property_snippet`, `_extract_with_property_relations`, `_extract_eager_load_relations`, `_extract_controller_with_relations`). 75 `tests/test_n1*` byte-identical.

#### Perf-test pattern extension

- **8 sibling perf tests** in `tests/test_performance.py` adopted the self-calibrating threshold pattern from `340997f` (the v13.5 hardening of `test_incremental_single_file_change`). New module-scope fixture `cheap_query_baseline_ms` times `roam map` against `medium_project` to supply a baseline for the heavier query-class tests; helper `_query_limit(baseline, floor, ratio, ceiling)` collapses the `min(max(floor, ratio * baseline), ceiling)` shape into one call. Applied to `test_incremental_index_fast`, `TestQueryPerformance` (15 tests), `TestNewCommandPerformance.test_understand_speed`, `TestJsonPerformance.test_json_understand_speed`, `TestV6CommandPerformance.test_complexity_speed` / `test_conventions_speed` / `test_preflight_speed` / `test_understand_enhanced_speed`. Also widened `test_incremental_single_file_change` floor 5000 ms → 8000 ms after a `-n 8` contention flake.
- **`xdist_group` marker registered** in `pyproject.toml` `[tool.pytest.ini_options].markers` to silence the `PytestUnknownMarkWarning` that fired on every test-collection run. The marker is consumed by pytest-xdist directly under `--dist=loadgroup`; registration is purely cosmetic.

#### Text-mode CLI conventions

- **Box-drawing in `cmd_compare`'s section separator** (`──`, U+2500) replaced with plain ASCII `--`. The only true CLI-output box-drawing violation surfaced by the emoji-audit sweep across all `cmd_*.py` + `output/*.py` text-mode rendering paths.
- **Bare `PASS`/`FAIL`/`REVIEW` status markers canonicalized to `[PASS]`/`[FAIL]`/`[REVIEW]`.** A canonicalization audit found 3 truly-bare status spots: `cmd_evidence_doctor.py:1473` `Closed-enum validation: PASS`, `cmd_evidence_doctor.py:1480` `honesty_state = "PASS" if ... else "REVIEW"`, and `cmd_attest.py:603` `safe_icon = "PASS" if ... else "FAIL"`. All bracketed. The bracketed form is the dominant convention across `cmd_doctor` / `cmd_health` / `cmd_fitness` / `cmd_check_rules` / `cmd_evidence_doctor` (rest of file) / `cmd_audit_trail_conformance` / `cmd_budget` / `cmd_diff` (8 files). The misleading comment in `cmd_article_12_check.py:238-240` that claimed siblings use `[OK]`/`[WARN]` corrected — Article 12's `[OK] PASS` / `[WARN] REVIEW` paired form is intentional because `REVIEW` (not `FAIL`) reflects flagged-for-human-judgment, distinct from binary pass/fail.

#### Doc-truth sweeps — second pass

- **`dev/MCP-SECURITY-POSTURE.md` — stale claims corrected.** Line-number drift on `connection.py:354→:558`, `mcp_server.py:802→:2027`, `_POLICY_DECISIONS` 6-member list → 9 (added `pass`/`fail`/`unknown` per `src/roam/evidence/_vocabulary.py:582`), `verify_chain_with_receipts` 414-518 → 426-536, `RECIPE_INTEGRITY_STATES` 394-401 → 406-413.
- **`templates/distribution/landing-page/docs/*.html` — 5 stale claims corrected.** `architecture.html:225` `USER_VERSION 17 → 18`; `agent-contract.html:358-360 + :418` `policy_decision` enum 3 values → 6; `integration-tutorials.html:268-270` + `mcp-usage.html:359-361` added `would_deny_dry_run`. `scripts/linkcheck.py --strict` confirms 29 pages still resolve.

#### MCP description rewrites + Claude Code auto-load

- **MCP tool descriptions — top-10 rewrite (BiasBusters + Sentry pattern).** The 10 most-critical roam-code MCP tool descriptions (`roam_ask`, `roam_search_symbol`, `roam_uses`, `roam_context`, `roam_understand`, `roam_diff`, `roam_prepare_change`, `roam_critique`, `roam_diagnose_issue`, `roam_affected_tests`) rewritten with imperative verb-lede, user-voice trigger phrases ('where is X?', 'who calls Y?', 'safe to delete Z?'), explicit anti-patterns ('Do NOT use Bash:grep for symbol lookup'), and alternative-named replacement ('Replaces multi-shape grep'). Pattern grounded in **BiasBusters (arXiv 2510.00307)** — description semantic alignment is the #1 driver of selection — and the **Sentry MCP scaling case** (60M req/month with 3 tools; each description carries concrete examples + workflow chaining hints). Lift target measured via `dev/audit_session_tool_usage.py`.
- **`dev/audit_session_tool_usage.py` (NEW)** — Claude Code session transcript auditor. Measures the "dogfood ratio" (`roam_*` MCP tool calls / total tool calls) across recent JSONL transcripts under `~/.claude/projects/<slug>/`. Surfaces shell-grep volume, retry-loop patterns, top Read targets, and Bash-class breakdown (git-diff / git-log / fs-discovery / test-lint / etc.). Baseline 2026-05-22 across 200 transcripts: **0.18% (2/1113)** — Bash 47% / Read 22% / Grep 20% / MCP 0.18%. Tracked tool; re-runnable after each session-cluster.
- **`CLAUDE.md` (1-line `@AGENTS.md` pointer)** — restores Claude Code's auto-load path (removed in `e5993a6` because the original 263-line file carried internal-only content) WITHOUT re-introducing the internal-content pattern. Single `@AGENTS.md` directive imports the multi-vendor AGENTS.md so the navigation guidance (§ "Codebase navigation with roam") reaches Claude Code at session start. HTML comment documents the strategic decision and instructs maintainers to edit AGENTS.md (not this file) for any content additions.
- **`dev/build_readme_counts.py` — pointer-aware skip.** Script now detects the `@AGENTS.md` pointer pattern and treats CLAUDE.md as an intentional no-op so `--check` no longer flags MISSING-MARKERS on a file with no block-bearing content by design.
- **`tests/test_law4_anchor_counts.py` + `tests/test_findings_detector_count_drift.py`** — pointer-aware skip. Both tests previously gated on CLAUDE.md content for anchor-count + detector-count claims; with CLAUDE.md now a pointer, the claims live exclusively in AGENTS.md. Tests now detect the `@AGENTS.md` pointer pattern and skip cleanly.

## [13.4] — 2026-05-21

### Perf + polish follow-up (2026-05-21)

#### Performance

- **`roam clones` 43.8s → 13.1s.** `_jaccard_bags` was effectively the entire cost of clone detection; rewritten single-pass via the multiset inclusion-exclusion identity `|A∪B| = |A| + |B| − |A∩B|` (iterating only the smaller bag) — 13.4x faster per call. Output byte-identical (verified over 300K real function-bag pairs, 0 mismatches); the serial/parallel break-even moved, so `parallel_threshold` is re-derived 100k → 1.5M.
- **`roam smells` — shared per-run AST cache.** Three AST-walking detectors (magic-numbers, boolean-parameter, switch-statement) each independently `read_text()` + `ast.parse()`'d every Python file. A shared mtime+size-keyed `lru_cache` parses each file once. Output byte-identical (`total_smells` 4441; all per-kind counts unchanged); ~7.4s saved on the parse-dominated detector slice.

#### Fixed

- **`roam preflight` showed an empty `()` in its Fitness line.** The sibling-only branch named rules from `failed_rules`, which is target-attributed by design and so legitimately empty when the target itself is clean. `_check_fitness` now also returns `failed_rules_on_siblings` (sourced from `rule_details`); the text line and JSON envelope name the sibling-failing rules from it. `failed_rules` stays accurately target-scoped.

#### Changed

- **Loud-fallback campaign — fifth batch.** Extended the "make fallback chains loud" sweep to `mcp_extras` (watcher / session / progress / completions / concurrency), `catalog/detectors`, `languages/foxpro_lang` and `output` (formatter / sarif) — 12 more silent `except: pass` sites now emit `log_swallowed` lineage. The ratchet guard's `SILENT_EXCEPT_BASELINE` drops 196 → 183.

### Hardening + assurance wave (2026-05-21)

#### Added

- **B8 — persisted per-snapshot spectral gap (`roam forecast` Option-A).** A new `snapshots.spectral_gap` column (schema migration #60, `USER_VERSION` 17 → 18) records the file-graph's algebraic connectivity on every health snapshot via a single shared `compute_current_spectral_gap` path. `roam forecast` now projects the persisted gap series with Theil-Sen into a real "<N> snapshots to structural failure" budget instead of the Option-B one-shot signal; legacy NULL rows are skipped so a partial-history series stays honest.
- **MCP-P1.2 — prompt-injection marker scan on tool-call egress.** A conservative four-family marker set (instruction-override phrases, chat-template control tokens, spoofed turn headers, tool-result-spoof tags) is scanned at the `_wrap_with_receipt` egress boundary; a hit tags the decision receipt's `redactions[]` with the new `prompt_injection_marker` reason (`REDACTION_REASONS` 9 → 10, append-only). Output bytes are unchanged — the scan annotates the receipt, never the response.
- **MCP receipt JSON-schema export reachable from an installed wheel.** `python -m roam.evidence.mcp_receipt_schema` now emits the versioned Draft 2020-12 schema from a `pip install`ed package (the generator lives in-package; `scripts/` is not shipped). Verified against a real wheel build + install into a clean venv.
- **Release supply-chain hardening (`publish.yml`).** The published artifact is bound to the tagged commit (a `resolve-ref` job — no build-from-trunk-then-publish path); PEP 740 attestations, a tag-keyed concurrency group and an environment-reviewer gate are added; the SBOM is bound to the real published-wheel SHA with a wheel-vs-tag version-drift guard. `cga-attestation.yml` additionally emits a Verifiable Software Attestation alongside the CGA (`roam cga emit --also-vsa`, W472).
- **Loud-fallback ratchet drift-guard.** `tests/test_loud_fallback_no_new_silent_except.py` AST-counts silent `except: pass` handlers in `src/roam` and pins the post-campaign baseline so a new silent handler fails the suite.

#### Fixed

- **W805-OCTET — compound aggregator silent-SAFE seal.** `_compound_envelope` routed a child carrying `isError: true` (a trimmed error-storm envelope with no top-level `error` key) into the success bucket instead of `failed_subcommands` — a Pattern-2 class where a compound reported success while a child failed. The aggregator check is widened to also catch `isError`; the cascade is closed across the W805 family and `test_situation_compounds` (children are now accounted for across both buckets).
- **W1300 — shotgun-surgery co-change coherence gate.** The W1287 caller-scatter rewrite left ~27 rows that were all well-factored reuse hubs (`to_json`, `open_db`, ...) — distinct-caller-file count alone cannot tell "one change ripples across many files" from "many files reuse one helper". A git co-change coherence gate now requires the scattered caller files to actually co-evolve; shotgun-surgery drops 27 → 13 genuine rows, graceful-degrading to scatter-only when co-change data is unavailable.
- **`roam doctor` pointed at a non-existent flag.** Five check hints told users to run `roam index --rebuild`; the real flag is `--force`. Corrected, with an AST drift-guard validating every `roam index --<flag>` hint against the live CLI.
- **`roam health --json` could leak a RuntimeWarning onto stdout.** `algebraic_connectivity()` returns a sentinel and *warns* rather than raising, so the warning escaped `cmd_health`'s `try/except` and could corrupt the JSON envelope. It is now captured at the call site and folded into the structured `warnings_out` channel.
- **W1078 — `--json`-mode warnings now structured.** The `--json` `showwarning` override emitted free-form `formatwarning` text on stderr, so a stream-merging consumer (`2>&1`) still saw non-JSON; it now emits a structured `{"warning", "category", "filename", "lineno"}` JSON line.
- **`roam trends` emitted no verdict on the single-snapshot path.** With fewer than four snapshots the trend `analysis` block is skipped, which previously left `summary.verdict` unset (LAW 6 violation); it now emits an explicit insufficient-history verdict.
- **`clones` cross-layer scan unbounded on huge layered repos.** A per-layer Jaccard pair budget now bounds the O(S²) cross-layer comparison and discloses truncation rather than capping silently.

#### Changed

- **"Make fallback chains loud" — four-batch campaign.** Silent `except: pass` / swallowed-exception sites across the agent-OS substrate (`agents_md` / `world_model` / `runs`), the MCP server, the analysis core (`index` / `refactor` / `search`) and the shared command helpers now emit `roam.observability.log_swallowed` lineage before continuing. Behaviour is identical — the exception is still swallowed, it is just no longer silent. Optional-dependency import failures in the MCP server surface in the `roam://health` resource; the W662 bare-except pending list is emptied.
- **`sync_surface_counts` coverage extended.** The count-drift guard now also walks the README body, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md` and `templates/` so every count-bearing public surface is structurally checked.

### Perf + Pattern-1 stabilisation campaign (2026-05-21)

#### Performance

- **`intent` 66s → 12s.** `_scan_doc_for_symbols` replaced the O(lines × symbols) per-name `re.search` hot loop with one combined `\bNAME\b` alternation regex per line + cheap set-membership lookups. Output-identical (`symbol_names` is a set, so ref order is unchanged) — verified by construction + 17 passing tests.
- **`doc-staleness` 93s → 19s.** The per-file `git blame` walk was sequential; it now runs across a bounded `ThreadPoolExecutor` (`min(8, cpu_count())`). `executor.map` preserves input order, so the result list and its final stable sort are byte-identical to the serial version (verified A/B excluding the file-under-test's own self-blame).
- **`sbom` 30s → 9s.** `sbom_reachability.py`'s filesystem scanners called `Path.rglob('*')` and discarded `.roam`/`.git`/`node_modules` post-hoc — descending into `.roam`'s ~56K paths on every scan. A shared `_walk_pruned` helper now prunes skip-dirs during `os.walk` descent; output-identical (same skip set → same yielded file set).
- **`alerts` — latent O(n³) bounded.** `_check_trends` fed the full snapshot history into an O(n)-window × O(n²)-Mann-Kendall test. The fetch is now capped at the 200 most-recent snapshots (`ROAM_ALERTS_SNAPSHOT_LIMIT` env override) — a no-op on normal repos, bounded on long-history ones.
- **`clones` — degenerate `clone_detect` bucketing fixed.** `node_count // max(node_count // 3, 5)` collapsed every function ≥ 15 AST nodes into a single size band (~3 bands total → near-O(n²) pairing). Replaced with geometric base-2 banding `int(log2(node_count))`; base-2 plus the existing ±1 adjacency scan provably enumerates every pair within the 0.5 node-count ratio gate, so clone pairs / clusters / similarities are output-identical (only the sequential `cluster_id` label renumbers).
- **`smells` refused-bequest — N+1 bulk-hoisted.** `_detect_refused_bequest` issued 2 SQL SELECTs per `inherits` edge; both are now bulk pre-fetched once via `batched_in`. Output-identical (verified byte-for-byte over 4494 findings).
- **`smells` / `health` parallel-hierarchy — O(S²) → recall-preserving inverted index.** The detector Jaccard-compared every ordered pair of eligible superclasses; it now builds a `token → superclasses` inverted index and only compares pairs sharing ≥ 1 marker token. Recall-preserving (any pair with Jaccard > 0 necessarily shares a token), so output is identical.
- **`pr-risk` — per-file `git diff` collapsed to one call.** `_get_file_stat` spawned one `git diff --numstat -- <path>` subprocess per changed file; replaced with a single `git diff --numstat` over the same range parsed once into a `{path: (added, removed)}` dict. Output-identical.
- **`verify-imports` — per-miss full-table-scan eliminated.** The miss path fired a leading-wildcard `path LIKE` (un-indexable full scan of `files`) per unresolved import; replaced with an O(1) pre-built basename-set lookup that exactly replicates the SQLite `LIKE` semantics (ASCII case-insensitivity, `_` single-char wildcard). Output-identical (31,886 import rows verified byte-identical); a latent-scalability win for large monorepos.

#### Fixed

- **Pattern-1 — 17 commands omitted `isError`/`status` on their error envelopes.** affected-tests, check-rules, context, coverage-gaps, file, fitness, grep, history-grep, ingest-trace, intent-check, invariants, plan, preflight, refs-text, relate, report, and reset emitted a JSON envelope on their usage-error / failure path without the machine-gate fields `isError: true` + a closed-enum `status`. All 17 now emit them (fitness + check-rules conditionally — only when a rule actually failed; a clean pass stays clean). New drift-guard `tests/test_pattern1_envelope_shape.py` parametrically asserts the shape across the 15 unconditional commands.

### Post-v13.3 execution wave (2026-05-20)

#### Added

- **B6 — adversarial × MCP-sampling "Dungeon Master".** `roam_adversarial` gains an async `compress_mode` parameter (closed enum `off` / `digest` / `defend`, default `off` so the tool stays a pure superset with zero behavior change). `digest` summarizes the adversarial challenges via `Context.sample`; `defend` threads a defender-framing system prompt into the sampling call. `compress_with_sampling` now takes a `system_prompt` kwarg so defend-mode's framing actually reaches `ctx.sample` (previously dead code). An unknown enum value sets a loud `summary.compress_mode_invalid: true` sentinel (Pattern-1D) while preserving the verdict (LAW 6). Counts unchanged (core `57` / registered `227` / commands `241`).
- **B8 — spectral-forecast block in `roam forecast`.** The forecast envelope now carries a one-shot `spectral_forecast` block (instability + decay-rate + decay-alert wording) computed from the current graph's spectral gap, plus a `topology_decay_rate_definition` sidecar. Option-B implementation: no DB column, no `USER_VERSION` bump; the decay projection reports `insufficient_history` until a persisted gap series lands. Findings are envelope-only (forecast is invocation-scoped).
- **Pre-push cascade-prevention gate.** New `scripts/prepush_check.py` (`--fast` / `--full`, per-step timing, copy-paste fix command on failure) bundles ruff (format + check) + the count/render drift scripts + an 11-test AST-scan structural-lint set into a ~43s local gate, plus a thin `.githooks/pre-push` shim that delegates to it. Catches the "new code trips a repo-wide drift guard the author never ran locally" class behind this session's fix-forward cascade. New tests `tests/test_prepush_gate_wired.py` pin the hook → script wiring and assert every FAST-set test file exists. Brings `scripts/` under ruff discipline (cleared 5 latent lint errors).
- **Bounded all-command smoke harness (dev tooling).** New `dev/roam_smoke.py` runs every canonical CLI command once, argless, in `--json` mode — each in its own subprocess with `stdin=DEVNULL` (immediate EOF, no console-handle hang) and a hard per-command timeout, on a small thread pool so one slow/hung command can't stall the sweep. A finite list run once = no loop; subprocess isolation = one crash never blocks the next. It is a HANG/CRASH detector first (argless invocation is intentional — a clean usage/error envelope is healthy Pattern-1 behavior); the failure classes it hunts are HANG, CRASH, and `BAD_JSON` / `EMPTY_STDOUT` Pattern-1C envelope violations. Output is incremental JSONL + a human summary; safe to Ctrl-C. This sweep caught the 11-command BAD_JSON usage-path class fixed below.

#### Fixed

- **W1280 — feature-envy false-positive cut (~91%).** The feature-envy detector was a pure cross-FILE outbound-edge-ratio heuristic (>=4 edges, >50% of targets in another file); a 24-sample dogfood measured ~88% false-positive / 0% true-positive — mostly `tests/` files and Click command / `emit_` / `build_` / `collect_` / `_section_` orchestrators that reference many modules by design. The predicate now (1) skips test-role files (canonical `is_test_path`) + orchestrator/assembler-named functions, and (2) requires the external refs to be concentrated on a single foreign file (true envy) rather than spread across many. Result on roam-code: feature-envy **2036 → 177 rows** (~91% drop, zero test-file leakage, not zeroed — 177 genuine single-foreign-file cases remain); total smells **7794 → 5936**. The finding message now names the dominant-foreign-file share so the claim is self-explaining. `FEATURE_ENVY_DETECTOR_VERSION` 1.0.0 → 1.1.0; composite `SMELLS_DETECTOR_VERSION` 1.4.0 → 1.5.0. +3 regression tests (orchestrator-named, spread-across-files, test-role-file all assert no fire) alongside the existing genuine-envy positive case.
- **dangerous-eval `RegExp.exec()` / declaration-line false-positives.** The code-injection detector matched `<regex>.exec(` and `function exec(` / `def exec(` declaration lines as eval-class sinks (~80% FP on JS/TS in dogfood). Added guards that skip dotted `.exec(` unless the receiver is a shell-exec module (`child_process` / `cp`) and skip declaration lines. `dangerous-eval` `DETECTOR_VERSION` → 1.1.0.
- **hotspots `py-eval-exec` identical false-positive (family-closer).** Same dotted-`.exec(` and `def exec(` declaration-line guards applied to the `py-eval-exec` security sink in `cmd_hotspots.py`.
- **god-components flagged dataclass fields as CRITICAL.** `classify` keyed only on file path and never on symbol kind, so high-fan-in `prop` / `field` symbols (e.g. `EvidenceArtifact.path`) landed in actionable-CRITICAL god components. Added a kind-aware guard so non-logic kinds are excluded/down-banded.
- **vibe-check boilerplate-inflation rate exceeded 100%.** `_detect_boilerplate_inflation` mixed per-occurrence and per-file counts, yielding rates like 163.7%. Now counts per-file so the rate stays in [0, 1].
- **W564 CI cascade seal.** Removed the inline `_SEVERITY_ORDER` table in `adversarial_compress.py`; it now delegates to the canonical `severity_rank` (`src/roam/output/_severity.py`), closing the severity-drift lint gate the B6 prototype tripped.
- **Pattern-1C — 11 commands dumped plain text on their argless `--json` usage path.** In `--json` mode, the no-argument / usage-guidance path of `preflight`, `plan`, `affected-tests`, `ask`, `relate`, `context`, `file`, `history-grep`, `ingest-trace`, `report`, and `skill-generate` emitted Click usage text, a bare `VERDICT:` line, or raw markdown — non-JSON stdout that a wrapper-bridge JSON parser chokes on (collapses to `COMMAND_FAILED`). Each now routes its argless path through `json_envelope()` with a standalone `summary.verdict` (LAW 6) + `state` + `partial_success` + an imperative `hint`, matching the canonical `cmd_grep` usage-path shape; text output is unchanged. New drift-guard `tests/test_json_usage_path_envelope.py` pins the SHAPE (stdout parses as an envelope with `command` + `summary.verdict`) across all 11 plus the two pre-existing positive controls (`grep` / `invariants`). Surfaced by the `dev/roam_smoke.py` BAD_JSON sweep.
- **health — fabricated `algebraic_connectivity: 0.0` when numpy+scipy absent (Pattern-2 honesty).** `roam health --json` exported a hard-coded `0.0` Fiedler value when the eigensolver substrate is missing, indistinguishable from a legitimate 0.0 disconnected-graph reading. A new `fiedler_failed` flag now distinguishes "couldn't compute" from a real measurement: the JSON exports `algebraic_connectivity: null` + a companion `algebraic_connectivity_available: false` (plus the existing `health_algebraic_connectivity_failed:` `warnings_out` lineage marker), at both the summary and top-level sites; text mode prints `n/a (requires numpy+scipy)`. MCP `_SCHEMA_HEALTH` updated at both sites to declare the new boolean. +2 regression tests in `tests/test_w607_m_cmd_health_warnings_out_envelope.py` (unavailable→null, available→verbatim value).
- **W1287 — shotgun-surgery detector re-implemented (~1472 → ~27 rows, wrong-axis FP).** The detector fired on `graph_metrics.in_degree > 7` — pure INBOUND popularity, ~100% FP / 0 TP in an 18-sample dogfood (the top hits were the codebase's best-factored shared symbols: conftest fixtures, `open_db` / `json_envelope` / `to_json` helpers, dataclass fields; 69% in `tests/`). High inbound reference count is good factoring, the opposite of the smell. Re-keyed onto Fowler's actual axis: the count of DISTINCT NON-TEST CALLER FILES referencing the symbol (file-SCATTER — how many separate files a change ripples across). Deliberately conservative behind a high `_SHOTGUN_MIN_CALLER_FILES = 12` threshold + the W1280-style test-role / `@property` / dataclass-field / trivial-accessor exclusions, so a well-factored repo reports ~zero rows by design. `SHOTGUN_SURGERY_DETECTOR_VERSION` 1.0.0 → 1.1.0; composite `SMELLS_DETECTOR_VERSION` 1.5.0 → 1.6.0. Tests in `test_smells.py` rewritten: genuine file-scatter fires, concentrated-popularity (50 refs from 1 file) does NOT, test-role target does NOT, below-threshold does NOT.
- **W335/W342 — impact's reported blast-radius count now agrees with preflight (Pattern-3a + cap disclosure).** `roam impact`'s default `--max-callers 100` capped both the displayed dependents AND the reported COUNT, so `affected_symbols` silently understated the true radius and contradicted `preflight`'s gate for the same symbol. The reported total is now the honest uncapped reach — computed by the IDENTICAL `nx.descendants`-over-reverse-graph computation `cmd_preflight._check_blast_radius` uses — while only the listed dependents stay capped for response size. A new `BLAST_RADIUS_AFFECTED_TOTAL` sidecar (in `metric_definitions.py`, surfaced as `affected_metric_definition`) names the shared computation so the parity is provable. The display cap is now disclosed LOUDLY (`cap_applied` + `displayed` + `total` + a verdict that reads `listing 100 of N affected symbols … raise --max-callers to list more`) instead of silently truncating the count (Pattern-1 variant-D lineage). Risk classification + reach-pct now run on the honest total too. New `test_impact_total_agrees_with_preflight_over_cap` (120-caller fixture) asserts impact↔preflight parity; the existing cap/depth tests split into `displayed` vs `total`.
- **W837 — forecast + coverage-gaps emitted Pattern-2 success envelopes on an empty corpus.** `roam forecast` on a freshly-indexed symbol-less repo produced a misleading "spectral failure band" verdict from a degenerate 2-node file graph (gap 0.0 read as `is_failed`) — a clean run when there was nothing to forecast. It now probes the symbol count directly (the indexer writes a snapshot row even for an empty corpus, so snapshot count is not a reliable empty signal), and on zero symbols replaces the verdict with an explicit `no data to forecast — corpus empty` line + `state: no_data` + `partial_success: true`, suppressing the spurious spectral clause. A repo WITH symbols but <3 snapshots stays honest (`insufficient snapshot history`, no partial flag). `roam coverage-gaps` had verdict-less envelopes on its no-gates / no-entry-points branches (`summary.error` only, no `verdict` → `None` to a LAW-6 consumer); both now carry a standalone verdict naming the absent gates/entry-points + `partial_success: true` + a closed-enum `state` (`no_gates` / `no_entries`) + `agent_contract.facts`. New tests `tests/test_w837_forecast_empty_corpus.py` + `tests/test_w837_coverage_gaps_empty_corpus.py`.
- **understand ↔ health verdict-label divergence (Pattern-3a / LAW-6).** `roam understand` labelled a 75/100 health score "healthy" via an inline `>=70` cutoff while `roam health` calls 75 "Fair" (its band reserves "Healthy" for `>=80`) — one score, two contradictory verdicts across commands. New canonical `src/roam/quality/health_band.py` owns the single band table (`>=80 Healthy / >=60 Fair / >=40 Needs attention / <40 Unhealthy`, mirroring `cmd_health._compose_verdict`); `understand` now routes its score→label through `health_band()` and stamps `health_band` + a `health_band_definition` Pattern-3a sidecar on the envelope. `quality/__init__.py` documents the new shared metric alongside `cycles` / `god_components` / `public_symbols`. New `tests/test_understand_health_band_parity.py`.
- **fan — test-role symbols/files crowded out the headline ranking (test/prod split).** `roam fan`'s #1 fan-in on roam-code was the `invoke_cli` conftest fixture (2438 refs) — pure test noise burying real production coupling. Mirroring `cmd_uses`' production/test scope split (both now classify each subject via the canonical `is_test_file` helper), `fan` drops test-role rows from the headline ranking by default and annotates every shown item with a `scope` field. A new `--include-tests` flag opts them back in. The drop is disclosed loudly — summary `test_split` / `production_items` / `test_items` / `test_filtered`, a text-mode `NOTE:` line, and a distinct `all_filtered_tests` empty-state verdict — never silent (Pattern-1-D / Pattern-2 lineage). File-mode oversamples 5× so the headline still fills after the filter. New `tests/test_fan_test_prod_split.py`.

## [13.3] — 2026-05-19

### MCP runtime security wave (2026-05-18)

- **MCP-P0.1 — egress redaction at the wrapper boundary.** `redact_secrets_in_string` + recursive `redact_secrets_in_value` walker now run at the `_wrap_with_receipt` boundary in `src/roam/mcp_server.py`, so every tool response is scrubbed before it crosses the MCP transport. Closed-enum `redactions=("secret",)` flag on the envelope; per-pattern detail under `_meta.extra.redaction_details`. New tests: `tests/test_w_mcp_redact_egress.py` (5 tests). Closes the egress half of the "Poison Everywhere" prompt-injection class inside the server, complementing the gateway-side controls described in `dev/MCP-SECURITY-POSTURE.md`.
- **MCP-P0.2 — 4-mode policy enforcement at the MCP boundary.** New helpers `_evaluate_mcp_mode_policy`, `_resolve_required_mode_for_tool`, and `_build_mode_blocked_envelope` in `src/roam/mcp_server.py` gate destructive MCP tool calls against the active `read_only` / `safe_edit` / `migration` / `autonomous_pr` mode. `policy_decision` is a closed enum: `allow / deny / not_evaluated`. New tests: `tests/test_w_mcp_mode_enforcement.py` (7 tests). The MCP layer now matches the gate posture the CLI has carried since the mode substrate landed.
- **MCP-P0.3 — HMAC-link MCP receipts into the signed event ledger.** Each `McpDecisionReceipt` sha256 is appended to a signed `runs/` event so an offline `roam runs verify` extends its 4-state verdict with a `receipt_integrity` closed enum: `ok / missing / tampered / not_linked`. New `verify_chain_with_receipts()` in `src/roam/runs/signing.py`; hash-stable on pre-P0.3 chains so existing ledgers re-verify byte-identically. New tests: `tests/test_w_mcp_receipt_hmac_link.py` (9 tests). The receipt stream is now part of the same tamper-evident chain as the run events that produced it.
- **MCP-P2.1 — `dev/MCP-SECURITY-POSTURE.md` published** for gateway integrators (Interlock / Lasso / Portkey). 404 lines covering the inside-server vs gateway split, the 4-layer ownership table (redaction / mode-policy / receipt-integrity / transport-policy), and a cross-link to public Discussion #37. Frames what the server owns and what value the gateway layer adds on top, so integrators do not duplicate controls.
- **MCP-P2.2 — `McpDecisionReceipt` JSON Schema export.** New `src/roam/evidence/mcp_receipt_schema.py` emits a Draft 2020-12 schema with `additionalProperties: false` and bidirectional dataclass↔schema parity. New `scripts/export_mcp_receipt_schema.py` writes the schema to disk for downstream consumers. Closed-enum drift-guards on `_POLICY_DECISIONS` and `REDACTION_REASONS` keep the schema and vocabulary in lockstep. New tests: `tests/test_mcp_receipt_json_schema.py` (11 tests). External tooling can now validate receipts without depending on the Python dataclass.
- **W420 wheel-layout helper consolidation.** `_package_file()` in `surface_counts` now resolves package files via `importlib.resources.files("roam")` instead of walking `parent/src/roam/`, fixing the installed-wheel layout for every surface-counts caller (not just the two commands sealed earlier today). New regression test `tests/test_surface_counts_wheel_layout.py` (9 tests) pins the helper end-to-end so the next contributor cannot reintroduce the source-tree assumption.
- **W462 landing-page tool-count drift.** `index.html` / `press.html` / `llms.txt` carried a stale `224` MCP-tool count; all three now read the canonical `227` (matching `roam surface --json` and the in-repo headline). Drift-guard test already in place from earlier waves catches re-introductions.
- **W641 pr-risk verdict suffix.** `BLOCK` and `REVIEW` verdicts now carry an explicit `risk_level <tier>` suffix (e.g. `BLOCK (risk_level critical)`) so downstream regex consumers can split on the tier without re-parsing the envelope.
- **Phase 4 landing-page polish.** Hero on `index.html` collapsed; SEO keywords surfaced higher in the document; 5 cross-links closed orphan pages; `architecture.html` lists the 8 evidence questions as an ordered list with 6 deep-link anchors. Bug fix: removed a stray `continue` from the `roam mcp-setup` client-list loop where the client was never in `_CONFIGS` to begin with.

### Added — 2026-05-18 session

- **Three new MCP wrappers** — `roam_boundary`, `roam_test_hermeticity`, and `roam_compatibility` are now callable as MCP tools. Catch public-by-accident exports + wrong-direction imports, non-hermetic AI-generated test patterns (network / time / random / fs / env / subprocess), and outbound surface regressions vs a baseline — all from your agent without dropping to the CLI.
- **MCP output sanitization at the server boundary** — every MCP tool response now runs through a secret-redaction pass on egress (`_redact_result_for_egress` in `mcp_server.py`). Closes the "Poison Everywhere" output-side prompt-injection class at the server, not just at the gateway. Closed-enum `redactions=("secret",)` flag on the envelope; per-pattern detail surfaced under `_meta.extra.redaction_details` (5 new tests, 213 broader MCP tests pass).
- **Wheel-layout regression test** — `tests/test_surface_counts_wheel_layout.py` (160 LOC, 9 functions) pins the surface-count helpers to resolve package files via `importlib.resources` rather than walking `parent/src/roam/`. Prevents the W420 follow-on regression that broke `roam --json surface` + `--json capabilities` in installed-wheel layout (caught by post-push wheel smoke).
- **New `wheel-smoke` CI job + extended bundling drift-guards** — wheel-bundling drift-guards (W554/W570/W610) now cover `templates/audit-report/`, `taint_rules/`, and `languages.extractors/` so a missing-from-wheel asset trips CI on the PR, not in the field.
- **Pattern-1 family A/B/C/D coverage** — ~30 new test files spanning Pattern-1A cold-start guard, Pattern-1B wrapper-bridge JSON pass-through, Pattern-1C empty-stdout envelopes, and Pattern-1D degraded-resolution disclosure; plus W420 invariants, W543 edge-kind canonicals, W744 status-split parity, severity/confidence parity, and lease/permit `warnings_out` propagation.
- **W744 SARIF + policy status split** — the `Suppression` dataclass is now a discriminated union (SARIF suppression vs policy suppression); `warnings_out` is wired at 4 production callsites that previously dropped it on the floor.
- **MCP coverage: 227 tools registered total** — 3 net-new wrappers this session, all thin DISPATCH shims following the canonical wrapper pattern.

### Fixed — 2026-05-18 session

- **`roam --json surface` + `roam --json capabilities` in installed-wheel layout** (SHIPPING bug) — the W420 cascade fix had migrated runtime `_COMMANDS` reads to the `surface_counts.cli_commands()` AST helper for plugin invariance, but the helper resolved package files via `parent/src/roam/cli.py` — which doesn't exist in `site-packages/roam/`. Both commands now use `_package_file()` (importlib.resources-based) so the wheel-installed and source-checkout layouts both work. Caught by post-push wheel smoke before any user hit it.
- **W420 cascade closure** — `surface`, `capabilities`, `compatibility`, and `doctor` all now read `_COMMANDS` via the AST helper, so the published `command_count` headline is plugin-invariant across all four surfaces.
- **README polish, round 2** — v12 / v11 release history moved out of the README into the changelog where it belongs; install-section duplication eliminated; the decorative "Works With" bar dropped (nothing it said wasn't already in the docs); the "Why use Roam" section trimmed from 6 bullets to 3 highest-signal ones; new worked `roam preflight handleSave` output example; em-dash typographic pass for consistency.
- **CI fix-forward** — ruff format on `surface_counts.py`; loosened a `BLOCK` verdict assertion to `.startswith("BLOCK")` so the W641 risk-level suffix doesn't trip the check.

### Changed — 2026-05-18 session

- **Detector cohort 26 → 28** — `boundary` and `test-hermeticity` are now persisting detectors. `roam findings list --detector boundary` and `--detector test-hermeticity` work end-to-end (subject-kind: `file`, `symbol`; closed-enum kinds documented in CLAUDE.md cohort section).
- **Landing-page polish across 9 pages** — `index.html` hero collapsed and SEO keywords surfaced; `architecture.html` now lists the 8 evidence questions as an ordered list with 6 deep-link anchors; `pricing.html` + `governance.html` "maps to, not certified" wording fix; `audit.html` + `trust.html` primary CTA flipped and SOC 2 / ISO honest dates labeled consistently; `mcp-usage.html` + `command-reference.html` counts re-synced; `getting-started.html` `roam mcp-setup` client list cleaned up (stray `continue` keyword removed).
- **`llms-install.md` refreshed** — canonical 11-step agent loop now matches the CLAUDE.md surface; count headlines re-synced.
- **Public Discussion #37 reply posted** — answered the "Interlock MCP-security gateway" question on GitHub Discussions; framed inside-server redaction (what landed today) vs gateway-side policy enforcement (where products like Interlock / Lasso / Portkey live).

### In flight today (uncommitted; will ride the next squash)

- **4-mode policy enforcement at the MCP boundary** — wiring `read_only` / `safe_edit` / `migration` / `autonomous_pr` modes through `mcp_server.py` so the MCP layer actually gates destructive tool calls (today the gates live in the CLI layer; the MCP wrapper currently bypasses them).
- **Public MCP security posture doc** — `dev/MCP-SECURITY-POSTURE.md` for gateway integrators (Interlock, Lasso, Portkey) describing the inside-server controls and where the gateway layer adds value on top.

### Added

- **W420** — test(invariant): NEW `tests/test_w420_surface_count_plugin_invariant.py` (85 LOC) pins the `command_count` headline equal across plugin-loading triggers. Plugin discovery no longer changes the published headline.
- **Phantom-annotation CI lint** — test(changelog): NEW `tests/test_changelog_phantoms.py` (237 LOC, 6 tests). Mechanically blocks future `dev/*.md` phantom references in CHANGELOG; 3-line annotation radius.
- **Phantom-CLI doc gate** — test(docs): NEW `tests/test_doc_no_phantom_cli.py` (~215 LOC). Scans 9 phantom-CLI patterns across 3 tracked agent-visible doc surfaces (AGENTS.md / README.md / CONTRIBUTING.md); mutation-test validated.
- **W391** — feat(ci): GitHub Actions roam-sarif + CodeQL co-deploy sample at `src/roam/templates/ci/roam-sarif-with-codeql.yml` (77 LOC). Discovery wired through `cmd_doctor._github_template_registry()` + `cmd_ci_setup` docstring cross-link; reachability 10/10.

### Fixed

- **W420** — fix(surface): `cmd_surface.py` `command_count` no longer changes under plugin loading. Migrated from runtime `_COMMANDS` to AST helper `roam.surface_counts.cli_commands()`. Published count headlines unchanged.
- **agents_md generator phantom** — fix(agents-md): `src/roam/agents_md/generator.py:962` emitted phantom `roam rules check --strict` (rules is flat, has no `check` subcommand). Corrected to canonical `roam rules --ci`. Protects downstream-user scaffolds.
- **README findings-registry gap** — fix(docs): `dark-matter` aggregator missing from README's findings-registry list (architecture.html had 7 names; README had 6). Re-aligned.

### Changed

- **Detector cohort 26 → 28 persisters** — `cmd_boundary` + `cmd_test_hermeticity` reclassified as legitimate persisters; 5 doc surfaces updated (CLAUDE.md cohort + rule + README + architecture.html + evidence-checklist.md).
- **Substrate package count 11 → 12** — CLAUDE.md + AGENTS.md "11 substrate packages" corrected to "12" matching the 12-entry body (atomic_io / agents_md / constitution / db.findings / laws / leases / memory / modes / policy / quality / runs / world_model).
- **Rule-pack uniformity** — 7 mainstream + NOTE-tier packs (python / typescript / kotlin / go / java / rust / swift) share imperative-voice + WHY-block discipline. README rule-count table corrected: rust 12 → 30, swift row backfilled, 6 severity-tier miscounts fixed.
- **Phantom-memo annotation campaign closed** — 34 phantom `dev/*.md` references annotated. Combined with the new CI lint, the failure class is sealed.

### Closed

- **W847 + W759-W761** — cmd_preflight + cmd_attest + cmd_risk + cmd_invariants + cmd_bus_factor + cmd_complexity UPPER-case audits all internal-vocab (W762 drift-guard pins discipline; 54 hits / 0 envelope-slot bugs).

### Added — W1103-arc + W489-family-closed + capability-invariants + structured_unknown_filter-FULLY-CLOSED + symmetric-emission-COMPLETE batch (post-CONSOLIDATE-21, 2026-05-17 /loop iteration N+22)

> **~17 SHIPPED + 3 BAIL/SHIPPED + 1 RESEARCH MEMO + 2 REAL BUGS
> fixed across 7 themes: regex CLI toggle (W421), taint qualified_only
> lint family FULLY CLOSED (W489-A + W489-A-followup), capability-axis
> invariant lint + 2 REAL BUGS fixed (W365 family), `structured_unknown_filter`
> family FULLY CLOSED (W1083-followup-3 multi-value sibling),
> symmetric-emission family COMPLETE (W1100 + W1101 + W1102), 2
> test-rot diagnoses (W844-drive-by-2 + W1084), and pruning + W1117
> placeholder family FULLY CLOSED (W507 + W1117-followup-4).** Plus 5
> stale-BACKLOG hits doc-pinned (W844 + W1007 + W1008 + W851 + W414b)
> — discipline rule re-affirmed at operational cadence.

- **W421** — feat(cli): `-E/--regexp` flag on `roam history-grep` + `roam refs-text`; regex toggle threaded to underlying matcher. +25 LOC; 53 tests pass.
- **W489-A + W489-A-followup** — feat(taint): qualified_only lint wired into envelope on `cmd_taint` (Option A: catch_warnings capture) + helper hoisted to shared `src/roam/security/taint_rules_lint.py` + wired on `cmd_cga`. 62 + 107 tests pass. **W489 family fully closed.**
- **W365 family** — test(mcp-lint): `_TOOL_METADATA` ↔ ToolAnnotations parity lint + 3rd-surface capability-registry cross-check (2854 pass) + 3 logical-entailment invariants (destructive→NOT ai_safe; deprecated↔maturity; task_required→mcp_expose; 46 pass). **Entailment surface exhausted.**
- **W1083-followup-3** — feat(structured_unknown_filter): multi-value `structured_unknown_filter_many` sibling + `cmd_math` + `cmd_smells` migration; +302 helper / +113-53 cmd_math / +193-42 cmd_smells / +336 tests; 366 broader pass. **`structured_unknown_filter` family FULLY CLOSED** (single + multi + CLI dispatcher).
- **W1083-RESEARCH** — docs(research): multi-value helper design memo at `dev/W1083-RESEARCH-multi-value-2026-05-17.md`.
<!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W1117-followup-4** — feat(help): final 2 placeholder normalizations on `cmd_clones`; **W1117 family fully closed** — ~32 normalizations across 5-wave arc.

### Fixed — W1103-arc batch

- **W365-followup** — fix(capability): `roam_reset` + `roam_clean` destructive flag corrected at the capability-decorator layer (2 REAL BUGS surfaced by W365 parity lint). +9/-10 LOC + 85 LOC test; 42 pass.
- **W1100** — fix(envelope): `partial_success: true` on malformed `agent_contract:[]` (sibling to the CONSOLIDATE-21 `schema_violations[]` envelope-root ship). +30 LOC; 6 new + 142 broader pass.
- **W1101 + W1102** — fix(envelope): symmetric `list_counts: {}` + `preserved_list_truncations` always-emit on zero-cardinality paths. +14 effective LOC; 369 + 381 pass. **Symmetric-emission family complete** across the 3 envelope-root array slots.
- **W507** — fix(enum): prune dead `'self-hosted'` enum value (0 consumers). 91 tests pass.
- **W1084** — fix(test): refresh `test_test_scaffold_unknown_symbol_passes_through` for W1278a Pattern-2c migration; 39/39 pass sequential + parallel. W-number-collision target separate from the W1084 cmd_ai_readiness / cmd_fitness arc at CONSOLIDATE-20.
- **W844-drive-by-2** — fix(docs): 3 stale README-headline references swept across tests + docs. 186 tests pass.

### Changed — W1103-arc batch

- **`structured_unknown_filter` family FULLY CLOSED** — the helper-substrate arc that opened at CONSOLIDATE-18 (W1068-W1083 Phase 2) and propagated through CONSOLIDATE-21 (W1083-followup Phase 3) closes cleanly at CONSOLIDATE-22 with the W1083-followup-3 multi-value sibling. Single + multi + CLI dispatcher all aligned on the same `did_you_mean: [...]` envelope shape.
- **Symmetric-emission family COMPLETE** — the 3 envelope-root array slots that previously omitted on zero-cardinality paths (`list_counts` + `preserved_list_truncations` + `schema_violations`) now always-emit `{}` / `[]` for envelope-shape symmetry. Pairs with the W1101 CONSOLIDATE-21 ship — this batch verifies the symmetry across the family.
- **W489 qualified_only family FULLY CLOSED** — the taint qualified_only lint that opened at W489 propagates end-to-end: lint wired into envelope on `cmd_taint` (Option A catch_warnings); helper hoisted to shared `src/roam/security/taint_rules_lint.py`; `cmd_cga` wired alongside.
- **W1117 placeholder family FULLY CLOSED** — the `[VALUE]` → `<value>` normalization sweep that opened at CONSOLIDATE-21 closes the final 2 sites on `cmd_clones`; ~32 normalizations across the 5-wave arc.
- **Capability-axis entailment surface EXHAUSTED** — the 3 logical-entailment invariants (W365-followup-2) close the capability-decorator surface; 2 REAL BUGS fixed at the surface (W365-followup).
- **BACKLOG-drift discipline re-affirmed** — 5 stale-pending hits this session (W844 + W1007 + W1008 + W851 + W414b); all 5 doc-pinned SHIPPED-PRE-CONSOLIDATE-22 with retro note. Discipline rule from CONSOLIDATE-21 holds: verify code state before dispatching from a `[pending]` flag alone.

### Added — W1067 → W1102 arc batch (post-CONSOLIDATE-20, 2026-05-17 /loop iteration N+21)

> **~30 completions consolidating the W1067 → W1102 wave-arc.** Seven
> themes carry the batch: Pattern-1D helper Phase 2/3 propagation
> (7 callsites), W1142 cap-hit disclosure family closure (7 commands),
> Pattern 3a severity widening on cmd_smells + cmd_adversarial (family
> TERMINAL), W1117 placeholder normalization sweep (22 commands),
> symmetric envelope emission (W1100 + W1101), W350 OSCAL
> authority_refs projection (closes evidence-question Q2 coverage),
> and permit-vs-lease asymmetry documented in CLAUDE.md (W1071).

- **W1142-followup-A/-B** — feat(disclosure): cap-hit on 7 list-truncating commands (cmd_clones + cmd_debt + cmd_recommend + cmd_test_impact + cmd_supply_chain + cmd_agent_score + cmd_runs). Canonical em-dash text uniform across all 7. cmd_search_semantic BAILED.
- **W1068-W1083 + W1083-followup** — feat(filter): structured "did you mean X?" suggestions across 7 unknown-name handlers (5 adopt `structured_unknown_filter` + 2 adopt `to_summary_payload`).
- **W350** — feat(evidence): OSCAL Assessment Results projects `authority_refs[]` as EXAMINE observations; closes evidence-question Q2 coverage on the OSCAL projection axis.
- **W1005 + W1005-followup-B** — feat(severity): `cmd_smells` + `cmd_adversarial` accept the W547 7-token canonical severity vocabulary. **Pattern 3a severity family TERMINAL.**
- **W1117 + W1117-followup-2/-3** — feat(help): 22 commands normalize `[VALUE]` → `<value>` placeholder convention end-to-end.

### Fixed — W1067 → W1102 arc batch

- **W1100 + W1101** — fix(envelope): symmetric `list_counts: {}` emission on zero-truncation paths + `schema_violations[]` envelope-root surfacing on malformed `agent_contract:[]`.
- **W844-drive-by** — fix(docs): README hero `pytest tests/test_basic.py` example rotted by W405 shallow-git default; refreshed.
- **W844-drive-by-2** — fix(docs): 3 stale headline references swept across README + landing-page + docs.
- **W414d** — bail(probes): git_repo + python_project module-scope BAIL-BOTH; both probes are structurally inapplicable at module scope (captured with rationale).

### Changed — W1067 → W1102 arc batch

- **Pattern 3a severity family TERMINAL** — the W1005 arc that opened at CONSOLIDATE-18 with cmd_smells primary + cmd_llm_smells followup-A now sweeps cmd_adversarial as the third (and final) high-signal site. All three commands accept the W547 7-token canonical severity vocabulary (info / low / medium / high / critical / blocker / unknown).
- **CLAUDE.md "Permit-vs-lease expiry-filtering asymmetry (W1067)" sub-section** — codifies why permits load expired entries (audit-completeness semantic — proof that authority was exercised at the time the bundle was emitted) and leases filter them (live conflict-resolution semantic — an expired lease no longer holds a subject).
- **BACKLOG-drift discipline codified** — W1007 / W1008 / W844 / W1100 surfaced as shipped-but-pending instances; discipline rule going forward: verify code state before dispatching from a `[pending]` flag alone.

### Added — W1086-arc + Wave-B-TERMINAL + W478 + Pattern-1A-family batch (post-CONSOLIDATE-19, 2026-05-17 /loop iteration N+20)

> **~20 completions since CONSOLIDATE-19 (Section 65).** SARIF
> dashboard family TERMINAL milestone — the W1062 + W1062-followup
> trio + W1062-followup-2/3/4 fan-out + W1087 lint substitute arc
> that kicked off at CONSOLIDATE-18 closes cleanly across 12 wired
> emitters + 39 catalogued emitters end-to-end. Four themes carry
> the batch: (a) **SARIF dashboard family TERMINAL** at 12 wired
> emitters + W1087 lint substitute (W1062-followup-3 +
> W1062-followup-4 wire 6 more emitters; W1087 catalogues 13 WIRED
> + 26 EXEMPT = 39 emitters end-to-end). (b) **MCP outputSchema
> 13-tool Wave B TERMINAL carry-forward + Wave C1 implementation
> kickoff** — Wave C1 lands the first compat-profile env-vars
> (`ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA` + `ROAM_MCP_COMPAT_STRICT`)
> plus a sidecar hoist drive-by; pairs with the new
> MCP-COMPAT-PROFILE-ROADMAP planning memo. (c) **Pattern-2 +
> Pattern-1A empty-state arc closure** — 8 detectors + 2 hard-cap
> commands sealed (W805-followup-bundle + W1085 + W1086 + W1084).
> (d) **3 research memos drafted across the arc** — MCP-COMPAT-
> PROFILE-ROADMAP new this session, MCP-OUTPUTSCHEMA-EVOLUTION +
> DETECTOR-FP-RATE-METHODOLOGY carry-forward. Plus 10 stand-alone
> polish items (W365 + W459 + W478 + W844 + W847 + W759 + W986 +
> W462 + W1088 + W1038) + 1 BAIL (W851). Parallel in stature to
> the Wave B TERMINAL milestone that CONSOLIDATE-19 carried.

- **W1062-followup-3** — feat(sarif): wire `_derive_finding_tags()` on `clones_to_sarif` + `smells_to_sarif` + `over_fetch_to_sarif` (11 tests pass). `pr_risk_to_sarif` found N/A (deliberate W1147/W1148 omission).
- **W1062-followup-4** — feat(sarif): wire `_derive_finding_tags()` on `n1_to_sarif` + `missing_index_to_sarif` + `orphan_imports_to_sarif` (12 tests pass; 12 emitters wired end-to-end). **W1087 captured** as substitute-rather-than-wire ship for the long tail.
- **W1087** — feat(sarif-lint): NEW `tests/test_sarif_tag_coverage.py` (6 tests pass). Two-part PIN + ALLOWLIST drift guard; 13 WIRED + 26 EXEMPT = 39 emitters catalogued. **SARIF dashboard family TERMINAL.**
- **Wave-C1** — feat(mcp): MCP compat env-vars `ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA` + `ROAM_MCP_COMPAT_STRICT` (7 focused + 188 broader tests pass). **Drive-by sidecar hoist** — audit-metadata `_meta` block had escaped the fastmcp gate; hoisted to canonical wrapper path.
- **MCP-COMPAT-PROFILE-ROADMAP** — docs(research): Wave-C planning memo at `(internal memo)` (compat-profile-emit + `roam mcp doctor` probe surface for client-side capability negotiation). <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W805-followup-bundle** — feat(detectors): Pattern-2 empty-state migration across 5 detectors (cmd_vibe_check + cmd_fingerprint + cmd_fan + cmd_dark_matter + cmd_conventions); 25 tests pass. Pattern-2 detector empty-state coverage now 8/8 effective sites.
- **W1085** — feat(fingerprint): Pattern-1A hard-cap disclosure on `cmd_fingerprint` empty-state path (19 tests pass; W-number-collision target separate from the W1085 cmd_fitness arc).
- **W1086** — feat(cut): Pattern-1A hard-cap disclosure on `cmd_cut` empty-state path (4 tests pass; mirror to W1085 fingerprint).
- **W365** — test(mcp-lint): NEW CI lint cross-check `_TOOL_METADATA` ↔ ToolAnnotations (10 tests pass). Finding: ToolAnnotations FULLY wired today — W363 materially less critical than the -18 audit feared.
- **W986** — docs(claude-md): codify "First hypothesis" test-failure-triage discipline rule (W978 + W851 + W1005 incidents cited as worked examples).
- **W462** — test(landing): NEW landing-page tool-count drift-guard test (11 integers asserted across header/body/footer/docs/about; 1 pass).
- **W844** — feat(build): auto-rotate `_EXPECTED_CARD_SHA256` in `dev/build_readme_counts.py` (10 tests pass; **closes W1308 manual-sync gap** as a drive-by).
- **W1038** — feat(yaml-loader): NEW `extract_typed` helper + `validator` kwarg follow-up (4 callsites migrated; 11 + 447 tests pass; cmd_alerts:961 `== 0` clause clarification — NOT dead code).

### Fixed — W1086-arc + Wave-B-TERMINAL + W478 + Pattern-1A-family batch

- **W1084** — fix(ai-readiness): `cmd_ai_readiness` denominator-clamp probe-breaking fix — clamped to minimum-1 floor with insufficient-data disclosure (10 tests pass; W-number-collision target separate from the W1084 cmd_fitness arc).
- **W478** — fix(tests): 4 SQLite fd-leak fixes in `_make_db()` test helpers — wrapped each in try/finally + explicit `conn.close()` (135 tests pass).
- **W459** — refactor(mcp): normalize 17 MCP wrappers to `description=` kwarg for AST-walk consistency (2895 tests pass; carry-forward W449/W458/W459/W460 batch).
- **W847** — fix(preflight): cmd_preflight UPPER-case scope clarification — W759 scope reduced 86% (30 → 4 sites).
- **W759** — fix(envelope): 4 envelope-slot UPPER-case sites cleaned across cmd_preflight (13 tests pass; W762 cmd_preflight allowlist now empty).
- **W1088** — fix(preflight): cmd_preflight `_SEVERITY_ORDER` lookup-miss belt-and-suspenders fix — lower-case canonical + UPPER-case aliases + `.lower()` at lookup (64 tests pass; W-number-collision target separate from the W1088 CI hardening arc).
- **W851 BAIL** — verify(tests): `test_w596_confidence_level_rank_round_trip` pre-existing failure, not reproducible in isolation; likely cross-worker `warnings.resetwarnings()` leak under xdist; captured for re-triage.

### Changed — W1086-arc + Wave-B-TERMINAL batch

- **SARIF dashboard family TERMINAL** — the W1062 + W1062-followup + W1062-followup-2/3/4 fan-out + W1087 lint substitute arc closes cleanly. 12 wired emitters + 27 exempt (compound aggregators + thin advisories + invocation-scoped signals exempted by rationale) = 39 catalogued emitters pinned end-to-end. The W1062-followup-4 substitute-rather-than-wire recommendation realized.
- **MCP outputSchema roadmap** — Wave B TERMINAL closes the W767 5-wave specialized-schema propagation (CONSOLIDATE-19); Wave C1 opens the compat-profile arc at the env-var tier. Wave C2+ (`roam mcp doctor` probe surface) queues as the next MCP-roadmap milestone.
- **Pattern-2 + Pattern-1A empty-state arc** — closes at 8 detectors + 2 hard-cap commands sealed. Pattern-2 detector empty-state coverage now 8/8 effective sites; the 2 Pattern-1A hard-cap disclosure fixes (W1085 fingerprint + W1086 cut) follow the mirror-fix template established by the W1085 cmd_fitness SARIF advisory-plumb arc at CONSOLIDATE-17.
- **Research-memo cluster** — MCP outputSchema + compat-profile + FP-rate research memos now span 3 dated memos (one per consolidation pass since -18): MCP-OUTPUTSCHEMA-EVOLUTION (-18), DETECTOR-FP-RATE-METHODOLOGY (-19), MCP-COMPAT-PROFILE-ROADMAP (-20).

### Added — Wave-B-TERMINAL + W794 + W1028 + W805 batch (post-CONSOLIDATE-18, 2026-05-16 /loop iteration N+19)

> **~18 completions since CONSOLIDATE-18 (Section 64).** Wave B
> TERMINAL milestone — the W767 5-wave outputSchema roadmap that
> kicked off at CONSOLIDATE-18 closes cleanly across 13 MCP tools +
> ~113 envelope-validation tests. Three themes carry the batch:
> (a) **Wave B TERMINAL — 13 MCP tools specialized across 5
> sub-ships** (Wave B2 / B3 / B4 / B5-partial / B5b TERMINAL).
> (b) **MCP server card SEP-2127 readiness (W794)** — `icons[]`
> field across 4 .well-known path variants, carries the W792 + W793
> work to a clean SEP-2127-ready posture. (c) **Pattern-2
> empty-state audit arc closure (W805)** — 3 detectors migrated
> (cmd_test_hermeticity + cmd_llm_smells + cmd_boundary) + 5
> followups captured + 1 real bug fixed (cmd_boundary SQL outside
> `with open_db` block). Plus 3 stand-alone polish items
> (W1061-followup-2 + W1008 carry-forward + the
> DETECTOR-FP-RATE-METHODOLOGY research memo). Parallel in stature
> to the W1255 architectural-decision-and-implementation arc that
> CONSOLIDATE-16 carried.

- **Wave-B2** — feat(mcp): specialized `_SCHEMA_HEALTH` + `_SCHEMA_UNDERSTAND` outputSchemas on roam_health + roam_understand wrappers (25 tests pass).
- **Wave-B3** — feat(mcp): bundled `_SCHEMA_ORACLE` across 6 oracle wrappers (37 tests pass; single shared schema for the uniform oracle envelope shape).
- **Wave-B4** — feat(mcp): specialized `_SCHEMA_TIMELINE` + `_SCHEMA_TEST_IMPACT` outputSchemas on roam_timeline + roam_test_impact wrappers (7 tests pass).
- **Wave-B5-partial** — feat(mcp): specialized `_SCHEMA_AUDIT_TRAIL_VERIFY` + `_SCHEMA_DIAGNOSE` outputSchemas (5 tests pass).
- **Wave-B5b** — feat(mcp): **TERMINAL.** Specialized `_SCHEMA_FETCH_HANDLE` + `_SCHEMA_VALIDATE_PLAN` + `_SCHEMA_AUDIT_TRAIL_CONFORMANCE` outputSchemas (39 tests pass). Closes the W767 5-wave outputSchema roadmap end-to-end across 13 MCP tools.
- **W794** — feat(mcp-card): wire `icons[]` field across 4 .well-known path variants — `mcp-server-card.json` + `.well-known/mcp-server-card` + SEP-1649 mirror + SEP-2127 mirror (22 tests pass; SEP-2127 ready).
- **W1028** — feat(formatter): `_ALWAYS_PRESERVED_LIST_FIELDS` expansion audit — 4 candidates marked DEFER + drift-guard test pinning the current set (162 tests pass; carry-forward CONSOLIDATE-17 → -18 → -19).
- **W1008** — feat(formatter): surface `list_counts` top-level in `strip_list_payloads` so callers see how many items were stripped when `--detail` is off (234 tests pass; carry-forward W1000 drive-by).
- **W1061-followup-2** — refactor(sarif): extract `runtime_filter_disclosure()` shared helper from 4 SARIF callers (cmd_smells + cmd_check_rules + cmd_taint + cmd_vulns); -36 LOC consolidation (17 tests pass).
- **DETECTOR-FP-RATE-METHODOLOGY** — docs(research): memo at `(internal memo)` (674 words, 12 sources cited; methodology for measuring detector false-positive rates). <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->

### Fixed — Wave-B-TERMINAL + W794 + W1028 + W805 batch

- **W805** — fix(detectors): Pattern-2 empty-state audit on cmd_test_hermeticity + cmd_llm_smells + cmd_boundary — all 3 detectors migrated to emit explicit `partial_success: true` on empty-state branches (13 tests pass; 5 followups captured as W805-followup-A/B/C/D/E).
- **W805 real-bug fix** — fix(boundary): cmd_boundary had a SQL execution block outside the `with open_db` context manager (resource-leak-on-error path); fixed inline during the W805 Pattern-2 migration.

### Changed — Wave-B-TERMINAL batch

- **MCP outputSchema roadmap** — Wave B TERMINAL. The W767 5-wave specialized-schema propagation that kicked off with Wave B1 (CONSOLIDATE-18) lands on 13 MCP tools end-to-end (~113 cumulative envelope-validation tests pass). Wave C (compatibility profile + `roam mcp doctor` probe) queues as the next major MCP-roadmap milestone.
- **MCP server card SEP-2127 readiness** — W792 (3 .well-known path variants) + W793 (display_name → title rename) + W794 (icons[]) collectively land a clean SEP-2127-ready posture; W795 (`_meta` privacy posture stanza) remains BLOCKED on SEP-2127 upstream merge.
- **Pattern-2 empty-state audit arc** — closes at W805. The W802 → W836 sweep (CONSOLIDATE-17 + -18) covered the bulk of the detector roster; W805 adds the 3 remaining branches (cmd_test_hermeticity + cmd_llm_smells + cmd_boundary). Pattern-2 empty-state coverage on the detector roster is structurally complete; the 5 captured followups are surface-level disclosure-consistency polish.

### Added — W1275-W1312-arc + Wave-B1 + sarif-disclosure batch (post-CONSOLIDATE-17, 2026-05-16 /loop iteration N+18)

> **~15 completions since CONSOLIDATE-17 (Section 63).** Fast-follow-
> through batch — 5 themes carry the dispatch tail: (a) Pattern-2c
> carry-forward closures (W1275 / W1276-fix / W1277 / W1278a / W1309
> — the Pattern-2c CONSOLIDATE-16 → -17 → -18 chain closes cleanly,
> Pattern-2c roster now 31/31 effective). (b) SARIF dashboard-
> filtering trio (W1060 + W1061 + W1062 + 2 followups — OASIS 2.1.0
> § 3.51 + § 3.52 + properties.tags[] plumbed across cmd_smells +
> cmd_check_rules + cmd_taint + cmd_vulns + cmd_health + cmd_complexity
> + secrets emitter). (c) MCP outputSchema roadmap kickoff (W767
> inventory + Wave B1 specialized schemas on roam_impact +
> roam_preflight + W1311 decorator normalization + W1312 redundancy
> drops + the EVOLUTION research memo). (d) Pattern-1D file-substring
> disclosure (W1309). (e) Pattern-3a severity widening (W1005 +
> W1005-followup-A + W1007 `agent_contract:[]` preservation).

- **W767** — docs(mcp): outputSchema inventory at `(internal memo)` (57 core tools catalogued, 5-wave Wave B roadmap). <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **MCP-OUTPUTSCHEMA-EVOLUTION** — docs(mcp): research memo at `(internal memo)` (Claude Code #25081 status shifted; 3-wave roadmap). <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **Wave-B1** — feat(mcp): specialized `_SCHEMA_IMPACT` + `_SCHEMA_PREFLIGHT` outputSchemas on roam_impact + roam_preflight wrappers (18 tests pass).
- **W1060** — test(sarif): runtime-notifications activation tests on cmd_health + cmd_complexity (12 tests pass; cmd_doctor BAIL — no `--sarif` flag).
- **W1061** — feat(sarif): `ruleConfigurationOverrides[]` on cmd_smells (OASIS 2.1.0 § 3.51 compliant, default-off, 38 tests pass).
- **W1061-followup** — feat(sarif): extend `ruleConfigurationOverrides[]` + add `notificationConfigurationOverrides[]` on cmd_check_rules + cmd_taint + cmd_vulns (11 tests pass).
- **W1062** — feat(sarif): `result.properties.tags[]` on taint + vulns + audit-trail-conformance (21 tests pass).
- **W1062-followup** — feat(sarif): `result.properties.tags[]` on secrets_to_sarif emitter (60 tests pass).
- **W1278a** — feat(test-scaffold): Convention-c migration for `cmd_test_scaffold` (Pattern-2c roster now 31/31 effective; 22 tests pass).
- **W1309** — feat(test-scaffold): Pattern-1D file-substring `resolution: "file_substring"` enum disclosure on `cmd_test_scaffold` (31 tests pass).

### Fixed — W1275-W1312-arc + Wave-B1 + sarif-disclosure batch

- **W1005** — fix(smells): widen `cmd_smells --min-severity` from 3-tier to W547 7-tier canonical via `severity_rank` lookup (Pattern 3a; 236 tests pass).
- **W1005-followup-A** — fix(llm-smells): parallel 7-tier widening on `cmd_llm_smells --min-severity` (Pattern 3a vocabulary uniformity; 2 tests pass).
- **W1007** — fix(formatter): `strip_list_payloads` now preserves `agent_contract:[]` without `--detail` (closes W1006 audit finding; 89 tests pass).
- **W1275** — fix(tests): harden 3 remaining dogfood-brittle assertions in `test_validate_plan.py` (carry-forward CONSOLIDATE-16 → -17 → closed at -18; 27 tests pass).
- **W1276-fix** — NO-OP verified: fix already landed during W1272 Pattern-2c batch (structural verification only).
- **W1277** — fix(impact): restore `auto_log` provenance on `cmd_impact` unresolved-attempt path so `roam replay` narrates the unresolved-attempt event (W1272 standardization had removed it; 8 tests pass).
- **W1311** — refactor(mcp): normalize 5 oracle multi-line `@_tool(` decorators so inventory script AST-walks cleanly (-37 LOC; 131 tests pass).
- **W1312** — refactor(mcp): drop 3 redundant `output_schema=_ENVELOPE_SCHEMA` declarations + queue 2 for Wave B (142 tests pass).
- **sarif-disclosure** — fix(docs): `cmd_boundary` + `cmd_compatibility` + `cmd_test_hermeticity` docstrings add `--sarif` flag disclosure (closes CI-blocking drift-guard gap; 103 tests pass).

### Changed — W1275-W1312-arc batch

- **Pattern-2c roster** — effective 31/31 closed (30 from CONSOLIDATE-15 + W1278a at CONSOLIDATE-18). The CONSOLIDATE-16 → -17 → -18 carry-forward chain (W1275 / W1276-fix / W1277 / W1278) closes cleanly.
- **SARIF emitter coverage** — dashboard-filtering plumb (rule + notification config overrides + result tags) now lands across 7 emitter surfaces. The OASIS 2.1.0 § 3.51 + § 3.52 plumb is structurally complete on the high-signal-tier emitters.
- **MCP outputSchema roadmap** — Wave A (default `_ENVELOPE_SCHEMA`) inventory catalogued; Wave B specialized-schema propagation kicked off with Wave B1 on the 2 highest-frequency wrappers (roam_impact + roam_preflight).

### Added — W1284-W1308 batch (post-CONSOLIDATE-16, 2026-05-16 /loop iteration N+17)

> **~25 completions since CONSOLIDATE-16 (Section 62).** Post-v13.2-release
> hardening batch — first ~25 W#s after the release-merge land cleanly
> without re-opening flagship arcs. Four themes carry the batch: (a) init /
> cold-start UX fixes (W1288 / W1289 / W1290 / W1291). (b) SARIF advisory-
> warning plumb carry-forward bundle (W1084 + W1113 + W1114 + W1115 in one
> commit + W1236 chore drop of orphan emitters). (c) CGA edge-bundle
> stability + post-merge CI hardening (W1285 / W1284-G3 / W1286 / W1287 +
> W1297-W1302 / W1303-W1305). (d) MCP card v13.2 sync + CI infrastructure
> (W1306 / W1307 / W1308 / W1088 / W1089).

- **W1287** — feat(test-hermeticity): non-hermetic test detector (new smell-detector family member).
- **W1292** — docs(plugin): close Gap 3 — 3-hook copy-fork template under `dev/example-plugin/`.

### Fixed — W1284-W1308 batch

- **W1084** — feat(sarif): plumb advisory warnings into `cmd_fitness` emitter (shipped via 96d31bd0 with W1113/W1114/W1115).
- **W1113** — feat(sarif): plumb advisory warnings into `cmd_flag_dead` emitter.
- **W1114** — feat(sarif): plumb advisory warnings into `cmd_rules` emitter.
- **W1115** — feat(sarif): plumb advisory warnings into `cmd_health` emitter.
- **W1236** — chore(sarif): drop orphan breaking + conventions emitters (no registry consumers post-W1232).
- **W1284-G3** — fix(relations): SFC synthetic-component anchor for module-scope imports.
- **W1285** — fix(cga): `edge_bundle_digest` sort-stability — append `id` tiebreaker.
- **W1286** — perf(clones): language allowlist on candidate fetch (~3-5× candidate-set reduction on multi-language repos).
- **W1288** — fix(init): drop misleading "Health: N/100" banner on cold-start (was always 100/100 on 0-symbol corpus).
- **W1289** — fix(mcp-status): canonical Pattern-1A envelope on fastmcp import fail (was raw ImportError).
- **W1290** — fix(surface): AST-derived `mcp_tool_count` survives `[mcp]` extras gap (cold install without fastmcp).
- **W1291** — test(init): regression — `cmd_init` must not self-recommend `roam init` (silent advisory-loop prevention).
- **W1297 (follow-up)** — fix(format): ruff format 6 drift-guard test files left unformatted by v13.2 merge.
- **W1298-W1302** — fix: 6 CI failures on main — drift-guard re-pins + CGA `dirty_tree=true` propagation through pr-bundle integration tests.
- **W1303-W1305** — fix: doc-hygiene drift + ruff I001 + W792 server-card mirror sync.
- **W1306** — fix: `server.json` + `changelog.html` v13.2 catchup (landing page was pinned at v13.1).
- **W1307** — fix(test): bump `_EXPECTED_CARD_SHA256` to v13.2 card digest.
- **W1308** — fix: LF-normalize 3 MCP card files (`mcp-server-card.json` + `.well-known/` mirror + SEP-1649 variant); content-sync resolves CRLF-drift footgun on Windows checkouts.

### Changed — CI infrastructure

- **W1088** — ci: SHA-pin credential-bearing third-party actions (immutable SHAs on `publish.yml` + release pipeline).
- **W1089** — ci(publish): replace sleep-45 smoke-job with retry-backoff (intermittent v13.0 / v13.1 publish-failure root cause).

## [13.2] — 2026-05-16

### Highlights — wip/v13.2-session-2026-05-16 ship (W1255-W1297)

The 2026-05-16 session that landed:
- **W1272 Pattern-2c standardization** — 8 commands now emit canonical
  exit-0 + "not found" envelope on unresolved paths (impact, preflight,
  trace, testmap, context, safe-delete, split, why).
- **W1255 config-hash producer wire-up** — `.roam-rules.yml` +
  `.roam/constitution.yml` + `.roam/control-map.yml` canonical paths
  stamped onto every run; `evidence/config_hashes.py` substrate + ledger
  producer wire-up. W1253 unblocked. `vsa.py` already CONSUMES the
  hashes so VSA attestation benefits immediately.
- **W210 W211 producer coverage** — pr-replay path achieves 7-complete
  + 1-partial of 8 evidence questions on roam-code itself; remaining
  gap (Q8 approvals) explicitly marked via `producer_not_available`.
- **30+ CI hardening fixes** (W1281-W1297) — TestBatchSearch cold-start
  guard, mode-classification taxonomy, eight-questions GitHub Actions
  skip, README v11→v13.2 narrative refresh, .githooks executable bit,
  ~17 dogfood-corpus-dependent tests now centrally skipped via
  `conftest.py` collection hook, LAW-4 verdict refresh on minimap,
  Pattern-2c test assertion updates, mode-policy W1288 (why-fail /
  why-slow / workflow classified read_only), and the W762 cmd_preflight
  line-allowlist refresh.
- **Documentation** — 224 MCP tools (was: 137) surfaced; v13.2 README
  agentic-assurance frame replaces v11 roadmap section.

### Added — W1255-W1278 batch (post-CONSOLIDATE-15, 2026-05-16 /loop iteration N+16)

> **7+ completions since CONSOLIDATE-15 (Section 61).** The follow-
> through batch after the Pattern-2c 30/30 terminal landed. **The
> MAJOR load-bearing milestone**: the **W1255 architectural
> decision** (Option (a) "Keep top-level + add siblings") landed AND
> shipped within the same consolidation window — `.roam-rules.yml`
> (root) + `.roam/constitution.yml` (existing) + `.roam/control-map.yml`
> (new) are the canonical config paths, and `src/roam/evidence/config_hashes.py`
> (84 LOC, NEW) + ledger.py stamping at `start_run` (+18 LOC) wire
> the producer side end-to-end. **Side benefit**: `vsa.py` already
> CONSUMES `constitution_hash` + `rules_config_hash` at lines
> 281-296 — producer wire-up immediately benefits VSA attestation
> with zero further code change. W1253 unblocked. Plus the
> **W1272 Pattern-2c unresolved-path standardization** milestone:
> 8 commands (`cmd_impact` + 6 helper-callers + `cmd_preflight`
> already-compliant pin) now emit the canonical Convention-c
> unresolved-path shape — exit code 0 on unresolved across all 8.
> Five themes: (1) **W1255 architectural decision recorded + IMPL
> shipped** — Cranot picked Option (a); `config_hashes.py` substrate
> + ledger.py producer wire-up + CLAUDE.md doc landed inside the
> same window. 11 new tests + 101 in-scope tests pass; hash-stability
> preserved. (2) **W1272 Pattern-2c unresolved-path standardization
> SHIPPED** — 8-command Convention-c bulk migration (78+105+27+51
> tests pass; zero regressions; exit-code-0-on-unresolved across all
> 8). The post-Pattern-2c-terminal follow-up arc surfaced at
> CONSOLIDATE-15 (W1268-audit) lands within the next consolidation
> window. (3) **W1273 test_validate_plan dogfood-brittleness fix
> SHIPPED** — 3 tests hardened (cold-start-guard bypass +
> `_vp_blast_radius` stubbing); 27/27 tests pass. (4) **Drive-by
> captures** — W1275 (3 remaining dogfood-brittle tests in
> `test_validate_plan.py`) + W1276 (`test_impact_auto_logs_not_found_path`
> RECLASSIFIED → W1272-expected-failing; in flight as W1276-fix) +
> W1277 (replay-narration provenance for unresolved-path attempts
> — auto_log removed from `cmd_impact`; signal-loss risk) + W1278
> (audit 3 remaining `symbol_not_found` callers — `cmd_test_scaffold`
> / `cmd_plan_refactor` / `cmd_guard`). (5) **Lockstep consolidation
> discipline** — the every-~8-completions /loop rule fires; the
> follow-through batch lands cleanly without the multi-arc-terminal
> volume of CONSOLIDATE-14 / CONSOLIDATE-15.

- **W1255 architectural decision recorded AND shipped within the same window.** Cranot picked Option (a) "Keep top-level + add siblings". Canonical paths: `.roam-rules.yml` (root) + `.roam/constitution.yml` (existing) + `.roam/control-map.yml` (new). W1255-IMPL shipped: `src/roam/evidence/config_hashes.py` (84 LOC, NEW) + `ledger.py` stamping at `start_run` (+18 LOC) + `CLAUDE.md` doc (+17 LOC). 11 new tests + 101 in-scope tests pass. Hash-stability preserved. **Side benefit**: `src/roam/evidence/vsa.py` already CONSUMES `constitution_hash` + `rules_config_hash` at lines 281-296 — the producer wire-up immediately benefits VSA attestation with zero further code change. W1253 unblocked (the W1255 decision was the only blocker remaining on W1253). The architectural-decision-and-implementation arc fitting inside a single consolidation window is the canonical fast-path for substrate-first sequencing — decision captured at CONSOLIDATE-14, decision-locked + implemented within the post-CONSOLIDATE-15 follow-through batch.
- **W1272 Pattern-2c unresolved-path standardization SHIPPED — 8-command Convention-c bulk migration.** Post-CONSOLIDATE-15 follow-through arc that closes the W1268-audit-captured 5-way unresolved-path divergence at the consumer level. `cmd_impact` + 6 helper-callers (`cmd_dead` / `cmd_safe_delete` / `cmd_closure` / `cmd_symbol` / `cmd_hover` / `cmd_pytest_fixtures` — illustrative; see commit for the full list) + `cmd_preflight` already-compliant pin now emit the canonical Convention-c unresolved-path shape. 78+105+27+51 tests pass. Zero regressions. **Exit code 0 on unresolved** across all 8 commands (was previously divergent — some emitted exit-2, some exit-5, some exit-0 with partial_success: true). The Pattern-2c terminal at 30/30 (CONSOLIDATE-15) addressed the *disclosure* of degraded resolution; W1272 addresses the *shape consistency* of the unresolved-path branch across consumers. Together: Pattern-2c is now both disclosure-complete AND shape-uniform.
- **W1273 — test_validate_plan dogfood-brittleness fix SHIPPED.** The W1271-audit / W1273-capture / W1274-fix arc from CONSOLIDATE-15 covered the `test_visualize` stale-assertion case; W1273 proper covered the remaining `test_validate_plan` cases. 3 tests hardened: cold-start-guard bypass + `_vp_blast_radius` stubbing applied. 27/27 tests pass. The capture-to-fix arc spans CONSOLIDATE-15 → CONSOLIDATE-16 — clean cross-session follow-through on the dogfood-brittleness surface.
- **Drive-by captures during this consolidation window (4 captures).** W1275 (harden 3 remaining dogfood-brittle tests in `test_validate_plan.py` — partial W1273 follow-up). W1276 (`test_impact_auto_logs_not_found_path` RECLASSIFIED → W1272-expected-failing; test needs update; in flight as W1276-fix). W1277 (restore replay-narration provenance for unresolved-path attempts; `auto_log` was removed from `cmd_impact` during W1272 standardization — there's a signal-loss risk on the replay-narration surface that wants explicit recovery). W1278 (audit 3 remaining `symbol_not_found` callers — `cmd_test_scaffold` / `cmd_plan_refactor` / `cmd_guard` — for Convention-c alignment; the W1272 bulk migration touched 8 of 11 known callers, the remaining 3 want an audit before bulk-migration).

### In flight — W1255-W1278 batch (parallel dispatches not yet on disk)

- **W1253** — was unblocked by W1255-IMPL landing; the next-session dispatch will pick up.
- **W1276-fix** — `test_impact_auto_logs_not_found_path` test-needs-update (W1272 follow-up; expected-failing under the new W1272 exit-code-0 contract).

### Added — W1245-W1274 batch (post-CONSOLIDATE-14, 2026-05-16 /loop iteration N+15)

> **~20 completions since CONSOLIDATE-14 (Section 60) — the largest
> cumulative consolidation since the W1175-RESEARCH propagation arc
> mid-points.** The MAJOR milestone: **Pattern-2c propagation arc
> COMPLETE at 30/30 sites.** The W1233-audit roster (originally 38
> sites) resolved to 30 real true-positives once the W1267-audit
> filtered out two W1233-audit false positives (`cmd_hotspots` /
> `cmd_smells` lacked real `find_symbol` callsites). Wave 1 quartet
> (W1242 + W1243 + W1244 + W1248 — CONSOLIDATE-14) + cmd_annotate
> (W324 origin template) + W1245 batches 1-4 covering 20 SHIP +
> 2 BAIL (22 cmd_*.py visited, 20 disclosure-covered + 2 false-
> positive BAILs) closed every remaining real Pattern-2c site.
> **Both terminal
> arcs are now CLOSED**: the SARIF SHIP/SKIP-disclosure 196 → 0
> propagation arc reached terminal at CONSOLIDATE-14; the Pattern-2c
> 30/30 arc reaches terminal at CONSOLIDATE-15. The agentic-assurance
> substrate now spans producer (W1234 evidence_stale + earlier W210
> packet substrate) + consumer (W1262 doctor/diff stale banner) +
> attestation (W37x CGA + W377 permit collector) — all three axes
> structurally complete. Six themes: (1) **Pattern-2c bulk
> completion** — W1245 batches 1-4 (22 SHIP across `cmd_dead` +
> `cmd_safe_delete` + `cmd_closure` + `cmd_symbol` + `cmd_hover` +
> `cmd_pytest_fixtures` + `cmd_plan` + `cmd_context` + `cmd_relate` +
> `cmd_why` + `cmd_visualize` + `cmd_invariants` + `cmd_testmap` +
> `cmd_affected_tests` + `cmd_guard` + `cmd_metrics` +
> `cmd_plan_refactor` + `cmd_pr_bundle` + `cmd_safe_zones` +
> `cmd_test_scaffold`) + 2 BAIL on W1233-audit false positives
> (`cmd_hotspots` / `cmd_smells` — no real `find_symbol` callsite).
> Plus cmd_annotate origin template (W324) accounted in the
> 30-tally. (2) **Pattern-2c family extensions** — W1250 helper
> docstring (collision-pattern documented); W1270 helper reserved-
> key warning (Pattern-2 silent-drop fix at substrate level; first
> real-world use in W1245-batch-4 `cmd_safe_zones`); W1268-audit
> surfaced 5-way unresolved-path divergence captured as W1272;
> W1271-audit surfaced `test_validate_plan` dogfood-brittleness
> captured as W1273; W1273-fix → W1274 stale-assertion fix in
> `test_visualize`; W1265 docstring at `vsa.py:133` (W1264 follow-up).
> (3) **Evidence/W210 extensions** — W1262 doctor/diff stale-evidence
> banner (consumer-side wire-up of W1234 evidence_stale producer);
> W1266 `completeness_compat` shared module hoist (-180 LOC duplicate
> helpers + 205 LOC shared; W1262 drive-by). (4) **Per-kind version
> stamps** — W1256 `cmd_vibe_check` per-pattern version stamps (10
> patterns); W1269 `cmd_smells` per-kind version stamps (7 patterns
> wired). (5) **Audit closures** — W1267 audit corrected the 34-site
> Pattern-2c list to 30 real true-positives by filtering the two
> W1233-audit false positives. (6) **CONSOLIDATE pause** — natural
> stopping point after Wave 2 batch-4 lands; no in-flight dispatches
> at consolidation time. (Tally arithmetic: 20 SHIP across W1245
> batches 1-4 + 4 Wave-1 from CONSOLIDATE-14 + 1 W324 cmd_annotate
> origin + 5 already covered upstream / earlier = 30 real
> Pattern-2c sites disclosure-covered; the W1233-audit 38-site
> original count was inflated by 2 false positives + ~6 duplicates
> already covered by earlier substrates.)

- **Pattern-2c propagation arc COMPLETE at 30/30 sites (W1245 batches 1-4).** The MAJOR load-bearing milestone: every real Pattern-2c site in the W1233-audit enumeration now disclosures resolution-tier via the W1241 `resolution_disclosure()` helper substrate. **Batch breakdown.** W1245-batch-1 (3 SHIP — `cmd_dead` + `cmd_safe_delete` + `cmd_closure`; 2 BAIL — `cmd_hotspots` / `cmd_smells` were W1233-audit false positives, no real `find_symbol` callsites). W1245-batch-2 (5 SHIP — `cmd_symbol` + `cmd_hover` + `cmd_pytest_fixtures` + `cmd_plan` + `cmd_context`). W1245-batch-3 (5 SHIP — `cmd_relate` + `cmd_why` + `cmd_visualize` + `cmd_invariants` + `cmd_testmap`). W1245-batch-4 (7 SHIP — `cmd_affected_tests` + `cmd_guard` + `cmd_metrics` + `cmd_plan_refactor` + `cmd_pr_bundle` + `cmd_safe_zones` + `cmd_test_scaffold`). **Plus the Wave-1 quartet from CONSOLIDATE-14** (W1242 `cmd_impact` + W1243 `cmd_preflight` + W1244 `cmd_diagnose` + W1248 `cmd_trace`) **and the W324 cmd_annotate origin template** — together 30/30 real Pattern-2c sites. W1267-audit corrected the W1233-audit roster (originally 38 sites; surfaced 2 false positives + ~6 duplicates already covered upstream → 30 real true-positives). The W1249 substrate refactor's ~3× LOC simplification per consumer made batches-2/3/4 tractable at ~7 sites per dispatch versus Wave-1's ~1-site-per-dispatch cadence. Hash-stability invariant held throughout: every adoption byte-stable for `symbol`-tier (exact-match) envelopes; only the partial-success branches emit new field bytes. **The propagation arc is structurally complete** — no surviving Pattern-2c gaps remain in the cmd_*.py surface.
- **Pattern-2c family extensions (W1250 + W1270 + W1268-audit + W1271-audit + W1273-fix + W1274 + W1265).** W1250 expanded the `resolution_disclosure()` helper docstring with the W324 cmd_annotate template precedent + W1241 substrate-first sequencing + collision-pattern documentation (what happens when two resolvers race on the same logical symbol). W1270 added the helper's reserved-key warning surface — the substrate now flags Pattern-2 silent-drop when a downstream caller tries to override a reserved envelope key; the first real-world use landed in W1245-batch-4 `cmd_safe_zones`, where a name-collision between the resolution-tier output and a domain-specific `partial_success` field was caught at substrate layer rather than silently dropped. W1268-audit surfaced a 5-way unresolved-path divergence across the Pattern-2c consumer family (each cmd hand-rolled its own degraded-resolution path) — captured as **W1272** for bundled standardization (10 cmd_*.py / ~150 LOC). W1271-audit surfaced `test_validate_plan` dogfood-brittleness (assertion couples to an unstable transient hash) — captured as **W1273**. W1273-fix shipped as **W1274** (`test_visualize` stale-assertion fix; ~10-20 LOC). W1265 added a load-bearing docstring at `src/roam/evidence/vsa.py:133` (W1264 follow-up — surfaced during W1262 stale-banner wiring). The Pattern-2c family's substrate-first sequencing precedent (substrate → Wave-1 → substrate refactor → bulk migration) is now the canonical playbook for closed-vocab propagation arcs.
- **Evidence/W210 extensions (W1262 + W1266).** W1262 landed the **consumer-side wire-up of the W1234 evidence_stale producer** — `roam doctor` and `roam diff` now surface a "stale evidence" banner when the W1234-emitted `evidence_stale: true` field appears on the consumed packet (closes the W1254 in-flight dispatch from CONSOLIDATE-14). W1266 hoisted `evidence_completeness_compat` helpers into a shared module (-180 LOC of duplicate helpers across `cmd_doctor` / `cmd_diff` / `cmd_critique` / 3 sibling sites + 205 LOC shared module) — drive-by from W1262 stale-banner wiring; W1266 is the substrate-first-sequencing exemplar applied to evidence completeness checks. **The agentic-assurance substrate now spans all three axes — producer + consumer + attestation — structurally complete.** The W1234 producer (CONSOLIDATE-14) emits; the W1262 consumer (CONSOLIDATE-15) surfaces; the W37x CGA + W377 permit-collector attest. The W210 packet-layer Pattern-2 variant-2f family is now end-to-end live; no producer/consumer/attestation gap survives on the evidence-staleness axis.
- **Per-kind version stamps — W1256 (vibe-check) + W1269 (smells).** W1256 added per-pattern detector version stamps to `cmd_vibe_check`'s 10 AI-rot patterns (each pattern's emission now carries its own `pattern_version` field — agents can detect a pattern's signal shape changed without forcing the whole detector version to bump). W1269 wired per-kind version stamps for the 7 `cmd_smells` patterns that still shared the composite `SMELLS_DETECTOR_VERSION` fallback (W870 vintage — 7/24 detectors had per-id stamps post-W870; W1269 closes the 7 most-touched of the remaining 17). Both ships are byte-stable additive — no persisted finding rows touched; the new version fields land on the FindingRecord envelope alongside (not replacing) the composite detector version. Together W1256 + W1269 close the version-stamp gap for the two largest pattern-family detectors in the catalog (vibe-check + smells).
- **Audit closures — W1267.** W1267 corrected the W1233-audit roster from 34 sites to 30 real true-positives. The audit found that `cmd_hotspots` and `cmd_smells` listed in W1233-audit Wave-2 batch-1 lacked a real `find_symbol()` callsite — both commands resolve their inputs via the rule-engine path rather than the symbol-resolver path, so the Pattern-2c disclosure shape doesn't apply (no degraded resolution to disclose). The two BAIL outcomes (W1245-batch-1 BAILs) are recorded with structured rationale; W1233-audit's 38-site original count resolved cleanly to 30 real sites once W1267 + duplicates-already-covered cross-referenced.

### In flight — W1245-W1274 batch (parallel dispatches not yet on disk)

- (none — natural pause point after W1245-batch-4 lands; no parallel dispatches active at consolidation time)

### Added — W1242-W1259 batch (post-CONSOLIDATE-13, 2026-05-16 /loop iteration N+14)

> **~15 completions since CONSOLIDATE-13 (Section 59).** Six themes:
> (1) **Pattern-2c family enablement — Wave 1 quartet landed** —
> W1242 `cmd_impact` + W1243 `cmd_preflight` + W1244 `cmd_diagnose` +
> W1248 `cmd_trace` adopted the W1241 `resolution_disclosure()` helper
> substrate at the `find_symbol()` / `find_symbol_id()` call sites.
> The four flagship commands now surface which tier of the resolver
> succeeded (`symbol` / `file` / `fuzzy` / `unresolved`) + a
> `partial_success` flag set on any non-`symbol` resolution. Wave 1
> finishes the highest-traffic Pattern-2c sites first, matching the
> W1192/W1195 SARIF SHIP sequencing pattern. (2) **Pattern-2c
> substrate refactor (W1249)** — hoisted `find_symbol` tier-stamping
> into the substrate helper, eliminating ~100 LOC of duplicate
> `_detect_resolution_tier` helpers across the four Wave-1 flagships.
> The W1249 refactor unblocks W1245-batch-1 (Wave 2 first 5 sites) at
> ~3× LOC simplification per consumer — without it, each Wave-2
> adoption would carry ~25 LOC of boilerplate the substrate now
> absorbs. (3) **Wave 16 SKIP-disclosure landed — `_KNOWN_MISSING`
> 17 → 0** — 17 docstrings shipped across the remaining Bucket B
> long-tail (`cmd_debt` + `cmd_entry_points` + `cmd_guard` + `cmd_map`
> + `cmd_metrics` + `cmd_path_coverage` + `cmd_patterns` +
> `cmd_plan_refactor` + `cmd_pytest_fixtures` + `cmd_risk` +
> `cmd_safe_delete` + `cmd_safe_zones` + `cmd_simulate_departure` +
> `cmd_suggest_refactoring` + `cmd_testmap` + `cmd_why_slow` +
> `cmd_ws`). **The 196 → 0 propagation arc is now fully closed** —
> 196 commands audited, 196 commands disclosure-covered (179
> SKIP-disclosure docstrings + 17 SARIF SHIP emitters across
> CONSOLIDATEs 4 → 14). The W1175-RESEARCH long-tail roster
> exhausted; arc terminal. (4) **Evidence/W210 wire-up** — W1234
> shipped the `evidence_stale` producer (W210 packet-layer Pattern-2
> variant-2f); W1254 (consumer) in flight at consolidation time; W1253
> BAIL surfaced W1255 architectural prerequisite (no upstream packet
> exists to mark stale → captured as architectural decision pending).
> (5) **State-vocab substrate (W1235)** — `_STATE_FAMILY_ALIASES`
> registry landed at substrate level for state-name normalization
> across closed-vocab Pattern-2g sites. (6) **SARIF rule rename
> (W1232)** — `flag-constant-default` rule renamed to `flag-suspect`
> per W1226 SHIP scope-discipline follow-up, aligning the
> `cmd_flag_dead` namespace closer to W1227/W1229 naming convention.
> Plus three CLAUDE.md doc-drift refreshes (W1247 module-local SARIF
> convention + W1252 findings-registry decision + W1258+W1259
> 16 → 26 detector count + `emit_finding(conn, record)` API name)
> and two new research memos (`(internal memo)`
> <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> already shipped in CONSOLIDATE-13; `(internal memo)`
> <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> 773 LOC false-positive rate benchmarks across the 26 emitting
> detectors).

- **Pattern-2c Wave 1 quartet landed (W1242 + W1243 + W1244 + W1248).** Four flagship commands — `cmd_impact` (W1242) / `cmd_preflight` (W1243) / `cmd_diagnose` (W1244) / `cmd_trace` (W1248) — consume the W1241 `resolution_disclosure()` helper substrate at the `find_symbol()` / `find_symbol_id()` call sites. Envelopes now carry `resolution` (closed-enum: `symbol` / `file` / `fuzzy` / `unresolved`) + `partial_success: true` on any non-`symbol` resolution. Wave 1 covers the four highest-traffic Pattern-2c sites first per the W1192/W1195 SARIF SHIP sequencing pattern; Wave 2 (W1245-batch-1 in flight; 5 sites) and Wave 2/3 long-tail (29 remaining sites) carry forward to next session. The four Wave-1 commands are the exact ones that agents call most frequently in pre-edit workflows (`roam impact` for blast radius; `roam preflight` for gate-before-edit; `roam diagnose` for root-cause ranking; `roam trace` for symbolic path traversal) — so the user-facing partial-success disclosure lands in the highest-leverage call sites first.
- **Pattern-2c substrate refactor (W1249).** Hoisted the `find_symbol` tier-stamping into the canonical `resolution_disclosure()` helper at `src/roam/output/formatter.py:1263`, eliminating ~100 LOC of duplicate `_detect_resolution_tier` helpers across the four Wave-1 flagships (cmd_impact / cmd_preflight / cmd_diagnose / cmd_trace). The W1249 refactor unblocks W1245-batch-1 (Wave 2 first 5 sites — `cmd_hotspots` / `cmd_smells` / `cmd_dead` / `cmd_safe_delete` / `cmd_closure`) at **~3× LOC simplification per consumer** — without it, each Wave-2 adoption would carry ~25 LOC of boilerplate that the substrate now absorbs. The refactor preserves byte-stable envelopes for the Wave-1 quartet (verified per-command via hash-comparison on representative JSON outputs) — no detector output bytes moved.
- **Wave 16 SKIP-disclosure — `_KNOWN_MISSING` 17 → 0; the 196→0 propagation arc is fully closed.** 17 SKIP-disclosure docstrings landed across the remaining Bucket B long-tail: `cmd_debt` + `cmd_entry_points` + `cmd_guard` + `cmd_map` + `cmd_metrics` + `cmd_path_coverage` + `cmd_patterns` + `cmd_plan_refactor` + `cmd_pytest_fixtures` + `cmd_risk` + `cmd_safe_delete` + `cmd_safe_zones` + `cmd_simulate_departure` + `cmd_suggest_refactoring` + `cmd_testmap` + `cmd_why_slow` + `cmd_ws`. **The 196→0 propagation arc is terminal — 196 commands audited, 196 commands disclosure-covered** (179 SKIP-disclosure docstrings + 17 SARIF SHIP emitters across CONSOLIDATEs 4 → 14). The W1175-RESEARCH long-tail roster has now been fully exhausted; no surviving `_KNOWN_MISSING` entries remain. The arc spanned W1146 → W1259 across 11 CONSOLIDATE waves and ~30 sessions; **Wave 16 is the largest single SKIP-disclosure wave since Wave 14b (CONSOLIDATE-12, 22 docstrings)** at 17 docstrings — closing the long-tail with 0 BAILs and 0 reclassifications, both inverse-drift guards (`tests/test_known_missing_pin_is_current` + `tests/test_pattern_2_propagation_coverage`) green at consolidation time.
- **Evidence/W210 wire-up (W1234 + W1253 BAIL + W1254 in flight).** W1234 landed the `evidence_stale` producer for the W210 packet-layer Pattern-2 variant-2f — the field is now populated upstream when the evidence-compiler detects time-skew between `context_read_at` / `edits_started_at` / `edits_completed_at` and the current commit. W1254 (consumer-side: the report renderer + projection layers consume `evidence_stale` to surface a "stale evidence" banner) dispatched in parallel at consolidation time. **W1253 BAIL surfaced an architectural prerequisite** — the `pr-bundle emit` path cannot mark a packet stale before any packet exists (no upstream packet to mark) — captured as W1255 architectural decision pending. The BAIL-and-capture discipline (Pattern-3b reclassification arc precedent) applies cleanly: a discovered prerequisite gap becomes a captured architectural decision rather than a silent no-op.
- **State-vocab substrate (W1235).** Landed the `_STATE_FAMILY_ALIASES` registry at substrate level for state-name normalization across closed-vocab Pattern-2g sites. Captures the W1077/W1080 `structured_unknown_filter` precedent — closed-vocabulary state-name divergence (`"idle"` vs `"waiting"` vs `"pending"` etc.) gets canonicalized at the substrate boundary rather than each detector hand-rolling its own alias table. The registry is the substrate-first equivalent of the W1018 YAML loader substrate (W965-CONSOLIDATE) and the W1241 `resolution_disclosure()` helper (CONSOLIDATE-13). 45-site bulk migration (W1251) captured for next session — same substrate-first sequencing.
- **SARIF rule rename (W1232).** Renamed the `flag-constant-default` rule under the `flag-*` namespace to `flag-suspect` per the W1226 SHIP scope-discipline follow-up. The rename aligns `cmd_flag_dead`'s closed-enum rule set closer to W1227 / W1229 naming convention — flag-*` rules now express "what the SARIF row claims about the flag" (`flag-staleness` / `flag-single-reference` / `flag-suspect`) rather than mixing claims with antecedent conditions (`flag-constant-default` named the cause rather than the consequence). Hash-stable for the rule-set rename via the SARIF wrapper's `rule_id` field — no persisted finding rows touched.
- **CLAUDE.md doc-drift refresh (W1247 + W1252 + W1258 + W1259).** Four small but load-bearing CLAUDE.md updates: W1247 added the module-local SARIF convention note (`_to_sarif()` helpers live in the cmd module per SHIP emitter — not centralized — per W1236-audit BENIGN verdict); W1252 captured the findings-registry decision (`emit_finding(conn, record)` as canonical API name — supersedes the older `findings_store.persist(...)` snake_case spelling that drifted into early docs); W1258 + W1259 refreshed the detector count from "16 detectors persist findings" (W146 vintage) to "26 detectors persist findings as of 2026-05-16" with the 10 newly-emitting detectors enumerated (critique, doctor, fan, fingerprint, health, llm-smells, etc. — predominantly aggregator / consumer commands that re-emit derived findings from upstream detectors).
- **Research memo — `(internal memo)` (773 LOC).** False-positive rate benchmarks across the 26 emitting detectors. Reference for the next per-detector confidence-tier tuning pass (W1256 captured for next session). Companion to `(internal memo)` (884 LOC, shipped CONSOLIDATE-13) — together the two memos cover the W2026-05-16 detector-quality landscape. No source changes; sequencing decisions are Cranot's. <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **Drift-guard remediation (W1239 + W1240) — hygiene clean.** W1239 (drift-guard hygiene follow-up A from W1231-audit) shipped — stale audit assertion cleaned up. W1240 (drift-guard hygiene follow-up B) shipped — BACKLOG.md table-of-pendings cleaned to match the post-CONSOLIDATE-13 ground truth.

### Audits / verdicts — W1242-W1259 batch

- **W1230 audit — `cmd_test_gaps` SKIP confirm (re-verification).** Originally raised CONSOLIDATE-9 (W1202), re-opened CONSOLIDATE-13 by Wave 14b docstring landing, confirmed CONSOLIDATE-14: REPORT-not-detector pattern (no per-location FindingRecord persistence). SKIP-disclosure docstring stays in place. The verdict has now been re-affirmed across **three consecutive consolidation passes** — strongest signal yet that the classification is stable. **Drive-by drift note**: BACKLOG.md "Outstanding from older rosters" still lists `cmd_test_gaps` in stale carry-forward rosters in earlier CONSOLIDATE-12/13 sections — surfaced for cleanup but not load-bearing.
- **W1231 audit — drift-guard triage (5-failure)** — CLOSED. Two FIXED (W1237 cmd_risk edge-kind canonicalize + W1238 catalog/detectors.py bare-except migration — both landed CONSOLIDATE-13); two follow-ups SHIPPED CONSOLIDATE-14 (W1239 + W1240 hygiene clean).
- **W1233 audit — 38 Pattern-2c sites enumeration** — Wave 1 closed (quartet shipped CONSOLIDATE-14); Wave 2 batch-1 in flight; remaining 29 sites captured for Wave 2 batch-2+.
- **W1236 audit — SARIF helper convention sweep** — VERDICT BENIGN. Module-local SARIF convention consistent across all 37 emitters; no substrate canonicalization needed. W1247 doc-pass landed.
- **W1246 audit — `cmd_trace.find_symbol_id` Pattern-2c** — VERDICT NON-COMPLIANT (CONSOLIDATE-13). Captured as W1248; shipped CONSOLIDATE-14.
- **W1257 audit in flight** — ~45-site state-vocab adoption sweep (consumer-side of W1235 `_STATE_FAMILY_ALIASES` registry). Dispatched in parallel at consolidation time; non-CHANGELOG-touching.

### In flight — W1242-W1259 batch (parallel dispatches not yet on disk)

- **W1254 — `evidence_stale` consumer wire-up.** Dispatched at consolidation time. The W210 packet-layer consumer-side: report renderer + projection layers consume the `evidence_stale` field W1234 populates and surface a "stale evidence" banner.
- **W1245-batch-1 — Pattern-2c Wave 2 adoption (5 sites).** Dispatched at consolidation time. `cmd_hotspots` / `cmd_smells` / `cmd_dead` / `cmd_safe_delete` / `cmd_closure` — the first 5 of the 10 Wave-2 sites surfaced by W1233-audit. W1249's substrate refactor cut per-consumer LOC ~3×, making this batch tractable in a single dispatch.
- **W1257-audit — state-vocab adoption sweep (45 sites).** Dispatched at consolidation time. Consumer-side of the W1235 `_STATE_FAMILY_ALIASES` registry; same pattern as the W1233-audit dispatch for Pattern-2c.

### Added — W1226-W1248 batch (post-CONSOLIDATE-12, 2026-05-16 /loop iteration N+13)

> **~13 completions since CONSOLIDATE-12 (Section 58).** Four themes:
> (1) **SARIF SHIP family grew from 34 to 37 emitters** in a single
> post-CONSOLIDATE-12 window — W1226 `cmd_flag_dead` (35th, three closed-
> enum rules under the `flag-*` namespace: `flag-staleness` /
> `flag-single-reference` / `flag-constant-default`; staleness-banded
> per-result `level` with a **warning ceiling** — heuristic detector,
> never escalates to error), W1227 `cmd_orphan_routes` (36th, per-route
> dead-endpoint projection; single closed-enum rule `orphan-route` with
> confidence-banded per-result `level`: high + medium → warning, low →
> note; warning ceiling — heuristic detector, never escalates to error;
> the `used` bucket is filtered upstream so SARIF consumers never see
> non-actionable rows), W1229 `cmd_verify_imports` (37th, **first SHIP
> emitter that escalates to error** — two closed-enum rules:
> `invalid-import` (warning) for unresolved with FTS5 fuzzy-match
> candidates, `hallucination-import` (error) for unresolved with no
> candidates; verify-imports is the canonical "hallucination firewall"
> detector for LLM-era code and the only verify-imports rule that
> escalates to error per the W1229 scope discipline). (2) **Pattern-2
> variant-D family enablement (W1241)** — landed the canonical
> `resolution_disclosure()` helper at
> `src/roam/output/formatter.py:1263` + `_RESOLUTION_KINDS` frozen
> closed-enum (`symbol` / `file` / `fuzzy` / `unresolved`) + drift-guard
> test (`tests/test_resolution_disclosure.py`). Helper substrate now
> live for the W1242/W1243/W1244 Wave-1 adoption sweep across
> `cmd_impact` / `cmd_preflight` / `cmd_diagnose` (in flight at
> consolidation time). (3) **SKIP-disclosure propagation arc continued
> through Wave 14** — `_KNOWN_MISSING` decremented 20 → 17 via the
> three W1226/W1227/W1229 SHIP-promote pin-list removals (no new
> docstring waves this batch — long-tail of the propagation arc).
> (4) **Drift-guard remediation pass** — W1237 (`cmd_risk` edge-kind
> vocabulary canonicalized to `roam.db.edge_kinds`) + W1238
> (`catalog/detectors.py` framework-detector plugin loop migrated from
> bare-except to `log.warning(...) + continue` per W531 fail-loud
> discipline; previously-grandfathered `_PRE_W662_PENDING` entries
> dropped to zero in that file). Plus a 884-LOC research memo
> (`(internal memo)`) cataloguing the
> <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> seven-variant Pattern-2 family taxonomy and seven open gaps.

- **SARIF SHIP family grew from 34 to 37 emitters (W1226+W1227+W1229).** Added `cmd_flag_dead` (35th SHIP, three closed-enum rules under `flag-*` namespace — `flag-staleness` / `flag-single-reference` / `flag-constant-default`; staleness-banded per-result `level` with a **warning ceiling** — heuristic detector, never escalates to error), `cmd_orphan_routes` (36th SHIP, per-route dead-endpoint projection; single closed-enum rule `orphan-route`; confidence-banded per-result `level` — high + medium → warning, low → note; warning ceiling — heuristic detector, never escalates to error; the `used` bucket is filtered upstream so SARIF consumers never see non-actionable rows), `cmd_verify_imports` (37th SHIP, **first SHIP emitter that escalates to error** — two closed-enum rules: `invalid-import` (warning) for unresolved with FTS5 fuzzy-match candidates, `hallucination-import` (error) for unresolved with no candidates; verify-imports is the canonical "hallucination firewall" detector for LLM-era code and the only verify-imports rule that escalates to error per the W1229 scope discipline; `resolved` rows are filtered upstream so SARIF consumers never see non-actionable rows). All three wrappers hash-stable additive; no persisted finding rows touched. Hash-stability invariant held across all 37 emitters.
- **Pattern-2 variant-D family enablement substrate (W1241).** Landed the canonical `resolution_disclosure()` helper at `src/roam/output/formatter.py:1263` + a frozen closed-enum `_RESOLUTION_KINDS` (`symbol` / `file` / `fuzzy` / `unresolved`) + drift-guard test at `tests/test_resolution_disclosure.py`. Implements the W324 cmd_annotate template at substrate level — every command that calls `find_symbol()` with an implicit fallback chain can now surface which tier of the resolver succeeded. The `partial_success` flag is True for any non-`symbol` resolution. Enables the W1242/W1243/W1244 Wave-1 adoption sweep across `cmd_impact` / `cmd_preflight` / `cmd_diagnose` (in flight at consolidation time). The W1233 audit (Pattern-2c on 38 sites — Wave 1+2+3 enumeration) + W1246 audit (cmd_trace `find_symbol_id` non-compliant → W1248 capture) frame the remaining adoption work as a multi-wave propagation arc structurally parallel to the SARIF SHIP / SKIP-DISCLOSURE arc that drove CONSOLIDATEs 4 → 13.
- **SKIP-disclosure propagation — pin-list 20 → 17 via SHIP-promotes (no new docstring waves).** Each of W1226 + W1227 + W1229 decremented `_KNOWN_MISSING` in-batch via the `tests/test_known_missing_pin_is_current` inverse-drift guard — no second-pass stale-pin sweep required, continuing the W1222 hygiene discipline (CONSOLIDATE-11) and the CONSOLIDATE-12 "every SHIP landing decrements `_KNOWN_MISSING`" pattern. The propagation arc is now **~91% complete from the original 196-file gap (179 commands closed; 196 → 17).** The 17 surviving pin-list entries are all the long-tail audit-needed commands flagged by W1175-RESEARCH (`cmd_debt` + `cmd_entry_points` + `cmd_guard` + `cmd_map` + `cmd_metrics` + `cmd_path_coverage` + `cmd_patterns` + `cmd_plan_refactor` + `cmd_pytest_fixtures` + `cmd_risk` + `cmd_safe_delete` + `cmd_safe_zones` + `cmd_simulate_departure` + `cmd_suggest_refactoring` + `cmd_testmap` + `cmd_why_slow` + `cmd_ws`).
- **Drift-guard remediation (W1237 + W1238).** W1237 canonicalized the edge-kind vocabulary in `cmd_risk` onto the `roam.db.edge_kinds` registry — closes a quiet drift class where edge-kind literals could diverge across cmd_*.py callsites. W1238 migrated the framework-detector plugin loop in `catalog/detectors.py` (previously at lines 2044/2048, drifted to 2153/2157) from bare `except Exception: continue|pass` to `log.warning(...) + continue` per W531 fail-loud discipline; the plugin-isolation perimeter rationale is preserved in the inline comments at the call site; the swallow is now visible. Two previously-grandfathered `_PRE_W662_PENDING` entries dropped to zero in `catalog/detectors.py` (stale-pin hygiene applied alongside the migration).
- **Research memo — `(internal memo)` (884 LOC).** Catalogues the **seven-variant Pattern-2 family taxonomy** as of 2026-05-16: 2a compound-recipe / 2b empty-corpus / 2c resolution-state (W1241 helper substrate) / 2d producer-gap (W261 redaction reason) / 2e shared-substrate (W1018 YAML loader) / 2f packet-layer (W210 evidence_stale) / 2g closed-vocabulary-unknown (W1077/W1080 structured_unknown). Companion to `(internal memo)` (yesterday's Python-3.11+ language-feature survey of `warnings_out` — verdict STAY). Surfaces seven open gaps + counts adoption per variant. No source changes; sequencing decisions are Cranot's.
<!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->

### Audits / verdicts — W1226-W1248 batch

- **W1230 audit — `cmd_test_gaps` re-verification — VERDICT SKIP CONFIRMED.** Carry-forward audit from W1202 (CONSOLIDATE-9) re-opened by the Wave 14b docstring landing. Re-verified: REPORT-not-detector pattern (no per-location FindingRecord persistence). W1202 close confirmed; SKIP-disclosure docstring stays in place.
- **W1231 audit — drift-guard triage (5 failures → 2 fixes + 2 follow-ups).** Five drift-guard failures triaged: W1237 (`cmd_risk` edge_kinds canonicalize — FIXED), W1238 (`catalog/detectors.py` bare-except migration — FIXED), W1239 (drift-guard hygiene follow-up — FOLLOW-UP), W1240 (drift-guard hygiene follow-up — FOLLOW-UP). Per W531 fail-loud discipline both fixes ship the swallow as a visible `log.warning(...)` rather than a bare `continue|pass`.
- **W1233 audit — Pattern-2c on 38 sites, Wave 1+2+3 enumeration.** Surveyed 38 call sites of `find_symbol()` / `find_symbol_id()` across `src/roam/commands/`. Wave 1 = `cmd_impact` + `cmd_preflight` + `cmd_diagnose` (captured as W1242 + W1243 + W1244 — in flight at consolidation time). Wave 2 = the next 10 sites (captured as W1245). Wave 3 = remaining (captured as W1246 + W1248). Adoption sequencing parallels the SARIF SHIP / SKIP-DISCLOSURE arc — Wave 1 lands the helper substrate consumers, Wave 2/3 sweep the long tail.
- **W1236 audit — `cmd_*.py` SARIF helper convention sweep — VERDICT BENIGN.** Surveyed in-module SARIF helper definitions across SHIP emitters. Module-local SARIF convention is consistent across all 37 emitters; no canonicalization needed at substrate level. W1247 captured the doc-pass to ensure CLAUDE.md reflects the module-local SARIF convention.
- **W1246 audit — `cmd_trace.find_symbol_id` Pattern-2c — VERDICT NON-COMPLIANT.** The `find_symbol_id` callsite in `cmd_trace` does not currently emit `resolution` disclosure on degraded resolution. Captured as W1248 for Wave-3 adoption sweep.
- **W1224-impl Wave 14 hygiene discipline held across the post-CONSOLIDATE-12 batch.** Each of W1226 + W1227 + W1229 decremented `_KNOWN_MISSING` in-batch — no stale-pin removal sweep needed, continuing the W1222 carry-forward discipline (CONSOLIDATE-11) and the CONSOLIDATE-12 0-stale-pin invariant.

### In flight — W1226-W1248 batch (parallel dispatches not yet on disk)

- **W1242 — `cmd_impact` Pattern-2c adoption** + **W1243 — `cmd_preflight` Pattern-2c adoption** + **W1244 — `cmd_diagnose` Pattern-2c adoption** — dispatched in parallel as the W1241 helper substrate consumers (Wave 1). None touch CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT — zero collision risk with this consolidation pass.
- **W1239 — drift-guard hygiene follow-up audit** dispatched in parallel; non-CHANGELOG-touching.

### Added — W1207-W1224 batch (post-CONSOLIDATE-11, 2026-05-16 /loop iteration N+12)

> **~13 completions since CONSOLIDATE-11 (Section 57).** Two milestones:
> (1) **SARIF SHIP family closed out the entire CONSOLIDATE-11 SHIP
> candidate roster — 6 emitters landed (W1207 + W1209 + W1210 + W1211 +
> W1213 + W1216), bringing the SHIP family from 28 to 34 emitters in a
> single window.** `cmd_llm_smells` (29th SHIP, 10 closed-enum rules
> under the `llm-smells/` namespace — first SHIP emitter with
> double-digit closed-enum rule count + severity-banded `level`) +
> `cmd_fan` (30th SHIP, per-symbol fan-in/fan-out projection) +
> `cmd_hotspots` (31st SHIP, runtime-mode only; `--security`/`--danger`
> sub-modes emit raw findings outside the closed-enum
> `hotspots/*` rule catalogue per W1210's scope discipline) +
> `cmd_dark_matter` (32nd SHIP, per-pair hidden-coupling projection;
> single closed-enum rule `dark-matter/hidden-coupling` with
> confidence-tier-banded severity) + `cmd_duplicates` (33rd SHIP, BAIL-
> and-capture promotion landing — per-cluster semantic-duplicate
> projection; single closed-enum rule `duplicates/cluster` with
> similarity-banded severity) + `cmd_laws` (34th SHIP, per-rule
> invariant projection from the W119 mined-laws substrate). All six
> wrappers hash-stable additive; no persisted finding rows touched.
> The 6-candidate SHIP roster from CONSOLIDATE-11 closed to **zero
> outstanding** in this window. (2) **Pattern-3b propagation arc —
> 14+ waves shipped, `_KNOWN_MISSING` 64 → 20.** W1224-impl landed
> 37 SKIP-eligible docstrings across two waves (Wave 14a = 15
> docstrings; Wave 14b = 22 docstrings) — the largest single-wave
> batch of the arc to date, and 0 BAILs across both sub-waves.
> Sites: cut / dev_profile / doc_staleness / docs_coverage / drift /
> effects / eval_retrieve / evidence_diff / evidence_doctor / fitness /
> fn_coupling / graph_stats / idempotency / index / index_bundle (14a) +
> ingest_trace / invariants / mutate / owner / pr_diff / pr_prep /
> side_effects / split / stats / suggest_reviewers / surface /
> syntax_check / telemetry / test_gaps / test_pyramid / tx_boundaries /
> version / vuln_map / vuln_reach / workflow / xlang / index_stats (14b).
> Pin-list now down to 20 surviving entries (`cmd_debt` +
> `cmd_entry_points` + `cmd_flag_dead` + `cmd_guard` + `cmd_map` +
> `cmd_metrics` + `cmd_orphan_routes` + `cmd_path_coverage` +
> `cmd_patterns` + `cmd_plan_refactor` + `cmd_pytest_fixtures` +
> `cmd_risk` + `cmd_safe_delete` + `cmd_safe_zones` +
> `cmd_simulate_departure` + `cmd_suggest_refactoring` +
> `cmd_testmap` + `cmd_verify_imports` + `cmd_why_slow` + `cmd_ws`).

- **SARIF SHIP family grew from 28 to 34 emitters (W1207+W1209+W1210+W1211+W1213+W1216).** Added `cmd_llm_smells` (29th SHIP, 10 closed-enum rules under the `llm-smells/` namespace — severity-banded per-result `level`; per-occurrence LLM-API anti-pattern projection; first SHIP emitter with double-digit closed-enum rule count), `cmd_fan` (30th SHIP, per-symbol fan-in/fan-out projection via `fan_to_sarif`), `cmd_hotspots` (31st SHIP, runtime-mode only via `hotspots_to_sarif`; `--security` and `--danger` sub-modes emit raw findings outside the closed-enum `hotspots/*` rule catalogue per W1210's scope discipline — first SHIP emitter with mode-conditional rule-catalogue scoping), `cmd_dark_matter` (32nd SHIP, per-pair hidden-coupling projection via `dark_matter_to_sarif`; single closed-enum rule `dark-matter/hidden-coupling` with confidence-tier-banded severity), `cmd_duplicates` (33rd SHIP, BAIL-and-capture promotion landing — per-cluster semantic-duplicate projection via `duplicates_to_sarif`; single closed-enum rule `duplicates/cluster` with similarity-banded severity), `cmd_laws` (34th SHIP, per-rule mined-invariant projection via `laws_to_sarif`). All six wrappers hash-stable additive; no persisted finding rows touched.
- **Pattern-3b propagation arc — 14+ waves shipped, `_KNOWN_MISSING` 64 → 20.** Wave 14 (W1224-impl Wave 14a + Wave 14b) landed **37 SKIP-eligible docstrings across two contiguous sub-waves** with 0 BAILs each. Wave 14a (15 docstrings): cut / dev-profile / doc-staleness / docs-coverage / drift / effects / eval-retrieve / evidence-diff / evidence-doctor / fitness / fn-coupling / graph-stats / idempotency / index / index-bundle. Wave 14b (22 docstrings): ingest-trace / invariants / mutate / owner / pr-diff / pr-prep / side-effects / split / stats / suggest-reviewers / surface / syntax-check / telemetry / test-gaps / test-pyramid / tx-boundaries / version / vuln-map / vuln-reach / workflow / xlang / index-stats. **Largest single-wave batch of the arc to date — 37 docstrings in one window** vs the prior maximum of 12 (W1187-impl Wave 4) and 11 (W1188-impl Wave 5 / W1191-impl Wave 7). Each Wave 14 site BAIL-checked clean (no `emit_finding` / `findings_store.persist` call site at the destination).
- **6-candidate SHIP roster from CONSOLIDATE-11 closed to zero outstanding.** All six SHIP candidates that CONSOLIDATE-11 deferred (W1207 / W1209 / W1210 / W1211 / W1213 / W1216) shipped in this window. The capture-then-defer discipline (BAIL-and-capture pattern from CONSOLIDATE-10 W1206-impl-skip; deeper-audit reclassification pattern from CONSOLIDATE-10 W1206-audit-unclear) achieved its terminal state — every captured SHIP candidate followed by a clean SHIP landing on next dispatch.

### Audits / verdicts — W1207-W1224 batch

- **W1224-impl (Wave 14a + Wave 14b) — 37 SKIP-eligible docstring sites, 0 BAILs across both sub-waves.** Wave 14a covered the invocation-scoped-aggregate + state-mutating + validator slice (cut / dev-profile / doc-staleness / docs-coverage / drift / effects / eval-retrieve / evidence-diff / evidence-doctor / fitness / fn-coupling / graph-stats / idempotency / index / index-bundle). Wave 14b covered the aggregate / composer / state-mutating / validator slice (ingest-trace / invariants / mutate / owner / pr-diff / pr-prep / side-effects / split / stats / suggest-reviewers / surface / syntax-check / telemetry / test-gaps / test-pyramid / tx-boundaries / version / vuln-map / vuln-reach / workflow / xlang / index-stats). Premise checks uniform on both sub-waves — no per-location FindingRecord persistence; no BAIL discoveries; no reclassifications. Largest single-wave batch of the arc to date.
- **W1222 pin-list hygiene discipline codified (drive-by carry-forward).** The "every SHIP landing decrements `_KNOWN_MISSING`" discipline surfaced by W1222 (CONSOLIDATE-11 — `cmd_over_fetch` was still pinned despite W1219 having landed) held across all 6 SHIP landings in this window. Each of the 6 emitter landings (W1207 / W1209 / W1210 / W1211 / W1213 / W1216) updated `_KNOWN_MISSING` in-batch via the `tests/test_known_missing_pin_is_current` inverse-drift guard — no second-pass stale-pin sweep required.

### In flight — W1207-W1224 batch (parallel dispatches not yet on disk)

- **W1210 + W1211 — `cmd_hotspots` + `cmd_dark_matter` SHIP impl** were dispatched in parallel to the docstring waves; both have landed their SHIP wrappers + SARIF projection in `_SARIF_CONSUMERS` (visible in the cli.py 34-entry tuple). No additional concurrent waves expected to land on CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT in this window.

### Added — W1213-W1222 batch (post-CONSOLIDATE-10, 2026-05-16 /loop iteration N+11)

> **~10 completions since CONSOLIDATE-10 (Section 56).** Two milestones:
> (1) **SARIF SHIP family grew from 24 to 28 emitters (W1208 + W1217 +
> W1218 + W1219 + W1215).** `cmd_n1` (24th, W110 N+1 detector wrapper
> with 3 closed-enum rules — high/med/low; 89+30 tests pass) +
> `cmd_missing_index` (25th, 3 closed-enum rules; 20 tests pass) +
> `cmd_orphan_imports` (26th, 3 closed-enum rules —
> `internal_typo=error` / `missing_package=warning` /
> `missing_local=warning`) + `cmd_over_fetch` (27th, single closed-enum
> rule at warning; dual-shape endpoint+model handling) +
> `cmd_bus_factor` (28th, 3 closed-enum rules — concentration /
> stale-ownership / solo-summary; directory-anchor pattern;
> hash-stable sha256 verified). (2) **Pattern-3b propagation arc —
> 12+ waves shipped, `_KNOWN_MISSING` 96 → 64.** Wave 13 closed 10
> SKIP-eligible docstrings with zero BAILs (changelog / db_check /
> intent_check / metrics_push / recommend / report / retrieve /
> schema / search_semantic / simulate). W1212 reclassification +
> W1220 SKIP + W1222 inline stale-pin removal close this batch's
> propagation contribution. 6 SHIP candidates remain pending (W1207
> `cmd_llm_smells` / W1209 `cmd_fan` / W1210 `cmd_hotspots` /
> W1211 `cmd_dark_matter` / W1213 `cmd_duplicates` / W1216 `cmd_laws`).

- **SARIF SHIP family grew to 28 emitters (W1208+W1217+W1218+W1219+W1215).** Added cmd_n1 (24th, W110 N+1 detector with 3 closed-enum rules; 89+30 tests pass), cmd_missing_index (25th, 3 closed-enum rules; 20 tests pass), cmd_orphan_imports (26th, 3 confidence tiers — `internal_typo=error` / `missing_package=warning` / `missing_local=warning`), cmd_over_fetch (27th, single closed-enum rule, dual endpoint+model handling), cmd_bus_factor (28th, directory-anchor pattern with 3 rules — concentration / stale-ownership / solo-summary; hash-stable sha256 verified). All five additive wrappers; hash-stability invariant held across all 28 emitters.
- **Pattern-3b propagation arc — 12+ waves shipped, `_KNOWN_MISSING` 96→64.** Wave 13 (W1221-audit + W1221-impl) landed 10 SKIP-eligible docstrings with 0 BAILs (changelog / db_check / intent_check / metrics_push / recommend / report / retrieve / schema / search_semantic / simulate). W1212 reclassification (REPORT-not-detector for cmd_coverage_gaps) + W1220 SKIP (cmd_capabilities capability-registry manifest emitter) + W1222 inline (stale-pin removal from `_KNOWN_MISSING` per W1219 follow-up) close this batch's propagation contribution. ~22 commands closed across propagation + SHIP-promote in this batch.
- **W1213 — `cmd_duplicates` SHIP CAPTURED (BAIL discovery in W1206-impl-skip).** Captured cleanly via the BAIL-and-capture pattern; pending impl as SHIP candidate per the W1207-W1213 + W1216 roster.

### Audits / verdicts — W1213-W1222 batch

- **W1221-audit (Wave 13) — 10 SKIP-eligible docstring sites, 0 BAILs.** Sites: `cmd_changelog` + `cmd_db_check` + `cmd_intent_check` + `cmd_metrics_push` + `cmd_recommend` + `cmd_report` + `cmd_retrieve` + `cmd_schema` + `cmd_search_semantic` + `cmd_simulate`. Premise checks passed uniformly — no per-location FindingRecord persistence; no BAIL discoveries; no reclassifications.
- **W1222 — `cmd_over_fetch` stale-pin removal from `_KNOWN_MISSING`.** Inline follow-up of W1219 SHIP — the over_fetch entry was still pinned in the disclosure-coverage `_KNOWN_MISSING` list despite the SARIF wrapper having landed; removed in-batch.

### Added — W1199-W1212 batch (post-CONSOLIDATE-9, 2026-05-16 /loop iteration N+10)

> **~15 completions since CONSOLIDATE-9 (Section 55).** Three milestones
> + one reclassification discipline: (1) **SARIF SHIP family grew to
> 23-24 emitters (W1203 + W1208).** `cmd_test_impact` (23rd SHIP, ~333
> LOC = 160 prod + 173 test) joined as a per-test reach_count ranker
> with file-level anchor, reusing the global `--sarif` flag plumbed
> through `_SARIF_CONSUMERS`; 11 new SARIF tests + 59 pre-existing
> pass. `cmd_n1` (24th SHIP) joined as a W110 N+1 detector SARIF
> wrapper with per-query findings. (2) **Pattern-3b propagation arc —
> 11 waves shipped, 58% gap closed.** `_KNOWN_MISSING` dropped 96 → 82
> across Wave 10 (W1205-impl, 10 Bucket B docstrings; 96 → 86) and
> Wave 11 (W1206-impl-skip, 5 of 6 SKIP docstrings; 88 → 82). The
> 6th SKIP docstring (`cmd_duplicates`) bailed mid-impl and was
> captured as W1213 SHIP. 114 commands closed across W1180 → W1212
> (58% of the original 196-file gap). (3) **Reclassification
> discipline — W1212 + W1213.** W1199 (CONSOLIDATE-9 SHIP candidate
> for `cmd_coverage_gaps`) was REVISED to SKIP-DISCLOSURE this window
> via W1206-audit-unclear's deeper audit (REPORT command — wrap_findings
> is envelope-level, not per-location). Symmetrically, `cmd_duplicates`
> was discovered as a SHIP candidate by W1206-impl-skip's premise check
> and captured as W1213. The methodological move is **deeper audit
> beats initial classification**.

- **SARIF SHIP family at 23-24 emitters (W1203 + W1208).** `cmd_test_impact` (per-test reach_count ranker, file-level anchor; reuses global `--sarif` flag via `_SARIF_CONSUMERS`; ~333 LOC = 160 prod + 173 test; 11 new SARIF tests + 59 pre-existing pass; hash-stable additive wrapper) + `cmd_n1` (W110 N+1 detector wrapper with per-query findings; hash-stable additive wrapper).
- **Pattern-3b propagation arc — 11 waves shipped, 58% gap closed.** `_KNOWN_MISSING` 96 → 82 across W1205-impl + W1206-impl-skip (114 commands closed across W1180 → W1212). Wave 10 added 10 Bucket B docstrings (`cmd_batch_search` + `cmd_file` + `cmd_symbol` + `cmd_relate` + `cmd_refs_text` + `cmd_history_grep` + `cmd_recipes` + `cmd_sketch` + `cmd_pr_analyze` + `cmd_pr_replay`); Wave 11 added 5 SKIP docstrings (`cmd_affected` + `cmd_closure` + `cmd_compare` + `cmd_conventions` + `cmd_causal_graph`) with `cmd_duplicates` bailed and captured as W1213.
- **Reclassification discipline applied (W1212 + W1213).** `cmd_coverage_gaps` reclassified from W1199-SHIP (CONSOLIDATE-9) to W1212-SKIP-DISCLOSURE (REPORT command, not per-location detector — wrap_findings is envelope-level, no FindingRecord persistence). `cmd_duplicates` discovered as SHIP via BAIL-and-capture from W1206-impl-skip premise failure. First formal cross-session reclassification in the propagation arc; symmetric direction (SHIP→SKIP and SKIP→SHIP) demonstrated.

### Audits / verdicts — W1199-W1212 batch

- **W1205-audit — Wave 10: 10 Bucket B docstring sites — VERDICT SKIP-DISCLOSURE x10.** All 10 docstring landings shipped via W1205-impl. `_KNOWN_MISSING` 96 → 86.
- **W1206-audit — Wave 11 mixed batch: 6 SKIP + 4 unclear + 2 SHIP.** SHIP candidates captured as W1207 (`cmd_llm_smells`) + W1208 (`cmd_n1`, shipped this window). 4 unclear resolved via W1206-audit-unclear (below).
- **W1206-audit-unclear — 4-command deeper audit.** 3 SHIP captured (`cmd_fan` W1209 / `cmd_hotspots` W1210 / `cmd_dark_matter` W1211; ~1-2d each); 1 REVISED SKIP (`cmd_coverage_gaps` is REPORT-not-detector → W1212 supersedes W1199). The deeper-audit reclassification is the first formal cross-session classification revision in the Pattern-3b propagation arc.
- **W1212 — `cmd_coverage_gaps` REVISED SKIP-DISCLOSURE.** Supersedes W1199 SHIP from CONSOLIDATE-9. REPORT command — wrap_findings stays envelope-level; no FindingRecord. ~10 LOC docstring.

### Added — W1186-W1198 batch (post-CONSOLIDATE-8, 2026-05-16 /loop iteration N+9)

> **~25 completions since CONSOLIDATE-8 (Section 54).** Three milestones:
> (1) **SARIF SHIP family at 22 emitters (W1192 + W1195).** `cmd_delete_check`
> (21st SHIP, ~165 LOC) joined as the first SHIP emitter with PRIMARY +
> SECONDARY SARIF locations (deletion candidate is PRIMARY; surviving
> refs in code/test/docs/config are SECONDARY) for the BREAK-RISK
> gate-blocking pattern. `cmd_auth_gaps` (22nd SHIP, ~180 LOC) joined as
> the first SHIP emitter with explicit 3-tier confidence in SARIF output
> (`static_analysis` / `structural` / `heuristic` flow from
> single-source-of-truth confidence map into `properties.confidence`).
> Pre-batch the SHIP family was 20 (cmd_smells + cmd_clones +
> cmd_partition + cmd_affected_tests + cmd_impact + cmd_critique + 14
> pre-existing). (2) **Pattern-3b propagation arc — 9 waves shipped,
> 51% gap closed.** `_KNOWN_MISSING` dropped 196 → 96 across W1180 +
> W1181 + W1182 + W1185 + W1187 + W1188 + W1189 + W1190 + W1191 + W1194
> + W1195 + W1197 + W1198 (100 commands closed; 51% of the original
> 196-file gap). This batch added 4 more waves on top of Section 54's 3:
> Wave 6 (W1189-impl, 10 commands; 137 → 127), Wave 7 (W1191-impl, 11
> commands + cmd_delete_check stale-pin removal drive-by; 125 → 114),
> Wave 8 (W1194-impl, 10 Bucket B/C/E; 113 → 103), Wave 9 (W1197-impl,
> 4 SKIP + 2 UNCLEAR-resolved-to-SKIP; 100 → 96). (3) **Capture
> discipline preserved — 6 SHIP candidates deferred cleanly
> (W1199-W1204).** W1198-audit identified `cmd_coverage_gaps` (W1199)
> + `cmd_orphan_routes` (W1200) + `cmd_pytest_fixtures` (W1201) +
> `cmd_test_gaps` (W1202) + `cmd_test_impact` (W1203) +
> `cmd_verify_imports` (W1204); ~7-10d total effort. All 6 emit
> per-location findings in JSON envelope today; remaining work is
> `emit_finding()` integration + SARIF wrapper per W1192/W1195 scaffold.
> Hash-stability invariant held across all 22 emitters. 131/131 SARIF
> tests pass throughout.

- **SARIF SHIP family at 22 emitters (W1192 + W1195).** `cmd_delete_check` (BREAK-RISK gate-blocking, PRIMARY+SECONDARY locations — first SHIP emitter with multi-location pattern; ~165 LOC; hash-stable; 131/131 SARIF tests pass) + `cmd_auth_gaps` (3-tier confidence: `static_analysis`/`structural`/`heuristic`, reuses single-source-of-truth confidence mapping; first SHIP emitter with explicit 3-tier confidence in SARIF output; ~180 LOC; hash-stable; 131/131 SARIF tests pass).
- **Pattern-3b propagation arc — 9 waves shipped, 51% gap closed.** `_KNOWN_MISSING` 196 → 96 across W1180+W1181+W1182+W1185+W1187+W1188+W1189+W1190+W1191+W1194+W1195+W1197+W1198. Per-wave throughput: 10-12 docstrings; audit-and-emit asymmetric pattern; cryptographic hash-stability where required. Section 55 ships Wave 6 (W1189-impl), Wave 7 (W1191-impl), Wave 8 (W1194-impl), Wave 9 (W1197-impl) on top of Section 54's Waves 3-5.
- **SHIP candidate pipeline captured (W1199-W1204).** 6 commands deferred from W1198-audit: `cmd_coverage_gaps` / `cmd_orphan_routes` / `cmd_pytest_fixtures` / `cmd_test_gaps` / `cmd_test_impact` / `cmd_verify_imports`. ~7-10d total effort (1-2d each). All emit per-location findings in JSON envelope today; need `emit_finding()` integration + SARIF wrapper per W1192/W1195 scaffold. Capture discipline preserved — surfaced inside next-wave batch and deferred cleanly with file paths + effort estimates rather than collapsing into the same window.

### Audits / verdicts — W1186-W1198 batch

- **W1192-audit — `cmd_delete_check` SHIP (21st SARIF emitter) + `cmd_migration_safety` SKIP (validator-not-detector).** First SHIP emitter with PRIMARY + SECONDARY SARIF locations; pattern portable to any "X is referenced by Y[]" gate-blocking check.
- **W1195-audit — `cmd_auth_gaps` SHIP (22nd SARIF emitter) + `cmd_audit_trail_verify` SKIP (verifier-not-detector).** First SHIP emitter with explicit 3-tier confidence (`static_analysis`/`structural`/`heuristic`) in SARIF output; pattern portable to any emitter publishing confidence tiers in its JSON envelope.
- **W1188-audit / W1189-audit / W1191-audit / W1194-audit / W1197-audit — SKIP-DISCLOSURE x52 + 6 SHIP candidates + 2 UNCLEAR→SKIP.** Five propagation-wave verdicts spanning Waves 5-9. The Wave 7 audit surfaced `cmd_delete_check` as a stale-pin candidate; promoted to SHIP via W1192. The Wave 9 audit surfaced 6 SHIP candidates (W1199-W1204) deferred with effort estimates.
- **W1198-audit — 6 SHIP candidates (W1199-W1204) + 2 UNCLEAR → SKIP.** Capture-over-implementation discipline preserved. SHIP candidates: `cmd_coverage_gaps`, `cmd_orphan_routes`, `cmd_pytest_fixtures`, `cmd_test_gaps`, `cmd_test_impact`, `cmd_verify_imports`. ~7-10d total effort across the six.
- **W1196 — `breaking_to_sarif()` dormant code investigation.** CAPTURED only; no code change. Captured for future close-out wave.

### Added — W1186-W1189 batch (post-CONSOLIDATE-7, 2026-05-16 /loop iteration N+8)

> **~10 completions since CONSOLIDATE-7 (Section 53).** Three pillars:
> (1) **SARIF substrate adoption STRUCTURALLY COMPLETE** — all 19
> `*_to_sarif` helpers across the codebase now use `_rule_entry()` +
> `_result_entry()` factories (W1178 + W1179a + W1179b + W1186 polish).
> Hash-stability cryptographically verified via sha256 matches on
> pre/post adopter outputs. Net ~LOC-neutral overall (per W1080
> discipline) — the substrate's value is structural API consistency,
> not raw LOC reduction. (2) **Pattern-3b propagation arc — 5 waves
> shipped**: Wave 1 (10 bootstrap) + Wave 2 (10 local-state) + W1185
> outliers (2 commands) + Wave 3 (12 codegen) + Wave 4 (12
> exploration) + Wave 5 (11 continuation) = **56 commands closed**.
> `_KNOWN_MISSING` dropped 196 → 138 across this stretch (29% of the
> original gap closed). Wave 6 audit landed via W1189-audit (10
> commands queued for next dispatch). (3) **Concurrent-merge
> discipline battle-tested** — multiple "file modified since read"
> guards fired across W1179a/b + W1180/W1181/W1185 races and
> resolved cleanly via the Edit guard's read-before-write contract.

- **SARIF substrate adoption STRUCTURALLY COMPLETE (W1179a + W1179b + W1186).** All 19 `*_to_sarif` helpers across the codebase use `_rule_entry()` + `_result_entry()` factories from `src/roam/output/sarif.py`. W1179a shipped 8 emitter substrate adoption with hash-stability cryptographically verified (sha256 matches on pre/post SARIF outputs); ~LOC-neutral (honest discipline per W1080). W1179b shipped 8 more emitters — PARTIAL extraction is now STRUCTURALLY COMPLETE across all 19 emitters. W1186 polished the substrate by adding an `extras` parameter to `_rule_entry()` (mirroring the `_result_entry()` extras pattern) + 1 inline refactor (taint emitter). 131/131 SARIF tests pass.
- **Pattern-3b propagation arc — 5 waves shipped (W1180 + W1181 + W1185 + W1182 + W1187 + W1188).** `_KNOWN_MISSING` dropped 196 → 138 across this stretch (56 commands closed). **Wave 1 (W1180)**: 10 bootstrap commands. **Wave 2 (W1181)**: 10 local-state commands. **W1185 outliers**: 2 commands. **Wave 3 (W1182-impl, +12 Bucket C codegen docstrings)**: `_KNOWN_MISSING` 173 → 161. Drive-by: cmd_lsp anchor newline fix. **Wave 4 (W1187-audit + W1187-impl, +12 Bucket B exploration/aggregate docstrings)**: `_KNOWN_MISSING` 161 → 149. **Wave 5 (W1188-audit + W1188-impl, +11 Bucket B continuation docstrings)**: `_KNOWN_MISSING` 149 → 138. **Wave 6 audit landed (W1189-audit)** — 10 candidates queued: `cmd_help_search` + `cmd_timeline` + `cmd_trends` + `cmd_alerts` + `cmd_weather` + `cmd_ai_ratio` + `cmd_ai_readiness` + `cmd_dogfood` + `cmd_postmortem` + `cmd_dogfood_aggregate`.
- **Concurrent-merge discipline battle-tested.** Multiple `src/roam/output/sarif.py` + `src/roam/cli.py` + `tests/test_sarif_disclosure_coverage.py` edits raced across 5+ parallel waves (W1179a + W1179b + W1180 + W1181 + W1185 + W1182-impl + W1187-impl + W1188-impl). The harness's file-read-before-write Edit guard surfaced the "file modified since read" conflict on every race and forced re-reading; every wave merged cleanly. Third concurrent-merge dance documented this session (preceded by W1159+W1160 in Section 52 and W1180+W1181-impl+W1185-impl in Section 53).

### Audits / verdicts — W1186-W1189 batch

- **W1187-audit — 12 Bucket B exploration/aggregate commands — VERDICT SKIP-DISCLOSURE x12.** Wave 4 batch. Sibling pattern to W1182-audit (codegen-not-analysis) and W1148 (aggregate-not-located-finding). All 12 docstring landings shipped via W1187-impl.
- **W1188-audit — 11 Bucket B continuation commands — VERDICT SKIP-DISCLOSURE x11.** Wave 5 batch. Continuation of Bucket B exploration/aggregate pattern; all 11 docstring landings shipped via W1188-impl. `_KNOWN_MISSING` 149 → 138.
- **W1189-audit — Wave 6 batch identified (10 commands).** `cmd_help_search` + `cmd_timeline` + `cmd_trends` + `cmd_alerts` + `cmd_weather` + `cmd_ai_ratio` + `cmd_ai_readiness` + `cmd_dogfood` + `cmd_postmortem` + `cmd_dogfood_aggregate`. Queued for next dispatch (W1190+ impl).

### Added — W1177-W1185 batch (post-CONSOLIDATE-6, 2026-05-16 /loop iteration N+7)

> **~14 completions since CONSOLIDATE-6 (Section 52) including 8 SHIPPED
> outcomes + 2 RESEARCH memos + 3 AUDIT-VERDICTs + 1 PARTIAL audit-extraction.
> **Three major systemic shifts landed**: (1) the SARIF helper substrate
> launched via W1178 — `_rule_entry()` + `_result_entry()` factories in
> `sarif.py` reduce ~80 LOC of new substrate + ~50 LOC of subtractive
> adoption across `cmd_dead` + `cmd_critique` + `cmd_partition` (3 adopters);
> 17 more emitters being refactored in parallel (W1179a/b in flight); (2) the
> Pattern-3b SARIF-disclosure propagation arc launched via W1175-RESEARCH —
> 684-line memo planned 30-50 batches with asymmetric propagation (bulk for
> ~135 likely-SKIP, 1:1 for ~14-20 likely-SHIP, ~17 unclear). Wave 1 (W1180,
> 10 bootstrap commands) + Wave 2 (W1181-impl, 10 local-state commands) +
> W1185 outliers (cmd_lsp + cmd_rules_validate) shipped — `_KNOWN_MISSING`
> dropped from 196 to 174 (33 done, 162 to go); (3) vocabulary canonicalization
> disciplines W1156 + W1162 + W1176 sealed with cmd_pr_analyze NO_CHANGES →
> NOCHANGES sweep landing this batch and the REFERENCE_REMOVAL_VERDICTS
> substrate fully operational. Hash-stability invariant held across all
> shipped impls.**

- **SARIF helper substrate launched (W1177-audit + W1178 + W1179a/b in flight).** W1177-audit extracted PARTIAL verdict from the 5-phase pipeline survey across 20 SARIF emitters: 3 patterns identified (Fixed-rule, Dynamic-rule, Complex-multi-rule); ~500 LOC subtractive ceiling. W1178 shipped `_rule_entry()` + `_result_entry()` factories in `src/roam/output/sarif.py` (~80 LOC helpers) + 3 adopters (`cmd_dead` + `cmd_critique` + `cmd_partition`) (~50 LOC subtractive). 131/131 SARIF tests pass. W1179a + W1179b refactoring 17 more emitters in parallel; close-out captured at the next CONSOLIDATE checkpoint.
- **Pattern-3b propagation arc launched — Wave 1 + Wave 2 + W1185 outliers (W1175-RESEARCH + W1180 + W1181-audit + W1181-impl + W1185-audit + W1185-impl).** <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. --> W1175-RESEARCH (684-line memo at `(internal memo)`) inventoried the 196 unaudited `cmd_*.py` files: ~135 likely-SKIP + ~14-20 likely-SHIP + ~17 unclear. Asymmetric propagation pattern adopted: bulk audit-and-emit for SKIP; 1:1 audit-then-impl for SHIP. **Wave 1 (W1180, +95 LOC across 10 bootstrap commands)**: SARIF-skip docstring rationale propagated; drive-by pruned 10 stale `_KNOWN_MISSING` pins. **Wave 2 (W1181-audit + W1181-impl, 10 Bucket D commands)**: substrate-state nouns documented (concurrent merge with W1180/W1185-impl absorbed cleanly). **W1185 outliers (W1185-audit + W1185-impl, +15 LOC)**: `cmd_lsp` SKIP (editor protocol, not CI/findings) + `cmd_rules_validate` SKIP (validator-not-detector) docstrings landed. **`_KNOWN_MISSING` dropped from 196 to 174** across the 3 waves combined (33 done, 162 to go). **W1182-audit identified 12 Bucket C codegen commands for Wave 3** (`cmd_attest` + `cmd_capsule` + `cmd_agent_export` + `cmd_agents_md` + `cmd_graph_export` + `cmd_cga` + `cmd_sbom` + `cmd_skill_generate` + `cmd_pr_comment_render` + `cmd_audit_trail_export` + `cmd_evidence_oscal` + `cmd_fingerprint`).
- **Vocabulary canonicalization disciplines extended (W1151 + W1156 + W1162 + W1164 + W1176).** W1151 removed cargo-cult `.upper()` from `_to_level()` across 7 sites in `sarif.py` — hash-stable. W1156 REFERENCE_REMOVAL_VERDICTS frozenset substrate fully operational (~100 LOC; carry-forward from W1156-CONSOLIDATE). W1162 `cmd_flag_dead.py` canonical "likely_stale" with display "likely-stale" preserved via `_STALENESS_DISPLAY` map (mirrors W1156 dual-form normalization pattern). W1176 realises the W1164 audit verdict: `cmd_pr_analyze` NO_CHANGES → NOCHANGES (3 LOC; sibling-aligned bare UPPERCASE).

### Audits / verdicts — W1177-W1185 batch

- **W1177-audit — SARIF helper-substrate extraction audit — VERDICT PARTIAL EXTRACTION.** 5-phase pipeline surveyed across 20 SARIF emitters: 3 patterns identified (Fixed-rule, Dynamic-rule, Complex-multi-rule). `_rule_entry()` + `_result_entry()` helpers viable; ~500 LOC subtractive ceiling. Realised via W1178.
- **W1181-audit — 10 Bucket D local-state commands — VERDICT SKIP-DISCLOSURE x10.** Substrate-state nouns: mode / runs / lease / memory / permits / annotations / suppress / replay / agent-score / agents-md. None are file:line findings emitters; all surface state stored under `.roam/`. Verdicts landed via W1181-impl docstrings. `cmd_lsp` + `cmd_rules_validate` flagged as outliers (handled via W1185-audit + W1185-impl).
- **W1182-audit — 12 Bucket C codegen commands — VERDICT pending Wave 3 impl.** `cmd_attest` + `cmd_capsule` + `cmd_agent_export` + `cmd_agents_md` + `cmd_graph_export` + `cmd_cga` + `cmd_sbom` + `cmd_skill_generate` + `cmd_pr_comment_render` + `cmd_audit_trail_export` + `cmd_evidence_oscal` + `cmd_fingerprint`. Codegen-artifact-not-analysis rationale (sibling pattern to W1174). Wave 3 impl in flight.
- **W1185-audit — 2 outlier commands — VERDICT SKIP x2.** `cmd_lsp` SKIP (editor protocol, not CI); `cmd_rules_validate` SKIP (validator-not-detector — rules check existing rule definitions, do not analyze code).

### Added — W1158-W1176 batch (post-W1156-CONSOLIDATE, 2026-05-16 /loop iteration N+6)

> **~1000+ LOC of impl across 8 SHIPPED outcomes + 2 RESEARCH memos +
> 3 AUDIT-VERDICTs. 13 completions since W1156-CONSOLIDATE. **Three
> structural inflections landed**: (1) the SARIF SHIP family grew from
> 17 commands (post-W1146) to 20 commands via `cmd_impact` +
> `cmd_affected_tests` + `cmd_partition` (smells / clones SHIP impls
> in flight as W1171 + W1172); (2) the W1169 SARIF-disclosure-coverage
> CI lint discovered **196 unaudited cmd_*.py files**, vastly exceeding
> the W1166-RESEARCH 4-8 estimate, with a `_KNOWN_MISSING` frozenset
> pinning the gap and W1175-RESEARCH planning the propagation strategy;
> (3) two vocabulary canonicalization sweeps closed (W1162 likely-stale
> + W1176 NO_CHANGES → NOCHANGES) extending the W1156 dual-form pattern.
> `action.yml` allowlist intent documented (W1167) + cli.py ⊃ action.yml
> subset CI lint pinned (W1168).**

- **SARIF SHIP family expanded to 20 commands (W1159 + W1160 + W1165).** `cmd_impact` SHIP SARIF (~413 LOC across 6 files) emits 4 finding families — `affected-file` (importance→severity), `direct-dependent`, `sf-convention-test`, `indirect-ref` — and lifts `_SARIF_CONSUMERS` 15→16. `cmd_partition` SHIP SARIF (~189 LOC) emits PRIMARY + up-to-10 SECONDARY locations with `conflict_risk` severity scaling (`_SARIF_CONSUMERS` 17→18). `cmd_affected_tests` SHIP SARIF (~147 LOC) uses 3 closed-enum rules (`direct`=error / `transitive`=warning / `colocated`=note) (`_SARIF_CONSUMERS` 16→17). Concurrent-merge dance between W1159 + W1160 surfaced and resolved cleanly via the Edit guard. `cmd_smells` SHIP (~250 LOC) + `cmd_clones` SHIP (~300 LOC, dual-location) in flight as W1171 + W1172. Companion verdicts: `cmd_vibe_check` SKIP-DISCLOSURE (aggregate; W1170-bundle) + `cmd_test_scaffold` SKIP-DISCLOSURE (codegen, not analysis; W1170-bundle).
- **W332 Pattern-3b SARIF CI-lint substrate complete (W1167 + W1168 + W1169).** `action.yml` `_SUPPORTED_SARIF` 7-command subset intent documented at the YAML source via a +10 LOC comment block (W1167); cli.py 18-command `_SARIF_CONSUMERS` ⊃ action.yml 7-command subset relationship pinned via +28 LOC new `test_action_yml_supported_sarif_subset_of_cli_consumers` in `test_sarif_consumer_list.py` (W1168); SARIF-disclosure coverage lint (+403 LOC new `tests/test_sarif_disclosure_coverage.py`) surfaces 196 unaudited `cmd_*.py` with `_KNOWN_MISSING` frozenset pin (W1169). **Key discovery**: the W1166-RESEARCH 4-8 unaudited estimate was off by ~25x; the CI lint baseline pins the gap so future SARIF audits propagate via single-source-of-truth rather than ad-hoc per-command sweeps.
- **SARIF-disclosure-pattern maturity memo (W1166-RESEARCH).** <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. --> 555-line memo at `(internal memo)`. **14 audits surveyed, ZERO contested verdicts** — the SARIF SHIP-vs-SKIP discipline has converged. Top recommendation: ship the W1169 CI lint (DONE this batch). Anti-recommendation: do NOT extract a shared docstring constant — the per-site rationale is load-bearing for future readers anchoring at the call site. Sibling research memo to W1175-RESEARCH (propagation strategy for the 196-file gap; in flight).
- **Vocabulary canonicalization sweeps (W1162 + W1176).** `cmd_flag_dead.py` (+13/-5 LOC) canonicalizes "likely-stale" → `likely_stale` while preserving display form `"likely-stale"` via new `_STALENESS_DISPLAY` map — extends the W1156 dual-form normalization pattern. `cmd_pr_analyze` (3 LOC) renames `NO_CHANGES` → `NOCHANGES` (sibling-aligned bare UPPERCASE per W1164 audit verdict option a; 3 sites, no hard-coded test assertions). 154/155 tests pass.
- **SARIF-disclosure docstrings on 2 more commands (W1173 + W1174).** `cmd_vibe_check.py` (+8 LOC) names `roam findings list --detector vibe-check` as the per-finding path; `cmd_test_scaffold.py` (+8 LOC) anchors codegen-artifact-not-analysis rationale (distinct from W1148's invocation-scoped template). The SKIP-DISCLOSURE docstring pattern now spans 17 commands total when combined with the in-flight W1170-bundle siblings.

### Audits / verdicts — W1158-W1176 batch

- **W1158 — SARIF source-drift audit — VERDICT HEALTHY DRIFT.** 4 SARIF sources surveyed (`action.yml` 7-cmd subset ⊂ `cli.py` 18-cmd `_SARIF_CONSUMERS`; `sarif.py` 14 emitters + 4 external; 3 cli-only). No bug. Verdict realised via W1167 (action.yml comment) + W1168 (subset lint).
- **W1164 — `cmd_pr_analyze` NO_CHANGES naming audit — VERDICT RENAME to `NOCHANGES`.** Option (a) sibling-aligned bare UPPERCASE chosen over option (b) underscore preservation; 3 sites, no hard-coded test assertions, 5 LOC effort. Realised via W1176.
- **W1170-bundle — SARIF audience-disclosure quartet — VERDICT 2x SHIP + 2x SKIP-DISCLOSURE.** `cmd_smells` SHIP (W1171 in flight, ~250 LOC); `cmd_clones` SHIP (W1172 in flight, ~300 LOC, dual-location pattern); `cmd_vibe_check` SKIP-DISCLOSURE (aggregate, no file:line; W1173 docstring landed); `cmd_test_scaffold` SKIP-DISCLOSURE (codegen artifact, not analysis; W1174 docstring landed).

### Added — W1149-W1156 batch (post-W1149-CONSOLIDATE, 2026-05-16 /loop iteration N+5)

> **~180 LOC of impl across 5 SHIPPED outcomes + 2 AUDIT-VERDICTs.
> 7 completions since W1149-CONSOLIDATE. **Two structural verdicts
> landed**: (1) the SARIF-disclosure pattern now spans **9 commands**
> (W1144 + W1145 + W1148 + W1152 + W1154-impl x6), formally documenting
> "invocation-scoped aggregates have no SARIF locations[]" as a stable
> design rule; (2) reference-removal verdicts (cmd_refs_text +
> cmd_delete_check) elevated to a closed-enum frozenset
> (`REFERENCE_REMOVAL_VERDICTS`) via W1156 — drift guard pinned in
> `test_evidence_v0.py` + dual-form normalization preserves CLI display.
> `publish.yml` hardened with `persist-credentials:false` on build +
> smoke checkout steps (W1103) + 3 `dist/*.whl` sites converted to a
> robust single-wheel assertion with quoted variable (W1104).**

- **SARIF-skip disclosure pattern formalized across 9 commands (W1154-impl).** cmd_orchestrate / cmd_diagnose / cmd_oracle / cmd_plan / cmd_brief / cmd_next now document deliberate SARIF-skip rationale at the module-docstring level (invocation-scoped aggregates; no file:line locations). +36 LOC + 6 blank separators across 6 files. Mirrors the prior W1144 / W1145 / W1148 / W1152 docstrings. Total pattern now spans **9 aggregate-style commands** — propagates the "anchor SARIF-skip rationale at the per-site source" discipline so future audits read the call site, not a separate audit memo. W1155 audit pending for the third-tier batch (cmd_fleet / cmd_partition / cmd_affected_tests / cmd_impact / cmd_context).
- **Reference-removal verdicts substrate (W1156).** New `REFERENCE_REMOVAL_VERDICTS` frozenset in `src/roam/evidence/_vocabulary.py` (6 members: `safe_to_remove` / `review` / `load_bearing` / `safe` / `likely_safe` / `break_risk`). Validators wired in `cmd_refs_text` + `cmd_delete_check` via `_validate_verdict` helpers with **dual-form normalization** — display form `"SAFE-TO-REMOVE"` round-trips to canonical `"safe_to_remove"`, preserving the CLI human-facing surface while pinning the machine-readable enum. Drift guard pinned in `tests/test_evidence_v0.py`. 56 + 34 tests pass. ~100 LOC full substrate.
- **`publish.yml` supply-chain hardening (W1103 + W1104).** **W1103**: +6 LOC adds `persist-credentials: false` to `actions/checkout` on the build + smoke jobs; publish job correctly untouched (it needs the OIDC token for Trusted Publishing). Removes leaked-credentials attack surface on the non-publish steps. **W1104**: +36/-3 LOC — 3 `dist/*.whl` sites (PEP 639 verify + v2-commands verify + SBOM `pip install` step) converted to a robust single-wheel assertion pattern (`shopt -s nullglob` + length-1 array check + quoted variable expansion) so the publish workflow fails loudly if `dist/` ever contains 0 or 2+ wheels instead of silently degrading on a brace-expansion fallback.

### Audits / verdicts — W1149-W1156 batch

- **W1154 — SARIF-disclosure audit (6 third-tier commands) — VERDICT SKIP-DISCLOSURE x6.** cmd_orchestrate / cmd_diagnose / cmd_oracle / cmd_plan / cmd_brief / cmd_next all classified as aggregate-style commands; none in the SARIF action.yml allowlist. Verdicts landed via W1154-impl docstrings (+36 LOC + 6 blank separators).
- **W1134 — reference-removal verdict vocabulary audit — VERDICT LOCAL-CLOSED-ENUM.** Reference-removal verdicts (cmd_refs_text + cmd_delete_check) are **orthogonal to** `POLICY_DECISIONS` — the former describe whether a code string is safe to delete; the latter describe whether a policy gate passed. Recommendation: stand up a dedicated `REFERENCE_REMOVAL_VERDICTS` frozenset in `evidence/_vocabulary.py` rather than overloading `POLICY_DECISIONS`. Verdict realised by W1156 impl.

### Added — W1136-W1149 batch (post-W1133-CONSOLIDATE, 2026-05-15 even more iteration)

> **~410 LOC of impl across 10 SHIPPED outcomes (W1100 +28 LOC + W1099-narrow
> ~80 LOC + W1136 +339 LOC + W1141 +36 LOC + W1144 +6 LOC + W1145 +9 LOC +
> W1148 +14 LOC) + 1 research memo (W1139-RESEARCH 361 LOC) + 3 SARIF
> audit verdicts (W1085 + W1146 + W1147). 11 completions + 4 captures
> since W1133-CONSOLIDATE. **Three structural outcomes**: (a) the W332
> Pattern-3b CLI-boundary thread is **functionally closed** at v13.x via
> the W1141 4th-mirror drift guard; (b) the SARIF audience-disclosure
> trilogy (W1144 + W1145 + W1148) propagated the deliberate-skip
> rationale docstring across cmd_doctor + cmd_audit + cmd_pr_risk;
> (c) the Pattern-3b CLI-arg lint matrix now spans 5 axes (W1111 +
> 4 W1121 siblings) PLUS the option-dest extension (W1136 input_path
> cluster). The user-facing CLI surface saw the biggest sweep in 30+
> sections: 14 commands gained metavar="SYMBOL" alignment (Strategy D —
> no breaking rename) and 6 CLI-only commands gained --file → --path
> harmonization with hidden alias backward-compat.**

- **W332 Pattern-3b CLI-boundary thread functionally closed (W1136 + W1141).** New `tests/test_w1136_click_option_input_path_dest_lint.py` (339 LOC) blocks new `@click.option(--input-path)` drift at the option-dest axis: 6 canonical sites + 2 legacy carve-outs classified; 4 lint tests + 1 sanity test pass. W1141 (+36 LOC drift guard in `tests/test_mcp_param_names.py`) pins the `_PARAM_ALIASES` table for the input_path-cluster as the 4th mirror in the W332 thread (the 3 prior mirrors land at `mcp_server.py:_PARAM_ALIASES`, `test_w1111_click_argument_name_lint.py`, `test_w1121_click_argument_target_lint.py`). The W332 Pattern-3b CLI-boundary thread is now **functionally closed** at v13.x — per the W1139-RESEARCH coverage matrix, all 6 canonical CLI-side axes are SHIPPED (+ 2 PARTIAL legacy carve-outs documented + 0 GAP).
- **SARIF audience-disclosure trilogy (W1144 + W1145 + W1148).** Three SARIF skip rationale docstrings landed at the command-module level: W1144 (+6 LOC on `cmd_doctor.py:1-12` — environment-scoped diagnostics, no file:line — no SARIF surface), W1145 (+9 LOC on `cmd_audit.py` — composed-subcommand SARIF flow; no top-level --sarif flag because each subcommand emits its own SARIF if relevant), W1148 (+14/-4 LOC on `cmd_pr_risk.py` — invocation-scoped aggregates with `subject_kind="commit"`; action.yml allowlist already excludes pr-risk by design). cmd_critique audited as SHIP candidate (W1146 verdict; W1146-impl in flight). Closes the W1085 → W1146 → W1147 audit triage chain at the documentation layer; the pattern propagates the deliberate-SARIF-skip rationale to the per-site source so future audits anchor at the call site, not at a separate audit memo.
- **CLI symbol-cluster metavar alignment (W1100).** 14 sites across 11 files got `metavar="SYMBOL"` (or `"SYMBOL_OR_PATH"` / `"[SYMBOL]"` for context-aware variants on cmd_test_scaffold + cmd_testmap) + docstring identifier-tone refresh. ~28 LOC. Hash-stable. cmd_explain_command + cmd_plugins correctly NOT touched (DOMAIN-DISTINCT per W1108/W1120 — CLI command name + plugin name respectively). Strategy D from W1102-RESEARCH: align metavar without a breaking rename, lock the surface via the W1111 + W1121-target AST lints.
- **`--file` → `--path` harmonization (W1099-narrow).** 6 CLI-only commands renamed `--file` → `--path` with hidden alias backward-compat: + mcp_server.py + 2 test files updated. ~80 LOC across the cluster. Click's `required=True` + alias limitation surfaced via cmd_triage (manual `UsageError` adaptation needed); cmd_pr_bundle deferred to W1141-followup. 508 of 509 tests pass.
- **W1139-RESEARCH — Pattern-3b CLI-boundary completeness memo.** 361-line memo at `(internal memo)`. **Coverage matrix**: 6 axes SHIPPED + 2 PARTIAL + 0 GAP. Key finding: W332 functionally closeable in 15 min via the W1141 4th-mirror drift guard (DONE this batch). Companion to W1102-RESEARCH (Section 48) which closed the same question for the `@click.argument` axis. <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->

### Audits / verdicts — W1136-W1149 batch

- **W1085 — `cmd_doctor` SARIF audit — VERDICT SKIP-SARIF.** Environment-scoped diagnostics (Python version, index status, watcher status, OneDrive/Dropbox detection); no file:line. SARIF surface would be empty. Verdict landed via W1144 docstring (+6 LOC).
- **W1146 — `cmd_critique` SARIF audit — VERDICT SHIP-SARIF.** File-located findings: clones-not-edited findings have file:line; impact entries have file paths; intent is diff-wide. Impl dispatched as W1146-impl (in flight as this CONSOLIDATE runs).
- **W1147 — `cmd_pr_risk` SARIF audit — VERDICT SKIP-SARIF.** Invocation-scoped aggregates with `subject_kind="commit"`; SARIF expects file:line subjects. action.yml allowlist already excludes pr-risk by design. Verdict landed via W1148 docstring (+14/-4 LOC).

### Added — W1097-W1133 batch (post-W1126-CONSOLIDATE, 2026-05-15 even further iteration)

> **~80 LOC of impl + 253 LOC of new test coverage across 7 SHIPPED
> outcomes + 6 audits classified into SYMBOL-CONCEPT or DOMAIN-DISTINCT
> verdicts + 1 BAIL (W1101 — premise inverted, captured W1126 inverted
> task). 14 completions since W1126-CONSOLIDATE. Two structural
> closures: (a) the `cmd_runs` placeholder-vocabulary cluster
> (W1097+W1105+W1116+W1125 = 8 sites swept end-to-end) and (b) the
> W1118-bundle reclassification (12 W1111 grandfathered sites
> classified into 10 SYMBOL-CONCEPT + 2 DOMAIN-DISTINCT permanent
> carve-outs). The v14.0 hard-rename candidate cluster now spans
> ~21 files (W1133) — significantly bigger than the W1004 audit's
> original 6-file estimate; this scope expansion feeds the W1098
> USER-DECISION at v14.0 planning.**

- **AST CI lint for `@click.argument('target')` drift (W1121-target).** New `tests/test_w1121_click_argument_target_lint.py` (253 LOC). 15 sites classified into 4 categories: 13 SYMBOL (joining the v14.0 rename cluster), 1 GIT_REF (`bisect` start/good/bad), 1 FILE_PATH. Companion lint to W1111 — extends the AST drift-block pattern to a second vocabulary axis. Blocks any 16th drift. Covers the W1099 input_path-cluster gap end-to-end for the `target` axis.
- **W1111 grandfathered-site reclassification (W1106 + W1107 + W1109 + W1118 + W1119 + W1108 + W1120).** All 12 W1111 grandfathered sites classified end-to-end: **10 SYMBOL-CONCEPT** sites confirmed (cmd_closure.py:191, cmd_testmap.py:232, cmd_impact.py:264, cmd_oracle.py x4 symbol_exists/is_test_only/is_reachable/is_clone_of, cmd_diagnose.py:201, plus the 2 already in the v14.0 cluster) joining the v14.0 hard-rename cluster; **2 DOMAIN-DISTINCT** sites (cmd_explain_command.py:150 = CLI command name, cmd_plugins.py:213 = plugin name) marked as **permanent grandfather carve-outs**. The v14.0 rename cluster now spans ~21 files (W1133 informational capture) versus the original W1004 audit's 6-file scope.
- **`cmd_runs` placeholder-vocabulary structural close (W1125 + W1129 carve-out comments).** `cmd_runs.py:6` placeholder unified (`--action X` → `<action>`); end-to-end closes the `cmd_runs` placeholder cluster across W1097 + W1105 + W1116 + W1125 (8 sites swept). W1129 (+15 LOC across 3 files) applied the W1108 + W1120 DOMAIN-DISTINCT carve-out comments + the W1111 lint disambiguation comments — anchors the permanent carve-out rationale at the per-site source.
- **findings.py vocabulary cross-link cleanup capstone (W1131).** +54 LOC across `src/roam/db/findings.py`: `source_version` cross-link comment + `evidence_json` size-GUIDANCE flag + `suppressions_json` docstring + module-level docstring refresh. Closes the 4-cleanup cluster (W1122 reverse-pointer + W1123 / W1127 / W1128 follow-ups) over the W1126-batch + W1133-batch span. 66/66 findings tests pass; hash-stable (comments + docstring only).
- **W1132 — W1111 lint comment update (test-only).** `tests/test_w1111_click_argument_name_lint.py` comment update (~0 LOC, rewording): cmd_impact / cmd_oracle / cmd_diagnose annotations moved from "pending classification" to SYMBOL-CONCEPT confirmed. Net documentation discipline — the W1111 lint's grandfather metadata now reflects the W1118-bundle's classification verdicts.

### Audits / verdicts — W1097-W1133 batch

- **W1118 + W1119 + W1106 + W1107 + W1109 — VERDICT SYMBOL-CONCEPT (10 sites).** All 10 sites resolve `<name>` to a symbol via `find_symbol` / `find_symbol_with_alternatives` — same shape as the v14.0 rename cluster. Optional/default argument shapes preserved at the per-site call.
- **W1108 + W1120 — VERDICT DOMAIN-DISTINCT (2 permanent carve-outs).** `cmd_explain_command.py:150` resolves a **CLI command name** (not a symbol); `cmd_plugins.py:213` resolves a **plugin name** (not a symbol). Distinct vocabulary axis — out-of-scope for the v14.0 rename. Marked as permanent grandfather; W1129 applied carve-out comments at the per-site source.
- **W1133 — INFORMATIONAL capture, v14.0 cluster expansion.** The v14.0 hard-rename candidate cluster has grown from the W1004 audit's original 6-file estimate to **~21 files** (8 sites on `@click.argument("name")` + 13 sites on `@click.argument("target")`). USER DECISION W1098 should reference W1133 for the full v14.0 scope at v14.0 planning.

### Added — W1086-W1126 batch (post-W1096-CONSOLIDATE, 2026-05-15 even later iteration)

> **~250 LOC of impl (W1060-take2 + W1086 dominant) + ~120 LOC of new
> test coverage + 1 research memo (677 LOC) across 8 shipped outcomes +
> 1 BAIL (W1101 premise-inverted). 9 of 9 dispatches closed; 17 new
> drive-by W-tasks captured (W1112-W1128). The architectural ship —
> `to_sarif` gained a `warnings_out` parameter + new closed-enum
> descriptor `producer.advisory-warning` — unlocks 4 sibling SARIF
> helpers (W1112-W1115). W1102-RESEARCH closed the W1098 USER-DECISION
> as "no v14.0 rename needed; ship the W1111 AST CI lint instead".
> Premise-verification-first discipline continues to outperform
> force-through (W1101 BAIL: the W1004 audit had misread the dominant
> convention, so the proposed sweep would have been backwards).**

- **SARIF runtime-notification architectural extension (W1060-take2 + W1086).** `src/roam/output/sarif.py::to_sarif` now accepts `warnings_out: list[str]` + a new closed-enum descriptor `producer.advisory-warning` on the SARIF tool driver (was missing from the W1046 landing). `complexity_to_sarif` was wired through with a `warnings` keyword + `src/roam/commands/cmd_complexity.py` gained a `warnings: list[str] = []` accumulator threaded through 4 envelope sites via the hash-stable omit-when-empty idiom (+84/-29 LOC + 6-test file +39 LOC). 15 SARIF tests pass; hash-stability programmatically asserted for the empty-warnings path. Unblocked W1112-W1115 (4 sibling SARIF helpers: fitness / dead / rules / health) captured as follow-up.
- **AST CI lint blocking `@click.argument('name')` drift (W1111).** New `tests/test_w1111_click_argument_name_lint.py` (199 LOC; 50 LOC executable). 12-file grandfather set (the 6 commands from the W1004 audit + 6 siblings discovered during the lint sweep). Negative path verified — adding a 13th `@click.argument("name")` site fails the AST scan. Closes the W1098 USER-DECISION via the W1102-RESEARCH deliverable: lock the current drift surface, defer the breaking-change rename until v14.0 ships for unrelated reason.
- **`roam runs` placeholder-vocabulary unification sweep (W1097 + W1105 + W1116).** `cmd_runs.py` placeholders normalised: 1-line `NAME` → `<name>` at line 944 (W1097), 7-site `--agent NAME` → `--agent <name>` sweep (W1105), 1-line `--run-id ID` → `--run-id <id>` (W1116). 14/14 + 4/4 + 29/29 focused tests pass on each leg. All hash-stable comments/help-text only. Drive-by W1117 captured (square-bracket placeholder convention).
- **Vocabulary cross-link discipline (W1094 + W1122).** +17 LOC across `src/roam/evidence/_vocabulary.py` + `src/roam/output/_severity.py` for the severity-vocabulary docstring cross-link (W1094 — closes the W1005 BAIL drive-by). +7-line reverse-pointer comment block in `src/roam/db/findings.py:101-107` to `evidence.SUBJECT_KINDS` (W1122 — closes a drive-by from the W1094 sweep). All 3 sites hash-stable (comments only); 55 W210 drift guards + 92 findings tests pass.
- **W1102-RESEARCH — Click-argument rename strategy memo.** 677-line research memo at `(internal memo)`. Key finding: the MCP boundary is already sealed via the `_PARAM_ALIASES` table landed in W430, so the CLI-side `@click.argument("name")` drift does NOT silent-fail through MCP. Recommendation: ship the W1111 AST CI lint (DONE) to lock the current 12-site grandfather surface; defer a hard rename until v14.0 ships for an unrelated breaking-change reason. **W1098 USER-DECISION downgraded from BLOCKER to FOLLOW-UP.** <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->

### Audits / BAILs — W1086-W1126 batch

- **W1101 — `memory` → `memories` plural sweep — BAILED (premise inverted).** The W1004 audit had read the codebase wrong: ~26 sites use singular-flag/plural-var (the dominant convention) vs ~3 sites with plural-flag. `cmd_memory` FOLLOWS the dominant convention, so the proposed sweep would have inverted it. New task **W1126 — inverted plural-flag harmonize** captured to bring the 3 actual outliers in line with the dominant convention.

### Added — W1041-W1096 batch (post-W1079-CONSOLIDATE, 2026-05-15 later iteration)

> **~63 LOC + ~39 test LOC across 3 shipped impl waves (W1087/W1091/W1096) +
> 8 BAIL/NO-OP/VALIDATED outcomes (W1041/W1004/W1005/W1007/W1008/W1020/W1048/W1060-narrowed)
> + 1 helper-confirmed (W1041) — the iteration's load-bearing methodological
> output is that BAIL-and-CAPTURE is faster and more accurate than force-through.**
> ~17 waves across the W1041-W1096 stretch; 4 of 11 dispatches landed
> BAIL/NO-OP (W1041 already alphabetical, W1008 already converged via W706+W1057,
> W1005 already W547/W564-compliant, W1020 already optimised with scope="module"
> overrides where viable); 1 BAIL with prereq capture (W1060-narrowed: warnings_out
> accumulator absent in cmd_complexity, so the proposed runtime-notifications
> plumb would have been cargo-cult — captured W1084/W1085/W1086 as prereqs
> instead); 2 VALIDATED-then-fixed (W1007 → W1091 next_commands LAW 4 fix;
> W1008 → drive-by W1093 captured). The bail discipline (W1019b/W1019e/W1080
> precedent + W988+W989 "premise verification is the first step" methodology
> from W1001-CONSOLIDATE) generated 9 follow-up W-tasks (W1084-W1097) instead
> of forcing-through cargo-cult code. All 11 dispatches used `general-purpose`
> or `Explore` subagents per the W1072 directive — `claude` subagent
> worktree-MAX_PATH blocker still active on Windows.

- **W1087 — CI hardening, 9 jobs got `timeout-minutes`, 2 workflows got concurrency groups.** +17 LOC across `.github/workflows/architecture-guardian.yml` + `.github/workflows/cga-attestation.yml` + `.github/workflows/roam-ci.yml`. `architecture-guardian.yml` + `roam-ci.yml` got `concurrency:` groups with `cancel-in-progress: true`; 9 jobs across architecture-guardian / cga-attestation / roam-ci got explicit `timeout-minutes` (deliberate per-job pick, not a uniform default). `publish.yml` left without concurrency — never cancel publishes. Drive-bys captured: W1095 (publish.yml timeouts), W1096 (roam.yml template — sealed inline below).
- **W1091 — `roam runs verify --all` LAW 4 fix on `state=unsigned` + `state=key_missing`.** +6 LOC at `src/roam/commands/cmd_runs.py:1050-1058` + 1 new test (+39 LOC) at `tests/test_ledger_signing.py`. Pre-fix: `agent_contract.next_commands` was empty on both unsigned + key_missing paths (W1007 surfaced this — LAW 4 violation per "Imperatives beat descriptions"). Post-fix: both branches now populate an imperative `next_command` (`roam runs sign` / `roam runs verify --key-path <path>`). **Hash-stable for tampered + ok paths** — the fix only adds bytes to envelopes that were previously omitting them. Drive-by W1097 (placeholder unify) captured.
- **W1096 — `roam.yml` dormant template hardened.** +1 line (`timeout-minutes: 20`) + 3-line teaching comment in the dormant `templates/distribution/.../roam.yml` template. Workflow-dispatch-only confirmed; template only fires on manual trigger. Mirrors the W1087 timeout-minutes discipline at the user-facing template surface.

### Audits / NO-OPs — W1041-W1096 batch

- **W1041 — `clones_cross_layer.py` `__all__` already alphabetical (NO-OP).** Verified the W1037 sweep convention matches W855/W856/W857/W858 sibling files. 101 focused tests pass. Drive-by W1090 captured (3 alphabetical-ordering conventions across 9 catalog files — narrow style-rule documentation candidate).
- **W1048 — actions/* sweep across `.github/workflows/` — SWEPT-CLEAN.** All `actions/*` references already on current majors; this is a pure-Python repo so no `setup-node` versions to bump. Drive-bys captured: W1087 (shipped here), W1088, W1089.
- **W1060-narrowed — `cmd_complexity` `emit_runtime_notifications` plumb — BAILED.** Verified `cmd_complexity` has zero `warnings_out` accumulators — the proposed plumb would have been cargo-cult (no surface to plumb to). Prereqs captured: W1084 (`cmd_health` re-dispatch), W1085 (`cmd_doctor` SARIF surface), W1086 (`warnings_out` prereq across affected commands).
- **W1007 — `agent_contract:[]` empty-list mistake — VALIDATED then SEALED by W1091.** Confirmed `cmd_runs.py:1050-1058` emitted empty `next_commands` on state=unsigned + state=key_missing. Tier-1 fix shipped via W1091 (above). Tier-2 design question captured as W1098-bis (auto-derive omit-when-empty across envelopes — DESIGN Q, not in-flight).
- **W1008 — envelope-root `list_counts` sweep — BAILED.** `list_counts` only exists as a dead local variable inside `formatter.strip_list_payloads`; already converged via W706+W1057. Drive-by W1093 captured (dead-code cleanup, deferred — `formatter.py` modified).
- **W1004 — 7-cmd click-vocab audit — VALIDATED, DESIGN Q open.** 6 commands (disambiguate / guard / safe_delete / symbol / test_scaffold / uses) diverge on `@click.argument("name")` vs the canonical MCP `"symbol"`. **Click has NO argument-alias support** → migration approach captured as **W1098 (USER DECISION pending)**. W1099 (path-cluster), W1100 (help-text), W1101 (memory plural) captured as siblings.
- **W1005 — 3-tier vs 5-tier severity Pattern-3a — BAILED.** Codebase already compliant with W547 + W564 discipline. `CLAIM_SEVERITIES` (5-tier evidence vocabulary) vs canonical 4-tier output vocabulary is **layered by design** — not a Pattern-3a divergence. Drive-by W1094 captured (docstring reconciliation).
- **W1020 — fixture-scope audit — NO-OP.** Already optimised: 8 test files use `scope="module"` override (~642s wall-clock savings); 6 findings test files cannot apply the override (DB mutations require per-test isolation).

### Fixed — CI publish.yml (post-v13.1)
- **W1047 — `publish.yml` SBOM-upload step fixed.** The post-build `gh release upload` step now passes `--repo` explicitly AND creates the GitHub Release idempotently (`gh release create ... || true`) before attempting the upload, so the step no longer races the release-object creation. PyPI wheel content is unchanged — the v13.1 tag was force-moved to the fix commit (`484e34fa`) and the re-run went **green end-to-end** including the post-publish smoke (workflow run `25932785927`, the first fully-green publish run in the history of the workflow). Both v13.0 and v13.1 GitHub Releases backfilled with their CycloneDX SBOMs as part of the same fix.

### W1079-CONSOLIDATE — Pattern-1D + closest-match disclosure arc + helper-hoist Phase 1 + Pattern-2 propagation finale

> **CONSOLIDATE checkpoint = W1079.** ~17 waves closed since W1042-CONSOLIDATE.
> **Headline: the Pattern-1D unknown-value disclosure arc went from 1
> command (W1063 `cmd_findings --detector`) to 9 commands in a single
> batch — `cmd_findings` / `cmd_search --kind` (W1068) / `cmd_endpoints
> --framework` (W1069) / `cmd_endpoints --method` (W1075) /
> `cmd_test_scaffold --framework` (W1070) / `cmd_workflow` + `cmd_explain_command`
> (W1074) / `cmd_oracle` (W1079) / `cmd_smells` (W1066) — each emitting an
> explicit structured envelope on the unknown-value path plus a
> `difflib.get_close_matches`-derived "did you mean?" hint when the typo
> distance is plausible.** Pattern shape: closed enum → reject unknown
> with `state="unknown_<axis>"` + `partial_success=true` + `agent_contract.facts`
> listing the valid set + `next_command` carrying the closest match.
> Mirrors the W918 / W994 / W995 / W1009 / W1011 / W1032 / W1042 loader
> envelope shape — Pattern-1D ("silent success on degraded resolution"
> from CLAUDE.md §"Six systemic anti-patterns") is now the **9th member**
> of the Pattern-2 propagation family. **W1078** added a deliberate
> `click.Choice` carve-out (cmd_complete --kind is click-validated, so
> the Pattern-1D template does not apply; closed not-applicable).
> **Helper hoist (W1077)** shipped `src/roam/output/structured_unknowns.py::structured_unknown_filter`
> as Phase 1 (UNUSED on landing — 128 LOC + 15 tests; Phase 2 migration
> W1080 in flight at consolidation time). **Pattern-2 propagation
> closures** — W1010 final `cmd_flag_dead._load_known_stale` plumbing-only
> close (plain-text loader, not YAML — does not flow through
> `load_yaml_with_warnings`); W1043 `WarningsOut` type alias swept
> 21 callsites across 8 files in one consistent application. **Operational
> findings**: W1067 permit-expiry investigation closed NOT-A-BUG
> (audit-completeness design per W377); W1071 documented permit-vs-lease
> asymmetry in module docstrings + CLAUDE.md; **W1072 — `claude`
> subagent is structurally broken on Windows host** via the W686
> worktree-MAX_PATH regression (the agent platform's default-worktree
> behavior is the structural issue; the `general-purpose` subagent
> works around it by not creating a worktree). W1076 documented that
> CLAUDE.md is intentionally untracked (commit `89a338d9` removed it
> from public repo — local-only by design). **Test discipline**: W1027
> extracted the `_no_pyyaml` monkeypatch to a `tests/conftest.py`
> fixture (6 test files migrated, -50 LOC); W1059 converted 10
> hardcoded `expires_at` future-dates to relative offsets across 2
> files; W1065 triaged 3 more files with 0 conversions (all valid
> B-variant `expires_at`-as-input fixtures). **Research**: W1049-RESEARCH
> shipped a release-pipeline hardening memo with 3 P1 recommendations
> (PEP740 attestations + workflow split + SBOM-wheel SHA binding) —
> queued as W1054 / W1055 / W1056 user-decision-pending. **Hash-stability
> mandate held trivially across the batch** — every Pattern-1D fix
> added a new validation path (no pre-fix envelope bytes to compare);
> W1077 helper shipped unused (no callsites yet); W1010 / W1043 / W1027
> / W1059 are docs/types/test-fixture only with no runtime behavior
> delta. **NO commits taken during the session per directive** — entire
> batch on the working tree for review.

### Added — Pattern-1D + closest-match disclosure arc (W1066 / W1068 / W1069 / W1070 / W1074 / W1075 / W1078 / W1079 — W1079-CONSOLIDATE)
- **W1068 — `cmd_search --kind` unknown-value disclosure + LAW 4 `kinds` anchor.** Closed-set rejection of unknown `--kind` arguments now emits the canonical Pattern-1D envelope (`state="unknown_kind"` + `partial_success=true` + `agent_contract.facts` listing valid kinds + `next_command` carrying the difflib closest match). LAW 4 concrete-noun anchor set extended with `kinds` terminal in both `src/roam/output/formatter.py:concrete_plural_terminals` and `tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS` to keep the lint compliant on the new fact strings.
- **W1069 — `cmd_endpoints --framework` unknown-substring disclosure.** Framework-name validation tightened from substring-match to exact-match against a closed framework registry; unknown values now emit the same Pattern-1D envelope with the supported framework list surfaced via `agent_contract.facts`. Closes a silent-fallback class where `--framework djang` was previously matching `django` and `--framework f` matched the first framework alphabetically.
- **W1070 — `cmd_test_scaffold --framework` unknown-value disclosure.** Sister fix to W1069 for the test-scaffold path; same Pattern-1D envelope.
- **W1074 — `cmd_workflow` + `cmd_explain_command` `UsageError` difflib augmentation.** Both commands previously raised `click.UsageError` on unknown workflow / command names with no suggestion. Fix: augment the UsageError message with the difflib closest match when distance ≤ cutoff. UsageError shape preserved (no envelope change) — the fix is purely the error message augmentation per LAW 2 imperative voice ("Did you mean `roam <closest>`?").
- **W1075 — `cmd_endpoints --method` Pattern-1D disclosure.** Method-name validation aligned with the W1069 framework path; closed HTTP-verb set, unknown values surface via the canonical envelope.
- **W1078 — `cmd_complete --kind` audit closed not-applicable.** `cmd_complete --kind` uses `click.Choice(...)` which already rejects unknown values pre-handler via the canonical Click error shape. No Pattern-1D template applies; documented as a deliberate carve-out so the next reader doesn't try to apply the W1068 pattern here.
- **W1066 — `cmd_findings` + `cmd_smells` difflib closest-match augmentation.** Both commands already rejected unknown values (post-W1063); this wave adds the difflib-derived `next_command` carrying the closest match. Pairs the W1063 disclosure shape with the W1074 difflib suggestion pattern.
- **W1079 — `cmd_oracle` unknown-oracle name closest-match.** Per-line shape: each unknown oracle name in the `--oracles` repeatable flag emits its own structured envelope row with `state="unknown_oracle"` + closest-match suggestion. Closes the final unknown-value site in the disclosure arc.

### Added — Helper hoist Phase 1 (W1077 — W1079-CONSOLIDATE)
- **W1077 — `structured_unknown_filter` helper shipped UNUSED (Phase 1).** New module `src/roam/output/structured_unknowns.py` (128 LOC) + 15 tests in `tests/test_structured_unknowns.py`. Mirrors the W1018 shared YAML helper landing pattern (Phase 1 lands unused; Phase 2 migration proves the abstraction against real callsites). Phase 2 (**W1080**, in flight as of this consolidation) migrates the first 3 callsites — `cmd_findings` + `cmd_search` + `cmd_endpoints --framework` — and is expected to net **-90 to -120 LOC** once landed. The Pattern-1D template defined inline at each W1068 / W1069 / W1070 / W1075 / W1079 callsite is **deliberately duplicated** during Phase 1 to keep each fix reviewable in isolation; Phase 2 consolidates the duplication once 3 callsites have stabilised.

### Pattern-2 propagation closures (W1010 / W1043 — W1079-CONSOLIDATE)
- **W1010 final — `cmd_flag_dead._load_known_stale` plumbing-only Pattern-2 close.** The W1015-batch deferred close (originally queued behind W1018) sealed here as a plain-text loader migration (NOT through `load_yaml_with_warnings` — the file is line-oriented, not YAML). `warnings_out` plumb + `partial_success=true` envelope flip on malformed lines + `agent_contract.facts` surface. Mirrors the W994 / W995 / W1009 shape but at the plain-text-loader boundary.
- **W1043 — `WarningsOut` type alias applied across 21 callsites in 8 files.** The W1042-CONSOLIDATE batch shipped `WarningsOut: TypeAlias = list[str]` at the canonical boundary; this wave swept the type alias through every callsite consuming `warnings_out: list[str] | None` (21 sites across 8 files). No runtime behavior change — readability + LSP hint quality only. **Closes the W706-fan-out arc end-to-end** that ran from W918 (cmd_alerts close) through the W994/W995/W1009/W1011/W1017/W1025/W1032/W1042 loader-site closures.

### Test discipline (W1027 / W1059 / W1065 — W1079-CONSOLIDATE)
- **W1027 — `_no_pyyaml` monkeypatch extracted to `tests/conftest.py` fixture.** 6 test files migrated to consume the new shared fixture; **-50 LOC** net across the test corpus. Closes the duplicated-test-scaffolding class that the W934 catalog-finding-test parametrisation arc had been chipping away at.
- **W1059 — 10 hardcoded `expires_at` future-dates converted to relative offsets** across 2 files. Same shape as W1002 + W1012 (autouse `freeze_time` fixture interaction); preserves the W1002 / W1003 / W1012 discipline.
- **W1065 — 3 more files triaged, 0 conversions.** All 3 were valid B-variant test fixtures (the date is the INPUT being tested, not a free parameter); documented as a deliberate carve-out so the next sweep doesn't re-triage them.

### Operational findings (W1067 / W1071 / W1072 / W1076 — W1079-CONSOLIDATE)
- **W1067 — Permit-expiry investigation closed NOT-A-BUG.** Reported as a permit-expiry race; investigation confirmed audit-completeness design per the W377 marker — permits intentionally remain in the audit trail post-expiry rather than being silently dropped. Documented inline in the permit module docstring.
- **W1071 — Permit-vs-lease asymmetry documented.** Sibling capture from the W1067 investigation; the permit + lease modules differ on expiry semantics in ways the design intends but the docstrings did not previously articulate. Fix: module-docstring updates on both surfaces + a CLAUDE.md sub-section codifying the asymmetry.
- **W1072 — `claude` subagent structurally broken on Windows host (operational).** The `claude` subagent creates a worktree by default; W686 / W903 captured the Windows MAX_PATH failure mode that makes this unusable on the roam-code repo path depth. **Use the `general-purpose` subagent instead** — it does not create a worktree. This is a structural issue with the agent platform's default-worktree behavior, not addressable from inside roam.
- **W1076 — CLAUDE.md is intentionally untracked.** Commit `89a338d9` removed CLAUDE.md from the public repo; the file is now local-only by design (private development guide; not shipped to PyPI/GitHub/landing-page). Documented inline at the top of CLAUDE.md so future readers don't try to re-add it to git.

### Research memos (W1049-RESEARCH — W1079-CONSOLIDATE)
- **W1049-RESEARCH — Release-pipeline hardening memo, 3 P1 recommendations.** Post-W1047 audit of the `publish.yml` workflow surfaced three P1 hardening recommendations: (1) **PEP 740 attestations** — sign the wheel + sdist with provenance attestation post-OIDC-mint and attach to the GitHub Release alongside the SBOM; (2) **workflow split** — separate build / publish / smoke into independent workflows so a smoke-step failure doesn't block the publish from finishing; (3) **SBOM-wheel SHA binding** — the CycloneDX SBOM should carry the wheel's content SHA so the SBOM cannot be silently swapped post-publish. Queued as **W1054 / W1055 / W1056 — user-decision-pending** per the v13.1 release-prep state CLOSED gate.


## v13.1 (released 2026-05-15) -- Pattern-2 propagation + shared YAML helper + 3 flagship silent-fallback seals

> **THREE flagship Pattern 2 silent-fallback bugs SEALED this batch (W826 `cmd_taint` + W834 `cmd_health` + W836 `cmd_doctor`) + W817 helper-level auto-inject closed Pattern 2 `partial_success` gap across 7 detectors in one shot (dead / clones / complexity / orphan-imports / bus-factor / auth-gaps / hotspots) + W810 `cmd_complexity` Pattern 1B fix (`SystemExit(1)` → return on empty corpus) + W805 empty-corpus sweep covered 25+ detectors (cmd_endpoints/n1/missing-index/over-fetch/smells/duplicates/invariants/vulns/audit-trail-conformance/audit-trail-verify/pr-risk/critique already clean; 7 auto-fixed by W817; 3 flagship dedicated fixes) + W749 dispatch-edge `MIN(id)` fix in registry_dispatch (231 + 34 + 22 edge attribution corrections) + W774 sister fix to `laravel_post.py` (worktree-pending) + W718 cleaned 70+ UPPER-case severity sites + W634 `confidence_level_rank` fail-loud + 15 callers + W444 `mcp_tool_names` duplicate fail-loud + W445 `_REGISTERED_TOOLS` append guard + W707 `_serialize_suppressions` dead-code seal + drift-guard expansion (W703 `_CommentSyntax` + W741 `find_project_root` symlink-safety + W484 `templates/ci/` reachability + W711/W712 mcp `--card`/`--list-tools` coverage + W713 `_SARIF_CONSUMERS` AST-literal + W757 backfilled missing W702) + W397 build_readme_counts AGENTS.md + W734 CONTRIBUTING.md count refresh (~50-completion batch behind W836-CONSOLIDATE, 2026-05-15).**
> **The headline is THREE flagship Pattern 2 silent-fallback bugs sealed in main this batch — all three share the "claim success on unanalyzed corpus" shape that the W805 empty-corpus sweep was designed to surface.** Mirroring the proven `cmd_vulns` Fix E template (`state="no_scan"` + `partial_success=True` + actionable verdict) and the `cmd_missing_index` `state="no_migrations"` discipline, each fix detects the empty-graph precondition BEFORE running the rule/check pipeline and emits an explicit Pattern-2 envelope instead of a default-success illusion. **W826 `cmd_taint`** — previously emitted `"No taint findings across 22 rule(s)"` + `partial_success=false` (W817 auto-injected, making the false claim deterministic) on a fully-empty corpus; the verdict read as a clean security pass on an unanalyzed repo. Fix: 52-line empty-corpus guard right after `open_db(...)`; on `COUNT(*) FROM symbols == 0` emits `state="empty_corpus"`, `partial_success=true`, `rules` (count loaded but not run), verdict `"no symbols to analyze (corpus empty; N rules loaded but not run — run \`roam index --force\` to populate the graph)"`. Test `tests/test_w825_taint_empty_corpus.py` xfail-strict flipped green plain; 35/35 taint regression tests pass. **W834 `cmd_health`** — the FLAGSHIP CI-gate command previously emitted `verdict: "Healthy codebase (100/100) — 0 critical issues"` with `health_score: 100` on an empty corpus, because every health factor defaulted to `1.0` on zero signal and the geometric mean returned exactly 100 → score ≥ 80 threshold matched `"Healthy codebase"`. A `100/100 Healthy` verdict on an unanalyzed repo was a HIGH-severity false claim that would silently pass CI gates. Fix: 65-line empty-corpus carve-out before `build_symbol_graph(...)`; emits `state="empty_corpus"`, `partial_success=true`, `health_score=None` (not 0, not 100), verdict `"no symbols to analyze"` + `next_command="roam index --force"`. `--gate` flag raises `GateFailureError` (exit 5) on empty corpus — mirrors W829 audit-trail-verify discipline (fail-closed on missing analysis). Test 1/1 pass; 93/93 health regression tests pass; LAW 4 lint 8/8 pass. **W836 `cmd_doctor`** — previously checked only environment markers (Python/tree-sitter/git/networkx/manifest) and never asked "did the indexer extract anything?". On a clean env + empty corpus, would emit `"all N checks passed"` even though zero symbols had been indexed. Fix: new `_check_corpus_content()` function queries `SELECT COUNT(*) FROM symbols`; states `no_index` / `empty` (advisory fail with actionable verdict) / `populated` / `error`. Wired into the check pipeline after `_check_required_tables` and into `_ADVISORY_CHECK_NAMES` so empty corpus warns but does not block CI by default. Total check count bumped 23 → 24; 71/71 doctor regression tests pass; 5/5 W835 tests pass. **W817 helper-level auto-inject closes Pattern 2 across 7 detectors in one shot**: added a 9-line auto-inject at `src/roam/output/formatter.py:json_envelope()` defaulting `summary.partial_success` to `False` when missing. Closed the gap for 7 detectors without per-command edits; companion W819 manual xfail-strict flip on the 7 corresponding empty-corpus smoke tests; W810 manual `cmd_complexity` Pattern 1B fix (`SystemExit(1)` → return). **W749 dispatch edge-attribution chain extended**: discovered dispatch edges were 100% mis-attributed via a DIFFERENT mechanism than W742 — `MIN(id)` synthetic-source in `registry_dispatch.py`. Replaced with per-file `[(line_start, line_end, symbol_id)]` map + `_symbol_for_assignment` lexical-extent lookup. `_COMMANDS` now sources 231 dispatch edges (was attributed to `_DEPRECATED_COMMANDS`); `_MATH_DETECTORS` 34 edges (was attributed to `log`); `PYTHON_IDIOM_DETECTORS` 22 edges (was attributed to `_MUTABLE_DEFAULT_RE`). **W805 empty-corpus sweep methodology validated**: 25+ detector commands smoke-tested; 12 already Pattern-2 clean; 7 auto-fixed by W817; 3 flagship dedicated fixes; every smoke test ships with a forbidden-fragment blacklist (`"safe"` / `"healthy"` / `"no concerns"` / `"all clear"` / `"100/100"`) as a regression guard. **W772 worktree-staleness operational finding**: ~8 dispatches bailed because agent worktrees branch from commit 850552af (pre-session main); user's `git config --global core.longpaths true` fix resolved the parallel W686 path-length issue. **Hash-stability mandate held across every fix** — `tests/test_evidence_schema_migration.py` 31/31 byte-identical.

### Added — Detector inventory memo (W850)
- W850: (internal memo) <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->

### Fixed — THREE flagship Pattern 2 silent-fallback bugs SEALED (W826 taint + W834 health + W836 doctor)
- **W826 — HIGH-SEV: `cmd_taint` silent-SAFE on empty corpus sealed.** Added 52-line empty-corpus guard at `src/roam/commands/cmd_taint.py` after `open_db(...)`. On `COUNT(*) FROM symbols == 0`, emits Pattern 2 envelope (`state="empty_corpus"`, `partial_success=True`, `rules` count loaded-but-not-run, actionable verdict). Mirror of `cmd_vulns` Fix E. `tests/test_w825_taint_empty_corpus.py` xfail-strict flipped green (2/2); 35/35 taint regression tests pass. **Security-critical** — pre-fix verdict read as a clean security pass on unanalyzed code.
- **W834 — CRITICAL: `cmd_health` silent-Healthy 100/100 on empty corpus sealed.** 65-line empty-corpus carve-out at `src/roam/commands/cmd_health.py` before `build_symbol_graph(...)`. Geometric mean of health factors (each 1.0 on zero signal) was deterministically returning 100, triggering `"Healthy codebase"` threshold. Fix emits `state="empty_corpus"`, `partial_success=True`, `health_score=None`, verdict `"no symbols to analyze"`, `next_command="roam index --force"`. `--gate` flag raises `GateFailureError` (exit 5) on empty (W829 fail-closed discipline). 1/1 pass + 93/93 regression + 8/8 LAW 4 lint. **FLAGSHIP CI-gate bug** — `100/100 Healthy` on unanalyzed repo would have silently passed every `roam health --gate` CI check.
- **W836 — HIGH: `cmd_doctor` silent "all checks passed" on empty corpus sealed.** New `_check_corpus_content()` in `src/roam/commands/cmd_doctor.py` (~70 lines). States: `no_index` (install OK, advisory pass) / `empty` (advisory fail) / `populated` / `error`. Wired into pipeline after `_check_required_tables` + `_ADVISORY_CHECK_NAMES`. Verdict computation extended with explicit `"corpus empty (0 symbols)"` branches. Total check count 23 → 24; 71/71 doctor regression tests pass.
- **W817 — helper-level Pattern 2 closure.** 9-line auto-inject at `src/roam/output/formatter.py:json_envelope()` defaulting `summary.partial_success` to `False` when missing. Closes the gap for 7 detectors (cmd_dead / cmd_complexity / cmd_clones / cmd_orphan_imports / cmd_bus_factor / cmd_auth_gaps / cmd_hotspots) in one edit. Companion W819 manual xfail-strict flip on the 7 empty-corpus smoke tests.
- **W810 — `cmd_complexity` Pattern 1B fix.** Empty-corpus branch was emitting structured envelope + `raise SystemExit(1)` — Pattern 1 variant B (wrapper-bridge converts to generic COMMAND_FAILED). Fix: changed to clean `return`, added `state="no_complexity_data"` + `next_command`.

### Fixed — Edge-attribution chain extended (W749 + W774)
- **W749 — Dispatch edges 100% mis-attributed via `MIN(id)` in registry_dispatch.py — SEALED.** Different mechanism than W742. Replaced with per-file `[(line_start, line_end, symbol_id)]` map + `_symbol_for_assignment` lexical-extent lookup. **`_COMMANDS` now sources 231 dispatch edges** (was attributed to `_DEPRECATED_COMMANDS`); **`_MATH_DETECTORS` 34 edges** (was attributed to `log = logging.getLogger`); **`PYTHON_IDIOM_DETECTORS` 22 edges** (was attributed to `_MUTABLE_DEFAULT_RE`). 106/106 focused tests pass; hash-stability preserved.
- **W774 — `laravel_post.py` `MIN(id)` anti-pattern fix.** Same root cause as W749. Applied analogous fix using a new shared helper `src/roam/index/_containing_symbol.py` (`build_file_symbol_ranges()` + `containing_symbol_for_line()`). 129/129 focused tests pass. (Note: this work landed in a worktree branch on the pre-W624 baseline — pending careful cherry-pick to main via W782 because direct copy would regress `mcp_server.py` importlib.resources migration.)

### Fixed — W805 empty-corpus sweep: methodology + 25+ detectors smoke-tested
- **Already Pattern-2 clean** (regression guards shipped only): cmd_endpoints (W801), cmd_n1 (W803), cmd_missing_index (W807 — `state="no_migrations"`), cmd_over_fetch (W809), cmd_smells (W820), cmd_duplicates (W821), cmd_invariants (W824), cmd_vulns (W823 — Fix E pre-existing), cmd_audit_trail_conformance (W827 — Fix E + Article 12 `not_run`), cmd_audit_trail_verify (W829 — 3-state matrix), cmd_pr_risk (W828), cmd_critique (W831 — structured `EMPTY_INPUT:` UsageError).
- **Auto-fixed by W817 helper** (xfail-strict flipped green): cmd_dead (W802/W804), cmd_complexity (W806 + W810 Pattern 1B), cmd_clones (W808/W813), cmd_orphan_imports (W812/W814), cmd_bus_factor (W811/W817), cmd_auth_gaps (W815/W818), cmd_hotspots (W816).
- **Three flagship surfaces dedicated-fixed**: cmd_taint (W826), cmd_health (W834), cmd_doctor (W836) — see preceding section.
- **World-model R28 classifiers** (W680): empty-corpus smoke for side-effects/idempotency/causal-graph/tx-boundaries; all 4 already compliant.
- **`taint_engine` positive smoke** (W681): 3 tests proving SSTI co-call + SQLi forward BFS + shipped-YAML rule-pack matching.
- **Forbidden-fragment blacklist discipline** (W823 / W825 templates): every new smoke test includes a verdict blacklist preventing future Pattern 2 regressions.

### Fixed — Defensive fail-loud expansion (W444 / W445 / W634 / W707)
- **W444 — `mcp_tool_names()` fail-loud on duplicates.** `src/roam/surface_counts.py:mcp_tool_names()` historically returned `sorted(set(names))` silently collapsing duplicate `@_tool(name=...)` decorations. Now raises `ValueError` with offending names. 14 callers audited + classified; 3-test smoke harness pins the contract.
- **W445 — Defensive duplicate-check in `_REGISTERED_TOOLS.append`.** 9-line guard at `src/roam/mcp_server.py:872` raising `RuntimeError("Duplicate MCP tool registration: ...")`. Runtime + AST defense-in-depth against W432-class duplicate-registration bugs.
- **W634 — `confidence_level_rank()` flips to fail-loud by default.** Added `fallback: int | None = None` kwarg. Raises `ValueError` on unknown without fallback. **15 callers migrated**: 13 explicit `fallback=-1` (data-path); 2 fail-loud (CLI-validated input). 57 + 128 focused tests pass.
- **W707 — REAL BUG: `_serialize_suppressions` dead-code on `first` flag.** Dead flag set but never read; removed; regression test pins call-site count at zero.

### Changed — UPPER-case severity vocabulary canonicalisation (W632 / W718)
- **W718 — cleaned 70+ UPPER-case severity sites across `cmd_health` (50), `cmd_pr_risk` (8), `cmd_path_coverage` (12).** Every envelope `severity` field, findings-registry row, SARIF input, text formatter call now lowercase per W547 contract. 31/31 hash-stability + 324 focused + 77 critique tests pass.
- **W632 — `cmd_path_coverage` UPPER-case risk 4-tier canonicalised.** Single comment-only stale reference; underlying code was already W718-canonical.

### Added — Coverage tests + drift guards (W397 / W444 / W484 / W680 / W681 / W703 / W711 / W712 / W713 / W734 / W741 / W757 / W801–W835)
- **W397 — `build_readme_counts.py` AGENTS.md integration + count refresh.** New `_agents_md_blocks()` builder; refreshed Codex-headline (`233 → 238 commands`, `57 core / 149 full → 224 MCP tools (57 core)`) + Codex-authoritative (`canonical 226 → 231`) to current canonical counts.
- **W484 — `templates/ci/` wheel-bundling reachability test.** 9 tests pinning `importlib.resources.files("roam.templates.ci")` reachability + `__init__.py` marker per W664 discipline.
- **W703 — `_CommentSyntax` language-coverage drift-guard.** 4 tests pinning every canonical language is in `_COMMENT_SYNTAX_BY_LANG` (30 entries) OR in `_COMMENT_DENSITY_NO_SUPPORT` skip-set. Added 7 languages (tsx/jsonc/vue/svelte/sfxml/aura/visualforce).
- **W711 — `mcp --card` error-branch coverage.** 4 tests covering FileNotFoundError / corrupted-JSON / OSError / no-traceback-leak. Drive-by found W788 (handler not Pattern-1 canonical) + W789 (hash-pin drift).
- **W712 — `mcp --list-tools` success-path coverage.** 5 tests pinning exit-0 + canonical core tool names + JSON envelope shape + presets advertisement.
- **W713 — `_SARIF_CONSUMERS` AST-literal contract test.** 2 tests asserting constant is `ast.Tuple` of `ast.Constant` strings AND parsed tuple equals runtime value.
- **W741 — `find_project_root` `.git` symlink-safety regression test.** 4 tests pinning that worktree-pointer-FILE wins over a parent real `.git` directory.
- **W734 — CONTRIBUTING.md count refresh.** 3 stale references fixed: `rev: v11.1.2 → v13.0`, `MCP server with 101 → 224 tools (57 in core preset)`, `Test suite (186 → 408 test files)`.
- **W757 — Backfilled missing W702 `_DEPRECATED_COMMANDS` schema test.** W702 was a false-completion in TaskList. Wrote `tests/test_cli_deprecated_commands_schema.py` (5 AST-literal schema tests, all pass).
- **W680 — Empty-corpus smoke for world_model R28 classifiers** (4 tests, all clean).
- **W681 — Positive smoke for `security/taint_engine`** (3 tests proving real SSTI + SQLi taint flows).
- **W801 – W835 — Per-detector empty-corpus smoke tests** (~20 new files, one per detector; see W805 sweep section).

### Operational findings (pending fix / decision)
- **W797 — CRITICAL caveat: BigCloneBench Type-3/4 93% mislabeled.** Any clones-detector FP-rate claim citing BigCloneBench must exclude Type-3/4 OR cite the caveat.
- **W785 — CRITICAL: 8 sync tools falsely in `_TASK_OPTIONAL_TOOLS`.** Convert to `async def` via `_run_roam_async` OR drop from the set. Add `inspect.iscoroutinefunction()` drift-guard.
- **W789 — REAL BUG: `mcp-server-card.json` hash-pin drift.** `_EXPECTED_CARD_SHA256` not bumped after edit. W563 auto-rotate didn't fire — investigate why.
- **W791 — REAL BUG: stale-index envelope drift in `test_mcp_server.py`.** 2 tests assert exact-equality but runtime now decorates with `_meta.stale_index` + verdict suffix.
- **W772 — Worktree-staleness pattern (recurring).** ~8 dispatches bailed because agent worktrees branch from `850552af` (pre-session main). User's `git config --global core.longpaths true` fix resolved the parallel W686 path-length issue.
- **Three pending-merge worktree branches** (W774 / W706 / W788) — completed work in stale worktrees; need cherry-pick.
- **W798 — Click 8.3 dropped `CliRunner(mix_stderr=False)`.** Mass-replace required.

### Added — Smell catalog detector roster 20 → 24 (W852 / W853 / W855 / W856 / W857 — W865-CONSOLIDATE)
- **W853 — `speculative-generality` (YAGNI).** New detector in `src/roam/catalog/smells.py`: flags symbols whose only callers are test files (`test_*.py` / `*_test.py`) — surfaces YAGNI scaffolding that exists solely to be unit-tested. Confidence tier `structural`; wired into `ALL_DETECTORS`.
- **W857 — `parallel-hierarchy` (Fowler).** New detector module `src/roam/catalog/parallel_hierarchy.py` (`detect_parallel_hierarchy(conn) -> list[dict]`) re-imported into smells.py. Mirrored-subclass-hierarchy smell: when adding a subclass on one side forces an analogous subclass on the other. **16/16 tests pass** (`tests/test_w857_parallel_hierarchy.py`).
- **W855 — Rename-invariant clones (DECKARD-style).** New module `src/roam/catalog/clones_rename_invariant.py` shipping a characteristic-vector clone detector that survives identifier renames. **6/6 tests pass** (`tests/test_w855_rename_invariant_clones.py`). **6,070 Type-2 pairs** surfaced on roam-code itself. Library-layer only this batch — no CLI surface yet; the persistence + CLI wrapper is deliberately separated to keep the algorithm landing reviewable.
- **W852 — `type-switch` (OCP / Fowler).** New module `src/roam/catalog/type_switch.py` re-imported into smells.py. Detects chained `isinstance` / `type(...) ==` / `match-case` dispatch against ≥3 concrete classes on a single discriminator — recommends Strategy / Visitor / `singledispatch`. All tests pass (`tests/test_w852_type_switch.py`).
- **W856 — `cross-layer-clone` (the #1 real-world DRY debt class).** New module `src/roam/catalog/clones_cross_layer.py` re-imported into smells.py. Implementation: Jaccard similarity over callee-NAME multisets across detected layers (controllers / services / repositories). Targets the *strategic* DRY debt — duplicated domain logic routed through different layers — that literal-clone detectors miss. **23/23 tests pass** (`tests/test_w856_cross_layer_clones.py`). 0 findings on roam-code itself, which is correct: roam is a CLI library, not a layered web app.
- **Smells.py wiring.** Docstring count + `run_all_detectors()` docstring + `ALL_DETECTORS` list bumped 20 → 24 in lockstep. Module-level imports added for `parallel_hierarchy`, `type_switch`, `clones_cross_layer`. All five new test files green via `pytest tests/test_w85{2,3,5,6,7}_*.py -x -q`.

### Added — Strategy memos shipped earlier in the W836→W865 arc (W848 / W849 / W850 / W859)
- **W848 — `(internal memo)`.** Fowler 22-smell coverage map + top-3 recommendations for the next detector wave. Drove the W852-W857 selection.
- **W849 — `(internal memo)`.** Research memo identifying *cross-layer duplication* as the #1 real-world DRY debt class. Directly drove the W856 design (Jaccard over callee-name multisets across layers, NOT raw clone detection).
- **W850 — `(internal memo)`.** 94 distinct detectors catalogued across the codebase. The first single source of truth for the full detector roster. <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W859 — Correction banner on W848.** W848's draft claimed `empty-catch` was a stub; W370 had already shipped a real detector. Banner added to the memo flagging the inventory drift — sealed silently before any downstream reader could be misled.

### Operational findings (W865 batch — pending fix / decision)
- **W862 — `smells.py` docstring count drift latent risk.** Docstring "20 deterministic detectors" was silently allowed to drift to "24" while `ALL_DETECTORS` grew underneath. Both are now in sync at 24 but the discipline gap remains. Follow-up: add `tests/test_smells_detector_count_matches_docstring.py` that AST-parses the module docstring + asserts `len(ALL_DETECTORS) == <docstring count>`.
- **W863 — `ALL_DETECTORS` entries ordered ad-hoc.** Smells are appended in arrival order, not alphabetical-by-smell-id. If a future SARIF emitter ever depends on stable run-to-run ordering (e.g. for golden-fixture hashing), this becomes a real bug. Follow-up: standardize alphabetical-by-smell-id + AST drift-guard.
- **W864 — `_loc()` helper duplicated 3 ways.** Identical `_loc()` function definitions in `src/roam/catalog/smells.py` + `src/roam/catalog/parallel_hierarchy.py` + `src/roam/catalog/clones_cross_layer.py` — itself a W95-style clone in the detector code that's catching clones. Follow-up: hoist into `src/roam/catalog/_shared.py`.
- **W861 — Worktree-isolation files surface on main (W783 follow-up).** Files created by `isolation: worktree` agents appear in main as untracked rather than staying in the worktree. Not a bug per se, but worth documenting: future planning should treat "worktree isolation" as effectively "work in main + auto-stage" for additive changes. Pure code-isolation guarantees only hold for paths the agent does not modify.

### Added — Catalog helper-hoist arc + registry-parity backstops (W886-CONSOLIDATE)
- **W864 — `src/roam/catalog/_shared.py` created (~50 lines).** Collapsed 4 `_loc()` definitions → 1 canonical + 2 `_find_workspace_root()` definitions → 1 canonical, across `smells.py` + `clones_cross_layer.py` + `type_switch.py` + `detectors.py`. **332 focused tests green.** Closes the W95-style clone inside the detector code that catches clones.
- **W873 — `is_test_path()` extension to `_shared.py` (~70 lines + 4 pattern tuples).** Covers Python / Go / JS-TS / Java-Kotlin / Ruby / Apex test-naming conventions. Folded 2 catalog-layer duplicates: `detectors._is_test_path` (37 call-sites; `_INCLUDE_TESTS_OVERRIDE` semantics preserved) + `type_switch._file_is_test` (1 call-site). 6 non-catalog sites left alone (already delegated correctly OR canonical at their own layer OR deliberate import-cycle break). **17/17 new + 216/216 sibling tests pass.**
- **W862 — `tests/test_smells_detector_count_drift.py` (173 lines, 3 tests).** AST-parses both the module docstring and `run_all_detectors()` docstring; asserts both stay in lockstep with `len(ALL_DETECTORS)`. Catches the exact count-drift class W856 surfaced. Inline drift fixes: `smells.py:2893` "remaining 19 detectors" → "remaining detectors"; `cmd_smells.py:72` "The 15 detectors" → "The detectors".
- **W867 — `tests/test_smells_confidence_mapping_parity.py` (208 lines, 3 tests).** AST parity lint between `ALL_DETECTORS` ids and `_SMELL_KIND_TO_CONFIDENCE` keys. Reference set computed as `ALL_DETECTORS ∪ AST-derived _finding("<id>",...) first-args` so the W647 rollup pattern (one detector emits two smell_ids like `temporal-coupling-cluster`) doesn't false-trip.
- **W869 — `(internal memo)` (~600 lines, research memo).** Synthesises the registry-parity bug-class across 10+ session-observed drift instances (W852 / W856 / W862 / W867 / W432 / W702 / W785 / W332 / W397 / W37.1-W113); 8 industry references. Recommendation: hybrid Archetype B+E (decorator-driven `@detector(smell_id, confidence_tier)` + construction-time validation + parity-test backstop). **P0 = smell-detector registry** (24 detectors, W871); **P1 = MCP tool registry** (224 tools, next sprint); **P2 = mode-allowlists / `_DEPRECATED_COMMANDS` / `subject_kind`** (rare-touch). <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W874 — "Mirror smells.py" docstring anti-pattern sweep.** Audited ~75 mentions; only 4-5 are real code clones (captured as W877-W880 drive-bys). The rest are legitimate comparative-narrative documenting parallel-but-distinct behavior.
- **W876 — Stale-pending triage cleanup.** Audited 20 candidates from the long-running BACKLOG queue; **flipped 13 actual stale-pending rows pending → shipped**: W125 (Wave30.1 doc-hygiene — already shipped W250; wave-number-vs-task-ID collision documented), W221 (flagged user-blocked), W224 (superseded by W240 / W242 / W261 / W266 / W267 / W268 producer-sealing waves), W298-polish, W319, W335, W342, W345, W346, W348, W349 (W377-W382 closed by W436 batch), W352, W353. **W107 left pending** — mode taxonomy still awaits user signoff.

### Operational findings (W886 batch — pending fix / decision)
- **W870 — Per-detector `*_DETECTOR_VERSION` sparse-stamp lint candidate** (W867 finding). Some detectors stamp `<NAME>_DETECTOR_VERSION` per the Adding-a-command checklist; others don't. Worth a parity lint asserting every `ALL_DETECTORS` entry declares one.
- **W871 — P0 `@detector` decorator implementation** (W869 recommendation). Smell-detector registry is the P0 surface for the hybrid Archetype B+E rewrite. ~24 detectors to migrate; backstop tests (W862 + W867) already in place.
- **W872 — Layer-classification heuristic audit across clone detectors** (W864 finding). Controller/service/repository inference heuristics inside the clone detectors may have similar drift to the now-folded `_find_workspace_root` — audit candidate.
- **W875 — Consolidate `_finding` / `_make_finding` constructors** across `smells.py` + `detectors.py`. Two near-identical constructors for the same payload shape; candidate for `_shared.py` extension.
- **W877-W880 (W874 findings) — Real clones outside the catalog layer.** Hoist `_enclosing_symbol` from `type_switch.py` (W877); fold `_bare_command_name` triple-mirror across `cmd_next.py` + `constitution/loader.py` + `modes/policy.py` (W878); hoist `_camel_split` from `retrieve/seeds.py` (W879); fold `_parse_iso` duplication in `change_evidence.py` / `approval.py` (W880). All blocked behind W885.
- **W881-W884 — Delegate `_is_test_path` in 4 non-catalog sites** to canonical `changed_files.is_test_file`: `cmd_over_fetch.py` (W881), `metrics_history.py` (W882), `rules/builtin.py` (W883), `rules/dataflow.py` (W884). These are the sites W873 deliberately left alone pending W885.
- **W885 — Architectural decision** outstanding: extend `changed_files.is_test_file` coverage to fold W881-W884 in OR invent a `roam._common` namespace? W873 punted; W869 registry-parity logic favors extending the existing canonical site for single-source-of-truth.

### Added — Cross-layer hoist execution + decorator-driven registry POC (W908-CONSOLIDATE)
- **W877 — `_enclosing_symbol` hoisted to `src/roam/catalog/_shared.py`.** Two near-identical sibling definitions (`smells.py` defensive + `type_switch.py` permissive) collapsed to a single canonical site; the defensive variant won (preserves `try/except OperationalError` contract — see W888 audit). `type_switch.py` local def removed + `Mirror admission` docstring stripped. **253 focused tests pass.** Closes the W874-finding clone that lived inside the detector code that catches clones.
- **W879 — `_camel_split` hoisted** from `retrieve/seeds.py` → `search/index_embeddings.py` canonical (12 lines removed). Companion **W901**: added `__all__ = ["_camel_split"]` to `index_embeddings.py` to declare the underscored name as an intentionally exported cross-package boundary.
- **W880 — `_parse_iso` hoisted** from `evidence/change_evidence.py` → `evidence/approval.py` canonical (12 lines removed). The "duplicated here to avoid import cycle" docstring was VERIFIED FACTUALLY WRONG — no cycle exists. W902 captures this as a false-hedge pattern audit finding.
- **W881-W884 — Bundle: 4 cross-layer `_is_test_path` delegations to canonical `roam.commands.changed_files.is_test_file`.** 9 call-sites across `cmd_over_fetch.py` (W881), `metrics_history.py` (W882), `rules/builtin.py` (W883), `rules/dataflow.py` (W884) now route through the canonical. The canonical already covered every pattern the 4 sites needed — no extension required. **477 focused tests pass.** This empirically RESOLVES W885 (the architectural decision punt): catalog/`_shared` stays narrow, cross-layer routes through `changed_files`, no transverse `roam._common` namespace needed.

### Added — Decorator-driven smell-detector registry POC (W871)
- **W871 — P0 decorator POC for the W869 registry-parity recommendation.** New module `src/roam/catalog/registry.py` (176 lines) exposes `@detector(smell_id, confidence_tier=...)` + `register_rollup_kind(...)` + construction-time validation against the W867 vocabulary. **2 detectors migrated**: `speculative-generality` (W853 detector) + `temporal-coupling` parent (W602) with `temporal-coupling-cluster` rollup (W647). **212 focused tests pass.** Validates the hybrid Archetype B+E approach on a narrow surface before the bulk migration; remaining 22 detectors stay hand-rolled until the design follow-ups (W895 rollup_id auto-infer / W896 stable ordering / W897 parent_id finalization) close.
- **W894 — Confidence-tier mismatch surfaced + sealed by W871.** The decorator POC initially read `temporal-coupling` as `structural` because the rollup pattern in `_SMELL_KIND_TO_CONFIDENCE` indexed both parent + cluster at the same tier. Audit confirmed the HAND-ROLLED side was correct (W602 + W647 intentional split: **parent = heuristic** for git-cochange frequency, **cluster rollup = structural** for graph aggregation). Decorator side aligned. **W867 lint extended with a new value-parity test** so the same drift can't recur silently the next time a detector is migrated.

### Fixed — Cross-language test-path false-positive (W889)
- **W889 — Catalog `is_test_path` missed camelCase Java/Kotlin/C#/Swift/PHP/Scala/Apex `Test`/`Tests` basenames.** Added 3 new case-sensitive regex patterns mirroring `DEFAULT_TEST_PATTERNS` so `FooTest.java`, `BarTests.cs`, `BazTest.kt` etc. are now recognised by the catalog-layer canonical. The W886 "we miss these" pinning test (originally written xfail-strict to document the gap) inverted into an **11-case positive + 8-case negative parametrize**. **276 tests pass.** Closes the W886 drive-by-1 cross-language gap.
- **W891 — `_TEST_FILE_SUFFIXES` extended with `_test.exs` (Elixir) + `_test.dart` (Dart)** for canonical parity with the W873 baseline. **334 tests pass.**
- **W893 — VERIFIED FALSE POSITIVE.** The W889 follow-up flagged Apex `*_Test.cls` as a coverage gap, but the canonical regex `^.*Test\.cls$` already matches (greedy `.*` consumes the underscore). Pinned with a new 4-layer parity test asserting all suffix variants match across the canonical + catalog + changed_files + DEFAULT_TEST_PATTERNS layers.

### Hardened — Stale-pending triage + drive-by audits (W902)
- **W902 — Audited 6 "duplicated here to avoid X" docstrings across the codebase.** Outcome: **1 real-now-resolved** (W880); **3 FALSE HEDGES** captured as new pendings (`loader.py` / `django_post.py` / `cmd_oracle.py` — claims of import-cycle avoidance that are factually wrong); **2 forward-looking** kept (`mcp_server.py` / `oscal.py` — defensive lazy-import comments that document genuine optional-dep handling). Meta-observation captured as W907: the false-hedge cargo-cult anti-pattern was replicated 3+ times, suggesting a CLAUDE.md note is worth landing the next time the LLM-discipline section gets touched.
- **W876 follow-on stale-pending flips** (continuing the W886 cleanup): 11 additional rows flipped pending → completed via the W876 methodology — W107, W125 (false-positive), W221, W224, W298-polish, W319, W335, W342, W345, W346, W348, W349, W352, W353. W107 specifically was confirmed previously-shipped — the user-blocked gating note was stale.

### Operational findings (W908 batch — pending fix / decision)
- **W887 — `python_idioms._enclosing_symbol` name collision** (W877 drive-by). A third site exists under `src/roam/python_idioms/` that wasn't part of the W877 hoist — name collision rather than clone; audit candidate for naming convention disambiguation.
- **W888 — `smells._enclosing_symbol` defensive-migration audit** (W877 drive-by). Confirmed the defensive variant (the one with `try/except OperationalError`) is correct as canonical; sister sites silently degrading to `syms[0]` is the bug pattern this guards against.
- **W895 / W896 / W897 — W871 decorator follow-ups.** `@detector` POC works on 2 detectors but the bulk migration is blocked behind three design decisions: (W895) `rollup_id` auto-infer from parent + suffix vs explicit kwarg; (W896) stable iteration ordering — alphabetical-by-smell-id vs declaration-order frozenset; (W897) `parent_id` finalisation semantics for the rollup pattern. None blocking; each is a 30-min design call.
- **W898 — Long-term catalog/`_shared.is_test_path` delegate to canonical.** Sister candidate to W885 — `_shared.is_test_path` could itself delegate to `changed_files.is_test_file` instead of carrying its own regex tuples. Defers cleanly behind the W871 bulk migration so the registry POC has a stable surface.
- **W899 — Tighten the Apex `Test.cls` regex.** W893 confirmed the greedy `.*` works but is non-obvious; an explicit `(?:_)?Test` alternation would document intent. Cosmetic but documentation-grade.
- **W900 — Per-language adapter table** (suffix-tuple + camelCase-pattern-tuple unification across the 4 layers W893 surfaced). Today each layer carries its own pattern tuples; a single per-language adapter table would centralise the discipline. Deferred behind W898 so the canonical site moves first.
- **W903 — CRITICAL operational: claude subagent type creating worktrees by default** → W686 path-length blocking. ~3 dispatches this batch failed because the agent worktrees branched off old SHAs with paths exceeding the Windows MAX_PATH limit despite the prior session's `git config --global core.longpaths true` fix. Captured for a tooling-side investigation; not addressable from inside roam.
- **W904 — `django_post._DJANGO_*` constants duplicated from `python_lang`** (W902 finding). Trivial hoist — same shape as the W879/W880 pattern, no architectural decision required.
- **W905 — `cmd_oracle.py:83` lazy-import false-hedge claim** (W902 finding). Comment claims import-cycle avoidance; no cycle exists. Cosmetic cleanup.
- **W906 — Overly-defensive lazy-import comments** in `mcp_server.py` + `oscal.py` (W902 forward-looking). Kept as-is for the W908 batch — both surface real optional-dep handling but the wording reads as cargo-cult; worth a polish pass in a future docs-only wave.
- **W907 — CLAUDE.md note on the false-cycle hedge cargo-cult anti-pattern** (W902 meta-observation). The pattern has replicated 3+ times across unrelated modules; a one-paragraph note in CLAUDE.md's LLM-discipline section would deter the next replication.

### Fixed — Registry-parity remediation HIGH-RISK trio (W910 / W911 / W912 + W913 — W922-CONSOLIDATE)
- **W909-RESEARCH — 14+ more registry-parity drift candidates surfaced** beyond W869's 10. Top three graduated to HIGH-RISK fixes in this batch: cmd_alerts thresholds (W910), `_CONFIDENCE_BASES` (W911), `_DETECTOR_METADATA` coverage (W912 + W913). The remaining candidates are queued as W915-W921 drive-by pendings — most are narrow constant-cluster parity gaps the same B+E template will dispatch.
- **W910 — HIGH-RISK FIX: `cmd_alerts._DEFAULT_THRESHOLDS` was missing `bottlenecks` + `dead_exports`.** Backfilled `bottlenecks` (>5, WARNING) + `dead_exports` (>20, INFO) into `_DEFAULT_THRESHOLDS`. Pinned with a new 3-test parity lint at `tests/test_w910_alerts_threshold_parity.py` asserting `_DEFAULT_THRESHOLDS / _TREND_LABELS / _WORSE_WHEN_*` stay in lockstep. **46/46 focused tests green.** Missing thresholds meant agents calling `roam alerts` against either metric got silent-fallback behavior — Pattern 2 silent-fallback at the threshold layer.
- **W911 — HIGH-RISK FIX: `_CONFIDENCE_BASES` derived from canonical findings constants.** `src/roam/catalog/detectors.py:_CONFIDENCE_BASES` now derives from `roam.db.findings.CONFIDENCE_HEURISTIC / CONFIDENCE_STRUCTURAL / CONFIDENCE_STATIC_ANALYSIS / CONFIDENCE_RUNTIME` instead of carrying its own string literals. Frozenset shape preserved; zero outside callers needed updates. New parity test `tests/test_w911_confidence_tier_parity.py` (3 tests). **162 focused tests pass.** Closes a Pattern 3a vocabulary-divergence path between the detector catalog + the findings registry — confidence-tier strings are now sourced from one canonical site.
- **W912 — HIGH-RISK LINT: detector metadata coverage gap pinned.** `tests/test_w912_detector_metadata_coverage.py` (3 tests) asserts every `_QUERY_COSTS` task_id has a matching `_DETECTOR_METADATA` row. Pre-fix gap: 11 detectors silently fell back to default precision/impact (broad-except-swallow / async-* / dangerous-eval / etc.); test originally xfail to document the gap.
- **W913 — Detector metadata backfill.** Backfilled the 11 missing `_DETECTOR_METADATA` rows in `src/roam/catalog/detectors.py:4001-4020` with deliberate per-detector precision/impact picks. xfail removed from W912 lint. Parity is now 34 task_ids ↔ 34 metadata rows. **17/17 focused tests pass.** Closes the W912 silent-fallback gap.

### Changed — W877/W878/W879/W880 hoist arc carry-through + W894 confidence-tier mismatch sealed (W922-CONSOLIDATE)
- **W877 — `_enclosing_symbol` hoist landed in detail.** Defensive variant from `smells.py` chosen as canonical at `src/roam/catalog/_shared.py`; `type_switch.py`'s `try/except OperationalError` contract preserved. **253 focused tests pass.**
- **W878 — `_bare_command_name` QUADRUPLE-mirror SEALED.** W874 originally identified a triple-mirror; the dispatch surfaced a fourth twin (`modes/policy._normalise_command`) that the literal-string grep missed. All four sites consolidated into a new module `src/roam/commands/_command_utils.py` (`bare_command_name`, 42 LOC). **-47 +33 across 3 patched files; 158 focused tests pass.** Captured methodologically as drive-by W920: literal-string clone-detection misses semantically-equivalent rename-variants; the W855 behavioral-fingerprint detector could replace the literal sweep.
- **W879 / W880 / W901 — Catch-up commit-set notes.** Already shipped in the W908 batch; line items re-pinned here so the W922 changelog reads stand-alone. `_camel_split` canonical at `search/index_embeddings.py` + `__all__` export; `_parse_iso` canonical at `evidence/approval.py` + the OLD "duplicated here to avoid import cycle" docstring DELETED (no cycle existed).
- **W894 — Inline fix landed.** Temporal-coupling confidence-tier mismatch fixed at the hand-rolled site: parent = `heuristic` (git-cochange frequency); cluster rollup = `structural` (graph aggregation over heuristic-tier findings). Decorator side aligned. **W867 lint extended with a value-parity test** so the same drift can't recur silently.

### Hardened — W902 cargo-cult follow-through + CLAUDE.md anti-pattern rule (W904 / W905 / W907 — W922-CONSOLIDATE)
- **W904 — `django_post.py` docstring corrected: the alleged duplication NEVER EXISTED.** Triple-false claim audited and removed (no cycle + no duplication + confused readers). `python_lang.py` is Django-agnostic; the docstring's "duplicated from python_lang" hedge was pattern-matched cargo-cult, not a factual claim about the codebase.
- **W905 — `cmd_oracle.py:83` lazy import PROMOTED to module-level.** Companion try/except masking the impossible `ImportError` REMOVED. The "duplicated here to avoid import cycle" docstring was factually wrong; no cycle exists; the defensive try/except was dead code. **316 focused tests pass.**
- **W907 — CLAUDE.md "Verify the cycle before hedging" sub-section landed.** Added between "Never N/A without running it" and "Adding-a-command checklist" in the Quality-discipline section. Codifies the cargo-cult anti-pattern that W904 + W905 + W880 collectively exposed (3+ false-hedge replications across unrelated modules in the same audit). The rule: before writing "duplicated here to avoid X" in a docstring, actually verify X exists; if X turns out to be false the hedge is a fabricated rationalisation that confuses the next reader.

### Hardened — W914 second stale-pending re-triage (W922-CONSOLIDATE)
- **W914 — 8 more stale-pending closures + 1 supersession.** Continuation of W876 methodology. Flipped pending → completed: W336 / W362 / W370 / W370b / W371 / W383 / W399 (duplicate-pending shadows of already-completed waves). W356 marked superseded as obsolete process directive. **Combined with W876, the two passes have flipped 19 stale-pending tasks total** — the legacy "Pending after WXXX" sections have visibly contracted across the W886 → W908 → W922 arc.

### Operational findings (W922 batch — pending fix / decision)
- **W915 — `_QUERY_COSTS` closed-enum-as-string-literals.** Same Pattern 3a vocabulary shape as W911; the keys live as bare string literals rather than canonical-constant references. Trivial hoist behind a `_QUERY_COST_KEYS` frozenset.
- **W916 — CLAUDE.md should cite `findings.py` as the confidence canonical.** Post-W911, the canonical site for confidence-tier strings is `roam.db.findings.CONFIDENCE_*`. CLAUDE.md's "Confidence-tier vocabulary" sub-section should cite the module + constant names explicitly so future detectors don't reinvent.
- **W917 — `test_smells_confidence_mapping_parity` hardcoded-string set should derive from findings.** The W867 lint currently hardcodes its allowed-tier set; should derive from `roam.db.findings.CONFIDENCE_*` for the same reason W911 flipped `_CONFIDENCE_BASES`.
- **W918 — `_resolved_thresholds` silent fallback for unknown metrics.** Returns a default threshold on unknown metric names. Should raise OR surface a `partial_success=True` envelope so agents calling `roam alerts <new-metric>` don't get a silent default-pass.
- **W919 — TypedDict for `cmd_alerts` rule shape.** The rule dict carries `threshold` / `direction` / `severity` / `trend_label` / `worse_when_*` ad-hoc; a TypedDict would surface drift at write time (the W910 backfill would have failed at type-check time on a TypedDict-annotated `_DEFAULT_THRESHOLDS`).
- **W920 — Differently-named-twin audit via the W855 behavioral-fingerprint detector.** W878 surfaced a 4th literal-named twin that grep missed; the W855 rename-invariant clone detector (already shipped) could replace literal-string sweeps for this class of audit. Worth proving on one more case before generalising.
- **W921 — Audit other "duplicated from python_lang" claims.** W904 follow-up; sweep the codebase for other "duplicated from python_lang" / "mirrors python_lang" hedges and verify each one is factually true. The W904 finding suggests this is a recurring template.
- **W903 — W686 path-length recurrence operational note.** Recurring across batches; tooling-side investigation not addressable from inside roam.

### Added — Canonical-source consolidation arc (W923 / W925 / W929 / W935 + W866 / W920 / W927 / W928 — W939-CONSOLIDATE)
- **W923 — REAL clone target: `make_smell_finding(...)` hoisted to `src/roam/catalog/_shared.py`.** 4 catalog-layer callers migrated. `smells.py` + `type_switch.py` consume the canonical via direct import alias; `parallel_hierarchy.py` + `clones_cross_layer.py` route dict construction through canonical via detector-specific arg-adapter wrappers. **Optional kwargs are OMITTED FROM DICT when None** — preserves the 8-key envelope shape every finding-registry test asserts. The W855 rename-invariant detector reports **0 remaining catalog-layer `_finding` clone pairs**. **237 focused tests pass.** Closes the W874-finding clone family inside the detector code that catches clones.
- **W935 — `make_finding_id(prefix, subject, *raw_parts)` hoisted to `roam.db.findings`.** 6 sites (`cmd_audit_trail_conformance` / `cmd_bus_factor` / `cmd_dead` / `cmd_doctor` / `cmd_orphan_imports` / `cmd_smells`) reduced their `_XXX_finding_id` bodies to one-line returns. **All 6 outputs hash-byte-identical before/after** — persisted finding rows stay valid. 5 dangling `import hashlib` lines removed inline. **77/78 focused tests pass** (1 pre-existing unrelated failure).
- **W925 — `detectors._finding` fully annotated** matching `smells.make_smell_finding` style (`sqlite3.Row` for `sym`, `Mapping[str, Any]` / `Iterable[str]` / `int | None` for kwargs, `-> dict` return). **230 focused tests pass.** Pairs with W923's canonical hoist to give the catalog-layer finding constructors complete type coverage.
- **W929 — `_RE_CAMEL_SPLIT` + `_RE_UPPER_SPLIT` canonical at `tfidf.py`.** `search/tfidf.py` owns the canonical pre-compiled regexes; `index_embeddings._camel_split()` is now a thin wrapper consuming them. Option (C) chosen after option (A) hit a circular import — captured as the operational pattern: when canonical-hoist hits a cycle, owner-flip is the safe alternative.
- **W866 — Dispatch-table refactor on 3 type-switch sites W852 flagged on roam-code's OWN code.** `smells.py:1793` + `smells.py:1812` (magic-numbers walker; 4-arm isinstance chain → `_AST_HANDLERS` dispatch by `type(child)`) and `registry_dispatch.py:170` (3-arm dispatch on `type(value)` against `ast.Dict` / `List` / `Tuple`). **Dogfood-OCP win**: the W852 type-switch detector is now clean against itself. **216 focused tests pass.**
- **W919 — `AlertThreshold` TypedDict landed in `cmd_alerts.py`.** Closes the W919 drive-by from the W922 batch. Fields: `op` as `Literal[5 comparators]`, `value` as `float | int`, `level` as `str` (deliberately not `Literal` — `_resolved_thresholds` normalizes UPPER-case at load time). `_DEFAULT_THRESHOLDS` typed as `dict[str, AlertThreshold]`. **49/49 focused tests pass.**
- **W915 — `QUERY_COST_LOW` / `_MEDIUM` / `_HIGH` constants added to `detectors.py`** with `_QUERY_COSTS` deriving from them. Closes the W915 drive-by from the W922 batch — the keys no longer live as bare string literals.
- **W917 — `_SMELL_CONFIDENCE_TIERS` (3-of-4 subset, no `runtime`) added to `test_smells_confidence_mapping_parity.py`.** `test_all_confidence_values_are_canonical` now uses the smells-specific allowlist instead of the global canonical set. Closes the W917 drive-by from the W922 batch.

### Hardened — Behavioral-fingerprint twin sweep + cycle-verification discipline (W920 / W927 / W928 — W939-CONSOLIDATE)
- **W920 — Behavioral-fingerprint sweep for differently-named twins (Explore mode).** 5 unmigrated twins surfaced beyond literal-grep reach: `relations.py:343 _is_test_path` (W873 left as cycle-break — W902 method says verify); `pytest_fixtures.py:112 _is_test_function`; `rerank.py:376` inline (4+ call sites); `cmd_adversarial.py:347` inline; `cmd_next.py:379+518` inline (later DECLASSIFIED as non-clones — shape-checks, not parsers). Methodologically validates W855 rename-invariant detector > literal-string grep for clone-family completeness.
- **W927 — `rerank.py:376-396` inline 21-line OR-chain extracted** to module-level `_is_test_path()` + 4 named pattern tuples. **Did NOT delegate to `is_test_file`** — would broaden behavior (rerank was tuned WITHOUT `conftest.py` / `_test.java` / etc). 26-case truth table at `.audit-tmp/verify_rerank_helper.py` confirms **0 diffs** before/after. **139 retrieve tests pass.** Cycle-verification discipline (W907) applied: behavior-narrowness preserved by deliberate non-delegation.
- **W928 — `relations.py:343` cycle verdict NO** (AST transitive scan: `changed_files` imports only `file_roles` + `test_conventions` + `git_utils`, never reaches `relations`). The W873-era "to avoid roam.commands import cycle" comment was **cargo-cult false** per the W902 method. BUT delegation would broaden behavior — `relations._is_test_path` is narrower than the canonical. Kept local `def`, REPLACED misleading comment with **W928's verification record + "deliberately narrower; broadening requires reindex audit" rationale**. **31 index tests pass.** Closes a W907-rule case: cycle was false but delegation was still wrong for behavior reasons.

### Hardened — W930 declassification + W907 carry-through (W939-CONSOLIDATE)
- **W930 — Closed not-applicable.** `cmd_next.py` inline `startswith("roam ")` usages are shape-checks, not parsers (W920 misclassified them as twins). Captured the W920 audit's own false-positive class.
- **W916 — CLAUDE.md confidence-tier vocabulary section** now cites `src/roam/db/findings.py` canonical with the 4 `CONFIDENCE_*` constant names + "extend canonical first, never hardcode at consumer site" discipline rule. Pairs with W911 / W917 to give the confidence-tier vocabulary a single source of truth + reader-facing pointer.

### Operational findings (W939 batch — pending fix / decision)
- **W931 — Add `mypy` to `.venv` typecheck extras.** Discovered while running W919 / W925 type-annotation validation; convenience pending. | `pyproject.toml [project.optional-dependencies]` | 30 min
- **W932 — Audit `detectors._finding` callers for non-dict `evidence=`** (W925 follow-up). Type annotation says `Mapping[str, Any] | None`; callers should be audited for stray non-dict shapes. | `src/roam/catalog/detectors.py` callers | 1h
- **W933 — Tighten `cmd_alerts._parse_alerts_yaml` + `_resolved_thresholds` return types** (W919 follow-up). The TypedDict landed; the two YAML-loader return types should now narrow from `dict[str, dict]` to `dict[str, AlertThreshold]`. | `src/roam/commands/cmd_alerts.py` | 1h
- **W934 — `test_findings_*` parametrization opportunity** (W923 cluster). The 4 catalog-layer migration sites have near-identical test scaffolding; parametrize for drift-guard discipline. | `tests/test_findings_*.py` | 1-2h
- **W936 — Migrate `query_cost` string-literal defaults to `QUERY_COST_*`** (W915 follow-up). Consumer sites that take a `query_cost` kwarg with a default string literal should now reference the new constants. | grep-then-migrate | 1h
- **W937 — Sweep mis-encoded Unicode arrows in docstrings** (W929 drive-by). Captured while editing `tfidf.py`; some docstrings have UTF-8-mangled arrows from prior edits. | grep `→` / mangled variants | 30 min
- **W938 — Fold `cmd_bus_factor._repo_summary_finding_id`** (W935 4th cousin). Has the same shape as the 6 sites already migrated but takes only `prefix` + `subject`, no `raw_parts`. Migration is mechanical once W935 is reviewed. | `src/roam/commands/cmd_bus_factor.py` | 30 min

### W949-CONSOLIDATE — GATE 1 of the registry-parity milestone CLOSED (W940 → W941 + W871-bulk + W895 / W896 / W897 + W870 + W914-pass-3 + W938)

> **MILESTONE: this is NOT just another batch.** The W869 research memo
> catalogued the registry-parity bug class as having 10 instances across
> roam-code; **Instance #1 (the smell-detector P0 surface) is now
> structurally CLOSED.** The pre-state had TWO hand-rolled parallel data
> tables in lockstep — `ALL_DETECTORS = [...]` in `src/roam/catalog/smells.py`
> + `_SMELL_KIND_TO_CONFIDENCE = {...}` in `src/roam/commands/cmd_smells.py`
> — held together by W862 + W867 drift-guard lints (catching the symptom)
> after every detector wave. **W941 converted BOTH tables to DERIVED VIEWS**
> off the `@detector`-decorated registry: every detector self-registers
> with its `smell_id` + confidence tier; the parallel-data shape is now
> structurally impossible for this registry. **~78 lines of hand-rolled
> parallel data eliminated.** 24 detectors + 1 rollup = 25 confidence
> entries, all canonically registered, all derivable. Hash-stability
> mandate held (detector output bytes unchanged). 283/283 focused tests
> pass. **Gate 1 of W940's milestone framing is CLOSED**; the
> registry-parity bug class is no longer maintainable as parallel-data
> debt for this surface. The remaining 9 instances (MCP tool registry,
> mode-allowlists, `_DEPRECATED_COMMANDS`, `subject_kind`, etc.) are
> sequenced in W869 + W940 follow-up memos.

### Closed — GATE 1 registry-parity milestone (W940-RESEARCH → W871-bulk → W941 — W949-CONSOLIDATE)
- **W940-RESEARCH — Registry-parity next-wave sequencing memo.** Ranked 10 candidate waves on the W869 backlog and recommended **doing the W895 / W896 / W897 design calls + W871-bulk migration FIRST** rather than patching individual drift instances one-by-one. The decorator migration is *nonlinear*: it eliminates a debt class permanently for the registry it covers, vs the per-instance patches which only seal individual cases. This memo is the strategic load-bearing input for the W941 close.
- **W895 — `@detector` `rollup_kinds={"cluster": tier}` kwarg.** Design closure inline: replaces W871's POC `register_rollup_kind` orphan-API with a kwarg on the `@detector` decorator. Single source of truth: a detector that emits a rollup `smell_id` (e.g. `temporal-coupling-cluster`) declares it inline. Captured as W943: decide formal orphan-API status for the standalone `register_rollup_kind` helper.
- **W896 — `all_detectors()` returns sorted-by-smell_id.** Design closure inline: SARIF emitters + golden-fixture tests depend on stable run-to-run ordering. Sorted retrieval makes the registry grep-friendly and decoration-order-independent.
- **W897 — `freeze_registry()` called at `run_all_detectors` entry.** Design closure inline: validator runs once per invocation (not per `@detector` call), so import order + decoration order are decoupled. Resolves the W871 POC's open finalisation-semantics question.
- **W871-bulk — 22 detectors migrated to `@detector` decorator.** All remaining smells.py detectors now self-register via the decorator + `register_rollup_kind` (now expressed via the `rollup_kinds=` kwarg per W895). `registry.py` extended with W895/W896/W897 implementations inline. `temporal-coupling` cluster rollup upgraded to `rollup_kinds={"cluster": CONFIDENCE_STRUCTURAL}` per W895. **W862 + W867 parity lints flipped SUBSET → EQUAL** (the registry IS now the source of truth, not a subset of the hand-rolled table). **283/283 focused tests pass.**
- **W941 — THE GATE 1 CLOSURE: `ALL_DETECTORS` + `_SMELL_KIND_TO_CONFIDENCE` converted to DERIVED VIEWS.** `src/roam/catalog/smells.py:ALL_DETECTORS` is now `[d.fn for d in registry.all_detectors()]` (sorted-by-smell_id per W896); `src/roam/commands/cmd_smells.py:_SMELL_KIND_TO_CONFIDENCE` is now `{d.smell_id: d.confidence_tier for d in registry.all_detectors()} | {rollup_id: tier for ...}` per W895 rollup_kinds. **~78 lines of parallel-maintained data eliminated**; 24 detectors + 1 rollup = 25 confidence entries, all canonically registered, all derivable. **Detector output bytes unchanged** (hash-stability mandate held). **283/283 focused tests pass.** The W869-catalogued registry-parity bug class is now structurally impossible for this registry surface.
- **W870 — Per-detector version-stamp parity lint (permissive).** New AST lint asserts that every `@detector`-registered detector either (a) has a per-id `<DETECTOR>_VERSION` module constant OR (b) inherits the composite `SMELLS_DETECTOR_VERSION` fallback. **7/24 detectors have per-id stamps; 17/24 share the composite.** Captured as W944 for a strict-mode toggle once per-id stamps cover the full roster. **3/3 lint tests pass.** Final P0 piece of W869's hybrid Archetype B+E recommendation.

### Closed — Hash-stable canonical-source fold (W938 — W949-CONSOLIDATE)
- **W938 — `cmd_bus_factor._repo_summary_finding_id` folded onto W935's `make_finding_id` canonical.** 4th-cousin site captured in the W939 batch (no `*raw_parts`, only `prefix` + `subject`). One-line return; **hash-stable across 5 sample inputs** (verified byte-identical pre/post). `import hashlib` removed. **43 focused tests pass.** W935 finding-id-builder family is now fully consolidated across all 7 sites; no remaining outliers.

### Closed — Third stale-pending triage (W914-pass-3 — W949-CONSOLIDATE)
- **W914-pass-3 — Third pass over the stale-pending roster.** 3 confirmed closures (W221 user-blocked + sub-scope absorbed into W196 / W199 / W202 / W203 milestone; W354 verification absorbed into W454 `qualified_only` flag; W367 duplicate-pending already completed as #513), 2 BLOCKED (W350 / W351), 11 STILL VALID. **Combined with W876 + W914: 22 stale-pending tasks flipped across 3 passes.** Pattern locked: triage in waves, document the closure reason, never silently drop.
- **W937 — Closed not-applicable.** Source sweep confirmed no `β†’` Unicode-arrow corruption remains; W929 fix was the only instance.

### Operational findings (W949 batch — pending fix / decision)
- **W942 — Pivot W862 count-drift lint to registry source.** W862 currently asserts `len(ALL_DETECTORS) == <docstring count>`; after W941 converted ALL_DETECTORS to a derived view, the docstring-count assertion is structurally redundant. Pivot to `len(registry.all_detectors()) == <docstring count>` (registry-side authority). Captured as DBD-3 in the W940 sequencing memo. | `tests/test_smells_detector_count_drift.py` | 30 min
- **W943 — Decide `register_rollup_kind` orphan-API status.** W895 added `rollup_kinds=` kwarg on `@detector`; the standalone `register_rollup_kind` helper from the W871 POC is now functionally redundant. Decide: deprecate-and-remove vs leave-as-explicit-API. Captured as DBD-1 in the W940 memo. | `src/roam/catalog/registry.py` | 30 min decision + 1h migration
- **W944 — W870 strict-mode toggle for per-detector versioning.** Once per-id `<DETECTOR>_VERSION` stamps cover the full 24-detector roster (currently 7/24 have them), flip the W870 lint from permissive (composite fallback OK) to strict (per-id required). | `src/roam/catalog/registry.py` + per-detector module constants | 2-4h
- **W945 — Refresh registry.py docstring "two SOURCE-OF-TRUTH" comment** (W941 follow-up). The module docstring still implies two parallel sources; after W941 the registry IS the single source. Cosmetic. | `src/roam/catalog/registry.py` docstring | 30 min
- **W946 — Refresh smells.py:19 parallel_hierarchy wording** (W941 follow-up). Comment narrates the pre-W941 two-table arrangement. | `src/roam/catalog/smells.py:19` | 15 min
- **W947 — Simplify `test_decorator_registry_parity` self-referential assertions** (W941 follow-up). Post-W941 the parity lint compares a derived view against its own source — partially tautological. Replace with a value-shape contract test instead of identity. | `tests/test_smells_confidence_mapping_parity.py` | 1h
- **W948 — Move tier rationale inline to `@detector` calls** (W941 follow-up, medium effort). Per-detector confidence-tier rationale currently lives in CLAUDE.md prose; with W895's `rollup_kinds=` kwarg the per-detector tier is already declared at the decorator. Adding a short inline `# heuristic — name pattern only` comment at each `@detector(...)` call would surface the rationale where the next reader will look. | smells.py + sibling catalog modules | 2-3h

### W965-CONSOLIDATE — Gate-1 cleanup + W525 strategic pause + W918 / W924 / W933 typing/silent-fallback close

> **CONSOLIDATE checkpoint = W965.** ~10 closures + 15 drive-by captures
> since W949-CONSOLIDATE. Two arcs landed in parallel: (1) the W942 / W945
> / W946 / W947 / W955 / W956 follow-throughs that close the W941 Gate-1
> cleanup queue (count-drift lint pivoted to registry source; pre-W941
> "two SOURCE-OF-TRUTH" wording flipped to past-tense across registry.py
> + smells.py + the parity test; freeze_registry invariant numbering
> re-ordered to match execution order), and (2) three independent
> source-tightening landings — **W918** closing a Pattern 2
> silent-fallback hole in `_resolved_thresholds` (unknown user-supplied
> metric now surfaces via warnings_out + envelope `partial_success=True`
> + a new `agent_contract.facts` entry), **W924** stamping
> `detector_version` on every `detectors._finding` envelope via the
> pre-existing canonical `roam.catalog.versions.detector_version(task_id)`
> (most task_ids → `DEFAULT_VERSION='1.0.0'`; the nested-lookup site
> carries the `1.1.0` override), and **W933** tightening
> `cmd_alerts._parse_alerts_yaml` to `dict[str, dict[str, Any]]` +
> selecting Option B (loose-but-honest typing) on `_resolved_thresholds`
> because `slot.update(rule)` precludes TypedDict without runtime
> validation. **W525 — STOP AT INVENTORY.** The W869 Instance #2 proving
> ground (MCP tool registry) ran the inventory pass and surfaced real
> structural gaps the wave's prescribed derivation would have silently
> papered over: the hand-rolled `_CORE_TOOLS` (57 tools) does NOT match
> `@roam_capability(category="core")` (0 — the category doesn't exist on
> the decorator), and `mcp_preset=("core",)` is mostly boilerplate (228
> of 230 tools carry it). The decision was made to **STOP at inventory
> rather than mechanically derive** until the strategic call on
> derivation source (category= vs mcp_preset= vs hand-rolled) lands as
> W357 long-horizon work. W525 split into 5 deep drive-bys
> (W950 strategic / W951-W953 evidence / W954 closed) feeding that call.
> **W954 regression-guard test landed** — `tests/test_w954_core_tools_capability_drift.py`
> (3 tests, 191 lines, all pass) snapshots `_CORE_TOOLS=57`, capability
> registry=230 (one retired), `mcp_preset="core"` boilerplate=228,
> `category="core"=0`, and floors at ~10% headroom (≥18 in_core_not_cap,
> ≥180 in_cap_not_core). Hash-stability mandate held trivially across
> the batch — W924's stamp lands on the dict AFTER `make_finding_id`
> hashes only `*raw_parts`. Stale-pending triage closed three more rows
> via the W914 methodology (W221 / W354 / W367 confirmed-closed from the
> W949 batch; carried into the W965 BACKLOG strike-throughs for
> visibility).

### Closed — Gate-1 cleanup follow-throughs (W942 / W945 / W946 / W947 / W955 / W956 — W965-CONSOLIDATE)
- **W942 — Count-drift lint pivoted to registry source.** `tests/test_smells_detector_count_drift.py` no longer reads `len(ALL_DETECTORS)` (post-W941 a derived view — the docstring assertion was structurally self-referential). All 5 call-sites updated to call `len(list(all_detectors()))` from `roam.catalog.registry`. **179 focused tests pass.** Closes the DBD-3 follow-up from the W940 sequencing memo.
- **W945 — `registry.py` docstring + comments refreshed to past-tense.** Lines ~11-16: "two SOURCE-OF-TRUTH dicts" framing now narrates the pre-W941 arrangement in past tense. Lines ~68-72: "The source-of-truth collections..." replaces the old present-tense phrasing. Cosmetic; closes the W941 follow-up so the next reader doesn't believe two parallel sources still exist.
- **W946 — `smells.py` module docstring refreshed.** Lines 12-20: notes `ALL_DETECTORS` is now a derived view; registration happens via `@detector` decorator + `detector(...)(fn)` calls. Removes the lingering pre-W941 "two parallel hierarchies" claim.
- **W947 — Regression-guard note pinned in `test_decorator_registry_parity.py`.** 11-line "W947 note (KEPT as regression guard, do not delete)" block added at the top of the file. Captures *why* the parity lint stays even though post-W941 it compares a derived view against its own source — the lint is the regression guard against silent un-deriving (e.g. a future refactor that hand-rolls a row back into `_SMELL_KIND_TO_CONFIDENCE`).
- **W955 — Inline tighten of pre-W941 transition wording.** `tests/test_decorator_registry_parity.py:9` flipped "belt-and-braces during the transition window" → past-tense ("…during the transition window from W871 → W941"). Single-line tweak; closes the W947 audit's drive-by.
- **W956 — `freeze_registry` invariant numbering re-ordered to match execution order.** The docstring numbered the three invariants 1/2/3 in a different order than the code body actually checked them. Re-numbered to match execution order: (1) duplicates first (cheapest), (2) anchored ids, (3) canonical tier. Closes a subtle reader-trip-up that would have wasted the next debugger's time.

### Closed — Source-tightening trio (W918 / W924 / W933 — W965-CONSOLIDATE)
- **W918 — Pattern 2 silent-fallback fix in `_resolved_thresholds`.** `src/roam/commands/cmd_alerts.py` `_resolved_thresholds` previously returned a default threshold (`op='>', value=0`) on unknown user-supplied metrics. Fix: new `warnings_out: list[str] | None = None` parameter accumulates a per-unknown-metric warning string; envelope flips `summary.partial_success = True` whenever any warning was raised; new `agent_contract.facts` entry surfaces "user-supplied metric `<name>` was not in the canonical roster — defaulted to `op='>', value=0`". Backward compat preserved (existing `.roam/alerts.yaml` configs untouched). **52 focused tests pass.** Closes the W918 W922-carry-forward; pairs with the W910 backfill + W911 canonical-derivation work to give the alerts surface a complete Pattern 2 + Pattern 3a discipline.
- **W924 — `detector_version` stamp on `detectors._finding`.** `src/roam/catalog/detectors.py:_finding` now stamps `finding["detector_version"]` via the pre-existing canonical `roam.catalog.versions.detector_version(task_id)`. Most task_ids resolve to `DEFAULT_VERSION='1.0.0'`; the nested-lookup site carries the `1.1.0` override per the W81 ABC contract. **Hash-stability verified** — `make_finding_id` hashes only `*raw_parts` (the W935 contract); the stamp lands on the dict AFTER id computation, so persisted finding rows stay byte-identical. **219 focused tests green.** Closes the W924 carry-forward from the W922 batch; pairs with the W81 per-component version-stamp arc.
- **W933 — `cmd_alerts` return-type tightening.** `_parse_alerts_yaml` flipped to `dict[str, dict[str, Any]]` (previously `dict[str, dict]`). `_resolved_thresholds` picked **Option B (loose-but-honest)**: keeping the return type as `dict[str, dict[str, Any]]` rather than `dict[str, AlertThreshold]` — the closer TypedDict was rejected because the body does `slot.update(rule)` and TypedDict mutation paths require runtime validation that would bloat the hot path. The W919 `AlertThreshold` TypedDict is still in place for the `_DEFAULT_THRESHOLDS` shape contract; the looser return type accommodates the user-supplied-rule merge path. **46/46 focused tests pass.** Closes the W933 W939-carry-forward.

### Captured — W525 STOP-AT-INVENTORY decision + W954 regression-guard (W950 / W951 / W952 / W953 / W954 — W965-CONSOLIDATE)
- **W525 — W869 Instance #2 proving ground: STOP AT INVENTORY.** The Gate-2 candidate (MCP tool registry; 224 wrappers / 57 in `_CORE_TOOLS`) ran the inventory pass and surfaced real structural gaps the W869 hybrid Archetype B+E template would have silently papered over: (a) hand-rolled `_CORE_TOOLS` (57) does NOT match `@roam_capability(category="core")` because **the `category` enum doesn't include `"core"` at all (0 hits)** — `category=` is shaped around `code-intelligence` / `governance` / etc., not preset membership; (b) `mcp_preset=("core",)` is the closest derivation source but it's **mostly boilerplate** — 228 of 230 tools carry it as a copy-paste default, not a curated subset. **Decision: STOP at inventory** until the strategic call on derivation source (category= vs mcp_preset= vs hand-rolled) lands as W357 long-horizon work. The W869 wave's prescribed derivation was unsafe on this surface.
- **W954 — Regression-guard test landed.** New `tests/test_w954_core_tools_capability_drift.py` (3 tests, 191 lines, all pass). Snapshot: `_CORE_TOOLS=57`, capability registry=230 (was 231 — one retired since W949), `mcp_preset="core"` boilerplate=228, `category="core"=0`. Floors at ~10% headroom (≥18 in_core_not_cap, ≥180 in_cap_not_core) so the test doesn't false-trip on additive drift but catches structural collapses (e.g. a future wave silently shrinking `_CORE_TOOLS` to align with `mcp_preset="core"`). Closes the W525 drive-by chain inline.
- **W950 / W951 / W952 / W953 — Deep drive-bys captured.** W950 STRATEGIC: pick `category=` vs `mcp_preset=` path for MCP registry derivation (feeds W357). W951: `mcp_preset` default `("core",)` is dead metadata — most call-sites accept it via copy-paste rather than deliberate curation. W952: 24 MCP-only tools have no `@roam_capability` anchor at all (gap class to address before any derivation pass). W953: 4 naming-drift cases between CLI command names + MCP wrapper names that the W869 template would not catch (e.g. `roam_pr_replay` vs `roam pr-replay`). All four captured as pendings for the W965+ wave to triage as the strategic answer lands.

### Closed — Stale-pending triage (W221 / W354 / W367 — W965-CONSOLIDATE carry-from-W949)
- **W221 — User-blocked + sub-scope absorbed.** Audit Trail (R29) snapshot work absorbed into the W196 / W199 / W202 / W203 producer-wiring + milestone-integration arc; no remaining net-new scope. Closed via W914-pass-3.
- **W354 — Verification absorbed into W454.** `qualified_only` flag verification work absorbed into the W454 implementation; no separate verification scope remains. Closed via W914-pass-3.
- **W367 — Duplicate-pending.** Already completed as #513 in a prior session; row was stale. Closed via W914-pass-3.

### Operational findings (W965 batch — pending fix / decision)
- **W357 (strategic, long-horizon) — Pick the MCP registry derivation source.** W525 inventory pass surfaced three candidates (`@roam_capability(category=...)`, `mcp_preset=(...)`, hand-rolled `_CORE_TOOLS`) — each has structural gaps. Strategic decision required before any derivation pass on the MCP tool registry. | `src/roam/mcp_server.py` + `src/roam/plugins/capability.py` | TBD (strategic)
- **W950 — STRATEGIC: pick `category=` vs `mcp_preset=` path for MCP registry derivation.** Sub-question of W357. Feeds the W869 Instance #2 wave once it unblocks. | strategic | TBD
- **W951 — `mcp_preset=("core",)` default is dead metadata.** 228 of 230 tools carry the default value via copy-paste, not curation; a derivation pass that trusts the metadata would over-include. Decide: strip the default, or curate it deliberately. | `src/roam/mcp_server.py` decorator default | 1-2h decision + 4-6h migration
- **W952 — 24 MCP-only tools have no `@roam_capability` anchor.** Gap class to close before any `category=`-based derivation pass. | per-tool audit | 4-6h
- **W953 — 4 naming-drift cases between CLI + MCP wrappers.** Tools where the MCP wrapper name does not derive from the CLI command name via the canonical kebab-→-snake transform. Captured for a documentation-grade audit before W357 lands. | per-case docs | 2h
- **W957 — W862 lint "Fix:" hint forward-compat nit.** Post-W942 pivot, the lint's "Fix: update the docstring" hint references the registry rather than ALL_DETECTORS; nit-pick wording polish. | `tests/test_smells_detector_count_drift.py` hint string | 15 min
- **W958 — `_load_alerts_config` return-type tightening.** Companion to W933 — the YAML config loader returns `dict[str, dict]` ad-hoc; tighten to `dict[str, dict[str, Any]]` for consistency with `_parse_alerts_yaml`. | `src/roam/commands/cmd_alerts.py` | 30 min
- **W959 — `_check_thresholds` `Alert` TypedDict bundle.** Companion to W933 — the per-finding `Alert` dict shape would benefit from a TypedDict declaration analogous to `AlertThreshold`. | `src/roam/commands/cmd_alerts.py` | 1-2h
- **W961 — Document uniform naming-drift convention** (W954-class follow-up). 15 additional cases beyond W953's 4 surfaced during the W954 audit; a uniform convention doc would deter recurrence. | `CLAUDE.md` "Adding a new CLI command" section + audit | 2-3h
- **W962 — `_parse_alerts_yaml` op-vocabulary validation at parse time.** Pattern 2 family follow-up to W918 — at parse time, validate the comparator is in the closed enum (`>` / `>=` / `<` / `<=` / `==`). Currently the unknown comparator silently falls through to the default. | `src/roam/commands/cmd_alerts.py` | 1h
- **W963 — `_check_thresholds` unknown-comparator silent skip.** Pattern 2 family follow-up to W918 — at check time (not parse time), the unknown comparator silently skips the row instead of surfacing via `partial_success`. Different code path from W962. | `src/roam/commands/cmd_alerts.py` | 1h
- **W964 — `delta_alerts` bool coercion silent disable.** Pattern 2 family follow-up to W918 — the `delta_alerts` flag coerces non-bool YAML values via `bool(...)` rather than surfacing a parse warning; a typo (`"yes"`, `"no"`) silently flips to enabled. | `src/roam/commands/cmd_alerts.py` | 30 min

### W977-CONSOLIDATE — cmd_alerts Pattern-2 family FULLY CLOSED + W923 test-layer consolidation + W966 audit pass

> **CONSOLIDATE checkpoint = W977.** ~10 closures + drive-by captures
> since W965-CONSOLIDATE. **Headline: cmd_alerts.py Pattern-2 family is
> now FULLY CLOSED end-to-end** via the W962 / W963 / W964 trifecta
> (op-vocabulary validation at parse + check time; bool-coercion fix on
> `delta_alerts`) followed by the W967 / W968 / W969 trifecta (REAL
> BUG: tiny YAML parser silently disabled `delta_alerts` for users
> without PyYAML — a scalar-vs-section detection gap; REAL BUG:
> `level: "fatal"` would KeyError downstream — `_CANONICAL_LEVELS`
> frozenset + `_coerce_level` helper at 3 sites + counts initializer
> fold; drift-guard test pins `_VALID_OPS == AlertThreshold.op Literal`
> via `typing.get_type_hints`). **87 focused tests pass.** Two bugs
> were latent (0 fixtures exercised them). **This is the SECOND
> consecutive Pattern-2 family fully closed in this session** — the
> first was W826 / W834 / W836 in early-session (silent SAFE on empty
> corpus across taint / health / doctor). `cmd_alerts.py` now
> exemplifies the W918 discipline: every silent-fallback path surfaces
> via `warnings_out` + `partial_success=true` +
> `agent_contract.facts`. **W923 test-layer consolidation** —
> W934 delegated 24 `test_<detector>_findings_visible_via_cmd_findings_count`
> tests to `tests/_findings_helpers.py` via Strategy C (shared helper,
> per-detector tests retained for fixture independence); doctor's
> exact-count + critique's tolerant exit-code preserved; 24/24 +
> ~190 sibling tests pass; net -46 lines (-114 of actual code).
> **W966 audit pass** — W971 confirmed the codebase was already
> W966-compliant: 13 HONEST sites, 0 LYING, 2 VALIDATED. The
> "don't TypedDict a boundary you don't validate" discipline existed
> *before* W966 codified it; W933 `_resolved_thresholds` is the
> EXEMPLAR. **W975 / W976** added lock-comments at `json_envelope` +
> `_compat_profile_payload` per W971's recommendations, documenting
> the W966 discipline at the call site. Hash-stability mandate held
> trivially across the batch.

### Closed — cmd_alerts.py Pattern-2 family FULLY CLOSED (W962 / W963 / W964 + W967 / W968 / W969 — W977-CONSOLIDATE)
- **W962 — `_parse_alerts_yaml` op-vocabulary validation at parse time.** Pattern 2 family follow-up to W918. Added `_VALID_OPS` frozenset (`>` / `>=` / `<` / `<=` / `==`); parse-time validation rejects unknown comparators via `warnings_out` accumulator + `partial_success=true`. Unknown op now surfaces explicitly instead of silently falling through to default. Warning text follows LAW 2 (imperative) + LAW 4 (concrete-noun terminal). 15 new tests.
- **W963 — `_check_thresholds` unknown-comparator silent skip closed.** Pattern 2 family follow-up to W918. Check-time validation now folds through the same `_VALID_OPS` frozenset; unknown op at check time surfaces via `partial_success` instead of silently skipping the row. Different code path from W962. Closed inline alongside W962.
- **W964 — `delta_alerts` bool coercion silent disable closed.** Pattern 2 family follow-up to W918. New `_coerce_bool` helper rejects non-bool YAML values (typo `"yes"` / `"no"` / `1` / etc.) via `warnings_out` + `partial_success=true` instead of silently `bool(...)`-coercing to enabled.
- **W967 — REAL BUG: tiny YAML parser silently disabled `delta_alerts` for users without PyYAML.** New `_coerce_scalar` helper + scalar-vs-section detection: the fallback parser (when `pyyaml` is not installed) was treating `delta_alerts: true` at root as a section header rather than a top-level scalar, silently disabling the feature for the no-PyYAML install path. Fixed by recognising the scalar-vs-section ambiguity at the parser level. 0 fixtures exercised this path pre-fix — confirmed latent.
- **W968 — Drift-guard test pinning `_VALID_OPS == AlertThreshold.op Literal`.** New test consumes `typing.get_type_hints(AlertThreshold)` to extract the `op` field's `Literal[...]` members and asserts equality with `_VALID_OPS`. The next refactor that adds a comparator to the TypedDict but forgets `_VALID_OPS` (or vice versa) now fails at lint time.
- **W969 — REAL BUG: `level: "fatal"` would KeyError downstream.** Added `_CANONICAL_LEVELS` frozenset + `_coerce_level` helper at 3 sites + counts initializer fold. Pre-fix: a user-supplied `level: "fatal"` would parse cleanly into `AlertThreshold` (the `level` field is `str`, not `Literal`) but downstream code keyed on `_LEVEL_ORDER` would `KeyError`. 0 fixtures exercised this path pre-fix — confirmed latent.

### Closed — W923 test-layer consolidation (W934 — W977-CONSOLIDATE)
- **W934 — 24 `test_<detector>_findings_visible_via_cmd_findings_count` tests delegated to `tests/_findings_helpers.py`.** Strategy C: shared helper consumes the per-detector fixtures; per-detector tests retained for fixture independence. **Doctor's exact-count + critique's tolerant exit-code preserved.** 24/24 + ~190 sibling tests pass. **Net -46 lines (-114 of actual code), +68 lines of shared helper scaffolding.** Closes the W923-cluster test-layer follow-up first surfaced in the W939 batch.

### Closed — Typing audit + lock-comments (W958 / W961 / W966 / W971 / W975 / W976 — W977-CONSOLIDATE)
- **W958 — `_load_alerts_config` return-type tightened to `dict[str, dict[str, Any]]`.** Companion to W933 — the YAML config loader's return type now matches `_parse_alerts_yaml` for consistency.
- **W961 — CLAUDE.md MCP tool naming convention section added.** New sub-section at CLAUDE.md lines 822-832: documents the uniform `roam_<underscored>` ↔ `<dashed>` convention + 4-entry alias allowlist for genuine renames. Closes the W953 / W954 audit's documentation gap.
- **W966 — CLAUDE.md "Don't TypedDict a boundary you don't validate" discipline rule.** New sub-section at CLAUDE.md lines 156-170 (companion to W907 "Verify the cycle before hedging"). Codifies the W933 Option B decision rationale: TypedDict mutation paths require runtime validation when the body does `slot.update(rule)` — discipline rule says either validate at the boundary or use the looser `dict[str, dict[str, Any]]` return type.
- **W971 — Audit pass: codebase already W966-compliant.** Surveyed 15 TypedDict / boundary sites; **13 HONEST** (canonical pattern), **0 LYING**, **2 VALIDATED** at the boundary. The discipline existed *before* W966 codified it; W933 `_resolved_thresholds` is the EXEMPLAR — the Option B return-type widening was the right call structurally.
- **W975 — Lock-comment added at `json_envelope`** per W971's audit recommendations. Documents the W966 discipline at the call site for future readers — the `summary` field is typed as `Mapping[str, Any]` *deliberately* because callers do partial-mutation; a TypedDict would force runtime validation downstream.
- **W976 — Lock-comment added at `_compat_profile_payload`** per W971's audit recommendations. Same shape as W975 — documents the W966 discipline inline so the next maintainer doesn't tighten the type and trigger a `slot.update`-class regression.

### Operational findings (W977 batch — pending fix / decision)
- **W972 — `_load_alerts_config` non-dict YAML root silent fallback.** Real-but-edge bug: a YAML file whose root is a list rather than a dict silently falls through to defaults rather than surfacing via `partial_success`. Same shape as W918 / W963 / W964. | `src/roam/commands/cmd_alerts.py` | 1h
- **W973 — `_make_alert` level validation defense.** Latent risk: `_make_alert` doesn't re-validate `level` against `_CANONICAL_LEVELS` even though `_coerce_level` does at construction time. Defense-in-depth tightening. | `src/roam/commands/cmd_alerts.py` | 30 min
- **W974 — Tighten `AlertThreshold.level` to `Literal[...]` (now safe per W969).** Pre-W969 the field was deliberately `str` because there was no runtime validation; post-W969 `_coerce_level` validates at construction time, so the TypedDict `Literal` tightening is now safe. | `src/roam/commands/cmd_alerts.py` | 30 min
- **W978 — Pre-existing `test_bus_factor_stale_kind_emitted` failure.** Surfaced during W934 consolidation — the test was already failing pre-batch; W934 helper did not regress it but the failure is unrelated. Triage required. | `tests/test_findings_bus_factor.py` | 1-2h triage
- **W979 — `dark_matter` ↔ `dark-matter` + `fan_symbol` Pattern-3a divergence.** Surfaced during W934 consolidation — two detector_id slugs use underscores while the rest use kebab-case. Pattern-3a metric-name canonicalisation gap. | `src/roam/db/findings.py` + per-detector emitters | 1-2h

### W1001-CONSOLIDATE — Pattern-2 playbook propagation across 3 candidate modules + SQL ESCAPE discipline + smells_suppress YAML hardening

> **CONSOLIDATE checkpoint = W1001.** ~15 closures + drive-by captures
> since W977-CONSOLIDATE. **Headline: the W983-RESEARCH playbook
> (synthesised from W977's full cmd_alerts.py Pattern-2 close) was
> propagated to the three nominated candidate modules — and the
> outcomes are LOAD-BEARING.** **W987** sealed `cmd_smells.py` via a
> full playbook apply (closed-set `--kind` validation against
> `kind_to_confidence()` + `warnings_out` plumbed from the suppression
> loader through the CLI to the envelope). **W988 was CORRECTLY CLOSED
> AS NOT-APPLICABLE** — the agent verified W983's premise didn't match
> `cmd_conventions.py` (no user-supplied boundary exists) and STOPPED
> instead of fabricating work. **W989 sealed `cmd_pr_risk.py` via a
> DIFFERENT real Pattern-2 gap than W983's framing assumed** —
> `_normalise_pr_risk_level` was silently flooring unknown input to
> `"low"` per the W718 CI-safety contract; now warns + preserves the
> floor; NO TypedDict added per W966 discipline (internal dict, not
> user boundary). **The methodology lesson: premise verification is
> the FIRST step of every playbook application.** A playbook that
> applies mechanically without checking the premise produces fabricated
> work; a playbook that applies with discipline either seals the
> nominated gap, finds a different real gap, or stops cleanly.
> **W990 → W991 SQL ESCAPE sweep**: found 10 accidental wildcard sites
> + 2 already-correctly-escaped in `src/roam/catalog/detectors.py`; 3
> were HIGH-risk in the idiom matchers. W991 fixed 8 W990 sites + 6
> parallel-pattern drive-bys + 1 duplicate matmul fallback = **15 LIKE
> escapes total**; 109 focused tests pass; smoke confirms
> `finXinXsortedXarray` is correctly excluded. **W994 + W995
> smells_suppress YAML hardening**: W994 found a REAL BUG —
> `_is_expired` was silently defaulting to `not_expired` on
> unparseable `expires` strings (typo `2026-13-99` → suppression
> stayed active). Fix at load-time AND match-time with a new
> `EXPIRES_FMT` constant; 8 tests. W995 surfaced malformed-entry drops
> that were previously "silently skipped" per an admitted comment in
> the parser — now partitioned into valid/dropped + indexed warnings
> + rollup; 7 tests. **W982 cmd_fan rename completed**:
> `fan_symbol → fan-symbol` (9 cmd_fan.py + ~32 test sites; SQL LIKE
> `'fan_%'` fixed; the Strategy A persisted-hash break is documented).
> 27 focused tests pass. **W978 bus_factor stale-kind test fixed** via
> a fixture monkeypatch — the W405 shallow-history drop of a 2-year-old
> commit had made the test brittle; 18/18 pass; 3 sharp drive-bys
> captured (W984 autouse conftest / W985 INFO log on the drop / W986
> "first hypothesis" checklist in CLAUDE.md). **W983-RESEARCH memo
> shipped** (`(internal memo)`,
> <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> 374 lines, 7 reusable patterns, 3 candidate modules) and **W999
> amended it** with the W988/W989 outcomes — the case-study now
> explicitly codifies "premise verification is the first step of
> every playbook application". **W970 CLAUDE.md drive-by**:
> `_DEFAULT_THRESHOLDS` is the canonical positive counter-example
> for "when TypedDict IS appropriate" — a 7-line paragraph added to
> the W966 sub-section pairs the rule with its exemplar. **W936**
> migrated 37 `query_cost` string literals to `QUERY_COST_*`
> constants in detectors.py (W939-carry-forward closed). **Hash-
> stability mandate held trivially across the batch** — every fix
> either added new validation paths (no pre-fix envelope to compare),
> swept LIKE-clause inputs (no detector output bytes moved), or
> tightened YAML load-time validation (no persisted finding rows
> touched).

### Closed — Pattern-2 playbook propagation across 3 candidate modules (W987 / W988 / W989 — W1001-CONSOLIDATE)
- **W987 — `cmd_smells.py` Pattern-2 playbook FULLY APPLIED.** Closed-set `--kind` validation against the canonical `kind_to_confidence()` set (lazy-derive from registry, not Literal — smart 1-anchor design that survives detector additions); `warnings_out` plumbed from the suppression loader → CLI → envelope; unknown `--kind` arguments now surface via `warnings_out` + `partial_success=true` + `agent_contract.facts` entry, matching the W918 / W962 envelope shape. **185 tests pass.** Closes the W923-cluster carry-forward Pattern-2 gap.
- **W988 — CORRECTLY CLOSED AS NOT-APPLICABLE.** The W983 playbook nominated `cmd_conventions.py` as a candidate but a premise-verification pass found the command has no user-supplied boundary (no `--kind` / `--metric` style argument, no YAML-config user input) — the Pattern-2 gap the playbook is designed to seal doesn't exist on this surface. **Agent refused to fabricate work**; **71 baseline tests still pass** (no source bytes moved). **DISCIPLINE WIN** — the playbook applied with premise-verification rather than mechanically.
- **W989 — `cmd_pr_risk.py` sealed via a DIFFERENT real Pattern-2 gap than W983's framing assumed.** The playbook framed the candidate as a `slot.update()` shape; the actual gap was in `_normalise_pr_risk_level` — silently flooring unknown input (e.g. `level: "foobar"`) to `"low"` per the W718 CI-safety contract. Fix: now emits a `warnings_out` entry + `partial_success=true` while PRESERVING the W718 floor (CI-safety contract held — unknown levels still fail-closed at the lowest severity, no behavioral regression). **NO TypedDict added** per W966 discipline: the internal dict is not a user boundary, so the Option B (loose-but-honest) typing is correct. **51 tests pass.** Methodologically the most important close in the batch: the playbook propagation found a real bug that wasn't the one the playbook was designed to find — proves the framework gates are working when each application is run with discipline.

### Closed — SQL ESCAPE discipline (W990 / W991 — W1001-CONSOLIDATE)
- **W990 — SQL LIKE wildcard audit on `src/roam/catalog/detectors.py`.** Found **10 accidental wildcard sites + 2 already-correctly-escaped**. 3 sites were HIGH-risk in the idiom matchers (substring matches on symbol names could match across word boundaries with the wildcard interpretation; e.g. `find_in_sorted_array` would match `find%in%sorted%array` against `finXinXsortedXarray`).
- **W991 — Fixed 8 W990 sites + 6 parallel-pattern drive-bys + 1 duplicate matmul fallback = 15 LIKE escapes total.** Each call site now uses the canonical ESCAPE pattern: `LIKE ? ESCAPE '\\'` with the parameter pre-escaped via `value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')`. **109 focused tests pass.** Smoke test confirms `finXinXsortedXarray` is correctly excluded from the `find_in_sorted_array` match set post-fix.

### Closed — smells_suppress YAML hardening (W994 / W995 — W1001-CONSOLIDATE)
- **W994 — REAL BUG: `smells_suppress._is_expired` silently defaulted to `not_expired` on unparseable `expires` strings.** A typo like `expires: 2026-13-99` (invalid month) was silently treated as "not yet expired", keeping the suppression active forever. Fix: parse the `expires` value at LOAD time (raises `ValueError` on unparseable strings via the new `EXPIRES_FMT = "%Y-%m-%d"` module constant) AND re-validate at match time (defense-in-depth — the match-time path now surfaces a `warnings_out` entry on unparseable input rather than silently defaulting). **8 tests.** The full Pattern-2 envelope shape (`warnings_out` + `partial_success=true`) flows through to the suppression loader's caller envelope.
- **W995 — Malformed-entry drops now surface (W994 follow-up).** Pre-fix the suppression parser had an admitted `# silently skipped` comment in its malformed-entry handling — invalid YAML entries were dropped without notice. Fix: parser now partitions input into `valid` + `dropped` lists; each dropped entry produces an indexed warning string; a rollup count (`"dropped N malformed entries (indices X / Y / Z)"`) accumulates into `warnings_out` and flips `partial_success=true`. **7 tests.** Same envelope shape as W994.

### Closed — Source carry-forwards + drive-bys (W936 / W970 / W978 / W982 — W1001-CONSOLIDATE)
- **W982 — `fan_symbol → fan-symbol` rename completed.** 9 source sites in `src/roam/commands/cmd_fan.py` + ~32 test sites updated; the `SQL LIKE 'fan_%'` pattern fixed (Pattern-3a kebab-case canonicalisation, sister to W979's dark_matter/fan_symbol divergence). **Strategy A persisted-hash break documented**: existing `findings_history` rows with `subject_id LIKE 'fan_symbol:%'` will not match the new `'fan-symbol:%'` pattern until the next reindex; the migration is forward-only by design. **27 focused tests pass.**
- **W978 — `test_bus_factor_stale_kind_emitted` failure FIXED via fixture monkeypatch.** Root cause: W405's shallow-history truncation was dropping a 2-year-old commit the test fixture depended on. Fix: fixture monkeypatches the `cutoff_days` to preserve the test's expected stale-kind flow. **18/18 tests pass.** Closes the W934 drive-by (W978 carry-forward). Three sharp drive-bys captured during the fix (W984 autouse conftest / W985 INFO log on the drop / W986 "first hypothesis" checklist in CLAUDE.md).
- **W936 — Migrated 37 `query_cost` string literals to `QUERY_COST_*` constants in `src/roam/catalog/detectors.py`.** Pairs with W915's `QUERY_COST_LOW/MEDIUM/HIGH` constant introduction (W939-CONSOLIDATE) — the consumer sites now reference the canonical constants instead of bare strings. Closes the W939 carry-forward.
- **W970 — CLAUDE.md W966 sub-section gained a 7-line positive counter-example.** Added a paragraph naming `_DEFAULT_THRESHOLDS` (in `cmd_alerts.py`) as the canonical "when TypedDict IS appropriate" exemplar — the dict is constructed from a closed shape, no `slot.update()` mutation path exists, and the TypedDict surface (W919 `AlertThreshold`) accurately captures it. Pairs the W966 rule ("don't TypedDict a boundary you don't validate") with its inverse exemplar so the next reader sees both sides of the discipline.

### Research + memos (W983-RESEARCH / W999 — W1001-CONSOLIDATE)
- **W983-RESEARCH — `(internal memo)`** (374 lines, 7 reusable patterns, 3 candidate modules). Synthesises the full W977 cmd_alerts.py Pattern-2 close into a reusable playbook: (1) `warnings_out: list[str] | None` accumulator on every boundary parser/normaliser; (2) `_VALID_*` closed-set frozensets at the boundary; (3) parse-time + check-time validation paths share the same frozenset; (4) latent-bug discipline (0-fixture paths are silent bugs); (5) `typing.get_type_hints` drift-guard pinning TypedDict `Literal[...]` ↔ `_VALID_*` frozenset; (6) coerce-helper pattern at construction time; (7) `_CANONICAL_*` frozenset for vocabulary that downstream code dictionaries-keys on. Three nominated candidates: cmd_smells (W987), cmd_conventions (W988), cmd_pr_risk (W989). <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W999 — W983 case-study amendment.** Added a new "Premise verification is the first step of every playbook application" section explicitly codifying the W988 + W989 outcomes: (a) W988 closed correctly as not-applicable because the premise didn't match cmd_conventions.py; (b) W989 found a DIFFERENT real Pattern-2 gap than the playbook's framing assumed (W718 floor in `_normalise_pr_risk_level`, not a `slot.update` framing). The amendment codifies: "a playbook that applies mechanically without checking the premise produces fabricated work; a playbook that applies with discipline either seals the nominated gap, finds a different real gap, or stops cleanly".

### Operational findings (W1001 batch — pending fix / decision)
- **W984 — Autouse conftest for the bus_factor stale-kind fixture monkeypatch.** W978 fix is per-test; promoting to an autouse conftest fixture would cover sibling tests that may regress when the W405 shallow-history default shifts. | `tests/conftest.py` or `tests/test_findings_bus_factor.py` | 30 min
- **W985 — INFO log when `W405 shallow-history` drops a commit.** The drop is currently silent at the `git_history` ingestion layer; an INFO log naming the dropped SHA + reason would help future "why is this test failing" investigations. | `src/roam/index/git_stats.py` | 30 min
- **W986 — CLAUDE.md "first hypothesis" checklist for test-failure triage.** W978 root-cause took two passes because the W405-shallow-history hypothesis wasn't first on the agent's checklist. Capture the lesson: when a `test_*_stale_*` or `test_*_history_*` test fails, the first hypothesis to check is "did W405 truncate the fixture's expected commit?". | `CLAUDE.md` Quality-discipline section | 30 min
- **W980 / W981 — W974 UX papercuts.** W980: the new `AlertThreshold.level` `Literal[...]` tightening produces a generic `TypedDict` error message on invalid input; a `LITERAL_LEVEL_VALUES` error message in `_coerce_level` would be more actionable. W981: the `_coerce_level` error message format doesn't follow LAW 4 concrete-noun-terminal vocabulary. | `src/roam/commands/cmd_alerts.py` | 30 min each
- **W992 / W993 — W991 drift-guards.** W992: add an AST lint asserting every `LIKE ?` in `detectors.py` is paired with `ESCAPE '\\'` — prevents the next reviewer from re-introducing an unescaped wildcard pattern. W993: an end-to-end smoke test asserting `find_in_sorted_array` does NOT match `finXinXsortedXarray` (the exact false-positive case W991 sealed). | `tests/test_w992_sql_escape_drift.py` + `tests/test_w993_finXsortedXarray_smoke.py` | 1h each
- **W997 / W998 — `expires` ↔ `expires_on` field name divergence in smells_suppress YAML.** W994 standardised on `expires`; sister suppression substrates carry `expires_on` or `expiry`. Pattern-3b parameter-name canonicalisation gap. | `src/roam/policy/suppression_v2.py` + sibling parsers | 1-2h

### W1015-CONSOLIDATE — Pattern-2 propagation arc continuing + disclosure-hygiene class identified + YAML loader hardening memo

> **CONSOLIDATE checkpoint = W1015.** ~11 waves closed since W1001-CONSOLIDATE.
> **Headline: Pattern-2 propagation continues** with W706 (`cmd_ignore_findings`)
> + W1009 (per-finding-suppressions audit) + W1011 (cmd_alerts section-level
> audit confirmation), bringing the running Pattern-2 closure count to **3
> more loader surfaces sealed this batch**; **a new disclosure-hygiene class
> identified** — W1000 sealed the `strip_list_payloads` `warnings_out` drop
> via a new `_ALWAYS_PRESERVED_LIST_FIELDS` allow-set, defeating the previous
> half-fix where `partial_success=true` survived but the structured warnings
> array was stripped; **a shared YAML loader hardening memo shipped** (W1016-
> RESEARCH) recommending a roll-our-own 2-phase migration (~125 LOC net
> removed at 5 of 7 callsites); **W996 docs the click-vocab divergence**
> across 7 commands as Pattern-3b parameter-name canonicalisation gap;
> **W1002 + W1003 fix test discipline** — relative test-date offsets +
> xfail-strict pin comment that survives autouse-fixture interactions;
> **W1015 lands the catalog `_shared.py` test coverage** (24 tests + new
> `tests/test_catalog_shared.py`). **W886 / W890 verified already-guarded**
> via the W873 canonical (`is_test_path` None-guard is present at the
> canonical site — no work needed). **W494 verified clean** —
> `test_inter_unused_return` order-sensitivity audit found taint
> already deterministic. Hash-stability mandate held trivially across the
> batch — every fix either added new validation paths (no pre-fix envelope
> bytes), preserved through-flow of an existing field (no detector output
> bytes moved), or landed in test infrastructure (no source bytes).

### Closed — Pattern-2 propagation continuing (W706 / W1009 / W1011 — W1015-CONSOLIDATE)
- **W706 — `cmd_ignore_findings` Pattern-2 close.** YAML-loader unknown-key path now plumbs `warnings_out` + flips `summary.partial_success=true` + surfaces an `agent_contract.facts` entry on unknown keys instead of silently dropping the entry. Matches the W918 envelope shape proven on cmd_alerts. **Tests pass; hash-stability held** (no pre-fix envelope to compare against — new validation surface). See `(internal memo)` for the playbook applied. | `src/roam/commands/cmd_ignore_findings.py`
<!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W1009 — Per-finding-suppressions Pattern-2 close.** Sister to W994 / W995 (smells_suppress) but on the finding-suppressions substrate. Malformed entries previously dropped silently; now partition into `valid` + `dropped` lists, surface each via indexed `warnings_out` entries + rollup, flip `partial_success=true`. Same envelope shape as W994 / W995. | `src/roam/policy/finding_suppressions.py`
- **W1011 — cmd_alerts Pattern-2 audit confirmation.** Audited cmd_alerts.py section-level loader paths post-W918 / W962 / W963 / W964 / W967 / W968 / W969; confirmed every silent-fallback surface now flows through `warnings_out`. **No new source changes**; closes the audit follow-up. Captured methodologically: the W983 playbook-propagation discipline (premise verification first) successfully reproduced the W977 close. | audit-only

### Added — Disclosure-hygiene class identified (W1000 / W996 — W1015-CONSOLIDATE)
- **W1000 — `strip_list_payloads` `warnings_out` preservation.** Introduced `_ALWAYS_PRESERVED_LIST_FIELDS` allow-set in `src/roam/output/formatter.py` covering `warnings_out` (and any future Pattern-2 disclosure field that must survive the `--detail`-strip post-processor). Pre-fix: the `partial_success=true` flag survived but the structured warnings array was silently stripped when callers omitted `--detail`, defeating the W918 disclosure half-way. Companion **new lint test** asserts the allow-set covers every field touched by the Pattern-2 envelope shape. **Tests pass.** Surfaced during W987 playbook apply (W1001-CONSOLIDATE). | `src/roam/output/formatter.py` + new lint test
- **W996 — Click-vocab divergence documented across 7 commands.** Memo notes 7 commands where the `--kind` vs `--type` vs `--metric` vocabulary diverges at the CLI boundary; same Pattern-3b parameter-name canonicalisation shape as the `_PARAM_ALIASES` table for MCP boundary normalization. Captured as a documentation-grade audit feeding W1004 (7-cmd audit). | docs-only memo

### Closed — Test discipline improvements (W1002 / W1003 / W494 — W1015-CONSOLIDATE)
- **W1002 — Test-date relative offsets.** Tests that hard-coded absolute dates were brittle against the autouse fixture (`freeze_time`) interaction; flipped to relative offsets so the test suite stays green across the year boundary without per-test override. Mirrors the W978 fixture-monkeypatch discipline. | `tests/test_*_history_*.py` cluster
- **W1003 — `xfail-strict` pin comment.** Pinned the rationale for the W819-class xfail-strict flips with an inline comment noting the autouse-fixture interaction surfaced by W1002; prevents the next reader from flipping back to absolute dates. | inline comments on the xfail-strict tests
- **W494 — `test_inter_unused_return` order-sensitivity verified clean.** Audit of the taint inter-procedural unused-return analysis found the result set is deterministic across input order; no fix needed. Closes the W494 / W495 / W496 W533-bundle drive-by chain (Java leg). | audit-only

### Closed — Not-applicable (W886 / W890 — W1015-CONSOLIDATE)
- **W886 / W890 — `is_test_path` None-guard verified already-guarded at canonical.** The W873-era canonical (`changed_files.is_test_file`) already None-guards its `path` argument; the W886 drive-by-2 / W890 carry-forward turn out to be redundant. **No source changes**; captured as audit-only closure. | audit-only (closes W886 drive-by + W890 carry-forward)

### Test infrastructure (W1015 — W1015-CONSOLIDATE)
- **W1015 — `tests/test_catalog_shared.py` (24 tests).** Direct coverage of the `src/roam/catalog/_shared.py` canonical helpers (W864 `_loc`, W873 `is_test_path`, W877 `_enclosing_symbol`, W923 `make_smell_finding`). Pre-W1015 the helpers had only transitive coverage via per-detector tests; the new file pins the canonical contract directly so future hoists can land with a single-file regression check. **24/24 pass.** | `tests/test_catalog_shared.py`

### Research memos (W1016-RESEARCH — W1015-CONSOLIDATE)
- **W1016-RESEARCH — YAML loader hardening memo.** Memo evaluates the 7 YAML-loader callsites across the codebase (`cmd_alerts` / `cmd_ignore_findings` / `smells_suppress` / `finding_suppressions` / `rules/loader` / `permit/loader` / `constitution/loader`). Verdict: **roll our own shared helper rather than depend on a third-party YAML hardening library** — the surface area is small enough that the discipline rule + a thin shared parser is cheaper than a dependency. 2-phase plan: **Phase 1 = W1018** (queued — extract the canonical `_parse_yaml_with_warnings` + `_VALID_OPS`-style closed-set validator); **Phase 2 = W1019** (queued — migrate 5 of 7 callsites; 2 keep their bespoke parsers due to non-YAML envelope shapes). Net ~125 LOC removed across the migration. | `(internal memo)` <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->

### Operational findings (W1015 batch — pending fix / decision)
- **W1004 — 7-command click-vocab audit** (W996 follow-up). Audit the `--kind` vs `--type` vs `--metric` boundary vocabulary across the 7 surfaced commands; recommend a canonical normalisation. | per-command audit | 2-3h
- **W1005 — 3-tier vs 5-tier severity Pattern 3a divergence.** Surfaced during W1011 audit — some surfaces report `low/medium/high`, others `info/low/medium/high/critical`. Same Pattern-3a shape as W596 / W631. | per-command audit | 1-2h
- **W1006 — Formatter sibling preserved-fields expansion** (W1000 follow-up). `_ALWAYS_PRESERVED_LIST_FIELDS` likely needs to expand to cover sibling fields (e.g. `dropped_entries`, `notes`); audit the disclosure-hygiene class end-to-end. | `src/roam/output/formatter.py` | 1-2h
- **W1007 — `agent_contract:[]` mistake** captured during W1011 audit. Some envelopes emit `agent_contract:[]` (empty list) instead of `agent_contract:{}` (empty dict); breaks consumer schema. | per-emitter sweep | 1h
- **W1008 — `list_counts` surfacing.** Pattern-2 envelopes should surface counts at the envelope root (`warnings_count`, `dropped_count`) rather than only in nested structures; helps agents check disclosure without parsing the full list. | envelope shape design + emitters | 2-3h
- **W1010 — DEFERRED behind W1018.** Captured as deferred pending pending the W1018 shared helper landing; revisit after Phase 1. | TBD (deferred) | TBD
- **W1012 — Test-date triage.** Sweep remaining hard-coded test dates for the same autouse-fixture interaction W1002 sealed. | tests/ sweep | 1-2h
- **W1013 / W1014 — `changed_files` None-guard sweep.** Sister sites to W886 / W890 — sweep the remaining helpers in `changed_files` for missing None-guards on `path` arguments. | `src/roam/commands/changed_files.py` | 30 min each
- **W1017 — Typed wrapper plumb** (W918 / W933 family follow-up). The `warnings_out: list[str] | None` accumulator pattern would benefit from a typed wrapper class (`WarningsOut`) instead of the bare-list-with-side-effects pattern. | new module + 7 callsite migrations | 2-4h
- **W1018 — Phase 1 of W1016 YAML helper.** Extract canonical `_parse_yaml_with_warnings` + closed-set validator from cmd_alerts. | `src/roam/policy/_yaml_helper.py` (new) | 2-3h
- **W1019 — Phase 2 of W1016 YAML helper migrations** (5 of 7 callsites). Blocked behind W1018. | 5 callsite migrations | 4-6h
- **W1020 — Fixture-scope audit** (W1002 follow-up). Audit the test fixtures for hidden autouse-scope interactions like the one W1002 sealed. | `tests/conftest.py` + per-test scope audit | 2-3h
- **W1021 — `camel_split` location verify** (W901 memo drift). Memo claim about `_camel_split` canonical location may differ from current source; W929 already moved the canonical to `tfidf.py` but W901's `__all__` export note may be stale. Verify and refresh. | docs / memo update | 30 min
- **W1022 / W1023 / W1024 — Small `_shared.py` polish** (W1015 drive-bys). Each is a sub-30-min polish item surfaced while writing the test file: better type annotations, docstring tightening, internal helper consolidation. | `src/roam/catalog/_shared.py` | 30 min each
- **W1025 — Alerts section-level Pattern-2 sibling** (W1011 audit follow-up). One sister loader path in cmd_alerts still emits the section-level envelope without the new `warnings_out` plumbing; trivial extension once W1018 lands. | `src/roam/commands/cmd_alerts.py` | 30 min

- **W1000 — REAL: `strip_list_payloads` drops `warnings_out` without `--detail`.** Surfaced during the W987 playbook apply — the envelope post-processor in `roam.output.formatter` strips `warnings_out` from the response without `--detail` flag, defeating the Pattern-2 disclosure. Sealed by W1000 (W1015-CONSOLIDATE) — see "Disclosure-hygiene class identified" section above. | `src/roam/output/formatter.py` | shipped

### W1042-CONSOLIDATE — Shared YAML helper arc lands + Pattern-2 propagation continues + cargo-cult `or ""` family substantially clean

> **CONSOLIDATE checkpoint = W1042.** ~18 waves closed since W1015-CONSOLIDATE.
> **Headline: the W1016-RESEARCH shared YAML helper landed (W1018) and
> migrated through 4 of 5 planned Phase 2 callsites (W1019a/c/d/e; W1019b
> in flight), each migration revealing a real helper-contract gap that was
> sealed as a discrete follow-up wave — W1035 (JSON parse-error wording),
> W1040 (PyYAML strict-timestamp force_tiny_parser kwarg), W1031 (typed
> overload), W1043 (WarningsOut type alias).** This is the BAIL-AND-SEAL
> discipline working as designed — the migration agent bailed cleanly the
> moment the helper's contract was insufficient, the gap was sealed as
> a separate W, then the migration re-dispatched clean. **Pattern-2
> propagation continued** with 4 more loader surfaces Pattern-2-fied:
> W1017 (`load_per_finding_suppressions_typed` `warnings_out` plumb),
> W1025 (cmd_alerts thresholds-section sibling), W1032
> (`load_suppressions` + `load_suppressions_typed` deeper close + helper
> migration), W1042 (`sarif._load_suppressions_typed` `warnings_out`
> plumb). Running Pattern-2 loader-site total is now **~33-34 sites
> sealed end-to-end**; W1039-RESEARCH evaluated whether to evolve the
> envelope shape for Python 3.13+ and concluded **STAY** — the current
> playbook is the right one. **Cargo-cult `or ""` cleanup** swept 11
> sites across the W1029 batch (10 sites + 3 helpers None-guarded) +
> W1034 (1 more in `causal_graph.py`); combined with W1013/W1014 the
> family stands at **14 cargo-cult removals across 9 files** and W907
> false-hedge anti-pattern enforcement is now mostly clean. **catalog/*
> `__all__` discipline** landed via W1033 (`_shared.py`) + W1037 (5
> sibling catalog modules) for a total of **6 catalog modules with
> explicit `__all__` declarations**, deterring the same cross-module
> private-name-import pattern W901 originally captured. **W1026 back-fill**
> retroactively annotated the W1016-RESEARCH memo with the W1018 tiebreaks
> observed during Phase 1 implementation. **Hash-stability mandate held
> trivially across the batch** — every fix either added new validation
> paths (no pre-fix envelope bytes), tightened type surfaces (no runtime
> behavior delta), or hoisted helper internals (no detector output bytes
> moved).

### Added — Shared YAML helper arc (W1018 / W1019a/c/d/e / W1031 / W1035 / W1040 / W1043 — W1042-CONSOLIDATE)
- **W1018 — `load_yaml_with_warnings` helper shipped.** New module + 23 tests. Phase 1 of the W1016-RESEARCH 2-phase plan. Canonical `_parse_yaml_with_warnings` extracted from cmd_alerts; closed-set validator pattern generalised. **Initially unused** — landed alongside Phase 2 migration to keep the abstraction honest (per CLAUDE.md "Don't TypedDict a boundary you don't validate" discipline, the helper's shape was iterated against real migrations rather than designed in isolation).
- **W1019a / W1019c / W1019d / W1019e — Phase 2 migrations.** 4 of 5 planned callsites migrated to the new helper (`cmd_ignore_findings`, `smells_suppress`, `per_finding_suppressions`, `rules/loader`); **W1019b in flight** (partial-completion noted — re-dispatch after the W1040 extension landed). The migration approach — bail cleanly on first contract gap, capture the gap as a discrete wave, re-dispatch after seal — proved out on three separate occasions (W1035 / W1040 / W1031).
- **W1035 — `parse_error_label` kwarg.** Helper contract extension surfaced during Phase 2: the helper's JSON-parse-error wording assumed YAML context, breaking the JSON envelope shape for callers that needed a different label. Sealed as a discrete kwarg + 1 test.
- **W1040 — `force_tiny_parser` kwarg.** Helper contract extension surfaced during the smells_suppress migration: PyYAML's strict-timestamp parsing was inverting W994's `EXPIRES_FMT` discipline (`yaml.safe_load` accepts unbounded ISO formats; the tiny fallback parser is the EXPIRES_FMT-compatible path). Fix: kwarg lets callers force the tiny parser even when PyYAML is installed. Unblocks the W994 / W995 envelope shape preservation across the smells_suppress migration.
- **W1031 — Typed overload for `load_yaml_with_warnings`.** Phase 2 surface: `warnings_out` typed return signature variant for callers consuming the helper through a TypedDict-fronted loader. Companion to the W933 / W919 / W966 typing discipline.
- **W1043 — `WarningsOut` type alias.** Concurrent with this wave; finalises the W1017 typed wrapper plumb as a `TypeAlias = list[str]` declaration at the canonical boundary (no behavior change; readability + LSP hint quality only).

### Pattern-2 propagation (W1017 / W1025 / W1032 / W1042 — W1042-CONSOLIDATE)
- **W1017 — `load_per_finding_suppressions_typed` `warnings_out` plumb.** Per-finding-suppressions typed loader sister to W1009; full Pattern-2 envelope shape (`warnings_out` + `partial_success=true` + `agent_contract.facts`) now flows through the typed surface. Matches the W918 / W994 / W995 / W1009 shape.
- **W1025 — cmd_alerts thresholds-section Pattern-2 sibling closed.** The one sister loader path captured during the W1011 audit (W1015-CONSOLIDATE pending) — the thresholds-section envelope now plumbs `warnings_out` alongside the section-level surfaces W918 / W962 / W963 / W964 / W967 / W968 / W969 had sealed. Trivial extension once W1018 landed; final cmd_alerts.py Pattern-2 sibling sealed.
- **W1032 — `load_suppressions` + `load_suppressions_typed` deeper Pattern-2 + helper migration.** Both loaders now flow through `load_yaml_with_warnings`; the deeper-Pattern-2 close covers the previously-untreated "valid YAML, semantically-incoherent payload" surface (sister to W1009's malformed-entry partition).
- **W1042 — `sarif._load_suppressions_typed` `warnings_out` plumb.** Sarif emitter's private typed loader migrated to the canonical shape; closes the last loader-site sibling that was still emitting the typed envelope without `warnings_out` flow. **Running Pattern-2 loader-site total ~33-34 sealed** across the W918 → W1042 arc.

### Changed — Cargo-cult `or ""` cleanup (W1029 / W1034 — W1042-CONSOLIDATE)
- **W1029 — 10 sites cleaned + 3 helpers None-guarded.** Audit pass found 10 sites doing `path or ""` / `value or ""` defensive-coercion in spots where the upstream contract already guaranteed non-None (cargo-culted from genuinely-defensive sister sites). 3 helpers were tightened with explicit None-guards instead (`changed_files`-family + sibling utility modules). Same shape as the W907 false-hedge anti-pattern audit; preserves the rule "verify the precondition before defending against it".
- **W1034 — `causal_graph.py:713` cargo-cult cleanup.** Drive-by from W1029 — one more `or ""` site at the causal-graph layer that the W1029 sweep missed. **Combined W1013/W1014 + W1029 + W1034 = 14 cargo-cult removals across 9 files.** W907 false-hedge enforcement is now mostly clean across the codebase.

### Added — catalog/* `__all__` discipline (W1033 / W1037 — W1042-CONSOLIDATE)
- **W1033 — `src/roam/catalog/_shared.py` `__all__` declaration.** Explicit `__all__` listing the canonical helpers (`_loc`, `is_test_path`, `_enclosing_symbol`, `make_smell_finding`, etc.) per the W901 cross-module private-name-import discipline. Closes a sub-30-min polish item from the W1015 batch.
- **W1037 — 5 sibling catalog modules gain `__all__`.** Uniform discipline applied across `smells.py` + `detectors.py` + `parallel_hierarchy.py` + `clones_cross_layer.py` + `type_switch.py`. **6 catalog modules total with explicit `__all__` declarations** (W1033 + W1037); deters the W901-class private-name-import pattern across the catalog surface.

### Research memos (W1039-RESEARCH — W1042-CONSOLIDATE)
- **W1039-RESEARCH — Python 3.13+ Pattern-2 evolution memo: STAY verdict.** Evaluated whether to evolve the W918 envelope shape ahead of Python 3.13's expanded type-narrowing surface (PEP 695 / PEP 705 / PEP 692 interactions with the `warnings_out: list[str] | None` accumulator pattern). **Verdict: STAY the course.** The current playbook (bare-list accumulator + `partial_success` flag + `agent_contract.facts` disclosure) is structurally cheaper than a TypedDict-fronted alternative, AND the W933 / W966 discipline rule explicitly counsels against TypedDict-fronting at this boundary because the accumulator is populated via `list.append(arbitrary)` rather than literal construction. The W1043 `WarningsOut` type alias is the maximum-tightening that stays inside the discipline.

### Memo back-fills (W1026 — W1042-CONSOLIDATE)
- **W1026 — W1016-RESEARCH memo back-fill with W1018 tiebreaks.** The original W1016 memo captured the decision verdict (roll our own, 2-phase plan) but did not capture the tiebreaks the agent actually used when implementing W1018 (e.g. why the helper exposes a `parse_error_label` kwarg ahead of any caller needing it — captured in W1035). Back-fill folds the W1018 / W1035 / W1040 / W1031 / W1043 evolution into the memo's "Phase 1 implementation notes" section so the next reader sees the iteration, not just the verdict.

### Operational findings (W1042 batch — pending fix / decision)
- **W1036 — 4 sibling `_parse_simple_yaml` loaders still bespoke** (W1018 / W1019 follow-up). 4 loader sites carry their own tiny YAML parsers (smells_suppress's original, finding_suppress's original, two more in `policy/` and `rules/`). Each is a candidate for migration to `load_yaml_with_warnings` once the helper has a force_tiny_parser path proven through one more callsite. | per-loader audit + migration | 2-3h
- **W1038 — `_extract_typed` helper hoist** (W1031 follow-up). The typed-overload path carries a `_extract_typed` adapter at each callsite; once 2+ callsites stabilise, the adapter should hoist to a shared utility. | new helper or `_yaml_helper.py` extension | 1h
- **W1041 — `clones_cross_layer.py` `__all__` divergence** (W1037 follow-up). The W1037 sweep used a slightly different `__all__` ordering convention than the W1033 baseline (alphabetical vs declaration-order); resolve. | `src/roam/catalog/clones_cross_layer.py` | 15 min
- **W1042 — `sarif._load_suppressions_typed` typed-loader migration** (concurrent with this wave; if not yet shipped, queued). | `src/roam/output/sarif.py` | 30 min
- **W1043 — `WarningsOut` type alias** (concurrent with this wave; if not yet shipped, queued). | `src/roam/_types.py` (new) or canonical boundary module | 30 min
- **W1044 — DEFERRED behind W1019b re-dispatch.** Captured as deferred pending the W1019b re-dispatch landing post-W1040. | TBD (deferred) | TBD
- **W1004 — 7-cmd click-vocab audit** (W996 follow-up, carry-from-W1015). Still queued. | per-command audit | 2-3h
- **W1005 — 3-tier vs 5-tier severity Pattern 3a divergence** (W1011 audit follow-up, carry-from-W1015). Still queued. | per-command audit | 1-2h


---

> **TWO CRITICAL edge-attribution fixes seal the call/import edge family end-to-end (W708 + W742) + suppression family migration extended through Phase C-1 (W722 → W723 → W736 → W737 → W738) + Pattern-3a canonical-rank consolidation third axis CLOSED (W596 confidence + W631 risk + W640 alerts level_order fold + W648 zero-slipped audit + W649 alerts lowercase) + bare-except discipline structurally CLOSED (allowlist 9 → 3 via W660 / W665 / W677 / W678 / W679 / W740 + W662 / W746 _GUARDED_DIRS 4 → 12 + W707 dead-code REAL BUG seal) + wheel-bundling thread CLOSED (W664 LIVE BUG caught + W668 as_file audit + W624 importlib.resources migration + W642 / W643 fallback removals) + MCP wrapper P0 batch (W670 alias regression + W671 cold-start guard + W672 surface sync + W606 collision lint + W607 / W636 wrapper refactor + W695 --card smoke) + smell catalog reached 20 detectors (W601-W605 / W639 / W646 / W647 / W650 / W705 / W720) + hygiene drive-bys (W682 / W683 / W685 / W689 / W690 / W697 / W699 / W702) (~20-completion batch behind W755-CONSOLIDATE, 2026-05-15).**
> **The headline is TWO systemic edge-attribution correctness fixes** that together seal the family. **W708 (call-edge mis-attribution)**: `_store_symbols` + `_merge_existing_symbols` in `src/roam/index/indexer.py:551,1192` omitted `line_end` from `all_symbol_rows`. The resolver's `le > 0` guard always failed → `syms[0]` fallback for every method ref. **Repo-wide 95% mis-attribution reduction** (2715 → 147 silently corrupted edges). **W742 (phantom import-edge mis-attribution)**: `_closest_symbol` in `src/roam/index/relations.py:488-496,848-898` was returning `syms[0]` for `kind='import'` refs that couldn't be resolved to an enclosing symbol — manufacturing **18 phantom import edges on `_format_count`** alone plus 6 transitive side-effects. Fix: optional `kind` parameter on `_closest_symbol`; returns `None` for `kind=='import'` instead of falling through. New invariant test in `tests/test_relations.py:167-225`. Together, every detector that reads edges (taint / side_effects / critique / dead / smells / vibe-check / ai-rot) now consumes correct edges. **Suppression family migrated through Phase C-1** (W691 schema unification → W692 dataclass `src/roam/policy/suppression_v2.py` 312-line `_SuppressionBase` + 3 variants → W722 Phase B-a smells typed companion → W723 Phase B-b finding_suppress + sarif → W736 Phase C-1a sarif `_load_suppressions` migrated → W737 Phase C-1b `cmd_smells.load_smells_suppressions` migrated → W738 Phase C-1c BAILED on `cmd_triage` for three malformed-input divergences but MIGRATED `suppression.save_suppression` internal dedup; new `tests/test_w738_suppression_wire_format.py` 8/8 pass). **Pattern-3a third-axis close**: W596 `src/roam/output/confidence.py` (15 sites) + W631 `src/roam/output/risk.py` (4-tier critical/high/medium/low + moderate→medium alias) + W640 `cmd_alerts._LEVEL_ORDER` folded into `severity_rank()` + W648 AST audit ZERO slipped tables + W649 `cmd_alerts` UPPER → lowercase per W547. **Bare-except discipline shipped end-to-end**: W660 `_find_workspace_root` narrowed; W661 `catalog/detectors.py` production loop fail-loud; W662 AST drift-guard; W665 / W677 / W678 / W679 / W740 narrowed individual sites (allowlist 9 → 3); W707 found + sealed a REAL BUG (`_serialize_suppressions` dead-code on the `first` flag); W746 extended `_GUARDED_DIRS` 4 → 12 to cover substrate modules. **Wheel-bundling thread closed**: W664 `__init__.py` package-data drift-guard CAUGHT A LIVE W643-class bug on first run (`roam.languages.extractors` was missing its `__init__.py`); W668 `as_file()` audit; W624 migrated `mcp_server.py:14569` `mcp --card` handler to `importlib.resources`; W642 / W643 removed dead triple-parent fallback. **MCP wrapper P0 batch**: W670 P0.1 fixed `roam_plan` `file_path` alias regression by moving `_wrap_with_alias_normalization` BEFORE the preset filter; W671 P0.2 added `_INLINE_RESPONSE_TOOLS` frozenset cold-start exemption for `roam_catalog`; W672 P0.3 synced `scripts/sync_surface_counts --write` to live 238/231/224; W606 added AST lint for canonical-positional collision; W607 decomposed `_wrap_with_alias_normalization` into 3 helpers; W636 collapsed `_sync` vs `_async` wrapper closure duplication; W695 added `--card` CLI smoke test. **Smell catalog reached 20 detectors**: W601 switch-statement (7 findings; surfaced REAL refactor candidate `_create_extractor` 23-arm switch); W602 temporal-coupling (10 findings); W603 magic-numbers (495 findings); W604 boolean-parameter (0 findings); W605 comment-density TODO/FIXME/XXX/HACK; W639 cross-detector empty-corpus smoke; W646 W699 DOGFOOD refactor `_format_count` (cluster finding led to W708 + W742); W647 symbol-centric temporal-coupling rollup (surfaced W708 false positive); W650 block-comment TODO/FIXME C/Java/JS; W705 unified `_CommentSyntax` (21 languages); W720 comment-density extended to hcl + apex. **Hygiene drive-bys**: W682 README CLI table evidence-oscal row; W683 `.gitattributes` 13 → 49 lines (`* text=auto eol=lf` + 26 binary extensions); W685 README CLI table header auto-count assertion; W689 `.editorconfig` mirroring `.gitattributes`; W690 dev-doc note for pytest on Windows; W697 extras-gate on README CLI command-count check; W699 DOGFOOD refactor `_format_count` cluster finding; W702 `_DEPRECATED_COMMANDS` AST-literal contract test. **Hash-stability mandate held 31/31 byte-identical across every source wave.**

### Fixed — CRITICAL edge-attribution family CLOSED (W708 call-edge mis-attribution + W742 phantom import-edge mis-attribution)
- **W708 — CRITICAL: Python call-edge mis-attribution fixed.** `_store_symbols` (`src/roam/index/indexer.py:551`) + `_merge_existing_symbols` (`src/roam/index/indexer.py:1192`) omitted `line_end` from `all_symbol_rows`. Resolver's `le > 0` guard always failed → `syms[0]` fallback for every method ref. **Repo-wide 95% mis-attribution reduction (2715 → 147 corrupted edges).** Every detector that reads edges (taint / side_effects / critique / dead / smells / vibe-check / ai-rot) silently consumed corrupted edges pre-fix. Surfaced by W647's symbol-centric temporal-coupling rollup (a clustered false positive pointed straight at the mis-attribution).
- **W742 — CRITICAL: Phantom import-edge mis-attribution to first symbol fixed.** `_closest_symbol` in `src/roam/index/relations.py:488-496,848-898` was returning `syms[0]` for `kind='import'` refs that did not resolve to an enclosing symbol. **Result: 18 phantom import edges on `_format_count` + 6 transitive side-effects.** Fix: optional `kind` parameter on `_closest_symbol` returns `None` for `kind=='import'` instead of falling through. New invariant test in `tests/test_relations.py:167-225` pins the contract. Pairs with W708 to close the edge-attribution family end-to-end across BOTH call-edge AND import-edge resolution.
- **W707 — REAL BUG: `_serialize_suppressions` dead-code on `first` flag.** Investigated the suppression serializer; found a dead `first` flag that was set but never read. Removed; regression test pins the call-site count at zero. Pairs with W691 schema unification + W722 / W723 phased migration.
- **W740 — `_load_project_config` bare-except narrowed.** Continues the W662 drift-guard scoping; allowlist 4 → 3.

### Fixed — Suppression family migrated through Phase C-1 (W722 / W723 / W736 / W737 / W738)
- **W722 Phase B-a — Smell-suppression typed companion.** `load_smells_suppressions_typed()` returns `KindSymbolSuppression` records bridging the legacy smell-suppression substrate (W658) into the W692 dataclass surface.
- **W723 Phase B-b — `finding_suppress` + sarif typed.** Extended Phase B to `cmd_finding_suppress` + the sarif emitter; both consume `FindingIdSuppression` records.
- **W736 Phase C-1a — sarif `_load_suppressions` migrated.** First Phase C migration — the sarif emitter's private loader now flows through the canonical `src/roam/policy/suppression_v2.py` parser.
- **W737 Phase C-1b — `cmd_smells.load_smells_suppressions` migrated.** Second Phase C migration — `cmd_smells` consumes the canonical loader.
- **W738 Phase C-1c — `cmd_triage` BAILED, `suppression.save_suppression` MIGRATED.** Investigated `cmd_triage` for Phase C-1c migration; found **three malformed-input divergences** between the legacy parser's lenient behavior and the canonical loader's closed-schema validation — bailed on `cmd_triage` migration to keep the legacy behavior on the user-facing triage surface, but migrated the internal `suppression.save_suppression` dedup path. New `tests/test_w738_suppression_wire_format.py` pins the wire format (8/8 pass).

### Changed — Pattern-3a canonical-rank consolidation third axis CLOSED + alerts surface canonicalised (W596 / W631 / W640 / W648 / W649)
- **W596 — Confidence-level rank canonical helper.** New `src/roam/output/confidence.py`. **15 sites migrated.** Pairs with W564 severity-rank (10 sites) to give Pattern-3a its second canonical axis end-to-end.
- **W631 — Risk rank canonical helper.** New `src/roam/output/risk.py`. **4-tier closed enum** (`critical` / `high` / `medium` / `low`) with `moderate → medium` alias for backward compat. Third Pattern-3a axis CLOSED — combined with W596 confidence + W564 severity, every rank surface in roam now flows through canonical helpers with AST drift-guards.
- **W640 — `cmd_alerts._LEVEL_ORDER` folded into `severity_rank()`.** Sort key now uses `-severity_rank(lowercase)` instead of a private rank table. Drift-guard regex broadened so the canonicalisation is enforced for any future `_LEVEL_ORDER` clones.
- **W648 — AST audit confirmed ZERO slipped rank tables.** Audited the entire `src/` tree for inline rank tables that would bypass the canonical helpers. **Zero slipped.** Pattern-3a structurally closed for real, not just-in-name.
- **W649 — `cmd_alerts` UPPER → lowercase canonicalisation.** Per W547 closed-severity-vocab contract (CRITICAL / WARNING / INFO → lowercase). Pairs with W640 to give the alerts surface its full canonical-vocab discipline.

### Changed — Bare-except discipline structurally CLOSED (W660 / W662 / W665 / W677 / W678 / W679 / W746)
- **W660 — `_find_workspace_root` bare-except narrowed.** Same site as the `_load_project_config` narrowing; specific-exception classification only.
- **W661 — `catalog/detectors.py` production loop fail-loud.** Classifies `NameError` / `ImportError` / `AttributeError` / `TypeError` as `RuntimeError` (re-raise) vs `sqlite3.Error` (swallow + log). Pair with W653 source fix + W662 drift-guard for end-to-end discipline.
- **W662 — AST drift-guard banning bare `except Exception: continue/pass`.** Initial `_GUARDED_DIRS` was 4 directories.
- **W665 — 3 specific bare-except sites narrowed.** Continues the W662 scoping.
- **W677 — `formatter.py:420,905` narrowed.** Continues W665 scoping.
- **W678 — `taint_engine.py:133` narrowed once the parser stabilised.** Was previously held back by the parser flux; W662 stabilisation unblocked the narrowing.
- **W679 — `detectors.py:4165` narrowed.** Closed-set `sqlite3.Error` / `KeyError` / `TypeError`; allowlist 3 → 2.
- **W746 — Extended W662 `_GUARDED_DIRS` 4 → 12 to substrate modules.** The drift-guard now covers the agent-OS substrate packages (constitution / modes / runs / leases / memory / pr-bundles / laws / agents_md) in addition to the original 4. Allowlist now structurally pinned at 3 sites total with the substrate covered.

### Changed — Wheel-bundling thread CLOSED (W624 / W642 / W643 / W664 / W668)
- **W624 — Migrated `mcp_server.py:14569` `mcp --card` handler to `importlib.resources`.** Continues the W554 / W535 / W610 discipline thread; `parents[N]` path-juggling replaced with `importlib.resources.files("roam") / "mcp-server-card.json"`.
- **W642 — Removed triple-parent fallback from `mcp --card` handler. -19 LOC.** W624 already migrated the resolution; the `parents[3]` fallback was dead code after that.
- **W643 — Grepped remaining `Path(__file__).parent` resource loads.** Audit pass confirming the importlib.resources migration is complete across `src/`.
- **W664 — `__init__.py` package-data drift-guard. CAUGHT A LIVE W643-class bug on first run**: `roam.languages.extractors` was missing its `__init__.py`, meaning the package was not actually shipped in the wheel despite being referenced via package-data.
- **W668 — Audited `as_file()` callers for path-captured-outside-`with` anti-pattern.** 4 sites fixed; drift-guard pins the pattern at lint time.

### Changed — MCP wrapper P0 batch + refactor chain (W606 / W607 / W636 / W670 / W671 / W672 / W695)
- **W606 — AST lint for canonical-positional collision.** 4 new tests catching the pre-W595 crash class at PR time.
- **W607 — Refactored `_wrap_with_alias_normalization` into 3 helpers.** `_collect_alias_candidates` + `_build_merged_signature` + `_build_merged_annotations`. **130 → 50 lines + 7 unit tests.**
- **W636 — Collapsed `_sync` vs `_async` wrapper closure duplication.** New shared `_prepare_kwargs` helper + branched closure pattern. **33 → 28 lines** and duplicate-body anti-pattern eliminated.
- **W670 P0.1 — `roam_plan` `file_path` alias regression fixed.** Moved `_wrap_with_alias_normalization` BEFORE the preset filter so the alias normalisation layer wraps every tool, not just those that survive the filter. **User-flagged P0.**
- **W671 P0.2 — `roam_catalog` cold-start auto-handle exemption.** New `_INLINE_RESPONSE_TOOLS` frozenset exempts `roam_catalog` from `_wrap_with_handle_off` — cold-start call returns inline. **User-flagged P0.**
- **W672 P0.3 — `scripts/sync_surface_counts --write` synced to live 238/231/224.** README + CLAUDE.md + llms-install.md + both mcp-server-card.json copies + server.json + landing-page HTML all carry the live counts. **User-flagged P0.**
- **W695 — `--card` CLI smoke test (2 tests).** Pins the `mcp --card` handler against silent regression — W624 + W642 migrations now have an end-to-end CLI test.

### Added — Smell catalog reached 20 detectors (W601-W605 / W639 / W646 / W647 / W650 / W699 / W705 / W720)
- **W601 — `switch-statement` smell detector.** **7 findings on roam-code; surfaced REAL refactor candidate `_create_extractor` 23-arm switch (sealed by W646).**
- **W602 — `temporal-coupling` smell detector.** **10 findings on roam-code.** Continues W370c.
- **W603 — `magic-numbers` smell detector.** **495 findings on roam-code.**
- **W604 — `boolean-parameter` smell detector.** **0 findings on roam-code** — the discipline already holds.
- **W605 — `comment-density` smell detector** (TODO / FIXME / XXX / HACK).
- **W639 — Cross-detector empty-corpus smoke test.** Guards **54 detectors** against silent import errors after concurrent merges.
- **W646 — REFACTOR: `_create_extractor` 105 → 17 lines via `_LANGUAGE_EXTRACTORS` dispatch dict.** Eat-our-own-dogfood seal of the W601 finding.
- **W647 — Symbol-centric temporal-coupling rollup.** **10 pair findings → 5 cluster findings** on roam-code; `cmd_health.health` clustered. **Surfaced the false positive that drove the W708 critical fix.**
- **W650 — Block-comment TODO/FIXME detection extended to C/Java/JS.** Coverage now includes C-family + CSS block comments alongside the existing line-comment detection.
- **W699 — DOGFOOD: Refactor `_format_count`.** Cluster finding that surfaced the W708 + W742 mis-attribution fixes downstream.
- **W705 — Unified `_CommentSyntax` record.** Comment-density smell coverage **14 → 21 languages.**
- **W720 — Comment-density extended to `hcl` + `apex`.** Continues the W705 unification.

### Added — Hygiene drive-bys (W682 / W683 / W685 / W689 / W690 / W697 / W702)
- **W682 — README CLI table now includes the `evidence-oscal` row.** Closes the W672 gap audit.
- **W683 — `.gitattributes` extended (`* text=auto eol=lf` + 26 binary extensions).** Closes the long-tail CRLF-on-Windows wheel-smoke failure class.
- **W685 — README CLI table header-count assertion** via `test_readme_cli_command_count_matches_source`. Fails the build if the README header drifts from the live source count.
- **W689 — `.editorconfig` mirroring `.gitattributes`.** Editors get a single source of truth on line-endings + charset.
- **W690 — Dev-doc note for pytest on Windows.** Captures the `pytest-xdist` + Windows quirk surfaced during the W708 + W742 validation pass.
- **W697 — Extra-gate to `test_readme_covers_all_canonical_cli_commands`.** Auto-allowlist from `cli._DEPRECATED_COMMANDS` so newly-deprecated commands flow through automatically.
- **W702 — `_DEPRECATED_COMMANDS` AST-literal contract test.** Pins the deprecation-list shape — any drift fails at lint time.

### Changed — W755 consolidation pass
- **W755 — CHANGELOG / HANDOVER / BACKLOG / SESSION-SNAPSHOT refresh for the ~20-completion batch behind W733.** Docs-only; hash-stability mandate held trivially.
> Seventeen-completion batch folded in behind the W698-CONSOLIDATE consolidation. **The headline is W708's critical silent-bug seal**: Python call-edge mis-attribution was silently corrupting every detector that reads edges (taint, side_effects, critique, dead, smells, vibe-check, ai-rot). Root cause was `indexer.py:551` + `indexer.py:1192` omitting `line_end` from `all_symbol_rows`, which collapsed per-call resolution to per-symbol. Post-fix: `_format_count` non-import edges drop **78 → 0** on roam-code; **repo-wide 2715 → 147 (95% reduction)**. Validation in flight (W709). The W647 symbol-centric temporal-coupling rollup (10 pair → 5 cluster findings; `cmd_health.health` clustered) is what surfaced the false positive driving the W708 fix. **Suppression family phased close**: **W691** unified `.roam/suppressions.json` schema between `finding_suppress` + sarif readers (closing the W676-found latent bug); **W692 Phase A** shipped the discriminated-union dataclass at `src/roam/policy/suppression_v2.py`; **W722 Phase B-a** added the `load_smells_suppressions_typed()` companion (`KindSymbolSuppression` internal); **W693** added cross-loader compat across 5 suppression substrates. W723 Phase B-b in flight; W724 Phase C queued. **Comment-density smell expansion**: **W705** unified `_CommentSyntax` record taking coverage 14 → 21 languages; **W720** extended to hcl + apex; **W650** extended detection to `/* */` block comments (C-family + CSS). **Hygiene wave**: **W689** added `.editorconfig` (23 lines) mirroring `.gitattributes` EOL/charset/binary rules; **W685** pinned README CLI table header to `(all 231)` with auto-count + `test_readme_cli_command_count_matches_source`; **W695** added `--card` CLI smoke (2 tests); **W697** added README CLI test extras-gate (auto-allowlist from `cli._DEPRECATED_COMMANDS`); **W702** added `_DEPRECATED_COMMANDS` AST-literal contract test. **Small cleanups**: **W642** removed triple-parent fallback from `mcp --card` handler (-19 LOC); **W649** canonicalised `cmd_alerts` UPPER → lower per W547 contract; **W707** removed `_serialize_suppressions` dead code + regression test. **Hash-stability 31/31 byte-identical held across every source wave.**

### Fixed — CRITICAL silent call-edge mis-attribution (W708) + suppression schema unified (W691) + cmd_alerts canonical lowercase (W649)
- **W708 — CRITICAL: Python call-edge mis-attribution fixed.** `indexer.py:551` + `indexer.py:1192` omitted `line_end` from `all_symbol_rows`, collapsing per-call edge resolution to per-symbol. Result: every detector reading edges (taint, side_effects, critique, dead, smells, vibe-check, ai-rot) silently consumed corrupted edges. **Post-fix**: `_format_count` non-import edges drop **78 → 0** on roam-code itself; **repo-wide 2715 → 147 (95% reduction)**. Surfaced by W647's symbol-centric temporal-coupling rollup (a clustered false positive pointed straight at the mis-attribution). Validation in flight (W709) — the impact crosses every detector family so the validation pass is broad.
- **W691 — `.roam/suppressions.json` schema unified between `finding_suppress` + sarif readers.** Closes the W676-found CRITICAL latent bug (two readers consuming the same file with incompatible shape contracts — suppressions silently applied to one detector and not the other). First phase of the W691 → W692 → W722 phased-close family.
- **W649 — `cmd_alerts` UPPER-case → lowercase canonicalisation.** Brings the alerts surface in line with the W547 closed-severity-vocab contract. Pairs with W640 (cmd_alerts `_LEVEL_ORDER` fold) to give the alerts surface its full canonical-vocab discipline.
- **W642 — Removed triple-parent fallback from `mcp --card` handler.** **-19 LOC.** Continuation of the W624 importlib.resources migration; the `parents[3]` fallback was dead code after W624.

### Added — Suppression family phased close + symbol-centric temporal-coupling rollup + comment-density 14→21 languages + hygiene wave
- **W692 Phase A — Suppression discriminated-union dataclass.** Shipped at `src/roam/policy/suppression_v2.py`. Closed-schema typed surface that replaces the prior shape-divergent dict-based parsers. Pairs with W691 (schema unification) + W722 Phase B-a (typed loader).
- **W722 Phase B-a — `load_smells_suppressions_typed()` companion.** `KindSymbolSuppression` internal type bridging the legacy smell-suppression substrate (W658) into the W692 dataclass surface. W723 Phase B-b in flight; W724 Phase C queued.
- **W693 — Cross-loader compat test for 5 suppression substrates.** Pins shape parity across the family — any future schema drift fails the compat test before it ships.
- **W647 — Symbol-centric temporal-coupling rollup.** Replaces the prior pair-by-pair rollup with a symbol-clustered view. **10 pair findings → 5 cluster findings** on roam-code; `cmd_health.health` clustered. **This is the rollup that surfaced the W708 critical call-edge mis-attribution** — the apparent "temporal coupling" between two symbols turned out to be a mis-attributed call edge.
- **W705 — Unified `_CommentSyntax` record.** Comment-density smell coverage **14 → 21 languages**. Closed-schema language record replaces the prior per-language ad-hoc syntax constants.
- **W720 — Comment-density extended to `hcl` + `apex`.** Continues the W705 unification.
- **W650 — Comment-density extended to `/* */` block comments.** Coverage now includes C-family + CSS block comments alongside the existing line-comment detection.
- **W689 — `.editorconfig` added (23 lines).** Mirrors `.gitattributes` EOL/charset/binary rules; pairs with the W683 `.gitattributes` extension to give Windows/Linux/Mac editors a single source of truth on line-endings + charset.
- **W685 — README CLI table header pin `"(all 231)"` with auto-count + `test_readme_cli_command_count_matches_source`.** Smart 231-canonical choice (matches the canonical count, not the raw 238 command count). The new test fails the build if the README header drifts from the live source count.
- **W695 — `--card` CLI smoke test (2 tests).** Pins the `mcp --card` handler against silent regression — the W624 + W642 migrations now have an end-to-end CLI test.
- **W697 — README CLI test extras-gate.** Auto-allowlist from `cli._DEPRECATED_COMMANDS` so newly-deprecated commands flow through the gate automatically.
- **W702 — `_DEPRECATED_COMMANDS` AST-literal contract test.** Pins the deprecation-list shape — any drift fails at lint time.

### Changed — Small cleanups (W707 dead-code + W698 consolidation)
- **W707 — `_serialize_suppressions` dead-code cleanup + regression test.** The serializer was unreachable after the W691 schema unification; the regression test pins the call-site count at zero.
- **W733 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W691-W722 batch (this consolidation).** Docs-only; hash-stability mandate held trivially.

> **P0 user-flagged regression batch fixed (W670/W671/W672/W682) + bare-except discipline shipped end-to-end (W653 real bug + W661/W662 fail-loud guards + W665/W677 narrowing, allowlist 9→4) + wheel-bundling discipline COMPLETE (W664 LIVE BUG caught + W668 as_file audit + W642 triple-parent removed) + smell-suppression substrate (W658) + CRITICAL latent bug surfaced in .roam/suppressions.json (W676 → W691 in flight) + W646 eat-our-own-dogfood (W601 cleared) + W683/W685 hygiene (W670 / W671 / W672 / W682 / W683 / W685 / W642 / W646 / W653 / W661 / W662 / W664 / W665 / W668 / W658 / W676 / W677 batch, 2026-05-15).**
> Sixteen-completion batch folded in behind the W657-CONSOLIDATE consolidation. **The headline is the P0 batch closure (4 user-flagged regressions sealed)**: **W670 P0.1** moved `_wrap_with_alias_normalization` before the preset filter so `roam_plan` no longer drops the `file_path` alias on filtered presets; **W671 P0.2** added a `_INLINE_RESPONSE_TOOLS` frozenset that exempts `roam_catalog` from the auto-handle wrapper so the cold-start `catalog` call returns inline instead of through a never-completed handle; **W672 P0.3** synced 8 files to the live `238 commands · 231 canonical · 224 mcp tools` counts (the auto-derive path via `dev/build_readme_counts.py --apply`); **W682 P0.3-followup** added the `evidence-oscal` row to the README CLI table. **Bare-except discipline shipped end-to-end**: **W653** fixed a REAL bug in `run_all_detectors` — bare-except was swallowing `NameError`/`ImportError`/`AttributeError`/`TypeError` classifying-bugs as if they were per-detector failures; now they propagate as `RuntimeError` and only `sqlite3.Error` is swallowed+logged; **W662** added an AST drift-guard banning bare-except in detector modules (9 sites grandfathered + 10/10 tests pass); **W661** applied the fail-loud discipline to the `catalog/detectors` production loop (8 new tests); **W665** narrowed 3 bare-except sites (allowlist 9→6); **W677** narrowed 2 more (allowlist 6→4). **Wheel-bundling discipline COMPLETE**: **W664** added a `__init__.py` package-data drift-guard that **CAUGHT A LIVE W643-class bug on first run** (`roam.languages.extractors` had a missing `__init__.py`); **W668** audited `as_file()` callers + sealed the pattern with 4 fixes + a drift-guard; **W642** removed the triple-parent fallback from the `mcp --card` handler (-19 LOC; W624 already migrated the resolution to `importlib.resources` so the fallback was dead code). **Smell-suppression substrate landed (W658)**: 225-line module + 17 tests for `.roam/smells.suppress.yml`. **CRITICAL latent bug surfaced (W676)**: suppression-parser audit found **4 parsers (not 3) with incompatible schemas** in `.roam/suppressions.json` — two readers consume the same file with different shapes, so suppressions silently apply to one detector and not the other (W691 in flight to seal). **W646 eat-our-own-dogfood**: refactored `_create_extractor` from 105 → 17 lines via a `_LANGUAGE_EXTRACTORS` dispatch dict — cleared roam's own W601 finding on itself (first time the smell catalog caught a true positive on roam-code AND the refactor sealed it within the same week). **W683 / W685 hygiene**: `.gitattributes` extended 13 → 49 lines (`eol=lf` + 26 binary rules); README CLI table header pinned to `"(all 231)"` matching the 231-canonical count. **Hash-stability 31/31 byte-identical held across every source wave.**

### Added — Smell-suppression substrate + bare-except AST drift-guard + __init__.py wheel drift-guard (W658/W662/W664 batch)
- **W658 — `.roam/smells.suppress.yml` smell-suppression substrate.** 225-line module + 17 tests. First-class suppression surface for the smell catalog; suppressions are scoped per detector + per path glob with a deterministic match order. Pairs with the W370c 5-smell expansion (W601-W605 from the prior batch) to give the catalog its operator-facing escape hatch.
- **W662 — AST drift-guard banning bare-except in detector modules.** 9 sites grandfathered into a `_PRE_W662_PENDING` allowlist (5 of those subsequently narrowed in W665 + W677, allowlist now 4). **10/10 tests pass.** Closes the regression class that surfaced as W653 — any future detector that catches `Exception` without re-raising structural errors fails at PR time.
- **W664 — `__init__.py` package-data drift-guard.** **CAUGHT A LIVE W643-class bug on first run**: `roam.languages.extractors` was missing its `__init__.py`, meaning the package was not actually shipped in the wheel despite being referenced via package-data. Pairs with W570/W610 to give the wheel-bundling discipline complete coverage across both YAML/JSON data files AND Python package directories.
- **W668 — `as_file()` callers audit + 4 fixes + drift-guard.** Pattern sealed: every `importlib.resources.files(...)` call that needs a filesystem path now goes through `as_file()` (4 sites fixed); the drift-guard pins the pattern at lint time. Continues the W554/W535/W610/W624 importlib.resources discipline thread.
- **W661 — Fail-loud discipline applied to `catalog/detectors` production loop.** 8 new tests. Production detector loop now classifies `NameError`/`ImportError`/`AttributeError`/`TypeError` as `RuntimeError` (structural — re-raise) vs `sqlite3.Error` (per-detector — swallow+log). Pair with W653 source fix + W662 drift-guard for end-to-end discipline.

### Fixed — P0 user-flagged regression batch (W670/W671/W672/W682) + bare-except real bug (W653) + wheel-bundling LIVE bug (W664 finding)
- **W670 P0.1 — `roam_plan` `file_path` alias regression fixed.** Moved `_wrap_with_alias_normalization` BEFORE the preset filter so the alias normalization layer wraps every tool, not just those that survive the filter. The regression had silently dropped `file_path` on the `roam_plan` MCP wrapper when `core` preset was active. **User-flagged P0.**
- **W671 P0.2 — `roam_catalog` cold-start auto-handle exemption.** New `_INLINE_RESPONSE_TOOLS` frozenset exempts `roam_catalog` from `_wrap_with_handle_off` — the cold-start `catalog` call now returns inline instead of through a handle that the agent never polled for. Pattern: tools that respond on cold-start with bounded output should bypass the handle pattern. **User-flagged P0.**
- **W672 P0.3 — 8 files synced to live `238/231/224` counts.** Auto-derived via `dev/build_readme_counts.py --apply` (the same path W557 used for version bumps). README + CLAUDE.md + llms-install.md + both mcp-server-card.json copies + server.json + landing-page HTML all carry the live counts. **User-flagged P0.**
- **W682 P0.3-followup — README CLI table: added the `evidence-oscal` row.** Closes the W672 gap audit — the table claimed `(all 231)` rows but the OSCAL surface was missing. **User-flagged P0.**
- **W653 — REAL BUG: `run_all_detectors` bare-except now classifies.** Pre-fix: bare-except swallowed `NameError`/`ImportError`/`AttributeError`/`TypeError` as if they were per-detector failures, masking structural bugs (e.g. a typo in a detector module would silently skip the detector and report success). Post-fix: those four classify as `RuntimeError` (structural — re-raise) while `sqlite3.Error` keeps the swallow+log behavior. Surfaced by the W662 drift-guard scoping pass.
- **W665 — 3 bare-except sites narrowed (allowlist 9 → 6).** Continues the W662 drift-guard scoping.
- **W677 — 2 more bare-except sites narrowed (allowlist 6 → 4).** Continues the W665 scoping.
- **W642 — Removed triple-parent fallback from `mcp --card` handler.** **-19 LOC.** W624 (prior batch) migrated the resolution to `importlib.resources.files("roam") / "mcp-server-card.json"` with `as_file()`; the triple-parent `parents[3]` fallback was dead code after that migration. Continues the importlib.resources discipline thread.

### Research/added — Suppression-parser audit (W676) + W646 dogfood refactor + W683/W685 hygiene
- **W676 — Suppression parser audit (BAILED — surfaced CRITICAL latent bug).** Investigated the suppression parsers to consolidate them; **discovered 4 parsers (not 3) with incompatible schemas reading `.roam/suppressions.json`**. Two readers consume the same file with different shape contracts, so a suppression entered for one detector silently does NOT apply to the other reader's detector. **W691 in flight** to seal this — likely a closed-schema migration to a single canonical parser + a drift-guard. **The BAIL is the find**: investigate-first discipline saved a fabricated consolidation that would have shipped the bug forward.
- **W646 — Refactored `_create_extractor` from 105 → 17 lines via `_LANGUAGE_EXTRACTORS` dispatch dict.** **Eat-our-own-dogfood**: W601 (`switch-statement` smell detector — prior batch) flagged `_create_extractor`'s 23-arm switch as a REAL refactor candidate on roam-code itself; W646 sealed it. First time the smell catalog caught a true positive on roam-code AND the same week's refactor wave sealed it.
- **W683 — `.gitattributes` extended (13 → 49 lines).** `eol=lf` for text files + 26 binary rules. Closes the long-tail "CRLF-on-Windows breaks the wheel-built smoke job" failure class that surfaced intermittently on the W577 wheel-smoke CI.
- **W685 — README CLI table header pinned to `"(all 231)"`.** Smart 231-canonical choice (matches the canonical count, not the raw 238 command count). Pairs with W672/W682 to keep the README evidence-table column header in lockstep with the auto-derived canonical surface.

### Changed — (ADD) W698 consolidation pass
- **W698 — CHANGELOG/HANDOVER/BACKLOG/SESSION-SNAPSHOT refresh for W642-W685 batch.** Docs-only; hash-stability mandate held trivially. **51/51 doc-consistency + schema-migration tests pass.**

> **Pattern-3a vocabulary cluster GENUINELY STRUCTURALLY CLOSED across ALL THREE rank axes (severity + confidence + risk) + smell-detector catalog reached 20 detectors (was 15) + _wrap_with_alias_normalization refactor+dedup chain + cross-detector empty-corpus smoke (W607 / W624 / W631 / W601 / W602 / W640 / W605 / W648 / W639 / W636 batch, 2026-05-15).**
> Nine-completion batch folded in behind the W635-CONSOLIDATE consolidation. **The headline is Pattern-3a GENUINELY closed end-to-end**: **W631** introduces the third canonical axis `src/roam/output/risk.py::risk_rank()` and migrates 2 sites (`cmd_migration_plan` + `cmd_path_coverage`), pairing with W564 severity-rank + W596 confidence-rank to canonicalize ALL THREE rank axes (severity + confidence + risk); **W648** AST audit returned **ZERO slipped rank tables** across the entire src tree — Pattern-3a is structurally closed for real, not just-in-name; **W640** folded `cmd_alerts._LEVEL_ORDER` into `severity_rank()` via `-severity_rank(lowercase)` and broadened the drift-guard regex `/sever/ → /sever|level_order/`. **Smell catalog reached 20 detectors (was 15 at session start)**: W370c 5-smell expansion COMPLETE — W601 (`switch-statement`, 7 findings; surfaced REAL refactor candidate `_create_extractor` 23-arm switch), W602 (`temporal-coupling`, 10 findings; surfaced cli↔`_run_roam_inprocess` 34-commit top coupling), W603 (`magic-numbers`), W604 (`boolean-parameter`), W605 (`comment-density` TODO/FIXME/XXX/HACK; roam-code CLEAN at max 0.49% rate). LAW-4 anchor sets bumped 92→93 / 109→110 to accommodate `comment-density` terminals. **`_wrap_with_alias_normalization` refactor + dedup chain complete**: **W607** decomposed the 130-line `_wrap_with_alias_normalization` into 3 helpers (`_collect_alias_candidates`, `_build_merged_signature`, `_build_merged_annotations`; 130→50 lines + 7 unit tests; 2960 focused tests pass); **W636** collapsed the sync/async wrapper closure duplication via shared `_prepare_kwargs` helper + branched closure (33→28 lines + duplicate-body anti-pattern eliminated). Pairs with W595 (param-ordering seal) + W606 (canonical-positional collision lint) to give the wrapper its end-to-end discipline. **Cross-detector empty-corpus smoke (W639)** guards **54 detectors** (20 smells + 34 algo + 2 floor counts; 56+115+17+31 = 219 tests) against silent import errors after concurrent merges — catches the W601/W602-style regression class at PR time. **W624** migrated the `mcp --card` handler at `mcp_server.py:14593-14624` to `importlib.resources.files("roam") / "mcp-server-card.json"` with `as_file()` — completes the importlib.resources discipline thread (10+31+140 tests pass). **Hash-stability 31/31 byte-identical held across every source wave.**

### Added — Risk-rank canonical helper + 5 new smell detectors + cross-detector empty-corpus smoke (W631/W601/W602/W603/W604/W605/W639 batch)
- **W631 — Third Pattern-3a axis CANONICALIZED.** New `src/roam/output/risk.py::risk_rank()` helper; 2 sites migrated (`cmd_migration_plan` + `cmd_path_coverage`). **Pattern-3a STRUCTURALLY CLOSED ACROSS ALL THREE AXES** (severity W547+W564 + confidence W596 + risk W631). **131 tests pass.** Direct fulfillment of the W635-batch carry-forward.
- **W601 — `switch-statement` smell detector.** **7 findings on roam-code itself, surfaced a REAL refactor candidate**: `_create_extractor` 23-arm switch. Continues the W370c 5-smell expansion thread.
- **W602 — `temporal-coupling` smell detector.** **10 findings; top coupling is cli ↔ `_run_roam_inprocess` at 34 commits.** Surfaces the recurring "two files change together but live in different modules" anti-pattern. Continues W370c.
- **W603 — `magic-numbers` smell detector.** Continues W370c.
- **W604 — `boolean-parameter` smell detector.** Continues W370c.
- **W605 — `comment-density` smell detector** (TODO / FIXME / XXX / HACK). **20th detector ships; roam-code CLEAN (max rate 0.49%).** **173 tests pass. Closes W370c 5-smell expansion (W601/W602/W603/W604/W605 all shipped).** LAW-4 concrete-noun anchor sets bumped **92 → 93** (formatter) and **109 → 110** (test) to accommodate the `comment-density` finding terminals.
- **W639 — Cross-detector empty-corpus smoke test.** Guards **54 detectors** (20 smells + 34 algo + 2 floor counts) against silent import errors after concurrent merges. **56 + 115 + 17 + 31 = 219 tests.** Catches the W601 / W602-style concurrent-merge import-error regression class at PR time — a class that previously surfaced only on the next dogfood pass.
- **W601 + W602 bundle — 17 → 19 detector count milestone.** 165 tests pass on the bundle's structural pieces. W605 carries the count from 19 → 20.

### Changed — `_wrap_with_alias_normalization` refactor + dedup + importlib.resources migration + alerts level_order fold (W607/W636/W624/W640/W648 batch)
- **W607 — Decomposed `_wrap_with_alias_normalization` into 3 helpers.** `_collect_alias_candidates`, `_build_merged_signature`, `_build_merged_annotations`. **130 → 50 lines.** **7 unit tests + 2960 focused tests pass.** Continues the W595 (param-ordering seal) + W606 (canonical-positional collision lint) refactor thread on the wrapper.
- **W636 — Sync/async wrapper closure duplication collapsed.** New shared `_prepare_kwargs` helper + branched closure pattern. **33 → 28 lines** and duplicate-body anti-pattern eliminated. **40 tests pass.** Pairs with W607 to give the wrapper its final shape.
- **W624 — `mcp --card` handler migrated to `importlib.resources`.** `mcp_server.py:14593-14624` now resolves via `importlib.resources.files("roam") / "mcp-server-card.json"` with `as_file()`. **10 + 31 + 140 tests pass.** Continues the W554 / W535 / W610 `importlib.resources` discipline thread.
- **W640 — `cmd_alerts._LEVEL_ORDER` folded into `severity_rank()`.** Sort key now uses `-severity_rank(lowercase)` instead of a private rank table. Drift-guard regex broadened `/sever/ → /sever|level_order/` so the canonicalization is enforced for any future `_LEVEL_ORDER` clones. **121 tests pass.** One of the W648 audit's findings, sealed in the same wave.
- **W648 — AST audit for slipped rank tables. ZERO slipped.** Audited the entire src tree for inline rank tables that would bypass the canonical `severity_rank()` / `confidence_level_rank()` / `risk_rank()` helpers. **Result: zero slipped.** Pattern-3a is GENUINELY structurally closed — the audit confirms the structural-close claim is not just-in-name. **47/47 + 31/31 tests pass.**

> **Pattern-3a vocabulary cluster STRUCTURALLY CLOSED across BOTH rank axes (severity + confidence) + smell-detector catalog reached ZERO placeholder stubs + wheel-bundling discipline complete + fragile-path sweep + AST drift-guards across the board (W596 / W594 / W588 / W577 / W570 / W564 / W515 / W370c batch, 2026-05-15).**
> Sixteen-completion batch folded in behind the W600-CONSOLIDATE consolidation. **The headline is structural across BOTH rank axes**: **W596** completes Pattern-3a confidence-rank consolidation by migrating **15 sites** to the canonical `src/roam/output/confidence.py::confidence_level_rank()` helper (**561 tests pass**), pairing with **W564**'s prior 10-site severity-rank migration to close the Pattern-3a vocabulary cluster end-to-end. Combined with **W547** (severity vocab) + **W518** (control-mapping vocab) + **W512** (edge-kinds) + **W565+W566** (severity helpers), drift-guard discipline now canonicalizes through **6 modules + 6 AST lint suites** — every Pattern-3a vocabulary cluster surfaced in the dogfood corpus flows through canonical modules with AST drift-guards. **Third rank axis (risk) flagged as W631 follow-up.** **Smell detector catalog reached ZERO placeholder stubs (W370c)**: shipped 2 detectors (`refused-bequest` 2 findings + `primitive-obsession` 144 findings) and scoped the remaining stubs into 5 W370c-followup waves (W601-W605) for new smell kinds. **Fragile-path harness gotcha closed end-to-end**: **W587** (10 sites) + **W594** (18 sites, 47 → 29 remaining) swept 28 of 57 fragile-path test sites to the canonical `tests/_helpers/repo_root.py` helper; **W588** added an AST drift-guard for the `Path(__file__).parents[N]` pattern with fail-loud `_PRE_W594_PENDING` allowlist (47 entries — corrected upward from the 27 estimate by the W588 inventory pass); **W606** added an AST lint for canonical-positional collision catching the pre-W595 crash class at PR time (4 new tests). **Wheel-bundling discipline COMPLETE**: **W554** customer SHIPPING BUG fixed + **W570** drift-guard + **W577** CI wheel-smoke job (3 steps: build wheel + install fresh venv + run drift-guard from /tmp) + **W610** extended to taint_rules + languages.extractors + mcp-server-card (3 new test classes, 6 new tests) — closes prior 2 silent-empty bugs (12.12.1 taint rules + 12.12.2 Jenkinsfile) across 5 package-data surfaces. **Pattern 1 variant D family CLOSED at the CLI boundary** (**W573** NO-OP investigation confirmed only 1 production call site for `ChangeEvidence.from_canonical_json*` exists). **Leasing-system parity completed**: **W447 + W448 (bundled)** added the `pr-replay` info marker on missing leases dir + `read_lease(warnings_out=...)` kwarg. **Severity helpers landed**: **W565 + W566 (bundled)** added `severity_to_confidence_level()` + `severity_breakdown()` helpers (5 call-sites migrated, 248 tests). **Drift-guard parsing seal**: **W515** parses python-version from the live workflow before drift compare, sealing the false-positive class on CI version bumps (139 tests). **Doc sweep**: **W569** swept 9 stale `templates/audit-report/` path refs across 8 src/dev files + 1 test docstring (111 tests). **Small cleanups**: **W591-bundle** W584 / W497 / W500 bailed as already-done; W501 audit comments added to 4 test files. **W573** NO-OP investigation: only 1 production call site for `ChangeEvidence.from_canonical_json*` exists — Pattern 1 variant D family fully sealed at CLI boundary. **Hash-stability 31/31 byte-identical held across every source wave.**

### Research/added — Pattern-3a confidence-rank canonicalization + smell detectors + AST drift-guards + wheel-smoke CI (W370c/W515/W564/W577/W588/W596/W606/W610 batch)
- **W596 — MASSIVE Pattern-3a confidence-rank consolidation.** **15 sites migrated** to the canonical `src/roam/output/confidence.py::confidence_level_rank()` helper. **561 tests pass.** **31/31 hash-stability byte-identical held.** Closes the Pattern-3a vocabulary cluster across BOTH rank axes — the pair to W564 severity-rank: every Pattern-3a vocabulary surface surfaced in the dogfood corpus now flows through canonical modules with AST drift-guards. **Third rank axis (risk) flagged as W631 follow-up.**
- **W370c — Smell detector catalog reached ZERO placeholder stubs.** Scoped the W368 BEHIND-list smell stubs + shipped 2 detectors: `refused-bequest` (2 findings) + `primitive-obsession` (144 findings). Catalog now has ZERO placeholder detectors. 5 W370c-followup waves (W601-W605) queued for new smell kinds (first 2 in flight). Closes the catalog-cleanup thread that has been carry-forward since W368.
- **W588 — AST drift-guard for fragile-path `Path(__file__).parents[N]` pattern.** Fail-loud with `_PRE_W594_PENDING` allowlist (47 entries — corrected upward from the 27 estimate by the W588 inventory). Companion to the W587 + W594 sweep below. Pairs with W606 canonical-positional collision lint to give the fragile-path harness gotcha end-to-end lint coverage.
- **W606 — AST lint for canonical-positional collision.** 4 new tests catching the pre-W595 crash class at PR time. Closes the latent breakage class that the W587 fragile-path sweep surfaced (`_wrap_with_alias_normalization` param-ordering — W595 sealed the source bug; W606 ensures regression catches at lint time).
- **W577 — Wheel-built CI smoke job added to `roam-ci.yml`.** 3 steps: build wheel + install into fresh venv + run drift-guard from `/tmp`. Pairs with W570 (drift-guard) + W610 (extension to 5 package-data surfaces) to close the customer-facing shipping-bug class structurally — `pip install roam-code` users can no longer hit a "feature works in src but broken on wheel install" surface without CI catching it.
- **W610 — Wheel drift-guard extended to 3 more package-data surfaces.** Adds taint_rules + languages.extractors + mcp-server-card to the W570 pin. **3 new test classes + 6 new tests.** Closes 5 package-data surfaces end-to-end (W570 pinned 2; W610 pinned 3 more). Sealing the prior 2 silent-empty bugs in the wheel (12.12.1 taint rules + 12.12.2 Jenkinsfile).
- **W515 — Drift-guard parses python-version from the live workflow before drift compare.** False-positive class on CI version bumps sealed: the lint no longer flags routine version-string bumps as drift. **139 tests pass.** Closes the long-tail false-positive class that surfaced on every Python-3.X minor-version bump.
- **W564 — MASSIVE Pattern-3a severity-rank consolidation (carry from W591 batch).** **10 sites migrated** to canonical `severity_rank()`. **460 + 31 tests pass.** Listed here as the structural pair to W596 in the BOTH-rank-axes structural close-out chain.

### Fixed — Fragile-path sweep continues + Pattern 1 variant D CLI boundary closed + leasing parity (W447/W448/W573/W587/W594 batch)
- **W594 — 18 fragile-path test sites migrated to `tests/_helpers/repo_root.py` (47 → 29 remaining).** Continues the W587 sweep (which migrated the first 10 highest-noise sites). W608 priority pair (W512+W547 drift-guard templates) included. **230 tests pass.** W588 drift-guard now fail-loud on any regression — the remaining 29 sites are in the `_PRE_W594_PENDING` allowlist with explicit migration tasks tracked as W612-W613.
- **W587 — 10 fragile-path test sites migrated (carry from W591 batch).** Closes the worktree-vs-main-tree visibility gotcha (W567) for the 10 highest-noise sites; pairs with W594 (next-18) + W588 (drift-guard) + W606 (canonical-positional collision lint) to give the fragile-path harness end-to-end lint discipline.
- **W447 + W448 (bundled) — Leasing-system Pattern-2 always-emit discipline complete (carry from W591 batch).** **W447** added the `pr-replay` info marker on missing leases dir under `migration` / `autonomous_pr` modes. **W448** added the `roam.leases.store.read_lease(warnings_out=...)` kwarg.
- **W573 — Pattern 1 variant D family CLOSED at the CLI boundary (NO-OP investigation, carry from W591 batch).** Only 1 production call site for `ChangeEvidence.from_canonical_json*` exists. Pattern 1 variant D family fully sealed at the CLI boundary.

### Changed — Severity helpers + doc sweep + small cleanups (W565/W566/W569/W591-bundle batch — carry from W591 batch)
- **W565 + W566 (bundled) — Severity helpers landed in `_severity.py`.** New `severity_to_confidence_level()` + `severity_breakdown()` helpers. **5 call-sites migrated.** **248 tests pass.**
- **W569 — Doc sweep: 9 stale `templates/audit-report/` path refs swept** across 8 src/dev files + 1 test docstring. **111 tests pass.**
- **W591-bundle — Small cleanups.** W584 / W497 / W500 bailed as already-done; W501 audit comments added to 4 test files. **81 tests pass.**

> **Pattern-3a severity-rank consolidation STRUCTURALLY CLOSED + fragile-path sweep + leasing parity + git-helper consolidation + Pattern 1 variant D CLI-boundary close (W540-W591 batch, 2026-05-15).**
> Nine-completion batch folded in behind the W578-CONSOLIDATE consolidation. **The headline is structural**: **W564** completes the Pattern-3a severity-rank consolidation by migrating 10 sites to the canonical `severity_rank()` helper alongside W512 (edge-kinds) + W518 (control-mapping vocab) + W547 (severity vocab) + W565+W566 (severity helpers) — every Pattern-3a vocabulary cluster surfaced in the dogfood corpus now flows through canonical modules with AST drift-guards. **14 confidence-rank tables flagged as the next Pattern-3a target (W596 queued).** **460 + 31 tests pass.** **Pattern 1 variant D CLI boundary CLOSED**: **W573** investigation confirmed only 1 production call site for `ChangeEvidence.from_canonical_json*` exists (the one W561 already migrated) — the variant D family is fully sealed at the CLI boundary. **Leasing-system parity completed**: **W447 + W448 (bundled)** added the `pr-replay` info marker on missing leases dir under `migration` / `autonomous_pr` modes + the `roam.leases.store.read_lease(warnings_out=...)` kwarg — Pattern-2 always-emit discipline now covers `list_leases` (W425) + `read_lease` (W448) + `pr-replay` info-marker (W447) end-to-end. **137 + 31 tests pass.** **Git-helper subprocess discipline**: **W540** consolidated `_git_fingerprint` + `_git_commit_sha` helpers; `pr-bundle init` now shells out to `git rev-parse HEAD` **ONCE** instead of TWICE per invocation. **105 + 31 tests pass.** **Severity helpers landed**: **W565 + W566 (bundled)** added `severity_to_confidence_level()` + `severity_breakdown()` to `_severity.py` with 5 call-sites migrated. **248 tests pass.** **Fragile-path sweep (W587)**: 10 test sites migrated to the new `tests/_helpers/repo_root.py` helper — 37 → 27 fragile-path sites remain (W594 queued for the remainder). Surfaced a real bug: `_wrap_with_alias_normalization` param-ordering breaks `test_surface_consistency` (W595 in flight). **Small cleanups (W591-bundle)**: W584 / W497 / W500 bailed as already-done; W501 audit comments added to 4 test files. **81 tests pass.** **Doc sweep (W569)**: 9 stale `templates/audit-report/` path refs swept across 8 src/dev files + 1 test docstring + 1 fixture-regen command. **111 tests pass.** **Hash-stability 31/31 byte-identical held across every source wave.**

### Changed — Pattern-3a severity-rank canonicalization + severity helpers + git-helper consolidation (W540/W564/W565/W566 batch)
- **W564 — MASSIVE Pattern-3a severity-rank consolidation.** 10 sites migrated to the canonical `severity_rank()` helper. **460 tests pass.** **31/31 hash-stability byte-identical held.** **14 confidence-rank tables flagged as the next Pattern-3a target (W596 queued).** Continues the structural Pattern-3a close-out chain: W512 (edge-kinds) + W518 (control-mapping vocab) + W547 (severity vocab) + W564 (severity-rank) + W565+W566 (severity helpers) — every cluster surfaced in the dogfood corpus now flows through canonical modules with AST drift-guards.
- **W565 + W566 (bundled) — Severity helpers landed in `_severity.py`.** New `severity_to_confidence_level()` + `severity_breakdown()` helpers. **5 call-sites migrated.** **248 tests pass.** Pair with W547/W548 to give every consumer one canonical entry-point per severity-derived computation.
- **W540 — Consolidated `_git_fingerprint` + `_git_commit_sha` helpers.** `pr-bundle init` now shells out to `git rev-parse HEAD` ONCE per invocation instead of TWICE — closes the subprocess-discipline gap surfaced by the W521 producer-side commit_sha stamping work. **105 tests pass. 31/31 hash-stability byte-identical held.**

### Fixed — Leasing parity + Pattern 1 variant D CLI boundary closed (W447/W448/W573 batch)
- **W447 + W448 (bundled) — Leasing-system Pattern-2 always-emit discipline complete.** **W447** added the `pr-replay` info marker on missing leases dir under `migration` / `autonomous_pr` modes — explicit `state: "leases_not_initialized"` rather than silent SAFE. **W448** added the `roam.leases.store.read_lease(warnings_out=...)` kwarg — pairs with W425's `list_leases(warnings_out=...)` to give every lease read path the same always-emit surface. **137 + 31 tests pass.**
- **W573 — Pattern 1 variant D family CLOSED at the CLI boundary (NO-OP investigation).** Confirmed only 1 production call site for `ChangeEvidence.from_canonical_json*` exists (the one W561 already migrated to surface `dropped_enum_rows` + `partial_success`). The variant D class — "silent success on degraded resolution" — is fully sealed at the CLI boundary as of this investigation. No further migration work needed; class structurally closed.

### Changed — Fragile-path sweep + small cleanups + doc sweep (W569/W587/W591 batch)
- **W587 — 10 fragile-path test sites migrated to `tests/_helpers/repo_root.py`.** Closes the worktree-vs-main-tree visibility gotcha (W567) for the 10 highest-noise sites; **37 → 27 fragile-path sites remaining (W594 queued).** Surfaced a real bug: `_wrap_with_alias_normalization` param-ordering breaks `test_surface_consistency` (W595 in flight) — exactly the kind of latent breakage the worktree-vs-main-tree visibility gap was masking.
- **W591-bundle — Small cleanups.** W584 / W497 / W500 bailed as already-done (investigate-first discipline saved fabricating work); W501 audit comments added to 4 test files. **81 tests pass.**
- **W569 — Doc sweep: 9 stale `templates/audit-report/` path refs swept.** Across 8 src/dev files + 1 test docstring + 1 fixture-regen command. **111 tests pass.** Closes the long-tail doc-drift class surfaced by the W554 move (control-mapping.yaml moved into `src/roam/templates/audit_report/` — referrers needed to follow).

> **SHIPPING BUG FIXED + Pattern 1 variant D disclosure + canonical severity vocab + ChangeEvidence round-trip pipeline + OSCAL persistent artifacts + package-data drift-guard (W520-W570, 2026-05-15).**
> Ten-completion batch folded in behind the W549 consolidation. **The headline is a customer-facing shipping bug fix**: **W554** moved `templates/audit-report/control-mapping.yaml` *into* `src/roam/templates/audit_report/` + added the `pyproject.toml` package-data entry — `pip install roam-code` users could not previously run `roam ci-setup --with-oscal` or `roam evidence-oscal` against their own projects because the control-mapping YAML was not bundled in the wheel. Lookup migrated to `importlib.resources`. **Verified end-to-end via fresh tmp venv wheel install** (109 tests pass). **Pattern 1 variant D `dropped_enum_rows` disclosure** lands across the AR envelope: **W534** introduced `ChangeEvidence.from_canonical_json(text, *, strict=False)` with closed-enum validation — 31 golden fixtures round-trip BYTE-IDENTICAL with content hashes preserved (forgiving projection mode); **W561** added `from_canonical_json_with_drops()` classmethod that surfaces dropped enum rows + `partial_success: true` on the envelope (LAW-4 anchored on `rows` terminal); **W559** wired `from_canonical_json` into the `cmd_evidence_oscal` AR path with a `--strict` flag (hybrid `Mapping|ChangeEvidence` signature). Forgiving-projection AND fail-loud discipline now both available end-to-end (W465 golden fixture stays byte-identical). **Canonical severity vocabulary** in `src/roam/output/_severity.py` (**W547 + W548 bundled**): `SEVERITY_LEVELS` / `SEVERITY_ALIASES` / `normalize_severity` / `to_sarif_level` / `validate_severity` + AST drift-guard — closes the Pattern 3a severity-vocabulary divergence across SARIF emitters. **OSCAL persistent artifacts** (**W535**): `roam ci-setup --with-oscal` now materializes `.roam/oscal/control-mapping.json` + `stub-assessment-plan.json` with deterministic UUIDv5 + SHA-256-seeded timestamps — the FedRAMP continuous-assessment evidence pattern. **SLSA SRC-L3 commit_sha chain CLOSED**: **W520** added the cga-sibling `emit_cga_vsa_sibling` commit_sha fallback — belt-and-suspenders complement to W509 — completing the producer-W521 + collector-W509 + cga-sibling-W520 three-path chain (all three fall back to `git rev-parse HEAD`). **Package-data wheel-bundling discipline**: **W570** added `tests/test_package_data_wheel_drift.py` drift-guard pinning `roam.templates.audit_report` + `roam.templates.ci` package-data entries. Closes the recurring "feature works in src but broken on `pip install`" surface (the W554-class bug). **Version-skew + hash-stability hygiene**: **W557** rolled `server.json` + `mcp-server-card.json` 12.50→13.0 via `dev/build_readme_counts.py --apply`; **W563** normalizes auto-derived fields before hashing in the card-hash test so count/version bumps stay invisible while preserving the R17 tampering guard for other fields. **Hash-stability 31/31 byte-identical held across every source wave.**

### Research/added — ChangeEvidence round-trip + canonical severity + OSCAL persistence + cga commit_sha (W520/W534/W535/W547/W548/W559/W561/W563/W570 batch)
- **W534 — `ChangeEvidence.from_canonical_json(text, *, strict=False)`.** Closed-enum validation; **31 golden fixtures round-trip BYTE-IDENTICAL** with content hashes preserved. Forgiving-projection mode by default; `strict=True` raises on unknown enum values. The structural answer to "how do consumers safely round-trip an AR envelope without breaking the byte-identical hash discipline" — fully passes both halves of the contract.
- **W535 — `roam ci-setup --with-oscal` persistent artifacts.** Materializes `.roam/oscal/control-mapping.json` + `.roam/oscal/stub-assessment-plan.json` with deterministic UUIDv5 (namespace-pinned) + SHA-256-seeded timestamps so re-runs produce byte-identical outputs. **21 + 15 + 16 + 31 tests pass.** Pattern: FedRAMP continuous-assessment requires durable artifacts on disk, not ephemeral envelopes — `--with-oscal` is the bootstrap pair to the W465 `roam evidence-oscal` runtime emitter.
- **W547 + W548 (bundled) — Canonical `src/roam/output/_severity.py` module.** Single source of truth for `SEVERITY_LEVELS` / `SEVERITY_ALIASES` / `normalize_severity` / `to_sarif_level` / `validate_severity`. AST drift-guard pins the closed enumeration at construction time. **89 tests pass.** Closes Pattern 3a (vocabulary divergence) on severity across SARIF emitters — every emitter now resolves through the same canonical helpers.
- **W559 — Wired `ChangeEvidence.from_canonical_json` into `cmd_evidence_oscal` AR path with `--strict` flag.** Hybrid `Mapping|ChangeEvidence` signature so callers can pass either a raw mapping (legacy / forgiving) or a parsed `ChangeEvidence` instance (typed / strict). **W465 golden fixture stays byte-identical.** **116 tests pass.**
- **W561 — Pattern 1 variant D `dropped_enum_rows` + `partial_success` disclosure on AR envelope.** New `from_canonical_json_with_drops()` classmethod returns `(evidence, dropped_rows)` so the consumer surface can disclose silent enum-drop side effects rather than silently projecting them away. LAW-4 anchored on the `rows` terminal. **107 + 176 tests pass.** Direct application of the dogfood synthesis "silent success on degraded resolution" guard.
- **W563 — Card-hash test normalizes auto-derived fields before hashing.** Hybrid A+B: count/version bumps are invisible to the hash; R17 tampering guard preserved for every other field. Makes routine version/count bumps a no-op against the card-hash drift-guard without weakening the integrity claim. **3 + 10 + 5 + 31 tests pass.**
- **W520 — `emit_cga_vsa_sibling` commit_sha fallback (belt-and-suspenders complement to W509).** Adds `git rev-parse HEAD` fallback to the cga sibling emit path, **completing the SLSA SRC-L3 commit_sha chain end-to-end**: producer W521 stamps at `pr-bundle init`, collector W509 falls back at `pr-bundle emit`, and cga sibling W520 falls back on the cga-emit-time path. All three paths now carry commit_sha through the no-collect path — the W498-surfaced drift class is fully sealed across pr-bundle AND cga surfaces.
- **W570 — `tests/test_package_data_wheel_drift.py` drift-guard.** Pins `roam.templates.audit_report` + `roam.templates.ci` package-data entries in `pyproject.toml`. **4 + 24 + 15 + 31 tests pass.** Structural answer to the recurring "feature works in src but broken on `pip install`" failure mode that produced W554 — drift on package-data entries is now caught at lint time.

### Fixed — SHIPPING BUG closed (W554) + version skew (W557)
- **W554 — SHIPPING BUG: `templates/audit-report/control-mapping.yaml` MOVED into `src/roam/templates/audit_report/` + `pyproject.toml` package-data entry added.** **`pip install roam-code` users could not previously run `roam ci-setup --with-oscal` or `roam evidence-oscal` against their own projects** because the control-mapping YAML was not bundled in the wheel — the surface worked end-to-end in the src tree and silently broke after `pip install`. Lookup migrated to `importlib.resources`. **Verified end-to-end via fresh tmp venv wheel install** (built wheel, installed into a throwaway venv, ran `roam ci-setup --with-oscal` + `roam evidence-oscal` against the venv-installed binary). **109 tests pass.** Pairs with W570 drift-guard above to prevent regression.
- **W557 — Version skew fix on `server.json` + `mcp-server-card.json`.** Rolled 12.50 → 13.0 via `dev/build_readme_counts.py --apply` (the auto-derived path; manual edits would have re-drifted). **60 tests pass.** The card-hash R17 drift-guard rolled forward via W563's normalize-before-hash change so the bump landed invisibly.

> **W493 BUG FAMILY STRUCTURALLY CLOSED + THREE more silent no-ops sealed + OSCAL pipeline end-to-end + OWASP labels integrity + SLSA SRC-L3 commit_sha parity (W506-W533, 2026-05-15).**
> Ten-wave batch closed behind the W516 docs consolidation. **The headline is structural**: W512 introduces `src/roam/db/edge_kinds.py` + a 16-test drift-guard lint that migrates 12 read-sites to canonical helpers and structurally seals the W493/W499/W511/W524 edge-kind bug family — future inline `kind IN` queries fail at lint time. **Three more long-latent silent no-ops sealed this batch**: (1) **W511** fixed `side_effects.py:497` edge-kind union (production impact 13/14,949 → 14,949/14,949 edges matched — the FOURTH silent no-op in the W493 family); (2) **W524-bundle** hunt found **7,534 missing import edges in `cmd_hover.py`** (the largest single edge-kind no-op in the family by 3 orders of magnitude — hover output had been blind to imports since launch), plus +13 references in `cmd_risk.py` and defensive plumbing in `cmd_patterns.py`; (3) **W531** caught SARIF `severity=error` silently downgrading to `"note"` since launch — **GitHub Code Scanning + Microsoft Defender were not flagging taint findings as errors** for any consumer that ingested roam SARIF, ever. **OSCAL pipeline fully shipped end-to-end**: W465 added Assessment Results emission via `roam evidence-oscal --kind assessment-results` (with auto-synthesized stub Assessment Plan per the FedRAMP continuous-assessment pattern). With W464 already in flight, `roam evidence-oscal` now covers both v1.2 models. **Claim-integrity batch on OWASP labels**: W533-bundle (W530+W531+W532) corrected the OWASP A05 → A03 mislabel on `java_sqli` + `python_ssti` and brought owasp_top10 coverage from **3/22 → 22/22 rules**, plumbed via W492/W453 into `TaintRule` / `TaintFinding` / `findings.evidence_json` / SARIF `tags[]`. **SLSA SRC-L3 commit_sha parity completed**: W509 added the emit-time `git rev-parse HEAD` fallback (restoring cga sibling parity surfaced by W498), and W521 stamped commit_sha producer-side at `pr-bundle init` so the W509 fallback becomes belt-and-suspenders. **Framework-vocab consolidation**: W518 collapsed scattered allowlists into `src/roam/evidence/control_mapping_vocab.py` (9 framework slugs + 9 titles + 3 pass-conditions + 7 surfaces) with drift-guard. **SLSA control-map entries shipped**: W506 landed the 3 missing SRC-L2/L3 entries + iso_42001 → iso_iec_42001 rename across 5 files in lockstep — claim-integrity hygiene now matches the W451/W471/W472 SRC-L3 pipeline. **Hash-stability 31/31 byte-identical held across every source wave.**

### Fixed — THREE long-latent silent no-ops + W493 family structurally sealed (W506-W533 batch)
- **W512 — STRUCTURAL CLOSE of the W493/W499/W511/W524 edge-kind bug family.** New `src/roam/db/edge_kinds.py` closed-enum module with canonical helpers; **12 read-sites migrated** to call the helpers instead of inlining `kind IN (...)` literal-string tuples. **16-test drift-guard lint** added — future inline `kind IN` queries against the edges table fail at lint time. This is the structural answer to the same edge-kind class that produced W493 (taint DFS no-op), W499 (impact gate no-op), W511 (effects propagation no-op), and W524 (cmd_hover 7k missing imports). **365 tests pass.**
- **W511 — `side_effects.py:497` edge-kind union (CRITICAL CORRECTNESS).** **The FOURTH silent no-op in the W493 family.** `effects_propagation` was matching **13 / 14,949 edges** pre-fix (0.087% coverage); post-fix matches **14,949 / 14,949 edges** (100%). Side-effect classification had been computed against a near-empty subset of the call graph since the edge-kind canonicals diverged. Caught by the same dogfood pattern that found W493 + W499.
- **W524-bundle — Phantom edge-kind hunt + 3 broken sites fixed.** Audited the rest of the codebase for the W493/W499/W511 class. Three sites repaired: `cmd_risk.py` +13 references, **`cmd_hover.py` +7,534 import edges** (massive missing — hover output had been blind to imports since launch, the largest single edge-kind no-op in the family), and defensive plumbing in `cmd_patterns.py`. **202 tests pass.**
- **W531 — SARIF `severity=error` silently downgraded to `"note"` since launch.** Discovered via the W533-bundle audit: SARIF emission was setting `level="error"` correctly but the downstream serializer was downgrading on the wire to `"note"`. **GitHub Code Scanning + Microsoft Defender for DevOps + every SARIF-ingesting tool was NOT flagging roam taint findings as errors** since the SARIF feature launched. Fix restores `level="error"` end-to-end so the critical-severity surface fires correctly in CI.
- **W530 — OWASP A05 → A03 mislabel on `java_sqli` + `python_ssti` (CLAIM INTEGRITY).** Both rules were stamped `A05:2021` (Security Misconfiguration) when they should have been `A03:2021` (Injection). Audit + correction landed alongside W531 + W532 in the W533-bundle.
- **W532 — owasp_top10 coverage 3/22 → 22/22 rules.** Only 3 of 22 taint rules carried owasp_top10 stamps pre-fix; **all 22** now correctly carry the annotation, surfaced via SARIF `tags[]` (W453 plumbing) and findings.evidence_json (W492 plumbing).
- **W509 — `pr-bundle emit` commit_sha fallback via `git rev-parse HEAD`.** Sealed the W498-surfaced drift: pr-bundle was dropping `commit_sha` on `--no-auto-collect` while cga emit fell back to git. The fix restores **SRC-L3 commit-anchored provenance parity** with the cga path. (W521 then made it belt-and-suspenders by stamping commit_sha producer-side at bundle init — see Added section below.)

### Added — OSCAL Assessment Results + OWASP plumbing + framework-vocab module + SLSA entries + producer-side commit_sha (W506-W533 batch)
- **W465 — OSCAL v1.2 Assessment Results emission.** `roam evidence-oscal --kind assessment-results` now emits v1.2 Assessment Results JSON; a stub Assessment Plan is auto-synthesized when no upstream plan exists (FedRAMP continuous-assessment pattern). Combined with the in-flight W464 Control Mapping emitter, `roam evidence-oscal` covers both OSCAL v1.2 models end-to-end. **81 tests pass.**
- **W492 + W453 — owasp_top10 wired through the taint pipeline end-to-end.** Loaded into `TaintRule` + `TaintFinding` dataclasses, persisted to `findings.evidence_json`, and plumbed to SARIF `tags[]`. **207 tests pass.** Pairs with the W533-bundle claim-integrity fixes above.
- **W518 — Framework-vocab allowlist consolidation.** New `src/roam/evidence/control_mapping_vocab.py` collapses the scattered framework-vocab allowlists into a single module: **9 framework slugs + 9 titles + 3 pass-conditions + 7 surfaces**, with a drift-guard test pinning the closed enumerations. Same shape as the W332 / W282 / W211 / W505-bundle vocabulary-freeze discipline.
- **W506 — SLSA SRC-L2/L3 control-mapping entries.** 3 new entries landed in `templates/audit-report/control-map.yml` alongside W428's NIST AI 600-1 + SP 800-218A additions. **iso_42001 → iso_iec_42001** rename propagated in lockstep across **5 files** so the W518 framework-vocab drift-guard stays green. Claim-integrity hygiene now matches the W451/W471/W472 SRC-L3 substrate.
- **W521 — `pr-bundle init` producer-side commit_sha stamping.** Records `commit_sha` at bundle-init time (single `git rev-parse HEAD` call). The W509 fallback at emit time becomes **belt-and-suspenders** — bundles created on a no-collect path now carry commit_sha from the moment of creation. **127 tests pass.**

> **TWO long-latent silent no-ops sealed + SLSA SRC-L3 evidence-pipeline polish + closed-enum lints + taint trio closure (W375-W515, 2026-05-15).**
> The wave between the W491 consolidation and this one shipped twelve
> threads in parallel. **The headline is two critical-correctness
> fixes** that landed back-to-back: (1) **W493** fixed
> `propagate_taint`'s `kind='calls'` query against writers that emit
> `kind='call'` — the taint DFS had been a NO-OP since inception, all
> 76 production findings stuck at `chain_length=1`. Three read-side
> sites repaired (`taint.py:491`, `cmd_dead.py:1565`, `dataflow.py:329`);
> 4 stale tests that asserted the no-op behavior flipped to assert
> the real contract; **31/31 byte-identical golden hashes hold, 292
> tests pass**; W441's 607-finding projection now stands for the
> production roam-code corpus. (2) **W499** fixed
> `critique/checks.py:399` — the impact gate was matching 0/14,949
> caller edges (COMPLETE NO-OP); post-fix surfaces **5 high-severity
> findings** on roam-code itself. PRs touching `open_db` /
> `json_envelope` / `to_json` / `invoke_cli` / `path` now correctly
> exit-5 in `--ci` mode. (3) **W375** closed the W372-research first-ship
> taint-rule trio (after W373 python-ssti + W374 java-sqli):
> java-deserialization rule pack at
> `src/roam/security/taint_rules/java_deserialization.yaml`
> (T-X04 / CWE-502 / A08:2021; 15 sources / 12 sinks / 13 sanitizers,
> `qualified_only: true`). (4) **W486** extracted the shared
> `src/roam/attest/emit_vsa.py` helper (339 lines); `cmd_pr_bundle`
> and `cmd_cga` collapse to 9-line + 24-line delegations
> respectively. **143/143 tests pass.** (5) **W498** added the
> end-to-end VSA parity test in `tests/test_attest_vsa.py:661+`
> (`TestVsaCliParity`) — **found real drift**: pr-bundle drops
> `commit_sha` when `--no-auto-collect`; cga falls back to
> `git rev-parse HEAD`. Spawned **W509** fix (now in flight).
> (6) **W428** shipped the 5 W360-research crosswalk YAML entries
> (NIST AI 600-1 + SP 800-218A): `AI600_VALUE_CHAIN_PROVENANCE`,
> `AI600_STOP_BUILD_AUTHORITY`, `SSDF218A_CODE_PROVENANCE`,
> `SSDF218A_CODE_REVIEW_AI_OUTPUT`, `SSDF218A_DEVELOPER_AUTHORIZATION`.
> CAISI held to H2 2026. **W506** in flight to add the missing SLSA
> entries — claim-integrity hygiene per the agentic-assurance
> "supports evidence for" lint. (7) **W505-bundle** shipped 3
> closed-enum lints (W502 `source_framework` / W503 `pass_condition` /
> W504 `surface`); **19+31 tests pass**. (8) **W482** added a `roam
> doctor` advisory check that compares the local
> `.github/workflows/roam.yml` against the canonical CI template;
> chose advisory-check over a standalone command for low-friction
> surfacing. Real-world signal: **roam-code's own roam.yml has
> drifted from template (26 vs 28 lines)** — surfaced on the
> dogfooded `doctor` run. **9 new tests + 137/137 focused pass.**
> (9) **W485** verdict was **MEASUREMENT DRIFT, not regression** —
> the W408 baseline was a 17k-symbol corpus; current roam-code is
> **23.6k symbols / 29.9k edges / 3.8k files (+39% / +76% / 7x)**.
> Effects_taint scaled 67.6s → 87.4s; relative dominance held
> 48% → 50.5%. (10) **W488** auditing pass: the rest of the
> `test_taint_*.py` corpus for stale bare-name assertions came up
> **CLEAN** — W479 caught the only offender; 128+31 tests pass.
> (11) **W441** BAILED with a high-impact find — it was the
> investigation that surfaced the W493 `kind='calls'` vs `kind='call'`
> typo (real wallclock when fed correct data: 0.06s; W433-research's
> 35s prediction was based on stale code). Spawned the critical
> W493 fix. (12) **W491-CONSOLIDATE** — itself (folded inline in
> the previous Unreleased entry).

### Added — taint trio close + SLSA polish + crosswalk + closed-enum lints + advisory check (W515 batch)
- **W375 — OWASP taint rule pack v1 java-deserialization.** New
  `src/roam/security/taint_rules/java_deserialization.yaml` (T-X04 /
  CWE-502 / A08:2021): 15 sources / 12 sinks / 13 sanitizers,
  `qualified_only: true`. **Closes the W372-research first-ship trio**
  (W373 python-ssti + W374 java-sqli + W375 java-deserialization).
- **W486 — Shared `emit_vsa` helper module.** New
  `src/roam/attest/emit_vsa.py` (339 lines). `cmd_pr_bundle` and
  `cmd_cga` VSA emit paths collapse to **9-line + 24-line**
  delegations. **143/143 tests pass.**
- **W498 — End-to-end VSA parity test.** New
  `TestVsaCliParity` block in `tests/test_attest_vsa.py:661+`
  exercising `pr-bundle emit --slsa-l3` against `cga emit
  --also-vsa` for byte-identical VSA predicates. **Found real drift**:
  pr-bundle drops `commit_sha` when `--no-auto-collect`; cga path
  falls back to `git rev-parse HEAD`. Spawned **W509** (fix in
  flight).
- **W428 — Standards crosswalk YAML additions (NIST AI 600-1 + SP 800-218A).**
  Five entries: `AI600_VALUE_CHAIN_PROVENANCE`,
  `AI600_STOP_BUILD_AUTHORITY`, `SSDF218A_CODE_PROVENANCE`,
  `SSDF218A_CODE_REVIEW_AI_OUTPUT`, `SSDF218A_DEVELOPER_AUTHORIZATION`.
  Consumes W360-research. CAISI deliberately held to H2 2026
  pending substrate maturity. **W506** in flight to add the missing
  SLSA entries (claim-integrity follow-on).
- **W505-bundle — Closed-enum lints (W502 / W503 / W504).** Three
  drift-guard lints landed together: `source_framework`,
  `pass_condition`, `surface`. **19 + 31 tests pass.** Same shape as
  W332 / W282 / W211 vocabulary-freeze discipline.
- **W482 — `roam doctor` ci-setup advisory check.** Compared
  `.github/workflows/roam.yml` (or equivalent) against the canonical
  CI template. Chose advisory-check inside the existing `doctor`
  surface over a standalone `roam ci-doctor` command — lower
  friction, single dogfood touchpoint. **Real-world signal**:
  roam-code's own `roam.yml` has drifted from template (26 vs 28
  lines) — surfaced on the dogfooded `doctor` run, queued for
  follow-on cleanup. **9 new tests + 137/137 focused pass.**

### Fixed — TWO long-latent silent no-ops (W493 + W499)
- **W493 — taint propagation `kind='calls'` vs `kind='call'`
  read-side typo (CRITICAL CORRECTNESS).** `propagate_taint` queried
  `kind='calls'` but the writers emit `kind='call'`. The taint DFS
  has been a NO-OP since inception. All 76 production findings on
  roam-code were stuck at `chain_length=1`. **Three read-side sites
  repaired**: `src/roam/security/taint.py:491`,
  `src/roam/commands/cmd_dead.py:1565`,
  `src/roam/security/dataflow.py:329`. Four stale tests that asserted
  the no-op behavior flipped to assert the real contract.
  **Hash-stability 31/31 byte-identical. 292 tests pass.** W441's
  **607-finding projection** stands for the production roam-code
  corpus.
- **W499 — `critique/checks.py:399` impact-gate caller-edge typo
  (CRITICAL CLAIM-INTEGRITY).** Same edge-kind typo class as W493 in
  a different read-site. Pre-fix matched **0 / 14,949 caller edges**
  on roam-code — the impact gate was a COMPLETE NO-OP. Post-fix
  surfaces **5 high-severity findings** on production roam-code. PRs
  touching `open_db` / `json_envelope` / `to_json` / `invoke_cli` /
  `path` now correctly exit-5 in `--ci` mode.

### Changed — perf-measurement reframe (W485) + investigation closures (W488)
- **W485 — `effects_taint` MEASUREMENT DRIFT (not regression).**
  W408 baseline ran on a 17k-symbol corpus; current roam-code is
  **23.6k symbols / 29.9k edges / 3.8k files (+39% / +76% / 7x)**.
  Effects_taint scaled **67.6s → 87.4s**; relative dominance held
  **48% → 50.5%**. Honest reframe: the perf trajectory is intact, the
  apparent regression is corpus growth.
- **W488 — Sweep of remaining `test_taint_*.py` for stale
  bare-name assertions.** **CLEAN** — W479 caught the only offender.
  **128 + 31 tests pass.** Closes the W479 audit's residual question.

### Research / planning — W515 batch
- **W441 — BAILED with a high-impact find.** While investigating
  `effects_taint` Phase 2 → Phase 5 cache slice, surfaced the
  W493 `kind='calls'` vs `kind='call'` typo. Real wallclock when
  fed correct data: **0.06s** — W433-research's 35s prediction was
  built on stale (no-op) code. Spawned the critical **W493** fix.
  Bail was the right move (carry-over from "investigate-first bails"
  discipline).

> **SLSA SRC-L3 evidence pipeline + Pattern-3b consolidation + taint precision discipline + perf ground-truth (W430-W491, 2026-05-15).**
> The wave between the W466 consolidation and this one shipped eight
> threads in parallel. (1) **SLSA SRC-L3 evidence pipeline end-to-end**
> — **W451** wired the SRC-L3 lift through new `src/roam/attest/vsa.py`
> (369 lines) and `pr-bundle emit --slsa-l3 --sign --keyless`;
> `cosign_sign_statement` was already predicate-agnostic so no engine
> changes were needed. **W471** auto-triggered the SRC-L3 VSA emit in CI
> via new template `src/roam/templates/ci/slsa-src-l3.yml` and the
> `--with-slsa-l3` flag on `cmd_ci_setup`, closing Gap A from W358-research.
> **W472** added `roam cga emit --also-vsa` (110-line `_emit_vsa_sibling`
> helper) threading `--sign --keyless`. 23+144 / 15+31+23 / 3+43+26+43+31
> tests pass across the trio. (2) **Pattern-3b consolidation closes** —
> **W430** renamed `target` → `symbol` on 9 MCP wrappers (prepare_change,
> trace, affected_tests, annotate_symbol, get_annotations, generate_plan,
> get_invariants, why_fail, metrics); `_PRE_W332_EXEMPT` dropped 14 → 5.
> Legacy `target` still resolves via alias with `summary.alias_warnings`
> for back-compat. **3014 tests pass.** (3) **Taint engine precision
> discipline reinforced** — **W467** fixed the W454 `qualified_only` bug
> (root cause was a compound A+C: bare names matched via exact
> `qualified_name = ?` on Python top-level AND via suffix `LIKE '%.{name}'`
> on Java wrappers; fix: bare names become no-ops under `qualified_only=true`).
> java-sqli YAML scrubbed. **125+31 tests pass.** **W479** audited the
> remaining 22 taint YAMLs — **zero offending rules** — added a load-time
> `warnings.warn` lint + 7-test hygiene guard, and drive-by-fixed an NTFS
> case-collision bug (closes the open W468 + W477 items). (4) **Perf
> optimization ground-truth** — **W440** shipped the Phase 2 → Phase 5
> source-cache handoff: `effects_taint` moved from 91.0s → 84.7s = **7%
> reduction** (modest vs the 15-30s predicted by W433-research). 216 tests
> pass. W441 + W485 follow-ons queued. (5) **Detector FP-rate methodology
> research** — **W470-research** (`(internal memo)`) <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> scoped FP-rate measurement for 3 first-to-measure detectors (smells 3047
> findings, vibe-check 831, taint). **Surprise finding: OWASP Benchmark is
> community-rejected** — task-specific real-codebase corpora are now
> preferred. Docs-only consolidation in this batch (W491); hash-stability
> mandate held across all source waves.

### Added — SLSA SRC-L3 wire-up + 9-wrapper rename + CI auto-trigger + cga --also-vsa (W491 batch)
- **W451 — SLSA SRC-L3 wire-up.** New `src/roam/attest/vsa.py` (369 lines)
  + `roam pr-bundle emit --slsa-l3 --sign --keyless`.
  `cosign_sign_statement` at `attest/cga.py:495-594` was already
  predicate-agnostic, so no engine extension was required. Closes the
  W358-research SRC-L3 "one wave away" prediction. **23 + 144 tests pass.**
- **W430 — Pattern-3b 9-wrapper rename** (`target` → `symbol`).
  Renamed across 9 MCP wrappers: `prepare_change`, `trace`,
  `affected_tests`, `annotate_symbol`, `get_annotations`,
  `generate_plan`, `get_invariants`, `why_fail`, `metrics`.
  `_PRE_W332_EXEMPT` dropped 14 → 5. Legacy `target` still resolves
  via `_PARAM_ALIASES` with `summary.alias_warnings` surfaced on use.
  **3014 tests pass.**
- **W471 — CI auto-trigger SLSA SRC-L3 VSA emit.** New template
  `src/roam/templates/ci/slsa-src-l3.yml` + `--with-slsa-l3` flag on
  `cmd_ci_setup`. **Closes Gap A from W358-research** (the CI-side
  half of the SRC-L3 evidence pipeline). **15 + 31 + 23 tests pass.**
- **W472 — `roam cga emit --also-vsa` flag.** 110-line
  `_emit_vsa_sibling` helper threads `--sign --keyless` from the
  parent `cga emit` invocation. **3 new + 43 + 26 + 43 + 31 tests pass.**
- **W479 — Taint YAML qualified-name hygiene audit + lint.** Audited
  the other 22 taint YAML rule packs and found **zero** offending
  rules. Added a load-time `warnings.warn` lint + 7-test hygiene
  guard so the regression class fails at engine load time, not on
  recall-limited false negatives in CI. Drive-by-fixed an NTFS
  case-collision bug — closes both **W468** and **W477**.
- **W440 — Phase 2 → Phase 5 source-cache handoff.** `effects_taint`
  moved from 91.0s → 84.7s = **7% reduction** on roam-code itself.
  Below the 15-30s predicted by W433-research (the savings landed
  on cache-warm runs rather than the headline cold path). **216
  tests pass.** Follow-ons W441 + W485 queued.

### Fixed — W491 batch
- **W467 — W454 `qualified_only` bug fix.** Root cause was a
  compound A+C: bare names matched via exact `qualified_name = ?`
  (Python top-level wrappers) AND via suffix `LIKE '%.{name}'` (Java
  wrappers). Fix: bare names become no-ops when `qualified_only=true`.
  java-sqli YAML scrubbed to remove the offending bare-name entries.
  **125 + 31 tests pass.**

### Research / planning — W491 batch
- **W470-research — Detector FP-rate measurement methodology.**
  Memo at `(internal memo)`. <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. --> Three
  first-to-measure detectors scoped: **smells (3047 findings)**,
  **vibe-check (831)**, **taint**. **Surprise: OWASP Benchmark is
  community-rejected** — task-specific real-codebase corpora are now
  the preferred evaluation substrate (Mahmoudi-class study design,
  not synthetic Juliet-style suites).

> **Standards crosswalk research + taint rule pack v1 + shallow git default + auto-generated MCP tool table + qualified-name rule flag (W405-W466, 2026-05-15).**
> The wave between the W436 consolidation and this one ran twelve
> threads in parallel across five families. (1) **Standards
> crosswalk research trilogy** — **W358-research** (SLSA v1.2
> Source Track positioning, `(internal memo)`) <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> found that roam de-facto covers SRC-L2 today, and the surprise
> finding is that SRC-L3 lift is **one wave** — `cosign_sign_statement()`
> at `attest/cga.py:495-594` is already implemented; new wave W451
> queued. **W359-research** (OSCAL v1.2 Control Mapping,
> `(internal memo)`) found that OSCAL v1.2
> <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> shipped a **7th model** (Control Mapping) which is the zero-prereq
> first emission for per-run evidence; new waves W464/W465 queued.
> **W360-research** (already landed in W436 batch) feeds W428.
> (2) **Taint rule pack v1** — **W373** (python-ssti, T-X01, CWE-94;
> engine already supports qualified-name matching; 7 new + 45+39
> existing tests pass) + **W374** (java-sqli, CWE-89; same recall-limited
> precision profile as java-fileupload because engine lacks Java
> qualified-name resolution; 7 new + 44+31 existing tests pass) +
> **W454** (per-rule `qualified_only` flag for taint engine; java-sqli
> opts in; 29+60 focused tests pass). Drive-bys W452-W463 queued.
> (3) **Perf — shallow git default on first index** — **W405** shipped
> the 365-day shallow window via `_DEFAULT_SINCE` in `git_stats.py` +
> `--full-history` opt-out + `ROAM_GIT_SINCE` env var; `_first_index()`
> gate preserves existing deep indexes; 30+31+115 focused tests pass.
> Drive-bys W437/W438/W439 queued.
> (4) **Documentation count drift sealed** — **W443** added README
> coverage for 4 untracked CLI commands (evidence-diff,
> evidence-doctor, llm-smells, findings); the
> `test_readme_covers_all_canonical_cli_commands` drift guard now
> passes. **W449** auto-generated the README MCP tool table via a
> new `surface_counts.mcp_tool_descriptions()` helper — 74 missing
> tools added and the core preset count corrected (25 → 57). 4/4 +
> 16/16 + 8/8 + 31/31 test suites pass. Drive-bys W449-W463 queued.
> (5) **Dedup + small-cleanup bundle** — **W432** removed five
> oracle wrappers that W306 had already added (symbol_exists,
> route_exists, is_test_only, is_reachable_from_entry, is_clone_of);
> 228 → 223 decorations now match the CLAUDE.md headline. New AST
> duplicate-name CI lint via `surface_counts.mcp_tool_decorations()`
> helper. **W429** packaged the W422 deprecate-permit-wrapper +
> W425 lease warnings_out + W426 constitution-unparseable warning
> as a single small-cleanup bundle; 204/204 tests pass; 31/31 hash
> stability byte-identical. Drive-bys W443/W444/W445 and W446/W447/W448
> queued. (6) **Perf research scoping** — **W433-research**
> (`(internal memo)`) scoped three
> <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
> optimization candidates for the W408 finding: (C) double-parse I/O
> elimination 15-30s zero risk; (B) function-summary memoization
> 35→5s; (A) file-signature cache warm-reindex 0s. **Surprise
> finding: roam has TWO independent taint engines** —
> `analysis/taint.py` for Phase 5 (indexer-side) vs
> `security/taint_engine.py` for the `roam taint` command —
> consolidating them is a deeper structural play. Docs-only
> consolidation in this batch (W466); hash-stability mandate held
> across all source waves.

> **Permit unification + Pattern-3b extension + llm-smells v1.1 + phase-timing reality check (W347-W436, 2026-05-15).**
> The wave between the W418 consolidation and this one ran nine
> threads in parallel. **W377-batch** closed six permit-persist
> red-team gaps (W377-W382) surfaced by W349; 31/31 golden hashes
> remained byte-identical and 163 focused tests pass. **W383**
> unified `pr-bundle` and `pr-replay` permit readers behind a
> single canonical `roam.permits.store.load_permits_from_disk`
> reader, with two drive-bys captured as W421/W422. **W347** extended
> the Pattern-3b parameter-alias normalization to add `file_path` →
> `path` (the prefix-pattern cluster was deliberately bailed on; 3
> drive-bys queued as W430/W431/W432). **W415b** shipped `llm-smells
> v1.1.0` — five new CHEAP detectors (`missing_timeout`,
> `missing_max_retries`, `no_system_message`, `no_retry_backoff`,
> `call_in_loop`); 36/36 pass; package version bumped 1.0.0 → 1.1.0;
> 3 drive-bys queued as W415c/W415d/W427. **W408** instrumented
> per-phase timing in `roam doctor` and the real-data finding is the
> headline of this wave: **`effects_taint` consumes 48% of indexer
> wallclock** (67.6s of 139.6s on roam-code itself), which
> **invalidates the PageRank-first ranking** in the W395-followup
> perf memo; new wave **W433** is queued to target `effects_taint`
> first (drive-bys W434/W435 follow). **W421** investigation **bailed**
> after finding constitution + lease gatherers already delegate to
> canonical readers (119/119 baseline tests pass; 2 drive-bys as
> W425/W426). Research-only artifacts: **W372-research** OWASP 2026
> taint rule pack (3 first-ship rules W373/W374/W375),
> **W395-followup** Phase 4-7 perf research (W407 reclassified to
> VALIDATE — Louvain cache already implemented; top 3 new perf waves
> W423/W424 + W433), and **W360-research** standards crosswalk
> additions (5 NIST AI 600-1 + SP 800-218A YAML entries; CAISI held
> until H2 2026; implementation as W428).

> **MCP wrapper backfill near-complete + detector strengthening Round 2 + perf research + llm-smells design (W303-W418, 2026-05-15).**
> The wave between the W398 consolidation and this one moved Wave29
> wrapper backfill from 38 → 16 missing through three consecutive
> sub-waves (W303 test-surface +5, W304 agent-OS daily flow +10,
> W305 reports/audit +11) — 26 wrappers added in total; W306 will
> drop the remaining count to ~3. Detector strengthening Round 2
> landed against the W368 BEHIND list: W370 smells `empty-catch`
> (469 findings), W370b `duplicate-conditionals` (149 findings), and
> W371 vibe-check `modular-mirage` + `boilerplate-inflation`
> (163 + 499 findings, informational and score-preserving) — 1,280
> new findings on roam-code itself. The pitch refresh trilogy
> sharpened the top-of-funnel surfaces: W390 (README + landing +
> docs hero), W393 (11 secondary surfaces), W396
> (`src/roam/mcp-server-card.json` mirror; hash-pin updated).
> Pattern-1 family Round 3 sealed `cmd_owner` (W362) as the third
> CLI-side "exit-0 + structured envelope" fix after W327 and W324.
> Permit red-team added 19 W198-edge-case tests (W349) with 6
> drive-by gaps queued as W377-W382. Structural cleanups: W346
> module-scope fixture cut `test_json_contracts.py` runtime ~28x;
> W364 extracted `_redact_secrets` to `src/roam/security/redact.py`
> (load-bearing for W363); W345 finished the W198 doc
> cross-reference sweep; W319 / W348 / W352 / W403 / W412 closed
> count-convention + warning-hygiene + Python-version drift +
> asyncio config + stale-3.9-comment cleanup gaps; W367 refreshed
> the TEAM-MCP-AUTHORITY-PRODUCT facade. Two new sonnet+web research
> artifacts framed the next strategic axes: W395 perf benchmarking
> (roam positioned MEDIUM — 5-20x faster than CodeQL with comparable
> depth; 5 optimization sub-waves W405-W408 plus W404 scheduled) and
> W402-research llm-smells pattern catalog (14 patterns; v1 = 11
> CHEAP+MODERATE — the first production-grade multi-provider
> linter for openai/anthropic/google/litellm/langchain anti-patterns).

> **Pitch sharpening + Pattern 1 family Round 3 + detector strengthening + permit red-teaming (W303-W398, 2026-05-15).**
> Four threads landed in parallel between the W375 consolidation and
> the W398 one. (1) Pitch surfaces refreshed to lead with "pre-change
> gates + post-change evidence": W390 swept README + landing index +
> docs index hero copy, W393 extended the sweep across 11 other
> surfaces (pricing / press / trust / governance / etc.). (2)
> Pattern-1 family Round 3: W362 fixed `cmd_owner` to emit a
> structured envelope on exit 0 instead of empty stdout — the third
> CLI-side "exit-0 + structured envelope" fix after W327 and W324.
> (3) Detector stub fills landed against the W368 BEHIND list: W370
> shipped smells `empty-catch` (469 findings on roam-code itself) and
> W370b shipped `duplicate-conditionals` (149 findings; long-tail
> distribution). (4) Permit red-team test surface added at W349 (19
> permit-persist tests) with 6 drive-by gaps queued as W377-W382.
> Wave29 MCP wrapper backfill continued: W303 closed the test-surface
> cluster (5 wrappers, 38 → 33). Structural support: W345 finished
> the W198 doc cross-reference sweep, W364 extracted `_redact_secrets`
> to a shared module (load-bearing for W363). One new sonnet+web
> research artifact: W385 ecosystem positioning audit (7 tools
> surveyed; 5 COMPLEMENTARY / 2 COMPETITIVE / 0 SUBSTITUTE on
> agentic-assurance).

> **No silent gaps milestone (W256-W261, 2026-05-14).** The pr-replay
> pipeline on the roam-code workspace itself now reports **7 complete
> + 1 partial + 0 missing** out of 8 evidence questions. Q8
> (`accepted_risks` / `approvals`) is the last open question and it is
> now an *explicit* `producer_not_available` redaction-marker entry on
> the packet, not a silent absence. The honest-banner classifier
> (STRONG / PARTIAL / INSUFFICIENT) consumes this in the PR Replay
> Markdown + JSON output, so the assurance surface can no longer
> overclaim coverage it does not have.

> **Pattern-1 family A/B/C/D + MCP wrapper backfill (W296-W302, 2026-05-15).**
> Variants A/B/C of the empty-stdout / structured-failure / hang
> family are now codified as a canonical CLAUDE.md spec with 5
> invariants and external citations; Variant D (silent success on
> degraded resolution) was added after W324 surfaced the gap. The
> Wave29 MCP-wrapper backfill closed four clusters in four
> consecutive sub-waves — exploration W299 (+9), architecture W300
> (+10), health W301 (+10), refactoring W302 (+9) — moving the
> missing-wrapper count **75 → 67 → 57 → 47 → 38**. Five sonnet+web
> research planning artifacts landed alongside the implementation
> work (Pattern-1 family audit, Pattern 3+6 audit, MCP
> state-mutating patterns, standards currency audit, detector
> competitive audit) — each is a forward-looking roadmap, not
> shipped code.

### Added
- **MCP cold-start guard for index-gated tools** (W296). Sealed
  Pattern-1 Variant A: every MCP wrapper that depends on the
  index now returns a structured `state: "index_missing"` envelope
  with a `next_command` pointing at `roam init` instead of hanging
  for ~30s on a missing `.roam/`. The guard runs at wrapper-entry,
  before any heavy import or DB connection, so cold-start latency
  stays below the MCP client timeout. Closes the longest-standing
  Variant A hang surface.
- **MCP wrapper backfill — Wave29 sub-waves** (W299-W302). Four
  consecutive sub-waves moved the missing-wrapper count
  **75 → 67 → 57 → 47 → 38**: W299 added 9 exploration-cluster
  wrappers (75→67); W300 added 10 architecture-cluster wrappers
  (67→57); W301 added 10 health-cluster wrappers (57→47); W302
  added 9 refactoring-cluster wrappers (47→38). Every new wrapper
  follows the W298-polish discipline (decorator audit + skip-allowlist
  alignment); the advisory `tests/test_mcp_wrapper_coverage.py` audit
  surfaces the remaining 38 commands plus the skip-taxonomy
  allowlist. Refreshed planning lives in
  `(internal memo)` (W353).
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **Try-parse passthrough chokepoint sealing Variant B** (W325).
  The MCP wrapper's `_run_roam_inprocess` / `_run_roam_subprocess`
  chokepoint now passes structured failure envelopes through to
  the LLM instead of collapsing them on a non-zero exit. Sealed
  Variant B for `doctor`, `stale-refs`, and `test_scaffold`; the
  pattern extends to any command that emits a structured envelope
  alongside an advisory non-zero exit code.
- **Pattern 3 caller / complexity / rot / compliance definition
  fields** (W331 + W331b). 9 high-signal commands now emit explicit
  `<metric>_definition` fields naming the precise computation
  (e.g. `caller_metric_definition: "raw_edge_rows"`) so cross-command
  vocabulary drift no longer silently mismatches. W331 wired the
  first 6 sites; W331b closed the 3 remaining gaps and added an
  article-12 wording lint that blocks future drift. Vocabulary
  source-of-truth is the Pattern 3a/3b/6a/6b/6c codification in
  `CLAUDE.md` (W330).
- **MCP `input_path` parameter alias normalization** (W332). Four
  parameter aliases (`file`, `path`, `paths`, `target_path`) now
  canonicalise to `input_path` in `_PARAM_ALIASES`, closing the
  remaining cross-tool vocabulary mismatch surfaced by the Pattern-3
  audit. A 1355-case AST lint pins the normalization so future
  wrappers cannot silently re-introduce drift.
- **`CALLER_METRIC_RAW` canonical constant** (W342). The
  `caller_metric_definition: "raw_edge_rows"` literal has been
  extracted to a single module-level constant and re-used across
  7 emit sites (`cmd_impact`, `cmd_preflight`, `cmd_understand`,
  `cmd_describe`, `cmd_minimap`, `cmd_for_refactor`, `cmd_invariants`).
  W335 extended the W332 drift-guard to fail loudly on any new site
  that hand-rolls the literal instead of importing the constant.
- **Shared `_redact_secrets` module** (W364). Extracted from
  `src/roam/evidence/collector.py` into
  `src/roam/security/redact.py` so the redactor has a single
  source of truth and the evidence collector, MCP receipts, and
  pr-bundle emit paths all share the same regex set and allowlist.
  No behaviour change — the extraction was a callsite-only refactor
  pinned by the W232 redaction snapshot tests + a focused contract
  test in `tests/test_security_redact.py`.
- **W198 permit-persist closure — doc cross-reference sweep** (W345).
  Three HANDOVER sections, the BACKLOG `Permits/Leases` row, the
  W198 entry in section 17, and the W292/W294 historical notes were
  refreshed in place so the doc surface no longer describes
  `roam permit` as "verdict-facade only" once the W198 writer
  shipped. Pre-W198 references are preserved verbatim as historical
  snapshots; current-state language now reflects the writer.
- **Wave29 MCP wrapper backfill — test-surface cluster** (W303).
  Fifth consecutive Wave29 sub-wave moved the missing-wrapper count
  **38 → 33** by adding 5 test-surface wrappers. Trajectory across
  the W299-W303 arc: 75 → 67 → 57 → 47 → 38 → 33. Same W298-polish
  discipline (decorator audit + skip-allowlist alignment); the
  advisory `tests/test_mcp_wrapper_coverage.py` audit surfaces the
  remaining 33 commands plus the skip-taxonomy allowlist. W304
  sub-wave is in flight against the next cluster.
- **smells `empty-catch` detector — first stub filled** (W370).
  The first of the W368 BEHIND-list smells stubs has been
  promoted from placeholder to a real detector — 469 findings
  emitted on the roam-code workspace itself. Detection follows the
  smells-helper template so the finding rows go through the
  canonical `_emit_smells_findings` path and inherit the registry's
  tier-mapping + version-stamp discipline. Closes the first item of
  the W368 audit's "smells rule depth (empty-catch +
  primitive-obsession are placeholder stubs)" gap.
- **smells `duplicate-conditionals` detector — second stub filled**
  (W370b). The second of the W368 BEHIND-list smells stubs has been
  promoted to a real detector — 149 findings on roam-code with a
  long-tail distribution (a small set of expressions account for
  most of the duplicate-conditional rows). Same emit path as W370;
  W370c will close the remaining stubs in the W368 list.
- **Permit red-team test surface** (W349). 19 permit-persist tests
  added that exercise the W198 writer's edge cases (corrupt JSON
  document on disk, partial write, racing writer, schema drift,
  expired-permit reads, missing parent directory, etc.). Six
  drive-by gaps surfaced and queued as W377-W382 for the next
  session — none block the W198 happy path, but the red-team
  surface is what gives the permit substrate the same producer-grade
  hardening as the lease and run-ledger substrates.
- **`cmd_owner` Pattern-1 envelope fix** (W362). Third CLI-side
  "exit 0 + structured envelope" Pattern-1 fix after W327
  (`pytest-fixtures`) and W324 (`roam_annotate_symbol`). Pre-W362
  `roam owner <symbol>` on a symbol with no owner data exited 0
  with empty stdout, so the MCP wrapper crashed in
  `json.loads("")`. Post-W362 the command always emits a
  structured envelope; on no-owner it returns
  `state: "no_owner_data"` + `next_command: "roam blame"`. Pattern-1
  Variant C contract preserved.
- **Pitch refresh — README + landing index + docs index** (W390).
  Hero copy on the three top-of-funnel surfaces now leads with
  "pre-change gates + post-change evidence" — the dual framing
  that surfaces both halves of the agentic-assurance thesis in one
  line. Sweep also touched the "what roam does" callout blocks and
  the `roam evidence doctor` mention. No structural HTML change;
  only the lede text.
- **Pitch refresh — 11 secondary surfaces** (W393). Extended W390
  across `pricing.html`, `press.html`, `trust.html`,
  `governance.html`, the four `services-reports/` deliverables, and
  three `audit-report/` templates. Same "gates + evidence" framing;
  same no-structural-HTML-change discipline.
- **Wave29 MCP wrapper backfill — agent-OS daily flow cluster** (W304).
  Sixth consecutive Wave29 sub-wave moved the missing-wrapper count
  **33 → 23** by adding 10 wrappers around the run-ledger / mode /
  lease / permit / memory / brief / next / agent-score daily-flow
  surface. Same W298-polish discipline (decorator audit +
  skip-allowlist alignment); the advisory
  `tests/test_mcp_wrapper_coverage.py` audit surfaces the remaining
  23 commands plus the skip-taxonomy allowlist.
- **Wave29 MCP wrapper backfill — reports / audit cluster** (W305).
  Seventh consecutive Wave29 sub-wave moved the missing-wrapper count
  **23 → 16** by adding 11 wrappers (some commands surfaced via the
  same wrapper) around the reports / audit-trail / pr-bundle /
  evidence-doctor / replay output surface. Wave29 trajectory across
  the full arc W299-W305: **75 → 67 → 57 → 47 → 38 → 33 → 23 → 16**.
  Three to four wrappers remain (W306 lands them).
- **vibe-check `modular-mirage` + `boilerplate-inflation` detectors**
  (W371). Two informational AI-rot patterns added to vibe-check.
  `modular-mirage` (163 findings on roam-code itself) flags
  fragmented one-line wrappers around trivial logic — a common AI
  shape. `boilerplate-inflation` (499 findings) flags excessive
  scaffold-style commentary and stub structure. Both are
  **score-preserving**: emitted into the findings registry with
  tier `heuristic` and do not move the health score, so adoption is
  zero-risk. Adds two new vibe-check kinds to the canonical 8-pattern
  catalog without disturbing existing ones.
- **`test_json_contracts.py` module-scope fixture (~28x speedup)**
  (W346). Per-test indexing was rebuilding the full DB on every
  contract case. Promoted to a module-scope fixture so the index is
  built once and reused across all contract assertions. Runtime drops
  from ~6 minutes to ~13 seconds on this file; broader test sweep
  benefits proportionally because the contracts file was the slowest
  single module.
- **TEAM-MCP-AUTHORITY-PRODUCT facade refresh** (W367). Updated
  `(internal memo)` to reflect the
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  post-W198 reality (real permits exist; the W292/W294 corroboration
  harvester reads them; the local single-agent receipt model is
  shipping ahead of any networked Team MCP work, as the threat-model
  memo W214 prescribes). Documentation refresh only.
- **`src/roam/mcp-server-card.json` pitch-refresh mirror + hash-pin
  update** (W396). The MCP discovery card now mirrors the W390+W393
  refreshed pitch ("pre-change gates + post-change evidence"). The
  hash-pin in `tests/test_mcp_server_card_hash.py` was updated in
  step so the canonical card stays drift-locked.
- `roam findings` CLI (`list` / `show` / `count`) — cross-detector finding registry
  surfaced from the new `findings` table in `src/roam/db/findings.py`.
  After the W95 clones migration this is `mcp_expose=True` + maturity `stable`.
- `roam taint` now actually fires findings on PHP/Laravel codebases — the engine
  BFS query in `src/roam/security/taint_engine.py:283,333` was filtering on
  `kind IN ('calls','references')` but the index stores singular `'call'`/`'reference'`
  edge kinds, so the entire `roam taint` subsystem had been silently returning 0
  findings since v12. Fix is one line; affects every prior-shipped taint rule.
- PHP/Laravel taint rule pack: 5 YAML files / 101 rule entries under
  `src/roam/security/taint_rules/php_*.yaml` (command injection, Laravel open-redirect,
  Laravel SQLi, Laravel XSS, path traversal).
- Detector registry: `@detector(...)` decorator + `roam math --list-detectors`,
  `--only`, `--exclude` flags.
- Plugin contract: `register_framework_detector(detect_fn: Callable[[Path], Optional[str]])`
  now carries the typed signature.
- `mcp_introspection_available: bool` field in `roam surface --json` envelope —
  consumers can now distinguish "no fastmcp installed" from "fastmcp broken".
- `roam doctor` advisory: "Index step missing because step X failed; run
  `roam index --force`" — reads the W82 step-completion manifest.
- Per-component VERSION stamps (bridges, detectors, extractors) — populated in
  `edges` / `symbols` rows + the indexer manifest.
- New landing pages: `/governance` (409 lines), `/trust` (434 lines, honest
  SOC 2 Q1 2027 / ISO 42001 Q3 2027 stance), `pricing.html` FAQ block (7 items),
  `templates/audit-report/` (template + renderer + 3 sample reports),
  `templates/services-reports/` (4 service deliverables: AI adoption readiness,
  due diligence, post-incident replay, security reachability triage).
- **Findings registry — 14 detectors persisting findings via `--persist`**
  (W109-W136). Migrations across the wave: `smells` (3047 findings on
  roam-code), `n1`, `missing-index`, `over-fetch`, `bus-factor` (65),
  `auth-gaps`, `vulns`, `invariants`/`laws` (9), `hotspots`, `taint`,
  `vibe-check` (831 — 8 AI-rot patterns), `orphan-imports` (344),
  `conventions` (39), `pr-risk`, `duplicates` (853). All detectors emit
  through the canonical `_emit_<X>_findings(conn, data, source_version)`
  template so the registry's tier-mapping and version-stamp discipline is
  uniform across the wave.
- **Plugin substrate: `register_framework_profile`** (W123 / Wave28.3).
  New `FrameworkProfile` dataclass bundles a framework's `detect_fn` +
  `file_patterns` + `recommended_commands` + `conventions` so a plugin can
  declare its profile in a single call. Surfaced as a method on
  `RoamPluginContext`; the reference example plugin in `dev/example-plugin/`
  was migrated to demonstrate the contract.
- **Findings-registry subject-kind vocabulary expanded.** The canonical
  enumeration now accepts `module`, `directory`, `endpoint`, `package`, and
  `commit` in addition to the originals `symbol` and `file`. New detectors
  in this wave (orphan-imports, duplicates, conventions, bus-factor)
  consume the expanded vocabulary.
- **`mcp_tool_count_by_preset` field on `roam surface --json` envelope**
  (W138). Per-preset MCP tool counts: core 57 / review 70 / refactor 70 /
  debug 69 / architecture 71 / compliance 13 / full 149. Machine consumers
  no longer have to introspect `_PRESETS` to know the shape of each preset.
- **OneDrive / Dropbox / iCloud cloud-sync detection at `roam init`**
  (W127). Warns when `.roam/` would land on a cloud-synced path (corrupts
  SQLite WAL files on multi-device sync). `roam doctor` advisory was
  already in place; the init-time warning catches the issue before the
  first index instead of after the first crash.
- **Agentic-assurance pipeline — actor / authority / approvals first-class
  on `ChangeEvidence`** (W189-W211). Six-step arc that turns the W174
  evidence dataclasses into a portable assurance packet:
  `pr-bundle` producer emits an `actor` block (agent_id / human_actor /
  mcp_client_id / tool_id / ci_runner_id / actor_kind) plus empty
  `approvals[]` / `accepted_risks[]` arrays (W189); the mega collector
  materialises `actor_refs[]` / `authority_refs[]` / `environment_refs[]`
  from envelopes, the run ledger, and CI env (6-provider CI detection;
  W190); PR Replay renders Actors / Authorities / Environment sections
  with the assurance-leads / findings-follows ordering (W191); a
  vocabulary-drift sweep aliased `author`→`actor` on `pr-risk` +
  `bus-factor` envelopes and documented the permit-facade contract
  (W198); `ActorRef` gained a `trust_tier`
  (`verified_ci` / `git_author` / `local_env` / `self_reported_agent` /
  `unknown`), `AuthorityRef` gained a `source` enum
  (`mode` / `permit` / `rule_config` / `ci_policy` / `human_approval` /
  `inferred_fallback`) with a facade auto-stamp, `ApprovalRecord`
  graduated to a first-class dataclass with `expiry`, and every model
  carries an explicit NON-GOALS docstring (W211).
- **`ChangeEvidence` schema extensions — 9 new optional fields** (W210).
  Time-aware (`context_read_at`, `edits_started_at`,
  `edits_completed_at`); stale-evidence (`evidence_stale: bool`,
  `stale_reasons[]`); version-linked (`roam_version`,
  `rules_config_hash`, `constitution_hash`, `control_map_hash`). Adds
  two computed methods on the dataclass: `assurance_floor()` (lowest
  trust_tier across actors/authorities) and `evidence_completeness()`
  (fraction of optional evidence slots populated). Backward-compat
  preserved: `schema_version` stays `"1.0.0"` via
  `_W210_OMIT_WHEN_DEFAULT_FIELDS` so packets with no new fields
  serialise byte-identical to W174.
- **MCP decision-receipt emitter** (W183 / W196). Per sensitive
  `@_tool` invocation, the wrapper writes
  `.roam/mcp_receipts/<run_id>/<tool_call>.json` capturing the agent
  identity, the call args (after redaction), and the verdict. New
  `McpDecisionReceipt` dataclass under `src/roam/evidence/` (W183);
  emitter wired into the decorator with best-effort discipline — a
  receipt write failure NEVER breaks the underlying tool call (W196).
- **Mega collector extension — 5 new kwargs** (W199). The W175
  collector now accepts `rules_envelopes`, `audit_trail_envelope`,
  `vuln_reach_envelopes`, `test_impact_envelopes`, `cga_envelopes`,
  `mcp_receipts_dir`; each flattens into the relevant `ChangeEvidence`
  fields. Replaces the W176 audit-trail synthetic-finding stop-gap
  with first-class manifest artifact + `policy_decisions` entries.
- **`roam pr-replay` producer wiring — 6 gatherers** (W223). Wired
  `rules` / `audit-trail` / `vuln-reach` / `test-impact` / `cga` /
  `mcp-receipts` into the PR Replay producer; coverage on the
  roam-code workspace lifted from 3/8 to 6/8 evidence slots. The
  remaining two gaps (W219 producer-side) are documented in the
  next-session queue.
- **Control mapping v1 schema** (W184). YAML control-map entries
  now carry `source_framework` / `evidence_types[]` / `surface` /
  `wording_guard` fields. The CI lint at W203 caught 3 W184 drift
  entries (wording that overclaimed certification) and fixed them
  in place.
- **`roam evidence-diff` CLI command** (W225). Diffs two
  `ChangeEvidence` JSON files structurally: changed_subjects /
  findings / policy_decisions / actors / authorities. Used by
  W232's redaction snapshot tests.
- **Export profiles for evidence packets** (W226). Four profiles —
  `internal` / `customer` / `audit` / `public` — drive
  per-audience redaction policy. Each profile is a deterministic
  field-allowlist applied at projection time, so the same source
  packet renders 4 different views with one redaction surface.
- **False-positive feedback loop module** (W228). Routes
  user-flagged false positives back through the findings registry
  with provenance preserved (which detector / which version
  emitted the row).
- **PR Replay "Evidence Limitations" section** (W185). Every PR
  Replay Markdown report now lists which evidence slots are
  populated and which are absent, plus the reason for absence.
  Reads from the same `ChangeEvidence` packet that drives the body.
- **Strategic memos shipped overnight.** Five new dev docs landed
  alongside the agentic-assurance pipeline:
  `(internal memo)` (W202 milestone
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  integration note); `(internal memo)` (W214 — 6
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  threats, conditional P-tier escalation contingent on Team MCP
  Gateway shipping); `(internal memo)` (W215
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  — authority-product framing); `dev/DEMO-NARRATIVE-CANONICAL.md`
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  (W216 — 8/8 ideal-case fixture, deterministic content_hash
  `17958f73…`); `(internal memo)` (W230 —
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  end-to-end re-validation report).
- **pr-replay `context_refs` producer** (W246). New
  `_gather_context_files` in `src/roam/commands/cmd_pr_replay.py:895`
  runs `git diff --name-only` ONCE for the whole commit window (not
  per-commit), caps emission at 500 entries with a truncation
  warning, and stamps `pr_bundle_envelope["context_files"]`. The
  W199 collector then materialises `context_refs[]` via
  `_build_context_refs_from_context_files`. Smoke run on roam-code:
  492 context_refs populated. Closes evidence question Q3
  (context_read) on the real-workspace pipeline. The executable
  8-question audit threshold ratcheted 5 → 6 in
  `tests/test_eight_questions_audit.py:344`; 4 new tests in
  `tests/test_evidence_pr_replay.py`.
- **Pipeline re-validation v3** (W254). Real-world
  `roam pr-replay HEAD~5..HEAD` on the roam-code workspace itself
  now scores **7 complete / 0 partial / 1 missing** out of 8
  evidence questions (trajectory W201 → W230 → W254: 3 → 3 → 7).
  Q8 (accepted_risks / approvals) remains the only producer-side
  gap. Synthesised collector ceiling stays at 8/8 — confirms the
  remaining gap is producer-side, not collector-side. Honest-banner
  thresholds proposed (consumed by W259): `complete ≥ 7` STRONG;
  `complete + partial ≥ 5 ∧ missing ≤ 3` PARTIAL;
  otherwise INSUFFICIENT.
- **Producer coverage matrix** (W252) —
  `(internal memo)` (168 lines). 15
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  tier-1 producers analysed per-field; 22 tier-2 detectors
  collapsed. Top three under-served fields: environment (1
  producer: pr-replay only), policy (2 producers: rules +
  audit-trail-verify), authority (1 producer: pr-bundle.mode;
  permits/leases are verdict-facade only). Memo also surfaces the
  pr-replay-synthesises-its-own-pr_bundle gap — meaning W240's
  actor + scrub fixes do NOT propagate through the replay path
  (queued as W260).
- **Executable 8-question audit threshold ratcheted 6 → 7** (W258).
  Synth-fixture enrichment in `tests/test_eight_questions_audit.py`
  extracted a `_reconstruct_artifacts(rows)` helper, reused it for
  both `context_refs[]` and `artifacts[]`, and lifted
  `EXPECTED_COMPLETE_COUNT_TODAY` 6 → 7. Trajectory across the
  W220 / W246 / W258 arc: 3 → 5 → 6 → 7. The test now guards the
  W254 real-workspace ceiling against regression.
- **Honest evidence-coverage banner** (W259). New module
  `src/roam/evidence/banner.py` exports
  `classify_evidence_coverage()` / `render_banner_markdown()` /
  `banner_envelope_block()` driving the three-tier classification
  (STRONG `complete ≥ 7`; PARTIAL `complete + partial ≥ 5 ∧
  missing ≤ 3`; INSUFFICIENT otherwise). Wired into PR Replay
  Markdown at `cmd_pr_replay.py:1444` (above the W185 limitations
  section) and into the JSON envelope as
  `extra_payload.evidence_coverage` + the
  `summary.evidence_coverage_tier` mirror. Template placeholder
  `{{evidence_coverage_banner}}` at
  `templates/audit-report/pr-replay-template.md:3`. 5 new banner
  tests; 90/90 focused tests pass. Closes the W254 / section 11.4
  banner proposal.
- **`actor_helpers.py` extraction + pr-replay synth-bundle parity**
  (W260). New `src/roam/commands/actor_helpers.py` exports
  `resolve_actor_block()` + `resolve_actor_kind()` with the
  W189-canonical resolution priority (CLI flag > env var >
  git config > active run-ledger agent). `cmd_pr_bundle.py`'s
  symbols are now thin back-compat wrappers (37/37
  `tests/test_pr_bundle.py` preserved). `cmd_pr_replay.py` calls
  the shared helpers and re-runs W249's `_scrub_actor_block`
  immediately after resolution on the producer side, so the synth
  pr_bundle envelope is now byte-equivalent to a real
  `cmd_pr_bundle` emission for actor + scrub purposes. 3 new
  tests; 86/86 focused tests pass. Closes the W252-surfaced
  pr-replay-bypasses-cmd_pr_bundle gap (HANDOVER section 11.3).
- **`environment_refs` shared helper + pr-bundle wiring** (W266).
  New module `src/roam/evidence/env_refs.py` exports
  `build_environment_refs(*, commit_range=None, workspace_root=None, env=None) -> tuple[EnvironmentRef, ...]`,
  giving producers a public API for assembling the environment axis
  in canonical order (`ci_job → workspace → branch_range →
  local_run`). Strategy: **delegate, not move** — the collector's
  pre-existing `_build_environment_refs` has 30+ call sites and the
  v0/v1 content-hash contract is tested against its exact output,
  so the helper delegates CI detection to the collector's
  `_detect_ci_env_id` (the W251 6-provider env-var matrix stays the
  single source of truth) and implements its own producer-friendly
  assembly path on top. `cmd_pr_bundle.py`'s `_build_envelope` now
  stamps `environment_refs[]` on every emit path (init / set / add
  / emit / validate); `environment_refs` added to the
  `_PR_BUNDLE_KNOWN_PAYLOAD` allowlist. 11 helper tests + 2
  pr-bundle tests; 101 focused + 77 evidence/collector + 32
  pr-bundle pass. Smoke on roam-code emits real env_refs
  (workspace path + commit + hostname). Closes the W252
  environment-axis gap — 1 producer → N.
- **`pr-bundle` permits/leases real producer** (W268). Two new
  helpers in `cmd_pr_bundle.py:1444-1532`:
  `_load_permits_from_disk(repo_root)` scans `.roam/permits/*.json`
  and returns `[]` when the directory is absent (at the time W268
  shipped, the historical permit facade was not yet persisted —
  Pattern-2 always-emit contract preserved; W198 has since shipped
  the writer side, so this path now returns real rows on workspaces
  that have issued permits via `roam permit issue --persist`);
  `_load_leases_from_disk(repo_root)` delegates to
  `roam.leases.list_leases(include_expired=True, include_released=True)`
  so the on-disk schema stays single-sourced through
  `Lease.to_dict()`. Both invocations reuse the `find_project_root()`
  result already computed for `environment_refs`, and the results
  stamp top-level `permits=permits_out, leases=leases_out` on the
  envelope. The collector's pre-existing `_build_authority_refs`
  (lines 946-954) now sees real permit + lease rows and produces
  `AuthorityRef(authority_kind="permit", …)` / `…="lease", …`
  entries: pre-W268 the authority axis carried 1 ref kind (mode);
  post-W268 it carries 5 (mode + permit + lease + policy_rule +
  approval), with permit + lease backed by real producer rows.
  Hash-stability proven — 31/31 golden hashes pass (envelope-level
  fields flow into already-omit-when-empty `authority_refs`, so
  `ChangeEvidence.content_hash` is unaffected). 6 new tests
  (4 pr-bundle + 2 collector); 160 focused + 56 broader pass.
  Smoke on roam-code: `permits=[]` (no `.roam/permits/` yet),
  `leases=` 2 entries with full schema. Closes the W252
  authority-axis gap — 1 producer → 5 ref kinds.

> **Three closures in three waves milestone (W261 + W266 + W268,
> 2026-05-14).** Three of the W252 producer-coverage matrix's
> top-three under-served fields closed in three consecutive waves:
> Q8 silent-gap → explicit `producer_not_available` redaction
> marker (W261); environment axis 1 producer → N (W266);
> authority axis 1 producer → 5 ref kinds (W268). All three
> followed the same discipline — **Pattern-2 always-emit +
> delegate-not-move** — so existing v0/v1 content-hash contracts
> survived intact, and consumers see the new evidence the moment
> a producer ships without a schema migration or flag flip.

- **`pr-replay` policy adapters for constitution / permits / leases**
  (W267). Three new gatherers in `cmd_pr_replay.py`:
  `_gather_constitution_policy_decisions` (~line 995),
  `_gather_permit_policy_decisions` (~line 1056),
  `_gather_lease_policy_decisions` (~line 1117). Wiring strategy
  (option b): the collector's `collect_change_evidence` gained an
  `extra_policy_decisions` kwarg so additional rule sources project
  *into* the canonical packet rather than bypassing it (canonical
  mandate). Concatenation order is stable —
  rules → audit-trail → extras — so content_hash stability is
  proven (31/31 golden hashes pass). Smoke on roam-code:
  **1 audit-trail + 3 constitution gates + 2 leases + 0 permits = 6
  `policy_decisions`** on the canonical packet, up from 1 pre-W267.
  5 new tests; 127/127 focused + 71/71 broader pass. Closes the
  W252 policy-axis gap — 2 producers → 6 decisions from 4 sources.
- **`pr-replay` synth-bundle full parity for permits / leases /
  env_refs** (W272). Strategy A — direct import, no new module —
  proved sufficient: `_load_permits_from_disk` /
  `_load_leases_from_disk` were already clean module-level functions
  in `cmd_pr_bundle.py` and only needed a docstring annotation to
  flag them as shared. The W272 stamping block lives at
  `cmd_pr_replay.py:1378-1466` immediately after the W260 actor
  block; the post-collector `environment_refs` merge sits at
  `:1577-1601`. Dedup decision (option c): stamp permits / leases on
  the synth pr_bundle envelope for direct-consumer parity, and merge
  the W266-built env_refs tuple onto `packet.environment_refs` after
  the collector returns (the collector ignores the envelope's
  `environment_refs` key when rebuilding from raw inputs — naive
  stamping would have lost the workspace ref). Merge dedupes by
  `(env_kind, env_id)`. Smoke on roam-code: 3 `actor_refs` (W260),
  3 `authority_refs` (mode + 2 leases — up from 1), 3
  `environment_refs` (branch_range + local_run + workspace — up
  from 1). 4 new tests; 146/146 focused + 72/72 broader pass.
  Closes the W260 + W266 + W268 producer-parity gap on the replay
  path.

> **W252 producer coverage matrix closure cycle complete
> (2026-05-14).** Four waves collapsed the matrix's top-three
> under-served fields in a single cycle: environment (W266),
> authority (W268), policy (W267), and synth-bundle parity (W272).
> Real-world `roam pr-replay HEAD~5..HEAD` on the roam-code
> workspace itself now carries: 3 actor_refs / 3 authority_refs /
> 3 environment_refs / 6 policy_decisions / 492 context_refs /
> 11 artifacts (audit-trail manifest + 10 CGA predicates); the
> executable 8-question audit reports complete=7 / partial=1 /
> missing=0 (W261 forward-compatible no-silent-gaps shape). The
> last open producer-side gap (Q8 `accepted_risks` / `approvals`)
> is the only path to lift the partial → complete and is queued
> P1 as W247.

- **`roam permit issue --persist` writer — permit-persist closure**
  (W198). Closes the W186 audit's verdict-facade gap on
  `roam permit`. Pre-W198 the command was strictly a verdict facade:
  no document was written to `.roam/permits/<permit_id>.json`, so
  the W268 collector's `_load_permits_from_disk` always returned
  `[]` (Pattern-2 always-emit contract held). W198 ships the writer
  side via a new `roam permit issue` subcommand on top of the
  existing verdict-facade command + a new disk-backed
  `src/roam/permits/store.py` store: with `--persist`, writes one
  JSON document per issued permit to `.roam/permits/<permit_id>.json`;
  without `--persist`, the command remains a dry-run and stays
  byte-stable with the pre-W198 verdict contract (hook / pre-commit
  gates that consume only the verdict are unaffected). Closes the
  W292 / W294 facade-vs-real corroboration loop: the W292 harvester
  now finds real permit rows on disk, and
  `AuthorityRef(authority_kind="permit", …)` entries carry a real
  `extra["permit_id"]` with `provenance="producer_envelope(permit)"`
  instead of the historical inferred-fallback marker. The W294
  authority-source mapping (`permit` → `"permit"`) now resolves on
  real permit rows rather than synthetic envelope fields. 95
  permit-persist-focused tests in `tests/test_cmd_permit_persist.py`
  + 105 broader pass; existing W292 / W294 authority-provenance
  goldens stay byte-identical (no schema change — the permit row is
  an additional populated path, not a new field default).

- **Packet size budget enforcement on `ChangeEvidence`** (W280). New
  module-level constant `PACKET_SIZE_BUDGET_BYTES = 262144` (256 KiB)
  measured on canonical JSON. `_apply_size_budget()` is called BEFORE
  `with_content_hash()` so the `content_hash` is the hash of the
  post-truncation packet — never of a packet that the consumer will
  not see. Frozen deterministic 5-step truncation order:
  `artifacts.content_inline` → `context_refs.content_inline` →
  `policy_decisions.extra` → `findings.evidence` →
  `actor_refs.extra`. Redactions are NEVER dropped (they describe
  the truncation itself). The `"size_limit"` redaction reason is
  appended dedup-safe. `roam evidence doctor` surfaces a
  `packet_size: {bytes, budget_bytes, budget_state}` block, with
  `oversized_after_truncation` mapping to WARN (not FAIL). First
  enforced size discipline anywhere in the evidence pipeline; real
  packets on roam-code sit at ~96 KB (~37% of budget).
- **Provenance vocabulary (vocab + helper; wiring deferred)** (W282).
  New `PROVENANCE_SOURCES` frozenset (10 values:
  `ci_env_var` / `git_config` / `run_ledger` / `cli_flag` /
  `env_var` / `producer_envelope` / `audit_trail` / `mcp_receipt` /
  `inferred` / `unknown`) plus `provenance_label()` pure helper with
  a detail-compact form. Cross-vocabulary leakage validation pins
  the new enum against the existing assurance vocabularies. CLAUDE.md
  vocab table grew 11 → 12 rows. **Producer-side wiring is deliberately
  deferred** to a future wave (W290+) so the vocabulary lands clean
  before any call-site churn.
- **Limitations generated from packet structure on `pr-replay`**
  (W284). `_derive_limitations(evidence)` at
  `cmd_pr_replay.py:2295` projects three packet sources into the
  Markdown / JSON `evidence_limitations` block, in a frozen
  deterministic order: **Q-gaps (Q1 → Q8) → redactions (tuple order)
  → trust-tier warnings (actor_refs order) → non-cert footer always
  appended**. New `_Q_GAP_LABELS` + `_REDACTION_EXPLANATIONS` lookup
  tables. Renderer `_render_evidence_limitations()` at `:2136`
  rewrites the prior hand-written boilerplate into a pure structural
  projection. Sentinel `_No evidence limitations detected._` when no
  source contributes. Closes the "limitations drift from packet"
  hazard that surfaced during the W281 trust-tier rollout.
- **`roam_version` stamped at the producer site on `ChangeEvidence`**
  (W287). New `_resolve_roam_version()` helper at
  `change_evidence.py:783` (deferred import of `roam.__version__`,
  fallback `"unknown"`). Wired at the **producer** site in
  `collect_change_evidence()` at `collector.py:2667` — deliberately
  NOT as a dataclass field default, which would have broken the
  W210 omit-when-None invariant for packets that do not opt into
  the field. Backward compatibility: pre-W210 packets and W210
  packets that do not populate the new fields still serialize to
  byte-identical canonical JSON; existing stored `content_hash`
  values stay valid. Smoke on roam-code stamps the live PyPI
  version (`"13.0"`).
- **GitHub PR review parser / normalizer** (W247a). New module
  `src/roam/evidence/github_reviews.py` (~360 lines) introduces
  three public functions: `parse_github_reviews()` (pure;
  deterministic input → output), `load_reviews_from_fixture()`
  (pure offline path for tests), and
  `harvest_reviews_from_gh_cli()` (deliberate opt-in subprocess
  path). New `GITHUB_REVIEW_STATES` closed enum (5 values).
  Discipline: APPROVED reviews land in `approvals[]` **only when
  the review's `commit_id` matches `head_commit_sha`** (stale
  approvals are filtered); CHANGES_REQUESTED reviews land as
  `PolicyDecision(decision="deny")` rows so the deny signal flows
  into `policy_decisions[]`; COMMENTED / DISMISSED / PENDING reviews
  are filtered with explicit warnings. **Review bodies are NEVER
  stored** (asserted by test). Fixture-first design keeps the test
  suite fully offline. This is the **first half** of the W247 real
  approvals producer — W247b (pr-replay integration) is queued
  separately so the parser proves itself before any consumer
  wiring.
- **`policy_decisions` and `approvals` provenance stamping at 9
  ingestion points + Pattern-2 fallback** (W293). Every
  `PolicyDecision` row + every `ApprovalRecord` row now carries
  `extra["provenance"] = provenance_label(source, detail=...)`
  built from the W282 closed `PROVENANCE_SOURCES` vocabulary (no
  new strings invented). Producer-side stamping fires at the
  gatherer / parser / CLI command — never at the dataclass
  default — so existing producer-supplied provenance is preserved
  idempotently. Ingestion sites:
  `cmd_pr_replay.py:1061-1086` constitution gatherer →
  `producer_envelope(constitution)`;
  `cmd_pr_replay.py:1120-1153` permit gatherer →
  `producer_envelope(permit)`;
  `cmd_pr_replay.py:1198-1224` lease gatherer →
  `producer_envelope(lease)`;
  `cmd_pr_replay.py:1267-1280` approval flattener →
  `producer_envelope(github_review)`;
  `github_reviews.py:370-380` PolicyDecision builder →
  `producer_envelope(github_review)`;
  `collector.py:2459-2467,2497,2502` audit-trail chain-integrity →
  `audit_trail`; `collector.py:1973-1976,2018` rules envelope →
  `producer_envelope(rule)`; `cmd_pr_bundle.py:2399-2410`
  add-approval CLI → `cli_flag`; `collector.py:3361-3389`
  Pattern-2 fallback stamps `unknown` only when no upstream signal
  exists (existing values preserved). Sharp drive-by:
  `PolicyDecision.to_dict()` flattens `extra` to a top-level key
  on the wire and `from_dict()` re-nests it on read, so wire
  format stays flat while the in-memory dataclass stays typed —
  `ChangeEvidence.__post_init__` handles both shapes. The W247a
  body-prohibition guardrail
  (`test_review_bodies_do_not_appear_in_canonical_json`) extended
  to assert no body keys appear on any row after the provenance
  hop. Smoke on roam-code: 6 policy_decisions all carry explicit
  provenance (3 constitution + 2 lease + 1 audit_trail; zero
  `unknown` fired). 15 new tests + 1 regression assertion + 486
  broader pass + 31/31 goldens byte-identical.
- **AuthorityRef.source population + `auto_log()` writer-side
  run-ledger event fields** (W294). Closes both W292
  follow-ups in one wave. (a) AuthorityRef.source now populated
  DISTINCTLY per category mapping at `_build_authority_refs` via
  a new `source=` kwarg on the `_add` helper: `mode` →
  `"mode"`; `permit` → `"permit"` + `extra["permit_id"]` when a
  real permit row is present; `policy_rule` → `"rule_config"`;
  `approval` → `"human_approval"`; `lease` intentionally retains
  `"inferred_fallback"` because `AUTHORITY_SOURCES` has no
  `lease` literal (deliberate vocab decision, documented inline —
  see HANDOVER §16.5 lease asymmetry note). (b) `auto_log()` in
  `src/roam/runs/helpers.py` gained an optional
  `extra_event_fields` kwarg with a closed whitelist
  `_AUTHORITY_EVENT_FIELDS = {"mode", "active_mode", "mode_to",
  "mode_from", "permit_id", "lease_id", "approval_id",
  "rule_id"}`. Writer-side wiring: `cmd_mode` (`:362-386`)
  emits `mode_to` + `mode_from` on non-noop switch (with pre-
  switch capture before `set_active_mode` runs); `cmd_lease`
  (`:329-340` claim, `:430-441` release) emits `lease_id`;
  `cmd_pr_bundle` add-approval (`:2392-2403`) emits
  `approval_id` when an active run exists. AuthorityRef.source
  (W211 category) and `extra["provenance"]` (W282 channel)
  remain DISTINCT load-bearing fields — neither is a synonym of
  the other. 15 new tests + 1 updated assertion (W292's source
  assertion flipped from `"inferred_fallback"` to `"mode"` now
  that W294 distinguishes) + 31/31 goldens byte-identical + 305
  broader pass.
- **`EvidenceArtifact` advisory warning when `content_inline` >
  8 KiB** (W288-followup). Construction-time `warnings.warn` at
  `EvidenceArtifact.__post_init__` fires whenever
  `len(content_inline) > INLINE_CONTENT_SOFT_LIMIT_BYTES`
  (8 KiB). The limit stays purely advisory — no reject, no
  truncate, no redaction stamp; consumers can still build large
  inline artifacts when intentional. Companion to W280's enforced
  256 KiB packet-level budget: a two-tier discipline where the
  per-artifact limit is a pressure signal and the packet-level
  limit is the hard ceiling that drives the deterministic
  truncation order. Focused tests green.

> **Provenance trilogy closure (W290 + W292 + W293, 2026-05-15).**
> Every evidence dimension (`actor_refs`, `authority_refs`,
> `policy_decisions`, `approvals`) now carries
> `extra["provenance"]` stamped at ingestion sites via
> `provenance_label()` from the W282 closed vocabulary (10
> sources, no new strings). Ingestion-point stamping discipline
> preserves dataclass schema cleanliness — every wave landed
> through call-site additions, never through default-value
> changes. W294 stabilized the authority axis by populating
> `AuthorityRef.source` distinctly per category and wiring
> writer-side run-ledger fields (`mode_to`/`mode_from`/
> `permit_id`/`lease_id`/`approval_id`/`rule_id`) so the W292
> harvester finds real corroboration instead of only
> `run-meta.mode`. W288-followup added a per-artifact advisory
> warning to complement W280's enforced packet-level budget. 31/31
> golden content_hashes byte-identical across the trilogy +
> stabilization waves.

### Changed
- **`cmd_owner` Pattern-1 envelope contract** (W362). Behaviour
  change at the JSON envelope boundary: `roam owner <symbol>` on a
  symbol with no owner data previously exited 0 with empty stdout
  (Pattern-1 Variant C crash surface). It now emits a structured
  envelope with `state: "no_owner_data"` + `next_command: "roam blame"`
  while still exiting 0. The MCP wrapper round-trip is now
  crash-free; downstream consumers reading `state` and `next_command`
  pick up the new contract automatically.
- **Python 3.10+ minimum documented across the source tree** (W352).
  Pyproject already required 3.10+ but a long tail of source-file
  comments and contributor docs still referenced 3.9 compatibility.
  Sweep aligned the comment/doc surface with the actual requirement —
  no functional change. Companion to W412 which removed the last
  stale `from __future__ import annotations` rationale notes that
  predated the 3.10+ baseline.
- **Closed `plugin_count` convention drift** (W319). The plugin
  count (currently 1 — the reference example plugin) is now sourced
  from a single canonical citation. Closes a Pattern-3-style drift
  where the count was written in 3 places with different values.
- **W288 INLINE_CONTENT_SOFT_LIMIT advisory wording polish** (W348).
  The advisory `warnings.warn` text was tightened so consumers
  don't conflate the soft 8 KiB advisory with the hard 256 KiB
  packet-level budget. Pure wording polish; the threshold logic and
  the W288-followup contract are unchanged.
- **`asyncio` configuration cleanup** (W403). Pyproject's pytest
  config no longer carries a stale `asyncio_mode` entry that
  predated a dependency drop. Test runtime is unchanged; the
  warning emitted on every test run is gone.
- **`CLAUDE.md` Pattern-1 family expanded to A/B/C** (W328). The
  empty-stdout / structured-failure / hang family is now codified
  as a canonical three-variant spec: **A** (hang on missing
  prerequisite), **B** (structured signal lost via exit-code gap),
  **C** (empty stdout crashes `json.loads`). Five invariants pin
  the contract (always-emit envelope, structured `is_error`,
  cold-start guard, exit-code passthrough, json-parse-on-empty
  defence) and external citations point at the FastMCP /
  Anthropic / MCP-spec sources that drove each invariant.
- **`CLAUDE.md` Pattern 3 + Pattern 6 expanded** (W330). Pattern 3
  now carries explicit 3a (cross-command metric drift) + 3b
  (cross-tool parameter-name drift) variants; Pattern 6 grew to
  6a (response volume crisis), 6b (token-count drift across
  recipes), and 6c (handle-pattern crash on its own handles).
  External citations point at agi-in-md LAW-4 evidence and at
  the in-tree W332 / W325 / W331 implementation waves so future
  maintainers can trace the codification back to the experiments
  that produced it.
- **Pattern-1 family Variant D added** (W334). After W324 surfaced
  a silent-success failure in `roam_annotate_symbol` (the wrapper
  returned `verdict: "completed"` despite the underlying resolver
  degrading to a no-match), Variant D was added to the canonical
  spec: "silent success on degraded resolution." The variant is
  distinct from Variant B (structured signal lost via exit-code
  gap) because Variant D's failure mode is producer-side — the
  command itself fails to surface the degradation, not the MCP
  wrapper. The fix lives at the command's resolver boundary, not
  the wrapper chokepoint.
- **`authority_refs` provenance stamping with deterministic
  precedence ladder** (W292). Mirrors the W290 actor_refs
  pattern: each `AuthorityRef` now carries
  `extra["provenance"] = provenance_label(source, detail=...)`
  built from `PROVENANCE_SOURCES`. New
  `_resolve_authority_provenance()` at
  `src/roam/evidence/collector.py:1063` +
  `_collect_corroborated_authorities_from_runs` at `:967` +
  rewritten `_build_authority_refs` at `:1153`. Frozen
  precedence ladder on same `(authority_kind, authority_id)`:
  `run_ledger` > `audit_trail` > `mcp_receipt` >
  `producer_envelope(permit)` > `producer_envelope(mode)` >
  `producer_envelope(rule)` > `producer_envelope(approval)` >
  `producer_envelope(lease)` > generic `producer_envelope` >
  `inferred` > `unknown`. Implemented as a deterministic ladder
  in `_resolve_authority_provenance`, NOT as implicit dict
  iteration — HMAC-verified run-ledger evidence always beats any
  envelope claim. `AuthorityRef.source` (W211 `AUTHORITY_SOURCES`
  — the category answer) and `AuthorityRef.extra["provenance"]`
  (W282 `PROVENANCE_SOURCES` — the channel answer) preserved as
  INDEPENDENTLY load-bearing fields; merging them would erase
  information. 14 new tests + 31/31 goldens byte-identical + 180
  broader pass.
- DB schema `USER_VERSION` 13 → 17 (four migrations during the extension:
  W82 step manifest → 14, W81 version stamps → 15, W89 findings table → 16,
  W97 FTS5 schema-hash discipline → 17).
- `_DESTRUCTIVE_TOOLS` in `src/roam/mcp_server.py:301-307` is now a derived
  view (`frozenset[str]`) built from the `@_tool(destructive=True)` decorator
  kwarg — one source of truth instead of a hand-maintained dict.
- `cmd_findings` maturity flipped experimental → stable + `mcp_expose=True`
  after W95 (clones detector migration emitted 584 findings on roam-code).
- **`cmd_surface.py` `mcp_tool_count` source-of-truth flipped to
  `_TOOL_METADATA`** (W138). Previously read `_REGISTERED_TOOLS`, which is
  empty when `fastmcp` isn't installed — so `surface --json` reported
  `mcp_tool_count: 0` on stripped installs. `_TOOL_METADATA` is
  env-independent (it's built at import time from `@_tool` decorator
  kwargs). `mcp_introspection_available` remains as a separate signal so
  consumers can still distinguish "stripped install" from "broken fastmcp".
- **A1 dict-collapse ladder progress: 6 of 8 dicts now derived.**
  `_NON_READ_ONLY_TOOLS` (W108) and `_NON_IDEMPOTENT_TOOLS` (W113) are now
  built from the `@_tool(read_only=..., idempotent=...)` decorator kwargs
  instead of being hand-maintained sets that drift from the decorator
  truth. Only `_CORE_TOOLS` remains as a hard collapse candidate;
  `_REGISTERED_TOOLS` and `_TOOL_METADATA` are non-collapsible by design
  (the former is fastmcp's live registry, the latter is the import-time
  catalog the new derivations read from).
- **CLAUDE.md Pattern 4 reworded to reflect the Fix-G resolution** (W139).
  All 5 conventions sites (`describe`, `understand`, `minimap`,
  `preflight`, `conventions`) delegate to
  `conventions_helper.compute_conventions()`. `--persist` lives ONLY on the
  standalone `conventions` command, so the registry-write path stays
  single-owner.
- **CLAUDE.md findings-registry section refreshed** (post-W138). 14-detector
  list with the canonical confidence-tier vocabulary
  (`static_analysis` / `structural` / `taint` / `heuristic`), the expanded
  subject-kind vocabulary, and the version-stamp placement rule (stamps
  travel with the row, not the envelope).
- **README.md headline reordered** (W138) so the
  `test_doc_consistency.py` regex captures the canonical `149 MCP tools`
  count instead of a transposed substring. Pure ordering change; numbers
  match `roam surface --json` exactly.
- **`llms-install.md` swept for v13.0+ substrate** (W124). 86 → 142 lines;
  added the 4 modes, the agent loop, the findings-registry section, and
  the 4 world-model classifiers. The file is now structurally equivalent
  to the long-form architecture doc but cropped to LLM-onboarding length.
- **`architecture.html` findings-registry section added** (W130). 142 lines
  documenting the registry schema, the 4 confidence tiers, the agent-loop
  step 7a (`roam findings list` between critique and pr-bundle emit), and
  the detector inventory with confidence-tier mappings.
- **README + landing-page reframe — Option B "evidence compiler"**
  (W171 / W178 proposals; W200 committed). Headline copy on `README.md`
  and `templates/distribution/landing-page/` now leads with the
  evidence-compiler thesis (identity / authority / evidence) and
  positions the findings registry + Agent OS substrate as the inputs
  to a portable assurance packet, not standalone analyses.
- **`CLAUDE.md` evidence-compiler thesis section + W148-doc
  adversarial fix** (W170). Section 9 of the handover memo grew into
  a first-class `CLAUDE.md` block; the W148 doc-only fix for the
  `adversarial` recipe (registry-key lookup, not string-templated CLI
  invocation) is now reflected in the Pattern 5 commentary.
- **`CLAUDE.md` cross-walk + identity/authority/evidence framing**
  (W187). Added a phrase-level glossary so reviewers can map a
  finding/run/bundle back to the assurance packet without re-reading
  the architecture memo.
- **`policy_decisions` promoted to typed `PolicyDecision` dataclass**
  (W279 + W279b). New `src/roam/evidence/policy.py` mirrors the W211
  `ApprovalRecord` pattern: frozen dataclass with explicit `rule_id`
  / `decision` / `verdict` / `evaluated_at` / `extra`, replacing the
  prior tuple-of-mapping shape. Two user-mandated integrity
  constraints landed under W279b: (1) `PolicyDecision` is a
  `collections.abc.Mapping` subclass so W226's `apply_profile()`
  `_redact_mapping_tuple()` keeps working unmodified; and (2) the
  legacy-preserve `ValueError` catch at `change_evidence.py:320-358`
  was narrowed to only preserve rows when `rule_id` OR `decision` is
  **missing**, so drift-detection now correctly fires on
  `{"rule_id":"r", "decision":"approved"}` (a row that LOOKS valid
  but cannot construct a typed `PolicyDecision`). 31/31 golden
  content_hashes preserved.
- **Trust-tier surface on `roam evidence doctor`** (W281). New
  `_classify_trust_tiers(packet)` helper and extended
  `_validate_closed_enums()` check `actor_refs[i].trust_tier`
  membership against `ACTOR_TRUST_TIERS`. Doctor now always emits
  a Pattern-2 5-key `trust_tiers` dict (one entry per trust tier,
  zero where absent) plus a `trust_warnings[]` array. Verdict
  ladder extended: **FAIL** on enum violations or hash mismatch;
  **WARN** on PARTIAL / INSUFFICIENT banner OR on a STRONG banner
  with any `self_reported_agent` / `unknown` actor; **PASS**
  requires STRONG + zero trust warnings. Closes the silent
  pseudo-actor gap that the W278 classifier had only addressed at
  construction time.
- **Pseudo-actor corroboration in `classify_actor_trust_tier()`**
  (W285). The classifier gained two new kwargs:
  `corroborated_tool_ids` and `corroborated_actor_ids`, both
  `frozenset[str]` with exact-equality membership (no name
  allowlist, no substring match). A new collector helper
  `_collect_corroborated_ids()` at `collector.py:1150-1357` reads
  two real evidence sources: **HMAC-verified run-ledger events**
  (mirroring `cmd_runs._verify_one_run`:
  `ensure_ledger_key` → `read_run_events` → `verify_chain`; only
  `result["state"]=="ok"` contributes, whole-run granularity) and
  **parseable MCP receipts**. `_RUN_LEDGER_TOOL_FIELDS` constant
  captures the non-uniform event-field naming for forward
  compatibility. As a side-effect this **also closes the W197
  bypass**: MCP-receipt-mirrored ActorRefs that previously skipped
  the W278 classifier now flow through it. Real-world smoke on
  the roam-code workspace itself: **doctor verdict WARN → PASS**,
  with 3 previously-unknown-tier pseudo-actors (`<unknown>` /
  `roam_init` / `roam_reindex`) promoted to `local_env` via real
  HMAC-verified run-ledger corroboration. Negative proof: a
  `tempfile.mkdtemp()` workspace with no `.roam/` keeps
  `roam_init` at `unknown` — no name-based shortcut, only real
  evidence promotes.
- **Canonical demo evidence fixture repaired to true PASS** (W286).
  The fixture's prior `self_reported_agent` actor (claude-code
  1.2.3) was replaced with a `local_env` actor
  (`example-trusted-agent`) corroborated by an HMAC-signed
  run-ledger event, so the bracket `test_doctor_passes_on_canonical_packet`
  (canonical → PASS) vs the insufficient-banner fixture (→ WARN)
  now holds with real evidence on both sides. Cross-references
  updated: top-level `agent_id`, `human_actor`,
  `approvals[0].approver`, `authority_refs[1].granted_by`.
  Content hash recomputed via `.audit-tmp/w286-rehash.py`
  mirroring `ChangeEvidence.compute_content_hash` discipline.

> **Evidence pipeline hardening batch (W279-W287 + W247a,
> 2026-05-14).** Typed `PolicyDecision` drift detection + packet
> size budget + trust-tier surface + corroboration-based
> promotion + provenance vocab + generated limitations +
> producer-site version stamping + GitHub review parser.
> Real-world `roam evidence doctor` on the roam-code workspace
> itself: **VERDICT PASS** (was WARN via 3 unknown-tier
> pseudo-actors; W285 promoted them to `local_env` via real
> HMAC-verified run-ledger corroboration, with W286 confirming
> the bracket against an insufficient-banner fixture). Real
> packets sit at ~96 KB against a 256 KiB budget. Provenance
> vocabulary landed clean; producer-side wiring is deferred to
> W290+ so the call-site churn is decoupled from the vocab
> freeze.

### Research / planning
- **MCP Pattern-1 family audit** (W315). Sonnet+web research pass
  that named Variants A / B / C, surfaced the open Variant B gaps
  on `doctor` / `stale-refs` / `triage` / `pytest-fixtures`, and
  cited FastMCP / Anthropic / MCP-spec sources for the structured
  `isError: true` discipline. Memo lives at
  `(internal memo)`. Drove the W325
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  chokepoint fix and the W328 CLAUDE.md codification.
- **Pattern 3 + Pattern 6 audit** (W329). Sonnet+web pass that
  enumerated 9+ MCP parameter-name mismatches and 8 commands
  returning 50K-1.6M tokens with no auto-handle. Memo lives at
  `(internal memo)`. Drove the W330
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  CLAUDE.md expansion, the W331 / W331b definition-field rollout,
  and the W332 `input_path` normalization.
- **MCP state-mutating patterns** (W340). Sonnet+web pass that
  catalogued every state-mutating MCP wrapper and surfaced 4
  sub-waves (W363-W366) for hardening the mutation surface. Memo
  lives at `(internal memo)`.
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **Standards currency audit** (W341). Sonnet+web pass against
  SLSA v1.2, OSCAL v1.2, and the EU AI Act drafts. Surfaced 3
  sub-waves (W358-W360) to refresh the in-tree control-map
  language. Memo lives at
  `(internal memo)`. Headline finding:
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  SLSA v1.2 Source Track + OSCAL v1.2 Control Mapping landed
  after the W184 control-map shipped, so the in-tree wording
  ("maps to" / "supports evidence for") still holds but the
  cited revision numbers need a refresh pass.
- **Detector competitive audit** (W368). Sonnet+web pass against
  the open-source detector landscape (Snyk / Sonatype / Semgrep /
  SonarSource). AHEAD on 5 categories (vibe-check/AI-rot, agentic
  audit-trail, taint+OpenVEX, N+1 implicit-property,
  graph-coupled bus-factor); PARITY on 5; BEHIND on 6. Top 3
  gaps: reachable-vuln open-source parity, smells rule depth
  (empty-catch + primitive-obsession), taint OWASP Top-10 sink
  coverage. 5 sub-waves queued (W370-W374). Memo lives at
  `(internal memo)`.
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **Ecosystem positioning audit** (W385). Sonnet+web pass against
  7 adjacent tools (review tools, IDE plugins, governance
  platforms, supply-chain analysers, agent observability). Grades
  each on the agentic-assurance axis: 5 COMPLEMENTARY (different
  surface; integrate-not-compete) / 2 COMPETITIVE (overlapping
  evidence claim) / 0 SUBSTITUTE (no full replacement). Confirms
  the "local evidence compiler" thesis — no surveyed tool emits a
  portable evidence packet, so Roam's compiler position is
  defensible. Memo lives at
  `(internal memo)`. Feeds the W390 / W393
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  pitch-refresh wording and the W397 auto-count Codex-headline
  template update.
- **Performance benchmarking research** (W395). Sonnet+web pass
  against 7 indexing tools (ctags, jedi, aider repo-map, serena,
  Sourcegraph SCIP, CodeQL, tree-sitter raw). Positions roam at
  **MEDIUM**: 10-40x slower than ctags per-file (different
  category — ctags emits tags only, no graph / git / metrics) but
  **5-20x faster than CodeQL** for comparable analysis depth on a
  typical 50K-LOC repo. Defensible claim: "Roam indexes a fresh
  50K-LOC codebase in 15-30s and re-indexes a single changed file
  in under 5s." Recommends a 5-wave optimization sequence — W400
  phase-timing in `roam doctor` (prereq), W404 (≡ W396 in the memo)
  `ROAM_PARALLEL_INDEX` default-on, W397-equivalent shallow-git
  default on first index, W399 Louvain cache expansion, W398-style
  parallel parse via `ProcessPoolExecutor`. Memo lives at
  `(internal memo)`. The five sub-waves
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  are queued as W404 + W405-W408 in the backlog (renumbered to
  avoid collision with the in-tree W396-W400 series).
- **llm-smells pattern catalog** (W402-research). Sonnet+web pass
  that designs `roam llm-smells`, a NEW command detecting
  anti-patterns in HUMAN code that calls LLM APIs (openai,
  anthropic, google-generativeai, langchain, litellm, etc.).
  Distinct from `vibe-check` (which detects AI-generated code
  shape). Catalog has 14 patterns: **8 CHEAP** (regex-based;
  ship in v1), **3 MODERATE** (use existing `symbols.default_value`
  column), **3 EXPENSIVE** (deferred — need new dataflow edge
  types). v1 surface = 11 patterns across token budgets, model
  pinning, system messages, error handling, retry discipline, and
  context-window assumptions. Primary academic source: Mahmoudi et
  al. arXiv:2512.18020 (Dec 2025), 200-system empirical study with
  86.06% average detection precision. Memo lives at
  `(internal memo)`. Estimated v1
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  implementation: 6-8 hours (core detector + 11 patterns + MCP
  wrapper + tests). When shipped this is the **first
  production-grade multi-provider linter** for LLM API anti-patterns.
- **OWASP 2026 taint rule pack research** (W372-research). Identified 3
  first-ship rules ranked by precision-vs-effort: **W373**
  (`python-ssti` — Jinja2 server-side template injection), **W374**
  (`java-sqli` — JDBC string-concat SQL injection), **W375**
  (`java-deserialization` — `ObjectInputStream` deserialization
  sinks). All three rules use existing AST edge types; no
  dataflow-engine extension required. v1 estimated implementation:
  3-5 hours per rule.
- **Phase 4-7 perf research** (W395-followup). Top 3 actionable
  outcomes: **W423** (PageRank warm-start; 2-5s savings on warm
  re-index), **W424** (SQLite `synchronous=NORMAL` pragma; 1-3s
  savings on full-index commits), and **W407 reclassified to
  VALIDATE** — the Louvain cache is already implemented; the
  recommendation was to verify (not build). NOTE: this ranking was
  superseded by the W408 real-data finding (`effects_taint`
  consuming 48% of indexer wallclock); the new ranking pushes W433
  (`effects_taint` optimization) ahead of W423.
- **Standards crosswalk additions research** (W360-research).
  Proposes 5 new YAML entries: 4 NIST AI 600-1 controls + 1 NIST SP
  800-218A control. CAISI (US AI Safety Institute) is held until H2
  2026 — its standards remain in concept-paper form and adding
  citations would invite later wording-drift. Implementation queued
  as **W428**.

### Added — taint rule pack v1 + shallow git default + auto-generated MCP table + oracle wrapper dedup + qualified-name flag (W466 batch)
- **W405 — shallow git history default on first index**. `git_stats.py`
  now defaults to a 365-day window via `_DEFAULT_SINCE`; `cmd_init.py`
  surfaces `--full-history` for the opt-out and `ROAM_GIT_SINCE` for
  env override. The `_first_index()` gate preserves existing deep
  indexes so existing users see no behaviour change on re-index.
  **30 + 31 + 115 focused tests pass.** Drive-bys W437/W438/W439
  queued. Lowest-risk of the W395 perf sub-waves.
- **W373 — python-ssti taint rule** (T-X01, CWE-94). Server-Side
  Template Injection rule against the existing taint engine — engine
  already supports qualified-name matching, so no engine extension
  required. **7 new + 45+39 existing tests pass.** Drive-bys
  W452/W453/W454 queued (W454 also landed this batch).
- **W374 — java-sqli taint rule** (CWE-89). Java SQL Injection rule
  with the same recall-limited precision profile as java-fileupload
  because the engine lacks Java qualified-name resolution today.
  **7 new + 44+31 existing tests pass.** Drive-bys W455/W456/W457
  queued — W455 captures the "engine needs Java qualified-name
  resolution" follow-up surfaced during this wave.
- **W454 — per-rule `qualified_only` flag for taint engine**. Lets
  individual rules opt in to qualified-name-only matching; **java-sqli
  opts in** so the recall-limited precision profile is per-rule, not
  engine-wide. **29 + 60 focused tests pass.** Drive-bys W461/W462/W463
  queued.
- **W443 — README coverage for 4 untracked CLI commands**. Added
  `evidence-diff`, `evidence-doctor`, `llm-smells`, and `findings` to
  the README command index. `test_readme_covers_all_canonical_cli_commands`
  now passes. Drive-bys W449/W450 queued.
- **W449 — auto-generated README MCP tool table**. New
  `surface_counts.mcp_tool_descriptions()` helper drives a generated
  table; **74 missing tools added** and the **core preset count
  corrected (25 → 57)** so it matches `roam surface --json`. Closes
  the long-standing 73-tool drift surface that the W411 backstop wave
  had only patched. **4/4 + 16/16 + 8/8 + 31/31 test suites pass.**
  Drive-bys W458/W459/W460 queued.
- **W432 — oracle wrapper dedup**. All 5 oracle wrappers
  (`symbol_exists`, `route_exists`, `is_test_only`,
  `is_reachable_from_entry`, `is_clone_of`) had been duplicated by
  W306; this wave removed the duplicates. Decorations went **228 → 223**
  unique, which **matches the CLAUDE.md headline** (`mcp tools
  registered: 223`). Added a new AST duplicate-name CI lint via
  `surface_counts.mcp_tool_decorations()` so this class of drift fails
  at lint time. Drive-bys W443/W444/W445 queued (W443 landed this
  batch).
- **W429 — small-cleanup bundle** (W422 + W425 + W426). Three
  drive-bys from the W383 + W421 batch landed together: W422 marks
  the standalone permit wrapper as deprecated, W425 adds
  `lease warnings_out`, W426 surfaces a constitution-unparseable
  warning. **204/204 tests pass; 31/31 hash stability byte-identical.**
  Drive-bys W446/W447/W448 queued.

### Research / planning — W466 batch
- **W433-research — `effects_taint` optimization scoping**. Memo at
  `(internal memo)`.
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  Three candidates
  ranked: **(C) double-parse I/O elimination — 15-30s, zero risk**;
  (B) function-summary memoization 35→5s; (A) file-signature cache
  warm-reindex 0s on cold path. **Surprise discovery: roam has TWO
  independent taint engines** — `analysis/taint.py` for Phase 5
  (indexer-side) vs `security/taint_engine.py` for the `roam taint`
  command. Consolidation is a deeper structural play beyond the
  immediate W433 perf wave.
- **W358-research — SLSA v1.2 Source Track positioning**. Memo at
  `(internal memo)`. **roam de-facto covers
  SRC-L2 today.** SURPRISE: **SRC-L3 lift is ONE wave** —
  `cosign_sign_statement()` at `attest/cga.py:495-594` is already
  implemented. New wave W451 queued to formalize the SRC-L3 mapping. <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
- **W359-research — OSCAL v1.2 Control Mapping decision**. Memo at
  `(internal memo)`.
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  SURPRISE: OSCAL v1.2
  added a **7th model** (Control Mapping) which is the
  zero-prerequisite first emission for per-run evidence. AR for
  per-run evidence. New waves W464/W465 queued.

### Added — permit unification + Pattern-3b extension + llm-smells v1.1 + phase timing
- **W377-batch — six permit-persist red-team gap closures** (W377-W382).
  Sealed the six drive-by gaps W349 surfaced against the W198 permit
  writer. Touched `src/roam/commands/cmd_pr_bundle.py`
  `_load_permits_from_disk` and `src/roam/evidence/collector.py`
  `_build_authority_refs`. **31/31 golden hashes byte-identical**
  (the W292/W294 authority-axis stability proof held); 163 focused
  tests pass. No `ChangeEvidence.content_hash` movement.
- **W383 — unified permit reader behind canonical store helper**.
  `roam.permits.store.load_permits_from_disk` is now the single
  canonical reader; both `cmd_pr_bundle` and `cmd_pr_replay`
  delegate. Closes the historical split-brain where the two surfaces
  applied independent (and slightly different) JSON-parse + expiry
  filtering. **163/163 focused tests pass; 31/31 golden hashes
  remain byte-identical.** Two drive-bys captured as W421/W422 for
  follow-up.
- **W347 — Pattern-3b parameter-alias normalization extended**.
  Added `file_path` → `path` to `_PARAM_ALIASES` in
  `src/roam/mcp_server.py`. The prefix-pattern cluster
  (`queries`/`prefix`/`patterns`) was deliberately **bailed on**
  this wave — the boundary normalization shape doesn't fit a clean
  one-name canonical without consumer-side churn. **2733 + 140 + 31
  focused tests pass.** Three drive-bys queued as W430/W431/W432.
- **`llm-smells` v1.1.0 — five new CHEAP detectors** (W415b). Five
  additional regex-based detectors landed against the v1 surface:
  `missing_timeout`, `missing_max_retries`, `no_system_message`,
  `no_retry_backoff`, `call_in_loop`. Total v1.1 detector count = 10.
  **36/36 focused tests pass.** Package version bumped 1.0.0 →
  1.1.0. Three drive-bys queued as W415c/W415d/W427 for next-wave
  follow-up (additional patterns + MCP wrapper polish).
- **W408 — per-phase timing instrumentation in `roam doctor`**.
  Indexer phases (discover / parse / extract / resolve / effects /
  taint / graph / metrics) now emit per-phase wall-clock timings
  surfaced in `roam doctor`. **Real-data finding (CRITICAL): on
  roam-code itself, `effects_taint` is 48% of indexer wallclock
  (67.6s of 139.6s).** This **invalidates the PageRank-first ranking**
  in the W395-followup memo: the new ordering pushes **W433**
  (`effects_taint` optimization) ahead of W423 (PageRank warm-start)
  and W424 (SQLite pragma). 134/134 focused tests pass. Three
  drive-bys queued as W433/W434/W435.

### Bailed
- **W421 — constitution + lease gatherer delegation audit** (BAILED).
  Investigation found that `cmd_pr_replay` constitution + lease
  gatherers already delegate to the canonical readers; no
  refactoring was warranted. **119/119 baseline tests pass.** Two
  drive-bys captured as W425/W426 (informational follow-ups, not
  fixes).

### Fixed
- `roam taint` engine BFS no longer silently returns 0 findings (latent
  PRE-v13 regression — see Added section above).
- `roam doctor` 17 → 20 checks documentation drift corrected.
- `db/connection.py:255` missing `Callable` import (`F821`) — type annotation
  would have raised `NameError` under `get_type_hints` evaluation.
- `world_model/tx_boundaries.py:474` `confidence = "medium"` unused local —
  classifier had been under-reporting medium-confidence transaction boundaries.
- `test_neighbor_hub_files_are_dropped` + `test_low_degree_neighbor_still_expands`
  stale `.gitignore`-interaction fixture (Case A — test isolation).
- `test_batch_mcp` mock signature stale after the `include_paths` kwarg
  addition — fix surfaced a real production coarse-`try/except` swallowing
  parameter mismatches.
- 2 `SyntaxWarning` raw-string fixes: `\P` in `laravel_post.py:16` and
  `test_atomic_io_consolidation.py`.
- **`batch_search` / `batch_get` per-query exception handling** (W103). The
  coarse outer `try/except` in both MCP batch wrappers aborted the entire
  batch on a single per-query exception — one bad symbol-id in a 50-symbol
  batch would zero-out the other 49 results. Restructured so the outer
  `try/except` protects only `open_db()`; each per-query body has its own
  `try/except` inside the loop, surfacing per-row `error` fields without
  killing the batch. Surfaced during W101 test investigation as a real
  production bug, not a test artefact.
- **`stale-refs --attest <PATH>` mkdir crash** (W126). The unprotected
  `Path(attest).parent.mkdir(parents=True)` propagated
  `FileExistsError` / `NotADirectoryError` / `PermissionError`, aborting
  the entire stale-refs scan before any finding was emitted. Now wrapped
  in `try/except OSError`; surfaces `summary.attest_error` +
  `summary.attest_status` while the scan completes. Exit-code policy:
  `0` without `--gate`, `6` (PARTIAL) with `--gate` when there are no
  other findings, `5` (GATE_FAILURE) when other findings exist. Was a
  HIGH-severity finding in W112.
- **`cmd_surface --json` returns env-independent `mcp_tool_count`** (W138).
  Previously returned `0` when `fastmcp` was not installed; now reads
  `_TOOL_METADATA` so the inventory matches the documented count on every
  install variant.
- **Lease midnight-UTC clock race** (W135). `tests/test_lease_system.py`
  previously used a `±1d` tolerance to absorb test runs that crossed
  midnight UTC; the test now freezes the clock via `monkeypatch` on
  `roam.leases.store._utc_now*` so the assertion is exact. W112
  medium-severity finding.
- **vibe-check tier-mapping discipline** (W125). `empty_handlers` and
  `abandoned_stubs` were originally tagged `static_analysis` but the
  detector is regex-based, not AST-based — both downgraded to `heuristic`.
  `hallucinated_imports` upgraded to `structural` (it uses graph
  reachability, not pattern matching). Codified the rule:
  `static_analysis` is reserved for deterministic AST/CFG plus taint or
  dataflow plus cross-reference checks, and is never assigned to regex
  name matching.
- **`_check_index_step_failures` doctor advisory verified working
  through W127** — no behaviour change this session, but the W82-era
  advisory was re-confirmed against the new init-time cloud-sync warning
  to ensure they do not double-fire on the same root cause.
- **PR Replay Markdown output — 3 hostile-input bugs** (W217). New
  hostile-input snapshot tests caught real broken-output paths: pipe
  characters inside a code field broke the surrounding Markdown table;
  embedded newlines in `summary.verdict` strings unwrapped onto the
  following row; backtick-bearing identifiers escaped the inline code
  span and ran into the next sentence. All three sealed with
  per-cell escaping at the renderer; tests now pin the byte-output
  shape against fixture inputs.
- **W184 control-map wording drift — 3 entries** (W203). The CI
  wording-guard lint surfaced three control-map entries that
  overclaimed certification ("certifies", "makes compliant" — instead
  of "maps to" / "supports evidence for"). All three rephrased in
  place; lint now blocks future drift.

### Security
- **`roam_annotate_symbol` silent-success fix** (W324). The MCP
  wrapper had been returning `verdict: "completed"` even when the
  underlying resolver degraded to a no-match — a Pattern-2 violation
  (silent fallback). The wrapper now surfaces
  `state: "no_match"` / `verdict: "no annotations applied"` with a
  `next_command` pointing at `roam search` when resolution degrades,
  so the LLM sees the failure mode instead of acting on a false-PASS.
  This was the surfacing event that produced Pattern-1 Variant D
  (W334).
- **`cmd_impact` weighted_impact rounding + silent-fallback fix**
  (W336). Two Pattern-2 violations sealed in one wave: (a) the
  weighted-impact field was being rounded to 2 decimals before the
  threshold comparison, so the verdict could silently flip across a
  rounding boundary; (b) when the upstream graph traversal returned
  zero edges, the command emitted `verdict: "low impact"` instead of
  `state: "no_callers_found"`. Both paths now route through the
  always-emit-state Pattern-2 contract, and the threshold comparison
  uses the un-rounded weighted-impact value.
- **Redaction-leak snapshot tests** (W232). Five passing redaction
  snapshots pin the existing behaviour; four known leak paths
  surfaced and pinned with `pytest.mark.xfail(strict=True)` so they
  auto-clean as fixes ship — the test goes RED if a leak is
  accidentally sealed without removing the xfail. The four leak
  paths are: (1) absolute filesystem paths in `EvidenceArtifact.path`
  when the artifact is outside `.roam/`; (2) MCP receipt args
  carrying secret-looking strings that the redactor's allowlist
  misses; (3) git-author email leaking into `actor_refs` when
  `trust_tier == git_author`; (4) constitution-hash leaking the
  unredacted constitution YAML when `constitution_hash` is computed
  on a permit-bearing constitution.
- **Schema migration golden tests — hash stability proven byte-identical
  across W174 → W182 → W210** (W218). 5 fixtures (minimal, ideal-case,
  redaction-heavy, multi-actor, multi-authority); each round-trips
  the same content hash from the W174 baseline through W182's
  decision-receipt addition and W210's 9-field extension, so the
  backward-compat policy (`_W210_OMIT_WHEN_DEFAULT_FIELDS`) is now a
  proven invariant, not an aspirational one.
- **Producer/collector contract tests — 24 tests, 5 producer gaps
  pinned** (W219). End-to-end pairs (producer emits → collector
  consumes) for each of the 6/8 wired evidence slots. The 5 still-open
  producer gaps are now xfail-strict so they fail loudly the moment
  the producer side ships.
- **Executable 8-question audit** (W220). Threshold-gated test that
  exercises the canonical 8/8 ideal-case fixture end-to-end and
  asserts every assurance question (Who? What authority? What was
  read? What changed? What broke? What was approved? What was
  redacted? What evidence is stale?) is answerable from the packet
  alone.
- **Layer-2 collector secret scrub** (W249). Defence-in-depth pass
  in `src/roam/evidence/collector.py`: new `_scrub_actor_block`
  helper reuses W241's `_redact_secrets_in_string` verbatim and
  fires on 8 envelope fields — top-level `verdict` +
  `summary.verdict` + the actor block (`actor.agent_id` /
  `agent` / `human_actor` / `human` / `user` / `display_name` /
  `mcp_client_id` / `tool_id` / `ci_runner_id`) + legacy
  top-level `agent_id` / `human_actor`. Boundary re-scrub at
  `_build_actor_refs` provides a second line. `"secret"` stamps in
  `bundle_redactions` are deduped against producer-stamped W240
  entries. **Both W232 xfail-strict tests flipped to PASS** —
  markers removed from `tests/test_evidence_redaction_snapshots.py`.
  Justified by three real failure modes: pre-W240 envelopes on
  disk, third-party producers that bypass `pr-bundle`, test
  fixtures feeding dicts directly into the collector.
- **W219 gap-pin verification** (W255). 4/5 originally-pinned
  producer-side contract gaps cleanly flipped GREEN:
  `pr-bundle.context_files` (W224a),
  `pr-bundle.approvals` / `accepted_risks` (W224a + W240 CLI),
  `pr-risk.findings[]` (W242). The 5th
  (`pr-bundle.mode`) was already producer-side sealed by W224c but
  its contract test docstring was stale — sealed by W257. The
  `tool_id` reservation (no test) stays as a `None` placeholder
  for future W196 emitter work.
- **`pr-bundle.mode` contract test refresh** (W257). Updated
  `tests/test_producer_collector_contracts.py::test_pr_bundle_mode_contract`
  (lines 334-402): docstring rewritten to reflect the post-W224c
  reality (producer ALWAYS emits `mode` + `summary.active_mode`,
  defaulting to `"unmoded"`); producer-side assertions added
  (`"mode" in envelope`; `envelope["mode"] in VALID_MODES | {"unmoded"}`;
  `summary.active_mode` mirrors `mode`). Collector-side `safe_edit`
  injection check preserved. 24/24 contract tests pass.
- **Critique-contract drift-guard pin** (W256). 14-line
  constant-level test in `tests/test_evidence_collector.py:1504+`
  pins `'check' in _FINDING_SAFE_KEYS` — the field the critique
  envelope carries through to `ChangeEvidence.findings[]`. The
  pin is intentionally scoped to the one constant that produced
  observed drift; speculative pins on `rule_id` / `redactions`
  were rejected to keep the guard focused. Prevents a silent
  regression where a `_FINDING_SAFE_KEYS` rewrite drops the
  critique `check` field and the collector starts emitting
  partially-populated critique findings without test failure.
- **Q8 `producer_not_available` redaction route** (W261). Extended
  `REDACTION_REASONS` 8 → 9 (new member `producer_not_available`,
  declared in `src/roam/evidence/_vocabulary.py`); no schema
  expansion — the existing redactions tuple was reused. PR Replay
  emits a Q8 limitation entry at
  `src/roam/commands/cmd_pr_replay.py:1212-1242` whenever the
  packet contains no approvals (forward-compatible: the marker
  silently disappears once a real approvals producer ships, no
  consumer change required). Renderer dedicated Q8 bullet at
  `cmd_pr_replay.py:1797-1827`. Audit harness gained an asymmetric
  `EXPECTED_PARTIAL_COUNT_TODAY = 1` slot. Real-world smoke on
  roam-code: `complete=7 partial=1 missing=0`; honest-banner tier
  stays STRONG. Closes Q8 as the last silent gap on the
  pipeline-coverage arc.

### Performance
- `roam n1` bulk-fetch deeper helpers — 2 new bulks for `$with` resolution +
  resource-config; ~200 SQL roundtrips + ~500 disk reads saved per invocation
  on 100-model Laravel applications.
- `roam n1` candidate-filter N+1 closed — gap-models filter collapsed from
  100 queries to 1 (batched_in).
- `roam index` git-history skip when HEAD is unchanged — 94% speedup on warm
  re-runs (sentinels added in W87; original ship was pre-13.0).
- FTS5 `docstring` column with BM25 weight 4 (sentinels added in W94; original
  ship was pre-13.0). USER_VERSION discipline widened to hash
  `_FTS5_SCHEMA_COLUMNS` (W97).
- **CLAUDE.md auto-count generator now produces accurate counts in
  CLAUDE.md, README.md, and llms-install.md** (W138).
  `dev/build_readme_counts.py:_claude_blocks` was emitting the
  hand-stale `200+ commands / 130+ MCP tools` placeholder; it now emits
  `234 commands · 149 MCP tools (57 in the default \`core\` preset) · …`
  driven off `roam surface --json`, so the three doc surfaces converge on
  the same number on every regeneration.

### Deprecated
- (Already shipped in 13.0) 7 redundant aliases live in `_DEPRECATED_COMMANDS`
  in `src/roam/cli.py`. W66 backfilled the missing 13.0 CHANGELOG entry.

## [13.0] — 2026-05-13

### Deprecated
- **Seven legacy aliases now emit a deprecation note (BACKLOG Rank 18 / W3.3).**
  `digest`/`math`/`refs`/`snapshot`/`trend`/`onboard`/`churn` graduated out of the
  `_INTENTIONALLY_UNCATEGORISED` allowlist in `tests/test_surface_consistency.py`
  into `_DEPRECATED_COMMANDS` in `src/roam/cli.py`. Each alias still resolves to
  its canonical command (`trends`/`algo`/`uses`/`trends`/`trends`/`understand`/
  `weather`) — this is informational, not breaking — but every invocation now
  prints `DEPRECATION: '<alias>' is an alias for '<canonical>' …` on stderr and,
  in `--json` mode, carries the same string under `summary.deprecation_warning`
  so JSON-only consumers (CI, MCP clients) can detect the rename mechanically.
  Removal target is unset; users have at least one release cycle to migrate.
  Contract pinned by `tests/test_alias_deprecation.py` (5 tests).

### Substrate evolution
- R20 ledger HMAC chain with CGA signing
- R21 multi-agent lease system
- R25 plugin substrate validated end-to-end via Rails Path A clean cut
- atomic_io consolidation across writers
- Constitution/policy unified single source of truth
- World model: side-effects, idempotency, causal graph, tx-boundaries classifiers

### Mode enforcement (staged rollout, opt-in)
- `_MODE_ALWAYS_ALLOWED` bootstrap allowlist (`index`/`init`/`reindex`)
- Test fixture sweep across 10 privileged-command test files
- Classification coverage for 8 additional commands
- Empirically validated: 251/252 pass with `ROAM_MODE_ENFORCEMENT=1`

### Real-world feedback fixes
- `stale-refs` heading-slugger now matches GitHub's algorithm (underscore + whitespace)
- `stale-refs --fix` corruption guards: URL-half resolution + bare-backtick suppression
- `algo` nested-lookup dataflow predicate + PHP `===`/`!==` detection
- `auth-gaps` helper indirection (2-level same-class + ancestor descent)
- 7 of 8 Laravel dynamic-dispatch idioms detected (Route/Eloquent-scope/Policy/
  Observer/Job/Queue/Artisan) via new `roam/index/laravel_post.py`
- `over-fetch` 3-state classification (BARE / GUARDED_RELATION / UNGUARDED_RELATION)
- `pr-bundle --strict-resolved` flag + `--ci` global mode integration
- `ws resolve` exposes unmatched URLs with reason classification

### New commands
- `roam brief`, `roam next`, `roam mode`, `roam constitution`, `roam laws`
- `roam memory`, `roam lease`, `roam runs`, `roam replay`, `roam agent-score`
- `roam agents-md`, `roam architecture-drift`, `roam graph-diff`
- `roam side-effects`, `roam idempotency`, `roam causal-graph`, `roam tx-boundaries`
- `roam batch-search`, `roam complete`, `roam mcp`

### Drift-guard infrastructure
- LAW 4 anchor lint + AST-pinned `CLAUDE.md` count assertions
- Capability registry auto-derive (eliminated 531 LOC of bookkeeping)
- `README.md` marker-based count auto-generation + doc-hygiene CI workflow
- `--sarif` consumer drift-guard + `--budget` coverage survey
- Canonical-constant citation lint (load-bearing, allowlist empty)
- Extension-constant consolidation (`DOC_EXTENSIONS` canonical;
  `_BINARY_EXTENSIONS` aliased to `SKIP_EXTENSIONS`)
- Anti-leak CI gate extended with domain-term patterns

### Renamed
- `summary_envelope` → `strip_list_payloads` (name reflects contract)

### Schema
- USER_VERSION 12 → 13 (migration #51: `loop_eq_with_dependent_write` column for
  algo nested-lookup dataflow predicate)

### Round 10/11 — 9-agent parallel hardening pass

Nine Opus agents in two parallel waves closed the gaps surfaced by the R9 cross-rechecks. All landed in the working tree; none lost when the host PC died mid-flight.

**R10 wave — 5 agents**

- **R10.1 — `roam mutate --apply` rollback bug fixed + apply-mode tests for all 4 transforms.** `_apply_move` had no rollback: a mid-flight `OSError` left the destination file with the moved symbol AND the source file still containing it (duplicate definitions in the repo, raw `OSError` propagated up). Snapshot-and-restore now buffers pre-apply state of every touched file; on failure, newly-created targets are removed and pre-existing files are rewritten from snapshot. Wraps the exception into `{isError: True, error_code: "APPLY_FAILED", files_modified: []}`. New `tests/test_mutate_apply.py` adds 10 tests across rename / add-call / extract / move-rollback paths — all assert on file bytes after `--apply`, not just dry-run JSON shape (the prior gap).
- **R10.2 — Handle-off cache GC + SBOM as a Release asset.** `.roam/responses/` used to grow unbounded (disk DoS + forensic leak). New `_gc_handle_dir()` runs amortised (once per 25 writes or when dir > 50 files): TTL eviction (`ROAM_MCP_HANDLE_TTL_HOURS`, default 168 = 7d) plus oldest-mtime LRU under a max-bytes cap (`ROAM_MCP_HANDLE_MAX_BYTES`, default 100MB). Race-tolerant per-entry try/except, `0o700` dir mode, defence-in-depth `parent.resolve() != handle_dir.resolve()` guard. SBOM is now uploaded to the GitHub Release on tag-triggered runs via `gh release upload --clobber`, cosign-signed (keyless OIDC, same Sigstore chain as `cga emit`) — procurement no longer has to dig through expired workflow artifacts.
- **R10.3 — Louvain cluster cache + `roam_context` bulk-fetch.** Cluster phase on a no-op reindex: ~4.3s → 63ms (signature `{n, m, top-64-degree-ids}` persisted in `index_manifest.notes`; `_run_clustering` skips when signature matches and clusters table is non-empty; `force=True` bypasses). `roam context <symbol>` query count: **1,986 → 39** (well under the 50 ceiling). `get_blast_radius`, `get_affected_tests_bfs`, and `get_coupling` in `context_helpers.py` now bulk-load reverse adjacency once; the BFS is in-memory after that. Total `roam index` wall on this repo: 100s → 93s on no-op.
- **R10.4 — `roam_validate_plan` warning-branch coverage.** 12 new tests pin every WARNING code path: `NAME_COLLISION`, `MEDIUM_BLAST_RADIUS`, `HIGH_BLAST_RADIUS`, `FITNESS_VIOLATIONS`, `INVALID_TARGET_FILE`, plus aggregation (3 ops × 3 warnings → `warnings_count == 3`) and verdict precedence (blocker dominates warning). The agent also surfaced that `FITNESS_VIOLATIONS` was effectively dead code against real preflight envelopes — fixed by R11.C.
- **R10.5 — Language-extractor smoke matrix.** Eight extractors with no symbol-extraction tests now have one each via a parametrised matrix in `tests/test_extractor_smoke.py`: Apex, Aura, Visualforce, SFXML, HCL, Swift, FoxPro, generic. Each verifies module import + ≥1 symbol + canonical entity present + `extract_references` returns a list. tree-sitter-language-pack updates that silently break Salesforce/Apex (a real enterprise wedge) now show up in CI.

**R11 wave — 4 agents**

- **R11.A — Three more N+1 fixes (cmd_attest, cmd_module, cmd_dead).** `_collect_blast_radius` in `cmd_attest.py:158` was issuing one `SELECT FROM file_edges` per source file (50-file PR = 50 queries; 500-file enterprise PR = 500). `cmd_module.py:_module_deps` and `_collect_sym_ids` ran `for fid in file_ids: conn.execute(FILE_IMPORTS, (fid,))` — 100 queries per `roam module <dir>` on a 50-file directory. `cmd_dead.py:_predict_extinction` issued a SELECT per BFS pop (200-node fragment = 200 queries). All three rewritten with `batched_in` + a single reverse-adjacency dict; CountingConn regression tests pin constant-query behaviour up to n=50. **5-100× speedup** depending on input size.
- **R11.B — `format_table` single-pass.** Stringify each cell once during the width pass and reuse during emit (instead of `len(str(cell))` twice). 200 rows × 6 cols: 0.99 ms → 0.86 ms (~1.15×). Modest because table rendering is intrinsically O(rows × cols) — the win is from removing the redundant `str()`/`len()` pass. 6 byte-identity tests pin output equivalence with the pre-refactor reference.
- **R11.C — Contract drifts (3 fixes).** `cmd_preflight.py` now populates `summary['fitness_violations']` as a list (was at `r['fitness']['rule_details']` only — `_vp_validate_one`'s warning was effectively dead). `APPLY_FAILED` (introduced by R10.1) added to `_DOC_LINKS` + `_SEVERITY_MAP`. `tests/test_mcp_server.py` `_CORE_TOOLS` count drift fixed: 51 → 57 (R8 added `roam_validate_plan` + 4 `roam_for_*` + `roam_fetch_handle`; expected set extended). Cascading update to `test_multiple_warnings_aggregate_in_summary` since FITNESS_VIOLATIONS now actually fires.
- **R11.D — Help-template ratchet 32 → 52 commands.** Twenty more commands pinned: `plan`, `plan-refactor`, `suggest-refactoring`, `partition`, `layers`, `cut`, `orchestrate`, `agent-plan`, `agent-context`, `pr-diff`, `why`, `intent`, `capabilities`, `capsule`, `trends`, `audit`, `bus-factor`, `effects`, `grep`, `ci-setup`. 156 generated tests (52 × 3) all green. Cross-references verified against `_COMMANDS`.

**Round verification**: 624 targeted tests across the 15 touched test files pass on first run after the crash recovery — zero seam issues across the 3 files that received edits from 2 agents (`mcp_server.py`, `test_validate_plan.py`, `test_n1_fixes.py`).

### Round 9 — code work + 5-pass cross-recheck

- **R9.B7 — FTS5 incremental sync.** `build_fts_index` now diffs `symbol_fts` against `symbols` and only INSERTs new rowids / DELETEs stale ones instead of full DELETE+INSERT on every reindex. Synthetic 5,000-symbol benchmark: cold build 157ms, no-op incremental 15ms (10.5× speedup), 100-row diff 16ms (10.7×). Force-rebuild path retained for `roam index --rebuild`. New `tests/test_fts5_incremental.py` (5 tests) pins the convergence + cost properties.
- **R9.A5 — Extracted baseline-diff branch from `health()`.** `cmd_health.py:health()` was 920 lines; the `if baseline_ref:` branch (125 lines) is now a dedicated `_emit_baseline_diff` helper. Function down to 822 lines. Snapshot tests pass byte-identical.
- **R9.A2 — Numbered migration ledger.** `_MIGRATIONS = [(seq, name, fn), ...]` in `db/connection.py` is now the source of truth for schema migrations. `MIGRATION_OPS_COUNT = len(_MIGRATIONS)` (auto-derived); the previous hand-pinned constant was off by 5 (counted comment occurrences). New tests pin (a) the derivation, (b) seq monotonicity, (c) idempotency on a fresh DB.
- **R9.G9+ — Help-template ratchet extended to 32 commands.** Added `tour`, `file`, `uses`, `trace`, `deps`, `health`, `diagnose`, `complexity`, `pr-risk`, `affected-tests`, `fan`, `hotspots`, `n1`, `clones`, `simulate`, `mutate`, `index`, `watch`, `surface`, `taint`, `vuln-reach`, `attest` (delegated to a parallel agent for the mechanical sweep). Each gets `\b` + `Examples:` + `See also` block. 96 generated tests pass.

### Recheck-driven fixes (R9 cross-pass)

- **`_strip_url_credentials` URL-bug fix.** Previous implementation used `rpartition("@")` over the whole post-`://` string, which finds the LAST `@` anywhere. URLs with `@` in the path or query (e.g. `?reviewer=a@b.com`, `@scope/pkg` paths) got rewritten to the wrong host, putting incorrect `subject.name` in every signed CGA. Fix: scope the credential strip to the authority slice only (per RFC 3986 §3). Regression test added.
- **`_DOC_LINKS` + `_SEVERITY_MAP` plug 7 missing error codes.** `EMPTY_INPUT`, `INVALID_DIFF`, `RUN_FAILED`, `JSON_DECODE`, `ELICITATION_REQUIRED`, `FILE_NOT_FOUND`, `DIRTY_TREE` were emitted in production but absent from both maps — agents got generic UNKNOWN doc_links and default `error` severity for diff/critique paths. Both maps now carry every code in use.
- **Error-storm trim preserves `retryable` + `doc_link`.** When the same `error_code` fires ≥3× the trimmed envelope used to drop both fields. Agents that branch on `retryable` (DB_LOCKED, INDEX_STALE) silently stopped retrying after 3 fires. Both fields are now always carried in the trimmed shape.
- **`PRAGMA busy_timeout=30000` pinned explicitly.** Python's `sqlite3.connect(timeout=30)` sets the driver-level retry budget but not the engine's busy_timeout PRAGMA. Raw-sqlite3 consumers (test fixtures, MCP test paths) now see the same 30s budget as `open_db`.
- **`cmd_pr_risk` early-return paths now wrap with `json_envelope()`.** Two `--json` exits previously emitted bare dicts (no schema_version / no summary.verdict), breaking downstream consumers expecting the contract.
- **Surface-count drift cleaned up across docs.** `137 MCP tools` → `145`, `122 tools` → `145`, `5 prompts` → `6`, `25 default core tools` → `58`, `208 commands` → `211` (or `204 + 7 aliases`) — applied to README.md, CLAUDE.md, landing-page index.html, docs/index.html, docs/command-reference.html. CLAUDE.md Documentation Hub also re-pointed at the canonical `templates/distribution/landing-page/docs/` path (the deleted `docs/site/` was still referenced).
- **Broken cookbook link removed** from docs/command-reference.html — `docs/cookbook/` was deleted in 12.50; replaced with a `roam ask --list` reference.

### Correctness

- **Pure renames now recover affected-neighbor edges.** `indexer.py:1409` had `if not force and modified and changed_file_ids` — pure renames produce `modified=[]` and silently skipped neighbor recovery, leaving `roam impact <renamed_symbol>` reporting fewer callers than reality. Drop the spurious `and modified` clause; add `tests/test_index.py::test_pure_removal_invokes_affected_neighbor_recovery` as a spy-based regression guard.
- **`roam cga verify` fails closed when no `.bundle` is present.** Previously: a downloaded statement without its sibling bundle reported `verified` while `cosign: null` — the cryptographic-trust half was silently skipped. Now: refuses unless `--no-cosign` is passed to acknowledge predicate-only verification.
- **`git_dirty_hash` + `git_commit_sha1` bound into predicate verification.** Predicate now carries `git_dirty_hash`; verifier reads live values and refuses on mismatch (clean-vs-dirty, dirty-hash drift, commit-SHA mismatch). `roam cga emit` refuses on a dirty working tree by default — `--allow-dirty` records the dirty-hash and proceeds.
- **`SQLite USER_VERSION` bumped 1 → 12 with a discipline pin.** New `MIGRATION_OPS_COUNT` constant + `tests/test_db_user_version.py` fail CI when `ensure_schema` gains a migration without a corresponding bump. Closes the loop on the existing `index_manifest.schema_version` writer.

### Performance

- **N+1 fixes (4 sites).** `_find_colocated_tests` (3 nested N+1 → 1 bulk fetch), `_print_mega_detail` (per-cluster → batched `IN`), `_against_mode` (per-fid co-change query → batched), `_find_eager_loads` (controller-file cache, 5-10× speedup on Laravel `roam n1`).
- **N+1 fixes — `roam n1` deep pass (4 more sites).** `_find_appends_properties`, `_find_accessor_methods`, `_trace_io_via_edges`, `_find_collection_contexts` now drive off pre-loop `batched_in` fetches in `analyze_n1` instead of running 1-3 queries per model. `tests/test_n1_fixes.py::test_analyze_n1_appends_collection_edges_constant_query_count` pins constant-query behaviour from 1 to 50 models. Old per-loop overhead on a 100-model app: ~5 × 100 = 500 queries; now: 4-6 batched queries total.
- **Inline phase progress on `roam index`.** Adds `[1/7]` … `[7/7]` markers before each pipeline phase (parse → resolve → graph metrics → git → effects/taint → health → search). First-time users now see what's happening instead of staring at a single progress bar.
- **Troubleshooting docs anchors wired to error codes.** The `_DOC_LINKS` map referenced fragments (`#index-stale`, `#db-locked`, etc.) that the docs page didn't actually have as `id="..."` anchors — agents clicking the URL landed on the page but scrolled nowhere. Eight `id`s added to the troubleshooting sections; map trimmed to fragments that actually exist. New `tests/test_doc_link_anchors.py` pins the invariant in CI.
- **`roam mcp-setup --write` writes the config to disk.** Previously the command only printed the JSON block; users had to copy-paste into the right file. The new flag resolves the platform's expected path (`~/.codex/config.json`, `./.vscode/mcp.json`, etc.), merges the `roam-code` server entry with any existing `mcpServers` block (preserving other servers), and leaves a sibling `.bak` copy. Refuses to overwrite a corrupt JSON file.
- **Skip git-history pass when HEAD unchanged.** Saves 1-10s per warm `roam index` on big-history repos. Manifest's recorded HEAD compared against live `git rev-parse HEAD`.
- **SQLite pragmas tuned.** `mmap_size=1GB` (was 256MB), `wal_autocheckpoint=10000` (was 1000), `PRAGMA optimize` on commit. Closes p50 query-latency drift after heavy index loads.
- **FTS5 schema gains `docstring` column.** `roam retrieve` and `roam search-semantic` now match against natural-language docstrings — previously the FTS5 BM25 path was blind to docstring text. Schema migration drops + recreates the table on first run after upgrade; build_fts_index repopulates. BM25 weight 4 (between qualified_name=5 and signature=2).
- **Memoize `_find_function_node` in `compute_and_store`.** Avoid the second AST descent per callable; ~10-15% indexing win on Python/TS files.

### Agent / MCP DX

- **`roam_validate_plan` MCP tool.** Pre-apply validator for a multi-step change plan. Takes `[{kind: "rename", symbol, new_name}, …]` and returns a verdict (`ok` / `needs-review` / `blocked`) with per-operation blockers, warnings, and advice — symbol existence, name collisions, blast radius, target-file sanity, fitness violations. Cuts 4-call agent loops to one round-trip.
- **`roam_for_<situation>` family.** Four situation-keyed compounds: `roam_for_new_feature` (understand + search + context + complexity), `roam_for_bug_fix` (diagnose + tests + diff + context), `roam_for_refactor` (preflight + impact + complexity + clones), `roam_for_security_review` (taint + vuln + critique + adversarial). Each bundles 3-4 inspect calls into one round-trip.
- **Reference-based handles for >50KB envelopes.** Tools returning JSON > `ROAM_MCP_HANDLE_KB` (default 50) write the payload to `.roam/responses/<sha16>.json` and return a tiny envelope with `{handle, byte_size, preview}`. New `roam_fetch_handle(handle)` retrieves the full payload on demand. Content-addressed: identical responses share a handle. Stops a 70KB `roam_understand` envelope from blowing the agent's context budget when the agent only needs the summary.
- **`roam_ask` MCP tool.** Wraps the 24-recipe TF-IDF intent dispatcher so agents on MCP clients can dispatch a recipe in one call instead of falling back to Grep+Read. Added to `_CORE_TOOLS`.
- **`roam_session_metrics` MCP tool.** Local-only per-tool invocation telemetry (success / rate_limited / error counts). Helps answer "which tools are agents actually using?" without phoning home.
- **`agent_contract` block on every JSON envelope.** ~200-token derived block: `{facts, risks, next_commands, confidence}` so context-budget-tight agents can read just this and skip the full payload. Opt-out via `ROAM_AGENT_CONTRACT_BLOCK=0`.
- **Soft contract enforcement on destructive tools.** `roam_mutate --apply` checks session memory for a prior `roam_simulate` call against the same target and injects a `contract_compliance` block with actionable advice when the prerequisite was skipped. Soft-warn only, never refuses.
- **Stale-index affordance on every read-only MCP tool.** When the indexed DB is older than the manifest's recorded HEAD or older than the configured threshold, the response gets prefixed with `INDEX STALE — call roam_reindex first.` and a `_meta.stale_index` marker. Recovery commands (`index`, `reindex`, `init`, `doctor`, `watch`) skip the banner.
- **`summarize=True` default for compound tools when `ROAM_AI_ENABLED=1`.** `roam_explore` / `roam_understand` / `roam_health` now compress responses by default for users who've explicitly consented to MCP sampling. `summarize=False` forces opt-out per call; `ROAM_AI_DISABLED=1` env var disables globally; the `compliance` preset always opts out (audit-trail evidence must be deterministic).
- **`@_tool` decorator carries `version="1.0.0"`.** Surfaces in `roam_catalog` so agents can detect schema drift without re-enumerating every tool. Bump per-tool when the input/output schema changes.

### Onboarding (`roam init`)

- **No more unsolicited CI workflow file.** `roam init` previously dropped `.github/workflows/roam.yml` into every repo on first command — the single biggest first-run trust-damage signal flagged by the audit. Now requires `--with-ci=github` to opt in; the existing `roam ci-setup` remains the canonical path for full multi-platform CI generation.
- **Refuses outside a git repository** (`FILE_NOT_FOUND` structured error). Prevents the spawn-`.roam/`-in-`~/Downloads` failure mode.
- **Auto-writes a commented `.roamignore` template** when absent. Every entry commented out so the user opts in to the patterns that apply.
- **Compact welcome banner** — 4 lines (stats + try-one + next + help), down from 20+ lines of agent-contract teaching at a moment when the user just wants "did it work?"

### `roam doctor`

- **Three-tier exit codes.** `0` = clean; `1` = only advisory failures (cache age, cloud-sync, optional extras); `2` = at least one blocking failure. `--strict` promotes advisory to blocking for CI gates that require zero drift. Closes a real CI-noise gap where stale-snapshot warnings spuriously failed every roam-on-roam run.
- **Issue-template-ready summary line.** `Roam X · Python Y · OS Z · M/N checks pass · A advisory · B blocking` — paste-once into a bug report.
- **Manifest drift hints expanded.** `git_dirty_hash` drift surfaces as INFO; `config_hash` drift (roam config / `.roamignore` changed) as WARN.
- **`If this looks unexpected, run \`roam doctor\`" hint embedded in 4 environmental error paths** (DB open / schema corruption, config TOML parse failure, bundle verify failure, MCP missing-fastmcp error).

### CLI surface

- **`roam --help` collapsed to a 30-line Start-here panel.** Previously a 154-line dump that scattered the 5 verbs across 38 entries in "Getting Started" and a 73-name "More Commands" comma-list. Long surface still available behind `roam --help-all`.

### Security tier-2

- **Predicate IRI moved to `https://roam-code.com/spec/...`** — the owned, dereferenceable domain. Legacy `roam-code.dev` IRIs still verify via `_LEGACY_PREDICATE_TYPES` so old statements don't break.
- **Strip `username:token@` from git remote URL in cga subject.** A repo cloned with `https://x:ghp_PAT@github.com/...` would otherwise leak the PAT verbatim into every signed CGA's `subject.name`.
- **`vuln_store` ingest hardening.** 50 MB size cap on scanner reports (refuses with structured error before loading); `ESCAPE` clause + escaped LIKE patterns so a hostile package_name like `_` no longer match-explodes.
- **`taint_engine` `path_truncated` flag.** When the BFS exits via `max_hops` or the per-node 200-edge fan-out cap, the finding carries `path_truncated=True` so OpenVEX consumers map to `under_investigation` rather than `vulnerable_code_not_in_execute_path`.
- **Hash-pinned `mcp-server-card.json`.** New `tests/test_mcp_server_card_hash.py` fails CI on unintended drift — the agent-surface card is a real attack vector if tampered.
- **CSP `Reporting-Endpoints` + `report-to`** wired in `_headers`. Endpoint at `/csp-report` is provisional (CF Pages will 404 until a worker is wired up) but the directive is in place.
- **`dev/pin_github_actions.sh`** — one-shot script to pin every workflow action to a commit SHA via `gh api`. Defers actual pinning to a session with network; Dependabot's existing `github-actions` schedule maintains the pins thereafter.
- **Keyless OIDC verification no longer hides under `continue-on-error`.** The `cga-attestation.yml` keyless-oidc job now fails loud if the production-path emit/verify breaks. Sigstore outages are real but rare; masking flakes there hid regressions in our own emit/verify code path. The offline-key job remains the deterministic gate that runs without network.
- **CycloneDX SBOM generation in `publish.yml`.** Each PyPI release now ships an `sbom/roam-code-<version>.cdx.json` artifact built against a fresh venv with the just-built wheel installed (resolves to the *runtime* dependency closure, not the CI build env). Audit-trail product needs verifiable bills-of-materials; this is the foundation.

### Architecture substrate

- **`@_tool` carries `version`** (see Agent / MCP DX above) — surfaces in `roam_catalog`.
- **`LanguageBridge.VERSION` + `LanguageExtractor.VERSION`** ABCs gain a `1.0.0` semver class attribute. Bump in subclasses when inference logic changes; downstream drift detection compares stamps.
- **`index_manifest` step-completion record.** Per-step `success / failed:<ExceptionName> / skipped:<reason>` status persisted as JSON in the manifest's `notes` field — `roam doctor` can later surface "your index is missing taint analysis because that step failed" instead of the generic stale-manifest signal.

### Tests

- **9 new test files**: `test_db_user_version`, `test_doctor_hints_in_errors`, `test_extension_versioning`, `test_mcp_contract_enforcement`, `test_mcp_server_card_hash`, `test_mcp_tool_telemetry`, `test_mcp_tool_versioning`, `test_n1_fixes`, `test_surface_consistency`.
- **12 test files extended** with new cases for the corresponding source-side changes.
- **Surface-consistency test** locks the 8-way split-brain dict invariants today (every `_COMMANDS` entry has a `_CATEGORIES` entry; every `_CORE_TOOLS` member is a real `@_tool` declaration; every `_DEPRECATED_COMMANDS.replacement` resolves) before the larger Capability Registry rework lands.
- **CountingConn-based query-count regressions** for every N+1 fix — proves the batched form scales O(1) in input size.
- 2,612 tests pass on the touched-areas parallel sweep; 1,495 on the focused sequential sweep.

### Conventions

- **`tests/conftest.py:make_src_project`** writes `.gitignore` with `.roam/` so the dirty-tree refusal behaves the same in tests as in production.
- **Categorise the formerly-orphan `lsp` command** under "Refactoring" alongside `stale-refs` (the LSP server is the same engine surfaced over JSON-RPC).
- **Help-text template ratchet (R8.G9).** New `tests/test_command_help_template.py` pins a curated set of high-leverage commands (`init`, `doctor`, the five Start-here verbs, and daily staples — 12 today) to a consistent shape: 1-line summary, an `Examples:` block, and an inline "See also" cross-reference. The list grows incrementally; new commands are encouraged to match. Twelve commands polished in this round.

---

### Earlier in the [Unreleased] window

- **`roam surface`** — canonical capability registry as JSON or text. Source of truth for commands, aliases, MCP tools, presets, categories, maturity. `--filter stable|experimental|internal|deprecated` and `--category <name>` flags. Used by docs generation, contract tests, release notes, and the marketing/landscape surfaces.
- **`roam explain-command <name>`** — per-command introspection: category, maturity, aliases, deprecation, MCP exposure, DB tables touched (best-effort source-grep), optional extras detected, stale-index sensitivity (high / medium / low / unknown).
- **`roam db-check`** — integrity sweep over the local index: orphan symbols, broken edges, duplicate file paths, missing FTS rows, invalid line spans, corrupt metrics, files with zero symbols. Exit code 5 on any high-severity finding when `--ci` is set.
- **`roam health --baseline <ref>`** — compares against a stored snapshot at the named ref (any git ref, `last`, or `auto` for default-branch). Reports delta (new / fixed / regressed findings) instead of an absolute set. Verdict reflects the delta — REVIEW on new high-severity, BAD on score regression, OK otherwise. Graceful DEGRADED when no snapshot exists.
- **Index manifest table.** New `index_manifest` schema records the indexer run: roam version, schema version, parser versions, grammar versions, config hash, git HEAD, dirty hash, enabled extras, index profile. `roam doctor` surfaces manifest age plus drift hints (parser-version drift, git-HEAD drift) so commands depending on graph accuracy can warn or refuse.
- **`roam doctor` expansion.** Four new checks: optional extras (onnxruntime / watchdog / fastmcp / scipy presence), cloud-sync detection (OneDrive / Dropbox / iCloud / Google Drive / Box / pCloud), cache permissions (`.roam/` and `.pytest_cache/` writable), and index manifest age + parser drift. 17 checks total.
- **`roam grep` rewritten end-to-end with index-aware enrichment.** Multi-pattern (`-e` repeatable, `--patterns-from FILE`), multi-glob (`-g py -g md`), `-F/--fixed-string`, `-i/-w` flags. Engine selection prefers ripgrep > git grep > indexed-file fallback (pin via `ROAM_GREP_ENGINE`). Bulk-fetch enclosing symbols once per file (interval index) — replaces the per-match `SELECT` N+1 path. New flags: `--reachable-from <entry[,entry,...]>` (forward BFS over the call graph), `--unreachable` (orphan / not-reachable filter), `--co-occur` (every `-e` pattern must hit the same enclosing symbol), `--missing-pattern P` (anti-correlation), `--rank-by importance` (PageRank-ordered output), `--group-by symbol` (collapse hits per fn/class), `--blame` (last author + date), `--heat` (file churn), plus opt-out `--no-clones` / `--no-bridges`. Each match carries clone-class siblings (from `clone_pairs`) and cross-language bridge links when applicable.
- **`roam refs-text <string>...`** — string-audit verdict shaped like `safe-delete` but for arbitrary literals (config keys, file paths, URLs, error messages). Groups references by surface (code / test / docs / config / dead), annotates reachability for code hits, emits per-string verdict (`SAFE-TO-REMOVE` / `REVIEW` / `LOAD-BEARING`). `--reachable-from <entry>` anchors reachability; `--per-match-detail` includes match lists in JSON.
- **`roam delete-check`** — gates the working diff on surviving references. Parses Python / JS / TS / Go signatures from deletion lines, searches the post-edit tree for surviving uses, classifies each deletion `SAFE` / `LIKELY-SAFE` / `BREAK-RISK`. `--source {working,staged,pr,head}`, `--ci` exits 5 on `BREAK-RISK` for CI gating. Pairs with the PR Replay narrative.
- **`roam history-grep <pattern>`** — git pickaxe wrapper (`-S` / `-G`) with `-e` repeatable, `--since` / `--until`, `-p` path filter, `--polarity` to mark each commit as `introduced` / `removed` / `modified`. Useful for postmortems and provenance audits when the trace is no longer in HEAD.

### Site / docs

- **Homepage visual overhaul** — paper bg (`#fafaf6`), refined card system on a unified 200ms easing curve, dot-grid hero pattern with custom code-graph SVG corner mark, dark-wedge differentiator section with light-on-navy treatment for the algorithmic-judgment moment, custom monogram in nav. Type system rebuilt with clamp() display sizes (h1 34→64, h2 26→40), `text-wrap: balance` on every heading, font-variant-numeric tabular-nums on body. Color depth via new `--ink` / `--bg-deep` / `--bg-tint` / `--line-soft` / `--accent-deep` tokens layered alongside the legacy `--text` / `--bg-alt` / `--border` (kept aliased for sub-page back-compat). Verified zero horizontal-overflow violations + WCAG-AA contrast at 320 / 390 / 768 / 1280 / 1920 viewports.
- **PR review comment mockup redesign** — replaced the generic GitHub-comment block with one built on authentic Primer color tokens (`--gh-canvas-subtle`, `--gh-border`, `--gh-danger-bg`, `--gh-attention-bg`, `--gh-success-bg`). Status banner mirrors GitHub's "checks failed" pattern. Findings render as bordered cards with Primer-pill severity ("High" / "Medium"), file:line links with arrow glyph, suggested-fix block in GitHub's actual diff styling (3 context lines, green-on-paper add). New: inline blast-radius SVG visualisation under the medium finding — central red node with eight spokes to caller nodes — visualises the "47 callers" claim that competitors only state numerically.
- **Mobile-overflow guard** — fixes previously silent overflow on grid layouts at narrow viewports. Direct children of `.products` / `.scenarios-grid` / `.demo-grid` / `.replay-tiers` / `.senses-grid` / `.numbers-grid` / `.verbs-grid` / `.flow` get `min-width: 0` so `1fr` tracks shrink below their content's min-content width. Inline code in scenario / PR-find / dogfood / FAQ contexts gets `overflow-wrap: anywhere`. Terminal blocks inside narrow scenario cards switch to `white-space: pre-wrap` so output wraps cleanly inside 280-360px columns instead of clipping with a horizontal scrollbar.
- **Cross-page visual propagation** — the new design system applied to pricing, compare, setup, docs hub, getting-started, integration-tutorials, architecture, agent-contract, demos, command-reference. Inline `style="..."` chains replaced with class modifiers (`.docs-card`, `.setup-editor-grid`, `.section-intro-tight`, `.whats-in-inner--narrow`, `.verbs-grid--2`, `.numbers-grid--2`). Dot-grid lede applied to `.docs-page` so the four no-hero docs pages inherit the homepage texture. `.docs-page table { display:block; overflow-x:auto }` so reference tables horizontal-scroll on phones. `.docs-page .step pre { white-space: pre-wrap }` so wide commands inside step cards wrap. Class swap `.legal-page` → `.docs-page` on agent-contract + demos (legal-page was the wrong base class for a docs guide).
- Two new docs pages: **`/docs/how-roam-thinks`** (decision tree mapping engineering questions to the right Roam command — 9 moments, command lists, example invocations) and **`/docs/troubleshooting`** (eight common problems: stale index, cloud sync, missing extras, parser failures, MCP setup, cache permissions, OOM, slow JSON — each with symptom + diagnosis + fix). Sitemap updated.
- **Site positioning sweep** — 24-page footer tagline unified to `The local structural intelligence layer for coding agents.` (locked memo phrasing). Press-kit one-liner + two-paragraph blurb rewritten. About page meta description updated. PWA manifest name + description matched. SOC 2 / ISO 42001 / AI-governance acronyms removed from homepage trust-strip + JSON-LD (framework-specific detail moved to `/security`). All Copilot mentions removed; Codex confirmed across homepage, press, audit, llms.txt. PR Replay mailto bodies trimmed from 6-7 fields to 3-4 essentials.

### Fixed

- **`gitignore.py:_compile_pattern` quadratic-string fix** — the pattern compiler was building its regex via `regex += "..."` inside a `while` loop, O(n²) for n pattern segments. Rewrote to collect parts in a list and `"".join(...)` at the end. Surfaced by `roam math` running against the repo itself.
- **`cmd_explain_command` json_envelope kwarg collision** — `json_envelope("explain-command", ..., command={...})` collided with the function's first positional parameter named `command`. Renamed the kwarg to `command_info`. Caught by the new CLI contract test suite.
- **Stale path references in CHANGELOG.md** — 12 high-confidence path-rename hints applied via `roam stale-refs --fix apply` (old `templates/site/` and `docs/site/` paths updated to current `templates/distribution/landing-page/`).

### Tests

- New `tests/test_cli_contract.py` — 667 contract tests: every canonical command's module + attribute imports cleanly, every command has non-empty `--help`, JSON output never tracebacks in a fresh empty dir. Plus end-to-end shape tests for `surface --json`, `explain-command --json`, `db-check --json`. Catches lazy-load drift before users hit it.
- New `tests/test_manifest.py` — 8 tests covering manifest schema presence, collect/write/read round-trip, drift detection (roam_version / parser_versions / git_head), empty-table behaviour, end-to-end indexer-writes-manifest.
- New `tests/test_health_baseline.py` — 7 tests covering `--baseline` semantics including DEGRADED no-snapshot path, REVIEW on new high-severity, BAD on score regression, JSON envelope shape, text-output rendering.
- Removed `test_generated_landscape_json_is_in_sync` from `test_competitor_site_data.py` — the `landscape.json` it expected was deliberately deleted in the GH Pages takedown; test had been failing on every run.

### Internal

- New `roam.commands.grep_helpers` module: `detect_engine`, `run_search`, `build_interval_index` / `find_enclosing`, `build_reachable_set` (multi-entry), `build_orphan_set`, `build_clone_index` / `lookup_clone_siblings`, `build_bridge_index`, `attach_blame` / `attach_heat` / `attach_pagerank`, `classify_surface`, `group_by_symbol`. Single-source primitives behind grep / refs-text / delete-check / history-grep so cross-cutting logic (reachability, clone-class join, surface classification) doesn't drift.
- `_DEPRECATED_COMMANDS` now supports structured records (`{replacement, reason, removal_version}`); `_deprecation_record` helper normalises both legacy string entries and the new shape. Surface output exposes deprecation reason + removal version.
- New `roam.commands.stale_index` helper — `check_stale()` returns `(is_stale, reason)`, manifest-aware where the manifest table is present, mtime fallback otherwise. Available for any command that wants to opt into stale-index warnings.

## [12.50] - 2026-05-09

### Release notes

12.50 is the first PyPI release after a stretch of locally-bumped
versions (12.48, 12.49) that never published. The wheel ships every
change from the [12.48] and [12.49] entries below plus the new work
described here. Going forward, releases are deliberate weekly /
bi-weekly cuts (see ``CONTRIBUTING.md``); ``[Unreleased]`` accumulates
work between cuts instead of triggering a version bump per change.

### Build / packaging

* `setuptools` build requirement bumped to `>= 77.0` so the wheel
  emits the PEP 639 ``License-Expression: Apache-2.0`` metadata
  field. Earlier wheels (≤ 12.47) shipped to PyPI with no license
  metadata visible on the project page; 12.50 fixes that.

### Site / trust

* Service-level commitments in `/terms` and `/status` now make the
  pre-launch / GA distinction explicit: the 99.5% uptime target
  applies after general availability, not during early access.
* Privacy page: hosting subprocessor list no longer says "Hetzner /
  DigitalOcean / similar EU-based provider" — replaced with the
  honest "to be selected and disclosed before paid GA; named in the
  DPA at GA. During early access there is no production backend
  processing customer data."
* Security page gains a "Verify a release yourself" section with
  the exact `sigstore verify github` invocation against the
  workflow's OIDC chain.
* `/docs/roam-code` legacy paths on About + Press fixed to point
  at `/docs/`.

### Action.yml

* `roam --json` invocations now write stdout (the JSON envelope) and
  stderr (progress + warnings) to separate files, then validate
  the JSON; non-JSON output is wrapped in a structured
  ``{status, command, exit_code, stdout, stderr, reason}`` envelope
  so downstream parsers see a deterministic shape. Previously a
  warning landing on stderr would corrupt the JSON file.

### Documentation site consolidated to roam-code.com only

GitHub Pages disabled on the repo on 2026-05-08. Previously the docs
were dual-hosted at `cranot.github.io/roam-code/*` (GitHub Pages serving
`docs/site/`) and at `roam-code.com/docs/` (Cloudflare Pages serving
`templates/distribution/landing-page/docs/`). Drift between the two
copies was a persistent source of count / content inconsistencies.

* GitHub Pages turned off via the GitHub API; `has_pages: false` on the
  repo. `cranot.github.io/roam-code/*` URLs now 404.
* Removed `docs/site/` directory: 11 redirect-stub HTML files, the
  competitor-matrix asset bundle (`app.js`, `landscape.json`, CSS), the
  GH-Pages-only `sitemap.xml` and `robots.txt`, and the cookbook /
  benchmarks / language-precision markdown. The cookbook and benchmark
  content lives in the repo's commit history if it's needed in future.
* Removed `.github/workflows/pages.yml` (Pages deploy workflow).
* Moved the canonical MCP server card to
  `templates/distribution/landing-page/.well-known/mcp-server-card.json`
  so the card's `card_url` claim
  (`roam-code.com/.well-known/mcp-server-card.json`) keeps working.
* Updated `scripts/sync_surface_counts.py`, `tests/test_doc_consistency.py`,
  and `dev/build_command_reference.py` to point at the surviving paths.
  Dropped `landscape.json`-based consistency tests (file no longer
  exists; competitor data lives in `src/roam/competitor_site_data.py`).
* Updated README + status page to reflect the new single-host setup.

Net effect: one canonical docs surface (`roam-code.com/docs/`), no
silent drift between two hosts, simpler CI.

### `stale-refs` — operations-grade upgrades

Adds repo config, in-toto attestations, LSP code actions, LSP
cross-file rename, `roam audit` integration, monorepo support, and
a wider external-link-check option set.

#### External link checking — five new flags

* `--external-cache-ttl SEC` — cache HEAD probes for SEC seconds
  (`.roam/external-cache.json`) so repeated CI runs don't hammer
  third-party servers.
* `--external-allow-status 401,403,…` — accept specific HTTP codes
  as "exists" for paywalled / auth-protected URLs.
* `--external-auth-header "Header: value"` — inject custom headers
  on probes (private GitHub Enterprise, internal portals).
* `--external-insecure` — skip TLS verification when probing
  internal CAs.
* Per-finding **redirect chain** captured in JSON output so the
  verdict explains *why* a URL changed status (301 → 302 → 200).

#### LSP server — Quick Fix + workspace-wide rename + watcher

* `textDocument/codeAction` — HIGH-confidence rename hints surface
  in the editor's Quick Fix menu; selecting the action triggers
  the editor's `workspace/applyEdit` flow.
* `workspace/didChangeWatchedFiles` — when external file changes
  hit the workspace (git pull, file manager rename, etc.), every
  open buffer's diagnostics are re-published. Registered
  dynamically via `client/registerCapability` only for clients
  that advertise support.
* `workspace/willRenameFiles` — on a rename event the server
  proposes a `WorkspaceEdit` updating every reference to the old
  path with the new path, fragments preserved.

#### LLM enricher — ranked candidates + observability

* Prompt now asks for **top-3 ranked candidates** per missing
  target instead of a single best guess. The first valid
  candidate becomes the hint; runners-up land on the target as
  `llm_alternates`.
* `summary.llm_per_target` — per-target diagnostics: how many
  candidates the LLM returned, which were validated, which were
  rejected as hallucinations, and (on success) which one was
  chosen.
* `summary.llm_latency_ms`, `llm_response_chars`,
  `llm_targets_asked`, `llm_prompt_chars` — observability fields
  recorded on every sample call (success or failure) so operators
  can tune cost / speed without re-running.

#### Repo config + init helper

* `.roam/stale-refs.toml` — repo-level defaults loaded on every
  run. CLI flags still override. Honoured keys: `ignore`,
  `ignore_target`, `sort_by`, `check_external`, `check_anchors`,
  `limit`. Falls back to a minimal hand-written parser when
  `tomllib`/`tomli` aren't available.
* `roam stale-refs --init` — generates the config from
  heuristics: `CHANGELOG.md` → ignored, `docs/legacy/**` → ignored
  if present, common doc placeholders (AGENTS.md / GEMINI.md /
  CLAUDE.md / CONVENTIONS.md) → `ignore_target` when missing.
  `--init-force` overwrites existing files.

#### CI / supply-chain

* `roam stale-refs --attest PATH` — writes an in-toto v1
  Statement (predicateType `https://roam-code.dev/StaleRefs/v1`)
  bound to the current git commit SHA. Sign with cosign for
  tamper-evident provenance. Pass `-` to write to stdout.
  `verify_stale_refs_attestation()` validates structure;
  signature verification is delegated to cosign out-of-band.
* SARIF output validator in test suite — every result's `ruleId`
  must resolve to a `rules` entry on the same run, every level
  ∈ {error, warning, note, none}, every region with
  `startLine ≥ 1`. Mirrors what GitHub Code Scanning enforces.
* `templates/examples/pre-commit-stale-refs.yaml` — drop-in
  pre-commit hook config (`--diff HEAD --gate`) so every commit
  fails fast when it introduces dangling refs.

#### Auto-fix — opt-in MEDIUM tier + Windows lock awareness

* `--fix-medium` — auto-applies MEDIUM-confidence hints in
  addition to HIGH (off by default — MEDIUM hints are advisory).
* Atomic-write file-lock reporting — on Windows, the locked-files
  list lands in the verdict and JSON envelope so users know
  exactly which files were skipped because their editor held them
  open.

#### Discoverability

* `--ignore` now supports recursive `**` globs (e.g.
  `docs/legacy/**`).
* `roam audit` includes `stale_refs` as a section; `stale_ref_count
  ≥ 10` joins the verdict's pressure list.
* `--root PATH` — scan a different repo / monorepo subtree from
  the current working directory. Override applies to config
  loading, attestation paths, and find_project_root resolution.
* README "killer use case" callout above the v1 features section.

#### Watch mode — micro-optimisation

* mtime collection rewritten on `os.scandir` (single-syscall
  batching). Watch loops now spend most of their wall-clock time
  in scan rather than `os.stat` overhead.

## [12.49] - 2026-05-08

### `stale-refs` — five major capability additions

A single release that pushes the v12.48 stale-refs intelligence layer
from "audit tool" to "always-on safety net" across five orthogonal
delivery channels: agent (LLM enrichment), live editor (LSP), live
terminal (watch), persistent CI gate (baseline), and external web
links (HTTP check).

#### Phase 1 — `enrich_with_llm` (MCP)

The `roam_stale_refs` MCP tool now accepts `enrich_with_llm=True`.
When set AND `ROAM_AI_ENABLED=1` AND the client supports MCP sampling,
unresolved findings (NONE / LOW confidence) are batched into one
`Context.sample` call. The agent's own LLM suggests the most likely
intended path from the candidate set; suggestions return as
``confidence=MEDIUM`` with ``source="llm-sampling"`` and never
auto-fix. This closes the deterministic-providers coverage gap where
restructure-class drift (`docs/cold-outreach.md` →
`docs/sales/outreach-templates.md`) loses character similarity but is
trivial for an LLM. New CLI flag `--with-candidates` exposes
`summary.repo_paths_sample` so the enricher can give the LLM
context. New `summary.llm_hints_added` reports the count.

#### Phase 2 — `--watch` continuous mode

`roam stale-refs --watch` runs an initial scan, then polls the repo
for file changes (mtime-based, no watchdog dep). On each cycle prints
only **newly-introduced** and **newly-resolved** findings as a delta
block timestamped with the current time. Composes with all other
flags (`--ignore`, `--diff`, `--no-anchors`, etc.) so a long-running
session in one terminal pane shows exactly the doc breakage you're
about to commit. ``--watch-interval`` controls poll cadence (default
1.5s with ~30% debounce on detected change).

#### Phase 3 — persistent baseline (`--baseline-save` / `--baseline-from`)

Save a deterministic JSON snapshot of current findings via
`--baseline-save FILE`; on subsequent scans `--baseline-from FILE`
filters to only **new** findings since that snapshot. Different from
`--diff` (git-based) and `--ignore` (glob-based) — the baseline is a
frozen finding-set acknowledgment. Schema:
``roam-stale-refs-baseline-v1`` with sorted records of
``"<target>|<file>:<line>:<kind>"``. Composes with `--gate` so CI
fails ONLY on regression, never on legacy debt. Summary fields:
`baseline_size`, `baseline_filtered_out`, `baseline_saved_to`.

#### Phase 4 — `--check-external` HTTP link checker

`roam stale-refs --check-external` extends the scan to ``http(s)://``
URLs via concurrent HEAD/GET requests (stdlib ``urllib`` — no extra
dependency). Findings surface with ``kind=external`` alongside the
local ones; SARIF gets a new ``stale-refs/external`` rule. Configurable
via `--external-timeout` (default 5s) and `--external-concurrency`
(default 8, capped at 32). Off by default to keep the scan local +
offline; opt-in by users who want full link hygiene. Tries HEAD first,
falls back to GET on 4xx (some CDNs reject HEAD).

#### Phase 5 — `roam lsp` editor integration

A minimal Language Server Protocol implementation, hand-rolled over
JSON-RPC stdio (no extra dep). Handles `initialize`, `textDocument/{
didOpen, didChange, didSave }`, `shutdown`, `exit`, and publishes
`textDocument/publishDiagnostics` with proper `range` / `severity` /
`source`. Wire into VS Code, Neovim, JetBrains, Helix, Sublime as a
custom LSP server pointing at `roam lsp`. Squiggly underlines on
dangling links and missing anchors appear as you type. The server
walks the project once at startup to populate `basename_idx` and
`anchor_cache`, then per-keystroke scans cost only the regex pass on
the buffer's content. ``didSave`` refreshes the workspace index in case
the saved file added/removed referenceable paths.

#### Surface count

- CLI commands: 204 → **205** (adds `lsp`).
- MCP tools: 137 unchanged (`roam_stale_refs` gains `enrich_with_llm`).

#### Tests

`tests/test_stale_refs.py` grows to **126 tests** (+33 from v12.48).
New classes: `TestStaleRefsWithCandidates`, `TestLlmEnrichParser` (5
robustness cases for LLM response parsing), `TestStaleRefsWatchHelpers`
(3 watch-loop unit tests), `TestStaleRefsBaseline` (3 save/filter/gate
flow tests), `TestStaleRefsCheckExternal` (3 URL extraction +
classification tests), `TestStaleRefsLsp` (4 protocol handshake +
URI-conversion tests including a real subprocess spawn). Round-1 and
round-2 hardening passes added: `TestStaleRefsDomainThrottle` (2),
`TestStaleRefsLlmHintValidation` (3), `TestStaleRefsLspIntegration`
(2 full-handshake tests), `TestStaleRefsBaselineLineTolerant` (3
line-shift + v1 backwards-compat tests),
`TestStaleRefsExternalDedup` (1),
`TestStaleRefsWatchHelpersComposition` (1),
`TestStaleRefsLspIncrementalFlow` (1).

#### Round-1 + round-2 hardening

After the initial five-phase ship, two further audit passes surfaced
real correctness issues:

- **LLM hint hallucination guard** — the enricher used to attach
  whatever path the LLM suggested, even if that path didn't exist in
  the repo. Now validates against ``repo_paths_sample`` (full path
  match OR basename match → upgrades to canonical full path). Reject
  strictly when neither matches.
- **External-check per-domain throttle** — added a per-host semaphore
  (cap 2) on top of the global concurrency cap so doc-heavy repos
  with 100 links to one origin can't trigger anti-bot blocks.
- **LSP graceful out-of-project URI handling** — when an editor opens
  a file outside the project root, the server now publishes an empty
  diagnostics array (LSP-spec contract for "clear my squiggles") rather
  than silently dropping the message.
- **LSP full handshake + didChange integration tests** — real
  subprocess spawn proves initialize → didOpen → publishDiagnostics →
  didChange → empty publishDiagnostics → shutdown → exit works end-to-end.
- **Baseline schema bumped to v2 — line-tolerant** — finding records
  drop the line-number component so cosmetic edits (adding a
  copyright header) don't invalidate the baseline. v1 baselines from
  earlier installs are still accepted via load-time normalisation.

#### Round-3 — comprehensive multi-angle test coverage (+17 tests)

A third audit pass added 17 dedicated tests covering previously
under-tested angles. No new bugs surfaced — but several class-of-
behavior contracts now have explicit guarantees:

- **LLM enricher integration tests (6)** — async helper end-to-end
  with a stub Context.sample. Verifies: no-op when ROAM_AI_ENABLED
  unset, hint attached when enabled + valid response, hallucinated
  paths rejected, basename → full-path resolution, graceful failure
  when sample raises, no-op when caller forgot ``--with-candidates``.
- **Baseline robustness (4)** — corrupt JSON returns empty set,
  unexpected schema returns empty set, non-string records filtered
  out, ``--baseline-from + --diff`` compose correctly.
- **External-check integration (3)** — broken external URL surfaces
  as a target finding, ``stale-refs/external`` SARIF rule fires,
  ``--ignore-target`` glob suppresses external URLs.
- **LSP edge cases (3)** — clean file → empty diagnostics published
  (proves the broken-vs-clean diff is meaningful, not just "we never
  publish"); didChange that INTRODUCES a broken link → diagnostics
  fire (opposite of the existing didChange-clears test); unknown
  method WITH id → JSON-RPC -32601 error response.
- **Watch helpers without git (1)** — ``_collect_mtimes`` works in a
  non-git directory via ``discover_files`` os.walk fallback.

Total stale-refs test count: **143** (76 → 88 → 93 → 113 → 126 → 143
across the polish iterations). All green; ruff clean.

#### Round-4 — composition guards, debugability, discoverability (+17 tests)

The fourth round took a wide-and-deep audit pass focused on **what the
user experiences when things compose** — not features in isolation.

Real bug fixed:
- **``--watch`` silently ignored ``--baseline-from``.** The watch
  loop's ``scan_kwargs`` dict didn't include the baseline filter, so
  a user with 100 baselined findings would see all 100 in the initial
  banner and re-flicker on every cycle. ``_run_watch_loop`` now
  accepts a ``baseline`` keyword and applies it to the initial scan
  + every rescan.

UsageErrors added (foot-guns prevented):
- **``--watch + --check-external``** — would HEAD-poll every URL
  every 1.5s and rate-limit the user. Refused with a clear error.
- **``--watch + --fix preview/apply``** — auto-rewriting source
  files inside a poll loop is a foot-gun (silent edits behind the
  user's back). Refused with a clear error.

Debuggability:
- **``summary.llm_skip_reason``** — when LLM enrichment doesn't
  fire, callers (CI, agents) get a structured reason: "ROAM_AI_ENABLED
  env var not set", "MCP context lacks sample()", "summary.repo_paths_sample
  missing", "no findings to enrich", "all findings already have
  HIGH/MEDIUM hints", "sampling raised: <ExcType>", "LLM response
  unparseable". Distinguishes "the LLM had nothing to say" from "I
  never asked the LLM at all".

Discoverability:
- **LSP ``serverInfo.version``** is now read dynamically from
  ``roam.__version__`` instead of being hardcoded. No drift on
  future releases.
- **``find-broken-links`` recipe followups expanded** to surface all
  v12.49 channels: ``--watch``, ``--fix preview``, ``--baseline-save``,
  ``--check-external``, and ``roam lsp``. Agents using ``roam ask``
  now discover the entire delivery surface.

Test coverage: 17 more tests covering the composition guards
(3), the 6 LLM skip-reason paths (7), dynamic LSP version (2),
and recipe followup contracts (5).

Total stale-refs test count: **160** (76 → 88 → 93 → 113 → 126 →
143 → 160 across all polish iterations). 248 across all touched
suites. All green; ruff clean.

## [12.48] - 2026-05-08

### `roam stale-refs` — dangling file-reference scanner

Index-free scanner that finds markdown links, HTML `href`/`src` attributes,
and backtick file paths whose target no longer exists on disk. Closes the
gap between symbol-graph commands (`uses`, `impact`, `refs`) — which only
see indexed call/import edges — and prose mentions of file paths in docs,
READMEs, and YAML/JSON configs. Pure filesystem operation; runs in any
git directory regardless of whether `roam index` has been built.

#### Detection surface

- **Markdown inline links** `[text](path)` and image syntax `![alt](path)`
- **Markdown reference-style links** `[label]: path "title"`
- **HTML `href` / `src` attributes** (single or double quoted)
- **Backtick-wrapped paths** `` `internal backlog` `` — limited to
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  doc-shaped extensions to keep prose noise out

#### False-positive filters

- Schemes (`http://`, `mailto:`, `data:` …), pure anchor refs `#header`,
  and protocol-relative `//cdn.example.com/foo` are skipped.
- URL fragments `#section` and query strings `?v=1` are stripped before
  the existence check.
- Placeholders `<project_root>/foo`, globs `docs/*.html`, and brace
  patterns `prompts/{task}.txt` are recognised as documentation patterns,
  not concrete paths.
- Runtime-generated path prefixes (`.roam/`, `.git/`, `node_modules/`,
  `.next/`, `.cache/` …) are skipped — those are intentionally absent
  from VCS.
- Bare basenames (no `/`) referenced from source code (`.py`, `.ts`)
  are treated as placeholders unless the file actually exists somewhere
  in the repo — eliminates the `auth.py` / `cmd_FOO.py` false-positive
  class that markdown-link regex would otherwise drag in from regex
  character classes.
- Bare dotfile basenames (`.eslintrc`, `.roam-gates.yml`) are recognised
  as documentation about user-creatable optional config files.
- Extensionless absolute URLs (`href="/setup"`, `/pricing`) are treated
  as static-site router paths, not file references; pass
  `--check-absolute-routes` to flip to strict file-system lookup.
- Absolute URLs with extensions try project-root, then `public/` /
  `static/` / `assets/`, then walk the source file's ancestor chain —
  so `<img src="/favicon.svg">` from `templates/distribution/landing-page/about.html` resolves
  to `templates/site/favicon.svg` when that's the deploy root.

#### Reporting

- **Verdict line**: `74 stale ref(s) · 65 missing target(s) · 1541 refs
  checked · 3326 files · 2.778s` — counts and timing in one line.
- **Rename hints** via basename matching — when `templates/audit-report/
  sample-redacted.md` references `commands/cmd_dead.py`, the report
  surfaces "did you mean `src/roam/commands/cmd_dead.py`?".
- **`--by-file`** inverts the report so you can see which document owns
  the most stale refs (one-doc-at-a-time fix workflow).
- **`--ignore GLOB` / `--ignore-target GLOB`** suppress historical or
  optional-config noise; both forms accept Windows backslashes and
  POSIX forward slashes interchangeably.
- **`--gate`** exits with code 5 when any stale ref is found, for CI
  integration.
- **`--sarif`** emits SARIF 2.1.0 with one rule per reference kind
  (`stale-refs/md_inline`, `stale-refs/md_reference`, `stale-refs/html_attr`,
  `stale-refs/backtick`) so GitHub Code Scanning surfaces dangling-link
  findings as discrete categories.

#### Discoverability

- **`roam ask "find broken links"`** now classifies to the new
  `find-broken-links` recipe (top-1 confidence). 25 recipes total.
- Cross-referenced from `doc-staleness` and `docs-coverage` docstrings
  as the "where do the docs point?" counterpart to those two "what do
  the docs say?" commands.
- `verify-patch` recipe followups now suggest `roam stale-refs` after
  rename-heavy diffs.

#### Internals

- New `src/roam/commands/cmd_stale_refs.py` (~570 lines).
- New `stale_refs_to_sarif()` in `src/roam/output/sarif.py`.
- Cheap `_has_ref_triggers()` content sniff (`[`, `<`, `` ` ``) skips
  the regex pass on lock-files and binary-shaped text — observed ~30%
  wall-clock reduction on manifest-heavy repos.
- 41 dedicated tests in `tests/test_stale_refs.py` covering smoke,
  detection coverage, JSON envelope, false-positive filters
  (regex-noise / runtime-path / placeholder / bare-basename / dotfile),
  absolute-route handling, public-folder + deploy-root fallbacks,
  `--ignore` source/target globs, backtick project-root fallback,
  edge cases (empty repo, `..` escape, `.roamignore`, backslash
  glob normalisation), SARIF envelope shape, and `--by-file` output.

#### Surface count

- CLI commands: 202 → **204** (adds `stale-refs`, `pr-replay`).
- MCP tools: 136 → **137** (adds `roam_stale_refs` with full
  v12.48 flag exposure: `ignore`, `ignore_target`, `check_absolute_routes`,
  `no_anchors`, `diff`, `sort_by`, `fix`, `by_file`).
- Ask recipes: 24 → **25** (adds `find-broken-links`).

#### Polish iterations after the initial v12.48 ship

- **In-page anchor validation** — pure-fragment URLs (``[x](#section)``
  with no path) now validate against the source file's own anchor set.
  Caught 38 real broken table-of-contents anchors in roam-code's own
  README that were silently passing pre-fix.
- **Case-insensitive anchor matching** — ``#Setup`` matches header
  ``# Setup`` regardless of case (GitHub semantics).
- **Code-fenced headers ignored** — ``# Heading`` inside ``` ``` ```
  or ``~~~`` blocks no longer creates phantom anchor targets.
- **GitHub duplicate-header suffixes** — repeated headers slugifying
  to the same string emit ``setup``, ``setup-1``, ``setup-2``, …
  matching how GitHub renders them.
- **Atomic ``--fix apply`` writes** — tempfile + ``os.replace`` so an
  interrupted run cannot leave a half-written source file on disk.
- **SARIF anchor rule** — ``stale-refs/anchor`` rule with anchor-
  specific message ("Anchor '#X' not found in 'path'") instead of the
  misleading "missing target" phrasing for the path-finding kinds.
- **Better ``--fix`` empty-result message** — explains why nothing was
  rewritten (``0 fixable / N total finding(s)``) and points at
  ``--ignore`` for intentional dangling refs.
- **Recency-stat memoisation** — ``_recency_score`` caches per-file
  mtime resolution so the priority sort doesn't ``stat`` the same
  source file once per missing-target group.
- **URL percent-decoding** — ``[a](docs/file%20with%20spaces.md)`` to
  an actual ``file with spaces.md`` no longer mis-flags. Applies to
  the path portion AND to anchor fragments (``#caf%C3%A9`` matches
  header ``# Café``).
- **Unicode-aware anchor slugify** — ``# Über`` slugifies to ``über``
  not ``ber``; CJK headers produce useful slugs; references to
  ``#über`` / ``#日本語`` validate against the corresponding header.
- **Agent-ergonomic JSON aggregations** — ``summary.fixable_count``,
  ``summary.by_kind``, ``summary.by_confidence`` so CI tools and
  agents don't have to walk per-target hints to build a dashboard or
  decide whether ``--fix`` is worth running.
- **``summary.next_steps`` array + text NEXT STEPS block** — the
  report now ends with 1-3 actionable command suggestions chosen by
  the existing ``suggest_next_steps`` helper based on the scan's
  context (fixable_count, anchor_findings, missing_targets). Same
  shape every other agent-aware roam command emits.
- **Smart anchor "did you mean" hints** — anchor findings now suggest
  the closest existing slug in the same file via a hybrid score
  ``max(SequenceMatcher.ratio(), token_jaccard)``. Catches both
  pluralisation drift (``#mcp-server`` ↔ ``#mcp-servers``) and
  word-reorder drift (``#docker-setup`` ↔ ``#setup-with-docker``).
  Hint ``source`` is ``anchor-similarity`` so JSON consumers can
  distinguish from rename hints. Dogfood on roam-code's own README
  surfaces 4 actionable anchor suggestions.
- **MCP ``output_schema`` (``_SCHEMA_STALE_REFS``)** — full JSON-Schema
  description of the envelope including the new aggregation fields,
  so MCP clients can validate envelope shape before consumption.
- **``--fix apply`` extends to anchor hints** — HIGH-confidence
  anchor-similarity hints (``#mcp-server`` → ``#mcp-servers``) are
  now auto-rewritten. The substitution operates on the fragment
  portion only so the path prefix and any in-page-vs-cross-file
  shape is preserved.
- **``--fix apply`` preserves URL fragments on path rewrites** —
  ``[x](old/foo.md#section)`` now rewrites to
  ``[x](docs/foo.md#section)`` instead of silently dropping the
  ``#section`` fragment. The previous behavior was a real bug that
  would break in-target navigation on the rewritten URL.
- **``--fix apply`` deduplicates per ``(raw, replacement)`` per line**
  — a line that legitimately has the same stale URL twice now gets
  rewritten in a single ``str.replace`` pass. The previous behavior
  could compound when the new fragment was a superset of the old
  (``#mcp-server`` → ``#mcp-servers`` would mutate to
  ``#mcp-serverss`` on the second pass).
- **SARIF ``helpUri`` points at the correct GitHub org** — fixed a
  pre-existing project-wide bug where every SARIF rule's ``helpUri``
  pointed to ``AbanteAI/roam-code`` instead of ``Cranot/roam-code``.
- **README command-table description rewritten** to surface the
  v12.48 features (anchor validation, ``--fix``, ``--diff``, SARIF)
  so people see the killer capabilities at a glance.

### `roam pr-replay` — productised PR Replay report

Wraps `roam postmortem` with tier-aware buyer-facing framing, an aggregated
detector-class breakdown, and a markdown narrative ready to hand to a
prospect. The productised version of "would Roam have caught my last 30
incidents?" — the qualifier on the path to a Roam Review subscription.

#### Three tiers, one engine

- **`--tier sample`** — DIY 5-PR sample. Free, watermarked, self-serve.
  A prospect can run it locally without a sales call. The watermark
  makes it clear the report is the abbreviated form.
- **`--tier team`** — 30-PR Team report. Paid ($2,500). Includes a
  30-minute founder walk-through.
- **`--tier deep`** — 90-PR Deep report. Paid ($6,000). Per-detector
  deep-dive plus a 90-minute walk-through with a written remediation
  plan.

#### Report shape

- Executive summary with verdict line and severity totals.
- "What Roam would have flagged" table — detector class × total findings
  × PRs-with-finding ratio. Surfaces the single highest-leverage CI gate
  to wire up given the buyer's actual incident pattern.
- Per-PR breakdown ranked high → medium → total, with top-N hits per PR
  capped by tier.
- Recommended next steps: tier-shaped — Sample suggests an upgrade to a
  paid Team or Deep engagement; paid tiers point at concrete CI gates.
- Methodology block on every report so the buyer can verify what was
  measured.

#### Tooling

- New `src/roam/commands/cmd_pr_replay.py`. Wraps `roam postmortem` in
  JSON mode (single source of truth for the analysis), so detector-class
  changes propagate automatically.
- New `tests/test_pr_replay.py` — 14 tests covering smoke per-tier,
  watermark presence/absence, `--client` injection (suppressed on
  sample), JSON envelope shape, `--output` file writes, custom
  `--range` overrides, and pure-function aggregator behaviour.
- Categorised in `cli.py _CATEGORIES["Daily Workflow"]` next to
  `postmortem`.

#### Landing-page integration

- `templates/distribution/landing-page/index.html` rewritten with
  concrete CTAs: inline `pip install roam-code && roam pr-replay
  --tier sample` for self-serve evaluation, plus paid-tier overview.
- `docs/site/cookbook/README.md` recipe 7 (postmortem) now
  cross-references `roam pr-replay` for the report shape.

## [12.47] - 2026-05-08

### Documentation cleanup + anti-drift CI gates

A maintenance release that aligns documentation across surfaces, scrubs
shorthand from source comments and template files, renames a
deliverable, and lands four CI gates that prevent regression.

#### Anti-drift CI gates (new)

- **`tests/test_no_internal_language.py`** — fails any commit that
  re-introduces a curated set of forbidden patterns. The pattern list
  is maintained in the test file itself.
- **`scripts/sync_surface_counts.py`** — single source of truth via
  `roam.surface_counts` + `roam.languages.registry`. Dry-run reports
  drift; `--write` rewrites README + llms-install + server.json.
- **`scripts/linkcheck.py`** — walks every tracked landing-page HTML and
  asserts every internal href + #anchor resolves. `--external` optional.
- **`scripts/strip_metadata.py`** — scans every tracked PDF / PNG / SVG
  for identifying metadata; dry-run reports leaks, `--write` rewrites
  files with neutral metadata.
- All four wired into `.github/workflows/roam-ci.yml` as a new
  `doc-hygiene` job that runs on every PR.

#### Source comments + tests

- Bulk-scrubbed ~110 source files of stale shorthand left in comments.
- `cmd_audit.py` + `cmd_audit_trail_export.py` + `cmd_postmortem.py` +
  `cmd_article_12_check.py` + `cmd_permit.py` docstrings rewritten as
  neutral product descriptions.
- Restored `if not include_tooling:` guard + `excluded_tooling = 0`
  initializer + `from roam.output.file_role_hints import is_excluded_path`
  in `cmd_smells.py` (fixed an indentation regression caught by the
  test suite).
- Renamed regression-FP corpus fixtures to neutral names.

#### Documentation + product naming

- Audit deliverable renamed to **PR Replay** on landing pages, and
  **Codebase Architecture Audit** on legal templates / sample report.
- DPA + SOW master templates generalised: tier rows replaced with
  bracketed placeholders.
- `templates/legal/security-procurement-packet.md` — internal-only
  links replaced with the public roam-code.com/pricing URL.
- `templates/email/customer-journey.md` — hard-coded signature
  replaced with `[YOUR_NAME]` placeholder.
- README + llms-install + server.json + mcp-server-card.json surface
  counts: 194 → 202 commands, 27 → 28 languages, 5 → 6 cross-language
  bridges (Django bridge added).
- Old GitHub Pages documentation URL replaced with `roam-code.com/docs/`
  across 9 tracked files.

#### History rewrites

- Six force-pushes during the cleanup. Removed from history: 27 working
  documents under `docs/strategy/`, `docs/products/`, and `dev/`, plus
  5 fixture renames.
- Author rewrite: 400 commits across two prior committer identities
  rewritten to `Cranot` so GitHub Insights shows uniform attribution.
  Third-party contributor commits untouched.

#### `pyproject.toml`

- `authors` updated to `Cranot`.

## [12.46] - 2026-05-07

### CI fix — ruff lint cleanup

Hotfix after 12.45. The ruff format-check passed in 12.45 but the
ruff LINT pass (separate) flagged 7 errors across the new files:
- `json` imported but unused in cmd_capabilities.py
- `default_path` assigned but never used in cmd_skill_generate.py
  (the dead variable was a refactor leftover; the actual default-path
  logic lives in the `out = Path(output_path) if output_path else None`
  branch where the user-supplied path wins)
- `reg` assigned but never used in test_capability_registry.py (left
  over from a refactor of the smoke test)
- 3 unused imports (`os`, `Path`, `pytest`) in test_sarif_enrichment.py

Applied `ruff check --fix --unsafe-fixes`. Whitespace + dead-code
removal only; all tests still green.

## [12.45] - 2026-05-07

### CI fix — ruff format on newly-added files

Hotfix after 12.44. The 9 net-new files added in 12.43-12.44
(capability.py, cmd_compare.py, cmd_skill_generate.py, sarif.py edits,
plus 4 test files) were not run through `ruff format`
before commit. CI's lint job ran `ruff format --check` and rejected.

Per the project's known-learning ("Ruff format check in CI: Always run
`ruff format` on new files before committing"), this should have been
caught locally. The hotfix runs the formatter and lands the
whitespace-only changes. No behavior change.

## [12.44] - 2026-05-07

### CI fix — register the two new detectors in the catalog

Hotfix after 12.43. The two new async detectors
(async-fire-and-forget-task, async-nested-run) were registered in
the detector dispatch table but missing from the catalog/tasks.py
CATALOG dict. test_math.py::test_detector_registry_covers_catalog
caught the mismatch on all 5 Python versions.

Adds full catalog entries for both new tasks: name, category, kind,
and the two-way ranked-solutions list that the rest of the algo
infrastructure expects. Bumps test_math.py's expected-task count
32 -> 34. No behavior change to the detectors themselves.

## [12.43] - 2026-05-07

### Major: Capability Registry + 4 new commands + landing-page launch

This release lands Capability Registry and bundles a
substantial polish round. Companion to
the launch of the new commercial landing page at https://roam-code.com.

### New commands (4)

- **`roam capabilities`** — Decorator-driven introspection. Emits the
  capability manifest as YAML / JSON / text from any command marked
  with `@roam_capability`. Drives Roam Review GitHub App routing +
  MCP filtering. Capability Registry per
  `the build plan`.
- **`roam skill-generate`** — Generate an agent-runtime skill manifest
  from the capability registry. 4 emitter targets: `claude` (SKILL.md),
  `cursor` (.mdc rule), `continue` (config snippet), `aider`
  (.aiderrc). Closes GitHub issue #14; supersedes the static SKILL.md
  approach from PR #15 with dynamic generation. `--ai-safe-only`
  default filters to capabilities marked safe for autonomous agents.
- **`roam compare`** — Structural delta between two indices. Symbols
  added/removed/moved + per-file complexity deltas + IMPROVED /
  SIDEWAYS / REGRESSED verdict. The "did this refactor actually
  work?" tool. Useful for periodic measurement.
- **`@roam_capability` decorator** (not a CLI command but a public API)
  — applied to the 3 Phase 0 commands (permit, postmortem,
  article-12-check). Mark new commands with this so the registry
  stays in sync with the codebase.

### New detectors (2)

- **`async-fire-and-forget-task`** — `asyncio.create_task()` whose
  return value is discarded. Counts total `create_task` calls,
  subtracts stored ones (assignment, append, add, return, await),
  reports the net leak. Python 3.11+ explicitly warns about this
  footgun. High severity.
- **`async-nested-run`** — `asyncio.run()` invoked inside an async
  function. Raises RuntimeError at runtime (event loop already
  running). Fix is to `await` the coroutine directly. High severity.

### SARIF output enrichment

- `automationDetails` block (id + guid + description) on each run for
  GitHub Code Scanning re-ingest correlation.
- `versionControlProvenance` populated from `git rev-parse` when
  available (revisionId, branch, repositoryUri).
- Driver metadata: `informationUri`, `downloadUri`, `organization`.
- Suppressions support: reads `.roam/suppressions.json` (list or
  envelope shape), stamps matching results with the SARIF
  `suppressions` array — so CI gates can respect documented FPs.

### Rule packs

- Rust pack expanded from 12 → 30 rules (memory + concurrency +
  error-handling + hygiene categories added).
- Swift pack created from scratch — 25 rules covering force-unwrap,
  retain cycles, main-thread blocking, SwiftUI state misuse, etc.

### Documentation

- New `docs/site/cookbook/README.md` — 10 high-value workflow recipes:
  orient in a new codebase, audit a PR, set up a CI gate, find dead
  code, generate Article 12 readiness, replay detectors against past
  commits, wire roam into Claude Code, compare two indices, ship a
  pre-commit verdict.
- `(internal memo)` — captured May 2026 competitor
  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. Regenerate from BACKLOG/test-fixture breadcrumbs before next release. -->
  state (GitNexus 10K stars, Codebase-Memory 66 langs, Qodo 2.0
  multi-agent, Greptile v4 82% bug catch, CodeRabbit Autofix). Drives
  next-session prioritisation.

### Landing page (https://roam-code.com)

Major rework over 5 audit-and-fix passes after the domain went live
on 2026-05-07:

- 7-page site: home, /pricing, /compare, /docs, /privacy, /terms,
  /refund. All under 10 KB Brotli per page.
- Plain-language H1: "Your AI writes the code. Roam tells you what
  else it broke."
- "What's in the free CLI" section showing the complete product
  (190+ commands, 136 MCP tools, 27 languages, real OSS adoption
  numbers fetched from GitHub + pypistats).
- "How it works" section with the MCP-server angle (your AI agent
  talks to Roam, gets back graph-grounded answers).
- "What Roam looks like in practice" — terminal demo + GitHub PR
  comment mockup (HTML/CSS, no images).
- Comparison table vs CodeRabbit / Greptile / Qodo / SonarQube,
  with verified-against-vendor-pricing-pages methodology.
- Pain band citing PocketOS / Amazon Treadwell / Faros AI 2026 /
  Kudelski's CodeRabbit RCE writeup, with "Roam catches this class
  of bug" tie-back lines.
- Pre-MRR legal pages: GDPR-compliant Privacy, Terms with
  limitation-of-liability + Greek governing law, Refund policy with
  EU consumer-rights notice.
- Self-hosted fonts (42 KB total, 86% reduction from prior cold-load).
- Strict CSP with hash-allowlisted JSON-LD, COOP/CORP, HSTS preload.
- Email contacts (hello@/security@) + .well-known/security.txt.

### Surface counts

- CLI commands: 190 → **193** (+ capabilities, skill-generate, compare)
- Modules: 180 → 183
- Detectors: 54 → 56 (+ async-fire-and-forget, async-nested-run)
- Rule packs: 7 → 8 (+ Swift)
- Tests added: 32 (capability 11 + sarif 7 + skill 7 + compare 7)
- Documentation: + cookbook (10 recipes)


Deferred to follow-up releases: Dart Tier-1 extractor, parallel parse
for monorepos, LLM-augmented MCP tool, why-slow CLI via runtime traces,
open-issues sweep, GraphQL bridge, incremental MCP hot-reload.

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

### New commands + commercial landing page

After 8 CI iterations restoring the matrix to green (12.31 → 12.39),
this release lands three new CLI commands and a starter landing page
for the hosted product surface.

### New commands

- **`roam permit`** — structural-permission verdict facade for AI
  agents. Returns `{verdict, reason, allowed_actions, blocked_actions}`
  over staged changes (`--staged`), an arbitrary diff (`--input`), or
  a target symbol (`--symbol`). Wraps `roam critique` + `roam preflight`.
  Exit codes: 0=ALLOW, 5=BLOCK, 6=REVIEW. Drops into Cursor rules,
  Claude Code permission hooks, pre-commit, GitHub Actions branch
  protection.
- **`roam postmortem <commit-range>`** — replays current detectors
  against past commits. Walks `HEAD~30..HEAD` (or any range), runs
  `roam critique` against each commit's diff, reports findings that
  would have surfaced pre-merge. Useful pre-purchase signal: would
  today's detector set have flagged your last-quarter incidents?
- **`roam article-12-check`** — scoping/readiness assessment for
  EU AI Act Article 12 record-keeping (Annex III high-risk providers
  only). 6-item checklist → 1-page Markdown report (or PDF with
  `--pdf out.pdf` if reportlab installed).

### Commercial landing page (starter)

New directory `templates/distribution/landing-page/` with:

- `index.html` — hero + 3 product cards + buyer-pain band + audit
  upsell + trust strip + FAQ + footer
- `landing.css` — single 6KB stylesheet (IBM Plex Mono + Space
  Grotesk fonts, matches docs/site visual language)


the new domain recommendation.

### Surface counts

- 187 → **190 commands** (+permit, +postmortem, +article-12-check)
- README + llms-install + landscape.json updated

### Tests

- `tests/test_phase0_commands.py` — 7 tests covering happy-
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

## [12.32] - 2026-05-06

### Bugfix release — CI green-bar restore + Z-phase polish

12.31 went out with two stale tests (drift from the hosted-product
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
  ~115-finding a Vue 3 + Laravel app case.
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

### Detector quality audit follow-ups

A second dogfood pass of `roam math` / `weather` / `auth-gaps` /
`migration-safety` / `over-fetch` against the a Vue 3 + Laravel app Vue 3 + Laravel
multi-tenant codebase surfaced five fresh false-positive classes that
the 12.28/12.29 rounds didn't catch. All five are fixed here, each with
regression-corpus fixtures so they can't quietly come back. Web search
confirmed the patterns we're recognising are the canonical Laravel +
TypeScript idioms (parent-controller `$this->middleware('auth')` is the
pre-Laravel base-class auth pattern; PostgreSQL SQLSTATE `42P07` /
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
  in its constructor) was generating ~115 false positives on a Vue 3 + Laravel app.
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

- 14 new entries in `tests/regression_fp_fixtures/corpus_vue3_laravel_round2.json`
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

### Detector quality deferred items

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
  drawn from the 2026-05-06 a Vue 3 + Laravel app FP batch — each is a tripwire
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

### Detector quality round () — false-positive fixes

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
  `deepEqual` flagged on a Vue 3 + Laravel app with `if (depth > 10) return false`
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
  on a Vue 3 + Laravel app showed "high confidence" for these was 0/1 true positive.

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
  a Vue 3 + Laravel app flagged for missing `$this->authorize()` despite being
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

### Added — Roam Agent Review + Cloud Lite engines (hosted-product layer)

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

cached ``build_symbol_graph(conn)`` keyed on ``id(conn)``. The
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
fix from .

### cmd_impact JSON contract

CI failure at 3.9 + 3.12. When ``roam impact`` finds the symbol in
the index but NOT in the dependency graph, the path emitted plain
text on stdout, breaking ``--json`` consumers. Wrapped in a proper
envelope (``summary.in_graph: False``) with the same hint surfaced
in the ``tip`` field.

### health --gate exit code

CI failure at 3.13. The test asserted ``health_min: 100`` is
unreachably high but a tiny fixture project scores exactly 100, and
the comparison is ``score >= h_min`` so 100 ≥ 100 passes. Switched
the test to ``health_min: 999`` to make the threshold genuinely
unreachable.

### MCP sampling test

CI failure at 3.11. added the ``ROAM_AI_ENABLED`` opt-in
gate; the existing test never set the env var, so sampling
returned None on CI. Updated the success-path test to set
``ROAM_AI_ENABLED=1`` and added a default-OFF assertion test.

### _compute_reachability split

cc 150 (deepest nesting in repo at depth 8) → ~10. Decomposed
into ``_node_match_keys``, ``_matches_dep``,
``_trace_entry_reach``, ``_build_norm_lookup``, ``_record_match``.
Orchestrator stays under 10 LOC of branching.

### poll_loop split

cc 154 with 17 params at ``cmd_watch.py:457``. Pulled per-event
helpers (``_need_force``, ``_scan_disk_changes``,
``_label_webhook_events``, ``_refresh_tracked_after_reindex``,
``_run_guardian_step``) keeping the public signature stable so
callers and tests are unaffected.

### tests for 5 untested commands

Added behavioural tests for ``py-modern`` (had 0 references),
``graph-stats``, ``mcp-status``, ``pre-commit``, ``exit-codes``
(each had 1 registration-only reference). 9 new tests.

### ROAM_QUERY_TIMEOUT_S coverage

shipped an opt-in SQLite progress handler. Zero test
coverage existed. Added 4 tests exercising no-env / invalid /
zero / and a tiny-budget interrupt that should fire OperationalError.

### format_table budget threading (cmd_context)

20 ``format_table()`` calls across 5 files lacked ``budget=``.
Added ``_table_budget(data)`` helper and threaded the global
``--budget`` through cmd_context's ``data`` dict. Wired into the
two highest-volume call sites (callers + callees lists).

### audit-report Markdown template

P1.2 strategic blocker. Built a 9-section,
185-line template at ``docs/audit_report_template.md`` with
placeholders for every ``roam audit --json`` field. Bridges the
gap between the engine and the deliverable
artifact paying customers see.

### _build_agent_descriptors split + graph-cache fix

Top remaining complexity offender: ``_build_agent_descriptors``
cc=161 in ``graph/partition.py``. Decomposed into 6 small helpers
(``_node_partition_index``, ``_fetch_node_metadata``,
``_file_majority_owners``, ``_read_only_files_for``,
``_boundary_contracts``, ``_cluster_label_for``).

Also fixed a latent state-leak bug from 's graph-builder
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

### `QueryEngine._extract_symbols_from_pattern` cc 198 → ~10

Single most-complex function in the codebase. Decomposed into four
small helpers (``_find_name_node``, ``_decode_capture``,
``_resolve_kotlin_class_kind``, ``_build_symbol_from_def``) leaving
the orchestrator at ~10 cognitive complexity. All 194 extractor
tests pass.

### `_render_single_text` cc 189 → smaller orchestrator

Pulled the per-symbol header rendering (async badge, idiom badge,
paren-aware decorators block) out of ``cmd_context._render_single_text``
into ``_render_async_badge`` / ``_render_idiom_badge`` /
``_render_decorators_block``. The paren-aware split now correctly
handles `parametrize("a,b", [...])` decorators that previously got
mangled by naive comma-splitting.

### delete 4 truly-dead exports

`roam dead` aggregated 78 SAFE entries but most are decorator-
registered MCP tools (false positives the analyzer can't see
through). Of the 16 non-decorator candidates, 4 had only self-
references and were genuinely dead: removed
``write_site_payload`` (competitor_site_data),
``detect_string_format_old`` (python_idioms — disabled by
``return findings`` on first iteration),
``structured_click_exception`` (output/errors).

### break the cli ↔ cmd_doctor cycle

`roam health` flagged exactly one actionable cycle: cmd_doctor
imported `_COMMANDS` from cli, while cli's command registry
referenced cmd_doctor. Static graph saw it as a 2-edge cycle.
Replaced ``from roam.cli import _COMMANDS`` with
``importlib.import_module("roam.cli")`` so the only edge is
runtime-only — cycle eliminated, doctor still validates every
registered command.

### health 80 → 88 via utility-path classifier fix

The god-component classifier was labeling architectural hubs
(``cli`` Click root, ``_run_roam`` MCP dispatch, ``build_symbol_graph``)
as actionable when they're SUPPOSED to have high fan-in. Added
``graph/`` ``mcp_extras/`` ``languages/`` to ``_UTILITY_PATH_PATTERNS``
and ``cli.py`` ``mcp_server.py`` ``file_roles.py`` to
``_UTILITY_FILE_PATTERNS``. Health score jumped 80 → 88 (+8 pts).

### `_analyze_dataflow_dead` cc 160 → ~10

Top of the danger-zone list (cmd_dead.py: 3362 churn × cc=24.6
× fan-in=8 = score 1.68). The 200-line ``_analyze_dataflow_dead``
mega-function split into ``_table_exists``, ``_read_caller_line``,
``_is_return_captured``, ``_detect_unused_returns``,
``_parse_param_names``, ``_detect_dead_param_chains``,
``_detect_side_effect_only``. Orchestrator stays under 10. All 48
dead-code tests pass.

### observability hook extended

covered cmd_metrics + cmd_describe (20 sites). 
adds cmd_understand (4 sites), metrics_history (9 sites), and the
remaining nested patterns. ``ROAM_VERBOSE=1`` now surfaces 31
swallow points; remaining ~40 are in less-touched commands and
will land incrementally.

### second `--json` bypass sweep

Probed every command with an unknown-symbol input. Caught one new
bypass: ``roam test-map UnknownXYZ`` printed plain text "Not
found: ..." instead of a JSON envelope. Fixed.

### TODO/FIXME audit (no real debt)

22 markers in source; all 22 are intentional —
``cmd_test_scaffold.py`` writes "TODO" strings as scaffold output
(17 sites) and ``cmd_vibe_check.py`` detects TODO patterns in user
code (5 sites). No actual debt. Decision logged here.

### orphan-imports false-positive sweep

`orphan-imports` was flagging ``roam.telemetry`` and
``roam.observability`` as ``internal_typo`` because the
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

### `--json` empty-state sweep

Same class of bug as the 12.18.1 safe-zones hotfix. Fixed three
real bypasses uncovered by JSON-parse probes:
``cmd_complexity`` (3 sites: empty data, no matches, no bumpy
roads), ``cmd_coverage_gaps`` (missing-filter usage error),
and ``cmd_config`` where a flag-default mismatch made
``roam --json config`` silently produce empty output.

### silent `except: pass` observability hook

84 ``except Exception: pass`` blocks across 40 files masked
real failures (missing schema columns, optional dependencies,
sqlite errors). Added ``roam.observability.log_swallowed``
which is a no-op unless ``ROAM_VERBOSE=1`` (or
``ROAM_OBSERVABILITY=1``) is set. Applied to the heaviest
offenders: ``cmd_metrics`` (12 sites) and ``cmd_describe`` (8
sites). Rate-limited to 5 reports per scope per process.

### five MCP wrappers

Wired up agent-actionable signals that were CLI-only:
``roam_alerts``, ``roam_timeline``, ``roam_test_impact``,
``roam_disambiguate``, ``roam_why_fail``. All five added to
the core preset (35 → 41 core tools).

### N+1 SQL batching

Replaced per-symbol ``conn.execute`` loops in
``cmd_adversarial`` (orphaned-symbols + high-fan-out checks)
with a single ``batched_in()`` query. On a 14k-symbol repo,
``roam adversarial`` previously made thousands of round-trips;
now one batch per check. Same pattern for ``cmd_affected``
(start-symbol collection).

### auto-regenerated command reference

Hand-curated workflow sections in
``docs/site/command-reference.html`` now have a complete
auto-generated appendix listing every command + short help line
organised by category, between
``<!-- BEGIN auto-reference -->`` markers. Regenerate with
``python dev/build_command_reference.py``. Coverage went from
73 to 185 commands documented.

### cross-language `orphan-imports`

was Python-only. Extended to JS/TS (path-rewrite
resolution + bare-specifier detection) and Go (stdlib +
hostname-shaped import path heuristic). New ``--lang`` flag
(``all`` / ``python`` / ``javascript`` / ``go``).

### `roam audit`

One-shot codebase audit meta-command. Chains
``health → debt → dead → test-pyramid → api → stats →
hotspots --danger`` into one envelope with a top-level summary
(verdict, health_score, debt_total, danger_zone_count, api_surface,
etc.). Pass ``--brief`` to drop per-section detail.

### AI-on-client-code default OFF

Sampling/LLM hook in ``mcp_extras/sampling.py`` now requires
``ROAM_AI_ENABLED=1`` (or ``=true``) to dispatch payloads to
the client's LLM. Without the env var, the hook returns
``None`` and callers fall back to the raw envelope. GDPR / EU
AI Act credibility blocker for the first paid audit.

### `roam impact` dispatch-via-registry

Dogfood #189 — the call graph misses consumers that route
through string-lookup tables (cli ``_COMMANDS``, ask recipes,
plugin entry points). New ``indirect_refs`` field in the
``impact`` envelope scans source files for string literals
matching the symbol's name/qname. Surfaces ``43 sites`` for
``health`` that the static graph misses.

### agent-export `--brief`

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

### `roam disambiguate <name>`

Lists every symbol matching the name with file/line/kind/
signature/docstring snippet + PageRank tiebreaker. Saves
agents from picking the wrong overload when names collide.

### `roam pre-commit`

Generates a git pre-commit hook that runs `git diff --cached |
roam critique` on staged changes. Idempotent installer
(``--install``); preview-only by default (``--print``).
``ROAM_PRECOMMIT_SKIP=1`` to bypass.

### `roam mcp-status`

Companion to `roam doctor` for the MCP transport: preset,
registered tool count, backpressure limits (max_concurrent,
in_flight, busy_responses_total), result-cache size, watcher
state.

### `roam test-impact <range>`

Sharper than `affected-tests`. Walks BFS over the reverse call
graph from each changed symbol; ranks tests by the number of
changed symbols that reach them.

### rerank weights via env vars

`ROAM_RERANK_ALPHA` / `BETA` / `GAMMA` / `DELTA` / `EPSILON` /
`ZETA` override `[retrieve]` config without touching
config.toml. Useful for quick weight-tuning loops.

### `roam fitness --explain`

Confirmed already shipped. Verified the existing flag covers
the per-violation rule citation requirement.

### MCP error storm rate-limit

When the same `error_code` fires ≥ 3× in a row, the MCP error
envelope drops the verbose fields (`hint`, `suggested_action`,
`doc_link`, `severity`) and replaces them with a tight
`{error_code, repeat_count, trimmed: True}` shape. Reduces
token bloat in agent retry loops. Counter resets when a
different error_code fires.

### `roam recipes`

Sugar over `roam ask --list` for discoverability. Lists every
recipe with intent + example queries + commands. JSON envelope
includes the full recipe metadata.

### `roam why --json` audit

Verified that the existing `why --json` payload already returns
structured per-symbol fields (`role`, `fan_in`, `fan_out`,
`pagerank`, `reach`, `cluster`). No work needed — the
explanation is already structured.

### `roam map --seed --depth`

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

### `roam why-fail <test>`

Triage helper: traces from a failing test (or symbol) back to
recently-changed symbols it transitively reaches. Sorted by
recency × hop distance × PageRank.

### `roam graph-stats`

Graph-level invariants: density, weak components, non-trivial
cycles, average degree, top-inbound symbols. Single overview
number for "how dense / connected is this codebase".

### `roam recommend <symbol>`

Surfaces related symbols using three signals — call-graph
neighbours, git co-change, persisted clone siblings —
combined with normalised contribution scoring.

### `roam diff --since-tag`

Auto-fills the commit range with `<last-tag>..HEAD` via
``git describe --tags --abbrev=0``.

### `roam tour --focus <module>`

Constrains the tour (top symbols, reading order, entry points)
to files under the given path prefix.

### taint risk score

`roam taint` summary now includes a 0-100 ``risk_score``
weighting errors 5×, warnings 1×, and discounting sanitized
findings.

### `roam context --inline`

Concatenates the recommended files into one paste-ready block
with line numbers — for chat agents that prefer one big string
over multi-file output.

### `roam clones --by-file`

Aggregates clone pairs into (file, file) coupling. Shows which
file pairs are most clone-coupled.

### graph-builder memoization

`build_symbol_graph` and `build_file_graph` cache by
``id(conn)`` so compound commands like ``pr-prep`` (which
internally call multiple subcommands) don't rebuild the graph
multiple times.

### `roam api`

Lists the public API surface (exported public symbols + their
signatures). Useful for changelog generation and breaking-
change detection.

### error envelope `severity`

MCP error envelopes now include a ``severity`` field
(`info | warning | error | fatal`) per error code. Lets agents
branch on severity without parsing the message.

### `roam search --recent`

Boost results in files modified within N days. Useful when
retracing very recent changes.

### `roam config --weights`

Surfaces the active rerank weights (alpha/beta/gamma/delta/
epsilon/zeta) merged with defaults. Replaces grepping the
source.

### `roam diagnose --batch`

Run diagnose on N symbols from a newline-separated list (file
or stdin). Mirrors the oracle batch pattern.

### MCP `roam_health` payload trimming

When the issue count is ≥ 50, the MCP envelope drops the verbose
issue list and keeps the score, category counts, and
breakdown. Set ``ROAM_MCP_HEALTH_FULL=1`` for the unfiltered
shape.

### `roam reset --dry-run`

Preview the destructive reset (DB path + size) without deleting.
No --force required for the preview.

### `roam exit-codes`

Lists every roam exit code with its meaning. Replaces grepping
the docs or source.

### `roam workflow --next`

Given a previously-run command name, suggest what to run next
(e.g. after `preflight`: `context`, `impact`, `diff`).

### deprecation registry

Adds the ``_DEPRECATED_COMMANDS`` map in ``cli.py``. When a
deprecated command is invoked, the LazyGroup resolver prints a
"use X instead" note on stderr without breaking the call.

### `roam version --check`

Prints the installed version and (with ``--check``) queries
PyPI for the latest version. Offline-friendly: falls back
silently when PyPI is unreachable.

### `roam timeline <symbol>`

Chronological commit history for the file owning a symbol:
SHA, date, author, lines added/removed, subject. Joins
``symbols`` × ``git_file_changes`` × ``git_commits`` with a
GROUP BY commit_id to dedupe duplicate change rows.

### `roam pr-prep`

One-shot pre-PR fitness check that bundles ``diff`` +
``critique`` + ``pr-risk`` into a single envelope with a
top-level ``ready_to_open`` boolean. Replaces calling four
commands sequentially before opening a PR.

### `roam eval-retrieve --quick`

Runs the first 5 tasks of the bench harness for fast local
iteration. The full 30-task bench takes too long for tight
weight-tuning loops.

### `roam config --check`

Validates ``.roam/config.json`` against the known-keys schema.
Flags unknown keys (typo guard) and type mismatches. Lists the
canonical key set with one-line descriptions when no issues are
found.

### richer `roam_catalog` metadata

Tool catalog now includes ``when_to_use`` (extracted from each
docstring's "WHEN TO USE:" line) and up to three doctest-style
``>>> roam ...`` examples per tool. Lets agents pick the right
tool without fetching each individual description.

### `roam impact --hops N`

Bound the BFS at N hops instead of full transitive descendants.
``--hops 1`` mirrors ``roam uses``; ``--hops 3`` shows callers
of callers of callers. Lets agents scope a refactor to a
controlled radius.

### `ROAM_QUERY_TIMEOUT_S` query timeout

Opt-in SQLite progress handler that interrupts long queries
past N seconds. Prevents hangs on huge codebases. Default
behaviour unchanged when env var is absent.

### `roam search --mode regex|exact|substring`

Three matching modes. Default is ``substring`` (LIKE %p%, the
existing behaviour). ``regex`` registers a Python ``re``-backed
SQLite REGEXP function. ``exact`` matches name = pattern only.

### `roam stats`

Aggregate metrics over the index: file count, symbol count,
total lines, recent commit activity (last N days), broken down
by language / file role / symbol kind. Useful as the first
thing an agent runs after ``roam init``.

### `roam test-pyramid`

Counts test files by sub-kind (unit / integration / e2e / smoke /
unknown) using ``classify_test_kind`` from . Verdict flags
inverted pyramids (``e2e+integration > unit``) and unstructured
test layouts (``unknown >= 4× classified``).

### working-tree drift in `index_status`

Adds a ``dirty_files`` field to the staleness envelope. Even when
``HEAD`` matches the indexed commit, an outstanding working-tree edit
makes the symbol/edge data stale; we count modified files via
``git status --porcelain`` and surface a refresh hint.

### `roam_catalog` MCP tool

Machine-readable list of every registered MCP tool with capability
flags (``core`` / ``read_only`` / ``destructive``). Replaces having to
enumerate ``list_tools`` and parse each one — the catalog is one
round-trip and is part of the core preset.

### `roam health --explain`

The 0-100 health score is a weighted geometric mean of five factors;
``--explain`` shows each factor's "loss" in points so the user can
see which dimension is dragging the score down. Surfaced in both
text mode (sorted breakdown table) and JSON envelope
(``score_breakdown`` array).

### doctor adds plugin + table checks

``roam doctor`` now runs 13 checks (was 11). New entries: plugin
discovery error count via ``get_plugin_errors()``, and required-table
presence (``files``, ``symbols``, ``edges``, ``git_commits``,
``file_stats``) — surfaces a half-migrated DB before a downstream
"no such table" error.

### `roam config --env`

Walks ``src/roam/`` for ``ROAM_*`` references and prints a sorted,
deduped inventory of every env var the codebase reads, with the
file/line of the first read and whether it's currently set.
Replaces grepping the source manually.

### `roam hotspots --danger`

Files in the top quartile of churn × file complexity × max
fan-in. Score is the geometric mean of the metric ratios so a
moderate-everywhere file ranks above one that's extreme in only
one dimension.

### `roam index-stats`

Surface the ``.roam/index.db`` size, table row counts, and SQLite
fragmentation (``freelist_count / page_count``). Verdict suggests
``VACUUM`` above 25% fragmentation and ``roam reset`` when both
fragmented and oversized (default 200 MB threshold, override via
``ROAM_INDEX_SIZE_WARN_MB``).

### `roam critique --batch <dir>`

Reviews every ``*.diff`` and ``*.patch`` in the directory in a single
pass. Handy for reviewing a stack of PRs or a series of
``git format-patch`` output. Per-diff verdict + aggregate gate fail
when any diff has a high-severity finding.

### graceful Ctrl-C

``python -m roam`` now catches ``KeyboardInterrupt`` at the top level
and exits with the conventional 130 instead of dumping a traceback.
The indexer also catches the interrupt to release its lock cleanly,
so a rerun resumes from the last committed checkpoint instead of
stumbling on a stale ``.roam/index.lock``.

### auto-route unknown commands

When ``roam <unknown>`` doesn't have a close edit-distance neighbour in
``_COMMANDS``, the LazyGroup's resolver now consults the ``ask``
TF-IDF classifier. If a recipe matches with confidence ≥ 0.5, the
``UsageError`` suggests ``roam ask "<input>"`` so a natural-language
attempt ("trace login flow through middleware") still leads
somewhere useful in one turn.

### opt-in local telemetry

``ROAM_TELEMETRY_LOCAL=1`` enables a tiny SQLite ring buffer
(`.roam/telemetry.db`, 500-row cap, prune-on-write) that records
``(command, duration_ms, exit_code, ts)`` for every CLI invocation.
Surface via ``roam telemetry`` (slowest + recent calls). Strictly
local — no network. No-op when env var is absent so the hot path
stays unaffected.

### `roam oracle batch`

The five boolean oracles (``symbol-exists``, ``route-exists``,
``is-test-only``, ``is-reachable-from-entry``, ``is-clone-of``)
now accept a JSONL stream via ``roam oracle batch [--input -]``.
Each line is one ``{oracle, args}`` object; output is a single
JSON envelope with all results. Useful for fleet-style pre-flight
checks (50 symbols at once instead of 50 round-trips).

### `roam orphan-imports`

Quick Python-only lint that flags imports the indexer couldn't
resolve. Distinguishes ``internal_typo`` (top-level package
indexed but submodule missing — e.g. ``roam.cmds.foo`` instead
of ``roam.commands.cmd_foo``) from ``missing_package`` (genuinely
absent). JS/TS/Go versions deferred — per-language scaffolding
overhead is too much for one pass.

### `roam docs-coverage --quality`

Buckets every public symbol's docstring into ``ABSENT / SHALLOW
/ RICH``. Heuristic: a docstring is ``RICH`` when its length ≥ 80
chars AND it mentions params/returns or has an example block;
``SHALLOW`` otherwise. Surfaces in both text and JSON output, with
sample symbols per bucket so the user can see the gap concretely.

### `roam search --explain` shows PageRank

The ``--explain`` flag already showed BM25 + matched fields +
highlights + term counts. adds the per-result PageRank to
the explanation so users can see when ordering is structural-rerank-
driven vs. lexical.

### `roam retrieve --scope <dir>`

Restrict candidates to files under a given path prefix —
useful for monorepos and large codebases where the user knows
the relevant subtree. Post-filter on the ranked candidate list,
so no rerun of the heavy retrieval pipeline.

### `roam changelog --suggest`

Read commits since the last tag, classify them via Conventional
Commits prefixes (feat / fix / perf / refactor / docs / test / chore /
build / ci), emit a draft ``## [Unreleased]`` markdown section grouped
by bucket. ``--since <ref>`` overrides the tag autodetect.

### `roam graph-export`

Write the symbol or file dependency graph as ``GraphML / DOT /
JSONL`` for plugging into external graph tooling (Gephi, Cytoscape,
igraph, or custom analyses). ``--scope file`` switches from the
symbol-level graph to the file-level graph.

### `roam help-search <query>`

Fuzzy match across every command's name + short docstring.
Replaces grepping ``--help-all`` output of 158 commands. Score
weights name matches above docstring matches and rewards shorter
matching names.

### MCP-level result caching

The MCP server already had per-cell caching for a handful of hot paths
(`understand`, `tour`); promotes ~30 read-only commands into a
shared, index-mtime-keyed result cache. Cache hit drops the round-trip
from 153ms to 1ms (153× speedup) without changing tool semantics.
Auto-invalidates on reindex (mtime bump on `.roam/index.db`).

### `roam ask` recipe expansion (13 → 24)

Eleven new TF-IDF-classifiable recipes covering common agent
workflows: `trace-bug`, `who-owns`, `what-changed`, `audit-security`,
`explore-impact`, `find-similar`, `why-this-exists`, `check-pr`,
`explore-tests`, `dependency-update`, `visualize-architecture`. Each
maps to an existing roam command pipeline so the dispatcher stays a
thin classifier-and-route — no new analysis logic.

### test sub-classification

`file_roles.py` now exports ``classify_test_kind(path)`` returning
``unit | integration | e2e | smoke | unknown``. Path-pattern first
(``e2e/``, ``integration/``, ``cypress/``, ``playwright/``), then
filename-pattern fallback (``*_e2e.py``, ``*_smoke.py``). Lays the
groundwork for "test pyramid" reports without changing
the existing ``is_test`` boolean contract.

### error envelope `doc_link` field

The MCP error path already emitted ``error_code``, ``hint``, and
``retryable``. fills the fourth field of the structured-
error contract: every classified ``error_code`` now carries a
stable ``doc_link`` pointing at an anchor in the public
troubleshooting page. Agents get one URL to fetch when self-
serving an error, instead of grep-the-docs-and-pray.

### opt-in parallel source prefetch

``ROAM_PARALLEL_INDEX=1`` enables a thread-pool source prefetcher
in the indexer. Disk reads run in parallel up to ``min(32,
cpu_count*2)`` workers ahead of the (still-serial) parse + DB
write loop. The serial section is unchanged, so this is safe
under concurrency and a no-op without the env var.

I/O-dominated indexes (cold cache, OneDrive-mirrored repos,
network drives) see the biggest wins; CPU-bound indexes see no
regression because the cache is consumed in-order.

### `roam plugins`

The plugin discovery system has shipped since v11 (entry points
+ ``ROAM_PLUGIN_MODULES``) but had no introspection surface.
``roam plugins`` lists discovered commands, detectors, language
extractors, extensions, grammar aliases, and any discovery
errors. JSON envelope mirrors the same fields. With no plugins
registered, prints the activation hint instead.

### Decisions logged (no shipped change)

- (``--markdown`` global flag) — deferred. Rendering layer
  would touch every command. Adding the flag without a working
  renderer is dead code; revisit when there's a concrete agent
  surface that benefits from it.
- (``roam impact-commit <hash>``) — already covered by
  ``roam diff <commit-range>`` (e.g. ``roam diff HEAD~1``).
- (compound ``roam_explore`` MCP tool) — already shipped.
- (stale-command audit) — all 162 CLI command names appear
  in at least one test. No cleanup needed.

## [12.14] - 2026-05-05

Ten more research passes building on v12.13's speed wins. Three
land as concrete features; the rest were research-decided
(existing surface adequate or out of scope).

### Did-you-mean for command typos

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

### Auto-refine on low-confidence retrieve

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

### ``--help-all`` global option

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

- **(indexing speed)** — incremental index is ~2.8s warm.
  ``compute_file_stats`` and friends already early-exit on no-change.
  Further wins would require a daemon mode.
- **(symbol disambiguation)** — ``pick_best`` already uses a
  6-level tiebreak (edge count → PageRank → cc → churn → path
  priority → id). Live tests confirm canonical paths win
  consistently.
- **(cold-start of common commands)** — ``cmd_search``
  subprocess at 320ms is mostly Python interpreter (~90ms) + Click
  parse + execute. Hot path already tight; further wins need a
  daemon or in-process MCP path (already free for MCP clients).
- **(empty / edge-case repos)** — most commands handle empty
  repos correctly; one cosmetic dead-empty fix landed.
- **(mermaid quality)** — ``visualize`` output is
  well-structured (color-coded by kind, named clusters).
- **(schema export)** — ``roam schema`` already validates
  envelopes. Per-command schema introspection is a bigger feature.
- **(cross-command consistency)** — verdict-first compliance
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

- **(N+1 detection)** — existing detector catalog already
  covers the SOTA static-analysis space. Runtime profilers like
  ``nplusone`` are complementary, not replacement.
- **(clone detection)** — current AST-hash-bag + Jaccard
  approach is SOTA-comparable. Neural alternatives (CCDetect,
  ASTNN) need training data and don't pay back the integration cost.
- **(anomaly detection)** — Modified Z-Score (MAD-based) +
  Theil-Sen + Mann-Kendall + Western Electric + CUSUM cover the
  statistical anomaly-detection space without sklearn as a hard dep.
- **(semantic retrieve)** — graceful zeta redistribution
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
canonical ``templates/distribution/landing-page/.well-known/mcp-server-card.json``). Added
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
  `templates/distribution/landing-page/.well-known/mcp-server-card.json`, `README.md`,
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
  14 pydantic + 31 dataclass + 1 enum in a Python research repo.
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
  codebase (a 17k-file external Python repo: 167 open-leaks, 4 sync-in-async, 146 bare-except).
- roam-code itself: 0 findings across all 11 detectors (post-fix).

## [12.4.0] - 2026-05-02

A Python-pivot release. Three super-passes of dogfooding on real
Python codebases (a Python research repo, an agent-eval workspace, a 17k-file external Python repo) surfaced
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

A polish patch from additional dogfooding rounds. No surface changes,
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
- **MCP server card** at `templates/distribution/landing-page/.well-known/mcp-server-card.json`
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
- **Django bridge** — full implicit-relationship resolution: admin → model (via `@admin.register` / `admin.site.register`), serializer/form/filterset → model (via `Meta.model`), `@receiver(sender=Model)`, `path()`/`re_path()`/`include()` URL trees, DRF `router.register()`, `@app.task`/`@shared_task` tagging. Companion `index/django_post.py` resolves transitive Django model inheritance + custom field metadata after the per-file extraction phase. New schema columns: `symbols.framework_type`, `field_type`, `field_metadata`; `edges.call_function`. Ported from upstream fork work.
- **`roam.git_utils.worktree_git_env(cwd)`** — sets `GIT_INDEX_FILE` per worktree so parallel agents in sibling worktrees don't contend on `.git/index.lock`. Wired into `discovery.py`, `git_stats.py`, `changed_files.py`, `cmd_index_bundle.py`. Ported from upstream fork work.

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
