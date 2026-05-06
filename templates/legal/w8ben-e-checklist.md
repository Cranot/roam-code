# W-8BEN-E pre-fill checklist

> ⚠️ Pre-fill the **PDF** of W-8BEN-E using your details. The IRS PDF lives at <https://www.irs.gov/pub/irs-pdf/fw8bene.pdf>. **First US enterprise client's procurement will demand this within 24 hours of contract signature**, so have it ready before Phase 3 launch.

The W-8BEN-E (Certificate of Foreign Status of Beneficial Owner — Entities) is the form a US payor uses to confirm Greek-treaty 0% withholding instead of the default 30%. Most US enterprise procurement teams require it; some accept the smaller W-8BEN if you're paid as an individual rather than via a corp.

**Which form to use:**

- **W-8BEN-E** — if Provider is the Stripe Atlas Delaware C-corp (entity).
- **W-8BEN** — if Provider is invoicing personally as an individual. Different form, simpler, also Greece-treaty 0% withholding via Article 7.

This checklist covers W-8BEN-E. Adapt for W-8BEN if invoicing personally.

## Greek-resident-of-US-C-corp note

Counterintuitively, an Atlas Delaware C-corp owned by a Greek resident
files **W-9** (US entity) for US payors, not W-8BEN-E (foreign entity). The
W-8BEN-E only applies if the Provider entity itself is foreign (e.g. a
Greek IKE, a Cyprus Ltd, a UK Ltd).

**Confirm with the Greek accountant (Phase-0 task #1)**: which entity
structure are you actually invoicing through? The form follows the entity,
not the owner.

For now, this checklist assumes the **Greek-individual / Greek-IKE foreign-
entity** path — the W-8BEN or W-8BEN-E. If/when Atlas C-corp is the
invoicing entity, swap to W-9.

## Pre-fill values (W-8BEN-E)

Open the PDF and fill the following. **Type**, don't handwrite — most US
procurement OCRs the form.

### Part I — Identification of Beneficial Owner

| Field | Value |
|---|---|
| 1. Name of organisation | `[Greek IKE legal name]` (skip if no Greek entity yet) |
| 2. Country of incorporation | **Greece** |
| 3. Disregarded entity name | (leave blank unless a US LLC owns this) |
| 4. Chapter 3 status | **Corporation** (for IKE) — if individual, use W-8BEN instead |
| 5. Chapter 4 status (FATCA) | **Active NFFE** (Non-Financial Foreign Entity, Active) is the most common applicable box for a small consultancy with non-financial revenue > 50% |
| 6. Permanent residence address | `[Greek street, postal code, city]`, **Greece**. NO PO Box. |
| 7. Mailing address | Same as #6 (or different if applicable) |
| 8. US TIN | (leave blank — you don't have one) |
| 9a. GIIN | (leave blank — only if registered with IRS for FATCA, not applicable) |
| 9b. Foreign TIN | `[Greek Α.Φ.Μ. — 9 digits]` |
| 9c. Check box | (only if Greek law doesn't issue Foreign TIN; AFM does, leave unchecked) |
| 10. Reference numbers | (optional, leave blank) |

### Part III — Claim of Tax Treaty Benefits

This is the part that drops withholding from 30% to **0%** for services.

| Field | Value |
|---|---|
| 14a. Resident of treaty country | Tick the box. Country: **Greece**. |
| 14b. Special rates / conditions | Tick the box. **Article 7** (Business Profits) of the Greece-US Tax Treaty (1953 / 1955 protocol). 0% withholding rate for services not attributable to a US permanent establishment. |
| 15. Special rates and conditions | Optional explanation: *"The beneficial owner is a Greek resident entity providing professional services from Greece, with no US permanent establishment. Treaty Article 7 applies; rate of withholding 0%."* |

### Part XXV — Active NFFE (FATCA self-certification)

If you ticked "Active NFFE" in Field 5:

- Tick the box at Part XXV affirming: less than 50% of gross income from
  passive sources AND less than 50% of weighted-average assets are
  passive-income assets.

That's the typical small-services consultancy profile.

### Part XXX — Certification

- Print name: `[Cranot full legal name]`
- Capacity: **Director** (if Greek IKE) or **Owner** (if sole proprietor)
- Date: `[FILING_DATE]`
- Signature: handwrite OR digital sig in a recognised PDF signing tool
  (DocuSign, Adobe Sign).

## Verification before sending

- [ ] All boxes filled exactly per the IRS instructions PDF.
- [ ] Foreign TIN matches your Greek AFM.
- [ ] No US TIN field accidentally populated (this would imply you have a US
      tax obligation you don't).
- [ ] Treaty article cited correctly: **Article 7** for services.
- [ ] PDF signed and scanned at 300+ DPI.
- [ ] Save filed copy to `private/w8bene/[YEAR]-[CLIENT].pdf`.

## When to refresh

- Re-fill **annually** if you stay in long-term retainer relationships.
  W-8BEN-E expires 3 calendar years after signing; some US procurement
  refreshes annually as a policy.
- Re-fill if any field changes (name, address, AFM, treaty citation).
- Re-fill if Greek tax law / treaty changes (rare; 1953 treaty has been
  stable).

## Anti-patterns

- **Don't** sign a US W-9 if you're a foreign entity / individual. W-9 is
  for US persons; signing one wrongly can trigger US tax obligations.
- **Don't** skip Part III (treaty claim) — without it, withholding stays at
  30% and you're chasing the IRS for refunds the next year.
- **Don't** mix entities — if invoicing through Atlas C-corp, file W-9
  (US entity), not W-8BEN-E.
