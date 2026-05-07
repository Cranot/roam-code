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
8. [Wire roam into Claude Code as a skill](#8-wire-roam-into-claude-code-as-a-skill)
9. [Compare two indices to measure a refactor](#9-compare-two-indices-to-measure-a-refactor)
10. [Ship a structural-permission verdict to a pre-commit hook](#10-ship-a-structural-permission-verdict-to-a-pre-commit-hook)

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
roam dead --safe
```

What you get: list of symbols with zero callers, no test coverage, no
public exports. The `--safe` flag excludes anything that might be
called dynamically.

To narrow to a single file:

```bash
roam dead --path src/legacy/old_module.py
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
on each of the last 30 commits — pre-merge. A great signal for "is
adopting Roam worth it" conversations.

---

## 8. Wire roam into Claude Code as a skill

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

## 9. Compare two indices to measure a refactor

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

## 10. Ship a structural-permission verdict to a pre-commit hook

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
