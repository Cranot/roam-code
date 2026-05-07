# Customer-journey email templates

> ⚠️ Drafts. Pricing and offer references in these emails predate the current
> pricing on roam-code.com/pricing — review and update before sending.

11 templates covering qualification → sale → delivery → retention → crisis.

## Personalisation rules

- Replace bracketed `[placeholders]`; never send raw.
- Reference one specific thing the prospect said on a previous call (not just
  their company name) — that single sentence is the difference between a
  template and a personal note.
- Length stays short. The discovery flow tolerates 3–5 sentence emails much
  better than wall-of-text paragraphs. If a template exceeds 200 words after
  personalisation, cut.

---

## 4. Calendly screening question

Single field on the booking form, required:

> "In one sentence: what AI coding tool is your team using and what's the most
> recent thing it got wrong?"

Auto-DQ message for vague/generic answers (no specific tool, no specific pain):

> Thanks for booking! Looking at your answer, sounds like you're in good shape
> right now. I'd recommend re-engaging when you hit a specific issue — happy
> to chat then. Cancelling this slot so it goes to someone with active pain.

## 5. Pre-call email — 24h before discovery

> **Subject**: Tomorrow's call — quick prep
>
> [first_name], looking forward to talking tomorrow.
>
> Two things help me come prepared:
> 1. **Repo size + stack**: which languages, monorepo or polyrepo, ~LoC?
> 2. **Current AI tooling**: what's deployed, who's using it, since when?
>
> One-line answers are fine. If you can share the repo URL (even private — I
> won't browse without permission), I'll come with 1-2 specific observations
> to discuss.
>
> Talk tomorrow.
>
> — `[YOUR_NAME]`

## 6. Discovery call closer — in-call script

Use at minute 25 of a 30-min call, after they've described pain + goals:

> "Based on what you described, I think the **Standard Audit (`[FEE]`)** is
> the right fit — it covers your repo size, gives you the full report + 2
> calls + 30-day Q&A. Shall I send the proposal now to lock this week's
> slot, or do you want me to follow up after you discuss with [boss/team]?"

Two-option close (send-now OR follow-up) beats yes/no — either answer moves
the deal forward.

## 7. Proposal email

> **Subject**: Proposal — Standard Audit for [Company]
>
> [first_name], as discussed:
>
> **Scope**: 1 repo (~[X] LoC), 10-day delivery, full 15-page report + 2
> walkthrough calls + 30-day Q&A Slack channel.
>
> **Investment**: `[FEE]`. (Optional founding-customer pricing: `[DISCOUNT]`% off if
> you can share a public testimonial after delivery — your call.)
>
> **Timeline**: Kickoff [date], delivery [date].
>
> Sign + pay here: [DocuSign / Stripe link]. Includes mutual NDA + SOW +
> 30-day refund guarantee.
>
> Questions? Reply or [Calendly link] for a quick call. Proposal expires
> [date + 7 days].
>
> — `[YOUR_NAME]`

## 8. Kickoff email — sent on payment confirmation

> **Subject**: Welcome — your audit kicks off [date]
>
> Payment received, thank you. Three things to start:
>
> 1. **Repo access** (pick one):
>    - Add `[your audit email]` as a temporary GitHub collaborator (preferred —
>      7-day rotation).
>    - Email a git bundle to [secure email] (I'll provide encrypted upload link).
>    - Screen-share session — I run roam-code on your machine, no code transfer.
>
> 2. **Slack/email channel**: I'll send daily 3-sentence status updates from
>    Day 1 to delivery. Tell me which you prefer.
>
> 3. **Walkthrough call**: book your delivery-day call here [Calendly link,
>    60-min slot].
>
> Delivery date: **[date]**. If anything blocks me, you'll know the same day.
>
> Excited to dig in.
>
> — `[YOUR_NAME]`

## 9. Daily status email

> **Subject**: Day [X]/[N] — [client name] audit
>
> **Today**: [3–5 word summary]
> **Tomorrow**: [3–5 word summary]
> **On track for**: [delivery date]
> **Need from you**: [if anything; usually nothing]

Keep it that short. Daily presence > daily essay.

## 10. Post-delivery email — the value moment

> **Subject**: Your audit is ready
>
> [first_name], the full report is attached. Quick orientation:
>
> - **Pages 1-2**: Executive summary — read these first.
> - **Pages 3-13**: Findings, recommendations, suggested CLAUDE.md drop-in.
> - **Pages 14-15**: 30 / 60 / 90 day fix roadmap.
>
> Loom walkthrough: [link, 8 min]. Watch on 1.5×.
>
> **Q&A available**: 30 days. DM/email anytime.
>
> Two asks:
> 1. **Testimonial**: 2-3 sentences on what you found valuable.
>    Link: [Typeform / Google Form, 5 questions, 5 min].
> 2. **Retainer**: Monthly check-ins ($1,400/mo Pro) keep these wins
>    compounding. Want a quick call to discuss? [Calendly link].
>
> Thanks for the trust on this one.
>
> — `[YOUR_NAME]`

## 11. Day-7 follow-up

> **Subject**: Quick check on [audit project name]
>
> [first_name], quick check — did anything in the report not land? Want me to
> clarify any finding?
>
> Also — saw [recent development at their company]. Worth flagging that the
> [specific audit finding X] connects to that. Happy to talk through it.
>
> — `[YOUR_NAME]`

## 12. Retainer pitch — paragraph version (or 3-slide deck)

For audit clients 30+ days post-delivery.

> **Where you were** (start of audit): [their starting context, paste from
>   exec summary].
>
> **What we found**: [top 3 findings — paste from report].
>
> **How a Pro retainer keeps the wins compounding**:
> - Monthly health re-runs (catch regressions before they ship).
> - 2 new architecture rules per quarter (your rules, encoded into CI).
> - Slack support, 2-day SLA (questions during implementation).
>
> **Investment**: $1,400/mo, cancel anytime. Annual prepay 10% off.
>
> Want a 20-min call to walk through what would land first? [Calendly link]

## 13. Referral ask — Day 30

> **Subject**: One quick ask
>
> [first_name], one quick ask — if you know one other team that would benefit
> from an Agent Readiness Audit, intro me by email and I'll give them 15% off
> + you 15% off your next service. No pressure if no fit.
>
> P.S. did the [specific recommendation from audit] get implemented? Always
> curious how recommendations land.
>
> — `[YOUR_NAME]`

## 14. Crisis communication — delayed delivery

When you have to extend a delivery date.

> **Subject**: Heads-up on [audit project name] — new delivery date
>
> [first_name], heads-up: I'm hitting an unexpected [scope challenge / personal
> availability issue]. New delivery date: **[X + 5 days]**. Two options if
> this doesn't work:
>
> 1. Stick with new date (full deliverable, 5 days late) — most common choice.
> 2. Refund unworked portion (estimate: $[X]).
>
> Either way, no charge for the delay itself. I'll send daily updates from now
> to delivery. Sorry for the change.
>
> — `[YOUR_NAME]`

---

## Anti-patterns (don't ever do these)

- Don't promise specific findings before the audit runs. Say "I expect to
  find X-class issues based on what you described" — never "I will find X."
- Don't apologise for pricing. If pricing is wrong for the prospect, end the
  conversation respectfully — don't discount on the call.
- Don't send proposals without an expiration date (default: 7 days).
- Don't move to retainer pitch on Day 0 — wait for the value moment to land
  (Day 7+) before asking for monthly recurring.
- Don't cold-blast template 4-14 to every prospect at once; each is keyed to
  a specific stage.
