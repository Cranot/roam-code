"""``roam suppress`` — record an audit-trail-friendly finding suppression.

Use after manually verifying a finding is a false positive. Records the
suppression in ``.roam/suppressions.json`` keyed by the finding ID
(stable hash of task_id + location + symbol_name). Future detector
runs flag the matched finding with ``suppressed: {source: …, reason: …}``
instead of dropping it silently — so a future code change that
invalidates the suppression still surfaces in JSON output.

Companion paths to per-file inline annotations + ``.roamignore-findings``
(see :mod:`roam.commands.finding_suppress`).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam suppress`` operates on substrate state in ``.roam/``
(suppression records) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.finding_suppress import DEFAULT_SUPPRESSIONS_PATH
from roam.output.formatter import json_envelope, to_json


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _load_findings_from_envelope_path(path: str) -> list[dict]:
    """D7 — load `findings: [...]` from a JSON envelope on disk or stdin.

    `-` as the path reads stdin so the common pipeline works:
      ``roam --json math | roam suppress _ --from-finding -``.
    Returns the list verbatim; callers filter and re-key to finding_id.
    """
    if path == "-":
        import sys as _sys

        text = _sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    data = _json.loads(text)
    if not isinstance(data, dict):
        return []
    findings = data.get("findings")
    if isinstance(findings, list):
        return findings
    # Some commands wrap findings under a different key (over-fetch / etc.) —
    # fall back to scanning common shapes before giving up.
    for k in ("results", "items"):
        candidate = data.get(k)
        if isinstance(candidate, list):
            return candidate
    return []


def _load_suppression_store(path: Path) -> tuple[dict, str | None]:
    """Load the finding-id-keyed suppression store — UNKNOWN never reads as EMPTY.

    Returns ``(entries, error)``. *error* is ``None`` only when the store's
    contents are KNOWN: either the file is absent (legitimately empty) or it
    parsed to a JSON object. A store that EXISTS but cannot be read, does not
    parse, or has a non-object root resolves to UNKNOWN — the caller must fail
    closed instead of treating an unreadable audit ledger as "no suppressions".

    Before this split the loader collapsed every failure to ``{}``, which had
    two measured harms: ``--list`` published ``VERDICT: 0 suppression(s)`` over
    a store that visibly held entries, and — because ``current`` is written
    straight back — a single add/remove REWROTE the store from that empty view,
    destroying every prior suppression together with its audit rationale and
    timestamps while reporting success. This is the same data-loss shape that
    :func:`roam.commands.suppression.save_suppression` already learned to
    refuse (see its append-only comment); the mirror is deliberate.
    """
    if not path.exists():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError — a store
        # corrupted with binary bytes would otherwise escape as a traceback.
        return {}, f"could not be read ({exc.__class__.__name__}: {exc})"
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        return {}, f"is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})"
    if not isinstance(parsed, dict):
        return {}, f"has a JSON {type(parsed).__name__} at its root, but must be an object mapping finding_id -> entry"
    return parsed, None


def _abort_unreadable_store(path: Path, error: str, *, json_mode: bool) -> None:
    """Fail closed on an UNKNOWN suppression store — never publish or overwrite it.

    Emits an actionable terminal verdict and exits non-zero. Every caller
    reaches this BEFORE any ``write_text``, so the on-disk audit ledger is
    left byte-for-byte intact for a human to repair.
    """
    verdict = f"UNKNOWN — suppression store {error}"
    hint = (
        f"Refusing to read or rewrite {path} — doing so would report 0 suppressions "
        f"and destroy any entries it holds. Repair the JSON, or move the file aside "
        f"to start a new store. Nothing was modified."
    )
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "suppress",
                    summary={
                        "verdict": verdict,
                        "state": "unreadable_store",
                        "partial_success": False,
                        "count": None,
                        "path": str(path),
                    },
                    status="error",
                    isError=True,
                    error_code="SUPPRESSION_STORE_UNREADABLE",
                    error=verdict,
                    hint=hint,
                    suppressions=None,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  path: {path}")
        click.echo(f"  {hint}")
    raise SystemExit(2)


def _filter_findings(findings: list[dict], filter_expr: str | None) -> list[dict]:
    """Apply a single ``key=value`` filter (D7 keeps it intentionally tiny)."""
    if not filter_expr:
        return findings
    if "=" not in filter_expr:
        return findings
    key, _, val = filter_expr.partition("=")
    key = key.strip()
    val = val.strip()
    return [f for f in findings if str(f.get(key, "")) == val]


@roam_capability(
    name="suppress",
    category="workflow",
    summary="Suppress a math / over-fetch / missing-index / auth-gaps finding",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="suppress")
@click.argument("finding_id", required=True)
@click.option(
    "--reason",
    default=None,
    help="Why this is a false positive (kept for audit). Required for `add`.",
)
@click.option(
    "--remove",
    is_flag=True,
    help="Remove an existing suppression instead of adding one.",
)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    help="List all current suppressions (ignores FINDING_ID; pass `_` as a placeholder).",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(),
    default=None,
    help=f"Suppressions file path (default: {DEFAULT_SUPPRESSIONS_PATH}).",
)
@click.option(
    "--from-finding",
    "from_finding_path",
    default=None,
    help=(
        "D7 — batch ingest finding IDs from a JSON envelope "
        "(e.g. `roam --json math` output). Pass `-` to read stdin. "
        "Combine with --filter and --reason; --remove still removes."
    ),
)
@click.option(
    "--filter",
    "filter_expr",
    default=None,
    help="Single key=value filter for --from-finding (e.g. `task_id=membership`).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="With --from-finding, print what would change without writing the suppressions file.",
)
@click.pass_context
def suppress(
    ctx,
    finding_id: str,
    reason: str | None,
    remove: bool,
    list_only: bool,
    input_path: str | None,
    from_finding_path: str | None,
    filter_expr: str | None,
    dry_run: bool,
) -> None:
    """Suppress a math / over-fetch / missing-index / auth-gaps finding.

    \b
    Examples:
      roam suppress a1b2c3d4e5f60718 --reason "depth-limited; verified"
      roam suppress a1b2c3d4e5f60718 --remove
      roam suppress _ --list
      roam --json suppress _ --list   # JSON envelope for scripts

      # D7 batch ingest:
      roam --json math > findings.json
      roam suppress _ --from-finding findings.json --reason "vetted batch"
      roam --json math | roam suppress _ --from-finding - --reason "stdin"

    Get the finding ID from `roam --json math` (each finding carries a
    `finding_id` field added by the suppression layer).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    path = Path(input_path) if input_path else DEFAULT_SUPPRESSIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing. An absent store is legitimately empty; an unreadable one
    # is UNKNOWN and must not be published as 0 or silently overwritten — every
    # path below either reports len(current) as a total or writes current back.
    current, store_error = _load_suppression_store(path)
    if store_error is not None:
        _abort_unreadable_store(path, store_error, json_mode=json_mode)

    if list_only:
        summary = {
            "verdict": f"{len(current)} suppression(s)",
            "count": len(current),
            "path": str(path),
        }
        if json_mode:
            click.echo(to_json(json_envelope("suppress", summary=summary, suppressions=current)))
        else:
            click.echo(f"VERDICT: {summary['verdict']}")
            click.echo(f"  path: {path}")
            for fid, entry in sorted(current.items()):
                click.echo(f"  {fid}  {entry.get('reason', '<no reason>')[:80]}")
        return

    if from_finding_path:
        if not reason:
            ctx.fail("--reason is required with --from-finding")
        try:
            findings = _load_findings_from_envelope_path(from_finding_path)
        except FileNotFoundError:
            ctx.fail(f"--from-finding path not found: {from_finding_path}")
        except _json.JSONDecodeError as exc:
            ctx.fail(f"--from-finding could not parse JSON: {exc}")
        findings = _filter_findings(findings, filter_expr)
        # Each finding must carry a `finding_id` populated by the suppression
        # layer. Findings without one are skipped (logged in JSON output).
        added: list[str] = []
        skipped_no_id: list[dict] = []
        already: list[str] = []
        for f in findings:
            fid = f.get("finding_id")
            if not fid:
                skipped_no_id.append({k: f.get(k) for k in ("task_id", "name", "location") if f.get(k)})
                continue
            if remove:
                if current.pop(fid, None) is not None:
                    added.append(fid)
                continue
            if fid in current:
                already.append(fid)
                continue
            entry_record: dict = {
                "reason": reason,
                "added_at": _utc_now_iso(),
                "source": "from-finding",
            }
            # W691 — capture rule_id + location so the SARIF reader can
            # match this dict-shaped suppression against (ruleId, location)
            # results. finding_id alone is a one-way hash so we cannot
            # reverse it from the SARIF result side.
            rule_id_val = f.get("rule_id") or f.get("ruleId")
            location_val = f.get("location")
            if rule_id_val:
                entry_record["rule_id"] = str(rule_id_val)
            if location_val:
                entry_record["location"] = str(location_val)
            current[fid] = entry_record
            added.append(fid)
        if not dry_run:
            path.write_text(_json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        action = "removed" if remove else "suppressed"
        verdict = f"{action} {len(added)} finding(s) from {from_finding_path}"
        if dry_run:
            verdict = f"DRY-RUN — would have {action} {len(added)} finding(s) from {from_finding_path}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "suppress",
                        summary={
                            "verdict": verdict,
                            "added": len(added),
                            "skipped_no_finding_id": len(skipped_no_id),
                            "already_suppressed": len(already),
                            "dry_run": dry_run,
                        },
                        added_ids=added,
                        already_suppressed_ids=already,
                        skipped=skipped_no_id,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
            if already:
                click.echo(f"  already suppressed: {len(already)}")
            if skipped_no_id:
                click.echo(f"  skipped (no finding_id): {len(skipped_no_id)}")
        return

    if remove:
        existed = current.pop(finding_id, None)
        path.write_text(_json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        verdict = f"removed {finding_id}" if existed else f"no-op: {finding_id} not found"
        if json_mode:
            click.echo(to_json(json_envelope("suppress", summary={"verdict": verdict, "removed": bool(existed)})))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    # Add path
    if not reason:
        ctx.fail("--reason is required when adding a suppression (use `roam suppress _ --list` to view existing)")
    current[finding_id] = {
        "reason": reason,
        "added_at": _utc_now_iso(),
    }
    path.write_text(_json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    verdict = f"suppressed {finding_id}"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "suppress",
                    summary={"verdict": verdict, "finding_id": finding_id},
                    entry=current[finding_id],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  reason: {reason}")
        click.echo(f"  saved:  {path}")
