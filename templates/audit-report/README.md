# Audit report templates

This directory holds the markdown template + render script for a
codebase-architecture report, plus a sample of the **PR Replay** report
that `roam pr-replay` emits today.

## Files

- `audit-report.md.tmpl` — Markdown skeleton with `{{PLACEHOLDER}}` slots for
  auto-generated content and `<!-- TODO[narrative]: ... -->` slots for the
  auditor's prose. Used by the older codebase-architecture audit deliverable.
- `render.py` — Chains `roam audit --json` plus supporting commands, fills the
  auto-generated slots, and emits a partial markdown file ready for narrative
  completion.
- `sample-redacted.md` / `sample-redacted.pdf` — Redacted, narrative-complete
  example of the codebase-architecture audit (~12 pages).
- `sample-pr-replay-team.md` — **PR Replay sample** (illustrative, not real
  customer data). The shape `roam pr-replay --tier team` produces today.
  Share with prospects who ask "what does the Team-tier deliverable look
  like?". Read this before quoting a paid PR Replay engagement so you and
  the buyer align on the artefact shape.

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

## Sections at a glance

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
