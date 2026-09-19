# Team PR Replay — full report example

**Synthetic engagement. No customer, repository audit or measured outcome is represented here.**
Prepared as a format demonstration on 19 September 2026. Scenario findings below
are invented to explain how a report separates observations, interpretation and
next steps. They are not Roam command output. The separately linked checkout
fixture is real captured output, and is labelled separately.

## 1. Executive summary

**Example question:** Which patterns in a checkout service's recent changes
deserve follow-up before the next pricing change?

**Example conclusion:** Investigate a shared pricing calculation first. Check
whether a similar implementation needs the same change. Do not add a blocking
CI rule until the proposed check has been evaluated against valid changes too.
The demonstration includes one retained investigation lead, one unresolved
similarity lead and one rejected performance suggestion. These are scenario
dispositions, not a measured finding rate or a claim about a real codebase.

No production correctness, performance, security or incident-prevention
conclusion is available from this example. Tests were not executed as part of
this synthetic engagement; missing evidence is not a clean result.

## 2. Agreed scope and delivered object

The current Team offer is **<!-- product-fact:teamReplayPrice -->$2,500<!-- /product-fact --> USD** for an agreed
**<!-- product-fact:teamReplayPrCount -->30<!-- /product-fact -->-PR window**, a Markdown + PDF report and a 30-minute
founder walk-through. This public preview is the Markdown format only, not an
invoice, a signed scope of work or a PDF delivery. See the
[current offer and terms](https://roam-code.com/audit) before commissioning.

The paid object is reviewed interpretation of the agreed history and useful
next steps, not access to otherwise locked analysis. The local tools are free.
Deep adds a broader agreed window, per-detector depth and a written 90-day plan;
those additions are not silently promised by this Team example.

| Scope field | This demonstration | Required in a real report |
|---|---|---|
| Client | None; synthetic engagement | Agreed client identity, with publication consent if shared |
| Repository / branch | No real target | Exact repository and branch |
| PR window | Illustrative Team scope only | Named PR identifiers mapped to commits, agreed before kickoff |
| Base / head / index revision | Not available; no invented SHAs | Exact recorded revisions and dirty/freshness state |
| Engine / configuration | Not run for this scenario | Actual installed version, relevant configuration and check selection |
| Run date / environment | Not applicable to fictional findings | Real run timestamp and environment |
| PRs and commits actually inspected | **0** | Observed denominator, unresolved mappings and omissions |
| Tests / benchmarks executed | **0** | Separate execution evidence, if separately in scope |
| Approval / actor authentication | Not collected | Not inferred from Git author labels or a valid ledger |

The scope owner must agree how squash merges, rebase merges, merge commits and
shallow history map to the requested PRs. A paid PR window is not inferred by
counting commits. The free `HEAD~5..HEAD` sample is a recent commit range, not
five PR identities and not a substitute for that agreement.

## 3. Methodology and collection record

A real engagement records the agreed window, current checkout/index, selected
checks, exact invocations, output files and completion state. Current replay
uses the current detector set and index; it does not reconstruct and test the
application before every historical merge. Reviewer interpretation stays
distinct from the detector's original finding and severity.

For this demonstration, **nothing was collected from a target repository**.
The following is the delivery structure, not a list of artifacts claimed to exist:

- Scope record: PR-to-commit mapping and unresolved entries.
- Run record: engine/configuration, source/index identity and exact commands.
- Raw results: full structured output with errors, truncation and partial state.
- Reviewed findings: source locations, rationale, disposition and next check.
- Coverage appendix: completed, failed, skipped and unavailable checks.
- Final Markdown and matching PDF: revisioned together after the walk-through.

If a raw result is truncated, request the documented full result or retain that
limit. A missing field is UNKNOWN, not zero. Do not fill a missing check with
an empty array merely to make the report look complete.

## 4. Findings and reviewer dispositions — fictional scenarios

### EX-1 — A shared pricing calculation deserves a wider review

**Scenario:** A change to `calculate_total` affects a function used by checkout,
previews and discounts. These fictional change details are not a repository finding.

**Observation needed:** indexed caller locations and the intended diff, tied to
the source/index revision. Caller edges identify places to inspect; they do not
establish that any caller will break or that all runtime callers were found.

**Reviewer disposition:** retain as an investigation lead. Prioritise it because
pricing behavior matters to the stated question, not because a relative graph
score alone establishes severity. No production severity is assigned here.

**Next step / closure evidence:** inspect the callers; add or select tests for
rounding, discounts and previews under the intended requirements; record actual
test execution separately. A passing replay is not those tests running.

### EX-2 — A similar calculation might need the same change

**Scenario:** a similarity check points to a second calculation after a patch.

**Observation needed:** both source spans, their callers and domain requirements.
Similarity alone does not establish duplicated intent. The second calculation
may intentionally implement different rules.

**Reviewer disposition:** unresolved pending domain review. Keep it out of a
confirmed-defect count. Record the question, not a forced “fix both” recommendation.

**Next step / closure evidence:** establish whether the behaviors should agree.
If they should, compare meaningful paired tests before sharing an implementation.
If not, record the reason for retaining separate code and close the lead.

### EX-3 — Reject an unsupported “replace every lookup with a Set” suggestion

**Scenario:** a repeated list lookup prompts an algorithmic alternative.

**Observation needed:** actual value types, membership semantics, ordering,
duplicate behavior, collection updates and workload. A syntactic pattern is not
proof of a slow application or a behavior-preserving replacement.

**Reviewer disposition:** reject the blanket replacement. A Set can change
behavior; even an equivalent replacement needs workload measurement before a
speed claim. This example shows a rejected recommendation, not a measured false
positive rate for Roam.

**Next step / closure evidence:** retain the existing code unless behavioral
controls and a relevant measurement support the alternative. The
[published algorithm example](https://roam-code.com/docs/command-reference#algorithm-choices)
includes a concrete counterexample to an unconditional rewrite.

## 5. Repeated patterns and priorities

The fictional examples share one review question: does the proposed change
preserve the behavior of the consumers that matter? No recurrence frequency,
percentage of affected PRs or savings is claimed. A real report would keep
per-check totals distinct from unique findings and PR populations, and would
retain failed checks alongside completed ones.

| Priority | Recommended action | Evidence needed to close it |
|---|---|---|
| First | Review shared pricing consumers | Source inspection and relevant behavioral test results |
| Next | Resolve the similar-code question | Domain decision plus paired cases, or recorded reason to retain it |
| Conditional | Evaluate an algorithm change | Semantics controls and workload measurement; otherwise no change |
| Before gating | Trial a proposed CI check | Genuine positives, valid negative controls and explicit partial-state refusal |

These are example next steps, not an executed remediation plan, customer
commitment or automatic shipping decision. Assign owners and dates with the
actual team during the walk-through; none are fabricated here.

## 6. Evidence gaps and interpretation limits

| Question | Available in this synthetic engagement |
|---|---|
| What changed? | No real PR mapping, diff or commit inspected |
| What connections were found? | No target graph; see the separate real fixture below |
| What did the checks complete? | No target checks ran; no “all clear” conclusion |
| What did tests establish? | No target tests ran |
| Who acted / what did they read? | Not observed or authenticated |
| What permissions or approvals existed? | Not collected; cannot be reconstructed from this document |
| Did replay prevent an incident? | Not established; historical correlation would not prove prevention |
| Is the code secure or correct? | Not established; scoped static review is not a security audit or certification |

An intact evidence file could help detect changes to that file. It would not
prove complete observation, actor identity, authorization, correctness, risk
acceptance or regulatory conformity. Those require their own evidence and decisions.

## 7. A real output you can inspect separately

The homepage's four-function checkout fixture was run with Roam 14.1.0 on
13 September 2026. Its [full captured JSON](https://roam-code.com/data/examples/checkout-impact-2026-09-13.json)
and [reproduction instructions](https://roam-code.com/measurements#examples) are
public. This is real bounded output, **not output from the fictional engagement**.

That fixture returns three direct callers for `calculate_total` and retains
`cap_applied`, `partial_success` and `truncated`. It establishes the observed
indexed traversal for those files, not general accuracy or runtime safety.
The source fixture and assertions live in
[`tests/test_homepage_contract.py`](https://github.com/Cranot/roam-code/blob/main/tests/test_homepage_contract.py).
Follow its published reproduction path; no fabricated target SHA or run log is supplied here.

## 8. Walk-through, delivery and conditional credit

A useful walk-through starts with the agreed question, examines disputed
findings and gaps, and settles which next checks are worth trying. Corrections
must preserve the original raw finding and explain the reviewer disposition.
Delivery is the report, not a promise that every proposed remediation is implemented.

Under the current Team offer, the conditional future Review credit is
<!-- product-fact:teamCreditPrice -->$1,250<!-- /product-fact -->. It is usable within 60 days after Roam Review
reaches general availability, **if it launches**; there is no promised launch
date. It is not cash or a refund if Review does not launch. This synthetic
document awards no credit. The [offer and credit terms](https://roam-code.com/audit#credit)
and agreed written terms govern an actual engagement.

## 9. Appendix — what to inspect before relying on a real report

1. Confirm that the PR list, commit mapping, engine and index identify the agreed subject.
2. Open raw output for each material finding; inspect scope, limits and completion.
3. Separate reviewer judgments and suggested tests from checks actually executed.
4. Keep unmapped PRs, unreadable sources, skipped checks and unresolved leads visible.
5. Reproduce bounded observations before promoting a recommendation into a blocking gate.

**Bottom line:** this is a complete report-format example, not a completed
30-PR audit. It lets you inspect the object you would commission without
inventing a customer, findings population or successful outcome.
