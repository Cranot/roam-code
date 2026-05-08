# Roam cookbook

Short, copy-pasteable recipes for the most common roam-code workflows.
Each recipe is one section, one snippet, one expected outcome. No long
prose.

## Index

1. [Get oriented in a new codebase](#1-get-oriented-in-a-new-codebase)
2. [Audit a pull request](#2-audit-a-pull-request)
3. [Set up a CI gate that fails on high-severity findings](#3-set-up-a-ci-gate-that-fails-on-high-severity-findings)
4. [Find dead code that's safe to delete](#4-find-dead-code-thats-safe-to-delete)
5. [Trace what calls a function](#5-trace-what-calls-a-function)
6. [Generate an Article 12 scope/readiness report](#6-generate-an-article-12-scopereadiness-report)
7. [Replay current detectors against past commits](#7-replay-current-detectors-against-past-commits)
8. [Generate a PR Replay report (DIY sample or paid)](#8-generate-a-pr-replay-report-diy-sample-or-paid)
9. [Wire roam into Claude Code as a skill](#9-wire-roam-into-claude-code-as-a-skill)
10. [Compare two indices to measure a refactor](#10-compare-two-indices-to-measure-a-refactor)
11. [Ship a structural-permission verdict to a pre-commit hook](#11-ship-a-structural-permission-verdict-to-a-pre-commit-hook)

---

## 1. Get oriented in a new codebase

```bash
cd <unfamiliar-repo>
roam init
roam understand
roam tour
```

What you get: tech-stack summary, top symbols by PageRank, suggested
reading order, entry points.

---

## 2. Audit a pull request

```bash
git diff main..HEAD | roam critique
```

What you get: ranked findings — clones-not-edited, blast-radius,
layer-violations. Exit code 5 if any are high severity.

For richer output (suggested fixes + JSON envelope):

```bash
git diff main..HEAD | roam critique --json | jq '.findings'
```

---

## 3. Set up a CI gate that fails on high-severity findings

`.github/workflows/roam-gate.yml`:

```yaml
name: roam structural review
on: pull_request
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install roam-code
      - run: roam init
      - run: |
          git diff origin/main...HEAD | roam critique
        # exit code 5 fails the build on high-severity findings
```

---

## 4. Find dead code that's safe to delete

```bash
roam dead
```

What you get: list of unreferenced exported symbols with confidence
scores, grouped by removal verdict (SAFE, REVIEW, INTENTIONAL).

Useful add-ons:

```bash
roam dead --summary               # aggregate counts only
roam dead --clusters              # find dead subgraphs (symbols that
                                  # only reference each other)
roam dead --aging --effort        # age + estimated removal hours
```

---

## 5. Trace what calls a function

```bash
roam uses my_function
```

What you get: every caller of `my_function`, ranked by hop distance.
Includes test callers (separately marked).

For the full transitive set:

```bash
roam impact my_function --depth 3
```

---

## 6. Generate an Article 12 scope/readiness report

```bash
roam article-12-check
```

What you get: a 6-item Markdown checklist (audit-trail dir, trail records,
retention policy, technical docs, attestation surface, high-risk
classification heuristic). This is a scoping/readiness report for actual
Annex III high-risk AI-system buyers, not a claim that every software team has
an Article 12 obligation.

For PDF (requires `reportlab`):

```bash
roam article-12-check --pdf article-12-readiness.pdf
```

---

## 7. Replay current detectors against past commits

The "would Roam have caught my last incident?" demo:

```bash
roam postmortem HEAD~30..HEAD
```

What you get: every finding the current detector set would have flagged
on each of the last 30 commits — pre-merge. A signal for "is adopting
Roam worth it" conversations. Raw output. For a polished, buyer-facing
narrative, use recipe 8 (`roam pr-replay`).

---

## 8. Generate a PR Replay report (DIY sample or paid)

Three tiers, same engine. The free sample is the buyer-facing entry
point; Team and Deep are paid engagements.

```bash
# Free 5-PR DIY sample (watermarked, self-serve, no email needed)
roam pr-replay --tier sample

# Paid Team report — 30 PRs, written to file with client name
roam pr-replay --tier team --client "Acme Inc" --output acme.md

# Paid Deep report — 90 PRs with per-detector deep-dive
roam pr-replay --tier deep --client "Acme Inc" --output acme-deep.md

# Custom range (overrides tier-default commit count, keeps tier framing)
roam pr-replay --tier deep --range "v1.0..main" --output q1-replay.md
```

What you get: an executive summary with a verdict line, an aggregated
detector-class breakdown table (which class keeps surfacing), per-PR
ranking, recommended CI gates surfacing from the actual finding pattern,
and a methodology block. Deep tier adds a per-detector deep-dive
section.

JSON envelope for machine consumption:

```bash
roam --json pr-replay --tier sample > replay.json
# .summary, .commits, .by_detector, .report_markdown
```

A polished sample of the Team-tier output lives at
[`templates/audit-report/sample-pr-replay-team.md`](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-pr-replay-team.md)
— share with prospects who ask "what does the deliverable look like?".

See <https://roam-code.com/#audit>.

---

## 9. Wire roam into Claude Code as a skill

```bash
mkdir -p ~/.claude/skills/roam
roam skill-generate --target claude --output ~/.claude/skills/roam/SKILL.md
```

Restart Claude Code. The skill auto-activates when you ask it
codebase-comprehension questions.

For Cursor instead:

```bash
roam skill-generate --target cursor --output .cursor/rules/roam.mdc
```

For Continue or Aider, see `roam skill-generate --help`.

---

## 10. Compare two indices to measure a refactor

After a big refactor, did coupling actually go down?

```bash
# Save a baseline
cp .roam/index.db /tmp/before.db

# ... do your refactor, then re-index
roam reindex --force

# Compare
roam compare /tmp/before.db .roam/index.db
```

What you get: VERDICT (IMPROVED / SIDEWAYS / REGRESSED), lists of
symbols added/removed/moved, files that got more or less complex.

---

## 11. Ship a structural-permission verdict to a pre-commit hook

`.git/hooks/pre-commit`:

```bash
#!/usr/bin/env bash
git diff --cached | roam permit
case $? in
  0) exit 0 ;;                  # ALLOW
  6) echo "REVIEW recommended — proceeding"; exit 0 ;;
  5) echo "BLOCKED by roam"; exit 1 ;;
esac
```

What you get: every commit that AI agents prepare gets a structural-
permission check before it lands. Blocks high-severity changes;
flags medium ones.

For richer integration (Cursor rule, Claude Code permission hook),
see the [`roam permit`](https://roam-code.com/docs/command-reference#permit)
documentation.
