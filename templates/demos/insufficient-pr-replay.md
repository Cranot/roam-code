<!-- W276 INSUFFICIENT-tier demo fixture; companion to canonical-pr-replay.md. Values are illustrative, not from a real repo. -->

# What does an INSUFFICIENT-tier evidence packet look like?

Not every tool that drops a PR-replay-shaped artifact into your repo
actually answers the eight evidence questions. A third-party CI
plugin might emit a JSON file that *looks* like a Roam evidence
packet — it has a `schema_version`, a `commit_sha`, a `diff_hash`,
even a few `changed_subjects` — but skips identity, authority,
context, risk, policy, and verification entirely.

This fixture is what that packet looks like, and what the W259
honest-coverage banner renders for it. The companion JSON lives in
`insufficient-evidence.json`. The canonical 8-of-8 STRONG-tier
companion lives in `canonical-pr-replay.md` /
`canonical-evidence.json`. The two side by side show the gap
between what a real Roam pipeline produces and what a thin
third-party export looks like.

The W259 banner thresholds are:

| Tier | Threshold |
|---|---|
| STRONG | `complete >= 7` |
| PARTIAL | `(complete + partial) >= 5` AND `missing <= 3` |
| INSUFFICIENT | otherwise |

This packet scores `(complete=1, partial=1, missing=6)`. That fails
both STRONG (complete < 7) and PARTIAL (missing > 3), so the banner
falls through to **INSUFFICIENT**. The rationale string the banner
emits warns the reader explicitly: *"do not publish as governance
evidence."*

---

## The rendered ChangeEvidence Markdown report

What follows is what `roam pr-replay` would render if a reviewer
pointed it at this packet. The banner blockquote near the top is
the first signal — it tells the reviewer to stop and audit the
source before treating the report as governance evidence.

---

# PR Replay — a1b2c3d4

> **Evidence coverage: Insufficient evidence**
> 1 of 8 evidence questions answered; do not publish as governance evidence.

**Verdict**: UNKNOWN: third-party export does not record verification state
**Risk level**: unknown
**Mode**: pr_replay
**Range**: `main:9f8e7d6..a1b2c3d`
**Run IDs**: _(no runs recorded)_
**Schema**: 1.0.0

## Scope

- 2 symbols changed across 0 files
- Diff hash: `diff:cafebabe1234567890abcdef1234567890abcdef`

## Changed subjects (top 20)

| Subject | Kind | Blast radius |
|---|---|---|
| `src/example/feature_flag.py` | file | 0 |
| `src/example/cache.py` | file | 0 |

## Actors

_No actors recorded. The change cannot be attributed to a specific human or agent — see Evidence limitations below._

## Authorities

_No authorities recorded. The change is not bound to a mode, permit, approval, policy rule, or token scope — see Evidence limitations below._

## Environment

_No environment recorded. The packet does not name the workspace, branch range, or CI job that produced this change — see Evidence limitations below._

## Findings (0)

| Detector | Confidence | Count |
|---|---|---|
| _(none)_ | — | 0 |

## Tests

- Required: 0
- Run: 0
- Status: no test data attached to this replay

## Approvals and accepted risks

_No approvals or accepted risks recorded for this replay window._

## Suggested Review configuration

_No recurring detector hits in this replay window — no Review configuration to suggest. Run a longer range (`--tier deep` or `--range HEAD~90..HEAD`) for a more representative sample._

## Evidence limitations

- **Missing actor identity**: no `actor_refs`, `agent_id`, or `human_actor` populated on the evidence packet. The change cannot be attributed to a specific human or agent. Recommendation: pass `--agent-id` when running `roam pr-replay` or feed identity via `roam runs start --agent-id`.
- **Missing test evidence**: `tests_required` and `tests_run` are both empty. The packet does not assert which tests should have run for this change. Recommendation: connect test results to the run ledger via `roam runs end --with-tests`.
- **No external artifacts**: the packet does not reference any external artifacts (SARIF reports, CGA attestations, ledger snapshots). The verdict rests on the inline findings table alone. Recommendation: attach proofs via `roam pr-bundle emit` before replay.
- **Acceptance evidence not collected**: this report includes the `producer_not_available` redaction marker — `roam pr-replay` has no approvals / accepted-risks harvester wired in today, so Q8 ("Who accepted residual risk?") cannot be answered from automated sources. The replay window's banner counts Q8 as *partial* (limitation declared) rather than *missing* (silently absent). Recommendation: capture human signoff via `roam pr-bundle emit --approval` or wire a CI step that posts PR-review events into the run ledger before re-running replay.
- **Non-certification**: this report **supports evidence for** governance review and **maps to** change-management controls. It is not certification of compliance with any framework (SOC 2 / ISO 42001 / EU AI Act / etc.). Mapping to specific framework controls and the conformity assessment remain with the customer.

---

*Per the agentic-assurance crosswalk, this report **supports evidence for** governance review and **maps to** change-management controls. It does not certify compliance with SOC 2, ISO 42001, the EU AI Act, or any other framework — the conformity assessment remains with the customer.*

---

## How this packet answers the eight evidence questions

The same eight-question scoreboard the canonical demo passes 8/8 on
shows where the thin third-party packet falls down. Compare row-by-
row against `canonical-pr-replay.md`.

| Q | Question | Field on the packet | This packet says | Score |
|---|---|---|---|---|
| Q1 | Actor — who made the change? | `actor_refs[]` (W182) + legacy `agent_id` / `human_actor` | Empty. No identity surface at all. The change cannot be attributed to a specific human or agent. | **missing** |
| Q2 | Authority — who authorised it? | `authority_refs[]` (W182) + `mode` | Empty. No mode, no permit, no approval, no policy-rule binding. | **missing** |
| Q3 | Context — what did the actor read? | `context_refs[]` | Empty. The packet does not reference any preflight / impact / context envelopes the actor read before editing. | **missing** |
| Q4 | Changes — what was touched? | `changed_subjects[]` | Two `file`-kind subjects (`src/example/feature_flag.py`, `src/example/cache.py`) plus a diff hash. **This is the only fully complete question.** | complete |
| Q5 | Risk — what risk did this introduce? | `risk_level` + `findings[]` | Empty. No `risk_level`, no findings, no detector output. | **missing** |
| Q6 | Policy — which rules fired? | `policy_decisions[]` | Empty. No rule outcomes recorded. | **missing** |
| Q7 | Verify — how was it verified? | `tests_run[]` + `artifacts[]` + `tests_required[]` | Empty. No tests declared, no tests run, no external artifacts attached. | **missing** |
| Q8 | Accept — who signed off on residual risk? | `approvals[]` + `accepted_risks[]` + `redactions[]` | `approvals[]` and `accepted_risks[]` are both empty, but `redactions[]` carries the W261 `producer_not_available` marker — the producer honestly declared "no approvals harvester wired in." That lifts Q8 from *missing* to **partial**. | partial |

The packet's `assurance_floor()` returns `passes=False` with
`missing=("actor", "authority", "findings", "policy_state")`. The
minimum-viable-assurance gate trips on four of its six slots — a
clear "do not publish as governance evidence" signal for any
consumer that runs the gate.

## Why this fixture exists

The W259 honest-coverage banner is a stop sign, not a verdict. The
canonical demo (`canonical-pr-replay.md`) shows what a STRONG-tier
packet looks like when every evidence question is answered. This
fixture shows what an INSUFFICIENT-tier packet looks like when six
of eight questions are skipped — exactly the failure mode the
banner is designed to flag.

For a buyer evaluating Roam: if the reports you currently get from
your CI tooling look more like `insufficient-pr-replay.md` than
`canonical-pr-replay.md`, the banner names the gap. The path to
STRONG-tier coverage is the same one the canonical demo walks
through: wire `roam runs start` with `--agent-id`, declare a mode,
attach a `pr-bundle`, run preflight + impact + critique, and emit a
human approval before merge.

## How the packet was constructed

Reproducible from the JSON companion file:

1. Load `insufficient-evidence.json` with `json.loads`.
2. Reconstruct a `ChangeEvidence` packet (the constructor accepts
   the field shape one-to-one; tuple fields are coerced from JSON
   arrays via `__post_init__`).
3. Call `compute_content_hash()`. The result must match the
   declared `content_hash`
   (`436c8827d1c434d60c68f29139a634bffe13dbcc6ae0d5b5c89ef6600b86e531`)
   byte-for-byte — that is the evidence-compiler guarantee.
4. Call `evidence_completeness()`. The result must be
   `complete=1, partial=1, missing=6`.
5. Call `classify_evidence_coverage(packet)` from
   `roam.evidence.banner`. The result must be
   `("insufficient", "Insufficient evidence", "1 of 8 evidence questions answered; do not publish as governance evidence.")`.

These five invariants are pinned by tests in
`tests/test_demo_fixtures.py`. Any drift fails the suite.

---

*This page (and the rest of `templates/demos/`) is a fixture for
illustrative use. The repo, the file paths, the commit hashes are
synthetic. The data shape is real — it is the same `ChangeEvidence`
schema (v1.0.0) the canonical demo uses, just with most fields
deliberately left empty so the W259 honest-coverage banner has
something to flag.*
