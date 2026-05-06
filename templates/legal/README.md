# Legal templates — first drafts

This directory holds the contract templates used for paid roam-code engagements.

## ⚠️ REVIEW BEFORE USE

These are **v1 starting points**, not legal advice. Each template should be:

1. Read end-to-end by you.
2. Run past a qualified attorney in the relevant jurisdiction (Greek for EU clients, Delaware/state-of-incorporation for US clients) before being used to bind a client.
3. Tailored per engagement — bracketed placeholders `[LIKE_THIS]` show where to fill in.

The plan-sourced source-of-truth lives in
`redactedDesktop/memory/roam_code_plan_v1.md`
(SOW / NDA / liability section) and `roam_code_plan_appendix.md` (Phase 2 §6,
Phase 3 §7, Phase 1 §1.10 Union exclusions, Phase 1 §1.7 personal availability
clause).

## Files

| File | Purpose |
|---|---|
| `sow-master.md` | Master SOW with all 3 audit tiers (Indie / Standard / Enterprise) plus rollout add-on. Copy + customise per engagement. |
| `nda-mutual.md` | One-page mutual NDA. Sign at first call when discussing a real codebase. |
| `dpa.md` | Data Processing Agreement (GDPR Article 28-style). Required for EU clients before code access. |
| `refund-guarantee.md` | Public-facing refund copy plus internal SOP for handling refund requests. |

## Per-engagement workflow

1. **Discovery call held**, prospect signals interest.
2. Send **NDA** + ask for repo size, languages, current AI tooling.
3. Send **SOW** (right tier filled in) + Stripe Checkout link (Indie) or Stripe Invoicing (Standard / Enterprise).
4. Send **DPA** if client is EU-based or asks about data handling.
5. After signature + payment, kick off using the kickoff email template (see
   `templates/email/` once that exists, or use Phase 3 templates from the appendix).

## Anti-pattern reminders

- Do NOT ship to first paying customer without at least the NDA + SOW in place.
- Do NOT skip the DPA for an EU client — first sophisticated procurement blocks the deal otherwise.
- Do NOT use these templates without your own attorney's review for the
  governing law you actually intend to use. Liability cap, IP assignment, and
  governing-law sections are the highest-risk clauses to leave at default.

## Greek-specific notes

- Default governing law for EU clients via Stripe Atlas Delaware C-corp:
  Delaware (US). For Greek-resident-of-US-C-corp setup, confirm with Greek
  accountant before relying on Atlas (per pre-mortem 1.9).
- Greek freelancer fallback (no Atlas): invoice with reverse-charge notation
  per Article 14 L.2859/2000. No VAT to US clients; no VAT to EU B2B with VIES
  VAT ID.
