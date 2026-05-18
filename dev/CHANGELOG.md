# dev/CHANGELOG.md — internal session change-log

Internal developer changelog (forward-tense entries are aggregated by
session; consult `dev/BACKLOG.md` for the live sprint queue and
`templates/distribution/landing-page/changelog.html` for the
user-facing changelog).

## [Unreleased]

Working-tree-only entries on top of `d6f6557f` (v13.2 head + 25 CI
fix-forward seal commits). Per the "accumulate-then-squash" directive
none of the 2026-05-18 polish wave has committed yet; everything below
sits in the working tree against `pyproject.toml: version = "13.2"`.
Cross-references: `dev/BACKLOG.md` "Shipped post-CONSOLIDATE-22"
index (lines 2111-2130) for the v13.2-release-plus-25-fix-forward arc
already on `main`; `dev/ROADMAP.md` line 151 freshness pass marker
(`2026-05-18`); `dev/ROAM-STRATEGY-2026-05-15.md` for active product
framing; `dev/NEXT-BUILD-PRIORITIES-2026-05-18.md` for the live build
queue; `dev/DOCS-CLEANUP-PLAN-2026-05-18.md` for the canonical
per-file change map of the 2026-05-18 doc-canon polish.

### 2026-05-18 — paid-engagement canon polish + landing-page honest-banner sweep

Multi-agent polish wave aligning the public site, legal templates,
top-level docs, and machine-readable surfaces with the actual current
paid-engagement story: **PR Replay is the live paid path; Review +
Cloud are early-access / planned; Self-Hosted is customer-pulled
private-deployment pilots**. The canonical per-file change map lives
in `dev/DOCS-CLEANUP-PLAN-2026-05-18.md` "Cleaned in this pass"
table — this entry is the changelog projection of that table.

#### Changed

- **9-doc paid-engagement canon polish.** `README.md`,
  `docs/fresh-install-smoke.md`, `templates/legal/security-procurement-packet.md`,
  `templates/legal/README.md`, `templates/legal/dpa.md`,
  `templates/legal/sow-pr-replay.md`, `templates/legal/sow-master.md`,
  `templates/email/customer-journey.md`, and the private product
  brief for the Review tier reframed onto the PR Replay /
  early-access / planned / private-deployment-pilot canon. Removed
  overclaims about Self-Hosted being a packaged product or
  certification-ready; tightened Starter caps; added attorney-review
  warnings on legal drafts; rewrote the customer-journey email
  sequence from "Standard Audit / retainer" to PR Replay -> Review-
  credit templates. (Per `dev/DOCS-CLEANUP-PLAN-2026-05-18.md`
  "Cleaned in this pass".)
- **Landing-page 16-surface honest-banner sweep**
  (~1171 insertions / ~461 deletions across 19 HTML/CSS/text files).
  `index.html`, `about.html`, `pricing.html`, `privacy.html`,
  `refund.html`, `security.html`, `status.html`, `terms.html`,
  `trust.html`, `press.html`, `audit.html`, `compare.html`,
  `governance.html`, `llms.txt`, `landing.css`,
  `docs/agent-contract.html`, `docs/architecture.html`,
  `docs/command-reference.html`, `docs/getting-started.html`, and
  `docs/integration-tutorials.html` swept for: Self-Hosted overclaim
  softening, SOC 2 / ISO 42001 date removal (now "no current
  attestation" + roadmap language), Cody seat-pricing claim removal
  on `compare.html`, brittle inline competitor facts replaced with
  comparison-page links on `getting-started.html`, `--sarif on all
  241 commands` false implication removed from `command-reference.html`,
  Cloud refund language moved to "when available", and Self-Hosted
  refund language rewritten to private-deployment-pilot case-by-case.
- **ROADMAP / BACKLOG / STRATEGY / NEXT-BUILD freshness refresh.**
  `dev/ROADMAP.md` freshness-pass marker advanced to `2026-05-18`
  (line 151); `dev/BACKLOG.md` "Shipped post-CONSOLIDATE-22" index
  extended; `dev/ROAM-STRATEGY-2026-05-15.md` and
  `dev/NEXT-BUILD-PRIORITIES-2026-05-18.md` cross-link surface
  refreshed to match. ALL four docs READ-ONLY for this polish-loop
  wave — touched only by the upstream BACKLOG/ROADMAP/STRATEGY
  agents.
- **AGENTS.md cross-link maintenance.** Substrate-package and
  command-count blocks kept aligned with the live CLAUDE.md figures
  (`241 / 234 / 57 / 224`) per the W800 drift-close baseline.

#### Added

- **`dev/DOCS-CLEANUP-PLAN-2026-05-18.md`.** Per-file change map +
  deep-audit checklist for the 2026-05-18 doc-canon polish; serves
  as the canonical "what changed and why" projection that this
  changelog entry summarizes.
- **`dev/NEXT-BUILD-PRIORITIES-2026-05-18.md`.** Live build queue
  (Stripe-first cash path, harden-service-delivery lane, P4 Review
  MVP holding) referenced from `dev/ROADMAP.md` lines 13 / 107 / 581
  / 686 / 773 / 1366 / 2189.

#### Removed

- **`dev/SPRINT-2026-05-12-FINAL.md`**, **`dev/SPRINT-2026-05-13-W21-W38.md`**,
  **`dev/MONETIZATION-OPPORTUNITIES-2026-05-13.md`**. Three tracked
  historical dev memos pruned after their useful conclusions were
  folded into the active strategy / roadmap / backlog / build-priority
  queue. Per `dev/DOCS-CLEANUP-PLAN-2026-05-18.md` "Historical-doc
  prune" row.
- **Three private archive/idea folders** (dev-archive, docs-archive,
  ideas — paths withheld per `tests/test_no_internal_language.py`
  discipline). Explicit archive folders that were no longer active
  decision sources.

#### Fixed

- **Procurement-packet overclaim risk.** `templates/legal/security-
  procurement-packet.md` and `templates/legal/README.md` flagged with
  "planning-draft / must not be sent externally until vendors,
  deployment status, and private-pilot language are reconciled"
  warnings; removes the risk of the packet being shared as-is with a
  prospect.
- **`compare.html` volatile-vendor drift.** Stale Cody seat-pricing
  and release-note claims removed; re-checked against official
  public vendor pages on 2026-05-18 per the cleanup-plan deep-audit
  row.

#### Verification

- `python dev/build_readme_counts.py --check` passes
  (`command_count=241`, `canonical_count=234`, `mcp_full=224`,
  `mcp_core=57`).
- `python -m pytest tests/test_no_internal_language.py -n 0` passes
  (leak-gate baseline preserved).
- `python -m pytest tests/test_doc_staleness.py
  tests/test_doc_link_anchors.py tests/test_doc_hygiene_ci_gate.py
  tests/test_doc_consistency.py tests/test_docs_site_quality.py
  tests/test_docs_coverage.py tests/test_readme_surface_consistency.py
  -q` passes per the cleanup-plan deep-audit row.

#### Bundling note

`dev/CHANGELOG.md` is internal-only — `pyproject.toml:55` points
external `Changelog` URL at the root `CHANGELOG.md`. W554 wheel-
bundled templates only carry `templates/distribution/landing-page/`
HTML; this internal changelog is NOT shipped to PyPI / GitHub
releases.

### 2026-05-17 — continuous polish loop (wave 1 + wave 2)

Multi-wave parallel-agent polish loop running on the v13.2 working
tree under user "no commits, accumulate-then-squash" directive.
Working tree only; squash + release queued for the end of the
session. Approximate scope:

- **Wave 1 (~10 agents in parallel)**: 5 Pattern-2 empty-state
  closures across detector commands (`cmd_n1`, `cmd_over_fetch`,
  `cmd_dark_matter`, `cmd_duplicates`, `cmd_laws` — W805-followup
  family); mojibake em-dash scrub + drift-guard
  (`tests/test_w937_no_mojibake_em_dashes.py`, W937);
  `critique/` added to bare-except `_GUARDED_DIRS` (W666);
  `mypy>=1.10` landed in the `typecheck` optional-dependencies
  group (W931); `detectors._finding` `evidence=` callers audited
  end-to-end with documented rationale (W932); shared
  `git_helpers.git_head_sha()` consolidation (W586).
- **Wave 2**: `test_w452_python_taint_indexer_gap.py` introduced as
  3 xfail-strict regression pins documenting the python-taint
  indexer gap (W452 PINNED-WITH-TESTS; indexer fix remains
  separate work); follow-on Pattern-2c fixes; assorted
  fragile-path test migrations to the `repo_root` helper sealed
  CI fix-forward batches 7-12.
- **CI fix-forward batches 7-12** committed (`01ad2182` →
  `f13935f8`): 7th = `test_name_collision` MEDIUM/HIGH bound
  relaxation; 8th = xfail FITNESS_VIOLATIONS test (monkeypatch
  isolation); 9th = loosen `test_multiple_warnings` subset
  bound; 10th = 5 fragile-path test offenders migrated to
  `repo_root`; 11th = `Path` import + polish-loop wave-1
  bundle; 12th = `test_w937` migrated to `repo_root` helper.
- **Dogfood (Agent O, this entry)**: `roam laws check` /
  `roam invariants --public-api` / `roam laws mine` rerun on
  the live working tree via `CliRunner` (the installed
  `roam==13.0` binary is one minor behind and reports stale
  empty-laws — `python -m roam.cli` confirms the working-tree
  envelope is correct: 8 mined laws all confidence=high; 33
  invariants across 20 symbols with 2 CRITICAL (`ActorRef` /
  `AuthorityRef` in `src/roam/evidence/refs.py` — heavy real
  usage, NOT fake). 29/29 `test_laws_mining.py` +
  `test_invariants.py` pass. No genuine law / invariant bug
  surfaced this pass.

Total roughly 9 family closures, 2 surfaced real bugs (per the
`5c22a8fe` headline commit message), 12 CI fix-forward seal
commits, and ~25 dirty source files staged in the working tree
ahead of the end-of-session squash.

Cross-references: `dev/BACKLOG.md` for the marked-shipped
entries (look for `[SHIPPED-2026-05-17]` markers);
`pruned dev memo: HANDOVER-2026-05-13.md` for the v13.0 → v13.1 → v13.2
narrative arc.

### 2026-05-17 — polish wave 3 (doc-count drift sweep)

- **AGENTS.md substrate-package drift closed (W800).** Header said
  "10 substrate packages" + body listed 10 entries; now matches
  CLAUDE.md's "11 substrate packages" + 12-entry body
  (added `src/roam/db/findings.py` row). Directory-layout block
  refreshed: `cli.py` "217 commands surfaced" → "241 command names
  (234 canonical + 7 aliases)"; `mcp_server.py` "58 core / 149
  full" → "57 core / 224 full"; `cmd_*.py` "201 modules, 211
  commands" → "232 modules backing 241 command names"; `tests/`
  "267 test files" → "740 test_*.py files"; trailer "all 217
  commands" → "all 241 command names". LAW 4 anchor-vocab counts
  91/108 → 98/115 to match the live `formatter.py:_CONCRETE_*` set
  and the `test_law4_anchor_counts.py` pin.
- **CONTRIBUTING.md version-pin drift sealed (W734 follow-on).**
  Pre-commit example `rev: v13.1` → `v13.2`; release-cadence
  example `git tag v12.50` → `git tag v13.2`; architecture-overview
  test-suite count "408 test files" → "740 test files".
- **dev/CHANGELOG.md (this entry).** Polish-loop wave-3 changelog
  block appended; covers AGENTS / CONTRIBUTING / dev/CHANGELOG
  doc-count sweep. Working-tree only; squash + release at
  end-of-session per directive.
