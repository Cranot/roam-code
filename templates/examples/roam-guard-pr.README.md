# Roam Guard for PRs — adoption guide

`roam guard-pr` is the aggregate CLI that wraps the Roam Guard pipeline
(auto-collect → AgentChangeProofBundle v1 → render → optional GitHub
Check Run) into one call.

## Quickstart — local

```bash
# 1. Initialize a pr-bundle on your feature branch:
roam pr-bundle init --intent "fix auth retry leak"

# 2. (Optional) populate it with the usual agent commands.
#    Each writes a response envelope to .roam/responses/.
roam preflight refresh_token
roam impact refresh_token
roam critique  # if you have a diff staged

# 3. Run the aggregate verdict:
roam guard-pr --format markdown --output verdict.md
cat verdict.md   # reviewer-readable summary

# 4. CI gate locally:
roam guard-pr --strict  # exit 5 if blocked, 4 if needs_review, 0 if pass
```

## Quickstart — CI

Five drop-in templates ship; pick the one matching your CI provider:

| Provider | Template |
|---|---|
| GitHub Actions | `roam-guard-pr.github-actions.yml` → `.github/workflows/roam-guard.yml` |
| GitLab CI | `roam-guard-pr.gitlab-ci.yml` → `.gitlab-ci.yml` |
| Bitbucket Pipelines | `roam-guard-pr.bitbucket-pipelines.yml` → `bitbucket-pipelines.yml` |
| CircleCI | `roam-guard-pr.circleci.yml` → `.circleci/config.yml` |
| Jenkins | `roam-guard-pr.jenkinsfile` → `Jenkinsfile` (declarative pipeline) |

### GitHub Actions example

Copy `roam-guard-pr.github-actions.yml` to `.github/workflows/roam-guard.yml`.
On every PR push the action will:

1. Check out the PR head.
2. Run `roam guard-pr --ci` (equivalent to `--strict --init-if-missing --format markdown`).
3. POST the markdown verdict to GitHub Check Runs API.
4. Fail the build if the verdict is `blocked`.

The `--ci` preset is the right default for CI workflows. Override individual
flags if you need finer control:

| Flag | Default | Purpose |
|---|---|---|
| `--strict` | off | Exit 5 on blocked, 4 on needs_review |
| `--init-if-missing` | off | Bootstrap an empty bundle if none exists |
| `--format markdown\|json\|text` | text | Output format |
| `--ci` | off | Implies `--strict --init-if-missing --format markdown` |
| `--post-check` | off | POST to GitHub Check Runs API |
| `--skip-collect` | off | Skip auto-collect (use existing bundle as-is) |

## How the verdict is computed

```
changed_files × risk × mode × policy
        │
        ├──→ command_graph (G2 — "what CAN be run")
        │
        └──→ verification_contract (G3 — "what MUST run")
                │
                └──→ executed_checks (from bundle.tests_run)
                       │
                       └──→ verdict (closed enum: pass / pass_with_warnings / needs_review / blocked)
```

The verdict engine emits a closed-enum result with machine-readable reason
codes (`required_check_not_run`, `high_risk_path`, `optimizer_warning`, ...)
that CI / dashboards can act on programmatically.

## Verdict → GitHub Check conclusion mapping

| Roam verdict | GitHub conclusion | Build status |
|---|---|---|
| `pass` | `success` | ✅ green |
| `pass_with_warnings` | `neutral` | 🟡 yellow |
| `needs_review` | `action_required` | 🟠 attention |
| `blocked` | `failure` | 🛑 red |

## Policy profiles

| Profile | Floor |
|---|---|
| `startup` (default) | File-pattern rules only (auth/migration/public-API trigger required tests) |
| `regulated` | Tests required on every change |

Set via `--policy-profile`. Choose `regulated` for compliance-sensitive repos.

## Modes

`--mode` controls what the agent is allowed to do:

- `read_only` — analysis only, no edits
- `safe_edit` (default) — bounded edits
- `migration` — schema/data migration
- `autonomous_pr` — full PR authoring

Mode flows through verification_contract to drive risk-adjusted requirements.

## Files generated

- `.roam/pr-bundles/<branch>.json` — the legacy pr-bundle (incremental writer)
- `verdict.md` (or `--output PATH`) — reviewer-readable markdown
- GitHub Check Run (when `--post-check` is set) — surfaced on the PR
