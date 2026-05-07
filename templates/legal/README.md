# Legal templates

This directory holds contract templates used for paid roam-code engagements.

## ⚠️ REVIEW BEFORE USE

These are **v1 starting points**, not legal advice. Each template should be:

1. Read end-to-end by you.
2. Run past a qualified attorney in the relevant jurisdiction before being used to bind a client.
3. Tailored per engagement — bracketed placeholders `[LIKE_THIS]` show where to fill in.

## Files

| File | Purpose |
|---|---|
| `sow-master.md` | Master Statement of Work template. Copy and customise per engagement. |
| `nda-mutual.md` | One-page mutual NDA. Sign at first call when discussing a real codebase. |
| `dpa.md` | Data Processing Agreement (GDPR Article 28-style). Required for EU clients before code access. |
| `security-procurement-packet.md` | Pre-fills the questions a typical security or procurement review will ask about Roam Review (hosted PR bot): data flow, GitHub App permissions, sub-processors, retention, no-training statement, incident response, SLA. Pair with `dpa.md` for binding terms. |

## Per-engagement workflow

1. **Discovery call held**, prospect signals interest.
2. Send **NDA** + ask for repo size, languages, current AI tooling.
3. Send **SOW** (right tier filled in) + payment link.
4. Send **DPA** if client is EU-based or asks about data handling.
5. Send **security/procurement packet** when procurement review starts.

## Anti-pattern reminders

- Do NOT ship to first paying customer without at least the NDA + SOW in place.
- Do NOT skip the DPA for an EU client — first sophisticated procurement blocks the deal otherwise.
- Do NOT use these templates without your own attorney's review for the
  governing law you actually intend to use. Liability cap, IP assignment, and
  governing-law sections are the highest-risk clauses to leave at default.
