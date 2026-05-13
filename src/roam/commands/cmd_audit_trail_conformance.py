"""``roam audit-trail-conformance-check`` — score against EU AI Act Article 12.

The EU AI Act (Regulation 2024/1689), in force since 2 August 2026 for
high-risk systems, requires automatic logging of events relevant to
identification of risks. Article 12 specifies (paraphrasing): logs must
capture period of use, reference database, input data leading to a
match, identification of the natural persons involved, and be retained
for the lifetime of the system or a minimum of six months.

This command scores a roam audit trail against an Article 12-shaped
checklist. **It is not legal advice** — compliance verdicts depend on
the deployer's full system context. The score is a triage signal:
"would this trail survive a procurement review?".

Six checks (each contributes equally to the score):

1. **Chain integrity** — SHA-256 hash chain unbroken (delegates to
   :mod:`roam.commands.cmd_audit_trail_verify`).
2. **Timestamp completeness** — every record has an ISO-8601 timestamp.
3. **Actor attribution** — every record has a non-empty ``actor`` field.
4. **Reproducibility metadata** — diff hash + git SHA + tool version.
5. **Verdict + rationale present** — verdict field set, rationale_summary
   non-empty.
6. **Retention** — at least one record older than the ``--retention-days``
   threshold (default 180 = six months), so we can show "we have N+
   months of history" to a regulator.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import DEFAULT_AUDIT_TRAIL_PATH
from roam.commands.audit_trail_helpers import load_records as _load_records
from roam.output.formatter import json_envelope, to_json

EXIT_GATE_FAILURE = 5
DEFAULT_RETENTION_DAYS = 180  # ~6 months, the Article 12 minimum

# Required fields for a record to be Article 12-shaped.
REQUIRED_RECORD_FIELDS = ("timestamp", "actor", "verdict", "diff_sha256", "git_sha", "tool_version")


def _parse_iso(ts: str) -> _dt.datetime | None:
    """Parse an ISO-8601 timestamp; return ``None`` if unparseable."""
    if not ts:
        return None
    try:
        # Tolerate both ``Z`` and ``+00:00`` suffixes.
        cleaned = ts.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(cleaned)
    except (TypeError, ValueError):
        return None


def _check_chain_integrity(path: Path) -> tuple[bool, str]:
    from roam.commands.cmd_audit_trail_verify import _verify_chain

    records, issues = _verify_chain(path)
    if not records:
        return False, "no records in audit trail"
    real_issues = [i for i in issues if "not found" not in i.get("issue", "")]
    if real_issues:
        return False, f"chain has {len(real_issues)} integrity issue(s)"
    return True, f"chain integrity verified across {len(records)} record(s)"


def _check_timestamps(records: list[dict]) -> tuple[bool, str]:
    missing = [i for i, r in enumerate(records) if not _parse_iso(r.get("timestamp", ""))]
    if missing:
        return False, f"{len(missing)} record(s) lack a parseable timestamp"
    return True, f"all {len(records)} record(s) have parseable timestamps"


def _check_actors(records: list[dict]) -> tuple[bool, str]:
    missing = [i for i, r in enumerate(records) if not (r.get("actor") and r["actor"] != "<unknown>")]
    if missing:
        return False, f"{len(missing)} record(s) lack an actor (or actor=<unknown>)"
    return True, f"all {len(records)} record(s) have actor attribution"


def _check_reproducibility(records: list[dict]) -> tuple[bool, str]:
    """Each record must have diff hash + git SHA + tool version for replay."""
    missing_count = 0
    for r in records:
        if not (r.get("diff_sha256") and r.get("git_sha") and r.get("tool_version")):
            missing_count += 1
    if missing_count:
        return False, f"{missing_count} record(s) lack full reproducibility metadata"
    return True, f"all {len(records)} record(s) have diff_sha256 + git_sha + tool_version"


def _check_verdicts_and_rationale(records: list[dict]) -> tuple[bool, str]:
    missing_verdict = sum(1 for r in records if not r.get("verdict"))
    missing_rationale = sum(1 for r in records if not r.get("rationale_summary"))
    if missing_verdict:
        return False, f"{missing_verdict} record(s) missing verdict"
    if missing_rationale:
        return False, f"{missing_rationale} record(s) missing rationale_summary"
    return True, f"all {len(records)} record(s) have verdict + rationale"


def _check_retention(records: list[dict], retention_days: int) -> tuple[bool, str]:
    """At least one record must be older than the retention threshold."""
    if not records:
        return False, "no records to check retention against"
    now = _dt.datetime.now(_dt.timezone.utc)
    threshold = now - _dt.timedelta(days=retention_days)
    parsed = [_parse_iso(r.get("timestamp", "")) for r in records]
    valid = [p for p in parsed if p is not None]
    if not valid:
        return False, "no parseable timestamps to compute retention from"
    oldest = min(valid)
    age_days = (now - oldest).days
    if oldest <= threshold:
        return True, f"oldest record is {age_days} day(s) old (≥ {retention_days} day requirement)"
    return False, (
        f"oldest record is only {age_days} day(s) old; minimum retention is {retention_days} days. "
        "Either keep records longer or document a shorter retention policy."
    )


def _checks_to_sarif(
    checks: list[dict],
    audit_trail_path: Path,
    score: int,
) -> dict:
    """Render the 6-check verdict as a SARIF 2.1.0 envelope.

    Each FAIL becomes a SARIF result; each rule (one per check id) is
    declared in the run's tool.driver.rules. GitHub Code Scanning ingests
    this directly, surfacing the failures as triage items in the Security
    tab — useful for quarterly compliance gates.
    """
    from roam.output.sarif import to_sarif

    rules = [
        {
            "id": c["id"],
            "shortDescription": f"EU AI Act Article 12 conformance check: {c['id'].replace('_', ' ')}",
            "defaultLevel": "warning",
            "helpUri": "https://artificialintelligenceact.eu/article/12/",
            "properties": {"category": "compliance", "regulation": "EU AI Act Article 12"},
        }
        for c in checks
    ]
    results = []
    for c in checks:
        if c["passed"]:
            continue
        results.append(
            {
                "ruleId": c["id"],
                "level": "error" if score < 67 else "warning",
                "message": {"text": c["message"]},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": str(audit_trail_path)},
                        }
                    }
                ],
            }
        )
    return to_sarif(
        tool_name="roam-code",
        version="audit-trail-conformance-check",
        rules=rules,
        results=results,
    )


@roam_capability(
    name="audit-trail-conformance-check",
    category="workflow",
    summary="Score the audit trail against an EU AI Act Article 12 checklist",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "compliance"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=False,
    requires_index=True,
)
@click.command(name="audit-trail-conformance-check")
@click.option(
    "--input",
    "input_path",
    type=click.Path(),
    default=None,
    help=f"Audit trail JSONL path (default: {DEFAULT_AUDIT_TRAIL_PATH}).",
)
@click.option(
    "--retention-days",
    type=int,
    default=DEFAULT_RETENTION_DAYS,
    show_default=True,
    help="Minimum retention requirement in days (Article 12 floor: 180).",
)
@click.option(
    "--gate",
    is_flag=True,
    help="Exit 5 (gate failure) when conformance score < 100; useful for quarterly compliance gates.",
)
@click.option(
    "--sarif-output",
    type=click.Path(),
    default=None,
    help="Write SARIF to this file (default: stdout when global --sarif is set). Requires --sarif.",
)
@click.pass_context
def audit_trail_conformance_check(
    ctx,
    input_path: str | None,
    retention_days: int,
    gate: bool,
    sarif_output: str | None,
) -> None:
    """Score the audit trail against an EU AI Act Article 12 checklist.

    \b
    Examples:
      roam audit-trail-conformance-check
      roam audit-trail-conformance-check --retention-days 90
      roam audit-trail-conformance-check --gate           # CI gate
      roam --json audit-trail-conformance-check           # for procurement consumption

    Six checks: chain integrity, timestamp completeness, actor attribution,
    reproducibility metadata, verdict+rationale, retention. Each pass adds
    1/6 to the score; a perfect 100 indicates the trail is procurement-ready.

    NOT legal advice — compliance depends on full system context. This is a
    triage signal: "would this trail survive a procurement review?".
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif = ctx.obj.get("sarif") if ctx.obj else False

    path = Path(input_path) if input_path else DEFAULT_AUDIT_TRAIL_PATH
    records = _load_records(path)

    # Fix E (Pattern 2: silent fallbacks) — when no audit trail exists yet,
    # do NOT compute a 0/6 score and report NON-conformant. That misleads
    # consumers into thinking the trail was scanned and failed. Instead emit
    # an explicit "no audit trail to check" state so callers know the
    # underlying check did not run. Adopts the two-state framing from
    # article-12-check (directory exists vs trail populated).
    trail_absent = not path.exists() or not records
    if trail_absent:
        no_trail_reason = (
            f"audit trail file does not exist at {path}"
            if not path.exists()
            else f"audit trail at {path} contains zero records"
        )
        no_trail_summary = {
            "verdict": "no audit trail to check",
            "state": "no_trail",
            "partial_success": True,
            "score": None,
            "chain_compliance_score": None,
            "compliance_kind": "audit_trail_chain_integrity",
            "compliance_kind_definition": (
                "Chain-of-custody score for an existing roam audit trail: "
                "6 per-record integrity checks. NOT the same as "
                "article-12-check (repo-level readiness)."
            ),
            "checks_passed": 0,
            "checks_total": 6,  # 6 Article 12 checks, none ran in this state
            "total_records": 0,
            "audit_trail_path": str(path),
            "retention_days_required": retention_days,
            "schema_reference": "EU AI Act Regulation 2024/1689, Article 12",
            "disclaimer": "Triage signal only — not legal advice.",
            "reason": no_trail_reason,
            "fix": "Run `roam audit-trail-export` to bootstrap a trail, or use `roam pr-analyze --audit-trail` to emit the first record.",
        }
        # Build the same 6 checks but uniformly marked "not_run" so consumers
        # that iterate checks[] see the state explicitly rather than getting
        # silent FAIL=0 fields.
        no_trail_checks = [
            {"id": cid, "passed": False, "state": "not_run", "message": "no audit trail to check"}
            for cid in (
                "chain_integrity",
                "timestamp_completeness",
                "actor_attribution",
                "reproducibility_metadata",
                "verdict_and_rationale",
                "retention",
            )
        ]

        if sarif:
            from roam.output.sarif import write_sarif

            sarif_doc = _checks_to_sarif(no_trail_checks, path, 0)
            sarif_text = write_sarif(sarif_doc, sarif_output)
            if not sarif_output:
                click.echo(sarif_text)
            elif not json_mode:
                click.echo(f"VERDICT: {no_trail_summary['verdict']} — SARIF written to {sarif_output}")
        elif json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "audit-trail-conformance-check",
                        summary=no_trail_summary,
                        checks=no_trail_checks,
                        disclaimer="Triage signal only — not legal advice. Compliance depends on full system context.",
                        schema_reference="EU AI Act Regulation 2024/1689, Article 12",
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {no_trail_summary['verdict']}")
            click.echo(f"  path:    {path}")
            click.echo(f"  records: 0")
            click.echo(f"  state:   no_trail ({no_trail_reason})")
            click.echo(f"  fix:     {no_trail_summary['fix']}")
            click.echo()
            click.echo("Reference: EU AI Act Regulation 2024/1689, Article 12.")
            click.echo("Disclaimer: triage signal only — not legal advice.")

        if gate:
            sys.exit(EXIT_GATE_FAILURE)
        return

    chain_ok, chain_msg = _check_chain_integrity(path)
    ts_ok, ts_msg = _check_timestamps(records) if records else (False, "no records loaded")
    actor_ok, actor_msg = _check_actors(records) if records else (False, "no records loaded")
    repro_ok, repro_msg = _check_reproducibility(records) if records else (False, "no records loaded")
    verdict_ok, verdict_msg = _check_verdicts_and_rationale(records) if records else (False, "no records loaded")
    retention_ok, retention_msg = _check_retention(records, retention_days) if records else (False, "no records loaded")

    checks = [
        {"id": "chain_integrity", "passed": chain_ok, "message": chain_msg},
        {"id": "timestamp_completeness", "passed": ts_ok, "message": ts_msg},
        {"id": "actor_attribution", "passed": actor_ok, "message": actor_msg},
        {"id": "reproducibility_metadata", "passed": repro_ok, "message": repro_msg},
        {"id": "verdict_and_rationale", "passed": verdict_ok, "message": verdict_msg},
        {"id": "retention", "passed": retention_ok, "message": retention_msg},
    ]

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    score = round(100 * passed / total)

    if score == 100:
        verdict = f"conformant ({passed}/{total} checks)"
    elif score >= 67:
        verdict = f"partial conformance ({passed}/{total} checks, score {score}/100)"
    else:
        verdict = f"NON-conformant ({passed}/{total} checks, score {score}/100)"

    # W17.2 / Pattern 3c: the audit-trail conformance score and the
    # article-12-check readiness score are GENUINELY different metrics
    # (one is ledger-integrity over recorded events; the other is
    # repo-level readiness artifacts). Both publish a
    # `compliance_kind` + `compliance_kind_definition` so consumers
    # never confuse them. The legacy `score` field stays for
    # back-compat; new code should read `chain_compliance_score`.
    summary = {
        "verdict": verdict,
        "score": score,
        "chain_compliance_score": score,
        "compliance_kind": "audit_trail_chain_integrity",
        "compliance_kind_definition": (
            "Chain-of-custody score for an existing roam audit trail: "
            "6 per-record integrity checks (chain hash, timestamps, "
            "actor attribution, reproducibility metadata, verdict + "
            "rationale, retention). NOT the same as article-12-check, "
            "which measures repo-level readiness artifacts. "
            "Reference: EU AI Act Article 12 (event logging)."
        ),
        "checks_passed": passed,
        "checks_total": total,
        "total_records": len(records),
        "audit_trail_path": str(path),
        "retention_days_required": retention_days,
        "schema_reference": "EU AI Act Regulation 2024/1689, Article 12",
        "disclaimer": "Triage signal only — not legal advice.",
    }

    if sarif:
        from roam.output.sarif import write_sarif

        sarif_doc = _checks_to_sarif(checks, path, score)
        sarif_text = write_sarif(sarif_doc, sarif_output)
        if not sarif_output:
            click.echo(sarif_text)
        elif not json_mode:
            click.echo(f"VERDICT: {verdict} — SARIF written to {sarif_output}")
    elif json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "audit-trail-conformance-check",
                    summary=summary,
                    checks=checks,
                    # Top-level disclaimer so procurement consumers can't miss it.
                    disclaimer="Triage signal only — not legal advice. Compliance depends on full system context.",
                    schema_reference="EU AI Act Regulation 2024/1689, Article 12",
                )
            )
        )
    elif not sarif:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  path:    {path}")
        click.echo(f"  records: {len(records)}")
        click.echo(f"  score:   {score}/100  ({passed}/{total} checks passed)")
        click.echo()
        click.echo("Conformance checks:")
        for c in checks:
            status = "PASS" if c["passed"] else "FAIL"
            click.echo(f"  [{status}] {c['id']}: {c['message']}")
        click.echo()
        click.echo("Reference: EU AI Act Regulation 2024/1689, Article 12.")
        click.echo("Disclaimer: triage signal only — not legal advice.")

    if gate and score < 100:
        sys.exit(EXIT_GATE_FAILURE)
