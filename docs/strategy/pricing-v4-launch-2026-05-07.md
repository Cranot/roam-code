# Roam launch pricing strategy - saved state

**Date saved:** 2026-05-07
**Verdict:** not 100% confident. Ship a patched launch plan, but treat four inputs as empirical: procurement path, COGS/review, CLI cannibalization, and competitor response.

## Verified facts

Fetched on 2026-05-07:

- Roam live `/pricing` still showed `$25/dev/mo` for Review, not the flat launch plan.
- CodeRabbit pricing showed Pro at `$24/user/mo` annual and Pro Plus at `$48/user/mo` annual.
- Greptile pricing showed `$30/seat/mo`, 50 reviews included per seat, then `$1` per additional review.
- Qodo pricing showed Teams at `$38/user/mo` monthly or `$30/user/mo` annual, with 20 PRs/user/month.
- SonarQube Cloud pricing started at `$32/mo` for up to 100k private LOC.

Canonical source URLs:

- https://roam-code.com/pricing
- https://roam-code.com/compare
- https://www.coderabbit.ai/pricing
- https://www.greptile.com/pricing
- https://www.qodo.ai/pricing/
- https://www.sonarsource.com/plans-and-pricing/

## Main conclusion

The original flat-rate plan is directionally right for launch, but unsafe as specified.

Flat headline pricing reduces sticker shock, but it does **not** reliably avoid procurement because Roam Review processes PR diffs in cloud. The pricing also needs usage caps because review volume, not author count alone, drives COGS.

The public `$25/dev/mo` anchor should be removed for launch. It invites direct comparison with CodeRabbit at `$24/user/mo`, makes Roam look more expensive as a complement, and creates a future migration fight.

## Launch pricing to ship

### Roam Review

| Tier | Price | Included | Overage / ceiling |
|---|---:|---|---|
| Community | Free | Public repos, free CLI, OSS use | hard stop for private hosted Review |
| Starter | `$99/mo` | 5 repos, 10 active PR authors, 200 reviews/mo | `$12/extra author/mo` to 20; `$0.50/extra review` |
| Team | `$299/mo` | 20 repos, 30 active PR authors, 900 reviews/mo, current-run Cloud dashboard | `$10/extra author/mo` to 75; `$0.40/extra review` |
| Business | `$799/mo` | 100 repos, 100 active PR authors, 3,000 reviews/mo, SSO, audit export | `$8/extra author/mo` to 150; `$0.30/extra review` |
| Scale | `$1,499/mo` annual only | 250 active PR authors, 8,000 reviews/mo, SAML/SCIM, priority support | `$6/extra author/mo`; custom quote above 300 authors or repeated cap pressure |

Definitions:

- Active PR author = unique human author of a reviewed PR in the trailing 30 days.
- Bots, Dependabot, Renovate, and generated release PR authors do not count.
- Usage warnings at 80% and 100%. No surprise bills by default; customer must opt into paid overage or upgrade.

### Migration mechanics

- Do not publish a future `$25/dev/mo` list price.
- Reprice new customers only after at least 50 paying Review customers and at least 3 months of COGS data.
- Existing launch customers keep their package through the 2027 renewal.
- Renewal increase capped at 15% unless they exceed included limits for 2 consecutive months.
- Renewal copy must say: "Your launch package stays. New customers are moving to usage-based pricing; your renewal changes only if your usage has outgrown the package."

### Roam Cloud

Cloud is supporting infrastructure, not the revenue engine.

| Tier | Price | Included |
|---|---:|---|
| Free | `$0` | public repos, 30-day history |
| Pro | `$19/repo/mo` | private repo, unlimited history, alerts |
| Team | `$99/mo` | 10 repos, team dashboard |
| Growth | `$299/mo` | 75 repos, API, SSO |

No unlimited repos at launch. Monorepos count as one git remote; split billing only when a customer asks for multiple logical projects inside a monorepo.

### PR Replay

PR Replay should qualify Review buyers, not become low-margin consulting.

- DIY 5-PR sample: automated only. No manual free consulting.
- Team: `$2,500`, 30 PRs, capped at 3 founder hours, 30-minute call.
- Deep: `$6,000`, 90 PRs, capped at 8 founder hours, 90-minute call.
- Credit 50% toward annual Review if bought within 60 days.
- Kill or raise price if fewer than 25% convert to Review within 60 days or gross margin drops below 70%.

### Self-hosted / regulated buyers

- Public price: inquiry only, `$15k/year` floor.
- Private regulated pilot: `$7.5k/90 days`, no SLA, no custom work, capped at 3 design partners, credited to annual.
- No RFPs below `$50k ARR`.
- Self-hosted is for source-control/security blockers, not for small teams avoiding cloud subscription fees.

## Residual uncertainty

1. Procurement may happen even at `$99/mo` because source-access vendors trigger security review.
2. COGS per review is not measured yet; caps are conservative guesses.
3. Free CLI may cannibalize Review for CI-heavy teams.
4. CodeRabbit, Greptile, or Qodo can cut price or bundle structural review.
5. Live site copy still needs a sweep to remove broad Article 12 claims and replace `$25/dev` with flat launch tiers.

## Build priorities

### P0 - before pricing launch

1. Update `/pricing`, `/compare`, homepage pricing mentions, and JSON-LD to show the flat launch plan.
2. Remove broad "EU AI Act Article 12 evidence pack" claims from pricing/compare/homepage. Use SOC 2 CC8.1, ISO 42001, and internal AI-governance evidence as the primary frame.
3. Publish a security/procurement packet: DPA, no-training/no-retention statement, data-flow diagram, subprocessors, GitHub App permissions, incident contact.
4. Add usage-limit language: active PR authors, review caps, warning thresholds, no surprise overage.

### P1 - revenue engine

1. Build Roam Review GitHub App MVP: install flow, webhook signature verification, sticky PR comment, branch-protection check, idempotency, and per-org config.
2. Add COGS telemetry: review runtime, LLM cost, clone/index time, retries, support touch count, reviews/author/month.
3. Ship paid beta to 5-10 design partners on Starter/Team only. Do not sell Scale until COGS is measured.

### P2 - conversion and proof

1. Build PR Replay automation from CLI output to report draft so founder time stays capped.
2. Publish a head-to-head benchmark on Roam-only structural findings: clones-not-edited, blast radius, layer/cycle violations, and missed sibling edits.
3. Rewrite CLI-to-Review messaging: "CLI catches one diff when someone remembers; Review catches every PR and records the decision."

### P3 - supporting surfaces

1. Build Cloud only as the dashboard layer Review customers land into; standalone Cloud is secondary.
2. Add Cloud ingestion/dashboard after Review MVP is live enough to generate real data.
3. Start the private self-hosted pilot only after one real regulated buyer explicitly cannot use cloud.

## CFO objection answer

"Why would I pay Roam on top of CodeRabbit?"

Because Roam is not another semantic reviewer. CodeRabbit/Greptile/Qodo review what the code appears to do; Roam gates what the change touches structurally: callers, clones, layers, cycles, and untested blast radius. Launch pricing is flat and capped, so the buyer can pilot without a per-seat expansion trap. If Roam cannot show at least one material class of blocked issue that the existing reviewer missed during the first 30 days, the account should not convert.

## Related docs

- Product index: [`../products/README.md`](../products/README.md)
- Roam Review: [`../products/roam-review.md`](../products/roam-review.md)
- Roam Cloud: [`../products/roam-cloud.md`](../products/roam-cloud.md)
- Roam Self-Hosted: [`../products/roam-self-hosted.md`](../products/roam-self-hosted.md)
