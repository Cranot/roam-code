# Evidence checklist — reproduce the artifact extraction

This checklist is the canonical reference for reproducing the evidence
pipeline behind both customer-facing samples:

- [`sample-pr-replay-team.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-pr-replay-team.md)
  (PR Replay — merged-history detector replay, no run ledger).
- [`sample-audit-report.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-audit-report.md)
  (Governance Evidence Pack — `runs` + `pr-bundle` + `replay` evidence).

Each row names a Roam command, the on-disk artifact it produces, and the
`ChangeEvidence` field a downstream renderer reads. Run the commands
against an indexed repository; re-index with `roam init` before
extraction so artifacts reflect the audit period's HEAD SHA.

This checklist **supports evidence for** AI-agent change-management
controls and **maps to** SOC 2 CC8.1, ISO/IEC 42001, NIST AI RMF, and
EU AI Act Article 12 record-keeping. It does not certify compliance with
any framework — the conformity assessment remains with the buyer.

## The eight evidence questions

Every Roam customer-facing report answers some subset of the eight
axes the agentic-assurance crosswalk asks about an AI-assisted code
change. The table below maps each question to the command(s) that
produce it and the `ChangeEvidence` field a downstream renderer reads.

| # | Question | Command(s) | `ChangeEvidence` field |
|---|---|---|---|
| Q1 | Who acted? | `roam --json runs show <run_id>`; `roam --json runs list --since <YYYY-MM-DD>` | `actor_refs[]`, `run_ids[]`, `agent_id` |
| Q2 | What authority existed? | `roam mode`; `roam --json permit list`; `roam --json lease list`; `roam --json runs show <run_id>` (filter `mode-switch` events) | `authority_refs[]`, `mode`, `permits[]`, `leases[]`, `policy_decisions[]` |
| Q3 | What context was read? | Inspect `.roam/pr-bundles/<run_id>.json` → `context_read.{commands_run, symbols_inspected, files_inspected}` | `context_files[]`, `context_commands[]` |
| Q4 | What changed? | `roam --json diff`; `roam --json pr-risk`; inspect `.roam/pr-bundles/<run_id>.json` → `affected_symbols[]` + `diff_hash` | `commit_sha`, `git_range`, `diff_hash`, `changed_subjects[]` |
| Q5 | What could break? | `roam --json preflight <sym>`; `roam --json impact <sym>`; `roam --json critique` | `findings[]`, `risk_level` |
| Q6 | What policy applied? | `roam --json runs show <run_id>` (filter `policy_decision` events); `roam constitution show` | `policy_decisions[]`, `rules_config_hash`, `constitution_hash` |
| Q7 | What verified it? | Inspect `.roam/pr-bundles/<run_id>.json` → `tests_required[]` vs `tests_run[]`; `roam pr-bundle validate --strict` | `tests_required[]`, `tests_run[]` |
| Q8 | Who accepted risk? | `roam --json runs show <run_id>` (filter `mode-switch` + `accepted-risk` events); inspect bundle `approvals[]` + `accepted_risks[]` | `approvals[]`, `accepted_risks[]` |

Where an axis cannot be produced by Roam alone (e.g., a human approval
recorded only in Slack), the collector emits
`redactions[].reason = "producer_not_available"` rather than synthesising
one. Producer gaps are listed in `CLAUDE.md` under "Pipeline coverage +
sealed producer gaps".

## Sections to extract for `sample-audit-report.md`

Section numbers match the Governance Pack sample. Each row pairs the
artifact with the command that produces it.

### 1. Which agents changed what

| Artifact | Command | Notes |
|---|---|---|
| Per-agent run counts | `roam --json runs list --since <YYYY-MM-DD>` | Group rows by the `agent` field. |
| Per-run narrative | `roam replay <run_id>` | Human-readable reconstruction; reads the ledger at `.roam/runs/<run_id>/`. |
| Affected-symbol fan-out | Inspect `.roam/pr-bundles/<run_id>.json` → `affected_symbols[].blast_radius` | Bundles are plain JSON; use `jq` or `python -m json.tool`. |

### 2. Context each agent read before changing code

| Artifact | Command | Notes |
|---|---|---|
| Pre-edit commands run | Inspect `.roam/pr-bundles/<run_id>.json` → `context_read.commands_run` | Array of `roam ...` strings recorded inside the run. |
| Symbols inspected | same file → `context_read.symbols_inspected` | Array of symbol names. |
| Files inspected | same file → `context_read.files_inspected` | Array of repo-relative paths. |
| "Did the agent preflight?" rollup | Grep `context_read.commands_run` for `preflight` / `impact` / `describe` | One-line rollup across runs in the period. |

### 3. Risks accepted vs mitigated

| Artifact | Command | Notes |
|---|---|---|
| Per-bundle risk list | Inspect `.roam/pr-bundles/<run_id>.json` → `risks[]` | Each entry carries `id`, `severity`, `description`, optional `rationale`. |
| Risk classes across the period | Aggregate `risks[].id` prefixes | E.g. `side_effect_*`, `causal_diff_*`, `blast_radius_high`. |
| Test-mitigation linkage | Cross-reference `risks[]` with `tests_run[]` | Risks whose `description` names a symbol that also appears in a passed test. |

### 4. Who authorized risky edits

| Artifact | Command | Notes |
|---|---|---|
| Mode transitions | `roam --json runs show <run_id>` | Filter events where `action == "mode-switch"`. |
| Authorizer field | same | Free-form string set by the agent / human at switch time. |
| Active mode right now | `roam mode` | Current mode only; full history lives in the ledger. |
| Chain integrity | `roam --json runs verify <run_id>` | HMAC-chain verification — non-zero exit on tamper. |
| Active permits | `roam --json permit list` | Permits scope what an agent may read / write. |
| Active leases | `roam --json lease list` | Multi-agent coordination claims. |

### 5. Which tests closed the loop

| Artifact | Command | Notes |
|---|---|---|
| Required tests | Inspect `.roam/pr-bundles/<run_id>.json` → `tests_required[]` | Names of the tests the agent declared as required. |
| Tests actually executed | same file → `tests_run[]` | Each entry carries `passed`, `duration_ms`. |
| Structural completeness gate | `roam pr-bundle validate --strict` | Non-zero exit when `tests_run[]` is empty while `tests_required[]` is non-empty. Add `--strict-resolved` (or `--ci` / `ROAM_CI=1`) to also gate on unresolved blast-radius symbols. |

### 6. Compliance evidence mapping

Roam ships a control map at
[`src/roam/templates/audit_report/control-mapping.yaml`](https://github.com/Cranot/roam-code/blob/main/src/roam/templates/audit_report/control-mapping.yaml).
The schema, the worked example, the wording-discipline rules ("maps to"
/ "supports evidence for" / "audit-ready record"), and the v0 → v1
migration notes live in
[`control-mapping-README.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/control-mapping-README.md).
Use that file as the canonical reference when adapting the mapping to
the framework a customer is being assessed against; do not hand-edit
the table in `sample-audit-report.md` without first updating the YAML.

### 7. Agent quality signal (optional addendum)

| Artifact | Command | Notes |
|---|---|---|
| Per-agent composite score | `roam --json agent-score --since <YYYY-MM-DD>` | 0–100 composite of completion rate, clean-signal rate, breadth. |
| Score formula | same envelope → `score_formula` field | Document the math verbatim in the report appendix when the customer wants it. |

## Sections to extract for `sample-pr-replay-team.md`

PR Replay reads merged git history only — no run ledger, no bundles,
no mode / permit / lease evidence. The pack answers Q4 (changed) and
Q5 (could break) with full coverage; Q1-Q3 / Q6-Q8 are explicitly out
of scope (the sample marks them under "What this report does *not*
cover" and the eight-question table).

| Artifact | Command | Notes |
|---|---|---|
| Per-PR diff replay | `roam postmortem --range <HEAD~30..HEAD>` | Walks the range commit-by-commit. |
| Detector findings per diff | `roam --json critique` (per range) | Returns exit 5 on any high-severity finding. |
| Cross-PR clone classes | `roam clones --persist` then `roam --json critique` | Persisting clone pairs lets `critique` flag clone-not-edited cases on every diff. |
| Detector breakdown table | Aggregate `findings[].kind` across the replay | One row per detector class. |

## Substrate (where the evidence compiler reads from)

The pipeline above is implemented as a thin layer over four substrate
locations. The customer-facing exporters are **projections** — they
read from `ChangeEvidence`, never from the raw SQLite graph (the
canonical mandate in `CLAUDE.md`).

| Substrate | On-disk location | Source module |
|---|---|---|
| Run ledger (HMAC-chained events) | `.roam/runs/<run_id>/` | `src/roam/runs/` |
| Proof bundles | `.roam/pr-bundles/<run_id>.json` | `src/roam/commands/cmd_pr_bundle.py` |
| Findings registry (cross-detector rows) | SQLite `findings` table | `src/roam/db/findings.py` |
| Evidence compiler (collector + dataclasses) | in-memory `ChangeEvidence` | `src/roam/evidence/` |

The `ChangeEvidence` dataclass + the closed enumerations
(`SUBJECT_KINDS`, `LINK_KINDS`, `ARTIFACT_KINDS`,
`PROVENANCE_SOURCES`, …) live under `src/roam/evidence/`. Run
`roam --json pr-bundle emit` to produce a bundle the collector
can then compile into an evidence packet.

## Production tips

- **Run `roam --json runs verify <run_id>` for every run before extracting evidence.**
  A failing chain check is a section-level red flag, not silent elision.
- **Archive both substrates alongside the report.** Bundles
  (`.roam/pr-bundles/`) and the ledger (`.roam/runs/`) are repo-local
  plain JSON — package them with the report PDF for the customer's
  records.
- **Pin the Roam version in the report appendix.** The `--json`
  envelopes are stable across point releases and include
  `schema_version`; pinning the producing version makes re-runs
  reproducible. The collector also stamps `rules_config_hash`,
  `constitution_hash`, and `control_map_hash` onto every packet so
  drift between "what produced this" and "what's on disk at audit
  time" is detectable.
- **Use `roam findings list --detector <name>` for per-detector
  drill-down.** 28 detectors can persist findings to the central
  registry (registry holds rows from each detector's most recent run;
  counts are last-run state, not cumulative). The same rows feed the
  customer report and any SARIF / OSCAL projection.
- **Mark producer gaps explicitly.** Where an axis cannot be produced
  (no approval recorded, no permit issued, no test run), surface
  `redactions[].reason = "producer_not_available"` rather than
  synthesising a value.

---

_Apache 2.0. Ships with the open-source CLI
([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code)).
Sample reports this powers:
[`sample-pr-replay-team.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-pr-replay-team.md),
[`sample-audit-report.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-audit-report.md).
Control mapping reference:
[`control-mapping-README.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/control-mapping-README.md)._
