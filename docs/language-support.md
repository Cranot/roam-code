# Language support

Roam uses dedicated extractors for the languages below and a generic tree-sitter
walker for others. An extractor lists supported constructs, not complete semantic
resolution. Dynamic dispatch, reflection and framework behavior can remain
unresolved. See [detector evidence](concepts/detector-evidence.md) before using
static connections as evidence, and the [README](../README.md#language-support)
for the overview. This table was moved from the README on 2026-09-12.

## Dedicated extractors

| Language | Extensions | Symbols | References | Inheritance |
|----------|-----------|---------|------------|-------------|
| Python | `.py` `.pyi` | classes, functions, methods, decorators, variables | imports, calls, inheritance | extends, `__all__` exports |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | classes, functions, arrow functions, CJS exports | imports, require(), calls | extends |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | interfaces, type aliases, enums + all JS | imports, calls, type refs | extends, implements |
| Java | `.java` | classes, interfaces, enums, constructors, fields | imports, calls | extends, implements |
| Go | `.go` | structs, interfaces, functions, methods, fields | imports, calls | embedded structs |
| Rust | `.rs` | structs, traits, impls, enums, functions | use, calls | impl Trait for Struct |
| C / C++ | `.c` `.h` `.cpp` `.hpp` `.cc` | structs, classes, functions, namespaces, templates | includes, calls | extends |
| C# | `.cs` | classes, interfaces, structs, enums, records, methods, properties, delegates, events | using directives, calls, `new`, attributes | extends, implements |
| PHP | `.php` | classes, interfaces, traits, enums, methods, properties | namespace use, calls, static calls, `new` | extends, implements, use (traits) |
| Ruby | `.rb` | classes, modules, methods, singleton methods, constants | require, require_relative, include/extend, calls | class inheritance |
| Kotlin | `.kt` `.kts` | classes, interfaces, enums, objects, functions, methods, properties | imports, calls, type refs | extends, implements |
| Scala | `.scala` `.sc` | classes, traits, objects, case classes, functions, val/var, type aliases | imports, calls, `new` | extends, with (trait mixins) |
| Swift | `.swift` | classes, structs, enums, protocols, functions, methods, properties | imports, calls, type refs | extends, conforms |
| Dart | `.dart` | classes, mixins, extensions, enums, type aliases, functions, methods, constructors | imports, calls, type refs | extends, implements, with |
| Visual FoxPro | `.prg` | functions, procedures, classes, methods, properties, constants | DO, SET PROCEDURE/CLASSLIB, CREATEOBJECT, `obj.method()` | DEFINE CLASS ... AS |
| SQL (DDL) | `.sql` | tables, columns, views, functions, triggers, schemas, types, sequences | foreign keys, view table deps, trigger refs | — |
| YAML (CI/CD) | `.yml` `.yaml` | GitLab CI jobs/anchors, GitHub Actions workflows/jobs, generic top-level keys | `extends:`, `needs:`, `!reference`, `uses:` | — |
| HCL / Terraform | `.tf` `.tfvars` `.hcl` | `resource`, `data`, `variable`, `output`, `module`, `provider`, `locals` | `var.*`, `module.*`, `data.*`, `local.*` | — |
| Vue / Svelte | `.vue` `.svelte` | via `<script>` block extraction (TS/JS) | imports, calls, type refs | extends, implements |

<details>
<summary><strong>Salesforce ecosystem (Tier 1)</strong></summary>

| Language | Extensions | Symbols | References |
|----------|-----------|---------|------------|
| Apex | `.cls` `.trigger` | classes, triggers, SOQL, annotations | imports, calls, System.Label, generic type refs |
| Aura | `.cmp` `.app` `.evt` `.intf` `.design` | components, attributes, methods, events | controller refs, component refs |
| LWC (JavaScript) | `.js` (in LWC dirs) | anonymous class from filename | `@salesforce/apex/`, `@salesforce/schema/`, `@salesforce/label/` |
| Visualforce | `.page` `.component` | pages, components | controller/extensions, merge fields, includes |
| SF Metadata XML | `*-meta.xml` | objects, fields, rules, layouts | Apex class refs, formula field refs, Flow actionCalls |

Supported cross-language bridges can connect Apex, LWC, Aura, Visualforce and
Flows. Inspect the returned edges; this is not complete runtime impact coverage.

</details>

Tier 2 languages (and `.jsonc` / `.mdx`) get basic symbol extraction via a generic tree-sitter walker.
