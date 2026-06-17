# Audit report templates

This directory holds three sample deliverables Roam ships today: a
codebase-architecture audit, a PR Replay engagement report, and the
**Agent Governance Evidence Pack** sample for AI-change audit support.

Every Governance Pack deliverable is a projection from one
`ChangeEvidence` packet (carries `schema_version`, content hash, and
explicit `redactions[]`). See `evidence-checklist.md` for the eight
evidence questions (Q1-Q8) each engagement answers, and
`control-mapping-README.md` for the YAML schema that drives the
"Compliance evidence mapping" section.

## When to use

- **Sales conversations about Governance Pack** — hand prospects
  [`sample-audit-report.md`](sample-audit-report.md) so they see the
  artifact shape before signing.
- **Customer onboarding** — use the sample as the base for the first
  delivered report; the structure is engagement-ready. Pick the export
  profile up front (`internal` / `customer` / `audit` / `public`) — it
  controls what fields surface in the rendered report and what lands in
  `redactions[]`.
- **AI policy / risk-committee briefings** — share the sample as the
  neutral evidence layer the org's AI-change policy maps to (Roam
  *supports evidence for* controls; it does not *certify* or *make
  compliant*).

## Files

- [`sample-audit-report.md`](sample-audit-report.md) — **Agent
  Governance Evidence Pack sample** (illustrative). Mirrors the
  eight-section outline the paid deliverable ships with: which agents
  changed what, context each agent read, risks accepted vs. mitigated,
  who authorized risky edits, which tests closed the loop,
  compliance-control cross-reference, and the disclaimer.
- [`evidence-checklist.md`](evidence-checklist.md) — Per-section command
  index. Maps each section of the Governance sample to the exact `roam`
  commands and envelope fields that produce its evidence, plus the
  Q1-Q8 evidence-question crosswalk (who acted / what authority / what
  context / what changed / what could break / what policy / what
  verified / who accepted risk).
- `audit-report.md.tmpl` — Markdown skeleton for the older codebase-
  architecture audit (pre-evidence-compiler era; kept for backward
  compatibility). `{{PLACEHOLDER}}` slots for auto-content,
  `<!-- TODO[narrative]: ... -->` slots for auditor prose. For new
  engagements use the Governance Pack flow above.
- `render.py` — Chains `roam audit --json` plus supporting commands, fills
  the auto-content slots of `audit-report.md.tmpl`, and emits a partial
  markdown file ready for narrative completion.
- `sample-redacted.md` / `sample-redacted.pdf` — Redacted, narrative-
  complete codebase-architecture audit (~12 pages, older template).
- [`sample-pr-replay-team.md`](sample-pr-replay-team.md) — **PR Replay
  sample** (illustrative). The shape `roam pr-replay --tier team`
  produces today. Read this before quoting a paid PR Replay engagement.
- [`pr-replay-template.md`](pr-replay-template.md) — Markdown skeleton
  the `roam pr-replay --markdown report.md` renderer mirrors. Drift between this
  template and the rendered output is asserted in
  `tests/test_evidence_pr_replay.py` (`test_pr_replay_emits_markdown`).
- [`control-mapping-README.md`](control-mapping-README.md) — v1 schema
  reference for the YAML that drives the "Compliance evidence mapping"
  section. Defines the closed `wording_guard` enumeration enforced by
  `tests/test_doc_consistency.py`.

## PR Replay rehearsal workflow

Before publishing live payment links or accepting the first paid PR
Replay engagement, run one complete dry run on a public repository:

```bash
roam pr-replay --tier team \
    --client "Demo Buyer" \
    --range HEAD~30..HEAD \
    --rehearsal
```

`--rehearsal` writes a private packet under
`internal/engagements/rehearsals/<timestamp>-<client>-<tier>/`:

- `report.md` — buyer-facing Markdown report.
- `evidence-bundle/evidence.json` — canonical `ChangeEvidence` packet.
- `evidence-bundle/report.md` — evidence companion report with the
  coverage banner and limitations section.

Rehearsal mode deliberately skips `.roam/engagements.jsonl` so a dry run
does not look like a paid buyer engagement.

### Markdown and PDF delivery

Markdown is the source of record. PDF is a delivery convenience:

```bash
roam pr-replay --tier team \
    --client "Acme Inc" \
    --output acme-pr-replay.md \
    --pdf acme-pr-replay.pdf \
    --evidence-bundle acme-pr-replay-evidence
```

The command prefers `pandoc` for PDF rendering and falls back to
`reportlab` when installed. If neither backend is present, the command
still writes the Markdown report and evidence bundle; render the PDF on
a machine with one backend installed, then deliver both Markdown and PDF.

Operator pre-flight:

```bash
command -v pandoc || python -c "import reportlab"
roam pr-replay --tier sample \
    --range HEAD~1..HEAD \
    --pdf /tmp/pr-replay-smoke.pdf
file /tmp/pr-replay-smoke.pdf
```

The smoke PDF should identify as a PDF document. A temporary virtualenv can be
used for a rehearsal, but paid delivery should use a stable operator
environment where `pandoc` or `reportlab` is always available to `roam`.

## How to generate a real Governance report

1. Index the target repository at the audit-period HEAD SHA:

   ```bash
   cd /path/to/target-repo && roam init
   ```

2. Verify ledger integrity for the audit period (any failure is a finding):

   ```bash
   for r in $(roam --json runs list --since 2026-04-01 | jq -r '.runs[].run_id'); do
       roam --json runs verify "$r"
   done
   ```

3. Walk the per-section command list in
   [`evidence-checklist.md`](evidence-checklist.md). Each section names
   the exact `roam` commands and envelope fields needed, and the
   evidence question (Q1-Q8) it answers.

4. Render the markdown report (start from
   [`sample-audit-report.md`](sample-audit-report.md) as the structural
   template), fill in the customer name, period, and per-section
   findings.

5. Optionally render to PDF with Pandoc (see the codebase-architecture
   workflow below for an eisvogel-template example).

## Workflow

1. **Index the target repo** in a temporary working directory:

   ```bash
   cd /tmp/audit-target && roam init
   ```

2. **Render the auto-content**:

   ```bash
   python templates/audit-report/render.py \
       --client "Acme Inc" \
       --date 2026-05-05 \
       --repo /tmp/audit-target \
       --output ./acme-audit.md
   ```

3. **Fill the narrative slots** by hand. Open `acme-audit.md` and replace each
   `<!-- TODO[narrative]: ... -->` block with the prose for that section. Roughly
   60-90 minutes of writing for the old Standard audit; update scope, pricing,
   and names before using this for PR Replay.

4. **Render to PDF** with Pandoc (eisvogel template recommended):

   ```bash
   pandoc acme-audit.md \
       -o acme-audit.pdf \
       --template eisvogel \
       --listings \
       --toc
   ```

   Install eisvogel: <https://github.com/Wandmalfarbe/pandoc-latex-template>.

## Sections at a glance (codebase-architecture audit, legacy template)

The table below describes the older codebase-architecture audit driven
by `audit-report.md.tmpl` + `render.py`. For the Governance Pack
deliverable, see the Q1-Q8 crosswalk in
[`evidence-checklist.md`](evidence-checklist.md) instead.

| Section | Auto-filled? | Source |
|---|---|---|
| Executive summary | narrative | auditor |
| Repository overview | auto | `roam describe --agent-prompt` |
| Architecture map | auto | `roam map` |
| Health scorecard | auto | `roam audit` -> health |
| Top risk findings | auto | `roam audit` -> hotspots --danger |
| Dead code | auto | `roam audit` -> dead |
| Ownership and bus-factor | auto | `roam owner` |
| Test coverage gaps | auto | `roam audit` -> test_pyramid |
| Suggested CLAUDE.md / AGENTS.md drop-in | auto | `roam describe --agent-prompt` |
| Suggested CI gates | narrative | auditor |
| 30 / 60 / 90 day fix roadmap | narrative | auditor |
| Methodology | auto | template |

## Notes

- The render script is best-effort: if a `roam` subcommand exits non-zero, the
  affected section is replaced with an inline `_command failed: ..._` note and
  the rest of the report still emits.
- All processing happens locally on the auditor's machine. No client code is
  transmitted to any third-party service. (See SOW Section 2 for the data-handling
  policy that backs that claim.)
- roam-code is licensed under Apache 2.0; you may share a redacted sample of this
  report (with the client's permission) as a case study.

## Disclaimer (Governance Pack)

The Agent Governance Evidence Pack **supports evidence for** controls;
it does not deliver formal certification. The control-mapping table in
[`sample-audit-report.md`](sample-audit-report.md) cross-references
Roam artifacts to commonly-cited controls (SOC 2, ISO/IEC 42001, EU AI
Act, NIST AI RMF) so auditors and risk officers can locate the
relevant Roam evidence for their own framework. Roam does not perform
compliance attestation; whether any particular control applies, and
what additional evidence is required for formal certification, is a
determination for qualified counsel or auditors. The wording guardrail
that enforces this distinction inside generated reports is documented
in [`control-mapping-README.md`](control-mapping-README.md).
