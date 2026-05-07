# Legacy cold outreach DM templates

> Superseded for launch. These drafts still use the old Agent Readiness audit
> offer. Before sending anything, rewrite around **PR Replay** and **Roam
> Review** using `docs/strategy/pricing-v4-launch-2026-05-07.md`.

Drafted for Phase 3 launch. Each template personalises in 30-60 seconds with
one specific reference (their post / their company / their role / a recent
incident). Generic blasts get <1% reply; personalised gets 5-10%.

## Hard rules (per pre-mortem 1.1)

- **Decision-makers only.** First 30 contacts must be Head/VP/Director titles.
  Save IC outreach for week 4+ once a decision-maker conversation is in flight.
- **One specific reference per DM.** No "I saw your work and..." — name the
  post, the company event, the LinkedIn post.
- **Calendly link only after consent.** Lead with a real question; if they
  bite, then drop the link.
- **Apply Union exclusion list before sending** to any Greek company per
  appendix Phase 1 §1.10.

---

## X DM — Variant A: "Recent Cursor pain"

For prospects who recently posted about a Cursor / Claude Code incident.

> Hi [first_name] — saw your post about [specific Cursor/Claude Code pain].
> I built roam-code (open-source, used in 50+ codebases) to fix exactly that
> for teams 6mo into agent rollout. Doing 3 free 30-min Agent Readiness
> diagnostics this week — happy to run one on your repo and share what I see.
> No pitch on the call; just want feedback on the new format.

**Personalisation slots**: `[first_name]`, `[specific Cursor/Claude Code pain]`.

**Length**: 50-60 words. **Hit-rate target**: 8-12% reply on personalised.

## X DM — Variant B: "Big monorepo"

For prospects whose company is known to run a large monorepo and they've
posted about scaling pain.

> Hi [first_name] — noticed [Company] is at [size or commit count] in your
> monorepo. The pattern Cursor / Claude Code stops handling around 200K+ LoC
> usually shows up as silent context truncation on cross-module changes.
> Built roam-code specifically for that size. Worth a 20-min look at your
> blast-radius patterns? No pitch.

## X DM — Variant C: "Sev-2 incident"

For prospects whose company recently had a public post-mortem citing AI tooling.

> Hi [first_name] — sorry to see the post-mortem on [incident]. roam-code's
> `risk` and `pr-risk` commands catch the architectural pattern that usually
> causes those (high-churn × high-complexity × high fan-in files that AI
> agents disproportionately touch). If you're rebuilding the prevention layer,
> happy to run a 30-min diagnostic on the affected repos at no cost.

## LinkedIn DM — Decision-maker variant

For VP Eng / Head of Platform / Head of DevEx titles. Slightly more formal.

> [first_name], quick question — your team has been on Cursor/Claude Code for
> ~[X] months now. What's the #1 thing it still gets wrong on your codebase?
>
> Asking because I'm running 5 free Agent Readiness diagnostics this month
> for VPs of Eng and Heads of Platform. Output is a 15-page report with
> top-10 fixes you can implement directly. Built on roam-code (open source,
> Apache 2.0, ~[STARS]★). Worth 30 minutes? [Calendly link only after consent.]

## Cold email — Warm-intro variant

For prospects introduced by a mutual contact.

> **Subject:** [Referrer name] suggested I reach out
>
> Hi [first_name],
>
> [Referrer name] mentioned you might find value in this.
>
> I run AI Agent Readiness Audits for Series B/C SaaS teams 6 months into
> Cursor / Claude Code rollout. The output is a 15-page report covering risky
> files, dead code, ownership hotspots, suggested CI gates, and a ready-to-
> commit CLAUDE.md / AGENTS.md file. About 60-90 minutes of reading, weeks of
> implementation savings.
>
> Backed by [roam-code](https://github.com/Cranot/roam-code) (open source,
> Apache 2.0, [STARS]★) and the [arXiv paper] on prompt engineering at LLM
> scale.
>
> Three packages: $1,800 Indie / $4,500 Standard / $12,000 Enterprise.
> 30-day refund guarantee.
>
> Worth a 30-min call to see if it fits? [Calendly link]
>
> — Dimitris (Cranot)

---

## Personalisation discovery sources

When prospect-sourcing, mine these for triggers:

- **Cursor Forum** (`forum.cursor.com`) — Bug Reports + Help threads tagged
  monorepo, multi-root workspace, Cursor Rules.
- **GitHub Issues** on `cline/cline`, `RooCodeInc/Roo-Code`, `getzep/graphiti`,
  `block/goose` — search "context", "monorepo", "lost context".
- **Reddit** weekly: "monorepo AI", "Cursor lost context", "agent broke
  production", r/ClaudeAI, r/cursor, r/ExperiencedDevs.
- **LinkedIn job posts** with "Agent QA Lead", "AI Ops Manager", "AI Platform
  Engineer" — each = budget being formed.
- **Stack Overflow blog + InfoQ AI track** — outage post-mortems; reach out
  same week.
- **EU AI Act compliance Q&A pages** on Bird & Bird, HSF Kramer.

## Tracking

Keep a simple log in `private/outreach-log.md` (do not commit publicly):

| Date | Channel | Prospect | Title | Reference used | Reply? | Call booked? | Outcome |
|---|---|---|---|---|---|---|---|

Review weekly: at week 4, if reply rate is <3%, halt and review messaging
before continuing.
