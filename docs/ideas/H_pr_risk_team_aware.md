# Idea H: PR-Risk Team-Aware Novelty

## Origin
chuckjewell fork -- PR-risk with hypergraph novelty.
The idea is to make risk scoring AUTHOR-AWARE: who is making the change matters.

## Why
Current pr-risk is purely structural (blast radius, churn, coupling, novelty).
But a senior dev touching their own code is far less risky than a newcomer touching
unfamiliar code. Research shows "minor contributor" count is the #1 defect predictor.

## Research Findings

### Key Finding (Bird et al., Microsoft)
Number of "minor" contributors (< 5% of changes to a file) is the SINGLE
STRONGEST predictor of post-release defects -- stronger than complexity, churn,
or dependency counts.

### Two New Factors for pr-risk

**Factor 1: Author Familiarity Risk (weight: up to 25%)**
```python
familiarity(author, file) = sum(
    (lines_added + lines_removed) * exp(-0.005 * days_since)
    for each commit by author to file
)
normalized = author_familiarity / max(all_authors_familiarity)
familiarity_risk = 1.0 - avg(normalized across changed files)
```
Half-life: ~139 days (4.6 months). A 100-line change from 4.6 months ago
= 50-line change today in familiarity weight.

**Factor 2: Minor Contributor Risk (weight: up to 15%)**
For each changed file, check if author's churn < 5% of file's total_churn.
Fraction of "minor" files scaled to 0.15 = risk contribution.

### Data: Already in Our Schema
- `git_commits` has author + timestamp
- `git_file_changes` has lines_added/removed per commit per file
- `file_stats` has total_churn and distinct_authors
- Zero schema changes needed

### CLI Interface
```bash
roam pr-risk [RANGE] [--staged] [--author NAME]
```
Auto-detects author via `git config user.name`. Skips gracefully if undetectable.

### Output Additions
```
  Familiarity:  72.3%  (author knows 3/4 changed files well)
  Minor risk:    0.0%  (author is major contributor to all files)
```

### Privacy/Ethics
- Frame as self-assessment, not surveillance
- No productivity metrics (lines/day, speed)
- Opt-in (--author flag or auto-detect)
- Local-only (never leaves machine)
- No leaderboards or developer ranking
- Compute on-the-fly, don't persist scores

## Priority: MEDIUM (Tier 2)
## Effort: Low-Medium
## Files touched: cmd_pr_risk.py only
