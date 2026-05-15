# AI-Agent Change Audit Report — Example Co.

**Report period**: 2026-04-01 to 2026-04-30
**Repository**: `example-co/checkout-service`
**Auditor**: Example Co. AI Risk Office (internal)
**Tools used**: roam-code v13.x (Apache 2.0, local-only)

## Executive summary

Across the April window, four named AI agents opened 47 runs against the
checkout-service repository, of which 38 reached an emitted `pr-bundle` and
29 merged to `main`. Every merged change carries a Roam-signed event ledger
(`runs verify` passed for 29 / 29), a `context_read` manifest, and at least
one declared risk-or-non-goal. Three high-severity edits invoked the
`migration` mode override; all three name a human authorizer in the run
metadata. The single open governance gap is two PRs whose `tests_required`
list was declared but whose `tests_run` list is empty — flagged in Section 5.

## 1. Which agents changed what

Source: `roam runs list --json` plus `roam replay <run_id>` for narrative
reconstruction.

| Agent | Runs opened | Runs completed | PR-bundles emitted | Merged | Mean affected_symbols |
|---|---:|---:|---:|---:|---:|
| `claude-code` | 22 | 21 | 19 | 16 | 7.4 |
| `cursor` | 14 | 13 | 11 | 9 | 4.1 |
| `human-pair` | 8 | 8 | 6 | 3 | 12.7 |
| `aider` | 3 | 2 | 2 | 1 | 2.0 |

The `affected_symbols` column is the mean size of the `pr-bundle.affected_symbols[]`
array for completed runs. Higher values are not inherently risky — they
correlate with refactor-shaped work — but combined with Section 3 they help
identify which agents took on the largest blast-radius changes.

## 2. Context each agent read before changing code

Source: `roam pr-bundle show <run_id>` — specifically `context_read.commands_run`,
`context_read.symbols_inspected`, and `context_read.files_inspected`.

Roam records every analysis command an agent ran inside a run, so the audit
trail answers "did the agent look before it leapt?" without re-asking the
agent. Representative bundle excerpt (run `2026-04-12T09:14:02Z-claude-code`):

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

Aggregate finding for the month: 36 / 38 emitted bundles record at least one
of `roam preflight`, `roam impact`, or `roam describe` in `commands_run`
before the first edit event. The two exceptions are listed in Section 5
under "tests did not close the loop".

## 3. Risks accepted vs. mitigated

Source: `roam pr-bundle show <run_id>` — `risks[]` array, classified by
`status` and `severity`.

| Risk class | Mitigated by test | Mitigated by gate / mode | Accepted (documented) | Total |
|---|---:|---:|---:|---:|
| `side_effect_*` (R28 world-model) | 7 | 4 | 2 | 13 |
| `causal_diff_*` (added/removed edges) | 5 | 1 | 1 | 7 |
| `blast_radius_high` | 3 | 6 | 0 | 9 |
| `clones_not_edited` | 2 | 3 | 0 | 5 |
| `layer_violation` | 0 | 4 | 1 | 5 |

"Accepted" risks are not failures — they are documented in the bundle's
`risks[].rationale` field, with a named authorizer in `runs` events. The
2 accepted `side_effect_*` risks correspond to deliberate write-path changes
in the receipts emailer; both name the on-call engineer as authorizer
(Section 4).

## 4. Who authorized risky edits

Source: `roam runs show <run_id>` — events whose `action` is `mode-switch`
to `migration` or `autonomous_pr`, plus accepted risks tagged with an
`authorizer` field.

| Date | Run id (short) | Agent | Mode invoked | Authorizer | Rationale (excerpt) |
|---|---|---|---|---|---|
| 2026-04-08 | `…claude-code-a31f` | claude-code | `migration` | `j.park@example.co` | "DB column rename, coordinated with deploy window" |
| 2026-04-17 | `…cursor-b88e` | cursor | `migration` | `j.park@example.co` | "Schema split, replayed against staging snapshot" |
| 2026-04-23 | `…human-pair-c12d` | human-pair | `autonomous_pr` | `s.rao@example.co` | "Vendor SDK upgrade, isolated under a feature flag" |

Mode overrides above `safe_edit` produce ledger events of action
`mode-switch`. The audit period contained three such events; all three
include an `authorizer` field. None were issued under `read_only` (which
the substrate physically blocks from mutating bundles).

## 5. Which tests closed the loop

Source: `roam pr-bundle show <run_id>` — comparison of `tests_required[]`
to `tests_run[]`, plus CI logs cross-referenced by commit SHA.

- 27 of 29 merged runs have a non-empty `tests_run[]` array.
- 25 of those 27 have `tests_run[].passed == true` for every entry.
- 2 of 27 have at least one failed test recorded but documented mitigation.

**Open gap.** Two merged PRs (`#1184`, `#1196`) declared `tests_required`
entries but emitted with an empty `tests_run` list. Both passed CI through
unrelated test suites; neither violates a Roam gate today because the
bundle's `validate` command only checks structural completeness. Recommend
adding `roam pr-bundle validate --strict` to CI to surface this gap
pre-merge going forward.

## 6. Compliance evidence mapping

This table maps Roam-generated artifacts to common controls so reviewers
can locate the relevant evidence for their own framework. It is a
cross-reference, **not a certification**. See Section 7.

| Roam evidence | Maps to control (representative) |
|---|---|
| `pr-bundle.context_read[]` | SOC 2 CC8.1 (change-management documentation of pre-change review) |
| `runs` ledger (HMAC-chained events) | ISO/IEC 42001 §6.1.3 (AI-system change tracking) |
| `pr-bundle.affected_symbols[]` | EU AI Act Article 12 (record-keeping / activity logs for high-risk systems) |
| `pr-bundle.risks[]` + `tests_run[]` | NIST AI RMF MAP-2.1 (risk identification) and MEASURE-2.7 (test-based risk treatment) |
| `mode` history + `runs` `mode-switch` events | SOC 2 CC6.3 (logical-access change events) and internal AI-policy attestations |
| `roam runs verify` (chain integrity) | NIST AI RMF GOVERN-1.7 (tamper-evident audit trail) |

The mapping cites controls by their commonly-used identifiers. Whether any
particular control applies, and what additional evidence a qualified
auditor would require for that control, is outside Roam's scope.

## 7. Disclaimer

This report is **evidence support**, not formal certification. It maps
Roam-generated artifacts to relevant compliance controls so that auditors,
risk officers, and engineering leadership can assess AI-agent-driven code
change against their organization's policy framework. Roam does not
perform compliance attestation; consult qualified counsel or auditors for
formal certification against any specific framework (SOC 2, ISO/IEC 42001,
EU AI Act, NIST AI RMF, or internal policy).

The data in this sample report is illustrative. PR numbers, agent names,
authorizers, and risk counts are representative of the shape of a real
engagement and are not drawn from any production system.

---

_Generated using `roam-code` (Apache 2.0, <https://github.com/Cranot/roam-code>).
All analysis runs locally; no source code is transmitted to any third-party
service. Reproduce the artifact extraction with the commands in
`evidence-checklist.md`._
