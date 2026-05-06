# Roam architecture rule packs

Starter `.roam-rules.yml` packs tuned for the most common AI-generated-code
anti-patterns Q1-Q2 2026. Each pack is opinionated but conservative — every
rule has a `description` so the PR comment surfaces *why* it fired.

## Packs

| Path | Language(s) | Rules | Severities |
|---|---|---|---|
| `python/.roam-rules.yml` | Python | 14 | 7 BLOCK, 7 WARN |
| `typescript/.roam-rules.yml` | TS / JS / JSX / Vue | 14 | 5 BLOCK, 9 WARN |
| `go/.roam-rules.yml` | Go | 12 | 3 BLOCK, 9 WARN |
| `java/.roam-rules.yml` | Java | 12 | 5 BLOCK, 7 WARN |

## Usage

Drop a pack at `.roam/rules.yml` in your repo root:

```bash
cp templates/rules/python/.roam-rules.yml .roam/rules.yml
roam rules-validate .roam/rules.yml --explain
```

Then `roam pr-analyze` auto-loads it on every PR. Pair with `--gate` in CI
to fail builds on BLOCK-severity violations.

## Customising

The packs are intentionally generic — fork them and tune to your codebase:

- Tighten `source_glob` to your real layout (e.g. `apps/api/**/*.py` not `src/**/*.py`)
- Demote BLOCK → WARN for rules you want as advisory
- Add custom `forbidden_target_glob` patterns for your internal banned APIs

After editing, validate before committing:

```bash
roam rules-validate .roam/rules.yml --strict --gate
```

## Pattern types (the four matchers)

- **`import_from`** — Python `from X import` / `import X`, JS/TS `import ... from "X"`
- **`function_call`** — any call `name(` or `ns.name(` (e.g. `eval`, `pickle.loads`)
- **`class_inherit`** — base classes in `class Foo(Base, ...)` declarations
- **`decorator_use`** — decorator lines `@name` or `@ns.name`

Run `roam rules-validate --explain` for examples + glob syntax.
