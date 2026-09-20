# Statement of Work — Roam PR Replay

> **REVIEW BEFORE USE.** v5 draft, 2026-09-20. Have a qualified
> attorney review before binding execution and an accountant validate the
> invoice wording and applicable tax treatment. Bracketed placeholders
> `[LIKE_THIS]` are filled in per engagement. If Stripe Payment Links are
> live, checkout custom fields should map 1:1 to **Section 2 (Scope)**.
> If payment is handled by manual invoice, collect the same fields before
> countersignature.

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

The replay window and points of focus are captured either at checkout via
Stripe Payment Link custom fields or during manual-invoice intake:

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

- **Day 0** — Client pays via Stripe Payment Link or agrees the manual
  invoice / bank-transfer path in writing; Provider confirms payment or
  written purchase approval before work begins.
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

Total fee: **`[TIER_PRICE]` USD**, paid in full before kickoff via the
payment path named in the order form or invoice. No additional fees apply
to the engagement scope above.

A separate Greek-myDATA-compliant invoice for tax purposes will be
issued by Provider's accountant within 30 days of payment receipt;
the Stripe receipt does not substitute for the Greek invoice. The invoice
follows the applicable VAT treatment. EU B2B reverse charge is applied
where applicable, not solely because a VAT-ID is supplied. Provider's
accountant confirms the treatment from the service, the parties' business
status and establishments, and applicable rules. Confirm the billing
details and any applicable VAT in writing before payment.

---

## 6. Acceptance

The deliverables are deemed accepted **5 business days after the
walk-through call** unless Client emails `hello@roam-code.com` with
specific written objections within that window. A timely objection keeps
the disputed findings open. Provider supplies its evidence response within
10 business days of receiving the objection. Findings still disputed
30 calendar days after receipt receive the refund treatment in Section 8;
requests for more information do not restart this deadline. Statutory and
already-agreed contractual rights are not reduced by this acceptance window.

---

## 7. Roam Review subscription credit

Fifty percent (50%) of the engagement fee — **`[CREDIT_AMOUNT]` USD**
(`$1,250` for Team, `$3,000` for Deep) — credits toward Client's
first year of a Roam Review subscription if Client subscribes within
**60 calendar days after Roam Review reaches general availability**.
Roam Review is not available to subscribe to today. The redemption window
starts at general availability, not at report delivery; no launch date is
promised by this SOW.

Mechanics:
- Client mentions this SOW (by `[EFFECTIVE_DATE]` and `[CLIENT_LEGAL_NAME]`) when subscribing.
- Provider applies the credit to Client's first invoice.
- Credit is single-use, non-transferable, and non-refundable as cash.
- Credit expires 60 calendar days after Roam Review reaches general availability.

Provider does not guarantee a specific Roam Review pricing tier; the
credit applies against whichever tier Client selects, capped at the
credit amount.

---

## 8. Refunds and cancellation

The Provider Refund Policy at `https://roam-code.com/refund` supplements
this SOW; this SOW takes precedence where they diverge. This revised
finding-review process applies only to new engagements expressly adopting
the September 20, 2026 terms. Already-agreed customer rights, including
earlier refund guarantees, remain unchanged. The clauses most material
to PR Replay engagements:

- **Pre-kickoff (Day 1 only)**: Full refund, no questions asked, on
  Client written request to `hello@roam-code.com`.
- **EU 14-day right of withdrawal**: EU consumers may withdraw within
  14 calendar days of payment per Directive 2011/83/EU. Where Client
  is a business buyer (B2B), the right of withdrawal does not strictly
  apply but Provider honours it in practice.
- **Finding-review guarantee**: An unsupported finding is one whose
  material claim is not supported by the evidence against the agreed
  repository snapshot, scope and methodology. A later code change does
  not by itself disprove a finding about the agreed snapshot. Client
  identifies the finding and disputed claim by email within 5 business
  days after the walk-through, including any relevant evidence Client has.
  No independent expert is required. Within 10 business days of receipt,
  Provider supplies matched source locations, recorded check results and
  reasoning, or removes the finding as unsupported. Provider may not
  introduce a new assessment standard after delivery. If still disputed
  30 calendar days after receipt, the finding is marked unresolved and
  excluded from surviving findings for refund purposes, without requiring
  agreement that it is technically false. Requests for more information
  do not restart the deadline; Provider's disagreement cannot postpone
  refund treatment. Unsupported findings are removed from the report.
- **Refund calculation**: Each unsupported or deadline-unresolved finding
  receives an equal share of the fee: fee paid multiplied by excluded
  findings divided by all findings in the original delivered report.
  Count each finding once. If fewer than half the original high/medium
  findings survive review, Client receives a full refund. If the report
  surfaces zero material findings, the refund is 100% of the fee (not 50%).
  These full-refund thresholds take precedence over the pro-rata calculation.
  Original finding IDs and severity levels are fixed for this calculation;
  adding, splitting or reclassifying findings during review cannot reduce
  the refund. Total refunds are capped at the fee paid. Provider initiates
  the refund within 5 business days after review closes or the
  30-calendar-day deadline, whichever comes first; payment-provider
  processing time is additional. There is no review fee. Client keeps
  the delivered report and evidence packet. This applies to Team and Deep
  engagements alike and does not restrict statutory remedies or
  already-agreed rights.
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
- **Personal-data breach notification**: Where the engagement involves
  Personal Data within the meaning of the GDPR, breach-handling is
  governed by **DPA Section 9** (Article 33 GDPR — notification without
  undue delay and, where feasible, within 72 hours).

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
- **Aggregate detector improvements** (the "dogfood right"): Provider
  may incorporate **non-identifying, aggregate learnings** from the
  engagement into the open-source `roam-code` detector set. Scope of
  the right is limited to:
  (a) new or refined detector heuristics and rules,
  (b) anonymised counts and ratios (e.g., "30% of engagements have an
      N+1 pattern in their auth path") without naming Client, the repo,
      contributors, or any code path,
  (c) test fixtures synthesised to reproduce a pattern, where the
      fixture contains no Client source, identifier, comment, or quote.
  Provider will **not** publish, quote, or reference Client source code,
  file paths, function names, commit messages, contributor identities,
  or any other identifying detail without Client's prior written consent.

---

## 11. Termination for cause

Either party may terminate this SOW for material, uncured breach by
the other on 14 calendar days' written notice. On termination:

- Client receives any work-in-progress Provider has produced.
- Provider deletes Client code per Section 9.
- Refund position is determined by Section 8.

---

## 12. Warranty disclaimer and limitation of liability

### 12.1 Warranty disclaimer

The deliverables are provided on an **"as-is" structural-review basis**.
Provider warrants only that the engagement is performed with reasonable
skill and care, using the `roam-code` detector set in effect at the
**`[EFFECTIVE_DATE]`**. Provider makes no warranty, express or implied,
that the report:

- identifies every structural risk in the codebase or commit range,
- is exhaustive of the categories listed in **Section 2 ("What is out
  of scope")** above,
- substitutes for semantic correctness review, security audit,
  penetration testing, or performance profiling,
- **certifies** Client's codebase as compliant with any law, regulation,
  framework, or standard, or **makes** Client compliant with any of the
  foregoing. Where the report references control families, frameworks,
  or standards, it **maps to** and **supports evidence for** them; it
  does not constitute certification.

Recommended CI gates and remediation items are professional
recommendations, not guarantees of incident prevention.

### 12.2 Limitation of liability

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

The EU Online Dispute Resolution platform closed on 20 July 2025.
The European Commission provides current
[consumer redress information](https://consumer-redress.ec.europa.eu/site-relocation_en).

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
