# Code-session backlog

**Last updated:** 2026-05-05 (after C.1.a `roam pr-analyze` shipped — Roam Agent Review CLI engine is now real).
**How to use:** every Claude Code session starts here. Pick a task by track + ID, mark it `in_progress` (TaskCreate / TaskUpdate or just by editing this file), work it, mark done, append discoveries.

This file holds **code-related work only** — file creation/edits, CLI features, MVPs, content drafts. Non-code work (phone calls, Stripe Atlas, Calendly setup, sending DMs) lives in the TaskCreate task system or in the user's external todo.

---

## Snapshot — where we are (2026-05-05)

### Codebase
- HEAD: `9ebb651` and downstream (12.25 shipped). Working tree has untracked artifacts in `templates/`, `dev/`, plus a fresh memory file.
- License: Apache 2.0 (landed in 12.23, by parallel agent merging my edits).
- 181 commands (after C.1.a `pr-analyze` + `pr-comment-render` + `metrics-push`), 131 MCP tools, 27 languages. PyPI: 12.25.
- `roam audit` command exists and produces a usable JSON envelope (CLI side complete).

### Strategic plan version
- **v2 active** as of 2026-05-05. See `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/monetization_v2_subscription_pivot.md`.
- **v1 source of truth** kept in `~/.claude/projects/D--OneDrive---CosmoHac-Desktop/memory/roam_code_plan_v1.md` for the audit-as-product framing — still valid for the audit ladder, superseded for the SaaS timeline.

### Phase 1 deliverables done (2026-05-05 sprint)
- Audit deliverable infra: `templates/audit-report/{README, audit-report.md.tmpl, render.py}`. Smoke-tested + sample PDF rendered.
- Legal templates: `templates/legal/{sow-master, nda-mutual, dpa, refund-guarantee, uspto-trademark-checklist, w8ben-e-checklist}.md`
- Distribution: `templates/distribution/{readme-cta-snippets, cold-outreach, landing-page-spec, awesome-list-prs}.md` (landing-page now v2-shaped)
- Email: `templates/email/customer-journey.md`
- v2 product specs: `templates/products/{roam-cloud-lite-spec, roam-agent-review-spec, roam-self-hosted-spec}.md`
- Sample PDF: `dev/sample-audit-report.{md,pdf}` (filled narrative, ~12 pages, professional Pandoc output)

### What's wired but uncommitted
Untracked at HEAD: `templates/audit-report/`, `templates/legal/`, `templates/distribution/`, `templates/email/`, `templates/products/`, `dev/{CODE-BACKLOG.md, sample-audit-report.md, sample-audit-report.pdf, .audit-envelope.json, roam-self-audit-test.md}`. Nothing committed by the assistant in this run — waiting for explicit user OK on the commit boundary.

---

## v1 → v2 transition map

| Concept | v1 (services-first) | v2 (subscription-first) |
|---|---|---|
| Primary offer | Audit at $1.8K-$12K | Subscriptions: Cloud Lite $19-$299/mo + Agent Review $20/dev/mo + Self-Hosted $5K-$100K/yr |
| MRR target month 6 | €2K MRR-equivalent (audits annualized) | **$10K MRR** ($25K stretch) |
| Audit's role | Engine | Ladder / paid onboarding / Self-Hosted on-ramp |
| Time-to-build SaaS | Month 4-6 (validate first) | **Now** (parallel with audit pilot) |
| Outreach pitch | "AI Agent Readiness Audit — for teams 6 mo in Cursor" | "Roam tells you when AI-generated changes are structurally risky" |
| Distribution focus | Cold DMs to VPs + awesome-lists + Show HN | GitHub Marketplace (Agent Review) + product-led growth + audit upsell |
| Build priority | Audit pipeline + report template + case studies | Cloud Lite MVP + Agent Review MVP, in parallel |

---

## Track A — Polish & refinement (small, fast wins; 30 min – 2 h each)

These tighten what already exists. Pick one to warm up at the start of a session.

| ID | Item | Effort | Status | Notes |
|---|---|---|---|---|
| A.1 | Move `dev/sample-audit-report.{md,pdf}` → `templates/audit-report/sample-redacted.{md,pdf}` | 5 min | ☐ | Permanent home; the landing-page spec links to that path |
| A.2 | Reframe `templates/distribution/cold-outreach.md` for v2 — add product-pitch variants (Agent Review GitHub install, Cloud Lite signup) alongside audit-pitch variants | 1 h | ☐ | Keep v1 audit DMs; add v2 lane |
| A.3 | Reframe `templates/distribution/readme-cta-snippets.md` — point at products primary, audit secondary, per v2 landing page | 30 min | ☐ | |
| A.4 | Reframe `templates/email/customer-journey.md` — add product-onboarding variants (Cloud Lite signup nurture, Agent Review trial conversion) | 1 h | ☐ | Keep audit variants; audit ↔ product paths coexist |
| A.5 | Update `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/build_priorities.md` for v2 (currently v1-shaped) | 30 min | ☐ | Demote audit-pipeline tasks; promote v2 product MVPs |
| A.6 | Delete + recreate task #31 (Cloud Lite MVP) and task #32 (Self-Hosted) to clear circular dep — TaskUpdate API has no remove-blocker verb | 5 min | ☐ | After delete, recreate with proper deps: 32 blocked by 30 + 31 only |
| A.7 | Add `templates/README.md` — index file explaining the templates/ directory structure for users discovering the tree | 30 min | ☐ | |
| A.8 | Clean up `dev/.audit-envelope.json` and `dev/roam-self-audit-test.md` (debug artifacts) — decide keep/move/delete | 5 min | ☐ | Could keep envelope as a fixture for tests in E.1 |
| A.9 | Commit Phase 1 artifacts (with explicit user OK) — single commit per logical group: templates, sample, memory | 15 min | ☐ | Do NOT commit without user instruction. Commit message style per `feedback_commit_style.md` |

---

## Track B — Content drafts (Claude can draft; user voices the final)

The case studies and Show HN need the user's voice for final versions, but Claude Code can produce strong first drafts that the user iterates. Most blocked-on-A-track-being-done are NOT blocking these — content can run in parallel.

| ID | Item | Effort | Depends on | Status | Notes |
|---|---|---|---|---|---|
| B.1 | Express.js case study (clone, index, render, write 800 words) | 1-2 h | repo cloneable | ☐ | Same data path as the sample PDF (A.4 reuse) |
| B.2 | Vue.js case study | 1-2 h | B.1 (template emerges) | ☐ | Sequential — let template settle from B.1 |
| B.3 | Laravel case study | 1-2 h | B.2 | ☐ | Optional — only if time permits + adds new finding angle |
| B.4 | Svelte case study | 1-2 h | B.3 | ☐ | Optional |
| B.5 | Axios case study | 1-2 h | B.4 | ☐ | Optional — small library, may not yield 5+ findings |
| B.6 | 5-repo benchmark blog post (long-form anchor for Show HN + X thread) | 1 d | B.1 + B.2 (mandatory) | ☐ | "What roam-code caught in Express/Vue/Laravel/Svelte/Axios that Cursor/Cline missed" |
| B.7 | Show HN title + first comment + X thread (8-12 tweets) | 4 h | B.6 | ☐ | DRAFT only; launch is Phase 3 |
| B.8 | LinkedIn EU AI Act post (Aug 2 2026 deadline framing) | 1 h | — | ☐ | Independent — can produce anytime |
| B.9 | X bio + pinned tweet copy (per v1 plan E.5) | 30 min | B.6 (pinned tweet links to it) | ☐ | DRAFT |
| B.10 | "Phase 2 readiness review" walkthrough — exercise the 13-touchpoint customer journey end-to-end with all artifacts in hand | 4 h | most A-track + B.1-B.7 | ☐ | Last gate before any Phase 3 launch |
| B.11 | Reframe v2 launch sequence — what does Phase 3 look like for a subscription-first launch? | 2 h | A.2 + A.3 + A.4 | ☐ | New artifact: `templates/distribution/v2-launch-sequence.md` |

---

## Track C — v2 product MVP engineering (weeks each)

This is where the real revenue work lives. Specs are in `templates/products/`. Build order: **Agent Review first** (highest leverage, GitHub Marketplace virality), Cloud Lite in parallel if capacity, Self-Hosted last (depends on the other two).

### C.1 Roam Agent Review MVP — total ~8-10 weeks

| ID | Sub-task | Effort | Depends | Status |
|---|---|---|---|---|
| C.1.a | Add `roam pr-analyze --diff` aggregator command — DONE 2026-05-05 (`src/roam/commands/cmd_pr_analyze.py` + `tests/test_pr_analyze.py`, 23/23 tests green, ruff clean). Aggregates `pr-prep` foundation + 6-signal AI-likelihood scorer + `.roam/rules.yml` import-pattern enforcement + INTENTIONAL/SAFE/REVIEW/BLOCK verdict mapping + `--gate` exit-5 CI behavior. Smoke-tested on real diff (SAFE) + synthetic AI-shaped diff (66/100 ai-likelihood) + BLOCK rule (exit 5) + intentional bypass (overrides BLOCK). | 3-5 d (actual: ~3 hr focused) | — | ✅ |
| C.1.b | GitHub App skeleton (Octokit / Probot, hosted on Vercel / Fly / Cloudflare Workers) — install flow, webhook handler for `pull_request` events that shells out to `roam pr-analyze --json` and posts a sticky PR comment | 1 wk | C.1.a ✅ | ☐ NEXT |
| C.1.c | Sandboxed analysis worker (ephemeral Docker container with `roam-code` installed, bounded by CPU/memory/wallclock) | 1 wk | C.1.b | ☐ |
| C.1.d | PR comment formatter + posting logic (single sticky comment, edit-on-update; 🟢 minimal for clean PRs, full breakdown for REVIEW/BLOCK) | 4-5 d | C.1.c | ☐ |
| C.1.e | AI-likelihood scoring heuristics (rapid feature-add velocity, comment density, structural anti-patterns common in agent output) | 4-5 d | C.1.c | ☐ |
| C.1.f | `.roam/rules.yml` enforcement at PR-time (custom architecture rules) | 1 wk | C.1.d | ☐ |
| C.1.g | Stripe billing integration (Team $20/dev/mo, Business $499/mo flat) | 4-5 d | C.1.f | ☐ |
| C.1.h | Slack / Linear / email webhook integrations | 4-5 d | C.1.g | ☐ |
| C.1.i | GitHub Marketplace listing (artwork, screenshots, listing copy) | 2 d | C.1.h | ☐ |

### C.2 Roam Cloud Lite MVP — total ~5-6 weeks

| ID | Sub-task | Effort | Depends | Status |
|---|---|---|---|---|
| C.2.a | Add `roam metrics push --token --repo` command (consumes audit envelope, strips source bodies, POSTs to Cloud Lite API) | 2-3 d | — | ☐ |
| C.2.b | Define metrics-push JSON schema with versioning | 1 d | C.2.a | ☐ |
| C.2.c | Backend API (Hono / Fastify / Next.js API routes; Postgres for metrics, Stripe for billing) | 1 wk | C.2.b | ☐ |
| C.2.d | Frontend dashboard (Next.js 14 App Router on Vercel) — repos list, repo detail with line charts | 1.5 wk | C.2.c | ☐ |
| C.2.e | Auth (GitHub OAuth + Google OAuth, no passwords) | 3 d | C.2.c | ☐ |
| C.2.f | Stripe self-serve billing (Pro $19/repo/mo, Team $99/mo, Growth $299/mo) | 4 d | C.2.e | ☐ |
| C.2.g | Threshold-alert notifications (Slack / Linear / email) | 3 d | C.2.f | ☐ |
| C.2.h | Free-tier rate limits + public-repo-history-cap | 2 d | C.2.f | ☐ |
| C.2.i | Audit-log export (Growth tier; the EU AI Act hook) | 4 d | C.2.f | ☐ |

### C.3 Roam Self-Hosted MVP — total ~3-5 weeks (DEPENDS on C.1 + C.2)

| ID | Sub-task | Effort | Depends | Status |
|---|---|---|---|---|
| C.3.a | Docker compose for Cloud Lite + Agent Review combined | 1 wk | C.1.* + C.2.* | ☐ |
| C.3.b | Helm chart for Kubernetes deployments | 1 wk | C.3.a | ☐ |
| C.3.c | License-key issuance CLI (`roam-license issue --customer --tier --expires --dev-count`) using RSA-signed JWT | 4 d | — (independent) | ☐ |
| C.3.d | Runtime license validation in the hosted services (startup check + daily refresh, 30-day grace) | 4 d | C.3.c | ☐ |
| C.3.e | Backup / restore tooling (Postgres dump scripts) | 2 d | C.3.b | ☐ |
| C.3.f | Audit-log feature (append-only, JSON / CSV export) | 4 d | C.2.i (reuse Cloud audit-log) | ☐ |
| C.3.g | Install docs + air-gapped operation runbook | 2 d | C.3.b + C.3.f | ☐ |

### C.4 Domain & infra (mostly user actions; surfaced here so they're visible)

| ID | Item | Owner | Status |
|---|---|---|---|
| C.4.a | Register `roam.consulting` (or equivalent) | user | ☐ |
| C.4.b | Vercel project setup for Cloud Lite + commercial page | user | ☐ |
| C.4.c | DNS (CNAME + verification) | user | ☐ |
| C.4.d | Stripe Atlas + product configuration (existing user task #8) | user | ☐ |
| C.4.e | GitHub App registration (Provider account) | user | ☐ |

---

## Track D — Existing roam-code OSS improvements

These tighten the OSS funnel itself. Independently scheduled; nothing here blocks v2 product work.

| ID | Item | Why | Effort | Status |
|---|---|---|---|---|
| D.1 | Multi-repo cross-graph | Closes 3-point gap in competitive scoring rubric | 2-3 wk | ☐ |
| D.2 | VS Code extension (auto-publishes to Cursor via OpenVSX) | 2-point gap; reaches 25M VS Code users | 2-3 wk | ☐ |
| D.3 | Anthropic Skill #1: `@roam analyze this PR` | Defensive priority per v1 Threat 4.1; thin wrapper around C.1 once it exists | 1 wk | ☐ |
| D.4 | Anthropic Skill #2: `@roam find blast radius` | Same channel; second skill compounds visibility | 1 wk | ☐ |
| D.5 | Anthropic Skill #3-5: `@roam who owns this` / `@roam architecture risk` / `@roam dead code` | Same channel; per v1 plan, 5 skills total | 3 wk | ☐ |
| D.6 | Cursor Marketplace MCP listing | Cursor's reportedly $500M+ ARR base; code-intelligence MCP slot empty | 1 wk | ☐ |
| D.7 | EU AI Act Article 12 audit-trail product (CLI side first; productize as part of Cloud Lite Growth + Self-Hosted Business) | The non-obvious enterprise wedge per v1 | 3-4 wk | ☐ |
| D.8 | SARIF / SCIP export expansion | Compliance-driven enterprises | 1 wk | ☐ |
| D.9 | Sourcebot-style code search competitor parity (defer per v1 anti-priority) | NOT planned | — | ✗ |
| D.10 | OSS bench expansion (existing follow-up #37 in MEMORY.md) | Trust signal for Phase 3 launch | 1 wk | ☐ |
| D.11 | MCP listings web-form submissions (PulseMCP, mcp.so, mcpservers.org, Smithery, Cline) — pending follow-up #31 | Distribution surface | 30 min each | ☐ |

---

## Track E — Hygiene / refactoring / docs

| ID | Item | Effort | Status |
|---|---|---|---|
| E.1 | Add tests for `templates/audit-report/render.py` (use a fixture audit envelope; verify section rendering + edge cases) | 4 h | ☐ |
| E.2 | Update `CLAUDE.md` to document `templates/` structure and intended use | 30 min | ☐ |
| E.3 | After any new CLI command (C.1.a, C.2.a, etc.): run `pip install -e .` and update command count in 12+ surface-count files (per workflow-rules.md) | 30 min per | ☐ |
| E.4 | Update `README.md` to mention `templates/` for users wanting deliverable infrastructure | 15 min | ☐ |
| E.5 | Run `ruff format` on any new Python files before committing (CI checks formatting) | included with each | ☐ |
| E.6 | Run `pytest tests/ -m "not slow"` before any release commit (per workflow-rules.md, discuss with user before full suite) | 5-10 min | ☐ |

---

## Anti-priorities (do not build / parked)

Per v1 + v2 strategy:

1. **Per-language extractor gating** — keep the OSS CLI fully capable in all 27 languages. Tier on scale, not capability.
2. **Restrictive license switch** (BUSL / FSL / SSPL / AGPL) on the CLI — blast radius is platform-vendor absorption, which licensing doesn't stop.
3. **Per-seat pricing on the CLI** — stays free, Apache 2.0, forever.
4. **Sourcebot-style code search competitor** — different layer; don't fight on search.
5. **A SaaS PR-review product competing semantically with CodeRabbit** — they own that surface ($40M ARR). Roam Agent Review competes on STRUCTURE, not semantics, deliberately.
6. **JetBrains plugin** — defer to Y2 unless an audit client requests + funds.
7. **Enterprise SSO / SOC2 in first 12 months** for Cloud Lite — wait until enterprise sale demands it. Self-Hosted gets SSO earlier (it's expected at the Self-Hosted price point).
8. **Newsletter as primary CTA** — funnel is product-signup or audit-call; newsletter dilutes it.
9. **Free Audit CTA** — attracts tire-kickers; the CLI is the genuine free product.
10. **Fake scarcity** — "only 3 slots left this month" reads desperate at the persona level.

---

## User-action backlog (NOT in scope for Claude Code sessions; surfaced for context)

These block downstream work but the user must do them. From TaskList #1, #2, #3, #8, #24, #26 + scattered:

- Phone call to Greek accountant (blocks Stripe Atlas)
- Coffee with Owner B (blocks Greek-prospect outreach)
- DM 3 backup-freelancer candidates (overflow capacity)
- File Stripe Atlas ($500, ~2-week wallclock)
- Calendly account setup with screening question
- LinkedIn Sales Navigator prospect list (10 decision-makers)
- USPTO TESS conflict search + filing ($350)
- Fill W-8BEN-E PDF (after accountant confirms entity)
- Register `roam.consulting` domain
- Send the held cold DMs at Phase 3 trigger
- Submit the held awesome-list PRs at Phase 3 trigger
- Send the held MCP-listing web-form submissions
- Sit at keyboard 4 hours when Show HN goes live

---

## Recommended next-session priorities (top 5)

In order of leverage. Each picks up cleanly from current state.

1. **A.6 fix the circular task dep** + **A.5 update build_priorities.md for v2** — 35 min total. Cleans up the meta-state so future sessions read it correctly.
2. **C.1.a — Add `roam pr-analyze --diff` aggregator command** to the CLI. ~3-5 days standalone. Single most leverage-per-day item: unblocks the entire Agent Review MVP (C.1.b–C.1.i). Pure CLI work, fits Claude Code session naturally. Lives in `src/roam/commands/cmd_pr_analyze.py`.
3. **C.2.a — Add `roam metrics push --token --repo` command** + C.2.b schema definition. ~3 days. Same pattern: unblocks Cloud Lite MVP. Pure CLI work.
4. **B.1 Express case study draft** — 1-2 hours. Reuses A.4 sample PDF infrastructure. Produces the first concrete trust signal for Phase 3.
5. **A.2 + A.3 + A.4 reframes** — 2.5 hours combined. Brings outreach + email + README CTAs in line with v2 positioning.

Items 2 and 3 are CLI features that can run in parallel-via-merge (different files, no shared changes). Items 1, 4, 5 are independent.

---

## Cross-references

- **v2 strategy memory**: `~/.claude/projects/D--OneDrive---CosmoHac-Project-roam-code/memory/monetization_v2_subscription_pivot.md`
- **v1 source-of-truth memory**: `~/.claude/projects/D--OneDrive---CosmoHac-Desktop/memory/{roam_code_plan_v1.md, roam_code_plan_appendix.md, roam_code_monetization_playbook.md}`
- **v1 working bridge memory** (this dir): `monetization_strategy.md`, `build_priorities.md` (latter needs v2 reframe per A.5)
- **Workflow rules memory**: `workflow-rules.md` — read at session start for max-3-agents rule, full-test-suite-once rule, post-agent checklist
- **Commit style memory**: `feedback_commit_style.md` — no internal tracker codes / nicknames / shorthand in commit messages
- **Product specs in repo**: `templates/products/{roam-cloud-lite,roam-agent-review,roam-self-hosted}-spec.md`
- **Landing page spec in repo**: `templates/distribution/landing-page-spec.md`
- **Audit deliverable infra in repo**: `templates/audit-report/`
- **Sample PDF**: `dev/sample-audit-report.pdf` (move to `templates/audit-report/sample-redacted.pdf` per A.1)
- **TaskList tool**: tasks #1-#32 in the Claude Code session (some pending, some completed; #31 ↔ #32 has a circular dep to fix per A.6)

---

## Update log

- **2026-05-06 (round 3, GM session — 12 phases polish + dogfood)** — P1: centralised `DEFAULT_AUDIT_TRAIL_PATH` (was 3-way duplicated) + `metrics-push --timeout`. P2: 5 small polish wins (--explain↔--json hint, --quiet+--json mutex warning, conformance disclaimer top-level, rules-validate gate-fail hint, --parallel oversubscription warning). P3: parametrised guardrail test for all 9 signals having explanations + a meta-test ensuring scorer signal set matches test parametrise. P4: `rules-validate --explain` mode with pattern reference + glob examples. P5: pr-comment-render previous-verdict link + baseline age line. P6: `audit-trail-export --aggregate` top-snapshot fields (top_actor / top_repo / top_month / top_verdict). P7: `sequence_number` in audit-trail records + `audit-trail-export --finalize` writes closing AuditIntegritySummary record (chain head + event count + algorithm). P8: starter rule packs at `templates/rules/{python,typescript}/.roam-rules.yml` (14 rules each, validated clean) + README. **P9: new command `roam dogfood`** (one-shot v2 stack runner: audit + pr-analyze + audit-trail + conformance). **P10: HEAVY DOGFOOD** — ran roam against itself, found 2 CRITICAL functions (`_compute_ai_likelihood` cc=110, `_render_github_markdown` cc=101), refactored both into helper-decomposed forms (now <28 cc each). **P11: cache speedup measurement** — built 5-real-commit batch, found and fixed real bug (--cache wasn't propagated through batch mode + cache_hit metadata was being stripped by json_envelope's _meta rebuild), measured **24.5× cold→warm speedup** (12.2s → 0.5s). P12: report at `dev/DOGFOOD-RESULTS-2026-05-06.md`. **383 tests pass** (+50 new). Surface: 185 → 186 CLI commands, 135 → 136 MCP tools, core preset 48 → 49. **No version bump, no commits, no push.** HEAD unchanged at f1101fb.
- **2026-05-06 (overnight 16-phase round 2)** — Built phases 1-10 (shared `git_helpers` extraction + version dedup, `roam rules-validate` new command with --strict / --against / --gate, rules engine production hardening with type coercion + structured warnings + rules-strict mode, audit-trail safety with auto-verify-before-emit + auto-escalate-to-BLOCK on chain break + UTC timestamp stability, `audit-trail-export --aggregate` procurement summary tables, `roam audit-trail-conformance-check` new command with 6-check Article 12 scorer, pr-analyze --quiet mode + drift before-after rendering + signal explanations in PR comment, +3 v2 AI-likelihood signals (placeholder_density / llm_phrase_density / suspicious_imports), pr-analyze --batch parallel via ProcessPoolExecutor + --progress stderr lines, pr-analyze --cache by sha256(diff+rules+threshold) + metrics-push --include-pr-analysis enrichment with stale-detection). Plus meta-phases 11-16 (synergy review with 3 inline implementations + 13 logged TODOs, edge-case sweep with 19 boundary tests, polish ponder with BLOCK-bypass hint, fresh-eyes audit-trail-helpers extraction reducing 3-way `_load_records` duplication, web research on EU AI Act + AI detection + competitor pricing + PyPI Trusted Publishing + tamper-evident logging surfacing 5 research-derived TODOs, 5 deep multi-angle TODOs + 20 dogfooding deep phases logged). **351 tests pass** (+150 new, surface-counts green). Surface: 183 → 185 CLI commands, 133 → 135 MCP tools, core preset 46 → 48. **No version bump, no commits, no push.** Full report at `dev/REPORT-2026-05-06.md` (replaced).
- **2026-05-05** — File created. Initial backlog reflects v1 → v2 transition. 5 tracks defined. Top-5 next-session priorities recommended.
- **2026-05-05 (later)** — **C.1.a shipped** (`roam pr-analyze`). Keystone command for Roam Agent Review now exists. 23 unit + integration tests passing. Adjacent test files (test_pr_diff, test_critique, test_pr_risk_author) confirmed unaffected. Ruff format + check clean. Discoveries appended to top-5 priorities: (1) C.1.b GitHub App is now the unblocked next move; (2) AI-likelihood scoring is heuristic v1 — could be tightened with a corpus of real AI-generated diffs as ML training data (logged as new D-track candidate); (3) rules engine supports `import_from` only — adding `function_call`, `class_inherit`, `decorator_use` patterns is a natural follow-up (logged as Track-C extension). Working-tree state: `src/roam/commands/cmd_pr_analyze.py` (new), `src/roam/cli.py` (registered), `tests/test_pr_analyze.py` (new), `dev/test-ai-shaped.diff` + `dev/test-rules.yml` (smoke-test fixtures, can be cleaned or kept as docs).
- **2026-05-06 (overnight 16-phase pass)** — Built phases 11-20 (audit-trail-verify, audit-trail-export, comment-renderer drift visualisation + reviewer block, batch mode, README v2 section + sample rules, MCP wrappers + count bumps to 183 CLI / 133 MCP, GitHub Actions agent-review.yml workflow, CHANGELOG entry, helper unit tests, ruff clean) + meta-phases 21-26 (synergy review surfacing 13 TODOs + 2 inline implementations, edge-case tests + 1 real UX bug fix, polish ponder surfacing 12 TODOs, fresh-eyes recheck surfacing 6 structural TODOs, web research validating pricing + EU AI Act format alignment + 5 research-derived TODOs, 5 deep multi-angle TODOs planned for future). 201 tests pass. **No version bump, no commits, no push** per user instruction. Full report at `dev/REPORT-2026-05-06.md`. Release checklist at `dev/RELEASE-CHECKLIST.md`.
- **2026-05-05 (10-phase deepening pass)** — Both v2 product engines now real, smarter, and integrated.
  - **Phase 1**: `pr-analyze --explain` — verbose human rationale block (concerns + evidence + next steps).
  - **Phase 2**: `roam pr-comment-render` — new command, markdown PR comment from envelope. GitHub App layer is now trivial Octokit glue.
  - **Phase 3**: Rules engine extended from 1 → 4 patterns: `import_from` + `function_call` + `class_inherit` + `decorator_use`. Pattern dispatcher refactor. Smoke-tested with `eval`, `pickle.loads`, `@deprecated`.
  - **Phase 4**: AI-likelihood **language-aware weights** — Python emphasises comment_density (×0.25), TypeScript emphasises orphan_imports (×0.30), Go emphasises function_size (×0.25), 7 languages mapped. Auto-detected from file extensions plus `--language` override.
  - **Phase 5**: `pr-analyze --with-reviewers` — invokes existing `suggest-reviewers` and folds reviewer suggestions into the rationale. No duplication.
  - **Phase 6**: `pr-analyze --audit-trail` — **EU AI Act Article 12 record emission** with SHA-256 chain integrity. Genesis → linked → linked verified across 3-record chain. Schema `roam-audit-trail-v1`, JSONL format.
  - **Phase 7**: **3 new MCP tool wrappers** in `mcp_server.py`: `roam_pr_analyze`, `roam_pr_comment_render`, `roam_metrics_push`. Total MCP tools 128 → 131. Core preset 41 → 44. Surface counts updated everywhere (README, CLAUDE.md, llms-install.md, both mcp-server-card.json copies).
  - **Phase 8**: `pr-analyze --save-baseline` + `--baseline` — **drift detection**. Compute deltas: blast_radius_delta, ai_likelihood_delta, new_violations, resolved_violations. Auto-escalate verdict (SAFE → REVIEW on regression; REVIEW → BLOCK on severe regression).
  - **Phase 9**: Comment renderer **drift visualization** — `(+5 vs prev)`, `(-22 vs prev)`, `(+3 new, -2 resolved)`, regression / improvement banners.
  - **Phase 10**: Ruff format + check pass on all touched files (4 files reformatted, 0 lint errors). 137 tests pass across pr_analyze + metrics_push + pr_diff + critique + pr_risk_author + surface_counts + readme_surface_consistency.
  - **End-to-end smoke** verified the full v2 flow: `audit → metrics-push (dry-run, anonymize) → pr-analyze (with-reviewers, audit-trail, save-baseline) → pr-comment-render`. Audit trail JSONL written, baseline saved, verdict BLOCK with structured rationale.

### 5 deep TODO items — planned for future sessions (Phase 26 — 2026-05-06)

These five items each combine multiple angles (customer + engineering + security + product + compliance). Logged here so future Claude Code sessions know the substantive work ahead. **Not started this round.**

#### Deep #1 — Pen-test + adversarial robustness of Agent Review pipeline

**Angle:** security + engineering reliability.
**Why:** If the bot is the gate, a diff crafted to bypass it is a real attack vector. Examples worth testing: AI-generated code with all the right test fixtures + custom-named functions to fool naming heuristics; rule-bypass via creative import paths; audit-trail tampering with hash recomputation.
**Scope:**
- Red-team exercise: produce 10+ adversarial diffs that *should* score >70 AI-likelihood but score <30, and 10+ that score >70 despite being human-written.
- Differential test harness: feed each canonical fixture through 5 transformations (strip comments, rename functions to specifics, add fake test file, etc.) and verify the scorer behaves rationally.
- Threat model document at `dev/THREAT-MODEL.md` covering scorer-bypass + chain-tampering + rule-spoofing + DoS via huge-diff input + side-channel via stderr leakage.
- Hardening: rate-limit pr-analyze via `roam pr-analyze --max-diff-bytes`, cap rule fan-out, sanitise stderr.
**Effort:** ~1 week (investigation + 3-5 hardening fixes + threat model doc).

#### Deep #2 — Production observability + SLA for the Roam Agent Review GitHub App

**Angle:** engineering + customer + product.
**Why:** Once paying customers depend on the App, we need to know when it's slow, broken, or returning suspicious verdicts. Without this, the first SLA breach is a customer-found incident.
**Scope:**
- OpenTelemetry instrumentation in the GitHub App worker: spans for diff fetch, analysis run, comment post, audit-trail emit.
- Per-customer metrics namespace (`customer_id` as a span attribute) — so we can answer "what's @acme's p95 latency?"
- Alerting rules: chain-break (P0), >25% BLOCK rate spike (P1), >5s p95 latency (P2), missing audit-trail records (P0).
- Dashboard template (Grafana + Datadog) shipped at `templates/observability/roam-agent-review.json`.
- SLA matrix in `templates/legal/sla-matrix.md`: Team-tier 99.5% / 5s p95; Business 99.9% / 3s p95; Enterprise 99.95% / 2s p95.
- Customer-facing status page at `roam.cloud/status` with real metrics.
**Effort:** ~2 weeks once GitHub App is built (depends on C.1.b + Cloud Lite scaffold).

#### Deep #3 — Multi-language AI-likelihood validation corpus + weight tuning

**Angle:** engineering rigor + product credibility + compliance defensibility.
**Why:** Today's language-aware weights are educated guesses. For the EU AI Act compliance pitch and for honest customer claims, we need empirical evidence.
**Scope:**
- Curate **50+ known AI-generated diffs** per major language (Python / TS / JS / Go / Rust) — sources: GitHub PRs from Copilot rollouts, Cursor sessions logged with our consent, Anthropic's evaluation suites.
- Curate **50+ known human-generated diffs** per language — pre-Copilot era (2020-2022 commits).
- Run pr-analyze against both; measure precision / recall / F1 per language.
- Tune weights via grid search; document evidence chain in `evidence/ai-likelihood-tuning-2026.md` (data source, methodology, results, weight changes).
- Set up the long-term ML training pipeline (D.12 in this backlog) — distil the heuristics into a small classifier (LightGBM, ~100 features), maintain feature parity with rule-based path so we can A/B compare.
- Publish results as a blog post — credibility play for the v2 launch ("we measured this, here's what we found").
**Effort:** ~2 weeks (corpus curation is the long pole) + ~1 week if extending to ML.

#### Deep #4 — Customer onboarding flow + first-deal sales playbook

**Angle:** customer + product + revenue.
**Why:** Engines are real. Now we need the human-side playbook for converting Free → Team → Business → Enterprise. Without this, every deal is a one-off scramble.
**Scope:**
- **First-call script** (templates/sales/discovery-call.md): 6-step structure (5 min context, 10 min pain, 5 min goal, 5 min offer, 3 min logistics, 2 min close).
- **30-day onboarding email sequence** (templates/email/onboarding-d0-d30.md): Day 0 welcome, Day 1 first-PR check-in, Day 7 dashboard tour, Day 14 customisation help, Day 21 testimonial ask, Day 30 retainer pitch.
- **Free → Team upsell trigger logic** in pr-analyze: when free-tier hits 50+ PR analyses in a month, the comment includes a one-line "you'd unlock custom rules + Slack alerts on the Team tier" CTA. Disabled by default; opt-in for the eventual SaaS.
- **Churn-prevention playbook** (templates/sales/churn-prevention.md): early-warning signals (decreased PR analysis count, reduced active users), intervention scripts, save offers.
- **Annual-prepay closer** (templates/sales/annual-prepay.md): scripts + DocuSign template.
- **Onboarding Loom** (5-7 min video) recorded once first 2 customers ship; lives at roam.cloud/welcome.
**Effort:** ~1 week of writing + 30 min Loom recording. Needs first paying customer to anchor against.

#### Deep #5 — Roam Cloud Lite MVP scaffold

**Angle:** product + engineering + revenue.
**Why:** `metrics-push` works on the CLI side. The receiving API + dashboard don't exist. This is the next big chunk of v2 product engineering.
**Scope:**
- **Next.js 14 App Router scaffold** at `roam.cloud` (or a separate repo `roam-cloud-lite`).
- **Postgres schema** for metrics ingestion: `repos`, `metrics_pushes` (one row per push with allow-listed columns matching `roam-metrics-v1` schema), `users`, `subscriptions`. Idempotent ingestion (dedupe by `repo_id + git_sha`).
- **Auth**: NextAuth.js with GitHub OAuth + Google OAuth. No password.
- **Dashboard pages**: repo list (with sparkline trends), per-repo detail with line charts (health, debt, dead, danger zones, bus-factor), push history.
- **Stripe integration**: Checkout for Pro tier, Customer Portal for Team / Growth.
- **Audit-log export** (Growth tier): UI for filtering and exporting metric history as JSONL — pairs with `roam audit-trail-export`.
- **Free-tier rate limiting**: 1 push per hour per public repo (per the spec); Pro tier unlimited.
- **Public-repo metric history** retention: 30 days for Free, unlimited for Pro+.
- **Sketch lives at** `templates/products/cloud-lite-scaffold/` (architecture diagram + API endpoints + Postgres DDL) so future sessions can re-load context fast.
**Effort:** ~5-6 weeks of focused TypeScript engineering. Depends on Stripe Atlas being filed (Phase 0 #8) for billing.

---

### Web research findings (Phase 25 — 2026-05-06)

Research surfaced 5 directions worth pursuing. Pricing benchmarks confirmed; UX patterns already aligned; AI-detection state of the art is meaningfully ahead of our v1 heuristics. Logged as TODOs:

- **C.1.tt — Expand AI-likelihood signal set per industry benchmarks.** [CodeSlick analyses 150+ signals (105 hallucination patterns, 13 heuristic, 32 LLM fingerprints)](https://codeslick.dev/learn/ai-code-detection); [DEV Community catalog of 164 fingerprints across GPT-4, Copilot, Claude, Cursor](https://dev.to/roymorken/detecting-ai-generated-code-164-signals-and-tools-2026-guide-1j0g). Our 6 signals are a starting point. Promising additions: (1) **perplexity / burstiness** of identifier names, (2) **hallucinated-import detection** (already partially via orphan_imports — extend to verify imported modules exist), (3) **per-LLM fingerprints** (GPT-4 vs Claude vs Cursor have distinct signatures), (4) **comment-style fingerprints** (e.g. "This function...", "We use this approach because...", excessive "Note:"). ~1-2 wk to implement an ML-augmented detector layered on the heuristics; ~2 d for additional regex heuristics. Logged as **D.12** earlier; this expands it.
- **C.1.uu — EU AI Act Article 12 alignment check.** [Augment Code's 2026 dev-team guide](https://www.augmentcode.com/guides/eu-ai-act-2026) and [FireTail Article 12 logging mandate](https://securityboulevard.com/2026/04/article-12-and-the-logging-mandate-what-the-eu-ai-act-actually-requires-firetail-blog/) confirm: JSONL is the right format (✓ we use it), six-month retention (consider auto-rotation), full reconstructability (we have diff hash + verdict + actor + git SHA — confirm we'd survive an audit). Add a `roam audit-trail-conformance-check` command that scores the trail against Article 12 requirements (timestamps present, actor attribution, no gaps, retention met). ~3 h.
- **C.1.vv — Sticky-comment + virtualization UX confirmed.** [GitHub's 2026 PR experience update](https://github.blog/changelog/2026-04-27-github-copilot-code-review-will-start-consuming-github-actions-minutes-on-june-1-2026/) confirms sticky single-comment-per-PR is the right pattern (we already do this) and that GitHub virtualizes up to 3,000 files. For our GitHub App: collapsible `<details>` sections (we use), single sticky comment (we use), one-click "show details" (consider adding). No code changes required; UX validated.
- **C.1.ww — PyPI Trusted Publishing migration.** [PyPA gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish) recommends OIDC-based trusted publishing (no long-lived API tokens). Our current release flow likely uses an API token. Migrate `.github/workflows/release.yml` (or wherever the publish step lives) to use `pypa/gh-action-pypi-publish@release/v1` with `id-token: write` permission. Lower risk profile + zero secrets to rotate. ~30 min.
- **C.1.xx — Release-on-tag GitHub Action.** Best-practice: workflow triggers on `tags: v*`, builds with `python -m build`, uploads via Trusted Publishing. Couple with the existing CHANGELOG.md "Unreleased" → "[X.YZ] - YYYY-MM-DD" promotion. Document in `dev/RELEASE.md` so future maintainers (or me-tomorrow) know the flow. ~30 min for the workflow + ~30 min for the doc.

Pricing benchmarks confirmed (May 2026):

| Tool | Price | Source |
|---|---|---|
| CodeRabbit Pro | $24/user/month annual ($30/mo monthly) | [DEV Community](https://dev.to/rahulxsingh/coderabbit-pricing-in-2026-free-tier-pro-plans-and-enterprise-costs-1pc4) |
| Greptile | $30/seat/month + $1/review over 50 | [Surmado Blog](https://www.surmado.com/blog/best-greptile-alternatives-2026/) |
| Qodo Teams | $30/user/month | [DEV Community](https://dev.to/rahulxsingh/qodo-ai-pricing-free-vs-teams-vs-enterprise-plans-in-2026-2mh5) |

Our **Roam Agent Review Team tier at $20/dev/month** is correctly positioned below all three — undercutting on price while differentiating on the structural-not-semantic angle. No action required; positioning validated.

### Fresh-eyes recheck findings (Phase 24 — 2026-05-06)

After running 201 tests + ruff check across all v2 work, the following structural issues are visible (none broke anything; logged for future tightening):

- **C.1.nn — Duplicated git-helper code.** `_git_actor`, `_git_origin_short`, `_git_head_sha`, `_git_metadata` exist in slightly different shapes across `cmd_pr_analyze.py` and `cmd_metrics_push.py`. Extract to a shared `roam.commands.git_helpers` module (mirrors the existing pattern of `changed_files.py` + `codeowners_helpers.py`). ~30 min. Adds DRY without behavioural change.
- **C.1.oo — `_detect_roam_version` / `_detect_tool_version` duplicated.** Same function under two names in `cmd_pr_analyze.py` and `cmd_metrics_push.py`. Single source of truth in `roam.__init__.__version__` already; helpers should call it directly. ~10 min.
- **C.1.pp — `cmd_pr_analyze.py` is now ~900+ lines.** Could split into `cmd_pr_analyze.py` (CLI surface) + `pr_analyze/scoring.py` (AI-likelihood) + `pr_analyze/rules.py` (rule patterns) + `pr_analyze/audit_trail.py` (chain emission) + `pr_analyze/drift.py` (baseline comparison). Improves discoverability for future contributors. ~2 h.
- **C.1.qq — Test coverage gap on `_emit_batch`.** Currently exercised by smoke test only; no unit test. Add a fixture-based test that mocks `runner.invoke` and verifies aggregation. ~30 min.
- **C.1.rr — `_load_rules_yaml` doesn't validate field types.** A rule with `severity: 42` (number not string) would pass the current loader and break later in `_check_rules` when comparing to `"BLOCK"`. Add type-coerce + warn. ~20 min.
- **C.1.ss — Audit-trail uses local timezone notation (`Z` suffix on `datetime.now(...)`).** Already UTC, but `.isoformat().replace("+00:00", "Z")` is fragile across Python versions. Use `datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")` for stability. ~5 min.

### Polish ponder TODOs (Phase 23 — 2026-05-06)

After running edge-case tests + reviewing the new commands, the following polish opportunities surfaced:

- **C.1.bb — Surface a clear warning when `--rules PATH` doesn't exist.** Today the loader silently returns `[]` and the user sees `0 violations` with no hint. Should log `_meta.rules_warning: "rules file not found at PATH"` into the envelope. ~20 min.
- **C.1.cc — `--rules-strict` mode:** fail (exit 5) if rules file is malformed. Default tolerant; opt-in strict for CI. ~30 min.
- **C.1.dd — Streaming JSON in `--batch` mode:** emit per-file as completed instead of buffering everything. Large batches feel slow today. ~1 h.
- **C.1.ee — Progress bar for `--batch`:** "Analysing 7/50 (test-foo.diff)..." stderr line. Use rich/click's progressbar. ~30 min.
- **C.1.ff — `pr-analyze --watch SECONDS`:** poll git diff and re-run on change. For local dogfooding + live PR review. ~1 h.
- **C.1.gg — Rule schema validator:** emit structured warnings ("rule `foo` missing `severity`, defaulted to WARN") instead of silent skips. ~45 min.
- **C.1.hh — AI-likelihood signal explanations in the PR comment.** Today: "generic_naming: 80/100". Better: "generic_naming 80 = 80% of new functions use handle_/process_/manage_ prefix (characteristic of agent output)." ~30 min.
- **C.1.ii — Verdict comparison rendering on drift.** Today: "blast +5 vs prev". Better: "blast 51 → 56 (+5)". Explicit before-after for clarity. ~15 min.
- **C.1.jj — Whole-repo rule scope** (not just added lines). E.g. removing a defensive check counts as a violation. Optional `--rule-scope=diff|repo` flag. ~3 h.
- **C.1.kk — `--quiet` output mode for CI:** verdict + 1-line summary only; full breakdown only via `--json`. ~20 min.
- **C.1.ll — Per-rule baseline:** drift detection should highlight when a NEW rule was added vs an existing rule's violation count changed. ~1.5 h.
- **C.1.mm — `roam audit-trail-prune --before TIMESTAMP`:** rotate old records out of the JSONL while preserving chain integrity (write a "rotation" record that links the old chain's last hash). ~2 h.

### Synergy & smart-improvement TODOs (Phase 21 — 2026-05-06)

After 10 more phases (11-20: audit-trail-verify, audit-trail-export, batch mode, README v2 section, MCP wrappers, GitHub Actions template, CHANGELOG, helper unit tests, ruff polish), the following synergy / smart-loop opportunities are now visible:

- **C.1.q — pr-analyze auto-verifies audit-trail chain when `--audit-trail` is used.** Append + verify in one go; warn if chain corruption detected before the new record lands. ~30 min.
- **C.1.r — pr-analyze `--gate` fails when audit-trail is broken.** Today gate fires only on BLOCK verdict. A tampered trail in CI is a bigger compliance risk; should gate too. ~15 min.
- **C.2.c — metrics-push folds in `last-pr-analysis.json` summary** (verdict, blast, ai) so Cloud Lite dashboard shows last-PR-verdict alongside health-trend. ~30 min.
- **C.1.s — pr-comment-render `--from-baseline` shorthand.** Today needs envelope on stdin / file; auto-load `.roam/last-pr-analysis.json` when neither is given. ~15 min.
- **C.1.t — `roam rules-validate FILE`** — lint a `.roam/rules.yml` for syntax + glob validity + dry-run match against a sample diff. Catches typos before customers ship. ~2 h.
- **C.1.u — pr-analyze `--explain` shows weighted signal contributions** — not just "comment_density: 60/100" but "comment_density: 60/100 → 12.0 pts (×0.20 weight)". Educational. ~20 min.
- **C.1.v — `audit-trail-export --aggregate`** — aggregate counts per actor / repo / verdict / month. Procurement loves "Q1 2026: 15 BLOCK, 3 INTENTIONAL bypass" tables. ~1 h.
- **C.1.w — pr-prep result caching** — SHA256(diff_text + rules + threshold) → cached envelope so repeated pr-analyze on the same diff is fast. ~1.5 h.
- **C.1.x — pr-analyze `--batch` parallel via multiprocessing** — current is sequential; 50-diff batch takes minutes. Parallel: 4-8x speedup. ~2 h.
- **C.2.d — metrics-push `--watch N`** — push every N minutes for long-running dev sessions; useful for dogfood + always-on monitoring. ~1.5 h.
- **C.1.y — README "Quickstart" subsection for the v2 features** — `git diff | roam pr-analyze` should appear in the existing Quick Start, not just the dedicated section. ~15 min.
- **C.1.z — `roam pr-analyze --diff-from-pr URL`** — fetch a GitHub PR diff via the GH API; handy for `roam pr-analyze --diff-from-pr https://github.com/foo/bar/pull/123`. ~2 h.
- **C.1.aa — Cosign signing on audit-trail records** — pair with the existing `roam.attest.cga` infrastructure. Hash chain → cryptographic chain. ~4 h.

### Discoveries from this pass (new ☐ items)

- **C.1.j** — `roam audit-trail-verify` command: walk the JSONL, verify each SHA-256 chain link, report tampered records. Pairs with `pr-analyze --audit-trail`. ~3 hr. Procurement readiness.
- **C.1.k** — `roam audit-trail-export --format=pdf|json|csv` for procurement deliverables. ~4 hr.
- **C.1.l** — Comment renderer should display the `rationale.suggested_reviewers` block when `--with-reviewers` was used (currently shows in --explain text but not the markdown comment). ~30 min.
- **C.1.m** — `pr-analyze --batch DIR` mirroring `critique --batch`: analyse every `*.diff` / `*.patch` in a dir in one pass. ~1 hr.
- **C.1.n** — `pr-analyze --diff-from-pr URL` — fetch a GitHub PR diff directly via the GH API. ~2 hr (octokit-equivalent in Python urllib).
- **C.1.o** — Cosign signing on audit-trail records — pair with the existing `roam.attest.cga` infrastructure for cryptographic guarantee on top of the SHA-256 chain. ~4 hr.
- **C.2.b'** — `roam metrics-pull` companion to push — fetch trends from Cloud Lite (or read from `--from-file` for offline), render trend deltas + alerts. ~4 hr (depends on Cloud Lite API existing or `--from-file` mode).
- **C.1.p** — Tests for the new pr-analyze helpers: `_compute_drift`, `_emit_audit_trail_record`, `_detect_primary_language`, `_capture_suggest_reviewers` (currently covered E2E only). ~2 hr.
- **D.12** — AI-likelihood v2 ML model trained on a corpus of real AI vs human diffs. Could replace heuristics or augment them. ~1 wk + dataset curation.
- **D.13** — README v2 product section — dedicated "Roam Agent Review + Cloud Lite" section in README.md explaining the wedge for casual repo visitors. ~1 hr.

---

## Round 2 (2026-05-06 overnight) — meta-phase outputs

### Synergy & smart-loop TODOs (Phase 11 — Round 2)

3 inline implementations shipped this round (rules-validate suggestion when warnings fire; metrics-push stale-pr detection with `age_days` + `stale: bool` in payload; batch-mode `parallel_workers` in summary). Remaining TODOs:

- **C.1.zz** — pr-analyze `--audit-trail` should optionally call `audit-trail-conformance-check` post-emission; warn if score drops. ~30 min.
- **C.1.aaa** — audit-trail-export `--aggregate` JSON summary should include "top actor / top month" snapshot fields for at-a-glance procurement view. ~20 min.
- **C.1.bbb** — Document `pr-analyze --cache + --batch` combo in the v2 README section (re-running the same batch is now instant). 5 min docs.
- **C.1.ddd** — metrics-push integrates conformance score (when `--include-pr-analysis`) — Cloud Lite Growth tier dashboard surfaces compliance posture alongside trends. ~30 min.
- **C.1.eee** — `pr-analyze --batch --cache` summary reports cache hit-rate (e.g. "47/50 from cache"). ~20 min.
- **C.1.fff** — pr-comment-render `--last-pr-link` — when drift block present, link to previous analysis ("Previous: BLOCK at 2026-05-04T12:00:00Z"). ~20 min.
- **C.1.ggg** — rules-validate `--fix` mode auto-coerces severity case + trims whitespace + writes back. Risky for typos so keep opt-in. ~1 h.
- **C.1.iii** — audit-trail-conformance-check `--sarif` for GitHub Code Scanning ingestion (verdict surfaces as a SARIF rule, failed checks as findings). ~1 h.
- **C.1.jjj** — `audit-trail-export --top-actors` — top 10 actors by BLOCK count in the period. Useful for pen-test reviews. ~30 min.
- **C.1.kkk** — rules-validate `--explain` shows what each pattern matches with a glob example. Good first-time-author UX. ~30 min.
- **C.1.lll** — pr-comment-render `--last-pr-analysis-summary` — when `--from-baseline`, prepend "Last analysis was X days ago" line. ~15 min.

### Edge-case test TODOs (Phase 12 — Round 2)

`tests/test_v2_edge_cases.py` (19 tests) covers CRLF, empty / sparse, malformed YAML aliases, unicode in rules, huge diffs, fully-empty records, garbage timestamps, PyYAML billion-laughs resistance. Specific findings worth tracking:

- **E.7** — `_compute_ai_likelihood` with CRLF diffs scores slightly differently — `\r` ends up as part of the line text and may flip the leading-whitespace check. Low priority but worth a `.replace("\r", "")` upfront. ~10 min.
- **E.8** — pr-analyze on a truly trivial single-line diff (`{}`) goes through full pr-prep aggregator on every invocation — cache helps, but could short-circuit when `len(diff_text.strip()) < 50`. ~30 min.

### Polish TODOs (Phase 13 — Round 2)

1 inline (BLOCK-without-intent now hints at `--intent "[intentional] reason"` bypass). Remaining:

- **P.5** — pr-analyze `--explain` should mention pairing with `--json` for full programmatic access. ~5 min.
- **P.6** — pr-analyze `--quiet --json` shouldn't be allowed simultaneously (--json wins, but Click warning would help). ~10 min.
- **P.7** — audit-trail-conformance-check JSON should also include the `disclaimer` field at top level (not buried in summary), so procurement consumers don't miss it. ~5 min.
- **P.8** — rules-validate text output: when `--gate` is the trigger for non-zero exit, emit a "Re-run without --gate to see warnings" line. ~10 min.
- **P.9** — pr-analyze `--batch --parallel N` should warn when N > os.cpu_count() (oversubscription wastes time). ~10 min.

### Fresh-eyes TODOs (Phase 14 — Round 2)

1 implementation done (`audit_trail_helpers.py` extracts `load_records` + `DEFAULT_AUDIT_TRAIL_PATH` from cmd_audit_trail_export + cmd_audit_trail_conformance — was a 3-way duplicate). Remaining:

- **F.7** — `cmd_pr_analyze.py` is 1862 lines, +50% from start of session. Split plan still valid: `pr_analyze/scoring.py` (signals + weights), `pr_analyze/rules.py` (matchers), `pr_analyze/audit_trail.py` (chain emission), `pr_analyze/drift.py` (baseline comparison), `pr_analyze/cache.py` (cache helpers). ~3 h refactor; preserves all imports via re-exports.
- **F.8** — `_signal_explanation` (in cmd_pr_comment_render.py) duplicates the signal name list from cmd_pr_analyze. If a new signal is added to the scorer but not the explanation, the comment shows it without explanation (degrades gracefully). Add a `test_all_signals_have_explanations` parametrised test. ~20 min.
- **F.9** — `DEFAULT_AUDIT_TRAIL_PATH` exists in cmd_pr_analyze.py + cmd_audit_trail_verify.py + audit_trail_helpers.py. Re-export from `audit_trail_helpers` and import from there. ~10 min cleanup.
- **F.10** — metrics-push `HTTP_TIMEOUT = 15` is a hardcoded constant. Expose as `--timeout SECONDS` flag for slow-network customers. ~15 min.

### Web research findings (Phase 15 — Round 2)

Five research topics covered:

- **EU AI Act Article 12 enforcement** — confirmed [August 2 2026 enforcement deadline](https://artificialintelligenceact.eu/article/12/) for high-risk systems. Tier 2 penalties up to €15M / 3% global turnover for non-compliance. JSONL with hash chains is the right format ([FireTail blog](https://www.firetail.ai/blog/article-12-and-the-logging-mandate-what-the-eu-ai-act-actually-requires)). Our 6-check conformance scorer aligns with the recognised checklist (timestamps + actor + reproducibility + retention). [Help Net Security 2026](https://www.helpnetsecurity.com/2026/04/16/eu-ai-act-logging-requirements/).
  - **Action**: keep the `disclaimer` field surfaced; consider adding `conformance-check --evidence-pack` that bundles the trail + verifier output + conformance score for procurement evidence packages. Logged as **C.1.zzz** ~3 h.
- **CodeRabbit / Greptile / Qodo competitor pricing 2026** — [CodeRabbit Pro $24/user/mo annual ($30 monthly)](https://aicodereview.cc/blog/coderabbit-pricing/), Greptile $30/seat with [82% bug catch rate](https://www.greptile.com/greptile-vs-coderabbit) but high false positives, Qodo Merge $19/seat self-hosted with [60.1% F1 score](https://dev.to/rahulxsingh/qodo-vs-coderabbit-ai-code-review-tools-compared-2026-kdp). **Roam Agent Review at $20/dev/mo Team is correctly positioned** below all three. Our differentiator: STRUCTURAL signals (blast radius + AI-likelihood + drift) vs SEMANTIC bug-catching (CodeRabbit/Greptile/Qodo). No code change required.
- **AI-generated code detection** — Industry research: [29-45% of AI-generated code has security vulnerabilities](https://sqmagazine.co.uk/llm-hallucination-statistics/), 20% of AI-suggested package imports are hallucinated. Modern detection uses ensemble methods (10-15% accuracy improvement) + statistical fingerprints (Seq-Logprob). Our 9-signal heuristic covers a reasonable surface. **Action: add `D.14` — extend `suspicious_imports` to verify imports actually exist on PyPI / npm registry** (would cover the hallucinated-package angle). ~3-4 d (needs registry caching to stay fast).
- **GitHub App / Probot / Octokit best practices** — [Probot framework recommends TypeScript](https://probot.github.io/docs/development/), `context.octokit.rest.issues.updateComment` for sticky comments, sticky-comment pattern is current best practice. Our pr-comment-render Python output works fine via shell-out from the Probot worker. No code change required; documents the architecture for the future GitHub App.
- **PyPI Trusted Publishing** — [pypa/gh-action-pypi-publish action](https://github.com/pypa/gh-action-pypi-publish) eliminates API tokens via OIDC. Requires `id-token: write` permission at job level. Sigstore attestations now default-on. **Action: migrate `release.yml` workflow to Trusted Publishing pre-12.27 release.** Logged as **C.1.www** ~30 min when ready to ship next release.
- **SHA-256 chained log forensic format** — [DEV Community guide](https://dev.to/veritaschain/building-a-tamper-evident-audit-log-with-sha-256-hash-chains-zero-dependencies-h0b) confirms our approach is canonical. Best practice: include monotonic sequence numbers per record + an `AuditIntegritySummary` closing record (`hash_algorithm`, `event_count`, `chain_head`, `merkle_root`). Our records have implicit ordering via JSONL position; explicit sequence numbers would harden against partial-write detection. **Action: add `sequence_number` field to audit-trail records (back-fill on demand from line position)** + emit a closing `AuditIntegritySummary` line when `audit-trail-export --finalize` is invoked. Logged as **C.1.xxx** ~2 h.

### 5 deep multi-angle TODOs — planned for future sessions (Phase 16 — Round 2)

These are NEW for this round (the 5 from previous round remain valid). Each combines 3+ angles (customer + engineering + security + product + compliance + operations).

#### Deep #6 — Roam Agent Review GitHub App TypeScript scaffold

**Angles:** product + engineering + customer onboarding + revenue.
**Why:** The CLI engine (`pr-analyze` + `pr-comment-render` + audit trail) is now production-grade. The missing piece is the hosted GitHub App that webhook-fires on PR events. Without this, the v2 Agent Review product can't be sold.
**Scope:**
- Probot scaffold at `roam-agent-review/` (separate repo) with TypeScript, deployed on Vercel / Fly / Cloudflare Workers.
- Install flow: GitHub App OAuth + organisation install + per-repo configuration page.
- Webhook handler for `pull_request.opened` + `pull_request.synchronize` + `pull_request.reopened`.
- Sandboxed worker (ephemeral Docker / Firecracker microVM with `pip install roam-code` cached): clone repo at PR head, run `roam pr-analyze --json --rules .roam/rules.yml --audit-trail`, capture envelope.
- Sticky comment posting via `context.octokit.rest.issues.updateComment` (find-or-create by deterministic marker).
- Per-customer audit-trail storage (S3 + retention policy).
- Stripe Checkout for Team ($20/dev/mo) + Business ($499/mo flat) tiers.
- Status page at `roam.cloud/status` with uptime + p95 latency badges.
**Effort:** ~6-8 weeks of focused TypeScript engineering. Depends on Stripe Atlas being filed (currently user-pending) for billing.

#### Deep #7 — Roam Cloud Lite metrics dashboard scaffold

**Angles:** product + engineering + revenue + UX.
**Why:** `metrics-push` works on the CLI side. The receiving API + Next.js dashboard don't exist. Without these, the Cloud Lite product can't accept paying customers.
**Scope:**
- Next.js 14 App Router scaffold at `roam-cloud-lite/`.
- Postgres schema: `repos`, `metrics_pushes` (one row per push, allow-listed columns matching `roam-metrics-v1` schema), `users`, `subscriptions`. Idempotent ingestion (dedupe by `repo_id + git_sha`).
- Auth: NextAuth.js with GitHub OAuth + Google OAuth. No password flow.
- Dashboard pages: repo list (sparkline trends), per-repo detail with line charts (health, debt, dead, danger zones, bus-factor), push history, last-PR verdict card (uses the new `last_pr_analysis` block from metrics-push).
- Stripe self-serve Checkout for Pro tier; Customer Portal for Team / Growth.
- Free-tier rate limiting (1 push/hour per public repo); Pro+ unlimited.
- Public-repo metric history retention: 30 days Free, unlimited Pro+.
- Threshold-alert webhooks (Slack / Linear / email) for Team+.
- **Article 12 audit-log export** at Growth tier (UI for filtering + downloading metric history as JSONL). Pairs with `roam audit-trail-export --aggregate` from the CLI.
**Effort:** ~5-6 weeks of focused TypeScript engineering. Depends on Stripe Atlas + Vercel + Postgres provider.

#### Deep #8 — Adversarial robustness suite for AI-likelihood scoring

**Angles:** security + engineering + customer trust + compliance defensibility.
**Why:** The Agent Review bot becomes the gate. A motivated adversary can craft an AI-generated diff that scores <30 to bypass the gate. We must measure and defend against this. For the EU AI Act compliance pitch, we need empirical evidence the gate is hard to subvert.
**Scope:**
- Curate **adversarial test corpus**: 50+ AI-generated diffs that *should* score >70 but score <30 (real evasion attempts) + 50+ human-written diffs that *should* score <30 but score >70 (false positives).
- Differential test harness: each fixture run through 5 transformations (strip comments, rename functions to specific terms, add fake test file, etc.) — verify scorer behaves rationally.
- Threat model document at `dev/THREAT-MODEL.md`: scorer-bypass + chain-tampering + rule-spoofing + DoS via huge-diff input + side-channel via stderr leakage.
- Hardening pass: `roam pr-analyze --max-diff-bytes`, cap rule fan-out, sanitise stderr.
- Publish results as a blog post — credibility play for the v2 launch ("we measured this, here's what we found"). Differentiates us from competitors who publish accuracy claims without adversarial testing.
**Effort:** ~2 weeks (corpus curation is the long pole) + 3-5 hardening fixes + threat model doc.

#### Deep #9 — Customer-deploy audit-trail "evidence pack" for procurement

**Angles:** compliance + customer + product + revenue.
**Why:** Procurement reviews are the long pole in enterprise sales. A turnkey "evidence pack" — chain-verified trail + conformance score + aggregate report + signing receipt + threat model — turns a 6-week procurement loop into a 2-day one. This is the EU AI Act wedge, productised.
**Scope:**
- New CLI command: `roam audit-evidence-pack --output evidence-pack.zip` that bundles:
  - Full audit trail JSONL (with sequence numbers + integrity summary).
  - audit-trail-verify output.
  - audit-trail-conformance-check JSON.
  - audit-trail-export --aggregate markdown.
  - Cosign / Sigstore signature on the bundle (using the existing `roam.attest.cga` infrastructure).
  - Pre-filled procurement Q&A template (data residency, retention, encryption, access controls).
- Procurement playbook in `templates/sales/procurement-evidence.md`: when to use, what to expect from a reviewer, common follow-up questions.
- Reference customer testimonial collection process — first 5 enterprise deals get a free evidence pack + a structured interview about the procurement experience.
- Pricing: included in Self-Hosted ($5K-$100K/yr) and Cloud Lite Growth ($299/mo); à-la-carte at $1.5K per pack for Team-tier customers.
**Effort:** ~2 weeks engineering + 1 week sales playbook + 1-2 weeks first-customer feedback loop.

#### Deep #10 — Multi-language pr-analyze validation corpus + ML-augmented scorer

**Angles:** engineering rigor + product credibility + compliance defensibility + research.
**Why:** Today's 9 heuristic signals + language-aware weights are educated guesses. For the EU AI Act compliance pitch and for honest customer claims, we need empirical evidence. For the long-term, the heuristics are a feature ceiling; an ML-augmented layer can push accuracy meaningfully higher.
**Scope:**
- Curate **150+ known AI-generated diffs** per major language (Python / TS / JS / Go / Rust). Sources: GitHub PRs from Copilot rollouts, Cursor sessions logged with consent, Anthropic evaluation suites, internal dogfood.
- Curate **150+ known human-generated diffs** per language — pre-Copilot era (2020-2022 commits) + manually-vetted recent.
- Run pr-analyze against both; measure precision / recall / F1 per language. Publish baseline.
- Tune weights via grid search; document evidence chain in `evidence/ai-likelihood-tuning-2026.md`.
- Add a small classifier (LightGBM, ~100 features derived from the existing signals + raw_metrics). Maintain feature parity with rule-based path so we can A/B compare.
- Publish results as a blog post — "What 1,500 diffs taught us about AI code". Anchor for the v2 launch + ongoing thought leadership.
- Open-source the corpus (after PII review) so the field benefits + customers trust the methodology.
**Effort:** ~3 weeks corpus curation (long pole) + ~1.5 weeks ML scaffolding + ~1 week blog/launch prep.

### 20 deep dogfooding phases — for future sessions (Round 2 user request)

These are dogfood-themed deep tasks. Each represents 1+ week of substantive work. The aim: use roam-code on roam-code (and other real codebases) to find weaknesses + ship improvements + generate trust signals for v2 launch. Log here so any future session can pick one.

#### DF.1 — Daily dogfood: run `roam pr-analyze --gate` on every roam-code commit for 30 days
Set up a self-CI workflow that runs Agent Review on every PR / push to roam-code itself. Capture the audit trail; review weekly. Goal: surface false positives / false negatives in the scorer that only emerge on real, evolving code. Effort: 1 d setup + 30 d passive observation + ~3 d analysis. **Outputs**: weight tuning evidence, scorer bug list, blog post "30 days of dogfood".

#### DF.2 — Index 20 awesome-list OSS projects, run pr-analyze on their last 10 PRs each
Pick 20 popular projects (FastAPI, ruff, polars, hypothesis, etc.). For each, clone + index + analyse last 10 PRs. Capture: false-positive rate, signal coverage, language-specific quirks. **Outputs**: 200-PR validation corpus, blog post "How Roam scored 200 OSS PRs". Effort: ~1.5 wk.

#### DF.3 — Run all 9 v2 commands on a 100k-LOC monorepo (Apache Beam, Bazel, or similar)
Stress-test with size. Measure: latency per command, memory peak, where pr-prep / pr-analyze starts to feel slow. **Outputs**: perf-test fixtures + 5-10 perf bugs filed. Effort: ~1 wk.

#### DF.4 — Self-score: roam audit + critique + pr-analyze on roam-code itself, fix top 10 findings
Eat our own cooking. The output verdict should be SAFE; if not, fix what we find. **Outputs**: 10 PRs that improve roam-code's own metrics + a "we scored ourselves" trust badge for the README. Effort: ~1.5 wk.

#### DF.5 — Author 5 production-grade .roam/rules.yml packs for popular frameworks (Django, FastAPI, Express, Next.js, Spring)
For each framework, identify the 10-15 anti-patterns specifically harmful in agent-generated code. Ship as `templates/rules/{framework}/.roam-rules.yml`. **Outputs**: 75 rules, blog post "Architecture rules every {framework} team needs". Effort: ~2 wk.

#### DF.6 — Build a real GitHub App POC with the Probot scaffold; run it against 5 friendly repos
Don't ship publicly — just prove the end-to-end pipe works. Webhook → ephemeral worker → pr-analyze → sticky comment → audit trail. **Outputs**: working POC + bug list + 5 testimonials. Effort: ~2 wk.

#### DF.7 — Run audit-trail-conformance-check on 30 days of real audit trails; tune the 6 checks
Validate that "score 100 = procurement-ready" matches reality. Calibrate retention default; add or split checks based on real procurement reviewer feedback. **Outputs**: tuned scorer + evidence the score predicts procurement outcomes. Effort: ~1.5 wk.

#### DF.8 — Write the "Roam Agent Review changelog" — automated from the audit trail
Use the audit trail as the source-of-truth for "what changes the bot caught last quarter". Generate a customer-facing changelog. Demo for prospects. **Outputs**: `roam audit-trail-changelog --since Q1` command + sample report. Effort: ~1 wk.

#### DF.9 — Test the cache on a 50-file batch with 30 unchanged + 20 modified — measure speedup
Real benchmark of `--cache + --batch + --parallel`. Goal: show 10x+ speedup on incremental runs. **Outputs**: bench harness, blog post "Why CI runs are 10x faster with cache". Effort: ~3 d.

#### DF.10 — Push 90 days of Roam audit metrics to a fake Cloud Lite endpoint; build the trend dashboard mockup
Doesn't need real Cloud Lite — generate sample data into a local Postgres + Streamlit / Next.js mockup. Validate the dashboard shape before building the real one. **Outputs**: dashboard mockup + UX validation feedback from 5 pilots. Effort: ~2 wk.

#### DF.11 — Adversarial corpus: curate 50 diffs that bypass the scorer; ship hardening fixes
The "red team Roam" exercise from Deep #8 above. **Outputs**: 50 adversarial fixtures, 5+ hardening fixes, threat model doc. Effort: ~2 wk.

#### DF.12 — Test pr-analyze on Apache Beam, Bazel, Linux kernel — all 3 are >1M LOC
Stress at extreme size. Identify where the indexing assumptions break. **Outputs**: perf bugs + perhaps a `--scope DIRECTORY` flag to bound analysis. Effort: ~2 wk.

#### DF.13 — Run Agent Review on a deliberately-broken PR (drop test, weaken validation, etc.) — measure detection rate
50 test PRs designed to be subtly broken. Score detection rate per category (test removal, validation weakening, security regression). **Outputs**: detection-rate scorecard published quarterly. Effort: ~1.5 wk.

#### DF.14 — Friendly-customer pilots — ship real Agent Review to 3 paying-pilot teams for 2 months
After GitHub App POC, recruit 3 small teams ($20/dev/mo discount for early access) for a 60-day pilot. Capture: setup time, false-positive rate, churn signals. **Outputs**: 3 case studies + product-market-fit signal. Effort: 2 mo elapsed, ~3 wk active engineering.

#### DF.15 — Run audit-trail evidence pack through a real procurement reviewer (consultant or friendly enterprise)
Pay a procurement consultant to review one Roam evidence pack as if it were a real vendor submission. Capture: gaps, confusing parts, unanswered questions. **Outputs**: evidence-pack v2 + procurement playbook updates. Effort: ~1 wk + consultant fee.

#### DF.16 — Migrate roam-code's own release flow to PyPI Trusted Publishing
Migrate the existing release workflow per the research finding. Drop API tokens, add `id-token: write`, switch to OIDC. Use roam-code's release as a customer-facing example. **Outputs**: hardened release workflow + blog post. Effort: ~3 d.

#### DF.17 — Build a `roam dogfood` command that runs the full v2 stack on the current repo
Bundles audit + pr-analyze (on uncommitted) + audit-trail-conformance-check + evidence-pack into one invocation. The "show me everything roam can do for me" command. **Outputs**: 1 new CLI command + onboarding tutorial centered on it. Effort: ~1 wk.

#### DF.18 — Publish the Roam audit trail of roam-code itself — public on roam.cloud/audit/cranot/roam-code
Show what a fully-instrumented project looks like. Live. Updated on every push. Trust signal + concrete example. **Outputs**: public dashboard + blog post. Effort: ~2 wk.

#### DF.19 — Test cross-language pr-analyze: a single PR touching Python + TypeScript + Go simultaneously
Real PRs do this. Validate the language-detection + signal weighting handles mixed PRs gracefully. **Outputs**: bug fix or confirmation, plus a multi-language test fixture. Effort: ~1 wk.

#### DF.20 — Run Agent Review on Anthropic's own public AI evaluation diffs (where consent permits)
The reverse: use AI-eval datasets to *validate* our AI-detection, instead of the other way around. Anchor publishable results: "Roam detected X% of AI-generated patches in the OpenAI Code corpus". **Outputs**: F1 / precision / recall numbers + blog post + credibility for v2 launch. Effort: ~2 wk.
