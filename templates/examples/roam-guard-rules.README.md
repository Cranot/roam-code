# Roam Guard rule packs — adoption guide

A rule pack is the YAML file that tells Roam Guard *which checks must run for
this change*. The bundled examples in this directory cover the four
language archetypes we've validated:

| File | Pack | Stack |
|---|---|---|
| `roam-guard-rules.default.yml` | `default` | Built-in baseline (Python / generic) |
| `roam-guard-rules.rails.yml` | `rails-default` | Ruby on Rails (controllers, models, jobs, routes) |
| `roam-guard-rules.nextjs.yml` | `nextjs-default` | Next.js / TypeScript frontend (app + pages router) |
| `roam-guard-rules.go.yml` | `go-default` | Go services + libraries |

## Choosing a pack

Pick the closest match for your repo's primary stack. Copy it to your repo
as `.roam/guard-rules.yml` (the default location Roam Guard checks for) or
keep it under any path and pass `--rules <path>` to commands:

```bash
roam guard-pr --rules templates/examples/roam-guard-rules.rails.yml
```

## Customizing — `extends:` inheritance

Every shipped pack uses `extends: default` so you inherit the built-in
auth / migration / config / test-file rules and only define the
stack-specific ones. Override a rule by re-declaring its `id`:

```yaml
# my-repo/.roam/guard-rules.yml
name: my-repo
version: 1.0
extends: default

file_patterns:
  # ADD a new rule for your repo's payment surface.
  - id: payments_changed
    regex: '^src/billing/.*\.py$'
    applies_to_kinds: [test, lint]

  # OVERRIDE the inherited auth rule to be stricter for your codebase.
  - id: auth_file_changed
    regex: '^src/auth/.*\.py$'         # tighter than the default
    applies_to_kinds: [test, lint, build]  # more kinds than the default
```

Same-id replaces; new-id appends. See
`templates/examples/roam-guard-rules.rails.yml` for a real example that
overrides `config_file_changed` to be stricter on Ruby.

## The YAML schema

```yaml
name: <required-string>                # the pack's identity
version: <string, default "1.0">       # informational
extends: <pack-name>                   # optional — currently only "default" recognized
file_patterns:                         # required (can be empty when extending)
  - id: <required-string>              # becomes the verdict reason code
    regex: <required-string>           # Python re.IGNORECASE pattern
    applies_to_kinds: [<kind>, ...]    # non-empty list of command_graph kinds
```

Closed `kind` values match what `roam commands` (the G2 command graph) emits:
`test`, `lint`, `build`, `migration`, etc.

## Testing a pack

Before shipping a custom pack, validate it and dry-run rule matching:

```bash
# Parse + structural check
roam guard-rules validate .roam/guard-rules.yml

# Dry-run: which rules match this file?
roam guard-rules test src/billing/charges.py
# → MATCHES (1): src/billing/charges.py
# →   - payments_changed → kinds=[lint,test]

# Inspect the active pack as YAML (after --rules)
roam guard-rules show --rules .roam/guard-rules.yml
```

## Pre-flight check

`roam guard-doctor` will validate your rule pack as part of its health check:

```bash
roam guard-doctor --rules templates/examples/roam-guard-rules.default.yml
# ✓ rule_pack — rule pack `my-repo` loaded with 8 pattern(s)
```

A blocking failure here means the pack file is malformed and `roam guard-pr`
can't proceed. Run `roam guard-rules validate <path>` for the specific
parser error.

## When to write your own

The shipped packs are starting points, not load-bearing. Replace them when:

- Your monorepo has stack-specific subdirectories (e.g. `frontend/` + `backend/`)
- Your security boundary lives somewhere non-conventional (e.g. `internal/secrets/`)
- Your test runners are kind-specific (e.g. `e2e` vs `unit` test commands)
- Your compliance posture is `regulated` (set `--policy-profile regulated` AND
  define rules that exercise every required check)

## Contributing a pack

If you've built a pack that's broadly useful for an archetype not covered
here (Django, Phoenix, Spring, .NET, etc.), open a PR adding it to
`templates/examples/roam-guard-rules.<stack>.yml` with a short header
comment documenting the patterns.
