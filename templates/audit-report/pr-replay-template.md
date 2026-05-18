<!--
PR Replay deliverable template.

This file is the **prose-stable reference** for the Markdown report that
`roam pr-replay --markdown` emits per engagement. The renderer at
`src/roam/commands/cmd_pr_replay.py::_render_markdown_report` mirrors
this file heading-by-heading; the drift test at
`tests/test_evidence_pr_replay.py::test_pr_replay_emits_markdown`
asserts the contract. Any heading change here MUST be reflected in both.

Placeholder convention: `{{snake_case}}` slots are populated by the
renderer from a `ChangeEvidence` packet (see
`src/roam/evidence/collector.py::collect_change_evidence`). Slots are
documentation-only — the renderer constructs the output directly.

Cross-links:
- Sample post-fill shape: `templates/audit-report/sample-pr-replay-team.md`
- Reproduction checklist: `templates/audit-report/evidence-checklist.md`
- Control mapping (Section 6 / policy): `templates/audit-report/control-mapping-README.md`
- Engagement contract (delivery shape / acceptance window): `templates/legal/sow-pr-replay.md` §3-§6
- Substrate (collector + dataclasses): `src/roam/evidence/` (`ChangeEvidence`, `EvidenceSubject`, `EvidenceLink`, `EvidenceArtifact`)

The non-certification footer is unconditional. Per CLAUDE.md wording
discipline, the report **supports evidence for** governance review and
**maps to** change-management controls — it does NOT certify compliance
with any framework, and the conformity assessment remains with the
customer.
-->

# PR Replay — {{commit_sha_short}}

<!-- W259 honest-banner slot. Rendered by
     `src/roam/commands/cmd_pr_replay.py::_render_banner_markdown` from
     `roam.evidence.banner` thresholds — first signal a reviewer sees,
     above every populated section, so over-claiming is caught at the
     top of the report. -->
{{evidence_coverage_banner}}

**Verdict**: {{verdict}}
**Risk level**: {{risk_level}}
**Mode**: {{mode}}
**Range**: `{{git_range}}`
**Run IDs**: {{run_ids}}
**Schema**: {{schema_version}}

## Scope

- {{changed_subject_count}} symbols changed across {{changed_file_count}} files
- Diff hash: `{{diff_hash}}`

## Changed subjects (top 20)

| Subject | Kind | Blast radius |
|---|---|---|
{{changed_subjects_table}}

<!--
W191 — Agentic-assurance identity + authority + environment frame.
The next three sections (Actors / Authorities / Environment) answer Q1
(who acted) + Q2 (what authority) + the environmental axis of the
eight-question crosswalk BEFORE findings, so a reviewer reading
top-to-bottom sees WHO acted under WHAT authority in WHICH environment
before seeing WHAT was found. All three sections are unconditional —
an empty packet renders a "no X recorded" sentinel rather than a
missing heading, so a Markdown diff against the template is loud.

Cross-link: evidence-checklist.md → "The eight evidence questions"
table maps each section here to its Q-id and the producing roam
command.
-->

## Actors

{{actors_table_or_none}}

## Authorities

{{authorities_table_or_none}}

## Environment

{{environment_table_or_none}}

## Findings ({{total_findings}})

| Detector | Confidence | Count |
|---|---|---|
{{findings_summary_table}}

## Tests

- Required: {{tests_required_count}}
- Run: {{tests_run_count}}
- Status: {{tests_status}}

## Approvals and accepted risks

{{approvals_section}}

## Suggested Review configuration

<!-- This section corresponds to Section 6 ("Compliance evidence
     mapping") shape in the Governance Pack — Review rules are how a
     replay's findings translate into a continuous policy posture. For
     the canonical control mapping (SOC 2 / ISO 42001 / EU AI Act /
     NIST AI RMF / SLSA), see `templates/audit-report/control-mapping-README.md`
     and the YAML at `src/roam/templates/audit_report/control-mapping.yaml`. -->

Based on this replay's findings, the following Review configuration would have caught {{would_block_count}} of {{total_findings}} findings before merge:

### Recurring risk classes

{{recurring_risk_classes}}

### Suggested .roam/rules.yml

```yaml
{{suggested_rules_yml}}
```

### Suggested CI gates

```bash
{{suggested_ci_gates}}
```

### What Review would have blocked

{{would_block_findings}}

## Evidence limitations

<!-- Limitations are generated from packet structure at render time
     (W284): per-Q gaps from `ChangeEvidence.evidence_completeness()`,
     redaction reasons from `packet.redactions`, and trust-tier
     warnings from `actor_refs`. The non-certification statement is
     always appended.

     Producer gaps (e.g., no approvals harvester yet → Q8 partial) are
     surfaced explicitly via `redactions[].reason = "producer_not_available"`
     rather than synthesised. The current synthetic-vs-shipped status
     is tracked in `CLAUDE.md` under "What's still synthetic / recently
     sealed". To reproduce the packet that generated this section, see
     `templates/audit-report/evidence-checklist.md`. -->
{{evidence_limitations}}

---

*Per the agentic-assurance crosswalk, this report **supports evidence for** governance review and **maps to** change-management controls. It does not certify compliance with SOC 2, ISO 42001, the EU AI Act, or any other framework — the conformity assessment remains with the customer.*
