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


def _build_rollup(_records, _issues, _records_count: int, _issues_count: int) -> dict:
    """Chain-verify rollup metrics: total / verified / broken / unsigned.

    Module-level (was a nested closure) so the genesis-exemption rule is
    unit-testable in isolation. Pure function of its arguments — captures
    nothing from any enclosing scope.

    W607-EA chain_rollup boundary semantics: W978 5th-discipline — len()
    calls live INSIDE this function, never at the kwarg-bind site of the
    ``_run_check_ea`` wrapper.

    ``missing_signatures`` counts records carrying NO integrity hash. The
    genesis record (index 0) is EXEMPT: an empty ``previous_record_hash``
    on genesis is the chain root marker (see module docstring "genesis,
    previous_record_hash = ''"), NOT a missing signature. Counting genesis
    as unsigned falsely reported ``missing_signatures: 1`` on every
    well-formed chain — a Pattern-2 self-contradicting envelope
    (``chain_valid: true`` alongside ``missing_signatures: 1``). A
    non-genesis record with an empty ``previous_record_hash`` AND no
    ``record_hash`` is genuinely unsigned (and also breaks the chain link,
    so it is additionally counted as a broken issue).
    """
    broken = 0
    missing_sigs = 0
    for _i in _issues:
        _kind = _i.get("issue", "")
        if "not found" in _kind:
            continue
        if "previous_record_hash mismatch" in _kind or "invalid JSON" in _kind:
            broken += 1
    for _idx, _r in enumerate(_records):
        if _idx == 0:
            continue  # genesis: empty previous_record_hash is by design
        if not _r.get("previous_record_hash", "") and not _r.get("record_hash", ""):
            missing_sigs += 1
    verified = max(0, _records_count - broken)
    return {
        "total_runs": _records_count,
        "verified_runs": verified,
        "broken_runs": broken,
        "missing_signatures": missing_sigs,
    }


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

    # --- W607-AI: substrate-CALL marker plumbing -------------------------
    # cmd_audit_trail_verify is the VERIFIER half of the cryptographic-
    # attestation triad (cmd_attest=producer / cmd_pr_bundle=composer /
    # cmd_cga=signer / cmd_audit_trail_verify=verifier). The verifier's
    # substrate boundaries are the SHA-256 chain walk (``verify_chain``),
    # the registry-write path (``emit_findings`` / ``open_findings_db`` /
    # ``commit_findings``). A raise on any boundary previously either
    # crashed the verifier (chain walk) OR was silently swallowed by a
    # bare ``except (sqlite3.OperationalError, click.ClickException)``
    # (registry path). The silent swallow is exactly the Pattern-2
    # silent-fallback antipattern documented in CLAUDE.md.
    #
    # Each wrapped phase becomes a structured
    # ``audit_trail_verify_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607ai_warnings_out`` and the envelope still emits cleanly. The
    # marker rides BOTH ``summary.warnings_out`` and top-level
    # ``warnings_out`` so consumers reading either surface see the
    # disclosure. ``partial_success`` flips on non-empty bucket.
    #
    # Triad-quartet milestone: with W607-AD (cmd_attest), W607-AE
    # (cmd_pr_bundle), W607-AF (cmd_cga) and now W607-AI
    # (cmd_audit_trail_verify), the producer + composer + signer +
    # verifier of the cryptographic-attestation path are all W607-
    # plumbed. A raise anywhere in {sign, hash, write, verify} now
    # surfaces a per-phase marker rather than crashing.
    _w607ai_warnings_out: list[str] = []

    def _run_check_ai(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AI marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``audit_trail_verify_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ai_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ai_warnings_out.append(f"audit_trail_verify_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CN: ADDITIVE aggregation-phase marker plumbing ------------
    # W607-AI (above) wraps the substrate-CALL boundaries (verify_chain /
    # open_findings_db / emit_findings / commit_findings); W607-CN extends
    # marker coverage to the AGGREGATION-PHASE boundaries that W607-AI
    # left unguarded:
    #
    #   - ``compute_predicate``    -- per-field derivation of the
    #                                 boolean state predicates
    #                                 (``trail_missing`` /
    #                                 ``has_records`` /
    #                                 ``has_real_issues`` /
    #                                 ``chain_valid``) used to compose the
    #                                 verdict string + state classifier.
    #                                 Floor to a literal "broken" predicate
    #                                 set so the downstream verdict still
    #                                 disambiguates from a clean SAFE
    #                                 (Pattern-2 silent-fallback
    #                                 discipline + W826 / W829 / W830
    #                                 regression-guard alignment).
    #   - ``compute_verdict``      -- verdict string assembly +
    #                                 state classification (4-way switch
    #                                 between valid / uninitialized
    #                                 (missing file) / uninitialized
    #                                 (empty file) / broken). Floor to a
    #                                 literal "Audit-trail verification
    #                                 completed" string per LAW 6 + W978
    #                                 first-hypothesis discipline (no
    #                                 re-interpolation of the same values
    #                                 that just raised).
    #   - ``serialize_envelope``   -- ``json_envelope("audit-trail-verify",
    #                                 ...)`` projection (downstream
    #                                 contract changes / shape
    #                                 regressions).
    #
    # cmd_audit_trail_verify is the HMAC chain-verify READER --
    # AUDIT-TRAIL family: opens alongside cmd_audit_trail_conformance
    # (W607-AL, ``audit_trail_conformance_*`` markers) and
    # cmd_audit_trail_export (W607-AP, ``audit_trail_export_*`` markers).
    # The W607-CN markers fire AT RUNTIME when an aggregation-phase
    # boundary raises, complementing the W607-AI substrate-CALL coverage.
    #
    # Marker family ``audit_trail_verify_*`` -- same family as W607-AI
    # (additive, not a separate prefix). Empty bucket -> byte-identical
    # envelope on the success path.
    #
    # No ``score_classify`` phase: cmd_audit_trail_verify has no numeric
    # risk-score classifier (chain integrity is binary valid/broken plus
    # a 4-way state enum, NOT a 0-100 risk score). The W607-CD canonical
    # 3-phase set (compute_predicate / compute_verdict /
    # serialize_envelope) covers all aggregation boundaries.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_cn(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(records) if ...``). A computed
    # default expression evaluates BEFORE the wrap call, so a raise
    # inside the expression escapes the try-block. cmd_sbom's W607-CG
    # sealed this axis after a regression where a ``len(_BadDeps())``
    # default eagerly raised. cmd_taint's W607-CJ added the additional
    # discipline of MOVING ``len()`` calls INSIDE the wrapped closure
    # (not at kwarg-bind time). Floors below are documented constants.
    _w607cn_warnings_out: list[str] = []

    def _run_check_cn(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CN marker emission.

        Mirror of ``_run_check_ai`` shape (same
        ``audit_trail_verify_<phase>_failed:`` marker family) but writes
        into ``_w607cn_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cn_warnings_out.append(f"audit_trail_verify_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-EA: ADDITIVE aggregation-LAYER marker plumbing -------------
    # Sits ON TOP of the W607-AI substrate-CALL layer AND the W607-CN
    # aggregation-phase layer. cmd_audit_trail_verify is the HMAC
    # chain-verify READER -- closing the runs-ledger reader 3-way at
    # AGGREGATION-PARITY alongside cmd_postmortem (W607-AN + W607-CV +
    # W607-DR, 16 phases; git-log reader) and cmd_pr_replay
    # (W607-AH + W607-CA + W607-DV, 18 phases; ledger consumer +
    # replay-renderer).
    #
    # W607-EA phases focus on the chain-verify-state aggregation slice that
    # W607-CN does NOT cover. CN wraps compute_predicate / compute_verdict /
    # serialize_envelope (the verdict-string assembly axes); EA wraps the
    # 4-tier chain-state classifier + rollup metrics + verdict synthesis +
    # additive envelope re-projection:
    #
    #   * ``verify_classify``      -- buckets the chain-verify state into one
    #                                 of FOUR closed-enum tiers
    #                                 (CHAIN_VERIFIED / CHAIN_BROKEN /
    #                                 NOT_INITIALIZED / DEGRADED). Floor
    #                                 lands on DEGRADED (Pattern-2
    #                                 silent-fallback discipline + W826 /
    #                                 W829 / W830 silent-SAFE guard).
    #   * ``chain_rollup``         -- rollup metrics dict
    #                                 (total_runs / verified_runs /
    #                                 broken_runs / missing_signatures).
    #                                 Floor to a literal-constant dict of
    #                                 0s per W978 discipline.
    #   * ``verify_verdict``       -- single-line verdict synthesis with
    #                                 LAW 6 literal floor
    #                                 ``"audit_trail_verify completed"``.
    #   * ``ea_serialize_envelope`` -- additive ``json_envelope`` re-projection.
    #                                 DISTINCT phase name from CN's
    #                                 ``serialize_envelope`` (W978 4th
    #                                 discipline: phase-name collision
    #                                 check) so the per-phase marker prefix
    #                                 stays unambiguous and the 4 EA phases
    #                                 stay disjoint from the 3 CN phases +
    #                                 4 AI substrate phases.
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_ea(...)`` call:
    #   1. f-string verdict floor: the verdict default= floor is the
    #      LITERAL string "audit_trail_verify completed", NEVER
    #      re-interpolating counts that may have tripped the closure.
    #   2. kwarg-default eagerness: ``default=`` is a literal constant on
    #      every call. The AST audit in the test file pins this.
    #   3. json.dumps(default=str) sentinel: floors are str/int/dict/None
    #      (json-serialisable with the standard encoder).
    #   4. phase-name collision: the 4 EA phases (verify_classify /
    #      chain_rollup / verify_verdict / ea_serialize_envelope) MUST NOT
    #      collide with the 4 AI substrate phases (verify_chain /
    #      open_findings_db / emit_findings / commit_findings) OR the 3
    #      CN aggregation phases (compute_predicate / compute_verdict /
    #      serialize_envelope). The phase-name disjointness test pins this.
    #   5. len() at kwarg-bind: every len() call lives INSIDE the wrapped
    #      closure, never at the ``_run_check_ea(...)`` call site.
    #   6. unguarded len()/if on poisoned object: floors are concrete
    #      dict/str/None, never sentinels that __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    #
    # Marker family ``audit_trail_verify_*`` -- same family as W607-AI +
    # W607-CN (additive, NOT a separate prefix). Empty bucket -> byte-
    # identical envelope on the success path (hash-stable happy path).
    _w607ea_warnings_out: list[str] = []

    def _run_check_ea(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-LAYER boundary with W607-EA marker emission.

        Mirror of ``_run_check_ai`` / ``_run_check_cn`` shape (same
        ``audit_trail_verify_<phase>_failed:`` marker family) but writes
        into ``_w607ea_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ea_warnings_out.append(f"audit_trail_verify_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # The CRYPTOGRAPHIC verify boundary: the SHA-256 chain walk. A raise
    # here (corrupted file, encoding error, hash collision) previously
    # crashed the verifier wholesale; now surfaces a structured marker
    # and the envelope still emits with empty records/issues lists.
    _verify_result = _run_check_ai(
        "verify_chain",
        _verify_chain,
        path,
        default=([], []),
    )
    records, issues = _verify_result if _verify_result else ([], [])

    # --- W146: mirror chain anomalies into the central findings registry ---
    # Runs ONLY with --persist. We emit one row per chain anomaly
    # (previous_record_hash mismatch / invalid JSON); the synthetic
    # "audit trail not found" issue is filtered inside the helper so a
    # missing trail does not get mirrored as a finding.
    #
    # W607-AI: the prior bare ``try / except (sqlite3.OperationalError,
    # click.ClickException): pass`` was a Pattern-2 silent fallback --
    # findings-table-missing OR no .roam/index.db yet degraded to a
    # silent no-op with NO disclosure channel. Now each substrate
    # boundary (open_findings_db / emit_findings / commit_findings) is
    # wrapped separately so the disclosure marker names which step
    # crashed. The verifier still keeps working without a roam index
    # because each wrapper has a sensible default.
    if persist:

        def _open_findings_db():
            from roam.db.connection import open_db

            return open_db(readonly=False)

        _db_ctx = _run_check_ai("open_findings_db", _open_findings_db, default=None)
        if _db_ctx is not None:
            try:
                with _db_ctx as conn:
                    _run_check_ai(
                        "emit_findings",
                        _emit_audit_trail_verify_findings,
                        conn,
                        issues,
                        str(path),
                        AUDIT_TRAIL_VERIFY_DETECTOR_VERSION,
                        default=0,
                    )
                    _run_check_ai(
                        "commit_findings",
                        conn.commit,
                        default=None,
                    )
            except Exception as exc:  # noqa: BLE001 -- top-level disclosure
                # The context-manager exit itself raised (rare: e.g.
                # OperationalError on close). Capture via the
                # ``commit_findings`` phase since the most likely cause
                # is a deferred-write failure flushing on close.
                _w607ai_warnings_out.append(f"audit_trail_verify_commit_findings_failed:{type(exc).__name__}:{exc}")

    # Fix E (Pattern 2: silent fallbacks) — distinguish "trail does not exist
    # yet" (state=uninitialized) from "trail exists but is corrupted"
    # (state=broken). The previous code reported "chain BROKEN (1 issue
    # across 0 records)" for an absent trail, which misled consumers into
    # thinking a real tamper had been detected. Match the article-12-check
    # two-state pattern: directory/file exists vs file populated.
    #
    # W607-CN -- compute_predicate boundary. Wraps the boolean predicate
    # derivation step so a future ``issues`` schema refactor that breaks
    # the ``not found`` substring probe (e.g. renaming the "audit trail
    # not found" sentinel) surfaces a marker rather than crashing the
    # envelope. Floor to the "broken" predicate shape (no records / has
    # real issues / chain_invalid) per W826 / W829 / W830 silent-SAFE
    # discipline: a poisoned predicate MUST land on broken/uninitialized,
    # NEVER on a clean SAFE. W978 discipline: ``default=`` is a literal
    # dict with explicit False/True values, NOT a comprehension that
    # re-walks the (potentially poisoned) inputs.
    def _build_predicates(_path: Path, _records, _issues) -> dict:
        _trail_missing = not _path.exists()
        _has_records = bool(_records)
        _has_real_issues = any("not found" not in i.get("issue", "") for i in _issues)
        _records_count = len(_records)
        _issues_count = len(_issues)
        _chain_valid = _issues_count == 0 and _has_records
        return {
            "trail_missing": _trail_missing,
            "has_records": _has_records,
            "has_real_issues": _has_real_issues,
            "chain_valid": _chain_valid,
            "records_count": _records_count,
            "issues_count": _issues_count,
        }

    _pred = _run_check_cn(
        "compute_predicate",
        _build_predicates,
        path,
        records,
        issues,
        default={
            # Pattern-2 / W826 silent-fallback discipline: a poisoned
            # predicate floors to BROKEN (chain_valid=False), NOT a
            # clean SAFE. The downstream verdict assembly then names
            # the absent state via the broken branch. Floor counts to
            # 0 (literal-constant int per W978 discipline) so the
            # envelope's total_records / issues_count fields stay
            # non-null on the floor path.
            "trail_missing": False,
            "has_records": False,
            "has_real_issues": True,
            "chain_valid": False,
            "records_count": 0,
            "issues_count": 0,
        },
    )
    trail_missing = _pred["trail_missing"]
    has_records = _pred["has_records"]
    has_real_issues = _pred["has_real_issues"]
    chain_valid = _pred["chain_valid"]
    # W607-CN: ``len(records)`` / ``len(issues)`` are computed INSIDE
    # the wrapped compute_predicate closure so a poisoned
    # ``_BadChainState`` whose ``__len__`` raises is caught by the wrap
    # rather than crashing the envelope (W978 + W607-CJ kwarg-default
    # eagerness trap, follow-on axis: len() calls in summary-builder
    # are an unguarded boundary if not hoisted into the wrap).
    records_count = _pred["records_count"]
    issues_count = _pred["issues_count"]
    partial_success = False
    state = "valid"

    # W607-CN -- compute_verdict boundary. Wraps the verdict + state
    # classifier together (they switch on the same predicates) so a
    # downstream f-string refactor surfaces a marker rather than crashing
    # the envelope. Floor must NOT re-interpolate the same values that
    # tripped the closure (W978 first-hypothesis discipline). Use a
    # literal "Audit-trail verification completed" floor (LAW 6 still
    # holds: the line works standalone). The state floors to "broken"
    # paired with partial_success=True per the W826 silent-SAFE
    # discipline carried into compute_predicate above.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP (W607-CJ axis): ``len(records)`` /
    # ``len(issues)`` are computed INSIDE the wrapped closure rather than
    # at the call site -- a ``_BadIssueList`` whose ``__len__`` raises
    # would otherwise escape the try-block at kwarg-bind time.
    def _build_verdict_and_state(
        _trail_missing: bool,
        _has_records: bool,
        _has_real_issues: bool,
        _chain_valid: bool,
        _path: Path,
        _records,
        _issues,
    ) -> dict:
        if _trail_missing:
            return {
                "state": "uninitialized",
                "partial_success": True,
                "verdict": f"chain not initialized (no audit trail at {_path})",
            }
        if not _has_records and not _has_real_issues:
            # File exists but is empty (zero records, no parse errors)
            return {
                "state": "uninitialized",
                "partial_success": True,
                "verdict": f"chain not initialized (audit trail at {_path} is empty)",
            }
        if _chain_valid:
            return {
                "state": "valid",
                "partial_success": False,
                "verdict": f"chain valid ({len(_records)} records)",
            }
        return {
            "state": "broken",
            "partial_success": True,
            "verdict": (f"chain BROKEN ({len(_issues)} issue(s) across {len(_records)} record(s))"),
        }

    _verdict_dict = _run_check_cn(
        "compute_verdict",
        _build_verdict_and_state,
        trail_missing,
        has_records,
        has_real_issues,
        chain_valid,
        path,
        records,
        issues,
        default={
            "state": "broken",
            "partial_success": True,
            "verdict": "Audit-trail verification completed",
        },
    )
    state = _verdict_dict["state"]
    partial_success = _verdict_dict["partial_success"]
    verdict = _verdict_dict["verdict"]

    # --- W607-EA: aggregation-LAYER classification + rollup + verdict ----
    # W607-EA -- verify_classify boundary. Buckets the chain-verify state
    # into one of FOUR closed-enum tiers
    # (CHAIN_VERIFIED / CHAIN_BROKEN / NOT_INITIALIZED / DEGRADED). The
    # 4-tier vocabulary is a STRICT SUPERSET of the W829 3-state matrix
    # (valid / broken / uninitialized) with a 4th DEGRADED tier reserved
    # for the case where W607-AI substrate markers landed but the chain
    # walk itself completed -- i.e. registry-write failed but verify did
    # not. The floor lands on DEGRADED so a raise inside the closure
    # itself NEVER produces a clean CHAIN_VERIFIED verdict (Pattern-2 +
    # W826 silent-SAFE discipline). W978 6th-discipline: the floor is a
    # literal constant string, NOT a re-interpolation of the same state
    # value that may have tripped the closure.
    def _classify_chain_state(_state: str, _has_substrate_warnings: bool) -> str:
        if _state == "valid":
            return "CHAIN_VERIFIED" if not _has_substrate_warnings else "DEGRADED"
        if _state == "broken":
            return "CHAIN_BROKEN"
        if _state == "uninitialized":
            return "NOT_INITIALIZED"
        return "DEGRADED"

    _has_ai_warnings = bool(_w607ai_warnings_out)
    chain_tier = _run_check_ea(
        "verify_classify",
        _classify_chain_state,
        state,
        _has_ai_warnings,
        default="DEGRADED",
    )

    # W607-EA -- chain_rollup boundary. Rollup metrics dict counting
    # total_runs / verified_runs / broken_runs / missing_signatures for
    # the verified chain. ``_build_rollup`` is the module-level helper
    # (hoisted out of this closure so the genesis-exemption rule is
    # unit-testable). W978 2nd-discipline: floor is a literal-constant
    # dict of int 0s (NOT a comprehension or expression that re-walks
    # the potentially poisoned inputs).
    rollup = _run_check_ea(
        "chain_rollup",
        _build_rollup,
        records,
        issues,
        records_count,
        issues_count,
        default={
            "total_runs": 0,
            "verified_runs": 0,
            "broken_runs": 0,
            "missing_signatures": 0,
        },
    )

    # W607-EA -- verify_verdict boundary. Synthesises a single-line
    # additive verdict from the rollup metrics. LAW 6 standalone-parse:
    # the floor is the literal "audit_trail_verify completed" (NOT
    # re-interpolating rollup values that may have tripped the closure).
    # The chain_valid path uses "X verified of Y total runs"; broken /
    # uninitialized fall through to the literal floor.
    def _compose_ea_verdict(_tier: str, _rollup: dict) -> str:
        if _tier == "CHAIN_VERIFIED":
            return f"{_rollup['verified_runs']} verified of {_rollup['total_runs']} total runs"
        return "audit_trail_verify completed"

    ea_verdict = _run_check_ea(
        "verify_verdict",
        _compose_ea_verdict,
        chain_tier,
        rollup,
        default="audit_trail_verify completed",
    )

    # --- Missing-signature disclosure (Pattern-2 silent-fallback guard) ---
    # A record with no ``previous_record_hash`` AND no ``record_hash`` is
    # an *unsigned* event: the SHA-256 chain still links unbroken (so
    # ``chain_valid`` stays True by design — unsigned-but-unbroken is a
    # genuine "valid" chain), but the event carries no integrity proof of
    # its own. Reporting a flat "chain valid" while ``chain_rollup`` shows
    # ``missing_signatures > 0`` is a silent fallback: the verdict hides a
    # real evidence gap. We DO NOT redefine ``chain_valid`` — instead the
    # verdict NAMES the unsigned events and ``partial_success`` flips so
    # consumers reading only the verdict (LAW 6) still see the gap.
    missing_sigs = 0
    if isinstance(rollup, dict):
        try:
            missing_sigs = int(rollup.get("missing_signatures", 0) or 0)
        except (TypeError, ValueError):
            missing_sigs = 0
    if missing_sigs > 0 and state == "valid":
        partial_success = True
        if "unsigned" not in verdict:
            _plural = "event" if missing_sigs == 1 else "events"
            verdict = f"{verdict}; {missing_sigs} {_plural} unsigned"

    # W607-AI / W607-CN / W607-EA -- thread substrate-CALL AND
    # aggregation-phase AND aggregation-LAYER markers onto BOTH
    # summary.warnings_out and the top-level envelope.warnings_out so
    # consumers reading either surface see the disclosure channel.
    # ``partial_success`` flips when ANY bucket is non-empty. Empty
    # buckets on the clean path keep the envelope shape byte-identical
    # to the pre-W607 verifier (hash-stable happy path). All three
    # buckets share the canonical ``audit_trail_verify_*`` marker family
    # (W607-EA is additive, NOT a separate prefix); the additive bucket
    # stays distinguishable via its phase names (``verify_classify`` /
    # ``chain_rollup`` / ``verify_verdict`` / ``ea_serialize_envelope``).
    _combined_warnings_out = list(_w607ai_warnings_out) + list(_w607cn_warnings_out) + list(_w607ea_warnings_out)
    if _combined_warnings_out:
        partial_success = True

    # W607-CN follow-on (W978 + W607-CJ axis): use the predicate-computed
    # counts (``records_count`` / ``issues_count``) NOT raw ``len(records)``
    # / ``len(issues)`` calls here. A poisoned ``_BadChainState`` whose
    # ``__len__`` raises would otherwise crash the unguarded summary
    # builder; the predicate-derived counts already came through the
    # wrap (or floored to 0 on raise). Likewise gate the ``records[0]``
    # / ``records[-1]`` access on ``has_records`` (a predicate-derived
    # bool) rather than ``if records else None`` -- the truthiness check
    # also calls ``__len__`` for list subclasses and would re-raise.
    summary = {
        "verdict": verdict,
        "state": state,
        "partial_success": partial_success,
        "chain_valid": chain_valid,
        "total_records": records_count,
        "issues_count": issues_count,
        "first_timestamp": records[0].get("timestamp") if has_records else None,
        "last_timestamp": records[-1].get("timestamp") if has_records else None,
        "first_actor": records[0].get("actor") if has_records else None,
        "audit_trail_path": str(path),
        # W607-EA: surface aggregation-LAYER signals on the envelope
        # summary so consumers can read the 4-tier classifier and rollup
        # metrics WITHOUT re-walking issues/records. Hash-stable on the
        # clean path: CHAIN_VERIFIED + {total_runs=N, verified_runs=N,
        # broken_runs=0, missing_signatures=0} is the deterministic
        # success projection.
        "chain_tier": chain_tier,
        "chain_rollup": rollup,
        "ea_verdict": ea_verdict,
        # Promote the unsigned-event count out of the nested rollup so a
        # consumer reading only ``summary`` (not ``summary.chain_rollup``)
        # still sees the evidence gap that ``verdict`` + ``partial_success``
        # disclose. 0 on a fully-signed chain (hash-stable success path).
        "unsigned_events": missing_sigs,
    }
    if _combined_warnings_out:
        summary["warnings_out"] = list(_combined_warnings_out)

    if json_mode:
        envelope_kwargs: dict = {
            "summary": summary,
            "issues": issues,
            "records": records_count,
        }
        if _combined_warnings_out:
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CN -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("audit-trail-verify", ...)`` would
        # otherwise crash AFTER all substrate + aggregation signals were
        # already gathered. Floor to a minimal envelope stub so consumers
        # still receive a parseable JSON object with the marker attached
        # + the canonical command name. Mirror of cmd_taint's W607-CJ
        # serialize_envelope floor pattern. Carry ``chain_valid`` +
        # ``state`` through to the floor so the 3-state matrix (W829)
        # survives a json_envelope raise on the floor path -- a consumer
        # parsing the floor stub still sees the broken/uninitialized
        # state vs. a clean SAFE.
        _envelope_floor: dict = {
            "command": "audit-trail-verify",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "state": state,
                "partial_success": True,
                "chain_valid": chain_valid,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        _envelope = _run_check_cn(
            "serialize_envelope",
            json_envelope,
            "audit-trail-verify",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-CN -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``audit_trail_verify_serialize_envelope_failed:`` marker was
        # appended to ``_w607cn_warnings_out`` and the floor stub carries
        # only the pre-raise combined list. Rebuild the floor stub's
        # warnings_out so the new marker reaches the JSON output. Clean
        # path -> envelope is the real json_envelope return value, no
        # rebuild needed.
        if _envelope is _envelope_floor and _w607cn_warnings_out:
            _combined_warnings_out = (
                list(_w607ai_warnings_out) + list(_w607cn_warnings_out) + list(_w607ea_warnings_out)
            )
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            _envelope = _envelope_floor

        # W607-EA -- ea_serialize_envelope boundary. Additive
        # ``json_envelope`` re-projection over the assembled envelope.
        # DISTINCT phase name from CN's ``serialize_envelope`` (W978 4th
        # discipline) so the per-phase marker prefix stays unambiguous.
        # No-op on the success path: the closure simply re-validates the
        # already-built ``_envelope`` and returns it. A raise here lands
        # a ``audit_trail_verify_ea_serialize_envelope_failed:`` marker
        # without disturbing the upstream CN envelope. Mirror of cmd_dead
        # W607-DL / cmd_pr_replay W607-DV serialize_envelope discipline.
        def _revalidate_envelope(_env: dict) -> dict:
            # Touch the envelope to confirm shape; any raise here floors
            # to the already-emitted CN envelope.
            _ = _env["command"]
            _ = _env["summary"]
            return _env

        _envelope = _run_check_ea(
            "ea_serialize_envelope",
            _revalidate_envelope,
            _envelope,
            default=_envelope,
        )
        # W607-EA -- if ``ea_serialize_envelope`` raised AFTER the
        # combined bucket was already snapshotted, the new
        # ``audit_trail_verify_ea_serialize_envelope_failed:`` marker
        # was appended to ``_w607ea_warnings_out``. Rebuild the
        # envelope's warnings_out so the new marker reaches the JSON
        # output. Bond-bug check (W607-DV finding): the top-level
        # warnings_out mirror AND summary-level mirror must BOTH carry
        # the late-phase marker; never break the byte-identical
        # empty-bucket envelope on the clean path.
        if _w607ea_warnings_out and isinstance(_envelope, dict):
            _combined_warnings_out = (
                list(_w607ai_warnings_out) + list(_w607cn_warnings_out) + list(_w607ea_warnings_out)
            )
            _envelope.setdefault("summary", {})
            if isinstance(_envelope["summary"], dict):
                _envelope["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope["warnings_out"] = list(_combined_warnings_out)

        click.echo(to_json(_envelope))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  path:    {path}")
        # W607-CN: use predicate-derived ``records_count`` here too so a
        # poisoned ``_BadChainState`` ``__len__`` raise doesn't crash the
        # text-mode rendering path.
        click.echo(f"  records: {records_count}")
        click.echo(f"  state:   {state}")
        # Disclose unsigned events in text mode too — the verdict already
        # names them, but a separate line keeps text/JSON parity for the
        # ``unsigned_events`` summary field.
        if missing_sigs > 0:
            click.echo(f"  unsigned: {missing_sigs} event(s) carry no integrity hash")
        if has_records:
            click.echo(f"  first:   {records[0].get('timestamp')}")
            click.echo(f"  last:    {records[-1].get('timestamp')}")
        # W607-CN: gate the issues block on the predicate-derived
        # ``issues_count`` rather than ``if issues:`` (truthiness uses
        # ``__len__`` on list subclasses) so a poisoned issues sentinel
        # doesn't crash text-mode rendering.
        if issues_count:
            click.echo()
            click.echo("Chain issues:")
            for i in issues[:10]:
                click.echo(f"  line {i['line']}: {i['issue']}")
                if "expected_prev" in i:
                    click.echo(f"    expected: {i['expected_prev']}")
                    click.echo(f"    computed: {i['computed_prev']}")
            if issues_count > 10:
                click.echo(f"  ... and {issues_count - 10} more (use --json for full list)")
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
