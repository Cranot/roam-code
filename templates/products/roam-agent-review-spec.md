# Product spec — Roam Agent Review

**Status**: spec, not yet built (2026-05-05).
**Source plan**: `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/monetization_v2_subscription_pivot.md`.
**This is the v2 wedge product** — the one that captures the "safe AI coding" budget where CodeRabbit/Greptile/Qodo already operate.

## One-line value prop

> A GitHub / GitLab PR bot that **scores AI-generated changes for structural risk**. Catches the "Cursor changed this confidently but broke 47 callers" pattern before merge.

## Buyer persona

- **Primary**: VP Engineering / Head of Platform / Head of DevEx at 30-300-engineer SaaS, 6+ months into Cursor / Cline / Claude Code rollout.
- **Trigger**: First Sev-2 traced to AI-generated code; quarterly OKR demanding "we need to justify the AI tooling spend"; senior engineer departure exposes the bus-factor risk.
- **Budget bucket**: same as CodeRabbit Pro ($24/user/mo) and Greptile ($30/seat/mo). The buyer is looking for "AI safety net" tools, not codebase auditors.

## Pricing

| Tier | Price | What |
|---|---|---|
| **Free** | $0 | Public repos only. 1 PR comment per push. Blast-radius score + top 3 affected files + verdict. |
| **Team** | **$20/dev/month** | Private repos. Custom architecture rules (`.roam/rules.yml`). Slack / Linear webhooks. Trend graphs. |
| **Business** | **$499/month** flat | Up to 50 devs. Audit-log export (the EU AI Act hook). Multi-org rollups. SSO. |
| **Enterprise / Self-Hosted** | custom ($15K+/yr) | Air-gapped install. Dedicated support. Mirrors CodeRabbit Enterprise floor. |

Floor of $20/dev/mo intentionally **below** CodeRabbit Pro's $24/user/mo to read as "structural complement, not replacement". Lift to $24/dev/mo only after 100+ Team customers prove price elasticity is fine.

## Functional spec (MVP)

On every PR opened or pushed:

1. Webhook fires from GitHub / GitLab.
2. Service clones the PR's HEAD into a sandboxed worker.
3. Runs `roam init` + a focused `roam pr-risk` analysis on the diff.
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
🛡️ Roam Agent Review

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

- 100 Team devs × $20 = $2,000 MRR
- 500 Team devs × $20 = $10,000 MRR (the headline target)
- 5 Business × $499 = $2,495 MRR
- 2 Self-Hosted × $15K/yr = $2,500 MRR equivalent

**Realistic month-6 outcome**: $5K-$15K MRR from Agent Review alone if it lands.

## Build phases

| Phase | Scope | Effort |
|---|---|---|
| **Phase 1** — MVP | GitHub App, Webhook handler, PR comment with blast-radius + verdict, public-repo free tier | 3-4 weeks |
| **Phase 2** — AI scoring | AI-likelihood heuristics + signal extraction; threshold-configurable BLOCK gate | 2-3 weeks |
| **Phase 3** — Team tier | `.roam/rules.yml` enforcement, Slack/Linear webhooks, custom thresholds, billing | 2-3 weeks |
| **Phase 4** — Business tier | Multi-org rollups, audit-log export, SSO, dashboard | 3-4 weeks |
| **Phase 5** — Self-hosted | Docker/Helm packaging + license key | 2-3 weeks |

**Total to first revenue (Team tier)**: ~8-10 weeks.
**Total to enterprise-ready**: ~16-20 weeks.

## Distribution

- **Primary**: GitHub Marketplace listing (`roam-review`). Free-tier-on-public-repos virality is mandatory (CodeRabbit's 2M-repo virality came from this).
- **Cursor / Cline marketplaces**: list as "code intelligence MCP" or similar. Cross-promote with the CLI.
- **Anthropic Skills**: the v1 plan's `@roam analyze this PR` skill IS this product. Ship the Skill as a thin wrapper that calls the GitHub App.
- **Awareness**: same Phase 3 channels (Show HN, X, Reddit).

## Why this is the v2 wedge (vs. just a feature of Cloud Lite)

1. **Different buyer journey.** Cloud Lite buyers want trends + dashboards;
   Agent Review buyers want catch-it-before-merge.
2. **Different unit economics.** Cloud Lite is per-repo billing; Agent Review
   is per-dev billing — much higher ARPU at scale.
3. **Different competitive frame.** Agent Review goes head-to-head with
   CodeRabbit / Greptile (a known $40M-ARR market). Cloud Lite competes
   indirectly with CodeScene + SonarCloud (a slower-growing market).
4. **Different distribution.** GitHub Marketplace gives Agent Review a
   built-in install funnel. Cloud Lite needs more direct outbound or
   product-led growth.

## Anti-patterns

1. **Don't position as a CodeRabbit replacement.** Position as the
   architecture layer beneath review. CodeRabbit reviews semantics; Roam
   reviews structure. Coexistence narrative wins.
2. **Don't gate the PR comment itself.** Free tier MUST get a meaningful
   comment on public repos — that's the marketing surface. Gate the
   custom-rules + integrations + history.
3. **Don't run roam-code in the user's CI.** Run it in our cloud, with
   our infrastructure. Customers don't pay $20/dev to provision their own
   runners. (Self-hosted tier is the exception, intentionally.)
4. **Don't cross-talk the bot's verdict on every PR.** PRs that pass
   thresholds cleanly get a single 🟢 line; only REVIEW/BLOCK PRs get the
   full breakdown. Reviewer fatigue tanks the product faster than slow
   delivery.
5. **Don't compete with Cursor / Cline / Claude Code on the IDE.** They
   own that surface. Roam lives on the PR surface — different layer, not
   substitutable.

## Open questions

- Self-host MVP shipping with the Cloud product, or 6 months later? Lean: ship cloud first, self-hosted at month 6 once enterprise pull is real.
- Build native GitLab/Bitbucket/Azure-DevOps support in MVP, or GitHub-only? Lean: GitHub-only at MVP. GitLab in Phase 4.
- Charge based on **GitHub seats** (= per dev) or **active PR authors** (= more aligned with value)? Lean: GitHub seats, mirrors CodeRabbit.

## Cross-references

- v2 strategy: `monetization_v2_subscription_pivot.md`
- Sister product specs: `roam-cloud-lite-spec.md`, `roam-self-hosted-spec.md`
- CLI dependency: `roam pr-risk`, `roam impact`, `roam preflight`, `roam owner` already exist; need a thin `roam pr-analyze --diff` aggregator command (or an internal API in the worker)
- Related v1 plan section: `roam_code_plan_v1.md` "The eventual SaaS — recommended v1 (build month 4-6 if services validate)" — that's THIS product, but built earlier in the v2 timeline.
