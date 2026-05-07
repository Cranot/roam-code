# Product spec — Roam Cloud

**Status**: hosted dashboard not yet built; CLI sender exists. Pricing patched 2026-05-07 after launch-pricing review.
**Source plan**: `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/monetization_v2_subscription_pivot.md`.

## One-line value prop

> See your codebase's structural health trend over time. **Metrics only — no source code ever leaves your machine.**

## Buyer persona

- **Primary**: Engineering Manager / Staff Engineer / DevEx at 10-50-engineer teams already running roam-code locally.
- **Trigger**: Wants a longitudinal view of health-score deltas across PRs, weeks, months. Doesn't want to wire it to Grafana themselves.
- **Procurement objection neutralised**: source code never uploaded. The CLI computes everything locally and pushes a small JSON of metrics.

## Pricing

| Tier | Price | What |
|---|---|---|
| **Free** | $0 | Public repos only. 30-day rolling history. 1 user. |
| **Pro** | **$19/repo/month** | Private repos. Unlimited history. Slack/Linear/email notifications on threshold breach. 1 user per repo. |
| **Team** | **$99/month** | Up to 10 repos. Unlimited users. Per-team dashboard. Custom thresholds via `.roam-cloud.yml`. |
| **Growth** | **$299/month** | Up to 75 repos. SSO. AI-governance audit export. API access. |

No unlimited-repo tier at launch. Monorepos count as one git remote. If a
customer wants multiple logical projects inside one monorepo, treat that as a
sales conversation rather than inventing metering in the MVP.

Cloud is a supporting surface for Roam Review, not the primary revenue engine.
Build the dashboard layer Review customers land into first; standalone Cloud is
secondary until Review data proves demand.

## Functional spec (MVP)

The CLI uses `roam metrics-push`. It computes the audit envelope locally and POSTs only the **summary** (no source code, no symbol bodies — just numbers, file paths, and identifier names). The web app stores the time series and renders trend charts.

### CLI extension (in roam-code repo)

```bash
roam metrics-push --token $ROAM_CLOUD_TOKEN --repo myorg/myrepo
```

Sends a payload like:
```json
{
  "repo": "myorg/myrepo",
  "git_sha": "abc1234",
  "timestamp": "2026-05-05T18:00:00Z",
  "metrics": {
    "health_score": 88,
    "debt_total_minutes": 134843,
    "dead_safe": 78,
    "dead_review": 302,
    "danger_zone_count": 5,
    "bus_factor_high_risk": 53,
    "test_pyramid": {"total": 251, "unit": 0, "integration": 0, "e2e": 1},
    "hotspots": [
      {"path_hash": "...", "danger_score": 1.97},
      ...
    ]
  }
}
```

Note: `path_hash` rather than full path is sent for danger-zone files at the user's option (`--anonymize`); default is full path because most users want to navigate from the dashboard.

### Web dashboard (cloud)

- **Repos page**: list of connected repos with current health score + 90-day delta.
- **Repo detail**: time-series charts for each metric (health, debt, dead, danger zones, bus-factor concentrations).
- **Threshold alerts**: configure per-repo or per-team thresholds; alerts fire to Slack / email / Linear when crossed.
- **Audit export** (Growth tier only): every metric snapshot exportable as JSON / CSV for SOC 2 CC8.1, ISO 42001, and internal AI-governance evidence.

### Auth & billing

- **Auth**: Google / GitHub OAuth. No password.
- **Billing**: Stripe Checkout for self-serve. Stripe Invoicing for Team/Growth annual.

## Architecture sketch

- **Frontend**: Next.js 14 App Router, deployed on Vercel.
- **API**: Next.js API routes or a separate Hono/Fastify service. Postgres for metrics + Stripe webhooks.
- **DB**: Postgres (Neon or Supabase). One table per metric, indexed by `(repo_id, timestamp)`.
- **CLI integration**: new `commands/cmd_metrics_push.py` in the roam-code repo. Reuses `roam audit --json` output, strips source-code bodies, POSTs to API.
- **Free tier rate limits**: 1 push per hour per public repo to prevent abuse.

## MRR path (illustrative)

- 30 paying repos × $19 = $570 MRR (lower-bound)
- 100 paying repos × $19 = $1,900 MRR
- 25 small teams × $99 = $2,475 MRR
- 8 medium teams × $299 = $2,392 MRR
- 5 Growth × $299 = $1,495 MRR

A good month-6 outcome is Cloud helping Review retention and expansion. Treat
standalone Cloud revenue as upside until 100 paying Review accounts reveal
whether teams buy the dashboard independently.

## Build phases

| Phase | Scope | Effort |
|---|---|---|
| **Phase 1** — Review-attached MVP | `roam metrics-push` CLI command, current-run dashboard for Review installs, public-repo-only free tier, basic line charts | 3-4 weeks |
| **Phase 2** — Billing + Pro | Stripe self-serve, private-repo paid tier, history retention rules | 1-2 weeks |
| **Phase 3** — Teams | Team accounts, invitations, RBAC, custom thresholds | 2-3 weeks |
| **Phase 4** — Growth | SSO, API access, AI-governance audit export, Linear/Slack/email integrations | 2-3 weeks |

**Total to revenue-ready Pro tier**: ~5-6 weeks of focused work.
**Total to feature-complete Growth tier**: ~12 weeks.

## Distribution

- **Primary**: self-serve at `roam.cloud` (or `cloud.roam-code.dev`). Free trial = public repo signup.
- **Funnel**: CLI users see a one-line "see your trend at roam.cloud" hint after `roam health`.
- **Awareness**: Show HN, Reddit, X. Same channels as Phase 3 launch in v1 plan.
- **Cross-sell**: existing audit clients are pushed to Cloud at delivery (`templates/email/customer-journey.md` template 10 retainer pitch becomes "monthly Cloud subscription tracks the wins" rather than retainer-only).

## Anti-patterns

1. **Don't accept source code uploads**, ever. The whole positioning is metrics-only.
   Compromising this collapses the trust differentiator vs. CodeScene / SonarCloud.
2. **Don't gate per-language metrics**. If the CLI computes a metric, the
   dashboard can show it. Tier on scale (repos, users, history depth, alerts),
   not on capability.
3. **Don't lead with Article 12.** Use SOC 2 CC8.1, ISO 42001, and internal
   AI-governance evidence as the primary compliance frame. Article 12 only
   appears with explicit Annex III scope language.
4. **Don't require a sales call to upgrade from Free to Pro.** Pro must be
   one-click Stripe Checkout. Sales call ONLY for Team/Growth/Enterprise.
5. **Don't build a chat / collaboration / commenting layer.** That's a
   different product (PR review, see Roam Review). Keep this one focused
   on metrics + alerts.
6. **Don't tightly couple to the OSS CLI's release cadence.** The Cloud
   service should accept payloads from any compatible CLI version (use schema
   versioning that already exists in the audit envelope).

## Open questions

- Should public-repo Free tier be unlimited repos with limited history, OR limited repos with unlimited history? Lean: unlimited repos, 30-day history.
- Is the audit export compelling enough as a Growth-tier wedge? Unknown. Test
  with Review customers before making it the hero feature.
- Do we host on Vercel + Neon (cheaper) or AWS / Cloudflare (more enterprise-friendly)? Lean: Vercel + Neon for MVP; revisit at $10K MRR.

## Cross-references

- v2 strategy: `monetization_v2_subscription_pivot.md`
- Pricing v4: `../strategy/pricing-v4-launch-2026-05-07.md`
- Sister product specs: `roam-review.md`, `roam-self-hosted.md`
- CLI dependency: `src/roam/commands/cmd_audit.py` already produces the envelope structure this product consumes
