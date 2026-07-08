# PR Replay Report — Showcase — roam-code self-audit

**Tier:** Team  
**Commit range:** `HEAD~30..HEAD` — 53 merged PRs replayed  
**Generated:** 2026-07-07 22:11 UTC  
**Tool:** `roam pr-replay` — `postmortem` + `critique` engine

> **Real sample — roam-code auditing its own history.** This is an actual
> `roam pr-replay --tier team` report on roam's own recent merged PRs — real
> detectors, real SHAs, real findings, not synthetic data. Run
> `roam pr-replay --tier sample` for a free self-serve preview on your own repo,
> or email <hello@roam-code.com> to commission a paid Team or Deep engagement.

This report **supports evidence for** structural-review governance and **maps to**
change-management controls. It does not certify compliance with SOC 2, ISO 42001,
the EU AI Act, or any other framework — the conformity assessment remains with the buyer.

---


The most-recent merged PRs on the target branch in this commit range (53 replayed), scored against the current Roam detector set. Includes founder review of the top findings on a 30-minute call.

> **Evidence framing.** PR Replay produces a structural-review report that **supports evidence for** governance review and **maps to** change-management controls. It does not certify compliance with SOC 2, ISO 42001, the EU AI Act, or any other framework; the control mapping and conformity assessment stay with the customer.

## Executive summary

**Verdict:** 11 of 53 PRs (20%) would have surfaced findings — 0 review-eligible (high), 76 review-required (medium).

- PRs replayed: **53**
- PRs Roam would have flagged pre-merge: **11**
- High-severity findings (would block CI): **0**
- Medium-severity findings (would gate review): **76**

## Evidence coverage

PR Replay is a merged-history replay, not a continuous approval system. The companion evidence bundle answers all eight evidence questions fully or partially when `--evidence-bundle` is used; the buyer-facing coverage floor is:

| Evidence question | PR Replay coverage |
|---|---|
| Who acted? | Out of scope for attribution; git metadata may appear only as source metadata. |
| What authority existed? | Out of scope except for the replay mode used to produce this report. |
| What context was read? | Partial: commit range, detector version, and local run context. |
| What changed? | In scope: replay window and per-commit changed subjects. |
| What could break? | In scope: detector findings, blast-radius signals, and severity. |
| What policy applied? | In scope: current Roam detector set and any supplied rules. |
| What verified it? | Partial: replay detectors only; no test execution is implied. |
| Who accepted risk? | Out of scope unless GitHub approval data is attached explicitly. |

## What Roam would have flagged

| Detector | Total findings | PRs with this finding |
|---|---:|---:|
| `impact` | 13 | 3 / 53 |
| `intent` | 8 | 8 / 53 |

The highest-impact class on this window was **`impact`** (13 findings across 3 PRs). Wiring a CI gate against this class is the single highest-leverage move surfacing from this replay.

## Per-PR breakdown

Top 11 PRs ranked by severity (high → medium → total).

| Date | SHA | Subject | High | Medium | Top hits |
|---|---|---|---:|---:|---|
| 2026-07-03 | `27efec7d` | chore: automated code-quality and release hygiene batch | 0 | 65 | impact x10 |
| 2026-06-20 | `5652edba` | style: ruff format + autofix repo drift; fix orphaned return | 0 | 2 | impact x2 |
| 2026-07-03 | `b9030bdf` | fix: satisfy pre-push quality gates | 0 | 1 | impact x1 |
| 2026-06-20 | `aa263622` | fix: restore 4 more detectors autopilot falsely removed as g | 0 | 1 | intent x1 |
| 2026-06-20 | `10630b7a` | fix: restore 7 detectors autopilot falsely removed as graph- | 0 | 1 | intent x1 |
| 2026-06-19 | `295178c3` | review-security: Potential unsanitized taint flow: os.enviro | 0 | 1 | intent x1 |
| 2026-06-19 | `635210c4` | backlog: broad `except Exception:` (codebase has 34 specific | 0 | 1 | intent x1 |
| 2026-06-19 | `7123b60b` | review-security: Potential unsanitized taint flow: os.enviro | 0 | 1 | intent x1 |
| 2026-06-19 | `c2df1caf` | backlog: broad `except Exception:` (codebase has 34 specific | 0 | 1 | intent x1 |
| 2026-06-19 | `c7f22971` | backlog: broad `except Exception:` (codebase has 34 specific | 0 | 1 | intent x1 |
| 2026-06-19 | `ca8c0cae` | review-security: Potential unsanitized taint flow: os.enviro | 0 | 1 | intent x1 |

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

_Generated by `roam pr-replay --tier team` on 2026-07-07 22:11 UTC. Engine: `roam postmortem` walks the range; `roam critique` evaluates each diff. Both ship in the open-source CLI ([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))._
