"""``roam article-12-check`` — EU AI Act Article 12 readiness assessment.

EU AI Act Article 12 takes effect August 2, 2026, and requires high-risk
AI systems to "technically allow for the automatic recording of events
(logs) over the lifetime of the system." Penalty for non-compliance: up
to €15M or 3% of global turnover.

Most coding tools are NOT classified as high-risk under Annex III, BUT
they BECOME high-risk if their telemetry feeds into HR / promotion /
retention decisions. The non-applicability assessment itself is a
deliverable that DPOs and compliance officers need to file.

This command runs a checklist over the indexed repo and emits a
1-page assessment report (Markdown by default; ``--pdf`` if reportlab
is installed). Each item references the specific Article (12, 18, 19)
or Annex (III) it maps to.

A scoping/readiness helper for buyers whose product may fall under
Article 12 (Annex III high-risk providers). Code-generation tooling
itself is not in Annex III — Article 12 only applies to Roam's outputs
when the buyer's product is in scope.

Usage:

    roam article-12-check                          # markdown to stdout
    roam article-12-check --output report.md       # write markdown
    roam article-12-check --pdf out.pdf            # write PDF (needs reportlab)
    roam --json article-12-check                   # structured envelope

The assessment is deliberately conservative: items that don't apply
to a typical codebase get a "N/A — not classified as high-risk" tag
instead of a "FAIL" so the readiness report doesn't false-alarm
compliance teams.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because article-12-check outputs are compliance assessment
reports — not per-location violations. article-12-check's primary
deliverable is the 1-page Markdown / PDF readiness report. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.commands.git_helpers import git_origin_url
from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json
from roam.output.metric_definitions import ARTICLE_12_READINESS_DEFINITION

# ---------------------------------------------------------------------------
# Checklist items — each mapped to an Article / Annex
# ---------------------------------------------------------------------------


def _check_logs_directory_exists(project_root: Path) -> dict:
    """Item 1 — Article 12: events must be recorded automatically."""
    candidates = [".roam", ".roam/audit-trail", "logs", ".github/audit"]
    found = [c for c in candidates if (project_root / c).exists()]
    return {
        "item": "Audit trail directory exists",
        "article": "12 (event logging)",
        "passed": bool(found),
        "evidence": f"found: {', '.join(found)}" if found else "no .roam/ or logs/ directory found",
        "fix": "Run `roam audit-trail-export` to bootstrap an audit trail under .roam/audit-trail.jsonl",
    }


def _check_audit_trail_has_records(project_root: Path) -> dict:
    """Item 2 — Article 12: trail must accumulate over the lifetime of the system."""
    trail_path = project_root / ".roam" / "audit-trail.jsonl"
    record_count = 0
    if trail_path.exists():
        try:
            record_count = sum(1 for line in trail_path.open(encoding="utf-8") if line.strip())
        except OSError:
            record_count = 0
    return {
        "item": "Audit trail has at least 1 record",
        "article": "12 (event logging)",
        "passed": record_count > 0,
        "evidence": f"{record_count} record(s) in {trail_path}"
        if record_count
        else "audit-trail.jsonl missing or empty",
        "fix": "Pipe `roam pr-analyze --emit-audit-record` (default) on every PR; first record bootstraps the chain",
    }


def _check_retention_documented(project_root: Path) -> dict:
    """Item 3 — Article 19: minimum 6-month log retention by deployers."""
    candidates = [
        "docs/retention.md",
        "docs/audit-retention.md",
        "RETENTION.md",
        ".roam/retention.yaml",
        "AGENTS.md",
        "CLAUDE.md",
    ]
    keywords = ("retention", "6 month", "180 day", "Article 19")
    matches = []
    for c in candidates:
        p = project_root / c
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if any(k in text for k in keywords):
            matches.append(c)
    return {
        "item": "Log-retention policy documented (≥6 months per Article 19)",
        "article": "19 (deployer obligations)",
        "passed": bool(matches),
        "evidence": f"keyword found in: {', '.join(matches)}"
        if matches
        else "no retention keyword in standard locations",
        "fix": "Add a `## Retention` section to AGENTS.md or docs/retention.md stating 6-month minimum",
    }


def _check_technical_documentation(project_root: Path) -> dict:
    """Item 4 — Article 18: technical documentation retained 10 years."""
    candidates = ["README.md", "ARCHITECTURE.md", "docs/", "AGENTS.md", "CLAUDE.md"]
    found = [c for c in candidates if (project_root / c).exists()]
    return {
        "item": "Technical documentation present (Article 18: 10-year retention)",
        "article": "18 (provider obligations)",
        "passed": len(found) >= 2,
        "evidence": f"found: {', '.join(found)}",
        "fix": "Add ARCHITECTURE.md or expand AGENTS.md with system overview, data flows, and contacts",
    }


def _check_attestation_artifacts(project_root: Path) -> dict:
    """Item 5 — bonus: in-toto / sigstore attestations for AI-generated changes."""
    candidates = [".roam/attestations", ".roam/cga", "attestations/"]
    found = [c for c in candidates if (project_root / c).exists()]
    return {
        "item": "Cryptographic attestation surface exists (in-toto / sigstore)",
        "article": "12 (event integrity)",
        "passed": bool(found),
        "evidence": f"found: {', '.join(found)}" if found else "no attestation artifacts",
        "fix": "Run `roam attest cga --sign` to bootstrap signed CodeGraph attestations",
    }


def _classify_high_risk_likelihood(project_root: Path) -> dict:
    """Item 6 — heuristic: does the codebase look like an AI-tool that influences HR decisions?"""
    risk_keywords = re.compile(
        r"\b(?:promotion|retention|hr_decision|performance_review|workforce|hiring|firing|"
        r"employee_score|developer_rating|promote|demote|terminate)\b",
        re.IGNORECASE,
    )
    hits = 0
    sample = 0
    for path in project_root.rglob("*.py"):
        if "test" in str(path).lower():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sample += 1
        if risk_keywords.search(text):
            hits += 1
        if sample > 200:
            break
    is_high_risk = hits > 0
    return {
        "item": "High-risk classification likelihood (Annex III)",
        "article": "Annex III (high-risk system definitions)",
        "passed": not is_high_risk,
        "evidence": (
            f"NOT high-risk — no HR/employment keywords matched across {sample} files scanned"
            if not is_high_risk
            else f"REVIEW — {hits} file(s) reference HR/employment workflows; consult counsel"
        ),
        "fix": (
            "Document the non-applicability in compliance/eu-ai-act-assessment.md"
            if not is_high_risk
            else "Engage DPO immediately; this codebase MAY be high-risk under Annex III"
        ),
    }


_CHECKS = [
    _check_logs_directory_exists,
    _check_audit_trail_has_records,
    _check_retention_documented,
    _check_technical_documentation,
    _check_attestation_artifacts,
    _classify_high_risk_likelihood,
]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _render_markdown_report(results: list[dict], project_root: Path) -> str:
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    repo = git_origin_url() or str(project_root)
    lines: list[str] = [
        "# EU AI Act — Article 12 Readiness Assessment",
        "",
        f"**Generated**: {timestamp}",
        f"**Repository**: {repo}",
        f"**Score**: {passed} / {total} items passing",
        "",
        "## Background",
        "",
        "EU AI Act Article 12 takes effect **August 2, 2026** for high-risk AI",
        "systems (Annex III). It requires automatic event logging built into",
        "the system. Penalties for non-compliance: **€15M or 3% of global",
        "annual turnover**, whichever is higher. Article 19 requires deployers",
        "to retain logs for at least 6 months. Article 18 requires providers",
        "to retain technical documentation for 10 years.",
        "",
        "Most coding tools are NOT classified as high-risk under Annex III.",
        "However, they BECOME high-risk if telemetry feeds into HR /",
        "promotion / retention decisions. **Even the non-applicability",
        "assessment is a deliverable** that DPOs need on file.",
        "",
        "## Checklist",
        "",
    ]
    for r in results:
        mark = "✅ PASS" if r["passed"] else "⚠️  REVIEW"
        lines.append(f"### {mark} — {r['item']}")
        lines.append(f"_Article {r['article']}_")
        lines.append("")
        lines.append(f"**Evidence**: {r['evidence']}")
        lines.append("")
        if not r["passed"]:
            lines.append(f"**Fix**: {r['fix']}")
            lines.append("")
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## Next steps",
            "",
            "1. Address any ⚠️ REVIEW items above.",
            "2. If you suspect this codebase IS high-risk under Annex III,",
            "   consult counsel and your DPO immediately.",
            "3. Run `roam audit-trail-export --since 2025-01-01` to capture",
            "   historical PR-analyze records into the chain.",
            "4. Run `roam audit-trail-conformance-check` for a deeper",
            "   per-record audit.",
            "",
            "## Disclaimer",
            "",
            "This report is a tool-level readiness assessment generated by",
            "`roam article-12-check`. It is NOT legal advice and does NOT",
            "constitute a conformity assessment under the EU AI Act. For a",
            "binding determination, consult qualified counsel and a notified",
            "body where required.",
            "",
            "_Powered by [roam-code](https://github.com/Cranot/roam-code) — Apache 2.0_",
            "",
        ]
    )
    return "\n".join(lines)


def _render_pdf_report(markdown_text: str, output_path: Path) -> bool:
    """Best-effort PDF emission via reportlab (optional)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return False
    doc = SimpleDocTemplate(str(output_path), pagesize=A4)
    styles = getSampleStyleSheet()
    flowables = []
    for line in markdown_text.split("\n"):
        if not line.strip():
            flowables.append(Spacer(1, 6))
            continue
        if line.startswith("# "):
            flowables.append(Paragraph(line[2:], styles["Heading1"]))
        elif line.startswith("## "):
            flowables.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("### "):
            flowables.append(Paragraph(line[4:], styles["Heading3"]))
        else:
            flowables.append(Paragraph(line, styles["BodyText"]))
    doc.build(flowables)
    return True


from roam.capability import roam_capability


@roam_capability(
    category="compliance",
    summary="EU AI Act Article 12 readiness assessment — 6-item checklist over the current repo.",
    inputs=["repo_path"],
    outputs=["readiness_score", "checklist_results", "markdown_report"],
    examples=[
        "roam article-12-check",
        "roam article-12-check --pdf report.pdf",
    ],
    tags=["compliance", "eu-ai-act", "audit", "phase0"],
    ai_safe=True,
    requires_index=False,
    since="12.40",
)
@click.command(name="article-12-check")
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default=None,
    help="Write the markdown report to PATH (default: stdout).",
)
@click.option(
    "--pdf",
    "pdf_path",
    type=click.Path(),
    default=None,
    help="Also write a PDF to PATH (requires reportlab; pip install reportlab).",
)
@click.pass_context
def article_12_check_cmd(ctx, output_path: str | None, pdf_path: str | None):
    """EU AI Act Article 12 readiness assessment for the indexed repo.

    Runs a 6-item checklist (audit trail, retention policy, technical
    docs, attestation surface, high-risk classification heuristic) and
    emits a 1-page markdown report. Optional PDF via reportlab.

    \b
    Examples:
      roam article-12-check                       # markdown to stdout
      roam article-12-check --output report.md
      roam article-12-check --pdf assessment.pdf
      roam --json article-12-check > envelope.json

    A scoping/readiness helper. Article 12 only applies when the
    buyer's product is in EU AI Act Annex III; this command checks
    whether the artifacts (audit-trail dir, retention, etc.) are in
    place if it does.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    project_root = Path.cwd()
    results = [check(project_root) for check in _CHECKS]
    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    markdown_text = _render_markdown_report(results, project_root)

    if output_path:
        Path(output_path).write_text(markdown_text, encoding="utf-8")

    pdf_written = False
    if pdf_path:
        pdf_written = _render_pdf_report(markdown_text, Path(pdf_path))

    if json_mode:
        # W17.2 / Pattern 3c: name the compliance kind so consumers
        # never confuse article-12-check (repo-level readiness) with
        # audit-trail-conformance-check (chain integrity over recorded
        # events). Both publish `compliance_kind` +
        # `compliance_kind_definition` for unambiguous downstream use.
        gov_score = round(100 * passed / total) if total else 0
        click.echo(
            to_json(
                json_envelope(
                    "article-12-check",
                    summary={
                        "verdict": f"{passed}/{total} items passing",
                        "passed": passed,
                        "total": total,
                        "governance_compliance_score": gov_score,
                        # W331b (Pattern 3a): pair the readiness score with
                        # an explicit definition of what the 6 checks map
                        # to. Wording follows the agentic-assurance
                        # guardrails: "maps to" / "supports evidence
                        # for", never "certifies" / "makes compliant".
                        "governance_compliance_score_definition": ARTICLE_12_READINESS_DEFINITION,
                        "compliance_kind": "eu_ai_act_governance_readiness",
                        "compliance_kind_definition": (
                            "Repo-level readiness for EU AI Act Article 12: 6 "
                            "artifact-existence checks (audit-trail directory, "
                            "audit-trail records, retention policy doc, technical "
                            "docs, attestation surface, high-risk classification). "
                            "NOT the same as audit-trail-conformance-check, which "
                            "scores per-record chain integrity. Reference: "
                            "EU AI Act Article 12 (event logging)."
                        ),
                        "high_risk_classification": next(
                            (r["evidence"] for r in results if "high-risk" in r["item"].lower()),
                            "unknown",
                        ),
                        "output_path": output_path,
                        "pdf_path": pdf_path if pdf_written else None,
                        "pdf_skipped_reason": ("reportlab not installed" if (pdf_path and not pdf_written) else None),
                    },
                    items=results,
                )
            )
        )
        ctx.exit(EXIT_SUCCESS)
        return

    if output_path:
        click.echo(f"VERDICT: {passed}/{total} items passing — written to {output_path}")
        if pdf_path and pdf_written:
            click.echo(f"  PDF: {pdf_path}")
        elif pdf_path and not pdf_written:
            click.echo("  PDF skipped: reportlab not installed (pip install reportlab)", err=True)
    else:
        click.echo(markdown_text)

    ctx.exit(EXIT_SUCCESS)
