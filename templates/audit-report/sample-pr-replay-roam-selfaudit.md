# PR Replay Report — Showcase — roam-code self-audit

**Tier:** Team — 30 PRs  
**Commit range:** `HEAD~30..HEAD`  
**Generated:** 2026-07-07 13:07 UTC  
**Tool:** `roam pr-replay` — `postmortem` + `critique` engine

> **Real sample — roam-code auditing its own history.** This is an actual
> `roam pr-replay --tier team` report on roam's own last 30 merged PRs — real
> detectors, real SHAs, real findings, not synthetic data. Run
> `roam pr-replay --tier sample` for a free self-serve 5-PR preview on your own
> repo, or email <hello@roam-code.com> to commission a paid Team or Deep engagement.

This report **supports evidence for** structural-review governance and **maps to**
change-management controls. It does not certify compliance with SOC 2, ISO 42001,
the EU AI Act, or any other framework — the conformity assessment remains with the buyer.

---

Thirty most-recent merged PRs on the target branch, scored against the current Roam detector set. Includes founder review of the top findings on a 30-minute call.

## Executive summary

**Verdict:** 12 of 30 PRs (40%) would have surfaced findings — 0 review-eligible (high), 16 review-required (medium).

- PRs replayed: **30**
- PRs Roam would have flagged pre-merge: **12**
- High-severity findings (would block CI): **0**
- Medium-severity findings (would gate review): **16**

## What Roam would have flagged

| Detector | Total findings | PRs with this finding |
|---|---:|---:|
| `intent` | 10 | 10 / 30 |
| `impact` | 6 | 4 / 30 |

The highest-impact class on this window was **`intent`** (10 findings across 10 PRs). Wiring a CI gate against this class is the single highest-leverage move surfacing from this replay.

## Per-PR breakdown

Top 12 PRs ranked by severity (high → medium → total).

| Date | SHA | Subject | High | Medium | Top hits |
|---|---|---|---:|---:|---|
| 2026-07-07 | `3ecbccfa` | feat(service-report): add `roam service-report` client-facin | 0 | 3 | impact x2, intent x1 |
| 2026-05-22 | `83f5c44c` | fix: dogfood-v2 defects - agent-score clamp, audit-trail-ver | 0 | 3 | impact x2, intent x1 |
| 2026-07-07 | `167cb72b` | fix(output): unwrap finding triples so the inner message rea | 0 | 1 | intent x1 |
| 2026-07-07 | `eab88e6d` | perf(graph): output-identical topology + boundary-scan optim | 0 | 1 | intent x1 |
| 2026-07-07 | `2cb23e9c` | fix(compare): migrate to symbol_metrics schema + key symbols | 0 | 1 | intent x1 |
| 2026-05-22 | `fb6f278f` | fix(duplicates): deterministic clone-pattern tie-break | 0 | 1 | intent x1 |
| 2026-05-22 | `04c79ded` | fix(simulate,agents-md): non-saturating health score + canon | 0 | 1 | intent x1 |
| 2026-05-22 | `28fbf6c3` | refactor: loud-fallback sweep of 19 cmd_*.py modules | 0 | 1 | impact x1 |
| 2026-05-22 | `7c47add4` | fix(clones): deterministic tie-break in clone-pattern infere | 0 | 1 | intent x1 |
| 2026-05-21 | `2852c675` | chore(release): v13.4 | 0 | 1 | intent x1 |
| 2026-05-21 | `bf52b940` | perf(smells): shared per-run AST cache deduplicates redundan | 0 | 1 | impact x1 |
| 2026-05-21 | `764d8045` | fix(preflight): name the sibling-failing rules in the Fitnes | 0 | 1 | intent x1 |

## Representative findings

A sample of the individual findings behind the counts above — each row is one detector hit with its location and a one-line rationale. These rows **support evidence for** structural-review triage and **map to** change-management controls; they are observations for a reviewer, not a correctness verdict.

| PR | Location | Detector | Severity | Detail |
|---|---|---|---|---|
| `3ecbccfa` | `src/roam/commands/cmd_service_report.py:196` | `impact` | medium | _verdict has 15 direct callers |
| `3ecbccfa` | `tests/test_service_report.py:256` | `impact` | medium | _invoke has 15 direct callers |
| `83f5c44c` | `src/roam/commands/cmd_audit_trail_verify.py:163` | `impact` | medium | _verify_chain has 12 direct callers |
| `83f5c44c` | `tests/test_audit_trail_verify.py:18` | `impact` | medium | _write_chain has 12 direct callers |
| `28fbf6c3` | `src/roam/commands/cmd_secrets.py:457` | `impact` | medium | scan_file has 17 direct callers |
| `bf52b940` | `src/roam/catalog/smells.py:130` | `impact` | medium | _parse_param_count has 12 direct callers |
| `3ecbccfa` | `diff-wide` | `intent` | low | PR title says 'fix' but the diff is dominated by additions |
| `83f5c44c` | `diff-wide` | `intent` | low | PR title says 'fix' but the diff is dominated by additions |

## Recommended next steps

- **Wire CI gates against the top 2 detector class(es)** — `intent`, `impact`. `roam critique` returns exit code 5 on any high-severity finding, so a single CI step gates every PR. See <https://roam-code.com/docs/>.
- **Run `roam preflight <symbol>` before changing high-blast-radius code.** The blast radius doesn't show up in the diff; it shows up in the graph.
- **Add `roam clones --persist` to your indexing pipeline.** Then `roam critique` picks up clone-not-edited cases on every PR — the single most common AI-shaped bug across replays in similar codebases.
- **Consider the Deep tier** if the patterns above warrant a 90-PR window, per-detector deep-dive, and a 90-minute walk-through with a written remediation plan: <https://roam-code.com/#audit>.

## Apply this fee toward Roam Review

50% of the engagement fee — **$1,250** — banks as a founding-customer credit toward your first year of [Roam Review](https://roam-code.com/pricing). Roam Review is not yet generally available; the credit is held for you and the **60-day** subscription window starts at Review GA, not at report delivery. Roam Review runs the same detectors on every pull request automatically, with a sticky PR comment, BLOCK / REVIEW / APPROVE verdict, and exit-code-5 CI gating. Mention this report when subscribing and we apply the credit to the first invoice.

## What this report does *not* cover

- **Semantic correctness** — whether the code does the right thing. We complement semantic reviewers (CodeRabbit, Greptile, Qodo), we don't replace them.
- **Security audit** of the kind a third-party penetration test would produce. We surface structural risks (clones, blast radius, layer violations) — not exploit paths.
- **Performance profiling**. Some findings touch hot paths (when runtime telemetry is wired), but this isn't a benchmark run.
- **Code review of in-flight PRs.** This report covers *merged* history. For pre-merge gating, install the free CLI plus, when it ships, the Roam Review GitHub App.

## Methodology

Roam replays the current detector set against each commit's outgoing diff as if it were a PR — no historical re-indexing. Findings reflect what Roam catches today on those PRs, not what an earlier Roam version would have. The detector set is stable across Team (30 PRs) and Deep (90 PRs) windows.

_Generated by `roam pr-replay --tier team` on 2026-07-07 13:07 UTC. Engine: `roam postmortem` walks the range; `roam critique` evaluates each diff. Both ship in the open-source CLI ([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))._
