# Product spec — Roam Self-Hosted

**Status**: not yet built; depends on Review + Cloud buyer pull. Pricing patched 2026-05-07 after launch-pricing review.
**Source plan**: `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/monetization_v2_subscription_pivot.md`.

## One-line value prop

> Roam Review + Roam Cloud, **deployed in your VPC**. Air-gapped, license-keyed, ready for regulated industries and security-conscious enterprises.

## Buyer persona

- **Primary**: CTO / Head of Security / Head of Platform at 200+ engineer
  companies in regulated industries (finance, health, gov, defence) or with
  strict data exfiltration policies.
- **Trigger**: Procurement explicitly forbids cloud-hosted code analysis;
  SOC 2 / ISO 27001 / ISO 42001 governance demands on-prem; an internal
  security review of Roam Cloud kicks back the procurement track.
- **Budget bucket**: same as Sourcegraph Enterprise / SonarQube Server /
  CodeScene Self-Hosted. Annual contracts, sales-led, $15K-$100K range.

## Pricing

| Tier | Price | What |
|---|---|---|
| **Regulated pilot** | **$7,500 / 90 days** | Private, capped at 3 design partners. No SLA, no custom work, credited to annual. |
| **Business** | **$15,000/year floor** | Multiple teams. Core Review + Cloud features in customer network. Business-hours support. |
| **Enterprise** | **$50,000-$150,000/year** | SSO/SAML/SCIM, audit export, custom deployment support, SLA, named technical contact. |

Do not publish a `$5k` Startup tier. Below `$15k/year`, support and security
review overhead are likely to eat the deal. Do not enter RFP processes below
`$50k ARR`.

## Functional spec

This is **not a separate product** — it is the same code as Roam Cloud +
Roam Review, packaged for self-hosted deployment with a license-key gate.

Required functionality on top of the Cloud product:

- **License key validation**: Public/private RSA keypair. License key is a
  signed JWT containing tier, expiry, dev-count limit. Validation happens
  on service startup and once per day.
- **Helm chart** for Kubernetes deployments + **docker-compose** for simpler
  setups. Both ship a sane default config.
- **Air-gapped operation**: no outbound network calls beyond the licence
  validation server (which can be self-hosted on Provider's side at a
  static IP, allow-listed by enterprise firewall).
- **Backup / restore tooling**: Postgres dump + restore scripts for the
  metrics history.
- **Upgrade path**: documented blue/green or rolling-upgrade Helm flow with
  Postgres migrations gated by tier.
- **Audit log**: every dev-action that touches the system (PR analysis run,
  metric pushed, dashboard viewed) writes to an append-only audit log,
  exportable as JSON / CSV (mandatory for tier Business and above).

### License-key generation flow (Provider-side)

```
- Customer signs Order Form / SOW / DPA
- Provider runs internal CLI: `roam-license issue --customer "Acme" --tier business --expires 2027-05-05 --dev-count 200`
- Provider sends the signed JWT to customer
- Customer pastes into Helm values.yaml or .env
- Service validates on startup; refuses to serve if invalid/expired
```

## Architecture sketch

- Same as Roam Cloud + Roam Review, but distributed as Docker images +
  Helm chart.
- Single-node minimum: 4 vCPU, 16 GB RAM, 100 GB SSD.
- Optional Postgres replication for HA tier (Business+).
- All telemetry (if enabled by customer) is opt-in and goes to a Provider
  endpoint they can allow-list. Default: telemetry off.

## MRR path (cash, mostly annualized)

Each enterprise deal is a **chunky cash event**, not steady MRR:

- 3 regulated pilots × $7.5K = $22.5K cash, but only if they teach the product
- 3 Business × $15K/yr = $45K cash, ~$3,750 MRR equivalent
- 1 Enterprise × $75K/yr = $75K cash, ~$6,250 MRR equivalent

**Realistic month-6 outcome**: 1 regulated pilot or Business deal if a real
buyer cannot use cloud. Do not proactively build enterprise wrappers without
that pull.

## Build phases

| Phase | Scope | Effort | When |
|---|---|---|---|
| **Phase 0** | Pre-req — Roam Cloud + Roam Review must be built first (cannot self-host what doesn't exist) | depends | months 2-4 |
| **Phase 1** — Packaging | Helm chart, docker-compose, basic install docs | 2-3 weeks | month 4-5 |
| **Phase 2** — License system | Key issuance CLI, runtime validation, dev-count enforcement | 1 week | month 5 |
| **Phase 3** — First pilot | Onboard first paying Self-Hosted customer (ideally an existing audit client) | 2-3 weeks of CSE time | month 5-6 |
| **Phase 4** — Enterprise hardening | SSO (OIDC + SAML), HA Postgres, backup/restore, named CSE process | 4-6 weeks | month 6-9 |

**First Self-Hosted revenue**: realistic month 5-6, dependent on first audit
client wanting to continue.

## Distribution

- **Sales-led, PR-Replay-funneled.** Most realistic path: an existing PR Replay
  or Review customer says "we love this, can we run it in our VPC?" —
  Provider's response is "yes, here's the Self-Hosted option, here's the
  pricing." The audit IS the sales motion for Self-Hosted.
- **No Marketplace listing.** Self-hosted enterprise software is sales-led
  by nature. Marketplace listings here just generate noise.
- **Cold outreach via LinkedIn / industry events** for regulated-industry
  CTOs once 1-2 Self-Hosted customers exist as references.

## Why Self-Hosted (vs. only Cloud)

1. **Procurement reality.** Many enterprises will never sign a contract that
   sends source code or PR-content to a third-party cloud, no matter how
   reassuring the SOC2 / DPA. Self-Hosted is the only door open at that buyer.
2. **EU/regulatory positioning.** Self-Hosted in the customer's own
   EU-region cluster simplifies data-residency requirements without
   requiring Provider to operate EU infrastructure.
3. **Margin profile.** Self-Hosted deals are **higher margin per dev** than
   Cloud — no Provider hosting cost, customer absorbs infra.
4. **Defensibility.** Once a customer has Self-Hosted set up + their custom
   rules in `.roam/rules.yml` + 6 months of metrics history, switching cost
   is enormous (cf. v1 plan's Strategic Moat #5).

## Anti-patterns

1. **Don't ship Self-Hosted before Review + Cloud are real.** Self-hosted enterprise
   sales without a reference customer + product maturity = ugly support
   contracts you can't honour.
2. **Don't try to license-gate the OSS CLI.** Self-Hosted licenses ONLY the
   Roam Cloud + Roam Review stack. The OSS CLI stays free, Apache 2.0,
   forever.
3. **Don't price below $15K/year publicly.** Below that, the support burden + sales
   cycle eat the margin. Direct prospects below $15K-feeling-budget to Cloud
   Tier instead.
4. **Don't build custom features per enterprise.** Stay platform-shaped:
   build features that benefit all customers; offer professional services
   for true customizations.
5. **Don't use self-hosted to rescue small-team price objections.** It is for
   source-control, residency, and security blockers.
6. **Don't undercut hosted pricing.** Self-Hosted should be
   **more** expensive than hosted Review + Cloud once volume is factored in,
   reflecting the support + licensing overhead.

## Open questions

- Helm chart only, or Docker compose + Helm + manual? Lean: Docker compose for
  regulated pilots, Helm for $15K+, manual install scripts for esoteric environments
  on request.
- Telemetry **default-on with opt-out**, OR **default-off with opt-in**? Per
  the EU/GDPR-friendly positioning: **default-off**, opt-in clearly
  surfaced in install docs.
- Air-gapped license validation (no callback) vs. periodic license refresh?
  Lean: periodic refresh (24h) with 30-day grace for transient outages.
  Pure air-gapped only on Enterprise tier, custom contract.
- Greek IKE vs. Atlas Delaware C-corp as the contracting entity? Per v1
  Phase 0 prereq #1 (Greek accountant call) — confirm before issuing first
  Self-Hosted invoice.

## Cross-references

- v2 strategy: `monetization_v2_subscription_pivot.md`
- Pricing v4: `../strategy/pricing-v4-launch-2026-05-07.md`
- Sister product specs: `roam-cloud.md`, `roam-review.md`
- PR Replay / rollout legal templates feeding into Self-Hosted: `templates/legal/sow-master.md`
  ("let me run this in your VPC permanently" upsell path).
- v1 plan: `roam_code_plan_v1.md` "Tier 4 — Custom Extractors" + Tier 5
  Enterprise Support — those services-tier offerings overlap with what
  Self-Hosted Enterprise customers will pull on.
