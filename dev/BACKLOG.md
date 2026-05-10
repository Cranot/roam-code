# Backlog — current sprint queue

Forward-looking only. **What to build / research / test next.**
Read at session start to know what's on deck.

Full demand index (~155 items, all tiers, source citations):
[`dev/ROADMAP.md`](ROADMAP.md). Pull items from there as they get
queued. When you ship one, delete the line; don't archive.

**Status as of 2026-05-10**: Sprint 1 (10 items) + Sprint 2 (3 items) +
Sprint 3 (3 items) all shipped — see CHANGELOG-staged work in working
tree. No commits yet (user holding). 522 tests pass across touched
areas. Total tests added: ~37.

---

## Next pickup — pick from ROADMAP

When this queue clears (it has), pull from `ROADMAP.md` in this order:

1. **Tier ★★★★ — pick one focus area at a time**:
   - **A** = architecture substrate (Capability Registry adoption, migration
     sequence numbers, finding registry, split health(), version stamps,
     MCP versioning, step-completion manifest)
   - **B** = perf heavy hitters (fused AST walker, cache controller reads
     in `_find_eager_loads`, bulk-fetch n1 helpers, ProcessPoolExecutor,
     skip-git-when-HEAD-unchanged)
   - **C** = GTM (Marketplace listing, Starter caps, Founding Customer
     lock, /enterprise pull, annual toggle)
   - **D** = site/copy/CTA pass (hero CTAs on /pricing + /compare, kill
     default-AI-prose lede, fix docs subnav)
   - **E** = agent/MCP DX (`roam_ask` MCP tool, SKILL.md rewrite,
     compact contract block, soft-enforce destructive tools)
   - **F** = security tier-2 (predicate IRI, /security cleanup, vuln
     telemetry endpoint)
   - **G** = DX onboarding (R1 stop unsolicited CI write, R2 install
     ordering, R3 short --help, R7 OneDrive auto-protect, R10 compact
     welcome banner)
2. **Tier ★★★** strategic moves once ★★★★ winds down

`ROADMAP.md` has a "Sequencing recommendation" section near the bottom
that lays out 7 sprints in dependency order — use it for scope decisions.

---

## ANTI-PRIORITIES (do not revisit unless evidence flips)

- Auto-deploy from `git push` — user explicitly chose manual wrangler
- "AI PR review tool" framing — locked to "structural intelligence layer"
- Per-dev pricing as launch tier — flat $99/$299/$799/$1,499 per pricing v3
- README hero rewrite — already correct
- Mailto → Stripe migration today — gated to specific event per pricing v3
- Page restructure to 7 sections — homepage stays at 13, restraint via spacing
- Per-session version bumps — accumulate under `[Unreleased]`
- "agent senses" being killed from copy — locked positioning keeps it
- Auto-deletion of dead exports — manual triage required (public-API risk)
- Building Pro+ tier ($45/dev/mo) pre-emptively — wait for 5+ Business asks
- IDE plugin as standalone product — wait for first Pro+ Audit Trail customer

---

## User-action items (cannot be fixed from code)

- **CI test (3.13) bypass** — workflow is correctly configured at
  `.github/workflows/roam-ci.yml:37` (matrix includes 3.13). The
  "expected but not running" status is a stale required-check name in
  GitHub's branch-protection settings. Fix in repo Settings → Branches
  → Protection rules: re-pin the required check or remove the stale
  expectation. Only the repo owner can do this.
