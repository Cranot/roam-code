# Example workflow templates

Small operational templates you can copy into a repository, issue, PR
description, or team runbook. These are lighter than the report templates
under `templates/audit-report/` and `templates/services-reports/`: they are
meant to shape day-to-day Roam usage.

## Files

| File | Use |
|---|---|
| `agent-change-packet.md` | Issue / PR template for an AI-assisted code change. Forces the agent to collect Roam context, preflight evidence, blast radius, tests, and post-edit proof before asking for review. |
| `pre-commit-stale-refs.yaml` | Local pre-commit hook that gates newly broken markdown links, HTML hrefs, backtick paths, and anchors. |
| `post-merge-stale-refs.sh` | Git hook for refreshing stale-reference baselines after a merge. |
| `.roam-rules.yml` | Example architecture rules for `roam pr-analyze` (banned imports, calls, base classes, decorators). |
| `smells.suppress.yml` | Allowlist for `roam smells` findings (W658). Drop at `.roam/smells.suppress.yml` to suppress intentional smells by `kind` + `symbol` with optional `expires`. |
| `.roamignore-findings` | Rule-based allowlist for `roam math` / `over-fetch` / `missing-index` / `auth-gaps` findings (W706). Drop at `.roamignore-findings` (repo root, extensionless YAML) to suppress findings in bulk by `task_id` + `path_glob`. |
| `suppressions.json` | Per-finding-hash allowlist for `roam suppress` (W691). Drop at `.roam/suppressions.json` to record audit-trail-friendly carve-outs keyed by the 16-char finding_id sha256. The SARIF projection consumes the same file via optional `rule_id` + `location` fields. |
| `.roam-suppressions.yml` | Triage allowlist for `roam triage` (W692). Drop at `.roam-suppressions.yml` (repo root) to suppress findings by `rule` + `file` (`+ line` optional) with explicit `status` (safe / acknowledged / wont-fix). |

## Recommended use

Start with `agent-change-packet.md` for high-risk or agent-authored
changes. Put it in an issue body, PR description, or `.github/PULL_REQUEST_TEMPLATE.md`
variant. The packet is deliberately command-first: every required claim names
the Roam command that produces it.
