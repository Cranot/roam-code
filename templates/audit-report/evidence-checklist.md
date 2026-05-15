# Evidence checklist — Agent Governance Evidence Pack

This checklist maps each section of `sample-audit-report.md` to the Roam
commands that produce its evidence. Use it when assembling a real report
for a customer engagement.

All commands run locally against an indexed repository. Re-indexing with
`roam init` before extraction guarantees the artifacts reflect the audit
period's HEAD SHA.

## 1. Which agents changed what

| Artifact | Command | Notes |
|---|---|---|
| Per-agent run counts | `roam --json runs list --since <YYYY-MM-DD>` | Group rows by the `agent` field. |
| Per-run narrative | `roam replay <run_id>` | Human-readable reconstruction. |
| Affected-symbol fan-out | `roam --json pr-bundle show <run_id>` | Use `affected_symbols[].blast_radius`. |

## 2. Context each agent read before changing code

| Artifact | Command | Notes |
|---|---|---|
| Pre-edit commands run | `roam --json pr-bundle show <run_id>` | `context_read.commands_run` array. |
| Symbols inspected | same | `context_read.symbols_inspected` array. |
| Files inspected | same | `context_read.files_inspected` array. |
| "Did the agent preflight?" rollup | grep `context_read.commands_run` for `preflight` / `impact` / `describe` | One-line summary across runs. |

## 3. Risks accepted vs. mitigated

| Artifact | Command | Notes |
|---|---|---|
| Per-bundle risk list | `roam --json pr-bundle show <run_id>` | `risks[]` array; each entry has `id`, `severity`, `description`, optional `rationale`. |
| Risk classes across the period | aggregate `risks[].id` prefixes | E.g. `side_effect_*`, `causal_diff_*`, `blast_radius_high`. |
| Test-mitigation linkage | cross-reference `risks[]` with `tests_run[]` | Risks whose `description` references a symbol that also appears in a passed test. |

## 4. Who authorized risky edits

| Artifact | Command | Notes |
|---|---|---|
| Mode transitions | `roam --json runs show <run_id>` | Filter events where `action == "mode-switch"`. |
| Authorizer field | same | Free-form string set by the agent / human at switch time. |
| Active mode at any moment | `roam mode` | Current mode only; history is in the ledger. |
| Chain integrity | `roam --json runs verify <run_id>` | HMAC-chain verification — non-zero exit on tamper. |

## 5. Which tests closed the loop

| Artifact | Command | Notes |
|---|---|---|
| Required tests | `roam --json pr-bundle show <run_id>` | `tests_required[]`. |
| Tests actually executed | same | `tests_run[]`, each entry has `passed`, `duration_ms`. |
| Structural completeness gate | `roam pr-bundle validate --strict` | Non-zero exit if `tests_run` is empty when `tests_required` is non-empty. |

## 6. Compliance evidence mapping

The mapping table in the sample report is illustrative — adapt it to the
framework the customer is being assessed against. Roam does not assert
applicability of any specific control.

## 7. Agent quality signal (optional addendum)

| Artifact | Command | Notes |
|---|---|---|
| Per-agent composite score | `roam --json agent-score --since <YYYY-MM-DD>` | 0–100 composite of completion rate, clean-signal rate, breadth. |
| Score formula | same | The envelope's `score_formula` field documents the computation; include verbatim in the report appendix if the customer wants the math. |

## Production tips

- Run `roam runs verify` for every run before extracting evidence. A
  failing chain check should be reported as a section-level red flag, not
  silently elided.
- Bundles live under `.roam/pr-bundles/`; the ledger lives under
  `.roam/runs/`. Both are repo-local plain JSON and can be archived
  alongside the report PDF for the customer's records.
- The `--json` envelopes are stable across point releases and include a
  `schema_version` field; pin the Roam version in the report appendix so
  re-runs are reproducible.
