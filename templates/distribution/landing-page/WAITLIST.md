# Waitlist / signup mechanics

The site has zero cookies, zero JS, no third-party form providers (Mailchimp,
ConvertKit, HubSpot are all explicitly out of scope per the brand promise).
Buyers signal interest by email.

## Current mechanism (in production)

Every CTA on the site is a `mailto:` link with a pre-filled subject line:
- `mailto:hello@roam-code.com?subject=Roam Review early access`
- `mailto:hello@roam-code.com?subject=Roam Cloud early access`
- `mailto:hello@roam-code.com?subject=Roam Self-Hosted enquiry`
- `mailto:hello@roam-code.com?subject=Audit enquiry`
- `mailto:hello@roam-code.com?subject=Pilot enquiry`
- `mailto:security@roam-code.com` (no subject — security policy lives at /security)

Mail arrives at the Proton Mail mailbox (DNS configured: MX, SPF, DKIM,
DMARC, TLS-RPT — see infra_state memory).

**Pros**: zero infrastructure, zero JS, brand-aligned, GDPR-clean by
construction.
**Cons**: no automation; no autoresponder; no record of "subscribed but
not yet bought." Manual triage in the inbox.

## Upgrade path A: stay with mailto, add Proton autoresponder

The Proton Plus tier supports auto-replies. Configure one for inbound mail
to hello@ that:
1. Confirms receipt within seconds
2. Sets a 24-48h response expectation
3. Includes a "if urgent, here's the security disclosure path" pointer

Effort: ~30 minutes inside Proton settings. No code change.

## Upgrade path B: Cloudflare Worker + form

If/when we want a structured waitlist (e.g., for a public Roam Review beta
launch), the design we'd ship:

1. Static form on `/waitlist` (or in-page on `/pricing`) — pure HTML, no JS
2. Form posts to a Cloudflare Worker at `/api/waitlist`
3. Worker validates input + rate-limits (Cloudflare Turnstile for bot
   defense — note: Turnstile is cookieless, OK per brand promise)
4. Worker writes a row to a Cloudflare D1 SQLite table OR forwards to a
   webhook to the Proton SMTP relay
5. Worker returns a 303 redirect to a static `/thanks` page

Costs: free tier of CF Workers covers ~100k req/day, well above any
realistic launch traffic. D1 is also free at this scale. No vendor lock-in.

**No external JS still required** — the form posts natively. The Worker
runs server-side at CF edge. Cookieless, trackerless, no third-party.

## Decision rule

- Pre-launch / single-vendor stage: keep `mailto:` (current).
- Public Roam Review beta opens: upgrade to path B with a structured form.
- Don't conflate "we want metrics" with "we need a fancy form" — see
  MEASUREMENT.md. The mailto signal is high-quality; the form is needed
  only when volume exceeds what manual triage can handle.

## Acceptance criteria for the upgrade

When path B ships:
- [ ] Form is HTML-only; no JavaScript required to submit
- [ ] CF Turnstile challenge added (cookieless variant)
- [ ] Confirmation page returns 200 with no JS
- [ ] No third-party origins in the form action URL
- [ ] CSP allows the same-origin form-action (already does — `form-action 'self'`)
- [ ] Submitter receives an email confirmation within 60 seconds
- [ ] /privacy is updated to disclose the new data flow
