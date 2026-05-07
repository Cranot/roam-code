# Product spec — Roam Review

**Status**: hosted product not yet built; CLI engine exists. Pricing patched 2026-05-07 after adversarial launch-pricing review.
**Source plan**: `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/monetization_v2_subscription_pivot.md`.
**This is the v2 wedge product** — the one that captures the "safe AI coding" budget where CodeRabbit/Greptile/Qodo already operate.

## One-line value prop

> A GitHub / GitLab PR bot that **scores AI-generated changes for structural risk**. Catches the "Cursor changed this confidently but broke 47 callers" pattern before merge.

## Buyer persona

- **Primary**: VP Engineering / Head of Platform / Head of DevEx at 30-300-engineer SaaS, 6+ months into Cursor / Cline / Claude Code rollout.
- **Trigger**: First Sev-2 traced to AI-generated code; quarterly OKR demanding "we need to justify the AI tooling spend"; senior engineer departure exposes the bus-factor risk.
- **Budget bucket**: same as CodeRabbit Pro ($24/user/mo) and Greptile ($30/seat/mo). The buyer is looking for "AI safety net" tools, not codebase auditors.

## Pricing

Launch pricing is flat-rate with explicit usage caps. Do **not** publish a
`$25/dev/mo` anchor at launch; it invites a direct comparison with CodeRabbit
while Roam is still being sold as a complement.

| Tier | Price | Included | Overage / ceiling |
|---|---:|---|---|
| **Community** | $0 | Public repos, free CLI, OSS use | hard stop for private hosted Review |
| **Starter** | **$99/mo** | 5 repos, 10 active PR authors, 200 reviews/mo | `$12/extra author/mo` to 20; `$0.50/extra review` |
| **Team** | **$299/mo** | 20 repos, 30 active PR authors, 900 reviews/mo, current-run Cloud dashboard | `$10/extra author/mo` to 75; `$0.40/extra review` |
| **Business** | **$799/mo** | 100 repos, 100 active PR authors, 3,000 reviews/mo, SSO, audit export | `$8/extra author/mo` to 150; `$0.30/extra review` |
| **Scale** | **$1,499/mo annual only** | 250 active PR authors, 8,000 reviews/mo, SAML/SCIM, priority support | `$6/extra author/mo`; custom quote above 300 authors |

**Active PR author** means a unique human author of a reviewed PR in the
trailing 30 days. Bots, Dependabot, Renovate, and generated release PR authors
do not count. Overage is opt-in; by default the product warns at 80% and 100%
instead of surprising the customer with a bill.

Existing launch customers are grandfathered through the 2027 renewal. Renewal
increases are capped at 15% unless the account exceeds included limits for 2
consecutive months.

## Functional spec (MVP)

On every PR opened or pushed:

1. Webhook fires from GitHub / GitLab.
2. Service clones the PR's HEAD into a sandboxed worker.
3. Runs `roam init` + a focused `roam pr-analyze` analysis on the diff.
4. Computes:
   - **Blast-radius score (0-100)**: composite of files affected, layers crossed, fan-in of touched symbols.
   - **AI-likelihood score (0-100)**: heuristics for AI-generated diffs (rapid feature-add velocity, characteristic comment density, structural anti-patterns common in agent output).
   - **Verdict**: INTENTIONAL / SAFE / REVIEW / BLOCK based on configurable thresholds.
5. Posts a single PR comment with:
   - One-line summary verdict.
   - Top 3 affected files with `roam impact` / `roam preflight` highlights.
   - Owner suggestion (from `roam owner`).
   - Architecture-rule violations (if `.roam/rules.yml` is configured).
6. Optional: blocks merge via GitHub branch-protection check if verdict = BLOCK.

### Sample PR comment

```
Roam Review

**Verdict**: REVIEW · blast-radius 67/100 · ai-likelihood 84%

This PR touches 12 files across 3 layers (high fan-in: `lib/auth/session.py`).
Recommended reviewers based on git history: @alice (97% of churn in this dir).

Top affected:
- `lib/auth/session.py` — used by 47 callers; consider invariant tests
- `services/orders/checkout.py` — high churn × complexity hotspot
- `tests/test_auth.py` — would benefit from new cases for the changed flow

Architecture rule violations: 1 (frontend importing database layer; see
`.roam/rules.yml#no-frontend-db-import`).

[View full Roam report](https://roam-review.app/...) · Configure thresholds
```

### Configurable architecture rules (`.roam/rules.yml`)

Per Tier-3 (custom rules) of v1 plan, customers can encode their own rules:

```yaml
# .roam/rules.yml
rules:
  - id: no-frontend-db-import
    description: Frontend modules must not import from db/ directly
    pattern: import_from
    source_glob: "frontend/**/*.{ts,tsx,js,jsx}"
    forbidden_target_glob: "lib/db/**"
    severity: BLOCK
```

## Architecture sketch

- **GitHub App** (Probot or Octokit-based) running on Vercel / Cloudflare Workers / Fly.io.
- **Worker queue**: BullMQ + Redis or Cloudflare Queues. Each PR analysis is a job.
- **Sandboxed analysis worker**: ephemeral Docker container with `roam-code` installed, bounded by CPU/memory/wallclock.
- **Storage**: Postgres for analysis history, S3 for full report artifacts.
- **Auth**: GitHub App installation tokens; no OAuth user flow needed for the bot itself.
- **Self-hosted distribution**: Docker compose / Helm chart.

## MRR path

- 20 Starter accounts × $99 = $1,980 MRR
- 20 Team accounts × $299 = $5,980 MRR
- 5 Business accounts × $799 = $3,995 MRR
- 2 Scale accounts × $1,499 = $2,998 MRR

**Realistic month-6 outcome**: $5K-$15K MRR from Review if the GitHub App
lands and COGS per review stays inside the caps above.

## Build phases

| Phase | Scope | Effort |
|---|---|---|
| **Phase 1** — MVP | GitHub App, Webhook handler, PR comment with blast-radius + verdict, public-repo free tier | 3-4 weeks |
| **Phase 2** — AI scoring | AI-likelihood heuristics + signal extraction; threshold-configurable BLOCK gate | 2-3 weeks |
| **Phase 3** — Launch billing | Starter/Team billing, active-author counting, review caps, usage warnings, COGS telemetry | 2-3 weeks |
| **Phase 4** — Business tier | Multi-org rollups, audit-log export, SSO, dashboard | 3-4 weeks |
| **Phase 5** — Scale / self-hosted | Scale tier, Docker/Helm packaging + license key only after measured demand | 2-3 weeks |

**Total to first revenue (Starter/Team tiers)**: ~8-10 weeks.
**Total to enterprise-ready**: ~16-20 weeks.

## Distribution

- **Primary**: GitHub Marketplace listing (`roam-review`). Free-tier-on-public-repos virality is mandatory (CodeRabbit's 2M-repo virality came from this).
- **Cursor / Cline marketplaces**: list as "code intelligence MCP" or similar. Cross-promote with the CLI.
- **Anthropic Skills**: the v1 plan's `@roam analyze this PR` skill IS this product. Ship the Skill as a thin wrapper that calls the GitHub App.
- **Awareness**: same Phase 3 channels (Show HN, X, Reddit).

## Why this is the v2 wedge (vs. just a feature of Cloud)

1. **Different buyer journey.** Cloud buyers want trends + dashboards;
   Review buyers want catch-it-before-merge.
2. **Different unit economics.** Cloud is per-repo billing; Review is
   author/review-usage-backed flat pricing — higher ARPU at scale without a
   launch-day per-seat procurement fight.
3. **Different competitive frame.** Review goes head-to-head with
   CodeRabbit / Greptile (a known $40M-ARR market). Cloud competes
   indirectly with CodeScene + SonarCloud (a slower-growing market).
4. **Different distribution.** GitHub Marketplace gives Review a
   built-in install funnel. Cloud needs more direct outbound or
   product-led growth.

## Anti-patterns

1. **Don't position as a CodeRabbit replacement.** Position as the
   architecture layer beneath review. CodeRabbit reviews semantics; Roam
   reviews structure. Coexistence narrative wins.
2. **Don't gate the PR comment itself.** Free tier MUST get a meaningful
   comment on public repos — that's the marketing surface. Gate the
   custom-rules + integrations + history.
3. **Don't sell the cloud bot before the security packet is ready.** Even
   `$99/mo` can hit procurement because PR diffs are source-adjacent.
4. **Don't run roam-code in the user's CI for the hosted SKU.** Run it in our
   cloud, with our infrastructure. Customers don't pay for Review to provision
   their own runners. Self-hosted is the exception, intentionally.
5. **Don't cross-talk the bot's verdict on every PR.** PRs that pass
   thresholds cleanly get a single 🟢 line; only REVIEW/BLOCK PRs get the
   full breakdown. Reviewer fatigue tanks the product faster than slow
   delivery.
6. **Don't compete with Cursor / Cline / Claude Code on the IDE.** They
   own that surface. Roam lives on the PR surface — different layer, not
   substitutable.

## Open questions

- Self-host MVP shipping with the Cloud product, or 6 months later? Lean: ship cloud first, self-hosted at month 6 once enterprise pull is real.
- Build native GitLab/Bitbucket/Azure-DevOps support in MVP, or GitHub-only? Lean: GitHub-only at MVP. GitLab in Phase 4.
- COGS per review is unknown. Instrument runtime, LLM cost, clone/index time,
  retries, and support touches from the first private beta.
- Does flat pricing actually avoid procurement? Treat as unproven; run 20 buyer
  calls and ask whether they can buy Review on a card this month.

## Cross-references

- v2 strategy: `monetization_v2_subscription_pivot.md`
- Pricing v4: `../strategy/pricing-v4-launch-2026-05-07.md`
- Sister product specs: `roam-cloud.md`, `roam-self-hosted.md`
- CLI dependency: `roam pr-analyze`, `roam impact`, `roam preflight`, `roam owner`
- Related v1 plan section: `roam_code_plan_v1.md` "The eventual SaaS — recommended v1 (build month 4-6 if services validate)" — that's THIS product, but built earlier in the v2 timeline.
