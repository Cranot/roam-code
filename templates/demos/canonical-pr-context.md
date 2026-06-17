<!-- W216 canonical demo fixture; values are illustrative, not from a real repo -->

# AI agent changes payment-processing logic — what evidence does Roam capture?

> **Scenario.** A Laravel e-commerce team accepts an AI-authored PR
> against `app/Services/PaymentProcessor.php`. The change refactors
> `settle()` to call a new payment provider's SDK. The PR is high
> blast-radius, crosses a layer boundary (service to vendor), has
> tests already written, and turns out to have an out-of-date clone
> in `LegacyPaymentProcessor::settle()`. A human engineer reviews and
> approves it — but the reviewer wants the same answers they would
> demand from any pull request: who changed what, under what
> authority, with what evidence?

This page is the buyer-facing setup for the canonical Roam demo. It
describes the fictional PR, the actors involved, and the evidence the
agent (and Roam) must surface before merge. The matching evidence
packet lives in `canonical-evidence.json` and the rendered buyer
report lives in `canonical-pr-replay.md`.

## The repo

A mid-sized Laravel monolith for an e-commerce shop (`acme-shop`).
The team uses Cursor + Claude as an AI pair, gated through the
Model Context Protocol (MCP). Roam is wired into the MCP toolchain
so every gated action goes through a Roam tool call.

The payment surface lives under `app/Services/`:

- `PaymentProcessor.php` — the modern path, owner of `settle()`.
- `LegacyPaymentProcessor.php` — a legacy path retained behind a
  feature flag while the team retires it. Carries a near-identical
  `settle()` clone of 47 lines (clone class confirmed by
  `roam clones --persist`).

## The change

PR 42 (synthetic) updates `PaymentProcessor::settle()` to call a new
payment provider's SDK (`newpay-sdk-php@1.0.2`). The agent
(`example-trusted-agent`, mediated through an MCP client) was asked to:

1. Replace the existing internal HTTP call with the SDK's `charge()`
   method.
2. Keep the existing validation order intact.
3. Surface a `provider_ref` field on the return value so the
   downstream order-status code can correlate.

The agent touched three symbols:

| Symbol | Why |
|---|---|
| `settle` | Refactored to call `Newpay\Client::charge` |
| `_validatePayment` | Validation signature changed to thread the new SDK's options object |
| `_logTransaction` | Updated to log the new `provider_ref` |

## Who acted, under what authority, in what environment

The agent declared the active Roam mode (`safe_edit`) at the start
of the run via `roam mode safe_edit`. A run id was opened with
`roam runs start --agent-id agent:example-trusted-agent`. Every gated
tool call (`roam preflight`, `roam impact`, `roam diff`,
`roam critique`) was logged to the run's HMAC-chained event ledger,
so the agent identity carries the `local_env` trust tier (the
run-ledger HMAC corroborates the actor claim).

When the agent finished, a human engineering lead
(`dev@example-org.com`) reviewed the diff and approved the PR. The
commit author email matches `git config user.email`, so the human
identity carries the `git_author` trust tier. The approval is
recorded in the evidence packet as `approval:pr_42_review_1`, granted
by the human actor.

The change ran inside a CI job
(`ci.acme.example/acme-shop/pipelines/9821/jobs/417`) on the
`feat/new-payment-sdk` branch (`abc1234..def5678`) inside the
`/srv/acme-shop` workspace. All three environment refs are recorded
on the evidence packet.

## The risk profile (and how Roam knew)

Roam surfaced six findings during the gated tool calls. They span
five detectors and three confidence tiers:

| Detector | Severity | Confidence | What it caught |
|---|---|---|---|
| `clones-not-edited` | high | structural | `LegacyPaymentProcessor::settle` is a 47-line clone of `settle()`; the agent updated the modern path but did not update the clone. |
| `complexity` | medium | structural | Cognitive complexity of `settle()` rose from 18 to 26 (payment-code threshold: 20). |
| `taint` | high | static_analysis | Tainted request input flows into the SDK call with only one validation hop; pre-refactor had two. |
| `vuln-reach` | medium | static_analysis | `newpay-sdk-php@1.0.2` transitively pulls in `DEMO-2026-0001` (synthetic advisory); reachable from `settle`. |
| `n1` | low | structural | `_logTransaction` issues one `DB::insert` per `$lineItems` iteration. Pre-existing; not introduced by this PR. |
| `bus-factor` | low | heuristic | 82% of file history is from one author. |

Three policy decisions fired during the change. All three are
recorded:

- `require_tests_for_payment_paths` — required four tests; the agent
  ran them.
- `block_layer_violation` — service-to-vendor edges are gated;
  `newpay-sdk-php` is on the approved-vendor allowed-list so the
  outcome was `allowed`.
- `allow_with_approval` — blast radius (19 callers) exceeded the
  15-caller default threshold; a human approval was attached, so the
  outcome was `allowed_with_approval`.

## The tests

Four tests were declared required by the `require_tests_for_payment_paths`
rule. Four were run. **Three passed. One failed.** The failure was on
`tests/Feature/LegacyPaymentProcessorTest::test_legacy_settle_matches_modern`
— exactly the test that asserts the legacy clone matches the modern
schema. The failure surfaces the clone-not-edited finding the
detector independently caught.

This is intentional. The fixture demonstrates how Roam reports
**partial success honestly**: the verdict is `REVIEW`, not `SAFE`,
and the buyer report names the failing test by id.

## The accepted risk

The engineering lead reviewed the failing test and the clone
finding, decided the legacy path was gated behind a feature flag
that is off in production, and accepted the residual risk under
`risk:pr_42_clone_diverged_legacy` (expires 2026-08-14). The
accepted-risk entry is linked back to the approval id so the audit
trail closes cleanly.

## What the evidence packet asserts

A single `ChangeEvidence` packet (schema v1.0.0) captures everything
above:

- 1 run id, 1 agent id, 1 human actor
- 2 actor refs (agent + human, both with trust tiers)
- 3 authority refs (mode + approval + policy rule)
- 3 environment refs (workspace + branch range + CI job)
- 2 context refs (the `roam preflight` and `roam impact` envelopes the agent read before editing)
- 3 changed subjects, 6 findings, 3 policy decisions
- 4 tests required, 4 tests run (3 pass, 1 fail)
- 1 approval, 1 accepted risk
- 2 artifacts (SARIF + proof bundle, both referenced by path and hash)
- 0 redactions
- 1 deterministic `content_hash`

The packet is the input. The `canonical-pr-replay.md` report is the
output. The reviewer reads the report; the evidence packet is what
makes the report reproducible byte-for-byte across machines.

---

*This page (and the rest of `templates/demos/`) is a fixture for
illustrative use. The repo, the actors, the SDK, the CVE id, and
the finding counts are synthetic. The data shape is real — it is
what `roam pr-replay --evidence evidence.json` emits today for any real PR.*
