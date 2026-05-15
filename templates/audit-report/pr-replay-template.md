# PR Replay — {{commit_sha_short}}

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
     (W284): per-Q gaps from evidence_completeness(), redaction reasons
     from packet.redactions, and trust-tier warnings from actor_refs.
     The non-certification statement is always appended. -->
{{evidence_limitations}}

---

*Per the agentic-assurance crosswalk, this report **supports evidence for** governance review and **maps to** change-management controls. It does not certify compliance with SOC 2, ISO 42001, the EU AI Act, or any other framework — the conformity assessment remains with the customer.*
