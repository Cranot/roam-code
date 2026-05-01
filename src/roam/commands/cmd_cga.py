"""roam cga — Code Graph Attestation (E.1 v12.0 scaffold).

Emits and verifies an in-toto v1 Statement with predicate type
``https://roam-code.dev/CodeGraph/v1`` over the indexed graph.

v12.0 ships unsigned attestations only. v12.1 layers cosign keyless
signing on top (the predicate body is signature-format-agnostic, so
adding `cosign attest-blob` later is a wrapper, not a redesign).

Examples
--------

    roam cga emit                            # write to .roam/attestations/<sha>.intoto.json
    roam cga emit --output - >cga.json       # stdout
    roam cga verify .roam/attestations/<sha>.intoto.json
    roam --json cga emit --no-write          # for piping
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
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json


@click.group()
def cga():
    """Code Graph Attestation — sign-ready in-toto evidence over the index."""


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
@click.pass_context
def cga_emit(ctx, output_path, no_write, include_taint, taint_rules_dir, sign, key_path, keyless, aibom):
    """Emit a Code Graph Attestation (in-toto v1, optionally cosign-signed).

    With ``--aibom`` the predicate type promotes to
    ``roam-code.dev/CodeGraph-AIBOM/v1`` and the predicate gains an
    ``aibom`` block binding AI-authored commits to indexed symbols.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    ensure_index()
    project_root = find_project_root()

    taint_findings = None
    if include_taint:
        from roam.security.taint_engine import load_rules, run_taint

        rules_path = (
            Path(taint_rules_dir)
            if taint_rules_dir
            else Path(__file__).resolve().parents[1] / "security" / "taint_rules"
        )
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
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(canonical + "\n", encoding="utf-8")
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
                    },
                    budget=token_budget,
                    statement=statement,
                    sign_result=sign_result,
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
    click.echo()
    click.echo(f"Verify with:    roam cga verify {written_to or '<file>'}")


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
        raise click.UsageError(f"attestation is not valid JSON: {exc}") from exc

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
    if cosign_bundle and not no_cosign:
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

    verdict = (
        "CGA verified — predicate matches live index"
        + (" + cosign" if cosign_result and cosign_result["verified"] else "")
        if ok
        else f"CGA mismatch: {len(errors)} error(s)"
    )

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
