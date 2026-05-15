<!-- W216 canonical demo fixture; values are illustrative, not from a real repo -->

# What did Cursor + Claude actually do in this PR, and how do you know it's safe to merge?

A pull request lands on your `main` branch from an AI pair. The diff
looks reasonable. The tests are green-ish. The author field on the
commit reads `example-trusted-agent`. Before you click **Merge**, you
have eight questions you would ask of any human contributor — the
same eight Roam's `evidence_completeness()` helper scores every
evidence packet against:

1. **Actor.** Who made the change?
2. **Authority.** Who authorised it?
3. **Context.** What did the actor read before editing?
4. **Changes.** What in the codebase actually changed?
5. **Risk.** What risk did the change introduce?
6. **Policy.** Which rules fired, and what did they decide?
7. **Verify.** How was the change verified?
8. **Accept.** Who accepted any residual risk?

This demo answers all eight from a single Roam evidence packet
(`canonical-evidence.json`) for a synthetic Laravel PR. The verdict
is **REVIEW**, not **SAFE** — because one of the four required tests
failed, and an honest report has to say so out loud. The fictional
setup is described in `canonical-pr-context.md`; this page shows the
report Roam renders for the reviewer.

---

## The rendered ChangeEvidence Markdown report

What follows is the report a reviewer would see if they ran:

```bash
roam pr-replay \
  --range main:abc1234..def5678 \
  --evidence-bundle .roam/reports/pr-42 \
  --client "Acme Shop"
```

against the canonical evidence packet. The format mirrors the
`templates/audit-report/pr-replay-template.md` skeleton; values are
filled in deterministically from the packet.

---

# PR Replay — def5678

**Verdict**: REVIEW: 4/5 high-confidence checks pass; 1 test failure requires reviewer attention before merge
**Risk level**: high
**Mode**: safe_edit
**Range**: `main:abc1234..def5678`
**Run IDs**: run:run_20260514_demo_a1b2c3
**Schema**: 1.0.0

## Scope

- 3 symbols changed across 1 file
- Diff hash: `diff:0123456789abcdef0123456789abcdef01234567`

## Changed subjects (top 20)

| Subject | Kind | Blast radius |
|---|---|---|
| `app/Services/PaymentProcessor.php::settle` | symbol | 19 |
| `app/Services/PaymentProcessor.php::_validatePayment` | symbol | 7 |
| `app/Services/PaymentProcessor.php::_logTransaction` | symbol | 3 |

## Actors

| Kind | ID | Trust tier | Display |
|---|---|---|---|
| `agent` | `agent:example-trusted-agent` | `local_env` | Example trusted agent (CI-mediated run) |
| `human` | `human:dev@example-org.com` | `git_author` | Engineering lead (PR approver, commit author) |

## Authorities

| Kind | ID | Granted by |
|---|---|---|
| `mode` | `mode:safe_edit` | system:.roam/modes/active |
| `approval` | `approval:pr_42_review_1` | human:dev@example-org.com |
| `policy_rule` | `policy_rule:require_tests_for_payment_paths` | system:.roam/rules.yml |

## Environment

| Kind | ID |
|---|---|
| `workspace` | `workspace:/srv/acme-shop` |
| `branch_range` | `branch_range:main:abc1234..def5678` |
| `ci_job` | `ci_job:ci.acme.example/acme-shop/pipelines/9821/jobs/417` |

## Findings (6)

| Detector | Confidence | Count |
|---|---|---|
| `clones-not-edited` | structural | 1 |
| `complexity` | structural | 1 |
| `taint` | static_analysis | 1 |
| `vuln-reach` | static_analysis | 1 |
| `n1` | structural | 1 |
| `bus-factor` | heuristic | 1 |

## Tests

- Required: 4
- Run: 4
- Status: 3 passed, 1 failed (`tests/Feature/LegacyPaymentProcessorTest::test_legacy_settle_matches_modern`)

## Approvals and accepted risks

Approvals:

- `approval:pr_42_review_1` — `human:dev@example-org.com` at
  2026-05-14T10:18:00Z. Scope: `blast_radius_acceptance`. Comment:
  "Approving the merge with the noted layer change. Please open a
  follow-up PR to update LegacyPaymentProcessor::settle or schedule
  its retirement."

Accepted risks:

- `risk:pr_42_clone_diverged_legacy`, accepted by
  `approval:pr_42_review_1`. Rationale: "Legacy path is gated behind
  a feature flag that's off in prod. Retirement is tracked on
  JIRA-DEMO-1837." Expires 2026-08-14.

## Suggested Review configuration

Based on this replay's findings, the following Review configuration
would have caught 2 of 6 findings before merge:

### Recurring risk classes

| Class | Total findings | PRs with this finding |
|---|---:|---:|
| `clones-not-edited` | 1 | 1 / 1 |
| `taint` | 1 | 1 / 1 |

### Suggested .roam/rules.yml

```yaml
rules:
  - id: block_clones_not_edited
    when:
      detector: clones-not-edited
      severity: high
    then: block
  - id: review_payment_taint
    when:
      detector: taint
      subject_glob: "app/Services/PaymentProcessor*"
    then: review
```

### Suggested CI gates

```bash
# Block PRs where a clone-class divergence is reachable
roam critique --ci --fail-on clones-not-edited:high
# Gate PRs with new taint edges through the payment path
roam critique --ci --fail-on taint:high
```

### What Review would have blocked

| SHA | Date | Subject | High findings | Rationale |
|---|---|---|---:|---|
| `def5678` | 2026-05-14 | Refactor PaymentProcessor::settle to call Newpay SDK | 2 | clones-not-edited::settle_clone_diverged + taint::request_to_sdk_no_intermediate_validation |

## Evidence limitations

- **Non-certification**: this report **supports evidence for**
  governance review and **maps to** change-management controls. It
  is not certification of compliance with any framework (SOC 2 /
  ISO 42001 / EU AI Act / etc.). Mapping to specific framework
  controls and the conformity assessment remain with the customer.

---

*Per the agentic-assurance crosswalk, this report **supports evidence for** governance review and **maps to** change-management controls. It does not certify compliance with SOC 2, ISO 42001, the EU AI Act, or any other framework — the conformity assessment remains with the customer.*

---

## How this PR answers the eight evidence questions

The packet on disk (`canonical-evidence.json`) is the source of
truth. The table below walks each of the eight buyer questions
through the field that carries the answer and the value it carries
in this demo packet.

| Q | Question | Field on the packet | This packet says | Score |
|---|---|---|---|---|
| Q1 | Actor — who made the change? | `actor_refs[]` (W182) + legacy `agent_id` / `human_actor` | One agent (`agent:example-trusted-agent`, `local_env` tier — run-ledger HMAC corroborated) and one human (`human:dev@example-org.com`, `git_author` tier — matches `git config user.email`). Both refs are above the W278/W281 trust-tier warning bar. | complete |
| Q2 | Authority — who authorised it? | `authority_refs[]` (W182) | Three: active mode `safe_edit` (source: `mode`), human approval `approval:pr_42_review_1` (source: `human_approval`), policy rule `require_tests_for_payment_paths` (source: `rule_config`). | complete |
| Q3 | Context — what did the actor read? | `context_refs[]` | Two captured Roam envelopes: `roam preflight settle` and `roam impact settle`, each referenced by path + sha256 so a reviewer can verify what informed the change. | complete |
| Q4 | Changes — what was touched? | `changed_subjects[]` | Three Laravel PHP symbols in `app/Services/PaymentProcessor.php`. Blast-radius counts attached. | complete |
| Q5 | Risk — what risk did this introduce? | `risk_level` + `findings[]` | `risk_level=high`. Six findings spanning five detectors and three confidence tiers (structural / static_analysis / heuristic). Two high-severity. | complete |
| Q6 | Policy — which rules fired? | `policy_decisions[]` | Three rule outcomes: `required` (tests), `allowed` (layer crossing), `allowed_with_approval` (high blast radius). | complete |
| Q7 | Verify — how was it verified? | `tests_run[]` + `artifacts[]` | Four tests run (3 pass, 1 fail) plus two artifacts (SARIF + proof bundle, both referenced by path and sha256). | complete |
| Q8 | Accept — who signed off on residual risk? | `approvals[]` + `accepted_risks[]` | One approval (`approval:pr_42_review_1`) linked to one accepted risk (`risk:pr_42_clone_diverged_legacy`), with rationale, scope, and expiry. | complete |

The environment surface (workspace, branch range, CI job) is recorded
on `environment_refs[]` and rendered in the **Environment** section
above. The eight-question scoreboard does not score environment as a
separate slot — it folds into context (`Q3`) and accept (`Q8`) as
provenance for the other answers.

The packet is byte-stable. The `content_hash` field
(`19a6b4e3628b6d8c451da93c4d7fd5781fbb8cdb8a95f840d86b2091b21cf163`
for this fixture) is sha256 of the canonical JSON form with the
hash field cleared. Any consumer that recomputes the hash will get
the same value — that is the evidence-compiler guarantee.

## What is still partial or unverified

The "Evidence limitations" section above (rendered automatically
from W185) names what the report does not cover. For this fixture:

- **Non-certification (always).** The report supports evidence for
  governance review; it does not certify compliance with any named
  framework.
- **One test failed.** The verdict is `REVIEW`, not `SAFE`. The
  approver explicitly accepted the residual risk on the legacy
  clone path. The reviewer should not interpret the human
  acceptance as test-passage: the test is recorded as `failed` and
  the failure message is in the packet.
- **Synthetic advisory.** `DEMO-2026-0001` is a placeholder; on a
  real repo `vuln-reach` would emit the actual advisory id from
  npm / pip / OSV / Trivy ingestion.
- **Trust tiers are corroborated, not self-reported.** The agent
  identity is `local_env` (corroborated by the active run-ledger
  entry's HMAC-signed agent), and the human identity is `git_author`
  (matches `git config user.email`). Neither is the strongest tier
  (`verified_ci`) — to upgrade further, wire the change through a CI
  runner that emits OIDC tokens. The packet shape stays the same;
  only the per-ref `trust_tier` field would change.

## Honesty banner

This is the **ideal case**. The packet covers 8 of 8 evidence
questions affirmatively. A real PR will often answer fewer — and
the renderer will surface every gap in the "Evidence limitations"
section above. The substrate exists so an agent can earn the right
to change code; this demo shows what "earning it" looks like when
all eight slots are filled.

> **8 / 8 questions covered.** Identity, authority, environment,
> subjects, findings, policy, tests, risk acceptance — all named on
> the packet, all rendered above, all verifiable from the
> content-hashed JSON.

See `insufficient-pr-replay.md` for an example of what the
INSUFFICIENT tier looks like when six of the eight questions are
skipped. The W259 honest-coverage banner flags that packet
explicitly and warns the reviewer not to publish it as governance
evidence.

---

*Per the agentic-assurance crosswalk
(`(internal memo)`), Roam **supports
evidence for** governance review and **maps to** change-management
controls. The conformity assessment for any specific framework
remains with the customer.*
