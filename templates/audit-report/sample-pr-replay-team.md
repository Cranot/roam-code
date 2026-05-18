# PR Replay Report — Demo Buyer Inc

**Tier:** Team — 30 PRs
**Commit range:** `HEAD~30..HEAD`
**Generated:** 2026-05-08 10:00 UTC
**Tool:** `roam pr-replay` — `postmortem` + `critique` engine
**Schema:** `pr-replay/1.0`

> **Sample report — illustrative, not real customer data.** The
> buyer name, PR subjects, SHAs, and finding counts below are
> representative of the shape a paid Team engagement produces. Run
> `roam pr-replay --tier sample` for a self-serve 5-PR preview on
> your own repo, or email <hello@roam-code.com> to commission a paid
> Team or Deep engagement.

This report **supports evidence for** structural-review governance
and **maps to** change-management controls. It does not certify
compliance with SOC 2, ISO 42001, the EU AI Act, or any other
framework — the conformity assessment remains with the buyer.

---

Thirty most-recent merged PRs on the target branch, scored against the
current Roam detector set. Includes founder review of the top
findings on a 30-minute call.

## Executive summary

**Verdict:** 11 of 30 PRs (36%) surfaced **18 findings** — 4 high-severity, 13 medium-severity, 1 low.

- PRs replayed: **30**
- PRs Roam would have flagged pre-merge: **11**
- High-severity findings (would block CI): **4**
- Medium-severity findings (would gate review): **13**
- Low-severity findings: **1**

## The eight evidence questions

This report answers structural-review questions only. Identity,
authority, approval, and verification axes are out of scope for PR
Replay; the engagement reads only merged git history.

| Question | Coverage on this report |
|---|---|
| **Who acted?** | Out of scope. Git author per commit is in `git log`, not re-derived here. |
| **What authority existed?** | Out of scope. Roam Review (continuous) records `mode` + `permits` + `leases`; PR Replay does not. |
| **What context was read?** | The 30 commits in `HEAD~30..HEAD`, the current repo's symbol/call graph, and the active detector set. |
| **What changed?** | Per-PR table below: date, SHA, subject, top-hit detectors. |
| **What could break?** | Detector breakdown table: `clones-not-edited`, `blast-radius`, `layer-violation`, `intent-mismatch`. |
| **What policy applied?** | Default Roam detector set. No per-repo `.roam-rules.yml` was provided for this run. |
| **What verified it?** | Replay only — no test execution. Detector versions stamped in run ledger. |
| **Who accepted risk?** | Out of scope (`producer_not_available` — PR Replay does not collect approvals). For continuous approval evidence, run Roam Review. |

## What Roam would have flagged

| Detector | Total findings | PRs with this finding |
|---|---:|---:|
| `clones-not-edited` | 7 | 5 / 30 |
| `blast-radius` | 6 | 6 / 30 |
| `layer-violation` | 3 | 2 / 30 |
| `intent-mismatch` | 1 | 1 / 30 |
| `dead-code-reintroduced` | 1 | 1 / 30 |

The highest-impact class on this window was **`clones-not-edited`** (7 findings across 5 PRs). Wire a CI gate against this class — single highest-leverage move surfacing from this replay.

## Per-PR breakdown

Top 11 PRs ranked by severity (high → medium → total).

| Date | SHA | Subject | High | Medium | Top hits |
|---|---|---|---:|---:|---|
| 2026-04-29 | `a1b2c3d` | Refactor user-creation flow to share helper | 1 | 2 | clones-not-edited x1, blast-radius x2 |
| 2026-04-27 | `d4e5f6g` | Add idempotency keys to payment-webhook handler | 1 | 1 | clones-not-edited x1, blast-radius x1 |
| 2026-04-22 | `h7i8j9k` | Migrate auth middleware to async | 1 | 1 | layer-violation x1, blast-radius x1 |
| 2026-04-19 | `l0m1n2o` | Wire feature-flag check into checkout flow | 1 | 0 | clones-not-edited x1 |
| 2026-04-16 | `p3q4r5s` | Update order-status state machine | 0 | 2 | blast-radius x2 |
| 2026-04-14 | `t6u7v8w` | Add retry semantics to background-job runner | 0 | 2 | clones-not-edited x1, intent-mismatch x1 |
| 2026-04-11 | `x9y0z1a` | Inline pricing calculation into route handler | 0 | 2 | layer-violation x2 |
| 2026-04-08 | `b2c3d4e` | Add address-normalisation step to checkout | 0 | 1 | clones-not-edited x1 |
| 2026-04-05 | `f5g6h7i` | Tighten admin-route authorisation | 0 | 1 | blast-radius x1 |
| 2026-04-02 | `j8k9l0m` | Refactor SMS-notification dispatcher | 0 | 1 | clones-not-edited x1 |
| 2026-03-30 | `n1o2p3q` | Update DB pool sizing for analytics workers | 0 | 0 | dead-code-reintroduced x1 |

## Recommended next steps

Four actions, ordered by leverage:

1. **Wire CI gates against the top 3 detector classes** — `clones-not-edited`, `blast-radius`, `layer-violation`. `roam critique` returns exit code 5 on any high-severity finding, so a single CI step gates every PR. See <https://roam-code.com/docs/>.
2. **Run `roam preflight <symbol>` before changing high-blast-radius code.** Blast radius is invisible in the diff; it shows up in the graph.
3. **Add `roam clones --persist` to your indexing pipeline.** `roam critique` then picks up clone-not-edited cases on every PR — the single most common AI-shaped bug across replays in similar codebases.
4. **Consider the Deep tier** if these patterns warrant a 90-PR window, a per-detector deep-dive section, and a 90-minute walk-through call with a written remediation plan: <https://roam-code.com/audit#tiers>.

## Apply this fee toward Roam Review

50% of the engagement fee — **$1,250** — credits toward your first
year of [Roam Review](https://roam-code.com/pricing) if you
subscribe within **60 days** of report delivery. Roam Review runs
the same detectors on every pull request automatically, with a
sticky PR comment, BLOCK / REVIEW / APPROVE verdict, and exit-code-5
CI gating. Mention this report when subscribing and we apply the
credit to the first invoice.

> _Roam Review is in early access at the time this sample was
> generated. The credit applies once the hosted service is generally
> available, or against an equivalent founding-customer arrangement
> in the interim._

## What this report does *not* cover

- **Semantic correctness** — whether the code does the right thing.
  We complement semantic reviewers (CodeRabbit, Greptile, Qodo),
  we don't replace them.
- **Security audit** of the kind a third-party penetration test would
  produce. We surface structural risks (clones, blast radius, layer
  violations) — not exploit paths.
- **Performance profiling.** Some findings touch hot paths when
  runtime telemetry is wired, but this isn't a benchmark run.
- **Code review of in-flight PRs.** This report covers *merged*
  history. For pre-merge gating, install the open-source CLI and,
  when it ships, the Roam Review GitHub App.
- **Authority / approval evidence.** PR Replay reads merged history
  only — no mode, permit, lease, or approval records are produced.
  Continuous Roam Review covers those axes.

## Methodology

Roam replays the current detector set against each commit's outgoing
diff as if it were a PR — no historical re-indexing. Findings reflect
what Roam catches today on those PRs, not what an earlier Roam
version would have. The detector set is stable across Team (30 PRs)
and Deep (90 PRs) windows.

The engagement runs against a temporary clone of the buyer's repo,
which is deleted within 7 days of report delivery. The SOW and DPA
under [`templates/legal/`](https://github.com/Cranot/roam-code/tree/main/templates/legal)
cover retention, training-exclusion, and confidentiality.

_Generated by `roam pr-replay --tier team` on 2026-05-08 10:00 UTC.
Engine: `roam postmortem` walks the range; `roam critique`
evaluates each diff. Both ship in the open-source CLI
([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))
under Apache 2.0._
