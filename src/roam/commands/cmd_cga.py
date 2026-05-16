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
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    ensure_index()
    project_root = find_project_root()

    # Refuse on dirty tree by default. The attestation binds to a commit
    # SHA — emitting on uncommitted state produces a misleading receipt.
    # --allow-dirty opts in (still records the dirty-hash in the predicate).
    if not allow_dirty:
        from roam.attest.cga import _git_dirty_hash

        dirty = _git_dirty_hash(project_root)
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
    if include_taint:
        from roam.security.taint_engine import load_rules, run_taint

        # W643: route the default rules-dir through ``cmd_taint``'s
        # importlib.resources-aware helper so wheel installs resolve the
        # bundled directory canonically (mirrors W554/W570/W577/W624).
        if taint_rules_dir:
            rules_path = Path(taint_rules_dir)
        else:
            from roam.commands.cmd_taint import _default_rules_dir as _taint_default_rules_dir

            rules_path = _taint_default_rules_dir()
        rules = load_rules(rules_path)

    with open_db(readonly=True) as conn:
        if include_taint:
            taint_findings = run_taint(conn, rules)
        statement = build_cga_statement(
            conn,
            project_root=project_root,
            taint_findings=taint_findings,
            include_aibom=aibom,
        )

    canonical = serialize_statement(statement)

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

            atomic_write_text(target, canonical + "\n")
            written_to = str(target)
            written_path = target

    sign_result = None
    if sign:
        if no_write or output_path == "-" or written_path is None:
            sign_result = {
                "signed": False,
                "skipped_reason": "cannot sign without a written statement file",
            }
        else:
            cresult = cosign_sign_statement(
                written_path,
                key_path=Path(key_path) if key_path else None,
                keyless=keyless,
            )
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
        vsa_result = _emit_vsa_sibling(
            statement=statement,
            written_path=written_path,
            written_to=written_to,
            no_write=no_write,
            project_root=project_root,
            sign=sign,
            key_path=key_path,
            keyless=keyless,
        )

    pred = statement["predicate"]
    n_claims = len(pred.get("reachability_claims") or [])
    n_sanitized = sum(1 for c in (pred.get("reachability_claims") or []) if c.get("status") == "not_affected")
    claim_summary = f", {n_claims} reachability claim(s) ({n_sanitized} sanitized)" if n_claims else ""
    verdict = (
        f"CGA emitted: {pred['symbol_count']} symbols / "
        f"{pred['edge_count']} edges, merkle={pred['merkle_root'][:12]}…"
        f"{claim_summary}"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "cga-emit",
                    summary={
                        "verdict": verdict,
                        "merkle_root": pred["merkle_root"],
                        "edge_bundle_digest": pred["edge_bundle_digest"],
                        "symbol_count": pred["symbol_count"],
                        "edge_count": pred["edge_count"],
                        "predicate_type": statement.get("predicateType", PREDICATE_TYPE),
                        "statement_type": STATEMENT_TYPE,
                        "written_to": written_to,
                        "signed": bool(sign_result and sign_result.get("signed")),
                        "vsa_emitted": bool(vsa_result and vsa_result.get("vsa_path")),
                    },
                    budget=token_budget,
                    statement=statement,
                    sign_result=sign_result,
                    vsa_result=vsa_result,
                )
            )
        )
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
@click.pass_context
def cga_verify(ctx, statement_path, cosign_bundle, cosign_key, no_cosign):
    """Re-derive the merkle root + edge digest and compare to *statement_path*.

    Verifier exits 0 on a clean match. Any mismatch (changed symbols,
    different commit, edited edges) exits 5 — CI-gateable.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    raw = Path(statement_path).read_text(encoding="utf-8")
    try:
        statement = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        from roam.output.errors import INVALID_FORMAT, structured_usage_error

        raise structured_usage_error(INVALID_FORMAT, f"attestation is not valid JSON: {exc}") from exc

    ensure_index()
    project_root = find_project_root()
    with open_db(readonly=True) as conn:
        ok, errors = verify_cga_statement(statement, conn, project_root=project_root)

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
        cosign_ok, message = cosign_verify_statement(
            sp,
            bundle_path=Path(cosign_bundle) if cosign_bundle else None,
            public_key_path=Path(cosign_key) if cosign_key else None,
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
        click.echo(
            to_json(
                json_envelope(
                    "cga-verify",
                    summary={
                        "verdict": verdict,
                        "ok": ok,
                        "error_count": len(errors),
                        "cosign_verified": bool(cosign_result and cosign_result["verified"]),
                    },
                    errors=errors,
                    statement_path=str(statement_path),
                    cosign=cosign_result,
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
