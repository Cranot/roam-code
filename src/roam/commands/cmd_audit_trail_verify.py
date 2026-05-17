"""``roam audit-trail-verify`` — verify SHA-256 chain integrity of an audit trail.

Walks an EU AI Act Article 12-shaped audit-trail JSONL produced by
``roam pr-analyze --audit-trail`` and confirms the SHA-256 hash chain is
unbroken from the first record (genesis, ``previous_record_hash = ""``)
to the last. Returns exit code 5 (gate failure) when the chain breaks,
so it can be wired into CI as a tamper-detection gate.

Gate semantics (W830). ``--gate`` is **fail-closed by design**. Three
states map onto the gate as follows:

* ``valid``         → exit 0  (chain verified end-to-end)
* ``broken``        → exit 5  (real tamper / parse anomaly)
* ``uninitialized`` → exit 5  (no trail OR empty trail at the path)

The ``uninitialized`` exit is deliberate. Treating a missing or empty
audit trail as a silent pass would let an agent ship a change with no
evidence chain at all — exactly the failure mode the gate exists to
prevent. To initialize the chain, run ``roam runs start`` (or
``roam pr-analyze --audit-trail``) so a genesis record exists; the gate
will then pass on the next run. Pattern 2 (silent-fallback) discipline:
the structured JSON envelope always emits BEFORE the gate's
``sys.exit(5)`` so consumers can read ``state="uninitialized"`` and
disambiguate uninitialized-chains from tampered-chains.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because audit-trail-verify produces chain-integrity verdicts
(``valid`` / ``broken`` / ``uninitialized``) — not per-code-location
violations. Findings are persisted via ``--persist`` with
``subject_kind="ledger_entry"``, which intentionally does not resolve
to source code symbols. The CI gate (exit 5 on tamper) is the
actionable signal; SARIF would conflate ledger-state-audit with
code-analysis-result. See action.yml _SUPPORTED_SARIF allowlist
+ W1195 audit memo.
"""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import DEFAULT_AUDIT_TRAIL_PATH
from roam.output.formatter import json_envelope, to_json

EXIT_GATE_FAILURE = 5


# W146 (W93 follow-up): audit-trail-verify is the next detector migrating
# onto the central findings registry (after ``clones`` in W95, ``dead`` in
# W99, ``complexity`` in W102, ``smells`` in W109, and the W110-W145
# emitters). Each row in the registry is one per-entry chain anomaly
# (previous_record_hash mismatch or invalid JSON) keyed deterministically
# on ``(audit_trail_path, line_number, issue_kind)`` so a re-run upserts
# in place. Bump this when ``_verify_chain``'s issue-shape changes.
AUDIT_TRAIL_VERIFY_DETECTOR_VERSION: str = "1.0.0"


# Per-issue-kind confidence tier mapping. All current issue kinds
# emitted by ``_verify_chain`` are deterministic checks — either a
# cryptographic hash comparison or a JSON parse failure — so they all
# land at ``static_analysis``. Future heuristic checks (e.g.,
# timing-based gap detection) would add ``heuristic`` entries here.
_ISSUE_KIND_TO_CONFIDENCE: dict[str, str] = {
    "previous_record_hash mismatch": "static_analysis",
    "invalid JSON": "static_analysis",
}
_ISSUE_DEFAULT_CONFIDENCE: str = "static_analysis"


# Map the raw issue string to a stable short slug used in the
# finding_id_str. Keeping the slug independent of the human-readable
# issue string means we can rephrase the issue text without forcing
# every persisted row's id to drift.
_ISSUE_KIND_TO_SLUG: dict[str, str] = {
    "previous_record_hash mismatch": "hash_mismatch",
    "invalid JSON": "invalid_json",
}


def _audit_trail_verify_finding_id(audit_trail_path: str, line_number: int, issue: str) -> str:
    """Stable, deterministic finding id for one chain anomaly.

    The (audit_trail_path, line_number, issue_kind) tuple re-identifies
    the same anomaly across runs. We hash the full path so a per-run
    trail under ``.roam/runs/<id>/`` doesn't conflict with the canonical
    ``.roam/audit-trail.jsonl`` rows.
    """
    slug = _ISSUE_KIND_TO_SLUG.get(issue, "unknown")
    raw = f"{audit_trail_path}:{int(line_number)}:{slug}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"audit-trail-verify:{slug}:{digest}"


def _emit_audit_trail_verify_findings(
    conn: sqlite3.Connection,
    issues: list[dict],
    audit_trail_path: str,
    source_version: str,
) -> int:
    """Mirror each chain anomaly into the central findings registry.

    Returns the count of finding rows written. Caller is responsible
    for opening ``conn`` writable; emit_finding does not commit
    (the caller commits once at the end of the persist branch).

    Skips the synthetic "audit trail not found" issue — that's a state
    flag, not a per-entry tamper finding. Wrapped by the caller in a
    defensive try/except so a pre-W89 DB (without the ``findings``
    table) silently no-ops rather than crashing.

    Subject_kind is ``ledger_entry`` — one row per JSONL line in the
    audit trail. ``audit-trail-conformance-check`` operates at a
    different granularity (whole-trail 6-check rollup) and uses a
    different subject_kind by design.
    """
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for issue in issues:
        issue_kind = issue.get("issue") or ""
        # Skip the synthetic "not found" state — that's a no-trail
        # signal, not a per-entry tamper. The summary already reports
        # state=uninitialized in that case.
        if "not found" in issue_kind:
            continue
        line_number = int(issue.get("line") or 0)
        finding_id = _audit_trail_verify_finding_id(audit_trail_path, line_number, issue_kind)
        evidence = {
            "audit_trail_path": audit_trail_path,
            "line": line_number,
            "issue": issue_kind,
            "expected_prev": issue.get("expected_prev"),
            "computed_prev": issue.get("computed_prev"),
            "timestamp": issue.get("timestamp"),
            "verdict": issue.get("verdict"),
            "detail": issue.get("detail"),
        }
        claim = f"audit-trail-verify: line {line_number} of {audit_trail_path} — {issue_kind}"
        confidence = _ISSUE_KIND_TO_CONFIDENCE.get(issue_kind, _ISSUE_DEFAULT_CONFIDENCE)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="ledger_entry",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="audit-trail-verify",
                source_version=source_version,
            ),
        )
        written += 1
    return written


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
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist chain-anomaly findings to .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector "
        "audit-trail-verify`). The detector-specific output is unchanged; "
        "the registry rows are the denormalised cross-detector surface. "
        "Subject_kind is `ledger_entry` (one row per anomalous JSONL line). "
        "No-ops cleanly when .roam/index.db is missing or the findings "
        "table is absent (pre-W89 schema)."
    ),
)
@click.pass_context
def audit_trail_verify(ctx, input_path: str | None, gate: bool, persist: bool) -> None:
    """Verify SHA-256 chain integrity of a roam audit trail.

    \b
    Examples:
      roam audit-trail-verify
      roam audit-trail-verify --input .roam/audit-trail.jsonl --gate
      roam --json audit-trail-verify   # for CI parsing

    Tampering with any record (or splicing a record into the middle)
    breaks the chain — this command surfaces the affected line.
    """
    # W107/W120 composition: global `roam --ci` also flips the local
    # `--gate` flag so a chain-broken audit trail fails the CI job
    # without needing a separate --gate. LAW 11: explicit local flag
    # (`--gate`) still wins (no-op when already True).
    if not gate and ctx.obj and ctx.obj.get("ci_mode"):
        gate = True
    json_mode = ctx.obj.get("json") if ctx.obj else False

    path = Path(input_path) if input_path else DEFAULT_AUDIT_TRAIL_PATH
    records, issues = _verify_chain(path)

    # --- W146: mirror chain anomalies into the central findings registry ---
    # Runs ONLY with --persist. We emit one row per chain anomaly
    # (previous_record_hash mismatch / invalid JSON); the synthetic
    # "audit trail not found" issue is filtered inside the helper so a
    # missing trail does not get mirrored as a finding. Wrapped
    # defensively so a missing index DB / pre-W89 schema (no
    # ``findings`` table) degrades cleanly without breaking the
    # standard verify output path.
    if persist:
        try:
            from roam.db.connection import open_db

            with open_db(readonly=False) as conn:
                _emit_audit_trail_verify_findings(conn, issues, str(path), AUDIT_TRAIL_VERIFY_DETECTOR_VERSION)
                conn.commit()
        except (sqlite3.OperationalError, click.ClickException):
            # findings table missing OR no .roam/index.db yet — degrade
            # gracefully. The verifier itself must keep working without
            # a roam index because the audit trail is independent state.
            pass

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
        click.echo(f"  state:   {state}")
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
        # W829 disclosure — surface the bootstrap path in text mode when
        # the trail is uninitialized AND name the gate exit explicitly when
        # ``--gate`` will fail-closed below. Without these hints a CI run
        # would log "exit 5" with no in-band cue distinguishing tamper
        # (state=broken) from "no trail yet" (state=uninitialized), and a
        # human reader of the text envelope had no remediation step.
        # JSON consumers read summary.state directly; this only adds
        # parity for text-mode consumers.
        if state == "uninitialized":
            click.echo(
                "  fix:     run `roam pr-analyze --audit-trail` to append the first record, "
                "or `roam runs start --agent <name>` to open a run."
            )
        if gate and not chain_valid:
            click.echo(f"  gate:    --gate set; exiting {EXIT_GATE_FAILURE} (state={state}).")

    # W830 — Gate is fail-closed on both ``broken`` AND ``uninitialized``.
    # The structured envelope has already been emitted above (Pattern 2
    # always-emit), so an agent inspecting stdout can read
    # ``summary.state`` to tell the two failure modes apart. Rationale:
    # a missing/empty audit trail means there is no evidence chain to
    # gate on — silently passing would defeat the purpose of the gate
    # in CI on fresh / mis-configured projects. To get the gate to pass,
    # initialise the chain (`roam runs start` or
    # `roam pr-analyze --audit-trail`) so a genesis record exists.
    # See module docstring "Gate semantics (W830)" for the full
    # decision record.
    if gate and not chain_valid:
        sys.exit(EXIT_GATE_FAILURE)
