# Roam commercial landing page (starter)

This is the **starting point** for `roam-code.com` (claimed
2026-05-06). Drop into Cloudflare Pages, Vercel, or a static GitHub
Pages repo.

## What's here

* `index.html` — hero + 3 product cards + buyer-pain band + audit
  upsell + trust strip + FAQ + footer
* `landing.css` — single stylesheet, ~6KB, no JS, no fonts beyond
  Google's Plex Mono + Space Grotesk
* Total page weight: ~12KB HTML + 6KB CSS + Google Fonts CDN call.
  Aim is sub-200KB total transfer per the spec.

## Domain (claimed 2026-05-06)

**Primary:** `roam-code.com` — matches PyPI (`roam-code`) + GitHub
(`Cranot/roam-code`) exactly. $10.46/yr.

**Subdomains** (all CNAME → Pages project, free under same zone):

* `www.roam-code.com` — landing page (this site)
* `review.roam-code.com` — Roam Review GitHub App marketing + waitlist
* `cloud.roam-code.com` — Roam Cloud dashboard
* `audit.roam-code.com` — AI-governance audit evidence product
* `docs.roam-code.com` — alias of cranot.github.io/roam-code

**Defensive (recommended to register if cheap):**
`roam-code.dev` — already hardcoded as the in-toto attestation
predicate URL (`https://roam-code.dev/CodeGraph/v1`). If unavailable,
predicates can be repointed to `roam-code.com/CodeGraph/v1`.

## Deploy in 10 minutes

### Option A — Cloudflare Pages (recommended)

```bash
# 1. Push this directory to a new GitHub repo (e.g. roam-website)
cd templates/distribution/landing-page
git init && git add . && git commit -m "feat: initial roam-code.com landing page"
gh repo create roam-website --public --source=. --push

# 2. Connect to Cloudflare Pages
#    https://dash.cloudflare.com → Pages → Connect to Git → roam-website
#    Build command: (none — static)
#    Output directory: /

# 3. Wire roam-code.com DNS (after adding zone to Cloudflare)
#    Add CNAME: @    → <project>.pages.dev
#    Add CNAME: www  → <project>.pages.dev
#    Add CNAME: review → <project>.pages.dev  (or separate project)
#    Add CNAME: cloud  → <project>.pages.dev  (or separate project)
#    Add CNAME: audit  → <project>.pages.dev  (or separate project)
#    (Or use Cloudflare Registrar transfer — DNS auto-managed)
```

### Option B — Vercel

Same as above, but Vercel's "Other / static" framework preset.

### Option C — GitHub Pages (cheapest)

Just push to a `gh-pages` branch on a public repo. No build needed.
DNS: CNAME `roam-code.com` → `cranot.github.io`.

## Content TODOs before going live

* [ ] **Pick the naming.** v1 docs say "Roam Sentinel", v2 plan + this
  page say "Roam Review". Decide and find/replace.
* [ ] **Wire the waitlist forms** at `#waitlist-review` and
  `#waitlist-cloud` to a real provider (ConvertKit, Buttondown,
  Beehiiv, or just a Google Form for the MVP).
* [ ] **Substitute the OG image** at `og:image` — currently no
  image; add a 1200×630 PNG at `/og.png`.
* [ ] **Update the footer privacy/terms/refund links** to actual
  pages (or to GitHub markdown for v0).
* [ ] **Add Plausible/Fathom analytics snippet** before going public
  (avoid Google Analytics for the post-CodeRabbit-RCE trust angle).
* [ ] **Pick a real Calendly link** for the Self-Hosted "Talk to us"
  button.

## Content kept minimal on purpose

The page does NOT have:

* Customer logos (no real ones yet)
* Testimonials (no real ones yet)
* "Trusted by" strip (Tighten promised "won't sell social proof we
  don't have")
* Sample-report download (held until A.4 PDF moves to permanent home
  per landing-page-spec.md step 7)
* Case study tease (held per spec step 5)

These slots are reserved in the source HTML as comments — drop them
in as social proof becomes available.

## Why this layout (per the v2 plan)

* **Hero leads with the pain quote**, not features. Per
  `monetization_v2_subscription_pivot.md`: "the budget is **safe AI
  coding** — same line item that funds CodeRabbit / Greptile."
* **3 product cards in a row** with the middle card (Review) marked
  POPULAR. Same anchor-CTA pattern CodeRabbit / Greptile use.
* **Pain band cites real incidents by name** — PocketOS, Amazon
  Treadwell memo, DORA 2025. Per the v2 plan: production incidents
  are in CEO/SVP memos now; cite them.
* **Audit upsell repositioned** as "white-glove option" not the lead
  product. v1 led with audit; v2 demoted.
* **Trust strip emphasises 100% local + Apache 2.0** — load-bearing
  post-CodeRabbit-RCE.
* **FAQ leads with "how is this different from CodeRabbit"** because
  that's the buyer's first question.
