# Statement of Work — Roam PR Replay

> ⚠️ **REVIEW BEFORE USE.** v1 draft, 2026-05-08. Have a qualified
> attorney review before binding execution. Bracketed placeholders
> `[LIKE_THIS]` are filled in per engagement; the Stripe Payment Link
> custom fields the buyer completed at checkout map 1:1 to the
> bracketed fields in **Section 2 (Scope)**.

---

## Parties and effective date

This Statement of Work ("**SOW**") is entered into on `[EFFECTIVE_DATE]` between:

- **Provider**: `[PROVIDER_LEGAL_NAME]`, sole-trader (atomiki epicheirisi),
  Greek tax ID (AFM) `[PROVIDER_AFM]`, with registered address at
  `[PROVIDER_ADDRESS]` ("**Provider**", "we", "us").
- **Client**: `[CLIENT_LEGAL_NAME]`, with registered address at
  `[CLIENT_ADDRESS]` ("**Client**", "you").

This SOW incorporates by reference Provider's Data Processing Agreement
(`https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md`,
the "**DPA**") and Refund Policy (`https://roam-code.com/refund`).

---

## 1. Engagement

Provider will perform a **PR Replay** structural-review engagement at
the **`[TIER]`** tier on the codebase identified in **Section 2** below.

**Tier selected** (mark one):

- [ ] **Team** — 30 most-recent merged PRs · 5-business-day delivery ·
  30-minute walk-through call · USD $2,500 ($1,250 credits toward
  Roam Review per Section 7).
- [ ] **Deep** — 90 PRs (or specified range) · 10-business-day delivery
  · 90-minute walk-through call · per-detector deep-dive section ·
  written 90-day remediation plan · USD $6,000 ($3,000 credits toward
  Roam Review per Section 7).

---

## 2. Scope

The replay window and points of focus are as captured at checkout via the
Stripe Payment Link custom fields:

| Field | Buyer-supplied value |
|---|---|
| Repository URL or "will share privately" | `[REPO_URL]` |
| Default branch | `[DEFAULT_BRANCH]` |
| Specific commit range (overrides tier default) | `[COMMIT_RANGE]` |
| Preferred walk-through windows | `[WALK_THROUGH_WINDOWS]` |
| Areas to emphasise (optional) | `[EMPHASIS]` |
| Specific incidents to look for (optional) | `[INCIDENTS]` |

Where `[COMMIT_RANGE]` is left blank, Provider will replay the trailing
30 PRs (Team) or 90 PRs (Deep) on the default branch.

### What is in scope

- Replay of Provider's current `roam` detector set (as of the date of
  this SOW) against each commit in the agreed range.
- Aggregated detector-class breakdown identifying the highest-impact
  patterns across the window.
- Per-PR ranking of findings by severity (high → medium).
- Recommended CI gates surfacing from the actual finding pattern.
- Live walk-through call of the report at the agreed time.
- Markdown + PDF deliverable shipped to a Client-nominated email.

### What is out of scope (mirroring `/audit#what-this-report-does-not-cover`)

- **Semantic correctness review** — whether the code does the right thing.
  Provider complements semantic reviewers (e.g., CodeRabbit, Greptile,
  Qodo); does not replace them.
- **Security audit** of the kind a third-party penetration test would
  produce. Provider surfaces structural risks (clones, blast radius,
  layer violations), not exploit paths.
- **Performance profiling**. Some findings touch hot paths if Client has
  runtime telemetry wired, but this SOW does not include benchmark runs.
- **Pre-merge review of in-flight PRs.** This engagement covers merged
  history. For pre-merge gating, Client may install the free CLI or
  subscribe to Roam Review when available.

---

## 3. Deliverables

1. **Markdown report** matching the template at
   `https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-pr-replay-team.md`,
   parameterised with Client name and the scope above.
2. **PDF render** of the same report.
3. **Walk-through call** of length per the selected tier (30 min Team /
   90 min Deep) over Client's preferred video tool. Up to five Client
   attendees. Recording optional and at Client's discretion.
4. **(Deep tier only)** Written 90-day remediation plan listing the
   recommended CI gates ranked by leverage with concrete `roam` commands
   and integration snippets.

---

## 4. Timeline

- **Day 0** — Client pays via Stripe Payment Link; Provider receives
  webhook confirmation.
- **Day 1** — Provider sends this SOW to Client for countersignature
  along with a read-only deploy-key invitation to the temporary working
  tree Provider will use.
- **Day 1–2** — Client countersigns the SOW and approves the deploy key.
- **Day 2 (kickoff)** — Provider begins the replay against the agreed
  range.
- **Day 5 (Team) / Day 10 (Deep)** — Walk-through call held at the
  Client-preferred slot from `[WALK_THROUGH_WINDOWS]`. Markdown + PDF
  deliverables emailed to Client immediately after the call ends.

Business days are calculated against Provider's calendar (Greek public
holidays observed). Provider may agree on faster turnaround on small
repos but does not guarantee it.

---

## 5. Fees and payment

Total fee: **`[TIER_PRICE]` USD**, paid in full at checkout via the
Stripe Payment Link before this SOW is issued. No additional fees
apply to the engagement scope above.

A separate Greek-myDATA-compliant invoice for tax purposes will be
issued by Provider's accountant within 30 days of payment receipt;
the Stripe receipt does not substitute for the Greek invoice.

---

## 6. Acceptance

The deliverables are deemed accepted **5 business days after the
walk-through call** unless Client emails `hello@roam-code.com` with
specific written objections within that window. If Client raises
objections, Provider will work in good faith to resolve them; if
objections are reasonable and not resolved within 30 calendar days,
the refund clauses in Section 8 apply.

---

## 7. Roam Review subscription credit

Fifty percent (50%) of the engagement fee — **`[CREDIT_AMOUNT]` USD**
(`$1,250` for Team, `$3,000` for Deep) — credits toward Client's
first year of a Roam Review subscription if Client subscribes within
**60 calendar days** of report delivery.

Mechanics:
- Client mentions this SOW (by `[EFFECTIVE_DATE]` and `[CLIENT_LEGAL_NAME]`) when subscribing.
- Provider applies the credit to Client's first invoice.
- Credit is single-use, non-transferable, and non-refundable as cash.
- Credit expires 60 calendar days after report delivery.

Provider does not guarantee a specific Roam Review pricing tier; the
credit applies against whichever tier Client selects, capped at the
credit amount.

---

## 8. Refunds and cancellation

The full Provider Refund Policy at `https://roam-code.com/refund`
governs. The clauses most material to PR Replay engagements:

- **Pre-kickoff (Day 1 only)**: Full refund, no questions asked, on
  Client written request to `hello@roam-code.com`.
- **EU 14-day right of withdrawal**: EU consumers may withdraw within
  14 calendar days of payment per Directive 2011/83/EU. Where Client
  is a business buyer (B2B), the right of withdrawal does not strictly
  apply but Provider honours it in practice.
- **Zero-findings clause**: If a Team engagement surfaces zero findings
  worth wiring into CI (Provider's reasonable judgement, narrated in
  the report), Provider will at Client's choice either (a) re-run the
  replay on a different range at no additional fee, or (b) refund 50%
  of the fee. Deep engagements do not carry this clause due to the
  longer window.
- **Post-kickoff cancellation (Client request)**: Non-refundable, but
  Provider will deliver the work-in-progress at the time of cancellation
  if Client requests it.

---

## 9. Confidentiality and data handling

- **Confidentiality**: Each party will treat the other's confidential
  information as confidential. Provider's commitments around buyer
  source code, diffs, identifiers, and the engagement narrative are
  documented in the DPA.
- **Temporary clone**: Provider clones the repo to a temporary working
  tree only for the duration of the engagement. Provider deletes the
  clone within 7 calendar days of report delivery.
- **No training**: Provider does not use Client source code, diffs,
  comments, metrics, or any derived artefact to train, fine-tune, or
  evaluate any machine-learning model — Provider's, Provider's-via-
  third-party, or any third party's.
- **Sub-processors**: Stripe (billing only); GitHub (only the repository
  Client has authorised). Full sub-processor list in the DPA.
- **Engagement record**: Provider keeps a single-line entry per
  engagement in an internal ledger (`tier`, `client`, `commits scanned`,
  `output path`, `generated_at`) for reconciliation and tax purposes.
  Available to Client on written request.
- **Right to delete**: Client may request deletion of all engagement
  artefacts at any time after acceptance. Provider will comply within
  30 days, except where Greek tax law requires retention of the
  invoice + ledger entry (typically 5 years).

---

## 10. Intellectual property

- **Report ownership**: The report is delivered to Client free of
  ongoing licence fees. Client may redistribute internally without
  restriction. External public redistribution requires written
  permission (a one-line email to `hello@roam-code.com` is sufficient
  for typical cases).
- **Client code**: Client retains all rights in their source code.
  Provider acquires no licence in Client code beyond what is necessary
  to perform this engagement.
- **Provider tooling**: Provider's `roam-code` CLI is Apache 2.0; Client
  may continue to use it after the engagement under that licence.
- **Detector improvements**: Provider may incorporate generalised
  learnings (patterns, rule additions) into the open-source `roam-code`
  detector set. Provider will not include any Client-specific code,
  identifier, or quote in such improvements without written consent.

---

## 11. Termination for cause

Either party may terminate this SOW for material, uncured breach by
the other on 14 calendar days' written notice. On termination:

- Client receives any work-in-progress Provider has produced.
- Provider deletes Client code per Section 9.
- Refund position is determined by Section 8.

---

## 12. Limitation of liability

To the maximum extent permitted by Greek law, Provider's total
aggregate liability under this SOW will not exceed the fees paid by
Client under this SOW. Neither party is liable for indirect,
consequential, or punitive damages. Nothing in this clause limits
liability for fraud, gross negligence, or where applicable law
prohibits exclusion.

---

## 13. Governing law and venue

This SOW is governed by the laws of **Greece**. Disputes are subject
to the exclusive jurisdiction of the courts of **Athens, Greece**.
Each party waives any objection to that venue.

For EU consumer disputes only, the EU Online Dispute Resolution
platform (`https://ec.europa.eu/consumers/odr`) is also available.

---

## 14. Miscellaneous

- **Entire agreement**: This SOW, the DPA, and the Refund Policy are
  the entire agreement on this engagement.
- **Amendments**: Any amendment must be in writing, signed by both
  parties.
- **Severability**: If any clause is unenforceable, the rest remain in
  force.
- **No assignment**: Neither party may assign without written consent.
  Provider may assign to a successor entity (e.g., on incorporation
  into an IKE) on 30 days' written notice.

---

## 15. Signatures

**Provider**

`[PROVIDER_LEGAL_NAME]`
Signature: ____________________  Date: ____________________

**Client**

`[CLIENT_LEGAL_NAME]`
Print name: ____________________
Title: ____________________
Signature: ____________________  Date: ____________________
