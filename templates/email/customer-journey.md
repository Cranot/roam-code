# Customer-journey email templates

> Drafts for PR Replay engagements. Review before sending: fill placeholders,
> confirm the payment path, and avoid implying the hosted Roam Review app is
> live until that launch path is ready.

Templates covering qualification -> sale -> delivery -> retention -> crisis.
Each template names the **playbook SOP** it triggers and the **funnel
stage** it sits in:

```
cold-introduce  ->  /audit visit  ->  SOW sign  ->  engagement-kickoff
                                                            |
                                                            v
                            delivery  ->  Roam Review upsell
```

**Read these first before personalising:**
- `templates/distribution/landing-page/audit.html` — the public buyer page
  emails point to (canonical prices, credit math, tier copy)
- `templates/legal/sow-pr-replay.md` — the SOW the buyer countersigns

## Personalisation rules

- Replace bracketed `[placeholders]`; never send raw.
- Reference one specific thing the prospect said on a previous call.
- Keep emails short. If a template exceeds 200 words after personalisation,
  cut it.
- Do not promise specific findings before Roam has run.
- Never overclaim certification — PR Replay **maps to** and **supports
  evidence for** control frameworks; it does **not certify compliance**
  with SOC 2, ISO 42001, the EU AI Act, or any other framework (mirrors
  SOW §12.1).
- Pricing is fixed at `$2,500` (Team) / `$6,000` (Deep) with `$1,250` /
  `$3,000` Roam Review credits within 60 days of delivery. Match
  `/audit` and SOW §1 / §7 verbatim; do not improvise.

## Honest-banner discipline (mandatory canonical wording)

When an email references compliance, audit, or governance framing, use
this canonical clause verbatim:

> PR Replay produces a structural-review report that **maps to** and
> **supports evidence for** SOC 2 / ISO 42001 change-management controls
> and, where your own system is high-risk, EU AI Act Article 14
> human-oversight controls. It does **not certify** compliance with any
> framework; the classification call stays with you and your DPO.

This mirrors SOW §12.1 (warranty disclaimer) and the report's
honest-banner clause (`templates/audit-report/sample-pr-replay-team.md`).

## GDPR opt-out (append to every outbound)

The footer below MUST appear in every cold-introduce, proposal, and
upsell email (not strictly required on transactional emails — kickoff,
daily status, post-delivery, crisis — but harmless to include and
simpler to keep it universal):

```
---
You're receiving this because you booked a call / opened a paid
engagement / asked about PR Replay. Reply with the word "unsubscribe"
or email hello@roam-code.com to be removed from all future outreach;
we'll confirm removal within 2 business days and never email again.
Data-handling details: https://roam-code.com/privacy and
https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md
```

---

## 1. Calendly screening question — funnel stage: cold-introduce

Single field on the booking form, required:

> "In one sentence: what AI coding tool is your team using and what is the
> most recent thing it got wrong?"

Auto-DQ message for vague/generic answers:

> Thanks for booking. Looking at your answer, it sounds like you are in good
> shape right now. I would recommend re-engaging when you hit a specific issue.
> Cancelling this slot so it goes to someone with active pain.

## 2. Pre-call email — 24h before discovery — funnel stage: cold-introduce

Triggers: nothing operator-side; this is pre-engagement.

> **Subject**: Prep for tomorrow's call — repo + AI tooling
>
> [first_name], looking forward to talking tomorrow.
>
> Two things help me come prepared:
> 1. **Repo size + stack**: languages, monorepo/polyrepo, rough LOC.
> 2. **Current AI tooling**: what is deployed, who uses it, and since when.
>
> One-line answers are fine. If you can share a public repo URL or a redacted
> example PR, I will come with 1-2 concrete observations.
>
> If you want to skim what a paid engagement produces before the call:
> [https://roam-code.com/audit](https://roam-code.com/audit) (tiers + sample
> report + the credit math).
>
> Talk tomorrow.
>
> — [TBD: founder signature]

## 3. Discovery call closer — funnel stage: /audit visit -> SOW sign

Use at minute 25 of a 30-minute call, after they have described pain and goals.

This is the call where you point the buyer at `/audit` for the canonical
prices + sample report, and offer to send the SOW to lock the delivery slot.
Triggers: playbook **Pre-flight** (verify the buyer page + Stripe Payment
Links are live before quoting).

> "Based on what you described, **PR Replay [Team/Deep]** is the right fit.
> Team is `$2,500` (30 PRs, 30-min walk-through, `$1,250` credits toward
> Roam Review within 60 days). Deep is `$6,000` (90 PRs, 90-min walk-through,
> per-detector deep-dive + 90-day remediation plan, `$3,000` credits).
>
> Same credit math + sample report at roam-code.com/audit. Want me to send
> the SOW now to hold this week's delivery slot, or follow up after you
> discuss with [boss/team]?"

## 4. Proposal email — funnel stage: SOW sign

Triggers: playbook **Day 0** (Stripe payment lands → 1-hour ack → 24-hour
SOW package).

> **Subject**: PR Replay [Tier] proposal for [Company]
>
> [first_name], as discussed:
>
> **Scope**: PR Replay [Team / Deep]. [Team: trailing 30 merged PRs,
> 5-business-day delivery, 30-min walk-through / Deep: trailing 90 PRs
> (or agreed range), 10-business-day delivery, 90-min walk-through +
> per-detector deep-dive + written 90-day remediation plan].
>
> **Investment**: `$[2,500 / 6,000]`. **`$[1,250 / 3,000]` credits toward
> Roam Review** if you subscribe within 60 days of delivery. Credit math
> + sample report shape: [roam-code.com/audit](https://roam-code.com/audit).
>
> **What it does NOT do**: certify compliance with any framework. The
> report **maps to** and **supports evidence for** SOC 2 / ISO 42001
> change-management controls and, where your own system is high-risk, EU
> AI Act Article 14 human-oversight controls — useful as evidence, not a
> certification.
>
> **Timeline**: kickoff [date], delivery [date].
>
> Sign here: [SOW link — fill from Day 0 step 5]. Pay here: [Stripe
> Payment Link / invoice instructions]. Includes the
> [DPA](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md),
> [refund terms](https://roam-code.com/refund), and repo-access options
> ([security policy](https://roam-code.com/security)).
>
> Questions? Reply or [TBD: Calendly link] for a quick call. Proposal
> expires [date + 7 days].
>
> — [TBD: founder signature]

## 5. Kickoff email — funnel stage: engagement-kickoff

Send on payment or written purchase-approval confirmation.
Triggers: playbook **Day 0 step 9** (kickoff email template, verbatim
in playbook).

> **Subject**: Kickoff package — your PR Replay starts [date]
>
> Payment / purchase approval confirmed, thank you. Three things to start:
>
> 1. **Repo access** (pick one):
>    - Add the deploy-key public key below as a temporary read-only key
>      on [REPO_URL]: `[PUBLIC_KEY]`
>    - Send a git bundle through [secure upload path].
>    - Screen-share session: I run roam-code on your machine, no code
>      transfer. (Data-handling details:
>      [roam-code.com/privacy](https://roam-code.com/privacy).)
>
> 2. **Status updates**: I will send daily 3-sentence updates from Day 1
>    to delivery. Tell me Slack or email.
>
> 3. **Walkthrough call**: book your delivery call here
>    [TBD: Calendly link, `[30/90]`-minute slot].
>
> Delivery date: **[date]**. If anything blocks me, you will know the
> same day.
>
> — [TBD: founder signature]

## 6. Daily status email — funnel stage: engagement-kickoff -> delivery

Triggers: playbook **Day 2–N** (replay execution).

> **Subject**: Day [X]/[N] — [client name] PR Replay
>
> **Today**: [3-5 word summary]
> **Tomorrow**: [3-5 word summary]
> **On track for**: [delivery date]
> **Need from you**: [if anything; usually nothing]

Daily presence beats daily essays.

## 7. Post-delivery email — funnel stage: delivery

Triggers: playbook **Day N step 19** (send report ~10 min before walk-
through call); see playbook for the verbatim "ahead of our call" body.
This template covers the **post-call** email that ships the final deliverable.

> **Subject**: Your PR Replay report — final deliverable
>
> [first_name], the final report is attached (Markdown + PDF). Quick
> orientation:
>
> - **Executive summary**: read this first.
> - **Eight-evidence-questions table**: what the report covers + what's
>   explicitly Out of scope (per the honest-banner discipline at SOW
>   §12.1).
> - **Detector breakdown**: detector classes, example PRs, severity
>   ranking.
> - **Recommended CI gates**: the few checks worth wiring into CI now.
>
> Reminder on framing: the report **maps to** and **supports evidence
> for** SOC 2 / ISO 42001 change-management controls and, where your own
> system is high-risk, EU AI Act Article 14 human-oversight controls; it
> does **not certify** compliance with any framework. Useful as evidence
> for your auditor, not a substitute for certification.
>
> Walkthrough: [link / calendar invite already sent].
>
> **Q&A available**: through `[DATE]` (5 business days from the walk-
> through call, per SOW §6 acceptance window). DM/email anytime.
>
> Two asks:
> 1. **Testimonial**: 2-3 sentences on what landed. Link:
>    [TBD: testimonial-form URL].
> 2. **Review follow-up**: Want me to price the Roam Review founding-
>    customer path for this repo using your **`$[1,250 / 3,000]` PR
>    Replay credit**? [TBD: Calendly link].
>
> Thanks for the trust on this one.
>
> — [TBD: founder signature]

## 8. Day-7 follow-up — funnel stage: delivery -> Roam Review upsell

Triggers: playbook **Day N+5** (acceptance window closes); useful prompt
to confirm the buyer is silent (= accepted) or surface objections early.

> **Subject**: Day-7 check on [PR Replay project name]
>
> [first_name], quick check — did anything in the report not land? Want me
> to clarify any finding?
>
> Also, [specific finding X] is the one I would wire into CI first because
> it connects directly to [pain they described]. Happy to talk through it.
>
> Reminder: the SOW §6 acceptance window closes [date — 5 business days
> after walk-through call]. If nothing is wrong, no action needed on
> your side.
>
> — [TBD: founder signature]

## 9. Roam Review follow-up pitch — funnel stage: Roam Review upsell

For PR Replay clients 7–30 days post-delivery.
Triggers: playbook **Day N+60** (credit window closes); send the soft
nudge at D+45 per the playbook reminder.

> **Subject**: Apply your `$[1,250 / 3,000]` Review credit — [Company]
>
> **Where you were**: [their starting context, pasted from the exec summary].
>
> **What we found**: [top 3 findings].
>
> **How Roam Review keeps the wins compounding**:
> - Same structural checks on every new PR.
> - Repo-specific rules encoded into CI instead of living in a report.
> - A monthly evidence digest for engineering leadership (maps to SOC 2
>   CC7.x change-management evidence; does not certify SOC 2).
>
> **Investment**: `[REVIEW_TIER]`, with **`$[1,250 / 3,000]` credit from
> PR Replay** applied to the first year if signed by `[CREDIT_EXPIRY —
> 60 calendar days from delivery per SOW §7]`.
>
> Want a 20-minute call to walk through what would land first?
> [TBD: Calendly link]

## 10. Referral ask — Day 30 — funnel stage: Roam Review upsell

> **Subject**: One quick ask — referral on [Company]
>
> [first_name], one quick ask — if you know one other team that would benefit
> from PR Replay, intro me by email and I will give them 15% off + you 15% off
> your next service. No pressure if no fit.
>
> P.S. did [specific recommendation from the report] get implemented? Always
> curious how recommendations land.
>
> — [TBD: founder signature]

## 11. Crisis communication — delayed delivery — funnel stage: delivery

When you have to extend a delivery date.
Triggers: playbook **When things go wrong → mid-replay smaller than
expected**. Refund position resolves via SOW §8.

> **Subject**: New delivery date — [PR Replay project name]
>
> [first_name], heads-up: I am hitting an unexpected [scope challenge /
> availability issue]. New delivery date: **[X + 5 days]**.
>
> Two options if this does not work:
> 1. Stick with the new date — full deliverable, 5 days late.
> 2. Refund the unworked portion per SOW §8 — estimate: $[X]. Refund
>    policy: [roam-code.com/refund](https://roam-code.com/refund).
>
> Either way, no charge for the delay itself. I will send daily updates
> from now to delivery. Sorry for the change.
>
> — [TBD: founder signature]

## 12. Chargeback-acknowledgment — funnel stage: post-delivery dispute

Triggers: playbook **When things go wrong → buyer disputes the credit-
card charge**, step 1 (try to resolve before responding to Stripe;
refund-policy commitment: acknowledge within 2 business days, resolve
within 5).

> **Subject**: Your PR Replay chargeback — let's resolve directly
>
> [first_name], Stripe just notified me of a chargeback on the [Team /
> Deep] PR Replay engagement [date]. Before this goes through the
> Stripe dispute process, I would like to resolve it with you directly.
>
> What works for you?
> 1. Acknowledge a specific issue with the deliverable and I issue a
>    partial / full refund directly (faster than Stripe's process, and
>    avoids the chargeback-handling fee per
>    [roam-code.com/refund](https://roam-code.com/refund)).
> 2. We get on a 15-minute call to walk through whatever didn't land,
>    and you withdraw the dispute.
> 3. You confirm you want the Stripe process; I respond there with the
>    signed SOW + delivery proof + engagement record per the refund
>    policy.
>
> I respond to Stripe within 7 days regardless. Either way, here's a
> 2-business-day acknowledgement per our refund commitment.
>
> — [TBD: founder signature]

## 13. Right-to-delete acknowledgment — funnel stage: post-delivery

Triggers: playbook **When things go wrong → buyer requests deletion of
engagement artefacts (DPA §7 / SOW §9)**, step 1 (acknowledge within 2
business days; comply within 30 calendar days; state Greek tax
retention exception explicitly).

> **Subject**: Deletion request received — [PR Replay project name]
>
> [first_name], confirming receipt of your deletion request for the
> [Team / Deep] PR Replay engagement [date]. Per SOW §9 "Right to
> delete" and our DPA §7, I will comply within **30 calendar days**.
>
> What gets deleted:
> - Temporary clone (already deleted on Day N+1 per DPA §7.1).
> - The delivered report copies (PDF + Markdown) on my side.
> - All working-file notes containing your repo identifiers, file
>   paths, or contributor names.
>
> What is retained (Greek tax obligation, ~5 years):
> - The Greek-myDATA invoice issued by my accountant.
> - The single-line ledger entry: `tier`, `client`, `commits scanned`,
>   `output path`, `generated_at`.
>
> Confirmation of deletion will land in your inbox by **[date + 30
> calendar days]**.
>
> — [TBD: founder signature]

## 14. Day N+90 retention-deletion confirmation — funnel stage: post-delivery

Triggers: playbook **Day N+90** (DPA §7.3 retention enforcement); send
when the 90-day follow-up window has elapsed and the buyer has not
already invoked SOW §9.

> **Subject**: 90-day follow-up window closed — report copies deleted
>
> [first_name], a quick housekeeping note. Ninety calendar days have
> passed since the [Team / Deep] PR Replay delivery on [date], so per
> our DPA §7.3 I have now deleted the delivered report copies on my
> side. You still have your own copies and the report remains yours;
> this is purely about Provider-side retention.
>
> What is retained (Greek tax, ~5 years): the single-line ledger entry
> + the Greek-myDATA invoice. Nothing identifying your source.
>
> If you ever need a re-issue from the engagement record, reply and I
> can confirm exactly what's retained per DPA §7.4–7.5.
>
> — [TBD: founder signature]

---

## Anti-patterns

- Do not promise specific findings before the replay runs.
- Do not apologise for pricing. If pricing is wrong for the prospect, end the
  conversation respectfully.
- Do not send proposals without an expiration date.
- Do not pitch hosted Review as generally available before the launch path is
  ready.
- Do not cold-blast templates to every prospect; each is keyed to a stage.
- Do not write "certifies SOC 2 / ISO 42001 / EU AI Act compliance" or any
  variant — the report **maps to** and **supports evidence for** controls,
  never certifies. Mirror SOW §12.1 verbatim when in doubt.
- Do not invent customer quotes, case studies, prior-engagement counts, or
  testimonials. Leave `[TBD: testimonial-form URL]` and similar markers in
  place until real ones land.
- Do not omit the GDPR opt-out footer on cold-introduce / proposal / upsell
  outbound. Transactional emails (kickoff, daily status, post-delivery,
  crisis, chargeback, deletion, retention) are exempt by GDPR but harmless
  to include.

## [TBD: ...] placeholders the operator must resolve before first send

- [TBD: founder signature] — the literal sign-off block (name, title,
  one-line context). Used in every template; resolve once and substitute
  globally.
- [TBD: Calendly link] — used in templates 2, 4, 5, 7, 9 for booking the
  walk-through call + Review follow-up call. One Cal.com / Calendly URL;
  resolve once and substitute globally.
- [TBD: testimonial-form URL] — used in template 7 (post-delivery ask).
  Could be a Typeform, a Google Form, or a plain `mailto:` — pick one
  and resolve.
- [TBD: secure upload path] — used in template 5 (kickoff) for the git
  bundle alternative path. Currently `mailto:` works for small bundles;
  if engagements grow, switch to a temporary signed-URL pattern and
  document it here.
