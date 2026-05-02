# Roam — language extractor precision matrix

External reviewers are right that "27 languages" is less informative
than "what's actually accurate per language". This page is the
honest version. Each row is a Tier 1 language's extractor:

* **Solid** = the extractor reliably resolves the construct without
  manual intervention.
* **Heuristic** = the extractor identifies the shape but resolution
  may be incomplete (dynamic dispatch, conditional imports, etc).
* **Not extracted** = doesn't surface the construct (yet).

## Tier 1 language matrix

| Language | Symbols | Edges (call/import) | Decorators | Type info | Async | Known limits |
|---|---|---|---|---|---|---|
| **Python** | Solid | Solid for static imports; Heuristic for dynamic / `__getattr__` / metaclasses | Solid (v12.4+) | Solid (annotations preserved) | Solid (`is_async` v12.4+) | Dynamic dispatch via `getattr`/`globals`; `eval`/`exec`; runtime monkey-patching; conditional imports under `try:` |
| **JavaScript / TypeScript** | Solid | Solid for static imports; Heuristic for dynamic `import()` / require | Solid for TS decorators (Stage 2) | TS: solid; JS: not extracted | Async detected at signature level | JSX/TSX shapes occasionally misparsed; TS generics drop type args; CommonJS `module.exports` heuristics |
| **Go** | Solid | Solid (Go's import system is static) | n/a | Solid (signatures) | Goroutines detected by `go fn(...)` regex (heuristic) | Generics partial; build tag conditional code paths not branched |
| **Rust** | Solid | Solid for `use` paths | Macro `#[derive(...)]` captured as decorator | Solid | `async fn` detected | Macro expansion not performed; conditional `#[cfg(...)]` blocks not branched |
| **Java** | Solid | Solid for class imports | Annotations captured | Solid | Project Loom / `CompletableFuture` only by signature | Reflection / Spring autowiring not tracked; generics partial |
| **C / C++** | Solid for fns/structs | Solid for `#include` | n/a | Pointer types preserved | n/a | Templates partial; preprocessor branches not evaluated; macro-defined symbols missed |
| **C#** | Solid | Solid for `using` | Attributes captured | Solid | `async Task` detected | LINQ expressions opaque; partial classes joined heuristically |
| **PHP** | Solid | Solid for `use`/`require` | Attributes (PHP 8) captured | Solid in modern PHP | n/a (Promise libs invisible) | `__call`/`__get` magic methods not resolved; Laravel facades partial |
| **Ruby** | Solid | Solid for `require`/`require_relative` | n/a | Sorbet/RBI not parsed | n/a | `method_missing` / `define_method` / `class_eval` not resolved; metaprogramming opaque |
| **Kotlin** | Solid (v11.1+ regex fallback for grammar drift) | Solid for `import` | Annotations captured | Solid | `suspend` detected | Inline / reified generics partial; coroutines flow not graphed |
| **Swift** | Solid | Solid for `import` | Property wrappers captured | Solid | `async` detected | Protocol-oriented dispatch not always resolved; `@main` lifecycle not tracked |
| **Scala** (v11.1.2+) | Solid for class/object/trait | Solid for `import` | Annotations captured | Solid | n/a (no async keyword in Scala) | Implicits not resolved; macros / metaprogramming opaque |
| **SQL** (v11.1.3+) | Tier 1 DDL: tables, views, fns, procs, triggers | Solid for FK | n/a | Column types preserved | n/a | Stored-procedure body call graph not extracted; dialect quirks (Postgres array, MSSQL temp tables) partial |
| **Apex** | Solid (Salesforce-specific extractor) | Solid for `@AuraEnabled` etc | Annotations captured | Solid | n/a | Trigger context limited; SOQL/SOSL parsed but not graph-resolved |
| **Aura / Visualforce / SfXml** | Tag/attribute level | Cross-language bridges to Apex via Salesforce bridge | n/a | n/a | n/a | Limited to declarative-shape extraction; expression language not resolved |
| **HCL (Terraform)** | Solid for `resource`/`module`/`variable` | Solid for `module` source / `var`/`local` references | n/a | n/a | n/a | `for_each`/`count` not unrolled; data-source resolution partial |
| **YAML** | Symbols where YAML acts as code (rules, configs) | Cross-references via key path | n/a | n/a | n/a | Schema-specific resolvers limited to known shapes (Kubernetes, GitHub Actions) |
| **FoxPro** | Tier 1 dedicated extractor | Limited to file-level | n/a | n/a | n/a | Niche; precision good but ecosystem coupling minimal |

## Tier 2 (generic extraction)

These languages get tree-sitter symbol extraction without dedicated
language semantics:

`HTML, CSS, Markdown, TOML, JSON, Bash, Lua, Dart, Elixir, R, Perl,
Haskell, OCaml, Erlang`

What you get: symbols with names + line ranges. What you don't get:
typed signatures, decorator semantics, framework-aware resolution,
cross-language edges via bridges.

## What's NEVER extracted (any language)

* **Runtime-only behaviour**: anything that depends on values
  available only at runtime (`getattr(obj, computed_name)`,
  `eval()`, `exec()`).
* **Dynamic class generation**: classes built at runtime via
  metaclasses, `type()`, decorators that synthesize methods.
* **Reflection-based DI**: Spring autowiring, Java reflection,
  Python `pkgutil.iter_modules` discovery.
* **Build-tag/feature-flag branches**: code paths conditional on
  values not knowable from static analysis.
* **String-built SQL**: SQL constructed via string concatenation;
  the literal SQL strings are extracted, but call graph through them
  isn't resolved.

## Known false-positive classes

| Detector / signal | False-positive class | Mitigation |
|---|---|---|
| `py-broad-except` | Defensive outer `except Exception:` in CLI plumbing is legitimate | Marked low-confidence in `roam math` |
| `py-except-pass` | Test cleanup / optional-feature gating | Marked low-confidence |
| `py-lambda-in-loop` | Click callback `--option` definitions | Marked low-confidence |
| `cmd_FOO.py` retrieve boost | Commands matching multiple FOO substrings | Capped magnitude per match |
| `roam health` god-component count | Utility hubs (`json_envelope`, `to_json`) flagged as god | Excluded by `_is_utility_path` discount |

## Known false-negative classes

| Detector / signal | False-negative class | Status |
|---|---|---|
| `py-async-not-awaited` | Coroutine assigned to a `dict[str, Coroutine]` for later batch await | Conservative regex misses |
| `py-django-n1` | ORM queries inside list/dict comprehensions | Loop-detection heuristic doesn't cover comprehensions |
| `py-sqlalchemy-lazy` | Implicit lazy-load via `repr()` / `print()` of related entity | Requires deeper attribute-access tracking |
| `is_async` extraction | Async functions wrapped in `functools.partial` | Wrapper not unwrapped |

## How to report incorrect extraction

Open an issue at https://github.com/Cranot/roam-code/issues with:

1. The minimal source snippet that demonstrates the problem.
2. What you expected `roam search`/`roam context`/`roam math` to
   return.
3. What it actually returned.

We add representative cases to
`tests/test_python_idioms_e2e.py` /
`tests/test_extractor_grammar_drift.py` /
`tests/test_languages.py` so the regression doesn't return.
