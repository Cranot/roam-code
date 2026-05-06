# Commercial landing-page spec (v2 — subscription-first)

Implementation-ready copy + structure. Built privately during Phase 1, deployed at Phase 3 launch.

**Target domain (claimed 2026-05-06)**: `roam-code.com` — matches PyPI (`roam-code`) + GitHub (`Cranot/roam-code`) exactly; $10.46/yr. The exact-match `.com` (`roamcode.com`) was taken; the hyphenated form preserves naming consistency across the entire stack. Products at subdomains: `www.`, `review.`, `cloud.`, `audit.`, `docs.`.
**Defensive registration**: `roam-code.dev` is already hardcoded as the in-toto attestation predicate URL (`https://roam-code.dev/CodeGraph/v1`); register if cheap, otherwise repoint predicates to `roam-code.com/CodeGraph/v1` in a future release.
**Target stack**: HTML/CSS following `docs/site/` pattern OR a quick builder (Webflow / Carrd / Astro). Copy is the same either way. Page weight under 200 KB.

## v2 vs v1 — what changed

The v1 version of this spec led with 3 audit tiers as the primary pricing cards. **v2 leads with 3 subscription products** (Cloud Lite, Agent Review, Self-Hosted) per `monetization_v2_subscription_pivot.md`. The audit is repositioned as a **white-glove onboarding option** for teams that want hand-holding into the products. Kept v1 copy quality + sample-report + FAQ structure.

## Implementation order (when ready to deploy)

1. Copy this spec into the chosen builder.
2. Wire Stripe Checkout for **Cloud Lite Pro** ($19/repo/mo) — needs Cloud Lite MVP + Stripe Atlas (Phase 0 #1, #8).
3. Wire **GitHub App install link** for Agent Review free tier — needs Agent Review MVP shipped.
4. Wire Calendly link for **Self-Hosted** + **Audit** consultation paths — needs E.1 Calendly screening.
5. Substitute case-study tease once C.1 Express case study drafts.
6. Substitute testimonial once first paying customer ships.
7. Add sample-report download once A.4 PDF moves to a permanent home.
8. **Hold deploy until Phase 2 readiness review passes.**

---

## Page structure (sections in order)

1. **Hero** — new positioning + 3 product anchor CTAs
2. **Three product cards** (Cloud Lite, Agent Review, Self-Hosted)
3. **Audit upsell callout** ("Need white-glove setup?")
4. **"How it works"** — the analysis + scoring flow
5. **Sample report download**
6. **Trust strip** — paper, GitHub stars, EU/Greek positioning
7. **Case study tease**
8. **FAQ**
9. **About**
10. **Footer** — refund guarantee, privacy, terms, sub-processors

---

## 1. Hero

**Eyebrow** (small caps, above headline):
> Code intelligence for the agent era

**Headline** (h1, ~10 words):
> **Your team uses Claude, Cursor, Codex.**
> **Roam tells you when AI-generated changes are structurally risky.**

**Subhead** (~25 words):
> A local code-graph engine + a hosted dashboard + a PR bot. Indie devs run Roam free; engineering teams pay for the parts that protect production.

**Primary CTAs** (3 anchor-linked buttons):
- [ Roam Cloud Lite — $19/repo/mo ]  → #cloud
- [ **Roam Agent Review — $20/dev/mo** ]  → #review (MOST POPULAR badge)
- [ Roam Self-Hosted — from $5K/yr ]  → #self-hosted

**Hero badge row** (small, under CTAs):
> Apache 2.0 CLI · Source code never leaves your machine · GDPR + EU AI Act ready · 30-day refund guarantee

---

## 2. Three product cards `#products`

Three side-by-side cards, with **Agent Review** visually highlighted (MOST POPULAR pill, slight border emphasis). Each card has its own anchored block and its own pricing micro-table inside.

### Card 1 — Roam Cloud Lite `#cloud`

**Tag**: For teams that want to track structural health over time.

**One-liner**: Hosted dashboard. Metrics + history. **Source code never uploaded.**

**Sub-tiers**:

| Tier | Price | What you get |
|---|---|---|
| Free | $0 | Public repos, 30-day rolling history |
| **Pro** | **$19/repo/mo** | Private repos, unlimited history, Slack/Linear alerts |
| Team | $99/mo | 10 repos, unlimited users, custom thresholds |
| Growth | $299/mo | Unlimited repos, SSO, audit-log export (EU AI Act) |

**Primary CTA** (per sub-tier): `[ Sign up free ]` (Free) → free dashboard. `[ Start Pro — Stripe Checkout ]` (Pro/Team/Growth) → Stripe one-click.

**Footer note**: Fully self-serve. No sales call.

### Card 2 — Roam Agent Review `#review` ⭐ MOST POPULAR

**Tag**: For teams 6+ months into Cursor / Cline / Claude Code.

**One-liner**: GitHub / GitLab PR bot scoring AI-generated changes for structural risk. Catches "Cursor changed this confidently but broke 47 callers" before merge.

**Sub-tiers**:

| Tier | Price | What you get |
|---|---|---|
| Free | $0 | Public repos, 1 PR comment per push |
| **Team** | **$20/dev/mo** | Private repos, custom rules, Slack/Linear webhooks |
| Business | $499/mo | Up to 50 devs, audit-log export, SSO |
| Enterprise | from $15K/yr | Self-hosted, named CSE, custom rules consulting |

**Primary CTA** (per sub-tier): `[ Install on GitHub ]` (Free/Team) → GitHub App install link. `[ Talk to me ]` (Business/Enterprise) → Calendly 30-min slot.

**Footer note**: Free tier is permanent on public repos. Same budget as CodeRabbit ($24/user/mo) and Greptile ($30/seat/mo) — different layer (structural, not semantic).

### Card 3 — Roam Self-Hosted `#self-hosted`

**Tag**: For regulated industries + security-conscious enterprises.

**One-liner**: All of Cloud Lite + Agent Review, deployed in your VPC. Air-gapped, license-keyed.

**Sub-tiers**:

| Tier | Price | What you get |
|---|---|---|
| Startup | $5K/year | ≤25 devs, email support, 5-day SLA |
| Business | $15K/year | ≤200 devs, Slack support, 2-day SLA, custom-rules consulting (4 hrs/qtr) |
| Enterprise | $25K-$100K/year | Unlimited, SSO, named CSE, 1-day SLA, white-glove onboarding |

**Primary CTA**: `[ Talk to me ]` → Calendly 45-min slot. Sales-led only.

**Footer note**: Most Self-Hosted deals start with the AI Agent Readiness Audit (below) as the "let's see if this fits" first step.

---

## 3. Audit upsell callout

Small visual block under the 3 product cards. **Not** a competing primary CTA — positioned as the white-glove on-ramp.

> **Need someone to set this up for you?**
> An AI Agent Readiness Audit takes 5–10 days. You get a 15-page report
> covering the structural risks in your repo, a 30/60/90 day fix roadmap,
> and a ready-to-commit `CLAUDE.md` / `AGENTS.md` drop-in. Most clients
> continue to one of the products above after delivery.
>
> Tiers: $1,800 Indie · **$4,500 Standard** · $12,000 Enterprise · $22,000 closer bundle (Audit + Rollout + 6 mo Pro support).
> 30-day refund guarantee.
>
> [ Book a 30-min call → ] [ Download sample report (PDF, 1.4 MB) → ]

The "Download sample report" CTA links to A.4 (`templates/audit-report/sample-redacted.pdf` once moved).

---

## 4. How it works

3-column layout or a stacked sequence depending on builder.

### "1. Index your codebase"

Run roam-code locally — it builds a SQLite graph of your symbols, dependencies, layers, and git history. Apache 2.0, zero API keys, your code never leaves your machine.

### "2. Connect to Cloud or Agent Review"

The CLI pushes **only metrics** (numbers, not source) to Roam Cloud Lite. The GitHub App runs analysis on your PRs and posts the risk score as a comment. Both tiers are no-config to start.

### "3. Tighten the loop"

Configure thresholds. Encode your architecture rules in `.roam/rules.yml`. Stream alerts to Slack or Linear. Block merges that exceed risk thresholds.

---

## 5. Sample report download

Single block with a button:

> **Curious what an audit deliverable looks like?**
> Download the redacted sample (PDF, 15 pages) — same structure paying clients receive.

[ Download sample report ] — links to A.4 redacted PDF.

---

## 6. Trust strip

Horizontal strip with quick proof points:

- 📄 [Paper](https://arxiv.org/abs/...) on prompt engineering at LLM scale
- ⭐ `[STARS]+` GitHub stars on roam-code
- 🇬🇷 EU-based · GDPR-aligned · No source code transmitted (CLI / Cloud Lite) · Air-gapped option (Self-Hosted)
- 🛡️ Apache 2.0 CLI · Zero API keys

---

## 7. Case study tease

(Stub until C.1 / C.2 sanitised OSS-repo case studies are written.)

> **Real-world examples**
>
> - **Express.js** — _What roam caught: 47 SAFE-bucket dead exports, 3 cycles, and 1 god-component that explained 80% of recent merge-conflict velocity._ [Read the case study →]
> - **Vue.js core** — _Where AI agents lost the plot, and the 5-rule `.roam/rules.yml` we recommended._ [Read the case study →]

Each link goes to the published case-study post.

---

## 8. FAQ

Reordered for v2 — trust + safety + product fit go first.

**Is my source code safe?**
Yes — the CLI runs **100% locally**. Roam Cloud Lite receives only metrics (numbers, file paths, identifier names) — never source code. Roam Agent Review runs analysis on PR diffs in our cloud, but you can self-host if your security policy requires it. roam-code itself is open-source and zero-API-key by design.

**How is this different from CodeRabbit / Greptile / Qodo?**
Those tools review **semantics** — they read the diff and tell you if the logic looks right. Roam Agent Review reviews **structure** — it tells you if the diff breaks invariants in the graph (cycles, fan-in, layer violations, bus-factor risks). They coexist; many teams run both.

**How is this different from SonarQube / CodeScene?**
Roam ships a graph engine designed for AI-agent workflows from day one (e.g. ready-to-commit `CLAUDE.md` / `AGENTS.md`, MCP server with 128 tools). SonarQube and CodeScene predate the agent era; they're great at what they do, but the integrations are bolt-on.

**What languages do you support?**
27 languages — including Python, JavaScript, TypeScript, Vue, PHP, Java, Go, Rust, C#, Ruby, Kotlin, Scala, SQL, Apex / Lightning. Full list and Tier-1 vs Tier-2 details on the open-source repo.

**Do you sign NDAs?**
Yes — mutual NDA goes out before any repo access (audit) or any private-repo connection (Cloud Lite / Agent Review). Greek law / Delaware / Ireland governing-law options.

**What's the refund policy?**
Audits: full refund within 30 days if the report doesn't surface 5+ actionable findings. Subscriptions: cancel anytime, prorated refund of the unused portion of the current month. No questions, no hoops.

**Do you do follow-up implementation?**
Yes — Rollout SOWs (Audit's $4.8K-$45K add-on tier) cover wiring the recommendations into CI + setting up `.roam/rules.yml` rules + custom integrations.

**EU AI Act compliance?**
The audit-log-export feature in Cloud Lite Growth and Self-Hosted Business+ is built specifically for Article 12 (audit-trail of AI-generated code). Greek/EU-based provider; GDPR Article 28 DPA available on request.

**Can I run the CLI without paying?**
Yes — roam-code (the CLI + MCP server) is open-source under Apache 2.0 forever. The paid products are all *separate hosted layers* on top of the OSS — none of them gate CLI capability.

---

## 9. About

> **Dimitris (Cranot)** — built `roam-code` and the [paper] on prompt engineering at LLM scale. Greek/EU-based, dogfooding the tool daily on real codebases since 2025. Day-job background in commercial software, which is why this practice deliberately excludes Greek B2B accounting / ERP / POS / tutoring vendors (conflict-of-interest disclosure on every SOW).

Photo: optional, low-key. Skip if it makes the page slower.

---

## 10. Footer

- 30-day refund guarantee → /refund-guarantee
- Privacy policy → /privacy
- Sub-processor list → /sub-processors
- Terms → /terms
- DPA template → email request
- Status page → /status (once any hosted product exists)
- GitHub: roam-code | claude-code-guide
- Email: `[contact]`
- LinkedIn: `[profile]`
- arXiv paper: `[link]`

---

## Design notes

- **Type**: Source Sans Pro / Inter for body, JetBrains Mono / Fira Code for code blocks. 16-17 px on desktop.
- **Color**: monochrome with a single accent — match docs/site/index.html for visual continuity (the existing site already uses Space Grotesk + IBM Plex Mono per the modified header; can match).
- **Pricing-card visual hierarchy**: Agent Review card scaled at 1.0; Cloud Lite + Self-Hosted at 0.95. MOST POPULAR pill on Agent Review only.
- **No animations / scroll-triggered effects.** Buyers in this segment want signal, not spectacle.
- **No exit-intent popup / chat widget.** Both lower trust at this price point.
- **Mobile**: pricing cards stack vertically; everything else flows naturally with `max-width: 720px` on text columns.
- **OG / Twitter card**: "Roam — code intelligence that catches AI-generated regressions before merge." Image: simple typographic poster, no stock photography.

---

## Anti-patterns (do not do these)

1. **No "Free Audit" CTA.** Free audits attract tire-kickers and drag delivery slots away from paying clients. The 30-min discovery call is where free value lives. The CLI is the genuine free product; pushing a free audit on top is value erosion.
2. **No fake scarcity.** "Only 3 slots left this month" reads as desperate at the persona level (VP Eng / Platform). Real scarcity from delivery capacity is fine to mention organically.
3. **No client-logo wall** until logos are real. Stock-photo client logos kill credibility instantly with this audience.
4. **No newsletter signup as primary CTA.** Funnel is Cloud Lite signup or Agent Review GitHub install. Newsletter dilutes funnel and triggers procurement skepticism.
5. **No "AI-generated reports!" tagline.** Contradicts the determinism pitch.
6. **No price-anchoring against CodeRabbit / Greptile.** Position as **complementary**, not competitive. Roam owns architecture; they own semantics.
7. **No "limited time offer" pricing.** All tiers stay published; no fake discounts. (Founding-customer pricing is OK — but disclose openly, don't dress up as urgency.)
8. **No Self-Hosted self-serve.** That tier is sales-led for a reason; pretending otherwise will burn the relationship the first time procurement notices the absence of a contract.
