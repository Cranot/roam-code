"""roam cga — Code Graph Attestation (E.1).

Emits and verifies an in-toto v1 Statement with predicate type
``https://roam-code.com/spec/CodeGraph/v1`` over the indexed graph.

Supports unsigned attestations by default; pair with ``--sign``/
``--keyless`` for Cosign keyless signing (Fulcio + Rekor). The predicate
body is signature-format-agnostic, so the signing layer is a wrapper.

Examples
--------

    roam cga emit                            # write to .roam/attestations/<sha>.intoto.json
    roam cga emit --output - >cga.json       # stdout
    roam cga verify .roam/attestations/<sha>.intoto.json
    roam --json cga emit --no-write          # for piping

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cga outputs are code-graph attestation Statements — not
per-location violations. SARIF is reserved for findings with file:line
coordinates; cga's primary deliverable is the in-toto v1 Statement. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from roam.attest.cga import (
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    build_cga_statement,
    cosign_sign_statement,
    cosign_verify_statement,
    serialize_statement,
    verify_cga_statement,
)
from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


@roam_capability(
    name="cga",
    category="reports",
    summary="Code Graph Attestation: sign-ready in-toto evidence over the index",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "compliance"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=False,
    requires_index=True,
)
@click.group()
def cga():
    """Code Graph Attestation: sign-ready in-toto evidence over the index.

    Emits an in-toto v1 statement (predicate
    ``roam-code.com/spec/CodeGraph/v1``) covering symbols, edges, taint
    findings, and AIBOM material. Optionally cosign-signs the
    statement so auditors can verify the artifact later.

    \b
    Examples:
      roam cga emit
      roam cga emit --include-taint --aibom
      roam cga emit --sign --keyless
      roam cga verify .roam/attestations/abc123.intoto.json

    See also ``attest`` (proof-carrying PR attestation),
    ``audit-trail-verify`` (verify a stored artifact), and ``taint``
    (the source-to-sink findings cited inside the statement).
    """


def _default_output_path(project_root: Path, statement: dict) -> Path:
    sha = statement.get("subject", [{}])[0].get("digest", {}).get("git_commit_sha1", "unknown")
    short = sha[:12] if sha and sha != "unknown" else "unknown"
    out_dir = project_root / ".roam" / "attestations"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{short}.intoto.json"


@cga.command("emit")
@click.option(
    "--output",
    "output_path",
    type=str,
    default=None,
    help=("Write to this file. ``-`` writes to stdout. Default: ``.roam/attestations/<short_sha>.intoto.json``."),
)
@click.option(
    "--no-write",
    is_flag=True,
    help="Don't write to disk — useful with --json for piping.",
)
@click.option(
    "--include-taint",
    "include_taint",
    is_flag=True,
    help=(
        "Run the built-in taint rule pack and embed each finding as an "
        "OpenVEX-shaped reachability claim in the predicate. Sanitized "
        "paths map to status=not_affected with justification "
        "'inline_mitigations_already_exist'; unsanitized paths to "
        "status=affected. Closes the v12 compliance chain (taint feeds "
        "the CGA's VEX evidence)."
    ),
)
@click.option(
    "--taint-rules-dir",
    "taint_rules_dir",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Custom rules dir for --include-taint (default: built-in pack).",
)
@click.option(
    "--sign",
    is_flag=True,
    help=(
        "Sign the emitted statement with cosign. Requires the cosign "
        "binary on PATH (graceful skip with a clear message otherwise). "
        "Pair with --key for offline signing or --keyless for OIDC. "
        "Output: ``<stem>.sig`` and ``<stem>.bundle`` next to the "
        "statement file."
    ),
)
@click.option(
    "--key",
    "key_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Cosign private key (`cosign.key`). Pair with --sign.",
)
@click.option(
    "--keyless",
    is_flag=True,
    help=(
        "Keyless OIDC signing via Fulcio + Rekor. Requires ambient OIDC "
        "(GitHub Actions, GCP workload identity, etc.) or interactive "
        "browser flow."
    ),
)
@click.option(
    "--aibom",
    is_flag=True,
    help=(
        "Promote the predicate to ``CodeGraph-AIBOM/v1`` and embed an "
        "AIBOM block binding AI-authored commits to the indexed symbols "
        "they touched. Required for EU AI Act Art. 50 disclosure (effective "
        "2026-08-02) and the GPAI Code of Practice provenance mandate. "
        "Reference impl candidate for the in-toto attestation registry."
    ),
)
@click.option(
    "--allow-dirty",
    "allow_dirty",
    is_flag=True,
    help=(
        "Emit even when the working tree has uncommitted changes. Default "
        "behaviour refuses on a dirty tree because the resulting attestation "
        "binds to a commit SHA that doesn't reflect the analysed state. "
        "Pass this flag to record the dirty-hash in the predicate explicitly."
    ),
)
@click.option(
    "--also-vsa",
    "also_vsa",
    is_flag=True,
    help=(
        "W472: in addition to the CGA, emit a SLSA v1 Verification "
        "Summary Attestation (predicateType "
        "``https://slsa.dev/verification_summary/v1``) projected from "
        "the same ChangeEvidence that ``pr-bundle emit --slsa-l3`` "
        "would produce. The VSA lands next to the CGA at "
        "``<stem>.vsa.json``. Pair with ``--sign`` (and optionally "
        "``--keyless``/``--key``) to cosign-sign the VSA alongside "
        "the CGA. Gives teams that use ``roam cga emit`` directly "
        "(not ``pr-bundle``) parity with the SLSA-shaped output."
    ),
)
@click.pass_context
def cga_emit(
    ctx,
    output_path,
    no_write,
    include_taint,
    taint_rules_dir,
    sign,
    key_path,
    keyless,
    aibom,
    allow_dirty,
    also_vsa,
):
    """Emit a Code Graph Attestation (in-toto v1, optionally cosign-signed).

    With ``--aibom`` the predicate type promotes to
    ``roam-code.com/spec/CodeGraph-AIBOM/v1`` and the predicate gains an
    ``aibom`` block binding AI-authored commits to indexed symbols.

    With ``--also-vsa`` a sibling SLSA Verification Summary Attestation
    (predicateType ``slsa.dev/verification_summary/v1``) is written next
    to the CGA at ``<stem>.vsa.json``. The VSA is byte-identical to what
    ``pr-bundle emit --slsa-l3`` would produce on the same ChangeEvidence
    (W472). ``--sign`` covers both statements when both flags are set.

    NOTE (W487): the standalone CGA emit path produces a Code Graph
    Attestation but CANNOT achieve SLSA Source Track L3 by itself — a
    CGA alone is not a Verification Summary Attestation, so the
    supply-chain claim falls short of SRC-L3 requirements. For the
    canonical SRC-L3 path, pass ``--also-vsa`` (W472) to emit a sibling
    VSA alongside the CGA, or use ``roam pr-bundle emit --slsa-l3``
    (W451) to wire the same VSA through the proof-bundle pipeline.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    ensure_index()
    project_root = find_project_root()

    # W607-AF -- substrate-boundary plumbing for the cryptographic-attestation
    # triad (cmd_attest + cmd_pr_bundle + cmd_cga). Prior to W607-AF a raise
    # inside any of build_cga_statement / serialize_statement /
    # cosign_sign_statement / atomic_write_text / emit_vsa_sibling crashed the
    # whole CGA emit path wholesale. Each is wrapped via _run_check_af so a
    # raise becomes a structured
    # ``cga_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607af_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # cmd_cga is the cryptographic core of the W805 cross-artifact-consistency
    # family (CGA / VSA / Rekor pipeline) -- the W607-AF markers fire AT
    # RUNTIME when an emission boundary raises, complementing the W805
    # xfail-strict pins that catch structural inconsistency at the dataclass
    # level.
    #
    # Marker prefix discipline: every W607-AF substrate marker uses the
    # canonical ``cga_<phase>_failed:<exc_class>:<detail>`` shape. cmd_cga
    # has NO pre-existing W607 plumbing (fresh-template wave) so a single
    # bucket + single helper applies.
    _w607af_warnings_out: list[str] = []

    def _run_check_af(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AF marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``cga_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607af_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607af_warnings_out.append(f"cga_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BZ -- ADDITIVE aggregation-phase plumbing on top of the W607-AF
    # substrate-CALL markers. W607-AF already wrapped the 7 substrate-helper
    # boundaries on the emit path (git_dirty_hash / run_taint /
    # build_cga_statement / serialize_statement / atomic_write_text /
    # cosign_sign_statement / emit_vsa_sibling); W607-BZ extends marker
    # coverage to the AGGREGATION-PHASE boundaries that W607-AF left
    # unguarded:
    #
    #   - ``compute_predicate``    -- per-field extraction of predicate
    #                                 fields (symbol_count / edge_count /
    #                                 merkle_root / reachability_claims)
    #                                 used to compose the verdict string.
    #                                 A future predicate-schema refactor
    #                                 that drops or renames one of these
    #                                 keys would otherwise crash the
    #                                 envelope post-build.
    #   - ``compute_verdict``      -- verdict string assembly. Floor to a
    #                                 literal "CGA emit completed"
    #                                 string per LAW 6 (standalone-parse)
    #                                 + W978 first-hypothesis discipline
    #                                 (no re-interpolation of the same
    #                                 values that just raised).
    #   - ``auto_log``             -- active-run ledger write. Silent
    #                                 no-op when no run is active, but
    #                                 the underlying ``auto_log`` can
    #                                 still raise on HMAC chain misshape
    #                                 or filesystem failures. Mirror of
    #                                 cmd_attest W607-BT auto_log pattern.
    #   - ``serialize_envelope``   -- ``json_envelope("cga-emit", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions).
    #
    # cmd_cga is the CRYPTOGRAPHIC core of the W805 cross-artifact-
    # consistency family (CGA / VSA / Rekor pipeline). Closes the
    # attestation triad together with W607-AD/BT (cmd_attest) and
    # W607-AE/BW (cmd_pr_bundle). The W607-BZ markers fire AT RUNTIME
    # when an aggregation-phase boundary raises, complementing the W805
    # xfail-strict pins that catch structural inconsistency at the
    # dataclass level.
    #
    # Marker family ``cga_*`` -- same family as W607-AF (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope on the
    # success path.
    _w607bz_warnings_out: list[str] = []

    def _run_check_bz(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-BZ marker emission.

        Mirror of ``_run_check_af`` shape (same ``cga_<phase>_failed:``
        marker family) but writes into ``_w607bz_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bz_warnings_out.append(f"cga_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # Refuse on dirty tree by default. The attestation binds to a commit
    # SHA — emitting on uncommitted state produces a misleading receipt.
    # --allow-dirty opts in (still records the dirty-hash in the predicate).
    if not allow_dirty:
        from roam.attest.cga import _git_dirty_hash

        dirty = _run_check_af("git_dirty_hash", _git_dirty_hash, project_root, default=None)
        if dirty is not None:
            from roam.output.errors import DIRTY_TREE, structured_usage_error

            raise structured_usage_error(
                DIRTY_TREE,
                "working tree has uncommitted changes — refusing to emit a CGA "
                "that binds to a commit SHA but reflects un-committed state. "
                "Either commit / stash your changes, or pass --allow-dirty to "
                "record the dirty-hash in the predicate explicitly.",
            )

    taint_findings = None
    # W489-A-followup: stamp the W454/W479 `qualified_only` lint result
    # on the cga envelope so out-of-tree taint-rule packs loaded via
    # ``--taint-rules-dir`` get the same disclosure ``roam taint`` ships.
    # Mirror the cmd_taint envelope shape byte-for-byte to keep
    # drift-guards stable (the W489-A canonical shape is the source of
    # truth; this is the second consumer).
    _w489_a_violations: list[dict] = []
    _w489_a_total_rules = 0
    if include_taint:
        from roam.security.taint_engine import run_taint
        from roam.security.taint_rules_lint import capture_qualified_only_lint

        # W643: route the default rules-dir through ``cmd_taint``'s
        # importlib.resources-aware helper so wheel installs resolve the
        # bundled directory canonically (mirrors W554/W570/W577/W624).
        if taint_rules_dir:
            rules_path = Path(taint_rules_dir)
        else:
            from roam.commands.cmd_taint import _default_rules_dir as _taint_default_rules_dir

            rules_path = _taint_default_rules_dir()
        # W489-A-followup: capture qualified_only lint warnings alongside
        # the loaded rules — advisory disclosure, never gates execution.
        rules, _w489_a_violations = capture_qualified_only_lint(rules_path)
        _w489_a_total_rules = len(rules)

    with open_db(readonly=True) as conn:
        if include_taint:
            taint_findings = _run_check_af("run_taint", run_taint, conn, rules, default=[])
        statement = _run_check_af(
            "build_cga_statement",
            build_cga_statement,
            conn,
            project_root=project_root,
            taint_findings=taint_findings,
            include_aibom=aibom,
            default=None,
        )

    if statement is None:
        # Pattern 1C / Pattern 1D: build_cga_statement raised. Emit a
        # structured envelope that discloses the substrate failure rather
        # than crashing the whole emit path. The W607-AF marker on
        # ``_w607af_warnings_out`` carries the actionable diagnostic.
        _build_failed_envelope = json_envelope(
            "cga-emit",
            summary={
                "verdict": "CGA emit aborted: build_cga_statement substrate raised",
                "state": "build_failed",
                "partial_success": True,
                "warnings_out": list(_w607af_warnings_out),
            },
            warnings_out=list(_w607af_warnings_out),
            partial_success=True,
        )
        if json_mode:
            click.echo(to_json(_build_failed_envelope))
            return
        click.echo("VERDICT: CGA emit aborted -- build_cga_statement substrate raised")
        for marker in _w607af_warnings_out:
            click.echo(f"  - {marker}")
        return

    canonical = _run_check_af("serialize_statement", serialize_statement, statement, default="")

    written_to: str | None = None
    written_path: Path | None = None
    if not no_write:
        if output_path == "-":
            click.echo(canonical)
            written_to = "stdout"
        else:
            target = Path(output_path) if output_path else _default_output_path(project_root, statement)
            # Atomic write: attestations are cryptographically chained — a
            # torn file mid-write breaks downstream signature verification
            # (R28 substrate flagged this as ``unsafe_mutation``).
            from roam.atomic_io import atomic_write_text

            _write_ok = _run_check_af("atomic_write_text", atomic_write_text, target, canonical + "\n", default=None)
            written_to = str(target)
            written_path = target if _write_ok is not False else None

    sign_result = None
    if sign:
        if no_write or output_path == "-" or written_path is None:
            sign_result = {
                "signed": False,
                "skipped_reason": "cannot sign without a written statement file",
            }
        else:
            cresult = _run_check_af(
                "cosign_sign_statement",
                cosign_sign_statement,
                written_path,
                key_path=Path(key_path) if key_path else None,
                keyless=keyless,
                default=None,
            )
            if cresult is None:
                sign_result = {
                    "signed": False,
                    "skipped_reason": "cosign_sign_statement substrate raised (see warnings_out)",
                }
            else:
                sign_result = {
                    "signed": cresult.signed,
                    "statement_path": str(cresult.statement_path),
                    "signature_path": str(cresult.signature_path) if cresult.signature_path else None,
                    "bundle_path": str(cresult.bundle_path) if cresult.bundle_path else None,
                    "certificate_path": str(cresult.certificate_path) if cresult.certificate_path else None,
                    "skipped_reason": cresult.skipped_reason,
                    "cosign_version": cresult.cosign_version,
                }

    # W472 - optional SLSA VSA sibling, mirroring the pr-bundle
    # --slsa-l3 wiring at cmd_pr_bundle.py:_emit_slsa_l3_attestations.
    # Refuses gracefully (records a skipped reason, never crashes the
    # emit path) when the prerequisites aren't met.
    vsa_result: dict | None = None
    if also_vsa:
        vsa_result = _run_check_af(
            "emit_vsa_sibling",
            _emit_vsa_sibling,
            statement=statement,
            written_path=written_path,
            written_to=written_to,
            no_write=no_write,
            project_root=project_root,
            sign=sign,
            key_path=key_path,
            keyless=keyless,
            default=None,
        )

    pred = statement["predicate"]

    # W607-BZ -- compute_predicate boundary. Wraps the predicate-field
    # extraction so a future schema refactor that drops/renames keys
    # surfaces a marker rather than crashing the envelope. Floor to
    # documented empty-shape ints / lists matching the happy-path return
    # so downstream verdict/summary fields stay non-null.
    def _compute_predicate_fields(pred_local: dict) -> dict:
        claims_local = pred_local.get("reachability_claims") or []
        n_claims_local = len(claims_local)
        n_sanitized_local = sum(1 for c in claims_local if c.get("status") == "not_affected")
        return {
            "symbol_count": pred_local["symbol_count"],
            "edge_count": pred_local["edge_count"],
            "merkle_root": pred_local["merkle_root"],
            "n_claims": n_claims_local,
            "n_sanitized": n_sanitized_local,
        }

    _pred_fields = _run_check_bz(
        "compute_predicate",
        _compute_predicate_fields,
        pred,
        default={
            "symbol_count": 0,
            "edge_count": 0,
            "merkle_root": "",
            "n_claims": 0,
            "n_sanitized": 0,
        },
    )

    # W607-BZ -- compute_verdict boundary. Wraps the verdict-string
    # assembly so a downstream f-string refactor (e.g. a non-string
    # merkle_root from a vocabulary refactor) surfaces a marker rather
    # than crashing the envelope. Floor must NOT re-interpolate the
    # same values that tripped the closure (W978 first-hypothesis
    # discipline: a __format__-raising sentinel under test would
    # re-raise inside the default f-string). Use a literal
    # ``"CGA emit completed"`` floor instead (LAW 6 still holds: the
    # line works standalone). Mirror of cmd_attest W607-BT
    # compute_verdict pattern.
    def _build_verdict_str(fields: dict) -> str:
        claim_summary_local = (
            f", {fields['n_claims']} reachability claim(s) ({fields['n_sanitized']} sanitized)"
            if fields["n_claims"]
            else ""
        )
        merkle_local = fields["merkle_root"]
        merkle_short = merkle_local[:12] if merkle_local else ""
        return (
            f"CGA emitted: {fields['symbol_count']} symbols / "
            f"{fields['edge_count']} edges, merkle={merkle_short}…"
            f"{claim_summary_local}"
        )

    verdict = _run_check_bz(
        "compute_verdict",
        _build_verdict_str,
        _pred_fields,
        default="CGA emit completed",
    )

    if json_mode:
        # W489-A-followup: stamp the qualified_only lint result on the
        # cga-emit envelope. ``rules_lint`` is emitted symmetrically
        # whenever ``--include-taint`` actually loaded rules (per
        # W1101/W1006); the top-level ``qualified_only_violations`` list
        # only appears when N > 0 (W1006 redactions[] precedent for
        # content lists). Shape mirrors cmd_taint's envelope byte-for-byte.
        # W607-BZ -- accessors are safe-floored via ``.get()`` so a
        # malformed predicate (missing keys) caught by the
        # compute_predicate wrap above doesn't trip a KeyError post-
        # disclosure. The W607-BZ marker is already on the bucket; the
        # envelope still emits with documented empty-floor values so
        # consumers see the degradation lineage rather than empty stdout.
        _w489_a_summary: dict = {
            "verdict": verdict,
            "merkle_root": pred.get("merkle_root", ""),
            "edge_bundle_digest": pred.get("edge_bundle_digest", ""),
            "symbol_count": pred.get("symbol_count", 0),
            "edge_count": pred.get("edge_count", 0),
            "predicate_type": statement.get("predicateType", PREDICATE_TYPE),
            "statement_type": STATEMENT_TYPE,
            "written_to": written_to,
            "signed": bool(sign_result and sign_result.get("signed")),
            "vsa_emitted": bool(vsa_result and vsa_result.get("vsa_path")),
        }
        _w489_a_envelope_extra: dict = {
            "budget": token_budget,
            "statement": statement,
            "sign_result": sign_result,
            "vsa_result": vsa_result,
        }
        # W489-A pre-existing bucket: qualified_only lint flag (rules-shape
        # disclosure). W607-AF bucket: substrate-CALL markers (helper raised).
        # Both axes feed the SAME ``summary.warnings_out`` field on emission;
        # the marker PREFIX disambiguates them downstream
        # (``qualified_only lint flagged ...`` vs ``cga_<phase>_failed:*``).
        # ``partial_success`` flips when EITHER bucket is non-empty.
        _w489_a_lint_warnings: list[str] = []
        if include_taint:
            _w489_a_summary["rules_lint"] = {
                "qualified_only_violations": len(_w489_a_violations),
                "total_rules": _w489_a_total_rules,
            }
            if _w489_a_violations:
                _w489_a_lint_warnings.append(
                    f"qualified_only lint flagged {len(_w489_a_violations)} bare-name violations"
                )
                _w489_a_envelope_extra["qualified_only_violations"] = _w489_a_violations
        # W607-BZ -- ADDITIVE aggregation-phase markers join the combined
        # channel: W489-A lint + W607-AF substrate-CALL + W607-BZ
        # aggregation-phase. All three buckets share the canonical
        # ``cga_*`` family (W489-A uses prose strings; the W607-* buckets
        # use the ``cga_<phase>_failed:`` shape). The additive bucket
        # stays distinguishable in tests + audits via its phase names
        # (``compute_predicate`` / ``compute_verdict`` / ``auto_log`` /
        # ``serialize_envelope``).
        _combined_warnings_out: list[str] = (
            list(_w489_a_lint_warnings) + list(_w607af_warnings_out) + list(_w607bz_warnings_out)
        )
        if _combined_warnings_out:
            _w489_a_summary["partial_success"] = True
            _w489_a_summary["warnings_out"] = list(_combined_warnings_out)
            # W607-AF / W607-BZ top-level mirror so consumers reading the
            # envelope head (without descending into ``summary``) see the
            # marker channel.
            _w489_a_envelope_extra["warnings_out"] = list(_combined_warnings_out)
            _w489_a_envelope_extra["partial_success"] = True

        # W607-BZ -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("cga-emit", ...)`` would otherwise crash
        # AFTER all substrate + aggregation signals were already gathered.
        # Floor to a minimal envelope stub so consumers still receive a
        # parseable JSON object with the marker attached + the canonical
        # command name. Mirror of cmd_attest's W607-BT
        # serialize_envelope floor pattern.
        _envelope_floor: dict = {
            "command": "cga-emit",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        cga_envelope = _run_check_bz(
            "serialize_envelope",
            json_envelope,
            "cga-emit",
            default=_envelope_floor,
            summary=_w489_a_summary,
            **_w489_a_envelope_extra,
        )
        # W607-BZ -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``cga_serialize_envelope_failed:`` marker was appended to
        # ``_w607bz_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output. Clean path ->
        # envelope is the real json_envelope return value, no rebuild
        # needed.
        if cga_envelope is _envelope_floor and _w607bz_warnings_out:
            _combined_warnings_out = (
                list(_w489_a_lint_warnings) + list(_w607af_warnings_out) + list(_w607bz_warnings_out)
            )
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            cga_envelope = _envelope_floor

        # W607-BZ -- auto_log boundary. Silent no-op if no active run;
        # the wrap surfaces HMAC chain-misshape / filesystem failures as
        # ``cga_auto_log_failed:...`` markers instead of crashing the
        # envelope after it was already built. Mirror of cmd_attest's
        # W607-BT auto_log pattern.
        _run_check_bz(
            "auto_log",
            auto_log,
            cga_envelope,
            action="cga-emit",
            target=written_to or "no-write",
            repo_root=project_root,
            default=None,
        )
        # W607-BZ -- if ``auto_log`` raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log)
        # -> envelope stays byte-identical to the version already built
        # above.
        _had_pre_auto_log_serialize = any(
            m.startswith("cga_serialize_envelope_failed:") for m in (_w489_a_summary.get("warnings_out") or [])
        )
        if (
            _w607bz_warnings_out
            and any(m.startswith("cga_auto_log_failed:") for m in _w607bz_warnings_out)
            and not any(m.startswith("cga_auto_log_failed:") for m in (_w489_a_summary.get("warnings_out") or []))
        ):
            _combined_warnings_out = (
                list(_w489_a_lint_warnings) + list(_w607af_warnings_out) + list(_w607bz_warnings_out)
            )
            _w489_a_summary["warnings_out"] = list(_combined_warnings_out)
            _w489_a_summary["partial_success"] = True
            _w489_a_envelope_extra["warnings_out"] = list(_combined_warnings_out)
            _w489_a_envelope_extra["partial_success"] = True
            # Re-serialize only if the prior serialize succeeded; if it
            # already raised, keep the floor stub but update warnings_out.
            if not _had_pre_auto_log_serialize:
                cga_envelope = _run_check_bz(
                    "serialize_envelope",
                    json_envelope,
                    "cga-emit",
                    default=_envelope_floor,
                    summary=_w489_a_summary,
                    **_w489_a_envelope_extra,
                )
            else:
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                cga_envelope = _envelope_floor

        click.echo(to_json(cga_envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Predicate type: {statement.get('predicateType', PREDICATE_TYPE)}")
    click.echo(f"Statement type: {STATEMENT_TYPE}")
    click.echo(f"Languages:      {', '.join(pred['languages']) or '(none)'}")
    if written_to:
        click.echo(f"Written to:     {written_to}")
    if sign_result:
        if sign_result["signed"]:
            click.echo(f"Signed by:      cosign {sign_result['cosign_version']}")
            click.echo(f"  signature:    {sign_result['signature_path']}")
            click.echo(f"  bundle:       {sign_result['bundle_path']}")
        else:
            click.echo(f"Signing skipped: {sign_result['skipped_reason']}")
    if vsa_result:
        if vsa_result.get("vsa_path"):
            click.echo(f"VSA:            {vsa_result['vsa_path']}")
            vsa_sig = vsa_result.get("sign_result") or {}
            if vsa_sig.get("signed"):
                click.echo(f"  VSA signed:   cosign {vsa_sig.get('cosign_version')}")
                click.echo(f"  VSA bundle:   {vsa_sig.get('bundle_path')}")
            elif vsa_sig.get("skipped_reason"):
                click.echo(f"  VSA sign skipped: {vsa_sig['skipped_reason']}")
        else:
            reasons = vsa_result.get("skipped_reasons") or ["unknown"]
            click.echo(f"VSA skipped:    {'; '.join(reasons)}")
    click.echo()
    click.echo(f"Verify with:    roam cga verify {written_to or '<file>'}")


def _emit_vsa_sibling(
    *,
    statement: dict,
    written_path: Path | None,
    written_to: str | None,
    no_write: bool,
    project_root: Path,
    sign: bool,
    key_path: str | None,
    keyless: bool,
) -> dict:
    """W472 — emit a sibling SLSA VSA next to the freshly-written CGA.

    W486: delegates to the shared
    :func:`roam.attest.emit_vsa.emit_cga_vsa_sibling` helper. The wrapper
    stays in place so existing callers keep the same import path, and to
    localise any future cga-specific pre/post-processing.

    Output is byte-identical to what ``pr-bundle emit --slsa-l3`` would
    produce on the same evidence (parity contract guarded by
    ``tests/test_attest_vsa.py``).
    """
    from roam.attest.emit_vsa import emit_cga_vsa_sibling

    return emit_cga_vsa_sibling(
        statement=statement,
        written_path=written_path,
        written_to=written_to,
        no_write=no_write,
        project_root=project_root,
        sign=sign,
        key_path=key_path,
        keyless=keyless,
    )


@cga.command("verify")
@click.argument(
    "statement_path",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--cosign-bundle",
    "cosign_bundle",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Cosign bundle file (auto-detected as ``<stem>.bundle`` next to "
        "the statement when omitted). Verifies the cosign signature "
        "alongside the predicate digest match."
    ),
)
@click.option(
    "--cosign-key",
    "cosign_key",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Public key for cosign verification (offline mode).",
)
@click.option(
    "--no-cosign",
    is_flag=True,
    help="Skip cosign verification even if a sibling .bundle exists.",
)
@click.option(
    "--cert-identity",
    "cert_identity",
    type=str,
    default=None,
    envvar="ROAM_CGA_CERT_IDENTITY",
    help=(
        "Expected signer identity for keyless cosign verification "
        "(passed to ``cosign verify-blob --certificate-identity``). "
        "For GitHub Actions OIDC this is the workflow path, e.g. "
        "``https://github.com/<owner>/<repo>/.github/workflows/<wf>.yml@refs/heads/main``. "
        "Cosign >= 2.0 requires this for keyless verification. "
        "Also reads ``ROAM_CGA_CERT_IDENTITY`` from env."
    ),
)
@click.option(
    "--cert-oidc-issuer",
    "cert_oidc_issuer",
    type=str,
    default=None,
    envvar="ROAM_CGA_CERT_OIDC_ISSUER",
    help=(
        "Expected OIDC issuer for keyless cosign verification "
        "(passed to ``cosign verify-blob --certificate-oidc-issuer``). "
        "For GitHub Actions this is "
        "``https://token.actions.githubusercontent.com``. "
        "Cosign >= 2.0 requires this for keyless verification. "
        "Also reads ``ROAM_CGA_CERT_OIDC_ISSUER`` from env."
    ),
)
@click.pass_context
def cga_verify(ctx, statement_path, cosign_bundle, cosign_key, no_cosign, cert_identity, cert_oidc_issuer):
    """Re-derive the merkle root + edge digest and compare to *statement_path*.

    Verifier exits 0 on a clean match. Any mismatch (changed symbols,
    different commit, edited edges) exits 5 — CI-gateable.

    Keyless verification (no ``--cosign-key``) requires both
    ``--cert-identity`` and ``--cert-oidc-issuer`` because cosign >= 2.0
    refuses to verify keyless signatures without an explicit signer-
    identity check. Offline keypair mode (``--cosign-key cosign.pub``)
    does not need either flag.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    raw = Path(statement_path).read_text(encoding="utf-8")
    try:
        statement = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        from roam.output.errors import INVALID_FORMAT, structured_usage_error

        raise structured_usage_error(INVALID_FORMAT, f"attestation is not valid JSON: {exc}") from exc

    # W607-AF -- substrate-boundary plumbing for the verify path. cmd_cga
    # verify wraps verify_cga_statement and cosign_verify_statement so a
    # raise inside either substrate becomes a structured
    # ``cga_<phase>_failed:<exc_class>:<detail>`` marker rather than crashing
    # the verifier. The fresh-template single-bucket pattern applies here too
    # (no pre-existing W607 plumbing on the verify subcommand).
    _w607af_warnings_out: list[str] = []

    def _run_check_af(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AF marker emission (verify)."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607af_warnings_out.append(f"cga_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    ensure_index()
    project_root = find_project_root()
    with open_db(readonly=True) as conn:
        _verify_result = _run_check_af(
            "verify_cga_statement",
            verify_cga_statement,
            statement,
            conn,
            project_root=project_root,
            default=(False, ["verify_cga_statement substrate raised (see warnings_out)"]),
        )
        ok, errors = _verify_result

    # Auto-detect a sibling bundle if not explicit.
    sp = Path(statement_path)
    if not no_cosign and not cosign_bundle:
        sibling = sp.with_suffix(".bundle")
        if not sibling.exists():
            sibling = sp.parent / (sp.stem + ".bundle")
        if sibling.exists():
            cosign_bundle = str(sibling)

    cosign_result: dict | None = None
    cosign_skipped_reason: str | None = None
    if no_cosign:
        # User explicitly opted out of cosign — predicate-only verdict.
        cosign_skipped_reason = "skipped per --no-cosign"
    elif cosign_bundle:
        # Keyless (no --cosign-key) needs --cert-identity + --cert-oidc-issuer
        # under cosign >= 2.0. Refuse loudly rather than letting cosign emit
        # its own confusing error message buried in our envelope.
        if not cosign_key and (not cert_identity or not cert_oidc_issuer):
            ok = False
            missing = []
            if not cert_identity:
                missing.append("--cert-identity")
            if not cert_oidc_issuer:
                missing.append("--cert-oidc-issuer")
            errors = list(errors) + [
                f"keyless cosign verify requires {' and '.join(missing)} "
                "(or set ROAM_CGA_CERT_IDENTITY / ROAM_CGA_CERT_OIDC_ISSUER); "
                "for GitHub Actions OIDC pass the workflow-path identity and "
                "https://token.actions.githubusercontent.com as the issuer"
            ]
            cosign_result = {
                "verified": False,
                "bundle_path": cosign_bundle,
                "message": "keyless verify refused: cert-identity / cert-oidc-issuer missing",
            }
        else:
            cosign_ok, message = _run_check_af(
                "cosign_verify_statement",
                cosign_verify_statement,
                sp,
                bundle_path=Path(cosign_bundle) if cosign_bundle else None,
                public_key_path=Path(cosign_key) if cosign_key else None,
                certificate_identity=cert_identity,
                certificate_oidc_issuer=cert_oidc_issuer,
                default=(False, "cosign_verify_statement substrate raised (see warnings_out)"),
            )
            cosign_result = {
                "verified": cosign_ok,
                "bundle_path": cosign_bundle,
                "message": message[:300],
            }
            if not cosign_ok:
                errors = list(errors) + [f"cosign verification failed: {message[:200]}"]
                ok = False
    else:
        # FAIL CLOSED — load-bearing claim is "tamper-evident". Silently
        # passing the predicate-only check while skipping cosign cryptography
        # would let a downloaded statement read "verified" with no real
        # signer-identity check. Force the user to acknowledge.
        ok = False
        errors = list(errors) + [
            "cosign bundle not found alongside statement; pass --no-cosign "
            "to acknowledge predicate-only verification, or --cosign-bundle PATH "
            "to point at the bundle explicitly"
        ]

    if ok:
        if cosign_result and cosign_result["verified"]:
            verdict = "CGA verified — predicate matches live index + cosign"
        elif cosign_skipped_reason:
            verdict = f"CGA verified — predicate matches live index (cosign {cosign_skipped_reason})"
        else:
            verdict = "CGA verified — predicate matches live index"
    else:
        verdict = f"CGA mismatch: {len(errors)} error(s)"

    if json_mode:
        _verify_summary: dict = {
            "verdict": verdict,
            "ok": ok,
            "error_count": len(errors),
            "cosign_verified": bool(cosign_result and cosign_result["verified"]),
        }
        _verify_envelope_extra: dict = dict(
            errors=errors,
            statement_path=str(statement_path),
            cosign=cosign_result,
        )
        if _w607af_warnings_out:
            _verify_summary["partial_success"] = True
            _verify_summary["warnings_out"] = list(_w607af_warnings_out)
            _verify_envelope_extra["warnings_out"] = list(_w607af_warnings_out)
            _verify_envelope_extra["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "cga-verify",
                    summary=_verify_summary,
                    **_verify_envelope_extra,
                )
            )
        )
        if not ok:
            ctx.exit(5)
        return

    click.echo(f"VERDICT: {verdict}")
    if cosign_result:
        if cosign_result["verified"]:
            click.echo(f"Cosign:    verified ({cosign_result['bundle_path']})")
        else:
            click.echo(f"Cosign:    FAILED — {cosign_result['message']}")
    if errors:
        click.echo()
        for err in errors:
            click.echo(f"  - {err}")
        ctx.exit(5)
