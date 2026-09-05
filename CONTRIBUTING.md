# Contributing to roam-code

Thank you for your interest in contributing to roam-code! This document covers
everything you need to get started.

## Quick Start

1. Fork the repository
2. Clone your fork: `git clone https://github.com/<you>/roam-code.git`
3. Create the locked development environment: `uv sync --locked --no-default-groups --extra dev --group ci --python 3.12`
4. Run the relevant tests: `uv run --no-sync pytest tests/test_basic.py -n 0`
5. Create a branch, make changes, submit a PR

## Development Setup

### Prerequisites

- Python 3.10+; Python 3.12 is the standard local development interpreter
- Git
- uv (use the version pinned in `.github/workflows/roam-ci.yml` for CI parity)

### Installation

```bash
git clone https://github.com/Cranot/roam-code.git
cd roam-code
uv sync --locked --no-default-groups --extra dev --group ci --python 3.12
uv run --no-sync python --version
uv run --no-sync roam --version

# Enable the commit-msg hook (rejects Co-Authored-By trailers + AI attribution)
git config core.hooksPath .githooks

```

`uv sync` installs this checkout in editable mode from `uv.lock`. The `dev`
extra includes the MCP and test dependencies; the `ci` group adds the pinned
audit and documentation tools. Use `uv run --no-sync` for subsequent commands
so an invocation does not silently change the dependency selection. Run the
same sync command again after pulling a lockfile change. The
[repository maintenance guide](docs/repository-maintenance.md) covers package
repair, index freshness, and a repeatable health baseline.

An unlocked `python -m pip install -e ".[dev]"` is useful for compatibility
experiments, but it does not reproduce the dependency graph used by CI.

The separate `.pre-commit-config.yaml` exposes two checks for users of the
pre-commit framework. This clone uses `.githooks/`; do not install a second
hook manager over that configured hooks path.

- **`count-drift`** runs `scripts/sync_surface_counts.py` and blocks the commit
  when README / pyproject / landing-page counts diverge from the live CLI
  surface in `src/roam/cli.py` + `src/roam/mcp_server.py`.
- **`no-coauthor`** parses the commit message and rejects any
  `Co-Authored-By:` trailer — project policy is single-author on this repo.

## Git hooks (required)

roam-code keeps its git hooks under version control in `.githooks/` so every
clone runs the same gates. Enable them once per clone:

```bash
git config core.hooksPath .githooks
```

That single command activates all three tracked hooks:

| Hook | When | What it does |
|---|---|---|
| `pre-commit` | every `git commit` | Scan staged content for internal-language leaks; check generated counts, changelog, and the local internal-document index; lint and format-check staged Python files. |
| `pre-push` | every `git push` | Scan the exact pushed history for secrets and internal language, then run the FAST structural bundle and its whole-tree language scan. |
| `commit-msg` | every `git commit` | Rejects `Co-Authored-By:` trailers and AI-attribution lines (single-author project policy). |

**Why required:** the anti-leak gate used to run *only* in CI. With no
installed hook, a leak could reach the public repo before CI caught it. The
commit-time and push-time hooks now block leaks locally, on the staged content
and the whole tree respectively.

The forbidden-pattern catalogue is a single source of truth at
`scripts/internal_language_patterns.py` (stdlib-only), shared by the hooks
*and* the CI gate `tests/test_no_internal_language.py`. If a hit is
intentional, add the file to `WHITELIST_FILES` there (with a comment) or
tighten the offending regex — do not bypass with `--no-verify`.

### Running Tests

```bash
# Full test suite
uv run --no-sync pytest tests/

# Parallel execution (faster, requires pytest-xdist)
uv run --no-sync pytest tests/ -n 4 --dist loadgroup

# Skip timing-sensitive performance tests
uv run --no-sync pytest tests/ -m "not slow" -n 4 --dist loadgroup

# Single test file
uv run --no-sync pytest tests/test_comprehensive.py -x -v -n 0

# Single test class or method
uv run --no-sync pytest tests/test_comprehensive.py::TestHealth -x -v -n 0

# Sequential execution (useful for debugging)
uv run --no-sync pytest tests/ -n 0
```

Local pytest is sequential unless you pass `-n`. The `roam.testing.ci_xdist`
plugin enables `-n auto --dist loadgroup` when `CI` is set and no explicit
worker option is supplied. Use a bounded worker count locally to control memory,
native-library threads, and temporary disk usage. Runtime depends on the machine
and suite; the full non-slow suite can take hours on Windows.

Run focused tests while editing, then the appropriate repository gate:

```bash
uv run --no-sync python scripts/prepush_check.py --full --workers 4
# Before a release or an accumulated release-sized batch:
uv run --no-sync python scripts/prepush_check.py --release --workers 4
```

`--full` adds documentation checks to the FAST tier. Only `--release` runs the
entire non-slow suite. The release gate prints which CI lanes remain unproven;
wait for CI on the exact pushed commit before attaching a release tag.

#### Running tests on Windows

The dev `pytest` isn't always on PATH outside the venv. Use the venv
binary directly:

```powershell
.venv\Scripts\pytest.exe tests\test_yourfile.py -x -v -n 0
```

On macOS / Linux the equivalent is `.venv/bin/pytest tests/...`.

All tests must pass before submitting a PR.

For a regression, establish a defect-specific failing case before the repair
and a passing case afterward, retaining a valid-case control. Freeze the test
between those runs; if it changes, repeat both. Exercise the actual downstream
reader or CLI, not only a helper's output shape. See
[verification evidence](docs/concepts/verification-evidence.md) for proof-bundle
round trips, benchmark denominators, protected outcomes, and claim limits.

### Dogfood smoke (roam-on-roam)

After a meaningful change, run roam on its own source tree to confirm
the index still builds and the preflight gate behaves on a known
symbol:

```bash
uv run --no-sync roam index                 # refresh the existing checkout index
uv run --no-sync roam doctor                # environment and index diagnostics
uv run --no-sync roam health --explain       # architectural score and its contributors
uv run --no-sync roam preflight ensure_index # impact and tests for a real symbol
```

If `roam health` drops sharply or `roam preflight` fails the fitness
gate on a stable symbol, treat that as a regression and investigate
before opening the PR. The same loop is the canonical "earn the right
to change code" rehearsal documented in `AGENTS.md`.

### Linting

```bash
uv run --no-sync ruff format --check src/ tests/
uv run --no-sync ruff check src/ tests/
```

The project uses ruff with `target-version = "py310"` and `line-length = 120`.
Selected rule sets: E, F, W, I, T20 (pyflakes, pycodestyle, isort, print statements).

### Code Style

- **Functions and methods:** `snake_case`
- **Classes:** `PascalCase`
- **Imports:** Absolute imports for cross-directory references
- **Future annotations:** Every source file must start with `from __future__ import annotations` so type hints stay strings at runtime (cheaper import, safer forward references, avoids PEP 604 union evaluation costs). The project requires Python 3.10+ (`pyproject.toml`); this is a code-quality convention, not a back-compat shim.
- **Output format:** Plain ASCII only -- no emojis, no colors, no box-drawing characters. This keeps output token-efficient for LLM consumption.
- **Output abbreviations:** `fn` (function), `cls` (class), `meth` (method) -- via `abbrev_kind()`

See the [Architecture Guide](https://roam-code.com/docs/architecture) for the complete conventions reference.

## Pre-commit Hooks

roam-code ships a `.pre-commit-hooks.yaml` so you can run roam checks as
[pre-commit](https://pre-commit.com/) hooks in any project that has roam-code
installed.

Add the following to your project's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/Cranot/roam-code
    rev: v13.4            # pin to a release tag
    hooks:
      - id: roam-secrets        # secret scanning -- no index required
      - id: roam-syntax-check   # tree-sitter syntax validation -- no index required
      - id: roam-verify         # convention consistency check
      - id: roam-health         # composite health score (informational)
```

Available hook IDs and what they do:

| Hook ID | Command | Fails on | Index required? |
|---|---|---|---|
| `roam-secrets` | `roam secrets --fail-on-found` | Any secret found | No |
| `roam-syntax-check` | `roam syntax-check --changed` | Syntax errors | No |
| `roam-verify` | `roam verify --changed` | Score < 70 | Yes (auto-init) |
| `roam-health` | `roam health` | Never (informational) | Yes (auto-init) |
| `roam-vibe-check` | `roam vibe-check` | Never by default | Yes (auto-init) |

Notes:
- `roam-secrets` and `roam-syntax-check` operate directly on files and work
  without a pre-existing roam index.
- `roam-verify`, `roam-health`, and `roam-vibe-check` call `ensure_index()`
  internally and will auto-index the project on first run (equivalent to
  `roam init`).
- All hooks use `pass_filenames: false` and `always_run: true` because roam
  operates on the whole repository rather than individual files.
- To enforce a health threshold in CI, use the `gate` input of the
  [GitHub Action](docs/ci-integration.md) rather than `roam-health` alone.
- To enable the `--threshold` gate on `roam-vibe-check`, override the hook
  args in your config:
  ```yaml
  - id: roam-vibe-check
    args: ['--threshold', '50']
  ```

## How to Contribute

Before picking up work, skim:

- `AGENTS.md` — the agent-OS substrate, the 12 substrate packages, and the
  canonical 4-mode loop (`read_only` / `safe_edit` / `migration` /
  `autonomous_pr`).
- [Documentation map](docs/README.md) — maintained public guides, generated
  references, and the source of truth for each documentation surface.
- GitHub Issues — the public queue for community contributions. Comment on an issue before starting non-trivial work so we can coordinate scope.

### Reporting Bugs

Use the [Bug Report](https://github.com/Cranot/roam-code/issues/new?template=bug_report.yml) issue template. Please include:

- roam-code version (`roam --version`)
- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Actual vs expected output

### Suggesting Features

Use the [Feature Request](https://github.com/Cranot/roam-code/issues/new?template=feature_request.yml) issue template. Explain the use case and why it matters.

### Submitting Code

#### Adding a New CLI Command

1. Create `src/roam/commands/cmd_yourcommand.py` following the command template:

   ```python
   from __future__ import annotations
   import click
   from roam.db.connection import open_db
   from roam.output.formatter import to_json, json_envelope
   from roam.commands.resolve import ensure_index

   @click.command()
   @click.pass_context
   def your_command(ctx):
       json_mode = ctx.obj.get('json') if ctx.obj else False
       ensure_index()
       with open_db(readonly=True) as conn:
           # ... query the DB ...
           if json_mode:
               click.echo(to_json(json_envelope("your-command",
                   summary={"verdict": "...", ...},
                   ...
               )))
               return
           # Text output
           click.echo("VERDICT: ...")
   ```

2. Register in `cli.py`:
   - Add to `_COMMANDS` dict: `"your-command": ("roam.commands.cmd_yourcommand", "your_command")`
   - Add to the appropriate category in `_CATEGORIES` dict

3. Add an MCP tool wrapper in `mcp_server.py` if the command would be useful for AI
   agents. Four skip categories: setup/bootstrap (`init`, `surface`, `version`),
   local-state-only (`mode`, `memory`, `runs`, `lease`, `annotate`), daemon (`watch`),
   and REPL/interactive helpers. Otherwise add the wrapper, declare the read/write
   side-effect flag, and mark non-idempotent tools so the mode gate can enforce
   policy on them. The advisory `tests/test_mcp_wrapper_coverage.py` surfaces
   commands lacking a wrapper that aren't in the skip-taxonomy allowlist.

4. Add `@roam_capability(name="...", category="...", ...)` to the click command — the
   auto-derived `tests/test_capability_decoration.py` will fail without it. Aliases sharing
   the same `(module, function)` tuple in `_COMMANDS` go into `_DEPRECATED_COMMANDS` instead.

5. Anchor any `agent_contract.facts` strings on concrete-noun terminals (LAW 4); the
   `tests/test_law4_lint.py` lint blocks merges on un-anchored facts. See AGENTS.md
   "Concrete-noun anchor vocabulary" for the accepted terminal tokens.

6. Add tests in `tests/`

7. Refresh the command/MCP-tool counts that appear in `README.md`, `CLAUDE.md`,
   `llms-install.md`, and the MCP server cards:

   ```bash
   uv run --no-sync python scripts/sync_surface_counts.py --write
   uv run --no-sync python dev/build_readme_counts.py --apply
   uv run --no-sync python scripts/build_commands_doc.py
   ```

   CI runs `python dev/build_readme_counts.py --check` in the `doc-hygiene`
   job and will fail if any count drifts from the source of truth in
   `src/roam/cli.py` + `src/roam/mcp_server.py`.

#### MCP boundary security

roam's MCP boundary is where agent-emitted tool calls meet the assurance
substrate. It applies egress redaction to recognized secret patterns, enforces
the four-mode policy for tool calls, and links receipts to signed run-ledger
events when that ledger is available. Inspect the policy decision and receipt
integrity fields to distinguish linked, missing, tampered, or unlinked evidence.
These mechanisms support compliance evidence; they do not by themselves make any
project compliant.

**When you'd touch this:**

- Adding a new `@_tool` wrapper that mutates state
- Modifying `_wrap_with_receipt` redaction call sites
- Extending the `policy_decision` closed enum
- Adding a new mode classification
- Touching `src/roam/runs/signing.py`

**Closed-enum vocabulary** (extend the source-of-truth file; never hardcode a new
string at the call site):

- `policy_decision` (6 at the MCP boundary — `allow`, `deny`, `escalate`,
  `redact`, `not_evaluated`, `would_deny_dry_run`): `src/roam/evidence/mcp_receipt.py`
  (strict subset of the 9-member `POLICY_DECISIONS` in `_vocabulary.py`).
- `redactions[]` reasons: `src/roam/evidence/_vocabulary.py:REDACTION_REASONS`.
- `receipt_integrity` (4 — `ok`, `missing`, `tampered`, `not_linked`):
  `src/roam/runs/signing.py:RECEIPT_INTEGRITY_STATES`.

**Schema export.** The receipt JSON Schema (Draft 2020-12, `$id` =
`.../mcp-receipt/v1.json`) lives at `src/roam/evidence/mcp_receipt_schema.py`;
export via `scripts/export_mcp_receipt_schema.py`. Enums and receipt fields are
append-only; gateways pin the `$id` to observe breaking bumps.

**Shadow mode.** `ROAM_MODE_DRY_RUN` evaluates policy and emits
`would_deny_dry_run` instead of blocking, so gateways can observe enforcement
without disabling it. Findings still persist to the registry.

**Drift-guard tests every contributor should know:**

- `tests/test_w_mcp_redact_egress.py` — P0.1 redaction wiring
- `tests/test_w_mcp_mode_enforcement.py` — P0.2 4-mode enforcement
- `tests/test_w_mcp_receipt_hmac_link.py` — P0.3 HMAC-link integrity
- `tests/test_mcp_receipt_json_schema.py` — P2.2 schema parity
- `tests/test_evidence_v0.py` / `tests/test_evidence_schema_migration.py` —
  vocabulary + golden-hash drift

**Hash-stability discipline.** The `ChangeEvidence` content hash and the HMAC
ledger chain MUST stay byte-identical when default-valued fields are absent;
pre-P0.3 chains and pre-P2.2 receipts continue to verify cleanly with no
migration. See the `_W210_OMIT_WHEN_DEFAULT_FIELDS` discipline in
`src/roam/evidence/change_evidence.py`.

**Where to read more:** `dev/MCP-SECURITY-POSTURE.md` (gateway-integrator
audience), the "MCP runtime security" section of `CLAUDE.md`, the 12 substrate
packages and 8 evidence questions in `AGENTS.md`, and Discussion
[#37 reply](https://github.com/Cranot/roam-code/discussions/37#discussioncomment-16967163).

#### Findings-registry discipline

Canonical mandate: new detectors are only strategically useful when they emit
into the shared findings / evidence layer, and new exporters consume from that
layer rather than querying the graph independently.

- Call `emit_finding(conn, FindingRecord(...))` from `src/roam/db/findings.py`.
  Carry a `<DETECTOR>_DETECTOR_VERSION` constant at the call site for drift
  tracking (not in `src/roam/catalog/versions.py`).
- `roam findings count` returns last-run state per detector, not a cumulative
  tally — totals reflect the most recent invocation of each detector.
- Mirror the closest sibling detector in `tests/test_findings_*.py` when adding
  a new one.

Envelope-only is a narrow exception for invocation-scoped findings (diff-region
transients) or commands that gate the registry write behind a `--persist` flag.
Reference patterns: `cmd_clones`, `cmd_flag_dead`, `cmd_path_coverage`.

#### Adding a New Language (Tier 1)

1. Create `src/roam/languages/yourlang_lang.py` inheriting from `LanguageExtractor`
2. Use `go_lang.py` or `php_lang.py` as clean templates
3. Register in `src/roam/languages/registry.py`
4. Add tests in `tests/`

#### Schema Changes

1. Add column in `src/roam/db/schema.py` (CREATE TABLE statements)
2. Add migration in `src/roam/db/connection.py` via `ensure_schema()` using `_safe_alter()`
3. Populate the new column in `src/roam/index/indexer.py`

### Key Patterns to Follow

- **Verdict-first output:** Key commands should emit a one-line `VERDICT:` as the first text output and include `verdict` in the JSON summary.
- **JSON envelope:** All JSON output uses `json_envelope(command_name, summary={...}, **data)`.
- **Batched IN-clauses:** Never write raw `WHERE id IN (...)` with a list > 400 items. Use `batched_in()` from `connection.py`.
- **Lazy-loading:** Commands are lazy-loaded via `LazyGroup` in `cli.py` to avoid importing networkx on every CLI call.

See the [Architecture Guide](https://roam-code.com/docs/architecture) for the full list of patterns and conventions.

## Commit messages

Format: `<type>: <imperative summary>` — keep the summary under 72 chars.

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`, `perf`,
`build`, `style`.

Body lines are optional. When you add one, explain *why*, not *what* —
the diff already shows the what. Lead with the user-visible problem or
the design constraint.

**Good:**

```
feat: add roam stale-refs --attest for in-toto v1 attestations
fix: skip changelog.html in linkcheck (auto-rendered, contains examples)
docs: consolidate to roam-code.com, disable github pages
refactor: collapse two adjacent provider classes in stale-refs hints
ci: render changelog.html on every push
```

**Don't:**

- Phase numbering: `phase 1-5 build: …`, `phase 4C polish`
- Round numbering: `round 4 #15: …`, `pass 79 polish`
- Polish-speak: `5 micro passes`, `5 conversion-leverage polish`
- Session-named bundles: `25-phase polish round (5 leaks + 5 polish + …)`

This is professional, not personal-journal. The CHANGELOG entry is
where the colour goes; the commit subject is just the index.

### Author attribution

Single-author project policy: every commit is authored by Cranot. Do NOT append
`Co-Authored-By:` trailers, AI-attribution lines, or "Generated with" footers.
The local `no-coauthor` pre-commit hook and `.githooks/commit-msg` both reject
these trailers; CI re-runs the same check. If a hook rejects your message,
strip the trailer and re-stage — do not bypass via `--no-verify`.

### Wording lint (governance + security narrative)

CI lints generated reports AND commit messages / docs for over-claim wording:

- **Compliance / governance:** use "maps to" or "supports evidence for" only —
  the codebase intelligence layer emits portable evidence; external GRC tools
  consume it. Never "certifies" or "makes compliant".
- **MCP runtime security:** describe behaviour with the shipped capability
  names — "egress redaction", "mode-gated policy enforcement",
  "tamper-evident receipt chain". Avoid absolute claims ("prevents all secret
  leaks", "fully sandboxed"); the substrate is a defence layer, not a guarantee.

## Version + release cadence

Package identity comes from `pyproject.toml` → `version`. Install instructions
have a separate authority: the highest published `v*` tag, so documentation
does not tell users to fetch an unreleased version. The synchronization scripts
preserve that distinction:

| Script                             | Owns                                                                                                                                                              |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/sync_surface_counts.py`   | Release **pins** — `roam-code==X`, `Cranot/roam-code@vX`, `action.yml`'s `version` input default, `server.json` (server + PyPI package pin), `docs/COMMANDS.md`, `docs/ci-integration.md`, the shipped CI templates, `templates/examples/`, landing-page version stamps. Plus all surface counts. |
| `dev/build_readme_counts.py`       | The `mcp-server-card.json` family (bundled + 3 public mirrors) and the marker-protected count blocks in README / CLAUDE / AGENTS / llms-install.                     |

Both run in dry-run/check mode as CI gates. Identity drift is checked against
`pyproject.toml`; install-pin drift is checked against the published tag.
Repair surface pins with
`scripts/sync_surface_counts.py --write` and generated blocks/cards with
`dev/build_readme_counts.py --apply`. Regenerate the command index with
`scripts/build_commands_doc.py` when its registry changes.

Not every version literal is derived, and a find-replace across the repo is
wrong. Three other classes exist and each is deliberate:

- **Historical** — a record of what was measured or built at a past version:
  `CHANGELOG.md` and its rendered `changelog.html`, `benchmarks/cross-repo-l1/`,
  `templates/audit-report/sample-redacted.md`, and the narrative comments in
  `src/roam/plan/compiler.py`, `src/roam/plan/plan_cache.py`, `src/roam/verdict.py`.
  Rewriting one falsifies a result. Never sync these.
- **Deliberately lagging** — `.github/workflows/roam.yml` consumes this repo's
  *own published* action, so it can only pin a tag/SHA that already exists. It
  moves in a follow-up commit **after** the release tag is pushed.
- **Fixture / illustrative** — arbitrary version values in test fixtures and
  PEP 440 grammar examples. They carry no pin shape and nothing syncs them.

The exemption registry with a reason per entry is
`scripts/sync_surface_counts.py::_VERSION_PIN_EXEMPT`; the gate and both
controls are pinned by `tests/test_w1501_release_version_pins.py`.

**Workflow:**

1. Every PR / direct push lands under `[Unreleased]` in `CHANGELOG.md`.
2. A *release* is a deliberate event:
   1. bump `pyproject.toml`;
   2. rename `[Unreleased]` → `[X.Y.Z] — YYYY-MM-DD`, add a fresh empty
      `[Unreleased]` block;
   3. run `python scripts/sync_surface_counts.py --write` and
      `python dev/build_readme_counts.py --apply`, followed by
      `python scripts/build_commands_doc.py`; re-running their check modes
      must then be clean;
   4. run `python scripts/build_changelog_html.py --write` to re-render
      `changelog.html` from the new `CHANGELOG.md` section (authoring the
      section is the one genuinely manual step);
   5. `uv lock` so `uv.lock`'s own `roam-code` row follows — CI's
      `uv sync --locked` fails otherwise;
   6. tag and publish to PyPI.
3. **After** the tag and package are published, rerun the surface-sync script
   so install examples follow the newly available release, and check the targets
   with `python scripts/check_install_targets.py --network`. Update the dormant
   `.github/workflows/roam.yml` reference to a reviewed release SHA in a follow-up
   commit when refreshing that example.
4. Aim for **weekly to bi-weekly** releases. Patches (`X.Y.Z`) for
   hotfixes only. Don't bump version per commit.

**SemVer interpretation here:**

| Bump        | Meaning                                                |
| ----------- | ------------------------------------------------------ |
| Major (`X`) | Breaking change to CLI / MCP API surface               |
| Minor (`Y`) | New commands, new MCP tools, new languages, schema     |
| Patch (`Z`) | Bug fixes, doc updates, internal cleanup, CI tweaks    |

## Doc-hygiene gates (automatic)

The `doc-hygiene` job in `.github/workflows/roam-ci.yml` runs these gates on
every push. If your change fails one, fix the underlying drift; don't bypass
it.

1. `tests/test_no_internal_language.py` — fails on internal-session
   shorthand (phase numbering, sales-positioning words, day-job
   customer names, etc.).
2. `scripts/sync_surface_counts.py` — fails if README / llms-install /
   landing pages quote stale command / MCP-tool / language counts, or if any
   derived **release pin** disagrees with `pyproject.toml` (see above).
3. `dev/build_readme_counts.py --check` — fails if a marker-protected count
   block or the `mcp-server-card.json` family has drifted.
4. `scripts/build_changelog_html.py` — fails if the rendered
   `changelog.html` drifts from `CHANGELOG.md`.
5. `scripts/linkcheck.py --strict` — fails if any internal landing-page link
   or anchor 404s.
6. `scripts/strip_metadata.py` — fails if a tracked PDF / PNG / SVG carries
   identifying metadata.
7. `scripts/check_install_targets.py` — verifies that documented installation
   targets exist. The generated command index is additionally checked by
   `tests/test_commands_doc_synced.py` in the test suite.

### Drift-guard discipline

Four rules that consistently surface as fix-forward causes — fold them into the
same commit as the original change, not a follow-up PR:

- **When a count changes, refresh its generated surfaces in the same change.**
  Compare documentation against the authoritative registry in tests; a second
  hard-coded count can become stale independently. Existing surface and command
  index guards should cover routine additions.
- **Render `changelog.html` immediately after editing `CHANGELOG.md`** via
  `python scripts/build_changelog_html.py --write`. The drift gate hard-fails on stale
  renders, so a "doc-only" CHANGELOG edit will block the PR otherwise.
- **Phantom-annotate when a test pins a missing doc.** Use `skip` / `xfail` with
  a reason and a TODO pointing at the producing task. Silent-pass via
  `if path.exists()` trains future readers to ignore the gate.
- **Add a rationale comment when a canonical list outlives a refactor.** Lists
  like `_COMPOUND_INVOKERS` that reference modules no longer present need a short
  comment (`# kept for historical alias resolution` or `# moved to <path>`) in
  the same commit as the refactor.

## Deploys

Cloudflare Pages goes out by hand (`make site-deploy`; `make site-check`
compares the live changelog against the declared version and fails on
drift -- run it after any release cut). The deployment target refuses a dirty
checkout and records its exact commit. The equivalent direct command is:

```bash
set -eu
site_status="$(git status --porcelain=v1 --untracked-files=all)"
test -z "$site_status"
site_sha="$(git rev-parse --verify HEAD)"
wrangler pages deploy templates/distribution/landing-page \
  --project-name roam-code --branch main --commit-dirty=false --commit-hash="$site_sha"
```

Use a clean, reviewed revision whose required checks passed. After deployment,
verify the served changelog/content and security headers, not just HTTP 200.
The package tag, installed package, and Pages deployment are separate release
surfaces; record each exact version or source commit.

PyPI publishes from a tag (`.github/workflows/publish.yml`). Run the exact
release gate before pushing the version-bump commit:

```bash
python scripts/prepush_check.py --release
release_sha="$(git rev-parse HEAD)"
git push origin main
```

Wait for the commit CI run for `release_sha` to pass. Then prove local `HEAD`
and `origin/main` still name that exact reviewed commit, derive the version from
its committed `pyproject.toml`, and attach the annotated tag to the captured SHA:

```bash
git fetch origin main
test "$(git rev-parse HEAD)" = "$release_sha"
test "$(git rev-parse origin/main)" = "$release_sha"
version="$(python -c 'import sys; toml = __import__("tomllib" if sys.version_info >= (3, 11) else "tomli"); print(toml.load(open("pyproject.toml", "rb"))["project"]["version"])')"
git tag -a "v${version}" "$release_sha" -m "roam-code ${version}"
git push origin "v${version}"
```

Do not create the tag from a dirty tree or before the commit CI run is green.

## PR Guidelines

- One feature or fix per PR
- Include tests for new functionality
- All tests must pass (`pytest tests/`)
- Follow existing code conventions
- Please open an issue first to discuss larger changes

## Testing Tips

- For a detector correction, add both a false-positive regression and a genuine
  positive control. Use generic fixtures in public tests; keep private reports,
  source excerpts, command logs, and dated verification notes under `internal/`.
- Verify the affected CLI behavior and MCP description, then regenerate the
  references listed in the [documentation map](docs/README.md#documentation-authorities).
  Read [detector evidence](docs/concepts/detector-evidence.md) for scope and
  confidence semantics. A passing focused bundle is not a full-suite result.
- Tests create temporary project directories with fixture files
- Use `CliRunner` from Click for command tests
- Mark tests that need sequential execution with `@pytest.mark.xdist_group("groupname")`
- Use `-m "not slow"` to skip timing-sensitive performance tests during development

## Architecture Overview

roam-code is organized into these key areas:

| Directory | Purpose |
|-----------|---------|
| `src/roam/cli.py` | Click CLI entry point with lazy-loaded commands |
| `src/roam/commands/` | One `cmd_*.py` module per CLI command |
| `src/roam/db/` | SQLite schema, connection management, queries |
| `src/roam/index/` | Indexing pipeline: discovery, parsing, extraction, resolution |
| `src/roam/languages/` | One `*_lang.py` per language, inheriting `LanguageExtractor` |
| `src/roam/graph/` | NetworkX graph algorithms (PageRank, SCC, clustering, layers) |
| `src/roam/bridges/` | Cross-language symbol resolution |
| `src/roam/output/` | Formatting, JSON envelopes, SARIF output |
| `src/roam/mcp_server.py` | MCP server with 246 tools (17 in the default `core` preset) |
| `tests/` | Test suite |

For full architectural details, see the [Architecture Guide](https://roam-code.com/docs/architecture).

## Good First Contributions

- Add a Tier 1 language extractor (see `go_lang.py` or `php_lang.py` as templates)
- Improve reference resolution for an existing language
- Add benchmark repos to the test suite
- Extend SARIF converters
- Add MCP tool wrappers for existing commands
- Improve documentation

## Need Help?

- Open an [issue](https://github.com/Cranot/roam-code/issues) for questions
- Check [existing issues](https://github.com/Cranot/roam-code/issues) before creating new ones
- See the [Architecture Guide](https://roam-code.com/docs/architecture) for detailed technical conventions
