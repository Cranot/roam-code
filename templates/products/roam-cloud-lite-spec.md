# Product spec — Roam Cloud Lite

**Status**: spec, not yet built (2026-05-05).
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
| **Growth** | **$299/month** | Unlimited repos. SSO. Audit-trail export (the EU AI Act hook). API access. |

## Functional spec (MVP)

The CLI gains a `roam metrics push` command. It computes the audit envelope locally and POSTs only the **summary** (no source code, no symbol bodies — just numbers, file paths, and identifier names). The web app stores the time series and renders trend charts.

### CLI extension (in roam-code repo)

```bash
roam metrics push --token $ROAM_CLOUD_TOKEN --repo myorg/myrepo
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
- **Audit trail export** (Growth tier only): every metric snapshot exportable as JSON / CSV for EU AI Act Article 12 compliance.

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

A "good month-6" outcome: ~$5K MRR from Cloud Lite alone.

## Build phases

| Phase | Scope | Effort |
|---|---|---|
| **Phase 1** — MVP | `roam metrics push` CLI command, single-user dashboard, public-repo-only free tier, basic line charts | 3-4 weeks |
| **Phase 2** — Billing + Pro | Stripe self-serve, private-repo paid tier, history retention rules | 1-2 weeks |
| **Phase 3** — Teams | Team accounts, invitations, RBAC, custom thresholds | 2-3 weeks |
| **Phase 4** — Growth | SSO, API access, audit-trail export, Linear/Slack/email integrations | 2-3 weeks |

**Total to revenue-ready Pro tier**: ~5-6 weeks of focused work.
**Total to feature-complete Growth tier**: ~12 weeks.

## Distribution

- **Primary**: self-serve at `roam.cloud` (or `cloud.roam-code.dev`). Free trial = public repo signup.
- **Funnel**: CLI users see a one-line "see your trend at roam.cloud" hint after `roam health`.
- **Awareness**: Show HN, Reddit, X. Same channels as Phase 3 launch in v1 plan.
- **Cross-sell**: existing audit clients are pushed to Cloud Lite at delivery (`templates/email/customer-journey.md` template 10 retainer pitch becomes "monthly Cloud Lite subscription tracks the wins" rather than retainer-only).

## Anti-patterns

1. **Don't accept source code uploads**, ever. The whole positioning is metrics-only.
   Compromising this collapses the trust differentiator vs. CodeScene / SonarCloud.
2. **Don't gate per-language metrics**. If the CLI computes a metric, the
   dashboard can show it. Tier on scale (repos, users, history depth, alerts),
   not on capability.
3. **Don't price below CodeRabbit's $24/user/mo at the equivalent tier** — race
   to the bottom is the loser's game per appendix Phase 4 §4.5. Even if Cloud
   Lite is cheaper by feature, frame it as different positioning ("the
   architecture layer", not "code review").
4. **Don't require a sales call to upgrade from Free to Pro.** Pro must be
   one-click Stripe Checkout. Sales call ONLY for Team/Growth/Enterprise.
5. **Don't build a chat / collaboration / commenting layer.** That's a
   different product (PR review, see Roam Agent Review). Keep this one focused
   on metrics + alerts.
6. **Don't tightly couple to the OSS CLI's release cadence.** The Cloud Lite
   service should accept payloads from any compatible CLI version (use schema
   versioning that already exists in the audit envelope).

## Open questions

- Should public-repo Free tier be unlimited repos with limited history, OR limited repos with unlimited history? Lean: unlimited repos, 30-day history.
- Is the audit-trail export (EU AI Act) compelling enough as a Growth-tier wedge to be the hero feature for that tier? Lean: yes — pair with explicit Article 12 marketing copy.
- Do we host on Vercel + Neon (cheaper) or AWS / Cloudflare (more enterprise-friendly)? Lean: Vercel + Neon for MVP; revisit at $10K MRR.

## Cross-references

- v2 strategy: `monetization_v2_subscription_pivot.md`
- Sister product specs: `roam-agent-review-spec.md`, `roam-self-hosted-spec.md`
- CLI dependency: `src/roam/commands/cmd_audit.py` already produces the envelope structure this product consumes
