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
@click.pass_context
def audit_trail_conformance_check(
    ctx,
    input_path: str | None,
    retention_days: int,
    gate: bool,
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

    path = Path(input_path) if input_path else DEFAULT_AUDIT_TRAIL_PATH
    records = _load_records(path)

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

    summary = {
        "verdict": verdict,
        "score": score,
        "checks_passed": passed,
        "checks_total": total,
        "total_records": len(records),
        "audit_trail_path": str(path),
        "retention_days_required": retention_days,
        "schema_reference": "EU AI Act Regulation 2024/1689, Article 12",
        "disclaimer": "Triage signal only — not legal advice.",
    }

    if json_mode:
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
    else:
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
