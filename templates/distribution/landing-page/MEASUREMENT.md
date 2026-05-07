# Analytics-free measurement playbook

The site has zero cookies, zero JS analytics, zero third-party trackers
(see `/no-cookies` for the user-facing receipt). This is a brand promise,
not an oversight. So how do we measure what's working?

## What we DO have access to

### 1. Cloudflare Web Analytics (cookieless, GDPR-clean)
Available in the Cloudflare dashboard for the `roam-code` Pages project.
Server-side aggregation of:
- Page views per URL
- Top referrers
- Country distribution (anonymized)
- Browser / device class
- Core Web Vitals (CrUX-style, anonymized)

Does NOT set cookies. Does NOT use a tracker script. Aggregates at the
edge from request logs CF already has. Acceptable per the
no-cookies promise (CF is the operator of the infrastructure, not a
third-party tracker added on top).

### 2. Cloudflare Pages deployment metrics
- Deploy count, last deploy
- Build time, build size
- Edge cache hit ratio (per asset)

### 3. Server access logs (CF Pages)
Request paths, status codes, byte counts, timing. Aggregable to identify
broken links, hot paths, abandoned funnels. No PII collected.

### 4. PyPI download stats (pypistats.org)
Per-day, per-version, per-OS download counts for the `roam-code` package.
Strong proxy for actual product adoption.

### 5. GitHub repo metrics
- Stars (vanity but useful for momentum)
- Forks (signal of intent to use)
- Issue + PR open/close rates
- Traffic insights (clones + visitors, 14-day window)

### 6. Email replies
The most honest signal. If hello@roam-code.com gets 5 emails in a week
from VP-Eng-titled senders, that's worth more than 5,000 page views.

### 7. Search Console (when we wire it)
Query-level impressions + clicks from Google. Tells us what we're
ranking for vs what we want to rank for.

## What we DON'T have access to

- Per-user funnels ("user X visited /pricing then /compare then bounced")
- Heatmaps / session replays
- Email-open tracking pixels
- Conversion attribution (UTM-based) — we don't append UTM params to outbound links

## Measurement cadence

| Cadence    | Source                | Why                          |
|------------|-----------------------|------------------------------|
| Daily      | hello@ inbox          | Lead quality + intent signal |
| Weekly     | CF Web Analytics      | Page traffic + referrers     |
| Weekly     | PyPI downloads        | Product-side adoption        |
| Monthly    | Search Console        | SEO performance              |
| Monthly    | GitHub repo traffic   | OSS-side adoption            |
| Per launch | Both above + replies  | Launch-day signal review     |

## Conversion signals (what we count)

In order of value:
1. Email reply with a paying-tier interest (highest)
2. Email reply mentioning "Roam Review" / "Roam Cloud" / "Self-Hosted"
3. Email reply with any product question
4. New PyPI install from a unique IP (proxy via downloads-per-day delta)
5. GitHub star
6. Page view of /pricing
7. Page view of homepage

## Anti-pattern alarms

If we ever consider:
- Adding Google Analytics — DON'T. The brand promise is binding.
- Adding Mixpanel / Amplitude / Segment — DON'T. Same reason.
- Adding LinkedIn Insights / Twitter Pixel — DON'T. Same reason.
- "Just for marketing attribution we'll add..." — DON'T. The promise is the moat.

If a measurement need arises that none of the above can satisfy, write
the question down here and discuss with the user before acting. The
no-tracking commitment is more valuable than any single metric.
