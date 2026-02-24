# CI Integration

roam-code provides a composite GitHub Action for automated code analysis on
every pull request. No Docker build, no API keys, sub-60s PR analysis.

## Quick Start

Copy this workflow into your repository at `.github/workflows/roam.yml`:

```yaml
name: roam-code Analysis

on:
  pull_request:
    branches: [main, master]
  push:
    branches: [main, master]

permissions:
  contents: read
  pull-requests: write
  security-events: write  # Required for SARIF upload

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: cosmohac/roam-code@main
        with:
          commands: 'health pr-risk'
          sarif: 'true'
          comment: 'true'
          gate: 'health_score>=60'
```

That is all you need. The action installs roam-code, indexes your codebase,
runs the requested analysis commands, posts a sticky PR comment with results,
uploads SARIF findings to GitHub Code Scanning, and enforces quality gates.

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `version` | `latest` | roam-code version to install from PyPI. Use a pinned version for reproducibility (e.g., `10.0.1`). |
| `commands` | `health` | Space-separated roam commands to run. Each command produces JSON output that feeds into the PR comment and quality gate. |
| `changed-only` | `false` | Incremental CI mode. Adapts supported commands to changed files and transitive dependents (when detectable). |
| `changed-depth` | `3` | Dependency depth used when computing changed+dependent file scope in `changed-only` mode. |
| `base-ref` | _(auto)_ | Optional explicit base ref/SHA for incremental mode. Default is PR base SHA (or push `before` SHA). |
| `sarif` | `false` | When `true`, exports SARIF for the selected SARIF command set and uploads a guarded combined SARIF file to GitHub Code Scanning. Requires `security-events: write` permission. |
| `sarif-commands` | `auto` | Space-separated commands to export via `--sarif`. `auto` picks the SARIF-capable subset of `commands` (`health`, `dead`, `complexity`, `rules`, `secrets`, `algo`). |
| `sarif-category` | `roam-code` | Base SARIF upload category. The action appends job/runtime suffixes to reduce collisions. |
| `sarif-max-runs` | `20` | Pre-upload guardrail: maximum runs kept in combined SARIF. Extra runs are dropped from the tail with a warning. |
| `sarif-max-results` | `25000` | Pre-upload guardrail: maximum results per run. Extra results are dropped from the tail with a warning. |
| `sarif-max-bytes` | `10000000` | Pre-upload guardrail: maximum SARIF JSON bytes (conservative cap before upload). |
| `comment` | `true` | When `true` and running on a pull request, upserts one marker-managed sticky PR comment (idempotent) and removes duplicate sticky comments if they exist. Requires `pull-requests: write` permission. |
| `gate` | _(empty)_ | Quality gate expression. Supports scalar checks (`key>=value`) and trend-aware functions (`velocity(metric)<=0`, `direction(metric)!=worsening`). The action exits with code 5 when the gate fails. |
| `cache` | `true` | Cache pip packages and the `.roam/` SQLite index between runs for faster incremental analysis. |
| `python-version` | `3.11` | Python version to use. Supports 3.9 through 3.13. |

## Outputs

| Output | Description |
|--------|-------------|
| `health-score` | The health score (0-100) if the `health` command was included. |
| `exit-code` | The exit code from analysis. `0` = success, `5` = gate failure, `1` = error. |
| `sarif-file` | Path to the generated SARIF file (when `sarif` is `true`). |
| `sarif-category` | Resolved category used for SARIF upload. |
| `sarif-truncated` | `true` when SARIF guardrails dropped runs/results before upload. |
| `sarif-results` | Final SARIF result count after guardrails. |
| `changed-only` | Whether incremental mode was enabled. |
| `base-ref` | Resolved base ref/SHA used for incremental mode. |
| `affected-count` | Number of changed+dependent files detected for incremental mode. |

## Quality Gates

Quality gates let you enforce minimum standards on every PR. The gate
expression is evaluated against the JSON summary of each analysis command.

### Gate expression syntax

```
key operator value
```

Where:
- `key` is any field in the JSON summary (e.g., `health_score`, `tangle_ratio`, `risk_score`, `issue_count`)
- `operator` is one of: `>=`, `<=`, `>`, `<`, `=`
- `value` is a number

Trend-aware functions are also supported:

- `latest(metric)` — latest value from trend payloads
- `delta(metric)` — first-to-last change over the trend window
- `slope(metric)` — per-snapshot slope
- `velocity(metric)` — worsening velocity (positive means trending worse)
- `direction(metric)` — semantic direction (`improving`, `worsening`, `stable`)

You can combine multiple expressions with commas:

```yaml
gate: 'health_score>=70,velocity(cycle_count)<=0'
```

### Examples

```yaml
# Require health score of at least 70
gate: 'health_score>=70'

# Require tangle ratio below 5%
gate: 'tangle_ratio<=5'

# Require zero critical issues
gate: 'issue_count=0'

# Fail if cycle count is accelerating in a bad direction
gate: 'velocity(cycle_count)<=0'

# Require trend direction to avoid worsening health
gate: 'direction(health_score)!=worsening'
```

### Gate failure behavior

When a gate fails, the action:
1. Prints an error annotation with the actual vs required value
2. Exits with code **5** (distinct from code 1 for crashes)
3. Marks the check as failed in the PR

The PR comment will show the gate result as `PASSED` or `FAILED`.

## SARIF Integration

When `sarif: 'true'`, the action:
1. Generates SARIF per command from `sarif-commands` (or `auto` subset)
2. Merges SARIF runs into one payload
3. Applies upload guardrails (`sarif-max-runs`, `sarif-max-results`, `sarif-max-bytes`)
4. Uploads via `github/codeql-action/upload-sarif` using resolved `sarif-category`
5. Emits truncation warning metadata when guardrails drop findings
6. Results appear in the **Security** tab under **Code scanning alerts**

This requires the `security-events: write` permission and GitHub Advanced
Security (free for public repositories).

### Guardrail Notes

- `sarif-max-runs` and `sarif-max-results` align with documented GitHub SARIF
  scale constraints.
- `sarif-max-bytes` is a conservative pre-upload byte cap to reduce failed
  uploads on large payloads.
- If truncation occurs, use `sarif-truncated` and `sarif-results` outputs to
  surface that in downstream CI steps.

### SARIF rule categories

roam-code generates SARIF results for:
- `health/cycle` -- Dependency cycles
- `health/god-component` -- Components with excessive coupling
- `health/bottleneck` -- High-betweenness bottleneck symbols
- `health/layer-violation` -- Architectural layer violations

## Caching

When `cache: 'true'` (default), the action caches:

1. **pip packages** -- Keyed on OS, Python version, and roam-code version.
   Avoids re-downloading roam-code and its dependencies on every run.

2. **`.roam/` directory** -- The SQLite index database. Keyed on OS and a
   hash of all source files (`*.py`, `*.js`, `*.ts`, `*.go`, etc.). When
   source files change, the cache misses and `roam init` rebuilds only the
   changed files (incremental indexing).

Cache hits reduce analysis time from 30-60s to under 10s on typical
codebases.

## Changed-only Mode

Set `changed-only: 'true'` to run incremental PR analysis.

- The action resolves a base ref (PR base SHA by default) and computes changed
  plus transitive dependent files via `roam affected`.
- Supported commands are auto-adapted:
  - `verify`, `syntax-check`, `test-gaps`, `suggest-reviewers`, `file` get the
    affected file set.
  - `pr-risk`, `pr-diff`, `semantic-diff`, `affected`, `api-changes` get
    base/range aware flags.
- Unsupported commands still run in normal full-repo mode.

Example:

```yaml
- uses: cosmohac/roam-code@main
  with:
    commands: 'verify pr-risk api-changes'
    changed-only: 'true'
    changed-depth: '3'
```

## Commands Reference

Any roam command can be passed via the `commands` input. Common choices:

| Command | What it does |
|---------|-------------|
| `health` | Overall health score (0-100), cycles, god components, bottlenecks |
| `pr-risk` | PR risk score based on changed files, blast radius, coupling |
| `complexity` | Cognitive complexity analysis with severity ratings |
| `dead` | Dead code detection (unreferenced exports) |
| `debt` | Technical debt inventory |
| `fitness` | Fitness function evaluation against project rules |
| `breaking` | Detect breaking API changes |
| `conventions` | Naming convention violations |

Run `roam --help` for all 96 commands.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success -- analysis completed, no gate failures |
| 1 | Error -- unexpected failure or crash |
| 2 | Usage error -- invalid arguments or flags |
| 3 | Index missing -- `roam init` not run (should not happen with the action) |
| 5 | Gate failure -- quality gate check failed |

## Advanced Examples

### Multiple commands with strict gate

```yaml
- uses: cosmohac/roam-code@main
  with:
    commands: 'health complexity dead'
    sarif-commands: 'health complexity dead'
    sarif-category: 'roam-code-pr-${{ github.ref_name }}-${{ matrix.python-version }}'
    gate: 'health_score>=80'
    sarif: 'true'
```

### PR risk only, no comment

```yaml
- uses: cosmohac/roam-code@main
  id: roam
  with:
    commands: 'pr-risk'
    comment: 'false'

- name: Check risk score
  if: steps.roam.outputs.exit-code != '0'
  run: echo "Analysis found issues (exit ${{ steps.roam.outputs.exit-code }})"
```

### Pinned version without caching

```yaml
- uses: cosmohac/roam-code@v10.0.1
  with:
    version: '10.0.1'
    cache: 'false'
    commands: 'health'
```

### Use outputs in subsequent steps

```yaml
- uses: cosmohac/roam-code@main
  id: analysis
  with:
    commands: 'health'

- name: Report health score
  run: echo "Health score is ${{ steps.analysis.outputs.health-score }}"
```

## Troubleshooting

### "Permission denied" on SARIF upload

Add `security-events: write` to your workflow permissions:

```yaml
permissions:
  security-events: write
```

### Comment not appearing on PRs

Add `pull-requests: write` to your workflow permissions:

```yaml
permissions:
  pull-requests: write
```

### Slow first run

The first run indexes the entire codebase. Subsequent runs with `cache: 'true'`
will restore the cached index and only re-index changed files. Typical
improvement: 30-60s down to under 10s.

### Gate expression not matching

The gate key must exactly match a field in the JSON summary. Run
`roam --json health` locally to see available fields:

```bash
roam --json health | python3 -m json.tool | grep -A5 '"summary"'
```
