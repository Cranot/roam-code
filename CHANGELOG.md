# Changelog

All notable changes to [roam-code](https://github.com/Cranot/roam-code) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
