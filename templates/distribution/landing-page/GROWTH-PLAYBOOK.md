# Roam growth playbook

Pre-launch strategic decisions captured for execution when their gates open.
Written 2026-05-07. Each section ends with a clear "build when X" trigger.

## 1. Hero proof-point block (M10)

**Current**: 4-cell numbers grid showing "201 commands · 136 MCP tools · 27
languages · 7,731 PyPI installs/month."

**Upgrade plan when**: Roam Review GitHub App ships (Phase 2 in monetization v2)

Each cell becomes a short evidence link:
- "201 commands" → /docs (cookbook)
- "136 MCP tools" → /setup#mcp
- "27 languages" → /docs/languages
- "7,731 installs/month" → live PyPI stats badge

Each cell adds a screenshot or mini-diagram once we have proper visual
assets. Don't ship vector illustrations; ship terminal screenshots that
look like the actual product.

## 2. Demo / explainer video (M12)

**Decision**: defer until Phase 2 (Roam Review).

A 30-60s explainer GIF/MP4 showing a real PR getting reviewed, the bot
posting a comment, the human merging. Conversion data on B2B SaaS
landing pages is split — Vercel/Linear use video; Stripe doesn't. For
Roam specifically, a CLI-output screen-recording is on-brand and fits
the "no fluff" voice. MP4 with poster image, autoplay muted, captions
forced on.

**Build when**: real Roam Review screencast is recordable (i.e., Phase 2
GitHub App MVP works on roam-code's own PRs).

**Format**: 1080p MP4, ≤5 MB, ≤45 seconds, captions burned in. Hosted
self (no YouTube embed — third-party JS, off-brand). CSP allows
same-origin video.

## 3. ROI calculator (M15)

**Decision**: NO calculator. Reason: pre-product, the input variables
("PRs/week", "incidents prevented", "time saved per review") are all
made up. A made-up calculator is worse than no calculator.

**Build when**: we have at least 5 paying customers + their actual
metrics. Then a calibrated calculator becomes a closing tool for Sales.

Until then: a static "Cost of one outage" callout would be honest:
"One Cursor-agent incident at PocketOS = 30 hours of downtime + $X in
restoration. Roam Review at $25/dev/mo is the cheapest insurance against
that class of bug." Drop into the pricing page audit-upsell area when
the messaging needs reinforcing.

## 4. Industry-vertical pages (M16)

**Decision**: defer to month 6+.

Building /for/fintech, /for/healthcare, /for/eu-public-sector before we
have a representative customer in each is content theater. We'd write
generic pages that don't differentiate, and they'd dilute the homepage's
SEO authority.

**Build when**: at least 3 customers in a single vertical. First
predicted vertical based on audit-trail demand: fintech (credit-scoring
falls under EU AI Act Annex III). Second: healthtech. Third: edtech.

In the meantime: weave specific vertical examples into homepage scenarios
when they're representative. E.g., "Fintech credit-scoring teams use
Roam Self-Hosted for the audit trail" can appear in /security or in
about/ if/when we land that customer.

## 5. Blog / engineering-content strategy (M17)

**Strategy**: long-form pillar content, not weekly updates.

Three pieces to write before launch (Phase 2):

1. **"Catching the AI clone-not-edited bug — a deep dive"** (3000 words)
   Walks through the detector, the AST canonicalization step, the
   confidence scoring. Targets engineers who are skeptical it works.
   Deepest possible technical credibility.

2. **"What an audit trail for AI-generated code actually needs"** (2500 words)
   Explains SOC 2 CC8.1, ISO 42001, the EU AI Act narrowness, the
   actual log fields that matter, why hash-chained signed records.
   Counters the cargo-cult "Article 12" marketing on this category.

3. **"Roam vs CodeRabbit, Greptile, Qodo — full review"** (4000 words)
   Honest, head-to-head review of all four including running them
   against the same PR-fixture set. Includes wins for each competitor.
   This earns links and trust because it doesn't hide weaknesses.

Hosting: each as a /blog/<slug> page on the marketing site. No separate
blog engine; static HTML matching the rest of the site. RSS feed at
/feed.xml. Updates Sitemap+lastmod.

**Build when**: Phase 1 (decorator-driven Capability Registry + naming +
Phase 0 fact fixes) complete. Phase 2 work can begin in parallel.

## 6. GitHub Marketplace listing (M18)

**Critical**: when Roam Review ships, the Marketplace listing IS the
landing page for ~50% of buyers (per CodeRabbit pattern: 15K customers,
mostly bottoms-up via Marketplace).

Pre-write checklist:
- [ ] App name: "Roam — structural code review"
- [ ] Tagline: 80 chars max — "A second opinion on every PR. Catches
  clone-not-edited, blast-radius, runtime-hot-path issues your AI
  reviewer misses."
- [ ] Description: 800-1200 words. First sentence is the hook. List
  permissions + clearly state what data we don't store.
- [ ] Category: "Code review" (primary), "Continuous integration"
  (secondary)
- [ ] Pricing: monthly + annual; free for OSS forever
- [ ] Screenshots: 5 minimum. Each annotated. Show: install, first PR
  review, severity gate in CI, OSS-found-bug example, verbose mode.
- [ ] Setup video: 90 seconds, captions burned in
- [ ] Permissions: read-only on PR diffs; no repo-write; no admin

GitHub takes a 5% cut on Marketplace. Worth it for the discovery
volume — alternative is paid SaaS-marketplace (G2, Capterra) which is
both more expensive and lower intent.

## 7. Per-page OG images (M20)

**Decision**: ship dynamic OG images via Cloudflare Worker generated on
the fly.

Currently every page shares /og.png. That's adequate. Per-page OG cards
are a polish item, not a dealbreaker.

**Upgrade plan**: a Cloudflare Worker that takes a page slug, fetches
the page title + description from the static HTML, renders an SVG with
the title + Roam branding, returns a PNG. Cached at the edge.

Effort: ~3 hours for the Worker + ~1 hour to wire `og:image` URLs to
point at /og/<slug>.png.

**Build when**: enough Twitter/LinkedIn shares that per-page cards
matter (target: 10+ shares per page in a 7-day window — verifiable from
referrer logs).

## 8. Internationalization (M25)

**Decision**: English-only for the first year.

Reasons:
- ICP (VP Eng / Tech Lead at Series B-C SaaS) operates in English
  professionally regardless of country
- German + French translations require a full 14-page rewrite + an i18n
  toolchain we don't have
- /privacy and /terms in non-English would need legal review (extra cost)

**Re-evaluate when**: 25%+ of CF Analytics referrer traffic is from
non-English markets AND we have a customer in that market asking for it.

If it happens: Astro's i18n flow + native German/French speaker for
copy review + DPA review of /privacy translation. Don't auto-translate.

## 9. Authority signaling — about/team/funding (M26)

**Current state**: /about exists, mentions sole-trader Athens.

**Strengthen with**:
- Founder bio with prior work + credentials (link to LinkedIn or
  technical blog if available)
- "Why we built this" 200-word origin story (already partially there)
- Funding source: "customer-funded, no VC" — already in /about, keep
- Specific verifiable claims (years of experience, prior projects, OSS
  contributions)
- A real photo on /about (low-key, professional, not a stock image)

**Build now (this week)**: photo + 2-line credentials sentence on /about.

## 10. Press / launch-PR plan (M27)

**Pitch list when Roam Review launches**:
- Pragmatic Engineer newsletter (Gergely Orosz) — high-quality engineering
  audience overlapping our ICP
- The Changelog podcast — devtool-friendly, OSS-friendly
- Console newsletter — devtool roundup
- Lobsters + Hacker News (organic post by founder, not press release)
- Ben Lorica's data-engineering newsletter — broader AI/coding overlap
- Software Engineering Daily podcast — long-form interview format

Embargo strategy: 48-hour embargo with the 3 newsletter writers (Pragmatic
Engineer, Console, Changelog) coordinated for a Tuesday morning launch.
HN/Lobsters posts go up the same day.

**Build now**: press kit at /press is already complete. Pitch list
above is the next deliverable when Phase 2 lands.

## 11. Inbound link / SEO-PR (M28)

Top 5 backlink targets ranked by authority:

1. **artificialintelligenceact.eu** "tools" page — single highest-authority
   link for compliance keywords. Pitch via their contact form.
2. **awesome-mcp-servers** lists (already PR'd per memory) — drives
   "MCP server code review" SERP authority.
3. **HN front page** organic post — dwell-time + secondary citations.
4. **Anthropic Claude Code official docs / examples** — being listed in
   their MCP showcase is worth 100+ generic backlinks.
5. **dev.to + Medium "CodeRabbit alternatives" articles** — high commercial
   intent traffic.

**Cadence**: outreach starts when the first pillar blog post (M17)
publishes. Cold email isn't worth it — earned mentions via good content
are the plan.

## 12. ICP refinement (M7 follow-on)

Tier-to-decision-maker map for sales-conversation orientation:

| Tier             | Champion              | Approver        | Budget unlock              |
|------------------|-----------------------|-----------------|----------------------------|
| Free CLI         | Senior IC / Staff Eng | Self            | n/a                        |
| Roam Review $25  | Tech Lead / EM        | EM              | <$5K, no procurement       |
| Roam Cloud $99+  | Platform / DevEx Lead | VP Eng          | Tooling, single-PO         |
| Self-Hosted $1.8K-$12K | Security / Compliance Lead | CISO + VP Eng | Compliance, procurement    |

**Implication**: the homepage hero should resonate with Tech Lead /
Eng Manager (current copy works). The Self-Hosted page (currently
folded into /pricing) needs a security-first persona-band — but only
when we get our first regulated-vertical customer (chicken-and-egg).
