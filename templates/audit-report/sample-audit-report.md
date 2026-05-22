# AI-Agent Change Audit Report — Example Co.

**Report period:** 2026-04-01 to 2026-04-30
**Repository:** `example-co/checkout-service`
**Auditor:** Example Co. AI Risk Office (internal)
**Tool:** `roam runs` + `roam pr-bundle` + `roam replay` (Apache 2.0, local-only)
**Schema:** `governance-pack/1.0`

> **Sample report — illustrative, not real customer data.** Buyer name,
> agent runs, run ids, authorizer emails, PR numbers, and finding
> counts below are representative of the shape a paid Governance
> Evidence Pack engagement produces. Run the open-source CLI on your
> own repo to generate the same artifacts, or email
> <hello@roam-code.com> to commission a written pack against your
> last 30 or 90 agent runs.

This report **supports evidence for** AI-agent change-management
controls and **maps to** SOC 2 CC8.1, ISO/IEC 42001, NIST AI RMF, and
EU AI Act Article 12 record-keeping expectations. It does not certify
compliance with any framework — the conformity assessment remains
with the buyer and their qualified counsel or auditors.

---

## Executive summary

**Verdict:** 38 of 47 runs (81%) emitted a signed `pr-bundle`; 29 merged with a verified ledger chain — **2 open governance gaps**.

- Runs opened: **47**
- PR-bundles emitted: **38**
- Bundles merged to `main`: **29**
- `roam runs verify` chain integrity: **29 / 29 passed**
- Pre-edit context recorded (`roam preflight` / `impact` / `describe`): **36 / 38 bundles**
- High-severity edits invoking `migration` / `autonomous_pr` mode override: **3** (all 3 name a human authorizer)
- Open gaps: **2 merged PRs** declared `tests_required[]` but emitted with empty `tests_run[]` (Section 5)

## The eight evidence questions

This pack answers all eight axes the agentic-assurance crosswalk asks
about an AI-assisted code change. Per-axis coverage on this
engagement:

| Question | Coverage on this report |
|---|---|
| **Who acted?** | Section 1. Per-run agent identity + git author + MCP client id from `roam runs show`. |
| **What authority existed?** | Section 4. `mode` per run, lease holder, policy decisions, and human authorizer per mode override. |
| **What context was read?** | Section 2. `pr-bundle.context_read` — commands, symbols, files inspected before the first edit event. |
| **What changed?** | Section 1 affected_symbols mean + Section 5 per-PR diff hashes. |
| **What could break?** | Section 3. Risks classified by R28 world-model kind (`side_effect_*`, `causal_diff_*`, `blast_radius_high`, `clones_not_edited`, `layer_violation`). |
| **What policy applied?** | Section 4. Default Roam constitution plus `migration` / `autonomous_pr` overrides per run. |
| **What verified it?** | Section 5. `tests_required[]` vs `tests_run[]` reconciliation + CI logs cross-referenced by SHA. |
| **Who accepted risk?** | Section 4. `authorizer` field on every accepted risk + every mode override. (For runs where no human approval was recorded, the bundle emits `redactions[].reason = "producer_not_available"`.) |

## 1. Which agents changed what

Source: `roam runs list --json` plus `roam replay <run_id>` for narrative reconstruction.

| Agent | Runs opened | Runs completed | PR-bundles emitted | Merged | Mean affected_symbols |
|---|---:|---:|---:|---:|---:|
| `claude-code` | 22 | 21 | 19 | 16 | 7.4 |
| `cursor` | 14 | 13 | 11 | 9 | 4.1 |
| `human-pair` | 8 | 8 | 6 | 3 | 12.7 |
| `aider` | 3 | 2 | 2 | 1 | 2.0 |

The `affected_symbols` column is the mean size of the
`pr-bundle.affected_symbols[]` array for completed runs. Higher
values are not inherently risky — they correlate with refactor-shaped
work — but combined with Section 3 they help identify which agents
took on the largest blast-radius changes.

## 2. Context each agent read before changing code

Source: Inspect `.roam/pr-bundles/<run_id>.json` (with `jq` or
`python -m json.tool`) — specifically
`context_read.commands_run`, `context_read.symbols_inspected`, and
`context_read.files_inspected`.

Roam records every analysis command an agent ran inside a run, so the
audit trail answers "did the agent look before it leapt?" without
re-asking the agent. Representative bundle excerpt — run
`2026-04-12T09:14:02Z-claude-code` (illustrative; not from a real run):

```json
{
  "intent": "Add idempotency key to /charge endpoint",
  "context_read": {
    "commands_run": [
      "roam preflight charge_handler",
      "roam impact charge_handler",
      "roam describe payment_gateway"
    ],
    "symbols_inspected": ["charge_handler", "PaymentGateway.charge", "IdempotencyStore"],
    "files_inspected": ["src/api/charge.py", "src/payments/gateway.py"]
  },
  "affected_symbols": [
    {"name": "charge_handler", "file": "src/api/charge.py", "blast_radius": 11}
  ]
}
```

Aggregate finding for the month: **36 / 38 emitted bundles** record
at least one of `roam preflight`, `roam impact`, or `roam describe`
in `commands_run` before the first edit event. The 2 exceptions are
listed in Section 5 under "tests did not close the loop".

## 3. Risks accepted vs mitigated

Source: Inspect `.roam/pr-bundles/<run_id>.json` (with `jq` or
`python -m json.tool`) — `risks[]` array, classified
by `status` and `severity`.

| Risk class | Mitigated by test | Mitigated by gate / mode | Accepted (documented) | Total |
|---|---:|---:|---:|---:|
| `side_effect_*` (R28 world-model) | 7 | 4 | 2 | 13 |
| `causal_diff_*` (added / removed edges) | 5 | 1 | 1 | 7 |
| `blast_radius_high` | 3 | 6 | 0 | 9 |
| `clones_not_edited` | 2 | 3 | 0 | 5 |
| `layer_violation` | 0 | 4 | 1 | 5 |

"Accepted" risks are not failures — they are documented in the
bundle's `risks[].rationale` field, with a named authorizer in `runs`
events. The 2 accepted `side_effect_*` risks correspond to deliberate
write-path changes in the receipts emailer; both name the on-call
engineer as authorizer (Section 4).

## 4. Who authorized risky edits

Source: `roam runs show <run_id>` — events whose `action` is
`mode-switch` to `migration` or `autonomous_pr`, plus accepted risks
tagged with an `authorizer` field.

| Date | Run id (short) | Agent | Mode invoked | Authorizer | Rationale (excerpt) |
|---|---|---|---|---|---|
| 2026-04-08 | `…claude-code-a31f` | claude-code | `migration` | `j.park@example.co` | "DB column rename, coordinated with deploy window" |
| 2026-04-17 | `…cursor-b88e` | cursor | `migration` | `j.park@example.co` | "Schema split, replayed against staging snapshot" |
| 2026-04-23 | `…human-pair-c12d` | human-pair | `autonomous_pr` | `s.rao@example.co` | "Vendor SDK upgrade, isolated under a feature flag" |

Mode overrides above `safe_edit` produce ledger events of action
`mode-switch`. The audit period contained 3 such events; all 3
include an `authorizer` field. None were issued under `read_only`
(which the substrate physically blocks from mutating bundles).

> _Authorizer emails are illustrative `example.co` addresses. A real
> engagement records the actual `authorizer` field from the run
> ledger; redactions are listed under `redactions[]` per the export
> profile._

## 5. Which tests closed the loop

Source: Inspect `.roam/pr-bundles/<run_id>.json` (with `jq` or
`python -m json.tool`) — comparison of
`tests_required[]` to `tests_run[]`, plus CI logs cross-referenced by
commit SHA.

- 27 of 29 merged runs have a non-empty `tests_run[]` array.
- 25 of those 27 have `tests_run[].passed == true` for every entry.
- 2 of 27 have at least one failed test recorded but documented mitigation.

**Open gap — 2 merged findings.** Two merged PRs (`#1184`, `#1196`)
declared `tests_required` entries but emitted with an empty
`tests_run` list. Both passed CI through unrelated test suites;
neither violates a Roam gate today because the bundle's `validate`
command only checks structural completeness.

## 6. Compliance evidence mapping

This table maps Roam-generated artifacts to common controls so
reviewers can locate the relevant evidence for their own framework.
It is a cross-reference, **not a certification**. See Section 7.

| Roam evidence | Maps to control (representative) |
|---|---|
| `pr-bundle.context_read[]` | SOC 2 CC8.1 (change-management documentation of pre-change review) |
| `runs` ledger (HMAC-chained events) | ISO/IEC 42001 §6.1.3 (AI-system change tracking) |
| `pr-bundle.affected_symbols[]` | EU AI Act Article 12 (record-keeping / activity logs for high-risk systems) |
| `pr-bundle.risks[]` + `tests_run[]` | NIST AI RMF MAP-2.1 (risk identification) and MEASURE-2.7 (test-based risk treatment) |
| `mode` history + `runs` `mode-switch` events | SOC 2 CC6.3 (logical-access change events) and internal AI-policy attestations |
| `roam runs verify` (chain integrity) | NIST AI RMF GOVERN-1.7 (tamper-evident audit trail) |

The mapping cites controls by their commonly-used identifiers.
Whether any particular control applies, and what additional evidence
a qualified auditor would require for that control, is outside Roam's
scope. See [`control-mapping-README.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/control-mapping-README.md)
for the source YAML and the full evidence-to-control crosswalk.

## Recommended next steps

Four actions, ordered by leverage:

1. **Add `roam pr-bundle validate --strict` to CI.** Gates merge on
   structural completeness AND `tests_run[]` reconciliation —
   catches the 2 open gaps from Section 5 pre-merge going forward.
2. **Run `roam runs verify --since <last-audit>` in CI weekly.** The
   HMAC chain is tamper-evident only if you actually verify it; a
   weekly check produces a fresh attestation for the audit trail.
3. **Wire `roam mode safe_edit` as the agent default.** Three
   `migration` / `autonomous_pr` overrides in 30 days is sustainable
   only because each named an authorizer — make that the policy, not
   the convention.
4. **Consider the 90-run Deep tier.** A 90-day window surfaces drift
   that a 30-day audit misses (re-introduced findings, decaying
   authorizer attribution, mode-override frequency creep). See
   <https://roam-code.com/governance> for engagement terms.

## What this report does *not* cover

- **Model-training evidence.** The pack documents what an agent did
  against the local repository. Training datasets, model weights,
  and pre-training data are outside Roam's measurement surface.
- **Production runtime behaviour.** The ledger captures pre-merge
  evidence. Post-deploy telemetry requires separate evidence
  (`roam runtime` ingests OpenTelemetry traces but those are not
  collected by this engagement).
- **Dataset provenance** for training or fine-tuning. Out of scope.
- **Formal certification** against SOC 2, ISO/IEC 42001, EU AI Act,
  NIST AI RMF, or internal AI policy. The pack supports evidence
  for these frameworks; the conformity assessment is a separate
  engagement with qualified counsel and auditors.
- **Approvals not recorded in the run ledger.** Where a human
  approval happened outside the substrate (Slack, email, verbal),
  the bundle emits `redactions[].reason = "producer_not_available"`
  rather than synthesising one.

## 7. Disclaimer

This report is **evidence support**, not formal certification. It
maps Roam-generated artifacts to relevant compliance controls so
auditors, risk officers, and engineering leadership can assess
AI-agent-driven code change against their organization's policy
framework. Roam does not perform compliance attestation; consult
qualified counsel or auditors for formal certification against any
specific framework (SOC 2, ISO/IEC 42001, EU AI Act, NIST AI RMF, or
internal policy).

The data in this sample report is illustrative. PR numbers, agent
names, authorizer emails, and risk counts are representative of the
shape of a real engagement and are not drawn from any production
system.

## Methodology

Roam compiles the pack from the local SQLite-backed run ledger
(`.roam/runs/`), proof bundles (`.roam/pr-bundles/`), and the
findings registry. The substrate is in `src/roam/runs/`,
`src/roam/commands/cmd_pr_bundle.py`, `src/roam/evidence/`, and `src/roam/db/findings.py`.
Every artifact ships as JSON (machine-readable), Markdown
(human-readable), and an optional in-toto v1 attestation
(cryptographically verifiable with cosign).

The engagement runs against a temporary clone of the buyer's repo,
which is deleted within 7 days of report delivery. The SOW and DPA
under [`templates/legal/`](https://github.com/Cranot/roam-code/tree/main/templates/legal)
cover retention, training-exclusion, and confidentiality.

For the companion engagement focused on merged-PR detector replay
(no ledger required), see the
[PR Replay sample report](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-pr-replay-team.md).

---

_Generated by `roam runs` + `roam pr-bundle` + `roam replay` on
2026-05-08. All analysis runs locally; no source code is transmitted
to any third-party service. Engine ships in the open-source CLI
([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))
under Apache 2.0. Reproduce the artifact extraction with the commands
in [`evidence-checklist.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/evidence-checklist.md)._
