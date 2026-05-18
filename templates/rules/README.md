# Roam architecture rule packs

Starter `.roam-rules.yml` packs tuned for the most common AI-generated-code
anti-patterns. Each pack is opinionated but conservative — every rule carries
a `description` so the PR comment surfaces *why* it fired.

These packs map to the rule-substrate engine in `src/roam/rules/`. They
support evidence for architecture-discipline review; they do not certify
compliance with any standard.

## Packs

| Path | Language(s) | Rules | Severities |
|---|---|---|---|
| `python/.roam-rules.yml` | Python | 14 | 8 BLOCK, 6 WARN |
| `typescript/.roam-rules.yml` | TS / JS / JSX / Vue | 14 | 6 BLOCK, 8 WARN |
| `go/.roam-rules.yml` | Go | 12 | 3 BLOCK, 9 WARN |
| `java/.roam-rules.yml` | Java | 12 | 5 BLOCK, 7 WARN |
| `kotlin/.roam-rules.yml` | Kotlin (Android + Spring) | 12 | 5 BLOCK, 7 WARN |
| `rust/.roam-rules.yml` | Rust | 30 | 5 BLOCK, 16 WARN, 9 NOTE |
| `swift/.roam-rules.yml` | Swift | 25 | 4 BLOCK, 8 WARN, 13 NOTE |

For a hand-annotated example covering the four pattern types end-to-end, see
[`../examples/.roam-rules.yml`](../examples/.roam-rules.yml).

## Install

These packs ship in the repo, not in the `roam-code` wheel. Copy one out of
a source checkout into your project's `.roam/rules.yml` — the path
`roam pr-analyze` auto-loads by default. Override the path with `--rules
<path>` when you keep multiple packs alongside one repo.

```bash
cp templates/rules/python/.roam-rules.yml .roam/rules.yml
roam rules-validate .roam/rules.yml --explain
```

Then run `roam pr-analyze` on a PR — it auto-loads `.roam/rules.yml` and
surfaces every match in the PR comment. Pair with `--gate` in CI to fail
the build on BLOCK-severity violations.

## Customise

Fork a pack and tune it to your codebase — the defaults are intentionally
generic:

- Tighten `source_glob` to your real layout (e.g. `apps/api/**/*.py`, not `src/**/*.py`).
- Demote BLOCK to WARN for rules you want as advisory.
- Add `forbidden_target_glob` patterns for your internal banned APIs.

Validate before committing — `--strict` treats warnings as errors, `--gate`
exits 5 on any error:

```bash
roam rules-validate .roam/rules.yml --strict --gate
```

## Pattern types (the four matchers)

- **`import_from`** — Python `from X import` / `import X`, JS/TS `import ... from "X"`
- **`function_call`** — any call `name(` or `ns.name(` (e.g. `eval`, `pickle.loads`)
- **`class_inherit`** — base classes in `class Foo(Base, ...)` declarations
- **`decorator_use`** — decorator lines `@name` or `@ns.name`

Run `roam rules-validate --explain` for the pattern reference plus glob
syntax (fnmatch: `*` matches a segment, `**` recurses, `{a,b}` enumerates).

## Related

- `roam check-rules` — run the rule pack against the indexed graph (no PR diff required).
- `roam audit-trail-conformance-check` — validates EU AI Act Article 12 record-keeping when `pr-analyze --audit-trail` is enabled. Maps to evidence requirements; it does not certify compliance.
- See the `CLAUDE.md` "Adding a new CLI command" and "Evidence compiler layer" sections for how rule findings feed the shared findings registry.
