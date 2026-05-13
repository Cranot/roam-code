"""``roam audit-trail-verify`` — verify SHA-256 chain integrity of an audit trail.

Walks an EU AI Act Article 12-shaped audit-trail JSONL produced by
``roam pr-analyze --audit-trail`` and confirms the SHA-256 hash chain is
unbroken from the first record (genesis, ``previous_record_hash = ""``)
to the last. Returns exit code 5 (gate failure) when the chain breaks,
so it can be wired into CI as a tamper-detection gate.
"""

from __future__ import annotations

import hashlib
import json as _json
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import DEFAULT_AUDIT_TRAIL_PATH
from roam.output.formatter import json_envelope, to_json

EXIT_GATE_FAILURE = 5


def _verify_chain(path: Path) -> tuple[list[dict], list[dict]]:
    """Walk the JSONL; return ``(records, issues)``.

    For each record, compute the SHA-256 of the *previous* line and compare
    to the record's ``previous_record_hash``. Genesis (first record)
    expects an empty string. Any mismatch is recorded in ``issues`` so the
    caller can render line numbers + computed-vs-expected hashes.
    """
    records: list[dict] = []
    issues: list[dict] = []
    prev_hash = ""

    if not path.exists():
        return records, [{"line": 0, "issue": f"audit trail not found: {path}"}]

    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            try:
                rec = _json.loads(line)
            except _json.JSONDecodeError as exc:
                issues.append(
                    {
                        "line": line_no,
                        "issue": "invalid JSON",
                        "detail": str(exc)[:200],
                    }
                )
                # don't update prev_hash — broken record can't link forward
                continue

            expected_prev = rec.get("previous_record_hash", "") or ""
            if expected_prev != prev_hash:
                issues.append(
                    {
                        "line": line_no,
                        "issue": "previous_record_hash mismatch",
                        "expected_prev": expected_prev[:32] or "<empty>",
                        "computed_prev": prev_hash[:32] or "<empty>",
                        "timestamp": rec.get("timestamp"),
                        "verdict": rec.get("verdict"),
                    }
                )

            records.append(rec)
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()

    return records, issues


@roam_capability(
    name="audit-trail-verify",
    category="workflow",
    summary="Verify SHA-256 chain integrity of a roam audit trail",
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
@click.command(name="audit-trail-verify")
@click.option(
    "--input",
    "input_path",
    type=click.Path(),
    default=None,
    help=f"Path to the audit-trail JSONL (default: {DEFAULT_AUDIT_TRAIL_PATH}).",
)
@click.option(
    "--gate",
    is_flag=True,
    help="Exit 5 (gate failure) if the chain is broken; useful in CI.",
)
@click.pass_context
def audit_trail_verify(ctx, input_path: str | None, gate: bool) -> None:
    """Verify SHA-256 chain integrity of a roam audit trail.

    \b
    Examples:
      roam audit-trail-verify
      roam audit-trail-verify --input .roam/audit-trail.jsonl --gate
      roam --json audit-trail-verify   # for CI parsing

    Tampering with any record (or splicing a record into the middle)
    breaks the chain — this command surfaces the affected line.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    path = Path(input_path) if input_path else DEFAULT_AUDIT_TRAIL_PATH
    records, issues = _verify_chain(path)

    # Fix E (Pattern 2: silent fallbacks) — distinguish "trail does not exist
    # yet" (state=uninitialized) from "trail exists but is corrupted"
    # (state=broken). The previous code reported "chain BROKEN (1 issue
    # across 0 records)" for an absent trail, which misled consumers into
    # thinking a real tamper had been detected. Match the article-12-check
    # two-state pattern: directory/file exists vs file populated.
    trail_missing = not path.exists()
    has_records = bool(records)
    has_real_issues = any("not found" not in i.get("issue", "") for i in issues)

    chain_valid = len(issues) == 0 and has_records
    partial_success = False
    state = "valid"

    if trail_missing:
        state = "uninitialized"
        partial_success = True
        verdict = f"chain not initialized (no audit trail at {path})"
    elif not has_records and not has_real_issues:
        # File exists but is empty (zero records, no parse errors)
        state = "uninitialized"
        partial_success = True
        verdict = f"chain not initialized (audit trail at {path} is empty)"
    elif chain_valid:
        state = "valid"
        verdict = f"chain valid ({len(records)} records)"
    else:
        state = "broken"
        partial_success = True
        verdict = f"chain BROKEN ({len(issues)} issue(s) across {len(records)} record(s))"

    summary = {
        "verdict": verdict,
        "state": state,
        "partial_success": partial_success,
        "chain_valid": chain_valid,
        "total_records": len(records),
        "issues_count": len(issues),
        "first_timestamp": records[0].get("timestamp") if records else None,
        "last_timestamp": records[-1].get("timestamp") if records else None,
        "first_actor": records[0].get("actor") if records else None,
        "audit_trail_path": str(path),
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "audit-trail-verify",
                    summary=summary,
                    issues=issues,
                    records=len(records),
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  path:    {path}")
        click.echo(f"  records: {len(records)}")
        if records:
            click.echo(f"  first:   {records[0].get('timestamp')}")
            click.echo(f"  last:    {records[-1].get('timestamp')}")
        if issues:
            click.echo()
            click.echo("Chain issues:")
            for i in issues[:10]:
                click.echo(f"  line {i['line']}: {i['issue']}")
                if "expected_prev" in i:
                    click.echo(f"    expected: {i['expected_prev']}")
                    click.echo(f"    computed: {i['computed_prev']}")
            if len(issues) > 10:
                click.echo(f"  ... and {len(issues) - 10} more (use --json for full list)")

    if gate and not chain_valid:
        sys.exit(EXIT_GATE_FAILURE)
