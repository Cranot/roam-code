---
name: roam-release-readiness
description: "Qualify a Roam commit or accumulated batch for package, website, or server publication; reconcile source, tests, review, CI and deployed identities, and identify the next authorized release step. Use for Roam release/deployment or synchronization readiness requests, not ordinary local edits, copywriting, or detector diagnosis."
---

# Roam release readiness

Determine what can advance, what remains unverified, and the next authorized
operation for each requested surface. A readiness review is not publication;
publication on one surface is not evidence for the others.

## Bind the candidate and authority

Read current `AGENTS.md`, `docs/repository-maintenance.md`, and the relevant
release owner: `docs/releases.md` for packages or
`docs/website-maintenance.md` for Pages. Read `docs/containers.md` only when an
image is requested, and `docs/concepts/verification-evidence.md` when assessing
proof bundles, verdicts or review receipts. Use `internal/EXECUTION-SEQUENCE.md`
for the active cursor when present; otherwise consult `internal/INDEX.md` and
existing release-driver phase records without inventing a replacement board.
Missing private context is a named limit, not an automatic veto of sufficient
public release evidence. Treat earlier artifacts as dated context, not live
facts. Follow the user's specific scope; a skill does not grant release approvals
or alter policies.

Inventory HEAD/upstream, staged and unstaged changes, untracked files, relevant
worktrees/stashes and remote/server state before synchronization or cleanup.
Record exact identities and preserve unique work. A clean branch relationship
does not prove a worktree or server is clean. Stage only reviewed explicit paths;
never absorb unrelated edits, rewrite published history or move a release tag.
For index-only executable-bit changes, follow AGENTS.md's Windows commit-pathspec
exception and verify the staged set and resulting commit, not just command success.

Distinguish candidate contents, installed editable version, release tag, registry
artifact, CI commit, review subject, server revision and Pages deployment. Check
current state when a decision depends on it. Keep a compact per-surface record
linking required evidence to its exact source and operation; do not reduce these
identities to one green label.

## Qualify the change that will actually land

Derive checks from the complete candidate, not the last small edit or commit
message. Include relevant untracked files in the review packet and test scope.
The venv `roam --json verification-contract --files-from <list>` can suggest
obligations; inspect current help, its inspected denominator and skips. It does
not replace the project release rules or expert review of the diff.

FAST/FULL structural gates are not the release-sized suite. An accumulated
release-sized batch needs `scripts/prepush_check.py --release` with a bounded
worker budget in the locked venv. Read that run's actual coverage note rather
than copying a stale list of unproven CI lanes. Capture start, command, selected
scope, final exit and test outcomes. For long runs use a durable runner supported
by the host with an explicit completion marker; a launched or abandoned process
is pending, not green. Avoid concurrent heavy gates on a constrained workstation.

Bind independent review to the same artifact and requested model/effort when
specified. A prompt, dispatch, tool-read count or schema-valid receipt is not a
completed review. Preserve actual output, identity evidence and every finding's
disposition. Editorial review does not approve runtime changes. Changed bytes
need appropriate requalification; do not relabel an earlier verdict or accept
residual risk on the owner's behalf. Skips and missing baselines stay explicit.

For material product-copy changes, record factual support, product-meaning review
and owner acceptance separately from technical gates. Tests and browser checks
cannot establish that the page communicates the intended product. Recheck current
owner direction: an unresolved positioning objection is not superseded by a prior
editorial label or a green suite. Keep the affected publication pending while
advancing independent qualification; do not invent a new approval gate for typos.

Require the normal local gates before push and required CI on the exact pushed
commit before tagging or production deployment. Local success does not predict
all CI lanes. Do not bypass a failed gate or approve a protected older release
just because the user wants the current website live.

## Keep publication surfaces independent

For package pins, distinguish the version being developed from a package users
can actually install. A highest tag awaiting publication is not available PyPI
evidence. Preserve verified pins and report that synchronization hold; use the
maintained generators after publication, not global version replacement.
Classify other literals by the release guide: deliberately lagging self-consuming
action pins move only after their target is published; historical records and
illustrative fixtures are not synchronization defects. Qualify actual wheel/
sdist contents and fresh installed-package behavior outside the source checkout;
editable tests do not prove package completeness.

For the website, preserve the existing Cloudflare Pages project and committed-
export workflow. A Git push or local preview is not deployment. Use the current
`scripts/stage_site.py`/documented deploy route only after the clean candidate
and exact-commit gates pass; staging alone proves byte identity. Verify the
deployment URL and custom domain, representative content/assets, redirects and
headers against the deployed SHA. Report browser/accessibility limits separately.
A real website-only change need not wait for a Python release; a mixed batch
does not become website-only by assertion. An independent container hold blocks
the image, not automatically an otherwise qualified package or website.

For server updates, identify the actual host/checkout, process environment and
local edits before changing anything. Use the existing deployment procedure and
preserve recovery paths. Verify that the running installation uses the intended
artifact; successful SSH or an HTTP response alone is not synchronization proof.

Advance independent in-scope checks while another step waits. Stop the blocked
mutation when a missing decision, authority or evidence would materially change
it; state the narrow choice and what can still proceed. Preserve a prior verified
artifact for recovery without creating an unrequested rollback. At handoff update
the existing execution cursor, link exact artifacts, and regenerate the private
index.
