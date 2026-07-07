# PR Replay Report — Showcase — roam-code self-audit

**Tier:** Team — 30 PRs  
**Commit range:** `HEAD~30..HEAD`  
**Generated:** 2026-07-06 20:45 UTC  
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

**Verdict:** 18 of 30 PRs (60%) would have surfaced findings — 0 review-eligible (high), 41 review-required (medium).

- PRs replayed: **30**
- PRs Roam would have flagged pre-merge: **18**
- High-severity findings (would block CI): **0**
- Medium-severity findings (would gate review): **41**

## What Roam would have flagged

| Detector | Total findings | PRs with this finding |
|---|---:|---:|
| `impact` | 27 | 9 / 30 |
| `intent` | 14 | 14 / 30 |

The highest-impact class on this window was **`impact`** (27 findings across 9 PRs). Wiring a CI gate against this class is the single highest-leverage move surfacing from this replay.

## Per-PR breakdown

Top 18 PRs ranked by severity (high → medium → total).

| Date | SHA | Subject | High | Medium | Top hits |
|---|---|---|---:|---:|---|
| 2026-05-21 | `4b8c61c9` | refactor: make fallback chains loud — 4-batch campaign + rat | 0 | 12 | impact x12 |
| 2026-05-21 | `973e09e2` | fix(mcp): W805-OCTET seal, MCP-P1.2 injection scan, loud-fal | 0 | 6 | impact x6 |
| 2026-05-22 | `83f5c44c` | fix: dogfood-v2 defects - agent-score clamp, audit-trail-ver | 0 | 3 | impact x2, intent x1 |
| 2026-05-21 | `65adcd32` | feat(forecast): B8 Option-A — persist per-snapshot spectral  | 0 | 3 | impact x2, intent x1 |
| 2026-05-21 | `07b5fce6` | fix: v13.3.1 doctor/health, cli structured warnings, clones  | 0 | 2 | impact x1, intent x1 |
| 2026-05-21 | `c0ff390a` | perf(retrieve): bulk co-change matrix pre-fetch replaces O(c | 0 | 2 | impact x1, intent x1 |
| 2026-05-21 | `d1e6a5a3` | fix(pattern-2): disclose degraded state in compare/syntax-ch | 0 | 2 | impact x1, intent x1 |
| 2026-05-22 | `fb6f278f` | fix(duplicates): deterministic clone-pattern tie-break | 0 | 1 | intent x1 |
| 2026-05-22 | `04c79ded` | fix(simulate,agents-md): non-saturating health score + canon | 0 | 1 | intent x1 |
| 2026-05-22 | `28fbf6c3` | refactor: loud-fallback sweep of 19 cmd_*.py modules | 0 | 1 | impact x1 |
| 2026-05-22 | `7c47add4` | fix(clones): deterministic tie-break in clone-pattern infere | 0 | 1 | intent x1 |
| 2026-05-21 | `2852c675` | chore(release): v13.4 | 0 | 1 | intent x1 |
| 2026-05-21 | `764d8045` | fix(preflight): name the sibling-failing rules in the Fitnes | 0 | 1 | intent x1 |
| 2026-05-21 | `24cb78f2` | refactor: extend the loud-fallback campaign to mcp_extras/ou | 0 | 1 | impact x1 |
| 2026-05-21 | `0ff5e4a0` | fix(test): guard test_count_drift CLAUDE.md reads for untrac | 0 | 1 | intent x1 |
| 2026-05-21 | `f5e45d8f` | feat(packaging,ci): MCP receipt schema in wheel + supply-cha | 0 | 1 | intent x1 |
| 2026-05-21 | `885eb287` | fix(test): add status to W805-UUUUU _EXPECTED_TRIM_KEYS — c8 | 0 | 1 | intent x1 |
| 2026-05-21 | `34d9881f` | fix(test): scope W805-TTTTT drift-guard to the aggregator if | 0 | 1 | intent x1 |

## Recommended next steps

- **Wire CI gates against the top 2 detector class(es)** — `impact`, `intent`. `roam critique` returns exit code 5 on any high-severity finding, so a single CI step gates every PR. See <https://roam-code.com/docs/>.
- **Run `roam preflight <symbol>` before changing high-blast-radius code.** The blast radius doesn't show up in the diff; it shows up in the graph.
- **Add `roam clones --persist` to your indexing pipeline.** Then `roam critique` picks up clone-not-edited cases on every PR — the single most common AI-shaped bug across replays in similar codebases.
- **Consider the Deep tier** if the patterns above warrant a 90-PR window, per-detector deep-dive, and a 90-minute walk-through with a written remediation plan: <https://roam-code.com/#audit>.

## Apply this fee toward Roam Review

50% of the engagement fee — **$1,250** — credits toward your first year of [Roam Review](https://roam-code.com/pricing) if you subscribe within **60 days** of report delivery. Roam Review runs the same detectors on every pull request automatically, with a sticky PR comment, BLOCK / REVIEW / APPROVE verdict, and exit-code-5 CI gating. Mention this report when subscribing and we apply the credit to the first invoice.

## What this report does *not* cover

- **Semantic correctness** — whether the code does the right thing. We complement semantic reviewers (CodeRabbit, Greptile, Qodo), we don't replace them.
- **Security audit** of the kind a third-party penetration test would produce. We surface structural risks (clones, blast radius, layer violations) — not exploit paths.
- **Performance profiling**. Some findings touch hot paths (when runtime telemetry is wired), but this isn't a benchmark run.
- **Code review of in-flight PRs.** This report covers *merged* history. For pre-merge gating, install the free CLI plus, when it ships, the Roam Review GitHub App.

## Methodology

Roam replays the current detector set against each commit's outgoing diff as if it were a PR — no historical re-indexing. Findings reflect what Roam catches today on those PRs, not what an earlier Roam version would have. The detector set is stable across Team (30 PRs) and Deep (90 PRs) windows.

_Generated by `roam pr-replay --tier team` on 2026-07-06 20:45 UTC. Engine: `roam postmortem` walks the range; `roam critique` evaluates each diff. Both ship in the open-source CLI ([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))._
