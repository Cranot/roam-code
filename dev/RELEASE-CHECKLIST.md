# Release checklist — v12.26

State as of 2026-05-06 (round 2 finished): `pyproject.toml` is **still at 12.25**
(intentional — the assistant did not bump per your instruction "we will bump it
then the version"). All v12.26 work is staged in working-tree but **uncommitted**.

**Round 2 additions** (2026-05-06): +2 CLI commands (`rules-validate`,
`audit-trail-conformance-check`), +2 MCP tools (`roam_rules_validate`,
`roam_audit_trail_conformance_check`), +3 AI-likelihood signals, pr-analyze
`--quiet` / `--cache` / `--cache-dir` / `--parallel` / `--progress` / `--rules-strict`,
audit-trail safety hardening, audit-trail-export `--aggregate`, metrics-push
`--include-pr-analysis`, shared `git_helpers` + `audit_trail_helpers` modules.
**Surface counts: CLI 183 → 185, MCP 133 → 135, core preset 46 → 48.**

The CHANGELOG.md `[Unreleased]` entry is already written; promoting it to
`[12.26] - YYYY-MM-DD` is the only doc change required when bumping.

## Pre-release verification (already done, but re-run if you want to be sure)

> **Lesson from 12.31-12.33**: `_CORE_TOOLS` count appears in **three**
> assertions across **two** test files. Always sweep before tagging:
>
> ```bash
> grep -rn "tool_count\|_CORE_TOOLS" tests/ | grep -E "== [0-9]+"
> grep -rn "len(_CORE_TOOLS) == " tests/
> ```
>
> Same applies to surface counts in CLAUDE.md, README.md, and the
> docs site. `tests/test_surface_counts.py` covers some of these but
> not all (the test asserts source matches docs; not all assertions
> live in there).

```bash
# 1. Surface tests pass (verifies all hardcoded counts agree with source)
pytest tests/test_surface_counts.py tests/test_readme_surface_consistency.py -q

# 2. New-feature tests pass (round 2: ~150 new tests across 11 files)
pytest tests/test_pr_analyze.py tests/test_pr_analyze_edge_cases.py \
       tests/test_pr_analyze_v2_signals.py tests/test_pr_analyze_cache.py \
       tests/test_metrics_push.py tests/test_pr_comment_render.py \
       tests/test_audit_trail_verify.py tests/test_audit_trail_aggregate.py \
       tests/test_audit_trail_conformance.py tests/test_rules_validate.py \
       tests/test_git_helpers.py tests/test_v2_edge_cases.py -q

# 3. Adjacent tests still pass (regression check)
pytest tests/test_pr_diff.py tests/test_critique.py tests/test_pr_risk_author.py -q

# 4. Ruff clean on all v12.26 files (round 2: +6 files)
ruff check src/roam/commands/cmd_pr_analyze.py \
           src/roam/commands/cmd_pr_comment_render.py \
           src/roam/commands/cmd_metrics_push.py \
           src/roam/commands/cmd_audit_trail_verify.py \
           src/roam/commands/cmd_audit_trail_export.py \
           src/roam/commands/cmd_audit_trail_conformance.py \
           src/roam/commands/cmd_rules_validate.py \
           src/roam/commands/git_helpers.py \
           src/roam/commands/audit_trail_helpers.py

# 5. Optional — full test suite (per workflow-rules.md, discuss before running)
# pytest tests/ -m "not slow"
```

## Bump the version

When ready, update `pyproject.toml`:

```diff
-version = "12.25"
+version = "12.26"
```

(No other config changes needed — `__init__.py` reads version dynamically
via `importlib.metadata`.)

## Promote CHANGELOG entry

In `CHANGELOG.md`, change the `## [Unreleased]` heading to
`## [12.26] - 2026-05-06` (or whatever the actual release date is).
Add a new empty `## [Unreleased]` section above it for next round.

## Suggested commit sequence

Working-tree currently has many uncommitted files; suggest splitting into
logical chunks for cleaner history:

```bash
# Chunk 1 — Roam Agent Review CLI engine (the keystone)
git add src/roam/commands/cmd_pr_analyze.py
git add src/roam/commands/cmd_pr_comment_render.py
git add src/roam/cli.py
git add tests/test_pr_analyze.py
git add tests/test_pr_analyze_edge_cases.py
git add tests/test_pr_comment_render.py
git commit -m "feat(agent-review): pr-analyze + pr-comment-render — Roam Agent Review CLI engine"

# Chunk 2 — Roam Cloud Lite CLI engine
git add src/roam/commands/cmd_metrics_push.py
git add tests/test_metrics_push.py
git commit -m "feat(cloud-lite): metrics-push — no-source-code metrics push to Roam Cloud Lite"

# Chunk 3 — EU AI Act audit-trail toolkit
git add src/roam/commands/cmd_audit_trail_verify.py
git add src/roam/commands/cmd_audit_trail_export.py
git add tests/test_audit_trail_verify.py
git commit -m "feat(audit-trail): verify SHA-256 chain integrity + export md/csv/json for procurement"

# Chunk 4 — MCP wrappers for v12.26 commands
git add src/roam/mcp_server.py
git commit -m "feat(mcp): wrap pr-analyze / pr-comment-render / metrics-push / audit-trail-* as MCP tools"

# Chunk 5 — distribution surface + docs
git add README.md CLAUDE.md llms-install.md CHANGELOG.md
git add docs/site/.well-known/mcp-server-card.json
git add src/roam/mcp-server-card.json
git add src/roam/templates/ci/agent-review.yml
git add templates/
git commit -m "docs: v12.26 — Roam Agent Review + Cloud Lite product sections, surface counts, GitHub Actions template"

# Chunk 6 — version bump (do this last)
git add pyproject.toml
git commit -m "release: 12.26 — Roam Agent Review + Cloud Lite engines, EU AI Act audit-trail toolkit"
```

Or if you prefer one giant commit, that's fine too — the `git add .` then
single commit approach matches the existing release commits in the log
(`release: 12.25 — backport QueryCursor shim`).

## Tag the release

```bash
git tag -a v12.26 -m "Roam Agent Review + Cloud Lite engines, EU AI Act audit-trail toolkit"
git push origin main
git push origin v12.26
```

## Build + upload to PyPI

Existing infrastructure (per CHANGELOG history) uses standard
`python -m build` + `twine upload` flow.

```bash
# Clean any old builds
rm -rf dist/ build/ *.egg-info/

# Build sdist + wheel
python -m build

# Verify the artifacts
twine check dist/*

# Upload to PyPI (requires PYPI_TOKEN env var or ~/.pypirc)
twine upload dist/*
```

**v12.26-specific note (round 2)**: this release adds **7 new CLI commands and
7 new MCP tools** total (5 from round 1, 2 from round 2). Verify the wheel
includes all command modules + the 2 shared helpers + the agent-review template:
- `src/roam/commands/cmd_pr_analyze.py`
- `src/roam/commands/cmd_pr_comment_render.py`
- `src/roam/commands/cmd_metrics_push.py`
- `src/roam/commands/cmd_audit_trail_verify.py`
- `src/roam/commands/cmd_audit_trail_export.py`
- `src/roam/commands/cmd_audit_trail_conformance.py` (round 2)
- `src/roam/commands/cmd_rules_validate.py` (round 2)
- `src/roam/commands/git_helpers.py` (round 2 shared helper)
- `src/roam/commands/audit_trail_helpers.py` (round 2 shared helper)
- `src/roam/templates/ci/agent-review.yml` (per
  `[tool.setuptools.package-data]` `roam.templates.ci = ["*"]` already)

```bash
# Quick wheel-content verification
unzip -l dist/roam_code-12.26-py3-none-any.whl | grep -E "(pr_analyze|metrics_push|audit_trail|agent-review|rules_validate|git_helpers)"
```

## GitHub release

```bash
gh release create v12.26 \
    --title "Roam Agent Review + Cloud Lite engines, EU AI Act audit-trail toolkit" \
    --notes-file <(awk '/## \[12.26\]/,/## \[12.25\]/' CHANGELOG.md | head -n -2)
```

Or from the GitHub UI: "Releases → Draft new release → tag v12.26 → paste
the [12.26] section of CHANGELOG.md".

## Post-release sanity check

Verify PyPI install works:

```bash
# In a clean venv
python -m venv /tmp/roam-test
source /tmp/roam-test/bin/activate   # PowerShell: . /tmp/roam-test/Scripts/Activate.ps1
pip install roam-code==12.26
roam --version                        # should print 12.26
roam pr-analyze --help                # should render the help text
roam audit-trail-verify --help        # should render
```

## Things to watch for

- **PyPI versions are immutable** — if the upload has a bug, you can't
  re-upload `12.26`; you bump to `12.26.1` and re-publish. (Per the v11
  experience documented in MEMORY.md.)
- **Surface-count tests will keep CI honest** — if any hardcoded count
  drifted between source and docs, CI fails before publish.
- **fastmcp is in dev deps but not runtime deps** — the MCP server is
  optional (`pip install "roam-code[mcp]"`). The 5 new MCP wrappers
  share that gating.
- **Don't push to PyPI from a dirty working tree** — twine will publish
  whatever's in `dist/`, even if it's stale. Always `rm -rf dist/`
  before `python -m build`.

## What this release is

> v12.26 — Roam Agent Review + Cloud Lite engines, EU AI Act
> audit-trail toolkit. 5 new CLI commands, 5 new MCP tools, complete
> sub-stack for v2 monetization plan: structural-risk PR verdict,
> metrics-only Cloud Lite push, Article 12 audit trail with SHA-256
> chain integrity, drift detection, multi-language AI-likelihood
> scoring. License confirmed Apache 2.0.

That's the canonical one-line release description if you want it for
the GitHub release / PyPI summary / X announcement.
