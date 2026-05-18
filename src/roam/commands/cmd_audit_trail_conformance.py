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
import json as _json
import sqlite3
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import DEFAULT_AUDIT_TRAIL_PATH
from roam.commands.audit_trail_helpers import load_records as _load_records
from roam.output.formatter import json_envelope, to_json
from roam.output.metric_definitions import CHAIN_COMPLIANCE_SCORE_DEFINITION

EXIT_GATE_FAILURE = 5
DEFAULT_RETENTION_DAYS = 180  # ~6 months, the Article 12 minimum

# Required fields for a record to be Article 12-shaped.
REQUIRED_RECORD_FIELDS = ("timestamp", "actor", "verdict", "diff_sha256", "git_sha", "tool_version")


# W145 (W93 follow-up): audit-trail-conformance is the next detector
# migrating onto the central findings registry (after ``clones`` in W95,
# ``dead`` in W99, ``complexity`` in W102, ``smells`` in W109,
# ``orphan-imports`` in W132, and others). The shape mirrors those
# migrations — a stable detector version stamp and a deterministic
# ``finding_id_str`` so re-runs upsert instead of duplicating rows.
# Bump this when any of the 6 Article 12 checks changes its predicate
# or message shape meaningfully (the registry rows would otherwise
# silently drift).
AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION: str = "1.0.0"


# W145 — per-check confidence tier mapping.
#
# The 6 Article 12 checks split into two evidence classes:
#
# * Cryptographic / schema-deterministic (chain hash verify, presence
#   of required JSONL fields) — same input → same verdict, no
#   timing-based inference → ``static_analysis``.
# * Time-window heuristic (retention compares the oldest record's
#   timestamp to the configurable ``--retention-days`` threshold) —
#   the threshold itself is policy, not law → ``heuristic``.
#
# A failing retention check on a fresh trail isn't necessarily a
# violation (the deployer may have a documented shorter retention
# policy), which is exactly the heuristic-tier contract.
_AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE: dict[str, str] = {
    "chain_integrity": "static_analysis",
    "timestamp_completeness": "static_analysis",
    "actor_attribution": "static_analysis",
    "reproducibility_metadata": "static_analysis",
    "verdict_and_rationale": "static_analysis",
    "retention": "heuristic",
}
_AUDIT_TRAIL_CONFORMANCE_DEFAULT_CONFIDENCE: str = "heuristic"


def _audit_trail_conformance_finding_id(check_id: str, audit_trail_path: str) -> str:
    """Stable, deterministic finding id for one failed conformance check.

    The (check_id, audit_trail_path) tuple re-identifies the same failure
    across runs — a given trail at a given path either passes or fails
    a given check, and re-running with the same inputs must upsert in
    place rather than duplicate the row. We deliberately do NOT fold the
    number of failing records into the id: the *fact that the check
    failed* is the finding, and the evidence JSON carries the per-run
    detail (failing record indices, computed-vs-expected hashes, etc.).
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("audit-trail-conformance", check_id, check_id, audit_trail_path)


def _emit_audit_trail_conformance_findings(
    conn: sqlite3.Connection,
    checks: list[dict],
    audit_trail_path: str,
    retention_days: int,
    source_version: str,
) -> int:
    """Mirror each FAILED conformance check into the central findings registry.

    Returns the count of finding rows written (one per failed check).
    Passed checks are not findings — they're the absence of a finding —
    so we follow the SARIF convention of emitting results only for
    failures. Caller is responsible for opening ``conn`` writable;
    emit_finding does not commit (the caller commits once at the end
    of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard conformance command path.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for c in checks:
        # Only failed checks become findings. A "passed" check isn't a
        # finding; it's the absence of one. Skipping "not_run" states
        # (no-trail branch) for the same reason — if the underlying
        # check did not run, we don't fabricate a row.
        if c.get("passed"):
            continue
        if c.get("state") == "not_run":
            continue

        check_id = c.get("id") or "unknown"
        message = c.get("message") or ""
        confidence = _AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE.get(
            check_id, _AUDIT_TRAIL_CONFORMANCE_DEFAULT_CONFIDENCE
        )
        evidence = {
            "check_id": check_id,
            "audit_trail_path": audit_trail_path,
            "message": message,
            "retention_days_required": retention_days,
            "schema_reference": "EU AI Act Regulation 2024/1689, Article 12",
        }
        finding_id = _audit_trail_conformance_finding_id(check_id, audit_trail_path)
        claim = f"audit-trail-conformance ({check_id}): {audit_trail_path} — {message}"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                # subject_kind="file" with subject_id=None: the trail lives
                # at a known path on disk, but ``.roam/audit-trail.jsonl``
                # is gitignored repo-local state and never appears in the
                # indexed ``files`` table. The findings registry permits
                # NULL subject_id by design for file/edge/commit findings;
                # consumers locate the trail via ``audit_trail_path`` in
                # evidence.
                subject_kind="file",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="audit-trail-conformance",
                source_version=source_version,
            ),
        )
        written += 1
    return written


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
    from roam.output.sarif import _derive_finding_tags, to_sarif

    # W1062: stamp every rule + result with dashboard-filter tags so
    # GitHub Code Scanning / SonarQube can slice the 6 EU AI Act
    # Article 12 conformance checks by family (``compliance``) and
    # standard (``eu-ai-act-article-12``) — the existing ``properties``
    # carried regulation metadata but only in free-form fields the UI
    # cannot filter on. The per-rule ``extra=[c["id"]]`` lets a triage
    # user filter to one specific check (e.g. ``chain-integrity``)
    # without scanning the rule_id column.
    rules = [
        {
            "id": c["id"],
            "shortDescription": f"EU AI Act Article 12 conformance check: {c['id'].replace('_', ' ')}",
            "defaultLevel": "warning",
            "helpUri": "https://artificialintelligenceact.eu/article/12/",
            "properties": {
                "category": "compliance",
                "regulation": "EU AI Act Article 12",
                "tags": _derive_finding_tags(
                    family="compliance",
                    extra=["eu-ai-act-article-12", c["id"]],
                ),
            },
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
                "properties": {
                    "tags": _derive_finding_tags(
                        family="compliance",
                        severity="error" if score < 67 else "warning",
                        extra=["eu-ai-act-article-12", c["id"]],
                    ),
                },
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
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist failed conformance checks to .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector "
        "audit-trail-conformance`). The detector-specific output is unchanged; "
        "the registry rows are the denormalised cross-detector surface. "
        "Passed checks are NOT persisted — only failing checks become "
        "findings (mirrors the SARIF emit, which surfaces results only "
        "for failures)."
    ),
)
@click.pass_context
def audit_trail_conformance_check(
    ctx,
    input_path: str | None,
    retention_days: int,
    gate: bool,
    sarif_output: str | None,
    persist: bool,
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

    # --- W607-AL: substrate-CALL marker plumbing -------------------------
    # cmd_audit_trail_conformance is the COMPLIANCE-checking sibling of
    # cmd_audit_trail_verify. Both are downstream consumers of the same
    # audit-trail JSONL artifact:
    #
    #   cmd_audit_trail_verify (W607-AI)       -- chain-integrity verifier
    #   cmd_audit_trail_conformance (W607-AL)  -- Article-12 conformance
    #
    # Substrate boundaries wrapped here:
    #
    #   load_records                                  (JSONL trail-read)
    #   check_chain_integrity                         (delegates to verify)
    #   check_timestamp_completeness                  (Article-12 §2)
    #   check_actor_attribution                       (Article-12 §3)
    #   check_reproducibility_metadata                (Article-12 §4)
    #   check_verdict_and_rationale                   (Article-12 §5)
    #   check_retention                               (Article-12 §6)
    #   open_findings_db                              (registry conn)
    #   emit_findings                                 (rows)
    #   commit_findings                               (durable persist)
    #
    # The PRIOR code had TWO Pattern-2 silent-fallback blocks around the
    # registry persist path (an inner ``except sqlite3.OperationalError:
    # pass`` and an outer ``except Exception: pass``). Both are replaced
    # with structured ``_run_check_al`` boundaries so the disclosure
    # channel names which step crashed instead of silently degrading.
    #
    # Each raise becomes an
    # ``audit_trail_conformance_<phase>_failed:<exc_class>:<detail>``
    # marker via ``_w607al_warnings_out``. partial_success flips on any
    # non-empty bucket. Empty bucket on the clean path keeps the envelope
    # shape byte-identical to the pre-W607-AL command.
    #
    # TRIAD-CLOSURE milestone: with W607-AI (verify) and W607-AL
    # (conformance), both downstream consumers of the audit-trail JSONL
    # are W607-plumbed. Combined with W607-AD (cmd_attest, producer), the
    # complete attest → verify → conformance triad is now closed.
    _w607al_warnings_out: list[str] = []

    def _run_check_al(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AL marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``audit_trail_conformance_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607al_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607al_warnings_out.append(f"audit_trail_conformance_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CO: aggregation-phase marker plumbing (additive) -----------
    # cmd_audit_trail_conformance is the COMPLIANCE-checking leg of the
    # AUDIT-TRAIL FAMILY (cmd_audit_trail_verify W607-AI + CN aggregation;
    # cmd_audit_trail_export W607-AP). W607-AL plumbed the substrate-CALL
    # layer (10 substrate boundaries: load_records / 6 per-article checks /
    # open_findings_db / emit_findings / commit_findings). W607-CO adds
    # the AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- count passed checks + compute score 0-100
    #   compute_predicate    -- per-article totals (passed/failed/total)
    #   compute_verdict      -- composite verdict string ("conformant" /
    #                           "partial conformance" / "NON-conformant")
    #   serialize_envelope   -- json_envelope("audit-trail-conformance-check", ...)
    #
    # Marker family ``audit_trail_conformance_*`` -- same family as W607-AL
    # (additive, not a separate prefix). Empty bucket -> byte-identical
    # envelope on the success path. Both buckets are combined at envelope-
    # emit time so consumers see the full degradation lineage in marker-
    # emission order. The additive bucket stays distinguishable via its
    # phase names (``score_classify`` / ``compute_predicate`` /
    # ``compute_verdict`` / ``serialize_envelope``).
    #
    # AUDIT-TRAIL FAMILY pairing: with this in place, the family is dual-
    # bucket plumbed:
    #   cmd_audit_trail_verify       (W607-AI substrate + W607-CN aggregation)
    #   cmd_audit_trail_conformance  (W607-AL substrate + W607-CO aggregation)
    #   cmd_audit_trail_export       (W607-AP substrate; CP candidate)
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_co(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(checks) if ...``). A computed default
    # expression evaluates BEFORE the wrap call, so a raise inside the
    # expression escapes the try-block. cmd_sbom's W607-CG sealed this
    # axis. cmd_taint's W607-CJ added the 5th discipline (move ``len()``
    # INSIDE the closure, not at the kwarg-bind site).
    #
    # W607-AL/CO PHASE-NAME COLLISION (W607-CH): the substrate-CALL layer
    # has NO ``serialize_envelope`` phase (the substrate uses ``to_json``
    # at output, but it's NOT wrapped in W607-AL today). So no rename is
    # required. If a future W607-AL revision adds a ``serialize_envelope``
    # phase, rename W607-CO's to ``build_envelope`` to avoid collision.
    _w607co_warnings_out: list[str] = []

    def _run_check_co(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CO marker emission.

        Mirror of ``_run_check_al`` shape (same
        ``audit_trail_conformance_<phase>_failed:`` marker family) but
        writes into ``_w607co_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607co_warnings_out.append(f"audit_trail_conformance_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    records = _run_check_al("load_records", _load_records, path, default=[])

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
            # W331b (Pattern 3a): name the score computation explicitly
            # so consumers reading the no-trail envelope know what the
            # null score WOULD have measured if a trail existed.
            "chain_compliance_score_definition": CHAIN_COMPLIANCE_SCORE_DEFINITION,
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
            # ``audit-trail-export`` READS the trail (it is an exporter,
            # not an initializer) — recommending it here was a CONSTRAINT 12
            # executability bug: the user would run it and get an empty
            # export. The real bootstrap path is `pr-analyze --audit-trail`
            # which appends a genesis record, or `roam runs start` followed
            # by gate commands that auto-log. See module docstring +
            # cmd_pr_analyze.py:1870 (--audit-trail flag).
            "fix": "Run `roam pr-analyze --audit-trail` to append the first record, or `roam runs start --agent <name>` to open a run that auto-logs gate verdicts.",
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
            click.echo("  records: 0")
            click.echo(f"  state:   no_trail ({no_trail_reason})")
            click.echo(f"  fix:     {no_trail_summary['fix']}")
            click.echo()
            click.echo("Reference: EU AI Act Regulation 2024/1689, Article 12.")
            click.echo("Disclaimer: triage signal only — not legal advice.")

        if gate:
            sys.exit(EXIT_GATE_FAILURE)
        return

    # W607-AL: each of the 6 Article-12 checks is a substrate boundary. A
    # raise (corrupted record, encoding error, unexpected schema drift)
    # previously crashed the whole conformance command; now the failing
    # check degrades to a FAIL with a structured marker and the remaining
    # 5 still run.
    chain_ok, chain_msg = _run_check_al(
        "check_chain_integrity",
        _check_chain_integrity,
        path,
        default=(False, "chain integrity check did not run"),
    )
    ts_ok, ts_msg = (
        _run_check_al(
            "check_timestamp_completeness",
            _check_timestamps,
            records,
            default=(False, "timestamp completeness check did not run"),
        )
        if records
        else (False, "no records loaded")
    )
    actor_ok, actor_msg = (
        _run_check_al(
            "check_actor_attribution",
            _check_actors,
            records,
            default=(False, "actor attribution check did not run"),
        )
        if records
        else (False, "no records loaded")
    )
    repro_ok, repro_msg = (
        _run_check_al(
            "check_reproducibility_metadata",
            _check_reproducibility,
            records,
            default=(False, "reproducibility metadata check did not run"),
        )
        if records
        else (False, "no records loaded")
    )
    verdict_ok, verdict_msg = (
        _run_check_al(
            "check_verdict_and_rationale",
            _check_verdicts_and_rationale,
            records,
            default=(False, "verdict + rationale check did not run"),
        )
        if records
        else (False, "no records loaded")
    )
    retention_ok, retention_msg = (
        _run_check_al(
            "check_retention",
            _check_retention,
            records,
            retention_days,
            default=(False, "retention check did not run"),
        )
        if records
        else (False, "no records loaded")
    )

    checks = [
        {"id": "chain_integrity", "passed": chain_ok, "message": chain_msg},
        {"id": "timestamp_completeness", "passed": ts_ok, "message": ts_msg},
        {"id": "actor_attribution", "passed": actor_ok, "message": actor_msg},
        {"id": "reproducibility_metadata", "passed": repro_ok, "message": repro_msg},
        {"id": "verdict_and_rationale", "passed": verdict_ok, "message": verdict_msg},
        {"id": "retention", "passed": retention_ok, "message": retention_msg},
    ]

    # --- W145: mirror failed checks into the central findings registry ---
    # Runs ONLY with --persist. The persisted set covers ONLY failing
    # checks — passed checks aren't findings, and "not_run" states (the
    # no-trail branch handled above this point) are also skipped so we
    # don't fabricate rows for checks that never executed.
    #
    # W607-AL: the prior code had TWO nested Pattern-2 silent fallback
    # blocks here -- an inner ``except sqlite3.OperationalError: pass``
    # and an outer ``except Exception: pass``. Both are replaced with
    # structured ``_run_check_al`` boundaries so the disclosure channel
    # names which step crashed (open_findings_db / emit_findings /
    # commit_findings) instead of degrading silently. The conformance
    # checker still keeps working without a roam index because each
    # wrapper has a sensible default.
    if persist:

        def _open_findings_db():
            from roam.commands.resolve import ensure_index
            from roam.db.connection import open_db

            ensure_index(quiet=True)
            return open_db(readonly=False)

        _db_ctx = _run_check_al("open_findings_db", _open_findings_db, default=None)
        if _db_ctx is not None:
            try:
                with _db_ctx as conn:
                    _run_check_al(
                        "emit_findings",
                        _emit_audit_trail_conformance_findings,
                        conn,
                        checks,
                        str(path),
                        retention_days,
                        AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
                        default=0,
                    )
                    _run_check_al(
                        "commit_findings",
                        conn.commit,
                        default=None,
                    )
            except Exception as exc:  # noqa: BLE001 -- top-level disclosure
                # The context-manager exit itself raised (rare: e.g.
                # OperationalError on close). Capture via the
                # ``commit_findings`` phase since the most likely cause
                # is a deferred-write failure flushing on close.
                _w607al_warnings_out.append(
                    f"audit_trail_conformance_commit_findings_failed:{type(exc).__name__}:{exc}"
                )

    # W607-CO -- score_classify boundary. Wraps the passed-count + score
    # computation so a downstream refactor (e.g. a non-bool ``passed`` from
    # a vocabulary refactor, or a __bool__-raising sentinel) surfaces a
    # marker rather than crashing the envelope. Floor returns documented
    # zero counts matching the no-trail branch shape so downstream
    # verdict/compute_predicate stay non-null.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(checks)`` / ``sum(...)`` are
    # computed INSIDE the wrapped closure rather than at the call site -- a
    # _BadChecksList whose ``__len__`` or ``__iter__`` raises would
    # otherwise escape the try-block at kwarg-bind time. W978 5th-
    # discipline (cmd_taint W607-CJ): move ``len()`` INSIDE the closure.
    def _score_classify_checks(_checks):
        _passed = sum(1 for c in _checks if c["passed"])
        _total = len(_checks)
        _score = round(100 * _passed / _total) if _total else 0
        return {"passed": _passed, "total": _total, "score": _score}

    _score_dict = _run_check_co(
        "score_classify",
        _score_classify_checks,
        checks,
        default={"passed": 0, "total": 6, "score": 0},
    )
    passed = _score_dict["passed"]
    total = _score_dict["total"]
    score = _score_dict["score"]

    # W607-CO -- compute_verdict boundary. Wraps the verdict-string
    # assembly so a downstream f-string refactor (non-int passed/total
    # from a vocabulary refactor) surfaces a marker rather than crashing
    # the envelope. Floor must NOT re-interpolate the same values that
    # tripped the closure (W978 first-hypothesis: a __format__-raising
    # sentinel would re-raise inside the default f-string). Use the
    # literal "audit-trail-conformance check completed" floor (LAW 6
    # still holds: the line works standalone).
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``passed`` / ``total`` / ``score``
    # are int locals already bound BEFORE the wrap call (they are simple
    # Name lookups, not Call/Attribute/BinOp expressions), so kwarg-bind
    # is safe. The f-string interpolation itself is what could raise --
    # that lives INSIDE the closure.
    def _build_verdict_str(_passed: int, _total: int, _score: int) -> str:
        if _score == 100:
            return f"conformant ({_passed}/{_total} checks)"
        if _score >= 67:
            return f"partial conformance ({_passed}/{_total} checks, score {_score}/100)"
        return f"NON-conformant ({_passed}/{_total} checks, score {_score}/100)"

    verdict = _run_check_co(
        "compute_verdict",
        _build_verdict_str,
        passed,
        total,
        score,
        default="audit-trail-conformance check completed",
    )

    # W607-AL -- a non-empty substrate bucket flips partial_success. We
    # OR with any existing partial_success so we never DOWNGRADE a real
    # failure-induced flag set elsewhere.
    _w607al_partial = bool(_w607al_warnings_out)

    # W607-CO -- compute_predicate boundary. Wraps the per-article totals
    # extraction so a future ``checks[]`` schema refactor that drops or
    # renames count fields surfaces a marker rather than crashing the
    # envelope. Floor to documented zero-counts matching the no-trail
    # branch shape so downstream summary fields stay non-null. W978
    # discipline: ``default=`` is a literal dict, NOT a computed expression
    # over the (potentially poisoned) inputs.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(records)`` is computed
    # INSIDE the wrapped closure -- passing the raw records list keeps
    # the kwarg-bind step pure (no ``__len__`` call until we're inside
    # the try-block). cmd_taint W607-CJ 5th-discipline anchor.
    def _compute_predicate_fields(_records, _passed: int, _total: int) -> dict:
        return {
            "articles_checked": _total,
            "articles_passed": _passed,
            "articles_failed": _total - _passed,
            "total_records": len(_records),
        }

    _pred_fields = _run_check_co(
        "compute_predicate",
        _compute_predicate_fields,
        records,
        passed,
        total,
        default={
            "articles_checked": 6,
            "articles_passed": 0,
            "articles_failed": 6,
            "total_records": 0,
        },
    )

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
        # W331b (Pattern 3a): pair the score with an explicit definition
        # of what the 6 checks measure. Lives in metric_definitions.py
        # so the string cannot drift away from the actual checklist.
        "chain_compliance_score_definition": CHAIN_COMPLIANCE_SCORE_DEFINITION,
        "compliance_kind": "audit_trail_chain_integrity",
        "compliance_kind_definition": (
            "Chain-of-custody score for an existing roam audit trail: "
            "6 per-record integrity checks (chain hash, timestamps, "
            "actor attribution, reproducibility metadata, verdict + "
            "rationale, retention). NOT the same as article-12-check, "
            "which measures repo-level readiness artifacts. "
            "Reference: EU AI Act Article 12 (event logging)."
        ),
        "checks_passed": _pred_fields.get("articles_passed", passed),
        "checks_total": _pred_fields.get("articles_checked", total),
        "total_records": _pred_fields.get("total_records", len(records)),
        "audit_trail_path": str(path),
        "retention_days_required": retention_days,
        "schema_reference": "EU AI Act Regulation 2024/1689, Article 12",
        "disclaimer": "Triage signal only — not legal advice.",
    }
    # W607-AL / W607-CO: thread substrate-CALL markers AND aggregation-
    # phase markers onto BOTH summary.warnings_out AND top-level
    # envelope.warnings_out so consumers reading either surface see the
    # full disclosure lineage. Both buckets share the canonical
    # ``audit_trail_conformance_*`` marker family (W607-CO is additive,
    # not a separate prefix); the additive bucket stays distinguishable
    # via its phase names (``score_classify`` / ``compute_predicate`` /
    # ``compute_verdict`` / ``serialize_envelope``). Non-empty combined
    # bucket flips partial_success. Empty combined bucket on the clean
    # path keeps the envelope byte-identical to the pre-W607-AL/CO
    # command (hash-stable happy path).
    _combined_warnings_out = list(_w607al_warnings_out) + list(_w607co_warnings_out)
    if _combined_warnings_out:
        summary["warnings_out"] = list(_combined_warnings_out)
        summary["partial_success"] = True

    if sarif:
        from roam.output.sarif import write_sarif

        sarif_doc = _checks_to_sarif(checks, path, score)
        sarif_text = write_sarif(sarif_doc, sarif_output)
        if not sarif_output:
            click.echo(sarif_text)
        elif not json_mode:
            click.echo(f"VERDICT: {verdict} — SARIF written to {sarif_output}")
    elif json_mode:
        envelope_kwargs: dict = {
            "summary": summary,
            "checks": checks,
            # Top-level disclaimer so procurement consumers can't miss it.
            "disclaimer": "Triage signal only — not legal advice. Compliance depends on full system context.",
            "schema_reference": "EU AI Act Regulation 2024/1689, Article 12",
        }
        # W607-AL / W607-CO: mirror BOTH substrate-CALL markers AND
        # aggregation-phase markers at the top level too so a consumer
        # reading envelope.warnings_out (rather than
        # envelope.summary.warnings_out) sees the same disclosure.
        if _combined_warnings_out:
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CO -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("audit-trail-conformance-check", ...)``
        # would otherwise crash AFTER all substrate + aggregation signals
        # were already gathered. Floor to a minimal envelope stub so
        # consumers still receive a parseable JSON object with the marker
        # attached + the canonical command name. Mirror of cmd_taint's
        # W607-CJ serialize_envelope floor pattern.
        _envelope_floor: dict = {
            "command": "audit-trail-conformance-check",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        _envelope = _run_check_co(
            "serialize_envelope",
            json_envelope,
            "audit-trail-conformance-check",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-CO -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``audit_trail_conformance_serialize_envelope_failed:`` marker
        # was appended to ``_w607co_warnings_out`` and the floor stub
        # carries only the pre-raise combined list. Rebuild the floor
        # stub's warnings_out so the new marker reaches the JSON output.
        # Clean path -> envelope is the real json_envelope return value,
        # no rebuild needed.
        if _envelope is _envelope_floor and _w607co_warnings_out:
            _combined_warnings_out = list(_w607al_warnings_out) + list(_w607co_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            _envelope = _envelope_floor

        click.echo(to_json(_envelope))
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
