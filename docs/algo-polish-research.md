# Algo Polish Research

Updated: 2026-05-19

## Current State

`roam algo` is the canonical command; `roam math` is the compatibility alias.

What is already built:

| Surface | Current count | Notes |
| --- | ---: | --- |
| Universal catalog tasks | 34 | `src/roam/catalog/tasks.py`; Big-O / rank / language-tip model |
| Built-in catalog detectors | 34 | Decorated in `src/roam/catalog/detectors.py`; visible through `--list-detectors` |
| Python idiom detectors | 23 | Loaded by `run_detectors()` from `src/roam/catalog/python_idioms.py` |
| Total runtime detector surface | 57 | `roam algo --list-detectors` now reports catalog + Python idiom detectors |

What this pass improved:

- `--list-detectors` now shows the full runtime surface, not only the 34 decorator-backed detectors.
- `--only` / `--exclude` now work for Python idiom detectors too.
- Added `py-pandas-iterrows`, a Python-specific performance detector for pandas `DataFrame.iterrows()` row loops.
- README/public demo text now describes the 34-task catalog and the Python idiom detector pack instead of the old 23-pattern wording.

## Research Notes

The research direction is not "copy every linter rule." Roam should add detectors when it can add one of these advantages:

- graph or call-context evidence,
- runtime/hotspot impact scoring,
- framework-aware false-positive suppression,
- SARIF/proof-bundle output for agents,
- cross-language or cross-layer context.

Sources checked:

- Ruff rules list: Perflint includes `PERF101` unnecessary list cast, `PERF203` try/except in loop, `PERF401` manual list comprehension, `PERF402` manual list copy, `PERF403` manual dict comprehension. https://docs.astral.sh/ruff/rules/
- ESLint `no-await-in-loop`: recommends starting independent promises first and awaiting with `Promise.all`; also notes sequential awaits can create unhandled rejection risk. https://eslint.org/docs/latest/rules/no-await-in-loop
- Django database optimization: recommends `select_related()` / `prefetch_related()`, `values()` / `values_list()`, `defer()` / `only()`, `count()`, `exists()`, and bulk `update()` / `delete()` where appropriate. https://docs.djangoproject.com/en/3.2/topics/db/optimization/
- SQLAlchemy relationship loading: `selectinload()` is described as usually the simplest efficient eager-loading strategy for collections. https://docs.sqlalchemy.org/en/21/orm/queryguide/relationships.html
- pandas `DataFrame.iterrows`: pandas says row iteration via `iterrows()` does not preserve dtypes and recommends `itertuples()` for speed and type consistency. https://pandas.pydata.org/pandas-docs/dev/reference/api/pandas.DataFrame.iterrows.html
- CodeQL Python ReDoS: ambiguous repeated subexpressions can drive polynomial or exponential regex matching. https://codeql.github.com/codeql-query-help/python/py-redos/
- OWASP ReDoS: "evil regex" shapes include repeated groups containing repetition or overlapping alternation. https://owasp.org/www-community/attacks/Regular_expression_Denial_of_Service_-_ReDoS

## Priority Queue

### P0 - Make The Surface Trustworthy

1. Keep detector discovery honest.
   - Done in this pass for Python idiom detectors.
   - Next: include plugin-contributed detectors in `--list-detectors` with a `source="plugin"` row when metadata is available.

2. Add a `--list-tasks` view.
   - Why: `--task` is a task-id filter, but users currently discover task ids indirectly through `--list-detectors`.
   - Shape: task id, category/kind, detector count, source, best suggestion.

3. Give Python idiom findings catalog-like metadata.
   - Today they run and filter correctly, but most do not have `get_tip()` / `get_fix()` catalog entries.
   - Best fix: add a small `IDIOM_CATALOG` or merge them into a typed catalog view without inflating the universal Big-O task count.

### P1 - Add High-Signal Detectors

1. Django ORM count/existence misuse.
   - Detect `len(queryset)` when no iteration follows; suggest `.count()`.
   - Detect `if queryset` when only existence is needed; suggest `.exists()`.
   - Guard: suppress when the code immediately iterates the same queryset, matching Django's own "do not overuse count/exists" warning.

2. Django / SQLAlchemy eager-loading precision.
   - Upgrade `py-django-n1` and `py-sqlalchemy-lazy` to capture the exact relation access when possible.
   - Use Roam's call graph and template edges to rank views/routes higher than model helpers.

3. ReDoS / catastrophic regex detector.
   - Add a conservative detector for repeated groups with nested repetition or overlapping alternation.
   - Route to `algo` when framed as algorithmic complexity, and cross-link to `security`/`vulns` when input is user-controlled.

4. Python comprehension and allocation patterns.
   - Candidate rules: manual list/dict comprehension, unnecessary `list()` before iteration, dict value lookup via key inside `.items()`-like loops.
   - Only ship when Roam can add impact scoring or broader evidence than Ruff.

5. JS/TS serial async refinement.
   - Existing detector catches serial awaits.
   - Next: detect whether the awaited expression is independent of the previous iteration, and suggest bounded concurrency when the loop is I/O-heavy.

### P2 - Prove Precision

1. Add labelled fixture suites for every high-signal detector.
   - Mirror `tests/fixtures/detector_eval/*/expected.json`.
   - Track precision and recall per detector, not only "it fires once."

2. Add a public detector-quality doc.
   - Include "when it fires", "when it suppresses", false-positive classes, and source references.

3. Dogfood `roam algo` on three external OSS repos.
   - One Python web app, one JS/TS app, one mixed backend/frontend repo.
   - Save JSON envelopes as internal fixtures, then turn recurring FP classes into tests.
