# Statement of Work — Master Template

> ⚠️ **REVIEW BEFORE USE.** v1 draft, 2026-05-05. Run past a qualified attorney before binding execution. Bracketed placeholders `[LIKE_THIS]` must be filled in per engagement.
> Superseded for launch until rewritten. This SOW describes the old audit
> service and old prices; do not use for PR Replay, Roam Review, Roam Cloud,
> or Self-Hosted deals as written.

---

## Parties and effective date

This Statement of Work ("**SOW**") is entered into on `[EFFECTIVE_DATE]` between:

- **Provider**: `[PROVIDER_LEGAL_NAME]`, `[PROVIDER_ENTITY_TYPE]` (e.g. redacted Α.Φ.Μ. `[VAT_ID]`), with registered address at `[PROVIDER_ADDRESS]` ("**Provider**", "we", "us").
- **Client**: `[CLIENT_LEGAL_NAME]`, with registered address at `[CLIENT_ADDRESS]` ("**Client**", "you").

This SOW incorporates by reference the mutual non-disclosure agreement signed `[NDA_DATE]` (the "**NDA**") and, where applicable, the data processing agreement signed `[DPA_DATE]` (the "**DPA**").

---

## 1. Engagement

Provider will perform an **AI Agent Readiness Audit** at the **`[TIER]`** tier (one of: Indie / Standard / Enterprise) and any add-ons selected below, on the codebase identified in **Section 2 (Scope)**.

**Tier selected** (mark one):

- [ ] **Indie / Startup Audit** — USD **$1,800** — 1 repo ≤30K LoC — 5 business-day turnaround.
- [ ] **Standard Audit** — USD **$4,500** — 1 repo 30-200K LoC — 10 business-day turnaround.
- [ ] **Monorepo / Enterprise Audit** — USD **$12,000** — multi-repo or >200K LoC — 3-week turnaround.

**Optional add-ons:**

- [ ] **Rollout Lite** — USD **$4,800** — CI integration on 1 repo + team training (2 hrs).
- [ ] **Full Rollout** — USD **$14,500** — multi-repo CI, MCP server, dashboards, half-day workshop.
- [ ] **Custom rules pack** — USD **$3,500** for 5 rules (or `[CUSTOM]`).

---

## 2. Scope

Provider will deliver, for the codebase at `[REPO_URL_OR_DESCRIPTION]` (the "**Codebase**"):

1. A written audit report (15-20 pages, PDF) covering:
   - Executive summary with top 3 findings and recommended priority.
   - Repository overview and architecture map.
   - Health scorecard (composite 0-100 score with category breakdown).
   - Top 10 risk findings with file paths, root causes, and suggested fixes.
   - Dead-code candidates grouped SAFE / REVIEW / INTENTIONAL.
   - Ownership and bus-factor risks per directory.
   - Test coverage gaps.
   - Suggested `CLAUDE.md` and `AGENTS.md` drop-in (ready to commit).
   - Suggested CI gates tied to specific findings.
   - 30 / 60 / 90 day fix roadmap.
2. Walkthrough call(s) per tier:
   - **Indie**: 1 × 60-min walkthrough call.
   - **Standard**: 2 × calls (kickoff + delivery).
   - **Enterprise**: kickoff + delivery + dev-team workshop (2 hrs).
3. Q&A window after delivery:
   - Indie: not included.
   - Standard: 30 days via shared Slack channel or email.
   - Enterprise: 60 days via shared Slack channel or email.
4. Loom video walkthrough (Standard / Enterprise only, 5-10 min).

---

## 3. Out of scope

This SOW does **not** include:

- Implementation of recommended fixes (available separately under a Rollout SOW).
- Refactoring, code generation, or modifications to Client's codebase.
- Ongoing monitoring, alerts, or retainer support beyond the Q&A window.
- Custom rule authoring beyond what is bundled in the selected tier (available separately under a Custom Rules SOW).
- Training of Client engineers beyond the calls and workshop listed in Section 2.
- Performance benchmarking, load testing, or production observability.
- Security pentest or third-party penetration testing.
- Anything not explicitly listed in Section 2.

---

## 4. Timeline

- **Kickoff**: within `[N]` business days of payment confirmation (Indie/Standard) or initial deposit (Enterprise).
- **Delivery**: per the tier turnaround listed in Section 1, measured in business days from kickoff.
- **Personal availability clause**: Provider may extend delivery by up to 7 calendar days for personal or family circumstances, with same-day notice. Beyond 14 calendar days of cumulative extension, Client may request refund of the unworked portion.

---

## 5. Fees and payment

Total fee: **USD `[TOTAL_FEE]`** (sum of selected tier and add-ons).

Payment terms by tier:

- **Indie ($1,800)** and **Standard ($4,500)**: 100% upfront via Stripe Checkout at `[CHECKOUT_URL]`. Engagement begins on payment confirmation.
- **Enterprise ($12,000+)**: 50% upfront / 50% on delivery via Stripe Invoicing, NET 7. Provider may pause delivery if invoice exceeds 7 days past due.
- **Rollout / Custom Rules add-ons**: priced per Section 1; payment terms match the audit tier.

All fees are quoted in USD. Bank fees, currency conversion, and applicable VAT (if any) are Client's responsibility. Provider is `[VAT_STATUS]` (e.g. "not VAT-registered" / "VAT-registered Α.Φ.Μ. ___ — applies reverse-charge per Article 14 L.2859/2000 to EU B2B").

---

## 6. Client responsibilities

To enable timely delivery, Client will:

1. Provide repo access via one of: temporary GitHub collaborator (preferred, 7-day rotation), git bundle to Provider's encrypted upload endpoint, or screen-share session.
2. Identify a primary point of contact reachable within one business day during the engagement.
3. Provide non-confidential context as requested (current AI tooling, pain points, recent incidents).
4. Sign the NDA prior to repo access.
5. Sign the DPA if Client is EU-based or processes personal data via the Codebase.

---

## 7. Provider responsibilities and data handling

Provider will:

1. Process the Codebase **locally** on Provider's encrypted machine. No code is transmitted to third-party AI services. Provider's `roam-code` analysis tool is 100% local and zero-API-key by design (Apache 2.0, source: <https://github.com/Cranot/roam-code>).
2. Maintain a per-engagement access log: `client_id, file_accessed, timestamp, action`. Available on request.
3. Delete Client code and derivative working files within 14 calendar days of delivery (90-day extension by amendment for re-runs). Deletion confirmed in writing.
4. Notify Client within 72 hours of becoming aware of any breach affecting Client's data, per GDPR Article 33 timelines.
5. Provide daily status emails (3-4 sentences) for the duration of delivery.

Sub-processors:

- **Stripe** (billing data only; no Client code).
- **`[OTHER_TOOLS]`** as disclosed in the DPA, if any.

If Client opts in (separate written authorization), Provider may use Anthropic API for AI-summarised findings only on Client's explicit request. Default is **OFF** — no AI calls are made on Client code.

---

## 8. Intellectual property

- **Custom rules and extractors built specifically for Client**: work-for-hire — Client owns the work product upon full payment. Provider retains a non-exclusive, royalty-free licence to use generic patterns (not Client-identifiable code) for upstream contributions to roam-code OSS.
- **Generic improvements upstreamed to roam-code**: jointly owned where attributable; the Apache 2.0 licence applies to upstream releases.
- **roam-code itself**: Provider's pre-existing IP, licenced to Client under Apache 2.0 (https://github.com/Cranot/roam-code/blob/main/LICENSE).
- **Audit report and recommendations**: Client owns the report content for internal use. Provider may use sanitised, Client-anonymised excerpts as case studies only with Client's prior written permission (templates available on request).

---

## 9. Confidentiality

The NDA governs confidentiality of Client information. This SOW does not modify the NDA's terms. The NDA survives termination of this SOW for 3 years.

---

## 10. Liability cap and indemnity

Provider's total liability under this SOW (whether in contract, tort, or otherwise) is capped at **1× the fees actually paid by Client under this specific SOW**. Excluded from the cap: gross negligence, wilful misconduct, breach of confidentiality, breach of IP warranties.

Provider warrants that the deliverables are Provider's original work and do not knowingly infringe third-party IP. No other warranties — express or implied — are given. The deliverables are provided "as-is".

Client agrees to indemnify Provider for third-party claims arising from Client's use of the deliverables, subject to the same cap.

---

## 11. Refund and re-do guarantee

Per Provider's published guarantee: if the audit does not surface 5 or more actionable findings, Client may request a full refund within 30 days of delivery, no questions asked. "Actionable finding" means a specific, file-or-symbol-level recommendation that Client could implement.

---

## 12. Termination

Either party may terminate with 7 days' written notice. On termination:

- Provider refunds the unworked portion of any prepayment, calculated pro-rata against business days elapsed.
- Client is invoiced for any unbilled work completed.
- Provider deletes Client code per Section 7.4 within 7 calendar days of termination.

---

## 13. Governing law and disputes

This SOW is governed by the law of **`[GOVERNING_LAW]`** (default: Delaware, USA for US Clients; Greek law for Greek Clients; Republic of Ireland for other EU Clients). Disputes will be resolved by good-faith negotiation, escalating to binding arbitration in `[FORUM]` after 30 days. Each party bears its own fees unless the arbitrator awards otherwise.

Notwithstanding the above, either party may seek injunctive relief in any competent court for breaches of confidentiality or IP.

---

## 14. Miscellaneous

- **Entire agreement**: this SOW + the NDA + the DPA (if applicable) constitute the entire agreement between the parties on this subject.
- **Amendments**: only by written agreement signed by both parties.
- **Severability**: if any clause is held unenforceable, the remainder stays in force.
- **Assignment**: Provider may assign this SOW to a successor entity (e.g. on incorporation change). Client may not assign without Provider's written consent.
- **Notices**: by email to `[PROVIDER_EMAIL]` and `[CLIENT_EMAIL]`.

---

## Signatures

By signing below, both parties accept the terms above.

**Provider**

- Signed: ____________________
- Name: `[PROVIDER_NAME]`
- Title: `[PROVIDER_TITLE]`
- Date: ____________________

**Client**

- Signed: ____________________
- Name: `[CLIENT_SIGNATORY]`
- Title: `[CLIENT_TITLE]`
- Date: ____________________
