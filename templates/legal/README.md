# Legal templates

> **Cluster refreshed 2026-05-18.** This directory holds contract templates
> used for paid roam-code engagements. The four documents below operate as
> one procurement cluster — sign in the order shown, and apply the
> order-of-precedence rule (**SOW > DPA > ToS**) per `dpa.md` §1 when terms
> conflict.

## REVIEW BEFORE USE

These are draft starting points, not legal advice. Each template should be:

1. Read end-to-end by you.
2. Run past a qualified attorney in the relevant jurisdiction before being used to bind a client.
3. Tailored per engagement — bracketed placeholders `[LIKE_THIS]` and
   user-decision fields `[TBD: ...]` show where to fill in.

Canonical governing law for Provider-side execution is **Greek law, courts
of `[TBD: PROVIDER_VENUE_CITY]`** (mirrors `sow-pr-replay.md`,
`nda-mutual.md`, `dpa.md`). Replace the venue placeholder per engagement.

## Files

| File | Purpose | Last polish |
|---|---|---|
| `sow-pr-replay.md` | Current PR Replay Statement of Work template. Copy and customise per engagement. Canonical pricing: **Team $2,500 / Deep $6,000**, with **$1,250 / $3,000** Roam Review credits within 60 days of delivery. | 2026-05-18 |
| `nda-mutual.md` | One-page mutual NDA. Sign at first call when discussing a real codebase. SOW + DPA supersede confidentiality / personal-data clauses on execution (NDA §10A). | 2026-05-18 |
| `dpa.md` | Data Processing Agreement draft for PR Replay and planned Review processing. Required for EU clients before code access. Carries the canonical **14-day subprocessor notice** (§5) and **vuln SLA 1 business day ack / 2 business days triage** envelope. Still needs attorney review and final hosted-service placeholders before Review launch. | 2026-05-18 |
| `security-procurement-packet.md` | Pre-fills the questions a typical security or procurement review will ask about Roam Review (hosted PR bot): data flow, GitHub App permissions, sub-processors, retention, no-training statement, incident response, SLA. Pair with `dpa.md` for binding terms. | 2026-05-18 |

`security-procurement-packet.md` is a planning draft until Roam Review has
named production vendors and GA deployment terms. Do not send it externally
without replacing placeholders and reconciling the Self-Hosted / private-pilot
language.

See the customer-journey email templates in `../email/customer-journey.md`
for the outreach copy that triggers each procurement step below.

## Per-engagement workflow

1. **Discovery call held**, prospect signals interest.
2. Send **NDA** + ask for repo size, languages, current AI tooling.
3. Send **SOW** (right tier filled in) + payment link.
4. Send **DPA** if client is EU-based or asks about data handling.
5. Send **security/procurement packet** when procurement review starts.

## Productized services these templates support

**Template coverage today** (use the SOW template as the contract base for all):

- **PR Replay** — paid historical PR audit. **Template ready**: `sow-pr-replay.md`.

**Forward-looking engagements** (no dedicated SOW template yet — fork
`sow-pr-replay.md` and adapt scope, then route to attorney review before
binding):

- **Agent Governance Evidence Pack** — setup of run ledgers, proof-carrying
  PR bundles, audit-trail export, and control mapping for teams adopting AI
  coding agents.
- **Security Reachability Triage** — focused report on reachable dependency,
  taint, secret, and supply-chain findings.
- **Agent Vendor Benchmark Report** — repo-specific comparison of agent
  workflows and safety outcomes.
- **Custom Rules / Framework Intelligence Pack** — paid rule, extractor, or
  plugin work for a customer's stack.
- **Codebase Due Diligence** — investor / board-facing technical risk report
  covering code health, architecture, security, ownership, and remediation.
- **AI Adoption Readiness Audit** — assessment of where coding agents can
  safely work and which gates must exist first.
- **Migration / Refactor Assurance** — before / after structural evidence for
  large migrations, dependency upgrades, or service splits.
- **Post-Incident Replay** — structural postmortem that turns an incident into
  rules, tests, and Review / Governance gates.

## Anti-pattern reminders

- Do NOT ship to first paying customer without at least the NDA + SOW in place.
- Do NOT skip the DPA for an EU client — first sophisticated procurement blocks
  the deal otherwise.
- Do NOT use these templates without your own attorney's review for the
  governing law you actually intend to use. Liability cap, IP assignment, and
  governing-law sections are the highest-risk clauses to leave at default.
- Do NOT describe governance deliverables as compliance certification. Roam
  deliverables **map to** and **supports evidence for** control frameworks
  (SOC 2, ISO 42001, EU AI Act Article 14); they do **not certify**
  compliance with any framework. Use "evidence export", "control mapping",
  or "supports audit review" unless a qualified attorney approves stronger
  wording. The classification call stays with the client and their DPO.
- Do NOT improvise pricing — match `sow-pr-replay.md` §1 / §7 verbatim
  (Team $2,500 / Deep $6,000, with $1,250 / $3,000 Review credits within 60
  days of delivery). The customer-journey email cohort enforces the same
  numbers; drift in any one place is a drift everywhere.
