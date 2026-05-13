# Monetization opportunities - external research pass, 2026-05-13

Status: internal planning note. This is not public pricing copy.

## Executive read

Roam already has the right first ladder: Free CLI -> PR Replay -> Roam
Review -> Roam Cloud -> Self-Hosted. The web research adds four
under-exploited wedges that fit what is already built:

1. AI-agent governance evidence, sold to CTO/CISO/procurement buyers.
2. Premium rule/policy packs, sold as domain-specific safety rails.
3. Team MCP gateway, sold as a remote authenticated MCP surface for agents.
4. Security reachability triage, sold as a focused SCA/SAST prioritization layer.

The strongest near-term service after PR Replay is the **Agent Governance
Evidence Pack**: it packages `runs`, `pr-bundle`, `audit-trail-*`, `cga`,
`article-12-check`, `agent-score`, `mode`, and `constitution` into a buyer
story compliance teams understand.

## External market signals

- GitHub Marketplace supports paid app plans, flat-rate and per-unit plans,
  14-day free trials, and billing through the customer's GitHub account:
  https://docs.github.com/en/apps/github-marketplace/selling-your-app-on-github-marketplace/pricing-plans-for-github-marketplace-apps
- GitHub Actions can be published to Marketplace for distribution when the
  repo has a single root `action.yml` and a release:
  https://docs.github.com/actions/creating-actions/publishing-actions-in-github-marketplace
- GitHub code scanning accepts third-party SARIF uploads, which validates
  Roam's SARIF/action path as a native buyer workflow:
  https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/uploading-a-sarif-file-to-github
- Cursor supports MCP servers over local stdio and remote SSE/Streamable HTTP
  with OAuth, so a team/remote MCP product is an actual distribution shape:
  https://docs.cursor.com/en/context/mcp
- The official MCP Registry is in preview and standardizes public MCP server
  discovery with `server.json`, DNS namespaces, REST discovery, and install
  metadata:
  https://modelcontextprotocol.io/registry/about
- Anthropic's Connectors Directory exists to showcase quality MCP servers
  across Claude products:
  https://support.anthropic.com/en/articles/11596036-anthropic-connectors-directory-faq
- AI PR-review buyers are used to paid plans: CodeRabbit publishes
  $24/user/mo and $48/user/mo tiers; Greptile publishes $30/seat/mo with
  included review limits plus usage overage; Qodo publishes a $30/user/mo
  team tier.
  Sources:
  https://www.coderabbit.ai/pricing
  https://www.greptile.com/pricing
  https://www.qodo.ai/pricing/
- Reachability is a validated security-buying category: Endor Labs describes
  function-level reachability for vulnerability prioritization, while Snyk
  prices developer-security plans per contributing developer.
  Sources:
  https://docs.endorlabs.com/scan/sca/reachability-analysis/
  https://snyk.io/plans/
- AI governance has explicit evidence demand: EU AI Act Article 12 centers
  automatic record-keeping for high-risk systems, ISO/IEC 42001 defines an AI
  management-system standard, and NIST AI RMF frames voluntary AI risk
  management practices.
  Sources:
  https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-12
  https://www.iso.org/standard/42001
  https://www.nist.gov/itl/ai-risk-management-framework

## Already covered well

| Surface | Status | Notes |
|---|---|---|
| PR Replay | Built enough to sell manually | `roam pr-replay` generates Markdown/PDF and logs paid engagements. Payment remains the blocker. |
| Roam Review | Engine built, hosted app missing | `pr-analyze`, `pr-comment-render`, audit trail, rules, SARIF, PR bundle. Needs GitHub App, billing, install flow. |
| Roam Cloud | Sender built, hosted product missing | `metrics-push` emits metrics-only payloads. Needs ingestion API, storage, dashboard. |
| Self-Hosted | Plausible but not packaged | Local engine exists. Needs deployable bundle, support model, procurement pack. |
| Governance setup service | Mentioned implicitly | Needs clearer product packaging and pricing. |

## Missed or under-packaged opportunities

### 1. Agent Governance Evidence Pack

**What it is:** A paid implementation and evidence package for teams adopting
AI coding agents. Roam sets up signed run ledgers, proof-carrying PR bundles,
audit trail verification/export, agent modes, and required gates. The
deliverable is a repeatable control set a CTO/CISO can show internally.

**Built primitives:** `runs`, `replay`, `agent-score`, `mode`, `permit`,
`pr-bundle`, `audit-trail-verify`, `audit-trail-export`,
`audit-trail-conformance-check`, `cga`, `article-12-check`, `constitution`.

**Buyer:** CTO, CISO, VP Eng, platform engineering leader at teams deploying
Cursor/Claude Code/Copilot/Codex.

**Packaging:** $5k-$15k setup, then $499-$2k/mo for quarterly evidence review,
custom rules, and audit-trail support. Bundle into Self-Hosted for regulated
buyers.

**Why now:** The market has governance language. Roam has the local evidence
substrate. This is more defensible than "another PR bot."

**Next docs/product move:** Add a `/governance` or `/trust` page that says:
"Prove which agent changed what, what it read first, what risks it accepted,
and which tests closed the loop."

### 2. Premium Rules and Policy Packs

**What it is:** Paid rule packs for domains and frameworks, installed locally
or bundled with Review/Self-Hosted. Examples: fintech/payments, healthcare,
OWASP/appsec, Django/Rails/Laravel/Next.js, AI-generated-code quality gates.

**Built primitives:** `rules`, `rules-validate`, `check-rules`, taint rules,
community rules, plugin substrate, `policy/graph_clauses.py`, SARIF export,
future graph-aware DSL.

**Buyer:** Teams that want repo-specific guardrails without writing policy
YAML themselves.

**Packaging:** Free community pack in OSS; paid packs at $99-$499/mo or
$999-$5k annual. Custom pack buildout at $2.5k-$25k depending on scope.

**Why now:** Competitors sell broad AI review. Roam can sell precise,
domain-specific graph rules that are hard to copy without the indexed graph.

**Next docs/product move:** Add a `templates/rules/premium/README.md` stub
or public "rules packs" page only after the first paid pack is defined.

### 3. Team MCP Gateway

**What it is:** A remote authenticated MCP endpoint for teams, wrapping Roam's
repo graph with audit, access control, and shared configuration. This is not
the full Cloud dashboard; it is the agent-facing control plane.

**Built primitives:** `roam mcp`, MCP presets, `mcp-status`, `surface`,
tool metadata, completions, watcher/session extras, `server.json`.

**Buyer:** Platform teams standardizing agent tooling across Cursor, Claude
Code, Codex, and internal agents.

**Packaging:** $99/team/mo plus $19/repo/mo, or bundle into Review Team+.
Self-hosted version for regulated buyers.

**Why now:** MCP is becoming an app-store-like channel. Cursor supports remote
MCP with OAuth; the official registry creates a discovery surface; Anthropic
has a connector directory.

**Next docs/product move:** Draft a "Roam Team MCP Gateway" one-pager before
building. Build only after one Review or Self-Hosted prospect asks for remote
MCP.

### 4. Security Reachability Triage

**What it is:** A paid report or CI add-on that tells a team which dependency,
secret, taint, and supply-chain findings are reachable or relevant in their
actual code graph.

**Built primitives:** `sbom`, `supply-chain`, `vulns`, `vuln-reach`,
`vuln-map`, `taint`, `taint-classify`, `secrets`, SARIF export, rules engine.

**Buyer:** AppSec teams buried in scanner noise, especially teams already
using Snyk/GitHub CodeQL/Dependabot but missing reachability context.

**Packaging:** $1.5k-$7.5k one-shot "Reachability Triage" report, or
$199-$799/repo/mo add-on to Review/Cloud.

**Why now:** Snyk validates developer-security budgets. Endor validates the
reachability story. Roam's differentiator is local, source-aware graph context
plus agent-facing outputs.

**Next docs/product move:** Add a `Security Reachability Triage` service line
to the audit page only after sample output is generated from a real repo.

### 5. Agent Vendor Benchmark Report

**What it is:** A service that runs the same repo/task set through multiple
agent workflows and reports which agent is safest on that customer's codebase.

**Built primitives:** `eval-retrieve`, `agent-score`, `ai-readiness`,
`ai-ratio`, `dogfood`, retrieve benchmark harnesses, `pr-replay`, run ledger.

**Buyer:** Engineering leaders choosing between Cursor, Claude Code, Copilot,
Codex, and internal agents.

**Packaging:** $3k-$15k report. Add optional quarterly rerun.

**Why now:** Agent choice is moving from preference to budget line. A
repo-specific benchmark is more credible than generic leaderboard claims.

**Next docs/product move:** Add a sample benchmark report template under
`templates/audit-report/` before public copy.

### 6. Framework Intelligence Packs

**What it is:** Paid plugin/extractor packs for frameworks and stacks where
generic static analysis underperforms: Laravel, Rails, Next.js, Prisma,
Django, Salesforce, monorepo API contracts.

**Built primitives:** plugin substrate, example plugin, bridges, extractors,
framework detection, workspace overlay.

**Buyer:** Teams whose stack-specific conventions create false positives or
missed graph edges in generic tools.

**Packaging:** $2.5k-$25k custom extractor/plugin build, then support and
maintenance retainer. Bundle generic versions into paid rules packs later.

**Why now:** This is high-margin services revenue that also improves core
coverage and future Review accuracy.

### 7. Team Index Cache / CI Acceleration

**What it is:** Shared encrypted index artifacts and CI cache guidance for
large repos, especially teams running Roam on every PR without hosted Review.

**Built primitives:** `index-export`, `index-import`, action cache path,
graph export, incremental indexer.

**Buyer:** Teams that want local/self-hosted execution but complain about CI
cost or slow PR gates.

**Packaging:** Lower priority. $99-$299/mo add-on only if CI runtime becomes
a repeated sales objection.

**Why now:** GitHub Actions minutes are billable for private repos, and AI
review/scanning workflows increasingly consume CI time. This is a cost-control
angle, not a primary wedge.

## Ranking by launch readiness

| Rank | Opportunity | Readiness | Revenue shape | Main missing piece |
|---|---:|---|---|---|
| 1 | PR Replay | High | $2.5k/$6k service | Payment link + lead flow |
| 2 | Agent Governance Evidence Pack | High | $5k-$15k setup + retainer | Page + control mapping + legal pass |
| 3 | Roam Review | Medium | $99-$1,499/mo | GitHub App + billing |
| 4 | Premium Rules/Policy Packs | Medium | Pack subscription + services | Pack taxonomy + signing/versioning |
| 5 | Security Reachability Triage | Medium | Report or add-on | Sample report + positioning |
| 6 | Agent Vendor Benchmark | Medium | $3k-$15k report | Repeatable benchmark template |
| 7 | Team MCP Gateway | Low/medium | Team SaaS / self-hosted | Auth + remote deployment |
| 8 | Roam Cloud | Low/medium | $19/repo/mo+ | Backend + dashboard |
| 9 | Framework Intelligence Packs | Medium | Services + retainers | Public offer definition |
| 10 | Team Index Cache | Low | Add-on | Customer demand validation |

## Recommended sequencing

1. Keep PR Replay as the first cash product, but remove mailto-as-checkout.
2. Add Agent Governance Evidence Pack as the second sellable service before
   building more hosted SaaS.
3. Publish the free GitHub Action/Marketplace path as Review lead-gen.
4. Draft rule-pack taxonomy and sell one custom pack manually.
5. Generate one Security Reachability Triage sample from a real repo.
6. Delay Team MCP Gateway until a Review/Self-Hosted prospect asks for
   multi-user remote MCP.

## Anti-opportunities

- Do not sell telemetry/data exhaust. It conflicts with the local/no-training
  trust promise.
- Do not build a standalone IDE plugin before a customer asks. The existing
  MCP/CLI channels are stronger.
- Do not reframe as generic SAST. Roam's wedge is graph-aware agent
  governance and structural review.
- Do not hide the free CLI. It is the proof engine and the trust anchor.
